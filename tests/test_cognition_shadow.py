"""The observe-only shadow lane: safety boundary tests (oh-my-pi#83).

These tests deliberately use fake registries and fake backends so they prove the
boundary without a GPU, a model server or the network.
"""

from __future__ import annotations

import json
import time
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from z0int.cognition.actions import compile_actions
from z0int.cognition.adapters.transport import ChatOutcome, ServerConfig
from z0int.cognition.shadow import (
    SCHEMA,
    ShadowPayloadError,
    build_shadow_plan,
    run_shadow,
    shadow_receipt_path,
)


# --- fixtures / fakes ---------------------------------------------------


def _payload(**over):
    payload = {
        "op": "cognition_shadow",
        "trace_id": "trace-1",
        "session_id": "session-1",
        "state": "The agent is about to inspect a file and search it.",
        "actions": [
            {
                "action_id": "read",
                "kind": "tool",
                "description": "Read a file",
                "tool": "read",
                "family": "omp.tool",
                "risk_class": "read",
                "cost_units": 1,
                "arguments_schema": {"type": "object"},
            },
            {
                "action_id": "grep",
                "kind": "tool",
                "description": "Search files",
                "tool": "grep",
                "family": "omp.tool",
                "risk_class": "read",
                "cost_units": 1,
            },
            {
                "action_id": "write",
                "kind": "tool",
                "description": "Write a file",
                "tool": "write",
                "family": "omp.tool",
                "risk_class": "write",
                "cost_units": 1,
            },
        ],
        "granted_capabilities": ["read", "grep", "write"],
        "authority": ["read"],
        "budget_units": 8,
        "satisfied": [],
        "facts": {"tool_call_id": "call-fixture"},
        "objective": None,
        "goal_context": {
            "latest_user_message": "Inspect the file and locate the relevant code.",
            "last_assistant_sentence": "I will inspect the file.",
            "previous_tools": [],
            "sources": {"latest_user_message": "sessionManager.getBranch",
                        "last_assistant_sentence": "message_update"},
        },
        "risk_class": "read",
        "shadows": ["m1"],
    }
    payload.update(over)
    return payload


class _FakeBackend:
    """An existing transport that supplies real binary-response logprob shapes."""

    def __init__(
        self,
        action=None,
        *,
        backend_id="fake-backend",
        model="fake-model",
        raises=None,
        invalid_call=False,
        abstained=None,
    ):
        self._action = action
        self._id = backend_id
        self._model = model
        self._raises = raises
        self._invalid = invalid_call
        self._abstained = abstained
        self.seen_legal: list[tuple[str, ...]] = []
        self.config = ServerConfig("http://fixture", model)
        self._transport = self

    @property
    def backend_id(self):
        return self._id

    def health(self, *, load=False):
        return {"ready": True, "backend": self._id}

    def chat(self, messages, **kwargs):
        candidate = json.loads(messages[1]["content"])["candidate"]["id"]
        self.seen_legal.append((candidate,))
        assert kwargs["max_tokens"] == 1 and kwargs["logprobs"] is True
        assert kwargs["top_logprobs"] == 20
        if self._raises is not None:
            raise self._raises
        positive = candidate == self._action and not self._abstained
        yes, no = (-0.2, -2.0) if positive else (-2.0, -0.2)
        token, value = ("yes", yes) if positive else ("no", no)
        raw = {"choices": [{"logprobs": {"content": [{
            "token": token, "logprob": value, "top_logprobs": [
                {"token": "yes", "logprob": yes}, {"token": "no", "logprob": no},
            ],
        }]}}]}
        return ChatOutcome(
            content="write" if self._invalid else token, tool_call=None,
            usage={"prompt_tokens": 100, "completion_tokens": 1}, timings={},
            raw=raw, latency_ms=3.0,
        )


class _FakeRegistry:
    """Minimal LocalModelRegistry surface used by the shadow lane."""

    def __init__(self, *, backends=None, served=("m1",)):
        self._backends = dict(backends or {"m1": _FakeBackend(action="read")})
        self._served = tuple(served)

    def served(self):
        return self._served

    def backend_for(self, model_id):
        if model_id not in self._served or model_id not in self._backends:
            raise RuntimeError(f"{model_id} is not served")
        return self._backends[model_id]


