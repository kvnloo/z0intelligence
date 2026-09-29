#!/usr/bin/env python3
"""Per-turn OMP decision extractor — Decision Dataset v2.

Source: /workspace/kvn-home/.omp/agent/sessions/**/*.jsonl (2,477 files, 1.3 GB).

Unlike ~/.z0int/episodes/next_action.jsonl, these files contain the FULL per-turn
transcript, so the decision-time state can be reconstructed properly:

    user request  +  recent assistant text  +  tool history  ->  observed action

and each action is paired with its outcome through `toolCallId` -> the following
`toolResult` message. That pairing is what makes the gold outcome-backed rather
than a model's opinion.

Record shapes used
    {"type":"message","message":{"role":"user"|"assistant"|"toolResult",...}}
      assistant content blocks: text | thinking | toolCall
      toolResult: {"toolName":..,"toolCallId":..,"content":[{"type":"text",...}]}
    {"type":"compaction","tokensBefore":..,"tokensAfter":..}
    {"type":"model_usage","provider":..,"model":..,"usage":{..},"stopReason":..}

Everything is emitted keyed by session so splits are grouped, never random by row.
"""
from __future__ import annotations

import collections
import hashlib
import json
import re
from pathlib import Path

SESSIONS = Path("/workspace/kvn-home/.omp/agent/sessions")
REPO = Path(__file__).resolve().parents[2]
OUT = REPO / "results" / "julia-claims" / "decision-dataset-v2"
OUT.mkdir(parents=True, exist_ok=True)

#: Frozen tool -> coarse family mapping. Derived from the tool names observed in
#: this corpus plus the family vocabulary of the declared `next_action_family`
#: capability card. Kept explicit and versioned so it can be hashed.
TOOL_FAMILY = {
    "bash": "EXECUTE", "launch": "EXECUTE", "process": "EXECUTE", "execute_code": "EXECUTE",
    "read": "READ_SEARCH", "grep": "READ_SEARCH", "glob": "READ_SEARCH",
    "search_files": "READ_SEARCH", "read_file": "READ_SEARCH", "skill_view": "READ_SEARCH",
    "session_search": "READ_SEARCH", "list": "READ_SEARCH",
    "edit": "EDIT", "write": "EDIT", "patch": "EDIT", "write_file": "EDIT",
    "todo": "DELEGATE", "kanban_show": "DELEGATE", "kanban_create": "DELEGATE",
    "kanban_comment": "DELEGATE", "hub": "DELEGATE", "task": "DELEGATE",
    "web_search": "WEB", "web_extract": "WEB", "browser_navigate": "WEB",
    "vision_analyze": "WEB", "fetch": "WEB",
    "eval": "VERIFY", "browser_console": "VERIFY", "verify": "VERIFY",
    "ask": "RESPOND", "respond": "RESPOND",
}
ERROR_RE = re.compile(
    r"\b(error|failed|failure|traceback|exception|not found|no such file|timed out|"
    r"permission denied|exit code [1-9]|command not found)\b", re.I)
OK_RE = re.compile(r"\b(success|ok|passed|completed|done)\b", re.I)

RECENT_TOOLS = 4
RECENT_CHARS = 600


def outcome_of(text: str) -> str:
    """Observable outcome from the tool result text. Conservative: unknown unless clear."""
    if not text:
        return "unknown"
    if ERROR_RE.search(text):
        return "failure"
    if OK_RE.search(text):
        return "success"
    return "unclear"


def blocks(msg):
    c = msg.get("content")
    return c if isinstance(c, list) else []


def text_of(msg, limit=RECENT_CHARS):
    out = []
    for b in blocks(msg):
        if isinstance(b, dict) and b.get("type") == "text":
            out.append(str(b.get("text") or ""))
    return " ".join(out)[:limit]


