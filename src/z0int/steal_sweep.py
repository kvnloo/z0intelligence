#!/usr/bin/env python3
"""Steal-rate sweep: how much of hammer3b's work can a cheap backend take?

Items 2-4 of the Phase 2 block, in one tool:

  * present a candidate backend with the *exact* legal-action set the deterministic
    compiler offered hammer3b in the frozen Phase 2 corpus — the same menu, not a
    re-derived one, because the J1 pilot showed that a mismatched candidate set
    produced a 48.1% agreement rate that turned into 100% once the menu was held
    equal, and that number was nearly mistaken for a quality result
  * sweep a confidence threshold and report coverage against verified success
  * run a cascade (mushroom -> nanojev -> hammer3b) and report the share of
    hammer3b calls that disappear

Risk here is **wrong answers**, not danger. The corpus is compiler-first only, so
`dangerous_selected` is false on all 420 episodes by construction; there is no
danger signal to sweep. That is stated rather than papered over with a synthetic
one.

Usage:
  python -m z0int.steal_sweep --backend nanojev
  python -m z0int.steal_sweep --cascade nanojev,hammer
"""

from __future__ import annotations

import argparse
import collections
import json
import statistics as stats
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import paths as _paths

# Both of these used to be absolute paths to one machine's layout: the corpus
# pointed into an Evolution Lab checkout, and the fixture pointed at one clone
# of this repo. Neither survives a different checkout.
_LEGACY_CORPUS = (
    _paths.LEGACY_EVOLUTION_LAB_ROOT / "corpus/phase2/p1b-20260921T1430Z/episodes.jsonl"
)
CORPUS = _paths.evolution_lab_artifact(
    "corpus/phase2/p1b-20260921T1430Z/episodes.jsonl"
) or _LEGACY_CORPUS
FIXTURES = Path(__file__).resolve().parents[2] / "benchmarks/fixtures/local-cognition-v1/examples.jsonl"


@dataclass
class Task:
    task_id: str
    family: str
    state: dict[str, Any]
    legal_actions: tuple[str, ...]
    gold: str
    # every compiler-first arm's outcome on this task, keyed by arm
    arm_outcomes: dict[str, bool] = field(default_factory=dict)
    dangerous: frozenset[str] = frozenset()

    def request(self) -> dict[str, Any]:
        return {
            "state": {
                "task": self.task_id,
                "family": self.family,
                "features": self.state.get("features") or self.state,
                "authority": self.state.get("authority"),
                "budget_units": self.state.get("budget_units"),
                "legal_actions": list(self.legal_actions),
            },
            "questions": [
                {
                    "id": "action",
                    "type": "choice",
                    "instructions": (
                        "Choose the single next action from exactly these legal "
                        "actions. Prefer the cheapest action that satisfies the request."
                    ),
                    "options": [
                        {"id": a, "description": a.replace("_", " ")} for a in self.legal_actions
                    ],
                }
            ],
        }


def load_dangerous() -> dict[str, frozenset[str]]:
    """Declared dangerous actions per task, from the fixture that generated the run.

    The compiler removes *illegal* actions, not dangerous ones, so this is the only
    place a safety-relevant option can be identified. It is empty on this corpus —
    no arm selected a dangerous action — but the mapping is carried so the sweep
    reports the field honestly instead of implying it measured safety.
    """
    out: dict[str, frozenset[str]] = {}
    if not FIXTURES.is_file():
        return out
    for line in FIXTURES.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        ex = json.loads(line)
        tid = str(ex.get("fixture_id") or "")
        danger = {
            str(a["action_id"])
            for a in ex.get("actions") or []
            if str(a.get("risk_class") or "") in ("destructive", "privileged")
        }
        danger.discard(str(ex.get("gold_action")))
        if tid:
            out[tid] = frozenset(danger)
    return out


def load_tasks(corpus: Path = CORPUS) -> list[Task]:
    danger = load_dangerous()
    by_task: dict[str, Task] = {}
    for line in corpus.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        e = json.loads(line)
        tid = str(e["task_id"])
        arm = e["episode_id"].split(":")[1]
        t = by_task.get(tid)
        if t is None:
            gold = (e.get("verification") or {}).get("gold_action")
            t = Task(
                task_id=tid,
                family=str(e["task_family"]),
                state=dict(e.get("state_before") or {}),
                legal_actions=tuple(e.get("legal_actions") or ()),
                gold=str(gold),
                dangerous=danger.get(tid, frozenset()),
            )
            by_task[tid] = t
        # sanity: the compiler offered every arm the same menu on this task
        if tuple(e.get("legal_actions") or ()) != t.legal_actions:
            raise SystemExit(
                f"candidate set differs across arms for {tid}: the sweep requires one menu"
            )
        t.arm_outcomes[arm] = bool((e.get("outcome") or {}).get("correct"))
    return sorted(by_task.values(), key=lambda x: x.task_id)


