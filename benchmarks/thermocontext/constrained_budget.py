"""Sparse THRML proposals with an explicit *host-side* budget controller.

This is a hybrid constrained-output experiment, not a constrained Gibbs kernel.
Real THRML samples the original public QUBO without additional couplings. The
host then deletes selected items until the token constraint holds, choosing the
smallest original-energy damage per released token and recomputing the marginal
after each deletion. Projection changes the output distribution. It has neither
an exact-optimality guarantee nor a TSU implementation/speed/energy claim.

The controller never adds items or consults required IDs, facts, or the verifier.
Items individually exceeding the budget are removed first, preventing those
impossible items from forcing the deletion of all feasible support. A proposal
that contains at least one individually feasible item therefore stays nonempty;
an empty THRML proposal stays empty and is reported as such. There is no refill,
resampling, rescue search, or verifier-based selection.

For causal attribution, run this same controller on prototype and independent
random proposals. The controller's global token bookkeeping, comparisons, and
deletions are CPU work, even though THRML's proposal graph remains sparse.
"""

from __future__ import annotations

import time

import numpy as np


def project_arrays(unary, J, tokens, budget, trajectories):
    """Return delete-only feasible proposals and host-controller accounting.

    Inputs are exclusively the public objective, token costs, and proposals.
    No RNG or sampling occurs here. Leading proposal dimensions are preserved.
    """
    unary = np.asarray(unary, dtype=np.float64)
    J = np.asarray(J, dtype=np.float64)
    raw_tokens = np.asarray(tokens)
    raw = np.asarray(trajectories)
    if unary.ndim != 1 or unary.size == 0 or not np.isfinite(unary).all():
        raise ValueError("unary must be a nonempty finite vector")
    n = len(unary)
    if (
        J.shape != (n, n)
        or not np.isfinite(J).all()
        or not np.allclose(J, J.T, rtol=0, atol=1e-9)
        or np.any(np.diag(J) != 0)
    ):
        raise ValueError("J must be finite, symmetric, and zero-diagonal")
    if (
        raw_tokens.shape != (n,)
        or not np.isfinite(raw_tokens).all()
        or np.any(raw_tokens <= 0)
        or np.any(raw_tokens != np.floor(raw_tokens))
    ):
        raise ValueError("tokens must be positive integer costs")
    if isinstance(budget, bool) or not np.isscalar(budget) or not np.isfinite(budget):
        raise ValueError("budget must be a nonnegative integer")
    if budget < 0 or int(budget) != budget:
        raise ValueError("budget must be a nonnegative integer")
    if raw.ndim < 2 or raw.shape[-1] != n or not np.isin(raw, [0, 1]).all():
        raise ValueError("trajectories must have binary proposals in their last dimension")
    tokens = raw_tokens.astype(np.int64)
    budget = int(budget)
    proposals = raw.astype(bool, copy=True).reshape(-1, n)
    raw_empty = ~proposals.any(axis=1)
    raw_budget_valid = proposals @ tokens <= budget
    counts_before = proposals.sum(axis=1)

    # Legality, not optimization: these items cannot occur in any feasible pack.
    impossible = tokens > budget
    impossible_deleted = int(proposals[:, impossible].sum())
    proposals[:, impossible] = False
    had_legal_support = proposals.any(axis=1)
    used = proposals @ tokens
    marginals = unary[None, :] + proposals @ J.T
    active = np.flatnonzero(used > budget)
    rounds = 0
    while active.size:
        damage_per_token = -marginals[active] / tokens[None, :]
        choices = np.argmin(np.where(proposals[active], damage_per_token, np.inf), axis=1)
        proposals[active, choices] = False
        used[active] -= tokens[choices]
        # Removing i changes every remaining marginal by -J[:, i]. No new
        # interaction is introduced; dense numpy storage is not a dense graph.
        marginals[active] -= J[choices, :]
        active = active[used[active] > budget]
        rounds += 1
        if rounds > n:
            raise AssertionError("delete-only projection exceeded its fixed N-step bound")

    counts_after = proposals.sum(axis=1)
    deletions = counts_before - counts_after
    empty_after = counts_after == 0
    stats = {
        "proposal_count": len(proposals),
        "raw_budget_valid_count": int(raw_budget_valid.sum()),
        "projected_budget_valid_count": int((used <= budget).sum()),
        "raw_empty_count": int(raw_empty.sum()),
        "projected_empty_count": int(empty_after.sum()),
        "empty_projection_from_nonempty_legal_support_count": int((empty_after & had_legal_support).sum()),
        "deleted_bits": int(deletions.sum()),
        "individually_infeasible_deleted_bits": impossible_deleted,
        "max_deletions_per_proposal": int(deletions.max(initial=0)),
        "mean_deletions_per_proposal": float(deletions.mean()) if len(deletions) else 0.0,
        "controller_rounds": rounds,
        "controller_max_rounds": n,
        "refill_count": 0,
        "resample_count": 0,
        "verifier_calls": 0,
    }
    return proposals.reshape(raw.shape), stats


def project_budget(task, trajectories):
    """Shared public-objective controller for THRML/prototype/random controls."""
    return project_arrays(
        task.unary(),
        task.J,
        [item.tokens for item in task.items],
        task.budget,
        trajectories,
    )


def propose(task, seed, *, chains=64, sweeps=160):
    """Use real THRML once at the fixed draw cap, then apply host projection."""
    from benchmarks.thermocontext.thrml_backend import sample_qubo

    started = time.perf_counter()
    batch = sample_qubo(task.unary(), task.J, seed, chains=chains, sweeps=sweeps)
    projection_started = time.perf_counter()
    projected, stats = project_budget(task, batch.trajectories)
    projection_ms = (time.perf_counter() - projection_started) * 1000
    return {
        "trajectories": projected,
        "raw_trajectories": batch.trajectories,
        "timings": {
            **batch.timings,
            "host_projection_ms": projection_ms,
            "total_ms": (time.perf_counter() - started) * 1000,
        },
        "dependency": batch.dependency,
        "metadata": {
            **batch.metadata,
            "formulation": "thrml_host_budget_projection",
            "budget_enforcement": "host_delete_only_projection",
            "proposal_objective": "unchanged_original_qubo",
            "projected_distribution": "not_constrained_gibbs",
            "budget_coupling_count": 0,
            "budget_auxiliary_variable_count": 0,
            "host_global_budget_bookkeeping": True,
            "tsu_budget_enforcement_claim": False,
            "controller": stats,
        },
    }
