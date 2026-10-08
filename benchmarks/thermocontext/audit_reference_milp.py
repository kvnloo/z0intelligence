"""Post-run independent MILP oracle; never a sampler or sampler initializer.

The sparse original pair objective is linearized with binary y_ij=x_i*x_j.
This is an audit-only CPU optimization, including a global linear knapsack
constraint. It is not a proposed THRML/TSU budget implementation. Its role is
to independently check the component-DP reference on the completed cohort.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import time
import warnings

import numpy as np
import scipy
from scipy.optimize import Bounds, LinearConstraint, milp
from scipy.sparse import coo_matrix

from benchmarks.thermocontext import audit_next_wave as audit


def solve_reference(task, time_limit=5.0):
    if not 0 < time_limit <= 5:
        raise ValueError("Post-run audit is bounded to at most five seconds per task")
    started = time.perf_counter()
    n = len(task.items)
    edges = [(i, j, float(task.J[i, j])) for i in range(n)
             for j in range(i + 1, n) if task.J[i, j]]
    objective = np.concatenate([task.unary().astype(np.float64), [w for _, _, w in edges]])
    data, row_index, col_index = [], [], []
    lower, upper = [], []

    def constraint(terms, minimum, maximum):
        row = len(lower)
        for column, weight in terms:
            row_index.append(row)
            col_index.append(column)
            data.append(weight)
        lower.append(minimum)
        upper.append(maximum)

    constraint([(i, item.tokens) for i, item in enumerate(task.items)], -np.inf, task.budget)
    for k, (i, j, _) in enumerate(edges):
        y = n + k
        constraint([(y, 1), (i, -1)], -np.inf, 0)
        constraint([(y, 1), (j, -1)], -np.inf, 0)
        constraint([(y, 1), (i, -1), (j, -1)], -1, np.inf)
    matrix = coo_matrix((data, (row_index, col_index)), shape=(len(lower), len(objective))).tocsc()
    options = {"time_limit": time_limit, "mip_rel_gap": 0.0, "presolve": True, "threads": 1}
    with warnings.catch_warnings(record=True) as warning_records:
        warnings.simplefilter("always")
        result = milp(objective, integrality=np.ones(len(objective)),
                      bounds=Bounds(np.zeros(len(objective)), np.ones(len(objective))),
                      constraints=LinearConstraint(matrix, lower, upper), options=options)
    record = {
        "task_id": task.task_id, "N": n, "solver_status": int(result.status),
        "solver_message": result.message, "solver_success": bool(result.success),
        "options": options, "scipy_version": scipy.__version__,
        "warnings": [str(item.message) for item in warning_records],
        "binary_variables": len(objective), "linear_constraints": len(lower),
        "cpu_affinity": sorted(os.sched_getaffinity(0)) if hasattr(os, "sched_getaffinity") else None,
        "total_wall_ms": (time.perf_counter() - started) * 1000,
        "mip_gap": getattr(result, "mip_gap", None),
        "mip_dual_bound": getattr(result, "mip_dual_bound", None),
        "mip_node_count": getattr(result, "mip_node_count", None),
        "solver_objective": result.fun, "audit_errors": [],
    }
    if result.x is None:
        record["audit_errors"].append("No integer solution returned")
        record["certified_to_reported_numerical_tolerance"] = False
        return record
    rounded = np.rint(result.x).astype(bool)
    if np.max(np.abs(result.x - rounded.astype(float))) > 1e-6:
        record["audit_errors"].append("Solver solution is not binary within tolerance")
    if any(rounded[n + k] != (rounded[i] and rounded[j]) for k, (i, j, _) in enumerate(edges)):
        record["audit_errors"].append("Linearized product constraint violated")
    mask = rounded[:n]
    metrics = audit.independent_metrics(task, mask)
    frozen_energy = task.energy(mask)
    record.update(mask=mask.astype(int).tolist(), tokens=int(metrics["tokens"][0]),
                  verified=bool(metrics["verified"][0]), original_float32_energy=frozen_energy,
                  independent_float64_energy=float(metrics["energy"][0]))
    if not metrics["feasible"][0]:
        record["audit_errors"].append("Rounded solution violates token budget")
    if abs(frozen_energy - result.fun) > audit.TOL:
        record["audit_errors"].append("MILP objective differs from unchanged float32 scorer")
    if not result.success or result.status != 0 or record["mip_gap"] is None or record["mip_gap"] > 1e-8:
        record["audit_errors"].append("Optimality was not established within fixed time/gap bounds")
    record["certified_to_reported_numerical_tolerance"] = not record["audit_errors"]
    return record


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run", type=Path)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    path = args.run / "rows.jsonl" if args.run.is_dir() else args.run
    rows = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
    frozen = audit.load_frozen()
    references = {}
    disagreements = []
    for row in rows:
        key = (row["N"], row["task_seed"])
        if key not in references:
            references[key] = solve_reference(frozen.make_task(*key))
        reference = references[key]
        if not reference["certified_to_reported_numerical_tolerance"]:
            continue
        optimum = reference["original_float32_energy"]
        if row.get("component_reference_energy") is None or abs(row["component_reference_energy"] - optimum) > audit.TOL:
            disagreements.append({"task_id": row["task_id"], "arm": row["arm"], "seed": row.get("seed"),
                                  "error": "Component reference differs from independent MILP optimum"})
        if row["arm"] == "component_dp" and abs(row["energy"] - optimum) > audit.TOL:
            disagreements.append({"task_id": row["task_id"], "arm": row["arm"], "error": "Component-DP output is not exact"})
        if row.get("feasible") and row["energy"] < optimum - audit.TOL:
            disagreements.append({"task_id": row["task_id"], "arm": row["arm"], "error": "Feasible row allegedly beats certified optimum"})
    report = {
        "schema": "thermocontext.postrun-milp-reference-audit.v1",
        "rows_sha256": audit.sha256(path), "auditor_sha256": audit.sha256(__file__),
        "frozen_sha256": audit.FROZEN_SHA256, "energy_tolerance": audit.TOL,
        "tasks": list(references.values()), "disagreements": disagreements,
        "pass": not disagreements and all(r["certified_to_reported_numerical_tolerance"] for r in references.values()),
        "limits": [
            "Independent post-run CPU oracle only; no solver result enters any sampler.",
            "MILP uses a global linear budget constraint, not a proposed sparse THRML/TSU encoding.",
            "Solver optimality is numerical, with reported gap and original float32 rescoring tolerance; no exact-arithmetic proof.",
        ],
    }
    with args.out.open("x") as stream:
        json.dump(report, stream, indent=2, allow_nan=False)
        stream.write("\n")
    print(json.dumps({"tasks": len(references), "rows": len(rows), "pass": report["pass"], "disagreements": len(disagreements)}))
    return 0 if report["pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
