"""Canonical private layout under ``~/.z0int``.

Repo = code. User state / corpora / specialists live only under HOME.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

SCHEMA = "z0int.paths.v1"


def home() -> Path:
    override = os.environ.get("Z0INT_HOME")
    if override:
        return Path(override).expanduser().resolve()
    return (Path.home() / ".z0int").resolve()


def ensure_layout(root: Path | None = None) -> dict[str, Path]:
    """Create the standard private tree; return named paths."""
    root = root or home()
    names = (
        "config",
        "state",
        "sources",
        "vault",
        "episodes",
        "stream",
        "replay",
        "models",
        "specialists",
        "benchmarks",
        "research",
        "receipts",
        "tokenomics",
        "logs",
        "shadow",
        "autoresearch",
        "runtime",
    )
    out: dict[str, Path] = {"root": root}
    root.mkdir(parents=True, exist_ok=True)
    for name in names:
        p = root / name
        p.mkdir(parents=True, exist_ok=True)
        out[name] = p
    return out


def onboard_state_path(root: Path | None = None) -> Path:
    return (root or home()) / "state" / "onboard.json"


def config_path(root: Path | None = None) -> Path:
    return (root or home()) / "config" / "z0int.json"


# --------------------------------------------------------------------------
# Evolution Lab location
#
# The lab is a RESEARCH dependency, not a runtime one. Before this resolver,
# seven modules each carried their own absolute path and three of them
# disagreed:
#
#   mushroom.py, train.py, convert.py, driver.py, pipeline.py, doctor.py,
#   bridge/runtime.py
#     -> /home/kvn/tmp/evolution-lab, /home/kvn/tmp/evolution-lab-agy,
#        /workspace/evolution-lab
#
# Onboard writes the canonical hint to ~/.z0int/config/env_hints.json, which
# names /workspace/evolution-lab. On this machine there are THREE real checkouts
# at three different revisions:
#
#     /workspace/evolution-lab          2c95b59   <- the configured hint
#     /home/kvn/tmp/evolution-lab       5cdb8e1   <- what mushroom.py hardcoded
#     /home/kvn/tmp/evolution-lab-agy   a2d2485
#
# So the problem is not a stale path -- it is that different production call
# sites silently bound to different revisions of the same research dependency.
# Nothing here can make that safe on its own; `evolution_lab_status()` reports
# every existing checkout and sets `ambiguous` so the situation is visible.
#
# Resolution is therefore existence-aware. A candidate that is not actually a
# lab checkout is skipped, and the source that won is reported so a stale hint
# is visible instead of silently winning.
# --------------------------------------------------------------------------

# Migration shims, deliberately in ONE place instead of seven. These are the
# paths production code used to hardcode; they are discovery candidates only,
# never authoritative. Delete once EVOLUTION_LAB_ROOT is configured everywhere.
LEGACY_EVOLUTION_LAB_ROOT = Path("/home/kvn/tmp/evolution-lab")
LEGACY_EVOLUTION_LAB_ROOTS = (
    (Path("/home/kvn/tmp/evolution-lab"), "legacy-default"),
    (Path("/home/kvn/tmp/evolution-lab-agy"), "legacy:evolution-lab-agy"),
    (Path("/workspace/evolution-lab"), "legacy:/workspace"),
)

EVOLUTION_LAB_ROOT_ALIASES = ("EVOLUTION_LAB_ROOT", "EVOLUTION_LAB_DIR")
EVOLUTION_LAB_PYTHON_ALIASES = ("EVOLUTION_LAB_PYTHON", "EVOLUTION_LAB_PY")


def env_hints() -> dict[str, Any]:
    """Onboard's exported hints, if any. Never raises."""
    try:
        payload = json.loads((home() / "config" / "env_hints.json").read_text())
    except (OSError, ValueError):
        return {}
    return payload if isinstance(payload, dict) else {}


def is_lab_checkout(path: Path) -> bool:
    """A directory is an Evolution Lab checkout only if it looks like one."""
    return (path / "evolution_lab").is_dir() and (path / "pyproject.toml").is_file()


