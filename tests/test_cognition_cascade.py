"""The cascade: deterministic first, learned only for legal choices, fail open."""

from __future__ import annotations

import pytest

from z0int.cognition.actions import ActionCandidate, ActionGraph, Rule
from z0int.cognition.adapters.local_slm import ToolDecision, ToolDecisionRequest
from z0int.cognition.cascade import CascadeContext, CognitionCascade, mark_executed
from z0int.cognition.escalation import (
    EscalationPolicy,
    EscalationSignals,
    EscalationThresholds,
    entropy_of,
    margin_of,
)


def _action(action_id, **kw):
    kw.setdefault("kind", "tool")
    kw.setdefault("tool", action_id)
    kw.setdefault("family", "fs")
    kw.setdefault("description", f"action {action_id}")
    return ActionCandidate(action_id=action_id, **kw)


def _graph(*actions, rules=()):
    return ActionGraph(actions=tuple(actions), rules=tuple(rules))


class _Recorder:
    """A ToolDecisionBackend that returns a fixed answer and records its inputs."""

    def __init__(self, backend_id, action=None, abstain=False, raises=None,
                 distribution=None, confidence=None):
        self._id = backend_id
        self._action = action
        self._abstain = abstain
        self._raises = raises
        self._distribution = distribution
        self._confidence = confidence
        self.requests: list[ToolDecisionRequest] = []

    @property
    def backend_id(self):
        return self._id

    def health(self, *, load=False):
        return {"ready": True, "backend": self._id}

    def decide(self, request):
        self.requests.append(request)
        if self._raises is not None:
            raise self._raises
        return ToolDecision(
            backend=self._id,
            model=f"{self._id}-model",
            revision="rev",
            selected_action=None if self._abstain else self._action,
            arguments={},
            confidence=self._confidence,
            distribution=self._distribution,
            latency_ms=5.0,
            abstained=self._abstain or self._action is None,
            candidate_action_count=request.legal.candidate_count,
        )


def _ctx(**kw):
    graph = kw.pop("graph", _graph(_action("a"), _action("b")))
    return CascadeContext(state=kw.pop("state", "situation"), graph=graph, **kw)


# --- deterministic tier -------------------------------------------------


def test_deterministic_rule_short_circuits_every_learned_tier():
    ctx = _ctx(
        graph=_graph(
            _action("a"),
            _action("b"),
            rules=(Rule(id="r", when={"intent": "x"}, choose="a"),),
        ),
        facts={"intent": "x"},
        authority=("read",),
    )
    orchestrator = _Recorder("nemotron", action="b")
    outcome = CognitionCascade(orchestrator=orchestrator).run(ctx)
    assert outcome.tier == "deterministic"
    assert outcome.selected_action == "a"
    assert orchestrator.requests == []
    assert outcome.abstained is False


def test_no_legal_actions_abstains_without_calling_a_model():
    ctx = _ctx(
        graph=_graph(_action("rm", risk_class="destructive")),
        authority=("read",),
    )
    orchestrator = _Recorder("nemotron", action="rm")
    outcome = CognitionCascade(orchestrator=orchestrator).run(ctx)
    assert outcome.selected_action is None
    assert orchestrator.requests == []


# --- learned tiers ------------------------------------------------------


def test_learned_tier_receives_only_the_legal_set():
    ctx = _ctx(
        graph=_graph(_action("a"), _action("b"), _action("wipe", risk_class="destructive")),
        authority=("read",),
    )
    orchestrator = _Recorder("nemotron", action="a")
    CognitionCascade(orchestrator=orchestrator).run(ctx)
    assert orchestrator.requests[0].legal.ids() == ("a", "b")


def test_cascade_rejects_a_backend_that_names_a_filtered_action():
    """Defence in depth: a misbehaving backend cannot smuggle an illegal action."""
    ctx = _ctx(
        graph=_graph(_action("a"), _action("b"), _action("wipe", risk_class="destructive")),
        authority=("read",),
    )
    cheating = _Recorder("rogue", action="wipe")
    outcome = CognitionCascade(orchestrator=cheating).run(ctx)
    assert outcome.selected_action is None
    assert outcome.abstained is True
    assert any(row.get("rejected") == "selection_outside_legal_set" for row in outcome.attempts)


