"""Fail-closed retry policy for world mutations.

The router should retry cognition/provider failures aggressively when useful,
but world mutations require effect evidence. This module chooses a disposition;
it does not execute the retry or grant authority.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from z0int.cognition.mutation_outcome import MutationOutcome

Effect = Literal["none", "unknown", "observed"]


@dataclass(frozen=True)
class MutationCapabilities:
    """Capabilities available at the effect boundary for one operation."""

    can_observe: bool = False
    stable_mutation_key: bool = False
    receiver_durable_idempotency: bool = False


def decide_mutation_outcome(
    *,
    mutation_key: str,
    authority_scope: str,
    attempted: bool,
    effect: Effect,
    capabilities: MutationCapabilities,
    no_effect_evidence: str | None = None,
    durable_idempotency_evidence: str | None = None,
    mutation_hash: str | None = None,
    observed_effect_hash: str | None = None,
    verification_evidence: str | None = None,
    unresolved_evidence: str | None = None,
) -> MutationOutcome:
    """Choose the safest automatic disposition from observed capabilities.

    Ordering for an ambiguous attempted effect:

    1. durable same-identity resend, when its proof is complete;
    2. fresh observation/reconciliation;
    3. preserve ambiguity for a higher-level reconciler.

    Durable resend is preferred over observation because it can both reconcile
    and complete the request in one receiver-side operation, but only when the
    receiver's identity/content binding is proven.
    """
    if effect == "observed":
        evidence = verification_evidence or unresolved_evidence
        return MutationOutcome(
            mutation_key=mutation_key,
            authority_scope=authority_scope,
            attempted=attempted,
            effect="observed",
            verification="verified" if verification_evidence else "unverified",
            retry_disposition="stop",
            evidence_ref=evidence,
            effect_hash=observed_effect_hash,
        )

    if effect == "none":
        return MutationOutcome(
            mutation_key=mutation_key,
            authority_scope=authority_scope,
            attempted=attempted,
            effect="none",
            verification="unverified",
            retry_disposition="retry",
            evidence_ref=no_effect_evidence,
        )

    durable_resend = (
        attempted
        and capabilities.stable_mutation_key
        and capabilities.receiver_durable_idempotency
        and bool(durable_idempotency_evidence)
        and bool(mutation_hash)
    )
    if durable_resend:
        return MutationOutcome(
            mutation_key=mutation_key,
            authority_scope=authority_scope,
            attempted=True,
            effect="unknown",
            verification="unverified",
            retry_disposition="resend",
            idempotency="receiver-durable",
            mutation_hash=mutation_hash,
            evidence_ref=durable_idempotency_evidence,
        )

    if attempted and capabilities.can_observe:
        return MutationOutcome(
            mutation_key=mutation_key,
            authority_scope=authority_scope,
            attempted=True,
            effect="unknown",
            verification="unverified",
            retry_disposition="observe",
        )

    return MutationOutcome(
        mutation_key=mutation_key,
        authority_scope=authority_scope,
        attempted=True,
        effect="unknown",
        verification="unverified",
        retry_disposition="escalate",
        evidence_ref=unresolved_evidence or "mutation-outcome:unresolved",
    )


__all__ = ["MutationCapabilities", "decide_mutation_outcome"]
