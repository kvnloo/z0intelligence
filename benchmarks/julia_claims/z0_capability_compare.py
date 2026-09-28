#!/usr/bin/env python3
"""Phase 5-8 — baselines and backend comparison on tool_family_select.

Representation (R0/R1/R2) is selected on DEV, confirmed on VALIDATION, and only
then evaluated once on SEALED. Baselines are measured before any model so we know
how hard the capability actually is.
"""
from __future__ import annotations

import argparse
import collections
import json
import statistics
import time
from pathlib import Path

OUT = Path(__file__).resolve().parents[2] / "results" / "julia-claims"
DATA = OUT / "capability-tool_family_select.jsonl"

import sys
sys.path.insert(0, str(Path(__file__).parent))
from z0_capabilities import DESCRIPTIONS, LABELS, REPRESENTATIONS  # noqa: E402

KEYWORDS = {
    "WEB": ("web", "http", "url", "browse", "online", "google", "site", "search the web"),
    "DELEGATE": ("delegate", "kanban", "todo", "task list", "subagent", "scout", "assign"),
    "EXECUTE": ("run ", "execute", "test", "build", "install", "terminal", "command", "script"),
}


def keyword_rule(state: str) -> str:
    s = state.lower()
    for fam, words in KEYWORDS.items():
        if any(w in s for w in words):
            return fam
    return "READ_SEARCH"


def load():
    rows = [json.loads(l) for l in DATA.read_text().splitlines() if l.strip()]
    for r in rows:
        r["split"] = r["split"]
    return rows


def majority(rows) -> str:
    return collections.Counter(r["gold"] for r in rows).most_common(1)[0][0]


def macro_f1(rows, key) -> float:
    labels = sorted({r["gold"] for r in rows})
    f1s = []
    for lab in labels:
        tp = sum(1 for r in rows if r["gold"] == lab and r[key] == lab)
        fp = sum(1 for r in rows if r["gold"] != lab and r[key] == lab)
        fn = sum(1 for r in rows if r["gold"] == lab and r[key] != lab)
        prec = tp / (tp + fp) if tp + fp else 0.0
        rec = tp / (tp + fn) if tp + fn else 0.0
        f1s.append(2 * prec * rec / (prec + rec) if prec + rec else 0.0)
    return sum(f1s) / len(f1s) if f1s else 0.0


def report(rows, key):
    n = len(rows)
    correct = sum(1 for r in rows if r[key] == r["gold"])
    return {"n": n, "correct": correct, "accuracy": round(correct / n, 4) if n else None,
            "macro_f1": round(macro_f1(rows, key), 4) if n else None}


