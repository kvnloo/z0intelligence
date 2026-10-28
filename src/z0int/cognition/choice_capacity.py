"""Read only an exact-scope, all-risk capacity envelope for exploratory scoring.

The fallback is the existing exploratory candidate cap, not routing credit.
No model bytes are hashed on this path and no action is authorized here.
"""
from __future__ import annotations

from datetime import datetime, timezone
import json
import math
from pathlib import Path

from ..policy.compile import SCOPE_FIELDS, option_capacity_key
from ..policy.findings import digest, timestamp


_RISKS = ("read", "mutating_reversible", "destructive")
_TASKS = {"T1", "T2", "T3", "T4"}


def _ids(value: object, minimum: int) -> bool:
    return (isinstance(value, list) and len(value) >= minimum
            and all(isinstance(item, str) and len(item) == 64
                    and all(c in "0123456789abcdef" for c in item) for item in value)
            and len(set(value)) == len(value))


def _entry_cap(entry: object, now: datetime) -> int | None:
    if not isinstance(entry, dict):
        return None
    allowed = entry.get("allowed_k")
    inputs = entry.get("inputs")
    if (type(allowed) is not int or not 0 <= allowed <= 256
            or not isinstance(inputs, dict) or inputs.get("measured") is not True
            or inputs.get("status") != "measured" or inputs.get("blocked", False) is not False
            or inputs.get("cost_unit") not in {"latency_ms_lambda0", "o14_score_ms"}):
        return None
    cost_ids, ids = inputs.get("finding_ids"), entry.get("finding_ids")
    if not _ids(cost_ids, 4) or not _ids(ids, 4) or not set(cost_ids) <= set(ids):
        return None
    tasks = entry.get("task")
    if (not isinstance(tasks, list) or any(not isinstance(t, str) or not t for t in tasks)
            or len(tasks) != len(set(tasks)) or not _TASKS <= set(tasks)):
        return None
    values = [inputs.get(name) for name in ("cost_call", "cost_frontier", "cost_error", "abstain_rate")]
    if any(type(v) not in (int, float) or not math.isfinite(v) or v < 0 for v in values):
        return None
    call, frontier, error, abstain = values
    if frontier <= 0 or error <= 0 or abstain > 1:
        return None
    bar = entry.get("bar_derived")
    derived = 1 - (frontier * (1 - abstain) - call) / error
    if (type(bar) not in (int, float) or not math.isfinite(bar)
            or not math.isclose(bar, derived, rel_tol=1e-12, abs_tol=1e-12)
            or timestamp(entry["expires_at"]) <= now or timestamp(inputs["expires_at"]) <= now
            or timestamp(entry["expires_at"]) > timestamp(inputs["expires_at"])):
        return None
    lower = entry.get("wilson_lower")
    if allowed:
        if (type(lower) not in (int, float) or not math.isfinite(lower)
                or not 0 <= lower <= 1 or lower <= bar or len(ids) < len(cost_ids) + len(tasks)):
            return None
    elif lower is not None:
        return None
    return allowed


def effective_cap(model_parameters_b: float, policy_identity: dict, policy_home: Path) -> int:
    """Use the minimum measured risk capacity; otherwise retain exploratory fallback."""
    from .shadow_binary import candidate_cap
    base = candidate_cap(model_parameters_b)
    try:
        scope = {key: policy_identity[key] for key in SCOPE_FIELDS}
        key = option_capacity_key(scope)  # rejects aliases; no hot-path hashing
        policy = json.loads((Path(policy_home) / "policy" / "decision-policy.json").read_text())
        if policy.get("schema") != "z0.decision_policy.v1" or policy.get("synthetic", False):
            return base
        if policy.get("version") != digest({k: v for k, v in policy.items() if k != "version"}):
            return base
        now = datetime.now(timezone.utc)
        if timestamp(policy["compiled_at"]) > now:
            return base
        envelope = policy.get("option_capacity", {}).get(key)
        if not isinstance(envelope, dict):
            return base
        caps = [_entry_cap(envelope.get(risk), now) for risk in _RISKS]
        return min(caps) if all(cap is not None for cap in caps) else base
    except (OSError, ValueError, KeyError, TypeError, OverflowError):
        return base
