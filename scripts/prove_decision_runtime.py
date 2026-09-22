#!/usr/bin/env python
"""Prove a decision backend is really runnable, not merely registered.

For a named backend this does four things, in order, and prints the evidence:

1. ``backend_status()`` — the whole registry, so the backend is visible where the
   roster, bench, Pareto and shadow lanes look for it.
2. ``backend_status(<id>, load=True)`` — actually load the weights.
3. one **typed boolean** call — the smallest native decision.
4. one **multi-question batch** — several questions in a single request, which is
   where the non-generative decisions backends earn their keep (one forward pass,
   N answers) and where a naive per-question adapter would silently serialise.

It also records the latency split the runtime plane needs: load, forward, and
total, per invocation. It never downloads weights.

Usage::

    python scripts/prove_decision_runtime.py laya_421m
    python scripts/prove_decision_runtime.py decider_2b --batch 8
"""

from __future__ import annotations

import argparse
import json
import time

from z0int.backends import registry
from z0int.backends.base import (
    DecisionOption,
    DecisionQuestion,
    DecisionRequest,
)


BOOLEAN_Q = DecisionQuestion(
    id="verification_needed",
    type="boolean",
    instructions=(
        "Does this task require an independent verification step before the "
        "result may be reported as done?"
    ),
    false_criterion="the result is already checked by the tool that produced it",
    true_criterion="an independent check is required before reporting done",
)

STATE = (
    "task: repair the failing test in src/parser.py\n"
    "last tool result: pytest failed with 1 error in test_parse_nested\n"
    "budget remaining: 8 units\n"
    "authority: read, write"
)


def batch_questions(n: int) -> tuple[DecisionQuestion, ...]:
    axes = [
        ("context_relevant", "Is the retrieved passage relevant to the task?"),
        ("tool_family_git", "Is this a git/version-control task?"),
        ("needs_escalation", "Is this beyond a local model's competence?"),
        ("risk_class_high", "Would the next action be destructive or irreversible?"),
        ("evidence_sufficient", "Is the available evidence sufficient to act?"),
        ("budget_tight", "Is the remaining budget too small for a multi-step plan?"),
        ("review_required", "Does a human need to review the result?"),
        ("stop_ok", "Can the session stop now without losing work?"),
    ]
    qs = []
    for i in range(n):
        key, instr = axes[i % len(axes)]
        qs.append(
            DecisionQuestion(
                id=f"{key}_{i}",
                type="boolean",
                instructions=instr,
                false_criterion="no",
                true_criterion="yes",
            )
        )
    return tuple(qs)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("backend", nargs="?", default="laya_421m")
    ap.add_argument("--batch", type=int, default=8)
    ap.add_argument("--out", default=None, help="write the evidence JSON here")
    args = ap.parse_args()

    out: dict[str, object] = {"backend": args.backend}

    print("== 1. registry ==")
    listing = registry.backend_status()
    ids = [b["id"] for b in listing]
    print(json.dumps(ids))
    assert args.backend in ids, f"{args.backend} not registered; known={ids}"
    out["registered"] = ids

    print(f"\n== 2. backend_status({args.backend!r}, load=True) ==")
    t0 = time.perf_counter()
    status = registry.backend_status(args.backend, load=True)[0]
    load_s = time.perf_counter() - t0
    print(json.dumps({k: status[k] for k in ("id", "ready", "loaded", "model", "detail")}, indent=2))
    print(f"checkpoint: {status.get('checkpoint')}")
    print(f"wall for status+load: {load_s * 1000:.1f} ms")
    out["status"] = {k: status[k] for k in ("id", "ready", "loaded", "model", "checkpoint", "detail")}
    out["status_load_wall_ms"] = load_s * 1000

    backend = registry.create_backend(args.backend)

    print("\n== 3. one typed boolean call ==")
    req = DecisionRequest(state=STATE, questions=(BOOLEAN_Q,))
    t1 = time.perf_counter()
    res1 = backend.evaluate(req)
    dt1 = (time.perf_counter() - t1) * 1000
    answers1 = [a.to_dict() if hasattr(a, "to_dict") else a.__dict__ for a in res1.answers]
    print(json.dumps({"latency_ms": res1.latency_ms, "wall_ms": dt1, "answers": answers1}, indent=2, default=str))
    assert len(res1.answers) == 1, "expected exactly one answer"
    assert res1.answers[0].type == "boolean"
    out["boolean_call"] = {"latency_ms": res1.latency_ms, "wall_ms": dt1, "n_answers": 1}

    print(f"\n== 4. multi-question batch (n={args.batch}) ==")
    qs = batch_questions(args.batch)
    req2 = DecisionRequest(state=STATE, questions=qs)
    t2 = time.perf_counter()
    res2 = backend.evaluate(req2)
    dt2 = (time.perf_counter() - t2) * 1000
    print(f"answers returned: {len(res2.answers)} / {len(qs)} requested")
    print(f"reported latency_ms: {res2.latency_ms}   wall_ms: {dt2:.1f}")
    print(f"per-question wall: {dt2 / len(qs):.2f} ms")
    for a in res2.answers[:4]:
        print(f"   {a.question_id:26s} value={a.value!r:6s} conf={a.confidence}")
    assert len(res2.answers) == len(qs), (
        f"batch returned {len(res2.answers)} answers for {len(qs)} questions — "
        "batched questions are being dropped or serialised"
    )
    out["batch"] = {
        "requested": len(qs),
        "returned": len(res2.answers),
        "latency_ms": res2.latency_ms,
        "wall_ms": dt2,
        "per_question_ms": dt2 / len(qs),
        "capabilities_supports_batch_questions": backend.capabilities.supports_batch_questions,
    }

    print("\n== summary ==")
    print(json.dumps(out, indent=2, default=str))
    if args.out:
        from pathlib import Path

        p = Path(args.out)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(out, indent=2, default=str), encoding="utf-8")
        print(f"wrote {p}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
