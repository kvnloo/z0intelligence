"""Real retrieval executor for historical replay.

Simulation in ``replay._simulate_context_cost`` stays synthetic. This module
runs installed tools, times them, and refuses promotion credit for anything
that did not execute. Unavailable backends stay unavailable: no mocks.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import time
from pathlib import Path
from typing import Any, Callable

from z0int.context_resolve import RECIPE_SCHEMA, ResolutionRecipe

SCHEMA = "z0int.retrieval_replay.v1"
VERIFIER = "retrieval_exec.evidence.v1"

# Installed interfaces only. Do not invent flags.
CTX_SEARCH = ("search", "--refresh", "off", "--backend", "lexical", "--format", "json", "--quiet")
CTX_SHOW = ("show", "event", "--format", "json", "--quiet", "--before", "0", "--after", "0")


def promotion_allowed(row: dict[str, Any]) -> bool:
    """Simulation, mocks, and unavailable arms never earn promotion credit."""
    if row.get("measurement_source") != "executed":
        return False
    if row.get("promotion_credit") is not True:
        return False
    if row.get("verified_success") is not True:
        return False
    if row.get("unavailable"):
        return False
    return True


def backend_health() -> dict[str, Any]:
    """Classify installed backends. A tunnel that forwards off-host is unavailable."""
    ctx = _which("ctx")
    qmd = _which("qmd")
    agentsview = _which("agentsview")
    tunnel = Path.home() / ".config/systemd/user/tencentdb-memory-tunnel.service"
    forwards = False
    if tunnel.is_file():
        text = tunnel.read_text(encoding="utf-8", errors="replace")
        forwards = "100.113.138.100" in text or "ssh -N" in text
    return {
        "schema": "z0int.retrieval_backend_health.v1",
        "ctx_lexical": "available" if ctx else "source_absent",
        "ctx_path": ctx,
        "qmd": "source_absent" if not qmd else "available",
        "agentsview": "source_absent" if not agentsview else "available",
        "optmem": "not_indexed",
        "tencentdb": "unavailable_forwards_off_host" if forwards else "source_absent",
        "semantic": "not_permitted",
    }


def _which(name: str) -> str | None:
    for part in os.environ.get("PATH", "").split(":"):
        cand = Path(part) / name
        if cand.is_file() and os.access(cand, os.X_OK):
            return str(cand)
    return None


def _run(argv: list[str], *, timeout: float = 30.0) -> dict[str, Any]:
    t0 = time.perf_counter()
    proc = subprocess.run(argv, capture_output=True, text=True, timeout=timeout, check=False)
    wall_ms = (time.perf_counter() - t0) * 1000.0
    return {
        "argv0": argv[0],
        "returncode": proc.returncode,
        "stdout": proc.stdout,
        "stderr": proc.stderr,
        "wall_ms": wall_ms,
    }


def ctx_lexical(query: str, *, limit: int = 5, ctx_bin: str = "ctx") -> dict[str, Any]:
    """Read-only lexical search. ``--refresh off`` does not rebuild the index."""
    raw = _run([ctx_bin, *CTX_SEARCH, "--limit", str(limit), query])
    parsed = _parse_json(raw["stdout"]) if raw["returncode"] == 0 else None
    ids = _event_ids(parsed)
    return {
        "op": "ctx_lexical",
        "ok": raw["returncode"] == 0 and bool(ids),
        "wall_ms": raw["wall_ms"],
        "event_ids": ids,
        "generation_id": _nested(parsed, "retrieval", "generation_id"),
        "freshness": (parsed or {}).get("freshness") if isinstance(parsed, dict) else None,
        "error": None if raw["returncode"] == 0 else (raw["stderr"] or "ctx_search_failed")[:240],
        "bytes_read": len(raw["stdout"].encode()),
    }


def ctx_show_event(event_id: str, *, ctx_bin: str = "ctx") -> dict[str, Any]:
    """Exact hydration. Valid only when the decision point already has the locator."""
    raw = _run([ctx_bin, *CTX_SHOW, event_id])
    parsed = _parse_json(raw["stdout"]) if raw["returncode"] == 0 else None
    got = _show_event_id(parsed)
    return {
        "op": "ctx_show_event",
        "ok": raw["returncode"] == 0 and got == event_id,
        "wall_ms": raw["wall_ms"],
        "event_ids": [got] if got else [],
        "error": None if raw["returncode"] == 0 else (raw["stderr"] or "ctx_show_failed")[:240],
        "bytes_read": len(raw["stdout"].encode()),
    }


def unavailable_arm(name: str, reason: str) -> dict[str, Any]:
    return {
        "op": name,
        "ok": False,
        "unavailable": True,
        "reason": reason,
        "wall_ms": 0.0,
        "event_ids": [],
        "measurement_source": "unavailable",
        "promotion_credit": False,
    }


def verify_evidence(found: list[str], required: list[str]) -> dict[str, Any]:
    """Source-id coverage. The old assistant answer is not ground truth."""
    need = [x for x in required if x]
    have = set(found)
    missing = [x for x in need if x not in have]
    return {
        "verifier": VERIFIER,
        "verified_success": bool(need) and not missing,
        "missing": missing,
        "found": list(found),
        "abstained": not found,
    }


def recipe_for(ops: tuple[dict[str, Any], ...], *, capability_id: str, epochs: dict[str, str]) -> ResolutionRecipe:
    return ResolutionRecipe(
        capability_id=capability_id,
        request_signature=hashlib.sha256(json.dumps(list(ops), sort_keys=True).encode()).hexdigest()[:16],
        scope_fingerprint="ctx-local",
        policy_revision="retrieval_exec.v1",
        source_epochs=epochs,
        operations=ops,
        required_evidence_fields=("event_id",),
        verifier_revision=VERIFIER,
        hardware_profile="local",
    )


def execute_recipe(
    recipe: ResolutionRecipe,
    *,
    query: str,
    locator: str | None,
    ctx_bin: str = "ctx",
) -> dict[str, Any]:
    """Run one recipe. Does not consult gold ids. Locator is decision-point state."""
    found: list[str] = []
    steps: list[dict[str, Any]] = []
    t0 = time.perf_counter()
    for op in recipe.operations:
        kind = op.get("op")
        if kind == "ctx_lexical":
            step = ctx_lexical(query, limit=int(op.get("limit") or 5), ctx_bin=ctx_bin)
        elif kind == "ctx_show_event":
            if not locator:
                step = unavailable_arm("ctx_show_event", "locator_absent_at_decision_point")
            else:
                step = ctx_show_event(locator, ctx_bin=ctx_bin)
        elif kind in ("qmd", "agentsview", "optmem", "tencentdb", "semantic"):
            step = unavailable_arm(str(kind), str(op.get("reason") or "unavailable"))
        else:
            step = unavailable_arm(str(kind), "unsupported_op")
        steps.append({k: v for k, v in step.items() if k != "stdout"})
        found.extend(step.get("event_ids") or [])
        if step.get("unavailable"):
            break
    wall = (time.perf_counter() - t0) * 1000.0
    return {
        "schema": SCHEMA,
        "recipe": recipe.to_dict(),
        "measurement_source": "executed",
        "wall_ms": wall,
        "steps": steps,
        "event_ids": found,
        "unavailable": any(s.get("unavailable") for s in steps),
    }


def shadow_retrieval(
    model_io: dict[str, Any],
    run: Callable[[], dict[str, Any]],
) -> dict[str, Any]:
    """Run a challenger beside a frozen model I/O buffer. Default-off caller.

    Writes nothing into ``model_io``. A change is a hard failure, not a measurement.
    """
    before = _io_hash(model_io)
    snapshot = json.dumps(model_io, sort_keys=True)
    side = run()
    after = _io_hash(model_io)
    unchanged = before == after and json.dumps(model_io, sort_keys=True) == snapshot
    return {
        "schema": "z0int.retrieval_shadow.v1",
        "student_changed_execution": not unchanged,
        "model_io_unchanged": unchanged,
        "injected": False,
        "side_event_ids": list(side.get("event_ids") or []),
        "promotion_credit": False,
    }


def _io_hash(model_io: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(model_io, sort_keys=True).encode()).hexdigest()


def _parse_json(text: str) -> Any:
    text = text.strip()
    if not text:
        return None
    return json.loads(text)


def _event_ids(parsed: Any) -> list[str]:
    if not isinstance(parsed, dict):
        return []
    out: list[str] = []
    for row in parsed.get("results") or []:
        if isinstance(row, dict) and isinstance(row.get("ctx_event_id"), str):
            out.append(row["ctx_event_id"])
    return out


def _show_event_id(parsed: Any) -> str | None:
    if not isinstance(parsed, dict):
        return None
    for key in ("ctx_event_id", "event_id", "id"):
        if isinstance(parsed.get(key), str):
            return parsed[key]
    event = parsed.get("event")
    if isinstance(event, dict):
        for key in ("ctx_event_id", "id"):
            if isinstance(event.get(key), str):
                return event[key]
    return None


def _nested(parsed: Any, *keys: str) -> Any:
    cur = parsed
    for key in keys:
        if not isinstance(cur, dict):
            return None
        cur = cur.get(key)
    return cur


def recipe_schema_ok(recipe: ResolutionRecipe) -> bool:
    body = recipe.to_dict()
    return body.get("schema") == RECIPE_SCHEMA and bool(body.get("operations"))
