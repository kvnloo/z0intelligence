"""Stdlib projection of z0.latency_call.v1 derived observations, not raw logs.

No identifiers/content are serialized. Explicit labels only; malformed numeric
measurements and copied identities fail closed. Output sums cover known values,
not complete sessions. Response shape is not an upstream-provider verdict.
"""
from __future__ import annotations

import math
import re
from collections import defaultdict
from typing import Any, Iterable, Mapping

SCHEMA = "z0.latency_call.v1"
TOKEN_FIELDS = ("input_uncached_tokens", "cache_read_tokens", "cache_write_tokens", "output_tokens", "reasoning_tokens")
TIME_FIELDS = ("latency_ms", "ttft_ms", "after_first_token_ms")
RUN_PATTERN = re.compile(r"(?:^|[-_/])(r11|r12|r13b|r14|r15|r16|r17)(?:$|[-_/])")


def response_shape(value: Any) -> str:
    if not isinstance(value, str):
        return "unknown"
    if re.fullmatch(r"[0-9a-fA-F]{32}", value):
        return "32hex"
    if re.fullmatch(r"[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}", value):
        return "UUID"
    return "unknown"


def run_label(value: Any) -> str:
    if not isinstance(value, str):
        return "unknown"
    match = RUN_PATTERN.search(value)
    return match.group(1) if match else "unknown"


def numeric(value: Any, *, token: bool = False) -> int | float | None:
    if value is None:
        return None
    if type(value) not in (int, float):
        raise ValueError("invalid metric")
    try:
        valid = math.isfinite(value) and value >= 0
    except OverflowError:
        valid = False
    if not valid or (token and (type(value) is not int)):
        raise ValueError("invalid metric")
    return value


def distribution(values: list[int | float | None]) -> dict[str, Any]:
    known = sorted(v for v in values if v is not None)
    def percentile(q: float) -> int | float | None:
        return known[max(0, math.ceil(q * len(known)) - 1)] if known else None
    return {"known": len(known), "missing": len(values) - len(known), "sum_observed": sum(known) if known else None, "min": known[0] if known else None, "p10": percentile(.10), "p50": percentile(.50), "p90": percentile(.90), "p95": percentile(.95), "max": known[-1] if known else None}


def project(rows: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    groups: dict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)
    identities: set[str] = set()
    run_shapes: dict[str, set[str]] = defaultdict(set)
    count = 0
    for row in rows:
        if row.get("schema") != SCHEMA:
            raise ValueError("unsupported source schema")
        identity = row.get("call_id")
        if not isinstance(identity, str) or not identity:
            raise ValueError("missing call identity")
        if identity in identities:
            raise ValueError("duplicate call identity")
        identities.add(identity)
        count += 1
        metrics = {k: numeric(row.get(k), token=k in TOKEN_FIELDS) for k in TIME_FIELDS + TOKEN_FIELDS}
        duration, ttft, generation = (metrics[k] for k in TIME_FIELDS)
        if duration is not None and ttft is not None:
            if ttft > duration or (generation is not None and not math.isclose(duration - ttft, generation, abs_tol=.001)):
                raise ValueError("invalid timing relation")
        if duration is not None and generation is not None and generation > duration:
            raise ValueError("invalid timing relation")
        output, reasoning = metrics["output_tokens"], metrics["reasoning_tokens"]
        subset = row.get("reasoning_is_subset_of_output")
        if subset is not None and type(subset) is not bool:
            raise ValueError("invalid reasoning subset label")
        metrics["nonreasoning_output_tokens"] = None
        if subset is True and output is not None and reasoning is not None:
            if reasoning > output:
                raise ValueError("invalid reasoning subset")
            metrics["nonreasoning_output_tokens"] = output - reasoning
        metrics["output_tokens_per_second_observed"] = output * 1000 / generation if output is not None and generation is not None and generation > 0 else None
        harness = row.get("harness")
        harness = harness if isinstance(harness, str) and harness in {"omp", "codex", "hermes"} else "unknown"
        run = run_label(row.get("run"))
        shape = response_shape(row.get("native_response_id"))
        groups[(harness, run, shape)].append(metrics)
        if harness == "omp" and run != "unknown":
            run_shapes[run].add(shape)
    output_groups = []
    for (harness, run, shape), metrics in sorted(groups.items()):
        fields = TIME_FIELDS + TOKEN_FIELDS + ("nonreasoning_output_tokens", "output_tokens_per_second_observed")
        pairs = [(m["cache_read_tokens"], m["input_uncached_tokens"]) for m in metrics if m["cache_read_tokens"] is not None and m["input_uncached_tokens"] is not None]
        cache_read = sum(p[0] for p in pairs)
        cache_input = sum(p[0] + p[1] for p in pairs)
        output_groups.append({"harness": harness, "run": run, "response_shape": shape, "observations": len(metrics), "metrics": {k: distribution([m[k] for m in metrics]) for k in fields}, "cache_paired_calls": len(pairs), "cache_read_share_observed": cache_read / cache_input if cache_input else None})
    return {"schema": "zi139.aggregate.v1", "source_schema": SCHEMA, "observations": count, "percentile_method": "nearest-rank", "groups": output_groups, "mixed_stratum_runs": sorted(run for run, shapes in run_shapes.items() if len(shapes) > 1)}