def test_abstention_escalates_to_the_next_tier():
    ctx = _ctx(authority=("read", "write"))
    jev = _Recorder("jev", abstain=True)
    orchestrator = _Recorder("nemotron", action="b")
    outcome = CognitionCascade(jev=jev, orchestrator=orchestrator).run(ctx)
    # JEV abstained, so the orchestrator got the decision.
    assert outcome.tier == "orchestrator_slm"
    assert outcome.selected_action == "b"
    assert len(jev.requests) == 1
    assert len(orchestrator.requests) == 1


def test_all_tiers_abstaining_yields_an_abstention_not_a_guess():
    ctx = _ctx(authority=("read", "write"))
    outcome = CognitionCascade(
        jev=_Recorder("jev", abstain=True),
        orchestrator=_Recorder("nemotron", abstain=True),
        general=_Recorder("qwen", abstain=True),
    ).run(ctx)
    assert outcome.abstained is True
    assert outcome.selected_action is None
    assert outcome.tier == "abstained"
    assert outcome.reason == "all_tiers_abstained"


def test_a_tier_that_raises_is_skipped_not_fatal():
    ctx = _ctx(authority=("read", "write"))
    outcome = CognitionCascade(
        jev=_Recorder("jev", raises=RuntimeError("model server down")),
        orchestrator=_Recorder("nemotron", action="a"),
    ).run(ctx)
    assert outcome.selected_action == "a"
    assert any("model server down" in str(row.get("error", "")) for row in outcome.attempts)


def test_unconfigured_tiers_are_recorded_as_skipped():
    ctx = _ctx(authority=("read", "write"))
    outcome = CognitionCascade(orchestrator=_Recorder("nemotron", action="a")).run(ctx)
    skipped = [row for row in outcome.attempts if row.get("skipped")]
    assert skipped, outcome.attempts


# --- escalation drives the entry tier -----------------------------------


def test_high_risk_work_enters_at_the_orchestrator_tier():
    ctx = _ctx(authority=("read", "write"), risk_class="publish")
    jev = _Recorder("jev", action="a")
    orchestrator = _Recorder("nemotron", action="b")
    outcome = CognitionCascade(jev=jev, orchestrator=orchestrator).run(ctx)
    assert outcome.escalation.tier == "orchestrator_slm"
    assert jev.requests == []  # the bounded scorer was skipped entirely


def test_verification_required_skips_the_bounded_scorer():
    ctx = _ctx(authority=("read", "write"), verification_required=True)
    jev = _Recorder("jev", action="a")
    orchestrator = _Recorder("nemotron", action="b")
    outcome = CognitionCascade(jev=jev, orchestrator=orchestrator).run(ctx)
    assert jev.requests == []
    assert outcome.selected_action == "b"


def test_latency_budget_exhaustion_routes_to_remote():
    ctx = _ctx(authority=("read", "write"), latency_budget_ms=2000.0,
               estimated_candidate_latency_ms=1000.0)
    remote = _Recorder("frontier", action="a")
    outcome = CognitionCascade(remote=remote).run(ctx)
    assert outcome.escalation.tier == "remote_frontier"
    assert outcome.selected_action == "a"


def test_credited_tiny_specialist_is_tried_before_jev():
    ctx = _ctx(authority=("read", "write"))
    tiny = _Recorder("functiongemma", action="a", confidence=0.95)
    jev = _Recorder("jev", action="b")
    policy = EscalationPolicy(EscalationThresholds(credited_families=("fs",)))
    outcome = CognitionCascade(tiny=tiny, jev=jev, policy=policy).run(ctx)
    assert outcome.tier == "tiny_specialist"
    assert outcome.selected_action == "a"
    assert jev.requests == []


# --- shadow mode --------------------------------------------------------