# ------------------------------------------------------------------ backends


def _evaluate(backend, request: dict) -> dict:
    """Return a plain dict from any backend, whatever shape it returns.

    `DecisionResult` is a frozen dataclass with `to_dict` absent, so the first
    version handed the object straight to `.get()` and every row errored with
    "'DecisionResult' object has no attribute 'get'". `asdict` is the fallback.
    """
    import dataclasses

    from z0int.backends.base import request_from_mapping

    res = backend.evaluate(request_from_mapping(request))
    if hasattr(res, "to_dict"):
        return res.to_dict()
    if dataclasses.is_dataclass(res):
        return dataclasses.asdict(res)
    return res


def run_backend(name: str, tasks: list[Task]) -> dict[str, dict]:
    from z0int.backends import registry

    backend = registry.create_backend(name)
    out: dict[str, dict] = {}
    for t in tasks:
        t0 = time.perf_counter()
        try:
            res = _evaluate(backend, t.request())
            ans = (res.get("answers") or [{}])[0]
            out[t.task_id] = {
                "value": ans.get("value"),
                "confidence": ans.get("confidence"),
                "probabilities": ans.get("probabilities") or {},
                "latency_ms": float(res.get("latency_ms") or (time.perf_counter() - t0) * 1000),
                "error": None,
            }
        except Exception as exc:  # a crashing backend is a result
            out[t.task_id] = {"value": None, "confidence": None, "probabilities": {},
                              "latency_ms": (time.perf_counter() - t0) * 1000,
                              "error": f"{type(exc).__name__}: {exc}"[:200]}
    return out


def score(tasks: list[Task], preds: dict[str, dict], thr: float) -> dict:
    covered = correct = wrong = unsafe = 0
    for t in tasks:
        p = preds.get(t.task_id) or {}
        v, c = p.get("value"), p.get("confidence")
        if v is None:
            continue
        if c is not None and c < thr:
            continue
        covered += 1
        if v == t.gold:
            correct += 1
        elif v in t.dangerous:
            unsafe += 1
        else:
            wrong += 1
    n = len(tasks)
    return {
        "threshold": thr, "n": n, "covered": covered,
        "coverage": round(covered / n, 4) if n else 0.0,
        "correct": correct, "wrong": wrong, "unsafe": unsafe,
        "success_given_covered": round(correct / covered, 4) if covered else None,
        "escalated": n - covered,
    }


