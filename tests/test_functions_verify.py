"""Tests for the canonical ``verify.evidence_sufficiency`` function.

Stubbed implementations prove the contract, the policy, the receipt and the
escalation wiring deterministically (no network, no GPU). Live tests are marked
and prove the two real implementations on the real packets.
"""

from __future__ import annotations

import json

import pytest

from z0int.functions import (
    DEFAULT_ALLOW_AT,
    DEFAULT_ESCALATE_BELOW,
    FUNCTION_ID,
    PHRASINGS,
    PROPOSITION,
    BackendCapabilities,
    PhysicalCall,
    VerifierUnavailable,
    action_of,
    capabilities,
    is_uncertain,
    render_state,
    state_digest,
    verify,
    verify_with_escalation,
)
from z0int.functions import verify_evidence_sufficiency as func
from z0int.receipt import validate_receipt


class StubVerifier:
    """A minimal implementation of the contract with a fixed answer."""

    role = "fast_path"

    def __init__(self, backend: str, p_true: float, *, available: bool = True,
                 raises: Exception | None = None, model: str = "stub-1"):
        self.backend = backend
        self.p_true = p_true
        self.available = available
        self.raises = raises
        self.model = model
        self.calls = 0

    def capabilities(self, *, available=None):
        return BackendCapabilities(
            function_id=FUNCTION_ID, backend=self.backend, local=True, role=self.role,
            model=self.model, revision="rev-test",
            available=self.available if available is None else available,
            validated_on=("stub",), validation={"stub": True}, typical_latency_ms=1.0)

    def health(self):
        return self.available, "stub"

    def verify(self, state, *, proposition=PROPOSITION):
        self.calls += 1
        if self.raises:
            raise self.raises
        return self.p_true, {"model": self.model, "revision": "rev-test", "usage": {"input_tokens": 7}}


@pytest.fixture
def stubs(monkeypatch):
    """Replace both implementations with controllable stubs."""
    made: dict[str, StubVerifier] = {}

    def factory(name):
        return lambda: made[name]

    for name in ("laya", "jev"):
        made[name] = StubVerifier(name, 0.5)
        monkeypatch.setitem(func._FACTORIES, name, factory(name))
    return made


PACKET = {
    "query": "which port does the service listen on",
    "evidence_counts": {"snippets": 2, "facts": 1},
    "evidence": [
        {"rank": 0, "source_id": "conversation:a:b#1", "excerpt": "the service binds port 8791"},
        {"rank": 1, "source_id": "conversation:a:c#2", "excerpt": "unrelated note"},
    ],
}


# --- policy -----------------------------------------------------------------

@pytest.mark.parametrize("p,expected", [
    (1.0, "ALLOW"), (0.65, "ALLOW"), (0.6499, "ABSTAIN"),
    (0.5, "ABSTAIN"), (0.3501, "ABSTAIN"), (0.35, "ESCALATE"), (0.0, "ESCALATE"),
])
def test_action_bands(p, expected):
    assert action_of(p) == expected


def test_action_rejects_inverted_thresholds():
    with pytest.raises(ValueError):
        action_of(0.5, allow_at=0.2, escalate_below=0.8)


def test_uncertain_is_the_abstain_band():
    assert is_uncertain(0.5) is True
    assert is_uncertain(0.9) is False
    assert is_uncertain(0.1) is False


# --- state rendering --------------------------------------------------------

def test_render_state_carries_query_once_and_the_evidence():
    text = render_state(PACKET)
    assert text.startswith("Query: which port does the service listen on")
    assert text.count(PACKET["query"]) == 1
    assert "port 8791" in text and "snippets=2" in text


def test_render_state_is_a_formatter_and_omits_a_missing_query():
    """The renderer is the validated formatter; it does not police its input."""
    assert "Query:" not in render_state({"evidence": []})


def test_verify_refuses_a_packet_without_a_query(stubs):
    """The function rejects it: the proposition names 'the user's query'."""
    with pytest.raises(ValueError):
        verify({"evidence": []}, backend="laya")
    with pytest.raises(ValueError):
        verify({"query": "   "}, backend="laya")


