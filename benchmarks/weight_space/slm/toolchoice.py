"""Public tool-choice task for the FunctionGemma retarget (tiny-slm-gym-v0 queue item 00).

FunctionGemma-270M's measured z0 failures (slm-router bounded choice, 51/84): calling a tool when none fits, and
picking a dangerous near-miss among similar tools. This builds a PUBLIC proxy from BFCL v3 at a pinned revision:
  - abstain items: BFCL irrelevance + live_irrelevance (gold = no call)
  - choose items:  BFCL multiple + live_multiple (several similar functions; gold = the one function name)
Only the tool *choice* is graded (name or "none"), matching z0's bounded-choice decision, not argument filling.
Split is a fixed hash of the item id: practice 50% / dev 25% / sealed 25%. The editor sees practice only.

  python toolchoice.py build      # download pinned files, write items + split counts under WSP_TOOLCHOICE_DIR
  python toolchoice.py selftest   # grader unit checks (no model)
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import sys
from pathlib import Path

REPO = "gorilla-llm/Berkeley-Function-Calling-Leaderboard"
REV = "61fc0608cfd831fcfbbaa676ebdfef0ed963eeda"
ABSTAIN = ("BFCL_v3_irrelevance.json", "BFCL_v3_live_irrelevance.json")
CHOOSE = ("BFCL_v3_multiple.json", "BFCL_v3_live_multiple.json")
OUT = Path(os.environ.get("WSP_TOOLCHOICE_DIR", "/mnt/zer0models/zer0-tier/datasets/toolchoice-bfcl-v3"))
NONE = "none"


def _jsonl(path: str) -> list[dict]:
    return [json.loads(x) for x in Path(path).read_text().splitlines() if x.strip()]


def split_of(item_id: str) -> str:
    h = int(hashlib.sha256(item_id.encode()).hexdigest()[:8], 16) % 4
    return "practice" if h < 2 else ("dev" if h == 2 else "sealed")


def _user_text(question) -> str:
    turns = question[0] if question and isinstance(question[0], list) else question
    users = [t["content"] for t in turns if t.get("role") == "user"]
    return users[-1] if users else ""


def build() -> dict:
    from huggingface_hub import hf_hub_download  # noqa: PLC0415
    items = []
    for fn in ABSTAIN + CHOOSE:
        rows = _jsonl(hf_hub_download(REPO, fn, repo_type="dataset", revision=REV))
        gold = {}
        if fn in CHOOSE:
            for r in _jsonl(hf_hub_download(REPO, "possible_answer/" + fn, repo_type="dataset", revision=REV)):
                gt = r["ground_truth"]
                gold[r["id"]] = list(gt[0].keys())[0] if gt else None
        for r in rows:
            tools = r["function"]
            if len(tools) < 2 and fn in CHOOSE:
                continue
            g = NONE if fn in ABSTAIN else gold.get(r["id"])
            if g is None:
                continue
            iid = f"{fn.removesuffix('.json')}:{r['id']}"
            items.append({"id": iid, "kind": "abstain" if g == NONE else "choose", "user": _user_text(r["question"]),
                          "tools": tools, "gold": g, "split": split_of(iid)})
    OUT.mkdir(parents=True, exist_ok=True)
    with (OUT / "items.jsonl").open("w") as f:
        for it in items:
            f.write(json.dumps(it) + "\n")
    counts: dict = {}
    for it in items:
        counts.setdefault(it["split"], {}).setdefault(it["kind"], 0)
        counts[it["split"]][it["kind"]] += 1
    meta = {"repo": REPO, "revision": REV, "n": len(items), "counts": counts,
            "sha256": hashlib.sha256((OUT / "items.jsonl").read_bytes()).hexdigest()}
    (OUT / "meta.json").write_text(json.dumps(meta, indent=1) + "\n")
    return meta


def load(split: str) -> list[dict]:
    return [it for it in _jsonl(str(OUT / "items.jsonl")) if it["split"] == split]


def to_openai_tools(tools: list[dict]) -> list[dict]:
    out = []
    for t in tools:
        params = json.loads(json.dumps(t.get("parameters", {})).replace('"dict"', '"object"').replace(
            '"float"', '"number"').replace('"tuple"', '"array"').replace('"any"', '"string"'))
        out.append({"type": "function", "function": {"name": t["name"], "description": t.get("description", ""),
                                                      "parameters": params}})
    return out


SYSTEM = ("You are a model that can do function calling with the following functions. Call a function only if one "
          "of them fits the request; otherwise answer in plain text without calling any function.")
_CALL = re.compile(r"<start_function_call>\s*call:([A-Za-z0-9_.\-]+)")


def predicted(text: str, tool_names: list[str]) -> str:
    """Name of the first function FunctionGemma called, 'none' if no call, 'invalid:<x>' for an unknown name."""
    m = _CALL.search(text or "")
    if not m:
        return NONE
    name = m.group(1)
    return name if name in tool_names else f"invalid:{name}"


def score(rows: list[dict]) -> dict:
    """rows: {"kind","gold","pred"}. Overall/abstain/choose accuracy plus the two named failure rates."""
    def acc(rs):
        return round(100 * sum(r["pred"] == r["gold"] for r in rs) / len(rs), 1) if rs else None
    ab = [r for r in rows if r["kind"] == "abstain"]
    ch = [r for r in rows if r["kind"] == "choose"]
    return {"n": len(rows), "accuracy_pct": acc(rows), "abstain_acc_pct": acc(ab), "choose_acc_pct": acc(ch),
            "false_call_pct": round(100 * sum(r["pred"] != NONE for r in ab) / len(ab), 1) if ab else None,
            "near_miss_pct": round(100 * sum(r["pred"] not in (NONE, r["gold"]) for r in ch) / len(ch), 1) if ch else None,
            "missed_call_pct": round(100 * sum(r["pred"] == NONE for r in ch) / len(ch), 1) if ch else None}


def selftest() -> None:
    names = ["get_weather", "get_time"]
    assert predicted("<start_function_call>call:get_weather{city:<escape>Paris<escape>}<end_function_call>", names) == "get_weather"
    assert predicted("I can't help with that.", names) == NONE
    assert predicted("<start_function_call>call:delete_all{}<end_function_call>", names) == "invalid:delete_all"
    s = score([{"kind": "abstain", "gold": NONE, "pred": "get_time"}, {"kind": "abstain", "gold": NONE, "pred": NONE},
               {"kind": "choose", "gold": "get_time", "pred": "get_weather"}, {"kind": "choose", "gold": "get_time", "pred": "get_time"}])
    assert s["accuracy_pct"] == 50.0 and s["false_call_pct"] == 50.0 and s["near_miss_pct"] == 50.0, s
    assert {split_of(f"x:{i}") for i in range(200)} == {"practice", "dev", "sealed"}
    print("selftest ok")


if __name__ == "__main__":
    {"build": lambda: print(json.dumps(build(), indent=1)), "selftest": selftest}[sys.argv[1]]()
