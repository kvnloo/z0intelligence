"""Structured bridge identity, separate from hook-time key and attribution helpers."""
from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from .harness_id import detect_harness_id


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
