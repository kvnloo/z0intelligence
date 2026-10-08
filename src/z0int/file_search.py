"""Resident FFF-backed repository search.

FFF is a read-only discovery accelerator. It never grants authority: callers
receive normalized index hits and must carry them as provenance-bearing
evidence through ContextPacket/EventLog before using them for decisions.

The pool is process-local by design. OMP's resident bridge worker therefore
keeps one Rust index + watcher warm across repeated turns; Hermes or another
long-lived harness gets the same behavior when it imports this module.
"""

from __future__ import annotations

import atexit
import importlib
import os
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
    generation_id: int = field(default_factory=time.time_ns)
    created_at: float = field(default_factory=time.time)
    last_used: float = field(default_factory=time.time)
    epoch: int = 0
    epoch_error: str | None = None
    subscription: Any | None = None
    lock: threading.RLock = field(default_factory=threading.RLock)

    def on_change(self, _events: Any) -> None:
        # The watcher already updates FFF's index. This counter is only a cheap
        # invalidation/source-epoch signal for ContextPacket provenance.
        with self.lock:
            self.epoch += 1

    def close(self) -> None:
        with self.lock:
            sub = self.subscription
            self.subscription = None
            if sub is not None:
                try:
                    sub.unsubscribe()
                except Exception:
                    pass
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
            existing.last_used = time.time()
            _POOL.move_to_end(key)
            with existing.lock:
                if existing.subscription is None:
                    try:
                        existing.subscription = existing.finder.watch(None, existing.on_change)
                        existing.epoch_error = None if existing.subscription is not None else "watch returned no subscription handle"
                    except Exception as exc:
                        existing.epoch_error = f"{type(exc).__name__}: {exc}"
            status = "ready" if existing.ready else "warming"
            return existing, status, True, None

        mod = _load_fff()
        if mod is None:
            return None, "absent", False, None

        finder = None
        resident: _ResidentFinder | None = None
        try:
            finder = mod.FileFinder(
                root,
                watch=True,
                ai_mode=True,
                enable_content_indexing=True,
                follow_symlinks=False,
            )
            ready = bool(
                finder.wait_for_scan_blocking(
                    timeout_ms=_int_env(
                        "Z0INT_FFF_SCAN_TIMEOUT_MS",
                        _DEFAULT_SCAN_TIMEOUT_MS,
                        1,
                    )
                )
            )
            resident = _ResidentFinder(
                root=root,
                finder=finder,
                package_version=str(getattr(mod, "__version__", "unknown")),
                ready=ready,
            )
            try:
                resident.subscription = finder.watch(None, resident.on_change)
                if resident.subscription is None:
                    resident.epoch_error = "watch returned no subscription handle"
            except Exception as exc:
                # watch=True still lets FFF maintain its own index. The
                # subscription is needed to track our source epoch, so queries
                # remain partial if it cannot be registered.
                resident.subscription = None
                resident.epoch_error = f"{type(exc).__name__}: {exc}"
            _POOL[key] = resident
            _evict_if_needed()
            return resident, ("ready" if ready else "warming"), False, None
        except Exception as exc:
            if resident is not None:
                resident.close()
            elif finder is not None:
                try:
                    finder.close()
                except Exception:
                    pass
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