def test_state_digest_is_stable():
    assert state_digest("abc") == state_digest("abc")
    assert state_digest("abc") != state_digest("abd")


# --- capabilities -----------------------------------------------------------

def test_capabilities_expose_what_the_router_needs():
    rows = capabilities()
    assert {r["backend"] for r in rows} == {"laya", "jev"}
    for row in rows:
        assert row["function_id"] == FUNCTION_ID
        assert row["role"] in ("fast_path", "reference_escalation", "fallback")
        assert isinstance(row["local"], bool)
        assert isinstance(row["available"], bool)
        assert "validation" in row and "typical_latency_ms" in row
    jev = next(r for r in rows if r["backend"] == "jev")
    assert jev["model"] == "jev-1.13.0" and jev["local"] is False
    laya = next(r for r in rows if r["backend"] == "laya")
    assert laya["local"] is True


def test_phrasings_are_at_least_two_and_include_the_canonical_one():
    assert len(PHRASINGS) >= 2
    assert PHRASINGS[0] == PROPOSITION


# --- verify -----------------------------------------------------------------

def test_verify_returns_a_valid_receipt(stubs):
    stubs["laya"].p_true = 0.9
    r = verify(PACKET, backend="laya", provenance={"source": "z0int.context_resolve"})
    assert r.action == "ALLOW" and r.p_true == pytest.approx(0.9)
    assert r.p_false == pytest.approx(0.1)
    assert validate_receipt(r.receipt) == []
    assert r.receipt["schema"] == "z0int.decision_receipt.v1"
    assert r.receipt["capability_id"] == FUNCTION_ID
    extra = r.receipt["extra"]
    assert extra["function_id"] == FUNCTION_ID
    assert extra["state_packet"]["source"] == "z0int.context_resolve"
    assert extra["proposition"] == PROPOSITION
    assert extra["thresholds"]["allow_at"] == DEFAULT_ALLOW_AT
    assert extra["thresholds"]["escalate_below"] == DEFAULT_ESCALATE_BELOW
    assert extra["state_sha256_16"] == r.state_sha256_16
    assert len(extra["backend_calls"]) == 1
    assert extra["backend_calls"][0]["backend"] == "laya"
    assert extra["escalated"] is False


def test_verify_rejects_an_unknown_backend(stubs):
    with pytest.raises(ValueError):
        verify(PACKET, backend="nope")


def test_verify_rejects_an_unregistered_proposition(stubs):
    with pytest.raises(ValueError):
        verify(PACKET, backend="laya", proposition="something task-specific")


def test_verify_accepts_a_pre_rendered_state_string(stubs):
    stubs["jev"].p_true = 0.2
    r = verify("Query: q\nEvidence: e", backend="jev")
    assert r.action == "ESCALATE" and r.p_true == pytest.approx(0.2)


def test_verify_normalises_unexpected_failures_into_the_contract_error(stubs):
    stubs["laya"].raises = RuntimeError("boom")
    with pytest.raises(VerifierUnavailable) as exc:
        verify(PACKET, backend="laya")
    assert exc.value.backend == "laya" and "boom" in exc.value.detail


def test_verify_clamps_out_of_range_probabilities(stubs):
    stubs["laya"].p_true = 1.4
    assert verify(PACKET, backend="laya").p_true == 1.0


# --- escalation -------------------------------------------------------------

