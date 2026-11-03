"""Weight-Space Programming evals: run frontier editors on the paper's tasks and score them.

Tasks come verbatim from the paper's released logs (RikiyaT/lm_understand-logs, pinned SHA), fetched
into $Z0INT_HOME/cache/weight-space/ and never committed:

  exp1/<game>   read: which game was this 17,253-weight PPO policy trained on?   (open answer, keyword-scored)
  exp2/<shape>  read: what does this 23,824-weight DDPM generate?               (open answer, keyword-scored)
  exp5/<game>   write: all 20,486 weights of a MinAtar policy from scratch       (mean score, official MinAtar)

Each (editor, task, rep) is one fresh single-reply, tool-free call. Results are create-only:
  benchmarks/weight_space/results/<run_id>/<editor>/<task>/r<k>/{reply.md,score.json,call.json}
Raw provider streams stay under $Z0INT_HOME/state/weight-space/<run_id>/.

  python benchmarks/weight_space/run.py --run-id 20261102-a --editors astra,opus,fable,argon,deepseek \
      --tasks exp1,exp2,exp5 --reps 1
Credentials: bws-env-exec --scope hermes -- <with DEEPSEEK_API_KEY> python ...  (see README).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import editors
import edits

LOGS_REPO = "https://github.com/RikiyaT/lm_understand-logs.git"
LOGS_SHA = "4d09ba6d6a7e891dd8063974dfe81b3d78e3494f"
Z0 = Path(os.environ.get("Z0INT_HOME", Path.home() / ".z0int"))
CACHE = Z0 / "cache" / "weight-space" / "lm_understand-logs"

EFFORT = {"exp1": "high", "exp2": "high", "exp5": "xhigh"}  # the paper's per-experiment effort
EFFORT_OVERRIDE: str | None = None
EXP1 = {"breakout": "a9f535cc", "pong": "dfc6cb65", "pacman": "bdf99279", "freeway": "0bbcc3ea"}
EXP2 = {"circle": "d88aba69", "square": "a6a9dfb7", "triangle": "246a5b58"}
EXP5 = ["breakout", "seaquest", "asterix"]

GAME_WORDS = [("freeway", ["freeway", "frogger", "road-crossing", "road crossing", "traffic-crossing",
                           "traffic crossing", "crossy", "chicken"]),
              ("pacman", ["pac-man", "pacman", "pac man", "ms. pac", "ms pac"]),
              ("breakout", ["breakout", "brick-breaking", "brick breaking", "arkanoid", "brick breaker"]),
              ("pong", ["pong"])]
SHAPE_WORDS = [("circle", ["circle", "disc", "disk", "round", "oval", "ring", "ellipse"]),
               ("square", ["square", "rectangle", "rectangular", "box"]),
               ("triangle", ["triangle", "triangular", "pyramid", "wedge"])]


def fetch() -> Path:
    if not (CACHE / ".git").exists():
        CACHE.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(["git", "clone", "-q", "--filter=blob:limit=5m", LOGS_REPO, str(CACHE)], check=True)
    head = subprocess.run(["git", "-C", str(CACHE), "rev-parse", "HEAD"], check=False, capture_output=True, text=True).stdout.strip()
    if head != LOGS_SHA:
        subprocess.run(["git", "-C", str(CACHE), "checkout", "-q", LOGS_SHA], check=True)
    return CACHE


def tasks(root: Path, families: list[str]) -> list[dict]:
    out = []
    if "exp1" in families:
        key = json.loads((root / "exp1_which_game/eval/answer_key.json").read_text())
        for g, nid in EXP1.items():
            assert key[nid]["game"] == g
            out.append({"id": f"exp1/{g}", "family": "exp1", "truth": g,
                        "prompt_path": root / f"exp1_which_game/runs/{g}/prompt.txt"})
    if "exp2" in families:
        key = json.loads((root / "exp2_diffusion/eval/answer_key.json").read_text())
        for s, nid in EXP2.items():
            assert key[nid]["shape"] == s
            out.append({"id": f"exp2/{s}", "family": "exp2", "truth": s,
                        "prompt_path": root / f"exp2_diffusion/runs/{s}/prompt.txt"})
    if "exp5" in families:
        for g in EXP5:
            out.append({"id": f"exp5/{g}", "family": "exp5", "truth": g,
                        "prompt_path": root / f"exp5_write_from_scratch/runs/{g}/reply/prompt.txt"})
    return out


def _line(text: str, label: str) -> str | None:
    """The answer for `label`: text after "Label:", "**Label:**" or a "## Label" heading; when that line
    holds nothing but markup, the next non-empty line. The last occurrence wins (the answer block)."""
    lines = text.splitlines()
    pat = re.compile(rf"^[#>*_\s]*{label}[*_\s]*(?::|$)[*_\s]*(.*)$", re.IGNORECASE)
    found = None
    for i, ln in enumerate(lines):
        m = pat.match(ln)
        if m:
            rest = m.group(1).strip()
            if not re.sub(r"[*_#\s]", "", rest):
                rest = next((x.strip() for x in lines[i + 1:] if x.strip()), "")
            found = rest
    return found or None


def classify(line: str | None, table) -> str | None:
    if not line:
        return None
    # fold typographic hyphens/dashes (U+2010..U+2015, U+2212) so "Pac‑Man" matches "pac-man"
    low = re.sub("[\u2010-\u2015\u2212]", "-", line.lower())
    hits = [(low.find(w), name) for name, words in table for w in words if w in low]
    return min(hits)[1] if hits else None


def confidence(text: str) -> float | None:
    c = _line(text, "confidence")
    m = re.search(r"(\d+(?:\.\d+)?)", c or "")
    return float(m.group(1)) if m else None


def score_read(task: dict, text: str) -> dict:
    label, table = ("game", GAME_WORDS) if task["family"] == "exp1" else ("images", SHAPE_WORDS)
    line = _line(text, label)
    pred = classify(line, table)
    return {"answer_line": line, "predicted": pred, "truth": task["truth"], "correct": pred == task["truth"],
            "confidence": confidence(text)}


def score_write(task: dict, text: str, out: Path, episodes: int) -> dict:
    w, rep = edits.apply(text)
    import numpy as np
    np.savez(str(out / "net.npz"), **edits.to_npz_dict(w))  # type: ignore[arg-type]
    py = HERE.parent.parent / ".venv-wsp" / "bin" / "python"
    py = str(py) if py.exists() else sys.executable
    p = subprocess.run([py, str(HERE / "minatar_eval.py"), str(out / "net.npz"), "--game", task["truth"],
                        "--prompt", str(task["prompt_path"]), "--episodes", str(episodes), "--seed", "0"],
                       capture_output=True, text=True, check=False)
    res = json.loads(p.stdout) if p.returncode == 0 else {"error": p.stderr[-2000:]}
    return {"edit_report": rep, "minatar": res, "mean": res.get("mean")}


def score(task: dict, text: str, completed: bool, out: Path, episodes: int) -> dict:
    if not completed:
        return {"correct": None, "mean": None}
    if task["family"] == "exp5":
        return score_write(task, text, out, episodes)
    return score_read(task, text)


_locks: dict[str, threading.Semaphore] = {}


def run_one(run_id: str, editor: str, task: dict, rep: int, out_root: Path, episodes: int) -> dict:
    out = out_root / editor / task["id"] / f"r{rep}"
    if (out / "score.json").exists():
        return json.loads((out / "score.json").read_text())
    out.mkdir(parents=True, exist_ok=True)
    prompt = task["prompt_path"].read_text()
    work = editors.STATE / run_id / editor / task["id"] / f"r{rep}"
    with _locks[editor]:
        r = editors.call(editor, prompt, (EFFORT_OVERRIDE or EFFORT[task["family"]]), work, timeout_s=3600)
    (out / "reply.md").write_text(r.text)
    call = r.to_json() | {"task": task["id"], "rep": rep, "effort": (EFFORT_OVERRIDE or EFFORT[task["family"]]),
                          "prompt_sha256": hashlib.sha256(prompt.encode()).hexdigest(),
                          "logs_sha": LOGS_SHA, "finished_at": time.strftime("%Y-%m-%dT%H:%M:%S%z")}
    (out / "call.json").write_text(json.dumps(call, indent=1, default=str))
    completed = r.exit_code == 0 and r.tool_free and r.model_observed == r.model_requested
    sc = score(task, r.text, completed, out, episodes)
    sc |= {"editor": editor, "task": task["id"], "rep": rep, "valid": r.valid, "audit": r.audit_note,
           "usage": r.usage, "wall_s": round(r.wall_s, 1), "model_observed": r.model_observed,
           "finish": r.extra.get("finish_reason") or r.extra.get("stop_reason")}
    if r.exit_code == 0 and r.tool_free:
        # a completed, tool-free call is final even with an empty reply (e.g. it spent its whole output budget
        # reasoning): that is scored as no answer, never re-asked. Transport/auth failures are retried.
        (out / "score.json").write_text(json.dumps(sc, indent=1, default=str))
    return sc


def rescore(root: Path) -> None:
    """Re-apply the (fixed) read scorer to every frozen exp1/exp2 cell; record what changed."""
    ts = {t["id"]: t for t in tasks(fetch(), ["exp1", "exp2"])}
    for f in sorted(root.glob("*/exp[12]/*/r*/score.json")):
        sc = json.loads(f.read_text())
        if not sc.get("valid") and sc.get("correct") is None:
            continue
        new = score_read(ts[sc["task"]], (f.parent / "reply.md").read_text())
        if new["correct"] != sc.get("correct") or new["predicted"] != sc.get("predicted"):
            print(f"rescored {sc['editor']} {sc['task']}: {sc.get('predicted')}/{sc.get('correct')} -> "
                  f"{new['predicted']}/{new['correct']}")
            sc.setdefault("rescored", []).append({"from": {k: sc.get(k) for k in new}, "at": time.strftime("%F %T")})
        sc.update(new)
        f.write_text(json.dumps(sc, indent=1, default=str))


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--run-id", required=True)
    ap.add_argument("--editors", default=",".join(editors.EDITORS))
    ap.add_argument("--tasks", default="exp1,exp2,exp5")
    ap.add_argument("--reps", type=int, default=1)
    ap.add_argument("--episodes", type=int, default=1000)
    ap.add_argument("--per-editor", type=int, default=2, help="concurrent calls per editor")
    ap.add_argument("--only", default="", help="comma list of task ids to restrict to")
    ap.add_argument("--effort", default=None, help="override the paper's per-experiment effort for every cell")
    ap.add_argument("--rescore", action="store_true", help="re-score frozen read cells from reply.md and exit")
    a = ap.parse_args(argv)
    global EFFORT_OVERRIDE
    EFFORT_OVERRIDE = a.effort
    if a.rescore:
        return rescore(HERE / "results" / a.run_id)
    root = fetch()
    ts = tasks(root, a.tasks.split(","))
    if a.only:
        ts = [t for t in ts if t["id"] in a.only.split(",")]
    eds = a.editors.split(",")
    for e in eds:
        _locks[e] = threading.Semaphore(a.per_editor)
    out_root = HERE / "results" / a.run_id
    jobs = [(e, t, k) for k in range(1, a.reps + 1) for t in ts for e in eds]
    print(f"{len(jobs)} jobs -> {out_root}", flush=True)
    with ThreadPoolExecutor(len(eds) * a.per_editor) as ex:
        futs = {ex.submit(run_one, a.run_id, e, t, k, out_root, a.episodes): (e, t["id"], k) for e, t, k in jobs}
        for f in as_completed(futs):
            e, tid, k = futs[f]
            try:
                sc = f.result()
                show = sc.get("correct") if "correct" in sc else sc.get("mean")
                print(f"[{time.strftime('%H:%M:%S')}] {e:8s} {tid:16s} r{k} valid={sc['valid']} -> {show} "
                      f"({sc.get('predicted', '')}) {sc['wall_s']}s", flush=True)
            except Exception as ex_:  # noqa: BLE001 - one failed cell must not stop the grid
                print(f"[{time.strftime('%H:%M:%S')}] {e:8s} {tid:16s} r{k} ERROR {type(ex_).__name__}: {ex_}",
                      flush=True)


if __name__ == "__main__":
    main()
