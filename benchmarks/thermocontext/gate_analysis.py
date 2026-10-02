"""Apply the already-frozen ThermoContext gates; never execute a sampler."""
from __future__ import annotations

import argparse
import collections
import hashlib
import json
from pathlib import Path
import shlex
import statistics
import sys
import zipfile

CANDIDATES = ("adaptive", "slack", "thrml_projected")
EPS = 1e-5


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def average(values):
    values = [value for value in values if value is not None]
    return statistics.mean(values) if values else None


def spread(values):
    values = [value for value in values if value is not None]
    return {"n": len(values), "min": min(values), "mean": average(values), "max": max(values)} if values else None


def expected_grid(rows, sampled):
    actual = {(row["task_seed"], row.get("seed")) for row in rows}
    expected = {(task, seed) for task in range(12) for seed in (range(8) if sampled else [None])}
    return {"complete": actual == expected and len(rows) == len(expected),
            "expected_rows": len(expected), "actual_rows": len(rows),
            "missing": sorted(expected - actual), "extra": sorted(actual - expected)}


def facts(rows):
    good = [row for row in rows if row.get("status") == "ok"]
    by_task = collections.defaultdict(list)
    for row in rows:
        by_task[row["task_id"]].append(row)
    successes = sum(bool(row.get("verified")) and bool(row.get("feasible")) for row in rows)
    return {
        "rows": len(rows), "valid_output_rows": len(good), "errors": len(rows) - len(good),
        "verified_feasible_count": successes,
        "verified_feasible_rate": successes / len(rows) if rows else None,
        "empty_count": sum(row["empty"] for row in good),
        "over_budget_count": sum(not row["feasible"] for row in good),
        "no_feasible_count": sum(row.get("no_feasible", False) for row in good),
        "max_degree": max((row.get("graph_degree", 0) for row in good), default=None),
        "degree_missing_count": sum(row.get("graph_degree") is None for row in good),
        "mean_original_energy": average([row["energy"] for row in good]),
        "mean_exact_gap": average([row.get("energy_gap") for row in good]),
        "mean_component_gap": average([row.get("component_energy_gap") for row in good]),
        "tokens_verified_feasible": spread([row["tokens"] for row in good if row["verified"] and row["feasible"]]),
        "minimum_task_successes": min((sum(bool(row.get("verified")) and bool(row.get("feasible")) for row in group)
                                       for group in by_task.values()), default=0),
        "task_successes": {task: sum(bool(row.get("verified")) and bool(row.get("feasible")) for row in group)
                           for task, group in sorted(by_task.items())},
        "total_wall_ms": spread([row.get("timings", {}).get("total_wall_ms") for row in good]),
        "sampling_ms": spread([row.get("timings", {}).get("sampling_execution_ms") for row in good]),
        "first_valid_found_but_selected_wrong": sum(row.get("first_valid_sample") is not None and not row["verified"] for row in good),
        "first_valid_censored": sum(row.get("sample_count", 0) > 0 and row.get("first_valid_sample") is None for row in good),
    }


def candidate_gate(rows, prototype_rows, greedy_rows, size, degree_limit):
    current, baseline, greedy = facts(rows), facts(prototype_rows), facts(greedy_rows)
    checks = {
        "complete_12_task_8_seed_grid": expected_grid(rows, True)["complete"],
        "no_missing_or_invalid_output": current["valid_output_rows"] == 96,
        "no_empty_output": current["empty_count"] == 0,
        "no_over_budget_output": current["over_budget_count"] == 0,
        "sparse_full_graph_degree_screen": current["max_degree"] is not None and current["max_degree"] <= degree_limit
                                            and current["degree_missing_count"] == 0,
        "mean_original_energy_no_worse_than_greedy": current["mean_original_energy"] is not None
            and greedy["mean_original_energy"] is not None
            and current["mean_original_energy"] <= greedy["mean_original_energy"] + EPS,
    }
    if size <= 16:
        checks.update({
            "prototype_grid_complete": expected_grid(prototype_rows, True)["complete"],
            "verified_count_at_least_prototype": current["verified_feasible_count"] >= baseline["verified_feasible_count"],
            "mean_exact_gap_no_worse_than_prototype": current["mean_exact_gap"] is not None
                and baseline["mean_exact_gap"] is not None
                and current["mean_exact_gap"] <= baseline["mean_exact_gap"] + EPS,
        })
    else:
        checks.update({"verified_rate_at_least_95_percent": current["verified_feasible_rate"] is not None
                       and current["verified_feasible_rate"] >= 0.95,
                       "at_least_7_of_8_successes_every_task": current["minimum_task_successes"] >= 7})
    return {"passes_empirical_screen": all(checks.values()), "checks": checks,
            "failed_checks": [name for name, value in checks.items() if not value], "observed": current}


