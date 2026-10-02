"""Post-experiment, oracle-labelled diagnosis of saved adaptive trajectories.

This command performs no sampling and cannot alter a proposer or verifier.
"""
import argparse
import hashlib
import json
from pathlib import Path
import sys

import numpy as np


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def distribution(values):
    values = np.asarray(values, dtype=float)
    return {"n": len(values), "min": float(values.min()), "mean": float(values.mean()),
            "median": float(np.median(values)), "max": float(values.max())} if len(values) else None


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("results", type=Path)
    parser.add_argument("--repo", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()
    sys.path.insert(0, str(args.repo.resolve()))
    from benchmarks.thermocontext.audit_next_wave import independent_metrics, load_frozen
    frozen = load_frozen()
    root = args.results.resolve()
    manifest = json.loads((root / "provenance.json").read_text())
    summary = json.loads((root / "summary.json").read_text())
    if summary["result_rows_sha256"] != digest(root / "rows.jsonl"):
        raise ValueError("Rows differ from completed experiment")
    rows = [json.loads(line) for line in (root / "rows.jsonl").read_text().splitlines()]
    rows = [row for row in rows if row["arm"] == "adaptive" and row["status"] == "ok"]
    stage_rows = {str(stage): [] for stage in range(4)}
    failures = []
    for row in rows:
        task = frozen.make_task(row["N"], row["task_seed"])
        required_indices = [index for index, item in enumerate(task.items) if item.id in task.required_ids]
        path = root / row["trace_path"]
        if digest(path) != row["trace_sha256"]:
            raise ValueError("Trace hash mismatch")
        with np.load(path, allow_pickle=False) as artifact:
            shape = artifact["shape"]
            trajectory = np.unpackbits(artifact["packed"], bitorder="little", count=int(np.prod(shape))).reshape(shape).astype(bool)
        flat = trajectory.reshape(-1, len(task.items))
        metrics = independent_metrics(task, flat)
        valid = metrics["verified"] & metrics["feasible"]
        all_required = flat[:, required_indices].all(axis=1)
        histories = row["metadata"]["stage_history"]
        cursor = 0
        for stage, history in enumerate(histories):
            count = history["retained_samples"]
            region = slice(cursor, cursor + count)
            stage_rows[str(stage)].append({
                "task_id": row["task_id"], "seed": row["seed"], "samples": count,
                "price": history["price"],
                "budget_feasible_count": int(metrics["feasible"][region].sum()),
                "all_required_count": int(all_required[region].sum()),
                "all_required_and_feasible_count": int((all_required[region] & metrics["feasible"][region]).sum()),
                "verified_feasible_count": int(valid[region].sum()),
                "empty_count": int(metrics["empty"][region].sum()),
            })
            cursor += count
        if cursor != len(flat):
            raise ValueError("Adaptive stage counts do not partition retained trace")
        if not row["verified"]:
            best_valid = float(metrics["energy"][valid].min()) if valid.any() else None
            failures.append({"task_id": row["task_id"], "seed": row["seed"],
                             "selected_energy": row["energy"], "best_valid_sample_energy": best_valid,
                             "valid_minus_selected_energy": None if best_valid is None else best_valid - row["energy"],
                             "valid_sample_count": int(valid.sum()),
                             "failure_type": "valid_found_but_worse_original_energy" if valid.any() else "no_valid_feasible_draw"})
    aggregate = {}
    for stage, group in stage_rows.items():
        total = sum(row["samples"] for row in group)
        aggregate[stage] = {"row_count": len(group), "retained_samples": total,
                            "price": distribution([row["price"] for row in group])}
        for metric in ("budget_feasible_count", "all_required_count", "all_required_and_feasible_count", "verified_feasible_count", "empty_count"):
            count = sum(row[metric] for row in group)
            aggregate[stage][metric] = count
            aggregate[stage][metric.replace("_count", "_fraction")] = count / total
    report = {
        "schema": "thermocontext.saved-trace-adaptive-diagnosis.v1",
        "analysis_source_sha256": digest(__file__), "results_sha256": digest(root / "rows.jsonl"),
        "audit_module_sha256": digest(args.repo / "benchmarks/thermocontext/audit_next_wave.py"),
        "source_revision": manifest["git_revision"], "protocol_sha256": manifest["protocol_sha256"],
        "rows": len(rows), "new_sampler_calls": 0, "new_proposals": 0,
        "stage_aggregate": aggregate, "stage_rows": stage_rows,
        "failure_rows": failures,
        "valid_minus_selected_energy_in_wrong_outputs": distribution([row["valid_minus_selected_energy"] for row in failures
                                                                       if row["valid_minus_selected_energy"] is not None]),
        "interpretation_limits": [
            "Oracle labels are post-experiment diagnosis only; they never alter proposals or final selection.",
            "A coherent-set disappearance after a price change is observed here; no additional causal mutation was run.",
            "Original score is independently recomputed in float64 from frozen float32 coefficients; small roundoff differences are expected.",
            "Sampling budget remains exactly 160 sweeps x64 chains,5120 retained draws per row.",
        ],
    }
    with args.out.open("x") as stream:
        json.dump(report, stream, indent=2, sort_keys=True, allow_nan=False)
        stream.write("\n")
    print(json.dumps({"rows": len(rows), "failure_rows": len(failures), "new_sampler_calls": 0}))


if __name__ == "__main__":
    main()
