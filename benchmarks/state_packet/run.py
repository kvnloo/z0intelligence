"""State Packet vs raw recall on frozen current-work questions (z0intelligence#22 / z0evals#56).

Arms (same model, same lean Claude Code flags, sessions never persisted):
  packet_only   — packet injected through the real SessionStart hook; NO tools.
  raw_tools     — no packet; read-only tools (Read/Grep/Glob + read-only git/ls/stat/...)
                  with ~/.claude/projects readable. This is "direct/raw recall".
  packet_tools  — packet via SessionStart hook AND the same read-only tools.

Scoring uses benchmarks/state_packet/questions.json (key frozen before any run).
Outputs are create-only JSONL outside the repo by default (answers may quote local state).

    .venv/bin/python benchmarks/state_packet/run.py --out ~/.z0int/research/.../run1.jsonl
    .venv/bin/python benchmarks/state_packet/run.py --summarize ~/.z0int/research/.../run1.jsonl
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import statistics
import subprocess
import sys
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
QUESTIONS = Path(__file__).with_name("questions.json")
PY = ROOT / ".venv" / "bin" / "python"
WORKSPACE = Path(os.environ.get("Z0_BENCH_WORKSPACE", "~/workspace")).expanduser()
PROJECTS = Path("~/.claude/projects").expanduser()

READ_ONLY_TOOLS = [
    "Read", "Grep", "Glob",
    "Bash(git log:*)", "Bash(git status:*)", "Bash(git branch:*)", "Bash(git worktree list:*)",
    "Bash(git show:*)", "Bash(git diff:*)", "Bash(git rev-parse:*)", "Bash(git for-each-ref:*)",
    "Bash(git rev-list:*)", "Bash(git stash list:*)", "Bash(git remote:*)", "Bash(git reflog:*)",
    "Bash(ls:*)", "Bash(stat:*)", "Bash(find:*)", "Bash(wc:*)", "Bash(head:*)", "Bash(tail:*)",
    "Bash(grep:*)", "Bash(rg:*)", "Bash(jq:*)", "Bash(cat:*)", "Bash(date:*)",
]
GH_READ_TOOLS = ["Bash(gh issue list:*)", "Bash(gh issue view:*)", "Bash(gh pr list:*)", "Bash(gh pr view:*)"]
LEAN = ["--setting-sources", "project", "--strict-mcp-config", "--disable-slash-commands"]
HISTORY_HINT = ("Local Claude Code conversation history (JSONL transcripts) lives under ~/.claude/projects/. "
                "Do not modify anything; read-only inspection only.")


def hook_settings(z0home: Path) -> str:
    gh = " Z0INT_PACKET_GH=1" if os.environ.get("Z0INT_PACKET_GH") == "1" else ""
    cmd = f"env Z0INT_HOME={z0home}{gh} {PY} -m z0int.state_packet --hook --max-tokens 1500"
    return json.dumps({"hooks": {"SessionStart": [{"hooks": [{"type": "command", "command": cmd, "timeout": 60}]}]}})


def run_claude(q: dict, arm: str, args: argparse.Namespace, contract: str, z0home: Path) -> dict:
    repo = WORKSPACE / q["repo"]
    prompt = (f"{q['prompt']}\n\nFields to fill: {json.dumps(q['fields'])}\n\n{contract}")
    cmd = ["claude", "-p", prompt, "--output-format", "stream-json", "--verbose", "--model", args.model,
           "--no-session-persistence", "--max-budget-usd", str(args.max_usd), *LEAN]
    if args.effort:
        cmd += ["--effort", args.effort]
    if arm in ("packet_only", "packet_tools"):
        cmd += ["--settings", hook_settings(z0home)]
    if arm == "packet_only":
        cmd += ["--tools", ""]
    else:
        tools = READ_ONLY_TOOLS + (GH_READ_TOOLS if getattr(args, "raw_gh", False) else [])
        cmd += ["--allowedTools", *tools, "--add-dir", str(PROJECTS),
                "--append-system-prompt", HISTORY_HINT]
    env = dict(os.environ, GIT_OPTIONAL_LOCKS="0")
    t0 = time.time()
    proc = subprocess.run(cmd, cwd=repo, env=env, capture_output=True, text=True, timeout=args.timeout,
                          stdin=subprocess.DEVNULL)
    wall = time.time() - t0
    tool_calls: dict[str, int] = {}
    tool_result_bytes = 0
    hook_ctx_bytes = 0
    result: dict = {}
    for line in proc.stdout.splitlines():
        try:
            ev = json.loads(line)
        except ValueError:
            continue
        if ev.get("type") == "assistant":
            for c in (ev.get("message") or {}).get("content") or []:
                if isinstance(c, dict) and c.get("type") == "tool_use":
                    tool_calls[c["name"]] = tool_calls.get(c["name"], 0) + 1
        elif ev.get("type") == "user":
            for c in (ev.get("message") or {}).get("content") or []:
                if isinstance(c, dict) and c.get("type") == "tool_result":
                    content = c.get("content")
                    tool_result_bytes += len(json.dumps(content) if not isinstance(content, str) else content)
        elif ev.get("type") == "system" and "hook" in str(ev.get("subtype", "")):
            out = json.dumps(ev)
            if "z0-state-packet" in out:
                hook_ctx_bytes = max(hook_ctx_bytes, len(out))
        elif ev.get("type") == "result":
            result = ev
    return {
        "wall_s": round(wall, 2),
        "returncode": proc.returncode,
        "result_text": result.get("result"),
        "usage": result.get("usage"),
        "cost_usd": result.get("total_cost_usd"),
        "num_turns": result.get("num_turns"),
        "duration_ms": result.get("duration_ms"),
        "session_id": result.get("session_id"),
        "tool_calls": tool_calls,
        "tool_result_bytes": tool_result_bytes,
        "hook_event_bytes": hook_ctx_bytes,
        "stderr_tail": proc.stderr[-400:] if proc.returncode else "",
        "result_subtype": result.get("subtype"),
        "is_error": result.get("is_error"),
    }


def parse_answer(text: str | None) -> dict | None:
    """First JSON object in the reply that has an ``action`` key (tolerates fences/prose)."""
    if not text:
        return None
    dec = json.JSONDecoder()
    for i, ch in enumerate(text):
        if ch != "{":
            continue
        try:
            obj, _ = dec.raw_decode(text[i:])
        except ValueError:
            continue
        if isinstance(obj, dict) and "action" in obj:
            return obj
    return None


def _flat(v) -> str:
    return json.dumps(v, ensure_ascii=False) if not isinstance(v, str) else v


def score(q: dict, ans: dict | None) -> dict:
    key = q["key"]
    if ans is None:
        return {"correct": False, "action_ok": False, "fields": {}, "why": "unparseable"}
    action = str(ans.get("action", "")).lower()
    action_ok = action == key["action"]
    fields = ans.get("fields") or {}
    res = {}
    for name, rule in key["fields"].items():
        v = fields.get(name)
        ok = False
        try:
            if "re" in rule:
                ok = v is not None and re.search(rule["re"], _flat(v).strip(), re.I) is not None
            elif "int" in rule:
                ok = v is not None and int(v) == rule["int"]
            elif "num" in rule:
                ok = v is not None and abs(float(v) - rule["num"]) <= rule["tol"]
            elif "bool" in rule:
                ok = (v if isinstance(v, bool) else str(v).lower() in ("true", "yes")) == rule["bool"] and v is not None
            elif "contains_all" in rule:
                flat = _flat(v)
                ok = v is not None and all(re.search(p, flat, re.I | re.S) for p in rule["contains_all"])
                if ok and rule.get("forbid_any"):
                    ok = not any(re.search(p, flat, re.I) for p in rule["forbid_any"])
        except (TypeError, ValueError):
            ok = False
        res[name] = ok
    correct = action_ok and all(res.values())
    return {"correct": correct, "action_ok": action_ok, "fields": res, "action": action}


def packet_stats(repo: Path, z0home: Path) -> dict:
    """Cold (no cache) and warm build of the packet the hook will inject."""
    env = dict(os.environ, Z0INT_HOME=str(z0home))
    out = {}
    for label, extra in (("cold", ["--no-cache"]), ("warm", [])):
        t0 = time.time()
        p = subprocess.run([str(PY), "-m", "z0int", "context", "packet", "--repo", str(repo), "--json", *extra],
                           capture_output=True, text=True, env=env, timeout=120)
        wall = (time.time() - t0) * 1000
        pkt = json.loads(p.stdout)
        m = pkt["measurements"]
        out[label] = {"process_wall_ms": round(wall, 1), "build_ms": m["wall_ms"], "cache_hit": m["cache_hit"],
                      "raw_source_reads": m["raw_source_reads"], "bytes_read": m["bytes_read"]}
        if label == "cold":
            out["packet_id"] = pkt["packet_id"]
            out["evidence_refs"] = [
                {"source_id": e["source_id"], "source_version": e["source_version"], "trust_class": e["trust_class"],
                 "locator_hash": hashlib.sha256(e["locator"].encode()).hexdigest()[:12]}
                for e in pkt["evidence"].values()]
            out["decision"] = pkt["decision"]["mode"]
    r = subprocess.run([str(PY), "-m", "z0int", "context", "packet", "--repo", str(repo), "--render"],
                       capture_output=True, text=True, env=env, timeout=120)
    out["rendered_bytes"] = len(r.stdout.encode())
    out["rendered_approx_tokens"] = int(len(r.stdout) / 3.5) + 1
    return out


def oracle() -> dict:
    """Independent plain-git probe of the key's structural facts (not using z0int.state_packet)."""
    env = dict(os.environ, GIT_OPTIONAL_LOCKS="0")

    def g(repo: str, *a: str) -> str:
        return subprocess.run(["git", "--no-optional-locks", "-C", str(WORKSPACE / repo), *a],
                              capture_output=True, text=True, env=env).stdout.strip()

    return {r: {"head": g(r, "rev-parse", "--short", "HEAD"), "branch": g(r, "branch", "--show-current"),
                "status": hashlib.sha256(g(r, "status", "--porcelain").encode()).hexdigest()[:12]}
            for r in ("z0intelligence", "z0evals", "aodl")}


