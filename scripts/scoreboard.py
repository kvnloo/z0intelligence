#!/usr/bin/env python3
"""Frontier tokens per merged, cross-checked PR. Read-only: no store, no service.

Agent sessions are joined to pull requests by git branch:

  Hermes  <hermes-home>/state.db and profiles/*/state.db   sessions.git_branch, else the
          branch checked out at sessions.cwd; delegated children inherit their parent's
  Codex   ~/.codex/sessions/**/*.jsonl                      session_meta git.branch or cwd
  Claude  $CLAUDE_CONFIG_DIR, ~/.claude, ~/.claude-home     gitBranch per line, else the branch
          (projects/**/*.jsonl, including subagents)        checked out at that line's cwd

A merged PR counts as cross-checked when one of its comments starts with
"VERDICT: ACCEPT" and names the merged head SHA on a "head:" line.

Token fields: fresh = uncached input + cache writes + output (reasoning is part of
output); cached = cache reads. Sessions on branches with no PR stay unattributed.
Claude streamed messages use the last valid usage observation for each message id.
"""
from __future__ import annotations

import argparse
import calendar
import glob
import json
import os
import re
import sqlite3
import subprocess
import sys
import time
from collections import defaultdict
from pathlib import Path

REPO = "kvnloo/z0intelligence"
SKIP_BRANCHES = {"", "HEAD", "main", "master", "dev", "preview", "nightly"}
VERDICT = re.compile(r"^\s*VERDICT:\s*ACCEPT\b", re.I)
HEAD_LINE = re.compile(r"^\s*head:\s*([0-9a-f]{40})\b", re.I | re.M)


def _branch_at(cwd: str | None, cache: dict) -> str:
    if not cwd:
        return ""
    if cwd not in cache:
        try:
            out = subprocess.run(["git", "-C", cwd, "rev-parse", "--abbrev-ref", "HEAD"],
                                 capture_output=True, text=True, timeout=5)
            cache[cwd] = out.stdout.strip() if out.returncode == 0 else ""
        except (OSError, subprocess.TimeoutExpired):
            cache[cwd] = ""
    return cache[cwd]


def _row(harness, sid, branch, start, fresh_in, cache_write, out, cached):
    return {"harness": harness, "id": sid, "branch": branch or "", "start": start,
            "fresh": int(fresh_in or 0) + int(cache_write or 0) + int(out or 0), "cached": int(cached or 0)}


def hermes_sessions(dbs: list[str], since: float) -> list[dict]:
    rows, seen, cache = [], set(), {}
    for db in dbs:
        real = os.path.realpath(db)
        if real in seen or not os.path.exists(real) or os.path.getsize(real) == 0:
            continue
        seen.add(real)
        con = sqlite3.connect(f"file:{real}?mode=ro", uri=True)
        try:
            data = con.execute(
                "select id, parent_session_id, git_branch, cwd, started_at, input_tokens, cache_write_tokens,"
                " output_tokens, cache_read_tokens from sessions where started_at >= ?", (since,)).fetchall()
        except sqlite3.Error:
            data = []
        finally:
            con.close()
        own = {r[0]: (r[2] or _branch_at(r[3], cache)) for r in data}
        parent = {r[0]: r[1] for r in data}
        for sid, _, _, _, start, fin, cw, out, cr in data:
            branch, hop, cur = own.get(sid, ""), 0, sid
            while not branch and parent.get(cur) and hop < 8:
                cur, hop = parent[cur], hop + 1
                branch = own.get(cur, "")
            rows.append(_row("hermes", sid, branch, start, fin, cw, out, cr))
    return rows


def _last_line_with(path: str, needle: str, chunk: int = 262144) -> str | None:
    with open(path, "rb") as fh:
        fh.seek(0, os.SEEK_END)
        end = fh.tell()
        fh.seek(max(0, end - chunk))
        for line in reversed(fh.read().decode("utf-8", "replace").splitlines()):
            if needle in line:
                return line
    return None


