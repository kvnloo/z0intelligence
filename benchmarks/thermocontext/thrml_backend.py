"""Pinned Extropic THRML sampler for a sparse binary quadratic objective.

Only the representation and measurement live here. All transitions are produced
by upstream THRML's IsingSamplingProgram/sample_states, running on JAX CPU. This
is neither TSU execution nor evidence of hardware latency or energy savings.
"""
from __future__ import annotations

import dataclasses
import functools
import hashlib
import importlib.metadata
import json
from pathlib import Path
import time

import jax
import jax.numpy as jnp
import numpy as np
from thrml import Block, SamplingSchedule, SpinNode, sample_states
from thrml.models import IsingEBM, IsingSamplingProgram
from thrml.models.discrete_ebm import SpinEBMFactor

THRML_REVISION = "dfb3bde16962f9637a1845ccb2bea80aaa19e4bd"


class _UnaryIsingEBM(IsingEBM):
    # Upstream 0.1.4 constructs an invalid empty pair factor on an edgeless
    # graph. Omit that factor; the genuine THRML conditional/sampler is unchanged.
    @property
    def factors(self):
        return [SpinEBMFactor([Block(self.nodes)], self.beta * self.biases)]


@dataclasses.dataclass
class SampleBatch:
    trajectories: np.ndarray
    timings: dict
    dependency: dict
    metadata: dict

    @property
    def trajectory(self):
        return self.trajectories


@functools.lru_cache(maxsize=1)
def dependency_identity():
    distribution = importlib.metadata.distribution("thrml")
    direct = json.loads(distribution.read_text("direct_url.json") or "{}")
    revision = direct.get("vcs_info", {}).get("commit_id")
    digest = hashlib.sha256()
    for file in sorted(distribution.files or [], key=str):
        if str(file).startswith("thrml/") and str(file).endswith(".py"):
            digest.update(str(file).encode() + b"\0")
            digest.update(Path(distribution.locate_file(file)).read_bytes())
    return {
        "package": "thrml", "version": distribution.version,
        "upstream": "https://github.com/extropic-ai/thrml",
        "revision": revision, "expected_revision": THRML_REVISION,
        "revision_verified": revision == THRML_REVISION,
        "installed_python_sha256": digest.hexdigest(),
        "jax": jax.__version__, "jaxlib": importlib.metadata.version("jaxlib"),
        "equinox": importlib.metadata.version("equinox"),
        "numpy": np.__version__, "platform": jax.default_backend(),
        "hardware_claim": False,
    }


def _validate(unary, J):
    unary = np.asarray(unary, dtype=np.float32)
    J = np.asarray(J, dtype=np.float32)
    if unary.ndim != 1 or not len(unary) or J.shape != (len(unary), len(unary)):
        raise ValueError("unary must be nonempty 1D and J must be square with matching size")
    if not np.isfinite(unary).all() or not np.isfinite(J).all():
        raise ValueError("QUBO coefficients must be finite")
    if not np.array_equal(J, J.T) or np.any(np.diag(J) != 0):
        raise ValueError("J must be symmetric with a zero diagonal")
    return unary, J


def qubo_to_ising(unary, J):
    """E(x)=u*x+sum(i<j)Jij*xixj, x=(s+1)/2.

    THRML's energy is -beta*(b*s+sum(wij*sisj)); it equals
    beta*(E(x)-offset), so its conditional and Boltzmann law are unchanged.
    """
    unary, J = _validate(unary, J)
    edges = tuple(zip(*np.nonzero(np.triu(J, 1))))
    biases = -0.5 * unary - 0.25 * J.sum(axis=1)
    weights = np.asarray([-J[i, j] / 4 for i, j in edges], dtype=np.float32)
    offset = float(unary.sum() / 2 + np.triu(J, 1).sum() / 4)
    return biases, edges, weights, offset


def _color(n, edges):
    neighbors = [set() for _ in range(n)]
    for i, j in edges:
        neighbors[i].add(j)
        neighbors[j].add(i)
    colors = {}
    for vertex in sorted(range(n), key=lambda i: len(neighbors[i]), reverse=True):
        occupied = {colors[j] for j in neighbors[vertex] if j in colors}
        color = 0
        while color in occupied:
            color += 1
        colors[vertex] = color
    blocks = tuple(tuple(i for i in range(n) if colors[i] == c)
                   for c in range(max(colors.values()) + 1))
    return blocks, max(map(len, neighbors), default=0)


_RUNNERS = {}


