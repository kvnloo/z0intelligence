#!/usr/bin/env python3
"""FROZEN evaluator for the resolve fast path.

Scoring rules are fixed here and are not parameters. The autoresearch loop may
not alter this file's verdicts; a challenger that raises fast coverage by
introducing even one unsupported memory loses outright.

Definitions
-----------
safe_fast      : not fallback_used AND the resolved value matches the source
                 fact. This is the metric being optimised.
verified_wrong : a value WAS resolved (claimed verified) and does not match.
                 HARD GATE: any challenger with verified_wrong > 0 cannot become
                 champion at all.
value_match    : normalised containment either way -- the fast path may return a
                 longer path that contains the expected one, or a prefix.
"""

from __future__ import annotations

import argparse
import json
import random
import re
import statistics
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

FIXTURES = ROOT / "benchmarks" / "fixtures" / "resolve-fast-v1"
_TRAIL = re.compile(r"[\s`'\".,;:)\]}]+$")


def normalise(v: str) -> str:
    return _TRAIL.sub("", (v or "").strip()).lower()


def value_match(resolved: str | None, expected: str) -> bool:
    if not resolved:
        return False
    r, e = normalise(resolved), normalise(expected)
    if not e:
        return False
    return e in r or r in e


def load(split: str, limit: int | None, seed: int = 0) -> list[dict]:
    rows = [json.loads(l) for l in (FIXTURES / f"{split}.jsonl").read_text().splitlines() if l.strip()]
    if limit and limit < len(rows):
        rnd = random.Random(seed)
        # stratify by slot kind so a small run is not all PATH
        by_kind: dict[str, list[dict]] = {}
        for r in rows:
            by_kind.setdefault(r["slot_kind"], []).append(r)
        picked: list[dict] = []
        per = max(1, limit // max(1, len(by_kind)))
        for kind, group in by_kind.items():
            rnd.shuffle(group)
            picked.extend(group[:per])
        rnd.shuffle(picked)
        rows = picked[:limit]
    return rows


def evaluate(rows: list[dict], *, policy: dict | None, use_full: bool = False) -> dict:
    from z0int.context_resolve import resolve_fast

    safe_fast = wrong = fell_back = unresolved = 0
    ttfas: list[float] = []
    evidence: list[int] = []
    per_slot: dict[str, dict[str, int]] = {}
    per_family: dict[str, dict[str, int]] = {}
    wrong_examples: list[dict] = []
    for row in rows:
        t = time.perf_counter()
        answer = resolve_fast(row["question"], allow_model=False, emit=False, policy=policy)
        ms = (time.perf_counter() - t) * 1000
        vals = [s.value for s in answer.slots if s.resolved]
        ok = any(value_match(v, row["expected_value"]) for v in vals)
        slot = row["slot_kind"]
        bucket = per_slot.setdefault(slot, {"n": 0, "safe_fast": 0, "wrong": 0})
        fam = per_family.setdefault(row["family"], {"n": 0, "safe_fast": 0, "wrong": 0})
        bucket["n"] += 1
        fam["n"] += 1
        evidence.append(answer.evidence_opened)
        if vals:
            if ok:
                if not answer.fallback_used:
                    safe_fast += 1
                    bucket["safe_fast"] += 1
                    fam["safe_fast"] += 1
                    if answer.ttfa_ms:
                        ttfas.append(answer.ttfa_ms)
                else:
                    # answered only after paying full cost: not a fast win
                    pass
            else:
                wrong += 1
                bucket["wrong"] += 1
                fam["wrong"] += 1
                if len(wrong_examples) < 5:
                    wrong_examples.append(
                        {"question": row["question"], "expected": row["expected_value"], "got": vals[0]}
                    )
        else:
            unresolved += 1
        if answer.fallback_used:
            fell_back += 1
    n = len(rows) or 1
    return {
        "n": len(rows),
        "safe_fast": safe_fast,
        "safe_fast_coverage": round(safe_fast / n, 4),
        "verified_wrong": wrong,
        "unresolved": unresolved,
        "fallback_rate": round(fell_back / n, 4),
        "p50_ttfa_ms": round(statistics.median(ttfas), 1) if ttfas else None,
        "p50_evidence": round(statistics.median(evidence), 1) if evidence else None,
        "per_slot": per_slot,
        "per_family": per_family,
        "wrong_examples": wrong_examples,
        "gate_pass": wrong == 0,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--split", default="dev")
    ap.add_argument("--limit", type=int, default=200)
    ap.add_argument("--policy", default=None, help="path to a policy JSON")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()
    policy = json.loads(Path(a.policy).read_text()) if a.policy else None
    rows = load(a.split, a.limit)
    out = evaluate(rows, policy=policy)
    if a.json:
        print(json.dumps(out, indent=2))
    else:
        print(f"split={a.split} n={out['n']} policy={a.policy or 'champion(default)'}")
        print(f"  safe_fast_coverage : {out['safe_fast_coverage']:.3f}  ({out['safe_fast']}/{out['n']})")
        print(f"  verified_wrong     : {out['verified_wrong']}   GATE {'PASS' if out['gate_pass'] else 'FAIL'}")
        print(f"  fallback_rate      : {out['fallback_rate']:.3f}")
        print(f"  p50_ttfa_ms        : {out['p50_ttfa_ms']}   p50_evidence: {out['p50_evidence']}")
        print("  per-slot :", {k: f"{v['safe_fast']}/{v['n']}" for k, v in out["per_slot"].items()})
        print("  per-family:", {k: f"{v['safe_fast']}/{v['n']}" for k, v in out["per_family"].items()})
        for w in out["wrong_examples"][:3]:
            print(f"  WRONG Q={w['question'][:60]!r} expected={w['expected'][:40]!r} got={w['got'][:40]!r}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
