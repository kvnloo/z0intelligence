from __future__ import annotations

import dataclasses
import json
import math
import time

import jax
import jax.numpy as jnp
import numpy as np

REL_W = 0.80
TOKEN_PRICE = 2.50
BETA = 3.5
THERMO_CHAINS = 64
THERMO_SWEEPS = 160
THERMO_SEEDS = 8


@dataclasses.dataclass(frozen=True)
class Item:
    id: str
    tokens: int
    relevance: float
    facts: dict
    necessary: bool = False


@dataclasses.dataclass
class Task:
    task_id: str
    items: list[Item]
    required_ids: tuple[str, ...]
    expected: str
    budget: int
    J: np.ndarray

    def unary(self):
        return np.asarray(
            [
                TOKEN_PRICE * (it.tokens / self.budget) - REL_W * it.relevance
                for it in self.items
            ],
            dtype=np.float32,
        )

    def energy(self, mask: np.ndarray) -> float:
        x = mask.astype(np.float32)
        return float(self.unary() @ x + 0.5 * x @ self.J @ x)

    def tokens(self, mask):
        return int(sum(it.tokens for it, take in zip(self.items, mask) if take))

    def selected_ids(self, mask):
        return {it.id for it, take in zip(self.items, mask) if take}

    def verify(self, mask):
        ids = self.selected_ids(mask)
        if not set(self.required_ids).issubset(ids):
            return False

        facts = {}
        for it, take in zip(self.items, mask):
            if take:
                facts.update(it.facts)

        if facts.get("_insufficient") is True:
            return False
        if (
            "rfc_revision" not in facts
            or "superseded" not in facts
            or "approved" not in facts
        ):
            return False

        if facts["superseded"] is True or facts["approved"] is not True:
            pred = "SUPERSEDED"
        else:
            pred = f"rev-{int(facts['rfc_revision'])}"
        return pred == self.expected


def add_edge(J, i, j, w):
    J[i, j] += w
    J[j, i] += w


def make_task(N: int, seed: int) -> Task:
    rng = np.random.default_rng(seed + 1000 * N)
    rev = 3 + seed % 5

    # Stale facts intentionally appear before current facts. The full champion
    # remains correct, but a bounded selector can still waste budget on them.
    base = [
        Item("stale_rfc", 24 + seed % 5, 0.96, {"rfc_revision": rev - 1}),
        Item("old_supersede", 22 + (seed * 2) % 6, 0.90, {"superseded": True}),
        Item(
            "dup_summary",
            28 + (seed * 3) % 7,
            0.84,
            {"stale_summary": f"rev-{rev - 1}"},
        ),
        Item(
            "rfc_current",
            42 + (seed % 7),
            0.53 + 0.02 * rng.random(),
            {"rfc_revision": rev},
            True,
        ),
        Item(
            "supersede_current",
            36 + (seed % 5),
            0.48 + 0.02 * rng.random(),
            {"superseded": False},
            True,
        ),
        Item(
            "approval_current",
            34 + ((seed + 2) % 5),
            0.46 + 0.02 * rng.random(),
            {"approved": True},
            True,
        ),
    ]

    while len(base) < N:
        k = len(base)
        base.append(
            Item(
                f"noise_{k}",
                int(rng.integers(18, 39)),
                float(rng.uniform(0.50, 0.82)),
                {f"noise_{k}": int(seed)},
            )
        )

    req_tokens = sum(x.tokens for x in base if x.necessary)
    budget = req_tokens + 32 + (seed % 4) * 5
    J = np.zeros((N, N), dtype=np.float32)
    idx = {x.id: i for i, x in enumerate(base)}

    # Pairwise complementarity deliberately creates a shallow global-search
    # barrier: each required fact is individually unattractive under the token
    # price, while the coherent set is favorable together.
    for a, b in [
        ("rfc_current", "supersede_current"),
        ("rfc_current", "approval_current"),
        ("supersede_current", "approval_current"),
    ]:
        add_edge(J, idx[a], idx[b], -0.72 - 0.04 * rng.random())

    add_edge(J, idx["stale_rfc"], idx["rfc_current"], 0.70)
    add_edge(J, idx["old_supersede"], idx["supersede_current"], 0.66)
    add_edge(J, idx["dup_summary"], idx["rfc_current"], 0.42)

    noise = list(range(6, N))
    rng.shuffle(noise)
    for a, b in zip(noise[::2], noise[1::2]):
        add_edge(J, a, b, float(rng.uniform(0.15, 0.35)))

    return Task(
        f"N{N}-s{seed}",
        base,
        ("rfc_current", "supersede_current", "approval_current"),
        f"rev-{rev}",
        budget,
        J,
    )


