"""Declarative owner-selected voice profiles; no per-turn routing or execution.

Export: python -m z0int.voice_plan manifests/z0live_voice.v1.json
Consumers pin the complete returned value for a conversation, including its digest.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re

ROLES = frozenset({"smol", "default", "plan", "slow", "task", "judge"})
IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,199}$")


def validate_voice_plan(value: dict) -> dict:
    """Validate without repairing identifiers or selecting alternate models."""
    if not isinstance(value, dict) or value.get("schema") != "z0int.voice_plan.v1":
        raise ValueError("unsupported VoicePlan schema")
    for field in (
        "revision",
        "actor_id",
        "provider",
        "model",
        "voice",
        "adapter",
        "harness",
        "fixture_revision",
    ):
        if not isinstance(value.get(field), str) or not IDENTIFIER.fullmatch(
            value[field]
        ):
            raise ValueError("invalid VoicePlan " + field)
    roles = value.get("roles")
    if not isinstance(roles, dict) or set(roles) != ROLES:
        raise ValueError("VoicePlan must bind smol/default/plan/slow/task/judge")
    for role, binding in roles.items():
        if not isinstance(binding, dict) or set(binding) != {"provider", "model"}:
            raise ValueError("invalid binding: " + role)
        if any(
            not isinstance(v, str) or not IDENTIFIER.fullmatch(v)
            for v in binding.values()
        ):
            raise ValueError("invalid model identifier: " + role)
    if value.get("meta_role") not in {"default", "plan", "slow"}:
        raise ValueError("invalid meta_role")
    if not isinstance(value.get("evidence"), list) or not value["evidence"]:
        raise ValueError("VoicePlan needs provenance evidence")
    if value.get("fallbacks") != []:
        raise ValueError(
            "this profile version requires explicit recovery, not automatic fallback"
        )
    result = {k: v for k, v in value.items() if k != "digest"}
    digest = hashlib.sha256(
        json.dumps(result, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    if "digest" in value and value["digest"] != digest:
        raise ValueError("VoicePlan digest mismatch")
    return {**result, "digest": digest}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("profile", type=Path)
    args = parser.parse_args()
    print(
        json.dumps(
            validate_voice_plan(json.loads(args.profile.read_text())), sort_keys=True
        )
    )


if __name__ == "__main__":
    main()
