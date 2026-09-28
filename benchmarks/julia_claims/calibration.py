#!/usr/bin/env python3
"""Phase 4 — calibration and selective-risk analysis.

Max softmax probability was anti-correlated with correctness on the z0 corpora.
This tests every standard alternative and asks the only question that matters for
a cascade: is there ANY statistic that lets us reject Julia's wrong answers?

Note on logits: softmax is a shift-invariant transform, so
``log p_i - log p_j == z_i - z_j`` exactly. Logit margin, entropy, NLL and Brier
are therefore computed from the probabilities with no loss. Raw logits are not
needed to answer the selective-risk question.

Hygiene: temperature is fitted on DEV only and evaluated once on SEALED.
"""
from __future__ import annotations

import json
import math
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).parent))
from representation_ablation import (  # noqa: E402
    Runner, evaluate, load_corpus, render_options,
)

OUT = Path(__file__).resolve().parents[2] / "results" / "julia-claims"
OUT.mkdir(parents=True, exist_ok=True)
ERROR_BUDGETS = (0.01, 0.02, 0.05, 0.10)


def entropy(p):
    return -sum(x * math.log(x) for x in p if x > 0)


def collect(rows, runner):
    """Full probability vectors per row, using the current z0 representation."""
    out = []
    for r in rows:
        opts = render_options(r, "B0")
        pred, probs = runner.ask(r["state"], r["question"], opts)
        ids = [o[0] for o in opts]
        pred_id = ids[int(pred)]
        out.append(dict(id=r["id"], split=r["_split"], gold=r["_gold"], pred=pred_id,
                        correct=pred_id == r["_gold"], probs=probs, ids=ids))
    return out


def auroc(scores, labels):
    """Mann-Whitney AUROC of `scores` predicting `labels` (True=positive)."""
    pairs = sorted(zip(scores, labels), key=lambda t: t[0])
    pos = sum(1 for _, l in pairs if l)
    neg = len(pairs) - pos
    if pos == 0 or neg == 0:
        return None
    rank_sum, i = 0.0, 0
    while i < len(pairs):
        j = i
        while j + 1 < len(pairs) and pairs[j + 1][0] == pairs[i][0]:
            j += 1
        avg_rank = (i + j) / 2 + 1
        for k in range(i, j + 1):
            if pairs[k][1]:
                rank_sum += avg_rank
        i = j + 1
    return (rank_sum - pos * (pos + 1) / 2) / (pos * neg)


def risk_coverage(records, score_key, higher_is_confident=True):
    ordered = sorted(records, key=lambda r: -r[score_key] if higher_is_confident else r[score_key])
    curve, correct = [], 0
    for i, r in enumerate(ordered, 1):
        correct += r["correct"]
        curve.append({"coverage": i / len(ordered), "risk": 1 - correct / i})
    return curve


def coverage_at(curve, budget):
    """Largest coverage whose empirical risk is <= budget."""
    best = 0.0
    for point in curve:
        if point["risk"] <= budget:
            best = max(best, point["coverage"])
    return best


def ece(records, key, bins=10):
    vals = [r[key] for r in records]
    lo, hi = min(vals), max(vals)
    if hi <= lo:
        return 0.0
    tot, n = 0.0, len(records)
    for b in range(bins):
        a, z = lo + (hi - lo) * b / bins, lo + (hi - lo) * (b + 1) / bins
        bucket = [r for r in records if (a <= r[key] < z) or (b == bins - 1 and r[key] == hi)]
        if not bucket:
            continue
        conf = sum(r[key] for r in bucket) / len(bucket)
        acc = sum(1 for r in bucket if r["correct"]) / len(bucket)
        tot += len(bucket) / n * abs(conf - acc)
    return tot


def augment(records):
    for r in records:
        p = sorted(r["probs"], reverse=True)
        r["p1"] = p[0]
        r["softmax_margin"] = p[0] - p[1]
        r["logit_margin"] = math.log(p[0]) - math.log(p[1]) if p[1] > 0 else 30.0
        r["entropy"] = entropy(r["probs"])
        gi = r["ids"].index(r["gold"])
        r["nll"] = -math.log(max(r["probs"][gi], 1e-12))
        r["brier"] = sum((r["probs"][i] - (1.0 if r["ids"][i] == r["gold"] else 0.0)) ** 2
                         for i in range(len(r["probs"])))
    return records


def temperature_scale(records, dev):
    """Fit T on dev by minimising NLL; report ECE before/after on sealed."""
    best_t, best_nll = 1.0, None
    for t in [x / 20 for x in range(4, 121)]:
        tot = 0.0
        for r in dev:
            ps = [math.exp(math.log(max(v, 1e-12)) / t) for v in r["probs"]]
            s = sum(ps)
            ps = [v / s for v in ps]
            gi = r["ids"].index(r["gold"])
            tot += -math.log(max(ps[gi], 1e-12))
        if best_nll is None or tot < best_nll:
            best_t, best_nll = t, tot
    return best_t


