"""Decision surface + replayable receipts (z0intelligence#20).

Contract under test:

* the surface answers suitability / uncertainty / required class / escalation;
* legal-action filtering happens *before* any learned selection;
* the ladder keeps JEV-OpenJev distinct from the SLM router;
* nothing here accounts for quota or latency budgets that belong to Kerdoios.
"""

from __future__ import annotations

import inspect
import json

import pytest

from z0int.cognition.actions import ActionCandidate, ActionGraph, compile_actions
from z0int.cognition.adapters.local_slm import ToolDecision
from z0int.cognition.cascade import CascadeContext, CognitionCascade
from z0int.cognition.candidates import (
    CapabilityProfile,
    CapabilityRequirement,
    CandidateModel,
    CandidateRungBackend,
    candidates_from_pi_ai_catalog,
    filter_candidates,
    rank_candidates,
)
from z0int.cognition.escalation import EscalationSignals
from z0int.cognition.manifest import load_local_cognition
from z0int.cognition.candidates import candidates_from_local_manifest
from z0int.cognition.receipts import (
    CognitionReceipt,
    CostState,
    ExecutionOutcome,
    LatencyState,
    QuotaState,
    TokenState,
    receipt_from_dict,
)
from z0int.cognition.surface import (
    DecisionSurface,
    SurfaceRequest,
    SurfaceThresholds,
    uncertainty_of,
)

CATALOG = {
    "openai-completions": {
        "gpt-oss-120b": {
            "id": "gpt-oss-120b",
            "provider": "cerebras",
            "api": "openai-completions",
            "reasoning": True,
            "input": ["text"],
            "contextWindow": 131072,
        },
        "llama-3.3-70b-versatile": {
            "id": "llama-3.3-70b-versatile",
            "provider": "groq",
            "api": "openai-completions",
            "reasoning": False,
            "input": ["text"],
            "contextWindow": 131072,
        },
    }
}

HINTS = {
    "groq/llama-3.3-70b-versatile": {
        "cost_class": "free",
        "quality_class": "standard",
        "max_risk_class": "write",
        "serves_tiers": ["orchestrator_slm", "general_slm"],
    },
    "cerebras/gpt-oss-120b": {
        "cost_class": "free",
        "quality_class": "strong",
        "max_risk_class": "write",
        "serves_tiers": ["orchestrator_slm", "general_slm"],
    },
}


def _candidate(candidate_id="local/x", **kw):
    kw.setdefault("provider", candidate_id.split("/")[0])
    kw.setdefault("model_id", candidate_id.split("/")[-1])
    return CandidateModel(candidate_id=candidate_id, **kw)


def _actions(*ids, authority=("read", "write")):
    graph = ActionGraph(
        actions=tuple(
            ActionCandidate(action_id=i, kind="tool", tool=i, family="fs", description=i)
            for i in ids
        )
    )
    return compile_actions(
        graph=graph, granted_capabilities=(), authority=authority, budget_units=8, facts={}
    )


class _FakeBackend:
    def __init__(self, backend_id, action, *, abstain=False):
        self._id = backend_id
        self._action = action
        self._abstain = abstain

    @property
    def backend_id(self):
        return self._id

    def health(self, *, load=False):
        return {"ready": True}

    def decide(self, request):
        return ToolDecision(
            backend=self._id,
            model=f"{self._id}-model",
            revision="rev",
            selected_action=None if self._abstain else self._action,
            arguments={},
            confidence=0.9,
            distribution=None,
            latency_ms=4.0,
            abstained=self._abstain or self._action is None,
            candidate_action_count=request.legal.candidate_count,
            prompt_tokens=120,
            completion_tokens=8,
            diagnostics={
                "supervisor": {"wall_ms": 42.0, "cold": False, "resident_model": self._id},
                "quota": {"source": "kerdoios", "free_tier": True, "remaining": 7, "limit": 10},
            },
        )


# --- the four questions -------------------------------------------------


def _surface_with(*candidates, **thresholds):
    return DecisionSurface(list(candidates), thresholds=SurfaceThresholds(**thresholds))


