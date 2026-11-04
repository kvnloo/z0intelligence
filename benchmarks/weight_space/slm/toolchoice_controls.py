"""Cheap no-model controls for the tool-choice task (the bar a weight edit must clear).

  python toolchoice_controls.py        # tune on practice, score on dev -> <run-free> data file

Arms:
  always_none     never call a tool
  lexical         pick the tool whose name+description shares the most request words (IDF-weighted), always call
  lexical+abstain same, but answer "none" when the best overlap is below a threshold tuned on practice
Scores use toolchoice.score (accuracy, false_call, near_miss, missed_call). Public data only.
"""
from __future__ import annotations

import json
import math
import re
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import toolchoice as T  # noqa: E402

WORD = re.compile(r"[a-z][a-z0-9]+")
STOP = set("the a an of to in for and or is are be on with by from at as it this that what how can i me my you your "
           "please get set do does use using want need".split())


def words(s: str) -> list[str]:
    s = re.sub(r"([a-z])([A-Z])", r"\1 \2", s or "").replace("_", " ").replace(".", " ").lower()
    return [w for w in WORD.findall(s) if w not in STOP]


def tool_text(t: dict) -> str:
    props = (t.get("parameters") or {}).get("properties") or {}
    return " ".join([t["name"], t.get("description", ""), *props.keys()])


def idf(items: list[dict]) -> dict:
    df = Counter()
    n = 0
    for it in items:
        for t in it["tools"]:
            df.update(set(words(tool_text(t))))
            n += 1
    return {w: math.log((n + 1) / (c + 1)) + 1 for w, c in df.items()}


def best(it: dict, w: dict) -> tuple[str, float]:
    q = set(words(it["user"]))
    if not it["tools"]:
        return T.NONE, float("-inf")
    scored = []
    for t in it["tools"]:
        tw = set(words(tool_text(t)))
        s = sum(w.get(x, 1.0) for x in q & tw) / (1 + math.sqrt(len(tw)))
        scored.append((s, t["name"]))
    s, name = max(scored)
    return name, s


def run(items, w, thr):
    rows = []
    for it in items:
        name, s = best(it, w)
        rows.append({"kind": it["kind"], "gold": it["gold"], "pred": name if s >= thr else T.NONE})
    return rows


def main():
    pr, dv = T.load("practice"), T.load("dev")
    w = idf(pr)
    grid = sorted({round(best(it, w)[1], 3) for it in pr if it["tools"]})
    thr = max(grid, key=lambda g: T.score(run(pr, w, g))["accuracy_pct"])
    res = {"data": T.REV, "tuned_on": "practice", "scored_on": "dev", "abstain_threshold": thr,
           "arms": {"always_none": T.score([{"kind": i["kind"], "gold": i["gold"], "pred": T.NONE} for i in dv]),
                    "lexical": T.score(run(dv, w, -1)),
                    "lexical+abstain": T.score(run(dv, w, thr))},
           "practice_lexical+abstain": T.score(run(pr, w, thr)),
           "items_with_no_tools": {s: sum(not i["tools"] for i in T.load(s)) for s in ("practice", "dev")}}
    out = Path("/mnt/zer0models/worktrees/z0evals-tiny-slm/studies/tiny-slm-gym-v0/data/toolchoice-controls-dev.json")
    out.write_text(json.dumps(res, indent=1) + "\n")
    for k, v in res["arms"].items():
        print(f"{k:16s} acc {v['accuracy_pct']:5.1f}  false_call {v['false_call_pct']}  near_miss {v['near_miss_pct']}  "
              f"missed_call {v['missed_call_pct']}")
    print("no-tool items", res["items_with_no_tools"], "threshold", thr, "practice acc", res["practice_lexical+abstain"]["accuracy_pct"])


if __name__ == "__main__":
    main()
