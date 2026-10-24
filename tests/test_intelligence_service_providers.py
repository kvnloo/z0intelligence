import io
import json
import threading
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest

import z0int.intelligence_service as svc
import z0int.provider_saturation as saturation


@pytest.fixture
def providers(monkeypatch):
    clock = [100.0]
    calls = {"policy": 0, "snapshot": 0, "aodl": 0, "governed": 0}
    config = {
        "provider_caps": {"openrouter": 2},
        "free_only": True,
        "validated_free_routes": [
            {"provider": "openrouter", "model": "free", "validated": True, "price_usd": 0},
            {"provider": "openrouter", "model": "paid", "validated": True, "price_usd": 1},
            {"provider": "openrouter", "model": "unverified", "price_usd": 0},
        ],
    }

    def policy():
        calls["policy"] += 1
        return config

    def snapshot(provider, selected_config):
        assert provider == "openrouter"
        assert selected_config is config
        calls["snapshot"] += 1
        return {"available": True, "active": calls["snapshot"], "held_tokens": ["private"]}

    def aodl():
        calls["aodl"] += 1
        return {"aodl_admission_ready": True, "aodl_canon_version": "aodl-canon-1"}

    def governed():
        calls["governed"] += 1
        return {"governed_remote_enabled": False, "governed_remote_ready": False}

    monkeypatch.setattr(svc, "PROVIDERS_RESPONSE", None)
    monkeypatch.setattr(svc, "PROVIDERS_EXPIRES", 0.0)
    monkeypatch.setattr(svc, "time", SimpleNamespace(monotonic=lambda: clock[0]))
    monkeypatch.setattr(saturation, "policy", policy)
    monkeypatch.setattr(saturation, "snapshot", snapshot)
    monkeypatch.setattr(svc, "aodl_admission_state", aodl)
    monkeypatch.setattr(svc, "governed_worker_state", governed)
    return clock, calls


def handler(path="/v1/providers"):
    result = svc.Handler.__new__(svc.Handler)
    result.path = path
    result.close_connection = False
    result.wfile = io.BytesIO()
    result.statuses = []
    result.response_headers = {}
    result.send_response = result.statuses.append
    result.send_header = result.response_headers.__setitem__
    result.end_headers = lambda: None
    return result


def test_providers_cache_reuses_encoded_response_until_exact_ttl(providers, monkeypatch):
    clock, calls = providers
    original_dumps = svc.json.dumps
    encoded = []

    def dumps(*args, **kwargs):
        encoded.append(True)
        return original_dumps(*args, **kwargs)

    monkeypatch.setattr(svc.json, "dumps", dumps)
    first = svc.providers_response()
    clock[0] = 129.999
    assert svc.providers_response() is first
    assert calls == {"policy": 1, "snapshot": 1, "aodl": 1, "governed": 1}
    assert len(encoded) == 1
    clock[0] = 130.0
    refreshed = svc.providers_response()
    assert refreshed is not first
    assert json.loads(refreshed)["providers"]["openrouter"]["active"] == 2
    assert calls == {"policy": 2, "snapshot": 2, "aodl": 2, "governed": 2}
    assert len(encoded) == 2


def test_providers_endpoint_preserves_payload_and_http_headers(providers):
    request = handler()
    request.do_GET()
    body = request.wfile.getvalue()
    assert request.statuses == [200]
    assert request.response_headers == {
        "Content-Type": "application/json", "Content-Length": str(len(body)),
    }
    assert json.loads(body) == {
        "authority_protocol_version": 3,
        "supported_authority_protocol_versions": [2, 3],
        "aodl_admission_ready": True,
        "aodl_canon_version": "aodl-canon-1",
        "governed_remote_enabled": False,
        "governed_remote_ready": False,
        "providers": {"openrouter": {"available": True, "active": 1}},
        "free_only": True,
        "validated_free_models": {"openrouter": ["free"]},
    }