def test_surface_answers_all_four_questions():
    rows = candidates_from_pi_ai_catalog(CATALOG, hints=HINTS)
    surface = _surface_with(*rows)
    decision = surface.assess(
        SurfaceRequest(tier="orchestrator_slm", legal_ids=("a", "b"), risk_class="read")
    )
    # which candidates are semantically suitable?
    assert decision.eligible_ids == (
        "cerebras/gpt-oss-120b",
        "groq/llama-3.3-70b-versatile",
    )
    # how uncertain is this decision?
    assert decision.uncertainty == 0.0
    # what class is required?
    assert decision.quality_class_required == "bounded"
    # should we escalate?
    assert decision.escalate is False
    assert decision.tier == "orchestrator_slm"


def test_surface_escalates_on_risk_and_verification():
    rows = candidates_from_pi_ai_catalog(CATALOG, hints=HINTS)
    general = _candidate(
        "local/general",
        quality_class="strong",
        max_risk_class="publish",
        serves_tiers=("orchestrator_slm", "general_slm"),
    )
    surface = _surface_with(general, *rows)
    decision = surface.assess(
        SurfaceRequest(
            tier="bounded_jev",
            legal_ids=("a", "b"),
            risk_class="publish",
            verification_required=True,
        )
    )
    assert decision.quality_class_required == "standard"
    assert decision.tier == "orchestrator_slm"
    assert decision.escalate is True
    assert decision.escalate_reason == "quality_class:standard"
    # The read-only remote rows are still rejected at publish risk.
    assert "groq/llama-3.3-70b-versatile" not in decision.eligible_ids


def test_high_uncertainty_escalates_and_lifts_the_class():
    rows = candidates_from_pi_ai_catalog(CATALOG, hints=HINTS)
    surface = _surface_with(*rows)
    rng = EscalationSignals(
        legal_action_count=4,
        candidate_entropy=0.95,
        top1_top2_margin=0.02,
        novel_tool_combination=True,
    )
    decision = surface.assess(
        SurfaceRequest(tier="bounded_jev", legal_ids=("a", "b"), signals=rng)
    )
    assert decision.uncertainty >= 0.5
    assert decision.quality_class_required == "strong"
    assert decision.tier == "general_slm"
    assert decision.escalate is True
    assert decision.escalate_reason == "uncertainty:" + f"{decision.uncertainty:.3f}"


def test_uncertainty_is_not_a_function_of_prompt_length():
    long_but_easy = uncertainty_of(EscalationSignals(legal_action_count=2, state_novelty=0.0))
    short_but_hard = uncertainty_of(
        EscalationSignals(
            legal_action_count=7, candidate_entropy=0.9, novel_tool_combination=True
        )
    )
    assert long_but_easy == 0.0
    assert short_but_hard > 0.5
    # the surface never even receives a prompt
    params = inspect.signature(DecisionSurface.assess).parameters
    assert set(params) == {"self", "request"}
    assert "prompt" not in SurfaceRequest.__dataclass_fields__


def test_verification_demand_lifts_the_class_without_faking_uncertainty():
    signals = EscalationSignals(legal_action_count=3, verification_required=True)
    assert uncertainty_of(signals) == 0.0


def test_surface_fails_closed_when_no_candidate_is_eligible():
    surface = _surface_with(_candidate(quality_class="bounded", serves_tiers=("bounded_jev",)))
    decision = surface.assess(
        SurfaceRequest(tier="bounded_jev", legal_ids=("a", "b"), risk_class="publish")
    )
    assert decision.fail_closed is True
    assert decision.eligible == ()
    assert decision.allowed_candidates == ()
    assert decision.escalate is True
    assert decision.escalate_reason == "no_eligible_candidate"


def test_surface_fails_closed_on_a_capability_no_candidate_has():
    """Never lower the bar to spend free capacity."""
    rows = candidates_from_pi_ai_catalog(CATALOG, hints=HINTS)
    surface = _surface_with(*rows)
    decision = surface.assess(
        SurfaceRequest(
            tier="orchestrator_slm",
            legal_ids=("a",),
            required=CapabilityRequirement(reasoning=True, min_context_window=1_000_000),
        )
    )
    assert decision.fail_closed is True
    assert all("capability:" in r.reason for r in decision.rejected)


