"""Resident FFF-backed repository search.

FFF is a read-only discovery accelerator. It never grants authority: callers
receive normalized index hits and must carry them as provenance-bearing
evidence through ContextPacket/EventLog before using them for decisions.

The process-local pool holds bounded, watcher-free indexes for admitted Git
toplevels. A serialized lazy rebuild after ten minutes bounds freshness without
recursive inotify churn from build/cache directories or unrelated worktrees.
"""

from __future__ import annotations

import atexit
import importlib
import logging
import os
import subprocess
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

SCHEMA = "z0int.file_search.v1"
_DEFAULT_LIMIT = 8
_DEFAULT_BUDGET_MS = 120
_DEFAULT_SCAN_TIMEOUT_MS = 5000
_DEFAULT_MAX_ROOTS = 4
_DEFAULT_MAX_FILES = 200_000
_LOG = logging.getLogger(__name__)
_DEFAULT_INDEX_TTL_SECONDS = 600


def _admit_root(root: Path) -> int:
    """Fail before constructing an index or registering any filesystem watch."""
    forbidden = {Path("/mnt/zer0models"), Path("/mnt/zer0models/workspace/zer0")}
    if root in forbidden or root == Path(root.anchor):
        raise ValueError(f"forbidden workspace root: {root}")
    result = subprocess.run(
        ["git", "-C", str(root), "rev-parse", "--show-toplevel"],
        capture_output=True, text=True, timeout=5, check=True,
        env={k: v for k, v in os.environ.items() if not k.startswith("GIT_")},
    )
    if Path(result.stdout.strip()).resolve() != root:
        raise ValueError(f"root is not a git toplevel: {root}")
    ceiling = _int_env("Z0INT_FFF_MAX_FILES", _DEFAULT_MAX_FILES, 1)
    count = 0
    def failed(exc: OSError) -> None:
        raise exc
    for directory, dirs, files in os.walk(root, followlinks=False, onerror=failed):
        here = Path(directory)
        dirs[:] = [
            name for name in dirs
            if name != ".git" and not (here / name).is_symlink()
        ]
        count += len(files)
        if count > ceiling:
            raise ValueError(f"file count exceeds Z0INT_FFF_MAX_FILES={ceiling}: {root}")
    return count


def _load_fff() -> Any | None:
    try:
        return importlib.import_module("fff")
    except (ImportError, OSError):
        return None


