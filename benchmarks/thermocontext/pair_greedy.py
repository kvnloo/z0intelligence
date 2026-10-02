"""Deterministic controls for the original public ThermoContext objective.

``pair_greedy`` is only an improving one/two-item addition heuristic.
``component_dp`` is a structured exact baseline: enumerate each bounded graph
component, then solve the integer token-budget knapsack over their local states.
The latter exploits the frozen generator's disconnected structure; it is not a
sampler or evidence that general sparse QUBOs are cheaply exactly solvable.

Both read only unary coefficients, J, item token costs, and the budget. No facts,
required IDs, verifier, or exact reference solution are used. Selection uses
float64 arithmetic on public coefficients; callers must evaluate returned masks
with the unchanged Task.energy and compare float32 results at tolerance 1e-5.
"""

from __future__ import annotations

import numpy as np

ENERGY_FLOAT_TOLERANCE = 1e-5


def _public_problem(task):
    unary = np.asarray(task.unary(), dtype=np.float64)
    J = np.asarray(task.J, dtype=np.float64)
    costs = np.asarray([item.tokens for item in task.items])
    budget = task.budget
    if unary.ndim != 1 or not len(unary) or not np.isfinite(unary).all():
        raise ValueError("unary must be a nonempty finite vector")
    n = len(unary)
    if (
        J.shape != (n, n) or not np.isfinite(J).all()
        or not np.array_equal(J, J.T) or np.any(np.diag(J) != 0)
    ):
        raise ValueError("J must be finite, symmetric, and zero-diagonal")
    if (
        costs.shape != (n,) or not np.isfinite(costs).all()
        or np.any(costs <= 0) or np.any(costs != np.floor(costs))
    ):
        raise ValueError("token costs must be positive integers")
    if (
        isinstance(budget, bool) or not np.isscalar(budget)
        or not np.isfinite(budget) or budget < 0 or int(budget) != budget
    ):
        raise ValueError("budget must be a nonnegative integer")
    return unary, J, costs.astype(np.int64), int(budget)


def pair_greedy(task):
    """Add the best feasible improving singleton or pair until none remains.

    This has no global-optimum guarantee. Changes smaller than the reported
    float32 comparison tolerance are treated as no improvement. Exact ties
    prefer singletons, then ascending item indices; no deletion occurs.
    """
    unary, J, costs, budget = _public_problem(task)
    selected = np.zeros(len(unary), dtype=bool)
    used = 0
    while True:
        marginal = unary + J @ selected
        available = np.flatnonzero(~selected)
        best = None
        best_delta = -ENERGY_FLOAT_TOLERANCE
        for i in available:
            if used + costs[i] <= budget and marginal[i] < best_delta:
                best, best_delta = (i,), float(marginal[i])
        for pos, i in enumerate(available):
            for j in available[pos + 1:]:
                if used + costs[i] + costs[j] > budget:
                    continue
                delta = float(marginal[i] + marginal[j] + J[i, j])
                if delta < best_delta:
                    best, best_delta = (i, j), delta
        if best is None:
            return selected
        selected[list(best)] = True
        used += int(costs[list(best)].sum())


def _components(J):
    unseen = set(range(len(J)))
    components = []
    while unseen:
        start = min(unseen)
        unseen.remove(start)
        stack = [start]
        component = []
        while stack:
            item = stack.pop()
            component.append(item)
            # Do not drop tiny nonzero interactions at a numerical threshold.
            neighbors = sorted(set(np.flatnonzero(J[item] != 0)) & unseen)
            for neighbor in neighbors:
                unseen.remove(neighbor)
                stack.append(neighbor)
        components.append(tuple(sorted(component)))
    return components


def component_dp(task, max_component_size=16):
    """Solve an additive-component integer-budget QUBO exactly in float64.

    Local enumeration costs sum(2**component_size), not 2**N. Each component
    contributes the least-energy state at each achievable token cost. A sparse
    cost-indexed DP then combines them. All components are checked against the
    explicit cap *before* enumeration. Worst-case DP size is budget+1; runtime
    is pseudo-polynomial in the integer budget and exponential in the cap.
    """
    unary, J, costs, budget = _public_problem(task)
    if isinstance(max_component_size, bool) or not isinstance(max_component_size, int) or max_component_size < 1:
        raise ValueError("max_component_size must be a positive integer")
    components = _components(J)
    for component in components:
        if len(component) > max_component_size:
            raise ValueError(f"component size {len(component)} exceeds cap {max_component_size}")

    local_tables = []
    enumerated = 0
    local_budget_feasible = 0
    for component in components:
        indices = np.asarray(component, dtype=int)
        local_unary = unary[indices]
        local_J = J[np.ix_(indices, indices)]
        local_costs = costs[indices]
        local_best = {}
        # A Python loop bounds resident memory independently of 2**cap. The
        # caller's cap still determines the explicitly documented runtime.
        for bits in range(1 << len(component)):
            enumerated += 1
            mask = np.asarray([(bits >> i) & 1 for i in range(len(component))], dtype=np.float64)
            cost = int(local_costs @ mask)
            if cost > budget:
                continue
            local_budget_feasible += 1
            energy = float(local_unary @ mask + 0.5 * mask @ local_J @ mask)
            old = local_best.get(cost)
            if old is None or energy < old[0]:
                local_best[cost] = (energy, bits)
        local_tables.append(local_best)

    # cost -> (energy, tuple of component masks). Equal-energy replacements
    # retain the first deterministic traversal result, without epsilon pruning.
    states = {0: (0.0, ())}
    states_visited = 1
    transitions = 0
    max_states = 1
    for local_best in local_tables:
        new_states = {}
        for cost, (energy, masks) in sorted(states.items()):
            for local_cost, (local_energy, local_bits) in sorted(local_best.items()):
                transitions += 1
                new_cost = cost + local_cost
                if new_cost > budget:
                    continue
                new_energy = energy + local_energy
                old = new_states.get(new_cost)
                if old is None or new_energy < old[0]:
                    new_states[new_cost] = (new_energy, masks + (local_bits,))
        states = new_states
        states_visited += len(states)
        max_states = max(max_states, len(states))

    selected_cost, (selected_energy, component_masks) = min(
        states.items(), key=lambda entry: (entry[1][0], entry[0], entry[1][1])
    )
    selected = np.zeros(len(unary), dtype=bool)
    for component, bits in zip(components, component_masks):
        for position, index in enumerate(component):
            selected[index] = bool((bits >> position) & 1)
    metadata = {
        "algorithm": "structured_exact_component_token_knapsack",
        "sampler": False,
        "proof_status": "additive_components_integer_budget",
        "component_sizes": [len(component) for component in components],
        "max_component_size": max(map(len, components)),
        "component_size_cap": max_component_size,
        "enumerated_local_states": enumerated,
        "local_budget_feasible_states": local_budget_feasible,
        "local_cost_states": sum(map(len, local_tables)),
        "dp_states": len(states),
        "dp_states_visited": states_visited,
        "max_dp_states": max_states,
        "dp_transitions_evaluated": transitions,
        "selected_tokens": selected_cost,
        "float64_objective_energy": selected_energy,
        "energy_float_tolerance": ENERGY_FLOAT_TOLERANCE,
        "selection_arithmetic": "float64; externally reevaluate with unchanged Task.energy",
        "limitation": "exponential in bounded component size; pseudo-polynomial in integer budget",
        "verifier_calls": 0,
    }
    return selected, metadata