def test_surface_is_inert_without_an_inventory():
    """No inventory means the escalation policy alone still picks the tier."""
    surface = DecisionSurface(())
    decision = surface.assess(
        SurfaceRequest(tier="bounded_jev", legal_ids=("a", "b"), risk_class="publish")
    )
    assert decision.enabled is False
    assert decision.tier == "bounded_jev"
    assert decision.fail_closed is False
    assert decision.escalate is False


def test_surface_refuses_when_there_are_no_legal_actions():
    surface = _surface_with(_candidate(serves_tiers=("bounded_jev",)))
    decision = surface.assess(SurfaceRequest(tier="bounded_jev", legal_ids=()))
    assert decision.tier == "abstained"
    assert decision.escalate_reason == "no_legal_actions"
    assert decision.eligible == ()


def test_quality_floor_keeps_a_bounded_scorer_out_of_high_risk_work():
    """A strong requirement cannot be met by a bounded scorer, however cheap."""
    scorer = _candidate("local/scorer", quality_class="bounded", serves_tiers=("bounded_jev",))
    surface = _surface_with(scorer)
    decision = surface.assess(
        SurfaceRequest(
            tier="bounded_jev",
            legal_ids=("a", "b"),
            verification_required=True,
        )
    )
    assert decision.quality_class_required == "standard"
    assert decision.fail_closed is True


# --- ownership boundary -------------------------------------------------


@pytest.mark.parametrize(
    "obj",
    [DecisionSurface.assess, filter_candidates, rank_candidates],
)
def test_no_quota_or_reset_accounting_parameters(obj):
    """RPM/RPD/TPM/TPD and reset timers belong to Kerdoios, not this plane."""
    forbidden = ("quota", "rpm", "rpd", "tpm", "tpd", "reset", "rate_limit", "placement")
    names = set(inspect.signature(obj).parameters)
    assert not {n for n in names if any(token in n.lower() for token in forbidden)}


# --- cascade integration ------------------------------------------------


def _cascade_with_router(*, risk_class="read", verification_required=False):
    inventory = candidates_from_local_manifest(load_local_cognition())
    catalog_rows = candidates_from_pi_ai_catalog(CATALOG, hints=HINTS)
    all_candidates = inventory + catalog_rows
    router = CandidateRungBackend(
        tier="orchestrator_slm",
        candidates=all_candidates,
        backends={
            "local/qwen3.5_4b": _FakeBackend("local/qwen3.5_4b", "a"),
            "groq/llama-3.3-70b-versatile": _FakeBackend("groq", "b"),
            "cerebras/gpt-oss-120b": _FakeBackend("cerebras", "a"),
        },
    )
    cascade = CognitionCascade(orchestrator=router, surface=DecisionSurface(all_candidates))
    context = CascadeContext(
        state="pick a or b",
        graph=ActionGraph(
            actions=(
                ActionCandidate(action_id="a", kind="tool", tool="a", family="fs", description="a"),
                ActionCandidate(action_id="b", kind="tool", tool="b", family="fs", description="b"),
            )
        ),
        authority=("read", "write"),
        risk_class=risk_class,
        verification_required=verification_required,
    )
    return cascade, context


def test_cascade_receipt_records_the_eligible_candidate_set_and_the_choice():
    cascade, context = _cascade_with_router()
    outcome = cascade.run(context)
    assert outcome.tier == "orchestrator_slm"
    receipt = outcome.receipts[-1]
    eligible = {c["candidate_id"] for c in receipt["eligible_candidates"]}
    # Groq and Cerebras are in the eligible set through the shared model.
    assert {"groq/llama-3.3-70b-versatile", "cerebras/gpt-oss-120b"} <= eligible
    assert receipt["chosen_candidate"]["candidate_id"] == "local/qwen3.5_4b"
    # The rung's allowed set is exactly its own eligible order.
    assert receipt["allowed_candidates"] == [
        c["candidate_id"] for c in receipt["eligible_candidates"]
    ]
    # The surface records the (narrower) entry-tier verdict for the same pass.
    assert set(receipt["surface"]["allowed_candidates"]) <= set(receipt["allowed_candidates"])
    # The observed quota state is carried through, never recomputed.
    assert receipt["quota"]["source"] == "kerdoios"
    assert receipt["quota"]["remaining"] == 7
    # GPU cost comes from the supervisor's own facts; cost class from the choice.
    assert receipt["cost"]["gpu_ms"] == 42.0
    assert receipt["cost"]["cost_class"] == "local"