def test_shadow_backends_record_but_never_execute():
    ctx = _ctx(authority=("read", "write"))
    winner = _Recorder("deterministic_winner", action="a")
    shadow = _Recorder("qwen", action="b")
    outcome = CognitionCascade(orchestrator=winner).run(ctx, shadow=(("qwen_shadow", shadow),))

    # The winner still owns the decision...
    assert outcome.selected_action == "a"
    assert outcome.tier == "orchestrator_slm"
    # ...and the shadow answer is recorded side by side.
    assert len(outcome.shadow) == 1
    assert outcome.shadow[0]["label"] == "qwen_shadow"
    assert outcome.shadow[0]["selected_action"] == "b"
    assert shadow.requests[0].legal.ids() == outcome.legal.ids()


def test_shadow_failure_never_breaks_the_turn():
    ctx = _ctx(authority=("read", "write"))
    shadow = _Recorder("qwen", raises=RuntimeError("boom"))
    outcome = CognitionCascade(orchestrator=_Recorder("nemotron", action="a")).run(
        ctx, shadow=(("qwen_shadow", shadow),)
    )
    assert outcome.selected_action == "a"
    assert "boom" in outcome.shadow[0]["error"]


def test_shadow_receipts_are_marked_shadow_and_live_ones_live():
    ctx = _ctx(authority=("read", "write"))
    outcome = CognitionCascade(orchestrator=_Recorder("nemotron", action="a")).run(
        ctx, shadow=(("qwen_shadow", _Recorder("qwen", action="b")),)
    )
    executions = {r["execution"] for r in outcome.receipts}
    assert "shadow" in executions
    assert "live" in executions


# --- receipt semantics --------------------------------------------------


def test_selected_action_and_executed_action_stay_separate():
    ctx = _ctx(authority=("read", "write"))
    outcome = CognitionCascade(orchestrator=_Recorder("nemotron", action="a")).run(ctx)
    payload = mark_executed(outcome, "b")  # runtime executed something else
    assert payload["selected_action"] == "a"
    assert payload["executed_action"] == "b"
    assert payload["selected_equals_executed"] is False


def test_execution_completed_is_not_verified_success():
    from z0int.receipt import DecisionReceipt

    row = DecisionReceipt(trace_id="a" * 32, provider="nemotron", execution="live").to_dict()
    assert "execution_completed" not in row
    assert "verified_success" not in row


def test_outcome_is_replayable_json():
    import json

    ctx = _ctx(authority=("read", "write"))
    outcome = CognitionCascade(orchestrator=_Recorder("nemotron", action="a")).run(ctx)
    text = json.dumps(outcome.to_dict(), sort_keys=True)
    assert "graph_digest" in text
    assert outcome.to_dict()["schema"] == "z0int.cognition.decision.v1"


def test_escalation_receipt_carries_the_policy_version():
    ctx = _ctx(authority=("read", "write"))
    outcome = CognitionCascade(orchestrator=_Recorder("nemotron", action="a")).run(ctx)
    assert outcome.escalation.policy_version == "escalation-v1"
    assert outcome.receipts[0]["extra"]["tier"] == outcome.tier


# --- escalation signal helpers ------------------------------------------


def test_entropy_and_margin():
    assert entropy_of(None) is None
    assert entropy_of({"a": 1.0}) == 0.0
    assert entropy_of({"a": 0.5, "b": 0.5}) == pytest.approx(1.0)
    assert margin_of({"a": 0.9, "b": 0.1}) == pytest.approx(0.8)
    assert margin_of({"a": 1.0}) is None


def test_escalation_is_not_a_function_of_prompt_length():
    policy = EscalationPolicy()
    short_but_hard = policy.decide(
        EscalationSignals(legal_action_count=6, novel_tool_combination=True)
    )
    long_but_easy = policy.decide(EscalationSignals(legal_action_count=2, state_novelty=0.0))
    assert short_but_hard.tier == "orchestrator_slm"
    assert long_but_easy.tier in ("tiny_specialist", "bounded_jev")
    # the policy never even looks at a prompt
    assert "prompt" not in short_but_hard.to_dict()
    assert "tokens" not in long_but_easy.to_dict()
