#!/usr/bin/env python3
"""Per-backend Pareto table from one or more ``z0int backends bench`` run dirs.

Reads the materialized ``raw.jsonl`` (derived from the canonical Tokenomics
events) and ``summary.json`` of each run and prints, per run (fixture family) and
backend: accuracy k/n, mean Brier, ECE (10 bins, top-1 confidence), p50/p95
latency, cold load, peak RSS/VRAM, device, plus the bench's own Pareto frontier
membership per capability.

    python scripts/bench_pareto_table.py results/decision-backends/<run> [...]
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from z0int.backends.bench.metrics import percentile  # noqa: E402


def top1(row: dict) -> float | None:
    """Top-1 probability of the row's (single) answer."""
    res = row.get("result") or {}
    answers = res.get("answers") or []
    if not answers:
        return None
    probs = answers[0].get("probabilities") or {}
    return max(probs.values()) if probs else None


def ece(rows: list[dict], bins: int = 10) -> float | None:
    pts = [(c, bool(r.get("verified_correct"))) for r in rows if (c := top1(r)) is not None]
    if not pts:
        return None
    total = 0.0
    for b in range(bins):
        lo, hi = b / bins, (b + 1) / bins
        sel = [(c, ok) for c, ok in pts if (lo < c <= hi) or (b == 0 and c == 0)]
        if sel:
            conf = sum(c for c, _ in sel) / len(sel)
            acc = sum(ok for _, ok in sel) / len(sel)
            total += len(sel) / len(pts) * abs(conf - acc)
    return total


def fmt(v, d=0):
    return "-" if v is None else f"{v:.{d}f}"


def table(run: Path) -> str:
    rows = [json.loads(l) for l in (run / "raw.jsonl").read_text().splitlines() if l.strip()]
    summary = json.loads((run / "summary.json").read_text())
    frontier = {cap: set(block.get("frontier") or []) for cap, block in
                ((summary.get("pareto") or {}).get("by_capability") or {}).items()}
    order = summary.get("candidates") or sorted({r["candidate_id"] for r in rows})
    out = [f"### {run.name}  (fixtures: {Path(summary.get('fixtures_path', '?')).parent.name}/"
           f"{Path(summary.get('fixtures_path', '?')).name}, n={len({r['fixture_id'] for r in rows})})", "",
           "| backend | device | status | acc | mean Brier | ECE | p50 ms | p95 ms | cold load ms | peak RSS MB | peak VRAM MB | per-capability acc | frontier caps |",
           "|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for cid in order:
        rs = [r for r in rows if r["candidate_id"] == cid]
        if not rs:
            continue
        ok = [r for r in rs if r.get("status") == "ok"]
        if not ok:
            reason = next((r.get("reason") or r.get("error_class") for r in rs if r.get("reason") or r.get("error_class")), "")
            out.append(f"| {cid} | - | {rs[0].get('status')}: {str(reason)[:90]} | - | - | - | - | - | - | - | - | - | - |")
            continue
        n_ok = len(ok)
        acc = sum(bool(r.get("verified_correct")) for r in ok)
        briers = [float(r["brier"]) for r in ok if r.get("brier") is not None]
        lat = [float(r["latency_ms"]) for r in ok if r.get("latency_ms") is not None]
        cold = [float(r["startup_ms"]) for r in ok if r.get("startup_ms") is not None]
        ram = [float(r["ram_mb"]) for r in ok if r.get("ram_mb") is not None]
        vram = [float(r["vram_mb"]) for r in ok if r.get("vram_mb") is not None]
        caps: dict[str, list] = {}
        for r in ok:
            caps.setdefault(r["capability"], []).append(bool(r.get("verified_correct")))
        per_cap = "; ".join(f"{c.split('.')[-1]} {sum(v)}/{len(v)}" for c, v in sorted(caps.items()))
        fr = ",".join(c.split(".")[-1] for c, ids in sorted(frontier.items()) if cid in ids) or "-"
        errs = len(rs) - n_ok
        status = "ok" + (f" ({errs} err)" if errs else "")
        out.append(
            f"| {cid} | {ok[0].get('device') or '-'} | {status} | {acc}/{len(rs)} ({acc / len(rs):.1%}) | "
            f"{fmt(sum(briers) / len(briers) if briers else None, 3)} | {fmt(ece(ok), 3)} | {fmt(percentile(lat, 50))} | "
            f"{fmt(percentile(lat, 95))} | {fmt(cold[0] if cold else None)} | {fmt(max(ram) if ram else None)} | "
            f"{fmt(max(vram) if vram else None)} | {per_cap} | {fr} |")
    return "\n".join(out) + "\n"


def main(argv: list[str]) -> int:
    for arg in argv:
        print(table(Path(arg)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
