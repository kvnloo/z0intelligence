"""Score FunctionGemma (unedited or edited) on the public tool-choice task (toolchoice.py) via the Modal GPU job.

  WSP_BACKEND=modal python toolchoice_eval.py RUN_ID [--split dev] [--edit-dir DIR] [--limit N]
  python toolchoice_eval.py RUN_ID --dry-run      # no model: fake generations, checks request + scoring end to end

Writes ~/.z0int/state/weight-space/slm/<RUN_ID>/toolchoice-<split>-<label>/{gen.jsonl,score.json} (create-only).
The sealed split is refused unless WSP_ROLE=tiny-evaluator.
"""
from __future__ import annotations

import argparse
import json
import os
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import toolchoice as T  # noqa: E402

TARGET = "functiongemma-270m"
STATE = Path(os.environ.get("Z0INT_HOME", Path.home() / ".z0int")) / "state" / "weight-space" / "slm"


def request(items: list[dict], max_tokens: int, edit_dir: str | None) -> dict:
    return {"target": TARGET, "edit_dir": edit_dir, "n": 1, "max_tokens": max_tokens, "seed": 0,
            "items": [{"id": it["id"], "source": it["kind"],
                       "messages": [{"role": "developer", "content": T.SYSTEM}, {"role": "user", "content": it["user"]}],
                       "tools": T.to_openai_tools(it["tools"])} for it in items]}


def fake_generate(req: dict, out: Path) -> dict:
    rng = random.Random(0)
    with (out / "gen.jsonl").open("w") as f:
        for it in req["items"]:
            names = [t["function"]["name"] for t in it["tools"]]
            pick = rng.choice(names + [None])
            text = f"<start_function_call>call:{pick}{{}}<end_function_call>" if pick else "No suitable function."
            f.write(json.dumps({"id": it["id"], "sample": 0, "text": text, "tokens": 9, "cut": False}) + "\n")
    return {"generations": len(req["items"]), "engine": "fake"}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("run_id")
    ap.add_argument("--split", default="dev", choices=["practice", "dev", "sealed"])
    ap.add_argument("--edit-dir")
    ap.add_argument("--limit", type=int)
    ap.add_argument("--label", help="name for this edited model in the run root (e.g. the editor)")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    if a.split == "sealed" and os.environ.get("WSP_ROLE") != "tiny-evaluator":
        raise SystemExit("sealed split is tiny-evaluator only")
    items = T.load(a.split)[: a.limit] if a.limit else T.load(a.split)
    label = (a.label or "edited" if a.edit_dir else "unedited") + ("-dry" if a.dry_run else "")
    if a.edit_dir:  # Modal syncs edits per session: outputs of an edited pass must live in that session's work dir
        if not any(Path(a.edit_dir).glob("*.npy")):
            raise SystemExit(f"no edits in {a.edit_dir}: the editor submitted the unedited model")
        out = Path(a.edit_dir).parent / "eval" / f"toolchoice-{a.split}"
    else:
        out = STATE / a.run_id / f"toolchoice-{a.split}-{label}"
    out.mkdir(parents=True, exist_ok=False)
    req = request(items, 256, a.edit_dir)
    if a.dry_run:
        res = fake_generate(req, out)
    else:
        import modal_backend as MB  # noqa: PLC0415
        res = MB.remote_gpu_job("generate", req, out)
    by_id = {it["id"]: it for it in items}
    rows = []
    for g in (json.loads(x) for x in (out / "gen.jsonl").read_text().splitlines()):
        it = by_id[g["id"]]
        rows.append({"id": g["id"], "kind": it["kind"], "gold": it["gold"],
                     "pred": T.predicted(g["text"], [t["name"] for t in it["tools"]]), "cut": g.get("cut")})
    s = T.score(rows) | {"target": TARGET, "split": a.split, "label": label, "data": T.REV,
                         "invalid_name_pct": round(100 * sum(r["pred"].startswith("invalid:") for r in rows) / len(rows), 1),
                         "cut_pct": round(100 * sum(bool(r["cut"]) for r in rows) / len(rows), 1), "gen": res}
    (out / "score.json").write_text(json.dumps(s, indent=1) + "\n")
    if a.edit_dir:
        (STATE / a.run_id / f"toolchoice-{a.split}-{label}.json").write_text(json.dumps(s, indent=1) + "\n")
    print(json.dumps({k: v for k, v in s.items() if k != "gen"}))


if __name__ == "__main__":
    main()
