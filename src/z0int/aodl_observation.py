"""AODL runtime observation/drift projection.

Authored intent stays immutable. Runtime drift is represented as an idempotent
HOTL stateUpdate event plus a canonical z0 decision receipt.
"""

from __future__ import annotations

import copy
import fcntl
import hashlib
import json
from typing import Any, Mapping, Sequence

from .receipt import append_receipt, receipts_path

OBSERVATION_PREFIX = "aodl-observation-"


def _api():
    import aodl_contract  # type: ignore

    if getattr(aodl_contract, "CANON_VERSION", None) != "aodl-canon-1":
        raise ValueError("aodl-canon-1 is required")
    return aodl_contract


def _digest(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode("utf-8")
    ).hexdigest()


def _event_id(
    *,
    source_hash: str,
    revision: int,
    trace_id: str,
    observation: Mapping[str, Any],
    source: str,
    causal_parents: Sequence[str],
) -> str:
    return "stateUpdate-" + _digest(
        {
            "source_hash": source_hash,
            "revision": revision,
            "trace_id": trace_id,
            "observation": observation,
            "source": source,
            "causal_parents": list(causal_parents),
        }
    )[:24]


def project_drift(
    document: object,
    *,
    trace_id: str,
    observation: Mapping[str, Any],
    source: str = "runtime",
    causal_parents: Sequence[str] = (),
) -> dict[str, Any]:
    """Return an observed-state projection without mutating the input document."""

    if not isinstance(trace_id, str) or not trace_id:
        raise ValueError("trace_id must be non-empty")
    if not isinstance(observation, Mapping):
        raise ValueError("observation must be an object")
    if not isinstance(source, str) or not source:
        raise ValueError("source must be non-empty")
    if (
        isinstance(causal_parents, (str, bytes))
        or not isinstance(causal_parents, Sequence)
        or not all(isinstance(x, str) for x in causal_parents)
    ):
        raise ValueError("causal_parents must be strings")
    canonical_parents = tuple(sorted(causal_parents))

    api = _api()
    issues = api.validate(document)
    if issues:
        raise ValueError("invalid AODL: " + str(issues[0]))
    if not isinstance(document, dict):
        raise ValueError("AODL document must be an object")

    provenance = document.get("provenance")
    if not isinstance(provenance, dict) or not isinstance(provenance.get("sourceHash"), str):
        raise ValueError("provenance.sourceHash is required")
    source_hash = provenance["sourceHash"]
    revision = document.get("revision")
    if isinstance(revision, bool) or not isinstance(revision, int) or revision < 0:
        raise ValueError("revision must be an integer >= 0")

    before = api.semantic_fingerprint(document)
    event_id = _event_id(
        source_hash=source_hash,
        revision=revision,
        trace_id=trace_id,
        observation=observation,
        source=source,
        causal_parents=canonical_parents,
    )
    event = {
        "eventId": event_id,
        "type": "stateUpdate",
        "sourceHash": source_hash,
        "revision": revision,
        "causalParents": list(canonical_parents),
        "payload": {
            "traceId": trace_id,
            "drift": True,
            "source": source,
            "observation": copy.deepcopy(dict(observation)),
        },
    }

    projected = copy.deepcopy(document)
    log = projected.setdefault("eventLog", [])
    if not isinstance(log, list):
        raise ValueError("eventLog must be an array")
    prior = next((entry for entry in log if isinstance(entry, dict) and entry.get("eventId") == event_id), None)
    replayed = prior is not None
    if prior is not None and prior != event:
        raise ValueError("observation event identity conflict")
    if prior is None:
        log.append(event)

    if projected.get("provenance", {}).get("sourceHash") != source_hash:
        raise AssertionError("runtime observation rewrote intent source lineage")
    after_issues = api.validate(projected)
    if after_issues:
        raise ValueError("projected AODL is invalid: " + str(after_issues[0]))
    after = api.semantic_fingerprint(projected)

    return {
        "document": projected,
        "event": event,
        "replayed": replayed,
        "aodl_intent_source_hash": source_hash,
        "semantic_fingerprint_before": before,
        "semantic_fingerprint_after": after,
    }


def _receipt_trace(event_id: str) -> str:
    return OBSERVATION_PREFIX + event_id


def _previous(event_id: str) -> dict[str, Any] | None:
    path = receipts_path()
    if not path.exists():
        return None
    trace = _receipt_trace(event_id)
    row = None
    with path.open() as stream:
        fcntl.flock(stream, fcntl.LOCK_SH)
        for line in stream:
            candidate = json.loads(line)
            if candidate.get("trace_id") == trace:
                row = candidate
    return row


def record_drift(
    document: object,
    *,
    trace_id: str,
    observation: Mapping[str, Any],
    source: str = "runtime",
    causal_parents: Sequence[str] = (),
) -> dict[str, Any]:
    """Project drift and persist one idempotent observation receipt."""

    projected = project_drift(
        document,
        trace_id=trace_id,
        observation=observation,
        source=source,
        causal_parents=canonical_parents,
    )
    event = projected["event"]
    prior = _previous(event["eventId"])
    if prior is not None:
        extra = prior.get("extra")
        if not isinstance(extra, dict) or extra.get("event") != event:
            raise ValueError("observation receipt identity conflict")
        return {**projected, "receipt": prior, "receipt_replayed": True}

    row = append_receipt(
        {
            "trace_id": _receipt_trace(event["eventId"]),
            "capability_id": "aodl.observation",
            "prediction": "DRIFT",
            "action_taken": "record_observation",
            "route": "observation",
            "execution": "log_only",
            "extra": {
                "status": "recorded",
                "caller_trace_id": trace_id,
                "event": event,
                "aodl_intent_source_hash": projected["aodl_intent_source_hash"],
                "semantic_fingerprint_before": projected["semantic_fingerprint_before"],
                "semantic_fingerprint_after": projected["semantic_fingerprint_after"],
            },
        }
    )
    return {**projected, "receipt": row, "receipt_replayed": False}
