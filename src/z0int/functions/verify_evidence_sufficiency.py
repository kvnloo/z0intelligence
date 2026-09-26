"""``verify.evidence_sufficiency`` — the function both verified models implement.

    state + query + evidence  ->  P(evidence is sufficient to answer correctly)
                              ->  ALLOW | ABSTAIN | ESCALATE
                              ->  z0int.decision_receipt.v1

No routing decision is made here. The caller (or the router that owns function
selection) names a backend, or asks for ``auto``; escalation is offered as an
explicit, self-contained composition that records *both* physical calls.

Escalation rule, stated plainly: the fast path answers; if its answer lands in
the abstain band the same question is put to the reference, in the same trace.
An uncertain fast answer is not a failure — it is the case escalation exists for.
"""

from __future__ import annotations

import time
from typing import Any, Protocol

from ..receipt import build_receipt, new_trace_id
from .contract import (
    ACTIONS,
    DEFAULT_ALLOW_AT,
    DEFAULT_ESCALATE_BELOW,
    FUNCTION_ID,
    PHRASINGS,
    PROPOSITION,
    BackendCapabilities,
    PhysicalCall,
    VerificationResult,
    VerifierUnavailable,
    action_of,
    is_uncertain,
    render_state,
    state_digest,
)
from .jev import JevVerifier
from .laya import LayaVerifier

__all__ = [
    "verify", "verify_with_escalation", "capabilities", "implementations",
    "FUNCTION_ID", "PROPOSITION", "PHRASINGS",
]


class _Implementation(Protocol):
    backend: str
    role: str

    def capabilities(self, *, available: bool | None = None) -> BackendCapabilities: ...
    def health(self) -> tuple[bool, str]: ...
    def verify(self, state: str, *, proposition: str = ...) -> tuple[float, dict[str, Any]]: ...


_FACTORIES = {"laya": LayaVerifier, "jev": JevVerifier}


def implementations() -> dict[str, _Implementation]:
    return {name: factory() for name, factory in _FACTORIES.items()}


def capabilities() -> list[dict[str, Any]]:
    """Metadata for the router: availability, role, latency, validation, version."""
    return [impl.capabilities().to_dict() for impl in implementations().values()]


def _coerce_state(state: str | dict[str, Any]) -> str:
    """Render a packet, or pass a pre-rendered state through.

    The query requirement lives here, not in the renderer: the renderer is the
    validated formatter and must stay byte-identical to it, while the function
    must refuse a packet whose proposition ("the user's query") has no referent.
    """
    if not isinstance(state, dict):
        return state
    if not str(state.get("query") or "").strip():
        raise ValueError("state packet has no 'query'; the proposition is unjudgeable")
    return render_state(state)


def _receipt(
    *,
    result_backend: str,
    model: str | None,
    revision: str | None,
    p_true: float,
    p_false: float,
    action: str,
    latency_ms: float,
    trace_id: str,
    state: str,
    proposition: str,
    allow_at: float,
    escalate_below: float,
    calls: tuple[PhysicalCall, ...],
    escalated: bool,
    provenance: dict[str, Any] | None,
    capabilities_block: BackendCapabilities | None,
    escalation: dict[str, Any] | None = None,
) -> dict[str, Any]:
    route = {"ALLOW": "local", "ABSTAIN": "shadow", "ESCALATE": "escalate"}[action]
    extra: dict[str, Any] = {
        "function_id": FUNCTION_ID,
        "proposition": proposition,
        "state_sha256_16": state_digest(state),
        "state_chars": len(state),
        "p_true": p_true,
        "p_false": p_false,
        "thresholds": {
            "allow_at": allow_at, "escalate_below": escalate_below,
            "provisional": True,
            "note": "uncalibrated; refit on a balanced set before treating as a boundary",
        },
        "backend_calls": [c.to_dict() for c in calls],
        "escalated": escalated,
    }
    if provenance:
        extra["state_packet"] = provenance
    if capabilities_block is not None:
        extra["backend_capabilities"] = capabilities_block.to_dict()
    if escalation:
        extra["escalation"] = escalation
    receipt = build_receipt(
        trace_id=trace_id,
        capability_id=FUNCTION_ID,
        provider=result_backend,
        model=model,
        prediction=action,
        confidence=p_true,
        action_taken=action,
        route=route,
        execution="log_only",
        latency_ms=latency_ms,
        extra=extra,
    ).to_dict()
    return receipt