# --- build_shadow_plan --------------------------------------------------


def test_build_shadow_plan_accepts_a_valid_payload_and_compiles_the_legal_set():
    context, specs = build_shadow_plan(_payload())

    assert [s.label for s in specs] == ["m1"]
    assert specs[0].model_id == "m1"
    assert context.authority == ("read",)
    assert context.budget_units == 8

    legal = compile_actions(
        graph=context.graph,
        granted_capabilities=context.granted_capabilities,
        authority=context.authority,
        budget_units=context.budget_units,
        facts=context.facts,
        satisfied=context.satisfied,
    )
    # `write` is compiled out by the read-only authority; the two reads survive.
    assert set(legal.ids()) == {"read", "grep"}
    assert "write" not in legal.ids()
    assert any(e.action_id == "write" and e.stage == "permission" for e in legal.eliminated)


def test_build_shadow_plan_defaults_authority_to_read_only():
    payload = _payload()
    payload.pop("authority")
    context, _ = build_shadow_plan(payload)
    assert context.authority == ("read",)


def test_unknown_extra_payload_keys_are_ignored_not_fatal():
    payload = _payload()
    payload["future_field"] = {"anything": [1, 2, 3]}
    payload["actions"][0]["mystery_flag"] = True
    payload["shadows"] = [{"model_id": "m1", "label": "orch", "future": "x"}]

    result = run_shadow(payload, registry=_FakeRegistry(), write=False)

    assert result["ok"] is True
    assert result["legal_ids"] == ["grep", "read"]
    assert result["shadow"][0]["label"] == "goal-binary"
    assert result["shadow"][0]["requested_model"] == "m1"


# --- malformed payloads -------------------------------------------------


def test_malformed_action_row_is_rejected_with_a_reason_not_an_exception():
    result = run_shadow(
        _payload(actions=[{"kind": "tool", "description": "no id here"}]),
        registry=_FakeRegistry(),
        write=False,
    )
    assert result["ok"] is False
    assert result["schema"] == SCHEMA
    assert "actions[0].action_id" in result["reason"]

    with pytest.raises(ShadowPayloadError) as excinfo:
        build_shadow_plan(_payload(actions=[{"kind": "tool"}]))
    assert "action_id" in str(excinfo.value)


@pytest.mark.parametrize(
    "row,needle",
    [
        ({"action_id": "x", "kind": "teleport"}, "kind"),
        ({"action_id": "x", "kind": "tool", "tool": "x", "risk_class": "chaos"}, "risk_class"),
        ({"action_id": "x", "kind": "tool", "tool": "x", "cost_units": -1}, "cost_units"),
        ({"action_id": "x", "kind": "tool", "tool": "x", "requires": "y"}, "requires"),
        ({"action_id": "x", "kind": "tool", "tool": 5}, "tool"),
    ],
)
def test_bad_known_fields_carry_a_precise_reason(row, needle):
    result = run_shadow(_payload(actions=[row]), registry=_FakeRegistry(), write=False)
    assert result["ok"] is False
    assert needle in result["reason"]


def test_non_object_payload_fails_open():
    result = run_shadow(["not", "an", "object"], registry=_FakeRegistry(), write=False)
    assert result == {"ok": False, "schema": SCHEMA, "reason": "payload must be an object"}


# --- shadow backends ----------------------------------------------------


def test_shadow_backend_only_ever_sees_the_legal_set():
    backend = _FakeBackend(action="read")
    registry = _FakeRegistry(backends={"m1": backend})

    run_shadow(_payload(shadows=["m1"]), registry=registry, write=False)

    assert len(backend.seen_legal) == 2
    assert {tool for call in backend.seen_legal for tool in call} == {"read", "grep"}
    assert all("write" not in call for call in backend.seen_legal)