def codex_sessions(root: str, since: float) -> list[dict]:
    rows, cache = [], {}
    for path in glob.glob(os.path.join(root, "**", "*.jsonl"), recursive=True):
        if os.path.getmtime(path) < since:
            continue
        try:
            with open(path) as fh:
                meta = json.loads(fh.readline()).get("payload") or {}
            last = _last_line_with(path, '"token_count"')
            usage = ((json.loads(last).get("payload") or {}).get("info") or {}).get("total_token_usage") if last else None
        except (OSError, ValueError, AttributeError):
            continue
        if not usage:
            continue
        branch = (meta.get("git") or {}).get("branch") or _branch_at(meta.get("cwd"), cache)
        cached = usage.get("cached_input_tokens") or 0
        rows.append(_row("codex", meta.get("id") or path, branch, os.path.getmtime(path),
                         (usage.get("input_tokens") or 0) - cached, usage.get("cache_write_input_tokens"),
                         usage.get("output_tokens"), cached))
    return rows


def claude_sessions(roots: list[str], since: float) -> list[dict]:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
    from z0int.claude_code import USAGE_KEYS

    rows, cache = [], {}
    # Include nested workflows and subagents whose parent transcript was removed.
    paths = {os.path.realpath(p) for root in roots for p in Path(root).rglob("*.jsonl")}
    for path in sorted(paths):
        latest = {}
        try:
            modified = os.path.getmtime(path)
            if modified < since:
                continue
            with open(path, encoding="utf-8", errors="replace") as fh:
                for line_number, line in enumerate(fh):
                    if '"usage"' not in line:
                        continue
                    try:
                        item = json.loads(line)
                    except ValueError:
                        continue
                    msg = item.get("message") if isinstance(item, dict) else None
                    if not isinstance(msg, dict):
                        continue
                    usage, mid = msg.get("usage") or {}, msg.get("id")
                    if not isinstance(usage, dict) or not usage:
                        continue
                    if mid is not None and not isinstance(mid, str):
                        continue
                    amounts = {key: 0 if usage.get(key) is None else usage[key] for key in USAGE_KEYS}
                    if any(type(value) is not int or value < 0 for value in amounts.values()):
                        continue
                    branch = item.get("gitBranch") or ""
                    if not isinstance(branch, str):
                        continue
                    if branch in SKIP_BRANCHES:  # detached or default: use the checkout at that line's cwd
                        cwd = item.get("cwd")
                        branch = _branch_at(cwd if isinstance(cwd, str) else None, cache) or branch
                    key = ("id", mid) if isinstance(mid, str) and mid else ("line", line_number)
                    latest[key] = (branch, amounts)
        except OSError:
            continue
        per_branch = defaultdict(lambda: dict.fromkeys(USAGE_KEYS, 0))
        for branch, usage in latest.values():
            for key in USAGE_KEYS:
                per_branch[branch][key] += usage[key]
        for branch, usage in per_branch.items():
            rows.append(_row("claude", f"{Path(path).stem}:{branch}", branch, modified,
                             usage["input_tokens"], usage["cache_creation_input_tokens"],
                             usage["output_tokens"], usage["cache_read_input_tokens"]))
    return rows


def gh_json(args: list[str]):
    return json.loads(subprocess.run(["gh", *args], capture_output=True, text=True, check=True).stdout)


def cross_checked(pr: dict, comments: list[dict]) -> bool:
    head = (pr.get("headRefOid") or "").lower()
    for c in comments:
        body = c.get("body") or ""
        match = HEAD_LINE.search(body)
        if VERDICT.match(body) and match and match.group(1).lower() == head:
            return True
    return False


def _utc_stamp(value: str | None) -> float:
    return calendar.timegm(time.strptime(value[:19], "%Y-%m-%dT%H:%M:%S")) if value else 0