def _runner(n, edges, chains, sweeps, burn_in):
    cache_key = (n, edges, chains, sweeps, burn_in)
    if cache_key in _RUNNERS:
        return _RUNNERS[cache_key], True
    colors, max_degree = _color(n, edges)
    nodes = [SpinNode() for _ in range(n)]
    free_blocks = [Block([nodes[i] for i in color]) for color in colors]
    edge_nodes = [(nodes[i], nodes[j]) for i, j in edges]
    # THRML observes immediately after warmup. This is deliberately +1:
    # prototype [80:] contains states after transitions 81 through 160.
    schedule = SamplingSchedule(burn_in + 1, sweeps - burn_in, 1)
    observe = [Block(nodes)]

    def run(key, biases, weights, beta, initial):
        model_type = IsingEBM if edges else _UnaryIsingEBM
        model = model_type(nodes, edge_nodes, biases, weights, beta)
        program = IsingSamplingProgram(model, free_blocks, [])
        keys = jax.random.split(key, chains)

        def chain(chain_key, state):
            init = [state[jnp.asarray(color)] for color in colors]
            return sample_states(chain_key, program, schedule, init, [], observe)[0]

        return jnp.swapaxes(jax.vmap(chain)(keys, initial), 0, 1)

    entry = {"function": jax.jit(run), "compiled": None,
             "colors": colors, "max_degree": max_degree}
    _RUNNERS[cache_key] = entry
    return entry, False


def sample_qubo(unary, J, seed, *, chains=64, sweeps=160, burn_in=None,
                beta=3.5, initial=None):
    """Return retained states and separately measured compile/execution costs.

    A compile-only lower/compile call precedes exactly one executed trajectory.
    Coefficients are dynamic arguments; cached topology cannot freeze old weights.
    No verifier, budget rejection, or final-candidate selection occurs here.
    """
    started = time.perf_counter()
    unary, J = _validate(unary, J)
    burn_in = sweeps // 2 if burn_in is None else burn_in
    if not isinstance(chains, int) or chains < 1 or not isinstance(sweeps, int) or sweeps < 1:
        raise ValueError("chains and sweeps must be positive integers")
    if not isinstance(burn_in, int) or not 0 <= burn_in < sweeps:
        raise ValueError("burn_in must be an integer in [0,sweeps)")
    if not np.isfinite(beta) or beta <= 0:
        raise ValueError("beta must be positive and finite")
    identity = dependency_identity()
    if not identity["revision_verified"]:
        raise RuntimeError("Install the exact pinned THRML VCS revision before running this experiment")
    biases, edges, weights, _ = qubo_to_ising(unary, J)
    entry, cache_hit = _runner(len(unary), edges, chains, sweeps, burn_in)
    key, init_key = jax.random.split(jax.random.key(int(seed)))
    if initial is None:
        initial_jax = jax.random.bernoulli(init_key, 0.35, (chains, len(unary)))
    else:
        initial = np.asarray(initial, dtype=bool)
        if initial.shape != (chains, len(unary)):
            raise ValueError("initial must have shape (chains,N)")
        initial_jax = jnp.asarray(initial)
    args = (key, jnp.asarray(biases), jnp.asarray(weights),
            jnp.asarray(beta, dtype=jnp.float32), initial_jax)
    jax.block_until_ready(args)
    build_ms = (time.perf_counter() - started) * 1000
    compile_ms = 0.0
    if entry["compiled"] is None:
        compile_start = time.perf_counter()
        entry["compiled"] = entry["function"].lower(*args).compile()
        compile_ms = (time.perf_counter() - compile_start) * 1000
    sample_start = time.perf_counter()
    trajectories = np.asarray(entry["compiled"](*args), dtype=bool)
    warm_ms = (time.perf_counter() - sample_start) * 1000
    return SampleBatch(
        trajectories,
        {"build_ms": build_ms, "compile_ms": compile_ms,
         "warm_sampling_ms": warm_ms,
         "sampling_execution_ms": warm_ms,
         "cold_first_execution_ms": 0.0 if cache_hit else warm_ms,
         "warm_cached_execution_ms": warm_ms if cache_hit else 0.0,
         "total_ms": (time.perf_counter() - started) * 1000},
        dict(identity),
        {"backend": "thrml.IsingSamplingProgram/sample_states",
         "nodes": len(unary), "edges": len(edges),
         "max_degree": entry["max_degree"], "graph_degree": entry["max_degree"],
         "color_blocks": len(entry["colors"]),
         "chains": chains, "sweeps": sweeps, "burn_in": burn_in,
         "transitions_per_chain": sweeps, "first_retained_transition": burn_in + 1,
         "sample_count": chains * (sweeps - burn_in),
         "compile_cache_hit": cache_hit, "beta": beta,
         "initial_probability": 0.35 if initial is None else None,
         "timing_note": "warm_sampling_ms is legacy execution-total; use cold_first_execution_ms and warm_cached_execution_ms for the split",
         "executed_trajectories": 1},
    )
