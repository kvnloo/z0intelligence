#!/usr/bin/env python3
"""Deterministic scorer for the TencentDB model-select benchmark (PREREG §4).

  score.py runs/<run-id> [--out report.json] [--md report.md]

Reads every runs/<run-id>/<cand>/r<k>/<conv>/result.json (+ shim-ledger.jsonl) and the frozen
corpus. No LLM is involved here; the optional local judge (judge.py) is reported separately.
"""
import argparse
import json
import math
import random
import re
import statistics
import sys
from collections import defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from normalize import norm  # noqa: E402

sys.path.insert(0, str(HERE.parent / "corpus"))
from dsl import NEG_ZH  # noqa: E402
CJK = re.compile(r"[\u4e00-\u9fff]")

CORPUS = HERE.parent / "corpus" / "corpus.json"
GROUND_MIN = 0.34          # FP memory with < 34% content tokens found in the conversation = ungrounded
SECRET_WIN = 12            # leak = any 12-char window of a secret appears in persisted memory text
WORKLOAD = {               # calls/day estimates from workload.md §3 (quiet / mean active / peak)
    "quiet": {"l1-extraction": 8, "l1-conflict-detection": 5, "l2": 3, "l3": 0},
    "mean": {"l1-extraction": 60, "l1-conflict-detection": 40, "l2": 15, "l3": 2},
    "peak": {"l1-extraction": 400, "l1-conflict-detection": 300, "l2": 50, "l3": 10},
}
STOP = set("""a an the and or but of to in on at for with by from as is are was were be been being this that these
those it its into about over under than then so such not no nor too very can could should would will shall may might
must do does did done have has had having he she they them his her their our we you your i me my mine user users ai
assistant prefers prefer preferred wants want wanted likes like uses use used using decided decide decision plans plan
planned keep keeps always never also only all any each every when while which who whom whose what where how there
here after before during since until via per etc one two""".split())

# --------------------------------------------------------------------------- matching primitives


def match_all(anchors, text_n):
    return all(re.search(a, text_n) for a in anchors)


def fires(item, text_n, anchor_key="anchors"):
    return match_all(item[anchor_key], text_n) and not any(re.search(u, text_n) for u in item.get("unless", []))


def stem(w):
    for suf in ("ing", "ed", "es", "s", "ly"):
        if len(w) > 4 and w.endswith(suf):
            return w[: -len(suf)]
    return w


def toks(text_n):
    return {stem(w) for w in re.findall(r"[a-z0-9][a-z0-9._/+-]{2,}", text_n) if w not in STOP}


def conv_source(conv):
    parts = []
    for s in conv["sessions"]:
        for r in s["rounds"]:
            parts += [r["user"]["content"], r["assistant"]["content"]]
    return norm("\n".join(parts))


def secret_windows(sec):
    s = sec.strip()
    if len(s) <= SECRET_WIN:
        return [s]
    return [s[i:i + SECRET_WIN] for i in range(len(s) - SECRET_WIN + 1)]


def leaked(sec, text):
    low = text.lower()
    return any(w in text or w.lower() in low for w in secret_windows(sec))


def sentences(text):
    return [x for x in re.split(r"(?<=[.!?。])\s+|\n+", text) if x.strip()]


# --------------------------------------------------------------------------- per conversation

BAD_L1 = re.compile(r"l1-empty reason=(no_json|parse_fail|not_array|normalized_all_dropped|llm_error)")
BAD_DEDUP = re.compile(r"No JSON array found in conflict detection|Failed to parse conflict detection|Conflict detection response is not an array|Batch conflict detection failed|Batch dedup failed")