def test_binary_backend_cannot_advertise_or_select_an_illegal_action():
    # A preference for a filtered tool cannot add it to single-candidate RPCs.
    backend = _FakeBackend(action="write")
    registry = _FakeRegistry(backends={"m1": backend})

    result = run_shadow(_payload(shadows=["m1"]), registry=registry, write=False)

    assert result["ok"] is True
    (row,) = result["shadow"]
    assert row["invalid_call"] is False
    assert row["selected_action"] is None
    assert row["abstained"] is True
    assert "write" not in row["model_candidate_ids"]
    assert row["diagnostics"]["abstention_reason"] == "no_positive_candidate"


def test_non_binary_backend_output_is_a_protocol_parse_failure():
    backend = _FakeBackend(action=None, invalid_call=True)
    registry = _FakeRegistry(backends={"m1": backend})

    result = run_shadow(_payload(shadows=["m1"]), registry=registry, write=False)

    (row,) = result["shadow"]
    assert row["invalid_call"] is False
    assert row["selected_action"] is None
    assert row["parse_error"] == "non_binary_response"
    assert row["error"] is None


def test_unreachable_backend_fails_open_with_a_partial_shadow_list():
    good = _FakeBackend(action="grep", backend_id="good")
    broken = _FakeBackend(raises=RuntimeError("connection refused"), backend_id="broken")
    registry = _FakeRegistry(backends={"m1": good, "m2": broken}, served=("m1", "m2"))

    result = run_shadow(_payload(shadows=["m1", "m2"]), registry=registry, write=False)

    assert result["ok"] is True
    assert set(result["legal_ids"]) == {"read", "grep"}
    by_model = {row["requested_model"]: row for row in result["shadow"]}
    assert all(row["label"] == "goal-binary" for row in result["shadow"])
    assert by_model["m1"]["selected_action"] in {"read", "grep"}
    assert by_model["m2"]["selected_action"] is None
    assert by_model["m2"]["abstained"] is True
    assert "connection refused" in by_model["m2"]["error"]
    assert by_model["m2"].get("parse_error") is None


def test_missing_model_is_recorded_and_does_not_fail_the_op():
    registry = _FakeRegistry(served=("m1",))  # m2 requested but not served

    result = run_shadow(_payload(shadows=["m1", "m2"]), registry=registry, write=False)

    assert result["ok"] is True
    by_model = {row["requested_model"]: row for row in result["shadow"]}
    assert by_model["m2"]["selected_action"] is None
    assert "not served" in by_model["m2"]["error"]
    assert by_model["m2"]["failure_class"] == "model_not_served"


def test_no_models_served_still_returns_the_compiled_legal_set(tmp_path, monkeypatch):
    monkeypatch.setenv("Z0INT_HOME", str(tmp_path))
    registry = _FakeRegistry(served=())

    result = run_shadow(_payload(shadows=["m1"]), registry=registry, write=True)

    assert result["ok"] is True
    assert result["shadow"] == []
    assert set(result["legal_ids"]) == {"read", "grep"}
    assert result["graph_digest"]
    assert Path(result["receipts_path"]).is_file()


def test_no_serving_config_yields_empty_shadow_list(tmp_path, monkeypatch):
    # registry=None -> LocalModelRegistry.from_environment() against an empty HOME.
    monkeypatch.setenv("Z0INT_HOME", str(tmp_path))
    monkeypatch.delenv("Z0INT_LOCAL_BASE_URL", raising=False)
    monkeypatch.delenv("Z0INT_LOCAL_MODEL", raising=False)

    result = run_shadow(_payload(shadows=["m1"]), write=True)

    assert result["ok"] is True
    assert result["shadow"] == []
    assert set(result["legal_ids"]) == {"read", "grep"}


def test_empty_action_set_compiles_to_an_empty_legal_set():
    result = run_shadow(_payload(actions=[], shadows=["m1"]), registry=_FakeRegistry(), write=False)
    assert result["ok"] is True
    assert result["legal_ids"] == []
    assert result["candidate_action_count"] == 0
    assert result["shadow"] == []


