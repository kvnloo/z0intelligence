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
    """behaviour(provider, call_number) -> (http_status, reported_cost[, whole 200 body])."""
    calls, bodies = [], []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            provider = self.path.strip("/").split("/")[0]
            calls.append(provider)
            bodies.append(body)
            status, cost, *override = behaviour(provider, len(calls))
            self.send_response(status)
            self.end_headers()
            if status != 200:
                self.wfile.write(b'{"error":{"message":"stub"}}')
                return
            if override:
                # A third element replaces the whole 200 body: a dict, or raw bytes.
                raw = override[0](body) if callable(override[0]) else override[0]
                self.wfile.write(raw if isinstance(raw, bytes) else json.dumps(raw).encode())
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


# ---- repair round 2 -------------------------------------------------------------
TWO = [{"provider": "openrouter", "model": OPENROUTER}, {"provider": "groq", "model": GROQ}]


def body_with(message, usage, **top):
    """A 200 body whose first choice carries `message`; `usage` is sent exactly as given."""
    def build(request):
        out = {"model": request["model"], "choices": [{"message": message, "finish_reason": "stop"}], "usage": usage}
        out.update(top)
        return out
    return build


def first_then_free(first_body):
    """The first provider answers 200 with `first_body`; any later one answers a clean free 200."""
    return lambda provider, n: (200, None, first_body) if n == 1 else (200, 0)


def assert_refused_for_cost(result, calls):
    assert result["ok"] is False
    assert result["requires_parent"] is True
    assert calls == ["openrouter"], "no candidate may be tried after a cost violation"
    last = extras(result)[-1]
    assert last["status"] == "failed"
    assert last["free_only"] is True
    assert last["free_only_violation"] == "provider_reported_nonzero_cost"
    assert "cost" in result["refusal_reason"]
    return last


# (R1) the cost is read and checked before any content-shape check, on every 200 response.
@pytest.mark.parametrize("content", [
    [{"type": "text", "text": "pong"}],          # OpenAI-style content parts
    7,                                           # a number
    {"text": "pong"},
    True,
])
def test_non_string_content_with_positive_cost_is_a_violation(monkeypatch, content):
    paid = body_with({"content": content}, {"prompt_tokens": 2, "completion_tokens": 1, "cost": 0.25})
    result, calls, _ = run(monkeypatch, first_then_free(paid), candidates=TWO)
    last = assert_refused_for_cost(result, calls)
    assert last["provider_reported_cost_usd"] == 0.25


def shapeless(kind):
    usage = {"prompt_tokens": 2, "completion_tokens": 1, "cost": 0.25}
    return {
        "no_choices": lambda request: {"model": request["model"], "choices": [], "usage": usage},
        "choices_missing": lambda request: {"model": request["model"], "usage": usage},
        "choice_not_object": lambda request: {"model": request["model"], "choices": ["pong"], "usage": usage},
        "message_not_object": lambda request: {"model": request["model"], "choices": [{"message": "pong", "finish_reason": "stop"}], "usage": usage},
        "choices_not_list": lambda request: {"model": request["model"], "choices": {"a": 1}, "usage": usage},
        "token_counts_not_objects": lambda request: {"model": request["model"], "choices": [{"message": {"content": "pong"}, "finish_reason": "stop"}],
                                                     "usage": {"prompt_tokens": 2, "completion_tokens": 1, "prompt_tokens_details": "x", "cost": 0.25}},
    }[kind]


@pytest.mark.parametrize("kind", ["no_choices", "choices_missing", "choice_not_object", "message_not_object",
                                  "choices_not_list", "token_counts_not_objects"])
def test_malformed_200_with_positive_cost_is_a_violation(monkeypatch, kind):
    result, calls, _ = run(monkeypatch, first_then_free(shapeless(kind)), candidates=TWO)
    last = assert_refused_for_cost(result, calls)
    assert last["provider_reported_cost_usd"] == 0.25


def test_non_string_content_with_positive_cost_on_sidestep_is_a_violation(monkeypatch):
    paid = body_with({"content": [{"type": "text", "text": "pong"}]}, {"prompt_tokens": 2, "completion_tokens": 1, "cost": 0.25})
    result, calls, _ = run(monkeypatch, lambda provider, n: (402, 0) if provider == "openrouter" else (200, None, paid),
                           policy_edit=head_order)
    assert result["ok"] is False
    assert calls == ["openrouter", "groq"]
    last = extras(result)[-1]
    assert last["sidestep"] is True
    assert last["free_only_violation"] == "provider_reported_nonzero_cost"
    assert last["provider_reported_cost_usd"] == 0.25


def test_caller_free_only_non_string_content_with_positive_cost_is_a_violation(monkeypatch):
    def edit(policy):
        policy["free_only"] = False

    paid = body_with({"content": 7}, {"prompt_tokens": 2, "completion_tokens": 1, "cost": 0.25})
    result, calls, _ = run(monkeypatch, first_then_free(paid), candidates=TWO, policy_edit=edit, args_extra={"free_only": True})
    assert_refused_for_cost(result, calls)