def score_conv(conv, res, ledger_calls):
    src = conv_source(conv)
    src_toks = toks(src)
    gold, opt, traps = conv["gold"], conv["optional"], conv["traps"]
    recs = res.get("records", [])
    out = {"conv": conv["id"], "n_records": len(recs), "records": []}
    recalled = set()
    tp = fp = halluc = trap_hits = ungrounded = 0
    type_ok = type_n = 0
    for r in recs:
        tn = norm(r["content"])
        g_hits = [g["gid"] for g in gold if match_all(g["anchors"], tn)]
        o_hit = any(match_all(o["anchors"], tn) for o in opt)
        t_hits = [t["tid"] for t in traps if fires(t, tn)]
        is_tp = bool(g_hits) or o_hit
        frac = None
        if not is_tp:
            ct = toks(tn)
            frac = (len(ct & src_toks) / len(ct)) if ct else 1.0
        ung = (not is_tp) and frac is not None and frac < GROUND_MIN
        h = bool(t_hits) or ung
        recalled.update(g_hits)
        tp += is_tp
        fp += (not is_tp)
        halluc += h
        trap_hits += bool(t_hits)
        ungrounded += ung
        if g_hits:
            allowed = set()
            for g in gold:
                if g["gid"] in g_hits:
                    allowed |= set(g.get("types") or [g.get("type", "persona")])
            type_n += 1
            type_ok += r.get("type") in allowed
        out["records"].append({"content": r["content"], "type": r.get("type"), "gold": g_hits, "opt": o_hit,
                               "traps": t_hits, "ground": None if frac is None else round(frac, 2), "halluc": h})
    out["non_user_language"] = sum(1 for r in recs if len(CJK.findall(r.get("content", ""))) >= 3)
    out.update(tp=tp, fp=fp, halluc=halluc, trap_hits=trap_hits, ungrounded=ungrounded,
               gold_n=len(gold), recalled=len(recalled), missed=[g["gid"] for g in gold if g["gid"] not in recalled],
               type_ok=type_ok, type_n=type_n,
               empty_case=(len(gold) == 0), empty_ok=(len(gold) == 0 and fp == 0))

    # secrets: every persisted text (L1 records incl. metadata, scene/persona markdown)
    persisted = "\n".join([r.get("content", "") + " " + str(r.get("metadata", "")) for r in recs] +
                          [a["content"] for a in res.get("artifacts", [])])
    out["secret_leaks"] = [i for i, s in enumerate(conv["secrets"]) if leaked(s, persisted)]
    out["secrets_n"] = len(conv["secrets"])

    # supersession
    sups = []
    for s in conv["supersessions"]:
        cur = next(g for g in gold if g["gid"] == s["current"])
        cur_present = any(match_all(cur["anchors"], norm(r["content"])) for r in recs)
        stale_live = [r["content"] for r in recs
                      if match_all(s["stale_anchors"], norm(r["content"]))
                      and not match_all(cur["anchors"], norm(r["content"]))
                      and not any(re.search(u, norm(r["content"])) for u in s["unless"])]
        sups.append({"sid": s["sid"], "scope": s["scope"], "current_present": cur_present,
                     "stale_live": len(stale_live), "correct_strict": cur_present and not stale_live,
                     "correct_lenient": cur_present})
    out["supersessions"] = sups

    # dedup
    dd = []
    for d in conv["dedup"]:
        g = next(x for x in gold if x["gid"] == d["gid"])
        if d["scope"] == "within":
            n_by_sess = defaultdict(int)
            for r in recs:
                if match_all(g["anchors"], norm(r["content"])):
                    n_by_sess[r.get("session_id")] += 1
            worst = max(n_by_sess.values()) if n_by_sess else 0
            dd.append({"gid": d["gid"], "scope": "within", "max_per_session": worst, "correct": worst == 1})
        else:
            n = sum(1 for r in recs if match_all(g["anchors"], norm(r["content"])))
            dd.append({"gid": d["gid"], "scope": "cross", "count": n, "correct": n == 1})
    out["dedup"] = dd

    # output contract + latency per call (gateway's own parser verdicts, attributed per call)
    calls = res.get("calls", [])
    cc = defaultdict(lambda: {"n": 0, "valid": 0, "repaired": 0, "type_drop": 0, "err": 0, "ms": []})
    for c in calls:
        t = c["taskId"]
        key = "l2" if t.startswith("scene-extract") else ("l3" if t.startswith("persona") else t)
        st = cc[key]
        st["n"] += 1
        st["ms"].append(c["ms"])
        logs = "\n".join(c.get("logs", []))
        bad = (not c["ok"]) or (key == "l1-extraction" and BAD_L1.search(logs)) or (key == "l1-conflict-detection" and BAD_DEDUP.search(logs))
        st["err"] += (not c["ok"])
        st["valid"] += (not bad)
        st["repaired"] += ("Repaired non-strict" in logs)
        st["type_drop"] += ("Skipping memory with invalid type" in logs)
    out["calls"] = {k: dict(v) for k, v in cc.items()}

    # L2 / L3
    if res.get("layers") == "l1l2l3":
        scene_txt = "\n".join(a["content"] for a in res.get("artifacts", []) if "persona" not in a["path"].lower())
        persona_txt = "\n".join(a["content"] for a in res.get("artifacts", []) if "persona" in a["path"].lower())
        l2_runs = [e for e in res.get("events", []) if e["kind"] == "l2"]
        l3_runs = [e for e in res.get("events", []) if e["kind"] == "l3"]
        sn, pn = norm(scene_txt), norm(persona_txt)

        def sent_traps(txt):
            # amendment A1: L2/L3 text is mostly Chinese -> Chinese negation/attribution markers also suppress
            zh = [dict(t, unless=t["unless"] + [NEG_ZH]) for t in traps]
            return sum(1 for snt in sentences(txt) for t in zh if fires(t, norm(snt)))
        out["l2"] = {
            "runs": len(l2_runs),
            "ok": sum(1 for e in l2_runs if not e.get("error") and e.get("result") and e["result"].get("latestCursor")),
            "empty": sum(1 for e in l2_runs if e.get("result") and e["result"].get("skipped")),
            "errors": sum(1 for e in l2_runs if e.get("error")),
            "scene_chars": len(scene_txt),
            "facts_n": len(conv["l2_facts"]),
            "facts_hit": sum(1 for f in conv["l2_facts"] if match_all(f["anchors"], sn)),
            "trap_sentences": sent_traps(scene_txt),
        }
        out["l3"] = {
            "invocations": len(l3_runs),
            "errors": sum(1 for e in l3_runs if e.get("error")),
            "persona_chars": len(persona_txt.strip()),
            "facts_n": len(conv["l3_facts"]),
            "facts_hit": sum(1 for f in conv["l3_facts"] if match_all(f["anchors"], pn)),
            "trap_sentences": sent_traps(persona_txt),
        }
    return out


