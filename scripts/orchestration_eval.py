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
EXPANSION_SCENARIOS = REPO / "benchmarks" / "fixtures" / "orchestration-v2" / "scenarios.jsonl"


def load_scenarios(path: Path, cohort: str) -> list[tuple[Scenario, str]]:
    out: list[tuple[Scenario, str]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        out.append((scenario_from_dict(json.loads(line)), cohort))
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
    """Deterministic control: cheapest affordable covering callable each turn.

    Extended for the Phase 1B cohort so the control is measured on the same
    axes as the models: it respects hard dependencies, retries a faulty callable
    once, and never takes optional evidence.
    """
    faulty_mode = {name: mode for name, mode in scenario.faulty_callables}
    faulty_hits: dict[str, int] = {}
    failed_faulty: set[str] = set()
    dead: set[str] = set()
    remaining = set(scenario.requires)
    dispatched: list[str] = []
    succeeded: set[str] = set()
    picks: list[str] = []
    turns = 0
    cost = 0
    wasted = 0
    order_violations = 0
    needless_escalation = 0
    optional_taken = 0
    resolved_log: list[tuple[str, tuple[str, ...]]] = []
    started = time.perf_counter()

    while remaining and turns < scenario.max_turns:
        best = None
        for c in sorted(scenario.callables, key=lambda c: (c.cost_units, c.name)):
            if cost + c.cost_units > scenario.budget_units:
                continue
            if c.name in succeeded or c.name in dead:
                continue
            covered = [s for s in c.resolves if s in remaining]
            if not covered:
                continue
            blocked = any(
                c.name == after and before not in dispatched
                for before, after in scenario.order_constraints
            )
            if blocked:
                continue
            best = (c, covered)
            break
        if best is None:
            break

        c, covered = best
        turns += 1
        cost += c.cost_units
        for before, after in scenario.order_constraints:
            if c.name == after and before not in dispatched:
                order_violations += 1
        hit = faulty_hits.get(c.name, 0)
        faulty_hits[c.name] = hit + 1
        mode = faulty_mode.get(c.name)
        failed_this_turn = mode == "empty" or (mode == "error" and hit == 0)
        if failed_this_turn:
            failed_faulty.add(c.name)
            if mode == "empty":
                # an empty result will not change on a retry
                dead.add(c.name)
            covered = []
        else:
            succeeded.add(c.name)
        if c.name in set(scenario.optional_evidence):
            optional_taken += 1
        dispatched.append(c.name)
        picks.append(c.name)
        resolved_log.append((c.name, tuple(covered)))
        for s in covered:
            remaining.discard(s)
        if not covered:
            wasted += 1

    latency = (time.perf_counter() - started) * 1000.0
    solved = not remaining
    recoveries = len(failed_faulty) if solved else 0
    recovered = bool(failed_faulty) and solved
    failure_to_escalate = False
    if remaining:
        for alt in scenario.callables:
            if alt.name in dispatched:
                continue
            if cost + alt.cost_units > scenario.budget_units:
                continue
            if remaining & set(alt.resolves):
                failure_to_escalate = True
                break
    engaging = [p for p in resolved_log]
    precision = (
        sum(1 for _, r in engaging if r) / len(engaging) if engaging else None
    )
    return {
        "scenario_id": scenario.scenario_id,
        "backend": "optimal_greedy",
        "model": None,
        "solved": solved,
        "solvable": scenario.solvable,
        "correct_stop": (solved) if scenario.solvable else bool(not picks),
        "stopped": True,
        "premature_stop": bool(remaining),
        "budget_exhausted": False,
        "turns": turns,
        "cost_units": cost,
        "unresolved": sorted(remaining),
        "order_violations": order_violations,
        "wasted_calls": wasted,
        "specialist_precision": precision,
        "latency_ms": latency,
        "invalid_calls": 0,
        "error": None,
        "recovered": recovered,
        "recoveries": recoveries,
        "needless_escalation": needless_escalation,
        "failure_to_escalate": failure_to_escalate,
        "optional_evidence_taken": optional_taken,
        "turns_detail": [{"chosen": p} for p in picks],
    }


def evaluate(
    backend_id: str, config: ServerConfig, scenarios: Sequence[tuple[Scenario, str]], *,
    label: str | None = None,
) -> dict[str, Any]:
    orchestrator = LocalOrchestrator(backend_id=label or backend_id, config=config)
    rows: list[dict[str, Any]] = []
    for scenario, cohort in scenarios:
        print(f"#   {scenario.scenario_id}", file=sys.stderr)
        try:
            result = run_scenario(scenario, orchestrator)
        except Exception as exc:  # noqa: BLE001 - a crash is a data point, not a stop
            rows.append(
                {
                    "scenario_id": scenario.scenario_id,
                    "cohort": cohort,
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
                    "recovered": False,
                    "recoveries": 0,
                    "needless_escalation": 0,
                    "failure_to_escalate": False,
                    "optional_evidence_taken": 0,
                    "turns_detail": [],
                }
            )
            continue
        row = result.to_dict()
        row["cohort"] = cohort
        rows.append(row)
    return summarise(label or backend_id, rows)


def summarise(label: str, rows: list[dict[str, Any]]) -> dict[str, Any]:
    n = len(rows)
    lat = [r["latency_ms"] for r in rows]
    by_family: dict[str, dict[str, int]] = {}
    by_cohort: dict[str, dict[str, int]] = {}
    for r in rows:
        fam = r["scenario_id"].split("_")[0]
        b = by_family.setdefault(fam, {"n": 0, "solved": 0})
        b["n"] += 1
        b["solved"] += int(bool(r["solved"]))
        c = by_cohort.setdefault(r.get("cohort", "?"), {"n": 0, "solved": 0, "correct_stop": 0})
        c["n"] += 1
        c["solved"] += int(bool(r["solved"]))
        c["correct_stop"] += int(bool(r.get("correct_stop")))
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
        "recovered": sum(int(bool(r.get("recovered"))) for r in rows),
        "recovery_opportunities": sum(
            1 for r in rows if r["scenario_id"].startswith("recover")
        ),
        "needless_escalation": sum(int(r.get("needless_escalation", 0)) for r in rows),
        "failure_to_escalate": sum(int(bool(r.get("failure_to_escalate"))) for r in rows),
        "optional_evidence_taken": sum(int(r.get("optional_evidence_taken", 0)) for r in rows),
        "latency_ms": {
            "p50": percentile(lat, 50),
            "p95": percentile(lat, 95),
            "p99": percentile(lat, 99),
            "mean": statistics.fmean(lat) if lat else None,
            "max": max(lat) if lat else None,
        },
        "by_family": by_family,
        "by_cohort": by_cohort,
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
        "- `recovered` = the world returned an error/empty result and the task still got solved.",
        "- `needless_escalation` = dispatched a costlier callable when a cheaper affordable one",
        "  covered the same specialty.",
        "- `failure_to_escalate` = stopped with work outstanding while an affordable callable",
        "  that could have resolved it was never dispatched.",
        "",
        "| backend | cohort | scenarios | solved | correct stop | premature stop | budget out | wasted "
        "| order viol | invalids | errors | cost units | turns | precision | recovered | needless esc "
        "| failed to esc | optional taken | p50 ms | p95 ms |",
        "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for r in results:
        lat = r["latency_ms"]
        prec = r["mean_specialist_precision"]
        for cohort, c in sorted(r.get("by_cohort", {"all": {"n": r["scenarios"], "solved": r["solved"],
                                                            "correct_stop": r["correct_stop"]}}).items()):
            lines.append(
                f"| {r['backend']} | {cohort} | {c['n']} | {c['solved']}/{c['n']} "
                f"| {c['correct_stop']}/{c['n']} "
                f"| {r['premature_stop']} | {r['budget_exhausted']} | {r['wasted_calls']} "
                f"| {r['order_violations']} | {r['invalid_calls']} | {r['errors']} "
                f"| {r['cost_units_total']} | {r['turns_total']} "
                f"| {_num(prec, 2)} | {r['recovered']}/{r['recovery_opportunities']} "
                f"| {r['needless_escalation']} | {r['failure_to_escalate']} "
                f"| {r['optional_evidence_taken']} "
                f"| {_num(lat['p50'], 0)} | {_num(lat['p95'], 0)} |"
            )
    return "\n".join(lines) + "\n"


def _stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--scenarios", action="append", default=[],
                    help="scenario file; repeatable. Default: the frozen regression cohort")
    ap.add_argument("--include-expansion", action="store_true",
                    help="also run benchmarks/fixtures/orchestration-v2 (the Phase 1B corpus)")
    ap.add_argument("--expansion-only", action="store_true",
                    help="run only the Phase 1B expansion corpus")
    ap.add_argument("--backend", action="append", default=[], help="served model_id")
    ap.add_argument("--served", action="store_true", help="evaluate every served model")
    ap.add_argument("--greedy", action="store_true", help="include the optimal_greedy control")
    ap.add_argument("--out", default=None)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    if args.expansion_only:
        sources: list[tuple[Path, str]] = [(EXPANSION_SCENARIOS, "expansion")]
    else:
        sources = [(Path(p), "regression" if "orchestration-v1" in p else "custom")
                   for p in args.scenarios] or [(DEFAULT_SCENARIOS, "regression")]
        if args.include_expansion:
            sources.append((EXPANSION_SCENARIOS, "expansion"))
    scenarios: list[tuple[Scenario, str]] = []
    for path, cohort in sources:
        found = load_scenarios(path, cohort)
        scenarios.extend(found)
        print(f"# cohort={cohort} file={path.name} scenarios={len(found)}", file=sys.stderr)

    endpoints = load_serving()
    wanted = list(args.backend)
    if args.served:
        wanted += [m for m in endpoints if m not in wanted]
    if not wanted and not args.greedy:
        print("no backends requested; use --backend, --served or --greedy", file=sys.stderr)
        return 2

    results: list[dict[str, Any]] = []
    if args.greedy:
        rows = []
        for scenario, cohort in scenarios:
            row = run_greedy(scenario)
            row["cohort"] = cohort
            rows.append(row)
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
        "scenario_sources": [{"path": str(p), "cohort": c} for p, c in
                             (sources if not args.expansion_only else [(EXPANSION_SCENARIOS, "expansion")])],
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
