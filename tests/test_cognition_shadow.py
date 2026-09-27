"""The observe-only shadow lane: safety boundary tests (oh-my-pi#83).

These tests deliberately use fake registries and fake backends so they prove the
boundary without a GPU, a model server or the network.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from z0int.cognition.actions import compile_actions
from z0int.cognition.adapters.local_slm import ToolDecision
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
                "family": "fs",
                "risk_class": "read",
                "cost_units": 1,
                "arguments_schema": {"type": "object"},
            },
            {
                "action_id": "grep",
                "kind": "tool",
                "description": "Search files",
                "tool": "grep",
                "family": "fs",
                "risk_class": "read",
                "cost_units": 1,
            },
            {
                "action_id": "write",
                "kind": "tool",
                "description": "Write a file",
                "tool": "write",
                "family": "fs",
                "risk_class": "write",
                "cost_units": 1,
            },
        ],
        "granted_capabilities": ["read", "grep", "write"],
        "authority": ["read"],
        "budget_units": 8,
        "satisfied": [],
        "facts": {},
        "objective": None,
        "risk_class": "read",
        "shadows": ["m1"],
    }
    payload.update(over)
    return payload


class _FakeBackend:
    """A ToolDecisionBackend that answers from a script."""

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

    @property
    def backend_id(self):
        return self._id

    def health(self, *, load=False):
        return {"ready": True, "backend": self._id}

    def decide(self, request):
        self.seen_legal.append(request.legal.ids())
        if self._raises is not None:
            raise self._raises
        return ToolDecision(
            backend=self._id,
            model=self._model,
            revision="rev",
            selected_action=self._action,
            arguments={},
            confidence=0.9 if self._action else None,
            distribution=None,
            latency_ms=3.0,
            abstained=self._abstained if self._abstained is not None else self._action is None,
            candidate_action_count=request.legal.candidate_count,
            invalid_call=self._invalid,
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
    assert result["shadow"][0]["label"] == "orch"


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

    assert len(backend.seen_legal) == 1
    assert set(backend.seen_legal[0]) == {"read", "grep"}
    assert "write" not in backend.seen_legal[0]


def test_backend_naming_an_illegal_action_is_invalid_call_with_no_selection():
    # `write` is not in the compiled legal set; the backend names it anyway.
    backend = _FakeBackend(action="write")
    registry = _FakeRegistry(backends={"m1": backend})

    result = run_shadow(_payload(shadows=["m1"]), registry=registry, write=False)

    assert result["ok"] is True
    (row,) = result["shadow"]
    assert row["invalid_call"] is True
    assert row["selected_action"] is None
    assert row["abstained"] is True
    assert row["attempted_action"] == "write"


def test_backend_self_reported_invalid_call_is_preserved():
    backend = _FakeBackend(action=None, invalid_call=True)
    registry = _FakeRegistry(backends={"m1": backend})

    result = run_shadow(_payload(shadows=["m1"]), registry=registry, write=False)

    (row,) = result["shadow"]
    assert row["invalid_call"] is True
    assert row["selected_action"] is None


def test_unreachable_backend_fails_open_with_a_partial_shadow_list():
    good = _FakeBackend(action="grep", backend_id="good")
    broken = _FakeBackend(raises=RuntimeError("connection refused"), backend_id="broken")
    registry = _FakeRegistry(backends={"m1": good, "m2": broken}, served=("m1", "m2"))

    result = run_shadow(_payload(shadows=["m1", "m2"]), registry=registry, write=False)

    assert result["ok"] is True
    assert set(result["legal_ids"]) == {"read", "grep"}
    by_label = {row["label"]: row for row in result["shadow"]}
    assert by_label["m1"]["selected_action"] in {"read", "grep"}
    assert by_label["m2"]["selected_action"] is None
    assert by_label["m2"]["abstained"] is True
    assert "connection refused" in by_label["m2"]["parse_error"]


def test_missing_model_is_recorded_and_does_not_fail_the_op():
    registry = _FakeRegistry(served=("m1",))  # m2 requested but not served

    result = run_shadow(_payload(shadows=["m1", "m2"]), registry=registry, write=False)

    assert result["ok"] is True
    by_label = {row["label"]: row for row in result["shadow"]}
    assert by_label["m2"]["selected_action"] is None
    assert "not served" in by_label["m2"]["parse_error"]


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
    assert [row["label"] for row in receipt["shadow"]] == ["m1"]


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
