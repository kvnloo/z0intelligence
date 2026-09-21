"""Supervisor tests: one endpoint, one resident model, honest labels.

These run without a GPU or a real llama-server — the child process is faked, so
the tests cover the swap/residency/metadata logic and the HTTP surface.
"""

from __future__ import annotations

import json
import threading
import time
import urllib.request

import pytest

from z0int.cognition.server import (
    SCHEMA,
    ModelSupervisor,
    SupervisorError,
    _handler_factory,
)


class _FakeLlamaServer:
    """Stands in for a real llama-server child."""

    instances = 0

    def __init__(self, *, binary, gguf, context, port=None, extra_args=()):
        type(self).instances += 1
        self.binary = binary
        self.gguf = gguf
        self.context = context
        self.port = port or 19000 + type(self).instances
        self.stopped = False
        self.calls: list[dict] = []

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def start(self):
        return self

    def stop(self):
        self.stopped = True


@pytest.fixture()
def supervisor(monkeypatch, tmp_path):
    gguf_root = tmp_path / "gguf"
    for rel in __import__(
        "z0int.cognition.serving", fromlist=["GGUF_LAYOUT"]
    ).GGUF_LAYOUT.values():
        p = gguf_root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"GGUF")

    import z0int.cognition.server as server_mod

    monkeypatch.setattr(server_mod, "LlamaServer", _FakeLlamaServer)
    monkeypatch.setattr(server_mod, "llama_build", lambda _b: "test-1.0")
    sup = server_mod.ModelSupervisor(
        binary="/nonexistent/llama-server",
        gguf_dir=gguf_root,
        default_context=2048,
        idle_unload_s=300.0,
    )
    return sup


def test_known_models_come_from_the_manifest(supervisor):
    known = supervisor.known_models()
    assert "hammer2.1_3b" in known
    assert "nemotron_orchestrator_8b" in known


def test_unknown_model_is_an_error_not_a_crash(supervisor):
    with pytest.raises(SupervisorError, match="unknown model"):
        supervisor.ensure("not_a_model")


def test_forward_rejects_an_unknown_model_with_503(supervisor):
    code, out = supervisor.forward("not_a_model", {"model": "not_a_model", "messages": []})
    assert code == 503
    assert "unknown model" in out["error"]["message"]


def test_swap_evicts_the_previous_model_and_records_history(supervisor):
    _FakeLlamaServer.instances = 0
    supervisor.ensure("hammer2.1_3b")
    first = supervisor.resident
    assert first == "hammer2.1_3b"
    supervisor.ensure("functiongemma_270m")
    assert supervisor.resident == "functiongemma_270m"
    history = supervisor.history()
    assert history and history[-1]["model_id"] == "hammer2.1_3b"
    assert history[-1]["evicted_at"] is not None
    assert history[-1]["load_ms"] >= 0.0
    assert _FakeLlamaServer.instances == 2


def test_requesting_the_resident_model_does_not_reload(supervisor):
    _FakeLlamaServer.instances = 0
    supervisor.ensure("hammer2.1_3b")
    supervisor.ensure("hammer2.1_3b")
    assert _FakeLlamaServer.instances == 1


def test_forward_attaches_supervisor_metadata_and_load_cost(supervisor, monkeypatch):
    sent = {}

    def fake_urlopen(req, timeout=0):
        sent["url"] = req.full_url
        sent["body"] = json.loads(req.data.decode())

        class _Resp:
            def __enter__(self_inner):
                return self_inner

            def __exit__(self_inner, *exc):
                return False

            def read(self_inner):
                return json.dumps(
                    {
                        "choices": [{"message": {"content": "ok"}}],
                        "usage": {"prompt_tokens": 5, "completion_tokens": 1},
                    }
                ).encode()

        return _Resp()

    import z0int.cognition.server as server_mod

    monkeypatch.setattr(server_mod, "urlopen", fake_urlopen)

    code, out = supervisor.forward(
        "hammer2.1_3b", {"model": "ignored", "messages": [], "max_tokens": 16}
    )
    assert code == 200
    meta = out["z0int_supervisor"]
    assert meta["schema"] == SCHEMA
    assert meta["resident_model"] == "hammer2.1_3b"
    assert meta["cold"] is True
    assert meta["load_ms"] >= 0.0
    assert meta["runtime"] == "llama.cpp-test-1.0"
    # the request was rewritten to the canonical model id
    assert sent["body"]["model"] == "hammer2.1_3b"
    assert sent["url"].endswith("/v1/chat/completions")


def test_a_warm_request_reports_cold_false(supervisor, monkeypatch):
    def fake_urlopen(req, timeout=0):
        class _Resp:
            def __enter__(self_inner):
                return self_inner

            def __exit__(self_inner, *exc):
                return False

            def read(self_inner):
                return json.dumps({"choices": [{"message": {"content": "ok"}}]}).encode()

        return _Resp()

    import z0int.cognition.server as server_mod

    monkeypatch.setattr(server_mod, "urlopen", fake_urlopen)
    supervisor.forward("hammer2.1_3b", {"model": "hammer2.1_3b", "messages": []})
    code, out = supervisor.forward("hammer2.1_3b", {"model": "hammer2.1_3b", "messages": []})
    assert code == 200
    assert out["z0int_supervisor"]["cold"] is False


