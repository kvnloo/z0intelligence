"""Final evaluation of submitted edits on held-out MATH-500 + GSM8K test subsets (1 sample each, the target's
protocol at eval_max_tokens). The unedited model is evaluated once per target with the same seeds, so every edited
result is a paired comparison against it.

  python evaluate.py --run-id 20261103 [--target qwen35-0.8b] [--editor opus]
Writes benchmarks/weight_space/results/slm/<run-id>/<target>__<editor|unedited>.json (per-problem rows included).
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import common as C
from session import _paired, gpu_job, summarize

OUT = HERE.parent / "results" / "slm"


def eval_one(run_id: str, target: str, label: str, edit_dir: Path | None) -> dict:
    dest = OUT / run_id / f"{target}__{label}.json"
    if dest.exists():
        return json.loads(dest.read_text())
    items = C.eval_items()
    for i, it in enumerate(items):
        it["seed"] = 1_000_003 + i
    out = C.STATE / run_id / "eval" / f"{target}__{label}"
    r = gpu_job("generate", {"target": target, "edit_dir": str(edit_dir) if edit_dir else None, "items": items,
                             "n": 1, "max_tokens": C.TARGETS[target]["eval_max_tokens"]}, out)
    rows = [json.loads(x) for x in (out / "gen.jsonl").read_text().splitlines()]
    res = {"target": target, "editor": label, "run_id": run_id, "data_revs": C.pin_data(),
           "model_dir": str(C.model_dir(target)), "max_tokens": C.TARGETS[target]["eval_max_tokens"],
           "sampling": C.TARGETS[target]["sampling"], "edit_hash": C.edit_hash(edit_dir) if edit_dir else None,
           "edited_tensors": sorted(p.stem for p in edit_dir.glob("*.npy")) if edit_dir else [],
           "all": summarize(rows),
           "math500": summarize([x for x in rows if x["source"] == "math500"]),
           "gsm8k": summarize([x for x in rows if x["source"] == "gsm8k"]),
           "wall_s": r["wall_s"], "evaluated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
           "rows": [{k: x[k] for k in ("id", "source", "tokens", "cut", "pred", "correct")} for x in rows]}
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps(res, indent=1))
    return res


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--run-id", required=True)
    ap.add_argument("--target")
    ap.add_argument("--editor")
    a = ap.parse_args()
    root = C.STATE / a.run_id
    sessions = sorted(p for p in root.glob("*__*") if (p / "state.json").exists())
    targets = sorted({p.name.split("__")[0] for p in sessions} | ({a.target} if a.target else set()))
    for t in targets:
        if a.target and t != a.target:
            continue
        base = eval_one(a.run_id, t, "unedited", None)
        print(f"{t:14s} unedited  MATH500 {base['math500']['accuracy_pct']}  GSM8K {base['gsm8k']['accuracy_pct']}"
              f"  cut {base['all']['cut_at_max_tokens_pct']}%", flush=True)
        for s in sessions:
            tgt, ed = s.name.split("__")
            if tgt != t or (a.editor and ed != a.editor):
                continue
            st = json.loads((s / "state.json").read_text())
            if not st.get("done") or st.get('aborted'):
                print(f"{t:14s} {ed:9s} session unfinished/aborted; skipped: {st.get('aborted', '')}")
                continue
            ed_dir = s / "work" / "edited"
            res = eval_one(a.run_id, t, ed, ed_dir if ed_dir.exists() and any(ed_dir.glob("*.npy")) else None)
            pr = _paired([dict(r, sample=0) for r in base["rows"]], [dict(r, sample=0) for r in res["rows"]])
            res["paired_vs_unedited"] = pr
            (OUT / a.run_id / f"{t}__{ed}.json").write_text(json.dumps(res, indent=1))
            print(f"{t:14s} {ed:9s} MATH500 {res['math500']['accuracy_pct']}  GSM8K {res['gsm8k']['accuracy_pct']}"
                  f"  cut {res['all']['cut_at_max_tokens_pct']}%  paired {pr}", flush=True)


if __name__ == "__main__":
    main()
