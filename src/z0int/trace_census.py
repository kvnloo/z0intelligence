#!/usr/bin/env python3
"""Trace-cost census over real DSH session traces.

Answers the only question that should set sprint priority: *which model calls are
actually expensive, and how often do they recur?*

Reads the harness's own session logs (`~/.dsh/sessions/*/*/session.v3.jsonl.zstd`).
Every `assistant/message` record carries `data.usage` (input/output/cache/reasoning
tokens), `data.message.source` (the provider and model that actually served the
call) and `data.message.content[]` (the tool calls the model asked for). Nothing
here is estimated or sampled: these are the harness's own accounting records.

Outputs:
  * per-model call counts, token totals, latency
  * per-tool-call-family frequency and the tokens spent on the step that made it
  * a ranked list of the most expensive (model, step-shape) responsibilities

Stdlib + zstandard only.
"""

from __future__ import annotations

import argparse
import collections
import glob
import json
import os
import statistics as stats
import sys
from dataclasses import dataclass, field
from pathlib import Path

try:
    import zstandard
except ImportError:  # pragma: no cover
    zstandard = None


DEFAULT_GLOB = "~/.dsh/sessions/*/*/session.v3.jsonl.zstd"


@dataclass
class Call:
    session: str
    turn: int
    step: int
    provider: str
    model: str
    input_tokens: int
    output_tokens: int
    cache_read: int
    reasoning_tokens: int
    latency_ms: float | None
    tool_names: tuple[str, ...]
    text_chars: int
    tool_arg_bytes: int

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens


@dataclass
class Census:
    calls: list[Call] = field(default_factory=list)
    sessions: int = 0
    parse_errors: int = 0


def _read_session(path: str) -> list[dict]:
    if zstandard is None:
        raise RuntimeError("zstandard not installed")
    d = zstandard.ZstdDecompressor()
    with open(path, "rb") as f:
        raw = d.stream_reader(f).read().decode("utf-8", errors="replace")
    out = []
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out


def build(glob_pat: str = DEFAULT_GLOB, limit: int | None = None) -> Census:
    paths = sorted(glob.glob(os.path.expanduser(glob_pat)))
    if limit:
        paths = paths[:limit]
    c = Census()
    for p in paths:
        try:
            recs = _read_session(p)
        except Exception:
            c.parse_errors += 1
            continue
        c.sessions += 1
        # step boundaries give a latency estimate for the step that produced a call
        step_start: dict[tuple[int, int], int] = {}
        step_end: dict[tuple[int, int], int] = {}
        for r in recs:
            t = r.get("type")
            d = r.get("data") or {}
            if t == "step/start":
                step_start[(d.get("turn", -1), d.get("step", -1))] = r.get("time", 0)
            elif t == "step/end":
                step_end[(d.get("turn", -1), d.get("step", -1))] = r.get("time", 0)
        for r in recs:
            if r.get("type") != "assistant/message":
                continue
            d = r.get("data") or {}
            msg = d.get("message") or {}
            src = msg.get("source") or {}
            u = d.get("usage") or {}
            content = msg.get("content") or []
            tools = tuple(
                str(x.get("name"))
                for x in content
                if isinstance(x, dict) and x.get("type") == "tool-call"
            )
            text_chars = sum(
                len(x.get("text") or "")
                for x in content
                if isinstance(x, dict) and x.get("type") == "text"
            )
            arg_bytes = sum(
                len(str(x.get("arguments") or ""))
                for x in content
                if isinstance(x, dict) and x.get("type") == "tool-call"
            )
            key = (d.get("turn", -1), d.get("step", -1))
            a, b = step_start.get(key), step_end.get(key)
            lat = (b - a) if (a and b and b >= a) else None
            c.calls.append(
                Call(
                    session=os.path.basename(os.path.dirname(p)),
                    turn=key[0],
                    step=key[1],
                    provider=str(src.get("provider") or "?"),
                    model=str(src.get("model") or "?"),
                    input_tokens=int(u.get("inputTokens") or 0),
                    output_tokens=int(u.get("outputTokens") or 0),
                    cache_read=int(u.get("cacheReadTokens") or 0),
                    reasoning_tokens=int(u.get("reasoningTokens") or 0),
                    latency_ms=float(lat) if lat is not None else None,
                    tool_names=tools,
                    text_chars=text_chars,
                    tool_arg_bytes=arg_bytes,
                )
            )
    return c


def p50(xs: list[float]) -> float | None:
    return round(stats.median(xs), 1) if xs else None


