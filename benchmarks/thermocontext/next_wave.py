"""Run the frozen ThermoContext next wave and retain auditable proposal traces.

Example (from repository root, with the pinned THRML environment):
  python -m benchmarks.thermocontext.next_wave --mode full --sizes 8 12 16 \
    --tasks 12 --seeds 8 --out benchmarks/thermocontext/results/next-wave/small

Each invocation creates a new output directory. There is deliberately no resume
or overwrite mode. A failed run remains evidence. Scale stages must be launched
separately after applying the preregistered gates to the preceding stage.
"""
from __future__ import annotations

import argparse
import dataclasses
import datetime as dt
import hashlib
import importlib
import importlib.metadata
import json
import os
from pathlib import Path
import platform
import subprocess
import sys
import time
import traceback

import jax
import jax.numpy as jnp
import numpy as np

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from benchmarks.thermocontext import phase_a_prototype as frozen


HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
PROTOCOL = HERE / "next_wave_protocol.json"
CHAINS = 64
SWEEPS = 160
DETERMINISTIC = ("champion", "topk", "greedy", "pair_greedy", "component_dp", "exact")
STOCHASTIC = ("prototype", "thrml_static", "adaptive", "slack", "thrml_projected",
              "prototype_projected", "random_projected")
DEFAULT_ARMS = DETERMINISTIC + STOCHASTIC
_PROTOTYPE_EXECUTABLES = {}


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _json(value):
    if isinstance(value, np.ndarray):
        return value.tolist()
    if isinstance(value, np.generic):
        return value.item()
    if isinstance(value, Path):
        return str(value)
    raise TypeError(f"Unsupported result value: {type(value).__name__}")


def write_json(path, data):
    with Path(path).open("x", encoding="utf-8") as stream:
        json.dump(data, stream, indent=2, sort_keys=True, allow_nan=False, default=_json)
        stream.write("\n")


def write_trace(path, trajectories, finalmask, raw_trajectories=None, initial_item_states=None):
    """Lossless packed masks, fixed little-endian bit order, never pickle."""
    trajectory = np.asarray(trajectories, dtype=bool)
    arrays = {
        "packed": np.packbits(trajectory.reshape(-1), bitorder="little"),
        "shape": np.asarray(trajectory.shape, dtype=np.int64),
        "finalmask": np.asarray(finalmask, dtype=bool),
    }
    for prefix, value in (("raw", raw_trajectories), ("initial", initial_item_states)):
        if value is not None:
            array = np.asarray(value, dtype=bool)
            arrays[prefix + "_packed"] = np.packbits(array.reshape(-1), bitorder="little")
            arrays[prefix + "_shape"] = np.asarray(array.shape, dtype=np.int64)
    with Path(path).open("xb") as stream:
        np.savez_compressed(stream, **arrays)


def validate_config(sizes, tasks, seeds, arms, mode):
    if not sizes or list(sizes) != sorted(set(sizes)) or any(size not in (8, 12, 16, 32, 64, 128) for size in sizes):
        raise ValueError("sizes must be unique ascending values from 8,12,16,32,64,128")
    if not 1 <= tasks <= 12 or not 1 <= seeds <= 8:
        raise ValueError("tasks must be 1..12 and seeds 1..8")
    if mode == "full" and (tasks != 12 or seeds != 8):
        raise ValueError("full mode requires exactly 12 tasks and 8 seeds")
    if not arms or len(set(arms)) != len(arms) or any(arm not in DEFAULT_ARMS for arm in arms):
        raise ValueError("arms must be unique known experiment arm names")
    if any(size >= 32 for size in sizes) and len(sizes) != 1:
        raise ValueError("each scale N requires a separate invocation and preceding gate review")
    if mode not in ("full", "diagnostic"):
        raise ValueError("unknown experiment mode")


