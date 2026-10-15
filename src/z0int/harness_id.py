"""Cross-harness identity fields (avoid generic omp_* naming)."""

from __future__ import annotations

import os
from dataclasses import asdict, dataclass
from typing import Any


@dataclass(frozen=True)
class HarnessIdentity:
    harness_id: str
    session_id: str | None = None
    process_id: int | None = None
    trace_id: str | None = None
    turn_id: str | None = None
    bridge_generation: int | None = None
    build_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {k: v for k, v in asdict(self).items() if v is not None}


def detect_harness_id(explicit: str | None = None) -> str:
    if explicit:
        return explicit
    env = os.environ.get("Z0INT_HARNESS_ID") or os.environ.get("HARNESS_ID")
    if env:
        return env
    # soft detect
    if os.environ.get("OMP_SESSION_ID") or os.environ.get("PI_SESSION_ID"):
        return "omp"
    if os.environ.get("HERMES_HOME") or os.environ.get("HERMES_PROFILE"):
        return "hermes"
    if os.environ.get("CLAUDECODE") == "1":
        return "claude-code"
    return "unknown"


def identity_from_bridge(
    *,
    session_id: str | None,
    process_id: int | None,
    trace_id: str | None,
    turn_id: str | None = None,
    bridge_generation: int | None = None,
    build_id: str | None = None,
    harness_id: str | None = None,
) -> HarnessIdentity:
    return HarnessIdentity(
        harness_id=detect_harness_id(harness_id),
        session_id=session_id,
        process_id=process_id,
        trace_id=trace_id,
        turn_id=turn_id,
        bridge_generation=bridge_generation,
        build_id=build_id,
    )