def test_upstream_failure_is_reported_not_raised(supervisor, monkeypatch):
    import urllib.error

    import z0int.cognition.server as server_mod

    def boom(req, timeout=0):
        raise urllib.error.URLError("connection refused")

    monkeypatch.setattr(server_mod, "urlopen", boom)
    code, out = supervisor.forward("hammer2.1_3b", {"model": "hammer2.1_3b", "messages": []})
    assert code == 502
    assert "unreachable" in out["error"]["type"]


def test_max_tokens_is_capped(supervisor, monkeypatch):
    seen = {}

    def fake_urlopen(req, timeout=0):
        seen["body"] = json.loads(req.data.decode())

        class _Resp:
            def __enter__(self_inner):
                return self_inner

            def __exit__(self_inner, *exc):
                return False

            def read(self_inner):
                return json.dumps({"choices": [{"message": {"content": "ok"}}]}).encode()

        return _Resp()

    import z0int.cognition.server as server_mod

    monkeypatch.setattr(server_mod, "urlopen", fake_urlopen)
    supervisor.forward(
        "hammer2.1_3b", {"model": "hammer2.1_3b", "messages": [], "max_tokens": 10_000_000}
    )
    assert seen["body"]["max_tokens"] == supervisor.max_tokens_cap


def test_idle_unload_evicts_and_is_recorded(supervisor):
    # idle_unload_s <= 0 means "disabled"; use a tiny positive window to force it.
    supervisor.idle_unload_s = 0.001
    supervisor.ensure("hammer2.1_3b")
    time.sleep(0.01)
    assert supervisor.maybe_idle_unload() is True
    assert supervisor.resident is None
    history = supervisor.history()
    assert history[-1]["evicted_at"] is not None


def test_idle_unload_zero_disables_the_reaper(supervisor):
    supervisor.idle_unload_s = 0.0
    supervisor.ensure("hammer2.1_3b")
    assert supervisor.maybe_idle_unload() is False
    assert supervisor.resident == "hammer2.1_3b"


def test_status_reports_resident_and_history(supervisor):
    supervisor.ensure("hammer2.1_3b")
    status = supervisor.status()
    assert status["schema"] == SCHEMA
    assert status["resident"] == "hammer2.1_3b"
    assert isinstance(status["history"], list)


# --- HTTP surface -------------------------------------------------------


@pytest.fixture()
def live_supervisor(supervisor, monkeypatch):
    from http.server import ThreadingHTTPServer

    def fake_urlopen(req, timeout=0):
        class _Resp:
            def __enter__(self_inner):
                return self_inner

            def __exit__(self_inner, *exc):
                return False

            def read(self_inner):
                return json.dumps({"choices": [{"message": {"content": "ok"}}]}).encode()

        return _Resp()

    import z0int.cognition.server as server_mod

    monkeypatch.setattr(server_mod, "urlopen", fake_urlopen)
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), _handler_factory(supervisor))
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    host, port = httpd.server_address
    yield f"http://{host}:{port}"
    httpd.shutdown()
    httpd.server_close()


def _get(url: str) -> dict:
    with urllib.request.urlopen(url, timeout=5) as resp:
        return json.loads(resp.read().decode())


def test_http_models_endpoint_lists_every_known_model(live_supervisor):
    data = _get(f"{live_supervisor}/v1/models")
    ids = [m["id"] for m in data["data"]]
    assert "hammer2.1_3b" in ids
    assert "nemotron_orchestrator_8b" in ids
    # every id is a real manifest model, not an invented name
    from z0int.cognition.manifest import load_local_cognition

    assert set(ids) <= set(load_local_cognition().models)


def test_http_health_and_status(live_supervisor):
    assert _get(f"{live_supervisor}/health")["status"] == "ok"
    status = _get(f"{live_supervisor}/z0int/status")
    assert status["schema"] == SCHEMA


def test_http_chat_returns_supervisor_metadata(live_supervisor):
    req = urllib.request.Request(
        f"{live_supervisor}/v1/chat/completions",
        data=json.dumps({"model": "hammer2.1_3b", "messages": []}).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=10) as resp:
        out = json.loads(resp.read().decode())
    assert out["z0int_supervisor"]["resident_model"] == "hammer2.1_3b"


def test_http_chat_without_a_model_is_a_400(live_supervisor):
    import urllib.error

    req = urllib.request.Request(
        f"{live_supervisor}/v1/chat/completions",
        data=json.dumps({"messages": []}).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with pytest.raises(urllib.error.HTTPError) as exc:
        urllib.request.urlopen(req, timeout=10)
    assert exc.value.code == 400


def test_http_unknown_route_is_404(live_supervisor):
    import urllib.error

    with pytest.raises(urllib.error.HTTPError) as exc:
        urllib.request.urlopen(f"{live_supervisor}/nope", timeout=5)
    assert exc.value.code == 404