def _result_coverage(
    *,
    status: str,
    warmup_complete: bool,
    path_result: Any,
    content_result: Any,
    generation_error: str | None = None,
) -> tuple[str, dict[str, Any], str | None]:
    path_total = int(getattr(path_result, "total_matched", 0) or 0)
    content_total = int(getattr(content_result, "total_matched", 0) or 0)
    searched_value = getattr(content_result, "total_files_searched", None)
    total_files_value = getattr(content_result, "total_files", None)
    files_searched = int(searched_value or 0)
    total_files = int(total_files_value or 0)
    # FFF 0.11 defines this as the number of files eligible after filtering,
    # not the count excluded from the query.
    eligible_value = getattr(content_result, "filtered_file_count", None)
    eligible_files = total_files if eligible_value is None else int(eligible_value or 0)
    scan_scope_known = searched_value is not None and (
        eligible_value is not None or total_files_value is not None
    )
    has_more = bool(getattr(content_result, "has_more", False))
    next_file_offset = int(getattr(content_result, "next_file_offset", 0) or 0)
    regex_error = getattr(content_result, "regex_fallback_error", None)
    partial_reasons: list[str] = []
    if status != "ready" or not warmup_complete:
        partial_reasons.append("index_warming")
    if has_more:
        partial_reasons.append("content_results_have_more")
    if path_total > len(getattr(path_result, "items", []) or []):
        partial_reasons.append("path_results_truncated")
    if content_total > len(getattr(content_result, "items", []) or []):
        partial_reasons.append("content_results_truncated")
    if not scan_scope_known:
        partial_reasons.append("content_scan_counts_unavailable")
    if eligible_files > files_searched:
        partial_reasons.append("content_scan_incomplete")
    if next_file_offset:
        partial_reasons.append("content_scan_has_next_page")
    if regex_error:
        partial_reasons.append("regex_fallback_error")
    if generation_error:
        partial_reasons.append("generation_unreliable")
    coverage = "partial" if partial_reasons else "complete"
    errors = [str(value) for value in (regex_error, generation_error) if value]
    error = "; ".join(errors) if errors else None
    detail = {
        "path_total_matched": path_total,
        "content_total_matched": content_total,
        "files_searched": files_searched,
        "total_files": total_files,
        "eligible_files": eligible_files,
        "has_more": has_more,
        "next_file_offset": next_file_offset,
        "regex_fallback_error": str(regex_error) if regex_error else None,
        "generation_error": generation_error,
        "partial_reasons": partial_reasons,
    }
    return coverage, detail, error


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
            "coverage": "unavailable",
            "scanning": False,
            "watcher_ready": False,
            "warmup_complete": False,
            "generation": None,
            "generation_status": "unavailable",
            "generation_reliable": False,
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
            "coverage": "unavailable",
            "scanning": False,
            "watcher_ready": False,
            "warmup_complete": False,
            "generation": None,
            "generation_status": "unavailable",
            "generation_reliable": False,
            "error": str(exc),
            "path_hits": [],
            "content_hits": [],
            "wall_ms": (time.perf_counter() - t0) * 1000.0,
        }
    if not root.is_dir():
        return {
            "schema": SCHEMA,
            "status": "missing_root",
            "coverage": "unavailable",
            "scanning": False,
            "watcher_ready": False,
            "warmup_complete": False,
            "generation": None,
            "generation_status": "unavailable",
            "generation_reliable": False,
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
            "coverage": "unavailable",
            "scanning": False,
            "watcher_ready": False,
            "warmup_complete": False,
            "generation": None,
            "generation_status": "unavailable",
            "generation_reliable": False,
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
            generation = (
                f"{resident.package_version}:resident={resident.generation_id}:epoch={resident.epoch}"
            )
            return {
                "schema": SCHEMA,
                "status": "error",
                "coverage": "unavailable",
                "scanning": bool(getattr(getattr(resident.finder, "scan_progress", None), "is_scanning", False)),
                "watcher_ready": bool(getattr(getattr(resident.finder, "scan_progress", None), "is_watcher_ready", False)),
                "warmup_complete": bool(getattr(getattr(resident.finder, "scan_progress", None), "is_warmup_complete", False)),
                "generation": generation,
                "generation_status": "unreliable" if resident.epoch_error else "tracked",
                "generation_reliable": resident.epoch_error is None,
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
        watcher_ready = bool(getattr(progress, "is_watcher_ready", not scanning)) if progress is not None else not scanning
        warmup_complete = bool(getattr(progress, "is_warmup_complete", resident.ready)) if progress is not None else resident.ready
        if not scanning and warmup_complete:
            resident.ready = True
            status = "ready"
        else:
            resident.ready = False
            status = "warming"

        coverage, content_scan, error = _result_coverage(
            status=status,
            warmup_complete=warmup_complete and watcher_ready,
            path_result=path_result,
            content_result=content_result,
            generation_error=resident.epoch_error,
        )
        generation = (
            f"{resident.package_version}:resident={resident.generation_id}:epoch={resident.epoch}"
        )

        return {
            "schema": SCHEMA,
            "status": status,
            "coverage": coverage,
            "root": str(root),
            "query": q,
            "kind": kind,
            "grep_mode": mode,
            "package_version": resident.package_version,
            "index_epoch": resident.epoch,
            "generation": generation,
            "generation_status": "unreliable" if resident.epoch_error else "tracked",
            "generation_reliable": resident.epoch_error is None,
            "reused": reused,
            "scanning": scanning,
            "watcher_ready": watcher_ready,
            "warmup_complete": warmup_complete,
            "error": error,
            "content_scan": content_scan,
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
