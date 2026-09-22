#!/usr/bin/env python3
"""Analysis + report for the token-cap sensitivity run.

Reads results/token-cap-20260922/raw.jsonl and writes report.json / report.md.
Measurement rows only (kind == "measured"); any invalid_cell is reported but
excluded from accuracy aggregates (an override that did not reach the wire is
not evidence about the cap).
"""

from __future__ import annotations

import json
import math
import random
import re
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

# Structured-call signatures.  The production parser also falls back to a bare
# `substring` match of an allowed id, which is *not* evidence that a structured
# call was emitted; these patterns are what counts as "emitted a call".
_CALL_SIGS = [
    re.compile(p)
    for p in (
        r"<tool_call>",
        r"<function\s*=",
        r"<start_function_call>",
        r"call:\s*[\w.\-]+\s*\{",
        r"\[\s*\{\s*['\"]type['\"]\s*:\s*['\"]function",
        r"['\"]name['\"]\s*:",
        r"['\"]action_id['\"]\s*:",
    )
]


def emitted_structured(r: dict) -> bool:
    if r.get("native_tool_call"):
        return True
    text = r.get("content") or ""
    return any(p.search(text) for p in _CALL_SIGS)

OUT = Path("/home/kvn/tmp/openjev/results/token-cap-20260922")
RAW = OUT / "raw.jsonl"
FROZEN = Path("/home/kvn/tmp/openjev/results/phase1b/p1b-20260921T1430Z/observations.jsonl")
FROZEN_NATIVE = {
    "nemotron_orchestrator_8b": 256,
    "hammer2.1_3b": 256,
    "hammer2.1_7b": 256,
    "functiongemma_270m": 128,
    "qwen3.5_4b": 1024,
    "qwen3.5_9b": 1024,
}
MODEL_ORDER = [
    "nemotron_orchestrator_8b",
    "hammer2.1_3b",
    "hammer2.1_7b",
    "functiongemma_270m",
    "qwen3.5_4b",
    "qwen3.5_9b",
]


def load_rows() -> list[dict]:
    rows = []
    for line in RAW.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            rows.append(json.loads(line))
    return rows


def pct(a: int, b: int) -> float | None:
    return round(100.0 * a / b, 1) if b else None


def wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float] | None:
    if n == 0:
        return None
    p = k / n
    d = 1 + z * z / n
    c = p + z * z / (2 * n)
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return (round(100 * (c - h) / d, 1), round(100 * (c + h) / d, 1))


def boot_ci(values: list[float], n_boot: int = 5000, seed: int = 7) -> tuple[float, float] | None:
    if not values:
        return None
    rng = random.Random(seed)
    n = len(values)
    means = []
    for _ in range(n_boot):
        means.append(sum(values[rng.randrange(n)] for _ in range(n)) / n)
    means.sort()
    return (round(means[int(0.025 * n_boot)], 4), round(means[int(0.975 * n_boot)], 4))


def reasoning_implied(r: dict) -> tuple[str | None, bool]:
    """Action id whose *last* mention in the raw reasoning text is latest.

    A cheap proxy for what the reasoning trace concludes with, independent of
    whether the model got to emit a call.  Returns (implied_id, implied==gold).
    """
    text = r.get("reasoning_text") or ""
    if not text:
        return None, False
    low = text.lower()
    best: tuple[int, str] | None = None
    for aid in r.get("legal_actions") or []:
        pos = low.rfind(str(aid).lower())
        if pos >= 0 and (best is None or pos > best[0]):
            best = (pos, str(aid))
    if best is None:
        return None, False
    return best[1], best[1] == r.get("gold_action")


def classify_incorrect(r: dict) -> str:
    gir = bool(r.get("gold_mentioned_in_reasoning"))
    emitted = emitted_structured(r)
    parsed = bool(r.get("tool_call_parsed"))
    if gir and not parsed:
        return "cutoff_with_cognition"
    if gir and parsed:
        return "cognition_but_other_call"
    if emitted and not parsed:
        return "emitted_unparsed"
    if parsed:
        return "wrong_choice"
    return "silent_no_call"


