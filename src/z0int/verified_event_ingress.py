"""Durable dedup for already-verified AgentWeb external event batches."""
from __future__ import annotations

import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
from typing import Any

from . import paths
from .outcome_observation import (
    SUBJECT_RE,
    ingest_outcome_observation,
    validate_outcome_observation,
    _parse_time,
)

SCHEMA_V1 = "agentweb.verified_event_batch.v1"
STORED_SCHEMA_V1 = "z0int.agentweb_verified_event.v1"
EVENT_RE = re.compile(r"^agentweb-event-[0-9a-f]{64}$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
ID_RE = re.compile(r"^[A-Za-z0-9._:-]{1,120}$")
MAX_PROJECTIONS = 16


def verified_events_path(root: Path | None = None) -> Path:
    layout = paths.ensure_layout(root)
    return layout["receipts"] / "agentweb_verified_events.jsonl"


def _digest(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            sort_keys=True,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()


def validate_verified_event_batch(row: dict[str, Any]) -> None:
    allowed = {
        "schema",
        "event_id",
        "provider",
        "event_type",
        "event_time",
        "received_at",
        "subject_ref",
        "verification",
        "payload_sha256",
        "projections",
        "quality_authoritative",
    }
    if not isinstance(row, dict) or set(row) - allowed:
        raise ValueError("Unknown verified event batch fields")
    if row.get("schema") != SCHEMA_V1:
        raise ValueError("Invalid verified event batch schema")

    event_id = row.get("event_id")
    if not isinstance(event_id, str) or not EVENT_RE.fullmatch(event_id):
        raise ValueError("Invalid verified event identity")

    for field in ("provider", "event_type"):
        value = row.get(field)
        if not isinstance(value, str) or not ID_RE.fullmatch(value):
            raise ValueError(f"Invalid verified event {field}")

    _parse_time(row.get("event_time"), "event_time")
    _parse_time(row.get("received_at"), "received_at")

    subject_ref = row.get("subject_ref")
    if subject_ref is not None and (
        not isinstance(subject_ref, str) or not SUBJECT_RE.fullmatch(subject_ref)
    ):
        raise ValueError("Verified event subject_ref must already be pseudonymous")

    verification = row.get("verification")
    if not isinstance(verification, dict):
        raise ValueError("Verified event verification must be an object")
    if set(verification) != {"status", "scheme", "verified_at"}:
        raise ValueError("Verified event verification fields are invalid")
    if verification.get("status") != "verified":
        raise ValueError("Verified event must already be signature-verified")
    scheme = verification.get("scheme")
    if not isinstance(scheme, str) or not ID_RE.fullmatch(scheme):
        raise ValueError("Invalid verified event verification scheme")
    _parse_time(verification.get("verified_at"), "verified_at")

    payload_sha256 = row.get("payload_sha256")
    if (
        not isinstance(payload_sha256, str)
        or not SHA256_RE.fullmatch(payload_sha256)
    ):
        raise ValueError("Invalid verified event payload digest")

    projections = row.get("projections")
    if (
        not isinstance(projections, list)
        or not 1 <= len(projections) <= MAX_PROJECTIONS
    ):
        raise ValueError(
            f"Verified event projections must contain 1..{MAX_PROJECTIONS} rows"
        )

    for index, projection in enumerate(projections):
        if (
            not isinstance(projection, dict)
            or set(projection) != {"kind", "value"}
            or projection.get("kind") != "outcome"
        ):
            raise ValueError(f"Invalid verified event projection at index {index}")
        value = projection.get("value")
        if not isinstance(value, dict):
            raise ValueError(
                f"Verified event outcome projection {index} must be an object"
            )
        validate_outcome_observation(value)

    if row.get("quality_authoritative") is not False:
        raise ValueError("Verified events must not be quality-authoritative")


def _find_event_locked(stream, event_id: str) -> dict[str, Any] | None:
    stream.seek(0)
    found = None
    for line in stream:
        line = line.strip()
        if not line:
            continue
        candidate = json.loads(line)
        if candidate.get("event_id") == event_id:
            found = candidate
    return found


def ingest_verified_event_batch(
    row: dict[str, Any],
    *,
    root: Path | None = None,
) -> dict[str, Any]:
    # Validate every child before any durable fan-out.
    validate_verified_event_batch(row)
    event_id = row["event_id"]
    fingerprint = _digest(row)

    path = verified_events_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)

    with path.open("a+", encoding="utf-8") as stream:
        fcntl.flock(stream, fcntl.LOCK_EX)
        previous = _find_event_locked(stream, event_id)
        if previous is not None:
            if previous.get("event_sha256") != fingerprint:
                raise ValueError(
                    "verified event identity reused for changed batch"
                )
            return {
                "ok": True,
                "event_id": event_id,
                "replayed": True,
                "fanout_count": previous["fanout_count"],
                "projection_results": previous["projection_results"],
            }

        projection_results: list[dict[str, Any]] = []
        for projection in row["projections"]:
            if projection["kind"] != "outcome":
                raise ValueError("Unsupported verified event projection kind")
            projection_results.append(
                ingest_outcome_observation(
                    projection["value"],
                    root=root,
                )
            )

        stored = {
            key: value
            for key, value in row.items()
            if key != "projections"
        }
        stored.update(
            schema=STORED_SCHEMA_V1,
            source_schema=SCHEMA_V1,
            event_sha256=fingerprint,
            observational=True,
            quality_authoritative=False,
            fanout_count=len(projection_results),
            projection_results=projection_results,
        )

        stream.seek(0, os.SEEK_END)
        stream.write(json.dumps(stored, allow_nan=False) + "\n")
        stream.flush()
        os.fsync(stream.fileno())
        fcntl.flock(stream, fcntl.LOCK_UN)

    return {
        "ok": True,
        "event_id": event_id,
        "replayed": False,
        "fanout_count": len(projection_results),
        "projection_results": projection_results,
    }
