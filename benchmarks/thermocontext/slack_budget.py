"""Sparse exact budget encoding; an experiment, not a claim of good mixing.

For each item, a ripple-carry adder adds ``tokens[i] * x[i]`` to a binary
cumulative register. A final free slack register must add to the fixed budget.
Every local cell contributes ``penalty * (a + b + carry - sum - 2*out)^2``.
Overflow is fixed to zero. Equality copy chains bound item-bit fanout. Thus no
global squared budget and no all-to-all budget edges are introduced.

Nonnegative integer costs imply a zero-penalty completion exists exactly when
the item mask is within budget. At zero penalty the frozen base energy is
unchanged. ``penalty = 1 + sum(abs(base coefficients))`` puts every invalid
completion above the feasible empty pack, so every global ground state is
feasible. This does NOT guarantee feasibility at finite temperature, rapid
mixing, or a hardware benefit. In particular, single-spin Gibbs may be trapped
by these equality penalties. Feasible random initialization is explicitly
reported as a separate comparator; its work must not be credited to THRML.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import time

import numpy as np


@dataclass(frozen=True)
class Cell:
    terms: tuple[tuple[int, int], ...]
    constant: int = 0


@dataclass(frozen=True)
class Adder:
    previous: tuple[int | None, ...]
    addend: tuple[int | None, ...]
    outputs: tuple[int | None, ...]
    carries: tuple[int, ...]
    fixed_output: int | None = None


@dataclass
class SlackQubo:
    unary: np.ndarray
    J: np.ndarray
    constant: float
    base_unary: np.ndarray
    base_J: np.ndarray
    costs: np.ndarray
    budget: int
    width: int
    penalty: float
    labels: list[dict]
    cells: list[Cell]
    copies: list[tuple[int, int]]
    adders: list[Adder]
    slack_bits: tuple[int, ...]

    @property
    def n_items(self):
        return len(self.costs)

    def base_energy(self, mask):
        x = np.asarray(mask, dtype=float)
        return float(self.base_unary @ x + 0.5 * x @ self.base_J @ x)

    def energy(self, state):
        state = np.asarray(state, dtype=float)
        return float(self.unary @ state + 0.5 * state @ self.J @ state + self.constant)

    def constraint_violations(self, state):
        state = np.asarray(state, dtype=np.int64)
        return sum((cell.constant + sum(weight * state[index]
                    for index, weight in cell.terms)) ** 2 for cell in self.cells)

    def encode(self, mask):
        """Build a zero-penalty witness; never optimizes/selects the item mask."""
        raw = np.asarray(mask)
        if raw.shape != (self.n_items,) or not np.isin(raw, [0, 1]).all():
            raise ValueError("mask must be a binary vector with one bit per item")
        mask = raw.astype(bool)
        used = int(self.costs @ mask)
        if used > self.budget:
            raise ValueError("over-budget mask has no zero-penalty completion")
        state = np.zeros(len(self.unary), dtype=bool)
        state[:self.n_items] = mask
        for target, source in self.copies:
            state[target] = state[source]
        slack = self.budget - used
        for bit, index in enumerate(self.slack_bits):
            state[index] = bool((slack >> bit) & 1)
        for adder in self.adders:
            carry = 0
            for bit in range(self.width):
                a, b = adder.previous[bit], adder.addend[bit]
                total = (int(state[a]) if a is not None else 0)
                total += (int(state[b]) if b is not None else 0) + carry
                output = total & 1
                target = adder.outputs[bit]
                if target is not None:
                    state[target] = bool(output)
                elif output != ((int(adder.fixed_output) >> bit) & 1):
                    raise AssertionError("feasible mask failed fixed final output")
                carry = total >> 1
                if bit < self.width - 1:
                    state[adder.carries[bit]] = bool(carry)
            if carry:
                raise AssertionError("feasible nonnegative prefix overflowed")
        return state

    def metadata(self):
        degree = np.count_nonzero(self.J, axis=1)
        i, j = np.nonzero(np.triu(self.J, k=1))
        coefficients = np.concatenate((self.unary, self.J[i, j]))
        nonzero = np.abs(coefficients[coefficients != 0])
        base_coefficients = np.concatenate((self.base_unary,
                                            self.base_J[np.triu_indices(self.n_items, 1)]))
        base_max = float(np.max(np.abs(base_coefficients), initial=0))
        return {
            "formulation": "sparse_ripple_carry_explicit_slack",
            "node_count": len(self.unary),
            "original_node_count": self.n_items,
            "auxiliary_node_count": len(self.unary) - self.n_items,
            "edge_count": len(i),
            "base_edge_count": int(np.count_nonzero(np.triu(self.base_J, 1))),
            "budget_edge_count": len(i) - int(np.count_nonzero(np.triu(self.base_J, 1))),
            "max_degree": int(degree.max(initial=0)),
            "mean_degree": float(degree.mean()),
            "auxiliary_max_degree": int(degree[self.n_items:].max(initial=0)),
            "auxiliary_mean_degree": float(degree[self.n_items:].mean()),
            "register_width": self.width,
            "local_constraint_count": len(self.cells),
            "penalty_weight": self.penalty,
            "coefficient_min_nonzero_abs": float(nonzero.min()),
            "coefficient_max_abs": float(nonzero.max()),
            "coefficient_dynamic_range": float(nonzero.max() / nonzero.min()),
            "base_coefficient_max_abs": base_max,
            "penalty_to_base_max_ratio": self.penalty / base_max if base_max else None,
            "nodes": [dict(label, degree=int(degree[k])) for k, label in enumerate(self.labels)],
            "edges": [{"i": int(a), "j": int(b), "coefficient": float(self.J[a, b])}
                      for a, b in zip(i, j)],
            "ground_state_feasibility_guarantee": True,
            "finite_temperature_feasibility_guarantee": False,
            "mixing_guarantee": False,
            "hardware_speed_or_energy_claim": False,
            "base_objective_unchanged_on_zero_penalty_completions": True,
            "limitation": "Equality penalties can freeze colored single-spin Gibbs; report warm-start baseline and motion.",
        }

    def graph_receipt(self):
        """Self-contained constraints and graph, independent of this constructor.

        SHA256 covers the entire returned dictionary encoded with
        ``json.dumps(graph, sort_keys=True, separators=(',', ':'),
        allow_nan=False).encode('utf-8')``. The digest is stored separately.
        Item coordinates are always the first ``n_items`` state columns.
        """
        i, j = np.nonzero(np.triu(self.base_J, 1))
        metadata = self.metadata()
        return {
            "schema": "thermocontext.sparse_slack_graph.v1",
            "n_items": self.n_items,
            "n_nodes": len(self.unary),
            "item_indices": list(range(self.n_items)),
            "node_order": "index is the state-column index; item prefix followed by declared auxiliaries",
            "energy_convention": "constant + sum(unary[i]*x[i]) + sum(edge.coefficient*x[edge.i]*x[edge.j]); edges occur once",
            "construction_dtype": "float64",
            "sampler_submission_dtype": "float32",
            "costs": self.costs.tolist(),
            "budget": self.budget,
            "register_width": self.width,
            "penalty": self.penalty,
            "base_unary": self.base_unary.tolist(),
            "base_edges": [{"i": int(a), "j": int(b), "coefficient": float(self.base_J[a, b])}
                           for a, b in zip(i, j)],
            "expanded_unary": self.unary.tolist(),
            "expanded_edges": metadata["edges"],
            "constant": self.constant,
            "nodes": metadata["nodes"],
            "cells": [{"terms": [[index, coefficient] for index, coefficient in cell.terms],
                       "constant": cell.constant} for cell in self.cells],
            "copy_equalities": [[target, source] for target, source in self.copies],
            "slack_indices": list(self.slack_bits),
        }


def build_slack_qubo(unary, J, costs, budget):
    """Accepts only public coefficients, integer costs, and the token budget."""
    unary, J = np.asarray(unary, dtype=float), np.asarray(J, dtype=float)
    raw_costs = np.asarray(costs)
    if unary.ndim != 1 or not len(unary) or J.shape != (len(unary), len(unary)):
        raise ValueError("nonempty unary and square matching J required")
    if not np.isfinite(unary).all() or not np.isfinite(J).all():
        raise ValueError("coefficients must be finite")
    if not np.allclose(J, J.T, rtol=0, atol=1e-10) or np.any(np.diag(J)):
        raise ValueError("J must be symmetric with zero diagonal")
    if (raw_costs.shape != unary.shape or not np.isfinite(raw_costs).all()
            or np.any(raw_costs < 0) or np.any(raw_costs != np.floor(raw_costs))):
        raise ValueError("costs must be matching nonnegative integers")
    if not isinstance(budget, (int, np.integer)) or budget < 0:
        raise ValueError("budget must be a nonnegative integer")
    costs = raw_costs.astype(np.int64)
    width = max(1, int(budget).bit_length(), int(costs.max()).bit_length())
    penalty = 1.0 + float(np.abs(unary).sum() + np.abs(np.triu(J, 1)).sum())
    labels = [{"index": i, "name": f"item_{i}", "role": "item"} for i in range(len(unary))]
    cells, copies, adders = [], [], []

    def node(name, role):
        index = len(labels)
        labels.append({"index": index, "name": name, "role": role})
        return index

    def add_stage(previous, addend, stage, fixed_output=None):
        outputs = (tuple(node(f"sum_{stage}_{k}", "cumulative_bit") for k in range(width))
                   if fixed_output is None else (None,) * width)
        carries = tuple(node(f"carry_{stage}_{k}", "carry") for k in range(width - 1))
        for bit in range(width):
            terms = []
            for index in (previous[bit], addend[bit], carries[bit - 1] if bit else None):
                if index is not None:
                    terms.append((index, 1))
            if outputs[bit] is not None:
                terms.append((outputs[bit], -1))
            if bit < width - 1:
                terms.append((carries[bit], -2))
            constant = 0 if fixed_output is None else -((fixed_output >> bit) & 1)
            cells.append(Cell(tuple(terms), constant))
        adders.append(Adder(previous, addend, outputs, carries, fixed_output))
        return outputs

    previous = (None,) * width
    for item, cost in enumerate(costs):
        addend = [None] * width
        source = item
        first = True
        for bit in range(width):
            if (int(cost) >> bit) & 1:
                if first:
                    target, first = item, False
                else:
                    target = node(f"item_{item}_copy_{bit}", "item_copy")
                    copies.append((target, source))
                    cells.append(Cell(((target, 1), (source, -1))))
                    source = target
                addend[bit] = target
        previous = add_stage(previous, tuple(addend), item)
    slack_bits = tuple(node(f"slack_{k}", "slack") for k in range(width))
    add_stage(previous, slack_bits, "slack", fixed_output=int(budget))

    expanded_unary, expanded_J = np.zeros(len(labels)), np.zeros((len(labels), len(labels)))
    expanded_unary[:len(unary)], expanded_J[:len(unary), :len(unary)] = unary, J
    constant = 0.0
    for cell in cells:
        constant += penalty * cell.constant ** 2
        for index, coefficient in cell.terms:
            expanded_unary[index] += penalty * (coefficient ** 2 + 2 * cell.constant * coefficient)
        for position, (a, coefficient_a) in enumerate(cell.terms):
            for b, coefficient_b in cell.terms[position + 1:]:
                weight = 2 * penalty * coefficient_a * coefficient_b
                expanded_J[a, b] += weight
                expanded_J[b, a] += weight
    return SlackQubo(expanded_unary, expanded_J, constant, unary.copy(), J.copy(), costs,
                     int(budget), width, penalty, labels, cells, copies, adders, slack_bits)


def initial_states(model, *, seed, chains):
    """Budget-feasible random masks, independent of relevance and verifier labels."""
    rng = np.random.default_rng(seed)
    states = []
    for _ in range(chains):
        mask, used = np.zeros(model.n_items, dtype=bool), 0
        for item in rng.permutation(model.n_items):
            if rng.random() < 0.35 and used + model.costs[item] <= model.budget:
                mask[item], used = True, used + int(model.costs[item])
        states.append(model.encode(mask))
    return np.asarray(states, dtype=bool)


def propose(task, seed, *, chains=64, sweeps=160):
    """Run exactly one bounded real-THRML job; the caller owns frozen evaluation."""
    from benchmarks.thermocontext.thrml_backend import sample_qubo

    if not 1 <= chains <= 64 or not 2 <= sweeps <= 160:
        raise ValueError("preregistered cap: 1..64 chains and 2..160 sweeps")
    started = time.perf_counter()
    model = build_slack_qubo(task.unary(), task.J, [item.tokens for item in task.items], task.budget)
    initial = initial_states(model, seed=seed, chains=chains)
    formulation_ms = 1000 * (time.perf_counter() - started)
    batch = sample_qubo(model.unary, model.J, seed, chains=chains, sweeps=sweeps,
                        beta=3.5, initial=initial)
    raw = np.asarray(batch.trajectories, dtype=bool)
    trajectories = raw[..., :model.n_items]
    flat = raw.reshape(-1, raw.shape[-1]).astype(np.int64)
    valid_constraints = np.ones(len(flat), dtype=bool)
    for cell in model.cells:
        residual = np.full(len(flat), cell.constant, dtype=np.int64)
        for index, coefficient in cell.terms:
            residual += coefficient * flat[:, index]
        valid_constraints &= residual == 0
    metadata = model.metadata()
    graph = model.graph_receipt()
    graph_digest = hashlib.sha256(json.dumps(graph, sort_keys=True, separators=(",", ":"),
                                             allow_nan=False).encode("utf-8")).hexdigest()
    metadata.update({
        "backend": batch.metadata,
        "dependency": batch.dependency,
        "initialization": "random permutation, include probability 0.35 when budget permits; deterministic zero-penalty encoding",
        "initial_baseline": {"label": "initialization_only_random_feasible_packs",
                             "sampler_transitions": 0, "pack_count": chains,
                             "state_field": "initial_item_states"},
        "auxiliary_graph_sha256": graph_digest,
        "auxiliary_graph_hash_serialization": "json.dumps(graph,sort_keys=True,separators=(',',':'),allow_nan=False).encode('utf-8')",
        "auxiliary_trajectory_axes": ["retained_sweep", "chain", "node_index"],
        "initial_full_state_axes": ["chain", "node_index"],
        "chain_order": "initial_full_states[c] initializes auxiliary_trajectories[:,c,:]; order is preserved",
        "initial_pack_count": chains,
        "initial_unique_pack_count": len(np.unique(initial[:, :model.n_items], axis=0)),
        "initial_best_feasible_energy": min(model.base_energy(state[:model.n_items]) for state in initial),
        "retained_item_change_fraction": float(np.any(trajectories != initial[None, :, :model.n_items], axis=-1).mean()),
        "retained_unique_pack_count": len(np.unique(trajectories.reshape(-1, model.n_items), axis=0)),
        "constraint_valid_sample_fraction": float(valid_constraints.mean()),
        "samples_retained": len(flat),
        "chains": chains,
        "sweeps": sweeps,
        "no_verifier_used_for_proposals": True,
    })
    timings = dict(batch.timings, formulation_build_ms=formulation_ms)
    timings["total_ms"] = 1000 * (time.perf_counter() - started)
    return {"trajectories": trajectories, "timings": timings, "metadata": metadata,
            "initial_item_states": initial[:, :model.n_items],
            "auxiliary_trajectories": raw, "initial_full_states": initial,
            "auxiliary_graph": graph, "auxiliary_graph_sha256": graph_digest}
