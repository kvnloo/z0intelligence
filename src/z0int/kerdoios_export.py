"""Project z0intelligence receipts → z0int.allocation_observation.v1 for Kerdoios.

Receipts remain authoritative. This is a projection only — no second ledger.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from . import paths

OBS_SCHEMA = "z0int.allocation_observation.v1"


def _iter_receipts(since: float | None = None):
    root = paths.home() / "receipts"
    if not root.is_dir():
        return
    for p in sorted(root.glob("**/*")):
        if p.suffix == ".json":
            try:
                data = json.loads(p.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    yield data
            except Exception:
                continue
            continue
        if p.suffix == ".jsonl" or p.name.endswith(".jsonl"):
            try:
                for line in p.read_text(encoding="utf-8").splitlines():
                    if not line.strip():
                        continue
                    row = json.loads(line)
                    if since is not None and float(row.get("ts") or row.get("closed_at") or 0) < since:
                        continue
                    yield row
            except Exception:
                continue


def _also_stream(since: float | None):
    heart = paths.home() / "stream" / "bridge_heart.jsonl"
    if not heart.is_file():
        return
    try:
        for line in heart.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            row = json.loads(line)
            ts = float(row.get("ts") or 0)
            if since is not None and ts < since:
                continue
            yield row
    except Exception:
        return


def receipt_to_observation(row: dict[str, Any]) -> dict[str, Any] | None:
    trace = row.get("trace_id")
    if not trace:
        return None
    vs = row.get("verified_success", row.get("verified"))
    # preserve null vs false
    if vs is False:
        verified: bool | None = False
    elif vs is True:
        verified = True
    else:
        verified = None
    return {
        "schema": OBS_SCHEMA,
        "allocation_id": row.get("allocation_id") or row.get("kerdoios_allocation_id") or f"alloc:{trace}",
        "trace_id": trace,
        "provider": row.get("provider") or (row.get("kerdoios_plan") or {}).get("provider"),
        "model": row.get("model") or (row.get("kerdoios_plan") or {}).get("model"),
        "execution_completed": bool(row.get("execution_completed") or row.get("completed") or False),
        "verified_success": verified,
        "verification_source": row.get("verification_source") or row.get("verifier_id"),
        "input_tokens": row.get("input_tokens") or row.get("measured_input_tokens"),
        "output_tokens": row.get("output_tokens") or row.get("measured_output_tokens"),
        "cached_input_tokens": row.get("cached_input_tokens") or 0,
        "latency_ms": row.get("latency_ms"),
        "failure_reason": row.get("failure_reason"),
    }


def export_observations(*, since: float | None = None, output: Path | str | None = None) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for src in list(_iter_receipts(since)) + list(_also_stream(since)):
        obs = receipt_to_observation(src)
        if not obs:
            continue
        key = str(obs["trace_id"]) + ":" + str(obs.get("allocation_id"))
        if key in seen:
            continue
        seen.add(key)
        rows.append(obs)
    out = {
        "schema": "z0int.kerdoios_export.v1",
        "exported_at": time.time(),
        "since": since,
        "n": len(rows),
        "observations": rows,
    }
    if output:
        p = Path(output)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(out, indent=2) + "\n", encoding="utf-8")
        out["path"] = str(p)
    return out
