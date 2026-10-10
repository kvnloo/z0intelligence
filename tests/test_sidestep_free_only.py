"""402/429 sidestep must fail closed under a free-only policy (issue #139, PR #141).

Stubbed loopback providers only. A socket guard fails any non-loopback connection.
"""
import copy
import json
import socket
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from z0int import provider_saturation as saturation
from z0int import worker_routing as wr
from z0int.receipt import append_receipt

OPENROUTER = "nvidia/nemotron-3-super-120b-a12b:free"
GROQ = "openai/gpt-oss-20b"
# The sidestep order shipped at PR head 2f96041, kept here so the code is tested
# against it whatever the manifest lists.
HEAD_SIDESTEP_ORDER = [
    {"provider": "nous", "model": "inclusionai/ling-3.0-flash-sante:free"},
    {"provider": "vercel", "model": "openai/gpt-oss-20b"},
    {"provider": "openrouter", "model": OPENROUTER},
    {"provider": "groq", "model": GROQ},
    {"provider": "nvidia", "model": "openai/gpt-oss-20b"},
]


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("Z0INT_HOME", str(tmp_path))
    monkeypatch.setattr("z0int.quota_budget.project", lambda *args, **kwargs: None)
    real_connect = socket.socket.connect

    def loopback_only(sock, address):
        host = address[0] if isinstance(address, tuple) else address
        if host not in ("127.0.0.1", "::1", "localhost"):
            raise AssertionError(f"non-loopback connection attempted: {address!r}")
        return real_connect(sock, address)

    monkeypatch.setattr(socket.socket, "connect", loopback_only)

    def no_oauth(provider):
        raise AssertionError("hermes oauth must not be resolved in tests")

    monkeypatch.setattr(wr, "resolve_hermes_oauth", no_oauth)


def run(monkeypatch, behaviour, *, candidates=None, policy_edit=None, args_extra=None):
    """behaviour(provider, call_number) -> (http_status, reported_cost)."""
    calls, bodies = [], []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            provider = self.path.strip("/").split("/")[0]
            calls.append(provider)
            bodies.append(body)
            status, cost = behaviour(provider, len(calls))
            self.send_response(status)
            self.end_headers()
            if status != 200:
                self.wfile.write(b'{"error":{"message":"stub"}}')
                return
            self.wfile.write(json.dumps({
                "model": body["model"],
                "choices": [{"message": {"content": "pong"}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 2, "completion_tokens": 1, "cost": cost},
            }).encode())

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever)
    thread.start()
    policy, providers = wr.configuration()
    policy, providers = copy.deepcopy(policy), copy.deepcopy(providers)
    if policy_edit:
        policy_edit(policy)
    for name, config in providers.items():
        config.update(base_url=f"http://127.0.0.1:{server.server_port}/{name}", api_key_env="TEST_SIDESTEP_KEY", auth="environment")
    monkeypatch.setenv("TEST_SIDESTEP_KEY", "local-test-only")
    plan = {
        "candidates": candidates or [{"provider": "openrouter", "model": OPENROUTER}],
        "source": "test", "category": "probe", "reason": "sidestep free-only",
        "harness": "omp", "caller_trace_id": "sidestep-free-only-test",
    }
    args = {"task": "Reply pong", "parent_agent": "test", "max_tokens": 16, **(args_extra or {})}
    try:
        result = wr.execute_plan(args, policy, providers, plan, receipt_sink=append_receipt)
    finally:
        server.shutdown()
        server.server_close()
        thread.join()
    return result, calls, bodies


def head_order(policy):
    policy["sidestep_order"] = copy.deepcopy(HEAD_SIDESTEP_ORDER)


def extras(result):
    return [row["extra"] for row in result["attempts"]]


# (1) manifest free_only: a sidestep attempt that reports positive cost is not a success.
def test_manifest_free_only_positive_cost_on_sidestep_fails_the_call(monkeypatch):
    assert wr.configuration()[0]["free_only"] is True
    result, calls, _ = run(monkeypatch, lambda provider, n: (402, 0) if provider == "openrouter" else (200, 0.0123),
                           policy_edit=head_order)
    assert result["ok"] is False
    assert result["requires_parent"] is True
    assert result["free_only"] is True
    last = extras(result)[-1]
    assert last["sidestep"] is True
    assert last["free_only"] is True
    assert last["status"] == "failed"
    assert last["provider_reported_cost_usd"] == 0.0123
    assert last["free_only_violation"] == "provider_reported_nonzero_cost"
    assert "cost" in result["refusal_reason"]
    # The violating attempt ends the call: nothing is tried after money was reported spent.
    assert calls == ["openrouter", "groq"]


