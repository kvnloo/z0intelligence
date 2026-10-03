"""Shared corpus + helpers for the bend-perf AODL gate benchmark.

The corpus is rebuilt exactly as the frozen parity harness builds it
(z0intelligence exp/bend-aodl-gate @a0e95785, benchmarks/bend_gate_parity.py
run(): fixtures + targeted mutations + seeded fuzz, seed 48, fuzz 20000), so it
is the same 22,079-case corpus the README's parity row was measured on.
"""
from __future__ import annotations

import copy
import hashlib
import json
import pickle
import random
import sys
from pathlib import Path

ROOT = Path("/mnt/zer0models/z0-wt/wiring/bend-perf")
GATE_WT = ROOT / "wt" / "gate"
WORK = ROOT / "results" / "work"
BIN = ROOT / "bin"
sys.path[:0] = [str(GATE_WT / "src"), str(GATE_WT / "benchmarks")]

import bend_gate_parity as par  # noqa: E402  (inserts third_party/aodl_a848270 on sys.path)
import z0int.bend_gate as bg  # noqa: E402

SEED, FUZZ = 48, 20000


def build_cases(seed: int = SEED, n_fuzz: int = FUZZ) -> list[tuple[str, object]]:
    base = par.fixtures()
    cases = list(base)
    muts = par.targeted()
    bases02 = [(n, d) for n, d in base if isinstance(d, dict) and d.get("specVersion") == "0.2"]
    for name, doc in bases02:
        for mname, fn in muts.items():
            d = copy.deepcopy(doc)
            try:
                fn(d)
            except (KeyError, IndexError, StopIteration, TypeError, AttributeError):
                continue
            cases.append((f"mut:{mname}@{name}", d))
    rng = random.Random(seed)
    for i in range(n_fuzz):
        name, doc = rng.choice(bases02)
        cases.append((f"fuzz:{i}@{name}", par.fuzz_mutate(doc, rng, rng.randint(1, 3))))
    return cases


def corpus_sha(cases) -> str:
    h = hashlib.sha256()
    for _, doc in cases:
        h.update(json.dumps(doc, sort_keys=True, default=str).encode())
    return h.hexdigest()


def line_of(tokens) -> bytes:
    return (" ".join(str(t) for t in tokens) + "\n").encode("ascii")


def load():
    return pickle.loads((WORK / "corpus.pkl").read_bytes())


def stats(xs):
    xs = sorted(xs)
    n = len(xs)
    if not n:
        return {}
    import statistics
    return {"n": n, "p50": round(xs[n // 2], 2), "p95": round(xs[min(n - 1, int(0.95 * n))], 2),
            "p99": round(xs[min(n - 1, int(0.99 * n))], 2), "mean": round(statistics.fmean(xs), 2),
            "min": round(xs[0], 2)}