def select_original_energy(task, trajectories):
    """Exactly the original feasible-minimum rule; never call the verifier."""
    states = np.asarray(trajectories)
    if states.ndim != 3 or states.shape[-1] != len(task.items) or not np.isin(states, [0, 1]).all():
        raise ValueError("proposals must be binary [retained sweep,chain,N]")
    flat = states.reshape(-1, len(task.items)).astype(bool)
    if not len(flat):
        raise ValueError("empty proposal trajectory")
    mask = None
    energy = float("inf")
    feasible = 0
    selected = None
    for index, candidate in enumerate(flat):
        if task.tokens(candidate) > task.budget:
            continue
        feasible += 1
        score = task.energy(candidate)
        if not np.isfinite(score):
            raise ValueError("nonfinite frozen objective")
        if score < energy:
            mask, energy, selected = candidate.copy(), score, index + 1
    no_feasible = mask is None
    if mask is None:
        mask = np.zeros(len(task.items), dtype=bool)
        energy = task.energy(mask)
    return {"mask": mask, "energy": energy, "selected_sample": selected,
            "no_feasible": no_feasible, "feasible_sample_count": feasible,
            "empty_sample_count": int((~flat.any(axis=1)).sum()), "sample_count": len(flat)}


def first_valid_sample(task, trajectories):
    for index, candidate in enumerate(np.asarray(trajectories).reshape(-1, len(task.items))):
        if task.tokens(candidate) <= task.budget and task.verify(candidate):
            return index + 1
    return None


