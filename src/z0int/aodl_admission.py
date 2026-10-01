"""AODL structural admission for bounded dynamic transitions.

The canonical AODL package owns validation and semantic identity. This module
only applies live observed state to that frozen contract. Bend may verify the
same transition semantics in CI, but is not required on the runtime hot path.
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

REQUIRED_CANON_VERSION = "aodl-canon-1"
BUDGET_DIMS = ("tokens", "premium_tokens", "latency_ms", "usd", "joules", "attention")
TRANSITION_CODES = {
    101: "revision-mismatch",
    102: "dynamic-spawn-disallowed",
    103: "max-children",
    104: "max-depth",
    105: "budget",
    106: "authority",
}


@dataclass(frozen=True)
class SpawnRequest:
    request_revision: int
    parent_node_id: str
    live_children: int = 0
    parent_depth: int = 0
    observed: Mapping[str, Any] = field(default_factory=dict)
    proposed: Mapping[str, Any] = field(default_factory=dict)
    requested: Sequence[str] = ()


@dataclass(frozen=True)
class AdmissionDecision:
    allowed: bool
    codes: tuple[str, ...]
    numeric_codes: tuple[int, ...]
    source: str
    semantic_fingerprint: str | None
    intent_source_hash: str | None
    contract_revision: int | None
    request_revision: int | None
    parent_node_id: str | None
    latency_us: float
    detail: str = ""

    def receipt(self) -> dict[str, Any]:
        """Durable gate evidence. This is not a task-success receipt."""

        return {
            "schema": "z0int.aodl_admission.v1",
            "allowed": self.allowed,
            "decision": "ALLOW" if self.allowed else "DENY",
            "codes": list(self.codes),
            "numeric_codes": list(self.numeric_codes),
            "source": self.source,
            "aodl_canon_version": REQUIRED_CANON_VERSION,
            "aodl_semantic_fingerprint": self.semantic_fingerprint,
            "aodl_intent_source_hash": self.intent_source_hash,
            "contract_revision": self.contract_revision,
            "request_revision": self.request_revision,
            "parent_node_id": self.parent_node_id,
            "latency_us": self.latency_us,
        }


def _number(value: Any, where: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{where} must be numeric")
    result = float(value)
    if not math.isfinite(result) or result < 0:
        raise ValueError(f"{where} must be finite and >= 0")
    return result


def _nonnegative_int(value: Any, where: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{where} must be an integer >= 0")
    return value


def _finish(
    started_ns: int,
    *,
    allowed: bool,
    codes: Sequence[str] = (),
    numeric_codes: Sequence[int] = (),
    source: str,
    fingerprint: str | None,
    contract_revision: int | None,
    request: SpawnRequest | None,
    intent_source_hash: str | None = None,
    detail: str = "",
) -> AdmissionDecision:
    return AdmissionDecision(
        allowed=allowed,
        codes=tuple(codes),
        numeric_codes=tuple(numeric_codes),
        source=source,
        semantic_fingerprint=fingerprint,
        intent_source_hash=intent_source_hash,
        contract_revision=contract_revision,
        request_revision=request.request_revision if request is not None else None,
        parent_node_id=request.parent_node_id if request is not None else None,
        latency_us=(time.perf_counter_ns() - started_ns) / 1000.0,
        detail=detail,
    )


def _contract_api():
    import aodl_contract  # type: ignore

    return aodl_contract


def decide_spawn(
    document: object,
    request: SpawnRequest,
    *,
    contract_api: Any | None = None,
) -> AdmissionDecision:
    """Apply the AODL structural spawn gate.

    Contract authority is never supplied by the caller. Revision, dynamic
    bounds, budgets and the parent's authority ceiling are extracted from the
    validated document itself.
    """

    started = time.perf_counter_ns()
    api = contract_api
    if api is None:
        try:
            api = _contract_api()
        except Exception as exc:
            return _finish(
                started,
                allowed=False,
                codes=("aodl-unavailable",),
                source="fail-closed",
                fingerprint=None,
                contract_revision=None,
                request=request,
                detail=type(exc).__name__,
            )

    try:
        canon_version = getattr(api, "CANON_VERSION")
        validate = getattr(api, "validate")
        semantic_fingerprint = getattr(api, "semantic_fingerprint")
    except Exception as exc:
        return _finish(
            started,
            allowed=False,
            codes=("aodl-api-invalid",),
            source="fail-closed",
            fingerprint=None,
            contract_revision=None,
            request=request,
            detail=type(exc).__name__,
        )

    if canon_version != REQUIRED_CANON_VERSION:
        return _finish(
            started,
            allowed=False,
            codes=("canonical-version-mismatch",),
            source="fail-closed",
            fingerprint=None,
            contract_revision=None,
            request=request,
            detail=f"expected {REQUIRED_CANON_VERSION}, got {canon_version!r}",
        )

    try:
        issues = validate(document)
    except Exception as exc:
        return _finish(
            started,
            allowed=False,
            codes=("contract-validation-error",),
            source="fail-closed",
            fingerprint=None,
            contract_revision=None,
            request=request,
            detail=type(exc).__name__,
        )
    if issues:
        return _finish(
            started,
            allowed=False,
            codes=("contract-invalid",),
            source="aodl",
            fingerprint=None,
            contract_revision=None,
            request=request,
            detail=str(issues[0]),
        )
    if not isinstance(document, Mapping):
        return _finish(
            started,
            allowed=False,
            codes=("contract-invalid",),
            source="aodl",
            fingerprint=None,
            contract_revision=None,
            request=request,
            detail="validated document is not a mapping",
        )

    try:
        fingerprint = str(semantic_fingerprint(document))
        if not fingerprint.startswith(REQUIRED_CANON_VERSION + ":"):
            raise ValueError("unexpected fingerprint version")
        provenance = document.get("provenance")
        if not isinstance(provenance, Mapping) or not isinstance(provenance.get("sourceHash"), str):
            raise ValueError("provenance.sourceHash must be present")
        intent_source_hash = str(provenance["sourceHash"])
        contract_revision = _nonnegative_int(document.get("revision"), "document.revision")
        request_revision = _nonnegative_int(request.request_revision, "request_revision")
        live_children = _nonnegative_int(request.live_children, "live_children")
        parent_depth = _nonnegative_int(request.parent_depth, "parent_depth")
        if not isinstance(request.parent_node_id, str) or not request.parent_node_id:
            raise ValueError("parent_node_id must be a non-empty string")
        if not all(isinstance(cap, str) for cap in request.requested):
            raise ValueError("requested capabilities must be strings")

        graph = document.get("intentGraph")
        if not isinstance(graph, Mapping):
            raise ValueError("intentGraph must be an object")
        nodes = graph.get("nodes")
        if not isinstance(nodes, list):
            raise ValueError("intentGraph.nodes must be an array")
        parent = next(
            (node for node in nodes if isinstance(node, Mapping) and node.get("id") == request.parent_node_id),
            None,
        )
        if parent is None:
            raise LookupError("parent node is not declared by the contract")

        policies = document.get("policies")
        constraints = document.get("constraints")
        if not isinstance(policies, Mapping) or not isinstance(constraints, Mapping):
            raise ValueError("policies/constraints must be objects")
        dynamic = policies.get("dynamic")
        if not isinstance(dynamic, Mapping):
            dynamic = {}
        budgets = constraints.get("budgets")
        if not isinstance(budgets, Mapping):
            raise ValueError("constraints.budgets must be an object")

        ceiling_raw = parent.get("authorityCeiling")
        if not isinstance(ceiling_raw, list) or not all(isinstance(cap, str) for cap in ceiling_raw):
            raise ValueError("parent authorityCeiling must be a string array")
    except LookupError as exc:
        return _finish(
            started,
            allowed=False,
            codes=("parent-not-declared",),
            source="aodl",
            fingerprint=fingerprint if "fingerprint" in locals() else None,
            contract_revision=contract_revision if "contract_revision" in locals() else None,
            request=request,
            intent_source_hash=intent_source_hash if "intent_source_hash" in locals() else None,
            detail=str(exc),
        )
    except Exception as exc:
        return _finish(
            started,
            allowed=False,
            codes=("proposal-or-contract-shape",),
            source="fail-closed",
            fingerprint=fingerprint if "fingerprint" in locals() else None,
            contract_revision=contract_revision if "contract_revision" in locals() else None,
            request=request,
            intent_source_hash=intent_source_hash if "intent_source_hash" in locals() else None,
            detail=str(exc),
        )

    numeric: list[int] = []
    if contract_revision != request_revision:
        numeric.append(101)
    if dynamic.get("allowed") is not True:
        numeric.append(102)

    try:
        max_children = _nonnegative_int(dynamic.get("maxChildren", 0), "dynamic.maxChildren")
        max_depth = _nonnegative_int(dynamic.get("maxDepth", 0), "dynamic.maxDepth")
    except ValueError as exc:
        return _finish(
            started,
            allowed=False,
            codes=("contract-dynamic-shape",),
            source="fail-closed",
            fingerprint=fingerprint,
            contract_revision=contract_revision,
            request=request,
            intent_source_hash=intent_source_hash,
            detail=str(exc),
        )
    if live_children + 1 > max_children:
        numeric.append(103)
    if parent_depth + 1 > max_depth:
        numeric.append(104)

    try:
        for dim in BUDGET_DIMS:
            limit = budgets.get(dim)
            if limit is None:
                continue
            lim = _number(limit, f"constraints.budgets.{dim}")
            seen = _number(request.observed.get(dim, 0) or 0, f"observed.{dim}")
            proposed = _number(request.proposed.get(dim, 0) or 0, f"proposed.{dim}")
            if seen + proposed > lim + 1e-12:
                numeric.append(105)
                break
    except ValueError as exc:
        return _finish(
            started,
            allowed=False,
            codes=("budget-shape",),
            source="fail-closed",
            fingerprint=fingerprint,
            contract_revision=contract_revision,
            request=request,
            intent_source_hash=intent_source_hash,
            detail=str(exc),
        )

    ceiling = set(ceiling_raw)
    if any(cap not in ceiling for cap in request.requested):
        numeric.append(106)

    labels = tuple(TRANSITION_CODES[code] for code in numeric)
    return _finish(
        started,
        allowed=not numeric,
        codes=labels,
        numeric_codes=numeric,
        source="aodl",
        fingerprint=fingerprint,
        contract_revision=contract_revision,
        request=request,
        intent_source_hash=intent_source_hash,
    )