def extract_file(path: Path):
    """Yield decision rows and side records from one session file."""
    rows, compactions, usages = [], [], []
    session_id = None
    user_request = ""
    tool_history = []
    last_assistant_text = ""
    pending = {}          # toolCallId -> (tool, turn)
    turn = 0
    try:
        lines = path.read_text(errors="ignore").splitlines()
    except OSError:
        return rows, compactions, usages
    for line in lines:
        if not line.strip():
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        t = rec.get("type")
        if t == "session":
            session_id = rec.get("id") or path.stem
            continue
        if t == "compaction":
            compactions.append({
                "session": session_id or path.stem,
                "ts": rec.get("timestamp"),
                "tokens_before": rec.get("tokensBefore"),
                "tokens_after": rec.get("tokensAfter"),
                "method": rec.get("method"),
                "target": str(rec.get("target") or "")[:120],
            })
            continue
        if t == "model_usage":
            usages.append({
                "session": session_id or path.stem,
                "ts": rec.get("timestamp"),
                "provider": rec.get("provider"), "model": rec.get("model"),
                "purpose": rec.get("purpose"), "stop_reason": rec.get("stopReason"),
                "error": rec.get("errorMessage"),
                "input": (rec.get("usage") or {}).get("input"),
                "output": (rec.get("usage") or {}).get("output"),
            })
            continue
        if t != "message":
            continue
        msg = rec.get("message") or {}
        role = msg.get("role")
        if role == "user":
            txt = text_of(msg, 4000)
            if txt.strip():
                user_request = txt.strip()
            continue
        if role == "assistant":
            last_assistant_text = text_of(msg)
            for b in blocks(msg):
                if not isinstance(b, dict) or b.get("type") != "toolCall":
                    continue
                tool = b.get("name") or b.get("toolName") or (b.get("function") or {}).get("name")
                call_id = b.get("id") or b.get("toolCallId")
                turn += 1
                args = b.get("arguments") or b.get("input") or {}
                rows.append({
                    "session": session_id or path.stem,
                    "turn": turn,
                    "ts": rec.get("timestamp"),
                    "tool": tool,
                    "family": TOOL_FAMILY.get(str(tool), "UNMAPPED"),
                    "user_request": user_request,
                    "recent_assistant": last_assistant_text,
                    "tool_history": list(tool_history[-RECENT_TOOLS:]),
                    "args_sha256": hashlib.sha256(
                        json.dumps(args, sort_keys=True, default=str).encode()).hexdigest()[:16],
                    "call_id": call_id,
                })
                pending[call_id] = turn
                tool_history.append(str(tool))
            continue
        if role == "toolResult":
            call_id = msg.get("toolCallId")
            res = text_of(msg, 4000)
            rows.append({
                "session": session_id or path.stem,
                "turn": pending.get(call_id, -1),
                "ts": rec.get("timestamp"),
                "tool": msg.get("toolName"),
                "outcome": outcome_of(res),
                "result_chars": len(res),
                "_is_result": True,
            })
        if role == "developer":
            continue
    return rows, compactions, usages


def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0, help="max session files (0 = all)")
    args = ap.parse_args()

    files = sorted(SESSIONS.rglob("*.jsonl"))
    # skip the nested render/aux directories that are not conversations
    files = [f for f in files if f.parent.name != f.stem.replace(".jsonl", "")]
    if args.limit:
        files = files[:args.limit]
    print(f"session files: {len(files)}")

    actions = (OUT / "actions.jsonl").open("w")
    compacts = (OUT / "compactions.jsonl").open("w")
    usages_f = (OUT / "model_usage.jsonl").open("w")
    n_actions = n_comp = n_use = 0
    sessions = set()
    tool_counts = collections.Counter()
    fam_counts = collections.Counter()
    for i, f in enumerate(files, 1):
        rows, comps, uses = extract_file(f)
        tool_outcomes = {}
        for r in rows:
            if r.get("_is_result"):
                # attach outcome to the matching action row
                key = (r["session"], r["turn"], r["tool"])
                tool_outcomes.setdefault(key, collections.Counter())[r["outcome"]] += 1
                continue
            sessions.add(r["session"])
            k = (r["session"], r["turn"], r["tool"])
            oc = tool_outcomes.get(k)
            r["outcome"] = (oc.most_common(1)[0][0] if oc else "missing")
            actions.write(json.dumps(r, sort_keys=True) + "\n")
            n_actions += 1
            tool_counts[r["tool"]] += 1
            fam_counts[r["family"]] += 1
        for c in comps:
            compacts.write(json.dumps(c, sort_keys=True) + "\n"); n_comp += 1
        for u in uses:
            usages_f.write(json.dumps(u, sort_keys=True) + "\n"); n_use += 1
        if i % 250 == 0:
            print(f"  {i}/{len(files)}  actions={n_actions} compactions={n_comp} usage={n_use}",
                  flush=True)
    for h in (actions, compacts, usages_f):
        h.close()

    from z0_capabilities import contract as _unused  # noqa: F401  (import check)
    summary = {
        "schema": "z0int.decision_dataset_v2.v1",
        "session_files_scanned": len(files),
        "sessions": len(sessions),
        "action_rows": n_actions,
        "compaction_rows": n_comp,
        "model_usage_rows": n_use,
        "tool_counts": dict(tool_counts.most_common()),
        "family_counts": dict(fam_counts.most_common()),
        "tool_family_mapping_sha256": hashlib.sha256(
            json.dumps(TOOL_FAMILY, sort_keys=True).encode()).hexdigest(),
        "outcome_rule": "toolResult text matched against ERROR_RE / OK_RE; 'unclear' otherwise",
        "state_fields": ["user_request", "recent_assistant", "tool_history"],
    }
    (OUT / "inventory.json").write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n")
    print(json.dumps({k: v for k, v in summary.items()
                      if k not in ("tool_counts", "family_counts")}, indent=2))
    print("family_counts:", summary["family_counts"])
    print(f"wrote {OUT}/inventory.json")


if __name__ == "__main__":
    main()