def graph_metadata(task):
    degrees = np.count_nonzero(task.J, axis=1)
    return {"nodes": len(task.items), "edges": int(degrees.sum() // 2),
            "max_degree": int(degrees.max(initial=0)), "mean_degree": float(degrees.mean()),
            "color_blocks": len(frozen.coloring(task.J))}


def prototype_batch(task, seed):
    """Call the unchanged prototype transition function with its original key."""
    started = time.perf_counter()
    blocks = frozen.coloring(task.J)
    signature = (len(task.items), tuple(tuple(map(int, block)) for block in blocks))
    function = frozen.sampler_for(len(task.items), blocks)
    arguments = (jax.random.key(seed), jnp.asarray(task.unary()), jnp.asarray(task.J))
    jax.block_until_ready(arguments)
    build_ms = (time.perf_counter() - started) * 1000
    cache_hit = signature in _PROTOTYPE_EXECUTABLES
    compile_ms = 0.0
    if not cache_hit:
        compiling = time.perf_counter()
        _PROTOTYPE_EXECUTABLES[signature] = function.lower(*arguments).compile()
        compile_ms = (time.perf_counter() - compiling) * 1000
    sampling = time.perf_counter()
    trajectory = np.asarray(_PROTOTYPE_EXECUTABLES[signature](*arguments), dtype=bool)
    execution_ms = (time.perf_counter() - sampling) * 1000
    return {
        "trajectories": trajectory,
        "timings": {"build_ms": build_ms, "compile_ms": compile_ms,
                    "warm_sampling_ms": execution_ms,
                    "sampling_execution_ms": execution_ms,
                    "cold_first_execution_ms": 0.0 if cache_hit else execution_ms,
                    "warm_cached_execution_ms": execution_ms if cache_hit else 0.0,
                    "total_ms": (time.perf_counter() - started) * 1000},
        "metadata": {**graph_metadata(task), "backend": "frozen_prototype_JAX",
                     "compile_cache_hit": cache_hit, "chains": CHAINS, "sweeps": SWEEPS,
                     "transitions_per_chain": SWEEPS, "first_retained_transition": 81,
                     "sample_count": len(trajectory) * CHAINS},
    }


def propose(task, arm, seed):
    """One bounded proposal call, or a matched deterministic projection."""
    if arm in ("prototype", "prototype_projected"):
        result = prototype_batch(task, seed)
    elif arm == "thrml_static":
        from benchmarks.thermocontext.thrml_backend import sample_qubo
        batch = sample_qubo(task.unary(), task.J, seed, chains=CHAINS, sweeps=SWEEPS)
        return dataclasses.asdict(batch)
    elif arm in ("adaptive", "slack", "thrml_projected"):
        module_name = {"adaptive": "adaptive_budget", "slack": "slack_budget",
                       "thrml_projected": "constrained_budget"}[arm]
        module = importlib.import_module("benchmarks.thermocontext." + module_name)
        return module.propose(task, seed, chains=CHAINS, sweeps=SWEEPS)
    elif arm == "random_projected":
        started = time.perf_counter()
        # Independent Bernoulli(0.35), matching the original initial density.
        # Exactly the retained draw cap; no rejected/resampled rescue draws.
        trajectory = np.random.default_rng(seed).random((SWEEPS // 2, CHAINS, len(task.items))) < 0.35
        execution_ms = (time.perf_counter() - started) * 1000
        result = {"trajectories": trajectory,
                  "timings": {"build_ms": 0.0, "compile_ms": 0.0,
                              "warm_sampling_ms": execution_ms, "sampling_execution_ms": execution_ms,
                              "cold_first_execution_ms": 0.0, "warm_cached_execution_ms": execution_ms,
                              "total_ms": execution_ms},
                  "metadata": {"backend": "numpy_independent_bernoulli", "probability": 0.35,
                               "nodes": len(task.items), "edges": 0, "max_degree": 0,
                               "color_blocks": 1, "chains": CHAINS, "sweeps": 0,
                               "transitions_per_chain": 0,
                               "sample_count": CHAINS * (SWEEPS // 2)}}
    else:
        raise ValueError(f"unknown stochastic arm {arm}")
    if arm in ("prototype_projected", "random_projected"):
        from benchmarks.thermocontext.constrained_budget import project_budget
        started = time.perf_counter()
        raw = result["trajectories"]
        projected, statistics = project_budget(task, raw)
        projection_ms = (time.perf_counter() - started) * 1000
        result["raw_trajectories"] = raw
        result["trajectories"] = projected
        result["timings"]["host_projection_ms"] = projection_ms
        result["timings"]["total_ms"] += projection_ms
        result["metadata"].update({"controller": statistics,
                                    "budget_enforcement": "host_delete_only_projection",
                                    "projected_distribution": "not_constrained_gibbs",
                                    "host_global_budget_bookkeeping": True,
                                    "tsu_budget_enforcement_claim": False})
    return result


def _metrics(task, mask, energy, exact_energy, component_energy):
    verification_start = time.perf_counter()
    verified = task.verify(mask)
    verification_ms = (time.perf_counter() - verification_start) * 1000
    tokens = task.tokens(mask)
    return {
        "mask": mask.astype(int).tolist(), "ids": sorted(task.selected_ids(mask)),
        "tokens": tokens, "budget": task.budget, "energy": energy,
        "exact_energy": exact_energy,
        "energy_gap": None if exact_energy is None else energy - exact_energy,
        "component_reference_energy": component_energy,
        "component_energy_gap": None if component_energy is None else energy - component_energy,
        "verified": bool(verified), "feasible": tokens <= task.budget,
        "empty": not bool(mask.any()), "count": int(mask.sum()),
    }, verification_ms


def sampled_row(task, task_seed, arm, sampler_seed, out, exact_energy, component_energy):
    started = time.perf_counter()
    rng_seed = 10_000 + task_seed * 100 + sampler_seed
    batch = propose(task, arm, rng_seed)
    proposal_call_ms = (time.perf_counter() - started) * 1000
    trajectory = np.asarray(batch["trajectories"])
    if trajectory.shape != (SWEEPS // 2, CHAINS, len(task.items)):
        raise ValueError(f"wrong retained trajectory shape: {trajectory.shape}")
    selecting = time.perf_counter()
    selected = select_original_energy(task, trajectory)
    selection_ms = (time.perf_counter() - selecting) * 1000
    metrics, verification_ms = _metrics(task, selected["mask"], selected["energy"], exact_energy, component_energy)
    diagnostic = time.perf_counter()
    first_valid = first_valid_sample(task, trajectory)
    diagnostic_ms = (time.perf_counter() - diagnostic) * 1000
    trace_path = Path("traces") / f"{task.task_id}--{arm}--s{sampler_seed}.npz"
    writing = time.perf_counter()
    write_trace(out / trace_path, trajectory, selected["mask"], batch.get("raw_trajectories"), batch.get("initial_item_states"))
    trace_hash = sha256(out / trace_path)
    auxiliary_fields = {}
    if "auxiliary_trajectories" in batch:
        graph = batch["auxiliary_graph"]
        canonical_hash = hashlib.sha256(json.dumps(graph, sort_keys=True, separators=(",", ":"),
                                                   allow_nan=False, default=_json).encode()).hexdigest()
        if canonical_hash != batch["auxiliary_graph_sha256"]:
            raise ValueError("auxiliary graph canonical hash differs from proposer metadata")
        graph_path = Path("graphs") / f"{task.task_id}--{arm}.json"
        if (out / graph_path).exists():
            if json.loads((out / graph_path).read_text()) != graph:
                raise ValueError("auxiliary graph changed between sampler seeds")
        else:
            write_json(out / graph_path, graph)
        auxiliary_path = Path("traces") / f"{task.task_id}--{arm}--s{sampler_seed}--auxiliary.npz"
        raw = np.asarray(batch["auxiliary_trajectories"], dtype=bool)
        raw_flat = raw.reshape(-1, raw.shape[-1])
        auxiliary_final = (raw_flat[selected["selected_sample"] - 1] if selected["selected_sample"] is not None
                           else np.zeros(raw.shape[-1], dtype=bool))
        write_trace(out / auxiliary_path, raw, auxiliary_final,
                    initial_item_states=batch["initial_full_states"])
        auxiliary_fields = {
            "auxiliary_trace_path": str(auxiliary_path),
            "auxiliary_trace_sha256": sha256(out / auxiliary_path),
            "auxiliary_graph_path": str(graph_path),
            "auxiliary_graph_file_sha256": sha256(out / graph_path),
            "auxiliary_graph_sha256": canonical_hash,
        }
    artifact_ms = (time.perf_counter() - writing) * 1000
    timing = batch.get("timings", {})
    metadata = dict(batch.get("metadata", {}))
    if auxiliary_fields:
        # Full expanded graph is retained once per task, referenced by hashes.
        metadata.pop("nodes", None)
        metadata.pop("edges", None)
    first_execution = timing.get("cold_first_execution_ms")
    cached_execution = timing.get("warm_cached_execution_ms")
    if first_execution is None or cached_execution is None:
        # Preserve uncertainty rather than relabel combined first/warm timing.
        first_execution = cached_execution = None
    row = {
        "status": "ok", "task_id": task.task_id, "N": len(task.items), "task_seed": task_seed,
        "arm": arm, "seed": sampler_seed, "rng_seed": rng_seed, **metrics,
        "sample_count": selected["sample_count"], "first_valid_sample": first_valid,
        "selected_sample": selected["selected_sample"], "no_feasible": selected["no_feasible"],
        "feasible_sample_count": selected["feasible_sample_count"],
        "empty_sample_count": selected["empty_sample_count"],
        "timings": {
            "construction_ms": timing.get("build_ms", timing.get("construction_ms", 0.0)) + timing.get("formulation_build_ms", 0.0),
            "jax_compile_ms": timing.get("compile_ms", timing.get("jax_compile_ms")),
            "first_execution_ms": first_execution,
            "warm_sampling_ms": cached_execution,
            "sampling_execution_ms": timing.get("sampling_execution_ms", timing.get("warm_sampling_ms")),
            "proposal_call_ms": proposal_call_ms,
            "host_postprocess_ms": selection_ms + timing.get("controller_ms", 0.0) + timing.get("host_projection_ms", 0.0),
            "selection_ms": selection_ms, "verification_ms": verification_ms,
            "diagnostic_ms": diagnostic_ms, "artifact_write_ms": artifact_ms,
            "total_wall_ms": (time.perf_counter() - started) * 1000,
        },
        "raw_backend_timings": timing,
        "graph_degree": metadata.get("max_degree", metadata.get("graph_degree")),
        "metadata": metadata, "dependency": batch.get("dependency", metadata.get("dependency")),
        "trace_path": str(trace_path), "trace_sha256": trace_hash,
        **auxiliary_fields,
    }
    if arm == "slack" and "initial_item_states" in batch:
        row["_initial_control"] = initial_control_row(
            task, task_seed, sampler_seed, np.asarray(batch["initial_item_states"])[None, :, :],
            out, exact_energy, component_energy)
    return row


def initial_control_row(task, task_seed, sampler_seed, trajectory, out, exact_energy, component_energy):
    """Score the already-generated slack initializer; no new proposal calls."""
    started = time.perf_counter()
    selected = select_original_energy(task, trajectory)
    metrics, verification_ms = _metrics(task, selected["mask"], selected["energy"], exact_energy, component_energy)
    first_valid = first_valid_sample(task, trajectory)
    path = Path("traces") / f"{task.task_id}--slack_initial--s{sampler_seed}.npz"
    write_trace(out / path, trajectory, selected["mask"])
    return {
        "status": "ok", "task_id": task.task_id, "N": len(task.items), "task_seed": task_seed,
        "arm": "slack_initial", "seed": sampler_seed, "rng_seed": 10_000 + task_seed * 100 + sampler_seed,
        **metrics, "sample_count": selected["sample_count"], "first_valid_sample": first_valid,
        "selected_sample": selected["selected_sample"], "no_feasible": selected["no_feasible"],
        "feasible_sample_count": selected["feasible_sample_count"],
        "empty_sample_count": selected["empty_sample_count"], "graph_degree": 0,
        "metadata": {"role": "zero_transition_initialization_control", "parent_arm": "slack",
                     "transitions_per_chain": 0, "chains": CHAINS,
                     "standalone_initialization_latency": "NOT_MEASURED; initialization is shared with parent slack arm",
                     "additional_proposal_calls": 0},
        "timings": {"construction_ms": None, "jax_compile_ms": 0.0,
                    "first_execution_ms": 0.0, "warm_sampling_ms": 0.0,
                    "verification_ms": verification_ms,
                    "total_wall_ms": (time.perf_counter() - started) * 1000},
        "trace_path": str(path), "trace_sha256": sha256(out / path),
    }


def deterministic_results(task, arms):
    outputs = {}
    for arm in DETERMINISTIC:
        if arm == "exact" and len(task.items) > 16:
            continue
        # Exact and component references are evaluation-only computations even
        # if omitted from display arms; they never initialize a proposal arm.
        if arm not in arms and arm not in ("exact", "component_dp"):
            continue
        started = time.perf_counter()
        metadata = graph_metadata(task)
        if arm in ("champion", "topk", "greedy"):
            mask = getattr(frozen, arm)(task)
        elif arm == "exact":
            mask, _ = frozen.exact(task)
        else:
            from benchmarks.thermocontext.pair_greedy import component_dp, pair_greedy
            if arm == "pair_greedy":
                mask = pair_greedy(task)
            else:
                mask, extra = component_dp(task)
                metadata.update(extra)
        energy = task.energy(mask)
        outputs[arm] = {"mask": mask, "energy": energy, "metadata": metadata,
                        "call_wall_ms": (time.perf_counter() - started) * 1000}
    return outputs


def deterministic_row(task, task_seed, arm, output, exact_energy, component_energy):
    started = time.perf_counter()
    metrics, verification_ms = _metrics(task, output["mask"], output["energy"], exact_energy, component_energy)
    return {"status": "ok", "task_id": task.task_id, "N": len(task.items), "task_seed": task_seed,
            "arm": arm, "seed": None, "rng_seed": None, **metrics,
            "sample_count": 0, "first_valid_sample": None, "no_feasible": False,
            "selected_sample": None, "graph_degree": output["metadata"].get("max_degree"),
            "metadata": output["metadata"], "trace_path": None,
            "timings": {"construction_ms": 0.0, "jax_compile_ms": 0.0,
                        "first_execution_ms": 0.0, "warm_sampling_ms": 0.0,
                        "host_postprocess_ms": output["call_wall_ms"],
                        "verification_ms": verification_ms, "diagnostic_ms": 0.0,
                        "total_wall_ms": output["call_wall_ms"] + (time.perf_counter() - started) * 1000}}


def _distribution(values):
    finite = [float(value) for value in values if value is not None and np.isfinite(value)]
    if not finite:
        return None
    return {"n": len(finite), "mean": float(np.mean(finite)), "median": float(np.median(finite)),
            "p95": float(np.quantile(finite, 0.95)), "p99": float(np.quantile(finite, 0.99)),
            "min": min(finite), "max": max(finite)}


def summarize(rows):
    summary = {}
    for size in sorted({row["N"] for row in rows}):
        summary[str(size)] = {}
        for arm in sorted({row["arm"] for row in rows if row["N"] == size}):
            group = [row for row in rows if row["N"] == size and row["arm"] == arm]
            ok = [row for row in group if row.get("status") == "ok"]
            per_task = {}
            for task_id in sorted({row["task_id"] for row in group}):
                task_rows = [row for row in group if row["task_id"] == task_id]
                per_task[task_id] = {"rows": len(task_rows),
                                     "successes": sum(bool(row.get("verified")) for row in task_rows),
                                     "verified_rate": float(np.mean([bool(row.get("verified")) for row in task_rows])),
                                     "energy_gap": _distribution([row.get("energy_gap") for row in task_rows])}
            per_seed = {}
            for seed in sorted({row["seed"] for row in group if row.get("seed") is not None}):
                seed_rows = [row for row in group if row["seed"] == seed]
                per_seed[str(seed)] = {"rows": len(seed_rows),
                                       "verified_rate": float(np.mean([bool(row.get("verified")) for row in seed_rows])),
                                       "energy_gap": _distribution([row.get("energy_gap") for row in seed_rows])}
            successful = [row for row in ok if row["verified"] and row["feasible"]]
            summary[str(size)][arm] = {
                "row_count": len(group), "error_count": len(group) - len(ok),
                "task_count": len(per_task), "verified_count": sum(bool(row.get("verified")) for row in group),
                "verified_rate_all_rows": float(np.mean([bool(row.get("verified")) for row in group])),
                "verified_and_feasible_count": len(successful),
                "empty_count": sum(row["empty"] for row in ok),
                "over_budget_count": sum(not row["feasible"] for row in ok),
                "no_feasible_count": sum(row.get("no_feasible", False) for row in ok),
                "sample_count_total": sum(row.get("sample_count", 0) for row in ok),
                "first_valid_censored_count": sum(row.get("sample_count", 0) > 0 and row.get("first_valid_sample") is None for row in ok),
                "first_valid_found": _distribution([row.get("first_valid_sample") for row in ok]),
                "tokens_all_outputs": _distribution([row.get("tokens") for row in ok]),
                "tokens_verified_and_feasible": _distribution([row["tokens"] for row in successful]),
                "energy": _distribution([row.get("energy") for row in ok]),
                "energy_gap": _distribution([row.get("energy_gap") for row in ok]),
                "component_energy_gap": _distribution([row.get("component_energy_gap") for row in ok]),
                "graph_degree": _distribution([row.get("graph_degree") for row in ok]),
                "timings": {name: _distribution([row.get("timings", {}).get(name) for row in ok])
                            for name in ("construction_ms", "jax_compile_ms", "first_execution_ms", "warm_sampling_ms",
                                         "sampling_execution_ms", "host_postprocess_ms", "verification_ms", "diagnostic_ms", "total_wall_ms")},
                "per_task": per_task, "per_seed": per_seed,
                "worst_task_verified_rate": min(value["verified_rate"] for value in per_task.values()),
                "seed_verified_rate_variance": float(np.var([value["verified_rate"] for value in per_seed.values()])) if per_seed else None,
            }
    return summary


def manifest(args, protocol):
    def git(*arguments):
        return subprocess.check_output(["git", *arguments], cwd=ROOT, text=True).strip()
    versions = {}
    for name in ("jax", "jaxlib", "numpy", "thrml", "equinox", "jaxtyping"):
        try:
            versions[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            versions[name] = None
    config = {"mode": args.mode, "sizes": args.sizes, "tasks": args.tasks, "seeds": args.seeds,
              "arms": args.arms, "chains": CHAINS, "sweeps": SWEEPS, "retained_sweeps": SWEEPS // 2,
              "implied_controls": ["slack_initial"] if "slack" in args.arms else [],
              "seed_schedule": "10000+task_seed*100+sampler_seed",
              "random_control": {"generator": "numpy.default_rng", "probability": 0.35,
                                 "draws": [SWEEPS // 2, CHAINS, "N"]},
              "projection": "Identical constrained_budget.project_budget for all three projected arms",
              "tie_rule": "strict original float32 energy improvement; first encountered exact tie"}
    from benchmarks.thermocontext.thrml_backend import dependency_identity
    return {
        "schema": "thermocontext.next-wave-run.v1", "started_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "git_revision": git("rev-parse", "HEAD"), "dirty_paths": git("status", "--short").splitlines(),
        "source_sha256": {str(path.relative_to(ROOT)): sha256(path) for path in sorted(HERE.glob("*.py"))},
        "protocol_sha256": sha256(PROTOCOL), "protocol": protocol,
        "arm_config": config,
        "arm_config_sha256": hashlib.sha256(json.dumps(config, sort_keys=True).encode()).hexdigest(),
        "dependency_versions": versions, "thrml_distribution_origin": dependency_identity(),
        "jax_backend": jax.default_backend(), "devices": [str(device) for device in jax.devices()],
        "python_version": platform.python_version(), "platform": platform.platform(),
        "command": [sys.executable, *sys.argv],
        "cpu_affinity": sorted(os.sched_getaffinity(0)) if hasattr(os, "sched_getaffinity") else None,
        "environment_limits": {name: os.environ.get(name) for name in
                               ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS", "XLA_FLAGS")},
        "timing_mode": args.timing_mode, "concurrent_jobs": args.concurrent_jobs,
        "limitations": ["Synthetic verifier and token labels; no consumed model tokens or Hermes outcome.",
                        "JAX CPU simulation; no Z1/TSU speed or energy claim.",
                        "Task is the statistical unit; seeds and chain draws are not independent tasks.",
                        "Compilation caches are shared across arms in the recorded order; per-arm incurred compilation is not isolated cold-start latency.",
                        "First-valid diagnostic and trace writing are included in experiment wall, separately reported.",
                        "Exact/component oracle construction is separate evaluation infrastructure, never sampler input.",
                        "Any scale stage requires manual preceding-gate review; this runner does not auto-promote."]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sizes", nargs="+", default=["8", "12", "16"])
    parser.add_argument("--tasks", type=int, default=12)
    parser.add_argument("--seeds", type=int, default=8)
    parser.add_argument("--arms", nargs="+", default=list(DEFAULT_ARMS))
    parser.add_argument("--mode", choices=("diagnostic", "full"), required=True)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--timing-mode", choices=("isolated", "contended"), default="contended")
    parser.add_argument("--concurrent-jobs", default="Not independently measured; shared host")
    args = parser.parse_args()
    args.sizes = [int(value) for part in args.sizes for value in part.split(",")]
    args.arms = [value for part in args.arms for value in part.split(",")]
    validate_config(args.sizes, args.tasks, args.seeds, args.arms, args.mode)
    protocol = json.loads(PROTOCOL.read_text())
    if sha256(HERE / "phase_a_prototype.py") != protocol["frozen"]["prototype_sha256"]:
        raise ValueError("frozen prototype source changed")
    started = time.perf_counter()
    args.out.mkdir(parents=True, exist_ok=False)
    (args.out / "traces").mkdir()
    (args.out / "graphs").mkdir()
    provenance = manifest(args, protocol)
    write_json(args.out / "provenance.json", provenance)
    tasks = [frozen.make_task(size, seed) for size in args.sizes for seed in range(args.tasks)]
    cohort = []
    for task in tasks:
        data = dataclasses.asdict(task)
        data["J"] = task.J.tolist()
        data["unary_float32"] = task.unary().tolist()
        cohort.append(data)
    write_json(args.out / "cohort.json", cohort)
    rows = []
    reference_times = []
    with (args.out / "rows.jsonl").open("x", encoding="utf-8") as stream:
        for task in tasks:
            task_seed = int(task.task_id.rsplit("-s", 1)[1])
            baselines = deterministic_results(task, args.arms)
            exact_energy = baselines.get("exact", {}).get("energy")
            component_energy = baselines.get("component_dp", {}).get("energy")
            reference_times.append({"task_id": task.task_id,
                                    "exact_ms": baselines.get("exact", {}).get("call_wall_ms"),
                                    "component_dp_ms": baselines.get("component_dp", {}).get("call_wall_ms")})
            for arm in args.arms:
                if arm in DETERMINISTIC:
                    if arm not in baselines:
                        continue  # Exhaustive exact is intentionally N<=16 only.
                    row = deterministic_row(task, task_seed, arm, baselines[arm], exact_energy, component_energy)
                    rows.append(row)
                    stream.write(json.dumps(row, sort_keys=True, allow_nan=False, default=_json) + "\n")
                    stream.flush()
                else:
                    for seed in range(args.seeds):
                        row_started = time.perf_counter()
                        try:
                            row = sampled_row(task, task_seed, arm, seed, args.out, exact_energy, component_energy)
                        except Exception as error:
                            row = {"status": "error", "task_id": task.task_id, "N": len(task.items),
                                   "task_seed": task_seed, "arm": arm, "seed": seed,
                                   "rng_seed": 10_000 + task_seed * 100 + seed,
                                   "verified": False, "error_type": type(error).__name__,
                                   "error": str(error), "traceback": traceback.format_exc(),
                                   "timings": {"total_wall_ms": (time.perf_counter() - row_started) * 1000}}
                        initial_control = row.pop("_initial_control", None)
                        rows.append(row)
                        stream.write(json.dumps(row, sort_keys=True, allow_nan=False, default=_json) + "\n")
                        stream.flush()
                        if initial_control is not None:
                            rows.append(initial_control)
                            stream.write(json.dumps(initial_control, sort_keys=True, allow_nan=False, default=_json) + "\n")
                            stream.flush()
                print(json.dumps({"completed": task.task_id, "arm": arm, "rows": len(rows)}), flush=True)
    result = {"schema": "thermocontext.next-wave-summary.v1", "mode": args.mode,
              "finished_at": dt.datetime.now(dt.timezone.utc).isoformat(),
              "protocol_sha256": provenance["protocol_sha256"],
              "arm_config_sha256": provenance["arm_config_sha256"],
              "result_rows_sha256": sha256(args.out / "rows.jsonl"),
              "summary": summarize(rows), "reference_construction": reference_times,
              "error_count": sum(row["status"] != "ok" for row in rows),
              "total_wall_ms": (time.perf_counter() - started) * 1000,
              "decision": "NOT_AUTOMATIC; apply frozen protocol gates and independent audit"}
    write_json(args.out / "summary.json", result)
    write_json(args.out / "SHA256SUMS.json", {
        str(path.relative_to(args.out)): sha256(path)
        for path in sorted(args.out.rglob("*")) if path.is_file()})
    if result["error_count"]:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