def test_cascade_legal_filtering_happens_before_learned_selection():
    cascade, context = _cascade_with_router()
    outcome = cascade.run(context)
    receipt_ids = list(outcome.receipts[-1]["legal_ids"])
    assert set(receipt_ids) == {"a", "b"}
    # The router is only ever handed the compiled set.
    assert receipt_ids == list(outcome.legal.ids())


def test_cascade_surface_fails_closed_and_emits_no_guess():
    # Only read-only remote candidates: a publish-risk request has nothing
    # eligible, so the cascade must abstain instead of guessing or downgrading.
    remote = candidates_from_pi_ai_catalog(CATALOG, hints=HINTS)
    cascade = CognitionCascade(
        orchestrator=CandidateRungBackend(
            tier="orchestrator_slm",
            candidates=remote,
            backends={"groq/llama-3.3-70b-versatile": _FakeBackend("groq", "b")},
        ),
        surface=DecisionSurface(remote),
    )
    context = CascadeContext(
        state="publish something",
        graph=ActionGraph(
            actions=(
                ActionCandidate(action_id="a", kind="tool", tool="a", family="fs", description="a"),
                ActionCandidate(action_id="b", kind="tool", tool="b", family="fs", description="b"),
            )
        ),
        authority=("read", "write"),
        risk_class="publish",
    )
    outcome = cascade.run(context)
    assert outcome.abstained is True
    assert outcome.reason == "no_eligible_candidate"
    assert outcome.surface.fail_closed is True


def test_cascade_surface_tier_never_lowers_the_escalation_floor():
    inventory = candidates_from_local_manifest(load_local_cognition())
    cascade = CognitionCascade(
        jev=_FakeBackend("jev", "a"), surface=DecisionSurface(inventory)
    )
    context = CascadeContext(
        state="s",
        graph=ActionGraph(
            actions=(
                ActionCandidate(action_id="a", kind="tool", tool="a", family="fs", description="a"),
                ActionCandidate(action_id="b", kind="tool", tool="b", family="fs", description="b"),
            )
        ),
        authority=("read", "write"),
        risk_class="publish",
    )
    outcome = cascade.run(context)
    # risk lifts the floor to the orchestrator rung; there is no backend there,
    # so the pass abstains rather than dropping back to the bounded scorer.
    assert outcome.surface.tier == "orchestrator_slm"
    assert outcome.abstained is True


def test_shadow_receipts_are_still_marked_shadow_and_carry_the_surface():
    inventory = candidates_from_local_manifest(load_local_cognition())
    cascade = CognitionCascade(
        orchestrator=_FakeBackend("local/qwen3.5_4b", "a"),
        surface=DecisionSurface(inventory),
    )
    context = CascadeContext(
        state="s",
        graph=ActionGraph(
            actions=(
                ActionCandidate(action_id="a", kind="tool", tool="a", family="fs", description="a"),
                ActionCandidate(action_id="b", kind="tool", tool="b", family="fs", description="b"),
            )
        ),
        authority=("read", "write"),
    )
    outcome = cascade.run(
        context, shadow=(("groq", _FakeBackend("groq", "b")),)
    )
    executions = {r["execution"] for r in outcome.receipts}
    assert executions == {"shadow", "live"}
    for receipt in outcome.receipts:
        assert receipt["surface"]["schema"] == "z0int.cognition.surface.v1"


