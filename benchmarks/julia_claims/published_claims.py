#!/usr/bin/env python3
"""Phase 2B/2C — reproduce the published BTZSC classification pilots.

Protocol is taken from the pinned benchmark repo, not reimplemented by guesswork:
the balanced sampler is IMPORTED from

    AbdelStark/jev-benchmarks @ 0d610cc53e79bcbec691312b0c4adb4a0e371642
    src/jev_benchmarks/data.py::_balanced_indices

so example selection is byte-identical to the published run. Label descriptions
are the ordered BTZSC `hypothesis` strings, exactly as data.py builds them.

configs/pilot-v1.yaml: seed 20260917, samples_per_dataset 100,
datasets [agnews(topic), emotiondair(emotion), banking77(intent)],
question "Which single label best describes the input text?".
"""
from __future__ import annotations

import argparse
import collections
import hashlib
import json
import sys
from pathlib import Path

BTZSC = Path("/home/kvn/tmp/btzsc")
JEVB = Path("/home/kvn/tmp/jev-benchmarks")
REPO = Path(__file__).resolve().parents[2]
OUT = REPO / "results" / "julia-claims"
OUT.mkdir(parents=True, exist_ok=True)

SEED = 20260917
SAMPLES = 100
QUESTION = "Which single label best describes the input text?"
DATASETS = [("agnews", "topic"), ("emotiondair", "emotion"), ("banking77", "intent")]

sys.path.insert(0, str(JEVB / "src"))
from jev_benchmarks.data import _balanced_indices  # noqa: E402  (pinned protocol)

PUBLISHED = {"agnews": "94/100", "emotiondair": "86/100", "emotion": "86/100 (card says DAIR Emotion)", "banking77": "64/100"}


def pilot_examples(name: str, offset: int):
    """Exactly jev_benchmarks.data.load_examples for one dataset, from local parquet."""
    # The CUDA interpreter has no pyarrow; the manifest written by the CPU run is
    # the same example set (identical pinned sampler), so reuse it when present.
    cached = OUT / f"{name}-manifest.jsonl"
    if cached.is_file():
        ex = [json.loads(l) for l in cached.read_text().splitlines() if l.strip()]
        return ex, list(ex[0]["labels"])

    import pyarrow.parquet as pq

    path = BTZSC / name / "test-00000-of-00001.parquet"
    rows = pq.read_table(path).to_pylist()
    binary = [int(r["labels"]) for r in rows]
    texts = [str(r["text"]) for r in rows]
    first = texts[0]
    n_classes = next((i for i in range(1, len(texts)) if texts[i] != first), None)
    if n_classes is None:
        raise ValueError("could not infer class count")
    total = len(rows) // n_classes
    labels = tuple(str(rows[i]["hypothesis"]) for i in range(n_classes))

    valid, targets = [], []
    for sample_index in range(total):
        off = sample_index * n_classes
        vals = binary[off:off + n_classes]
        if sum(vals) == 1:
            valid.append(sample_index)
            targets.append(vals.index(1))
    positions = _balanced_indices(targets, SAMPLES, SEED + offset)
    out = []
    for pos in positions:
        si = valid[pos]
        text = texts[si * n_classes]
        out.append(dict(dataset=name, example_id=f"{name}:{si}", text=text,
                        text_sha256=hashlib.sha256(text.encode()).hexdigest(),
                        labels=list(labels), target_index=targets[pos],
                        n_classes=n_classes))
    return out, labels