def verify(
    state: str | dict[str, Any],
    *,
    backend: str = "laya",
    proposition: str = PROPOSITION,
    allow_at: float = DEFAULT_ALLOW_AT,
    escalate_below: float = DEFAULT_ESCALATE_BELOW,
    trace_id: str | None = None,
    provenance: dict[str, Any] | None = None,
) -> VerificationResult:
    """Run one implementation over one state. Raises VerifierUnavailable."""
    if backend not in _FACTORIES:
        raise ValueError(f"unknown backend {backend!r}; have {sorted(_FACTORIES)}")
    if proposition not in PHRASINGS and proposition != PROPOSITION:
        raise ValueError("proposition is not one of the registered phrasings")

    text = _coerce_state(state)
    impl = _FACTORIES[backend]()
    caps = impl.capabilities()
    trace = trace_id or new_trace_id()

    started = time.perf_counter()
    try:
        p_true, meta = impl.verify(text, proposition=proposition)
    except VerifierUnavailable:
        raise
    except Exception as exc:  # normalise anything else into the contract's error
        raise VerifierUnavailable(backend, f"{type(exc).__name__}: {exc}") from exc
    latency_ms = (time.perf_counter() - started) * 1000

    p_true = min(max(float(p_true), 0.0), 1.0)
    p_false = 1.0 - p_true
    action = action_of(p_true, allow_at=allow_at, escalate_below=escalate_below)
    call = PhysicalCall(
        backend=backend, model=meta.get("model"), latency_ms=latency_ms, ok=True,
        p_true=p_true, usage=meta.get("usage") or {},
    )
    receipt = _receipt(
        result_backend=backend, model=meta.get("model"), revision=meta.get("revision"),
        p_true=p_true, p_false=p_false, action=action, latency_ms=latency_ms, trace_id=trace,
        state=text, proposition=proposition, allow_at=allow_at, escalate_below=escalate_below,
        calls=(call,), escalated=False, provenance=provenance, capabilities_block=caps,
    )
    return VerificationResult(
        p_true=p_true, p_false=p_false, action=action, backend=backend,
        model=meta.get("model"), revision=meta.get("revision"), latency_ms=latency_ms,
        trace_id=trace, state_sha256_16=state_digest(text), receipt=receipt,
        capabilities=caps, calls=(call,), escalated=False,
    )


