#!/usr/bin/env python3
"""Score DecisionBackends as the *bounded scorer* on local-cognition-v1.

The 28 fixtures in ``benchmarks/fixtures/local-cognition-v1`` are tool/control
selection problems. Each is compiled by the deterministic legal-action compiler
(the same ``compile_actions`` the cascade uses); the backend then sees only the
legal set, as a single ``choice`` question, through ``JevBoundedToolBackend`` —
exactly how a JEV/NanoJev/OpenJev scorer participates in the cognition cascade.

Three rows per backend, all from the same scorer call per fixture:

* ``scorer``   — the backend owns every decision over the compiled legal set
                 (empty legal set => abstain). This is the protocol behind the
                 "bounded scorer 19/28" line in docs/local-cognition-portfolio.md.
* ``compiler+scorer`` — a deterministic compiler solution wins when present,
                 otherwise the scorer's choice.
* ``cascade``  — ``CognitionCascade(jev=<backend>)`` with the default escalation
                 policy and no later tiers: uncertain scorer answers escalate to
                 an absent tier and therefore abstain.

Backends are constructed through the bench roster
(``z0int.backends.bench.roster.create_backend_for_candidate``), so the ids are
the same as ``z0int backends bench`` (e.g. ``laya_421m``, ``llama_http:<arm>``).

    python scripts/local_cognition_backend_eval.py --backend laya_421m,julia_1 \
        --out results/local-cognition/<ts>
"""

from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from z0int.backends.bench.metrics import percentile  # noqa: E402
from z0int.backends.bench.roster import create_backend_for_candidate, probe_candidate  # noqa: E402
from z0int.cognition.actions import compile_actions  # noqa: E402
from z0int.cognition.adapters.bounded import JevBoundedToolBackend  # noqa: E402
from z0int.cognition.adapters.local_slm import ToolDecisionRequest  # noqa: E402
from z0int.cognition.cascade import CascadeContext, CognitionCascade  # noqa: E402
from z0int.cognition.cli import _graph_from_payload  # noqa: E402

SCHEMA = "z0int.local_cognition_backend_eval.v1"
DEFAULT_FIXTURES = REPO / "benchmarks" / "fixtures" / "local-cognition-v1" / "examples.jsonl"


class _Memo:
    """One scorer call per (state, legal ids); the cascade reuses it."""

    def __init__(self, inner: JevBoundedToolBackend) -> None:
        self.inner = inner
        self.cache: dict[tuple[str, tuple[str, ...]], Any] = {}
        self.calls: list[float] = []

    @property
    def backend_id(self) -> str:
        return self.inner.backend_id

    def health(self, *, load: bool = False) -> dict[str, Any]:
        return self.inner.health(load=load)

    def decide(self, request: ToolDecisionRequest):
        key = (request.state or "", request.legal.ids())
        if key not in self.cache:
            t0 = time.perf_counter()
            self.cache[key] = self.inner.decide(request)
            self.calls.append((time.perf_counter() - t0) * 1000.0)
        return self.cache[key]


def _rss_mb(pids: list[int]) -> float | None:
    total = 0.0
    for pid in ["self", *pids]:
        try:
            for line in Path(f"/proc/{pid}/status").read_text().splitlines():
                if line.startswith("VmRSS:"):
                    total += int(line.split()[1]) / 1024.0
        except OSError:
            continue
    return total or None


def _compile(raw: dict[str, Any]):
    return compile_actions(
        graph=_graph_from_payload(raw),
        granted_capabilities=tuple(raw.get("granted_capabilities") or ()),
        authority=tuple(raw.get("authority") or ("read",)),
        budget_units=int(raw.get("budget_units") or 0),
        facts=raw.get("facts") or {},
        satisfied=tuple(raw.get("satisfied") or ()),
    )


def _correct(pred: str | None, raw: dict[str, Any]) -> bool:
    gold = raw.get("gold_action")
    if pred is None:
        return gold == "abstain" or bool(raw.get("expect_abstain"))
    return pred == gold


