"""Replayable receipts for learned cognition decisions (z0intelligence#20).

A receipt that records only "model X chose action Y" cannot train a router and
cannot adjudicate a disagreement. Every learned decision therefore emits one of
these, capturing all of:

``state``               the observed situation the decision was made in
``eligible_candidates`` the candidate set that survived the surface's filters
``chosen``              provider + model + candidate id actually invoked
``quota``               the *observed* quota state at decision time (see below)
``latency``             budget and observed TTFT / decode / decision latency
``prediction``          the selected action and the model's confidence
``execution``           what the runtime actually did with the selection
``verified``            the later, independent quality verdict
``tokens``              input / output / cached / reasoning tokens
``cost``                GPU milliseconds + provider cost class/amount
``retries``             candidate attempts made inside the rung

Quota is carried, never computed. RPM/RPD/TPM/TPD accounting, reset timers and
free-capacity placement are owned by Kerdoios; this module only records whatever
state the caller observed so that a replay can reconstruct the decision. Passing
no quota state is normal and is recorded as ``None``.

The object is plain JSON: :meth:`CognitionReceipt.to_dict` is deterministic and
:func:`receipt_from_dict` reads it back, so a receipt is enough to replay a
decision surface pass without the model server.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Any, Mapping
import json
import time

SCHEMA = "z0int.cognition.receipt.v1"

#: Keys kept at the top level for compatibility with the shared decision-receipt
#: consumers (tokenomics / Kerdoios read ``trace_id``, ``capability_id``,
#: ``provider``, ``model`` and the token fields).
_LEGACY_KEYS = (
    "trace_id",
    "session_id",
    "capability_id",
    "provider",
    "model",
    "prediction",
    "confidence",
    "action_taken",
    "route",
    "execution",
)


def _drop_none(payload: Mapping[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in payload.items() if v is not None}


@dataclass(frozen=True)
class TokenState:
    input_tokens: int | None = None
    output_tokens: int | None = None
    cached_input_tokens: int | None = None
    reasoning_tokens: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return _drop_none(self.__dict__)


@dataclass(frozen=True)
class LatencyState:
    """Observed latency plus the budget it was measured against."""

    budget_ms: float | None = None
    observed_ms: float | None = None
    ttft_ms: float | None = None
    decode_tok_s: float | None = None
    queue_wait_ms: float | None = None
    #: Where the observation came from (`model_response`, `supervisor`, ...).
    observed_by: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return _drop_none(self.__dict__)


@dataclass(frozen=True)
class QuotaState:
    """Observed provider quota state. Never computed by z0intelligence.

    Kerdoios owns live capacity and the quota ledger; this is a transparent
    carry-through so the receipt can be replayed next to the allocator's view.
    """

    source: str | None = None
    free_tier: bool | None = None
    remaining: float | None = None
    limit: float | None = None
    resets_at: str | None = None
    exhausted: bool | None = None
    raw: Mapping[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        out = _drop_none(
            {
                "source": self.source,
                "free_tier": self.free_tier,
                "remaining": self.remaining,
                "limit": self.limit,
                "resets_at": self.resets_at,
                "exhausted": self.exhausted,
            }
        )
        if self.raw:
            out["raw"] = dict(self.raw)
        return out


@dataclass(frozen=True)
class CostState:
    """GPU and provider cost actually attributable to this decision."""

    gpu_ms: float | None = None
    gpu_vram_mib: float | None = None
    provider_cost_usd: float | None = None
    provider_cost_units: float | None = None
    cost_class: str | None = None
    source: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return _drop_none(self.__dict__)


@dataclass(frozen=True)
class ExecutionOutcome:
    """What the runtime did with the selection, and what was later verified."""

    executed_action: str | None = None
    selected_equals_executed: bool | None = None
    completed: bool | None = None
    error: str | None = None
    verified_success: bool | None = None
    verifier: str | None = None
    note: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return _drop_none(self.__dict__)


@dataclass(frozen=True)
class CognitionReceipt:
    trace_id: str
    tier: str
    execution: str = "log_only"  # live | shadow | log_only
    state: str = ""
    eligible_candidates: tuple[Mapping[str, Any], ...] = ()
    allowed_candidates: tuple[str, ...] = ()
    chosen_candidate: Mapping[str, Any] | None = None
    quota: QuotaState = field(default_factory=QuotaState)
    latency: LatencyState = field(default_factory=LatencyState)
    prediction: str | None = None
    confidence: float | None = None
    action_taken: str | None = None
    route: str | None = None
    provider: str | None = None
    model: str | None = None
    candidate_id: str | None = None
    session_id: str | None = None
    capability_id: str | None = None
    legal_ids: tuple[str, ...] = ()
    surface: Mapping[str, Any] = field(default_factory=dict)
    policy_version: str | None = None
    tokens: TokenState = field(default_factory=TokenState)
    cost: CostState = field(default_factory=CostState)
    execution_outcome: ExecutionOutcome = field(default_factory=ExecutionOutcome)
    retries: int = 0
    extra: Mapping[str, Any] = field(default_factory=dict)
    ts: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "schema": SCHEMA,
            "ts": self.ts,
            "trace_id": self.trace_id,
            "tier": self.tier,
            "execution": self.execution,
            "state": self.state,
            "eligible_candidates": [dict(c) for c in self.eligible_candidates],
            "allowed_candidates": list(self.allowed_candidates),
            "chosen_candidate": dict(self.chosen_candidate) if self.chosen_candidate else None,
            "quota": self.quota.to_dict(),
            "latency": self.latency.to_dict(),
            "latency_ms": self.latency.observed_ms,
            "prediction": self.prediction,
            "confidence": self.confidence,
            "action_taken": self.action_taken,
            "route": self.route,
            "provider": self.provider,
            "model": self.model,
            "candidate_id": self.candidate_id,
            "session_id": self.session_id,
            "capability_id": self.capability_id,
            "legal_ids": list(self.legal_ids),
            "surface": dict(self.surface),
            "policy_version": self.policy_version,
            "tokens": self.tokens.to_dict(),
            "input_tokens": self.tokens.input_tokens,
            "output_tokens": self.tokens.output_tokens,
            "cached_input_tokens": self.tokens.cached_input_tokens,
            "cost": self.cost.to_dict(),
            "execution_outcome": self.execution_outcome.to_dict(),
            "verified_outcome": _drop_none(
                {
                    "verified_success": self.execution_outcome.verified_success,
                    "verifier": self.execution_outcome.verifier,
                }
            ),
            "retries": self.retries,
            "fallbacks": self.retries,
            "extra": dict(self.extra),
        }
        # Legacy aliases: the shared consumers read `outcome`.
        if self.execution_outcome.to_dict():
            payload["outcome"] = self.execution_outcome.to_dict()
        for key in _LEGACY_KEYS:
            payload.setdefault(key, None)
        return _drop_none(payload)

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), sort_keys=True, default=str)

    # ---- late joins (selection and world are separate facts) --------
    def mark_execution(
        self,
        *,
        executed_action: str | None = None,
        completed: bool | None = None,
        error: str | None = None,
    ) -> "CognitionReceipt":
        selected_equals_executed = (
            None if executed_action is None else executed_action == self.prediction
        )
        return replace(
            self,
            execution_outcome=replace(
                self.execution_outcome,
                executed_action=executed_action,
                selected_equals_executed=selected_equals_executed,
                completed=completed,
                error=error,
            ),
        )

    def mark_verification(
        self,
        *,
        verified_success: bool,
        verifier: str | None = None,
        note: str | None = None,
    ) -> "CognitionReceipt":
        return replace(
            self,
            execution_outcome=replace(
                self.execution_outcome,
                verified_success=verified_success,
                verifier=verifier,
                note=note,
            ),
        )


def receipt_from_dict(raw: Mapping[str, Any]) -> CognitionReceipt:
    """Rebuild a receipt from its JSON form (replay without a model server)."""
    tokens = raw.get("tokens") if isinstance(raw.get("tokens"), Mapping) else {}
    latency = raw.get("latency") if isinstance(raw.get("latency"), Mapping) else {}
    cost = raw.get("cost") if isinstance(raw.get("cost"), Mapping) else {}
    quota = raw.get("quota") if isinstance(raw.get("quota"), Mapping) else {}
    outcome = (
        raw.get("execution_outcome")
        if isinstance(raw.get("execution_outcome"), Mapping)
        else {}
    )
    return CognitionReceipt(
        trace_id=str(raw.get("trace_id") or ""),
        tier=str(raw.get("tier") or ""),
        execution=str(raw.get("execution") or "log_only"),
        state=str(raw.get("state") or ""),
        eligible_candidates=tuple(raw.get("eligible_candidates") or ()),
        allowed_candidates=tuple(raw.get("allowed_candidates") or ()),
        chosen_candidate=raw.get("chosen_candidate"),
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
            budget_ms=latency.get("budget_ms"),
            observed_ms=latency.get("observed_ms", raw.get("latency_ms")),
            ttft_ms=latency.get("ttft_ms"),
            decode_tok_s=latency.get("decode_tok_s"),
            queue_wait_ms=latency.get("queue_wait_ms"),
            observed_by=latency.get("observed_by"),
        ),
        prediction=raw.get("prediction"),
        confidence=raw.get("confidence"),
        action_taken=raw.get("action_taken"),
        route=raw.get("route"),
        provider=raw.get("provider"),
        model=raw.get("model"),
        candidate_id=raw.get("candidate_id"),
        session_id=raw.get("session_id"),
        capability_id=raw.get("capability_id"),
        legal_ids=tuple(raw.get("legal_ids") or ()),
        surface=dict(raw.get("surface") or {}),
        policy_version=raw.get("policy_version"),
        tokens=TokenState(
            input_tokens=tokens.get("input_tokens", raw.get("input_tokens")),
            output_tokens=tokens.get("output_tokens", raw.get("output_tokens")),
            cached_input_tokens=tokens.get(
                "cached_input_tokens", raw.get("cached_input_tokens")
            ),
            reasoning_tokens=tokens.get("reasoning_tokens"),
        ),
        cost=CostState(
            gpu_ms=cost.get("gpu_ms"),
            gpu_vram_mib=cost.get("gpu_vram_mib"),
            provider_cost_usd=cost.get("provider_cost_usd"),
            provider_cost_units=cost.get("provider_cost_units"),
            cost_class=cost.get("cost_class"),
            source=cost.get("source"),
        ),
        execution_outcome=ExecutionOutcome(
            executed_action=outcome.get("executed_action"),
            selected_equals_executed=outcome.get("selected_equals_executed"),
            completed=outcome.get("completed"),
            error=outcome.get("error"),
            verified_success=outcome.get("verified_success"),
            verifier=outcome.get("verifier"),
            note=outcome.get("note"),
        ),
        retries=int(raw.get("retries") or 0),
        extra=dict(raw.get("extra") or {}),
        ts=float(raw.get("ts") or time.time()),
    )


__all__ = [
    "SCHEMA",
    "CognitionReceipt",
    "CostState",
    "ExecutionOutcome",
    "LatencyState",
    "QuotaState",
    "TokenState",
    "receipt_from_dict",
]