def test_escalation_runs_both_backends_in_one_trace(stubs):
    stubs["laya"].p_true = 0.5      # abstain band -> uncertain
    stubs["jev"].p_true = 0.95
    r = verify_with_escalation(PACKET)
    assert r.escalated is True
    assert r.action == "ALLOW"
    assert r.p_true == pytest.approx(0.95)          # the reference decides
    assert [c.backend for c in r.calls] == ["laya", "jev"]
    assert all(c.ok for c in r.calls)
    assert stubs["laya"].calls == 1 and stubs["jev"].calls == 1
    extra = r.receipt["extra"]
    assert extra["escalated"] is True
    assert extra["escalation"]["reference_attempted"] is True
    assert extra["escalation"]["fast_p_true"] == pytest.approx(0.5)
    assert extra["escalation"]["reference_p_true"] == pytest.approx(0.95)
    assert [c["backend"] for c in extra["backend_calls"]] == ["laya", "jev"]
    assert r.receipt["provider"] == "jev"
    assert r.receipt["trace_id"] == r.trace_id
    assert validate_receipt(r.receipt) == []


def test_escalation_is_skipped_when_the_fast_path_is_decisive(stubs):
    stubs["laya"].p_true = 0.95
    stubs["jev"].p_true = 0.1
    r = verify_with_escalation(PACKET)
    assert r.escalated is False
    assert r.backend == "laya"
    assert stubs["jev"].calls == 0, "the reference must not be called when the fast path decides"
    assert [c.backend for c in r.calls] == ["laya"]
    assert r.receipt["extra"]["escalation"]["uncertain"] is False


def test_escalation_records_a_failed_reference_and_keeps_the_fast_answer(stubs):
    stubs["laya"].p_true = 0.5
    stubs["jev"].raises = VerifierUnavailable("jev", "auth_failed")
    r = verify_with_escalation(PACKET)
    assert r.escalated is True
    assert r.p_true == pytest.approx(0.5)           # fast answer stands
    assert r.action == "ABSTAIN"
    failed = [c for c in r.calls if not c.ok]
    assert len(failed) == 1 and failed[0].backend == "jev"
    esc = r.receipt["extra"]["escalation"]
    assert esc["reference_failed"] is True and "auth_failed" in esc["reference_failure"]
    assert validate_receipt(r.receipt) == []


def test_escalation_uses_one_trace_id_across_both_calls(stubs):
    stubs["laya"].p_true = 0.5
    r = verify_with_escalation(PACKET, trace_id="trace-fixed-123")
    assert r.trace_id == "trace-fixed-123"
    assert r.receipt["trace_id"] == "trace-fixed-123"
    assert len({c.backend for c in r.calls}) == 2


def test_escalation_refuses_when_the_fast_path_is_unavailable(stubs):
    stubs["laya"].available = False
    with pytest.raises(VerifierUnavailable):
        verify_with_escalation(PACKET)


def test_receipt_is_json_serialisable(stubs):
    stubs["laya"].p_true = 0.5
    r = verify_with_escalation(PACKET)
    assert json.loads(json.dumps(r.receipt, default=str))["trace_id"] == r.trace_id


# --- the live implementations ----------------------------------------------

def _packet(i: int) -> dict:
    return json.load(open(f"/tmp/gate-cases2/case{i}.json"))


@pytest.mark.live
@pytest.mark.parametrize("phrasing_index", [0, 1, 2])
def test_live_laya_orders_the_three_packets(phrasing_index):
    """supported > ambiguous > unsupported, surviving every registered phrasing."""
    scores = {}
    for case, label in ((1, "supported"), (3, "ambiguous"), (2, "unsupported")):
        r = verify(_packet(case), backend="laya",
                   proposition=PHRASINGS[phrasing_index])
        assert validate_receipt(r.receipt) == []
        scores[label] = r.p_true
    assert scores["supported"] > scores["ambiguous"] > scores["unsupported"], scores


@pytest.mark.live
def test_live_jev_reports_the_validated_revision():
    r = verify(_packet(1), backend="jev")
    assert r.receipt["schema"] == "z0int.decision_receipt.v1"
    assert r.receipt["extra"]["backend_calls"][0]["model"] == "jev-1.13.0"
    assert validate_receipt(r.receipt) == []


@pytest.mark.live
def test_live_escalation_records_both_physical_calls():
    r = verify_with_escalation(_packet(1))
    assert r.receipt["extra"]["backend_calls"][0]["backend"] == "laya"
    assert validate_receipt(r.receipt) == []