def evaluate_backend(candidate: str, fixtures: list[dict[str, Any]]) -> dict[str, Any]:
    status = probe_candidate(candidate)
    if status.status != "available":
        return {"candidate_id": candidate, "status": status.status, "reason": status.reason}
    backend = create_backend_for_candidate(candidate)
    t0 = time.perf_counter()
    health = backend.health(load=True)
    cold_ms = (time.perf_counter() - t0) * 1000.0
    scorer = _Memo(JevBoundedToolBackend(backend, backend_id=candidate))
    cascade = CognitionCascade(jev=scorer)
    rows = []
    rss_peak = 0.0
    for raw in fixtures:
        legal = _compile(raw)
        state = str(raw.get("state") or "")
        decision = None
        if not legal.is_empty:
            decision = scorer.decide(ToolDecisionRequest(state=state, legal=legal, max_tokens=256))
        scorer_pred = decision.selected_action if decision is not None else None
        combo_pred = legal.deterministic_solution or scorer_pred
        ctx = CascadeContext(
            state=state,
            graph=_graph_from_payload(raw),
            granted_capabilities=tuple(raw.get("granted_capabilities") or ()),
            authority=tuple(raw.get("authority") or ("read",)),
            budget_units=int(raw.get("budget_units") or 0),
            facts=raw.get("facts") or {},
            satisfied=tuple(raw.get("satisfied") or ()),
        )
        outcome = cascade.run(ctx)
        cascade_pred = None if outcome.abstained else outcome.selected_action
        gold = raw.get("gold_action")
        dist = dict(decision.distribution or {}) if decision is not None else {}
        brier = None
        if dist and gold in dist:
            brier = sum((p - (1.0 if k == gold else 0.0)) ** 2 for k, p in dist.items())
        dangerous = set(raw.get("dangerous_actions") or ())
        pids = getattr(backend, "resident_pids", lambda: [])()
        rss = _rss_mb(list(pids)) or 0.0
        rss_peak = max(rss_peak, rss)
        rows.append({
            "fixture_id": raw["fixture_id"],
            "family": raw.get("family"),
            "gold": gold,
            "legal_ids": list(legal.ids()),
            "gold_legal": gold in legal.ids(),
            "deterministic_solution": legal.deterministic_solution,
            "scorer_pred": scorer_pred,
            "scorer_confidence": decision.confidence if decision is not None else None,
            "scorer_correct": _correct(scorer_pred, raw),
            "compiler_scorer_pred": combo_pred,
            "compiler_scorer_correct": _correct(combo_pred, raw),
            "cascade_pred": cascade_pred,
            "cascade_tier": outcome.tier,
            "cascade_reason": outcome.reason,
            "cascade_correct": _correct(cascade_pred, raw),
            "dangerous_selected": bool(scorer_pred in dangerous or combo_pred in dangerous),
            "brier": brier,
            "latency_ms": decision.latency_ms if decision is not None else None,
            "error": decision.parse_error if decision is not None else None,
        })
    close = getattr(backend, "close", None)
    if callable(close):
        close()
    lat = [float(r["latency_ms"]) for r in rows if r["latency_ms"] is not None]
    n = len(rows)
    briers = [r["brier"] for r in rows if r["brier"] is not None]
    fams: dict[str, list[dict[str, Any]]] = {}
    for r in rows:
        fams.setdefault(str(r["family"]), []).append(r)
    return {
        "candidate_id": candidate,
        "status": "available",
        "device": str(getattr(backend, "device", None) or (health.diagnostics or {}).get("device") or ""),
        "model": health.model,
        "revision": getattr(backend, "revision", None),
        "n": n,
        "scorer_correct": sum(r["scorer_correct"] for r in rows),
        "compiler_scorer_correct": sum(r["compiler_scorer_correct"] for r in rows),
        "cascade_correct": sum(r["cascade_correct"] for r in rows),
        "cascade_abstained": sum(r["cascade_pred"] is None for r in rows),
        "dangerous_selected": sum(r["dangerous_selected"] for r in rows),
        "scorer_errors": sum(bool(r["error"]) for r in rows),
        "mean_brier": statistics.mean(briers) if briers else None,
        "brier_n": len(briers),
        "latency_ms_p50": percentile(lat, 50),
        "latency_ms_p95": percentile(lat, 95),
        "cold_load_ms": cold_ms,
        "rss_mb_peak": rss_peak or None,
        "by_family": {
            fam: {"n": len(rs), "scorer_correct": sum(r["scorer_correct"] for r in rs),
                  "compiler_scorer_correct": sum(r["compiler_scorer_correct"] for r in rs)}
            for fam, rs in sorted(fams.items())
        },
        "rows": rows,
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--backend", required=True, help="comma-separated bench roster candidate ids")
    ap.add_argument("--fixtures", default=str(DEFAULT_FIXTURES))
    ap.add_argument("--out", required=True, help="output directory")
    args = ap.parse_args(argv)
    fixtures = [json.loads(l) for l in Path(args.fixtures).read_text().splitlines() if l.strip()]
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    started = datetime.now(timezone.utc).isoformat()
    results = []
    for cand in [c.strip() for c in args.backend.split(",") if c.strip()]:
        try:
            res = evaluate_backend(cand, fixtures)
        except Exception as exc:  # noqa: BLE001
            res = {"candidate_id": cand, "status": "error", "reason": f"{type(exc).__name__}: {exc}"}
        results.append(res)
        print(json.dumps({k: v for k, v in res.items() if k not in ("rows", "by_family")}), flush=True)
    env = {k: v for k, v in os.environ.items() if k.startswith("Z0INT_") or k in ("OMP_NUM_THREADS",)}
    summary = {"schema": SCHEMA, "fixtures": args.fixtures, "n_fixtures": len(fixtures), "started_at": started,
               "finished_at": datetime.now(timezone.utc).isoformat(), "env": env, "backends": results}
    (out / "summary.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False) + "\n")
    lines = ["| backend | device | scorer | compiler+scorer | cascade (abst) | dangerous | Brier | p50 ms | p95 ms | cold ms | RSS MB |",
             "|---|---|---|---|---|---|---|---|---|---|---|"]
    for r in results:
        if r.get("status") != "available":
            lines.append(f"| {r['candidate_id']} | - | not run: {r.get('reason')} |||||||||")
            continue
        f = lambda v, d=0: "-" if v is None else f"{v:.{d}f}"  # noqa: E731
        lines.append(
            f"| {r['candidate_id']} | {r['device']} | {r['scorer_correct']}/{r['n']} | {r['compiler_scorer_correct']}/{r['n']} | "
            f"{r['cascade_correct']}/{r['n']} ({r['cascade_abstained']}) | {r['dangerous_selected']} | {f(r['mean_brier'], 3)} | "
            f"{f(r['latency_ms_p50'])} | {f(r['latency_ms_p95'])} | {f(r['cold_load_ms'])} | {f(r['rss_mb_peak'])} |")
    (out / "table.md").write_text("\n".join(lines) + "\n")
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