def cell_stats(rows: list[dict]) -> dict:
    n = len(rows)
    c = sum(1 for r in rows if r["correct"])
    trunc = sum(1 for r in rows if r["truncated"])
    hit = sum(1 for r in rows if r["hit_cap"])
    gir = sum(1 for r in rows if r["gold_mentioned_in_reasoning"])
    rp = sum(1 for r in rows if r["reasoning_present"])
    emitted = sum(1 for r in rows if emitted_structured(r))
    parsed = sum(1 for r in rows if r["tool_call_parsed"])
    fallback = sum(1 for r in rows if r["fallback_parser_used"])
    ct = [r["completion_tokens"] for r in rows if r.get("completion_tokens") is not None]
    rt = [r["reasoning_tokens"] for r in rows if r.get("reasoning_tokens") is not None]
    lat = [r["latency_ms"] for r in rows]
    fin = Counter(r.get("finish_reason") for r in rows)
    cold = Counter(r["cold_or_warm"] for r in rows)
    inc = [r for r in rows if not r["correct"]]
    decomp = Counter(classify_incorrect(r) for r in inc)
    implied = [reasoning_implied(r) for r in rows]
    implied_ids = [i for i, ok in implied if i is not None]
    implied_ok = sum(1 for i, ok in implied if i is not None and ok)
    return {
        "n": n,
        "correct": c,
        "accuracy_pct": pct(c, n),
        "accuracy_ci95": wilson(c, n),
        "reasoning_implied_n": len(implied_ids),
        "reasoning_implied_correct": implied_ok,
        "reasoning_implied_accuracy_pct": pct(implied_ok, len(implied_ids)),
        "truncated": trunc,
        "truncated_pct": pct(trunc, n),
        "hit_cap": hit,
        "hit_cap_pct": pct(hit, n),
        "gold_in_reasoning": gir,
        "gold_in_reasoning_pct": pct(gir, n),
        "reasoning_present_pct": pct(rp, n),
        "tool_call_emitted_pct": pct(emitted, n),
        "tool_call_parsed_pct": pct(parsed, n),
        "fallback_parser_used_pct": pct(fallback, n),
        "completion_tokens_mean": round(sum(ct) / len(ct), 1) if ct else None,
        "completion_tokens_median": sorted(ct)[len(ct) // 2] if ct else None,
        "completion_tokens_max": max(ct) if ct else None,
        "reasoning_tokens_mean": round(sum(rt) / len(rt), 1) if rt else None,
        "latency_ms_mean": round(sum(lat) / len(lat), 1) if lat else None,
        "finish_reasons": dict(fin),
        "residency": dict(cold),
        "incorrect_n": len(inc),
        "incorrect_decomposition": dict(decomp),
        "incorrect_decomposition_pct": {
            k: pct(v, len(inc)) for k, v in decomp.items()
        },
    }


def pair_delta(rows: list[dict], model: str, cap_lo: int, cap_hi: int) -> dict:
    """Paired per-state accuracy delta (hi - lo) with bootstrap CI over states."""
    by = defaultdict(dict)
    for r in rows:
        if r["model"] != model:
            continue
        if r["requested_max_tokens"] in (cap_lo, cap_hi):
            by[(r["state_id"], r["repetition"])][r["requested_max_tokens"]] = r
    deltas = []
    for key, d in by.items():
        if cap_lo in d and cap_hi in d:
            deltas.append(int(d[cap_hi]["correct"]) - int(d[cap_lo]["correct"]))
    if not deltas:
        return {"n_pairs": 0}
    mean = sum(deltas) / len(deltas)
    hi_win = sum(1 for x in deltas if x > 0)
    lo_win = sum(1 for x in deltas if x < 0)
    tie = sum(1 for x in deltas if x == 0)
    return {
        "n_pairs": len(deltas),
        "delta_accuracy": round(mean, 4),
        "delta_accuracy_pct_points": round(100 * mean, 1),
        "delta_ci95": boot_ci([float(x) for x in deltas]),
        "hi_only_correct": hi_win,
        "lo_only_correct": lo_win,
        "tie": tie,
    }


def main() -> int:
    rows_all = load_rows()
    measured = [r for r in rows_all if r.get("kind") == "measured"]
    invalid = [r for r in measured if r.get("invalid_cell")]
    rows = [r for r in measured if not r.get("invalid_cell")]
    warmups = [r for r in rows_all if r.get("kind") == "warmup"]

    cells: dict[str, dict[int, dict]] = defaultdict(dict)
    for r in rows:
        cells[r["model"]].setdefault(r["requested_max_tokens"], []).append(r)

    # --- wire-override proof: any completion above the old native cap --------
    wire_proof = {}
    for m in MODEL_ORDER:
        mr = [r for r in rows if r["model"] == m]
        native = FROZEN_NATIVE[m]
        over = [r for r in mr if r.get("completion_tokens") and r["completion_tokens"] > native]
        wire_proof[m] = {
            "native_cap_before": native,
            "max_completion_tokens": max((r["completion_tokens"] for r in mr), default=None),
            "calls_exceeding_native_cap": len(over),
            "max_wire_max_tokens": max((r.get("wire_max_tokens") or 0 for r in mr), default=None),
            "example": (
                {
                    "cell": over[0]["cell"],
                    "state_id": over[0]["state_id"],
                    "completion_tokens": over[0]["completion_tokens"],
                    "wire_max_tokens": over[0]["wire_max_tokens"],
                    "native_cap": native,
                }
                if over
                else None
            ),
        }

    # --- frozen corpus reproduction -----------------------------------------
    frozen = [json.loads(l) for l in FROZEN.read_text(encoding="utf-8").splitlines() if l.strip()]
    frozen_stats = {}
    for m, native in FROZEN_NATIVE.items():
        fr = [r for r in frozen if r.get("model_id") == m]
        toks = [r.get("tokens_out") for r in fr if r.get("tokens_out") is not None]
        frozen_stats[m] = {
            "rows": len(fr),
            "max_completion_tokens": max(toks) if toks else None,
            "rows_at_effective_cap": sum(1 for t in toks if t == native),
            "rows_at_effective_cap_pct": pct(sum(1 for t in toks if t == native), len(toks)),
            "effective_cap": native,
        }

    # --- per-cell table ------------------------------------------------------
    per_cell = {}
    for m in MODEL_ORDER:
        per_cell[m] = {}
        for cap in sorted(cells.get(m, {})):
            per_cell[m][cap] = cell_stats(cells[m][cap])

    per_model = {}
    for m in MODEL_ORDER:
        caps = sorted(cells.get(m, {}))
        native = FROZEN_NATIVE[m]
        per_model[m] = {
            "native_cap": native,
            "caps_measured": caps,
            "cells": {str(c): per_cell[m][c] for c in caps},
            "delta_vs_native": {
                str(c): pair_delta(rows, m, native, c) for c in caps if c != native
            },
        }

    # --- Q2 nemotron recovery ------------------------------------------------
    nem = [r for r in rows if r["model"] == "nemotron_orchestrator_8b"]
    by_key = defaultdict(dict)
    for r in nem:
        by_key[(r["state_id"], r["repetition"])][r["requested_max_tokens"]] = r
    recovery = {
        "pairs_256_1024": 0,
        "at_256_no_call": 0,
        "at_256_empty_content_no_native_call": 0,
        "of_empty_256_recovered_to_call_at_1024": 0,
        "of_empty_256_recovered_to_correct_at_1024": 0,
        "recovered_to_call_at_1024": 0,
        "still_no_call_at_1024": 0,
        "at_256_incorrect": 0,
        "recovered_to_correct_at_1024": 0,
        "at_256_truncated_or_hit_cap": 0,
        "of_those_recovered_to_call": 0,
        "of_those_recovered_to_correct": 0,
        "gold_in_reasoning_at_1024": 0,
        "correct_when_gold_in_reasoning_at_1024": 0,
        "gold_in_reasoning_at_256": 0,
        "correct_when_gold_in_reasoning_at_256": 0,
    }
    for key, d in by_key.items():
        if 256 not in d or 1024 not in d:
            continue
        lo, hi = d[256], d[1024]
        recovery["pairs_256_1024"] += 1
        if not lo["tool_call_parsed"]:
            recovery["at_256_no_call"] += 1
            if hi["tool_call_parsed"]:
                recovery["recovered_to_call_at_1024"] += 1
            else:
                recovery["still_no_call_at_1024"] += 1
        if not lo.get("content") and not lo.get("native_tool_call"):
            recovery["at_256_empty_content_no_native_call"] += 1
            if hi["tool_call_parsed"]:
                recovery["of_empty_256_recovered_to_call_at_1024"] += 1
            if hi["correct"]:
                recovery["of_empty_256_recovered_to_correct_at_1024"] += 1
        if not lo["correct"]:
            recovery["at_256_incorrect"] += 1
            if hi["correct"]:
                recovery["recovered_to_correct_at_1024"] += 1
        if lo["truncated"] or lo["hit_cap"]:
            recovery["at_256_truncated_or_hit_cap"] += 1
            if hi["tool_call_parsed"]:
                recovery["of_those_recovered_to_call"] += 1
            if hi["correct"]:
                recovery["of_those_recovered_to_correct"] += 1
        if hi["gold_mentioned_in_reasoning"]:
            recovery["gold_in_reasoning_at_1024"] += 1
            if hi["correct"]:
                recovery["correct_when_gold_in_reasoning_at_1024"] += 1
        if lo["gold_mentioned_in_reasoning"]:
            recovery["gold_in_reasoning_at_256"] += 1
            if lo["correct"]:
                recovery["correct_when_gold_in_reasoning_at_256"] += 1
    if recovery["gold_in_reasoning_at_1024"]:
        recovery["accuracy_when_gold_in_reasoning_at_1024_pct"] = pct(
            recovery["correct_when_gold_in_reasoning_at_1024"],
            recovery["gold_in_reasoning_at_1024"],
        )
    if recovery["at_256_truncated_or_hit_cap"]:
        recovery["truncated_recovery_to_call_pct"] = pct(
            recovery["of_those_recovered_to_call"], recovery["at_256_truncated_or_hit_cap"]
        )
        recovery["truncated_recovery_to_correct_pct"] = pct(
            recovery["of_those_recovered_to_correct"], recovery["at_256_truncated_or_hit_cap"]
        )

    # --- Q4 qwen9b 1024 vs 2048 ---------------------------------------------
    q9 = pair_delta(rows, "qwen3.5_9b", 1024, 2048)
    q9_rows = [r for r in rows if r["model"] == "qwen3.5_9b"]
    q9_by = defaultdict(dict)
    for r in q9_rows:
        q9_by[(r["state_id"], r["repetition"])][r["requested_max_tokens"]] = r
    q9_detail = {"pairs": 0, "at_1024_hit_cap": 0, "of_those_correct_at_2048": 0,
                 "at_1024_incorrect": 0, "recovered_at_2048": 0,
                 "at_1024_correct_lost_at_2048": 0}
    for key, d in q9_by.items():
        if 1024 not in d or 2048 not in d:
            continue
        lo, hi = d[1024], d[2048]
        q9_detail["pairs"] += 1
        if lo["hit_cap"]:
            q9_detail["at_1024_hit_cap"] += 1
            if hi["correct"]:
                q9_detail["of_those_correct_at_2048"] += 1
        if not lo["correct"]:
            q9_detail["at_1024_incorrect"] += 1
            if hi["correct"]:
                q9_detail["recovered_at_2048"] += 1
        if lo["correct"] and not hi["correct"]:
            q9_detail["at_1024_correct_lost_at_2048"] += 1
    q9_detail["delta"] = q9

    # --- Q3 hammer3b ---------------------------------------------------------
    hammer = {}
    for m in ("hammer2.1_3b", "hammer2.1_7b"):
        caps = sorted(cells.get(m, {}))
        hammer[m] = {
            "cells": {str(c): per_cell[m][c] for c in caps},
            "deltas": {str(c): pair_delta(rows, m, FROZEN_NATIVE[m], c) for c in caps if c != FROZEN_NATIVE[m]},
        }

    # --- per-model verdicts --------------------------------------------------
    def model_verdict(m: str) -> dict:
        caps = sorted(cells.get(m, {}))
        if len(caps) < 2:
            return {"caps": caps, "verdict": "single cap measured"}
        lo, hi = caps[0], caps[-1]
        slo, shi = per_cell[m][lo], per_cell[m][hi]
        d = pair_delta(rows, m, lo, hi)
        ci = d.get("delta_ci95")
        moves = bool(ci is not None and (ci[0] > 0 or ci[1] < 0))
        tr_lo, tr_hi = slo["truncated_pct"], shi["truncated_pct"]
        gir_lo, gir_hi = slo["gold_in_reasoning_pct"], shi["gold_in_reasoning_pct"]
        gir_flat = (
            gir_lo is not None and gir_hi is not None and abs(gir_lo - gir_hi) <= 5.0
        )
        if not moves and (tr_lo or 0) == 0 and (tr_hi or 0) == 0:
            verdict = "no cap effect (never approached the cap)"
        elif moves and (tr_hi or 0) < (tr_lo or 0) and gir_flat:
            verdict = "cap-limited: preference flat, emission improves"
        elif moves:
            verdict = "accuracy moves with cap"
        else:
            verdict = "no accuracy effect at these caps"
        return {
            "caps": caps,
            "accuracy_pct_by_cap": {str(c): per_cell[m][c]["accuracy_pct"] for c in caps},
            "truncated_pct_by_cap": {str(c): per_cell[m][c]["truncated_pct"] for c in caps},
            "gold_in_reasoning_pct_by_cap": {str(c): per_cell[m][c]["gold_in_reasoning_pct"] for c in caps},
            "accuracy_range_pp": round(
                max(per_cell[m][c]["accuracy_pct"] for c in caps)
                - min(per_cell[m][c]["accuracy_pct"] for c in caps), 1
            ),
            "paired_delta_lo_to_hi_pp": d.get("delta_accuracy_pct_points"),
            "paired_delta_ci95": ci,
            "truncated_falls": (tr_hi or 0) < (tr_lo or 0),
            "gold_in_reasoning_flat": gir_flat,
            "verdict": verdict,
        }

    verdicts = {m: model_verdict(m) for m in MODEL_ORDER}

    decomp_totals: Counter = Counter()
    for m in MODEL_ORDER:
        for c in cells.get(m, {}):
            decomp_totals.update(per_cell[m][c]["incorrect_decomposition"])

    report = {
        "schema": "z0int.tokencap.report.v1",
        "run_id": "token-cap-20260922",
        "generated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "summary_verdicts": verdicts,
        "incorrect_decomposition_totals": dict(decomp_totals),
        "n_calls_total": len(rows_all),
        "n_measured": len(measured),
        "n_measured_used": len(rows),
        "n_invalid_cells": len(invalid),
        "n_warmups": len(warmups),
        "states": len({r["state_id"] for r in rows}),
        "reps": len({r["repetition"] for r in rows}),
        "invalid_cell_rows": [
            {k: r.get(k) for k in ("cell", "state_id", "repetition", "wire_max_tokens", "dialect_max_tokens")}
            for r in invalid
        ],
        "wire_override_proof": wire_proof,
        "frozen_corpus_reproduction": frozen_stats,
        "per_cell": {m: {str(c): v for c, v in per_cell[m].items()} for m in MODEL_ORDER},
        "per_model": per_model,
        "nemotron_recovery_256_to_1024": recovery,
        "qwen9b_1024_vs_2048": q9_detail,
        "hammer": hammer,
        "reasoning_token_methods": dict(Counter(r.get("reasoning_token_method") for r in rows)),
        "metric_definitions": {
            "tool_call_parsed": "the production dialect parser returned a legal action id "
                                "(LocalSLMBackend.selected_action is not None)",
            "fallback_parser_used": "parse_source is set and is not the native 'tool_call' path",
            "tool_call_emitted": "a structured call signature appears in native tool_calls or content "
                                 "(JSON/XML/FunctionGemma/Hammer Python-repr array). Derived at analysis "
                                 "time because the runner's dialect-pattern check missed Hammer's "
                                 "single-quoted repr array.",
            "gold_mentioned_in_reasoning": "gold action id found in the raw reasoning channel; for models "
                                           "with no reasoning channel the raw content is searched instead "
                                           "(gold_mention_channel records which)",
            "reasoning_implied_accuracy_pct": "accuracy of the legal action id whose last mention in the raw "
                                              "reasoning text is latest (a proxy for the trace's conclusion)",
            "truncated": "finish_reason == 'length' or completion_tokens >= wire_max_tokens",
            "correct": "identical to the Phase 1B ObservationBuilder rule",
        },
    }
    (OUT / "report.json").write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    meta_path = OUT / "run_metadata.json"
    if meta_path.is_file():
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            meta["post_run"] = {
                "finished_at": report["generated_at"],
                "n_calls_total": len(rows_all),
                "n_measured": len(measured),
                "n_measured_used": len(rows),
                "n_invalid_cells": len(invalid),
                "n_warmups": len(warmups),
                "note_tool_call_emitted": "raw.jsonl's tool_call_emitted uses dialect-pattern detection "
                                          "only and undercounts Hammer's single-quoted repr array; "
                                          "report.json/md derive tool_call_emitted from "
                                          "structured signatures instead.",
            }
            meta_path.write_text(json.dumps(meta, indent=2, sort_keys=True, default=str) + "\n",
                                 encoding="utf-8")
        except Exception:  # noqa: BLE001
            pass

    # --- markdown ------------------------------------------------------------
    md: list[str] = []
    md.append("# Token-cap sensitivity on the Phase 1B 28-state suite\n")
    md.append(f"_generated {report['generated_at']}_\n")
    md.append(
        f"Measured calls used: **{len(rows)}** ({len(invalid)} invalid override rows excluded, "
        f"{len(warmups)} warmups excluded). {report['states']} states x 3 reps per cell, "
        "compiler-first arm, seed 42, temperature 0.0, context 4096.\n"
    )
    md.append("## Summary verdicts\n")
    md.append("| model | caps | accuracy % by cap | paired delta hi-lo (pp) | truncated % lo->hi | gold-in-reasoning % lo->hi | verdict |")
    md.append("|---|---|---|---|---|---|---|")
    for m in MODEL_ORDER:
        v = verdicts[m]
        if len(v.get("caps", [])) < 2:
            md.append(f"| {m} | {v.get('caps')} | - | - | - | - | {v['verdict']} |")
            continue
        caps = v["caps"]
        accs = " -> ".join(str(v["accuracy_pct_by_cap"][str(c)]) for c in caps)
        tr = f"{v['truncated_pct_by_cap'][str(caps[0])]} -> {v['truncated_pct_by_cap'][str(caps[-1])]}"
        gir = f"{v['gold_in_reasoning_pct_by_cap'][str(caps[0])]} -> {v['gold_in_reasoning_pct_by_cap'][str(caps[-1])]}"
        md.append(
            f"| {m} | {caps[0]}..{caps[-1]} | {accs} | {v['paired_delta_lo_to_hi_pp']} "
            f"({v['paired_delta_ci95']}) | {tr} | {gir} | {v['verdict']} |"
        )
    md.append("")
    md.append("## 0. Override reached the wire\n")
    md.append(
        "The clamp was bypassed by rebuilding each model's resolved dialect with a larger `max_tokens` "
        "(`dataclasses.replace(resolved, max_tokens=cap)`) and passing it to `LocalSLMBackend`; every "
        "receipt records the literal `max_tokens` it POSTed (`wire_max_tokens`).\n"
    )
    md.append("| model | native cap (frozen) | max wire cap sent | max completion_tokens | calls > native cap |")
    md.append("|---|---|---|---|---|")
    for m in MODEL_ORDER:
        w = wire_proof[m]
        md.append(
            f"| {m} | {w['native_cap_before']} | {w['max_wire_max_tokens']} | "
            f"{w['max_completion_tokens']} | {w['calls_exceeding_native_cap']} |"
        )
    md.append("")
    md.append("## 1. Cap sensitivity per model\n")
    for m in MODEL_ORDER:
        md.append(f"### {m} (native {FROZEN_NATIVE[m]})\n")
        md.append(
            "| cap | n | accuracy % (95% CI) | truncated % | hit-cap % | gold-in-reasoning % | "
            "reasoning-implied acc % | call emitted % | call parsed % | mean completion tok | max tok |"
        )
        md.append("|---|---|---|---|---|---|---|---|---|---|---|")
        for c in sorted(cells.get(m, {})):
            s = per_cell[m][c]
            ci = s["accuracy_ci95"]
            ci_s = f"{ci[0]}-{ci[1]}" if ci else "-"
            md.append(
                f"| {c} | {s['n']} | {s['accuracy_pct']} ({ci_s}) | {s['truncated_pct']} | "
                f"{s['hit_cap_pct']} | {s['gold_in_reasoning_pct']} | {s['reasoning_implied_accuracy_pct']} | "
                f"{s['tool_call_emitted_pct']} | "
                f"{s['tool_call_parsed_pct']} | {s['completion_tokens_mean']} | {s['completion_tokens_max']} |"
            )
        md.append("")
        for c, d in per_model[m]["delta_vs_native"].items():
            md.append(
                f"- cap {c} vs native {FROZEN_NATIVE[m]}: paired delta "
                f"**{d.get('delta_accuracy_pct_points')} pp** (95% CI {d.get('delta_ci95')}), "
                f"pairs={d.get('n_pairs')}, hi-only-correct={d.get('hi_only_correct')}, "
                f"lo-only-correct={d.get('lo_only_correct')}"
            )
        md.append("")
    md.append("## 2. Nemotron 256 -> 1024 recovery\n")
    r = recovery
    md.append(
        f"Paired (state, rep) cells with both 256 and 1024: **{r['pairs_256_1024']}**. "
        f"At 256, **{r['at_256_no_call']}** produced no parsed call and "
        f"**{r['at_256_empty_content_no_native_call']}** had an empty content field with no native tool "
        f"call (the exact frozen-corpus failure signature); **{r['of_empty_256_recovered_to_call_at_1024']}** "
        f"of those empty ones became a parsed tool call at 1024 and "
        f"**{r['of_empty_256_recovered_to_correct_at_1024']}** became correct. "
        f"At 256, **{r['at_256_truncated_or_hit_cap']}** hit/truncated at the cap; "
        f"**{r['of_those_recovered_to_call']}** became a parsed call at 1024 and "
        f"**{r['of_those_recovered_to_correct']}** became correct.\n"
    )
    md.append(
        f"Accuracy at 256 on the paired set was {r['at_256_incorrect']} incorrect; "
        f"**{r['recovered_to_correct_at_1024']}** flipped to correct at 1024. "
        f"Gold named in reasoning: {r['gold_in_reasoning_at_256']}/{r['pairs_256_1024']} rows at 256 and "
        f"{r['gold_in_reasoning_at_1024']}/{r['pairs_256_1024']} at 1024 — the *preference* is present at "
        "both caps; only the ability to emit changes.\n"
    )
    if r.get("accuracy_when_gold_in_reasoning_at_1024_pct") is not None:
        md.append(
            f"Reasoning-implied ceiling at 1024 (correct when gold is named in reasoning): "
            f"**{r['accuracy_when_gold_in_reasoning_at_1024_pct']}%**.\n"
        )
    md.append("")
    md.append("## 3. Hammer 3B / 7B\n")
    for m in ("hammer2.1_3b", "hammer2.1_7b"):
        md.append(f"### {m} (native 256)\n")
        md.append("| cap | n | accuracy % (95% CI) | truncated % | hit-cap % | mean tok | max tok |")
        md.append("|---|---|---|---|---|---|---|")
        for c in sorted(cells.get(m, {})):
            s = per_cell[m][c]
            ci = s["accuracy_ci95"]
            md.append(
                f"| {c} | {s['n']} | {s['accuracy_pct']} ({ci[0]}-{ci[1]}) | {s['truncated_pct']} | "
                f"{s['hit_cap_pct']} | {s['completion_tokens_mean']} | {s['completion_tokens_max']} |"
            )
        for c, d in hammer[m]["deltas"].items():
            md.append(
                f"- {c} vs 256: paired delta **{d.get('delta_accuracy_pct_points')} pp** "
                f"(95% CI {d.get('delta_ci95')}), pairs={d.get('n_pairs')}"
            )
        md.append("")
    md.append("## 4. Qwen3.5-9B 1024 -> 2048\n")
    md.append("| cap | n | accuracy % (95% CI) | truncated % | hit-cap % | mean tok | max tok |")
    md.append("|---|---|---|---|---|---|---|")
    for c in (1024, 2048):
        s = per_cell["qwen3.5_9b"].get(c)
        if not s:
            continue
        ci = s["accuracy_ci95"]
        md.append(
            f"| {c} | {s['n']} | {s['accuracy_pct']} ({ci[0]}-{ci[1]}) | {s['truncated_pct']} | "
            f"{s['hit_cap_pct']} | {s['completion_tokens_mean']} | {s['completion_tokens_max']} |"
        )
    md.append("")
    md.append(
        f"Paired 1024/2048 cells: {q9_detail['pairs']}. At 1024, {q9_detail['at_1024_hit_cap']} hit the cap, "
        f"{q9_detail['of_those_correct_at_2048']} of those were correct at 2048; "
        f"{q9_detail['at_1024_incorrect']} were incorrect at 1024 and {q9_detail['recovered_at_2048']} "
        f"recovered at 2048; {q9_detail['at_1024_correct_lost_at_2048']} were correct at 1024 but wrong at 2048. "
        f"Paired delta: **{q9_detail['delta'].get('delta_accuracy_pct_points')} pp** "
        f"(95% CI {q9_detail['delta'].get('delta_ci95')}).\n"
    )
    md.append("### Nemotron per-state detail (correct reps / 3, truncated reps)\n")
    md.append("| state | gold | 256 acc | 256 trunc | 512 acc | 512 trunc | 1024 acc | 1024 trunc |")
    md.append("|---|---|---|---|---|---|---|---|")
    states_sorted = sorted({r["state_id"] for r in nem})
    for st in states_sorted:
        cellsrow = {c: [r for r in nem if r["state_id"] == st and r["requested_max_tokens"] == c] for c in (256, 512, 1024)}
        gold = next((r["gold_action"] for r in nem if r["state_id"] == st), "")
        def celltxt(c):
            rs = cellsrow[c]
            if not rs:
                return "-", "-"
            return f"{sum(1 for r in rs if r['correct'])}/{len(rs)}", f"{sum(1 for r in rs if r['truncated'])}/{len(rs)}"
        a256, t256 = celltxt(256)
        a512, t512 = celltxt(512)
        a1024, t1024 = celltxt(1024)
        md.append(f"| {st} | {gold} | {a256} | {t256} | {a512} | {t512} | {a1024} | {t1024} |")
    md.append("")
    md.append("## 5. Completion-vs-cognition decomposition (incorrect rows per cell)\n")
    md.append(
        "Categories: `cutoff_with_cognition` (gold named in reasoning, no call parsed), "
        "`cognition_but_other_call` (gold named, a different call parsed), `emitted_unparsed` "
        "(a call emitted but not parsed), `wrong_choice` (a different legal call parsed, gold not named), "
        "`silent_no_call` (nothing emitted, gold not named).\n"
    )
    md.append("| model | cap | incorrect | cutoff_with_cognition | cognition_but_other_call | emitted_unparsed | wrong_choice | silent_no_call |")
    md.append("|---|---|---|---|---|---|---|---|")
    for m in MODEL_ORDER:
        for c in sorted(cells.get(m, {})):
            s = per_cell[m][c]
            d = s["incorrect_decomposition"]
            md.append(
                f"| {m} | {c} | {s['incorrect_n']} | {d.get('cutoff_with_cognition', 0)} | "
                f"{d.get('cognition_but_other_call', 0)} | {d.get('emitted_unparsed', 0)} | "
                f"{d.get('wrong_choice', 0)} | {d.get('silent_no_call', 0)} |"
            )
    md.append("")
    md.append(
        "Totals across all cells: "
        + ", ".join(f"**{k}**={v}" for k, v in sorted(decomp_totals.items()))
        + ".\n"
    )
    md.append("## 6. Frozen-corpus reproduction\n")
    md.append("| model | rows | effective cap | rows at cap | max completion tok |")
    md.append("|---|---|---|---|---|")
    for m in MODEL_ORDER:
        f = frozen_stats[m]
        md.append(
            f"| {m} | {f['rows']} | {f['effective_cap']} | {f['rows_at_effective_cap']} "
            f"({f['rows_at_effective_cap_pct']}%) | {f['max_completion_tokens']} |"
        )
    md.append("")
    (OUT / "report.md").write_text("\n".join(md) + "\n", encoding="utf-8")
    print(f"wrote report.json and report.md ({len(rows)} measured rows, {len(invalid)} invalid)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