# (2) an explicit caller free_only=True holds across a sidestep even if the manifest is not free-only.
def test_caller_free_only_is_honoured_across_sidestep(monkeypatch):
    def edit(policy):
        policy["free_only"] = False
        head_order(policy)

    result, calls, _ = run(monkeypatch, lambda provider, n: (429, 0) if n == 1 else (200, 0.5),
                           policy_edit=edit, args_extra={"free_only": True})
    assert result["ok"] is False
    assert result["free_only"] is True
    assert [extra["free_only"] for extra in extras(result)] == [True, True]
    assert extras(result)[-1]["free_only_violation"] == "provider_reported_nonzero_cost"
    assert calls == ["openrouter", "groq"]


# A positive cost on the first (non-sidestep) attempt also ends the call.
def test_positive_cost_on_primary_attempt_fails_the_call(monkeypatch):
    result, calls, _ = run(monkeypatch, lambda provider, n: (200, 0.01) if n == 1 else (200, 0),
                           candidates=[{"provider": "openrouter", "model": OPENROUTER}, {"provider": "groq", "model": GROQ}])
    assert result["ok"] is False
    assert calls == ["openrouter"]
    assert extras(result)[-1]["free_only"] is True
    assert extras(result)[-1]["free_only_violation"] == "provider_reported_nonzero_cost"


# (3) sidestep only goes to validated free routes that the manifest does not exclude.
def test_sidestep_skips_unvalidated_and_excluded_routes(monkeypatch):
    def edit(policy):
        head_order(policy)
        # Even a route with validated zero-cost evidence stays out while the manifest excludes its provider.
        policy["validated_free_routes"].append({"provider": "vercel", "model": "openai/gpt-oss-20b", "validated": True,
                                                "price_usd": 0, "evidence_sha256": "0" * 64})
        assert "vercel" in policy["free_policy_exclusions"]

    result, calls, _ = run(monkeypatch, lambda provider, n: (402, 0), policy_edit=edit)
    assert result["ok"] is False
    assert calls == ["openrouter", "groq"]
    assert not {"nous", "vercel", "nvidia"} & set(calls)


def test_excluded_provider_has_no_free_route():
    policy = copy.deepcopy(wr.configuration()[0])
    policy["validated_free_routes"].append({"provider": "vercel", "model": "openai/gpt-oss-20b", "validated": True,
                                            "price_usd": 0, "evidence_sha256": "0" * 64})
    assert wr.free_route(policy, "vercel", "openai/gpt-oss-20b") is None
    with pytest.raises(ValueError):
        wr.require_free_route(policy, "vercel", "openai/gpt-oss-20b", {"free_only": True})
    assert wr.free_route(policy, "groq", GROQ) is not None


def test_manifest_sidestep_order_lists_only_validated_free_routes():
    policy = wr.configuration()[0]
    assert policy["sidestep_order"]
    bad = [item for item in policy["sidestep_order"]
           if wr.free_route(policy, item["provider"], item["model"]) is None
           or item["provider"] in policy["free_policy_exclusions"]]
    assert bad == []


# (3) request constraints of the validated route are sent on a sidestep attempt too.
def test_sidestep_keeps_request_constraints(monkeypatch):
    def edit(policy):
        policy["sidestep_order"] = [{"provider": "openrouter", "model": OPENROUTER}]

    result, calls, bodies = run(monkeypatch, lambda provider, n: (402, 0) if provider == "groq" else (200, 0),
                                candidates=[{"provider": "groq", "model": GROQ}], policy_edit=edit)
    assert calls == ["groq", "openrouter"]
    assert result["ok"] is True
    assert extras(result)[-1]["sidestep"] is True
    assert bodies[1]["provider"] == {"max_price": {"prompt": 0, "completion": 0, "request": 0}, "allow_fallbacks": False}


