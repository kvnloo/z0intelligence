#!/usr/bin/env python3
"""Multi-turn orchestration evaluation.

The bounded-choice suite in ``local_tool_calling_eval.py`` cannot judge an
orchestrator. This runs the real multi-turn loop: the model picks a callable, the
world answers, and it keeps going until it finishes, runs out of turns or runs
out of budget.

    python scripts/orchestration_eval.py --served
    python scripts/orchestration_eval.py --backend nemotron_orchestrator_8b \
        --backend hammer2.1_3b --backend qwen3.5_4b --served
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from z0int.cognition.adapters.transport import ServerConfig  # noqa: E402
from z0int.cognition.orchestration import (  # noqa: E402
    LocalOrchestrator,
    Scenario,
    run_scenario,
    scenario_from_dict,
)
from z0int.cognition.registry import load_serving  # noqa: E402

SCHEMA = "z0int.orchestration_eval_report.v1"
DEFAULT_SCENARIOS = REPO / "benchmarks" / "fixtures" / "orchestration-v1" / "scenarios.jsonl"


def load_scenarios(path: Path) -> list[Scenario]:
    out: list[Scenario] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        out.append(scenario_from_dict(json.loads(line)))
    return out


def percentile(values: Sequence[float], pct: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    k = (len(ordered) - 1) * (pct / 100.0)
    lo = int(k)
    hi = min(lo + 1, len(ordered) - 1)
    return ordered[lo] + (ordered[hi] - ordered[lo]) * (k - lo)


def _num(value: float | None, digits: int) -> str:
    return "-" if value is None else f"{value:.{digits}f}"


def run_greedy(scenario: Scenario) -> dict[str, Any]:
    """Deterministic control: cheapest-first coverage, then stop."""
    resolved: set[str] = set()
    remaining = list(scenario.requires)
    max_by_specialty: dict[str, int] = {}
    for c in scenario.callables:
        for s in c.resolves:
            if s in scenario.requires:
                max_by_specialty.setdefault(s, 10_000)
    cost = 0
    picks: list[str] = []
    turns = 0
    started = time.perf_counter()
    order_violations = 0
    while remaining and turns < scenario.max_turns:
        turns += 1
        # cheapest callable that strictly cheapens the remaining set
        best = None
        for c in sorted(scenario.callables, key=lambda c: (c.cost_units, c.name)):
            covered = [s for s in c.resolves if s in remaining]
            if not covered:
                continue
            if cost + c.cost_units > scenario.budget_units:
                continue
            best = (c, covered)
            break
        if best is None:
            break
        c, covered = best
        cost += c.cost_units
        picks.append(c.name)
        for before, after in scenario.order_constraints:
            if c.name == after and before not in picks[:-1]:
                order_violations += 1
        for s in covered:
            remaining.remove(s)
            resolved.add(s)
    latency = (time.perf_counter() - started) * 1000.0
    return {
        "scenario_id": scenario.scenario_id,
        "backend": "optimal_greedy",
        "model": None,
        "solved": not remaining,
        "solvable": scenario.solvable,
        "correct_stop": (not remaining) if scenario.solvable else bool(not picks),
        "stopped": True,
        "premature_stop": bool(remaining),
        "budget_exhausted": False,
        "turns": turns,
        "cost_units": cost,
        "unresolved": remaining,
        "order_violations": order_violations,
        "wasted_calls": 0,
        "specialist_precision": 1.0 if picks else None,
        "latency_ms": latency,
        "invalid_calls": 0,
        "error": None,
        "turns_detail": [{"chosen": p} for p in picks],
    }


def evaluate(
    backend_id: str, config: ServerConfig, scenarios: Sequence[Scenario], *, label: str | None = None
) -> dict[str, Any]:
    orchestrator = LocalOrchestrator(backend_id=label or backend_id, config=config)
    rows: list[dict[str, Any]] = []
    for scenario in scenarios:
        print(f"#   {scenario.scenario_id}", file=sys.stderr)
        try:
            result = run_scenario(scenario, orchestrator)
        except Exception as exc:  # noqa: BLE001 - a crash is a data point, not a stop
            rows.append(
                {
                    "scenario_id": scenario.scenario_id,
                    "backend": label or backend_id,
                    "solved": False,
                    "solvable": scenario.solvable,
                    "correct_stop": False,
                    "error": f"{type(exc).__name__}: {exc}",
                    "latency_ms": 0.0,
                    "cost_units": 0,
                    "turns": 0,
                    "wasted_calls": 0,
                    "order_violations": 0,
                    "premature_stop": False,
                    "budget_exhausted": False,
                    "invalid_calls": 0,
                    "specialist_precision": None,
                    "unresolved": list(scenario.requires),
                    "turns_detail": [],
                }
            )
            continue
        rows.append(result.to_dict())
    return summarise(label or backend_id, rows)


def summarise(label: str, rows: list[dict[str, Any]]) -> dict[str, Any]:
    n = len(rows)
    lat = [r["latency_ms"] for r in rows]
    by_family: dict[str, dict[str, int]] = {}
    for r in rows:
        fam = r["scenario_id"].split("_")[0]
        b = by_family.setdefault(fam, {"n": 0, "solved": 0})
        b["n"] += 1
        b["solved"] += int(bool(r["solved"]))
    precisions = [r["specialist_precision"] for r in rows if r["specialist_precision"] is not None]
    return {
        "backend": label,
        "scenarios": n,
        "solved": sum(int(bool(r["solved"])) for r in rows),
        "correct_stop": sum(int(bool(r.get("correct_stop"))) for r in rows),
        "premature_stop": sum(int(bool(r["premature_stop"])) for r in rows),
        "budget_exhausted": sum(int(bool(r["budget_exhausted"])) for r in rows),
        "wasted_calls": sum(int(r["wasted_calls"]) for r in rows),
        "order_violations": sum(int(r["order_violations"]) for r in rows),
        "invalid_calls": sum(int(r["invalid_calls"]) for r in rows),
        "errors": sum(1 for r in rows if r["error"]),
        "cost_units_total": sum(int(r["cost_units"]) for r in rows),
        "turns_total": sum(int(r["turns"]) for r in rows),
        "mean_specialist_precision": (statistics.fmean(precisions) if precisions else None),
        "latency_ms": {
            "p50": percentile(lat, 50),
            "p95": percentile(lat, 95),
            "p99": percentile(lat, 99),
            "mean": statistics.fmean(lat) if lat else None,
            "max": max(lat) if lat else None,
        },
        "by_family": by_family,
        "rows": rows,
    }


def render_md(results: list[dict[str, Any]]) -> str:
    lines = [
        "# Multi-turn orchestration evaluation",
        "",
        f"- schema: `{SCHEMA}`",
        "- The model picks one callable per turn; the world answers deterministically.",
        "- `solved` = every required specialty was resolved before stopping.",
        "- `wasted_calls` = dispatches that resolved nothing.",
        "- `order_violations` = a dependency dispatched before its prerequisite.",
        "- `optimal_greedy` is the cheapest-covering-set control.",
        "",
        "| backend | scenarios | solved | correct stop | premature stop | budget out | wasted "
        "| order viol | invalids | errors | cost units | turns | precision | p50 ms | p95 ms |",
        "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for r in results:
        lat = r["latency_ms"]
        prec = r["mean_specialist_precision"]
        lines.append(
            f"| {r['backend']} | {r['scenarios']} | {r['solved']}/{r['scenarios']} "
            f"| {r['correct_stop']}/{r['scenarios']} "
            f"| {r['premature_stop']} | {r['budget_exhausted']} | {r['wasted_calls']} "
            f"| {r['order_violations']} | {r['invalid_calls']} | {r['errors']} "
            f"| {r['cost_units_total']} | {r['turns_total']} "
            f"| {_num(prec, 2)} | {_num(lat['p50'], 0)} | {_num(lat['p95'], 0)} |"
        )
    return "\n".join(lines) + "\n"


def _stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--scenarios", default=str(DEFAULT_SCENARIOS))
    ap.add_argument("--backend", action="append", default=[], help="served model_id")
    ap.add_argument("--served", action="store_true", help="evaluate every served model")
    ap.add_argument("--greedy", action="store_true", help="include the optimal_greedy control")
    ap.add_argument("--out", default=None)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    scenarios = load_scenarios(Path(args.scenarios))
    endpoints = load_serving()
    wanted = list(args.backend)
    if args.served:
        wanted += [m for m in endpoints if m not in wanted]
    if not wanted and not args.greedy:
        print("no backends requested; use --backend, --served or --greedy", file=sys.stderr)
        return 2

    results: list[dict[str, Any]] = []
    if args.greedy:
        rows = [run_greedy(s) for s in scenarios]
        results.append(summarise("optimal_greedy", rows))

    for model_id in wanted:
        endpoint = endpoints.get(model_id)
        if endpoint is None:
            print(f"# skipping {model_id}: not in the serving map", file=sys.stderr)
            continue
        print(f"# evaluating {model_id} ...", file=sys.stderr)
        config = ServerConfig(
            base_url=endpoint.base_url,
            model=endpoint.served_model,
            api_key=endpoint.api_key,
            runtime=endpoint.runtime,
            quantization=endpoint.quantization,

            timeout_s=600.0,
        )
        results.append(evaluate(model_id, config, scenarios))

    out = Path(args.out) if args.out else (REPO / "results" / "orchestration" / _stamp())
    out.mkdir(parents=True, exist_ok=True)
    report = {
        "schema": SCHEMA,
        "scenarios_path": str(args.scenarios),
        "scenario_count": len(scenarios),
        "results": results,
    }
    (out / "report.json").write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    (out / "report.md").write_text(render_md(results), encoding="utf-8")
    with (out / "raw.jsonl").open("w", encoding="utf-8") as fh:
        for r in results:
            for row in r["rows"]:
                fh.write(json.dumps(row, sort_keys=True) + "\n")
    print(f"# wrote {out}", file=sys.stderr)

    if args.json:
        print(
            json.dumps(
                {
                    "schema": SCHEMA,
                    "scenario_count": len(scenarios),
                    "results": [{k: v for k, v in r.items() if k != "rows"} for r in results],
                },
                indent=2,
                sort_keys=True,
            )
        )
    else:
        print(render_md(results))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