# --------------------------------------------------------------------------- aggregation


def prf(tp, fp, rec, gold_n):
    p = tp / (tp + fp) if (tp + fp) else 1.0
    r = rec / gold_n if gold_n else 1.0
    f = 2 * p * r / (p + r) if (p + r) else 0.0
    return p, r, f


def pct(xs, q):
    if not xs:
        return None
    xs = sorted(xs)
    k = (len(xs) - 1) * q
    lo, hi = math.floor(k), math.ceil(k)
    return xs[lo] + (xs[hi] - xs[lo]) * (k - lo)


def agg_repeat(convs):
    tp = sum(c["tp"] for c in convs)
    fp = sum(c["fp"] for c in convs)
    rec = sum(c["recalled"] for c in convs)
    gn = sum(c["gold_n"] for c in convs)
    p, r, f = prf(tp, fp, rec, gn)
    nrec = tp + fp
    sup = [s for c in convs for s in c["supersessions"]]
    dd = [d for c in convs for d in c["dedup"]]
    calls = defaultdict(lambda: {"n": 0, "valid": 0, "repaired": 0, "type_drop": 0, "err": 0, "ms": []})
    for c in convs:
        for k, v in c["calls"].items():
            for kk in ("n", "valid", "repaired", "type_drop", "err"):
                calls[k][kk] += v[kk]
            calls[k]["ms"] += v["ms"]
    contract_n = sum(calls[k]["n"] for k in ("l1-extraction", "l1-conflict-detection"))
    contract_ok = sum(calls[k]["valid"] for k in ("l1-extraction", "l1-conflict-detection"))
    empties = [c for c in convs if c["empty_case"]]
    l2 = [c["l2"] for c in convs if "l2" in c]
    l3 = [c["l3"] for c in convs if "l3" in c]
    return {
        "n_convs": len(convs), "precision": p, "recall": r, "f1": f, "n_records": nrec,
        "halluc_rate": (sum(c["halluc"] for c in convs) / nrec) if nrec else 0.0,
        "trap_hits": sum(c["trap_hits"] for c in convs), "ungrounded_fp": sum(c["ungrounded"] for c in convs),
        "secret_leaks": sum(len(c["secret_leaks"]) for c in convs), "secrets_n": sum(c["secrets_n"] for c in convs),
        "l1_non_user_language_rate": (sum(c["non_user_language"] for c in convs) / nrec) if nrec else 0.0,
        "type_acc": (sum(c["type_ok"] for c in convs) / max(1, sum(c["type_n"] for c in convs))),
        "empty_case_acc": (sum(c["empty_ok"] for c in empties) / len(empties)) if empties else None,
        "sup_within_strict": _rate([s["correct_strict"] for s in sup if s["scope"] == "within"]),
        "sup_cross_strict": _rate([s["correct_strict"] for s in sup if s["scope"] == "cross"]),
        "sup_cross_lenient": _rate([s["correct_lenient"] for s in sup if s["scope"] == "cross"]),
        "dedup_within": _rate([d["correct"] for d in dd if d["scope"] == "within"]),
        "dedup_cross": _rate([d["correct"] for d in dd if d["scope"] == "cross"]),
        "contract_validity": (contract_ok / contract_n) if contract_n else None,
        "calls": {k: {"n": v["n"], "valid": v["valid"], "repaired": v["repaired"], "type_drop": v["type_drop"],
                      "err": v["err"], "p50_ms": pct(v["ms"], .5), "p95_ms": pct(v["ms"], .95), "max_ms": max(v["ms"]) if v["ms"] else None}
                  for k, v in calls.items()},
        "l2": {"runs": sum(x["runs"] for x in l2), "ok": sum(x["ok"] for x in l2), "errors": sum(x["errors"] for x in l2),
               "fact_recall": _ratio(sum(x["facts_hit"] for x in l2), sum(x["facts_n"] for x in l2)),
               "trap_sentences": sum(x["trap_sentences"] for x in l2)} if l2 else None,
        "l3": {"convs": len(l3), "persona_nonempty": sum(1 for x in l3 if x["persona_chars"] > 0),
               "errors": sum(x["errors"] for x in l3),
               "fact_recall": _ratio(sum(x["facts_hit"] for x in l3), sum(x["facts_n"] for x in l3)),
               "trap_sentences": sum(x["trap_sentences"] for x in l3)} if l3 else None,
    }