@pytest.mark.parametrize("same_server", [True, False])
def test_challengers_share_one_server_without_interleaving(same_server):
    entered = threading.Event()
    release = threading.Event()
    other_entered = threading.Event()
    calls = []

    class First(_FakeBackend):
        def chat(self, messages, **kwargs):
            calls.append("first")
            entered.set()
            assert release.wait(3)
            return super().chat(messages, **kwargs)

    class Second(_FakeBackend):
        def chat(self, messages, **kwargs):
            other_entered.set()
            calls.append("second")
            return super().chat(messages, **kwargs)

    first, second = First(action="read"), Second(action="grep")
    first.config = ServerConfig("http://serialized-fixture", "m1")
    second.config = ServerConfig(
        "http://serialized-fixture/" if same_server else "http://independent-fixture", "m2")
    registry = _FakeRegistry(backends={"m1": first, "m2": second}, served=("m1", "m2"))
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(run_shadow, _payload(shadows=["m1", "m2"]),
                             registry=registry, write=False, timeout_s=2)
        try:
            assert entered.wait(2)
            overlapped = other_entered.wait(.1 if same_server else 2)
        finally:
            release.set()
        rows = future.result(timeout=3)["shadow"]
    assert overlapped is (not same_server)
    assert [row["selected_action"] for row in rows] == ["read", "grep"]
    if same_server:
        assert calls == ["first", "first", "second", "second"]


def test_expired_challenger_never_starts_after_inflight_arm_finishes():
    entered = threading.Event()
    release = threading.Event()
    completed = threading.Event()

    class First(_FakeBackend):
        def chat(self, messages, **kwargs):
            entered.set()
            assert release.wait(3)
            result = super().chat(messages, **kwargs)
            if len(self.seen_legal) == 2:
                completed.set()
            return result

    first, second = First(action="read"), _FakeBackend(action="grep")
    first.config = ServerConfig("http://deadline-fixture", "m1")
    second.config = ServerConfig("http://deadline-fixture", "m2")
    registry = _FakeRegistry(backends={"m1": first, "m2": second}, served=("m1", "m2"))
    try:
        rows = run_shadow(_payload(shadows=["m1", "m2"]), registry=registry,
                          write=False, timeout_s=.1)["shadow"]
        assert entered.is_set()
    finally:
        release.set()
    assert completed.wait(2)
    assert second.seen_legal == []
    assert [row["requested_model"] for row in rows] == ["m1", "m2"]
    assert all(row["error"] == "shadow_timeout" for row in rows)


def test_timed_out_arm_keeps_server_exclusive_across_shadow_calls():
    release = threading.Event()
    completed = threading.Event()

    class First(_FakeBackend):
        def chat(self, messages, **kwargs):
            assert release.wait(3)
            result = super().chat(messages, **kwargs)
            if len(self.seen_legal) == 2:
                completed.set()
            return result

    first, second = First(action="read"), _FakeBackend(action="grep")
    first.config = ServerConfig("http://cross-call-fixture", "m1")
    second.config = ServerConfig("http://cross-call-fixture", "m2")
    try:
        initial = run_shadow(_payload(shadows=["m1"]),
            registry=_FakeRegistry(backends={"m1": first}), write=False, timeout_s=.1)
        later = run_shadow(_payload(shadows=["m2"]),
            registry=_FakeRegistry(backends={"m2": second}, served=("m2",)),
            write=False, timeout_s=.1)
    finally:
        release.set()
    assert completed.wait(2)
    assert second.seen_legal == []
    assert initial["shadow"][0]["error"] == "shadow_timeout"
    assert later["shadow"][0]["error"] == "shadow_timeout"
    recovered = run_shadow(_payload(shadows=["m2"]),
        registry=_FakeRegistry(backends={"m2": second}, served=("m2",)),
        write=False, timeout_s=2)
    assert recovered["shadow"][0]["selected_action"] == "grep"


# --- receipts -----------------------------------------------------------


