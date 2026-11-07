"""OptMem-style temporal projection over the canonical z0 EventLog.

P0 proves structure, not semantic summarization:

- events.jsonl remains canonical;
- TREE is a rebuildable O(log n) age-decay cache, not another event copy;
- persisted nodes are the current aligned dyadic cover of coarse history;
- recent events remain raw;
- appends after nap remain raw without changing the coarse revision;
- sticky old events expand only their local node path;
- zoom() resolves exact canonical payloads.

A learned nap summarizer can later replace the structural node summary without
changing addresses, provenance, cover, or zoom semantics.
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import math
import os
import re
import shutil
import tempfile
from contextlib import contextmanager
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from .event_log import EventLog, EventLogCorruption, MemoryEvent

SCHEMA = "z0int.memory.optmem_tree.v2"
NODE_SCHEMA = "z0int.memory.optmem_node.v1"
COVER_SCHEMA = "z0int.memory.optmem_cover.v2"
ZOOM_SCHEMA = "z0int.memory.optmem_zoom.v2"


class CoverBudgetExceeded(ValueError):
    """A lossless requested cover cannot fit the configured hard budget."""


@dataclass(frozen=True)
class TreeNode:
    level: int
    start_event_id: int
    end_event_id: int
    event_count: int
    event_types: dict[str, int]
    sources: dict[str, int]
    first_checksum: str
    last_checksum: str
    digest: str
    schema: str = NODE_SCHEMA

    @property
    def address(self) -> str:
        return f"L{self.level}:{self.start_event_id}"

    @property
    def size(self) -> int:
        return self.end_event_id - self.start_event_id

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "address": self.address,
            "level": self.level,
            "start_event_id": self.start_event_id,
            "end_event_id": self.end_event_id,
            "event_count": self.event_count,
            "event_types": dict(self.event_types),
            "sources": dict(self.sources),
            "first_checksum": self.first_checksum,
            "last_checksum": self.last_checksum,
            "digest": self.digest,
        }

    @classmethod
    def from_dict(cls, row: Mapping[str, Any]) -> "TreeNode":
        return cls(
            level=int(row["level"]),
            start_event_id=int(row["start_event_id"]),
            end_event_id=int(row["end_event_id"]),
            event_count=int(row["event_count"]),
            event_types={str(k): int(v) for k, v in dict(row["event_types"]).items()},
            sources={str(k): int(v) for k, v in dict(row["sources"]).items()},
            first_checksum=str(row["first_checksum"]),
            last_checksum=str(row["last_checksum"]),
            digest=str(row["digest"]),
        )


def _canonical(value: Any) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")


def _digest(value: Any) -> str:
    return hashlib.sha256(_canonical(value)).hexdigest()


def _estimate_tokens(value: Any) -> int:
    return max(1, math.ceil(len(_canonical(value)) / 4))


def _dyadic_cover(end: int) -> list[tuple[int, int]]:
    """Canonical aligned power-of-two cover of [0,end)."""
    if end < 0:
        raise ValueError("end must be nonnegative")
    out: list[tuple[int, int]] = []
    pos = 0
    while pos < end:
        remaining = end - pos
        size = 1 << (remaining.bit_length() - 1)
        while size > 1 and pos % size:
            size //= 2
        out.append((pos, size))
        pos += size
    return out


class OptMemTree:
    def __init__(
        self,
        event_log: EventLog,
        *,
        raw_tail_events: int = 8,
        tree_dir: Path | None = None,
    ):
        if raw_tail_events < 0:
            raise ValueError("raw_tail_events must be nonnegative")
        self.event_log = event_log
        self.raw_tail_events = int(raw_tail_events)
        self.tree_dir = Path(tree_dir) if tree_dir is not None else event_log.root / "TREE"
        self.generations_dir = self.tree_dir / "generations"
        self.manifest_path = self.tree_dir / "manifest.json"
        self.lock_path = self.tree_dir / ".lock"

    @contextmanager
    def _locked(self, *, exclusive: bool):
        self.tree_dir.mkdir(parents=True, exist_ok=True)
        with self.lock_path.open("a+b") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH)
            yield

    def _generation_dir(self, manifest: Mapping[str, Any]) -> Path:
        generation = manifest.get("generation")
        if not isinstance(generation, str) or not re.fullmatch(r"gen-[a-z0-9_]+", generation):
            raise EventLogCorruption("invalid OptMem generation; run nap()")
        return self.generations_dir / generation

    @property
    def nodes_dir(self) -> Path:
        return self._generation_dir(self.manifest())

    def _node_path(self, level: int, start: int, *, nodes_dir: Path | None = None) -> Path:
        return (nodes_dir if nodes_dir is not None else self.nodes_dir) / f"L{level}-{start}.json"

    def _write_json_atomic(self, path: Path, value: Any) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
        tmp = Path(tmp_name)
        data = json.dumps(value, sort_keys=True, indent=2, ensure_ascii=False) + "\n"
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as out:
                out.write(data)
                out.flush()
                os.fsync(out.fileno())
            os.replace(tmp, path)
        finally:
            tmp.unlink(missing_ok=True)

    def _summarize_segment(
        self,
        events: Sequence[MemoryEvent],
        start: int,
        size: int,
    ) -> TreeNode:
        if size <= 0 or size & (size - 1) or start % size:
            raise ValueError("segment must be an aligned power of two")
        end = start + size
        if end > len(events):
            raise ValueError("segment exceeds event snapshot")
        segment = events[start:end]
        if not segment:
            raise ValueError("empty segment")
        event_types = dict(sorted(Counter(e.event_type for e in segment).items()))
        sources = dict(sorted(Counter(e.source for e in segment).items()))
        semantic = {
            "level": size.bit_length() - 1,
            "start": start,
            "end": end,
            "event_types": event_types,
            "sources": sources,
            "event_checksums": [e.checksum for e in segment],
        }
        return TreeNode(
            level=size.bit_length() - 1,
            start_event_id=start,
            end_event_id=end,
            event_count=size,
            event_types=event_types,
            sources=sources,
            first_checksum=segment[0].checksum,
            last_checksum=segment[-1].checksum,
            digest=_digest(semantic),
        )

    def nap(self) -> dict[str, Any]:
        """Publish a complete coarse cover without modifying canonical events."""
        with self._locked(exclusive=True):
            return self._nap_locked()

    def _nap_locked(self) -> dict[str, Any]:
        events = list(self.event_log.iter_events())
        n = len(events)
        coarse_end = max(0, n - self.raw_tail_events)
        segments = _dyadic_cover(coarse_end)
        self.generations_dir.mkdir(parents=True, exist_ok=True)
        generation_dir = Path(tempfile.mkdtemp(prefix="gen-", dir=self.generations_dir))
        published = False
        try:
            nodes = []
            for start, size in segments:
                node = self._summarize_segment(events, start, size)
                nodes.append(node)
                self._write_json_atomic(
                    self._node_path(node.level, start, nodes_dir=generation_dir), node.to_dict()
                )
            manifest = {
                "schema": SCHEMA,
                "generation": generation_dir.name,
                "event_count": n,
                "raw_tail_events": self.raw_tail_events,
                "coarse_end": coarse_end,
                "coarse_cover": [node.address for node in nodes],
                "coarse_node_count": len(nodes),
                "coarse_history_hash": _digest([node.digest for node in nodes]),
                "event_log_last_id": events[-1].event_id if events else None,
                "event_log_last_checksum": events[-1].checksum if events else None,
                "summary_mode": "deterministic_structural_baseline",
            }
            self._write_json_atomic(self.manifest_path, manifest)
            published = True
            # Readers hold a shared lock until they are done with their generation.
            for old in self.generations_dir.iterdir():
                if old != generation_dir and old.is_dir() and not old.is_symlink() and old.name.startswith("gen-"):
                    shutil.rmtree(old)
            legacy_nodes = self.tree_dir / "nodes"
            if legacy_nodes.is_dir() and not legacy_nodes.is_symlink():
                shutil.rmtree(legacy_nodes)
            return manifest
        finally:
            if not published:
                shutil.rmtree(generation_dir, ignore_errors=True)

    def _load_node(
        self, events: Sequence[MemoryEvent], nodes_dir: Path, start: int, size: int
    ) -> TreeNode:
        level = size.bit_length() - 1
        path = self._node_path(level, start, nodes_dir=nodes_dir)
        try:
            row = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise EventLogCorruption(f"invalid or missing OptMem node L{level}:{start}; run nap()") from exc
        expected = self._summarize_segment(events, start, size)
        if row != expected.to_dict():
            raise EventLogCorruption(f"OptMem node L{level}:{start} does not match canonical events")
        return expected

    def _read_manifest(self) -> dict[str, Any]:
        row = json.loads(self.manifest_path.read_text(encoding="utf-8"))
        if not isinstance(row, dict) or row.get("schema") != SCHEMA:
            raise EventLogCorruption("unsupported OptMem manifest; run nap()")
        self._generation_dir(row)
        return row

    def manifest(self) -> dict[str, Any]:
        with self._locked(exclusive=False):
            if self.manifest_path.is_file():
                return self._read_manifest()
        with self._locked(exclusive=True):
            if self.manifest_path.is_file():
                return self._read_manifest()
            return self._nap_locked()

    def rebuild(self) -> dict[str, Any]:
        """Recreate the TREE cache without removing the published generation first."""
        return self.nap()

    def _expand_segment(
        self,
        events: Sequence[MemoryEvent],
        start: int,
        size: int,
        sticky: set[int],
        roots: Mapping[tuple[int, int], TreeNode],
    ) -> list[dict[str, Any]]:
        has_sticky = any(start <= event_id < start + size for event_id in sticky)
        if not has_sticky:
            node = roots[(start, size)] if (start, size) in roots else self._summarize_segment(events, start, size)
            return [{"kind": "summary", "node": node.to_dict()}]
        if size == 1:
            event = events[start]
            return [{"kind": "raw", **self._resolved_event(event), "sticky": True}]
        half = size // 2
        return self._expand_segment(
            events, start, half, sticky, roots
        ) + self._expand_segment(
            events, start + half, half, sticky, roots
        )

    def _resolved_event(self, event: MemoryEvent) -> dict[str, Any]:
        return {"event": event.to_dict(), "resolved_payload": self.event_log.resolve_payload(event)}

    def cover(
        self,
        *,
        max_tokens: int = 4096,
        sticky_event_ids: Iterable[int] = (),
    ) -> dict[str, Any]:
        if max_tokens <= 0:
            raise ValueError("max_tokens must be positive")
        self.manifest()  # Preserve lazy initial construction without nested locks.
        with self._locked(exclusive=False):
            return self._cover_locked(self._read_manifest(), max_tokens, sticky_event_ids)

    def _cover_locked(
        self, manifest: Mapping[str, Any], max_tokens: int, sticky_event_ids: Iterable[int]
    ) -> dict[str, Any]:
        nap_count = manifest.get("event_count")
        old_end = manifest.get("coarse_end")
        tail_count = manifest.get("raw_tail_events")
        if (type(nap_count) is not int or nap_count < 0 or type(old_end) is not int
                or type(tail_count) is not int or tail_count < 0
                or old_end != max(0, nap_count - tail_count)):
            raise EventLogCorruption("invalid OptMem manifest range")
        events = list(self.event_log.iter_events())
        if len(events) < nap_count:
            raise RuntimeError("canonical event log shrank after TREE nap")
        last = events[nap_count - 1] if nap_count else None
        if (manifest.get("event_log_last_id") != (last.event_id if last else None)
                or manifest.get("event_log_last_checksum") != (last.checksum if last else None)):
            raise EventLogCorruption("OptMem manifest does not match canonical events")

        sticky = {int(x) for x in sticky_event_ids}
        if any(x < 0 or x >= len(events) for x in sticky):
            raise ValueError("sticky event id outside canonical event range")

        segments = _dyadic_cover(old_end)
        addresses = [f"L{size.bit_length() - 1}:{start}" for start, size in segments]
        if manifest.get("coarse_cover") != addresses or manifest.get("coarse_node_count") != len(segments):
            raise EventLogCorruption("invalid OptMem coarse cover")
        nodes_dir = self._generation_dir(manifest)
        roots = {(start, size): self._load_node(events, nodes_dir, start, size) for start, size in segments}
        if manifest.get("coarse_history_hash") != _digest([roots[segment].digest for segment in segments]):
            raise EventLogCorruption("OptMem revision does not match canonical events")
        items: list[dict[str, Any]] = []
        for start, size in segments:
            items.extend(self._expand_segment(events, start, size, sticky, roots))

        # Nap-time recent tail + post-nap appends are raw and exact.
        for event_id in range(old_end, len(events)):
            event = events[event_id]
            items.append(
                {
                    "kind": "raw",
                    **self._resolved_event(event),
                    "sticky": event_id in sticky,
                }
            )

        payload = {
            "schema": COVER_SCHEMA,
            "tree_revision": manifest["coarse_history_hash"],
            "nap_event_count": nap_count,
            "current_event_count": len(events),
            "raw_tail_start": old_end,
            "items": items,
        }
        tokens = _estimate_tokens(payload)
        if tokens > max_tokens:
            raw_items = [item for item in items if item["kind"] == "raw"]
            raw_tokens = _estimate_tokens(
                {
                    "schema": COVER_SCHEMA,
                    "tree_revision": manifest["coarse_history_hash"],
                    "items": raw_items,
                }
            )
            raise CoverBudgetExceeded(
                f"cover requires ~{tokens} tokens (raw tail alone ~{raw_tokens}); "
                f"budget is {max_tokens}"
            )
        payload["estimated_tokens"] = tokens
        payload["max_tokens"] = max_tokens
        return payload

    def zoom(self, start_event_id: int, end_event_id: int) -> dict[str, Any]:
        """Return [start,end) canonical events with large blobs resolved exactly."""
        if (
            type(start_event_id) is not int
            or type(end_event_id) is not int
            or start_event_id < 0
            or end_event_id < start_event_id
        ):
            raise ValueError("invalid zoom range")
        events = list(self.event_log.iter_events())
        if end_event_id > len(events):
            raise ValueError("zoom range exceeds canonical event log")
        rows = [self._resolved_event(event) for event in events[start_event_id:end_event_id]]
        return {
            "schema": ZOOM_SCHEMA,
            "start_event_id": start_event_id,
            "end_event_id": end_event_id,
            "source_event_ids": list(range(start_event_id, end_event_id)),
            "events": rows,
            "estimated_tokens": _estimate_tokens(rows),
        }