def compare_controls(groups, root, size):
    result = {}
    for projected, original in (("thrml_projected", "thrml_static"), ("prototype_projected", "prototype")):
        if (size, projected) not in groups or (size, original) not in groups:
            continue
        before = {(row["task_seed"], row["seed"]): row for row in groups[size, original]}
        paired = []
        for after in groups[size, projected]:
            key = after["task_seed"], after["seed"]
            prior = before.get(key)
            if prior is None or after.get("status") != "ok" or prior.get("status") != "ok":
                continue
            with zipfile.ZipFile(root / after["trace_path"]) as projected_trace, zipfile.ZipFile(root / prior["trace_path"]) as original_trace:
                raw_identical = (projected_trace.read("raw_packed.npy") == original_trace.read("packed.npy")
                                 and projected_trace.read("raw_shape.npy") == original_trace.read("shape.npy"))
            paired.append({"raw_identical": raw_identical, "energy_delta": after["energy"] - prior["energy"],
                           "verification_delta": int(after["verified"]) - int(prior["verified"])})
        result[projected + "_vs_" + original] = {
            "paired_rows": len(paired), "all_raw_proposals_identical": bool(paired) and all(row["raw_identical"] for row in paired),
            "net_verified_delta": sum(row["verification_delta"] for row in paired),
            "mean_original_energy_delta": average([row["energy_delta"] for row in paired]),
            "interpretation": "Identical raw proposals isolate the deterministic host projection effect; they do not prove a THRML advantage."}
    if (size, "slack") in groups and (size, "slack_initial") in groups:
        initial = {(row["task_seed"], row["seed"]): row for row in groups[size, "slack_initial"]}
        pairs = [(row, initial[row["task_seed"], row["seed"]]) for row in groups[size, "slack"]
                 if row.get("status") == "ok" and (row["task_seed"], row["seed"]) in initial]
        result["slack_vs_initialization"] = {
            "paired_rows": len(pairs), "identical_selected_masks": sum(a["mask"] == b["mask"] for a, b in pairs),
            "net_verified_delta": sum(int(a["verified"]) - int(b["verified"]) for a, b in pairs),
            "mean_energy_delta": average([a["energy"] - b["energy"] for a, b in pairs]),
            "retained_item_change_fraction": spread([a["metadata"]["retained_item_change_fraction"] for a, _ in pairs]),
            "interpretation": "The initialization control reuses exactly the 64 already-generated initial packs; no additional sampling."}
    return result


