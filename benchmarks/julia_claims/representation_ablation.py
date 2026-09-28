#!/usr/bin/env python3
"""Phase 3 — controlled representation ablations on the z0 corpora.

No transformation below adds information or changes the gold answer. Every
variation is deterministic and globally frozen (never per-example).

Hygiene: splits are by `group_id`, so every paraphrase/perturbation of a source
example stays in one split. Representation choices are made on DEV only; the
dev-selected combination is then evaluated once on SEALED.

Factors
  A  state rendering   A0 as-is | A1 labelled NL | A2 fact list
  B  option rendering  B0 corpus descriptions | B1 short ids | B2 frozen definitions
  D  question wording  D0 corpus | D1 preregistered | D2 preregistered
  E  option order      all 6 permutations of the 3-option sets
  F  batch invariance  question alone vs batched with 2 others
"""
from __future__ import annotations

import argparse
import hashlib
import itertools
import json
from pathlib import Path

REPO = Path("/home/kvn/tmp/z0int-canonical")
OUT = Path(__file__).resolve().parents[2] / "results" / "julia-claims"
OUT.mkdir(parents=True, exist_ok=True)

# ---------------------------------------------------------------- frozen schema
# Derived from the corpora's own taxonomies and frozen globally. Hashed below.
FROZEN_DEFINITIONS = {
    "evidence_interpretation": {
        "supported": "The supplied evidence establishes the claim",
        "insufficient": "The supplied information does not establish or refute the claim",
        "contradicted": "The supplied evidence establishes the opposite of the claim",
    },
    "rule_application": {
        "permitted": "The supplied rules allow the requested action",
        "prohibited": "The supplied rules forbid the requested action",
        "insufficient": "The supplied information does not determine whether the action is allowed",
    },
    "candidate_selection": {
        "A": "Candidate A is the one the question asks for",
        "B": "Candidate B is the one the question asks for",
        "insufficient": "Neither candidate supplies the requested evidence",
    },
}
SHORT_LABELS = {
    "evidence_interpretation": {"supported": "supported", "insufficient": "insufficient",
                                "contradicted": "contradicted"},
    "rule_application": {"permitted": "permitted", "prohibited": "prohibited",
                         "insufficient": "insufficient"},
    "candidate_selection": {"A": "A", "B": "B", "insufficient": "insufficient"},
}
QUESTIONS = {
    "D0": None,  # corpus question
    "D1": "Which single option is best supported by the supplied state?",
    "D2": "Based only on the supplied state, choose the applicable outcome.",
}


def schema_sha() -> str:
    blob = json.dumps({"definitions": FROZEN_DEFINITIONS, "short": SHORT_LABELS,
                       "questions": {k: v for k, v in QUESTIONS.items() if v}},
                      sort_keys=True)
    return hashlib.sha256(blob.encode()).hexdigest()


# ------------------------------------------------------------------ renderings
def render_state(rows_family, state, variant):
    if variant == "A0":
        return state
    if variant == "A1":
        return f"State:\n{state}"
    if variant == "A2":
        return f"Known facts:\n- {state}"
    raise ValueError(variant)


def render_options(row, variant):
    fam, opts = row["family"], row["options"]
    if variant == "B0":
        return [(o["id"], o["description"]) for o in opts]
    if variant == "B1":
        return [(o["id"], SHORT_LABELS[fam][o["id"]]) for o in opts]
    if variant == "B2":
        return [(o["id"], FROZEN_DEFINITIONS[fam][o["id"]]) for o in opts]
    raise ValueError(variant)


def render_question(row, variant):
    q = QUESTIONS[variant]
    return row["question"] if q is None else q


# ---------------------------------------------------------------------- splits
def split_of(group_id: str) -> str:
    h = int(hashlib.sha256(group_id.encode()).hexdigest()[:8], 16)
    return "dev" if h % 3 != 0 else "sealed"


def load_corpus(name):
    rows = [json.loads(l) for l in
            (REPO / f"benchmarks/data/{name}.jsonl").read_text().splitlines() if l.strip()]
    jev = {r["id"]: r.get("pred_id") for r in
           (json.loads(l) for l in
            (REPO / f"manifests/capability-evidence/jev-bench-{name}.jsonl").read_text().splitlines()
            if l.strip())}
    for r in rows:
        r["_split"] = split_of(r.get("group_id") or r["id"])
        r["_gold"] = r["options"][r["label"]]["id"]
        r["_jev"] = jev.get(r["id"])
    return rows


