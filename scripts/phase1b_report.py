#!/usr/bin/env python3
"""Phase 1B sections C + H -- turn the densified receipts into the decision artifact.

This reads only ``observations.jsonl`` (one raw receipt per model call) plus the
frozen baseline, and produces:

* per-arm benchmark with an interval, not a point estimate;
* warm/cold/flip residency distributions, kept apart;
* ``(state, arm)`` coverage counts, because a cell with n=1 cannot decide a gate;
* a revised Pareto frontier per state family;
* dominance calls that require the evidence to *separate* the arms, so an arm is
  never demoted on overlapping intervals;
* the meaningful pairwise comparisons section C names, read through the frozen
  non-inferiority logic rather than a freshly invented one;
* state-family niches, so "globally dominated" never erases an arm that wins a
  distinct family.

Nothing here trains, tunes a threshold, or promotes anything.

    python scripts/phase1b_report.py --run-id p1b-... --json
"""

from __future__ import annotations

import argparse
import json
import math
import statistics
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

SCHEMA = "z0int.phase1b.decision_artifact.v1"
RESULTS_ROOT = REPO / "results" / "phase1b"

#: Section C's meaningful comparisons, as ``(cheap, expensive, question)``.
PAIRWISE_QUESTIONS: tuple[tuple[str, str, str], ...] = (
    ("compiler+hammer2.1_3b", "compiler+qwen3.5_4b",
     "does the 4B earn its extra latency over Hammer3B?"),
    ("compiler+hammer2.1_3b", "compiler+qwen3.5_9b",
     "does the 9B earn its extra latency over Hammer3B?"),
    ("compiler+hammer2.1_3b", "compiler+nemotron_orchestrator_8b",
     "does Nemotron earn a bounded-choice role at all?"),
    ("compiler+hammer2.1_3b", "compiler+hammer2.1_7b",
     "does the 7B beat the 3B on the same family?"),
    ("compiler+qwen3.5_4b", "compiler+qwen3.5_9b",
     "is the 9B ever worth it once the 4B is available?"),
    ("compiler+hammer2.1_3b", "compiler+functiongemma_270m",
     "does anything beat the tiny specialist?"),
)


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


def wilson_interval(successes: int, n: int, z: float = 1.96) -> tuple[float, float]:
    """Wilson score interval -- honest at small n, unlike normal approximation."""
    if n == 0:
        return (0.0, 1.0)
    phat = successes / n
    denominator = 1 + z * z / n
    centre = (phat + z * z / (2 * n)) / denominator
    margin = (z * math.sqrt((phat * (1 - phat) + z * z / (4 * n)) / n)) / denominator
    return (max(0.0, centre - margin), min(1.0, centre + margin))


@dataclass
class ArmStats:
    arm: str
    family_kind: str
    n: int = 0
    states: set[str] = field(default_factory=set)
    successes: int = 0
    dangerous: int = 0
    abstained: int = 0
    invalid: int = 0
    errors: int = 0
    latencies: list[float] = field(default_factory=list)
    decision_ms: list[float] = field(default_factory=list)
    load_ms: list[float] = field(default_factory=list)
    tokens_out: list[float] = field(default_factory=list)
    by_residency: dict[str, list[float]] = field(default_factory=lambda: defaultdict(list))
    by_residency_n: dict[str, int] = field(default_factory=lambda: defaultdict(int))
    by_state: dict[str, list[bool]] = field(default_factory=lambda: defaultdict(list))
    by_family: dict[str, list[bool]] = field(default_factory=lambda: defaultdict(list))

    def to_dict(self) -> dict[str, Any]:
        lo, hi = wilson_interval(self.successes, self.n)
        return {
            "arm": self.arm,
            "kind": self.family_kind,
            "n": self.n,
            "n_states": len(self.states),
            "successes": self.successes,
            "success_rate": (self.successes / self.n) if self.n else None,
            "success_ci95": [lo, hi],
            "dangerous_selected": self.dangerous,
            "dangerous_rate": (self.dangerous / self.n) if self.n else 0.0,
            "abstained": self.abstained,
            "invalid_calls": self.invalid,
            "errors": self.errors,
            "latency_ms": {
                "p50": percentile(self.latencies, 50),
                "p95": percentile(self.latencies, 95),
                "mean": statistics.fmean(self.latencies) if self.latencies else None,
            },
            "decision_ms_p50": percentile(self.decision_ms, 50),
            "load_ms_p50": percentile(self.load_ms, 50) if any(self.load_ms) else 0.0,
            "tokens_out_p50": percentile(self.tokens_out, 50) if self.tokens_out else None,
            "by_residency": {
                k: {
                    "n": self.by_residency_n[k],
                    "p50_ms": percentile(v, 50),
                    "p95_ms": percentile(v, 95),
                    "mean_ms": statistics.fmean(v) if v else None,
                }
                for k, v in sorted(self.by_residency.items())
            },
            "per_state_n": {k: len(v) for k, v in sorted(self.by_state.items())},
            "states_with_disagreement": sorted(
                k for k, v in self.by_state.items() if 0 < sum(v) < len(v)
            ),
            "state_success": {
                k: (sum(v) / len(v)) for k, v in sorted(self.by_state.items())
            },
        }


