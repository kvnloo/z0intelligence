#!/usr/bin/env python3
"""Prepare immutable context ablations and score actual model responses.

Inference is deliberately a separate JSONL transport, allowing admitted remote
execution without granting the injection layer provider or routing authority.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
import hashlib
import json
import math
from pathlib import Path
import random
import statistics
from typing import Any

from z0int.slm_context import VARIANTS, canonical, digest, inject, split_rows


def load(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def create(path: Path, value: Any, *, lines: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x") as output:
        if lines:
            for row in value:
                output.write(canonical(row) + "\n")
        else:
            output.write(json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False) + "\n")

def shard(args: argparse.Namespace) -> None:
    """Project labels out before remote shipment and preserve frozen row order."""
    if args.chunk_size < 1:
        raise ValueError("chunk size must be positive")
    requests = load(args.requests)
    if len({row["id"] for row in requests}) != len(requests):
        raise ValueError("duplicate request IDs")
    completed: set[str] = set()
    for path in args.completed:
        for response in load(path):
            if response.get("ok") is True:
                if response["id"] in completed:
                    raise ValueError("duplicate completed request ID")
                completed.add(response["id"])
    expected = {row["id"] for row in requests}
    if completed - expected:
        raise ValueError("completed response outside frozen replay")
    pending = [row for row in requests if row["id"] not in completed]
    parts = []
    for offset in range(0, len(pending), args.chunk_size):
        rows = [{key: row[key] for key in ("id", "state", "question", "options")}
                for row in pending[offset:offset + args.chunk_size]]
        name = f"requests-shard-{len(parts):03d}.jsonl"
        path = args.output / name
        create(path, rows, lines=True)
        parts.append({"path": name, "rows": len(rows),
                      "file_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                      "first_id": rows[0]["id"], "last_id": rows[-1]["id"]})
    create(args.output / "shards.json", {
        "requests_file_sha256": hashlib.sha256(args.requests.read_bytes()).hexdigest(),
        "completed_rows": len(completed), "pending_rows": len(pending),
        "chunk_size": args.chunk_size, "parts": parts})
    print(canonical({"parts": len(parts), "completed_rows": len(completed), "pending_rows": len(pending)}))



def prepare(args: argparse.Namespace) -> None:
    rows = load(args.dataset)
    splits, manifest = split_rows(rows, args.seed)
    packet = json.loads(args.memory.read_text()) if args.memory else None
    if packet and "context_packet" in packet:
        packet = packet["context_packet"]
    sources = set(args.allow_memory_source)
    requests = []
    for split in ("dev", "test"):
        for row in splits[split]:
            for variant in VARIANTS:
                rendered = inject(row, variant, splits["pool"], memory_packet=packet,
                                  allowed_memory_sources=sources)
                requests.append({**rendered, "id": digest([row["id"], variant.name])[:24],
                                 "row_id": row["id"], "family": row["family"],
                                 "group": manifest["group_clusters"][row["group"]], "split": split,
                                 "teacher_label": row["teacher_label"],
                                 "human_label": row.get("human_label")})
    # Randomize balanced requests to reduce time/cache-order confounding. Frozen
    # before execution; transports must not sort by teacher labels or outcomes.
    random.Random(args.seed).shuffle(requests)
    manifest.update({"request_sha256": digest(requests),
                     "dataset_file_sha256": hashlib.sha256(args.dataset.read_bytes()).hexdigest(),
                     "memory_packet_sha256": digest(packet), "allowed_memory_sources": sorted(sources),
                     "variants": [{"name": v.name, "components": list(v.components), "budget": v.budget}
                                  for v in VARIANTS],
                     "selection_rule": "maximum dev agreement, then lower dev latency; test never used",
                     "agreement_gate": {"accuracy_min": 0.95, "ci95_lower_min": 0.90},
                     "budget_note": "UTF8 bytes upper-bound Qwen byte-level token count; actual prompt tokens measured by runtime"})
    create(args.output / "manifest.json", manifest)
    create(args.output / "requests.jsonl", requests, lines=True)
    print(canonical({"requests": len(requests), "splits": {k: len(v) for k, v in splits.items()},
                     "manifest_sha256": digest(manifest)}))


def percentile(values: list[float], probability: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = probability * (len(ordered) - 1)
    lo = int(index)
    return ordered[lo] + (ordered[min(lo + 1, len(ordered) - 1)] - ordered[lo]) * (index - lo)


def interval(rows: list[dict[str, Any]], key: str, *, paired: dict[str, float] | None = None,
             samples: int = 2000) -> list[float] | None:
    """Task-group cluster bootstrap, preserving dependence of replay nodes."""
    groups: dict[str, list[float]] = defaultdict(list)
    for row in rows:
        value = row[key]
        if value is None:
            continue
        if paired is not None:
            value -= paired[row["row_id"]]
        groups[row["group"]].append(value)
    if len(groups) < 2:
        return None
    units = list(groups.values())
    rng = random.Random(1729)
    means = []
    for _ in range(samples):
        sample = [value for _ in units for value in rng.choice(units)]
        means.append(statistics.fmean(sample))
    return [percentile(means, .025), percentile(means, .975)]


def calibration(rows: list[dict[str, Any]]) -> dict[str, Any]:
    available = [r for r in rows if r.get("confidence") is not None]
    bins = []
    for index in range(10):
        bucket = [r for r in available if min(9, int(r["confidence"] * 10)) == index]
        if bucket:
            bins.append({"low": index / 10, "high": (index + 1) / 10, "n": len(bucket),
                         "confidence": statistics.fmean(r["confidence"] for r in bucket),
                         "accuracy": statistics.fmean(r["correct"] for r in bucket)})
    ece = sum(b["n"] * abs(b["confidence"] - b["accuracy"]) for b in bins) / len(available) if available else None
    brier = [r["brier"] for r in rows if r.get("brier") is not None]
    return {"confidence_rows": len(available), "ece": ece,
            "multiclass_brier": statistics.fmean(brier) if brier else None, "bins": bins}


def score(args: argparse.Namespace) -> None:
    requests = load(args.requests)
    lookup = {r["id"]: r for r in requests}
    if len(lookup) != len(requests):
        raise ValueError("duplicate request IDs")
    results: dict[str, dict[str, dict[str, Any]]] = defaultdict(dict)
    for path in args.responses:
        for response in load(path):
            if response["id"] not in lookup:
                raise ValueError("response is not part of frozen replay")
            model = response["model"]
            if response["id"] in results[model]:
                raise ValueError("duplicate model/request response")
            results[model][response["id"]] = response
    scored = []
    for model, responses in results.items():
        for request in requests:
            response = responses.get(request["id"], {})
            choice = response.get("choice")
            probabilities = response.get("probabilities")
            options = [v["id"] for v in request["options"]]
            if probabilities is not None:
                if set(probabilities) != set(options) or not all(isinstance(p, (int, float)) and math.isfinite(p) and 0 <= p <= 1 for p in probabilities.values()):
                    raise ValueError("invalid probability distribution")
                if abs(sum(probabilities.values()) - 1) > .01:
                    raise ValueError("probabilities do not sum to one")
            confidence = probabilities.get(choice) if probabilities and choice in probabilities else response.get("confidence")
            if confidence is not None and (not isinstance(confidence, (int, float)) or not math.isfinite(confidence) or not 0 <= confidence <= 1):
                raise ValueError("invalid confidence")
            brier = sum((probabilities[v] - int(v == request["teacher_label"])) ** 2 for v in options) if probabilities else None
            scored.append({**{k: request[k] for k in ("id", "row_id", "family", "group", "split", "variant", "context_components", "context_bytes")},
                           "model": model, "choice": choice,
                           "correct": int(choice == request["teacher_label"]),
                           "human_correct": int(choice == request["human_label"]) if request.get("human_label") is not None else None,
                           "missing": not bool(response), "error": response.get("error"),
                           "confidence": confidence, "brier": brier,
                           "latency_ms": response.get("latency_ms"), "prompt_tokens": response.get("prompt_tokens")})
    grouped: dict[tuple[str, str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in scored:
        grouped[row["model"], row["family"], row["split"], row["variant"]].append(row)
    metrics = []
    for (model, family, split, variant), rows in sorted(grouped.items()):
        baseline = {r["row_id"]: r["correct"] for r in grouped[model, family, split, "none"]}
        accuracy = statistics.fmean(r["correct"] for r in rows)
        ci = interval(rows, "correct")
        latency = [r["latency_ms"] for r in rows if r["latency_ms"] is not None]
        tokens = [r["prompt_tokens"] for r in rows if r["prompt_tokens"] is not None]
        human = [r["human_correct"] for r in rows if r["human_correct"] is not None]
        metrics.append({"model": model, "family": family, "split": split, "variant": variant,
                        "n": len(rows), "groups": len({r["group"] for r in rows}),
                        "missing": sum(r["missing"] for r in rows), "errors": sum(bool(r["error"]) for r in rows),
                        "accuracy": accuracy, "ci95": ci,
                        "delta": statistics.fmean(r["correct"] - baseline[r["row_id"]] for r in rows),
                        "delta_ci95": interval(rows, "correct", paired=baseline),
                        "human_n": len(human), "human_accuracy": statistics.fmean(human) if human else None,
                        "human_ci95": interval(rows, "human_correct"),
                        "latency_p50_ms": percentile(latency, .5), "latency_p95_ms": percentile(latency, .95),
                        "prompt_tokens_mean": statistics.fmean(tokens) if tokens else None,
                        "context_nonempty": sum(bool(r["context_components"]) for r in rows),
                        "calibration": calibration(rows),
                        "agreement_gate_pass": accuracy >= .95 and ci is not None and ci[0] >= .9})
    selected = []
    for model, family in sorted({(r["model"], r["family"]) for r in metrics}):
        dev = [r for r in metrics if r["model"] == model and r["family"] == family and r["split"] == "dev"]
        winner = min(dev, key=lambda r: (-r["accuracy"], r["latency_p50_ms"] if r["latency_p50_ms"] is not None else float("inf"), r["variant"]))
        test = next(r for r in metrics if r["model"] == model and r["family"] == family and r["split"] == "test" and r["variant"] == winner["variant"])
        selected.append({"model": model, "family": family, "dev_selected_variant": winner["variant"], "heldout": test})
    create(args.output / "scored.jsonl", scored, lines=True)
    create(args.output / "metrics.json", {"metrics": metrics, "selected": selected,
           "requests_sha256": hashlib.sha256(args.requests.read_bytes()).hexdigest(),
           "responses_sha256": {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in args.responses},
           "ci_method": "2000 paired task-group bootstrap samples, seed1729; null for <2 independent groups",
           "calibration_note": "option-conditional token probabilities, not semantic correctness probabilities"})
    print(canonical({"models": list(results), "scored": len(scored), "metrics": len(metrics)}))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    prep = commands.add_parser("prepare")
    prep.add_argument("--dataset", type=Path, required=True)
    prep.add_argument("--output", type=Path, required=True)
    prep.add_argument("--seed", type=int, default=1729)
    prep.add_argument("--memory", type=Path)
    prep.add_argument("--allow-memory-source", action="append", default=[])
    prep.set_defaults(run=prepare)
    sharding = commands.add_parser("shard")
    sharding.add_argument("--requests", type=Path, required=True)
    sharding.add_argument("--chunk-size", type=int, default=100)
    sharding.add_argument("--completed", type=Path, nargs="*", default=[])
    sharding.add_argument("--output", type=Path, required=True)
    sharding.set_defaults(run=shard)
    scoring = commands.add_parser("score")
    scoring.add_argument("--requests", type=Path, required=True)
    scoring.add_argument("--responses", type=Path, nargs="+", required=True)
    scoring.add_argument("--output", type=Path, required=True)
    scoring.set_defaults(run=score)
    args = parser.parse_args()
    args.run(args)


if __name__ == "__main__":
    main()
