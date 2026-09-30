"""OptMem-style temporal projection over the canonical z0 EventLog.

This is a structural P0 baseline, not a semantic summarizer. It proves the hard
memory invariants first:

- the EventLog stays canonical and immutable;
- TREE is a rebuildable cache;
- nodes use deterministic aligned power-of-two addresses;
- older history is represented by coarse nodes;
- the newest tail stays as raw canonical event records;
- sticky historical events force local expansion instead of global retiling;
- zoom() returns exact canonical event payloads with provenance.

A learned or LLM nap summarizer can replace the structural summary later
without changing addressing, provenance, cover, or zoom semantics.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import shutil
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping

from .event_log import EventLog, MemoryEvent

SCHEMA = "z0int.memory.optmem_tree.v1"
NODE_SCHEMA = "z0int.memory.optmem_node.v1"
COVER_SCHEMA = "z0int.memory.optmem_cover.v1"
ZOOM_SCHEMA = "z0int.memory.optmem_zoom.v1"


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


def _counter_sum(a: Mapping[str, int], b: Mapping[str, int]) -> dict[str, int]:
    out = Counter({str(k): int(v) for k, v in a.items()})
    out.update({str(k): int(v) for k, v in b.items()})
    return dict(sorted(out.items()))


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
        self.nodes_dir = self.tree_dir / "nodes"
        self.manifest_path = self.tree_dir / "manifest.json"

    def _node_path(self, level: int, start: int) -> Path:
        return self.nodes_dir / f"L{level}-{start}.json"

    def _write_json_atomic(self, path: Path, value: Any) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        data = json.dumps(value, sort_keys=True, indent=2, ensure_ascii=False) + "\n"
        with tmp.open("w", encoding="utf-8") as out:
            out.write(data)
            out.flush()
            os.fsync(out.fileno())
        os.replace(tmp, path)

    def _leaf(self, event: MemoryEvent) -> TreeNode:
        semantic = {
            "level": 0,
            "start": event.event_id,
            "end": event.event_id + 1,
            "event_types": {event.event_type: 1},
            "sources": {event.source: 1},
            "first_checksum": event.checksum,
            "last_checksum": event.checksum,
        }
        return TreeNode(
            level=0,
            start_event_id=event.event_id,
            end_event_id=event.event_id + 1,
            event_count=1,
            event_types={event.event_type: 1},
            sources={event.source: 1},
            first_checksum=event.checksum,
            last_checksum=event.checksum,
            digest=_digest(semantic),
        )

    def _parent(self, left: TreeNode, right: TreeNode) -> TreeNode:
        if left.level != right.level or left.end_event_id != right.start_event_id:
            raise ValueError("children are not adjacent peers")
        size = left.size + right.size
        if size & (size - 1):
            raise ValueError("parent size is not a power of two")
        level = left.level + 1
        start = left.start_event_id
        if start % size:
            raise ValueError("parent is not aligned")
        event_types = _counter_sum(left.event_types, right.event_types)
        sources = _counter_sum(left.sources, right.sources)
        semantic = {
            "level": level,
            "start": start,
            "end": right.end_event_id,
            "children": [left.digest, right.digest],
            "event_types": event_types,
            "sources": sources,
            "first_checksum": left.first_checksum,
            "last_checksum": right.last_checksum,
        }
        return TreeNode(
            level=level,
            start_event_id=start,
            end_event_id=right.end_event_id,
            event_count=left.event_count + right.event_count,
            event_types=event_types,
            sources=sources,
            first_checksum=left.first_checksum,
            last_checksum=right.last_checksum,
            digest=_digest(semantic),
        )

    def nap(self) -> dict[str, Any]:
        """Rebuild the projection for the currently committed event prefix."""
        events = list(self.event_log.iter_events())
        self.nodes_dir.mkdir(parents=True, exist_ok=True)

        nodes: dict[tuple[int, int], TreeNode] = {}
        for event in events:
            node = self._leaf(event)
            nodes[(0, event.event_id)] = node
            self._write_json_atomic(self._node_path(0, event.event_id), node.to_dict())

        level = 1
        size = 2
        n = len(events)
        while size <= n:
            half = size // 2
            for start in range(0, n - size + 1, size):
                left = nodes[(level - 1, start)]
                right = nodes[(level - 1, start + half)]
                parent = self._parent(left, right)
                nodes[(level, start)] = parent
                self._write_json_atomic(self._node_path(level, start), parent.to_dict())
            level += 1
            size *= 2

        coarse_end = max(0, n - self.raw_tail_events)
        coarse_segments = _dyadic_cover(coarse_end)
        coarse_nodes = [self._load_segment(nodes, start, size) for start, size in coarse_segments]
        coarse_hash = _digest([node.digest for node in coarse_nodes])
        manifest = {
            "schema": SCHEMA,
            "event_count": n,
            "raw_tail_events": self.raw_tail_events,
            "coarse_end": coarse_end,
            "coarse_cover": [node.address for node in coarse_nodes],
            "coarse_history_hash": coarse_hash,
            "event_log_last_id": events[-1].event_id if events else None,
            "event_log_last_checksum": events[-1].checksum if events else None,
            "summary_mode": "deterministic_structural_baseline",
        }
        self._write_json_atomic(self.manifest_path, manifest)
        return manifest

    def _load_segment(
        self,
        cache: Mapping[tuple[int, int], TreeNode] | None,
        start: int,
        size: int,
    ) -> TreeNode:
        if size <= 0 or size & (size - 1) or start % size:
            raise ValueError("segment must be aligned power of two")
        level = size.bit_length() - 1
        if cache is not None and (level, start) in cache:
            return cache[(level, start)]
        path = self._node_path(level, start)
        if not path.is_file():
            raise FileNotFoundError(f"missing OptMem node {level}:{start}; run nap()")
        return TreeNode.from_dict(json.loads(path.read_text(encoding="utf-8")))

    def manifest(self) -> dict[str, Any]:
        if not self.manifest_path.is_file():
            return self.nap()
        row = json.loads(self.manifest_path.read_text(encoding="utf-8"))
        if row.get("schema") != SCHEMA:
            raise ValueError("unsupported OptMem manifest")
        return row

    def rebuild(self) -> dict[str, Any]:
        """Delete only TREE cache and recreate it from canonical events."""
        if self.tree_dir.exists():
            shutil.rmtree(self.tree_dir)
        return self.nap()

    def _expand_segment(
        self,
        start: int,
        size: int,
        sticky: set[int],
    ) -> list[dict[str, Any]]:
        contained = [event_id for event_id in sticky if start <= event_id < start + size]
        if not contained:
            return [{"kind": "summary", "node": self._load_segment(None, start, size).to_dict()}]
        if size == 1:
            event = self.event_log.get(start, resolve_blob=True)
            return [{"kind": "raw", "event": event.to_dict(), "sticky": True}]
        half = size // 2
        return self._expand_segment(start, half, sticky) + self._expand_segment(
            start + half, half, sticky
        )

    def cover(
        self,
        *,
        max_tokens: int = 4096,
        sticky_event_ids: Iterable[int] = (),
    ) -> dict[str, Any]:
        if max_tokens <= 0:
            raise ValueError("max_tokens must be positive")
        manifest = self.manifest()
        nap_count = int(manifest["event_count"])
        events = list(self.event_log.iter_events())
        if len(events) < nap_count:
            raise RuntimeError("canonical event log shrank after TREE nap")

        sticky = {int(x) for x in sticky_event_ids}
        if any(x < 0 or x >= len(events) for x in sticky):
            raise ValueError("sticky event id outside canonical event range")

        old_end = max(0, nap_count - self.raw_tail_events)
        items: list[dict[str, Any]] = []
        for start, size in _dyadic_cover(old_end):
            items.extend(self._expand_segment(start, size, sticky))

        # The nap-time recent tail and every event appended since the nap stay raw.
        for event_id in range(old_end, len(events)):
            event = self.event_log.get(event_id, resolve_blob=True)
            items.append(
                {
                    "kind": "raw",
                    "event": event.to_dict(),
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
        rows = [
            self.event_log.get(event_id, resolve_blob=True).to_dict()
            for event_id in range(start_event_id, end_event_id)
        ]
        return {
            "schema": ZOOM_SCHEMA,
            "start_event_id": start_event_id,
            "end_event_id": end_event_id,
            "source_event_ids": list(range(start_event_id, end_event_id)),
            "events": rows,
            "estimated_tokens": _estimate_tokens(rows),
        }
