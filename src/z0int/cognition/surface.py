"""The decision surface: suitability, uncertainty, required class, escalation.

This module answers exactly four questions, and is the only place that does:

1. **Which candidate models are semantically suitable?** — every candidate is
   run through the shared hard filters in :mod:`z0int.cognition.candidates`
   (served tier, capabilities, required quality class, risk ceiling, cost class)
   and the survivors are ranked by semantic suitability.
2. **How uncertain is this decision?** — a deterministic function of observed
   signals (candidate entropy, top-1/top-2 margin, state novelty, novel tool
   combination, historical failure family). It is *not* a function of prompt
   length, and it never looks at the text of the prompt. Verification and
   capability demand lift the required class directly instead of masquerading as
   uncertainty.
3. **What quality/risk class is required?** — derived from the risk class, the
   verification demand and the capability demand, then raised by measured
   uncertainty. Versioned thresholds so the policy itself can be benchmarked.
4. **Should we escalate?** — yes when the required class sits above the tier the
   escalation policy proposed, when uncertainty crosses the threshold, or when
   the proposed tier has no eligible candidate but a later one does. When no
   rung has an eligible candidate the surface *fails closed*: it returns an empty
   allowed set rather than quietly lowering the bar to spend free quota.

Ownership: this module never accounts for quota, RPM/RPD/TPM/TPD, reset timers or
GPU placement (Kerdoios owns those), and never reads a provider catalog directly
(DSH's ``llm-pi-ai`` owns that). It consumes observed quota *state* only to carry
it into receipts.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping, Sequence

from .candidates import (
    QUALITY_CLASSES,
    QUALITY_TIER_FLOOR,
    CandidateModel,
    CapabilityRequirement,
    Rejection,
    Suitability,
    filter_candidates,
    quality_at_least,
    quality_rank,
    rank_candidates,
)
from .escalation import EscalationSignals, at_least

SCHEMA = "z0int.cognition.surface.v1"
SURFACE_VERSION = "surface-v1"

#: Capability demands that lift the required quality class. Keyed by the value a
#: caller puts in ``SurfaceRequest.capability_required``.
_CAPABILITY_QUALITY: Mapping[str, str] = {
    "tool_calling": "standard",
    "multi_turn": "standard",
    "orchestration": "standard",
    "reasoning": "strong",
    "long_context": "standard",
    "vision": "standard",
    "verification": "strong",
}


@dataclass(frozen=True)
class SurfaceThresholds:
    """Versioned, inspectable knobs. Change these, not the control flow."""

    version: str = SURFACE_VERSION
    # Required quality class before any uncertainty/risk/capability lift.
    default_quality_class: str = "bounded"
    # Any risk class the escalation policy calls high gets the standard class, so
    # the surface's risk floor matches the measured escalation policy
    # (``high_risk_classes -> orchestrator_slm``) instead of silently raising it.
    high_risk_quality_class: str = "standard"
    # Independent verification means a bounded scorer is not enough.
    verification_quality_class: str = "standard"
    # Capability demand raises the class as declared in ``_CAPABILITY_QUALITY``.
    # Uncertainty at or above this escalates and lifts the class.
    uncertainty_escalate: float = 0.5
    high_uncertainty_quality_class: str = "strong"
    # When set, candidates costlier than this class are filtered (fail closed
    # before paid spill). ``None`` means "no cost ceiling".
    max_cost_class: str | None = None

    def __post_init__(self) -> None:
        for name in (
            self.default_quality_class,
            self.high_risk_quality_class,
            self.verification_quality_class,
            self.high_uncertainty_quality_class,
        ):
            quality_rank(name)  # validates
        if not 0.0 <= self.uncertainty_escalate <= 1.0:
            raise ValueError("uncertainty_escalate must be in [0, 1]")
        if self.max_cost_class is not None and not str(self.max_cost_class).strip():
            raise ValueError("max_cost_class must be nonempty when set")

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "default_quality_class": self.default_quality_class,
            "high_risk_quality_class": self.high_risk_quality_class,
            "verification_quality_class": self.verification_quality_class,
            "uncertainty_escalate": self.uncertainty_escalate,
            "high_uncertainty_quality_class": self.high_uncertainty_quality_class,
            "max_cost_class": self.max_cost_class,
        }


@dataclass(frozen=True)
class SurfaceRequest:
    """Everything the surface needs to answer the four questions.

    ``legal_ids`` is the already-compiled legal action set. The surface only ever
    narrows; it cannot add an action, and it records the set it was given so a
    replay can prove the compile happened first.
    """

    tier: str
    legal_ids: tuple[str, ...] = ()
    state: str = ""
    required: CapabilityRequirement = field(default_factory=CapabilityRequirement)
    risk_class: str = "read"
    verification_required: bool = False
    capability_required: str | None = None
    signals: EscalationSignals | None = None
    high_risk_classes: tuple[str, ...] = ("destructive", "credential", "payment", "publish")
    max_cost_class: str | None = None
    latency_budget_ms: float | None = None
    estimated_latency_ms: Mapping[str, float] = field(default_factory=dict)


@dataclass(frozen=True)
class SurfaceDecision:
    """The surface's replayable answer. Deterministic given the same inputs."""

    tier: str
    proposed_tier: str
    quality_class_required: str
    risk_class: str
    uncertainty: float
    escalate: bool
    escalate_reason: str | None
    fail_closed: bool
    enabled: bool
    eligible: tuple[CandidateModel, ...]
    suitable: tuple[Suitability, ...]
    rejected: tuple[Rejection, ...]
    legal_ids: tuple[str, ...]
    policy_version: str
    thresholds: Mapping[str, Any]

    @property
    def eligible_ids(self) -> tuple[str, ...]:
        return tuple(c.candidate_id for c in self.eligible)

    @property
    def allowed_candidates(self) -> tuple[str, ...]:
        """Semantically suitable candidates, best-first. Placement is Kerdoios'."""
        order = {s.candidate_id: i for i, s in enumerate(self.suitable)}
        return tuple(sorted(self.eligible_ids, key=lambda cid: order[cid]))

    def eligible_for_tier(self, tier: str) -> tuple[CandidateModel, ...]:
        return tuple(c for c in self.eligible if c.serves(tier))

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": SCHEMA,
            "proposed_tier": self.proposed_tier,
            "tier": self.tier,
            "quality_class_required": self.quality_class_required,
            "risk_class": self.risk_class,
            "uncertainty": self.uncertainty,
            "escalate": self.escalate,
            "escalate_reason": self.escalate_reason,
            "fail_closed": self.fail_closed,
            "enabled": self.enabled,
            "policy_version": self.policy_version,
            "legal_ids": list(self.legal_ids),
            "eligible_candidate_ids": list(self.eligible_ids),
            "allowed_candidates": list(self.allowed_candidates),
            "suitable": [s.to_dict() for s in self.suitable],
            "rejected": [r.to_dict() for r in self.rejected],
            "candidates": [c.to_dict() for c in self.eligible],
            "thresholds": dict(self.thresholds),
        }