def test_receipt_always_has_null_selected_and_executed_action(tmp_path, monkeypatch):
    monkeypatch.setenv("Z0INT_HOME", str(tmp_path))

    result = run_shadow(_payload(shadows=["m1"]), registry=_FakeRegistry(), write=True)

    assert result["selected_action"] is None
    assert result["executed_action"] is None
    assert result["receipts_path"] == str(tmp_path / "shadow" / "cognition-shadow.jsonl")

    rows = [json.loads(line) for line in Path(result["receipts_path"]).read_text().splitlines()]
    assert len(rows) == 1
    receipt = rows[0]
    assert receipt["selected_action"] is None
    assert receipt["executed_action"] is None
    assert "verified_success" not in receipt
    assert receipt["trace_id"] == "trace-1"
    assert receipt["session_id"] == "session-1"
    for key in ("legal_ids", "eliminated", "escalation", "graph_digest", "candidate_action_count"):
        assert key in receipt
    # Every shadow candidate is in the receipt, not just the winner.
    assert [row["label"] for row in receipt["shadow"]] == ["goal-binary"]
    assert [row["requested_model"] for row in receipt["shadow"]] == ["m1"]


def test_receipt_is_never_written_when_write_is_false(tmp_path, monkeypatch):
    monkeypatch.setenv("Z0INT_HOME", str(tmp_path))

    result = run_shadow(_payload(shadows=["m1"]), registry=_FakeRegistry(), write=False)

    assert result["receipts_path"] is None
    assert not (tmp_path / "shadow" / "cognition-shadow.jsonl").exists()


def test_shadow_lane_never_writes_outside_z0int_home(tmp_path, monkeypatch):
    home = tmp_path / "z0int-home"
    monkeypatch.setenv("Z0INT_HOME", str(home))

    result = run_shadow(_payload(shadows=["m1"]), registry=_FakeRegistry(), write=True)

    assert result["ok"] is True
    receipt = Path(result["receipts_path"])
    assert receipt == home / "shadow" / "cognition-shadow.jsonl"
    assert receipt.is_file()
    assert shadow_receipt_path() == receipt

    inside = {p.resolve() for p in home.rglob("*") if p.is_file()}
    everything = {p.resolve() for p in tmp_path.rglob("*") if p.is_file()}
    assert everything - inside == set()


def test_receipt_carries_the_harness_tool_call_identity(tmp_path, monkeypatch):
    monkeypatch.setenv("Z0INT_HOME", str(tmp_path))
    payload = _payload(shadows=["m1"])
    payload["facts"] = {"tool_name": "read", "tool_call_id": "call-42"}

    result = run_shadow(payload, registry=_FakeRegistry(), write=True)

    receipt = json.loads(Path(result["receipts_path"]).read_text().splitlines()[0])
    # A random trace id alone cannot be joined back to the native session's tool call.
    assert receipt["tool_call_id"] == "call-42"
    assert receipt["tool_name"] == "read"
    assert receipt["session_id"] == "session-1"

    without = _payload(shadows=["m1"])
    without.pop("facts", None)
    result = run_shadow(without, registry=_FakeRegistry(), write=True)
    receipt = json.loads(Path(result["receipts_path"]).read_text().splitlines()[-1])
    assert receipt["tool_call_id"] is None


class _SlowBackend(_FakeBackend):
    """Answers correctly, but only after ``delay_s`` — a model still loading."""

    def __init__(self, delay_s, **kwargs):
        super().__init__(**kwargs)
        self._delay_s = delay_s

    def chat(self, messages, **kwargs):
        time.sleep(self._delay_s)
        return super().chat(messages, **kwargs)


def test_payload_budget_bounds_a_slow_model_and_records_it_as_a_timeout(tmp_path, monkeypatch):
    monkeypatch.setenv("Z0INT_HOME", str(tmp_path))
    registry = _FakeRegistry(backends={"m1": _SlowBackend(0.6, action="read")})
    payload = _payload(shadows=["m1"])
    payload["timeout_ms"] = 150

    started = time.monotonic()
    result = run_shadow(payload, registry=registry, write=True)
    elapsed = time.monotonic() - started

    # The caller's budget wins over the 30 s default: the op returns inside it.
    assert elapsed < 0.5
    row = result["shadow"][0]
    assert row["error"] == "shadow_timeout"
    assert row["backend"] is None
    assert row["selected_action"] is None
    receipt = json.loads(Path(result["receipts_path"]).read_text().splitlines()[0])
    assert receipt["shadow"][0]["error"] == "shadow_timeout"