def run_julia(rows, rep):
    from z0int.backends.base import DecisionOption, DecisionQuestion, DecisionRequest
    from z0int.backends.julia import JuliaBackend

    b = JuliaBackend.for_manifest_id("julia_1")
    b.max_length = 1024
    b.head_length = 512
    h = b.health(load=True)
    if not h.loaded:
        raise SystemExit(h.detail)
    render = REPRESENTATIONS[rep]
    opts = tuple(DecisionOption(l, render(l)) for l in LABELS)
    out, lat = [], []
    for r in rows:
        req = DecisionRequest(state=r["state"],
                              questions=(DecisionQuestion(
                                  id="family", type="choice",
                                  instructions="Which tool family should handle this request?",
                                  options=opts),),
                              request_id=r["example_id"])
        t = time.perf_counter()
        try:
            res = b.evaluate(req)
            lat.append((time.perf_counter() - t) * 1000)
            r["julia"] = res.answers[0].value
            r["julia_p"] = max(res.answers[0].probabilities.values())
            r["julia_entropy"] = -sum(p * __import__("math").log(p)
                                      for p in res.answers[0].probabilities.values() if p > 0)
            order = sorted(res.answers[0].probabilities.values(), reverse=True)
            r["julia_margin"] = order[0] - order[1]
            r["julia_err"] = None
        except Exception as exc:  # noqa: BLE001
            r["julia"] = None
            r["julia_err"] = f"{type(exc).__name__}: {exc}"[:150]
    b.close()
    return lat


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--backends", default="baselines,julia,laya")
    args = ap.parse_args()
    rows = load()
    dev = [r for r in rows if r["split"] == "dev"]
    val = [r for r in rows if r["split"] == "validation"]
    sealed = [r for r in rows if r["split"] == "sealed"]
    print(f"tool_family_select: dev={len(dev)} validation={len(val)} sealed={len(sealed)}")

    # ---------------------------------------------------------------- baselines
    maj = majority(dev)
    for r in rows:
        r["majority"] = maj
        r["keyword"] = keyword_rule(r["state"])
    print(f"\n=== baselines (majority learned on dev = {maj}) ===")
    base = {}
    for split, rs in (("dev", dev), ("validation", val), ("sealed", sealed)):
        for key in ("majority", "keyword"):
            base[f"{key}:{split}"] = report(rs, key)
            print(f"  {key:<10} {split:<11} acc={base[f'{key}:{split}']['accuracy']:.4f} "
                  f"macroF1={base[f'{key}:{split}']['macro_f1']:.4f}")

    results = {"capability": "tool_family_select", "majority_label": maj, "baselines": base}

    # -------------------------------------------------------------------- Julia
    if "julia" in args.backends:
        print("\n=== Julia representation selection (dev -> validation) ===")
        rep_scores = {}
        for rep in ("R0", "R1", "R2"):
            for r in dev:
                r.pop("julia", None)
            lat = run_julia(dev, rep)
            rep_scores[rep] = report(dev, "julia")
            rep_scores[rep]["p50_ms"] = round(statistics.median(lat), 2) if lat else None
            print(f"  {rep} dev acc={rep_scores[rep]['accuracy']:.4f} "
                  f"macroF1={rep_scores[rep]['macro_f1']:.4f} p50={rep_scores[rep]['p50_ms']}ms")
        best = max(("R0", "R1", "R2"), key=lambda k: (rep_scores[k]["accuracy"] or 0,
                                                     rep_scores[k]["macro_f1"] or 0))
        for r in val:
            r.pop("julia", None)
        run_julia(val, best)
        val_rep = report(val, "julia")
        print(f"  SELECTED {best} on dev; validation acc={val_rep['accuracy']:.4f}")
        for r in sealed:
            r.pop("julia", None)
        sealed_lat = run_julia(sealed, best)
        sealed_rep = report(sealed, "julia")
        errs = sum(1 for r in sealed if r.get("julia_err"))
        print(f"  SEALED ({best}) acc={sealed_rep['accuracy']:.4f} "
              f"macroF1={sealed_rep['macro_f1']:.4f} failures={errs} "
              f"p50={round(statistics.median(sealed_lat),2) if sealed_lat else None}ms")
        results["julia"] = {"representation_selected_on_dev": best,
                            "dev": rep_scores, "validation": val_rep,
                            "sealed": sealed_rep, "sealed_failures": errs,
                            "sealed_p50_ms": round(statistics.median(sealed_lat), 2) if sealed_lat else None}
        # confusion + per-class
        labels = sorted({r["gold"] for r in sealed} | {r["julia"] for r in sealed if r.get("julia")})
        conf = {g: {p: 0 for p in labels} for g in labels}
        for r in sealed:
            if r.get("julia"):
                conf[r["gold"]][r["julia"]] += 1
        results["julia"]["confusion_sealed"] = conf
        preds = collections.Counter(r["julia"] for r in sealed if r.get("julia"))
        results["julia"]["sealed_prediction_distribution"] = dict(preds)
        golds = collections.Counter(r["gold"] for r in sealed)
        results["julia"]["sealed_gold_distribution"] = dict(golds)
        print(f"  gold dist : {dict(golds)}")
        print(f"  julia dist: {dict(preds)}")
        print("  confusion (rows=gold, cols=pred):")
        for g in labels:
            print(f"    {g:<12} " + " ".join(f"{conf[g][p]:>4}" for p in labels))
        # option-order robustness (Phase 8)
        print("\n=== option-order robustness (sealed, 8 deterministic permutations) ===")
        import itertools
        perms = list(itertools.permutations(range(len(LABELS))))[::5040 // 8][:8]
        flips = collections.Counter()
        accs = []
        base_pred = {r["example_id"]: r.get("julia") for r in sealed}
        for p in perms:
            order = [LABELS[i] for i in p]
            from z0int.backends.base import DecisionOption, DecisionQuestion, DecisionRequest
            from z0int.backends.julia import JuliaBackend
            b = JuliaBackend.for_manifest_id("julia_1")
            b.max_length, b.head_length = 1024, 512
            b.health(load=True)
            render = REPRESENTATIONS[best]
            opts = tuple(DecisionOption(l, render(l)) for l in order)
            c = 0
            for r in sealed:
                res = b.evaluate(DecisionRequest(
                    state=r["state"],
                    questions=(DecisionQuestion(id="f", type="choice",
                                                instructions="Which tool family should handle this request?",
                                                options=opts),),
                    request_id=r["example_id"]))
                pred = res.answers[0].value
                c += pred == r["gold"]
                if pred != base_pred[r["example_id"]]:
                    flips[r["example_id"]] += 1
            accs.append(c / len(sealed))
            b.close()
        results["julia"]["order_robustness"] = {
            "permutations": len(perms), "accuracy_min": round(min(accs), 4),
            "accuracy_max": round(max(accs), 4), "accuracy_range": round(max(accs) - min(accs), 4),
            "rows_with_changed_winner": len(flips), "n": len(sealed),
            "flip_rate": round(len(flips) / len(sealed), 4)}
        print(f"  acc range {min(accs):.4f}-{max(accs):.4f} (range {max(accs)-min(accs):.4f}) "
              f"rows changed={len(flips)}/{len(sealed)}")

    # --------------------------------------------------------------------- Laya
    if "laya" in args.backends:
        try:
            from z0int.backends.base import DecisionOption, DecisionQuestion, DecisionRequest
            from z0int.backends.registry import create_backend

            b = create_backend("laya_421m")
            h = b.health(load=True)
            print(f"\n=== Laya (ready={h.ready} loaded={h.loaded}) ===")
            if h.loaded:
                opts = tuple(DecisionOption(l, DESCRIPTIONS[l]) for l in LABELS)
                lat = []
                for r in sealed:
                    t = time.perf_counter()
                    res = b.evaluate(DecisionRequest(
                        state=r["state"],
                        questions=(DecisionQuestion(id="f", type="choice",
                                                    instructions="Which tool family should handle this request?",
                                                    options=opts),),
                        request_id=r["example_id"]))
                    lat.append((time.perf_counter() - t) * 1000)
                    r["laya"] = res.answers[0].value
                rep = report(sealed, "laya")
                rep["p50_ms"] = round(statistics.median(lat), 2)
                results["laya_sealed"] = rep
                print(f"  sealed acc={rep['accuracy']:.4f} macroF1={rep['macro_f1']:.4f} "
                      f"p50={rep['p50_ms']}ms")
        except Exception as exc:  # noqa: BLE001
            print(f"  laya unavailable: {type(exc).__name__}: {exc}")
            results["laya_sealed"] = {"error": str(exc)[:200]}

    (OUT / "capability-tool_family_select-results.json").write_text(
        json.dumps(results, indent=2, sort_keys=True) + "\n")
    (OUT / "capability-tool_family_select-scored.jsonl").write_text(
        "\n".join(json.dumps({k: v for k, v in r.items() if k != "group_id"}, sort_keys=True)
                  for r in rows) + "\n")
    print(f"\nwrote {OUT}/capability-tool_family_select-results.json")


if __name__ == "__main__":
    main()