def run_corpus(name):
    rows = load_corpus(name)
    runner = Runner()
    all_records = augment(collect(rows, runner))
    runner.close()
    dev = [r for r in all_records if r["split"] == "dev"]
    sealed = [r for r in all_records if r["split"] == "sealed"]

    # NLL and Brier are PROPER SCORING RULES: they take the gold label as input, so
    # they cannot be computed at inference time and their AUROC is 1.0 by
    # construction (a correct prediction has higher p_gold). They are reported only
    # to make that circularity explicit; they are never escalation signals.
    measures = {
        "p1": (True, "max softmax probability", False),
        "softmax_margin": (True, "p1 - p2", False),
        "logit_margin": (True, "logit margin (== log-prob margin)", False),
        "neg_entropy": (True, "negative entropy", False),
        "neg_nll": (True, "negative NLL -- CIRCULAR, needs gold", True),
        "neg_brier": (True, "negative Brier -- CIRCULAR, needs gold", True),
    }
    for r in all_records:
        r["neg_entropy"] = -r["entropy"]
        r["neg_nll"] = -r["nll"]
        r["neg_brier"] = -r["brier"]

    report = {"corpus": name, "n": len(all_records), "dev": len(dev), "sealed": len(sealed),
              "base_accuracy": {"dev": round(sum(1 for r in dev if r["correct"]) / len(dev), 4),
                                "sealed": round(sum(1 for r in sealed if r["correct"]) / len(sealed), 4)},
              "auroc_correctness": {}, "coverage_at_error_budget": {}, "ece": {}}

    print(f"=== {name} ===  dev={len(dev)} sealed={len(sealed)}")
    print(f"  base accuracy: dev={report['base_accuracy']['dev']:.4f} "
          f"sealed={report['base_accuracy']['sealed']:.4f}")
    labels = [r["correct"] for r in sealed]
    for key, (_hi, desc, circular) in measures.items():
        a = auroc([r[key] for r in sealed], labels)
        report["auroc_correctness"][key] = {"auroc": round(a, 4) if a else None,
                                           "meaning": desc, "circular": circular}
        curve = risk_coverage(sealed, key, higher_is_confident=key != "entropy")
        report["coverage_at_error_budget"][key] = {
            f"<= {int(b*100)}%": round(coverage_at(curve, b), 4) for b in ERROR_BUDGETS}
        flag = "  <-- CIRCULAR (needs gold; not usable)" if circular else ""
        print(f"  {key:<16} AUROC={a:.4f}{flag}" if a else f"  {key:<16} AUROC=n/a")

    report["ece"]["p1_sealed_uncalibrated"] = round(ece(sealed, "p1"), 4)
    t = temperature_scale(all_records, dev)
    for r in all_records:
        ps = [math.exp(math.log(max(v, 1e-12)) / t) for v in r["probs"]]
        s = sum(ps)
        r["p1_cal"] = max(v / s for v in ps)
    report["temperature"] = {"fitted_on_dev": t}
    report["ece"]["p1_sealed_calibrated"] = round(ece(sealed, "p1_cal"), 4)
    report["auroc_correctness"]["p1_calibrated"] = {
        "auroc": round(auroc([r["p1_cal"] for r in sealed], labels), 4),
        "meaning": "temperature-scaled max probability"}

    print(f"  ECE p1: uncalibrated={report['ece']['p1_sealed_uncalibrated']:.4f} "
          f"calibrated(T={t})={report['ece']['p1_sealed_calibrated']:.4f}")
    print("  coverage achievable at empirical error budget (SEALED):")
    for key in measures:
        if measures[key][2]:
            continue
        budgets = report["coverage_at_error_budget"][key]
        print(f"    {key:<16} " + "  ".join(f"{k}:{v:.2%}" for k, v in budgets.items()))

    # accuracy by confidence decile (sealed)
    ordered = sorted(sealed, key=lambda r: -r["p1"])
    dec = []
    for i in range(10):
        chunk = ordered[i * len(ordered) // 10:(i + 1) * len(ordered) // 10]
        if chunk:
            dec.append({"decile": i + 1, "mean_p1": round(sum(r["p1"] for r in chunk) / len(chunk), 4),
                        "accuracy": round(sum(1 for r in chunk if r["correct"]) / len(chunk), 4)})
    report["accuracy_by_confidence_decile_sealed"] = dec
    print("  accuracy by confidence decile (sealed): " +
          " ".join(f"{d['accuracy']:.2f}" for d in dec))

    (OUT / f"calibration-{name}.json").write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    return report


if __name__ == "__main__":
    reports = {}
    for name in ("authored144", "perturbations108"):
        reports[name] = run_corpus(name)
        print()
    (OUT / "calibration-summary.json").write_text(json.dumps(reports, indent=2, sort_keys=True) + "\n")
    print(f"wrote {OUT}/calibration-summary.json")
