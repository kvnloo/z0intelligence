"""The compiled cognition cascade.

    observe -> compile -> deterministic -> tiny specialist -> JEV ->
    orchestrator SLM -> general SLM -> remote frontier

Every tier receives the SAME :class:`LegalActionSet`. Tiers are tried in order and
the first one that produces a legal, non-abstained selection wins; an abstention
escalates to the next tier. Nothing here can re-admit an action the compiler
removed.

Shadow mode runs arbitrary extra backends over the same legal set, records their
answers side by side, and never executes anything.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

from ..receipt import DecisionReceipt, new_trace_id
from .actions import ActionGraph, LegalActionSet, compile_actions
from .adapters.local_slm import ToolDecision, ToolDecisionBackend, ToolDecisionRequest
from .candidates import CandidateModel, requirement_for_legal_set
from .escalation import (
    EscalationDecision,
    EscalationPolicy,
    EscalationSignals,
    entropy_of,
    margin_of,
)
from .receipts import (
    CognitionReceipt,
    CostState,
    ExecutionOutcome,
    LatencyState,
    QuotaState,
    TokenState,
)
from .surface import DecisionSurface, SurfaceDecision, SurfaceRequest

SCHEMA = "z0int.cognition.decision.v1"

# Tier -> the constructor kwarg that supplies that backend.
_TIER_BACKEND = {
    "tiny_specialist": "tiny",
    "bounded_jev": "jev",
    "orchestrator_slm": "orchestrator",
    "general_slm": "general",
    "remote_frontier": "remote",
}


@dataclass(frozen=True)
class CascadeContext:
    """Everything the cascade needs. Built by the harness, not by a model."""

    state: str
    graph: ActionGraph
    granted_capabilities: tuple[str, ...] = ()
    authority: tuple[str, ...] = ("read",)
    budget_units: int = 8
    facts: Mapping[str, Any] = field(default_factory=dict)
    satisfied: tuple[str, ...] = ()
    objective: str | None = None
    risk_class: str = "read"
    constraints: Mapping[str, Any] = field(default_factory=dict)
    latency_budget_ms: float | None = None
    estimated_candidate_latency_ms: float | None = None
    verification_required: bool = False
    novel_tool_combination: bool = False
    historical_failure_family: bool = False
    state_novelty: float | None = None
    capability_required: str | None = None
    max_tokens: int = 256


@dataclass(frozen=True)
class CascadeOutcome:
    """Result of one cascade pass — fully replayable."""

    trace_id: str
    legal: LegalActionSet
    escalation: EscalationDecision
    tier: str
    selected_action: str | None
    executed_action: str | None
    decision: ToolDecision | None
    attempts: tuple[Mapping[str, Any], ...]
    shadow: tuple[Mapping[str, Any], ...]
    abstained: bool
    reason: str
    receipts: tuple[Mapping[str, Any], ...]
    #: The candidate-surface verdict for this pass, when a candidate inventory
    #: was supplied. ``None`` means the surface was not enforced.
    surface: SurfaceDecision | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": SCHEMA,
            "trace_id": self.trace_id,
            "graph_digest": self.legal.graph_digest,
            "legal_ids": list(self.legal.ids()),
            "candidate_action_count": self.legal.candidate_count,
            "escalation": self.escalation.to_dict(),
            "surface": self.surface.to_dict() if self.surface is not None else None,
            "tier": self.tier,
            "selected_action": self.selected_action,
            "executed_action": self.executed_action,
            "abstained": self.abstained,
            "reason": self.reason,
            "attempts": [dict(a) for a in self.attempts],
            "shadow": [dict(s) for s in self.shadow],
            "receipts": [dict(r) for r in self.receipts],
        }


def _attempt_row(tier: str, decision: ToolDecision) -> dict[str, Any]:
    return {
        "tier": tier,
        "backend": decision.backend,
        "model": decision.model,
        "revision": decision.revision,
        "selected_action": decision.selected_action,
        "confidence": decision.confidence,
        "abstained": decision.abstained,
        "invalid_call": decision.invalid_call,
        "latency_ms": decision.latency_ms,
    }


def _maybe_float(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _maybe_int(value: Any) -> int | None:
    number = _maybe_float(value)
    return None if number is None else int(number)


class CognitionCascade:
    """Deterministic orchestration of learned semantic tiers.

    ``surface`` (a :class:`~z0int.cognition.surface.DecisionSurface`) is optional.
    When supplied with a candidate inventory it decides which rung the pass
    enters at, records the semantically suitable candidate set, and fails closed
    when no candidate is eligible. Without it the ladder behaves exactly as
    before: the escalation policy alone picks the entry tier.
    """

    def __init__(
        self,
        *,
        tiny: ToolDecisionBackend | None = None,
        jev: ToolDecisionBackend | None = None,
        orchestrator: ToolDecisionBackend | None = None,
        general: ToolDecisionBackend | None = None,
        remote: ToolDecisionBackend | None = None,
        policy: EscalationPolicy | None = None,
        surface: DecisionSurface | None = None,
        candidates: Sequence[CandidateModel] = (),
    ) -> None:
        self._backends: dict[str, ToolDecisionBackend | None] = {
            "tiny_specialist": tiny,
            "bounded_jev": jev,
            "orchestrator_slm": orchestrator,
            "general_slm": general,
            "remote_frontier": remote,
        }
        self._policy = policy or EscalationPolicy()
        self._surface = surface or (
            DecisionSurface(candidates) if candidates else None
        )

    @property
    def policy(self) -> EscalationPolicy:
        return self._policy

    @property
    def surface(self) -> DecisionSurface | None:
        return self._surface

    def backend(self, tier: str) -> ToolDecisionBackend | None:
        return self._backends.get(tier)

    def _has_later_tier(self, tier: str) -> bool:
        """True when a configured, more expensive tier could still take this."""
        try:
            start = self._TIER_INDEX[tier]
        except KeyError:
            return False
        return any(self._backends.get(t) is not None for t in self._TIER_SEQUENCE[start + 1 :])

    # ------------------------------------------------------------------
    def compile(self, context: CascadeContext) -> LegalActionSet:
        return compile_actions(
            graph=context.graph,
            granted_capabilities=context.granted_capabilities,
            authority=context.authority,
            budget_units=context.budget_units,
            facts=context.facts,
            satisfied=context.satisfied,
        )

    def _signals(
        self, context: CascadeContext, legal: LegalActionSet
    ) -> EscalationSignals:
        return EscalationSignals(
            legal_action_count=legal.candidate_count,
            novel_tool_combination=context.novel_tool_combination,
            historical_failure_family=context.historical_failure_family,
            risk_class=context.risk_class,
            verification_required=context.verification_required,
            latency_budget_ms=context.latency_budget_ms,
            estimated_candidate_latency_ms=context.estimated_candidate_latency_ms,
            state_novelty=context.state_novelty,
            capability_required=context.capability_required,
            families=legal.family_options(),
        )

    def _assess_surface(
        self,
        context: CascadeContext,
        legal: LegalActionSet,
        signals: EscalationSignals,
        plan: EscalationDecision,
    ) -> SurfaceDecision | None:
        if self._surface is None:
            return None
        estimate = context.constraints.get("estimated_latency_ms")
        return self._surface.assess(
            SurfaceRequest(
                tier=plan.tier,
                legal_ids=legal.ids(),
                state=context.state,
                required=requirement_for_legal_set(legal),
                risk_class=context.risk_class,
                verification_required=context.verification_required,
                capability_required=context.capability_required,
                signals=signals,
                high_risk_classes=tuple(self._policy.thresholds.high_risk_classes),
                latency_budget_ms=context.latency_budget_ms,
                estimated_latency_ms=estimate if isinstance(estimate, Mapping) else {},
            )
        )

    def _receipt_for(
        self,
        decision: ToolDecision,
        *,
        tier: str,
        execution: str,
        context: CascadeContext,
        legal: LegalActionSet,
        surface: SurfaceDecision | None,
        trace_id: str,
    ) -> Mapping[str, Any]:
        """Build the one replayable receipt a learned decision emits."""
        diagnostics = decision.diagnostics or {}
        supervisor = diagnostics.get("supervisor")
        supervisor = supervisor if isinstance(supervisor, Mapping) else {}
        quota_raw = diagnostics.get("quota")
        quota = quota_raw if isinstance(quota_raw, Mapping) else {}
        chosen_candidate_id = diagnostics.get("chosen_candidate_id")
        # A provider-neutral rung router knows exactly which candidates were
        # eligible for the tier that actually ran; prefer its view over the
        # surface's entry-tier view, which may name a rung that was skipped.
        rung_rows = diagnostics.get("eligible_candidates")
        if isinstance(rung_rows, (list, tuple)) and rung_rows:
            eligible = tuple(dict(row) for row in rung_rows if isinstance(row, Mapping))
        else:
            eligible = tuple(
                candidate.to_dict() for candidate in (surface.eligible if surface else ())
            )
        by_id = {
            str(row.get("candidate_id")): row
            for row in eligible
            if isinstance(row, Mapping)
        }
        allowed = diagnostics.get("allowed_candidates")
        if not isinstance(allowed, (list, tuple)):
            allowed = surface.allowed_candidates if surface else ()
        chosen = by_id.get(str(chosen_candidate_id)) if chosen_candidate_id else None
        gpu_ms = supervisor.get("wall_ms")
        receipt = CognitionReceipt(
            trace_id=trace_id,
            tier=tier,
            execution=execution,
            state=context.state,
            eligible_candidates=eligible,
            allowed_candidates=tuple(allowed),
            chosen_candidate=chosen,
            quota=QuotaState(
                source=quota.get("source"),
                free_tier=quota.get("free_tier"),
                remaining=quota.get("remaining"),
                limit=quota.get("limit"),
                resets_at=quota.get("resets_at"),
                exhausted=quota.get("exhausted"),
                raw=dict(quota.get("raw") or {}),
            ),
            latency=LatencyState(
                budget_ms=context.latency_budget_ms,
                observed_ms=decision.latency_ms,
                ttft_ms=decision.ttft_ms,
                decode_tok_s=decision.decode_tok_s,
                observed_by="model_response" if not supervisor else "supervisor",
            ),
            prediction=decision.selected_action,
            confidence=decision.confidence,
            action_taken=decision.selected_action,
            route=tier,
            provider=decision.backend,
            model=decision.model,
            candidate_id=chosen_candidate_id,
            capability_id=legal.graph_digest,
            legal_ids=legal.ids(),
            surface=surface.to_dict() if surface is not None else {},
            policy_version=(surface.policy_version if surface else self._policy.thresholds.version),
            tokens=TokenState(
                input_tokens=decision.prompt_tokens,
                output_tokens=decision.completion_tokens,
                cached_input_tokens=decision.cached_tokens,
                reasoning_tokens=_maybe_int(diagnostics.get("reasoning_tokens")),
            ),
            cost=CostState(
                gpu_ms=_maybe_float(gpu_ms),
                gpu_vram_mib=None,
                provider_cost_usd=_maybe_float(diagnostics.get("provider_cost_usd")),
                provider_cost_units=_maybe_float(diagnostics.get("provider_cost_units")),
                cost_class=(
                    "local"
                    if supervisor
                    else (diagnostics.get("cost_class") if isinstance(diagnostics.get("cost_class"), str) else None)
                ),
                source="supervisor" if supervisor else None,
            ),
            execution_outcome=ExecutionOutcome(),
            retries=int(diagnostics.get("retries") or 0),
            extra={
                "schema": SCHEMA,
                "tier": tier,
                "revision": decision.revision,
                "candidate_action_count": decision.candidate_action_count,
                "invalid_call": decision.invalid_call,
                "irrelevant_call": decision.irrelevant_call,
                "unnecessary_call": decision.unnecessary_call,
                "abstained": decision.abstained,
                "ttft_ms": decision.ttft_ms,
                "decode_tok_s": decision.decode_tok_s,
                "graph_digest": legal.graph_digest,
                "chosen_candidate_id": chosen_candidate_id,
            },
        )
        return receipt.to_dict()

    def run(
        self,
        context: CascadeContext,
        *,
        shadow: Sequence[tuple[str, ToolDecisionBackend]] = (),
        trace_id: str | None = None,
    ) -> CascadeOutcome:
        tid = trace_id or new_trace_id()
        legal = self.compile(context)
        receipts: list[Mapping[str, Any]] = []
        attempts: list[Mapping[str, Any]] = []

        # Signals, the escalation plan and the candidate-surface verdict are all
        # pure functions of the compiled set and the observed context. Computing
        # them before shadow means the observe-only lane records the same verdict
        # the live lane would use.
        signals = self._signals(context, legal)
        plan = self._policy.decide(signals)
        surface_decision = self._assess_surface(context, legal, signals, plan)

        def record(decision: ToolDecision, tier: str, execution: str) -> Mapping[str, Any]:
            receipt = self._receipt_for(
                decision,
                tier=tier,
                execution=execution,
                context=context,
                legal=legal,
                surface=surface_decision,
                trace_id=tid,
            )
            receipts.append(receipt)
            return receipt

        # --- shadow (never executes, even when the answer is already known)
        # Running this BEFORE any early return is the whole point of dogfooding:
        # a deterministic shortcut is exactly the case where we want candidate
        # behaviour on record for later grouped evaluation.
        shadow_rows: list[Mapping[str, Any]] = []
        if shadow:
            shadow_request = ToolDecisionRequest(
                state=context.state,
                legal=legal,
                objective=context.objective,
                risk_class=context.risk_class,
                constraints=context.constraints,
                max_tokens=context.max_tokens,
            )
            for label, backend in shadow:
                try:
                    decision = backend.decide(shadow_request)
                except Exception as exc:  # noqa: BLE001 - shadow must never break the turn
                    shadow_rows.append(
                        {"label": label, "error": f"{type(exc).__name__}: {exc}"}
                    )
                    continue
                record(decision, f"shadow:{label}", "shadow")
                shadow_rows.append({"label": label, **_attempt_row(label, decision)})

        # --- deterministic tier -------------------------------------
        if legal.deterministic_solution is not None:
            row = {
                "tier": "deterministic",
                "backend": "deterministic",
                "selected_action": legal.deterministic_solution,
                "abstained": False,
                "latency_ms": 0.0,
                "reason": legal.deterministic_reason,
            }
            attempts.append(row)
            receipts.append(
                DecisionReceipt(
                    trace_id=tid,
                    capability_id=legal.graph_digest,
                    provider="deterministic",
                    model=None,
                    prediction=legal.deterministic_solution,
                    confidence=1.0,
                    action_taken=legal.deterministic_solution,
                    route="deterministic",
                    execution="live",
                    latency_ms=0.0,
                    extra={
                        "schema": SCHEMA,
                        "tier": "deterministic",
                        "reason": legal.deterministic_reason,
                        "graph_digest": legal.graph_digest,
                    },
                ).to_dict()
            )
            return CascadeOutcome(
                trace_id=tid,
                legal=legal,
                escalation=EscalationDecision(
                    tier="deterministic",
                    reason=legal.deterministic_reason or "deterministic_solution",
                    policy_version=self._policy.thresholds.version,
                ),
                tier="deterministic",
                selected_action=legal.deterministic_solution,
                executed_action=None,
                decision=None,
                attempts=tuple(attempts),
                shadow=tuple(shadow_rows),
                abstained=False,
                reason=legal.deterministic_reason or "deterministic_solution",
                receipts=tuple(receipts),
                surface=surface_decision,
            )

        # --- nothing legal: do not bother a model --------------------
        if legal.is_empty:
            attempts.append({"tier": "none", "abstained": True, "reason": "no_legal_actions"})
            return CascadeOutcome(
                trace_id=tid,
                legal=legal,
                escalation=EscalationDecision(
                    tier="deterministic",
                    reason="no_legal_actions",
                    policy_version=self._policy.thresholds.version,
                ),
                tier="abstained",
                selected_action=None,
                executed_action=None,
                decision=None,
                attempts=tuple(attempts),
                shadow=tuple(shadow_rows),
                abstained=True,
                reason="no_legal_actions",
                receipts=tuple(receipts),
                surface=surface_decision,
            )

        # --- candidate surface: fail closed rather than lower the bar ----
        # Free capacity is a reason to *place* work, never a reason to accept a
        # worse answer; an empty allowed set stays empty.
        if surface_decision is not None and surface_decision.fail_closed:
            attempts.append(
                {
                    "tier": "surface",
                    "abstained": True,
                    "reason": surface_decision.escalate_reason or "no_eligible_candidate",
                    "quality_class_required": surface_decision.quality_class_required,
                }
            )
            return CascadeOutcome(
                trace_id=tid,
                legal=legal,
                escalation=plan,
                tier="abstained",
                selected_action=None,
                executed_action=None,
                decision=None,
                attempts=tuple(attempts),
                shadow=tuple(shadow_rows),
                abstained=True,
                reason="no_eligible_candidate",
                receipts=tuple(receipts),
                surface=surface_decision,
            )

        # --- learned tiers ------------------------------------------
        start_tier = plan.tier
        if surface_decision is not None and surface_decision.tier in self._TIER_INDEX:
            start_tier = surface_decision.tier
        start_index = self._TIER_INDEX[start_tier]

        for tier in self._TIER_SEQUENCE[start_index:]:
            backend = self._backends.get(tier)
            if backend is None:
                attempts.append({"tier": tier, "skipped": "backend_not_configured"})
                continue
            request = ToolDecisionRequest(
                state=context.state,
                legal=legal,
                objective=context.objective,
                risk_class=context.risk_class,
                constraints=context.constraints,
                max_tokens=context.max_tokens,
            )
            try:
                decision = backend.decide(request)
            except Exception as exc:  # noqa: BLE001 - fail open to the next tier
                attempts.append(
                    {"tier": tier, "error": f"{type(exc).__name__}: {exc}"}
                )
                continue
            attempts.append(_attempt_row(tier, decision))
            # Defence in depth: a backend is *supposed* to only return legal ids,
            # but the cascade must never let one smuggle a filtered action past
            # the compiler. An out-of-set selection is treated as an illegal call.
            if (
                decision.selected_action is not None
                and decision.selected_action not in set(legal.ids())
            ):
                attempts.append(
                    {
                        "tier": tier,
                        "rejected": "selection_outside_legal_set",
                        "selected_action": decision.selected_action,
                    }
                )
                record(decision, tier, "log_only")
                continue
            # Keep the last attempt as the record even when it abstains.
            if decision.selected_action is not None and not decision.abstained:
                accept, why = self._policy.accept_or_escalate(
                    tier,
                    confidence=decision.confidence,
                    distribution=decision.distribution,
                )
                if not accept and self._has_later_tier(tier):
                    attempts.append(
                        {"tier": tier, "escalated": True, "reason": why,
                         "selected_action": decision.selected_action}
                    )
                    record(decision, tier, "log_only")
                    continue
                record(decision, tier, "live")
                return CascadeOutcome(
                    trace_id=tid,
                    legal=legal,
                    escalation=plan,
                    tier=tier,
                    selected_action=decision.selected_action,
                    executed_action=None,
                    decision=decision,
                    attempts=tuple(attempts),
                    shadow=tuple(shadow_rows),
                    abstained=False,
                    reason=f"{tier}_selected",
                    receipts=tuple(receipts),
                    surface=surface_decision,
                )
            last = decision

        # --- nothing carried the decision ---------------------------
        attempts.append({"tier": "terminal", "abstained": True})
        return CascadeOutcome(
            trace_id=tid,
            legal=legal,
            escalation=plan,
            tier="abstained",
            selected_action=None,
            executed_action=None,
            decision=None,
            attempts=tuple(attempts),
            shadow=tuple(shadow_rows),
            abstained=True,
            reason="all_tiers_abstained",
            receipts=tuple(receipts),
            surface=surface_decision,
        )

    # Ordered learned tiers, cheapest first.
    _TIER_SEQUENCE: tuple[str, ...] = (
        "tiny_specialist",
        "bounded_jev",
        "orchestrator_slm",
        "general_slm",
        "remote_frontier",
    )
    _TIER_INDEX = {name: i for i, name in enumerate(_TIER_SEQUENCE)}
    _TIER_INDEX["deterministic"] = 0


def mark_executed(outcome: CascadeOutcome, executed_action: str | None) -> dict[str, Any]:
    """Attach what the *runtime* actually executed.

    Selection and execution are deliberately separate facts; a receipt that
    reports only the selection cannot detect the interesting disagreement.
    """
    payload = outcome.to_dict()
    payload["executed_action"] = executed_action
    payload["selected_equals_executed"] = (
        None if executed_action is None else executed_action == outcome.selected_action
    )
    return payload


__all__ = [
    "SCHEMA",
    "CascadeContext",
    "CascadeOutcome",
    "CognitionCascade",
    "mark_executed",
]