# (4) a plan candidate cannot buy its way past validation by carrying the flag.
@pytest.mark.parametrize("flag", [{}, {"sidestep": True}])
def test_plan_sidestep_flag_does_not_bypass_validation(monkeypatch, flag):
    with pytest.raises(ValueError, match="free_only"):
        run(monkeypatch, lambda provider, n: (200, 3.0),
            candidates=[{"provider": "vercel", "model": "anthropic/claude-opus-paid", **flag}])


def test_plan_sidestep_flag_makes_no_provider_call(monkeypatch):
    seen = []

    def behaviour(provider, n):
        seen.append(provider)
        return 200, 3.0

    with pytest.raises(ValueError):
        run(monkeypatch, behaviour, candidates=[{"provider": "nous", "model": "inclusionai/ling-3.0-flash-sante:free", "sidestep": True}])
    assert seen == []


# (5) the block list matches the manifest's real provider keys.
def test_blocked_providers_use_real_manifest_keys(monkeypatch):
    providers = wr.configuration()[1]
    assert "grok" in providers
    assert wr.blocked_provider("grok")
    assert wr.blocked_provider("Grok")
    assert wr.blocked_provider("codex")
    assert wr.blocked_provider("cursor")
    assert not wr.blocked_provider("groq")
    assert not wr.blocked_provider("openrouter")

    def edit(policy):
        policy["free_only"] = False
        policy["sidestep_order"] = [{"provider": "grok", "model": "grok-4.20-0309-non-reasoning"}]

    result, calls, _ = run(monkeypatch, lambda provider, n: (402, 0) if n == 1 else (200, 0), policy_edit=edit)
    assert result["ok"] is False
    assert calls == ["openrouter"]


# (6) an unmeasured cap is not a reason to admit an unhealthy provider.
@pytest.mark.parametrize("status", [403, 402, 429])
def test_sidestep_admission_refuses_blocked_or_cooling_unmeasured_provider(status):
    assert wr.configuration()[0]["provider_caps"]["nous"] is None
    first = saturation.acquire("nous", f"first-{status}", {"sidestep": True})
    assert first["admitted"] and first["reason"] == "sidestep_unmeasured_cap"
    saturation.release(first["token"], "nous", status, 5)
    assert saturation.snapshot("nous")["unhealthy"]
    second = saturation.acquire("nous", f"second-{status}", {"sidestep": True})
    assert second["admitted"] is False
    assert second["reason"] != "sidestep_unmeasured_cap"


def test_unmeasured_cap_without_sidestep_is_still_refused():
    assert saturation.acquire("nous", "plain", {})["admitted"] is False


# What already held and must keep holding.
def test_500_does_not_sidestep(monkeypatch):
    result, calls, _ = run(monkeypatch, lambda provider, n: (500, 0) if n == 1 else (200, 0), policy_edit=head_order)
    assert result["ok"] is False
    assert calls == ["openrouter"]


def test_all_402_ends_refused_and_cursor_is_never_called(monkeypatch):
    result, calls, _ = run(monkeypatch, lambda provider, n: (402, 0), policy_edit=head_order)
    assert result["ok"] is False
    assert result["requires_parent"] is True
    assert result["refusal_reason"] == "No candidate completed; no paid overflow"
    assert calls[0] == "openrouter" and len(calls) == len(set(calls))
    assert not [name for name in calls if name.startswith("cursor") or name in ("codex", "grok")]


# The fix is not "disable sidestep": a validated free route still completes after a 402 or 429.
@pytest.mark.parametrize("status", [402, 429])
def test_free_validated_sidestep_still_succeeds(monkeypatch, status):
    result, calls, _ = run(monkeypatch, lambda provider, n: (status, 0) if provider == "openrouter" else (200, 0),
                           policy_edit=head_order)
    assert result["ok"] is True
    assert result["provider"] == "groq" and result["model"] == GROQ
    assert calls == ["openrouter", "groq"]
    last = extras(result)[-1]
    assert last["sidestep"] is True
    assert last["free_only"] is True
    assert last["free_tier_validated"] is True
    assert last["provider_reported_cost_usd"] == 0
    assert last["status"] == "completed"
    assert "free_only_violation" not in last


def test_free_validated_sidestep_succeeds_on_shipped_manifest(monkeypatch):
    result, calls, _ = run(monkeypatch, lambda provider, n: (402, 0) if provider == "openrouter" else (200, 0))
    assert result["ok"] is True
    assert calls == ["openrouter", "groq"]