# ------------------------------------------------------------------- inference
class Runner:
    def __init__(self, max_length=1024, head_length=512):
        from z0int.backends.julia import JuliaBackend

        self.b = JuliaBackend.for_manifest_id("julia_1")
        self.b.max_length = max_length
        self.b.head_length = head_length
        h = self.b.health(load=True)
        if not h.loaded:
            raise SystemExit(h.detail)

    def ask(self, state, question, options):
        from z0int.backends.base import (
            DecisionOption, DecisionQuestion, DecisionRequest,
        )
        # synthetic positional ids so any option order maps back unambiguously
        opts = tuple(DecisionOption(str(i), str(desc))
                     for i, (_oid, desc) in enumerate(options))
        req = DecisionRequest(state=state,
                              questions=(DecisionQuestion(id="d", type="choice",
                                                          instructions=question, options=opts),))
        res = self.b.evaluate(req)
        probs = res.answers[0].probabilities
        order = list(probs)
        idx = order.index(res.answers[0].value)
        return order[idx], [probs[k] for k in order]

    def close(self):
        self.b.close()


def evaluate(rows, runner, A="A0", B="B0", D="D0", order=None):
    correct = 0
    preds = {}
    for r in rows:
        opts = render_options(r, B)
        idxs = list(range(len(opts)))
        if order is not None:
            idxs = [idxs[i] for i in order]
        opts = [opts[i] for i in idxs]
        pred_local, _ = runner.ask(render_state(r["family"], r["state"], A),
                                   render_question(r, D), opts)
        # pred_local is a synthetic option id ("0","1",...); map back
        pred = opts[int(pred_local)][0]
        preds[r["id"]] = pred
        correct += pred == r["_gold"]
    return correct, preds


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--corpus", default="authored144")
    ap.add_argument("--mode", default="ablate", choices=("ablate", "order", "batch", "combo"))
    ap.add_argument("--combo", default="")
    ap.add_argument("--sealed-combo", default="")
    args = ap.parse_args()

    rows = load_corpus(args.corpus)
    dev = [r for r in rows if r["_split"] == "dev"]
    sealed = [r for r in rows if r["_split"] == "sealed"]
    print(f"{args.corpus}: n={len(rows)} dev={len(dev)} sealed={len(sealed)} "
          f"families={sorted({r['family'] for r in rows})}")
    print(f"schema_sha256={schema_sha()[:16]}")

    runner = Runner()
    results = {"corpus": args.corpus, "schema_sha256": schema_sha(),
               "n": len(rows), "dev": len(dev), "sealed": len(sealed), "variants": {}}

    if args.mode in ("ablate", "combo"):
        c0_dev, _ = evaluate(dev, runner)
        c0_sealed, _ = evaluate(sealed, runner)
        results["baseline"] = {"A": "A0", "B": "B0", "D": "D0",
                               "dev_acc": round(c0_dev / len(dev), 4),
                               "sealed_acc": round(c0_sealed / len(sealed), 4)}
        results["baseline_sealed"] = round(c0_sealed / len(sealed), 4)
        print(f"  BASELINE (current z0 representation): dev={c0_dev/len(dev):.4f} "
              f"sealed={c0_sealed/len(sealed):.4f}")

    if args.mode == "ablate":
        variants = ([(f"A0", "A", a) for a in ("A0", "A1", "A2")] +
                    [(f"B0", "B", b) for b in ("B0", "B1", "B2")] +
                    [(f"D0", "D", d) for d in ("D0", "D1", "D2")])
        for label, axis, val in variants:
            kw = {axis: val}
            c_dev, _ = evaluate(dev, runner, **kw)
            c_sealed, _ = evaluate(sealed, runner, **kw)
            acc_dev, acc_sealed = c_dev / len(dev), c_sealed / len(sealed)
            jdev = sum(1 for r in dev if r["_jev"] == r["_gold"]) / len(dev)
            results["variants"][f"{axis}={val}"] = {
                "dev_acc": round(acc_dev, 4), "sealed_acc": round(acc_sealed, 4),
                "dev_correct": c_dev, "sealed_correct": c_sealed,
                "jev_dev_acc": round(jdev, 4)}
            print(f"  {axis}={val:<3} dev={acc_dev:.4f} ({c_dev}/{len(dev)}) "
                  f"sealed={acc_sealed:.4f} ({c_sealed}/{len(sealed)})")

    elif args.mode == "combo":
        # dev-selected best per axis, evaluated ONCE on sealed
        parts = dict(x.split("=") for x in args.combo.split(","))
        A, B, D = parts.get("A", "A0"), parts.get("B", "B0"), parts.get("D", "D0")
        c_dev, _ = evaluate(dev, runner, A=A, B=B, D=D)
        c_sealed, preds = evaluate(sealed, runner, A=A, B=B, D=D)
        base_sealed = results.get("baseline_sealed")
        results["combo"] = {"A": A, "B": B, "D": D,
                            "dev_acc": round(c_dev / len(dev), 4),
                            "sealed_acc": round(c_sealed / len(sealed), 4),
                            "sealed_correct": c_sealed, "sealed_n": len(sealed),
                            "dev_selected": True}
        print(f"  COMBO A={A} B={B} D={D}: dev={c_dev/len(dev):.4f} "
              f"sealed={c_sealed/len(sealed):.4f} ({c_sealed}/{len(sealed)})")
        # per-family sealed breakdown
        fam = {}
        for r in sealed:
            f = fam.setdefault(r["family"], [0, 0])
            f[1] += 1
            f[0] += preds[r["id"]] == r["_gold"]
        results["combo"]["sealed_by_family"] = {
            k: {"correct": v[0], "n": v[1], "acc": round(v[0] / v[1], 4)} for k, v in fam.items()}
        jev = sum(1 for r in sealed if r["_jev"] == r["_gold"]) / len(sealed)
        results["combo"]["jev_sealed_acc"] = round(jev, 4)
        print(f"  Jev on the same sealed rows: {jev:.4f}")

    elif args.mode == "order":
        # all 6 permutations of the 3-option sets, on the sealed split
        perms = list(itertools.permutations(range(3)))
        accs, flips = [], {r["id"]: set() for r in sealed}
        base = None
        for p in perms:
            c, preds = evaluate(sealed, runner, order=list(p))
            accs.append(c / len(sealed))
            for rid, pred in preds.items():
                flips[rid].add(pred)
            if base is None:
                base = preds
        unstable = [rid for rid, s in flips.items() if len(s) > 1]
        results["order"] = {
            "permutations": [list(p) for p in perms],
            "accuracies": [round(a, 4) for a in accs],
            "accuracy_min": round(min(accs), 4), "accuracy_max": round(max(accs), 4),
            "accuracy_range": round(max(accs) - min(accs), 4),
            "instances_with_changed_winner": len(unstable),
            "instances": len(sealed),
            "flip_rate": round(len(unstable) / len(sealed), 4),
        }
        print(json.dumps(results["order"], indent=2))

    elif args.mode == "batch":
        # Julia scores named questions independently; verify that claim
        from z0int.backends.base import (
            DecisionOption, DecisionQuestion, DecisionRequest,
        )
        single, batched = {}, {}
        for r in sealed:
            opts = render_options(r, "B0")
            dq = DecisionQuestion(id="d", type="choice", instructions=r["question"],
                                  options=tuple(DecisionOption(str(i), str(desc))
                                                for i, (_oid, desc) in enumerate(opts)))
            res = runner.b.evaluate(DecisionRequest(state=r["state"], questions=(dq,),
                                                    request_id=r["id"]))
            probs = res.answers[0].probabilities
            order = list(probs)
            single[r["id"]] = (order.index(res.answers[0].value), [probs[k] for k in order])
        for r in sealed:
            others = [x for x in sealed if x["id"] != r["id"]][:2]
            qs, keys = [], []
            for j, x in enumerate([r] + others):
                o = render_options(x, "B0")
                qs.append(DecisionQuestion(id=f"q{j}", type="choice", instructions=x["question"],
                                           options=tuple(DecisionOption(str(i), str(desc))
                                                         for i, (_oid, desc) in enumerate(o))))
                keys.append(x["id"])
            res = runner.b.evaluate(DecisionRequest(state=r["state"], questions=tuple(qs),
                                                    request_id="batch"))
            a = [x for x in res.answers if x.question_id == "q0"][0]
            probs = a.probabilities
            order = list(probs)
            batched[r["id"]] = (order.index(a.value), [probs[k] for k in order])
        same_winner = sum(1 for k in single if single[k][0] == batched[k][0])
        max_delta = max(max(abs(p - q) for p, q in zip(single[k][1], batched[k][1]))
                        for k in single)
        results["batch"] = {"n": len(single), "same_winner": same_winner,
                            "winner_agreement": round(same_winner / len(single), 4),
                            "max_probability_delta": max_delta}
        print(json.dumps(results["batch"], indent=2))

    runner.close()
    (OUT / f"ablation-{args.corpus}-{args.mode}.json").write_text(
        json.dumps(results, indent=2, sort_keys=True) + "\n")
    print(f"\nwrote {OUT}/ablation-{args.corpus}-{args.mode}.json")


if __name__ == "__main__":
    main()
