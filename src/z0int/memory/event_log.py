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

SCHEMA = "z0int.memory.event.v1"
INDEX_SCHEMA = "z0int.memory.event_index.v1"
STATE_SCHEMA = "z0int.memory.event_state.v1"
BLOB_SCHEMA = "z0int.memory.blob_ref.v1"
BLOB_THRESHOLD_BYTES = 16 * 1024
_EVENT_TYPE = re.compile(r"^[a-z][a-z0-9_.-]{0,127}$")


class EventLogCorruption(RuntimeError):
    """Committed memory history is malformed or inconsistent."""


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
    schema: str = SCHEMA

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
    expected = _checksum(_event_without_checksum(row))
    if row.get("checksum") != expected:
        raise EventLogCorruption("event checksum mismatch")


class EventLog:
    def __init__(self, root: Path | None = None, *, blob_threshold: int = BLOB_THRESHOLD_BYTES):
        base = Path(root) if root is not None else paths.home() / "memory"
        self.root = base
        self.events_path = base / "events.jsonl"
        self.index_path = base / "events.idx.jsonl"
        self.state_path = base / "events.state.json"
        self.blobs_dir = base / "blobs"
        self.blob_threshold = max(1, int(blob_threshold))
        self._ensure_layout()

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
    ) -> MemoryEvent:
        if not isinstance(event_type, str) or not _EVENT_TYPE.fullmatch(event_type):
            raise ValueError("invalid event_type")
        if not isinstance(source, str) or not source.strip() or len(source) > 512:
            raise ValueError("source must be nonempty text")
        payload_bytes = _canonical(payload)

        self.events_path.touch(mode=0o600, exist_ok=True)
        with self.events_path.open("r+b") as events:
            fcntl.flock(events, fcntl.LOCK_EX)
            event_id, committed_end = self._append_state_locked(events)
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
            return MemoryEvent.from_dict(base)

    def _append_index_row(self, row: dict[str, Any]) -> None:
        self.index_path.touch(mode=0o600, exist_ok=True)
        with self.index_path.open("a", encoding="utf-8") as idx:
            idx.write(json.dumps(row, sort_keys=True, separators=(",", ":")) + "\n")
            idx.flush()
            os.fsync(idx.fileno())

    def iter_events(self) -> Iterator[MemoryEvent]:
        if not self.events_path.is_file():
            return
        with self.events_path.open("rb") as fh:
            fcntl.flock(fh, fcntl.LOCK_SH)
            expected = 0
            while True:
                raw = fh.readline()
                if not raw:
                    break
                if not raw.endswith(b"\n"):
                    break
                row = self._decode_committed_line(raw, expected_id=expected)
                expected += 1
                yield MemoryEvent.from_dict(row)

    def rebuild_index(self) -> int:
        """Rebuild derived offset/state files without mutating events.jsonl."""
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
            self.rebuild_index()
        try:
            rows = [
                json.loads(line)
                for line in self.index_path.read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
        except (OSError, json.JSONDecodeError):
            self.rebuild_index()
            return self._index_rows()
        if any(
            not isinstance(row, dict)
            or row.get("schema") != INDEX_SCHEMA
            or row.get("event_id") != i
            for i, row in enumerate(rows)
        ):
            self.rebuild_index()
            return self._index_rows()
        return rows

    def get(self, event_id: int, *, resolve_blob: bool = False) -> MemoryEvent:
        if type(event_id) is not int or event_id < 0:
            raise KeyError(event_id)
        rows = self._index_rows()
        if event_id >= len(rows):
            # Index may be stale after a crash between event fsync and index fsync.
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
        events = list(self.iter_events())
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
