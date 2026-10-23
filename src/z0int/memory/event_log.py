"""Append-only canonical event ledger for z0 lifelong memory.

Design invariant: events.jsonl is source truth. The byte-offset index and blob
directory are supporting structures; every projection must retain event ids.

The index may be rebuilt freely. The event file is never rewritten by this
module. A malformed committed line fails closed. An incomplete trailing write
is ignored by readers but blocks further append until an operator repairs or
copies the log, avoiding silent history mutation.
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator, Mapping

from .. import paths
from ..memory_contract import EventIdentity

SCHEMA = "z0int.memory.event.v1"
INDEX_SCHEMA = "z0int.memory.event_index.v1"
STATE_SCHEMA = "z0int.memory.event_state.v1"
BLOB_SCHEMA = "z0int.memory.blob_ref.v1"
BLOB_THRESHOLD_BYTES = 16 * 1024
_EVENT_TYPE = re.compile(r"^[a-z][a-z0-9_.-]{0,127}$")
BODY_FIELDS = frozenset(
    {
        "body",
        "content",
        "text",
        "message",
        "prompt",
        "response",
        "thinking",
        "thinking_text",
        "output",
        "transcript",
        "command",
    }
)


class EventLogCorruption(RuntimeError):
    """Committed memory history is malformed or inconsistent."""


class EventIdentityConflict(ValueError):
    """A source identity was replayed with a different hash or event payload."""


class SourceIngestDisabled(PermissionError):
    """Identity-aware source ingestion is off until references-only is enabled."""


def source_ingest_enabled() -> bool:
    """Read the existing owner opt-in; never enable source ingestion implicitly."""
    if os.environ.get("Z0INT_MEMORY_SOURCE_INGEST") == "references":
        return True
    try:
        config = json.loads((paths.home() / "config" / "memory.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    return isinstance(config, dict) and config.get("eventlog_source_ingest") == "references"


def _body_keys(value: Any) -> set[str]:
    if isinstance(value, Mapping):
        found = {str(key) for key in value if str(key).lower() in BODY_FIELDS}
        for child in value.values():
            found |= _body_keys(child)
        return found
    if isinstance(value, (list, tuple)):
        found: set[str] = set()
        for child in value:
            found |= _body_keys(child)
        return found
    return set()


@dataclass(frozen=True)
class MemoryEvent:
    event_id: int
    event_type: str
    ts: float
    source: str
    payload: Any | None
    blob: dict[str, Any] | None
    project: str | None
    session_id: str | None
    parent_event_ids: tuple[int, ...]
    checksum: str
    identity: dict[str, Any] | None = None
    schema: str = SCHEMA

    def event_identity(self) -> EventIdentity | None:
        """Return stable source identity annotated with this ledger position."""
        if self.identity is None:
            return None
        return EventIdentity(**self.identity, ledger_seq=self.event_id)

    def to_dict(self) -> dict[str, Any]:
        row: dict[str, Any] = {
            "schema": self.schema,
            "event_id": self.event_id,
            "event_type": self.event_type,
            "ts": self.ts,
            "source": self.source,
            "payload": self.payload,
            "parent_event_ids": list(self.parent_event_ids),
            "checksum": self.checksum,
        }
        if self.blob is not None:
            row["blob"] = dict(self.blob)
        if self.project is not None:
            row["project"] = self.project
        if self.session_id is not None:
            row["session_id"] = self.session_id
        if self.identity is not None:
            row["identity"] = dict(self.identity)
        return row

    @classmethod
    def from_dict(cls, row: Mapping[str, Any]) -> "MemoryEvent":
        validate_event_dict(dict(row))
        return cls(
            event_id=int(row["event_id"]),
            event_type=str(row["event_type"]),
            ts=float(row["ts"]),
            source=str(row["source"]),
            payload=row.get("payload"),
            blob=dict(row["blob"]) if isinstance(row.get("blob"), Mapping) else None,
            project=str(row["project"]) if row.get("project") is not None else None,
            session_id=str(row["session_id"]) if row.get("session_id") is not None else None,
            parent_event_ids=tuple(int(x) for x in row.get("parent_event_ids") or []),
            checksum=str(row["checksum"]),
            identity=dict(row["identity"]) if isinstance(row.get("identity"), Mapping) else None,
        )


def _canonical(value: Any) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")


def _checksum(row_without_checksum: Mapping[str, Any]) -> str:
    return hashlib.sha256(_canonical(dict(row_without_checksum))).hexdigest()


def _event_without_checksum(row: Mapping[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in row.items() if k != "checksum"}


def validate_event_dict(row: dict[str, Any]) -> None:
    if row.get("schema") != SCHEMA:
        raise EventLogCorruption("unsupported event schema")
    event_id = row.get("event_id")
    if type(event_id) is not int or event_id < 0:
        raise EventLogCorruption("invalid event_id")
    event_type = row.get("event_type")
    if not isinstance(event_type, str) or not _EVENT_TYPE.fullmatch(event_type):
        raise EventLogCorruption("invalid event_type")
    if type(row.get("ts")) not in (int, float):
        raise EventLogCorruption("invalid timestamp")
    if not isinstance(row.get("source"), str) or not row["source"].strip():
        raise EventLogCorruption("invalid source")
    parents = row.get("parent_event_ids") or []
    if not isinstance(parents, list) or any(type(x) is not int or x < 0 or x >= event_id for x in parents):
        raise EventLogCorruption("invalid parent_event_ids")
    blob = row.get("blob")
    if blob is not None:
        if row.get("payload") is not None:
            raise EventLogCorruption("event cannot carry inline payload and blob")
        if not isinstance(blob, dict) or blob.get("schema") != BLOB_SCHEMA:
            raise EventLogCorruption("invalid blob reference")
        sha = blob.get("sha256")
        if not isinstance(sha, str) or not re.fullmatch(r"[0-9a-f]{64}", sha):
            raise EventLogCorruption("invalid blob checksum")
        if type(blob.get("bytes")) is not int or blob["bytes"] < 0:
            raise EventLogCorruption("invalid blob length")
    identity = row.get("identity")
    if identity is not None:
        if not isinstance(identity, dict):
            raise EventLogCorruption("invalid event identity")
        try:
            parsed_identity = EventIdentity(**identity)
            derived_identity = EventIdentity.from_source(
                source_system=parsed_identity.source_system,
                source_session=parsed_identity.source_session,
                source_event_id=parsed_identity.source_event_id,
                source_seq=parsed_identity.source_seq,
                payload_hash=parsed_identity.payload_hash,
            )
        except (TypeError, ValueError) as exc:
            raise EventLogCorruption("invalid event identity") from exc
        if parsed_identity.event_uid != derived_identity.event_uid:
            raise EventLogCorruption("invalid event identity")
    expected = _checksum(_event_without_checksum(row))
    if row.get("checksum") != expected:
        raise EventLogCorruption("event checksum mismatch")


class EventLog:
    def __init__(
        self,
        root: Path | None = None,
        *,
        blob_threshold: int = BLOB_THRESHOLD_BYTES,
        read_only: bool = False,
    ):
        base = Path(root) if root is not None else paths.home() / "memory"
        self.root = base
        self.events_path = base / "events.jsonl"
        self.index_path = base / "events.idx.jsonl"
        self.state_path = base / "events.state.json"
        self.blobs_dir = base / "blobs"
        self.blob_threshold = max(1, int(blob_threshold))
        self.read_only = bool(read_only)
        # event_uid -> (event_id, payload_hash, offset, byte length), warmed from
        # the canonical event file and invalidated when its inode changes.
        self._uids: dict[str, tuple[int, str, int, int]] = {}
        self._uids_at: tuple[int | None, int] = (None, 0)
        if not self.read_only:
            self._ensure_layout()

    def _writable(self) -> None:
        if self.read_only:
            raise PermissionError("read-only view of the canonical ledger: workers cannot write it")

    def _identity_index_locked(self, events, committed_end: int) -> dict[str, tuple[int, str, int, int]]:
        """Index source identities by reading only rows since the cached offset."""
        inode = os.fstat(events.fileno()).st_ino
        if self._uids_at[0] != inode or self._uids_at[1] > committed_end:
            self._uids = {}
            self._uids_at = (inode, 0)
        events.seek(self._uids_at[1])
        while events.tell() < committed_end:
            offset = events.tell()
            raw = events.readline()
            row = self._decode_committed_line(raw)
            identity = row.get("identity")
            if identity:
                self._uids.setdefault(
                    identity["event_uid"],
                    (row["event_id"], str(identity.get("payload_hash")), offset, len(raw)),
                )
        self._uids_at = (inode, committed_end)
        return self._uids

    def _ensure_layout(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.blobs_dir.mkdir(parents=True, exist_ok=True, mode=0o700)

    def _store_blob(self, payload_bytes: bytes) -> dict[str, Any]:
        sha = hashlib.sha256(payload_bytes).hexdigest()
        path = self.blobs_dir / f"{sha}.json"
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
        try:
            fd = os.open(path, flags, 0o600)
        except FileExistsError:
            existing = path.read_bytes()
            if not path.is_file() or path.is_symlink() or hashlib.sha256(existing).hexdigest() != sha:
                raise EventLogCorruption("content-addressed blob mismatch")
        else:
            try:
                view = memoryview(payload_bytes)
                total = 0
                while total < len(view):
                    written = os.write(fd, view[total:])
                    if written <= 0:
                        raise OSError("short blob write")
                    total += written
                os.fsync(fd)
            finally:
                os.close(fd)
        return {
            "schema": BLOB_SCHEMA,
            "sha256": sha,
            "bytes": len(payload_bytes),
            "media_type": "application/json",
        }

    def _decode_committed_line(self, raw: bytes, *, expected_id: int | None = None) -> dict[str, Any]:
        try:
            row = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise EventLogCorruption("malformed committed event") from exc
        if not isinstance(row, dict):
            raise EventLogCorruption("event is not an object")
        validate_event_dict(row)
        if expected_id is not None and row["event_id"] != expected_id:
            raise EventLogCorruption(
                f"event id discontinuity: expected {expected_id}, got {row['event_id']}"
            )
        return row

    def _scan_locked(self, fh) -> list[tuple[dict[str, Any], int, int]]:
        fh.seek(0)
        rows: list[tuple[dict[str, Any], int, int]] = []
        expected = 0
        while True:
            offset = fh.tell()
            raw = fh.readline()
            if not raw:
                break
            if not raw.endswith(b"\n"):
                # A crash can leave only the final append incomplete. Readers
                # ignore it, but append must not step over it.
                break
            row = self._decode_committed_line(raw, expected_id=expected)
            rows.append((row, offset, len(raw)))
            expected += 1
        return rows

    def _write_json_atomic(self, path: Path, value: Mapping[str, Any]) -> None:
        tmp = path.with_suffix(path.suffix + ".tmp")
        with tmp.open("w", encoding="utf-8") as out:
            out.write(json.dumps(dict(value), sort_keys=True, separators=(",", ":")) + "\n")
            out.flush()
            os.fsync(out.fileno())
        os.replace(tmp, path)

    def _write_index_rows(self, rows: list[tuple[dict[str, Any], int, int]]) -> None:
        tmp = self.index_path.with_suffix(".tmp")
        with tmp.open("w", encoding="utf-8") as out:
            for row, offset, length in rows:
                out.write(
                    json.dumps(
                        {
                            "schema": INDEX_SCHEMA,
                            "event_id": row["event_id"],
                            "offset": offset,
                            "length": length,
                            "checksum": row["checksum"],
                        },
                        sort_keys=True,
                        separators=(",", ":"),
                    )
                    + "\n"
                )
            out.flush()
            os.fsync(out.fileno())
        os.replace(tmp, self.index_path)

    def _write_state(
        self,
        *,
        next_event_id: int,
        end_offset: int,
        last_row: dict[str, Any] | None,
        last_offset: int = 0,
        last_length: int = 0,
    ) -> None:
        value: dict[str, Any] = {
            "schema": STATE_SCHEMA,
            "next_event_id": next_event_id,
            "end_offset": end_offset,
        }
        if last_row is not None:
            value.update(
                last_event_id=last_row["event_id"],
                last_offset=last_offset,
                last_length=last_length,
                last_checksum=last_row["checksum"],
            )
        self._write_json_atomic(self.state_path, value)

    def _repair_derived_locked(self, events) -> tuple[int, int]:
        rows = self._scan_locked(events)
        committed_end = rows[-1][1] + rows[-1][2] if rows else 0
        events.seek(0, os.SEEK_END)
        if events.tell() != committed_end:
            raise EventLogCorruption("incomplete trailing event blocks append")
        self._write_index_rows(rows)
        if rows:
            row, offset, length = rows[-1]
            self._write_state(
                next_event_id=len(rows),
                end_offset=committed_end,
                last_row=row,
                last_offset=offset,
                last_length=length,
            )
        else:
            self._write_state(next_event_id=0, end_offset=0, last_row=None)
        return len(rows), committed_end

    def _last_index_row(self) -> dict[str, Any] | None:
        if not self.index_path.is_file():
            return None
        with self.index_path.open("rb") as idx:
            idx.seek(0, os.SEEK_END)
            end = idx.tell()
            if end == 0:
                return None
            idx.seek(end - 1)
            if idx.read(1) != b"\n":
                return None
            start = max(0, end - 8192)
            idx.seek(start)
            lines = idx.read(end - start).splitlines()
        if not lines:
            return None
        try:
            row = json.loads(lines[-1])
        except json.JSONDecodeError:
            return None
        if (
            not isinstance(row, dict)
            or row.get("schema") != INDEX_SCHEMA
            or type(row.get("event_id")) is not int
            or type(row.get("offset")) is not int
            or type(row.get("length")) is not int
        ):
            return None
        return row

    def _append_state_locked(self, events) -> tuple[int, int]:
        """Fast O(1) append state; full ledger scan only on stale/crash recovery."""
        events.seek(0, os.SEEK_END)
        file_end = events.tell()
        try:
            state = json.loads(self.state_path.read_text(encoding="utf-8"))
            if (
                not isinstance(state, dict)
                or state.get("schema") != STATE_SCHEMA
                or type(state.get("next_event_id")) is not int
                or type(state.get("end_offset")) is not int
                or state["next_event_id"] < 0
                or state["end_offset"] != file_end
            ):
                raise ValueError("stale state")
            next_id = int(state["next_event_id"])
            if next_id == 0:
                if file_end != 0:
                    raise ValueError("empty state over nonempty log")
                return 0, 0
            offset = int(state.get("last_offset", -1))
            length = int(state.get("last_length", -1))
            if offset < 0 or length <= 0 or offset + length != file_end:
                raise ValueError("invalid last event range")
            events.seek(offset)
            raw = events.read(length)
            row = self._decode_committed_line(raw, expected_id=next_id - 1)
            if row["checksum"] != state.get("last_checksum"):
                raise ValueError("last checksum mismatch")
            index_last = self._last_index_row()
            if (
                index_last is None
                or index_last.get("event_id") != next_id - 1
                or index_last.get("offset") != offset
                or index_last.get("length") != length
                or index_last.get("checksum") != row["checksum"]
            ):
                raise ValueError("index tail does not match state")
            return next_id, file_end
        except (OSError, ValueError, TypeError, json.JSONDecodeError, EventLogCorruption):
            return self._repair_derived_locked(events)

    def append(
        self,
        event_type: str,
        payload: Any,
        *,
        source: str,
        project: str | None = None,
        session_id: str | None = None,
        parent_event_ids: tuple[int, ...] | list[int] = (),
        ts: float | None = None,
        identity: EventIdentity | None = None,
    ) -> MemoryEvent:
        """Append an ordinary event; native admission markers use claims API."""
        self._writable()
        if isinstance(payload, Mapping) and (
            (event_type == "source.reference" and payload.get("native_source_validated") is True)
            or (
                event_type == "memory.claim"
                and (payload.get("origin_trust") == "explicit_user" or "admission_method" in payload)
            )
        ):
            raise ValueError("native user admission must go through admit_user_claim")
        return self._append_event(
            event_type,
            payload,
            source=source,
            project=project,
            session_id=session_id,
            parent_event_ids=parent_event_ids,
            ts=ts,
            identity=identity,
        )

    def _append_admitted_user_event(
        self,
        event_type: str,
        payload: Any,
        *,
        source: str,
        project: str | None = None,
        session_id: str | None = None,
        parent_event_ids: tuple[int, ...] | list[int] = (),
        ts: float | None = None,
        identity: EventIdentity | None = None,
    ) -> MemoryEvent:
        """Internal writer used by admit_user_claim after native hydration validation."""
        return self._append_event(
            event_type,
            payload,
            source=source,
            project=project,
            session_id=session_id,
            parent_event_ids=parent_event_ids,
            ts=ts,
            identity=identity,
        )

    def _append_event(
        self,
        event_type: str,
        payload: Any,
        *,
        source: str,
        project: str | None = None,
        session_id: str | None = None,
        parent_event_ids: tuple[int, ...] | list[int] = (),
        ts: float | None = None,
        identity: EventIdentity | None = None,
    ) -> MemoryEvent:
        """Shared write primitive. Callers enforce any domain admission policy."""
        self._writable()
        if not isinstance(event_type, str) or not _EVENT_TYPE.fullmatch(event_type):
            raise ValueError("invalid event_type")
        if not isinstance(source, str) or not source.strip() or len(source) > 512:
            raise ValueError("source must be nonempty text")
        identity_row = None
        if identity is not None:
            derived_identity = EventIdentity.from_source(
                source_system=identity.source_system,
                source_session=identity.source_session,
                source_event_id=identity.source_event_id,
                source_seq=identity.source_seq,
                payload_hash=identity.payload_hash,
            )
            if identity.event_uid != derived_identity.event_uid:
                raise ValueError("event_uid does not match the source identity fields")
            if not source_ingest_enabled():
                raise SourceIngestDisabled(
                    "source-derived ingestion is off until the owner confirms references-only"
                )
            if event_type == "source.reference" and (body := _body_keys(payload)):
                raise ValueError(
                    f"identity-bearing source references store references only: body field(s) {sorted(body)}"
                )
            identity_row = {
                key: value
                for key, value in identity.to_dict().items()
                if key not in ("ledger_seq", "schema")
            }
        payload_bytes = _canonical(payload)

        self.events_path.touch(mode=0o600, exist_ok=True)
        with self.events_path.open("r+b") as events:
            fcntl.flock(events, fcntl.LOCK_EX)
            event_id, committed_end = self._append_state_locked(events)
            if identity_row is not None:
                seen = self._identity_index_locked(events, committed_end).get(identity_row["event_uid"])
                if seen is not None:
                    if seen[1] != identity_row["payload_hash"]:
                        raise EventIdentityConflict(
                            f"{identity_row['event_uid']} already has another payload_hash"
                        )
                    events.seek(seen[2])
                    raw = events.read(seen[3])
                    prior_row = self._decode_committed_line(raw, expected_id=seen[0])
                    prior_event = MemoryEvent.from_dict(prior_row)
                    prior_payload = (
                        self.read_blob(prior_event.blob)
                        if prior_event.blob is not None
                        else prior_event.payload
                    )
                    if (
                        prior_event.event_type != event_type
                        or prior_event.source != source
                        or prior_event.project != (str(project) if project is not None else None)
                        or prior_event.session_id != (str(session_id) if session_id is not None else None)
                        or prior_event.parent_event_ids != tuple(int(x) for x in parent_event_ids)
                        or _canonical(prior_payload) != payload_bytes
                    ):
                        raise EventIdentityConflict(
                            f"{identity_row['event_uid']} already has a different event payload"
                        )
                    return prior_event
            parents = tuple(int(x) for x in parent_event_ids)
            if any(x < 0 or x >= event_id for x in parents):
                raise ValueError("parent_event_ids must reference earlier events")

            blob = None
            inline_payload: Any | None = payload
            if len(payload_bytes) > self.blob_threshold:
                blob = self._store_blob(payload_bytes)
                inline_payload = None

            base: dict[str, Any] = {
                "schema": SCHEMA,
                "event_id": event_id,
                "event_type": event_type,
                "ts": float(time.time() if ts is None else ts),
                "source": source,
                "payload": inline_payload,
                "parent_event_ids": list(parents),
            }
            if blob is not None:
                base["blob"] = blob
            if project is not None:
                base["project"] = str(project)
            if session_id is not None:
                base["session_id"] = str(session_id)
            if identity_row is not None:
                base["identity"] = identity_row
            base["checksum"] = _checksum(base)
            line = _canonical(base) + b"\n"

            events.seek(0, os.SEEK_END)
            offset = events.tell()
            written = events.write(line)
            if written != len(line):
                raise OSError("short event write")
            events.flush()
            os.fsync(events.fileno())

            self._append_index_row(
                {
                    "schema": INDEX_SCHEMA,
                    "event_id": event_id,
                    "offset": offset,
                    "length": len(line),
                    "checksum": base["checksum"],
                }
            )
            self._write_state(
                next_event_id=event_id + 1,
                end_offset=offset + len(line),
                last_row=base,
                last_offset=offset,
                last_length=len(line),
            )
            if identity_row is not None and self._uids_at[1] == offset:
                self._uids[identity_row["event_uid"]] = (
                    event_id,
                    identity_row["payload_hash"],
                    offset,
                    len(line),
                )
                self._uids_at = (self._uids_at[0], offset + len(line))
            return MemoryEvent.from_dict(base)

    def _append_index_row(self, row: dict[str, Any]) -> None:
        self.index_path.touch(mode=0o600, exist_ok=True)
        with self.index_path.open("a", encoding="utf-8") as idx:
            idx.write(json.dumps(row, sort_keys=True, separators=(",", ":")) + "\n")
            idx.flush()
            os.fsync(idx.fileno())

    def _validate_complete_snapshot_state(
        self,
        events,
        *,
        event_count: int,
        last_row: Mapping[str, Any] | None,
        last_offset: int,
        last_length: int,
    ) -> None:
        """Check the committed endpoint/count while the event file lock is held."""
        try:
            state = json.loads(self.state_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise EventLogCorruption("event state is missing, unreadable, or malformed") from exc
        if not isinstance(state, dict) or state.get("schema") != STATE_SCHEMA:
            raise EventLogCorruption("event state has an unsupported schema")
        if (
            type(state.get("next_event_id")) is not int
            or type(state.get("end_offset")) is not int
            or state["next_event_id"] < 0
            or state["end_offset"] < 0
        ):
            raise EventLogCorruption("event state has invalid committed bounds")

        actual_end = os.fstat(events.fileno()).st_size
        if events.tell() != actual_end:
            raise EventLogCorruption("event file changed during complete snapshot")
        if state["next_event_id"] != event_count or state["end_offset"] != actual_end:
            raise EventLogCorruption("event state does not match committed event count or endpoint")

        last_fields = ("last_event_id", "last_offset", "last_length", "last_checksum")
        if event_count == 0:
            if any(field in state for field in last_fields):
                raise EventLogCorruption("empty event state has a last-event record")
            return
        if last_row is None or any(field not in state for field in last_fields):
            raise EventLogCorruption("event state is missing its last-event record")
        if (
            type(state.get("last_event_id")) is not int
            or type(state.get("last_offset")) is not int
            or type(state.get("last_length")) is not int
            or state["last_event_id"] != event_count - 1
            or state["last_offset"] != last_offset
            or state["last_length"] != last_length
            or state.get("last_checksum") != last_row.get("checksum")
        ):
            raise EventLogCorruption("event state last-event record does not match committed history")

    def iter_events(self, *, require_complete: bool = False) -> Iterator[MemoryEvent]:
        """Iterate committed events, optionally requiring a complete snapshot.

        The default preserves recovery behavior for callers that intentionally
        read the valid prefix after an interrupted append. Snapshot consumers
        must set ``require_complete`` so an unterminated tail cannot be mistaken
        for the whole canonical history.
        """
        if not self.events_path.is_file():
            if require_complete:
                raise EventLogCorruption("event log is unavailable")
            return
        with self.events_path.open("rb") as fh:
            fcntl.flock(fh, fcntl.LOCK_SH)
            expected = 0
            complete_events: list[MemoryEvent] = []
            last_row: dict[str, Any] | None = None
            last_offset = 0
            last_length = 0
            while True:
                offset = fh.tell()
                raw = fh.readline()
                if not raw:
                    break
                if not raw.endswith(b"\n"):
                    if require_complete:
                        raise EventLogCorruption("incomplete trailing event")
                    break
                row = self._decode_committed_line(raw, expected_id=expected)
                event = MemoryEvent.from_dict(row)
                last_row = row
                last_offset = offset
                last_length = len(raw)
                expected += 1
                if require_complete:
                    complete_events.append(event)
                else:
                    yield event
            if require_complete:
                self._validate_complete_snapshot_state(
                    fh,
                    event_count=expected,
                    last_row=last_row,
                    last_offset=last_offset,
                    last_length=last_length,
                )
                yield from complete_events

    def rebuild_index(self) -> int:
        """Rebuild derived offset/state files without mutating events.jsonl."""
        self._writable()
        self.events_path.touch(mode=0o600, exist_ok=True)
        with self.events_path.open("r+b") as events:
            fcntl.flock(events, fcntl.LOCK_EX)
            rows = self._scan_locked(events)
            self._write_index_rows(rows)
            committed_end = rows[-1][1] + rows[-1][2] if rows else 0
            if rows:
                row, offset, length = rows[-1]
                self._write_state(
                    next_event_id=len(rows),
                    end_offset=committed_end,
                    last_row=row,
                    last_offset=offset,
                    last_length=length,
                )
            else:
                self._write_state(next_event_id=0, end_offset=0, last_row=None)
        return len(rows)

    def _index_rows(self) -> list[dict[str, Any]]:
        if not self.index_path.is_file():
            if self.read_only:
                if not self.events_path.is_file():
                    return []
                with self.events_path.open("rb") as events:
                    rows: list[dict[str, Any]] = []
                    expected = 0
                    while True:
                        raw = events.readline()
                        if not raw:
                            break
                        if not raw.endswith(b"\n"):
                            break
                        row = self._decode_committed_line(raw, expected_id=expected)
                        rows.append({
                            "schema": INDEX_SCHEMA,
                            "event_id": expected,
                            "offset": events.tell() - len(raw),
                            "length": len(raw),
                            "checksum": row["checksum"],
                        })
                        expected += 1
                return rows
            self.rebuild_index()
        try:
            rows = [
                json.loads(line)
                for line in self.index_path.read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
        except (OSError, json.JSONDecodeError):
            if self.read_only:
                raise EventLogCorruption("read-only event index is malformed or unavailable")
            self.rebuild_index()
            return self._index_rows()
        if any(
            not isinstance(row, dict)
            or row.get("schema") != INDEX_SCHEMA
            or row.get("event_id") != i
            for i, row in enumerate(rows)
        ):
            if self.read_only:
                raise EventLogCorruption("read-only event index is stale")
            self.rebuild_index()
            return self._index_rows()
        return rows

    def get(self, event_id: int, *, resolve_blob: bool = False) -> MemoryEvent:
        if type(event_id) is not int or event_id < 0:
            raise KeyError(event_id)
        rows = self._index_rows()
        if event_id >= len(rows):
            # Index may be stale after a crash between event fsync and index fsync.
            if self.read_only:
                raise KeyError(event_id)
            self.rebuild_index()
            rows = self._index_rows()
        if event_id >= len(rows):
            raise KeyError(event_id)
        ref = rows[event_id]
        with self.events_path.open("rb") as events:
            events.seek(int(ref["offset"]))
            raw = events.read(int(ref["length"]))
        row = self._decode_committed_line(raw, expected_id=event_id)
        if row["checksum"] != ref.get("checksum"):
            if self.read_only:
                raise EventLogCorruption("read-only event index checksum mismatch")
            self.rebuild_index()
            return self.get(event_id, resolve_blob=resolve_blob)
        event = MemoryEvent.from_dict(row)
        if not resolve_blob or event.blob is None:
            return event
        payload = self.read_blob(event.blob)
        return MemoryEvent(
            event_id=event.event_id,
            event_type=event.event_type,
            ts=event.ts,
            source=event.source,
            payload=payload,
            blob=event.blob,
            project=event.project,
            session_id=event.session_id,
            parent_event_ids=event.parent_event_ids,
            checksum=event.checksum,
            identity=event.identity,
        )

    def read_blob(self, ref: Mapping[str, Any]) -> Any:
        sha = str(ref.get("sha256") or "")
        if not re.fullmatch(r"[0-9a-f]{64}", sha):
            raise EventLogCorruption("invalid blob reference")
        path = self.blobs_dir / f"{sha}.json"
        raw = path.read_bytes()
        if hashlib.sha256(raw).hexdigest() != sha or len(raw) != int(ref.get("bytes") or -1):
            raise EventLogCorruption("blob integrity check failed")
        return json.loads(raw)

    def verify(self) -> dict[str, Any]:
        # Verification certifies the complete committed ledger, not merely the
        # prefix that recovery readers intentionally expose after a truncation.
        events = list(self.iter_events(require_complete=True))
        blobs = 0
        bytes_total = 0
        for event in events:
            if event.blob is not None:
                self.read_blob(event.blob)
                blobs += 1
                bytes_total += int(event.blob["bytes"])
        return {
            "schema": "z0int.memory.event_log.verify.v1",
            "events": len(events),
            "blobs": blobs,
            "blob_bytes": bytes_total,
            "last_event_id": events[-1].event_id if events else None,
            "ok": True,
        }