def z0int_revision() -> str:
    return subprocess.run(["git", "-C", str(ROOT), "rev-parse", "--short", "HEAD"], capture_output=True,
                          text=True).stdout.strip()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out")
    ap.add_argument("--arms", nargs="+", default=["packet_only", "raw_tools", "packet_tools"])
    ap.add_argument("--only", nargs="*", default=None, help="question ids")
    ap.add_argument("--reps", type=int, default=1)
    ap.add_argument("--model", default="sonnet")
    ap.add_argument("--effort", default="low")
    ap.add_argument("--max-usd", type=float, default=1.0)
    ap.add_argument("--timeout", type=int, default=600)
    ap.add_argument("--jobs", type=int, default=3)
    ap.add_argument("--summarize", default=None)
    ap.add_argument("--gh", action="store_true", help="enable the network GitHub adapter in the packet")
    ap.add_argument("--raw-gh", action="store_true", help="also allow read-only gh issue/pr commands in tool arms")
    ap.add_argument("--questions", default=None, help="question/key file (default questions.json)")
    args = ap.parse_args()
    if args.gh:
        os.environ["Z0INT_PACKET_GH"] = "1"
    if args.summarize:
        summarize(Path(args.summarize).expanduser())
        return
    out = Path(args.out).expanduser()
    if out.exists():
        sys.exit("output exists (create-only)")
    out.parent.mkdir(parents=True, exist_ok=True)
    spec = json.loads(Path(args.questions).read_text() if args.questions else QUESTIONS.read_text())
    qs = [q for q in spec["questions"] if not args.only or q["id"] in args.only]
    z0home = out.with_suffix(".z0home")
    z0home.mkdir(parents=True, exist_ok=True)
    before = oracle()
    stats = {r: packet_stats(WORKSPACE / r, z0home) for r in sorted({q["repo"] for q in qs})}
    run_id = uuid.uuid4().hex[:10]
    rev = z0int_revision()
    plan = [(q, arm, rep) for rep in range(args.reps) for q in qs
            for arm in (args.arms if rep % 2 == 0 else list(reversed(args.arms)))]
    lock = threading.Lock()
    with out.open("x") as fh:
        fh.write(json.dumps({"type": "meta", "run_id": run_id, "z0int_revision": rev, "model": args.model,
                             "github_adapter": bool(args.gh), "raw_gh": bool(args.raw_gh),
                             "questions": str(args.questions or QUESTIONS.name),
                             "effort": args.effort, "oracle_before": before, "packet_stats": stats,
                             "started_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}) + "\n")

        def one(item):
            q, arm, rep = item
            r = run_claude(q, arm, args, spec["answer_contract"], z0home)
            ans = parse_answer(r["result_text"])
            sc = score(q, ans)
            ps = stats[q["repo"]]
            packet_arm = arm != "raw_tools"
            tool_n = sum(r["tool_calls"].values())
            u = r["usage"] or {}
            row = {
                "type": "trial", "schema": "z0eval.unified_memory_receipt.v0+claude_code", "harness": "claude_code",
                "arm": arm, "rep": rep, "question_id": q["id"], "kind": q["kind"], "repo": q["repo"],
                "trace_id": f"{run_id}-{q['id']}-{arm}-{rep}", "session_id": r["session_id"],
                "z0int_revision": rev,
                "retrieval_capability": "z0int.state_packet.v0" + ("+read_only_tools" if arm == "packet_tools" else "")
                if packet_arm else "raw_read_only_tools",
                "retrieval_ok": True, "injected": packet_arm,
                "answer": ans, "score": sc, "answer_supported": sc["correct"],
                "abstained": (ans or {}).get("action") == "abstain",
                "verified": False,
                "evidence_refs": ps["evidence_refs"] if packet_arm else [],
                "latency_ms": round(r["wall_s"] * 1000 + (ps["warm"]["build_ms"] if packet_arm else 0), 1),
                "model_wall_s": r["wall_s"],
                "context_bytes": (ps["rendered_bytes"] if packet_arm else 0) + r["tool_result_bytes"],
                "raw_source_reads": (ps["cold"]["raw_source_reads"] if packet_arm else 0) + tool_n,
                "agent_tool_calls": tool_n, "tool_calls": r["tool_calls"],
                "tool_result_bytes": r["tool_result_bytes"],
                "input_tokens_total": (u.get("input_tokens") or 0) + (u.get("cache_read_input_tokens") or 0)
                + (u.get("cache_creation_input_tokens") or 0),
                "output_tokens": u.get("output_tokens"), "cost_usd": r["cost_usd"], "num_turns": r["num_turns"],
                "hook_seen": r["hook_event_bytes"] > 0, "returncode": r["returncode"], "stderr_tail": r["stderr_tail"],
                "result_subtype": r["result_subtype"], "is_error": r["is_error"],
                "result_text": (r["result_text"] or "")[:3000],
            }
            with lock:
                fh.write(json.dumps(row) + "\n")
                fh.flush()
                print(q["id"], arm, rep, "OK" if sc["correct"] else "X", sc.get("action"), tool_n,
                      r["cost_usd"], flush=True)

        with ThreadPoolExecutor(max_workers=args.jobs) as pool:
            list(pool.map(one, plan))
        after = oracle()
        drift = {r: [k for k in ("head", "branch", "status") if before[r][k] != after[r][k]] for r in before}
        fh.write(json.dumps({"type": "meta_end", "oracle_after": after, "drift": drift,
                             "ended_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}) + "\n")
    summarize(out)


def summarize(path: Path) -> None:
    rows = [json.loads(line) for line in path.read_text().splitlines()]
    trials = [r for r in rows if r.get("type") == "trial"]
    arms = sorted({t["arm"] for t in trials})
    print(f"\n{path.name}: {len(trials)} trials")
    hdr = f"{'arm':14} {'correct':>8} {'abst_ok':>8} {'reads':>7} {'tools':>6} {'ctx_B':>8} {'in_tok':>8} {'lat_s':>6} {'cost$':>7}"
    print(hdr)
    for a in arms:
        ts = [t for t in trials if t["arm"] == a]
        ab = [t for t in ts if t["kind"] == "abstention"]
        med = lambda k: statistics.median([t[k] or 0 for t in ts])  # noqa: E731
        print(f"{a:14} {sum(t['answer_supported'] for t in ts):>4}/{len(ts):<3} "
              f"{sum(t['answer_supported'] for t in ab):>4}/{len(ab):<3} {med('raw_source_reads'):>7.0f} "
              f"{med('agent_tool_calls'):>6.0f} {med('context_bytes'):>8.0f} {med('input_tokens_total'):>8.0f} "
              f"{med('latency_ms')/1000:>6.1f} {sum(t['cost_usd'] or 0 for t in ts):>7.3f}")
    print("\nmeans per arm (all trials): tool calls, tool-result bytes, input tokens, latency s, action_ok")
    for a in arms:
        ts = [t for t in trials if t["arm"] == a]
        mean = lambda k: statistics.mean([t[k] or 0 for t in ts])  # noqa: E731
        print(f"  {a:14} tools={mean('agent_tool_calls'):.1f} tool_B={mean('tool_result_bytes'):.0f} "
              f"ctx_B={mean('context_bytes'):.0f} in_tok={mean('input_tokens_total'):.0f} "
              f"lat={mean('latency_ms')/1000:.1f} action_ok={sum(t['score'].get('action_ok', False) for t in ts)}/{len(ts)} "
              f"proto_fail={sum(t['answer'] is None for t in ts)}")
    print("\nper question: correct | mean tool calls | mean tool-result bytes | mean input tokens | mean latency s")
    for qid in sorted({t["question_id"] for t in trials}):
        cells = []
        for a in arms:
            ts = [t for t in trials if t["arm"] == a and t["question_id"] == qid]
            if not ts:
                continue
            m = lambda k: statistics.mean([t[k] or 0 for t in ts])  # noqa: E731
            cells.append(f"{a}={sum(t['answer_supported'] for t in ts)}/{len(ts)}|{m('agent_tool_calls'):.1f}|"
                         f"{m('tool_result_bytes'):.0f}|{m('input_tokens_total'):.0f}|{m('latency_ms')/1000:.1f}")
        print(f"  {qid:34} " + "  ".join(cells))


if __name__ == "__main__":
    main()