def diagnose_adaptive(rows, cohort):
    output = {}
    for size in sorted({row["N"] for row in rows if row["arm"] == "adaptive"}):
        subset = [row for row in rows if row["N"] == size and row["arm"] == "adaptive" and row.get("status") == "ok"]
        stages = {}
        for stage in range(4):
            history = [(row, entry) for row in subset for entry in row["metadata"]["stage_history"] if entry["stage"] == stage]
            stages[str(stage)] = {
                "prices": spread([entry["price"] for _, entry in history]),
                "mean_tokens_over_budget": spread([entry["mean_tokens"] / row["budget"] for row, entry in history]),
                "empty_fraction": spread([entry["empty_fraction"] for _, entry in history]),
                "feasible_samples": spread([entry["feasible_samples"] for _, entry in history]),
                "update_reasons": dict(collections.Counter(entry["update"] for _, entry in history)),
            }
        failures = [row for row in subset if not row["verified"]]
        missing_counts = collections.Counter()
        required_counts = collections.Counter()
        failure_rows = []
        for row in failures:
            missing = sorted(set(cohort[row["task_id"]]["required_ids"]) - set(row["ids"]))
            missing_counts[len(missing)] += 1
            required_counts.update(missing)
            failure_rows.append({"task_id": row["task_id"], "seed": row["seed"], "tokens": row["tokens"],
                                 "budget": row["budget"], "energy": row["energy"],
                                 "component_energy_gap": row.get("component_energy_gap"),
                                 "first_valid_sample": row.get("first_valid_sample"), "missing_required_ids": missing})
        output[str(size)] = {
            "failures": len(failures), "first_valid_censored_failures": sum(row.get("first_valid_sample") is None for row in failures),
            "found_valid_but_selected_wrong": sum(row.get("first_valid_sample") is not None for row in failures),
            "empty_selected_failures": sum(row["empty"] for row in failures),
            "missing_required_count_histogram": dict(missing_counts), "missing_required_id_counts": dict(required_counts),
            "stage_diagnostics": stages, "failure_rows": failure_rows,
            "limit": "Required IDs are inspected only by this post-experiment diagnosis. No proposer parameters or sampling budgets changed."}
    return output


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("results", type=Path)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--audit", type=Path)
    args = parser.parse_args()
    root = args.results.resolve()
    provenance = json.loads((root / "provenance.json").read_text())
    summary = json.loads((root / "summary.json").read_text())  # Completion is mandatory.
    if summary["result_rows_sha256"] != digest(root / "rows.jsonl"):
        raise ValueError("Completed summary does not bind current row file")
    if summary["protocol_sha256"] != provenance["protocol_sha256"] or summary["mode"] != "full":
        raise ValueError("Expected matching frozen protocol and full cohort")
    rows = [json.loads(line) for line in (root / "rows.jsonl").read_text().splitlines()]
    cohort = {task["task_id"]: task for task in json.loads((root / "cohort.json").read_text())}
    groups = collections.defaultdict(list)
    for row in rows:
        groups[row["N"], row["arm"]].append(row)
    protocol = provenance["protocol"]
    sizes = sorted({row["N"] for row in rows})
    if sizes != provenance["arm_config"]["sizes"]:
        raise ValueError("A configured cohort size is missing or unexpected")
    expected_candidates = [arm for arm in CANDIDATES if arm in provenance["arm_config"]["arms"]]
    gates = {}
    for size in sizes:
        gates[str(size)] = {arm: candidate_gate(groups[size, arm], groups[size, "prototype"], groups[size, "greedy"],
                                              size, protocol["sparsity"]["preregistered_max_degree_screen"])
                           for arm in expected_candidates}
    eligible = [arm for arm in expected_candidates if all(gates[str(size)][arm]["passes_empirical_screen"] for size in sizes)]
    audit_path = args.audit or root / "independent-audit.json"
    audit = json.loads(audit_path.read_text()) if audit_path.exists() else None
    if audit is not None and (
        audit.get("results_sha256") != summary["result_rows_sha256"]
        or audit.get("rows_audited") != len(rows)
        or audit.get("frozen_prototype_sha256") != protocol["frozen"]["prototype_sha256"]
    ):
        raise ValueError("Independent audit is not bound to these exact rows and frozen source")
    audit_pass = bool(audit and audit.get("all_row_checks_pass") and audit.get("complete_present_arm_grids"))
    is_scale = any(size >= 32 for size in sizes)
    stop = is_scale and len(eligible) != len(expected_candidates)
    next_size = (32 if max(sizes) <= 16 else {32: 64, 64: 128}.get(max(sizes)))
    if stop or not eligible:
        next_size = None
    command = None
    if next_size:
        arms = ["champion", "topk", "greedy", "pair_greedy", "component_dp", "prototype", "thrml_static", *eligible]
        if "thrml_projected" in eligible:
            arms += ["prototype_projected", "random_projected"]
        command = " ".join(shlex.quote(part) for part in [
            "/workspace/hermes-factory/venvs/thermocontext/bin/python", "-m", "benchmarks.thermocontext.next_wave",
            "--mode", "full", "--sizes", str(next_size), "--tasks", "12", "--seeds", "8", "--arms", *arms,
            "--out", f"benchmarks/thermocontext/results/next-wave/scale{next_size}-NEW", "--timing-mode", "contended"])
    parity = {}
    for size in sizes:
        original, thrml = facts(groups[size, "prototype"]), facts(groups[size, "thrml_static"])
        parity[str(size)] = {"prototype": original, "real_thrml": thrml,
                            "verified_count_nondecreasing": thrml["verified_feasible_count"] >= original["verified_feasible_count"],
                            "mean_original_energy_nondecreasing_quality": thrml["mean_original_energy"] <= original["mean_original_energy"] + EPS,
                            "scope": "Empirical frozen-cohort comparison; not bitwise RNG equivalence or certified population noninferiority."}
    report = {
        "schema": "thermocontext.frozen-gates.v1", "analysis_source_sha256": digest(__file__),
        "results_path": str(root), "results_sha256": digest(root / "rows.jsonl"),
        "source_revision": provenance["git_revision"], "protocol_sha256": provenance["protocol_sha256"],
        "audit": {"path": str(audit_path), "present": audit is not None, "passed": audit_pass,
                  "sha256": digest(audit_path) if audit else None},
        "sizes": sizes, "candidate_gates": gates, "empirically_eligible_candidates": eligible,
        "promotion_ready": audit_pass and bool(eligible) and not stop,
        "promotion_scope": "Advance only to the next experiment stage; never runtime activation or a product claim",
        "stop_entire_scale_ladder": stop,
        "larger_sizes_not_run": [n for n in (32, 64, 128) if n > max(sizes)] if stop else [],
        "next_scale_size": next_size, "next_scale_command": command,
        "execute_next_scale_only_after_independent_audit": True,
        "fixed_hamiltonian_thrml_parity": parity,
        "all_arm_metrics": {str(size): {arm: facts(group) for (n, arm), group in sorted(groups.items()) if n == size} for size in sizes},
        "projection_and_initialization_attribution": {str(size): compare_controls(groups, root, size) for size in sizes},
        "adaptive_diagnosis": diagnose_adaptive(rows, cohort),
        "frontier_limits": [
            "Compare pair_greedy and component_dp at identical task IDs; deterministic rows are not duplicated into independent observations.",
            "Gate passing is not evidence of incremental value over stronger deterministic or random-plus-projection controls.",
            "All output tokens are synthetic item costs, not consumed Hermes/model tokens. Failing low-token packs are not a savings frontier.",
            "Total experiment wall includes diagnostics/artifact writing; compare proposal/selection components as well before deployment claims.",
            "No TSU hardware speed/energy claims; no parameters changed and no extra samples generated for this analysis.",
        ],
    }
    with args.out.open("x") as stream:
        json.dump(report, stream, indent=2, sort_keys=True, allow_nan=False)
        stream.write("\n")
    print(json.dumps({"eligible": eligible, "audit_pass": audit_pass, "stop_ladder": stop, "next_size": next_size}))


if __name__ == "__main__":
    main()