@pytest.mark.parametrize("expired", [False, True])
def test_providers_concurrent_misses_build_once(providers, monkeypatch, expired):
    clock, calls = providers
    if expired:
        svc.providers_response()
        clock[0] = 130.0
    before = calls["policy"]
    original_policy = saturation.policy
    entered = threading.Event()
    release = threading.Event()
    start = threading.Barrier(5)

    def policy():
        entered.set()
        assert release.wait(5), "provider build was not released"
        return original_policy()

    def request():
        start.wait(timeout=5)
        return svc.providers_response()

    monkeypatch.setattr(saturation, "policy", policy)
    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = [pool.submit(request) for _ in range(4)]
        try:
            start.wait(timeout=5)
            assert entered.wait(5), "provider build did not start"
            assert all(not future.done() for future in futures)
        finally:
            release.set()
        results = [future.result(timeout=5) for future in futures]
    assert all(result is results[0] for result in results)
    assert calls["policy"] == before + 1
    assert calls["snapshot"] == before + 1


def test_providers_ttl_starts_after_build_and_failed_refresh_is_not_cached(providers, monkeypatch):
    clock, calls = providers
    original_policy = saturation.policy

    def slow_policy():
        clock[0] += 10
        return original_policy()

    monkeypatch.setattr(saturation, "policy", slow_policy)
    first = svc.providers_response()
    assert svc.PROVIDERS_EXPIRES == 140.0
    clock[0] = 139.999
    assert svc.providers_response() is first
    clock[0] = 140.0

    def failing_policy():
        raise ValueError("broken policy")

    monkeypatch.setattr(saturation, "policy", failing_policy)
    with pytest.raises(ValueError, match="broken policy"):
        svc.providers_response()
    assert svc.PROVIDERS_RESPONSE is first
    assert svc.PROVIDERS_EXPIRES == 140.0
    monkeypatch.setattr(saturation, "policy", original_policy)
    assert svc.providers_response() is not first
    assert calls["policy"] == 2


@pytest.mark.parametrize("error", [BrokenPipeError, ConnectionResetError])
@pytest.mark.parametrize("stage", ["headers", "body"])
def test_response_disconnect_closes_without_second_response(error, stage):
    request = handler()

    def disconnect(*args):
        raise error("client disconnected")

    if stage == "headers":
        request.end_headers = disconnect
    else:
        request.wfile = SimpleNamespace(write=disconnect)
    request.reply(200, {"ok": True})
    assert request.close_connection is True
    assert request.statuses == [200]


@pytest.mark.parametrize("error", [BrokenPipeError, ConnectionResetError])
def test_post_read_disconnect_does_not_send_error_response(error):
    request = handler("/v1/plan")
    request.headers = {"Content-Length": "1"}

    def disconnect(size):
        raise error("client disconnected")

    request.rfile = SimpleNamespace(read=disconnect)
    request.do_POST()
    assert request.close_connection is True
    assert request.statuses == []


@pytest.mark.parametrize("error", [BrokenPipeError, ConnectionResetError, RuntimeError])
def test_server_silences_only_client_disconnect_errors(monkeypatch, error):
    reported = []
    monkeypatch.setattr(svc.ThreadingHTTPServer, "handle_error", lambda *args: reported.append(args))
    server = svc.Service.__new__(svc.Service)
    try:
        raise error("request failed")
    except error:
        server.handle_error(object(), ("127.0.0.1", 1234))
    assert len(reported) == (0 if error in (BrokenPipeError, ConnectionResetError) else 1)


@pytest.mark.parametrize("error", [BrokenPipeError, ConnectionResetError])
def test_overload_disconnect_still_shuts_down_socket(monkeypatch, error):
    draining = threading.Event()
    draining.set()
    monkeypatch.setattr(svc, "DRAINING", draining)
    server = svc.Service.__new__(svc.Service)
    closed = []
    server.shutdown_request = closed.append

    def disconnect(data):
        raise error("client disconnected")

    request = SimpleNamespace(sendall=disconnect)
    server.process_request(request, ("127.0.0.1", 1234))
    assert closed == [request]


def test_response_write_failure_other_than_disconnect_is_not_silenced():
    request = handler()

    def fail(data):
        raise OSError("unexpected write failure")

    request.wfile = SimpleNamespace(write=fail)
    with pytest.raises(OSError, match="unexpected write failure"):
        request.reply(200, {"ok": True})