def _candidate_paths(explicit: str | Path | None) -> list[tuple[Path, str]]:
    out: list[tuple[Path, str]] = []
    if explicit:
        out.append((Path(explicit).expanduser(), "explicit"))
    for name in EVOLUTION_LAB_ROOT_ALIASES:
        value = os.environ.get(name)
        if value:
            out.append((Path(value).expanduser(), f"env:{name}"))
    hints = env_hints()
    for name in EVOLUTION_LAB_ROOT_ALIASES:
        value = hints.get(name)
        if value:
            out.append((Path(str(value)).expanduser(), f"env_hints:{name}"))
    out.extend(LEGACY_EVOLUTION_LAB_ROOTS)
    return out


def evolution_lab_root(*, explicit: str | Path | None = None) -> Path | None:
    """The Evolution Lab checkout, or None when it is not installed here.

    Returning None is the point: production code must be able to say "the lab is
    absent" rather than falling back to one machine's path.
    """
    for raw, _source in _candidate_paths(explicit):
        try:
            path = raw.resolve()
        except OSError:
            continue
        if is_lab_checkout(path):
            return path
    return None


def evolution_lab_status(*, explicit: str | Path | None = None) -> dict[str, Any]:
    """Where the lab was looked for, which candidate won, and which were stale.

    Exists so a stale configured path is REPORTED rather than silently winning.
    """
    seen: list[dict[str, Any]] = []
    winner: tuple[Path, str] | None = None
    for raw, source in _candidate_paths(explicit):
        try:
            path = raw.resolve()
        except OSError:
            path = raw
        ok = is_lab_checkout(path)
        seen.append({"path": str(path), "source": source, "exists": ok})
        if ok and winner is None:
            winner = (path, source)
    # Deduplicate by resolved path, FIRST occurrence wins: the same checkout can
    # be named by both a configured hint and a legacy candidate, and the
    # highest-precedence source is the one worth reporting.
    uniq: dict[str, dict[str, Any]] = {}
    for cand in seen:
        if cand["exists"] and cand["path"] not in uniq:
            uniq[cand["path"]] = cand
    return {
        "schema": "z0int.evolution_lab.status.v1",
        "found": winner is not None,
        "path": str(winner[0]) if winner else None,
        "source": winner[1] if winner else None,
        "missing_candidates": [c for c in seen if not c["exists"]],
        "existing_checkouts": list(uniq.values()),
        "ambiguous": len(uniq) > 1,
        "candidates": seen,
        "note": (
            "The lab is a research dependency; production must not require it. "
            "When `ambiguous` is true there is more than one checkout on this "
            "machine, and `path` is the one the precedence order selected -- "
            "different call sites can otherwise bind to different revisions."
        ),
    }


def evolution_lab_artifact(relative: str) -> Path | None:
    """First existing copy of an artifact under any candidate lab checkout.

    Artifacts are resolved rather than assumed, so a call site does not silently
    bind to whichever checkout its own hardcoded path happened to name.
    """
    for raw, _source in _candidate_paths(None):
        try:
            base = raw.resolve()
        except OSError:
            continue
        candidate = base / relative
        if candidate.is_file():
            return candidate
    return None


def evolution_lab_python(*, explicit: str | Path | None = None, root: Path | None = None) -> str:
    """Interpreter for the lab. Falls back to the running interpreter."""
    if explicit:
        return str(explicit)
    for name in EVOLUTION_LAB_PYTHON_ALIASES:
        value = os.environ.get(name)
        if value:
            return value
    hints = env_hints()
    for name in EVOLUTION_LAB_PYTHON_ALIASES:
        value = hints.get(name)
        if value:
            return str(value)
    base = root or evolution_lab_root()
    if base is not None:
        for candidate in (base / ".venv" / "bin" / "python", base / "venv" / "bin" / "python"):
            if candidate.is_file():
                return str(candidate)
    return sys.executable
