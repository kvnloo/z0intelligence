"""Independent arithmetic/trace audit; never called by a selector.

The source generator is frozen. This audit independently implements subset
energy, token count, the frozen fact oracle and exhaustive enumeration. It
does not turn synthetic cohort evidence into a production claim.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import sys

import numpy as np

FROZEN_SHA256 = "69e1b4537858c06f87968093c41f11118af8d842c95edd49cd71897767044a00"
TOL = 1e-5


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def load_frozen(path=None):
    path = Path(path or Path(__file__).with_name("phase_a_prototype.py"))
    if sha256(path) != FROZEN_SHA256:
        raise ValueError("Frozen prototype hash changed")
    spec = importlib.util.spec_from_file_location("thermocontext_frozen_audit", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def independent_metrics(task, masks):
    """Vectorized oracle arithmetic, independently of Task.energy/verify."""
    x = np.asarray(masks, dtype=bool)
    if x.ndim == 1:
        x = x[None, :]
    if x.ndim != 2 or x.shape[1] != len(task.items):
        raise ValueError("Malformed item mask")
    token_weights = np.array([it.tokens for it in task.items], dtype=np.int64)
    # Match the frozen unary rounding, then use independent float64 arithmetic.
    unary = np.array([
        2.50 * it.tokens / task.budget - 0.80 * it.relevance
        for it in task.items
    ], dtype=np.float32).astype(np.float64)
    energies = x @ unary
    for i in range(len(task.items)):
        for j in range(i + 1, len(task.items)):
            coefficient = (float(task.J[i, j]) + float(task.J[j, i])) / 2
            if coefficient:
                energies += coefficient * x[:, i] * x[:, j]
    for i in range(len(task.items)):
        if task.J[i, i]:
            energies += 0.5 * float(task.J[i, i]) * x[:, i]
    tokens = x @ token_weights
    verified = []
    required = set(task.required_ids)
    for mask in x:
        selected = [item for item, take in zip(task.items, mask) if take]
        facts = {}
        for item in selected:
            facts.update(item.facts)
        sufficient = (
            required.issubset({it.id for it in selected})
            and facts.get("_insufficient") is not True
            and {"rfc_revision", "superseded", "approved"}.issubset(facts)
        )
        if sufficient:
            answer = (
                "SUPERSEDED"
                if facts["superseded"] is True or facts["approved"] is not True
                else f"rev-{int(facts['rfc_revision'])}"
            )
            sufficient = answer == task.expected
        verified.append(sufficient)
    return {
        "energy": energies,
        "tokens": tokens,
        "feasible": tokens <= task.budget,
        "verified": np.array(verified, dtype=bool),
        "empty": x.sum(axis=1) == 0,
    }


def independent_exact(task):
    n = len(task.items)
    if n > 16:
        raise ValueError("Exact enumeration is intentionally bounded to N <= 16")
    indices = np.arange(1 << n, dtype=np.uint32)
    masks = ((indices[:, None] >> np.arange(n)) & 1).astype(bool)
    metrics = independent_metrics(task, masks)
    energies = np.where(metrics["feasible"], metrics["energy"], np.inf)
    winner = int(np.argmin(energies))
    return masks[winner], float(energies[winner])


def load_trace(path, row, root):
    root = Path(root).resolve()
    path = (root / path).resolve()
    if not path.is_relative_to(root):
        raise ValueError("Trace path escapes result directory")
    if sha256(path) != row["trace_sha256"]:
        raise ValueError("Trace hash mismatch")
    with np.load(path, allow_pickle=False) as trace:
        shape = tuple(int(v) for v in trace["shape"])
        if len(shape) not in (2, 3) or shape[-1] != row["N"]:
            raise ValueError("Trace shape does not match item dimension")
        bits = np.unpackbits(trace["packed"], bitorder="little")
        size = int(np.prod(shape))
        if size > len(bits) or len(bits) - size >= 8:
            raise ValueError("Packed trace length mismatch")
        if "finalmask" in trace and not np.array_equal(trace["finalmask"], row["mask"]):
            raise ValueError("Trace finalmask disagrees with row")
        return bits[:size].reshape(shape).reshape(-1, row["N"]).astype(bool)


def audit_auxiliary(row, task, root):
    """Reconstruct the slack QUBO and audit every saved auxiliary state."""
    errors = []
    root = Path(root).resolve()
    graph_path = (root / row["auxiliary_graph_path"]).resolve()
    trace_path = (root / row["auxiliary_trace_path"]).resolve()
    if not graph_path.is_relative_to(root) or not trace_path.is_relative_to(root):
        return ["Auxiliary path escapes result directory"]
    if sha256(trace_path) != row["auxiliary_trace_sha256"]:
        return ["Auxiliary trace hash mismatch"]
    if row.get("auxiliary_graph_file_sha256") and sha256(graph_path) != row["auxiliary_graph_file_sha256"]:
        return ["Auxiliary graph file hash mismatch"]
    graph = json.loads(graph_path.read_text())
    canonical = json.dumps(graph, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    if hashlib.sha256(canonical).hexdigest() != row["auxiliary_graph_sha256"]:
        return ["Auxiliary graph canonical hash mismatch"]
    n, v = graph["n_items"], graph["n_nodes"]
    if n != len(task.items) or graph["costs"] != [it.tokens for it in task.items] or graph["budget"] != task.budget:
        errors.append("Auxiliary public problem differs from frozen task")
    if not np.allclose(graph["base_unary"], task.unary(), rtol=0, atol=TOL):
        errors.append("Auxiliary base unary differs from frozen objective")

    def edges_to_map(edges):
        result = {}
        for edge in edges:
            if isinstance(edge, dict):
                i, j, weight = edge["i"], edge["j"], edge["coefficient"]
            else:
                i, j, weight = edge
            if not 0 <= i < j < v or (i, j) in result:
                raise ValueError("Malformed or duplicate auxiliary edge")
            if weight:
                result[(i, j)] = float(weight)
        return result

    base_edges = edges_to_map(graph["base_edges"])
    expected_base = {(i, j): float(task.J[i, j]) for i in range(n) for j in range(i + 1, n) if task.J[i, j]}
    if base_edges != expected_base:
        errors.append("Auxiliary base edges differ from frozen objective")
    expected_unary = np.zeros(v)
    expected_unary[:n] = graph["base_unary"]
    expected_edges = dict(base_edges)
    expected_constant = 0.0
    penalty = graph["penalty"]
    if not np.isfinite(penalty) or penalty <= 0:
        errors.append("Auxiliary penalty is not positive and finite")
    for cell in graph["cells"]:
        constant = cell["constant"]
        expected_constant += penalty * constant ** 2
        for index, coefficient in cell["terms"]:
            if not 0 <= index < v:
                raise ValueError("Auxiliary cell index out of range")
            expected_unary[index] += penalty * (coefficient ** 2 + 2 * constant * coefficient)
        for position, (a, weight_a) in enumerate(cell["terms"]):
            for b, weight_b in cell["terms"][position + 1:]:
                edge = tuple(sorted((a, b)))
                expected_edges[edge] = expected_edges.get(edge, 0.0) + 2 * penalty * weight_a * weight_b
    expected_edges = {edge: weight for edge, weight in expected_edges.items() if weight}
    actual_edges = edges_to_map(graph["expanded_edges"])
    if expected_edges.keys() != actual_edges.keys() or any(abs(weight - actual_edges.get(edge, 0)) > 1e-7 for edge, weight in expected_edges.items()):
        errors.append("Expanded auxiliary edges disagree with local constraints")
    if not np.allclose(expected_unary, graph["expanded_unary"], rtol=0, atol=1e-7):
        errors.append("Expanded auxiliary unary disagrees with local constraints")
    if abs(expected_constant - graph["constant"]) > 1e-7:
        errors.append("Expanded auxiliary constant disagrees with local constraints")
    degrees = np.zeros(v, dtype=int)
    for i, j in actual_edges:
        degrees[i] += 1
        degrees[j] += 1
    metadata = row.get("metadata", {})
    if degrees.max(initial=0) > 32:
        errors.append("Full auxiliary graph exceeds preregistered degree screen")
    for key, value in (("node_count", v), ("edge_count", len(actual_edges)),
                       ("max_degree", int(degrees.max(initial=0))),
                       ("auxiliary_max_degree", int(degrees[n:].max(initial=0)))):
        if key in metadata and metadata[key] != value:
            errors.append(f"Auxiliary {key} metadata mismatch")
    with np.load(trace_path, allow_pickle=False) as trace:
        shape = tuple(int(k) for k in trace["shape"])
        init_shape = tuple(int(k) for k in trace["initial_shape"])
        if len(shape) != 3 or shape[-1] != v or init_shape != (shape[1], v):
            raise ValueError("Auxiliary trace/initial shape mismatch")
        states = np.unpackbits(trace["packed"], bitorder="little")[:int(np.prod(shape))].reshape(shape).astype(bool)
        initial = np.unpackbits(trace["initial_packed"], bitorder="little")[:int(np.prod(init_shape))].reshape(init_shape).astype(bool)
    flat = states.reshape(-1, v).astype(np.int64)
    initial_integer = initial.astype(np.int64)
    valid = np.ones(len(flat), dtype=bool)
    init_valid = np.ones(len(initial), dtype=bool)
    for cell in graph["cells"]:
        residual = np.full(len(flat), cell["constant"], dtype=np.int64)
        init_residual = np.full(len(initial), cell["constant"], dtype=np.int64)
        for index, weight in cell["terms"]:
            residual += weight * flat[:, index]
            init_residual += weight * initial_integer[:, index]
        valid &= residual == 0
        init_valid &= init_residual == 0
    if not init_valid.all():
        errors.append("Slack initialization violates local constraints")
    for key, value in (("constraint_valid_sample_fraction", float(valid.mean())),
                       ("retained_item_change_fraction", float(np.any(states[..., :n] != initial[None, :, :n], axis=-1).mean()))):
        if key in metadata and abs(metadata[key] - value) > 1e-12:
            errors.append(f"Auxiliary {key} metadata mismatch")
    if row.get("trace_path"):
        items = load_trace(row["trace_path"], row, root)
        if not np.array_equal(items, states[..., :n].reshape(-1, n)):
            errors.append("Item trace differs from full auxiliary trace projection")
    return errors


def audit_row(row, task, *, exact_energy=None, root=None):
    errors = []
    required = {"N", "task_id", "arm", "mask", "tokens", "budget", "energy", "verified", "feasible", "empty"}
    missing = required - row.keys()
    if missing:
        return ["Missing row fields: " + ", ".join(sorted(missing))]
    raw_mask = np.asarray(row["mask"])
    if raw_mask.shape != (len(task.items),) or not np.isin(raw_mask, (0, 1)).all():
        return ["Invalid returned binary mask"]
    mask = raw_mask.astype(bool)
    metrics = independent_metrics(task, mask)
    if row["N"] != len(task.items) or row["task_id"] != task.task_id or row["budget"] != task.budget:
        errors.append("Task identity or budget mismatch")
    for field in ("tokens", "verified", "feasible", "empty"):
        if row[field] != metrics[field][0]:
            errors.append(f"Returned mask disagrees with {field}")
    if not np.isfinite(row["energy"]) or abs(row["energy"] - metrics["energy"][0]) > TOL:
        errors.append("Returned mask disagrees with frozen energy")
    if "ids" in row:
        ids = sorted(it.id for it, selected in zip(task.items, mask) if selected)
        if sorted(row["ids"]) != ids:
            errors.append("Returned ids disagree with mask")
    if exact_energy is not None:
        if row.get("exact_energy") is None or abs(row["exact_energy"] - exact_energy) > TOL:
            errors.append("Exact reference disagrees with independent enumeration")
        if row.get("energy_gap") is None or abs(row["energy_gap"] - (row["energy"] - exact_energy)) > TOL:
            errors.append("Energy gap arithmetic mismatch")
        if metrics["feasible"][0] and row["energy"] < exact_energy - TOL:
            errors.append("Feasible output allegedly beats exhaustive optimum")
        if row["arm"] == "component_dp" and abs(row["energy"] - exact_energy) > TOL:
            errors.append("Component-DP claimed exact output differs from independent exhaustive optimum")
        if row.get("component_reference_energy") is not None and abs(row["component_reference_energy"] - exact_energy) > TOL:
            errors.append("Component-DP reference differs from independent exhaustive optimum")
    if row.get("trace_path") and root is not None:
        try:
            samples = load_trace(row["trace_path"], row, root)
            trace_metrics = independent_metrics(task, samples)
            feasible = trace_metrics["feasible"]
            if len(samples) != row.get("sample_count"):
                errors.append("Retained trace sample count mismatch")
            if len(samples) > 5120:
                errors.append("Retained samples exceed preregistered per-seed budget")
            valid_indices = np.flatnonzero(feasible & trace_metrics["verified"])
            first_valid = int(valid_indices[0]) + 1 if len(valid_indices) else None
            if row.get("first_valid_sample") != first_valid:
                errors.append("First-valid sample differs from independent trace scan")
            if bool(row.get("no_feasible")) != (not bool(feasible.any())):
                errors.append("no_feasible disagrees with trace")
            if feasible.any():
                best_energy = float(trace_metrics["energy"][feasible].min())
                if abs(row["energy"] - best_energy) > TOL:
                    errors.append("Output is not minimum frozen-energy feasible retained draw")
                if not np.any(np.all(samples == mask, axis=1)):
                    errors.append("Selected pack never occurred in retained trace")
            elif not metrics["empty"][0]:
                errors.append("No feasible samples but nonempty fallback was returned")
        except (ValueError, KeyError, OSError) as exc:
            errors.append(f"Trace audit error: {exc}")
    elif row.get("seed") is not None:
        errors.append("Stochastic row missing auditable retained trace")
    if row.get("auxiliary_trace_path") and root is not None:
        try:
            errors.extend(audit_auxiliary(row, task, root))
        except (ValueError, KeyError, OSError, TypeError) as exc:
            errors.append(f"Auxiliary audit error: {exc}")
    return errors


def audit_results(data, root, frozen=None):
    frozen = frozen or load_frozen()
    rows = data["rows"]
    cache = {}
    failures = []
    groups = {}
    seen = set()
    for index, row in enumerate(rows):
        identity = (row["N"], row["task_seed"], row["arm"], row.get("seed"))
        if identity in seen:
            failures.append({"row": index, "identity": identity, "errors": ["Duplicate result row"]})
        seen.add(identity)
        key = identity[:2]
        if key not in cache:
            task = frozen.make_task(*key)
            reference = independent_exact(task)[1] if key[0] <= 16 else None
            cache[key] = (task, reference)
        task, reference = cache[key]
        errors = audit_row(row, task, exact_energy=reference, root=root)
        if errors:
            failures.append({"row": index, "identity": identity, "errors": errors})
        groups.setdefault((row["N"], row["arm"]), set()).add((row["task_seed"], row.get("seed")))
    grids = []
    for (n, arm), actual in sorted(groups.items()):
        stochastic = any(seed is not None for _, seed in actual)
        expected = {(task, seed) for task in range(12) for seed in (range(8) if stochastic else [None])}
        missing, extra = expected - actual, actual - expected
        grids.append({"N": n, "arm": arm, "complete": not missing and not extra,
                      "missing": sorted(missing), "extra": sorted(extra)})
    return {
        "schema": "thermocontext.independent-audit.v1",
        "frozen_prototype_sha256": FROZEN_SHA256,
        "rows_audited": len(rows),
        "exact_tasks_independently_enumerated": sum(n <= 16 for n, _ in cache),
        "all_row_checks_pass": not failures,
        "complete_present_arm_grids": all(g["complete"] for g in grids),
        "failures": failures,
        "grids": grids,
        "limits": [
            "Grid checks cover arms present in data; compare missing whole arms/stages against protocol separately.",
            "Output arithmetic and retained trajectories do not establish transition-kernel correctness or prove absence of oracle leakage in implementation.",
            "This is synthetic-cohort replay, not independently observed real Hermes task success.",
        ],
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("results", type=Path)
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()
    results_path = args.results / "rows.jsonl" if args.results.is_dir() else args.results
    if results_path.suffix == ".jsonl":
        data = {"rows": [json.loads(line) for line in results_path.read_text().splitlines() if line.strip()]}
    else:
        data = json.loads(results_path.read_text())
    report = audit_results(data, results_path.parent)
    report["results_sha256"] = sha256(results_path)
    report["auditor_sha256"] = sha256(__file__)
    with args.out.open("x") as stream:
        json.dump(report, stream, indent=2)
        stream.write("\n")
    print(json.dumps({k: report[k] for k in ("rows_audited", "all_row_checks_pass", "complete_present_arm_grids")}))
    return 0 if report["all_row_checks_pass"] and report["complete_present_arm_grids"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