def verify_with_escalation(
    state: str | dict[str, Any],
    *,
    proposition: str = PROPOSITION,
    allow_at: float = DEFAULT_ALLOW_AT,
    escalate_below: float = DEFAULT_ESCALATE_BELOW,
    fast: str = "laya",
    reference: str = "jev",
    trace_id: str | None = None,
    provenance: dict[str, Any] | None = None,
) -> VerificationResult:
    """Laya -> uncertain -> Jev, both physical calls under ONE trace.

    Failure of the fast path is itself a reason to escalate. Failure of the
    reference after an uncertain fast answer is recorded and the fast answer
    stands, with the reference failure in the receipt — an escalation that could
    not run must not silently look like it agreed.
    """
    text = _coerce_state(state)
    trace = trace_id or new_trace_id()
    fast_impl = _FACTORIES[fast]()
    fast_caps = fast_impl.capabilities()
    calls: list[PhysicalCall] = []

    if not fast_caps.available:
        raise VerifierUnavailable(fast, fast_caps.detail or "fast path unavailable")

    started = time.perf_counter()
    p_fast, meta_fast = fast_impl.verify(text, proposition=proposition)
    fast_ms = (time.perf_counter() - started) * 1000
    p_fast = min(max(float(p_fast), 0.0), 1.0)
    calls.append(PhysicalCall(backend=fast, model=meta_fast.get("model"), latency_ms=fast_ms,
                              ok=True, p_true=p_fast, usage=meta_fast.get("usage") or {}))
    uncertain = is_uncertain(p_fast, allow_at=allow_at, escalate_below=escalate_below)

    escalation: dict[str, Any] = {
        "policy": "abstain_band_to_reference",
        "fast": fast, "reference": reference,
        "uncertain": uncertain,
        "reason": "fast path landed in the abstain band" if uncertain else "fast path was decisive",
        "reference_attempted": False,
        "reference_failed": False,
    }

    if not uncertain:
        action = action_of(p_fast, allow_at=allow_at, escalate_below=escalate_below)
        receipt = _receipt(
            result_backend=fast, model=meta_fast.get("model"), revision=meta_fast.get("revision"),
            p_true=p_fast, p_false=1 - p_fast, action=action, latency_ms=fast_ms, trace_id=trace,
            state=text, proposition=proposition, allow_at=allow_at, escalate_below=escalate_below,
            calls=tuple(calls), escalated=False, provenance=provenance,
            capabilities_block=fast_caps, escalation=escalation,
        )
        return VerificationResult(
            p_true=p_fast, p_false=1 - p_fast, action=action, backend=fast,
            model=meta_fast.get("model"), revision=meta_fast.get("revision"),
            latency_ms=fast_ms, trace_id=trace, state_sha256_16=state_digest(text),
            receipt=receipt, capabilities=fast_caps, calls=tuple(calls), escalated=False,
        )

    ref_impl = _FACTORIES[reference]()
    ref_caps = ref_impl.capabilities()
    escalation["reference_attempted"] = True
    ref_ms = 0.0
    meta_ref: dict[str, Any] = {}
    p_ref: float | None = None
    failure: str | None = None
    started = time.perf_counter()
    try:
        p_ref_raw, meta_ref = ref_impl.verify(text, proposition=proposition)
        p_ref = min(max(float(p_ref_raw), 0.0), 1.0)
    except Exception as exc:
        failure = str(exc)
    ref_ms = (time.perf_counter() - started) * 1000

    if p_ref is None:
        escalation["reference_failed"] = True
        escalation["reference_failure"] = failure
        calls.append(PhysicalCall(backend=reference, model=ref_caps.model, latency_ms=ref_ms,
                                  ok=False, detail=failure or "failed"))
        action = action_of(p_fast, allow_at=allow_at, escalate_below=escalate_below)
        receipt = _receipt(
            result_backend=fast, model=meta_fast.get("model"), revision=meta_fast.get("revision"),
            p_true=p_fast, p_false=1 - p_fast, action=action, latency_ms=fast_ms + ref_ms,
            trace_id=trace, state=text, proposition=proposition, allow_at=allow_at,
            escalate_below=escalate_below, calls=tuple(calls), escalated=True,
            provenance=provenance, capabilities_block=fast_caps, escalation=escalation,
        )
        return VerificationResult(
            p_true=p_fast, p_false=1 - p_fast, action=action, backend=fast,
            model=meta_fast.get("model"), revision=meta_fast.get("revision"),
            latency_ms=fast_ms + ref_ms, trace_id=trace, state_sha256_16=state_digest(text),
            receipt=receipt, capabilities=fast_caps, calls=tuple(calls), escalated=True,
        )

    calls.append(PhysicalCall(backend=reference, model=meta_ref.get("model"), latency_ms=ref_ms,
                              ok=True, p_true=p_ref, usage=meta_ref.get("usage") or {}))
    escalation["fast_p_true"] = p_fast
    escalation["reference_p_true"] = p_ref
    action = action_of(p_ref, allow_at=allow_at, escalate_below=escalate_below)
    total_ms = fast_ms + ref_ms
    receipt = _receipt(
        result_backend=reference, model=meta_ref.get("model"), revision=meta_ref.get("revision"),
        p_true=p_ref, p_false=1 - p_ref, action=action, latency_ms=total_ms, trace_id=trace,
        state=text, proposition=proposition, allow_at=allow_at, escalate_below=escalate_below,
        calls=tuple(calls), escalated=True, provenance=provenance,
        capabilities_block=ref_caps, escalation=escalation,
    )
    return VerificationResult(
        p_true=p_ref, p_false=1 - p_ref, action=action, backend=reference,
        model=meta_ref.get("model"), revision=meta_ref.get("revision"), latency_ms=total_ms,
        trace_id=trace, state_sha256_16=state_digest(text), receipt=receipt,
        capabilities=ref_caps, calls=tuple(calls), escalated=True,
    )