def champion(task):
    return np.ones(len(task.items), dtype=bool)


def topk(task):
    order = sorted(
        range(len(task.items)),
        key=lambda i: task.items[i].relevance,
        reverse=True,
    )
    x = np.zeros(len(task.items), dtype=bool)
    used = 0
    for i in order:
        tokens = task.items[i].tokens
        if used + tokens <= task.budget:
            x[i] = 1
            used += tokens
    return x


def greedy(task):
    n = len(task.items)
    x = np.zeros(n, dtype=bool)
    unary = task.unary()
    used = 0

    while True:
        best = None
        best_delta = 0.0
        for i in range(n):
            if x[i] or used + task.items[i].tokens > task.budget:
                continue
            delta = float(unary[i] + task.J[i] @ x.astype(np.float32))
            if delta < best_delta:
                best_delta = delta
                best = i

        if best is None:
            break
        x[best] = 1
        used += task.items[best].tokens

    return x


def exact(task):
    n = len(task.items)
    best = None
    best_e = math.inf
    tokens = np.array([i.tokens for i in task.items])
    unary = task.unary()
    J = task.J

    for bits in range(1 << n):
        x = np.array([(bits >> i) & 1 for i in range(n)], dtype=np.float32)
        if int(tokens @ x) > task.budget:
            continue
        e = float(unary @ x + 0.5 * x @ J @ x)
        if e < best_e:
            best_e = e
            best = x.astype(bool)

    return best, best_e


def coloring(J):
    n = len(J)
    neighbors = [
        set(np.flatnonzero(np.abs(J[i]) > 1e-9).tolist()) for i in range(n)
    ]
    order = sorted(range(n), key=lambda i: len(neighbors[i]), reverse=True)
    color = {}

    for v in order:
        used = {color[u] for u in neighbors[v] if u in color}
        c = 0
        while c in used:
            c += 1
        color[v] = c

    return [
        np.array([i for i in range(n) if color[i] == c], dtype=np.int32)
        for c in range(max(color.values(), default=-1) + 1)
    ]


_sampler_cache = {}


