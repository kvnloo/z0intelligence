"""Mutation-outcome state for separating cognition retries from world retries.

A failed provider/decision call is cheap to retry. A failed *world mutation* is
different: once dispatch may have crossed the effect boundary, the caller must
either observe, resend the identical mutation under durable receiver-side
idempotency, or preserve the ambiguity.

This module intentionally has no AODL runtime dependency; its JSON shape is
compatible with the Frontier Lab `mutation-outcome/v0` receipt profile.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal
import re

Effect = Literal["none", "unknown", "observed"]
Verification = Literal["unverified", "verified"]
RetryDisposition = Literal["retry", "observe", "resend", "escalate", "stop"]
Idempotency = Literal["none", "receiver-durable"]

SCHEMA = "mutation-outcome/v0"
_SHA256_RE = re.compile(r"^[a-f0-9]{64}$")
_EFFECTS = {"none", "unknown", "observed"}
_VERIFICATIONS = {"unverified", "verified"}
_DISPOSITIONS = {"retry", "observe", "resend", "escalate", "stop"}
_IDEMPOTENCY = {"none", "receiver-durable"}


@dataclass(frozen=True)
class MutationOutcome:
    mutation_key: str
    authority_scope: str
    attempted: bool
    effect: Effect
    verification: Verification
    retry_disposition: RetryDisposition
    idempotency: Idempotency = "none"
    mutation_hash: str | None = None
    evidence_ref: str | None = None
    effect_hash: str | None = None

    def __post_init__(self) -> None:
        if self.effect not in _EFFECTS:
            raise ValueError("effect must be none|unknown|observed")
        if self.verification not in _VERIFICATIONS:
            raise ValueError("verification must be unverified|verified")
        if self.retry_disposition not in _DISPOSITIONS:
            raise ValueError("retry_disposition must be retry|observe|resend|escalate|stop")
        if self.idempotency not in _IDEMPOTENCY:
            raise ValueError("idempotency must be none|receiver-durable")
        if self.mutation_hash is not None and not _SHA256_RE.fullmatch(self.mutation_hash):
            raise ValueError("mutation_hash must be lowercase sha256 hex")
        if self.effect_hash is not None and not _SHA256_RE.fullmatch(self.effect_hash):
            raise ValueError("effect_hash must be lowercase sha256 hex")
        if not self.mutation_key:
            raise ValueError("mutation_key must be non-empty")
        if not self.authority_scope:
            raise ValueError("authority_scope must be non-empty")
        if not self.attempted and self.effect != "none":
            raise ValueError("an unattempted mutation cannot have an unknown/observed effect")

        if self.effect == "unknown" and self.retry_disposition not in {
            "observe",
            "resend",
            "escalate",
        }:
            raise ValueError(
                "an ambiguous mutation must observe, resend the same durable identity, or escalate"
            )

        if self.retry_disposition == "retry":
            if self.effect != "none":
                raise ValueError("retry requires proof that the previous effect is none")
            if self.attempted and not self.evidence_ref:
                raise ValueError("retry after an attempted mutation requires no-effect evidence")

        if self.retry_disposition == "resend":
            if not (self.attempted and self.effect == "unknown"):
                raise ValueError("resend is only valid for an attempted mutation with unknown effect")
            if self.idempotency != "receiver-durable":
                raise ValueError("resend requires receiver-durable idempotency")
            if not self.mutation_hash:
                raise ValueError("resend requires a stable mutation_hash")
            if not self.evidence_ref:
                raise ValueError("resend requires durable-idempotency evidence")

        if self.retry_disposition == "observe":
            if not (self.attempted and self.effect == "unknown"):
                raise ValueError("observe is only valid for an attempted mutation with unknown effect")

        if self.retry_disposition == "escalate":
            if not (self.attempted and self.effect == "unknown"):
                raise ValueError("escalate is only valid for an attempted mutation with unknown effect")
            if not self.evidence_ref:
                raise ValueError("escalate requires unresolved-boundary evidence")

        if self.effect == "observed":
            if self.retry_disposition != "stop":
                raise ValueError("observed effects must stop automatic retry")
            if not self.effect_hash:
                raise ValueError("observed effects require effect_hash")

        if self.verification == "verified":
            if self.retry_disposition != "stop":
                raise ValueError("verified outcomes must stop automatic retry")
            if not self.evidence_ref:
                raise ValueError("verified outcomes require evidence_ref")

        if self.retry_disposition == "stop" and self.effect == "unknown":
            raise ValueError("unknown effects cannot be silently converted to stop/success")

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "receiptKind": SCHEMA,
            "mutationKey": self.mutation_key,
            "authorityScope": self.authority_scope,
            "attempted": self.attempted,
            "effect": self.effect,
            "verification": self.verification,
            "retryDisposition": self.retry_disposition,
        }
        if self.idempotency != "none":
            out["idempotency"] = self.idempotency
        if self.mutation_hash is not None:
            out["mutationHash"] = self.mutation_hash
        if self.evidence_ref is not None:
            out["evidenceRef"] = self.evidence_ref
        if self.effect_hash is not None:
            out["effectHash"] = self.effect_hash
        return out


def mutation_outcome_from_dict(raw: dict[str, Any]) -> MutationOutcome:
    return MutationOutcome(
        mutation_key=str(raw.get("mutationKey") or ""),
        authority_scope=str(raw.get("authorityScope") or ""),
        attempted=bool(raw.get("attempted")),
        effect=raw.get("effect"),
        verification=raw.get("verification"),
        retry_disposition=raw.get("retryDisposition"),
        idempotency=raw.get("idempotency", "none"),
        mutation_hash=raw.get("mutationHash"),
        evidence_ref=raw.get("evidenceRef"),
        effect_hash=raw.get("effectHash"),
    )


__all__ = ["SCHEMA", "MutationOutcome", "mutation_outcome_from_dict"]