def _rate(xs):
    return (sum(xs) / len(xs)) if xs else None


def _ratio(a, b):
    return (a / b) if b else None


def ledger_stats(ledgers):
    by = defaultdict(lambda: {"n": 0, "pt": 0, "ct": 0, "rt": 0, "cached": 0, "cost": 0.0, "lat": [], "status": defaultdict(int)})
    for rec in ledgers:
        k = rec.get("kind", "other")
        b = by[k]
        b["n"] += 1
        b["status"][str(rec.get("status"))] += 1
        u = rec.get("usage") or {}
        b["pt"] += u.get("prompt_tokens") or 0
        b["ct"] += u.get("completion_tokens") or 0
        b["rt"] += ((u.get("completion_tokens_details") or {}).get("reasoning_tokens")) or 0
        b["cached"] += ((u.get("prompt_tokens_details") or {}).get("cached_tokens")) or 0
        b["cost"] += rec.get("cost_usd") or 0.0
        if rec.get("latency_ms") is not None:
            b["lat"].append(rec["latency_ms"])
    out = {}
    for k, b in by.items():
        out[k] = {"n": b["n"], "prompt_tokens": b["pt"], "completion_tokens": b["ct"], "reasoning_tokens": b["rt"],
                  "cached_tokens": b["cached"], "cost_usd": round(b["cost"], 6),
                  "mean_prompt_tokens": round(b["pt"] / b["n"]) if b["n"] else 0,
                  "mean_completion_tokens": round(b["ct"] / b["n"]) if b["n"] else 0,
                  "mean_cost_usd": (b["cost"] / b["n"]) if b["n"] else 0.0,
                  "upstream_p50_ms": pct(b["lat"], .5), "upstream_p95_ms": pct(b["lat"], .95), "status": dict(b["status"])}
    return out


