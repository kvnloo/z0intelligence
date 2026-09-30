"""decision-capability-v1 — backend-neutral bounded decision benchmark contract."""

from __future__ import annotations

BENCH_CONTRACT = "decision-capability-v1"
BENCH_SCHEMA = "z0int.backends_bench.v1"

CAPABILITIES = (
    "rlm.worker_needed",
    "tool_family_select",
    "retry_or_escalate",
    "context_compress_needed",
    "verification_needed",
)

ROSTER_CANDIDATES = (
    "laya_421m",
    "decider_2b",
    "nanojev_06b",
    "reflex",
    "system_one_4b",
    "openjev_06b",
    "openjev_4b",
    "local_mb",
    "julia_1",
    "llama_http",
)

#: Dynamic candidates: ``llama_http:<arm>`` for each GGUF arm configured under
#: ``backends.llama_http.arms`` in ``~/.z0int/config/z0int.json``.
LLAMA_HTTP_ARM_PREFIX = "llama_http:"


def is_roster_candidate(candidate_id: str) -> bool:
    return candidate_id in ROSTER_CANDIDATES or (
        candidate_id.startswith(LLAMA_HTTP_ARM_PREFIX) and len(candidate_id) > len(LLAMA_HTTP_ARM_PREFIX)
    )
