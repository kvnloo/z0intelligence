#!/usr/bin/env python3
"""Phase 2A — CPU FP32 vs CUDA BF16 vs CUDA FP32, item level.

The published typed-decisions card reports a CPU FP32 reproduction (1451/2000) and
an H200 BF16 run (1463/2000). This measures whether local BF16 moves toward the
historical totals, and reports the item-level disagreements rather than only totals.
"""
from __future__ import annotations

import argparse
import collections
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
OUT = REPO / "results" / "julia-claims"
OUT.mkdir(parents=True, exist_ok=True)
sys.path.insert(0, str(Path(__file__).parent))
from parity import build_rows, load_parquet  # noqa: E402

MODE = {
    "cpu-fp32": dict(device="cpu", autocast="off"),
    "cuda-bf16": dict(device="cuda", autocast="auto"),
    "cuda-fp32": dict(device="cuda", autocast="off"),
}


def run_mode(mode: str, n: int):
    from z0int.backends.julia import JuliaBackend

    cfg = MODE[mode]
    b = JuliaBackend.for_manifest_id("julia_1")
    b.device = cfg["device"]
    b.autocast = cfg["autocast"]
    b.max_length = 1024
    b.head_length = 512
    h = b.health(load=True)
    if not h.loaded:
        raise SystemExit(f"{mode}: {h.detail}")

    cases = load_parquet()
    rows, meta = build_rows(cases)
    case_by_id = {c["id"]: c for c in cases}
    from z0int.backends.base import DecisionOption, DecisionQuestion, DecisionRequest

    out = {}
    stats = collections.defaultdict(lambda: dict(count=0, correct=0))
    for i in range(n):
        case = case_by_id[meta[i]["case_id"]]
        q = json.loads(case["questions"])[meta[i]["qid"]]
        kind = q["type"]
        crit = q.get("criteria")
        if kind == "choice":
            options = tuple(DecisionOption(str(k), str(v)) for k, v in crit.items())
            dq = DecisionQuestion(id=meta[i]["qid"], type="choice",
                                  instructions=q["instructions"], options=options)
        elif kind == "score":
            dq = DecisionQuestion(id=meta[i]["qid"], type="score",
                                  instructions=q["instructions"],
                                  levels=tuple(str(x) for x in crit))
        else:
            c = crit or {"false": "false", "true": "true"}
            dq = DecisionQuestion(id=meta[i]["qid"], type="boolean",
                                  instructions=q["instructions"],
                                  false_criterion=str(c["false"]), true_criterion=str(c["true"]))
        req = DecisionRequest(state=rows[i]["state"], questions=(dq,),
                              request_id=meta[i]["id"])
        res = b.evaluate(req)
        probs = res.answers[0].probabilities
        order = list(probs)
        idx = order.index(res.answers[0].value) if res.answers[0].value in order else \
            max(range(len(order)), key=lambda j: probs[order[j]])
        # gold is a KEY string, not an index (score keys are '0'..'n-1', so index==key)
        pred = str(order[idx])
        correct = pred == meta[i]["gold"]
        stats[kind]["count"] += 1
        stats[kind]["correct"] += int(correct)
        out[meta[i]["id"]] = dict(pred=pred, correct=correct,
                                  probs=[round(probs[k], 8) for k in order])
        if (i + 1) % 400 == 0:
            print(f"  {mode} {i+1}/{n}", flush=True)
    b.close()
    total_c = sum(v["correct"] for v in stats.values())
    total_n = sum(v["count"] for v in stats.values())
    summary = {"mode": mode, "n": n, "total": f"{total_c}/{total_n}",
               "by_type": {k: dict(v, accuracy=v["correct"] / v["count"]) for k, v in stats.items()}}
    (OUT / f"device-{mode}.json").write_text(json.dumps(summary, indent=2) + "\n")
    (OUT / f"device-{mode}-items.json").write_text(json.dumps(out, sort_keys=True) + "\n")
    print(f"  {mode}: {summary['total']}  " +
          " ".join(f"{k}={v['correct']}/{v['count']}" for k, v in sorted(stats.items())))
    return summary, out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=2000)
    ap.add_argument("--modes", default="cpu-fp32,cuda-bf16,cuda-fp32")
    args = ap.parse_args()
    results, items = {}, {}
    for m in args.modes.split(","):
        results[m], items[m] = run_mode(m, args.n)
    base = "cpu-fp32"
    if base in items:
        print("\n=== item-level disagreement vs CPU FP32 ===")
        for m in items:
            if m == base:
                continue
            common = [k for k in items[base] if k in items[m]]
            diff = [k for k in common if items[base][k]["pred"] != items[m][k]["pred"]]
            acc_b = sum(items[base][k]["correct"] for k in common) / len(common)
            acc_m = sum(items[m][k]["correct"] for k in common) / len(common)
            print(f"  {m:<10} n={len(common)} differing_predictions={len(diff)} "
                  f"({100*len(diff)/len(common):.2f}%) cpu_acc={acc_b:.4f} {m}_acc={acc_m:.4f}")
            moved_right = sum(1 for k in diff if items[m][k]["correct"] and not items[base][k]["correct"])
            moved_wrong = sum(1 for k in diff if not items[m][k]["correct"] and items[base][k]["correct"])
            print(f"             flips right={moved_right} wrong={moved_wrong} "
                  f"both_wrong={len(diff)-moved_right-moved_wrong}")
    (OUT / "device-comparison.json").write_text(json.dumps(results, indent=2, sort_keys=True) + "\n")


if __name__ == "__main__":
    main()
