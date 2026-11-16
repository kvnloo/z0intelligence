#!/usr/bin/env python3
"""hz-compaction-carryover: what survives context compaction?

Offline, seeded, model-free. Each synthetic session loads skills and records
decisions (some later revised) amid filler tool output. A compactor squeezes the
session into a word budget. An item is carried when its current value appears in
the compacted text and no superseded value of the same key does.

    python3 experiments/hz-compaction-carryover/run.py --arm head-tail --tier public --out result.json

Add a candidate: write `def compactor_<name>(turns, budget) -> str`, register it in
ARMS as `compactor-<name>`, and run it. Any candidate is scored against the control
on the same split.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import random
import re
import subprocess
import sys
from pathlib import Path

EXP_ID, EPOCH, CONTROL = "hz-compaction-carryover", 1, "head-tail"
SESSIONS, TURNS, BUDGET, SEED = 200, 80, 400, 1729
WORDS = "amber birch cedar delta ember fjord grove harbor iris juniper kestrel lumen".split()
TOKEN = re.compile(r"[\w-]+")
REVISE = ["decision update: {k} = {v}", "actually, make {k} {v} instead", "switch {k} to {v} from now on"]


def session(rng: random.Random) -> dict:
    """Turns plus the ground truth: current value and superseded values per key."""
    turns = [" ".join(f"log {rng.getrandbits(24):06x}" for _ in range(rng.randint(8, 16)))
             for _ in range(TURNS)]
    live: dict[str, str] = {}
    stale: dict[str, list[str]] = {}
    names = iter(rng.sample([f"{w}-{n}" for w in WORDS for n in range(10, 100)], 12))
    values = iter(f"v-{x:05x}" for x in rng.sample(range(1 << 20), 12))  # unique keys and values
    for _ in range(rng.randint(3, 6)):
        sk = f"sk-{next(names)}"
        turns.insert(rng.randrange(len(turns)), f"loaded skill {sk}: follow its checklist")
        live[sk] = sk
    for _ in range(rng.randint(3, 6)):
        key, val = f"opt-{next(names)}", next(values)
        at = rng.randrange(len(turns))
        turns.insert(at, f"decision: {key} = {val}")
        if rng.random() < 0.4:
            new = next(values)
            turns.insert(rng.randrange(at + 1, len(turns) + 1), rng.choice(REVISE).format(k=key, v=new))
            stale.setdefault(key, []).append(val)
            val = new
        live[key] = val
    return {"turns": turns, "live": live, "stale": stale}


def fixture(n: int = SESSIONS, seed: int = SEED) -> list[dict]:
    rng = random.Random(seed)
    return [session(rng) for _ in range(n)]


def _fit(words: list[str], budget: int) -> list[str]:
    return words[:budget]


def compactor_head_tail(turns: list[str], budget: int) -> str:
    """Control: keep the opening quarter of the budget and the most recent tail."""
    words = " ".join(turns).split()
    head = budget // 4
    return " ".join(words[:head] + words[-(budget - head):]) if len(words) > budget else " ".join(words)


def compactor_pinned_manifest(turns: list[str], budget: int) -> str:
    """Reference candidate: pin the latest canonical skill/decision lines, then the tail."""
    pinned: dict[str, str] = {}
    for t in turns:
        if m := re.match(r"loaded skill (\S+):", t):
            pinned[m[1]] = t
        elif m := re.match(r"decision(?: update)?: (\S+) = ", t):
            pinned[m[1]] = t
    head = _fit(" ".join(pinned.values()).split(), budget)
    tail = " ".join(turns).split()[-(budget - len(head)):] if budget > len(head) else []
    return " ".join(head + tail)


ARMS = {"head-tail": compactor_head_tail, "pinned-manifest": compactor_pinned_manifest}


def score(s: dict, text: str) -> float:
    """Error rate for one session: share of items lost or shadowed by a stale value."""
    seen = set(TOKEN.findall(text))
    bad = sum(v not in seen or any(o in seen for o in s["stale"].get(k, []))
              for k, v in s["live"].items())
    return bad / len(s["live"])


def run_arm(arm: str, data: list[dict]) -> list[float]:
    fn = ARMS[arm]
    out = []
    for s in data:
        # The budget is enforced, not trusted, and counted in the same tokens the scorer reads.
        text = " ".join(TOKEN.findall(fn(list(s["turns"]), BUDGET))[:BUDGET])
        out.append(score(s, text))
    return out


def mean(xs: list[float]) -> float:
    return sum(xs) / len(xs)


def bootstrap_ci(xs: list[float], seed: int = SEED, reps: int = 2000) -> list[float]:
    rng = random.Random(seed)
    ms = sorted(mean([rng.choice(xs) for _ in xs]) for _ in range(reps))
    return [ms[int(0.025 * reps)], ms[int(0.975 * reps) - 1]]


def result(arm: str, data: list[dict]) -> dict:
    here = Path(__file__).resolve().parent
    sha = subprocess.run(["git", "rev-parse", "HEAD"], cwd=here, capture_output=True, text=True).stdout.strip()
    errs = run_arm(arm, data)
    r = {"schema": "z0.exp.result.v1", "exp_id": EXP_ID, "epoch": EPOCH, "arm": arm, "data_tier": "public",
         "data_manifest_sha256": hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest(),
         "repo_sha": sha or "unknown", "n": len(data),
         "metric": {"name": "carryover_error_rate", "value": round(mean(errs), 6),
                    "ci95": [round(x, 6) for x in bootstrap_ci(errs)]},
         "verdict": "NULL", "command": f"python3 experiments/hz-compaction-carryover/run.py --arm {arm} --tier public",
         "notes": f"{len(data)} synthetic sessions, seed {SEED}, budget {BUDGET} words. Control arm: no verdict."}
    if arm != CONTROL:
        ctrl = run_arm(CONTROL, data)
        lo, hi = bootstrap_ci([a - b for a, b in zip(errs, ctrl)])
        r["controls"] = {CONTROL: round(mean(ctrl), 6)}
        r["verdict"] = "KEEP" if hi < 0 else "DISCARD"
        r["notes"] = f"{r['notes'].split(' Control')[0]} Paired diff vs {CONTROL}: 95% CI [{lo:.4f}, {hi:.4f}]."
    return r


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--arm", required=True, choices=sorted(ARMS))
    p.add_argument("--tier", default="public", choices=["public"])
    p.add_argument("--out", required=True)
    a = p.parse_args(argv)
    r = result(a.arm, fixture())
    out = Path(a.out).expanduser()
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(r, indent=2) + "\n")
    print(f"{a.arm}: carryover_error_rate={r['metric']['value']:.4f} verdict={r['verdict']} -> {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