# Held before and still holds: a shape failure that reports no money spent is an ordinary
# failed attempt, so the next plan candidate is still tried.
@pytest.mark.parametrize("usage", [
    {"prompt_tokens": 2, "completion_tokens": 1, "cost": 0},
    {"prompt_tokens": 2, "completion_tokens": 1},
])
def test_non_string_content_without_reported_cost_is_not_a_violation(monkeypatch, usage):
    result, calls, _ = run(monkeypatch, first_then_free(body_with({"content": [{"type": "text", "text": "pong"}]}, usage)), candidates=TWO)
    assert result["ok"] is True
    assert calls == ["openrouter", "groq"]
    first = extras(result)[0]
    assert first["status"] == "failed"
    assert "free_only_violation" not in first


# (R2) under free-only a present cost is acceptable only when it is exactly a finite zero.
def raw_cost(literal):
    """A 200 body with the cost written as a raw JSON literal (NaN and Infinity included)."""
    def build(request):
        return ('{"model": %s, "choices": [{"message": {"content": "pong"}, "finish_reason": "stop"}], '
                '"usage": {"prompt_tokens": 2, "completion_tokens": 1, "cost": %s}}' % (json.dumps(request["model"]), literal)).encode()
    return build


BAD_COSTS = {
    "negative_float": ("-0.01", -0.01),
    "negative_int": ("-1", -1),
    "tiny_positive": ("1e-12", 1e-12),
    "positive_int": ("3", 3),
    "huge_int": ("1" + "0" * 400, 10 ** 400),          # too large for a float: must not raise
    "huge_negative_int": ("-1" + "0" * 400, -10 ** 400),
    "string_zero": ('"0"', "unreadable:str"),
    "string_zero_decimal": ('"0.00"', "unreadable:str"),
    "string_word": ('"free"', "unreadable:str"),
    "empty_string": ('""', "unreadable:str"),
    "empty_list": ("[]", "unreadable:list"),
    "list_of_zero": ("[0]", "unreadable:list"),
    "empty_object": ("{}", "unreadable:dict"),
    "object": ('{"usd": 0}', "unreadable:dict"),
    "null": ("null", "unreadable:NoneType"),
    "true": ("true", "unreadable:bool"),
    "false": ("false", "unreadable:bool"),
    "nan": ("NaN", "unreadable:nan"),
    "infinity": ("Infinity", "unreadable:inf"),
    "negative_infinity": ("-Infinity", "unreadable:-inf"),
    "overflowing_float": ("1e999", "unreadable:inf"),
}


@pytest.mark.parametrize("name", sorted(BAD_COSTS))
def test_free_only_refuses_any_present_cost_that_is_not_a_finite_zero(monkeypatch, name):
    literal, recorded = BAD_COSTS[name]
    result, calls, _ = run(monkeypatch, first_then_free(raw_cost(literal)), candidates=TWO)
    last = assert_refused_for_cost(result, calls)
    assert last["provider_reported_cost_usd"] == recorded
    assert type(last["provider_reported_cost_usd"]) is type(recorded)
    # The receipt on disk stays strict JSON even when the provider sent NaN or Infinity.
    json.dumps(last, allow_nan=False)


@pytest.mark.parametrize("literal", ["0", "0.0", "-0.0", "0e0", "0.000"])
def test_free_only_accepts_an_exact_finite_zero_cost(monkeypatch, literal):
    result, calls, _ = run(monkeypatch, first_then_free(raw_cost(literal)), candidates=TWO)
    assert result["ok"] is True
    assert calls == ["openrouter"]
    last = extras(result)[-1]
    assert last["status"] == "completed"
    assert last["provider_reported_cost_usd"] == 0
    assert "free_only_violation" not in last


# Stated explicitly: an ABSENT cost field is not a violation. This is the behaviour at
# ddf5a0c and it is left unchanged; only a cost field that is present is judged.
def test_absent_cost_field_is_not_a_violation_under_free_only(monkeypatch):
    no_cost = body_with({"content": "pong"}, {"prompt_tokens": 2, "completion_tokens": 1})
    result, calls, _ = run(monkeypatch, first_then_free(no_cost), candidates=TWO)
    assert result["ok"] is True
    assert calls == ["openrouter"]
    last = extras(result)[-1]
    assert last["status"] == "completed"
    assert last["provider_reported_cost_usd"] is None
    assert "free_only_violation" not in last


@pytest.mark.parametrize("usage_literal", ["null", "{}", None])
def test_absent_usage_is_not_a_violation_but_is_not_a_success_either(monkeypatch, usage_literal):
    def build(request):
        usage = "" if usage_literal is None else ', "usage": ' + usage_literal
        return ('{"model": %s, "choices": [{"message": {"content": "pong"}, "finish_reason": "stop"}]%s}'
                % (json.dumps(request["model"]), usage)).encode()

    result, calls, _ = run(monkeypatch, first_then_free(build), candidates=TWO)
    first = extras(result)[0]
    assert first["status"] == "completed_unmetered_or_unidentified"
    assert first["provider_reported_cost_usd"] is None
    assert "free_only_violation" not in first
    assert calls == ["openrouter", "groq"]
    assert result["ok"] is True