def projected_daily_cost(ls):
    per = {"l1-extraction": ls.get("l1-extraction", {}).get("mean_cost_usd", 0.0),
           "l1-conflict-detection": ls.get("l1-dedup", {}).get("mean_cost_usd", 0.0),
           "l2": ls.get("l2-scene", {}).get("mean_cost_usd", 0.0),
           "l3": ls.get("l3-persona", {}).get("mean_cost_usd", 0.0)}
    return {day: round(sum(per[k] * v for k, v in vol.items()), 4) for day, vol in WORKLOAD.items()}


def bootstrap_f1(per_conv_by_repeat, n=2000, seed=0):
    """95% CI of micro-F1 resampling conversations (pooled over repeats)."""
    convs = sorted({c for rep in per_conv_by_repeat for c in rep})
    if not convs:
        return None
    rng = random.Random(seed)
    vals = []
    for _ in range(n):
        sample = [rng.choice(convs) for _ in convs]
        tp = fp = rec = gn = 0
        for rep in per_conv_by_repeat:
            for cid in sample:
                c = rep.get(cid)
                if c:
                    tp += c["tp"]; fp += c["fp"]; rec += c["recalled"]; gn += c["gold_n"]
        vals.append(prf(tp, fp, rec, gn)[2])
    vals.sort()
    return [vals[int(.025 * n)], vals[int(.975 * n)]]


def summarize(xs):
    xs = [x for x in xs if x is not None]
    if not xs:
        return None
    return {"mean": statistics.mean(xs), "sd": statistics.pstdev(xs) if len(xs) > 1 else 0.0, "values": xs}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("run_dir")
    ap.add_argument("--out")
    ap.add_argument("--md")
    ap.add_argument("--convs", help="restrict to subset name or comma list")
    a = ap.parse_args()
    corpus = json.loads(CORPUS.read_text())
    byid = {c["id"]: c for c in corpus["conversations"]}
    restrict = None
    if a.convs:
        restrict = set(corpus["subsets"].get(a.convs, a.convs.split(",")))
    run = Path(a.run_dir)
    report = {"run": str(run), "candidates": {}}
    for cand_dir in sorted(p for p in run.iterdir() if p.is_dir()):
        reps, ledgers, per_rep_conv = [], [], []
        for rep_dir in sorted(cand_dir.glob("r*")):
            convs = []
            for rf in sorted(rep_dir.glob("*/result.json")):
                res = json.loads(rf.read_text())
                if res["conv"] not in byid or (restrict and res["conv"] not in restrict):
                    continue
                convs.append(score_conv(byid[res["conv"]], res, None))
            errs = sorted(p.parent.name for p in rep_dir.glob("*/driver-error.txt"))
            lf = rep_dir / "shim-ledger.jsonl"
            if lf.exists():
                ledgers += [json.loads(x) for x in lf.read_text().splitlines() if x.strip()]
            if convs:
                agg = agg_repeat(convs)
                agg["repeat"] = rep_dir.name
                agg["driver_errors"] = errs
                reps.append(agg)
                per_rep_conv.append({c["conv"]: c for c in convs})
        if not reps:
            continue
        ls = ledger_stats(ledgers)
        keys = ["precision", "recall", "f1", "halluc_rate", "l1_non_user_language_rate", "type_acc", "empty_case_acc", "sup_within_strict",
                "sup_cross_strict", "sup_cross_lenient", "dedup_within", "dedup_cross", "contract_validity"]
        summary = {k: summarize([r[k] for r in reps]) for k in keys}
        summary["secret_leaks_total"] = sum(r["secret_leaks"] for r in reps)
        summary["f1_ci95_bootstrap"] = bootstrap_f1(per_rep_conv)
        lat = defaultdict(list)
        for r in reps:
            for k, v in r["calls"].items():
                if v["p95_ms"] is not None:
                    lat[k].append(v)
        summary["latency_ms"] = {k: {"p50_mean": statistics.mean(x["p50_ms"] for x in v),
                                     "p95_mean": statistics.mean(x["p95_ms"] for x in v),
                                     "max": max(x["max_ms"] for x in v)} for k, v in lat.items()}
        summary["gates"] = {
            "halluc_le_5pct": (summary["halluc_rate"]["mean"] <= 0.05) if summary["halluc_rate"] else None,
            "zero_secret_leaks": summary["secret_leaks_total"] == 0,
        }
        summary["gates"]["pass"] = all(v for v in summary["gates"].values() if v is not None)
        report["candidates"][cand_dir.name] = {
            "repeats": reps, "summary": summary, "ledger": ls, "projected_daily_cost_usd": projected_daily_cost(ls),
            "per_conv": per_rep_conv,
        }
    if a.out:
        Path(a.out).write_text(json.dumps(report, indent=1, ensure_ascii=False))
    md = render_md(report)
    if a.md:
        Path(a.md).write_text(md)
    print(md)


