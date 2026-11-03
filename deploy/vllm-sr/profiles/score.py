#!/usr/bin/env python3
"""Score results.jsonl into per-model, per-family metrics and propose router profiles.

reliability = share of calls that returned a usable answer (any HTTP/timeout failure counts against it).
accuracy    = graded-correct share of ALL calls (a failed call is a wrong answer for a router).
quality     = accuracy on the hard tier only (the easy tier saturates).
Writes scores.json and prints a table plus the profile picks.
"""
import collections, json, statistics as st, sys
from pathlib import Path

from tasks import TASKS

HERE = Path(__file__).resolve().parent
FAMILY = {t["id"]: t["family"] for t in TASKS}
HARD = {t["id"] for t in TASKS if t["id"].startswith("hard-")}
FREE_CLASS = {"groq": "free", "nvidia": "free", "openrouter": "free", "cerebras": "free-credit", "gemini": "free-public", "router": "router"}


def score(rows):
    by = collections.defaultdict(list)
    for r in rows:
        by[r["model"]].append(r)
    out = {}
    for m, rs in by.items():
        ok = [r for r in rs if r.get("ok")]
        tt = sorted(r["ttft_s"] for r in ok if r.get("ttft_s"))
        tot = sorted(r["total_s"] for r in ok if r.get("total_s"))
        fam = collections.defaultdict(list)
        for r in rs:
            fam[r["family"]].append(r["correct"])
        hard = [r["correct"] for r in rs if r["task"] in HARD]
        errs = collections.Counter(((r.get("error") or "").split(" ") + ["", ""])[1] or ("empty" if "error" not in r else "timeout")
                                   for r in rs if not r.get("ok"))
        out[m] = {
            "provider": m.split("/", 1)[0], "free_class": FREE_CLASS[m.split("/", 1)[0]], "n": len(rs),
            "reliability": round(len(ok) / len(rs), 3), "accuracy": round(sum(r["correct"] for r in rs) / len(rs), 3),
            "quality_hard": round(sum(hard) / len(hard), 3) if hard else None, "n_hard": len(hard),
            "family_acc": {f: round(sum(v) / len(v), 3) for f, v in fam.items()},
            "ttft_p50": round(st.median(tt), 2) if tt else None, "ttft_p90": round(tt[int(0.9 * (len(tt) - 1))], 2) if tt else None,
            "total_p50": round(st.median(tot), 2) if tot else None, "errors": dict(errs),
        }
    return out


def pick(scores, key, min_rel=0.9, classes=("free",), max_ttft=None, need=None, k=4):
    c = [(m, s) for m, s in scores.items() if s["free_class"] in classes and s["reliability"] >= min_rel
         and (s["n_hard"] or 0) >= 8 and (max_ttft is None or (s["ttft_p50"] or 99) <= max_ttft)
         and (need is None or need(s))]
    c.sort(key=lambda kv: (-(key(kv[1]) or 0), kv[1]["total_p50"] or 99))
    return [m for m, _ in c[:k]]


def main():
    rows = [json.loads(l) for l in open(sys.argv[1] if len(sys.argv) > 1 else HERE / "results.jsonl")]
    s = score(rows)
    json.dump(s, open(HERE / "scores.json", "w"), indent=1)
    print(f"{'model':50s} {'class':11s} {'n':>3s} {'rel':>5s} {'acc':>5s} {'hard':>5s} {'ttft':>5s} {'tot':>5s}  families / errors")
    for m, v in sorted(s.items(), key=lambda kv: (-(kv[1]["quality_hard"] or 0), -kv[1]["accuracy"])):
        print(f"{m[:50]:50s} {v['free_class']:11s} {v['n']:3d} {v['reliability']:5.2f} {v['accuracy']:5.2f} "
              f"{(v['quality_hard'] if v['quality_hard'] is not None else -1):5.2f} {v['ttft_p50'] or 0:5.2f} {v['total_p50'] or 0:5.1f}  "
              f"{v['family_acc']} {v['errors'] or ''}")
    fam = lambda f: (lambda v: v["family_acc"].get(f))
    profiles = {
        "free-quality": pick(s, lambda v: v["quality_hard"]),
        "free-reasoning": pick(s, fam("reason")),
        "free-verify": pick(s, fam("verify"), need=lambda v: (v["family_acc"].get("verify") or 0) >= 0.9),
        "free-longctx": pick(s, fam("longctx"), min_rel=0.8, need=lambda v: (v["family_acc"].get("longctx") or 0) >= 0.9),
        "free-fast": pick(s, lambda v: v["accuracy"], max_ttft=0.6, classes=("free", "free-credit")),
        "free-public": pick(s, lambda v: v["quality_hard"], classes=("free-public",)),
    }
    json.dump(profiles, open(HERE / "profiles.json", "w"), indent=1)
    print("\nprofiles:", json.dumps(profiles, indent=1))


if __name__ == "__main__":
    main()