def uncertainty_of(signals: EscalationSignals | None) -> float:
    """Deterministic uncertainty in ``[0, 1]`` from observed signals only.

    Each available signal contributes one normalised term; the result is their
    mean. With no signals at all the uncertainty is 0.0, which keeps the
    deterministic and singleton paths unchanged.
    """
    if signals is None:
        return 0.0
    terms: list[float] = []

    def clamp(value: float) -> float:
        return max(0.0, min(1.0, float(value)))

    if signals.candidate_entropy is not None:
        terms.append(clamp(signals.candidate_entropy))
    if signals.top1_top2_margin is not None:
        terms.append(clamp(1.0 - float(signals.top1_top2_margin)))
    if signals.state_novelty is not None:
        terms.append(clamp(signals.state_novelty))
    if signals.novel_tool_combination:
        terms.append(1.0)
    if signals.historical_failure_family:
        terms.append(1.0)
    # ``verification_required`` and ``capability_required`` are deliberately NOT
    # uncertainty terms: they lift the required quality class directly in
    # :meth:`DecisionSurface.required_quality_class`. Counting them here too would
    # make every verified decision look maximally uncertain.
    if not terms:
        return 0.0
    return round(sum(terms) / len(terms), 6)


class DecisionSurface:
    """Filter candidates and decide whether the proposed rung is enough."""

    def __init__(
        self,
        candidates: Sequence[CandidateModel] = (),
        *,
        thresholds: SurfaceThresholds | None = None,
        enforce: bool = True,
    ) -> None:
        self._candidates = tuple(candidates)
        self._thresholds = thresholds or SurfaceThresholds()
        self._enforce = bool(enforce) and bool(self._candidates)

    @property
    def thresholds(self) -> SurfaceThresholds:
        return self._thresholds

    @property
    def enabled(self) -> bool:
        """True when there is a candidate inventory to enforce against."""
        return self._enforce

    @property
    def candidates(self) -> tuple[CandidateModel, ...]:
        return self._candidates

    # ---- question 3: required quality class -------------------------
    def required_quality_class(
        self,
        request: SurfaceRequest,
        *,
        uncertainty: float | None = None,
    ) -> str:
        t = self._thresholds
        quality = t.default_quality_class
        if request.risk_class in request.high_risk_classes:
            quality = quality_at_least(quality, t.high_risk_quality_class)
        if request.verification_required:
            quality = quality_at_least(quality, t.verification_quality_class)
        if request.capability_required:
            quality = quality_at_least(
                quality,
                _CAPABILITY_QUALITY.get(str(request.capability_required), t.default_quality_class),
            )
        measured = uncertainty_of(request.signals) if uncertainty is None else uncertainty
        if measured >= t.uncertainty_escalate:
            quality = quality_at_least(quality, t.high_uncertainty_quality_class)
        return quality

    # ---- the assessment ---------------------------------------------
    def assess(self, request: SurfaceRequest) -> SurfaceDecision:
        uncertainty = uncertainty_of(request.signals)
        quality_required = self.required_quality_class(request, uncertainty=uncertainty)
        thresholds_payload = self._thresholds.to_dict()

        if not request.legal_ids:
            # No legal action exists. This is not an escalation, it is a refusal.
            return SurfaceDecision(
                tier="abstained",
                proposed_tier=request.tier,
                quality_class_required=quality_required,
                risk_class=request.risk_class,
                uncertainty=uncertainty,
                escalate=False,
                escalate_reason="no_legal_actions",
                fail_closed=False,
                enabled=self._enforce,
                eligible=(),
                suitable=(),
                rejected=(),
                legal_ids=(),
                policy_version=self._thresholds.version,
                thresholds=thresholds_payload,
            )

        proposed = request.tier
        if not self._enforce:
            # No inventory: preserve the escalation policy's tier exactly.
            return SurfaceDecision(
                tier=proposed,
                proposed_tier=proposed,
                quality_class_required=quality_required,
                risk_class=request.risk_class,
                uncertainty=uncertainty,
                escalate=False,
                escalate_reason=None,
                fail_closed=False,
                enabled=False,
                eligible=(),
                suitable=(),
                rejected=(),
                legal_ids=tuple(request.legal_ids),
                policy_version=self._thresholds.version,
                thresholds=thresholds_payload,
            )

        max_cost_class = request.max_cost_class or self._thresholds.max_cost_class
        tier_floor = QUALITY_TIER_FLOOR[quality_required]
        tier = at_least(proposed, tier_floor)
        escalate = tier != proposed
        reason: str | None = None
        if escalate:
            reason = f"quality_class:{quality_required}"

        if uncertainty >= self._thresholds.uncertainty_escalate:
            escalate = True
            reason = f"uncertainty:{uncertainty:.3f}"

        eligible, rejected = filter_candidates(
            self._candidates,
            tier=tier,
            required=request.required,
            quality_class_required=quality_required,
            risk_class=request.risk_class,
            max_cost_class=max_cost_class,
        )

        # If the proposed rung has nothing suitable, climb rather than lower the
        # bar. Lowering the bar is how free quota silently becomes bad answers.
        if not eligible:
            for later in _later_tiers(tier):
                candidate_set, candidate_rejections = filter_candidates(
                    self._candidates,
                    tier=later,
                    required=request.required,
                    quality_class_required=quality_required,
                    risk_class=request.risk_class,
                    max_cost_class=max_cost_class,
                )
                if candidate_set:
                    eligible = candidate_set
                    rejected = candidate_rejections
                    tier = later
                    escalate = True
                    reason = reason or f"no_eligible_candidate_at:{proposed}"
                    break

        fail_closed = not eligible
        if fail_closed:
            # Fail closed is the headline: an empty allowed set is never a reason
            # to drop the quality bar and pick a cheaper candidate.
            escalate = True
            reason = "no_eligible_candidate"

        suitable = rank_candidates(
            eligible,
            required=request.required,
            latency_budget_ms=request.latency_budget_ms,
            estimated_latency_ms=request.estimated_latency_ms,
        )

        return SurfaceDecision(
            tier=tier,
            proposed_tier=proposed,
            quality_class_required=quality_required,
            risk_class=request.risk_class,
            uncertainty=uncertainty,
            escalate=escalate,
            escalate_reason=reason,
            fail_closed=fail_closed,
            enabled=True,
            eligible=tuple(eligible),
            suitable=tuple(suitable),
            rejected=tuple(rejected),
            legal_ids=tuple(request.legal_ids),
            policy_version=self._thresholds.version,
            thresholds=thresholds_payload,
        )


def _later_tiers(tier: str) -> tuple[str, ...]:
    from .escalation import TIERS

    try:
        index = TIERS.index(tier)
    except ValueError:
        return ()
    return tuple(TIERS[index + 1 :])


__all__ = [
    "SCHEMA",
    "SURFACE_VERSION",
    "DecisionSurface",
    "SurfaceDecision",
    "SurfaceRequest",
    "SurfaceThresholds",
    "uncertainty_of",
]