def test_a_model_inside_the_budget_is_recorded_as_having_answered(tmp_path, monkeypatch):
    monkeypatch.setenv("Z0INT_HOME", str(tmp_path))
    registry = _FakeRegistry(backends={"m1": _SlowBackend(0.05, action="read")})
    payload = _payload(shadows=["m1"])
    payload["timeout_ms"] = 2000

    row = run_shadow(payload, registry=registry, write=False)["shadow"][0]

    assert row["selected_action"] == "read"
    assert row["backend"] == "fake-backend"


def test_a_timeout_row_records_how_long_the_worker_waited(tmp_path, monkeypatch):
    monkeypatch.setenv("Z0INT_HOME", str(tmp_path))
    registry = _FakeRegistry(backends={"m1": _SlowBackend(0.6, action="read")})
    payload = _payload(shadows=["m1"])
    payload["timeout_ms"] = 150

    row = run_shadow(payload, registry=registry, write=False)["shadow"][0]

    # Timeout latency describes the shared budget, not an inference duration.
    assert row["error"] == "shadow_timeout"
    assert row["timed_out"] is True
    assert 140 <= row["latency_ms"] < 500
    assert 140 <= row["waited_ms"] < 500


def test_a_failure_that_is_not_a_timeout_reports_no_wait(tmp_path, monkeypatch):
    monkeypatch.setenv("Z0INT_HOME", str(tmp_path))
    registry = _FakeRegistry(
        backends={"m1": _FakeBackend(raises=ConnectionError("refused"))}
    )
    payload = _payload(shadows=["m1", "absent"])
    payload["timeout_ms"] = 2000

    rows = run_shadow(payload, registry=registry, write=False)["shadow"]

    for row in rows:
        assert "timed_out" not in row
        assert "waited_ms" not in row


class _ColdThenWarmBackend(_FakeBackend):
    """The first call pays a model load; later calls find the model resident."""

    def __init__(self, load_s, **kwargs):
        super().__init__(**kwargs)
        self._load_s = load_s
        self._loaded = False
        self.calls = 0

    def chat(self, messages, **kwargs):
        self.calls += 1
        if not self._loaded:
            time.sleep(self._load_s)
            self._loaded = True
        return super().chat(messages, **kwargs)


def test_a_cold_load_timeout_still_leaves_the_model_warm_for_the_next_call(tmp_path, monkeypatch):
    monkeypatch.setenv("Z0INT_HOME", str(tmp_path))
    backend = _ColdThenWarmBackend(0.4, action="read")
    registry = _FakeRegistry(backends={"m1": backend})
    payload = _payload(shadows=["m1"])
    payload["timeout_ms"] = 150

    cold = run_shadow(payload, registry=registry, write=False)["shadow"][0]
    assert cold["error"] == "shadow_timeout"
    assert cold["timed_out"] is True

    # The abandoned call is not cancelled: it finishes the load in the background.
    deadline = time.monotonic() + 2.0
    while not backend._loaded and time.monotonic() < deadline:
        time.sleep(0.01)
    assert backend._loaded

    warm = run_shadow(payload, registry=registry, write=False)["shadow"][0]
    assert warm["selected_action"] == "read"
    assert "timed_out" not in warm
    assert backend.calls == 4  # Two candidates per observation, using one warm transport.


class _RaisesLate(_FakeBackend):
    """Fails on its own, a little after the caller's budget has run out."""

    def __init__(self, delay_s, **kwargs):
        super().__init__(**kwargs)
        self._delay_s = delay_s

    def chat(self, messages, **kwargs):
        time.sleep(self._delay_s)
        raise ConnectionError("late refused")