def hammer_baseline(tasks: list[Task], arm: str = "compiler+hammer2.1_3b") -> dict:
    ok = sum(1 for t in tasks if t.arm_outcomes.get(arm))
    return {"arm": arm, "n": len(tasks), "correct": ok,
            "success": round(ok / len(tasks), 4) if tasks else None}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--corpus", default=str(CORPUS))
    ap.add_argument("--backend", action="append", default=None)
    ap.add_argument("--cascade", default=None,
                    help="comma-separated backend order, e.g. nanojev,hammer")
    ap.add_argument("--thresholds", default="0,0.3,0.4,0.5,0.6,0.7,0.8,0.9,0.95,0.99")
    ap.add_argument("--out", default=None)
    a = ap.parse_args(argv)

    tasks = load_tasks(Path(a.corpus))
    thr = [float(x) for x in a.thresholds.split(",")]
    print(f"corpus {a.corpus}")
    print(f"tasks {len(tasks)} | families {len({t.family for t in tasks})} | "
          f"distinct candidate-set sizes {sorted({len(t.legal_actions) for t in tasks})}")
    print(f"gold label source: all gold | "
          f"dangerous options declared by fixtures: {sum(len(t.dangerous) for t in tasks)}")
    print()

    baseline = hammer_baseline(tasks)
    print(f"baseline  {baseline['arm']}: {baseline['correct']}/{baseline['n']} "
          f"= {baseline['success']:.3f}")
    print()

    def report(name: str, preds: dict[str, dict]) -> dict:
        lat = [p["latency_ms"] for p in preds.values() if p.get("value")]
        errs = [p["error"] for p in preds.values() if p.get("error")]
        curves = [score(tasks, preds, t) for t in thr]
        print(f"=== {name} ===")
        if errs:
            print(f"  errors: {len(errs)} (first: {errs[0][:80]})")
        print(f"  latency p50 {round(stats.median(lat),1) if lat else '—'} ms")
        print(f"  {'thr':>5} {'covered':>9} {'coverage':>9} {'success':>8} {'wrong':>6} {'unsafe':>7} {'escalated':>10}")
        for c in curves:
            print(f"  {c['threshold']:>5} {c['covered']:>9} {c['coverage']:>9.3f} "
                  f"{str(c['success_given_covered']):>8} {c['wrong']:>6} {c['unsafe']:>7} "
                  f"{c['escalated']:>10}")
        print()
        return {"curves": curves,
                "latency_p50_ms": round(stats.median(lat), 1) if lat else None,
                "errors": len(errs),
                "predictions": {k: {"value": v.get("value"), "confidence": v.get("confidence")}
                                for k, v in preds.items()}}

    results: dict[str, Any] = {
        "schema": "z0int.steal-sweep.v1",
        "evidence_class": "exploratory_beta",
        "promotion_eligible": False,
        "corpus": a.corpus,
        "baseline": baseline,
        "note": (
            "Candidate sets are the compiler's own legal_actions from the frozen Phase 2 "
            "corpus, identical for every arm. Risk is wrong answers; the corpus is "
            "compiler-first so no dangerous selection exists to measure."
        ),
    }

    if a.cascade:
        order = [x.strip() for x in a.cascade.split(",") if x.strip()]
        preds = {n: run_backend(n, tasks) for n in order if n not in ("hammer", "hammer3b")}
        results["cascade"] = {"order": order, "backends": {}}
        for n, p in preds.items():
            results["cascade"]["backends"][n] = report(n, p)
        # Cascade accounting. The question is not just how many calls the cheap
        # tier absorbs but whether the *end-to-end* answer is still right, since the
        # escalated remainder is answered by hammer3b and hammer3b is not perfect on
        # every task. Both numbers are reported so the trade is visible.
        # `order` uses short aliases ("hammer"), but arm_outcomes is keyed by the
        # real arm id ("compiler+hammer2.1_3b"). Looking up the alias made every
        # escalated task score 0, which understated the cascade by ~9 tasks.
        ALIAS = {
            "hammer": "compiler+hammer2.1_3b",
            "hammer3b": "compiler+hammer2.1_3b",
            "hammer2.1_3b": "compiler+hammer2.1_3b",
        }
        hammer = ALIAS.get(
            [x for x in order if x in ("hammer", "hammer3b")][0], "compiler+hammer2.1_3b"
        )
        print(f"  {'thr':>5} {'eliminated':>11} {'cheap-ok':>9} {'esc-ok':>7} "
              f"{'e2e-ok':>7} {'e2e':>7} {'vs hammer':>10}")
        table: dict[str, Any] = {}
        for t in thr:
            handled = cheap_ok = esc_ok = 0
            for task in tasks:
                served = False
                for n in order:
                    if n in ("hammer", "hammer3b"):
                        break
                    pr = preds.get(n, {}).get(task.task_id) or {}
                    c = pr.get("confidence")
                    if pr.get("value") is not None and (c is None or c >= t):
                        served = True
                        if pr["value"] == task.gold:
                            cheap_ok += 1
                        break
                if served:
                    handled += 1
                elif task.arm_outcomes.get(hammer):
                    esc_ok += 1
            e2e = cheap_ok + esc_ok
            table[str(t)] = {
                "eliminated": handled,
                "eliminated_pct": round(handled / len(tasks), 4),
                "cheap_correct": cheap_ok,
                "escalated_correct": esc_ok,
                "end_to_end_correct": e2e,
                "end_to_end_success": round(e2e / len(tasks), 4),
                "vs_hammer_delta": round(e2e / len(tasks) - baseline["success"], 4),
            }
            r = table[str(t)]
            print(f"  {t:>5} {handled:>6}/{len(tasks):<4} {cheap_ok:>9} {esc_ok:>7} "
                  f"{e2e:>7} {r['end_to_end_success']:>7.3f} {r['vs_hammer_delta']:>+10.3f}")
        print()
        results["cascade"]["by_threshold"] = table
        results["cascade"]["hammer_baseline"] = baseline
    else:
        names = a.backend or ["nanojev"]
        results["backends"] = {}
        for n in names:
            results["backends"][n] = report(n, run_backend(n, tasks))

    if a.out:
        Path(a.out).parent.mkdir(parents=True, exist_ok=True)
        Path(a.out).write_text(json.dumps(results, indent=1) + "\n", encoding="utf-8")
        print(f"wrote {a.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
