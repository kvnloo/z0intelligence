#!/usr/bin/env python3
"""Same-split model-free baselines and dev-fitted confidence-gated replay.

No model inference or fresh teacher calls. Escalated decisions use the recorded
teacher label as a counterfactual oracle, never as an observed fresh Jev result.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import hashlib
import json
from pathlib import Path
import statistics
import time

from slm_context_replay import create, interval, load, percentile
from z0int.slm_context import examples_for, split_rows


def vocabulary(row):
    return tuple(option["id"] for option in row["options"])


def metrics(rows):
    return {"n": len(rows), "groups": len({r["group"] for r in rows}),
            "accuracy": statistics.fmean(r["correct"] for r in rows) if rows else None,
            "ci95": interval(rows, "correct") if rows else None}


def model_free(dataset, manifest):
    split, regenerated = split_rows(dataset, manifest["seed"])
    if regenerated["assignments"] != manifest["assignments"]:
        raise ValueError("dataset no longer reproduces frozen group assignments")
    majority = defaultdict(Counter)
    for row in split["pool"]:
        majority[row["family"], vocabulary(row)][row["teacher_label"]] += 1
    predictions = []
    for partition in ("dev", "test"):
        for row in split[partition]:
            started = time.perf_counter()
            neighbors = examples_for(row, split["pool"], limit=5)
            elapsed = (time.perf_counter() - started) * 1000
            counts = majority[row["family"], vocabulary(row)]
            fixed = max(counts, key=counts.get) if counts else None
            base = {"row_id": row["id"], "family": row["family"],
                    "group": manifest["group_clusters"][row["group"]], "split": partition}
            for k in (1, 3, 5):
                selected = neighbors[:k]
                votes = Counter(example["choice"] for example in selected)
                # Among tied label vote counts, choose the closest supporting
                # neighbor. Retriever similarity ties already use stable row IDs.
                choice = max((example["choice"] for example in selected), key=lambda label: votes[label]) if selected else None
                predictions.append({**base, "policy": f"knn_only_k{k}", "choice": choice,
                                    "correct": int(choice == row["teacher_label"]), "latency_ms": elapsed})
            predictions.append({**base, "policy": "available_vocabulary_majority", "choice": fixed,
                                "correct": int(fixed == row["teacher_label"]), "latency_ms": None})
    grouped = defaultdict(list)
    for row in predictions:
        grouped[row["family"], row["split"], row["policy"]].append(row)
    output = []
    for (family, partition, policy), rows in sorted(grouped.items()):
        times = [r["latency_ms"] for r in rows if r["latency_ms"] is not None]
        baseline = {r["row_id"]: r["correct"] for r in grouped[family, partition, "available_vocabulary_majority"]}
        output.append({"family": family, "split": partition, "policy": policy, **metrics(rows),
                       "delta_vs_majority": statistics.fmean(r["correct"] - baseline[r["row_id"]] for r in rows),
                       "delta_vs_majority_ci95": interval(rows, "correct", paired=baseline),
                       "latency_p50_ms": percentile(times, .5), "latency_p95_ms": percentile(times, .95),
                       "latency_scope": "local deterministic retrieval only, not MBP model time"})
    selected_knn = []
    for family in sorted({r["family"] for r in output}):
        dev = [r for r in output if r["family"] == family and r["split"] == "dev" and r["policy"].startswith("knn_only")]
        best = min(dev, key=lambda r: (-r["accuracy"], int(r["policy"].rsplit("k", 1)[1])))
        selected_knn.append({"family": family, "dev_selected_policy": best["policy"],
                             "selection": "maximum dev accuracy over k=1,3,5; tie chooses smaller k",
                             "heldout": next(r for r in output if r["family"] == family and r["split"] == "test" and r["policy"] == best["policy"])})
    return output, predictions, selected_knn


def gate(rows, threshold):
    answered = [r for r in rows if r["margin"] is not None and r["margin"] >= threshold]
    escalated = len(rows) - len(answered)
    return {"threshold": threshold, "answered_n": len(answered),
            "answered_accuracy": statistics.fmean(r["correct"] for r in answered) if answered else None,
            "answered_ci95": interval(answered, "correct") if answered else None,
            "coverage": len(answered) / len(rows), "escalation_rate": escalated / len(rows),
            "counterfactual_teacher_calls": escalated,
            "oracle_escalated_teacher_agreement": (sum(r["correct"] for r in answered) + escalated) / len(rows)}


def cascade(requests, response_paths):
    lookup = {r["id"]: r for r in requests}
    observed = defaultdict(dict)
    for path in response_paths:
        for response in load(path):
            if response["id"] not in lookup:
                raise ValueError("response outside frozen replay")
            model = response["model"]
            if response["id"] in observed[model]:
                raise ValueError("duplicate model/request response")
            observed[model][response["id"]] = response
    grouped = defaultdict(list)
    for model, responses in observed.items():
        for request in requests:
            response = responses.get(request["id"], {})
            probabilities = response.get("probabilities")
            margin = None
            if probabilities:
                values = sorted(probabilities.values(), reverse=True)
                margin = values[0] - values[1]
            grouped[model, request["family"], request["variant"], request["split"]].append({
                "row_id": request["row_id"], "group": request["group"], "margin": margin,
                "correct": int(response.get("choice") == request["teacher_label"])})
    curves = []
    for model, family, variant in sorted({key[:3] for key in grouped}):
        dev, test = grouped[model, family, variant, "dev"], grouped[model, family, variant, "test"]
        dev_margins = [r["margin"] for r in dev if r["margin"] is not None]
        # Every threshold comes from dev margins, including the maximum. A 1.01
        # threshold is the explicit all-escalate option for a failed quality gate.
        thresholds = sorted({0.0, 1.01, *(percentile(dev_margins, q) for q in (0, .1, .25, .5, .75, .9, .95, 1))} - {None})
        dev_curve = [gate(dev, threshold) for threshold in thresholds]
        eligible = [point for point in dev_curve if point["answered_n"] >= 10 and point["answered_accuracy"] >= .95]
        selected = max(eligible, key=lambda point: (point["coverage"], -point["threshold"]))["threshold"] if eligible else 1.01
        curves.append({"model": model, "family": family, "variant": variant,
                       "selection": "max dev coverage with >=95% observed answered accuracy and >=10 answers; otherwise all escalate",
                       "selected_threshold": selected, "dev_curve": dev_curve,
                       "test_curve": [gate(test, threshold) for threshold in thresholds],
                       "selected_test": gate(test, selected)})
    return curves


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--requests", type=Path, required=True)
    parser.add_argument("--responses", type=Path, nargs="+", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    manifest = json.loads(args.manifest.read_text())
    if hashlib.sha256(args.dataset.read_bytes()).hexdigest() != manifest["dataset_file_sha256"]:
        raise ValueError("dataset checksum differs from frozen manifest")
    baselines, predictions, selected_knn = model_free(load(args.dataset), manifest)
    curves = cascade(load(args.requests), args.responses)
    create(args.output / "policy-metrics.json", {"baselines": baselines, "selected_knn": selected_knn, "cascades": curves,
           "option_scoring": "All frozen SLM arms already choose argmax over measured one-token option log probabilities. No free-text output parser is involved.",
           "cascade_limit": "Counterfactual escalations assume recorded teacher answers. No fresh Jev calls or end-to-end cascade latency measured.",
           "threshold_fit_partition": "dev only", "test_reused_for_threshold_selection": False})
    create(args.output / "model-free-predictions.jsonl", predictions, lines=True)
    print(json.dumps({"baselines": len(baselines), "cascade_curves": len(curves)}))


if __name__ == "__main__":
    main()