def test_jev_and_slm_router_stay_distinct_in_the_cascade():
    inventory = candidates_from_local_manifest(load_local_cognition())
    jev = CandidateRungBackend(
        tier="bounded_jev",
        candidates=inventory,
        backends={"local/nanojev_06b": _FakeBackend("nanojev", "a")},
    )
    router = CandidateRungBackend(
        tier="orchestrator_slm",
        candidates=inventory,
        backends={"local/qwen3.5_4b": _FakeBackend("qwen", "b")},
    )
    cascade = CognitionCascade(jev=jev, orchestrator=router)
    context = CascadeContext(
        state="s",
        graph=ActionGraph(
            actions=(
                ActionCandidate(action_id="a", kind="tool", tool="a", family="fs", description="a"),
                ActionCandidate(action_id="b", kind="tool", tool="b", family="fs", description="b"),
            )
        ),
        authority=("read", "write"),
        facts={},
    )
    outcome = cascade.run(context)
    # Two configured candidates on two different rungs remain two backends.
    assert cascade.backend("bounded_jev") is jev
    assert cascade.backend("orchestrator_slm") is router
    assert jev is not router
    assert outcome.tier in ("bounded_jev", "orchestrator_slm")


# --- receipts -----------------------------------------------------------


def _receipt(**kw):
    kw.setdefault("trace_id", "t" * 32)
    kw.setdefault("tier", "orchestrator_slm")
    kw.setdefault("execution", "live")
    kw.setdefault("state", "pick a or b")
    kw.setdefault("eligible_candidates", ({"candidate_id": "groq/x"},))
    kw.setdefault("chosen_candidate", {"candidate_id": "groq/x", "provider": "groq"})
    kw.setdefault("quota", QuotaState(source="kerdoios", free_tier=True, remaining=3, limit=10))
    kw.setdefault("latency", LatencyState(budget_ms=2000.0, observed_ms=812.0, ttft_ms=50.0))
    kw.setdefault("prediction", "a")
    kw.setdefault("confidence", 0.83)
    kw.setdefault("provider", "groq")
    kw.setdefault("model", "llama-3.3-70b-versatile")
    kw.setdefault("tokens", TokenState(input_tokens=100, output_tokens=12))
    kw.setdefault("cost", CostState(gpu_ms=42.0, provider_cost_usd=0.0004, cost_class="free"))
    kw.setdefault("retries", 1)
    return CognitionReceipt(**kw)


def test_receipt_captures_every_required_field():
    row = _receipt().to_dict()
    for key in (
        "state",
        "eligible_candidates",
        "chosen_candidate",
        "quota",
        "latency",
        "prediction",
        "confidence",
        "execution_outcome",
        "verified_outcome",
        "tokens",
        "cost",
        "retries",
        "provider",
        "model",
    ):
        assert key in row, key
    assert row["latency_ms"] == 812.0
    assert row["input_tokens"] == 100
    assert row["provider"] == "groq"
    assert row["chosen_candidate"]["candidate_id"] == "groq/x"


def test_receipt_round_trips_through_json_for_replay():
    original = _receipt()
    restored = receipt_from_dict(json.loads(original.to_json()))
    assert restored.to_dict() == original.to_dict()


def test_receipt_joins_execution_and_verification_without_collapsing_them():
    receipt = _receipt()
    executed = receipt.mark_execution(executed_action="b", completed=True)
    assert executed.execution_outcome.selected_equals_executed is False
    assert executed.execution_outcome.verified_success is None
    verified = executed.mark_verification(verified_success=True, verifier="pytest")
    assert verified.execution_outcome.completed is True
    assert verified.execution_outcome.verified_success is True
    assert verified.to_dict()["verified_outcome"] == {
        "verified_success": True,
        "verifier": "pytest",
    }


def test_receipt_keeps_a_legacy_extra_tier_for_shared_consumers():
    from z0int.cognition.cascade import SCHEMA as CASCADE_SCHEMA

    receipt = _receipt(extra={"schema": CASCADE_SCHEMA, "tier": "orchestrator_slm"})
    row = receipt.to_dict()
    assert row["extra"]["tier"] == "orchestrator_slm"
    assert row["fallbacks"] == 1


def test_quota_state_is_carried_not_computed():
    """The receipt records whatever the caller observed, and nothing else."""
    empty = _receipt(quota=QuotaState()).to_dict()
    assert empty["quota"] == {}
    observed = _receipt(
        quota=QuotaState(source="kerdoios", raw={"rpd_remaining": 12})
    ).to_dict()
    assert observed["quota"]["raw"] == {"rpd_remaining": 12}
    assert observed["quota"]["source"] == "kerdoios"