def _kind(arm: str, declared: str | None) -> str:
    """Arm kind, taken from the receipt's own declaration rather than guessed.

    The harness stamps ``arm_kind`` on every observation, so the report does not
    have to re-derive it from the arm's name -- a guess that mislabelled a
    single-scorer arm as a cascade because its name contained "jev".
    """
    if declared:
        # The harness's own vocabulary: ``bounded`` means one model behind the
        # compiler, which is what the report calls ``single_model``.
        return {
            "unfiltered": "unfiltered_control",
            "bounded": "single_model",
        }.get(declared, declared)
    if arm.startswith("deterministic"):
        return "deterministic"
    if arm.startswith("unfiltered"):
        return "unfiltered_control"
    return "single_model"


def collect(rows: Iterable[Mapping[str, Any]]) -> dict[str, ArmStats]:
    stats: dict[str, ArmStats] = {}
    for row in rows:
        arm = str(row.get("arm"))
        st = stats.get(arm)
        if st is None:
            st = ArmStats(arm=arm, family_kind=_kind(arm, row.get("arm_kind")))
            stats[arm] = st
        st.n += 1
        st.states.add(str(row.get("state_id")))
        st.successes += int(bool(row.get("correct")))
        st.dangerous += int(bool(row.get("dangerous_selected")))
        st.abstained += int(bool(row.get("abstained")))
        st.invalid += int(bool(row.get("invalid_call")))
        st.errors += int(bool((row.get("failure_retry") or {}).get("error")))
        total = float(row.get("total_ms") or 0.0)
        st.latencies.append(total)
        st.decision_ms.append(float(row.get("decision_ms") or 0.0))
        if row.get("load_ms"):
            st.load_ms.append(float(row["load_ms"]))
        if row.get("tokens_out") is not None:
            st.tokens_out.append(float(row["tokens_out"]))
        residency = str(row.get("cold_or_warm") or "unknown")
        st.by_residency[residency].append(total)
        st.by_residency_n[residency] += 1
        st.by_state[str(row.get("state_id"))].append(bool(row.get("correct")))
        st.by_family[str(row.get("state_family") or "uncategorised")].append(
            bool(row.get("correct"))
        )
    return stats


