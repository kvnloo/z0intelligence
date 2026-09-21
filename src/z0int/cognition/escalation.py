"""Measured escalation policy — never "easy prompt -> small model".

Escalation is decided from *observed* signals: how many legal actions survived the
compiler, how peaked the candidate distribution is, whether a bounded scorer
abstained, how novel the tool combination is, the risk class, whether independent
verification is required, and the latency budget.

The policy is a pure function of those signals and an explicit, versioned
threshold table so it can itself be benchmarked as a policy (evolution-lab#23)
rather than being re-tuned by hand.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping

EscalationTier = str  # one of TIERS

TIERS: tuple[str, ...] = (
    "deterministic",
    "tiny_specialist",
    "bounded_jev",
    "orchestrator_slm",
    "general_slm",
    "remote_frontier",
)

# Ordered cheapest -> most expensive. Escalation moves strictly rightwards.
_TIER_ORDER = {name: i for i, name in enumerate(TIERS)}


@dataclass(frozen=True)
class EscalationThresholds:
    """Versioned, inspectable knobs. Change these, not the control flow."""

    version: str = "escalation-v1"
    # A bounded scorer is trusted when its confidence clears this.
    jev_min_confidence: float = 0.65
    # Below this top-1/top-2 margin the choice is too close to trust.
    min_top1_top2_margin: float = 0.15
    # Tiers whose *answer* is re-checked against the thresholds above. A bounded
    # scorer that always answers would otherwise make the rest of the ladder
    # decorative: measured on NanoJev, it decided 25/28 fixtures and never
    # abstained, so the orchestrator and the general fallback never ran.
    confidence_checked_tiers: tuple[str, ...] = ("tiny_specialist", "bounded_jev")
    # Above this normalised entropy the legal set is effectively unordered.
    max_candidate_entropy: float = 0.55
    # Semantic orchestration is required above this many legal actions...
    orchestrator_min_actions: int = 5
    # ...or for any of these capabilities/risk conditions.
    orchestrator_on_novel_combination: bool = True
    orchestrator_on_historical_failure: bool = True
    # Verification-required work never stops at a bounded scorer.
    verify_min_tier: str = "orchestrator_slm"
    # Risk classes that always get the strongest local semantic tier.
    high_risk_classes: tuple[str, ...] = ("destructive", "credential", "payment", "publish")
    # When the remaining latency budget is under this, do not start a new tier.
    latency_budget_floor_ms: float = 1500.0
    # A tiny specialist is credited only for these families (populated from
    # measured promotion evidence; empty means none are credited).
    credited_families: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "jev_min_confidence": self.jev_min_confidence,
            "min_top1_top2_margin": self.min_top1_top2_margin,
            "confidence_checked_tiers": list(self.confidence_checked_tiers),
            "max_candidate_entropy": self.max_candidate_entropy,
            "orchestrator_min_actions": self.orchestrator_min_actions,
            "orchestrator_on_novel_combination": self.orchestrator_on_novel_combination,
            "orchestrator_on_historical_failure": self.orchestrator_on_historical_failure,
            "verify_min_tier": self.verify_min_tier,
            "high_risk_classes": list(self.high_risk_classes),
            "latency_budget_floor_ms": self.latency_budget_floor_ms,
            "credited_families": list(self.credited_families),
        }


@dataclass(frozen=True)
class EscalationSignals:
    legal_action_count: int
    candidate_entropy: float | None = None
    top1_top2_margin: float | None = None
    backend_confidence: float | None = None
    specialist_abstained: bool | None = None
    jev_uncertain: bool | None = None
    novel_tool_combination: bool = False
    state_novelty: float | None = None
    historical_failure_family: bool = False
    risk_class: str = "read"
    verification_required: bool = False
    expected_utility: float | None = None
    latency_budget_ms: float | None = None
    estimated_candidate_latency_ms: float | None = None
    capability_required: str | None = None
    families: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {k: v for k, v in self.__dict__.items() if v is not None}


@dataclass(frozen=True)
class EscalationDecision:
    tier: str
    reason: str
    signals: Mapping[str, Any] = field(default_factory=dict)
    policy_version: str = "escalation-v1"

    def to_dict(self) -> dict[str, Any]:
        return {
            "tier": self.tier,
            "reason": self.reason,
            "policy_version": self.policy_version,
            "signals": dict(self.signals),
        }


def at_least(tier: str, floor: str) -> str:
    """Return whichever of the two tiers is more expensive."""
    return tier if _TIER_ORDER[tier] >= _TIER_ORDER[floor] else floor


class EscalationPolicy:
    """Choose the cheapest tier that measured evidence says can carry the decision."""

    def __init__(self, thresholds: EscalationThresholds | None = None) -> None:
        self.thresholds = thresholds or EscalationThresholds()

    # ---- helpers ---------------------------------------------------
    def accept_or_escalate(
        self,
        tier: str,
        *,
        confidence: float | None,
        distribution: Mapping[str, float] | None,
    ) -> tuple[bool, str | None]:
        """Should this tier's answer be accepted, or handed to the next tier?

        Returns ``(accept, reason_to_escalate)``. Tiers not listed in
        ``confidence_checked_tiers`` are always accepted -- this is only about
        re-checking a *bounded* scorer, not about second-guessing a generative
        model that was explicitly escalated to.
        """
        t = self.thresholds
        if tier not in t.confidence_checked_tiers:
            return True, None
        if confidence is not None and confidence < t.jev_min_confidence:
            return False, f"confidence {confidence:.3f} < {t.jev_min_confidence}"
        margin = margin_of(distribution)
        if margin is not None and margin < t.min_top1_top2_margin:
            return False, f"top1-top2 margin {margin:.3f} < {t.min_top1_top2_margin}"
        entropy = entropy_of(distribution)
        if entropy is not None and entropy > t.max_candidate_entropy:
            return False, f"entropy {entropy:.3f} > {t.max_candidate_entropy}"
        return True, None

    def _budget_exhausted(self, s: EscalationSignals) -> bool:
        if s.latency_budget_ms is None:
            return False
        need = s.estimated_candidate_latency_ms or 0.0
        remaining = float(s.latency_budget_ms)
        return remaining - need <= self.thresholds.latency_budget_floor_ms

    def decide(self, signals: EscalationSignals) -> EscalationDecision:
        t = self.thresholds
        base = signals.to_dict()

        if signals.legal_action_count == 0:
            return EscalationDecision(
                tier="deterministic",
                reason="no_legal_actions",
                signals=base,
                policy_version=t.version,
            )

        # A single surviving action is not a decision at all.
        if signals.legal_action_count == 1:
            return EscalationDecision(
                tier="deterministic",
                reason="singleton_legal_set",
                signals=base,
                policy_version=t.version,
            )

        tier = "tiny_specialist"
        reason = "default_tiny_specialist"

        tiny_credited = bool(
            t.credited_families
            and signals.families
            and all(f in t.credited_families for f in signals.families)
        )
        if not tiny_credited:
            tier = "bounded_jev"
            reason = "no_credited_tiny_specialist"

        # Bounded scorer is only trusted when it is actually decisive.
        uncertain = (
            signals.jev_uncertain is True
            or signals.specialist_abstained is True
            or (
                signals.backend_confidence is not None
                and signals.backend_confidence < t.jev_min_confidence
            )
            or (
                signals.top1_top2_margin is not None
                and signals.top1_top2_margin < t.min_top1_top2_margin
            )
            or (
                signals.candidate_entropy is not None
                and signals.candidate_entropy > t.max_candidate_entropy
            )
        )
        if uncertain:
            tier = "orchestrator_slm"
            reason = "bounded_scorer_uncertain"

        # Genuine semantic orchestration demand.
        needs_semantics = (
            signals.legal_action_count >= t.orchestrator_min_actions
            or (t.orchestrator_on_novel_combination and signals.novel_tool_combination)
            or (t.orchestrator_on_historical_failure and signals.historical_failure_family)
            or bool(signals.capability_required)
        )
        if needs_semantics:
            tier = at_least(tier, "orchestrator_slm")
            if reason == "default_tiny_specialist":
                reason = "semantic_orchestration_required"

        # Risk and verification raise the floor; they never lower it.
        if signals.risk_class in t.high_risk_classes:
            tier = at_least(tier, "orchestrator_slm")
            reason = f"risk_class:{signals.risk_class}"
        if signals.verification_required:
            tier = at_least(tier, t.verify_min_tier)
            reason = "independent_verification_required"

        # General reasoning is needed when the choice is not reducible to a
        # bounded pick among known actions.
        if signals.state_novelty is not None and signals.state_novelty >= 1.0:
            tier = at_least(tier, "general_slm")
            reason = "novel_state_requires_general_reasoning"

        # If we cannot afford the chosen tier locally, hand off rather than stall.
        if self._budget_exhausted(signals):
            tier = "remote_frontier"
            reason = "local_latency_budget_exhausted"

        return EscalationDecision(
            tier=tier, reason=reason, signals=base, policy_version=t.version
        )


def entropy_of(distribution: Mapping[str, float] | None) -> float | None:
    """Normalised Shannon entropy in [0,1] over a probability distribution."""
    if not distribution:
        return None
    import math

    values = [float(v) for v in distribution.values() if float(v) > 0.0]
    if not values:
        return None
    if len(distribution) <= 1:
        return 0.0
    total = sum(values)
    if total <= 0:
        return None
    probs = [v / total for v in values]
    h = -sum(p * math.log(p) for p in probs)
    return h / math.log(len(distribution))


def margin_of(distribution: Mapping[str, float] | None) -> float | None:
    """Top-1 minus top-2 probability; None when there is nothing to compare."""
    if not distribution:
        return None
    values = sorted((float(v) for v in distribution.values()), reverse=True)
    if len(values) < 2:
        return None
    return values[0] - values[1]


__all__ = [
    "TIERS",
    "EscalationDecision",
    "EscalationPolicy",
    "EscalationSignals",
    "EscalationThresholds",
    "at_least",
    "entropy_of",
    "margin_of",
]