def sampler_for(N, blocks):
    cache_key = (N, tuple(tuple(map(int, block)) for block in blocks))
    if cache_key in _sampler_cache:
        return _sampler_cache[cache_key]

    blocks_j = [jnp.asarray(block) for block in blocks]

    @jax.jit
    def run(key, unary, J):
        key, sub = jax.random.split(key)
        x = jax.random.bernoulli(sub, 0.35, (THERMO_CHAINS, N)).astype(
            jnp.float32
        )

        def sweep(carry, _):
            key, x = carry
            for inds in blocks_j:
                key, sub = jax.random.split(key)

                # No within-block edges by graph coloring, so these simultaneous
                # updates are a valid block-Gibbs step. This mirrors THRML's
                # colored-block sampling semantics but does not import THRML.
                delta = unary[inds][None, :] + x @ J[:, inds]
                p = jax.nn.sigmoid(-BETA * delta)
                vals = jax.random.bernoulli(sub, p).astype(jnp.float32)
                x = x.at[:, inds].set(vals)

            return (key, x), x

        (_, _), trajectory = jax.lax.scan(
            sweep,
            (key, x),
            None,
            length=THERMO_SWEEPS,
        )
        return trajectory[THERMO_SWEEPS // 2 :]

    _sampler_cache[cache_key] = run
    return run


def thermo(task, seed):
    blocks = coloring(task.J)
    fn = sampler_for(len(task.items), blocks)

    t0 = time.perf_counter()
    trajectory = np.asarray(
        fn(
            jax.random.key(seed),
            jnp.asarray(task.unary()),
            jnp.asarray(task.J),
        )
    )
    sampling_ms = (time.perf_counter() - t0) * 1000

    flat = trajectory.reshape(-1, len(task.items)).astype(bool)
    best = None
    best_e = math.inf
    first_valid = None

    for k, x in enumerate(flat):
        if task.tokens(x) > task.budget:
            continue
        e = task.energy(x)
        if first_valid is None and task.verify(x):
            first_valid = k + 1
        if e < best_e:
            best_e = e
            best = x.copy()

    if best is None:
        best = np.zeros(len(task.items), dtype=bool)
        best_e = task.energy(best)

    max_degree = max(
        (len(np.flatnonzero(task.J[i])) for i in range(len(task.items))),
        default=0,
    )
    return best, best_e, first_valid, sampling_ms, len(blocks), max_degree


def record(task, arm, x, energy=None, extra=None):
    return {
        "task_id": task.task_id,
        "N": len(task.items),
        "arm": arm,
        "verified": task.verify(x),
        "tokens": task.tokens(x),
        "count": int(x.sum()),
        "energy": task.energy(x) if energy is None else energy,
        "budget": task.budget,
        "ids": sorted(task.selected_ids(x)),
        **(extra or {}),
    }


def main():
    rows = []

    for N in (8, 12, 16):
        for seed in range(12):
            task = make_task(N, seed)
            exact_mask, exact_energy = exact(task)

            rows.extend(
                [
                    record(task, "champion", champion(task)),
                    record(task, "topk", topk(task)),
                    record(task, "greedy", greedy(task)),
                    record(task, "exact", exact_mask, exact_energy),
                ]
            )

            for thermo_seed in range(THERMO_SEEDS):
                mask, energy, first_valid, ms, nblocks, max_degree = thermo(
                    task,
                    10_000 + seed * 100 + thermo_seed,
                )
                rows.append(
                    record(
                        task,
                        "thermo",
                        mask,
                        energy,
                        {
                            "seed": thermo_seed,
                            "first_valid_sample": first_valid,
                            "sampling_ms": ms,
                            "color_blocks": nblocks,
                            "max_degree": max_degree,
                            "exact_energy": exact_energy,
                            "energy_gap": energy - exact_energy,
                        },
                    )
                )

    summary = {}
    for N in (8, 12, 16):
        summary[N] = {}
        n_rows = [r for r in rows if r["N"] == N]
        for arm in ("champion", "topk", "greedy", "exact", "thermo"):
            arm_rows = [r for r in n_rows if r["arm"] == arm]
            summary[N][arm] = {
                "n": len(arm_rows),
                "verified_rate": sum(r["verified"] for r in arm_rows)
                / len(arm_rows),
                "mean_tokens": sum(r["tokens"] for r in arm_rows)
                / len(arm_rows),
                "mean_energy": sum(r["energy"] for r in arm_rows)
                / len(arm_rows),
            }
            if arm == "thermo":
                summary[N][arm].update(
                    {
                        "median_sampling_ms": float(
                            np.median([r["sampling_ms"] for r in arm_rows])
                        ),
                        "mean_energy_gap": float(
                            np.mean([r["energy_gap"] for r in arm_rows])
                        ),
                        "exact_hit_rate": float(
                            np.mean(
                                [
                                    abs(r["energy_gap"]) < 1e-5
                                    for r in arm_rows
                                ]
                            )
                        ),
                        "first_valid_found_rate": float(
                            np.mean(
                                [
                                    r["first_valid_sample"] is not None
                                    for r in arm_rows
                                ]
                            )
                        ),
                        "max_degree": max(r["max_degree"] for r in arm_rows),
                    }
                )

    result = {
        "config": {
            "rel_w": REL_W,
            "token_price": TOKEN_PRICE,
            "beta": BETA,
            "chains": THERMO_CHAINS,
            "sweeps": THERMO_SWEEPS,
            "seeds": THERMO_SEEDS,
            "tasks_per_N": 12,
        },
        "summary": summary,
        "rows": rows,
    }
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
