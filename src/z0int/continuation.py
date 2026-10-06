"""Durable *data* at named suspension points, not executable coroutine stacks.

A restored checkpoint is never authorization. The runtime must own the session,
recheck permissions/deadlines and reconcile external effects before dispatch.
See docs/continuations.md and issue #130 for the ownership/provenance boundary.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import re
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Mapping, Protocol

SCHEMA = "z0int.continuation.v1"
MAX_BYTES = 256 * 1024
MAX_DEPTH = 32
MAX_SAFE_INTEGER = 2**53 - 1
_HASH = re.compile(r"^cont_[0-9a-f]{64}$")
_SYMBOL = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_.:-]{0,127}$")
_STATUSES = {"not_started", "started", "completed", "unknown"}
_POLICIES = {"reattach", "readback", "idempotent_retry", "reissue_safe_read", "require_reconciliation"}


class InvalidCheckpoint(ValueError):
    """Malformed, incompatible or integrity-invalid persisted data."""


class StaleCheckpoint(InvalidCheckpoint):
    """Current runtime identity/revisions differ from the saved boundary."""


def _text(value: Any, name: str) -> None:
    if type(value) is not str or not value.strip() or len(value) > 4096:
        raise InvalidCheckpoint(f"{name} must be bounded nonempty text")


def _json_value(value: Any, depth: int = 0) -> None:
    if depth > MAX_DEPTH:
        raise InvalidCheckpoint("JSON nesting limit exceeded")
    if value is None or type(value) in (bool, str):
        return
    if type(value) is int:
        if abs(value) > MAX_SAFE_INTEGER:
            raise InvalidCheckpoint("encode integers outside the JS/Luau safe range as strings")
        return
    if type(value) is float and math.isfinite(value):
        return
    if type(value) is list:
        for item in value:
            _json_value(item, depth + 1)
        return
    if type(value) is dict and all(type(k) is str for k in value):
        for item in value.values():
            _json_value(item, depth + 1)
        return
    raise InvalidCheckpoint("only finite JSON values with string keys are supported")


def canonical_json(value: Any) -> str:
    """Versioned Python encoding; wire consumers preserve the payload STRING.

    This is deliberately not a claim of RFC 8785/JCS number compatibility.
    Keeping the canonical payload as a string prevents JS/Luau from changing
    float spelling, empty objects/arrays, or nulls when saving the envelope.
    """
    _json_value(value)
    try:
        out = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)
        if len(out.encode("utf-8")) > MAX_BYTES:
            raise InvalidCheckpoint("checkpoint byte limit exceeded")
        return out
    except (ValueError, UnicodeError, RecursionError) as exc:
        raise InvalidCheckpoint(str(exc)) from exc


def _pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise InvalidCheckpoint(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def parse_json(raw: str | bytes) -> Any:
    try:
        if len(raw.encode("utf-8") if isinstance(raw, str) else raw) > MAX_BYTES:
            raise InvalidCheckpoint("checkpoint byte limit exceeded")
        value = json.loads(raw, object_pairs_hook=_pairs)
        _json_value(value)
        return value
    except (ValueError, TypeError, UnicodeError, RecursionError) as exc:
        raise InvalidCheckpoint(str(exc)) from exc


@dataclass(frozen=True)
class ContinuationContext:
    """Supplied by the owning host on capture AND independently on restore."""

    runtime: str
    runtime_revision: str
    code_revision: str
    policy_revision: str
    authority_ref: str
    scope: str
    trace_id: str
    work_item_id: str
    attempt_lineage_id: str

    def __post_init__(self) -> None:
        for name, value in asdict(self).items():
            _text(value, name)


@dataclass(frozen=True)
class PendingOperation:
    operation_id: str
    kind: str
    status: str = "not_started"
    resume_policy: str = "require_reconciliation"
    durable_ref: str | None = None
    result_ref: str | None = None
    idempotency_key: str | None = None

    def __post_init__(self) -> None:
        _text(self.operation_id, "operation_id")
        _text(self.kind, "kind")
        if (type(self.status) is not str or type(self.resume_policy) is not str
                or self.status not in _STATUSES or self.resume_policy not in _POLICIES):
            raise InvalidCheckpoint("unsupported operation status or resume policy")
        for name in ("durable_ref", "result_ref", "idempotency_key"):
            if getattr(self, name) is not None:
                _text(getattr(self, name), name)
        if self.status == "completed" and self.result_ref is None:
            raise InvalidCheckpoint("completed operation needs a durable result reference")
        if self.resume_policy == "idempotent_retry" and self.idempotency_key is None:
            raise InvalidCheckpoint("idempotent_retry needs a target-supported idempotency key")


def _string_map(value: Any, name: str) -> None:
    if type(value) is not dict:
        raise InvalidCheckpoint(f"{name} must be a JSON object")
    for key, item in value.items():
        _text(key, name)
        _text(item, name)


def _validate(body: dict[str, Any]) -> None:
    expected = {
        "continuation_id", "context", "source_revisions", "phase", "resume_entrypoint",
        "state", "durable_refs", "pending", "terminal", "execution_completed",
        "verified_success", "verifier_ref", "parent_checkpoint_id",
    }
    if type(body) is not dict or set(body) != expected:
        raise InvalidCheckpoint("unexpected/missing checkpoint fields")
    _text(body["continuation_id"], "continuation_id")
    _text(body["phase"], "phase")
    if type(body["resume_entrypoint"]) is not str or not _SYMBOL.fullmatch(body["resume_entrypoint"]):
        raise InvalidCheckpoint("resume_entrypoint must be a symbolic name, not code")
    try:
        if type(body["context"]) is not dict:
            raise InvalidCheckpoint("context must be an object")
        ContinuationContext(**body["context"])
        if type(body["pending"]) is not list:
            raise InvalidCheckpoint("pending must be a list")
        ops = [PendingOperation(**op) for op in body["pending"]]
    except (TypeError, KeyError) as exc:
        raise InvalidCheckpoint(str(exc)) from exc
    if len({op.operation_id for op in ops}) != len(ops):
        raise InvalidCheckpoint("duplicate operation id")
    _string_map(body["source_revisions"], "source_revisions")
    _string_map(body["durable_refs"], "durable_refs")
    if type(body["state"]) is not dict or type(body["terminal"]) is not bool:
        raise InvalidCheckpoint("state must be an object and terminal a boolean")
    for name in ("execution_completed", "verified_success"):
        if body[name] is not None and type(body[name]) is not bool:
            raise InvalidCheckpoint(f"{name} must be boolean or null")
    if body["verifier_ref"] is not None:
        _text(body["verifier_ref"], "verifier_ref")
    if body["verified_success"] is True and (
        body["execution_completed"] is not True or body["verifier_ref"] is None
    ):
        raise InvalidCheckpoint("verified success requires completed execution and verifier evidence")
    parent = body["parent_checkpoint_id"]
    if parent is not None and (type(parent) is not str or not _HASH.fullmatch(parent)):
        raise InvalidCheckpoint("invalid parent checkpoint id")


@dataclass(frozen=True)
class ContinuationCheckpoint:
    """Immutable canonical payload; decoded state is always a detached copy."""

    payload: str

    def __post_init__(self) -> None:
        body = parse_json(self.payload)
        _validate(body)
        if canonical_json(body) != self.payload:
            raise InvalidCheckpoint("payload is not canonically encoded")

    @classmethod
    def build(
        cls, *, continuation_id: str, context: ContinuationContext, phase: str,
        resume_entrypoint: str, state: dict[str, Any],
        source_revisions: Mapping[str, str] | None = None,
        durable_refs: Mapping[str, str] | None = None,
        pending: tuple[PendingOperation, ...] = (), terminal: bool = False,
        execution_completed: bool | None = None, verified_success: bool | None = None,
        verifier_ref: str | None = None, parent_checkpoint_id: str | None = None,
    ) -> ContinuationCheckpoint:
        return cls(canonical_json({
            "continuation_id": continuation_id, "context": asdict(context),
            "source_revisions": dict(source_revisions or {}), "phase": phase,
            "resume_entrypoint": resume_entrypoint, "state": state,
            "durable_refs": dict(durable_refs or {}), "pending": [asdict(op) for op in pending],
            "terminal": terminal, "execution_completed": execution_completed,
            "verified_success": verified_success, "verifier_ref": verifier_ref,
            "parent_checkpoint_id": parent_checkpoint_id,
        }))

    @property
    def checkpoint_id(self) -> str:
        return "cont_" + hashlib.sha256((SCHEMA + "\0" + self.payload).encode("utf-8")).hexdigest()

    @property
    def body(self) -> dict[str, Any]:
        return parse_json(self.payload)

    def to_dict(self) -> dict[str, str]:
        return {"schema": SCHEMA, "checkpoint_id": self.checkpoint_id, "payload": self.payload}

    def dumps(self) -> str:
        return canonical_json(self.to_dict())

    @classmethod
    def from_dict(cls, row: dict[str, Any]) -> ContinuationCheckpoint:
        if type(row) is not dict or set(row) != {"schema", "checkpoint_id", "payload"}:
            raise InvalidCheckpoint("invalid envelope fields")
        if row["schema"] != SCHEMA or type(row["payload"]) is not str:
            raise InvalidCheckpoint("unsupported schema or payload encoding")
        cp = cls(row["payload"])
        if row["checkpoint_id"] != cp.checkpoint_id:
            raise InvalidCheckpoint("checkpoint checksum mismatch")
        return cp

    @classmethod
    def loads(cls, raw: str | bytes) -> ContinuationCheckpoint:
        return cls.from_dict(parse_json(raw))

    def receipt_extra(self) -> dict[str, Any]:
        """Join existing DecisionReceipt.extra; this is not an outcome verifier."""
        body = self.body
        return {"continuation": {
            "schema": SCHEMA, "checkpoint_id": self.checkpoint_id,
            "continuation_id": body["continuation_id"],
            "replay_key": self.checkpoint_id,
            **{k: body["context"][k] for k in ("trace_id", "work_item_id", "attempt_lineage_id")},
            "phase": body["phase"], "execution_completed": body["execution_completed"],
            "verified_success": body["verified_success"], "verifier_ref": body["verifier_ref"],
        }}


@dataclass(frozen=True)
class RestorePlan:
    checkpoint: ContinuationCheckpoint
    disposition: str  # terminal | reconcile | runtime_owned


def plan_restore(
    checkpoint: ContinuationCheckpoint, *, context: ContinuationContext,
    source_revisions: Mapping[str, str], allowed_entrypoints: set[str],
) -> RestorePlan:
    """Pure validation/readback plan. Does not import, schedule or execute code.

    A policy label like 'idempotent_retry' is NOT proof the target supports it.
    Started operations become unknown after process loss, even with that label.
    """
    body = checkpoint.body
    if body["context"] != asdict(context) or body["source_revisions"] != dict(source_revisions):
        raise StaleCheckpoint("runtime identity, scope or revision changed")
    if body["resume_entrypoint"] not in allowed_entrypoints:
        raise StaleCheckpoint("entrypoint is not registered by the current runtime")
    changed = False
    for op in body["pending"]:
        if op["status"] == "started":
            op["status"] = "unknown"
            changed = True
    if changed:
        body["parent_checkpoint_id"] = checkpoint.checkpoint_id
        checkpoint = ContinuationCheckpoint(canonical_json(body))
    disposition = "runtime_owned"
    if body["terminal"]:
        disposition = "terminal"
    elif any(op["status"] == "unknown" for op in body["pending"]):
        disposition = "reconcile"
    return RestorePlan(checkpoint, disposition)


def write_atomic(path: Path, value: Any) -> None:
    """Publish a bounded JSON document; a failed replace leaves the old one.

    Runtime-selected path only. This is atomic publication, not a writer lease.
    POSIX additionally fsyncs the directory; directory fsync is unavailable on
    Windows. Callers must serialize competing owners themselves.
    """
    raw = canonical_json(value).encode("utf-8")
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as out:
            out.write(raw)
            out.flush()
            os.fsync(out.fileno())
        os.replace(tmp, path)
        if os.name == "posix":
            directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def read_json(path: Path) -> Any:
    with Path(path).open("rb") as source:
        return parse_json(source.read(MAX_BYTES + 1))


class EventAppender(Protocol):
    def append(self, event_type: str, payload: Any, **kwargs: Any) -> Any: ...


def record_checkpoint(log: EventAppender, checkpoint: ContinuationCheckpoint, *,
                      parent_event_ids: tuple[int, ...] = ()) -> Any:
    """Append via the existing EventLog (including its large-payload blobs).

    Duplicate admissions retain the same replay_key. Consumers deduplicate on
    that key; an event ID or a receipt is NOT an exactly-once execution lease.
    """
    body = checkpoint.body
    return log.append(
        "continuation.checkpoint", {"checkpoint": checkpoint.to_dict(), "extra": checkpoint.receipt_extra()},
        source="z0int.continuation", project=body["context"]["scope"],
        session_id=body["continuation_id"], parent_event_ids=parent_event_ids,
    )