def _flag(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _int_env(name: str, default: int, minimum: int = 0) -> int:
    try:
        return max(minimum, int(os.environ.get(name, str(default))))
    except ValueError:
        return default


@dataclass
class _ResidentFinder:
    root: Path
    finder: Any
    package_version: str
    ready: bool
    created_at: float = field(default_factory=time.monotonic)
    last_used: float = field(default_factory=time.time)
    epoch: int = field(default_factory=time.monotonic_ns)
    lock: threading.RLock = field(default_factory=threading.RLock)

    def close(self) -> None:
        with self.lock:
            try:
                self.finder.close()
            except Exception:
                pass


_POOL: "OrderedDict[str, _ResidentFinder]" = OrderedDict()
_POOL_LOCK = threading.RLock()


def _pool_limit() -> int:
    return _int_env("Z0INT_FFF_MAX_ROOTS", _DEFAULT_MAX_ROOTS, 1)


def _evict_if_needed() -> None:
    while len(_POOL) > _pool_limit():
        _key, resident = _POOL.popitem(last=False)
        resident.close()


def _resident(root: Path) -> tuple[_ResidentFinder | None, str, bool, str | None]:
    if _flag("Z0INT_FFF_DISABLE"):
        return None, "disabled", False, None

    key = str(root)
    with _POOL_LOCK:
        existing = _POOL.get(key)
        if existing is not None:
            ttl = _int_env("Z0INT_FFF_INDEX_TTL_SECONDS", _DEFAULT_INDEX_TTL_SECONDS, 1)
            if time.monotonic() - existing.created_at < ttl:
                existing.last_used = time.time()
                _POOL.move_to_end(key)
                status = "ready" if existing.ready else "warming"
                return existing, status, True, None
            _POOL.pop(key)
            existing.close()

        result: list[tuple[_ResidentFinder | None, str, bool, str | None]] = []

        def rebuild() -> None:
            # A short-lived dedicated thread lets descendants inherit nice/idle
            # IO without lowering (or trying to re-raise) the HTTP thread.
            try:
                os.setpriority(os.PRIO_PROCESS, threading.get_native_id(), 10)
                subprocess.run(
                    ["ionice", "-c", "3", "-p", str(threading.get_native_id())],
                    check=True, capture_output=True, timeout=5,
                )
                result.append(_build_resident(root))
            except Exception as exc:
                result.append((None, "error", False, f"{type(exc).__name__}: {exc}"))

        worker = threading.Thread(target=rebuild, name="fff-index-build")
        worker.start()
        worker.join()
        resident, status, reused, error = result[0]
        if resident is not None:
            _POOL[key] = resident
            _evict_if_needed()
        return resident, status, reused, error


def _build_resident(root: Path) -> tuple[_ResidentFinder | None, str, bool, str | None]:

    try:
        count = _admit_root(root)
    except (ValueError, OSError, subprocess.SubprocessError) as exc:
        error = f"FFF root rejected: {root}: {exc}"
        _LOG.warning("%s", error)
        return None, "rejected_root", False, error
    mod = _load_fff()
    if mod is None:
        return None, "absent", False, None
    finder = None
    try:
        _LOG.info("FFF admitting root=%s files=%d watch=False", root, count)
        finder = mod.FileFinder(
            root, watch=False, ai_mode=True, enable_content_indexing=True,
            follow_symlinks=False,
        )
        ready = bool(finder.wait_for_scan_blocking(timeout_ms=_int_env(
            "Z0INT_FFF_SCAN_TIMEOUT_MS", _DEFAULT_SCAN_TIMEOUT_MS, 1,
        )))
        if not ready:
            # Do not leave a background scan running after releasing the
            # global rebuild lock: another root could otherwise overlap it.
            finder.close()
            return None, "scan_timeout", False, "FFF initial scan timed out"
        resident = _ResidentFinder(
            root=root, finder=finder,
            package_version=str(getattr(mod, "__version__", "unknown")), ready=ready,
        )
        return resident, ("ready" if ready else "warming"), False, None
    except Exception as exc:
        if finder is not None:
            finder.close()
        return None, "error", False, f"{type(exc).__name__}: {exc}"


def _score_dict(score: Any) -> dict[str, Any]:
    return {
        "total": int(getattr(score, "total", 0) or 0),
        "exact_match": bool(getattr(score, "exact_match", False)),
        "match_type": str(getattr(score, "match_type", "") or ""),
    }


def _path_hits(result: Any, limit: int) -> list[dict[str, Any]]:
    items = list(getattr(result, "items", []) or [])
    scores = list(getattr(result, "scores", []) or [])
    out: list[dict[str, Any]] = []
    for i, item in enumerate(items[:limit]):
        score = scores[i] if i < len(scores) else None
        out.append(
            {
                "path": str(getattr(item, "relative_path", "") or ""),
                "file_name": str(getattr(item, "file_name", "") or ""),
                "git_status": str(getattr(item, "git_status", "") or ""),
                "size": int(getattr(item, "size", 0) or 0),
                "modified": int(getattr(item, "modified", 0) or 0),
                "score": _score_dict(score) if score is not None else None,
            }
        )
    return [hit for hit in out if hit["path"]]


def _content_hits(result: Any, limit: int) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for item in list(getattr(result, "items", []) or [])[:limit]:
        path = str(getattr(item, "relative_path", "") or "")
        if not path:
            continue
        out.append(
            {
                "path": path,
                "file_name": str(getattr(item, "file_name", "") or ""),
                "git_status": str(getattr(item, "git_status", "") or ""),
                "line": int(getattr(item, "line_number", 0) or 0),
                "col": int(getattr(item, "col", 0) or 0),
                "text": str(getattr(item, "line_content", "") or ""),
                "context_before": [str(x) for x in (getattr(item, "context_before", []) or [])],
                "context_after": [str(x) for x in (getattr(item, "context_after", []) or [])],
                "is_definition": bool(getattr(item, "is_definition", False)),
                "fuzzy_score": getattr(item, "fuzzy_score", None),
            }
        )
    return out


def search_repository(
    project_root: str | Path,
    query: str,
    *,
    kind: str = "natural_language",
    limit: int = _DEFAULT_LIMIT,
    time_budget_ms: int = _DEFAULT_BUDGET_MS,
) -> dict[str, Any]:
    """Search a repository through a process-resident FFF index.

    Returns normalized bounded results. Failures are data, not exceptions, so a
    caller can fall through to QMD/FTS5 or report a missing evidence layer.
    """
    t0 = time.perf_counter()
    q = str(query or "").strip()
    if not q:
        return {
            "schema": SCHEMA,
            "status": "error",
            "error": "empty query",
            "path_hits": [],
            "content_hits": [],
            "wall_ms": (time.perf_counter() - t0) * 1000.0,
        }

    try:
        root = Path(project_root).expanduser().resolve()
    except OSError as exc:
        return {
            "schema": SCHEMA,
            "status": "error",
            "error": str(exc),
            "path_hits": [],
            "content_hits": [],
            "wall_ms": (time.perf_counter() - t0) * 1000.0,
        }
    if not root.is_dir():
        return {
            "schema": SCHEMA,
            "status": "missing_root",
            "root": str(root),
            "path_hits": [],
            "content_hits": [],
            "wall_ms": (time.perf_counter() - t0) * 1000.0,
        }

    bounded_limit = max(1, min(int(limit), 50))
    bounded_budget = max(10, min(int(time_budget_ms), 5000))
    resident, status, reused, error = _resident(root)
    if resident is None:
        return {
            "schema": SCHEMA,
            "status": status,
            "root": str(root),
            "error": error,
            "path_hits": [],
            "content_hits": [],
            "wall_ms": (time.perf_counter() - t0) * 1000.0,
        }

    with resident.lock:
        resident.last_used = time.time()
        try:
            path_result = resident.finder.search(q, page_size=bounded_limit)
            mode = "plain"
            content_result = resident.finder.grep(
                q,
                mode=mode,
                smart_case=True,
                max_matches_per_file=3,
                page_limit=bounded_limit,
                time_budget_ms=bounded_budget,
                enforce_time_budget=True,
                before_context=1,
                after_context=1,
                classify_definitions=True,
            )
            content_hits = _content_hits(content_result, bounded_limit)
            # Natural-language/code discovery benefits from FFF's fuzzy grep if
            # the literal pass misses. Exact-symbol requests stay literal.
            if (
                not content_hits
                and kind == "natural_language"
                and len(q) <= 120
            ):
                mode = "fuzzy"
                content_result = resident.finder.grep(
                    q,
                    mode="fuzzy",
                    smart_case=True,
                    max_matches_per_file=3,
                    page_limit=bounded_limit,
                    time_budget_ms=bounded_budget,
                    enforce_time_budget=True,
                    before_context=1,
                    after_context=1,
                    classify_definitions=True,
                )
                content_hits = _content_hits(content_result, bounded_limit)
            path_hits = _path_hits(path_result, bounded_limit)
        except Exception as exc:
            return {
                "schema": SCHEMA,
                "status": "error",
                "root": str(root),
                "error": f"{type(exc).__name__}: {exc}",
                "path_hits": [],
                "content_hits": [],
                "package_version": resident.package_version,
                "index_epoch": resident.epoch,
                "reused": reused,
                "wall_ms": (time.perf_counter() - t0) * 1000.0,
            }

        progress = getattr(resident.finder, "scan_progress", None)
        scanning = bool(getattr(progress, "is_scanning", False)) if progress is not None else not resident.ready
        if not scanning:
            resident.ready = True
            status = "ready"

        return {
            "schema": SCHEMA,
            "status": status,
            "root": str(root),
            "query": q,
            "kind": kind,
            "grep_mode": mode,
            "package_version": resident.package_version,
            "index_epoch": resident.epoch,
            "reused": reused,
            "scanning": scanning,
            "path_hits": path_hits,
            "content_hits": content_hits,
            "wall_ms": (time.perf_counter() - t0) * 1000.0,
        }


def close_all() -> None:
    with _POOL_LOCK:
        residents = list(_POOL.values())
        _POOL.clear()
    for resident in residents:
        resident.close()


def _reset_for_tests() -> None:
    close_all()


atexit.register(close_all)
