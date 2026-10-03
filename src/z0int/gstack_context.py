"""Shadow adapter for GStack context-bill cost/benefit metadata.

The input is the JSON emitted by `gstack-context-bill --json`. GStack owns the
measurement/declaration surface; z0intelligence only normalizes it for offline
experiments.

Author-declared benefit is never treated as verified outcome or authority.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Mapping

SCHEMA = "z0int.gstack_context_value.v1"


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)


def _sha(value: Any, n: int = 20) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()[:n]


def _nonnegative_number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    if parsed < 0 or parsed != parsed or parsed in (float("inf"), float("-inf")):
        return None
    return parsed


def normalize_context_bill(bill: Mapping[str, Any]) -> dict[str, Any]:
    """Normalize a GStack context bill into non-authoritative shadow candidates."""
    skills = bill.get("skills")
    if not isinstance(skills, list):
        raise ValueError("context bill requires skills[]")

    token_error = _nonnegative_number(bill.get("tokenEstimateErrorPct"))
    token_source = bill.get("tokenSource")
    cost_state = "measured" if token_error == 0 else "estimated"

    rows: list[dict[str, Any]] = []
    for raw in skills:
        if not isinstance(raw, Mapping):
            continue
        name = raw.get("name")
        if not isinstance(name, str) or not name.strip():
            continue

        eager = _nonnegative_number(raw.get("eagerTokens"))
        saving_present = "estimatedTokenSaving" in raw
        saving = _nonnegative_number(raw.get("estimatedTokenSaving")) if saving_present else None

        reason_codes: list[str] = []
        if eager is None:
            reason_codes.append("cost_unavailable")
        if not saving_present:
            reason_codes.append("benefit_unmeasured")
        elif saving is None:
            reason_codes.append("benefit_invalid")

        density: float | None = None
        if eager is not None and saving is not None:
            if eager > 0:
                density = saving / eager
            else:
                reason_codes.append("zero_cost_no_density")

        candidate_id = "gs:" + _sha(
            {
                "name": name,
                "eager_tokens": eager,
                "estimated_token_saving": saving,
                "token_source": token_source,
                "token_error_pct": token_error,
            }
        )
        rows.append(
            {
                "schema": SCHEMA,
                "candidate_id": candidate_id,
                "skill": name,
                "shadow_only": True,
                "traffic_eligible": False,
                "cost": {
                    "eager_tokens": eager,
                    "state": cost_state if eager is not None else "unavailable",
                    "source": token_source,
                    "estimate_error_pct": token_error,
                },
                "benefit": {
                    "estimated_token_saving": saving,
                    "avg_execution_time": raw.get("avgExecutionTime"),
                    "state": "declared_estimate" if saving is not None else "unmeasured",
                },
                "value_density": density,
                "reason_codes": reason_codes,
                "authority": {
                    "granted": [],
                    "note": "context-bill metadata is instrumentation, never execution authority",
                },
                "outcome": {
                    "verified": False,
                    "note": "author benefit estimates require joined world outcomes before promotion",
                },
                "receipt_extra": {
                    "gstack_context_candidate_id": candidate_id,
                    "gstack_skill": name,
                    "gstack_declared_value_density": density,
                    "gstack_benefit_state": "declared_estimate" if saving is not None else "unmeasured",
                },
            }
        )

    rows.sort(
        key=lambda row: (
            row["value_density"] is None,
            -(row["value_density"] or 0.0),
            row["skill"],
        )
    )
    return {
        "schema": SCHEMA,
        "shadow_only": True,
        "traffic_eligible": False,
        "source": {
            "kind": "gstack-context-bill",
            "root": bill.get("root"),
            "token_source": token_source,
            "token_estimate_error_pct": token_error,
        },
        "candidates": rows,
        "authority": {"granted": []},
    }


def attach_context_candidate(
    receipt: Mapping[str, Any],
    candidate: Mapping[str, Any],
) -> dict[str, Any]:
    """Attach opaque shadow correlation fields without changing receipt outcome."""
    if candidate.get("schema") != SCHEMA or not candidate.get("shadow_only"):
        raise ValueError("expected a GStack context-value shadow candidate")
    extra_fields = candidate.get("receipt_extra")
    if not isinstance(extra_fields, Mapping) or not extra_fields.get("gstack_context_candidate_id"):
        raise ValueError("candidate has no receipt correlation fields")
    out = dict(receipt)
    extra = dict(out.get("extra") or {})
    extra.update({str(k): v for k, v in extra_fields.items()})
    out["extra"] = extra
    return out