def score(prs: list[dict], sessions: list[dict], comments: dict, since: float) -> dict:
    by_branch = defaultdict(lambda: {"fresh": 0, "cached": 0, "sessions": 0})
    for s in sessions:
        if s["branch"] not in SKIP_BRANCHES:
            b = by_branch[s["branch"]]
            b["fresh"] += s["fresh"]; b["cached"] += s["cached"]; b["sessions"] += 1
    merged = [p for p in prs if p.get("mergedAt") and _utc_stamp(p["mergedAt"]) >= since]
    closed = [p for p in prs if p.get("closedAt") and not p.get("mergedAt") and _utc_stamp(p["closedAt"]) >= since]
    rows = []
    for p in merged:
        t = by_branch.get(p["headRefName"], {"fresh": 0, "cached": 0, "sessions": 0})
        rows.append({"number": p["number"], "branch": p["headRefName"], "cross_checked": cross_checked(p, comments.get(p["number"], [])), **t})
    checked = [r for r in rows if r["cross_checked"]]
    pr_branches = {p["headRefName"] for p in prs}
    total = sum(s["fresh"] for s in sessions)
    attributed = sum(s["fresh"] for s in sessions if s["branch"] in pr_branches and s["branch"] not in SKIP_BRANCHES)
    per = lambda rs: round(sum(r["fresh"] for r in rs) / len(rs)) if rs else None
    return {"since": time.strftime("%Y-%m-%d", time.gmtime(since)), "merged": len(merged), "cross_checked": len(checked),
            "fresh_tokens_per_merged_pr": per(rows), "fresh_tokens_per_cross_checked_pr": per(checked),
            "open_prs": sum(1 for p in prs if p.get("state") == "OPEN"),
            "closed_unmerged": len(closed), "closed_unmerged_share": round(len(closed) / (len(closed) + len(merged)), 2) if closed or merged else None,
            "sessions": len(sessions), "fresh_tokens_total": total,
            "fresh_tokens_attributed_share": round(attributed / total, 3) if total else None, "prs": rows}


def receipts_summary() -> dict | None:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
    try:
        from z0int.receipt import summarize_tokenomics
        s = summarize_tokenomics()
        return {k: s.get(k) for k in ("verified_tasks", "tokens_per_verified_task", "verified_token_measurement_authoritative")}
    except Exception as exc:  # the scoreboard still prints without the receipt store
        return {"error": type(exc).__name__}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Frontier tokens per merged, cross-checked PR.")
    ap.add_argument("--repo", default=REPO)
    ap.add_argument("--days", type=float, default=7)
    ap.add_argument("--hermes-home", default=os.environ.get("HERMES_HOME", str(Path.home() / ".hermes")))
    ap.add_argument("--codex-sessions", default=str(Path.home() / ".codex" / "sessions"))
    ap.add_argument("--claude-projects", action="append",
                    help="Claude projects dir (repeatable; default $CLAUDE_CONFIG_DIR, ~/.claude, ~/.claude-home)")
    ap.add_argument("--prs-json", help="Use this gh pr list output instead of calling gh (tests)")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    since = time.time() - args.days * 86400
    home = Path(args.hermes_home)
    root = home.parent.parent if home.parent.name == "profiles" else home
    dbs = [str(root / "state.db"), *glob.glob(str(root / "profiles" / "*" / "state.db"))]
    sessions = (hermes_sessions(dbs, since) + codex_sessions(args.codex_sessions, since)
                + claude_sessions(args.claude_projects or [str(Path(d) / "projects") for d in dict.fromkeys(
                    [os.environ.get("CLAUDE_CONFIG_DIR") or "", str(Path.home() / ".claude"), str(Path.home() / ".claude-home")]) if d], since))
    if args.prs_json:
        data = json.loads(Path(args.prs_json).read_text())
        prs, comments = data["prs"], {int(k): v for k, v in data.get("comments", {}).items()}
    else:
        prs = gh_json(["pr", "list", "-R", args.repo, "--state", "all", "--limit", "500", "--json",
                       "number,headRefName,headRefOid,state,mergedAt,closedAt"])
        comments = {p["number"]: gh_json(["pr", "view", str(p["number"]), "-R", args.repo, "--json", "comments"])["comments"]
                    for p in prs if p.get("mergedAt") and _utc_stamp(p["mergedAt"]) >= since}
    result = score(prs, sessions, comments, since)
    result["receipts"] = receipts_summary()
    if args.json:
        print(json.dumps(result, indent=2))
        return 0
    print(f"since {result['since']}: {result['merged']} merged ({result['cross_checked']} cross-checked), "
          f"{result['open_prs']} open, {result['closed_unmerged']} closed unmerged")
    print(f"fresh tokens per merged PR: {result['fresh_tokens_per_merged_pr']}  "
          f"per cross-checked PR: {result['fresh_tokens_per_cross_checked_pr']}")
    print(f"attributed to a PR branch: {result['fresh_tokens_attributed_share']} of {result['fresh_tokens_total']} fresh tokens "
          f"over {result['sessions']} sessions")
    for r in result["prs"]:
        print(f"  #{r['number']} {r['branch']}: fresh {r['fresh']} cached {r['cached']} sessions {r['sessions']}"
              f"{' cross-checked' if r['cross_checked'] else ''}")
    print(f"receipts: {result['receipts']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