# Without free-only (manifest off, caller silent) a reported cost is recorded, never judged.
@pytest.mark.parametrize("literal,recorded", [("0.5", 0.5), ("-1", -1), ("null", "unreadable:NoneType"), ("NaN", "unreadable:nan")])
def test_reported_cost_is_not_judged_without_free_only(monkeypatch, literal, recorded):
    def edit(policy):
        policy["free_only"] = False

    result, calls, _ = run(monkeypatch, first_then_free(raw_cost(literal)), candidates=TWO, policy_edit=edit)
    assert result["ok"] is True
    assert result["free_only"] is False
    assert calls == ["openrouter"]
    last = extras(result)[-1]
    assert last["provider_reported_cost_usd"] == recorded
    assert "free_only_violation" not in last


# (R3a) execute_plan strips a plan's own sidestep flag even when nothing is free-only.
# nous has an unmeasured cap: only a real sidestep (402/429 in this call) may be admitted on it.
@pytest.mark.parametrize("flag", [{}, {"sidestep": True}])
def test_plan_sidestep_flag_is_not_an_admission_ticket_on_a_non_free_manifest(monkeypatch, flag):
    def edit(policy):
        policy["free_only"] = False

    nous = "inclusionai/ling-3.0-flash-sante:free"
    assert wr.configuration()[0]["provider_caps"]["nous"] is None
    assert wr.free_route(wr.configuration()[0], "nous", nous) is None
    result, calls, _ = run(monkeypatch, lambda provider, n: (200, 0), policy_edit=edit,
                           candidates=[{"provider": "nous", "model": nous, **flag}])
    assert result["free_only"] is False
    assert calls == [], "a flagged plan candidate was admitted on an unmeasured cap"
    assert result["ok"] is False
    only = extras(result)
    assert len(only) == 1
    assert only[0]["sidestep"] is False
    assert only[0]["status"] == "rejected"
    assert only[0]["admission_reason"] == "unmeasured_cap"
    assert only[0]["physical_call_attempted"] is False
    permits = [row for row in saturation._rows() if row["provider"] == "nous"]
    assert [row["extra"]["reason"] for row in permits] == ["unmeasured_cap"]
    assert not [row for row in permits if row["extra"].get("sidestep")]


# The flag is honoured only when this call itself saw the 402/429 (control for the test above).
def test_real_sidestep_is_still_admitted_on_an_unmeasured_cap_on_a_non_free_manifest(monkeypatch):
    nous = "inclusionai/ling-3.0-flash-sante:free"

    def edit(policy):
        policy["free_only"] = False
        policy["sidestep_order"] = [{"provider": "nous", "model": nous}]

    result, calls, _ = run(monkeypatch, lambda provider, n: (402, 0) if provider == "openrouter" else (200, 0), policy_edit=edit)
    assert calls == ["openrouter", "nous"]
    assert result["ok"] is True
    assert extras(result)[-1]["sidestep"] is True


# (R3b) grok and codex are blocked by the built-in set, not only by the manifest list.
@pytest.mark.parametrize("listed", [None, [], ["cursor"]])
@pytest.mark.parametrize("provider", ["grok", "codex"])
def test_grok_and_codex_are_blocked_without_the_manifest_list(monkeypatch, provider, listed):
    real = wr.configuration

    def without_list():
        policy, providers = real()
        policy.pop("sidestep_blocked_providers", None)
        if listed is not None:
            policy["sidestep_blocked_providers"] = list(listed)
        return policy, providers

    monkeypatch.setattr(wr, "configuration", without_list)
    assert provider not in (wr.configuration()[0].get("sidestep_blocked_providers") or [])
    assert wr.blocked_provider(provider)
    assert wr.blocked_provider(provider.upper())
    assert not wr.blocked_provider("groq")
    assert not wr.blocked_provider("openrouter")


def test_grok_is_never_called_as_sidestep_or_candidate_without_the_manifest_list(monkeypatch):
    real = wr.configuration

    def without_list():
        policy, providers = real()
        policy.pop("sidestep_blocked_providers", None)
        return policy, providers

    monkeypatch.setattr(wr, "configuration", without_list)

    def edit(policy):
        policy["free_only"] = False
        policy["sidestep_order"] = [{"provider": "grok", "model": "grok-4.20-0309-non-reasoning"}]

    result, calls, _ = run(monkeypatch, lambda provider, n: (402, 0) if n == 1 else (200, 0), policy_edit=edit,
                           candidates=[{"provider": "grok", "model": "grok-4.20-0309-non-reasoning"},
                                       {"provider": "openrouter", "model": OPENROUTER}])
    assert result["ok"] is False
    assert calls == ["openrouter"]
