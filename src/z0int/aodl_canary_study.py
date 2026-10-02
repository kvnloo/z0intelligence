"""Host-only bounds for one separately versioned public canary experiment.

This is an opt-in adapter policy, not an AODL language construct or router.
The ordinary policy has no governed_study entry and remains free-only.
"""
from __future__ import annotations

import json
import math
from typing import Any, Mapping

STUDY_ID = "aodl-nous-solar-completion-v1"
MODEL = "upstage/solar-mini4"
TASK = "Public synthetic diagnostic. Return exactly CANONICAL_OK and nothing else."
CONTEXT = "Public synthetic diagnostic only."


def bounds(policy: Mapping[str, Any], providers: Mapping[str, Any]) -> Mapping[str, Any] | None:
    study = policy.get("governed_study")
    if study is None:
        return None
    expected = {
        "id": STUDY_ID, "provider": "nous", "model": MODEL,
        "max_output_tokens": 128, "max_prompt_bytes": 2048,
        "max_estimated_cost_usd": 0.001, "max_physical_calls": 1,
        "task": TASK, "context": CONTEXT,
        "billing_cap_enforced_by_provider": False,
        "pricing": {
            "prompt_usd_per_million": 0.05, "completion_usd_per_million": 0.20,
            "catalog_sha256": "fe83a478f571525c964d68cfb1fc8711920106772c3e2c304ea3512008be45a2",
        },
    }
    provider = providers.get("nous", {})
    if (
        study != expected or policy.get("free_only") is not False
        or policy.get("policy_revision") != STUDY_ID
        or policy.get("max_attempts") != 1
        or policy.get("provider_caps") != {"nous": 1}
        or policy.get("defaults") != {"nous": MODEL}
        or set(providers) != {"nous"}
        or provider.get("base_url") != "https://inference-api.nousresearch.com"
        or provider.get("auth") != "environment"
        or provider.get("api_key_env") != "NOUS_API_KEY"
        or [row.get("id") for row in provider.get("models", [])] != [MODEL]
        or policy.get("validated_free_routes") != []
    ):
        raise ValueError("invalid frozen governed study policy")
    return study


def validate_worker(worker: Mapping[str, Any], study: Mapping[str, Any], messages: list[dict]) -> None:
    if worker.get("task") != TASK or worker.get("context", "") != CONTEXT:
        raise ValueError("governed study accepts only the frozen public intent")
    if type(worker.get("max_tokens")) is not int or worker["max_tokens"] != study["max_output_tokens"]:
        raise ValueError("governed study completion cap differs from frozen policy")
    prompt_bytes = len(json.dumps(messages, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
    if prompt_bytes > study["max_prompt_bytes"]:
        raise ValueError("governed study prompt byte budget exceeded")
    # A conservative byte-based reservation; it is an estimate, not a billing cap.
    rates = study["pricing"]
    estimated = (prompt_bytes * rates["prompt_usd_per_million"] + worker["max_tokens"] * rates["completion_usd_per_million"]) / 1_000_000
    if not math.isfinite(estimated) or estimated > study["max_estimated_cost_usd"]:
        raise ValueError("governed study estimated cost budget exceeded")


def exact_public_outcome(output: object) -> bool:
    """The old exact-output predicate: completion itself is not task success."""
    return isinstance(output, str) and output.strip() == "CANONICAL_OK"