def test_a_backend_error_that_lands_just_after_the_deadline_is_not_also_called_a_timeout(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("Z0INT_HOME", str(tmp_path))
    registry = _FakeRegistry(backends={"m1": _RaisesLate(0.2)})
    payload = _payload(shadows=["m1"])
    payload["timeout_ms"] = 100

    row = run_shadow(payload, registry=registry, write=False)["shadow"][0]

    # One story per row: either it timed out, or it failed with its own error.
    if row["error"] == "shadow_timeout":
        assert row["timed_out"] is True
    else:
        assert "late refused" in row["error"]
        assert "timed_out" not in row
        assert "waited_ms" not in row


@pytest.mark.parametrize("budget", [1e18, 1e308, float("inf"), 10**400])
def test_an_absurd_budget_is_capped_instead_of_raising(tmp_path, monkeypatch, budget):
    monkeypatch.setenv("Z0INT_HOME", str(tmp_path))
    registry = _FakeRegistry(backends={"m1": _FakeBackend(action="read")})
    payload = _payload(shadows=["m1"])
    payload["timeout_ms"] = budget

    row = run_shadow(payload, registry=registry, write=False)["shadow"][0]

    assert row["selected_action"] == "read"


def test_an_absurd_budget_from_the_environment_is_capped_too(tmp_path, monkeypatch):
    monkeypatch.setenv("Z0INT_HOME", str(tmp_path))
    monkeypatch.setenv("Z0INT_COGNITION_SHADOW_SERVER_TIMEOUT_MS", "inf")
    registry = _FakeRegistry(backends={"m1": _FakeBackend(action="read")})

    row = run_shadow(_payload(shadows=["m1"]), registry=registry, write=False)["shadow"][0]

    assert row["selected_action"] == "read"


@pytest.mark.parametrize("value", ["0", "-0.0", "-1", "-5", "nan", "-inf", "5e-324"])
def test_a_non_positive_environment_budget_still_means_the_shortest_wait(monkeypatch, value):
    from z0int.cognition import shadow

    monkeypatch.setenv("Z0INT_COGNITION_SHADOW_SERVER_TIMEOUT_MS", value)

    assert shadow._timeout_s({}, None) == 0.1


def test_the_cap_changes_no_ordinary_budget(monkeypatch):
    from z0int.cognition import shadow

    monkeypatch.delenv("Z0INT_COGNITION_SHADOW_SERVER_TIMEOUT_MS", raising=False)
    default = shadow.DEFAULT_SERVER_TIMEOUT_MS / 1000.0
    for raw in (0, -5, float("nan"), True, "150", None):
        assert shadow._timeout_s({"timeout_ms": raw}, None) == default
    assert shadow._timeout_s({"timeout_ms": 5e-324}, None) == 0.1
    assert shadow._timeout_s({"timeout_ms": 1}, None) == 0.1
    assert shadow._timeout_s({"timeout_ms": 150}, None) == 0.15
    assert shadow._timeout_s({}, 0) == 0.1
    assert shadow._timeout_s({}, 2) == 2.0
    with pytest.raises(ValueError):
        shadow._timeout_s({}, "abc")
    assert shadow._timeout_s({"timeout_ms": 7_200_000}, None) == shadow.MAX_SERVER_TIMEOUT_S


def test_persisted_observation_keeps_identity_without_prompt_or_tool_arguments(tmp_path, monkeypatch):
    monkeypatch.setenv("Z0INT_HOME", str(tmp_path))
    payload = _payload(shadows=["m1"])
    payload["state"] = "private visible context"
    payload["facts"] = {"tool_name": "read", "tool_call_id": "call-private"}
    payload["actual_tool"] = {"name": "read", "risk_class": "read", "input": "private argument"}
    result = run_shadow(payload, registry=_FakeRegistry(), write=True)
    receipt = json.loads(Path(result["receipts_path"]).read_text().splitlines()[-1])
    assert receipt["actual_tool"] == {"name": "read", "risk_class": "read"}
    assert receipt["tool_name"] == "read"
    assert receipt["tool_call_id"] == "call-private"
    assert "state" not in receipt


def test_absent_scoped_candidate_preserves_goal_binary_shadow(tmp_path, monkeypatch):
    monkeypatch.setenv("Z0INT_HOME", str(tmp_path))
    backend = _FakeBackend(action="read")
    result = run_shadow(_payload(), registry=_FakeRegistry(backends={"m1": backend}), write=False)
    assert result["ok"] is True
    assert result["shadow"][0]["selected_action"] == "read"
    assert {value for call in backend.seen_legal for value in call} == {"read", "grep"}
    assert result["selected_action"] is None and result["executed_action"] is None