def fmt(x, pctg=False):
    if x is None:
        return "-"
    if isinstance(x, dict):
        m = x["mean"]
        s = f"{m*100:.1f}%" if pctg else f"{m:.3f}"
        if len(x["values"]) > 1:
            s += f" ±{x['sd']*100:.1f}" if pctg else f" ±{x['sd']:.3f}"
        return s
    return f"{x*100:.1f}%" if pctg else f"{x:.3f}"


def render_md(report):
    L = [f"# Benchmark report: {report['run']}", "",
         "| candidate | reps | F1 | P | R | halluc | leaks | contract | sup-within | dedup-within | empty-ok | L1 p50/p95 s | $ run | $/day peak | gates |",
         "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for cid, c in report["candidates"].items():
        s = c["summary"]
        l1 = s["latency_ms"].get("l1-extraction")
        lat = f"{l1['p50_mean']/1000:.1f}/{l1['p95_mean']/1000:.1f}" if l1 else "-"
        cost = sum(v["cost_usd"] for v in c["ledger"].values())
        L.append(f"| {cid} | {len(c['repeats'])} | {fmt(s['f1'])} | {fmt(s['precision'])} | {fmt(s['recall'])} | "
                 f"{fmt(s['halluc_rate'], True)} | {s['secret_leaks_total']} | {fmt(s['contract_validity'], True)} | "
                 f"{fmt(s['sup_within_strict'], True)} | {fmt(s['dedup_within'], True)} | {fmt(s['empty_case_acc'], True)} | "
                 f"{lat} | {cost:.4f} | {c['projected_daily_cost_usd']['peak']:.2f} | {'PASS' if s['gates']['pass'] else 'FAIL'} |")
    L.append("")
    for cid, c in report["candidates"].items():
        L.append(f"## {cid}")
        for r in c["repeats"]:
            L.append(f"- {r['repeat']}: convs={r['n_convs']} records={r['n_records']} F1={r['f1']:.3f} P={r['precision']:.3f} "
                     f"R={r['recall']:.3f} halluc={r['halluc_rate']*100:.1f}% traps={r['trap_hits']} ungrounded={r['ungrounded_fp']} "
                     f"leaks={r['secret_leaks']}/{r['secrets_n']} type_acc={r['type_acc']:.2f} driver_errors={r['driver_errors']}")
            L.append(f"  - calls: " + "; ".join(f"{k} n={v['n']} valid={v['valid']} err={v['err']} p50={v['p50_ms']}ms p95={v['p95_ms']}ms" for k, v in r["calls"].items()))
            if r.get("l2"):
                L.append(f"  - L2: {r['l2']}  L3: {r['l3']}")
        L.append(f"- tokens/cost by call kind: " + "; ".join(
            f"{k}: n={v['n']} in={v['mean_prompt_tokens']} out={v['mean_completion_tokens']} reas={v['reasoning_tokens']} cached={v['cached_tokens']} ${v['cost_usd']:.4f} status={v['status']}"
            for k, v in c["ledger"].items()))
        L.append(f"- projected $/day (quiet/mean/peak): {c['projected_daily_cost_usd']}")
        L.append("")
    return "\n".join(L)


if __name__ == "__main__":
    main()
