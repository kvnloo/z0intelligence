"""Four-stage sparse THRML proposals with a bounded adaptive token price.

The controller is classical host work, not a hardware claim. It changes only
unary coefficients; every retained candidate is ranked by the frozen objective.
No verifier outputs or required-item identities enter the proposal mechanism.
"""
from __future__ import annotations

import time

import numpy as np

from .thrml_backend import sample_qubo

MAX_PRICE = 64.0
STAGES = 4


def initial_price(unary, tokens, budget, *, beta=3.5):
    """Bounded independent-spin occupancy estimate, without oracle labels.

    This initializer is explicitly approximate: it ignores pair interactions.
    Subsequent stages use measured occupancy, never the frozen verifier.
    """
    scaled_tokens = np.asarray(tokens, dtype=np.float64) / budget
    unary = np.asarray(unary, dtype=np.float64)

    def expected_tokens(price):
        logits = np.clip(beta * (unary + price * scaled_tokens), -60, 60)
        return float(np.asarray(tokens) @ (1 / (1 + np.exp(logits))))

    target = 0.8 * budget
    if expected_tokens(0) <= target:
        return 0.0
    low, high = 0.0, MAX_PRICE
    for _ in range(16):
        mid = (low + high) / 2
        if expected_tokens(mid) > target:
            low = mid
        else:
            high = mid
    return high


def update_price(*, price, lower, upper, mean_tokens, empty_fraction, budget):
    """Bracket occupancy, lower price on collapse, never subsidize past zero."""
    if empty_fraction >= 0.5:
        upper = price
        reason = "empty_context_safeguard"
    elif mean_tokens < 0.65 * budget:
        upper = price
        reason = "underfilled_lower_price"
    elif mean_tokens > 0.95 * budget:
        lower = price
        reason = "overfilled_raise_price"
    else:
        return price, lower, upper, "occupancy_in_target_band"
    # A stochastic stage can contradict an earlier bracket. Reset the lower
    # bound rather than forcing a negative or reversed interval.
    if upper is not None and lower > upper:
        lower = 0.0
    if upper is not None:
        next_price = (lower + upper) / 2
    else:
        next_price = min(MAX_PRICE, price + max(1.0, price / 2))
    return float(np.clip(next_price, 0, MAX_PRICE)), lower, upper, reason


def propose(task, seed, *, chains=64, sweeps=160):
    """Produce exactly chains*sweeps transitions, half retained in four stages.

    Stage zero preserves access to the original objective. Stage one uses a
    dimension-aware occupancy price, then two stages adapt within a bounded
    bracket. Later prices cannot erase an earlier feasible incumbent. No extra
    sampling is permitted for an empty or unsuccessful outcome.
    """
    started = time.perf_counter()
    if sweeps < 8 or sweeps % 8:
        raise ValueError("sweeps must be a positive multiple of 8 for four equal stages")
    tokens = np.array([item.tokens for item in task.items], dtype=np.float32)
    original_unary = task.unary()
    estimate = initial_price(original_unary, tokens, task.budget)
    stage_sweeps = sweeps // STAGES
    price = lower = 0.0
    upper = None
    initial = None
    trajectories, history = [], []
    totals = {"build_ms": 0.0, "compile_ms": 0.0, "warm_sampling_ms": 0.0,
              "sampling_execution_ms": 0.0, "cold_first_execution_ms": 0.0,
              "warm_cached_execution_ms": 0.0}
    backend_total_ms = 0.0
    incumbent = None
    incumbent_energy = float("inf")
    dependency = None
    backend_metadata = None

    for stage in range(STAGES):
        stage_seed = int(seed) if stage == 0 else int(seed) + stage * 1_000_003
        batch = sample_qubo(
            original_unary + price * tokens / task.budget, task.J, stage_seed,
            chains=chains, sweeps=stage_sweeps, burn_in=stage_sweeps // 2,
            beta=3.5, initial=initial,
        )
        trajectories.append(batch.trajectories)
        initial = batch.trajectories[-1].copy()
        dependency, backend_metadata = batch.dependency, batch.metadata
        for name in totals:
            totals[name] += batch.timings.get(name, 0.0)
        backend_total_ms += batch.timings["total_ms"]
        flat = batch.trajectories.reshape(-1, len(tokens))
        used = flat @ tokens
        feasible = used <= task.budget
        empty_fraction = float(np.mean(~flat.any(axis=1)))
        # Deduplication only avoids repeated host scoring. It cannot alter the
        # available candidate set or introduce oracle-selected candidates.
        for candidate in np.unique(flat[feasible], axis=0):
            energy = task.energy(candidate)
            if energy < incumbent_energy:
                incumbent, incumbent_energy = candidate.copy(), energy
        next_price, lower, upper, reason = update_price(
            price=price, lower=lower, upper=upper,
            mean_tokens=float(used.mean()), empty_fraction=empty_fraction,
            budget=task.budget,
        )
        if stage == 0:
            # The first feedback step is replaced by the preregistered
            # independent-spin initializer, not a tuned continuation.
            next_price = estimate
            lower, upper = 0.0, None
            reason = "independent_spin_occupancy_initializer"
        history.append({
            "stage": stage, "seed": stage_seed, "price": price,
            "retained_samples": len(flat), "mean_tokens": float(used.mean()),
            "median_tokens": float(np.median(used)),
            "feasible_samples": int(feasible.sum()),
            "empty_fraction": empty_fraction,
            "incumbent_energy": incumbent_energy if incumbent is not None else None,
            "next_price": next_price, "update": reason,
            "timings": dict(batch.timings),
            "compile_cache_hit": batch.metadata.get("compile_cache_hit"),
        })
        price = next_price

    trajectory = np.concatenate(trajectories, axis=0)
    total_ms = (time.perf_counter() - started) * 1000
    totals.update(total_ms=total_ms, controller_ms=max(0.0, total_ms - backend_total_ms))
    flat = trajectory.reshape(-1, len(tokens))
    return {
        "trajectories": trajectory,
        "timings": totals,
        "dependency": dependency,
        "metadata": {
            "formulation": "adaptive_lagrangian_four_stage",
            "backend": "real_thrml_with_host_price_controller",
            "selection_objective": "frozen_original_task_energy",
            "nodes": len(tokens), "auxiliary_nodes": 0,
            "max_degree": backend_metadata["max_degree"],
            "color_blocks": backend_metadata["color_blocks"],
            "added_budget_edges": 0,
            "chains": chains, "sweeps": sweeps,
            "transitions_per_chain": sweeps,
            "sample_count": len(flat),
            "stage_history": history, "initial_price_estimate": estimate,
            "price_bounds": [0, MAX_PRICE],
            "empty_samples": int((~flat.any(axis=1)).sum()),
            "over_budget_samples": int(((flat @ tokens) > task.budget).sum()),
            "incumbent_mask": incumbent.tolist() if incumbent is not None else None,
            "incumbent_energy": incumbent_energy if incumbent is not None else None,
            "failure": "no_feasible_sample" if incumbent is None else None,
            "verifier_used_for_proposals": False,
            "extra_rescue_samples": 0,
            "hardware_claim": False,
        },
    }