def run(name: str, offset: int, n: int, limit: int, device="cpu", autocast="off"):
    from z0int.backends.base import (
        DecisionOption, DecisionQuestion, DecisionRequest,
    )
    from z0int.backends.julia import JuliaBackend

    examples, labels = pilot_examples(name, offset)
    if limit:
        examples = examples[:limit]
    print(f"{name}: {len(examples)} examples, {len(labels)} ordered labels")
    for i, l in enumerate(labels[:4]):
        print(f"   [{i}] {l[:70]}")
    if len(labels) > 4:
        print(f"   ... {len(labels)-4} more")

    manifest_path = OUT / f"{name}-manifest.jsonl"
    manifest_path.write_text("\n".join(json.dumps(e, sort_keys=True) for e in examples) + "\n")
    manifest_sha = hashlib.sha256(manifest_path.read_bytes()).hexdigest()

    backend = JuliaBackend.for_manifest_id("julia_1")
    backend.device = device
    backend.autocast = autocast
    backend.max_length = 1024
    backend.head_length = 512
    h = backend.health(load=True)
    if not h.loaded:
        raise SystemExit(h.detail)

    opts = tuple(DecisionOption(str(i), d) for i, d in enumerate(labels))
    if len(opts) > 20:
        print(f"  NOTE: {len(opts)} labels exceeds Julia's 20-option native limit; "
              f"a single native call cannot represent this task")
        backend.close()
        return None

    preds = []
    stream = (OUT / f"{name}-predictions.jsonl").open("w")
    for e in examples:
        req = DecisionRequest(
            state=e["text"],
            questions=(DecisionQuestion(id="label", type="choice",
                                        instructions=QUESTION, options=opts),),
            request_id=e["example_id"])
        try:
            res = backend.evaluate(req)
            probs = res.answers[0].probabilities
            order = list(probs)
            idx = order.index(res.answers[0].value) if res.answers[0].value in order else \
                max(range(len(order)), key=lambda j: probs[order[j]])
            pred_index = int(order[idx])
            pred = labels[pred_index]
            correct = pred_index == e["target_index"]
            err = None
        except Exception as exc:  # noqa: BLE001
            pred, pred_index, correct, err = None, None, False, f"{type(exc).__name__}: {exc}"[:200]
        preds.append(dict(example_id=e["example_id"], correct=correct, error=err))
        stream.write(json.dumps(dict(example_id=e["example_id"],
                                     target_index=e["target_index"], prediction_index=pred_index,
                                     correct=correct, error=err,
                                     text_sha256=e["text_sha256"]), sort_keys=True) + "\n")
    stream.close()
    backend.close()

    answered = sum(1 for p in preds if p["error"] is None)
    correct = sum(1 for p in preds if p["correct"])
    report = {
        "dataset": name, "n": len(examples), "correct": correct, "answered": answered,
        "accuracy": round(correct / len(examples), 4),
        "coverage": round(answered / len(examples), 4),
        "abstained": len(examples) - answered,
        "published": PUBLISHED.get(name, "n/a"),
        "protocol": {"seed": SEED, "samples_per_dataset": SAMPLES,
                     "dataset_offset": offset, "question": QUESTION,
                     "labels": list(labels), "n_classes": len(labels),
                     "btzsc_revision": "fef2a2ac62b69c58670047dddf045c53d7c3cb5e",
                     "jev_bench_commit": "0d610cc53e79bcbec691312b0c4adb4a0e371642",
                     "sampler": "jev_benchmarks.data._balanced_indices (imported from the pinned repo)"},
        "manifest_sha256": manifest_sha,
        "encoding": {"max_length": 1024, "head_length": 512, "strict": True},
        "device": device, "autocast": autocast,
        "dtype": "bfloat16" if autocast == "auto" and device == "cuda" else "float32",
    }
    (OUT / f"{name}-summary-{device}-{autocast}.json").write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    print(f"  -> {correct}/{len(examples)} = {correct/len(examples):.4f}   published {PUBLISHED[name]}")
    return report


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dataset", default="all", choices=["all", "agnews", "emotiondair", "banking77"])
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--device", default="cpu")
    ap.add_argument("--autocast", default="off")
    args = ap.parse_args()
    offsets = {n: i for i, (n, _) in enumerate(DATASETS)}
    wanted = DATASETS if args.dataset == "all" else [(args.dataset, dict(DATASETS)[args.dataset])]
    results = []
    for name, _task in wanted:
        r = run(name, offsets[name], SAMPLES, args.limit,
                device=args.device, autocast=args.autocast)
        if r:
            results.append(r)
            print()
    if results:
        (OUT / "btzsc-pilot-summary.json").write_text(
            json.dumps({"results": results}, indent=2, sort_keys=True) + "\n")
        print("=== published vs reproduced ===")
        for r in results:
            print(f"  {r['dataset']:<12} reproduced {r['correct']}/{r['n']} "
                  f"({r['accuracy']:.2%})  published {r['published']}")


if __name__ == "__main__":
    main()