def report(c: Census, top: int = 25) -> dict:
    calls = c.calls
    by_model: dict[str, list[Call]] = collections.defaultdict(list)
    for x in calls:
        by_model[f"{x.provider}/{x.model}"].append(x)

    models = []
    for k, xs in by_model.items():
        lat = [x.latency_ms for x in xs if x.latency_ms]
        models.append(
            {
                "model": k,
                "calls": len(xs),
                "input_tokens": sum(x.input_tokens for x in xs),
                "output_tokens": sum(x.output_tokens for x in xs),
                "cache_read": sum(x.cache_read for x in xs),
                "reasoning_tokens": sum(x.reasoning_tokens for x in xs),
                "total_tokens": sum(x.total_tokens for x in xs),
                "latency_p50_ms": p50(lat),
                "latency_p95_ms": round(sorted(lat)[int(len(lat) * 0.95)], 1) if len(lat) > 3 else None,
                "tool_calls": sum(len(x.tool_names) for x in xs),
            }
        )
    models.sort(key=lambda m: -m["total_tokens"])

    # tool-call family census: tokens attributable to a step that requested a tool
    tool_steps: dict[str, list[Call]] = collections.defaultdict(list)
    for x in calls:
        if not x.tool_names:
            continue
        for name in set(x.tool_names):
            tool_steps[name].append(x)
    tools = []
    for name, xs in tool_steps.items():
        tools.append(
            {
                "tool": name,
                "steps": len(xs),
                "input_tokens": sum(x.input_tokens for x in xs),
                "output_tokens": sum(x.output_tokens for x in xs),
                "total_tokens": sum(x.total_tokens for x in xs),
                "latency_p50_ms": p50([x.latency_ms for x in xs if x.latency_ms]),
            }
        )
    tools.sort(key=lambda t: -t["total_tokens"])

    # step shape: how many tokens a call costs by what it is doing
    shapes: dict[str, list[Call]] = collections.defaultdict(list)
    for x in calls:
        if not x.tool_names:
            key = "text-only (no tool call)"
        elif len(x.tool_names) == 1:
            key = f"1 tool: {x.tool_names[0]}"
        else:
            key = f"{len(x.tool_names)} tools"
        shapes[key].append(x)
    shape_rows = [
        {
            "shape": k,
            "calls": len(v),
            "input_tokens": sum(x.input_tokens for x in v),
            "output_tokens": sum(x.output_tokens for x in v),
            "total_tokens": sum(x.total_tokens for x in v),
            "input_p50": p50([x.input_tokens for x in v]),
            "latency_p50_ms": p50([x.latency_ms for x in v if x.latency_ms]),
        }
        for k, v in shapes.items()
    ]
    shape_rows.sort(key=lambda r: -r["total_tokens"])

    return {
        "sessions": c.sessions,
        "parse_errors": c.parse_errors,
        "model_calls": len(calls),
        "total_input_tokens": sum(x.input_tokens for x in calls),
        "total_output_tokens": sum(x.output_tokens for x in calls),
        "total_cache_read": sum(x.cache_read for x in calls),
        "total_reasoning_tokens": sum(x.reasoning_tokens for x in calls),
        "tool_calls": sum(len(x.tool_names) for x in calls),
        "models": models[:top],
        "tools": tools[:top],
        "step_shapes": shape_rows[:top],
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="DSH trace-cost census")
    ap.add_argument("--glob", default=DEFAULT_GLOB)
    ap.add_argument("--limit", type=int)
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--top", type=int, default=25)
    a = ap.parse_args(argv)
    c = build(a.glob, a.limit)
    r = report(c, a.top)
    if a.json:
        print(json.dumps(r, indent=1))
        return 0
    print(f"sessions={r['sessions']}  model_calls={r['model_calls']:,}  tool_calls={r['tool_calls']:,}")
    print(f"input={r['total_input_tokens']:,}  output={r['total_output_tokens']:,}  "
          f"cache_read={r['total_cache_read']:,}  reasoning={r['total_reasoning_tokens']:,}")
    print()
    print(f"{'model':44} {'calls':>7} {'input':>12} {'output':>9} {'p50ms':>8}")
    print("-" * 84)
    for m in r["models"]:
        print(f"{m['model']:44} {m['calls']:>7,} {m['input_tokens']:>12,} "
              f"{m['output_tokens']:>9,} {str(m['latency_p50_ms']):>8}")
    print()
    print(f"{'tool family':28} {'steps':>7} {'input':>12} {'output':>9} {'p50ms':>8}")
    print("-" * 68)
    for t in r["tools"]:
        print(f"{t['tool']:28} {t['steps']:>7,} {t['input_tokens']:>12,} "
              f"{t['output_tokens']:>9,} {str(t['latency_p50_ms']):>8}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