def coverage(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    cells: dict[tuple[str, str], int] = defaultdict(int)
    for row in rows:
        cells[(str(row.get("state_id")), str(row.get("arm")))] += 1
    histogram: dict[int, int] = defaultdict(int)
    for n in cells.values():
        histogram[n] += 1
    return {
        "cells": len(cells),
        "observations": len(rows),
        "states": len({k[0] for k in cells}),
        "arms": len({k[1] for k in cells}),
        "cells_with_n_ge_2": sum(1 for n in cells.values() if n >= 2),
        "cells_with_n_ge_3": sum(1 for n in cells.values() if n >= 3),
        "cells_with_n_ge_10": sum(1 for n in cells.values() if n >= 10),
        "n_histogram": {str(k): v for k, v in sorted(histogram.items())},
        "cells_below_3": sorted(
            f"{state}/{arm}" for (state, arm), n in cells.items() if n < 3
        ),
    }


def dominated_arms(stats: Mapping[str, ArmStats]) -> list[dict[str, Any]]:
    """Dominance that requires the intervals to *separate* the arms.

    An arm is only called dominated when another arm's success lower bound is
    strictly above its upper bound, or when success is indistinguishable but the
    other arm is faster *and* at least as good on the interval.  Overlapping
    evidence is reported as inconclusive, never as a loss.
    """
    bounded = [
        s for s in stats.values()
        if s.family_kind in ("single_model", "deterministic", "cascade") and s.n >= 3
    ]
    out: list[dict[str, Any]] = []
    for arm in bounded:
        lo_a, hi_a = wilson_interval(arm.successes, arm.n)
        p50_a = percentile(arm.latencies, 50) or 0.0
        best: dict[str, Any] | None = None
        for other in bounded:
            if other.arm == arm.arm:
                continue
            lo_b, hi_b = wilson_interval(other.successes, other.n)
            p50_b = percentile(other.latencies, 50) or 0.0
            quality_strict = lo_b > hi_a
            # "Equal quality" must mean the other arm is not meaningfully worse
            # on the point estimate, using the frozen gate's own tolerance.
            # Overlapping intervals alone are not enough: an arm at 0.89 and one
            # at 0.61 overlap at the margin, and letting the faster-but-worse one
            # claim dominance would invert the finding.
            success_a = arm.successes / arm.n
            success_b = other.successes / other.n
            quality_equal = success_b >= success_a - 0.02
            faster = p50_b < p50_a * 0.95
            if quality_strict or (quality_equal and faster):
                candidate = {
                    "by": other.arm,
                    "quality_strict": quality_strict,
                    "quality_regression": success_a - success_b,
                    "faster": faster,
                    "other_success_ci95": [lo_b, hi_b],
                    "other_p50_ms": p50_b,
                }
                if best is None or candidate["other_p50_ms"] < best["other_p50_ms"]:
                    best = candidate
        if best is not None:
            out.append(
                {
                    "arm": arm.arm,
                    "n": arm.n,
                    "success": arm.successes / arm.n,
                    "success_ci95": [lo_a, hi_a],
                    "p50_ms": p50_a,
                    "dominated_by": best["by"],
                    "strict_quality_gap": best["quality_strict"],
                    "quality_regression": best["quality_regression"],
                    "faster": best["faster"],
                    "evidence": best,
                }
            )
    out.sort(key=lambda r: (r["arm"],))
    return out


def pareto_frontier(stats: Mapping[str, ArmStats]) -> dict[str, Any]:
    """Per state, arms on the (success, latency) frontier with interval evidence."""
    points: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for arm in stats.values():
        # The deterministic compiler-only arm makes no model call, so its 0 ms
        # latency puts it on every raw (success, latency) frontier and tells us
        # nothing.  It is a control, not a model tier, so it is excluded here and
        # remains visible in the per-arm table and the dominance list.
        if arm.family_kind not in ("single_model", "cascade"):
            continue
        for state, outcomes in arm.by_state.items():
            if len(outcomes) < 1:
                continue
            points[state].append(
                {
                    "arm": arm.arm,
                    "n": len(outcomes),
                    "success_rate": sum(outcomes) / len(outcomes),
                    "p50_ms": arm.by_residency and percentile(
                        [v for vals in arm.by_residency.values() for v in vals], 50
                    ) or 0.0,
                }
            )
    frontier: dict[str, list[str]] = {}
    for state, entries in points.items():
        keep: list[str] = []
        for entry in entries:
            dominated = False
            for other in entries:
                if other["arm"] == entry["arm"]:
                    continue
                ge = (other["success_rate"] >= entry["success_rate"] - 1e-12
                      and other["p50_ms"] <= entry["p50_ms"] + 1e-12)
                gt = (other["success_rate"] > entry["success_rate"] + 1e-12
                      or other["p50_ms"] < entry["p50_ms"] - 1e-12)
                if ge and gt:
                    dominated = True
                    break
            if not dominated:
                keep.append(entry["arm"])
        frontier[state] = sorted(keep)
    # The raw Pareto frontier is fast to read but weak in practice: an arm that
    # is merely the fastest is non-dominated on every state where nobody beats
    # it on *both* axes, so a 66 ms arm with 0.61 accuracy shows up everywhere.
    # The decision-relevant view is the lexicographic optimum -- highest measured
    # success, latency as the tie-break -- with ties kept explicit.
    owners: dict[str, Any] = {}
    for state, entries in points.items():
        best_success = max(e["success_rate"] for e in entries)
        tied = [e for e in entries if e["success_rate"] >= best_success - 1e-12]
        winner = min(tied, key=lambda e: (e["p50_ms"], e["arm"]))
        owners[state] = {
            "best_success": best_success,
            "tied_arms": sorted(e["arm"] for e in tied),
            "fastest_of_tied": winner["arm"],
            "is_tie": len(tied) > 1,
        }
    return {
        "states": len(frontier),
        "frontier": dict(sorted(frontier.items())),
        "frontier_membership_counts": _membership_counts(frontier),
        "per_state_optimum": dict(sorted(owners.items())),
        "optimum_counts": dict(sorted(
            _counts_of(owners).items(), key=lambda kv: (-kv[1], kv[0])
        )),
    }


def _counts_of(owners: Mapping[str, Mapping[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = defaultdict(int)
    for entry in owners.values():
        if entry["is_tie"]:
            for arm in entry["tied_arms"]:
                counts[arm] += 1
        else:
            counts[entry["fastest_of_tied"]] += 1
    return dict(counts)


def _membership_counts(frontier: Mapping[str, Sequence[str]]) -> dict[str, int]:
    counts: dict[str, int] = defaultdict(int)
    for arms in frontier.values():
        for arm in arms:
            counts[arm] += 1
    return dict(sorted(counts.items(), key=lambda kv: (-kv[1], kv[0])))


def state_family_niches(stats: Mapping[str, ArmStats]) -> dict[str, Any]:
    """Which arm wins each state family, so a global verdict cannot erase a niche."""
    per_family: dict[str, dict[str, list[bool]]] = defaultdict(lambda: defaultdict(list))
    for arm in stats.values():
        if arm.family_kind not in ("single_model", "deterministic", "cascade"):
            continue
        for family, outcomes in arm.by_family.items():
            per_family[family][arm.arm].extend(outcomes)
    niches: dict[str, Any] = {}
    for family, arms in sorted(per_family.items()):
        ranked = sorted(
            (
                {
                    "arm": name,
                    "n": len(values),
                    "success_rate": sum(values) / len(values) if values else 0.0,
                }
                for name, values in arms.items()
            ),
            key=lambda r: (-r["success_rate"], r["arm"]),
        )
        best = ranked[0] if ranked else None
        niches[family] = {
            "winner": best["arm"] if best else None,
            "winner_success": best["success_rate"] if best else None,
            "n_arms": len(ranked),
            "ranking": ranked,
        }
    return niches


def pairwise(
    stats: Mapping[str, ArmStats],
    *,
    fusion_latency_ratio: float = 1.0,
    fusion_utility_regression: float = 0.02,
) -> list[dict[str, Any]]:
    """Section C's comparisons, read through the frozen gate's *shape*.

    The frozen thresholds are unchanged (``max_latency_ratio=1.0``,
    ``max_utility_regression=0.02``).  What changes is that each verdict now
    carries the number of observations behind it and the success intervals, so a
    "non-inferior" call on n=1 is visibly different from one on n>=10.
    """
    out: list[dict[str, Any]] = []
    for cheap_name, expensive_name, question in PAIRWISE_QUESTIONS:
        cheap = stats.get(cheap_name)
        expensive = stats.get(expensive_name)
        if cheap is None or expensive is None:
            out.append({"cheap": cheap_name, "expensive": expensive_name,
                        "question": question, "status": "not_measured"})
            continue
        lo_c, hi_c = wilson_interval(cheap.successes, cheap.n)
        lo_e, hi_e = wilson_interval(expensive.successes, expensive.n)
        p50_c = percentile(cheap.latencies, 50) or 0.0
        p50_e = percentile(expensive.latencies, 50) or 0.0
        ratio = (p50_c / p50_e) if p50_e > 0 else float("inf")
        quality_regression = (expensive.successes / expensive.n) - (cheap.successes / cheap.n)
        separated = lo_c > hi_e or lo_e > hi_c
        out.append(
            {
                "cheap": cheap_name,
                "expensive": expensive_name,
                "question": question,
                "status": "measured",
                "n": {"cheap": cheap.n, "expensive": expensive.n},
                "success": {
                    "cheap": cheap.successes / cheap.n,
                    "expensive": expensive.successes / expensive.n,
                    "cheap_ci95": [lo_c, hi_c],
                    "expensive_ci95": [lo_e, hi_e],
                    "separated": separated,
                },
                "latency_p50_ms": {"cheap": p50_c, "expensive": p50_e, "ratio": ratio},
                "gate_shaped_verdict": {
                    "frozen_max_latency_ratio": fusion_latency_ratio,
                    "frozen_max_utility_regression": fusion_utility_regression,
                    "cheap_is_faster": ratio <= fusion_latency_ratio,
                    "quality_regression": quality_regression,
                    "cheap_non_inferior": (
                        quality_regression <= fusion_utility_regression and separated
                    ),
                    "evidence_sufficient": cheap.n >= 3 and expensive.n >= 3,
                    "note": (
                        "gate-shaped read only: the frozen gate consumes utility, not "
                        "raw success, and is not re-run here"
                    ),
                },
            }
        )
    return out


def residency_report(stats: Mapping[str, ArmStats]) -> dict[str, Any]:
    """Cold / swap / resident / warm, kept apart, per arm and overall."""
    overall: dict[str, dict[str, Any]] = {}
    for arm in sorted(stats):
        st = stats[arm]
        overall[arm] = {
            k: {
                "n": v["n"],
                "p50_ms": v["p50_ms"],
                "p95_ms": v["p95_ms"],
            }
            for k, v in st.to_dict()["by_residency"].items()
        }
    class_totals: dict[str, list[float]] = defaultdict(list)
    for st in stats.values():
        for klass, values in st.by_residency.items():
            class_totals[klass].extend(values)
    return {
        "by_arm": overall,
        "by_class": {
            k: {
                "n": len(v),
                "p50_ms": percentile(v, 50),
                "p95_ms": percentile(v, 95),
                "mean_ms": statistics.fmean(v) if v else None,
            }
            for k, v in sorted(class_totals.items())
        },
    }


def load_run(run_dir: Path) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    freeze = json.loads((run_dir / "baseline" / "freeze.json").read_text(encoding="utf-8"))
    rows: list[dict[str, Any]] = []
    obs = run_dir / "observations.jsonl"
    if obs.is_file():
        with obs.open(encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line:
                    rows.append(json.loads(line))
    return freeze, rows


def build_report(run_dir: Path) -> dict[str, Any]:
    freeze, rows = load_run(run_dir)
    stats = collect(rows)
    pareto = pareto_frontier(stats)
    return {
        "schema": SCHEMA,
        "run_id": freeze.get("run_id"),
        "frozen_at": freeze.get("created_at"),
        "generated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "provenance": {
            "repos": {r["label"]: {"sha": r.get("sha"), "branch": r.get("branch"),
                                   "dirty": r.get("dirty_count")}
                      for r in freeze.get("repos", [])},
            "models": [
                {"model_id": m["model_id"], "hf_revision": m.get("hf_revision"),
                 "quant": m.get("quant"),
                 "gguf_sha256": (m.get("gguf") or {}).get("sha256")}
                for m in freeze.get("models", [])
            ],
            "fixtures": [
                {"owner": f.get("owner"), "kind": f.get("kind"), "path": f.get("path"),
                 "sha256": f.get("sha256"), "rows": f.get("rows")}
                for f in freeze.get("fixtures", [])
            ],
            "frozen_config": freeze.get("frozen_config"),
            "serving": (freeze.get("serving") or {}).get("document"),
        },
        "coverage": coverage(rows),
        "arms": {arm: st.to_dict() for arm, st in sorted(stats.items())},
        "residency": residency_report(stats),
        "dominance": dominated_arms(stats),
        "pareto": pareto,
        "pareto_note": (
            "raw (success_rate, p50_ms) non-domination among model arms; the "
            "deterministic compiler-only control is excluded because 0 ms would "
            "otherwise make it non-dominated everywhere"
        ),
        "state_family_niches": state_family_niches(stats),
        "pairwise": pairwise(stats),
        "n_observations": len(rows),
    }


def render_md(report: Mapping[str, Any]) -> str:
    cov = report["coverage"]
    lines = [
        "# Phase 1B decision artifact -- densified evidence",
        "",
        f"- schema: `{SCHEMA}`",
        f"- run id: `{report['run_id']}` (frozen {report['frozen_at']})",
        f"- observations: {report['n_observations']}",
        "",
        "## Provenance",
        "",
        "| repo | sha | branch | dirty |",
        "|---|---|---|---|",
    ]
    for label, info in sorted(report["provenance"]["repos"].items()):
        lines.append(
            f"| {label} | `{(info.get('sha') or '')[:12]}` | {info.get('branch')} "
            f"| {info.get('dirty')} |"
        )
    lines += ["", "| model | hf revision | quant | gguf sha256 |", "|---|---|---|---|"]
    for model in report["provenance"]["models"]:
        sha = model.get("gguf_sha256") or "-"
        lines.append(
            f"| {model['model_id']} | `{(model.get('hf_revision') or '')[:12]}` "
            f"| {model.get('quant')} | `{sha[:16]}` |"
        )
    lines += [
        "",
        "## (state, arm) coverage",
        "",
        f"- cells: {cov['cells']} over {cov['states']} states x {cov['arms']} arms",
        f"- cells with n>=2: {cov['cells_with_n_ge_2']}",
        f"- cells with n>=3: **{cov['cells_with_n_ge_3']}**",
        f"- cells with n>=10: {cov['cells_with_n_ge_10']}",
        f"- cell-size histogram: {json.dumps(cov['n_histogram'], sort_keys=True)}",
        "",
        "## Per-arm benchmark",
        "",
        "| arm | kind | n | states | success | 95% CI | dangerous | abstained | errors "
        "| p50 ms | p95 ms | decision p50 | load p50 | tokens out p50 |",
        "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for arm, s in report["arms"].items():
        lo, hi = s["success_ci95"]
        lat = s["latency_ms"]
        def f(v: float | None, digits: int = 0) -> str:
            return "-" if v is None else f"{v:.{digits}f}"
        lines.append(
            f"| {arm} | {s['kind']} | {s['n']} | {s['n_states']} | {s['successes']}/{s['n']} "
            f"| [{lo:.2f}, {hi:.2f}] | {s['dangerous_selected']} | {s['abstained']} "
            f"| {s['errors']} | {f(lat['p50'])} | {f(lat['p95'])} "
            f"| {f(s['decision_ms_p50'])} | {f(s['load_ms_p50'])} "
            f"| {f(s['tokens_out_p50'], 1)} |"
        )
    lines += [
        "",
        "## Residency distributions (cold vs warm kept apart)",
        "",
        "| class | n | p50 ms | p95 ms | mean ms |",
        "|---|---|---|---|---|",
    ]
    for klass, s in report["residency"]["by_class"].items():
        lines.append(
            f"| {klass} | {s['n']} | {f(s['p50_ms'])} | {f(s['p95_ms'])} | {f(s['mean_ms'])} |"
        )
    lines += [
        "",
        "## Dominance (interval-separated only)",
        "",
        "| arm | n | success CI | p50 ms | dominated by | strict quality gap | faster |",
        "|---|---|---|---|---|---|---|",
    ]
    for row in report["dominance"]:
        lo, hi = row["success_ci95"]
        lines.append(
            f"| {row['arm']} | {row['n']} | [{lo:.2f}, {hi:.2f}] "
            f"| {row['p50_ms']:.0f} | {row['dominated_by']} "
            f"| {row['strict_quality_gap']} | {row['faster']} |"
        )
    lines += [
        "",
        "## Section C pairwise comparisons",
        "",
        "| cheap | expensive | n (c/e) | success c/e | separated | p50 ratio | verdict |",
        "|---|---|---|---|---|---|---|",
    ]
    for row in report["pairwise"]:
        if row.get("status") != "measured":
            lines.append(f"| {row['cheap']} | {row['expensive']} | - | - | - | - | not measured |")
            continue
        v = row["gate_shaped_verdict"]
        verdict = (
            "cheap non-inferior" if v["cheap_non_inferior"]
            else ("cheap faster, quality regression" if v["cheap_is_faster"]
                  else "cheap not faster and not separated")
        )
        lines.append(
            f"| {row['cheap']} | {row['expensive']} "
            f"| {row['n']['cheap']}/{row['n']['expensive']} "
            f"| {row['success']['cheap']:.2f}/{row['success']['expensive']:.2f} "
            f"| {row['success']['separated']} | {row['latency_p50_ms']['ratio']:.2f} "
            f"| {verdict} |"
        )
    lines += [
        "",
        "## Per-state optimum (highest measured success; latency breaks ties)",
        "",
        "| arm | states won (ties counted for each tied arm) |",
        "|---|---|",
    ]
    for arm, count in report["pareto"]["optimum_counts"].items():
        lines.append(f"| {arm} | {count} |")
    lines += [
        "",
        "## Raw Pareto membership (non-dominated in success_rate x p50_ms)",
        "",
        f"_{report.get('pareto_note', '')}_",
        "",
        "| arm | states on frontier |",
        "|---|---|",
    ]
    for arm, count in report["pareto"]["frontier_membership_counts"].items():
        lines.append(f"| {arm} | {count} |")
    lines += [
        "",
        "## State-family niches",
        "",
        "| family | winner | success | arms compared |",
        "|---|---|---|---|",
    ]
    for family, niche in report["state_family_niches"].items():
        win = niche["winner_success"]
        win_text = "-" if win is None else f"{win:.2f}"
        lines.append(
            f"| {family} | {niche['winner']} | {win_text} | {niche['n_arms']} |"
        )
    return "\n".join(lines) + "\n"


def main(argv: Iterable[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--run-id", required=True)
    ap.add_argument("--run-dir", default=None)
    ap.add_argument("--out", default=None)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(list(argv) if argv is not None else None)

    run_dir = Path(args.run_dir) if args.run_dir else (RESULTS_ROOT / args.run_id)
    report = build_report(run_dir)
    out = Path(args.out) if args.out else (run_dir / "analysis")
    out.mkdir(parents=True, exist_ok=True)
    (out / "decision_artifact.json").write_text(
        json.dumps(report, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8"
    )
    (out / "decision_artifact.md").write_text(render_md(report), encoding="utf-8")
    print(f"# wrote {out}", file=sys.stderr)
    if args.json:
        print(json.dumps(
            {k: v for k, v in report.items() if k != "arms"},
            indent=2, sort_keys=True, default=str))
    else:
        print(render_md(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
