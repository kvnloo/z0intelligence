"""V0 mutation axis: context acquisition / minimization only."""

from __future__ import annotations

import hashlib
import json
from typing import Any


def treatment_hash(policy: dict[str, Any]) -> str:
    blob = json.dumps(policy, sort_keys=True, default=str).encode()
    return hashlib.sha256(blob).hexdigest()[:16]


def champion_policy(snapshot: dict[str, Any] | None = None) -> dict[str, Any]:
    """Frozen champion context policy from the verified trajectory (or default)."""
    if snapshot and isinstance(snapshot.get("context_policy"), dict):
        pol = dict(snapshot["context_policy"])
    else:
        pol = {
            "ops": ["exact_path", "lexical_local", "qmd_lexical"],
            "use_cache": True,
            "allow_qmd_semantic": False,
            "ablate": [],
        }
    pol["role"] = "champion"
    return pol


def challenger_policies(champion: dict[str, Any]) -> list[dict[str, Any]]:
    """Generate context-minimization challengers; do not mutate model/tools/verifier."""
    base_ops = list(champion.get("ops") or ["exact_path", "lexical_local", "qmd_lexical"])
    out: list[dict[str, Any]] = []
    # drop one op at a time
    for i, op in enumerate(base_ops):
        ops = [o for j, o in enumerate(base_ops) if j != i]
        if not ops:
            continue
        out.append(
            {
                "role": "challenger",
                "ops": ops,
                "use_cache": champion.get("use_cache", True),
                "allow_qmd_semantic": False,
                "ablate": [op],
                "hypothesis": f"drop_{op}_sufficient",
            }
        )
    # cache-only
    out.append(
        {
            "role": "challenger",
            "ops": ["cached_packet"],
            "use_cache": True,
            "allow_qmd_semantic": False,
            "ablate": [o for o in base_ops if o != "cached_packet"],
            "hypothesis": "cached_evidence_packet_sufficient",
        }
    )
    # exact path only
    out.append(
            {
                "role": "challenger",
                "ops": ["exact_path"],
                "use_cache": True,
                "allow_qmd_semantic": False,
                "ablate": [o for o in base_ops if o != "exact_path"],
                "hypothesis": "exact_path_only",
            }
        )
    # dedupe by hash
    seen: set[str] = set()
    uniq = []
    for p in out:
        h = treatment_hash(p)
        if h in seen:
            continue
        seen.add(h)
        p = dict(p)
        p["treatment_hash"] = h
        uniq.append(p)
    return uniq
