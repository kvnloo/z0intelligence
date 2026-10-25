"""Read O13's exact-scope option capacity; configuration alone cannot raise it."""
from __future__ import annotations

from datetime import datetime, timezone
import json
import math
from pathlib import Path

from ..policy.compile import SCOPE_FIELDS
from ..policy.findings import canonical, digest, timestamp


def effective_cap(model_parameters_b: float, policy_identity: dict, policy_home: Path) -> int:
    from .shadow_binary import candidate_cap
    base = candidate_cap(model_parameters_b)
    try:
        scope = {key: policy_identity[key] for key in SCOPE_FIELDS}
        if any(not isinstance(value, str) or not value for value in scope.values()):
            return base
        policy = json.loads((Path(policy_home)/'policy'/'decision-policy.json').read_text())
        if policy.get('schema') != 'z0.decision_policy.v1' or policy.get('synthetic', False):
            return base
        if policy.get('version') != digest({k: v for k, v in policy.items() if k != 'version'}):
            return base
        now = datetime.now(timezone.utc)
        if timestamp(policy['compiled_at']) > now:
            return base
        capacity = policy.get('option_capacity', {}).get(canonical(scope).decode('utf-8'))
        if not isinstance(capacity, dict):
            return base
        allowed = capacity.get('allowed_k')
        if type(allowed) is not int or not 1 <= allowed <= 256:
            return base
        if allowed <= base:
            return allowed
        lower, bar = capacity.get('wilson_lower'), capacity.get('bar')
        if any(type(value) not in (int, float) or not math.isfinite(value) for value in (lower, bar)):
            return base
        if not .95 <= bar <= lower <= 1:
            return base
        if not {'T1', 'T2', 'T3', 'T4'} <= set(capacity.get('task', [])):
            return base
        ids = capacity.get('finding_ids')
        if not isinstance(ids, list) or len(set(ids)) < 4 or any(not isinstance(i, str) or len(i) != 64 for i in ids):
            return base
        if timestamp(capacity['expires_at']) <= now:
            return base
        return allowed
    except (OSError, ValueError, KeyError, TypeError, OverflowError):
        return base
