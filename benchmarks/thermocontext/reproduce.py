"""Reproduce the frozen Phase A cohort without changing its algorithm.

Run from the repository root with an isolated, pinned Python environment:
    python benchmarks/thermocontext/reproduce.py --out <new-directory>

The original implementation is the executable specification. This wrapper only
adds provenance, explicit compilation measurements, and create-only artifacts.
"""
from __future__ import annotations

import argparse
import dataclasses
import datetime as dt
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import platform
import subprocess
import time

import jax
import jax.numpy as jnp
import numpy as np

import phase_a_prototype as prototype


SIZES = (8, 12, 16)
TASK_SEEDS = tuple(range(12))
SOURCE_SHA256 = "69e1b4537858c06f87968093c41f11118af8d842c95edd49cd71897767044a00"
PUBLISHED_REVISION = "456ff17a734206bb5558d0b063cde5cd74f1a6ae"


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_new(path: Path, value: object) -> None:
    with path.open("x", encoding="utf-8") as stream:
        json.dump(value, stream, indent=2, sort_keys=True, allow_nan=False)
        stream.write("\n")


def frozen_manifest() -> dict:
    source = Path(prototype.__file__).resolve()
    if digest(source) != SOURCE_SHA256:
        raise ValueError("Frozen prototype source differs from the published revision")
    tasks = []
    for size in SIZES:
        for seed in TASK_SEEDS:
            task = prototype.make_task(size, seed)
            data = dataclasses.asdict(task)
            data["J"] = task.J.tolist()
            data["unary_float32"] = task.unary().tolist()
            data["task_seed"] = seed
            data["sampler_seeds"] = [10_000 + seed * 100 + s for s in range(8)]
            tasks.append(data)
    return {
        "published_revision": PUBLISHED_REVISION,
        "frozen_source_sha256": SOURCE_SHA256,
        "sizes": list(SIZES),
        "tasks_per_size": 12,
        "sampler_seeds_per_task": 8,
        "config": {
            "rel_w": prototype.REL_W,
            "token_price": prototype.TOKEN_PRICE,
            "beta": prototype.BETA,
            "chains": prototype.THERMO_CHAINS,
            "sweeps": prototype.THERMO_SWEEPS,
            "retained_sweeps": prototype.THERMO_SWEEPS // 2,
            "retained_samples": prototype.THERMO_CHAINS * (prototype.THERMO_SWEEPS // 2),
        },
        "first_valid_sample_definition": (
            "One-based position in the retained final 80 sweeps, flattened in "
            "sweep-major then chain-major order; budget-feasible and frozen verifier true. "
            "Burn-in draws are excluded. Selection itself never uses the verifier."
        ),
        "tasks": tasks,
    }


def environment() -> dict:
    packages = {}
    for name in ("jax", "jaxlib", "numpy", "thrml", "equinox", "jaxtyping"):
        try:
            packages[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            packages[name] = None
    return {
        "python": platform.python_version(),
        "platform": platform.platform(),
        "packages": packages,
        "devices": [str(device) for device in jax.devices()],
        "thread_environment": {
            key: os.environ.get(key)
            for key in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS", "XLA_FLAGS")
        },
        "published_dependency_versions": "NOT_RECORDED in the published tree",
        "dependency_parity": "UNKNOWN; current versions recorded, historical pins unavailable",
    }


def aggregate(rows: list[dict]) -> dict:
    summary = {}
    for size in SIZES:
        summary[str(size)] = {}
        for arm in ("champion", "topk", "greedy", "exact", "thermo"):
            arm_rows = [row for row in rows if row["N"] == size and row["arm"] == arm]
            entry = {
                "n": len(arm_rows),
                "verified_count": sum(row["verified"] for row in arm_rows),
                "verified_rate": float(np.mean([row["verified"] for row in arm_rows])),
                "mean_tokens": float(np.mean([row["tokens"] for row in arm_rows])),
                "mean_energy": float(np.mean([row["energy"] for row in arm_rows])),
                "budget_feasible_count": sum(row["tokens"] <= row["budget"] for row in arm_rows),
                "empty_count": sum(row["count"] == 0 for row in arm_rows),
                "total_call_wall_ms": sum(row["call_wall_ms"] for row in arm_rows),
            }
            if arm == "thermo":
                per_seed = []
                for seed in range(8):
                    seed_rows = [row for row in arm_rows if row["seed"] == seed]
                    per_seed.append({
                        "seed": seed,
                        "verified_rate": float(np.mean([row["verified"] for row in seed_rows])),
                        "mean_energy_gap": float(np.mean([row["energy_gap"] for row in seed_rows])),
                    })
                entry.update({
                    "median_sampling_ms": float(np.median([row["sampling_ms"] for row in arm_rows])),
                    "mean_energy_gap": float(np.mean([row["energy_gap"] for row in arm_rows])),
                    "exact_hit_count": sum(abs(row["energy_gap"]) < 1e-5 for row in arm_rows),
                    "exact_hit_rate": float(np.mean([abs(row["energy_gap"]) < 1e-5 for row in arm_rows])),
                    "first_valid_found_rate": float(np.mean([row["first_valid_sample"] is not None for row in arm_rows])),
                    "max_degree": max(row["max_degree"] for row in arm_rows),
                    "per_sampler_seed": per_seed,
                    "sampler_seed_verified_rate_variance": float(np.var([row["verified_rate"] for row in per_seed])),
                })
            summary[str(size)][arm] = entry
    return summary


def compare_published(summary: dict, rows: list[dict], historical: Path | None) -> dict:
    # The initial publication includes rounded aggregates, not row-level files.
    reported = {
        "8": {"verified_count": 96, "exact_hit_count": 96, "mean_tokens_1dp": "142.3"},
        "12": {"verified_count": 96, "exact_hit_count": 96, "mean_tokens_1dp": "147.2"},
        "16": {"verified_count": 95, "exact_hit_count": 86, "mean_tokens_1dp": "151.1"},
    }
    comparisons = {}
    for size, expected in reported.items():
        actual = summary[size]["thermo"]
        observed = {
            "verified_count": actual["verified_count"],
            "exact_hit_count": actual["exact_hit_count"],
            "mean_tokens_1dp": f"{actual['mean_tokens']:.1f}",
        }
        comparisons[size] = {"expected": expected, "observed": observed, "matches": expected == observed}
    result = {
        "published_source": "benchmarks/thermocontext/README.md at " + PUBLISHED_REVISION,
        "aggregate_comparison": comparisons,
        "all_reported_thermo_aggregates_match": all(row["matches"] for row in comparisons.values()),
        "historical_row_comparison": "UNAVAILABLE; no row-level artifact tracked at published revision",
        "historical_mask_parity": "UNKNOWN; aggregate agreement does not establish historical mask parity",
    }
    if historical is not None:
        previous = json.loads(historical.read_text())
        historical_rows = previous["rows"] if isinstance(previous, dict) else previous
        key = lambda row: (row["task_id"], row["arm"], row.get("seed"))
        old = {key(row): row for row in historical_rows}
        current = {key(row): row for row in rows}
        if len(old) != len(historical_rows):
            raise ValueError("Historical rows contain duplicate task/arm/seed identities")
        fields = ("verified", "tokens", "count", "energy", "budget", "ids", "first_valid_sample", "energy_gap")
        differences = []
        for identity in sorted(set(old) | set(current), key=str):
            before, after = old.get(identity), current.get(identity)
            if before is None or after is None:
                differences.append({"identity": identity, "missing": "historical" if before is None else "current"})
                continue
            changes = {name: {"historical": before.get(name), "current": after.get(name)}
                       for name in fields if before.get(name) != after.get(name)}
            if changes:
                differences.append({"identity": identity, "fields": changes})
        result["historical_row_comparison"] = {"sha256": digest(historical), "differences": differences,
                                                "match": not differences}
        result["historical_mask_parity"] = "MATCH" if not differences else "DIFFERENT"
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--historical-rows", type=Path)
    args = parser.parse_args()
    started = time.perf_counter()
    manifest = frozen_manifest()
    args.out.mkdir(parents=True, exist_ok=False)
    write_new(args.out / "cohort.json", manifest)
    run_environment = environment()
    try:
        revision = subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()
    except (OSError, subprocess.CalledProcessError):
        revision = "UNKNOWN"
    write_new(args.out / "environment.json", run_environment)
    rows = []
    compilations = []
    compiled = set()
    for size in SIZES:
        for task_seed in TASK_SEEDS:
            task = prototype.make_task(size, task_seed)
            exact_started = time.perf_counter()
            exact_mask, exact_energy = prototype.exact(task)
            exact_wall_ms = (time.perf_counter() - exact_started) * 1000
            for name, function in (("champion", prototype.champion), ("topk", prototype.topk), ("greedy", prototype.greedy)):
                call_started = time.perf_counter()
                mask = function(task)
                call_ms = (time.perf_counter() - call_started) * 1000
                rows.append(prototype.record(task, name, mask, extra={"mask": mask.astype(int).tolist(), "call_wall_ms": call_ms}))
            rows.append(prototype.record(task, "exact", exact_mask, exact_energy,
                                         {"mask": exact_mask.astype(int).tolist(), "call_wall_ms": exact_wall_ms}))
            blocks = prototype.coloring(task.J)
            signature = (size, tuple(tuple(map(int, block)) for block in blocks))
            if signature not in compiled:
                function = prototype.sampler_for(size, blocks)
                lower_started = time.perf_counter()
                lowered = function.lower(jax.random.key(10_000 + 100 * task_seed), jnp.asarray(task.unary()), jnp.asarray(task.J))
                lower_ms = (time.perf_counter() - lower_started) * 1000
                compile_started = time.perf_counter()
                lowered.compile()
                compile_ms = (time.perf_counter() - compile_started) * 1000
                compilations.append({"N": size, "task_id": task.task_id, "blocks": [block.tolist() for block in blocks],
                                     "lower_wall_ms": lower_ms, "compile_wall_ms": compile_ms})
                compiled.add(signature)
            for sampler_seed in range(8):
                call_started = time.perf_counter()
                mask, energy, first_valid, sampling_ms, nblocks, degree = prototype.thermo(task, 10_000 + task_seed * 100 + sampler_seed)
                call_ms = (time.perf_counter() - call_started) * 1000
                rows.append(prototype.record(task, "thermo", mask, energy, {
                    "seed": sampler_seed, "rng_seed": 10_000 + task_seed * 100 + sampler_seed,
                    "mask": mask.astype(int).tolist(), "first_valid_sample": first_valid,
                    "sampling_ms": sampling_ms, "call_wall_ms": call_ms,
                    "host_bookkeeping_ms": call_ms - sampling_ms,
                    "color_blocks": nblocks, "max_degree": degree,
                    "exact_energy": exact_energy, "energy_gap": energy - exact_energy,
                }))
            print(json.dumps({"completed": task.task_id, "rows": len(rows)}), flush=True)
    summary = aggregate(rows)
    result = {
        "schema": "thermocontext.frozen-reproduction.v1",
        "recorded_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "source_revision": revision,
        "wrapper_sha256": digest(Path(__file__)),
        "cohort_sha256": digest(args.out / "cohort.json"),
        "frozen_source_sha256": SOURCE_SHA256,
        "config": manifest["config"],
        "environment": run_environment,
        "compilations": compilations,
        "timing_definitions": {
            "compile": "Explicit lower() and compile() per unique size/color-block signature, before the original thermo() call.",
            "sampling_ms": "Original thermo() timer: dispatch plus device-to-host conversion after explicit compilation; initial dispatch can include additional initialization.",
            "call_wall_ms": "Full original thermo() including Python feasibility, original objective, frozen-verifier scan and minimum selection.",
            "total_wall_ms": "Wrapper runtime including environment discovery, exhaustive exact search, compilation, sampling and bookkeeping; excludes interpreter import/startup and final artifact writing.",
            "hardware_claim": "CPU JAX simulation only; no Z1/TSU speed or energy conclusion.",
        },
        "summary": summary,
        "published_comparison": compare_published(summary, rows, args.historical_rows),
        "rows": rows,
        "total_wall_ms": (time.perf_counter() - started) * 1000,
    }
    write_new(args.out / "result.json", result)
    write_new(args.out / "SHA256SUMS.json", {path.name: digest(path) for path in sorted(args.out.glob("*.json"))})


if __name__ == "__main__":
    main()
