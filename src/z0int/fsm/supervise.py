"""Read-only replay checker and `supervise` CLI for worker supervision (loop 2).

Reads real evidence, maps it to statechart events, runs them through
`worker_supervision.step` (the Python twin of the Bend-proven fsm.bend) and
reports every law the evidence breaks:

* Hermes delegation logs   .../cache/delegation/live/*/task-*.log
* Hermes state.db          async_delegations (the delegate_task lists),
                           subagent sessions, every tool call and result
                           (heavy.lock jobs, background processes, writes,
                           adb installs and inputs, "Child left N background
                           process(es)" notices)
* processes.json           background processes Hermes still tracks
* git worktrees            `git worktree list` under the given roots
* fleet run logs           ~/.local/state/fleet/runs/*.log

Everything is read-only: sqlite is opened `mode=ro`, git runs only list /
status / rev-parse / merge-base / for-each-ref, nothing is resumed, killed,
pruned or deleted. Findings are evidence for the owner, never authorization
or verified_success.

    PYTHONPATH=src python -m z0int.fsm.supervise            # summary + violations
    PYTHONPATH=src python -m z0int.fsm.supervise --json report.json --now 2026-10-21T05:00:00Z
    PYTHONPATH=src python -m z0int.fsm.supervise --worktree /mnt/zer0models/z0-wt/x --heavy
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import sqlite3
import shlex
import subprocess
from collections import defaultdict
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Iterable
from zoneinfo import ZoneInfo

from .worker_supervision import Ev, Exc, Host, Load, Phase, St, W, needs_kevin, refused, step

HERMES = Path(os.environ.get("Z0_FSM_HERMES_HOME", "/workspace/hermes-home/profiles/clean"))
DEFAULT_LIVE = HERMES / "cache/delegation/live"
DEFAULT_DB = HERMES / "state.db"
DEFAULT_PROCS = HERMES / "processes.json"
DEFAULT_ROOTS = ["/mnt/zer0models/z0-wt", "/mnt/zer0models/worktrees", "/mnt/zer0models/workspace/zer0",
                 "/mnt/zer0models/hermes-wt"]
DEFAULT_FLEET = Path.home() / ".local/state/fleet/runs"
DEFAULT_DECISIONS = Path("/mnt/zer0models/workspace/zer0/data/operations/fsm/worker-decisions.jsonl")
HOST_TZ = ZoneInfo(os.environ.get("Z0_FSM_TZ", "America/Chicago"))  # groot's clock, used by logs
LIMIT_HOURS = 2.0

HEAVY_LOCK = re.compile(r"flock\b[^\n]*?heavy\.lock")
HEAVY_CMD = re.compile(
    r"\bgradlew\b|\bgradle\s+\w|\bcargo\s+(build|test|clippy|run)\b|\bblender\b[^\n]*--background|"
    r"\bdocker\s+build\b|\b(npm|pnpm|bun|yarn)\s+(run\s+)?(build|test)\b|\bvitest\b|\bplaywright\s+test\b|"
    r"\bbend\b[^\n]*--verdict|\bhermes\s+bend\s+verify\b|\bcmake\s+--build\b|\bninja\b|\bmake\s+-j")
# Standing exceptions the owner declared (docs/fleet/BUILDS.md, loop-2 brief): GPU work and
# Bend/Lean, whose toolchains exist only on groot. Anything else heavy on groot breaks law 4.
GPU_WORK = re.compile(r"\bblender\b|comfyui|\bcuda|nvidia-smi|\bvllm\b|llama[.-]cpp|llama-server|"
                      r"--device\s+cuda|torch\.cuda|\bgpu\.lock", re.I)
BEND_WORK = re.compile(r"(?:^|[\s/;&|(])bend(?:\.bin)?\s+\S+\.bend|hermes\s+bend\s+verify|\blake\s+build|\blean\s", re.I)
DECLARED_EXC = re.compile(r"Z0_HEAVY_EXCEPTION=(gpu|toolchain|bend)|#\s*heavy-exception:\s*(gpu|toolchain|bend)", re.I)
FLEET_ROUTED = re.compile(r"\bfleet-build\b|scripts/fleet\s+run\b|\bfleet\s+run\b")
REMOTE = re.compile(r"^\s*(?:\w+=\S+\s+)*ssh\s")
FLEET_NODES = Path(os.environ.get("Z0_FSM_FLEET_NODES", "/workspace/.files-k3s/config/fleet/nodes.yaml"))


def s25_matcher() -> re.Pattern[str]:
    """How adb commands address the owner's S25: Z0_FSM_S25_MATCH (a regex), else
    the adb_host of the fleet node whose model is the S25 Ultra (SM-S938U).
    Kept out of the source: device addresses are not published."""
    env = os.environ.get("Z0_FSM_S25_MATCH")
    if env:
        return re.compile(env)
    try:
        text = FLEET_NODES.read_text()
    except OSError:
        return re.compile(r"(?!x)x")  # matches nothing
    m = re.search(r"model:\s*SM-S938U[\s\S]*?adb_host:\s*([\w.:-]+)", text)
    return re.compile(re.escape(m.group(1))) if m else re.compile(r"(?!x)x")
ADB_INSTALL = re.compile(r"\badb\b[^\n;&|]*\binstall(-multiple)?\b|\bpm\s+install\b")
ADB_INPUT = re.compile(r"\binput\s+(tap|text|swipe|keyevent|draganddrop|motionevent)\b")
FG_CHECK = re.compile(r"dumpsys\s+(window|activity)|mCurrentFocus|ResumedActivity|topResumedActivity|"
                      r"mFocusedApp|current_focus|foreground", re.I)
INSTALL_LOCK = re.compile(r"flock\b[^\n]*?install[\w.-]*\.lock")
GIT_WRITE = re.compile(r"\bgit\b(?:\s+-C\s+(\S+))?(?:\s+-c\s+\S+)*\s+(commit|push|merge|rebase|reset|checkout|"
                       r"switch|cherry-pick|am|apply|stash|worktree\s+add|branch\s+-[dDmM]|tag)\b")
CD_DIR = re.compile(r"(?:^|[;&|]\s*)cd\s+(\"[^\"]+\"|'[^']+'|\S+)")
CHILD_LEFT = re.compile(r"delegation/live/(deleg_\w+)/(task-\d+)\.log(?:\s|\\n)*Child left (\d+) background "
                        r"process\(es\) running that were (\w+)[^:]*:(.*?)(?:Re-launch|\\n|\n|$)")
PROC_ID = re.compile(r"proc_[0-9a-f]{10,}")
WORD = re.compile(r"[a-z0-9][a-z0-9_-]{3,}")


# ---------------------------------------------------------------- helpers

def sh(cmd: list[str], cwd: str | None = None, timeout: int = 60) -> tuple[int, str]:
    try:
        p = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return 127, str(exc)
    return p.returncode, p.stdout.strip()


def git(repo: str, *args: str, timeout: int = 60) -> str | None:
    rc, out = sh(["git", "-C", repo, *args], timeout=timeout)
    return out if rc == 0 else None


def iso(ts: float | None) -> str | None:
    return dt.datetime.fromtimestamp(ts, dt.timezone.utc).isoformat(timespec="seconds") if ts else None


def local_ts(s: str) -> float | None:
    """'2026-10-19 11:43:50' in groot's local time -> epoch seconds."""
    try:
        return dt.datetime.strptime(s.strip(), "%Y-%m-%d %H:%M:%S").replace(tzinfo=HOST_TZ).timestamp()
    except ValueError:
        return None


def words(s: str) -> set[str]:
    return set(WORD.findall(s.lower()))


@dataclass
class Finding:
    law: int | None
    severity: str  # violation | candidate | info
    code: str
    message: str
    evidence: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------- delegation logs

@dataclass
class Task:
    id: str                     # deleg_x/task-N
    goal: str
    started: float | None
    end: str | None             # completed | interrupted | error | failed | None (no final line)
    last_write: float
    log: str
    summary: str = ""
    branches: list[str] = field(default_factory=list)
    worktrees: list[str] = field(default_factory=list)
    kickoff: str = ""
    session: str | None = None  # state.db session id, when matched
    last_activity: float | None = None


def read_tasks(live: Path) -> list[Task]:
    out = []
    for log in sorted(live.glob("*/task-*.log")):
        try:
            text = log.read_text(errors="replace")
        except OSError:
            continue
        head = text[:6000]
        goal = re.search(r"^goal: (.*)$", head, re.M) or re.search(r"^task: (.*)$", head, re.M)
        started = re.search(r"^started: (.+)$", head, re.M)
        end = re.findall(r"end status=(\w+)", text)
        summ = re.findall(r"final\s+\| status=\w+ duration=[\d.]+s summary: (.*)", text)
        branches = set(re.findall(r"worktree add (?:-b|-B) ([\w./-]+)", text)) | set(
            re.findall(r"git push[^\n\"]*? -u origin ([\w./-]+)", text)) | set(
            re.findall(r"\bbranch (?:`)?((?:fix|feat|exp|ace|worker|integrate|wip)/[\w./-]+[\w-])", head))
        wts = set(re.findall(r"(/mnt/zer0models/[\w./-]*?(?:-wt|worktrees|\.worktrees)/[\w.-]*[\w-])", text))
        out.append(Task(id=f"{log.parent.name}/{log.stem}", goal=goal.group(1).strip() if goal else "",
                        started=local_ts(started.group(1)) if started else None, end=end[-1] if end else None,
                        last_write=log.stat().st_mtime, log=str(log), summary=summ[-1][:200] if summ else "",
                        branches=sorted(b.rstrip(".") for b in branches),
                        worktrees=sorted(w.rstrip(".") for w in wts), kickoff=text[:6000]))
    return out


# ---------------------------------------------------------------- state.db

@dataclass
class Call:
    session: str
    ts: float
    name: str
    args: dict[str, Any]
    raw: str
    result_ts: float | None = None
    result: str = ""


def open_db(path: Path) -> sqlite3.Connection:
    return sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=10)


def read_calls(db: sqlite3.Connection, since: float = 0.0) -> list[Call]:
    results: dict[str, tuple[float, str]] = {}
    for cid, ts, content in db.execute(
            "select tool_call_id, timestamp, substr(content, 1, 4000) from messages "
            "where role='tool' and tool_call_id is not null and timestamp >= ?", (since,)):
        results[cid] = (ts, content or "")
    out = []
    for sess, ts, tc in db.execute("select session_id, timestamp, tool_calls from messages "
                                   "where tool_calls is not null and timestamp >= ? order by timestamp, id", (since,)):
        try:
            calls = json.loads(tc)
        except (TypeError, ValueError):
            continue
        for c in calls if isinstance(calls, list) else []:
            fn = c.get("function") or {}
            raw = fn.get("arguments") or ""
            try:
                args = json.loads(raw) if isinstance(raw, str) else dict(raw)
            except ValueError:
                args = {}
            r = results.get(c.get("id") or "")
            name = fn.get("name") or ""
            args = args if isinstance(args, dict) else {}
            inner = args.get("calls") if name == "tool_call" else None
            # deferred tools (process_manage, phone_exec, ...) arrive wrapped in tool_call
            for sub in (inner if isinstance(inner, list) and inner else [{"name": name, "arguments": args}]):
                sa = sub.get("arguments") if isinstance(sub, dict) else {}
                out.append(Call(sess, ts, str(sub.get("name") or name) if isinstance(sub, dict) else name,
                                sa if isinstance(sa, dict) else {}, str(raw), r[0] if r else None, r[1] if r else ""))
    return out


def child_notices(db: sqlite3.Connection) -> list[Call]:
    """Parent-side 'Child left N background process(es)' notices, read in full
    (they sit at the end of long delegate_task results)."""
    return [Call(sess, ts, "notice", {}, "", ts, content or "") for sess, ts, content in db.execute(
        "select session_id, timestamp, content from messages where content like '%Child left %background process%'")]


def read_sessions(db: sqlite3.Connection) -> dict[str, dict[str, Any]]:
    rows = db.execute("select id, source, title, started_at, ended_at, end_reason, parent_session_id, "
                      "last_activity_at from sessions").fetchall()
    return {r[0]: {"source": r[1], "title": r[2] or "", "started": r[3], "ended": r[4], "end_reason": r[5],
                   "parent": r[6], "last_activity": r[7]} for r in rows}


def read_delegations(db: sqlite3.Connection) -> list[dict[str, Any]]:
    out = []
    for did, state, disp, comp, tj, rj in db.execute(
            "select delegation_id, state, dispatched_at, completed_at, task_json, result_json from async_delegations"):
        try:
            task = json.loads(tj or "{}")
        except ValueError:
            task = {}
        try:
            res = json.loads(rj or "{}")
        except ValueError:
            res = {}
        statuses = {r.get("task_index"): r.get("status") for r in (res.get("results") or []) if isinstance(r, dict)}
        out.append({"id": did, "state": state, "dispatched": disp, "completed": comp,
                    "goals": task.get("goals") or ([task["goal"]] if task.get("goal") else []),
                    "task_status": statuses})
    return out


def match_sessions(tasks: list[Task], sessions: dict[str, dict[str, Any]]) -> None:
    """A subagent session's title is 'Subagent: <goal prefix>…'; pair it with the
    task whose goal starts with that prefix and whose start is closest (<10 min)."""
    subs = [(sid, s) for sid, s in sessions.items() if s["source"] == "subagent"]
    by_prefix: dict[str, list[tuple[str, dict]]] = defaultdict(list)
    for sid, s in subs:
        by_prefix[s["title"].removeprefix("Subagent: ").rstrip("…").strip()[:40]].append((sid, s))
    taken: set[str] = set()
    for t in sorted(tasks, key=lambda t: (t.started or 0, t.id)):
        cands = [(abs((s["started"] or 0) - (t.started or 0)), sid) for sid, s in by_prefix.get(t.goal[:40].strip(), [])
                 if sid not in taken and t.started and abs((s["started"] or 0) - t.started) < 600]
        if not cands:  # titles cut mid-word: fall back to a shorter prefix
            cands = [(abs((s["started"] or 0) - (t.started or 0)), sid) for sid, s in subs
                     if sid not in taken and t.started and abs((s["started"] or 0) - t.started) < 600
                     and t.goal.startswith(s["title"].removeprefix("Subagent: ").rstrip("…").strip()[:25])]
        if cands:
            sid = min(cands)[1]
            taken.add(sid)
            t.session = sid
            s = sessions[sid]
            t.last_activity = s["last_activity"] or s["ended"]


# ---------------------------------------------------------------- command views

def command_of(c: Call) -> str:
    if c.name == "terminal":
        return str(c.args.get("command") or "")
    if c.name in ("execute_code", "phone_exec"):
        return str(c.args.get("code") or "")
    return ""


def proc_started(c: Call) -> str | None:
    if c.name == "terminal" and '"Background process started"' in c.result:
        m = PROC_ID.search(c.result)
        return m.group(0) if m else None
    return None


# ---------------------------------------------------------------- law 4: heavy jobs

@dataclass
class Job:
    id: str
    host: str          # groot | mbp | omarchy-phone | galaxy-s22plus ...
    start: float
    end: float | None  # None: never observed to finish
    cmd: str
    session: str = ""
    locked: bool = False
    exit: int | None = None
    exception: str = "none"            # declared exception (gpu|toolchain) or none
    exception_hint: str | None = None  # where the exception comes from (declared / standing)
    wait_s: float = 0.0


def proc_exits(calls: list[Call]) -> dict[str, float]:
    """proc id -> uptime at its observed exit, from process_manage results
    (poll/wait/list). Fields are read per proc record, not as JSON: results
    are truncated and commands contain braces."""
    exits: dict[str, float] = {}
    for c in calls:
        if c.name != "process_manage" or not c.result:
            continue
        sid = str(c.args.get("session_id") or "")
        parts = re.split(r'(?="session_id": "proc_)', c.result)
        for part in parts:
            m = re.match(r'"session_id": "(proc_[0-9a-f]+)"', part)
            pid = m.group(1) if m else sid
            if not pid:
                continue
            st = re.search(r'"status": "(\w+)"', part)
            up = re.search(r'"uptime_seconds": (\d+)', part)
            if st and st.group(1) == "exited" and up:
                exits[pid] = max(exits.get(pid, 0.0), float(up.group(1)))
            elif st and st.group(1) == "exited" and c.result_ts:
                exits.setdefault("@" + pid, c.result_ts)  # exited at or before this poll
    return exits


REDIRECT = re.compile(r">\s*(/[\w./+-]+\.log)\b")


def end_from_log(cmd: str, start: float) -> float | None:
    """The mtime of the file a background command redirected into: it stops
    changing when the command ends."""
    for p in REDIRECT.findall(cmd):
        try:
            m = Path(p).stat().st_mtime
        except OSError:
            continue
        if m >= start:
            return m
    return None


def exception_of(cmd: str) -> tuple[str, str]:
    """(exception, source): declared marker, or a standing owner exception."""
    m = DECLARED_EXC.search(cmd)
    if m:
        x = (m.group(1) or m.group(2)).lower()
        return ("toolchain" if x == "bend" else x), "declared"
    if GPU_WORK.search(cmd):
        return "gpu", "standing: GPU work"
    if BEND_WORK.search(cmd):
        return "toolchain", "standing: Bend/Lean only on groot"
    return "none", ""


def local_commands(cmd: str) -> list[list[str]]:
    """Executable shell segments, not quoted data or remote payloads.

    This is a conservative command view, not a general shell interpreter.
    Unknown interpreter payloads are deliberately not treated as shell code.
    """
    # Here-doc bodies are data (often source code or documentation).
    cmd = re.sub(r"<<-?\s*['\"]?(\w+)['\"]?[^\n]*\n[\s\S]*?\n\1\b", "", cmd)
    try:
        lex = shlex.shlex(cmd, posix=True, punctuation_chars=";&|()<>")
        lex.whitespace = " \t\r"
        lex.commenters = "#"
        parts, part = [], []
        for token in lex:
            if token == "\n" or token in (";", "&&", "||", "|", "&", "(", ")"):
                if part:
                    parts.append(part)
                part = []
            else:
                part.append(token)
        if part:
            parts.append(part)
    except ValueError:
        return []
    out = []
    for tokens in parts:
        while tokens and (re.match(r"^[A-Za-z_]\w*=", tokens[0]) or tokens[0] in ("then", "do", "!")):
            tokens = tokens[1:]
        while tokens:
            exe = Path(tokens[0]).name
            if exe in ("env", "command", "exec", "sudo", "nice", "timeout", "flock"):
                tokens = tokens[1:]
                while tokens and (tokens[0].startswith("-") or re.match(r"^\d+[smh]?$", tokens[0])
                                  or re.match(r"^[A-Za-z_]\w*=", tokens[0]) or tokens[0].endswith(".lock")):
                    tokens = tokens[1:]
                continue
            break
        if not tokens:
            continue
        exe = Path(tokens[0]).name
        if exe in ("ssh", "fleet", "fleet-build") or tokens[0].endswith("scripts/fleet"):
            continue
        if exe in ("bash", "sh", "zsh") and "-c" in tokens:
            idx = tokens.index("-c")
            if idx + 1 < len(tokens):
                out.extend(local_commands(tokens[idx + 1]))
        else:
            out.append(tokens)
    return out


def local_heavy(cmd: str) -> bool:
    return any(HEAVY_CMD.search(" ".join([Path(t[0]).name] + t[1:]))
               and Path(t[0]).name in {"gradlew", "gradle", "cargo", "blender", "docker", "npm", "pnpm",
                                      "bun", "yarn", "vitest", "playwright", "bend", "hermes", "cmake", "ninja", "make"}
               for t in local_commands(cmd))


def heavy_jobs_groot(calls: list[Call]) -> list[Job]:
    """Every terminal command on groot that took heavy.lock or matched a heavy
    build/test command. Background jobs end at start + the largest
    uptime_seconds observed for their proc id in a later 'exited' poll."""
    exits = proc_exits(calls)
    jobs = []
    for c in calls:
        cmd = command_of(c) if c.name == "terminal" else ""
        if not cmd or not local_heavy(cmd):
            continue
        pid = proc_started(c)
        start = c.ts
        if pid:
            end = (start + exits[pid] if pid in exits else end_from_log(cmd, start)
                   or exits.get("@" + pid))
        else:
            end = c.result_ts
            if c.result and "timed out after" in c.result[:300]:
                end = c.result_ts  # foreground timeout kills the command
        exc, why = exception_of(cmd)
        ec = re.search(r'"exit_code": (-?\d+)', c.result[:600]) if not pid else None
        jobs.append(Job(id=pid or f"fg:{c.session}:{c.ts:.3f}", host="groot", start=start, end=end, cmd=cmd[:400],
                        session=c.session, locked=bool(HEAVY_LOCK.search(cmd)),
                        exit=int(ec.group(1)) if ec else None,
                        exception=exc, exception_hint=why or None))
    return sorted(jobs, key=lambda j: (j.start, j.id))


def estimate_waits(jobs: list[Job]) -> None:
    """heavy.lock is exclusive, so holds are disjoint and ordered by end time:
    a locked job cannot have been granted the lock before every locked job
    that ended earlier had finished. wait >= max(0, latest earlier end - start).
    Assumes each command held the lock until it ended (true for the usual
    `flock heavy.lock CMD > log` shape; commands with work after the flock
    section make this an overestimate for their successors)."""
    locked = sorted((j for j in jobs if j.locked and j.end is not None), key=lambda j: (j.end, j.start))
    prev_end = float("-inf")
    for j in locked:
        j.wait_s = max(0.0, prev_end - j.start) if prev_end > float("-inf") else 0.0
        j.wait_s = min(j.wait_s, (j.end or j.start) - j.start)
        prev_end = max(prev_end, j.end or j.start)


def fleet_jobs(runs: Path) -> list[Job]:
    out = []
    for log in sorted(runs.glob("*.log")):
        m = re.match(r"(\d{8}-\d{6})-(\w{4})-(.+)\.log$", log.name)
        if not m:
            continue
        try:
            text = log.read_text(errors="replace")
        except OSError:
            continue
        start = dt.datetime.strptime(m.group(1), "%Y%m%d-%H%M%S").replace(tzinfo=HOST_TZ).timestamp()
        hdr = re.search(r"^# fleet job \S+ node=(\S+) cmd=(.*)$", text, re.M)
        ec = re.findall(r"^# exit (-?\d+)$", text, re.M)
        out.append(Job(id=f"{m.group(1)}-{m.group(2)}", host=hdr.group(1) if hdr else m.group(3), start=start,
                       end=log.stat().st_mtime if ec else None, cmd=(hdr.group(2) if hdr else "")[:400],
                       exit=int(ec[-1]) if ec else None))
    return out


def lock_observations(calls: list[Call]) -> list[dict[str, Any]]:
    """lslocks snapshots that agents took of heavy.lock: WRITE* rows are waiters."""
    obs = []
    for c in calls:
        if c.name == "terminal" and "lslocks" in command_of(c) and "heavy.lock" in c.result:
            holders = len(re.findall(r'\\"mode\\": \\"WRITE\\"|"mode": "WRITE"', c.result))
            waiters = len(re.findall(r'WRITE\*', c.result))
            flocks = len(re.findall(r'heavy\.lock', c.result))
            obs.append({"at": iso(c.result_ts or c.ts), "session": c.session, "flock_rows": flocks,
                        "holders": holders, "waiters": waiters})
    return obs


def wait_stats(groot: list[Job], fleet: list[Job], calls: list[Call]) -> dict[str, Any]:
    def dur(j: Job) -> float:
        return max(0.0, (j.end or j.start) - j.start)

    def waits(js: list[Job]) -> dict[str, Any]:
        ws = sorted(j.wait_s for j in js if j.locked and j.end is not None and j.wait_s > 1)
        fin = [j for j in js if j.locked and j.end is not None]
        return {"heavy_lock_jobs": sum(1 for j in js if j.locked), "heavy_lock_finished": len(fin),
                "jobs_that_waited": len(ws), "wait_total_s": round(sum(ws)),
                "wait_median_s": round(ws[len(ws) // 2]) if ws else 0, "wait_max_s": round(ws[-1]) if ws else 0,
                "run_total_s": round(sum(dur(j) - j.wait_s for j in fin))}

    fleet_from = min((j.start for j in fleet), default=None)
    viol = [j for j in groot if j.exception == "none"]
    after = [j for j in groot if fleet_from is not None and j.start >= fleet_from]
    by_node: dict[str, dict[str, float]] = defaultdict(lambda: {"jobs": 0, "run_s": 0.0, "failed": 0})
    for j in fleet:
        by_node[j.host]["jobs"] += 1
        by_node[j.host]["run_s"] += dur(j)
        by_node[j.host]["failed"] += 1 if (j.exit or 0) != 0 else 0
    routed = [c for c in calls if c.name == "terminal" and FLEET_ROUTED.search(command_of(c))]
    busy = sum(1 for c in routed if re.search(r"exit(?:ed)? (?:code )?75\b|no worker has room", c.result[:4000]))
    return {
        "groot_heavy_jobs": len(groot),
        "groot_heavy_excepted": {k: sum(1 for j in groot if j.exception == k) for k in ("gpu", "toolchain")},
        "groot_heavy_violations": len(viol),
        "groot_heavy_violations_since_fleet": sum(1 for j in after if j.exception == "none"),
        "groot_heavy_unlocked_violations": sum(1 for j in viol if not j.locked),
        "groot_all": waits(groot), "groot_since_fleet": waits(after),
        "fleet_available_from": iso(fleet_from),
        "fleet_jobs": len(fleet), "fleet_run_s": round(sum(dur(j) for j in fleet)),
        "fleet_by_node": {k: {"jobs": int(v["jobs"]), "run_s": round(v["run_s"]), "failed": int(v["failed"])}
                          for k, v in sorted(by_node.items())},
        "fleet_routed_calls": len(routed), "fleet_busy_retries_seen": busy,
        "window": {"groot_first": iso(min((j.start for j in groot), default=None)),
                   "groot_last": iso(max((j.start for j in groot), default=None)),
                   "fleet_last": iso(max((j.start for j in fleet), default=None))},
        "note": "groot waits are lower-bound estimates from disjoint heavy.lock holds (estimate_waits); a fleet "
                "job starts its capped scope when admitted, and fleet-build's own retry queue (exit 75) is "
                "counted as busy retries, so fleet queue time is not in the run logs",
    }


def placement_findings(groot: list[Job]) -> list[Finding]:
    """Law 4 on real jobs: each heavy job is a statechart worker started on
    groot; the Start is refused unless an exception is declared."""
    out = []
    for j in groot:
        w = W(0, load=Load.HEAVY, exc=Exc(j.exception) if j.exception in ("gpu", "toolchain") else Exc.NONE)
        s0 = St(Phase.DISPATCHED, w)
        if refused(s0, Ev("Start", h=Host.GROOT, t=int(j.start)), 0):
            out.append(Finding(4, "violation", "heavy_on_groot",
                               "heavy command ran on groot outside fleet-build with no GPU/Bend exception",
                               {"job": j.id, "session": j.session, "at": iso(j.start), "locked": j.locked,
                                "wait_s": round(j.wait_s), "cmd": j.cmd[:160]}))
    return out


# ---------------------------------------------------------------- law 3: background processes

def background(calls: list[Call], sessions: dict[str, dict], procs_json: Path, now: float) -> dict[str, Any]:
    spawned: dict[str, str] = {}      # proc id -> owner session
    reaped: dict[str, str] = {}       # proc id -> how
    for c in calls:
        pid = proc_started(c)
        if pid:
            spawned[pid] = c.session
        if c.name == "process_manage":
            act = str(c.args.get("action") or "")
            sid = str(c.args.get("session_id") or "")
            if act == "kill" and sid:
                reaped.setdefault(sid, "killed by owner")
            if act == "handoff" and sid:
                reaped.setdefault(sid, "handed to parent (adopted)")
            for m in re.finditer(r'"session_id": "(proc_[0-9a-f]+)"[^{}]*?"status": "exited"', c.result):
                reaped.setdefault(m.group(1), "exited")
            if '"status": "exited"' in c.result and sid:
                reaped.setdefault(sid, "exited")
    notices, seen = [], set()
    for c in calls:
        for m in CHILD_LEFT.finditer(c.result):
            ids = PROC_ID.findall(m.group(5))
            key = (m.group(1), m.group(2), tuple(ids), m.group(3))
            if key in seen:
                continue
            seen.add(key)
            notices.append({"child": f"{m.group(1)}/{m.group(2)}", "count": int(m.group(3)),
                            "how": m.group(4).lower(), "procs": ids, "at": iso(c.result_ts), "parent": c.session})
            for p in ids:
                reaped.setdefault(p, f"{m.group(4).lower()} by Hermes when the child ended")
    live = []
    try:
        entries = json.loads(procs_json.read_text())
    except (OSError, ValueError):
        entries = []
    for e in entries if isinstance(entries, list) else []:
        pid = e.get("pid")
        alive = bool(pid) and Path(f"/proc/{pid}").exists()
        owner = e.get("parent_session_id") or ""
        so = sessions.get(owner) or {}
        owner_dead = bool(so.get("ended"))
        live.append({"proc": e.get("session_id"), "pid": pid, "alive": alive, "owner_session": owner,
                     "owner_ended": owner_dead, "persist_on_release": bool(e.get("persist_on_release")),
                     "started": iso(e.get("started_at")), "cmd": str(e.get("command"))[:120]})
    findings = []
    for p in live:
        if p["alive"] and p["owner_ended"] and not p["persist_on_release"]:
            findings.append(Finding(3, "violation", "orphan_background_process",
                                    "process outlived its owner session and nobody adopted it", p))
        elif p["alive"] and p["owner_ended"]:
            findings.append(Finding(3, "info", "detached_background_process",
                                    "owner ended; process was started with persist_on_release (declared detached)", p))
    for n in notices:
        findings.append(Finding(3, "info", "child_left_background",
                                f"child {n['child']} left {n['count']} background process(es); Hermes {n['how']} them "
                                "(reaped, but their result never reached the parent)", n))
    open_spawn = [p for p, s in spawned.items() if p not in reaped]
    return {"spawned": len(spawned), "reaped_or_adopted": sum(1 for p in spawned if p in reaped),
            "unobserved_end": len(open_spawn), "child_left_notices": len(notices),
            "child_left_processes": sum(n["count"] for n in notices),
            "tracked_now": live, "findings": findings, "_spawned": spawned, "_reaped": reaped}


# ---------------------------------------------------------------- law 1: leases

def find_repos(roots: Iterable[str], depth: int = 2) -> list[str]:
    """Git working trees directly under each root (and one more level for the
    workspace's products/oss/services folders). Returns git common dirs."""
    commons: dict[str, str] = {}
    for root in roots:
        base = Path(root)
        if not base.is_dir():
            continue
        cands = [base] + [p for p in base.iterdir() if p.is_dir()]
        if depth > 1:
            cands += [q for p in base.iterdir() if p.is_dir() and not (p / ".git").exists()
                      for q in (p.iterdir() if os.access(p, os.R_OK) else []) if q.is_dir()]
        for p in cands:
            if not (p / ".git").exists():
                continue
            common = git(str(p), "rev-parse", "--path-format=absolute", "--git-common-dir", timeout=20)
            if common:
                commons.setdefault(common, str(p))
    return sorted(commons)


def worktree_inventory(commons: list[str]) -> list[dict[str, Any]]:
    rows = []
    for common in commons:
        out = git(common, "worktree", "list", "--porcelain", timeout=30) or ""
        for block in out.split("\n\n"):
            path = re.search(r"^worktree (.+)$", block, re.M)
            if not path:
                continue
            head = re.search(r"^HEAD ([0-9a-f]{40})$", block, re.M)
            br = re.search(r"^branch refs/heads/(.+)$", block, re.M)
            rows.append({"repo": common, "path": path.group(1), "head": head.group(1) if head else None,
                         "branch": br.group(1) if br else None, "bare": "\nbare" in "\n" + block,
                         "prunable": "prunable" in block, "locked": "\nlocked" in "\n" + block})
    return sorted(rows, key=lambda r: r["path"])


class Roots:
    """Longest-prefix map from a path to the worktree that contains it."""

    def __init__(self, paths: Iterable[str]):
        self.paths = sorted({p.rstrip("/") for p in paths}, key=len, reverse=True)
        self._real = {"/workspace/zer0": "/mnt/zer0models/workspace/zer0"}

    def norm(self, p: str) -> str:
        for a, b in self._real.items():
            if p == a or p.startswith(a + "/"):
                return b + p[len(a):]
        return p

    def find(self, p: str | None) -> str | None:
        if not p or not p.startswith("/"):
            return None
        p = self.norm(p.rstrip("/"))
        for r in self.paths:
            if p == r or p.startswith(r + "/"):
                return r
        return None


def writes(calls: list[Call], roots: Roots) -> list[tuple[float, str, str, str]]:
    """(time, session, worktree, what) for every write into a known worktree."""
    out = []
    for c in calls:
        what = None
        targets: list[str] = []
        if c.name in ("patch", "write_file"):
            targets = [str(c.args.get("path") or "")]
            what = c.name
        elif c.name == "terminal":
            cmd = command_of(c)
            cwd = str(c.args.get("workdir") or "")
            for tokens in local_commands(cmd):
                if tokens[0] == "cd" and len(tokens) > 1:
                    cwd = os.path.normpath(os.path.join(cwd, tokens[1]))
                    continue
                if Path(tokens[0]).name != "git":
                    continue
                executable = " ".join(["git"] + tokens[1:])
                m = GIT_WRITE.match(executable)
                if not m:
                    continue
                # Listing tags is read-only; only creation/deletion changes refs.
                tail = executable[m.end():].strip()
                if m.group(2) == "tag" and (not tail or tail.startswith(("-l", "--list", "--contains", "--points-at", "--sort", "--format", "-n"))):
                    continue
                if m.group(2) == "stash" and tail.split()[:1] in (["list"], ["show"]):
                    continue
                if "--dry-run" in tokens or "-n" in tokens and m.group(2) == "push":
                    continue
                d = m.group(1) or cwd
                targets.append(d)
                what = "git " + m.group(2).split()[0]
        for t in targets:
            r = roots.find(t)
            if r:
                out.append((c.ts, c.session, r, what or ""))
    return sorted(out)


def lease_findings(ws: list[tuple[float, str, str, str]], sessions: dict[str, dict], burst_s: float = 1800,
                   ) -> tuple[list[Finding], dict[str, list[str]]]:
    """A session holds a worktree's lease from its first write there until it
    ends (subagents) or its write burst ends (interactive sessions, 30 min
    idle). A write by anyone else inside that window breaks law 1."""
    by_wt: dict[str, list[tuple[float, str, str]]] = defaultdict(list)
    for t, s, wt, what in ws:
        by_wt[wt].append((t, s, what))
    findings, holders = [], {}
    for wt, rows in sorted(by_wt.items()):
        leases: list[tuple[float, float, str]] = []
        for sess in sorted({s for _, s, _ in rows}):
            ts = [t for t, s, _ in rows if s == sess]
            info = sessions.get(sess) or {}
            if info.get("source") == "subagent":
                leases.append((ts[0], info.get("ended") or info.get("last_activity") or ts[-1], sess))
            else:
                a = b = ts[0]
                for t in ts[1:]:
                    if t - b > burst_s:
                        leases.append((a, b, sess))
                        a = t
                    b = t
                leases.append((a, b, sess))
        holders[wt] = sorted({s for _, _, s in leases})
        clashes: dict[tuple[str, str], list[float]] = defaultdict(list)
        for a, b, owner in leases:
            for t, s, what in rows:
                if s != owner and a < t < b:
                    clashes[(owner, s)].append(t)
        for (owner, other), ts in sorted(clashes.items()):
            findings.append(Finding(1, "violation", "worktree_two_owners",
                                    f"{len(ts)} write(s) by another session while the lease was held",
                                    {"worktree": wt, "holder": owner, "intruder": other,
                                     "holder_source": (sessions.get(owner) or {}).get("source"),
                                     "intruder_source": (sessions.get(other) or {}).get("source"),
                                     "first": iso(ts[0]), "last": iso(ts[-1])}))
    return findings, holders


def branch_findings(inv: list[dict[str, Any]]) -> list[Finding]:
    """The same branch name checked out in two clones of one remote: two
    owners git itself cannot see."""
    remotes: dict[str, str] = {}
    by: dict[tuple[str, str], list[str]] = defaultdict(list)
    for r in inv:
        if not r["branch"] or r["prunable"]:
            continue
        url = remotes.get(r["repo"])
        if url is None:
            url = remotes[r["repo"]] = git(r["repo"], "config", "--get", "remote.origin.url", timeout=10) or r["repo"]
        by[(url, r["branch"])].append(r["path"])
    return [Finding(1, "candidate", "branch_in_two_clones", f"{b} is checked out in {len(p)} clones of {u}",
                    {"remote": u, "branch": b, "worktrees": sorted(p)})
            for (u, b), p in sorted(by.items()) if len(p) > 1 and b not in ("main", "master")]


# ---------------------------------------------------------------- laws 5 and 6: the phone

def phone_findings(calls: list[Call]) -> tuple[list[Finding], dict[str, Any]]:
    S25 = s25_matcher()
    out = []
    installs = inputs = locked = checked = harness = 0
    last_fg: dict[str, float] = {}
    spans = []
    for c in calls:
        if c.name in ("phone_exec", "phone_goal"):
            harness += 1
        cmd = command_of(c)
        if not cmd or "adb" not in cmd or not S25.search(cmd):
            continue
        if FG_CHECK.search(cmd):
            last_fg[c.session] = c.ts
        if ADB_INSTALL.search(cmd):
            installs += 1
            ok = bool(INSTALL_LOCK.search(cmd))
            locked += ok
            spans.append((c.ts, c.result_ts or c.ts, c.session, ok))
            if not ok:
                out.append(Finding(5, "violation", "install_without_lock",
                                   "adb install to the S25 outside the install lock",
                                   {"session": c.session, "at": iso(c.ts), "cmd": cmd[:160]}))
        if ADB_INPUT.search(cmd):
            inputs += 1
            hit = ADB_INPUT.search(cmd)
            pos = hit.start() if hit else 0
            fresh = bool(FG_CHECK.search(cmd[:pos])) or (c.ts - last_fg.get(c.session, -1e18) < 60
                                                          and c.ts != last_fg.get(c.session))
            checked += fresh
            if not fresh:
                out.append(Finding(6, "violation", "input_without_foreground_check",
                                   "input sent to the S25 with no foreground check in the same call or the "
                                   "previous 60 s", {"session": c.session, "at": iso(c.ts), "cmd": cmd[:160]}))
    spans.sort()
    for i, (a, b, s, ok) in enumerate(spans):
        for a2, b2, s2, ok2 in spans[i + 1:]:
            if a2 >= b:
                break
            if s2 != s and not (ok and ok2):
                out.append(Finding(5, "violation", "concurrent_installs",
                                   "two sessions installed to the S25 at the same time without both holding the lock",
                                   {"first": s, "second": s2, "at": iso(a2)}))
    return out, {"s25_installs": installs, "s25_installs_locked": locked, "s25_inputs": inputs,
                 "s25_inputs_with_fg_check": checked, "phone_harness_calls": harness,
                 "note": "phone_exec/phone_goal calls are harness-mediated and not judged here"}


# ---------------------------------------------------------------- law 2: workers

def resumer(old: Task, later: list[Task], calls_by_session: dict[str, list[Call]]) -> tuple[Task | None, bool]:
    """(successor, from_log). Deterministic evidence only: a later task naming
    old's delegation id, log, branch or worktree, or a RESUME/finish/continue
    task sharing >=35% of old's goal words. from_log: the successor names or
    reads old's log (or 'Previous transcript')."""
    deleg = old.id.split("/")[0]
    ow = words(old.goal)
    for new in later:
        if (new.started or 0) < old.last_write - 60:
            continue  # ran alongside old: a sibling, not a successor
        k = new.kickoff
        named = deleg in k or any(b in k for b in old.branches) or any(w in k for w in old.worktrees)
        same = old.goal[:100] and new.goal[:100] == old.goal[:100]
        similar = bool(re.match(r"(?i)\s*(resume|finish|continue)", new.goal)) and bool(ow) and \
            len(ow & words(new.goal)) / len(ow) >= 0.35
        if not (named or same or similar):
            continue
        from_log = old.log in k or deleg in k or "Previous transcript" in k or any(
            old.log in c.raw or deleg in c.raw for c in calls_by_session.get(new.session or "", []))
        return new, from_log
    return None, False


def session_successors(tasks: list[Task], calls_by_session: dict[str, list[Call]],
                       sessions: dict[str, dict]) -> list[Task]:
    """Interactive/fleet harness sessions qualify by actually reading a prior log.

    A title/branch mention alone does not establish adoption. The read time,
    rather than the lifetime of a long-running parent session, orders takeover.
    """
    out = []
    delegated = {t.session for t in tasks}
    for sid, calls in calls_by_session.items():
        if sid in delegated or not re.search(r"resume|continue|finish|take.over|recover|integrat", sessions.get(sid, {}).get("title", ""), re.I):
            continue
        for old in tasks:
            reads = [c for c in calls if c.ts > old.last_write and (
                c.name == "read_file" and c.args.get("path") == old.log or
                c.name == "terminal" and any(Path(t[0]).name in ("cat", "head", "tail", "less")
                    and old.log in t[1:] for t in local_commands(command_of(c))))]
            if reads:
                at = min(c.ts for c in reads)
                out.append(Task(f"session:{sid}:{old.id}", sessions.get(sid, {}).get("title", ""),
                                at, None, at, old.log, kickoff=old.log, session=sid))
    return out


def loop1_dropped(path: Path | None) -> set[str]:
    """Worker ids loop 1's replay flagged interrupted_dropped (cross-reference)."""
    try:
        rep = json.loads(Path(path).read_text()) if path else {}
    except (OSError, ValueError):
        return set()
    return {i["id"].split(":", 1)[1] for i in rep.get("items", []) if i.get("kind") == "worker"
            and any(f.get("code") == "interrupted_dropped" for f in i.get("findings", []))}


def load_decisions(path: Path | None) -> dict[str, dict[str, Any]]:
    """Kevin's answers to the needs-Kevin batch, one JSON object per line:
    {"worker": "deleg_x/task-0", "action": "abandon|resume|integrated", "by": "kevin", "at": "..."}."""
    if not path or not path.exists():
        return {}
    out = {}
    for line in path.read_text().splitlines():
        try:
            d = json.loads(line)
        except ValueError:
            continue
        if isinstance(d, dict) and d.get("worker") and d.get("action") in (
                "abandon", "resume", "resumed", "integrated", "already integrated"):
            out[d["worker"]] = d
    return out


@dataclass
class Worker:
    task: Task
    events: list[Ev] = field(default_factory=list)
    final: St | None = None
    findings: list[Finding] = field(default_factory=list)
    meta: dict[str, Any] = field(default_factory=dict)


def worker_traces(tasks: list[Task], calls_by_session: dict[str, list[Call]], bg: dict[str, Any],
                  decisions: dict[str, dict], now: float, lim_s: int,
                  successors: list[Task] | None = None) -> list[Worker]:
    spawned, reaped = bg["_spawned"], bg["_reaped"]
    ordered = sorted(tasks, key=lambda t: (t.started or 0, t.id))
    out = []
    for i, t in enumerate(ordered):
        wk = Worker(t)
        hb = int(max(t.last_write, t.last_activity or 0))
        start = int(t.started or t.last_write)
        evs = [Ev("Start", h=Host.GROOT, t=start)]
        mine = sorted(p for p, s in spawned.items() if t.session and s == t.session)
        evs += [Ev("Spawn", t=start) for _ in mine]
        evs.append(Ev("Beat", t=hb))
        evs += [Ev("Reap", t=hb) for p in mine if p in reaped]
        succ, from_log = resumer(t, sorted([o for o in ordered[i + 1:] if o.id != t.id] + (successors or []),
                                         key=lambda o: (o.started or 0, o.id)), calls_by_session)
        if t.end == "completed":
            evs.append(Ev("Finish", t=hb))
        elif t.end is not None:
            evs.append(Ev("Interrupt", t=hb, evidence=(("how", t.end),)))
        else:  # no final line: the heartbeat clock decides (Stalled after lim, Interrupted after 2 lim)
            evs += [Ev("Tick", t=hb + lim_s + 1), Ev("Tick", t=hb + 2 * lim_s + 1)]
            evs = [e for e in evs if e.kind != "Tick" or e.t <= now]
        if succ is not None:
            at = int(succ.started or now)
            evs += [Ev("Tick", t=at), Ev("Resume", t=at, by=i + 1, log=from_log)]
            wk.meta["successor"] = succ.id
            wk.meta["resumed_from_log"] = from_log
        d = decisions.get(t.id)
        if d:
            action = d.get("action")
            wk.meta["decision"] = d
            if action == "abandon":
                evs.append(Ev("Abandon", t=int(now)))
            elif action in ("resume", "resumed"):
                # A recorded routing decision clears the human queue, not proof
                # that the worker actually ran or read its log.
                wk.meta["disposition"] = "resume requested"
            elif action in ("integrated", "already integrated"):
                wk.meta["disposition"] = "integration recorded"
                # External disposition, not fabricated log-read/Finish evidence.
                # The projection below preserves the process-reaping guard.

        evs.append(Ev("Tick", t=int(now)))
        s = St(Phase.DISPATCHED, W(i + 1))
        for e in evs:
            if e.kind == "Resume" and refused(s, e, lim_s) and s.phase in (Phase.INTERRUPTED, Phase.OVERDUE):
                wk.findings.append(Finding(2, "violation", "resume_without_log",
                                           f"{succ.id if succ else '?'} took the work over without reading its log",
                                           {"successor": succ.id if succ else None}))
            if e.kind == "Abandon" and refused(s, e, lim_s):
                wk.findings.append(Finding(3, "violation", "abandon_with_live_processes",
                                           "abandon refused: background processes still running", {"bg": s.bg}))
            s = step(s, e, lim_s)
        if d and d.get("action") in ("resume", "resumed"):
            s = St(Phase.RESUMED, s.w, bg=s.bg, by=i + 1)
        elif d and d.get("action") in ("integrated", "already integrated") and s.bg == 0:
            s = St(Phase.INTEGRATED, s.w)
        wk.events, wk.final = evs, s
        wk.meta.update({"log": t.log, "goal": t.goal[:160], "started": iso(t.started), "last_write": iso(hb),
                        "end": t.end or "none", "session": t.session, "background_spawned": len(mine),
                        "background_left": s.bg, "branches": t.branches, "worktrees": t.worktrees})
        if needs_kevin(s):
            wk.findings.append(Finding(2, "violation", "interrupted_past_limit",
                                       f"{'interrupted' if t.end else 'vanished'}; no resume from its log and no "
                                       f"abandon decision within {lim_s / 3600:g} h",
                                       {"age_h": round((now - hb) / 3600, 1)}))
        out.append(wk)
    return out


# ---------------------------------------------------------------- recommendations

_TOP: dict[str, str | None] = {}


def toplevel(p: str) -> str | None:
    """The git toplevel holding p (its nearest existing ancestor), cached per dir."""
    q = Path(p)
    while not q.exists() and q != q.parent:
        q = q.parent
    d = str(q if q.is_dir() else q.parent)
    if d not in _TOP:
        _TOP[d] = git(d, "rev-parse", "--show-toplevel", timeout=10)
    return _TOP[d]


SKIP_TOPS = ("/home/kvn", "/workspace/hermes-home", "/home/kvn/.hermes")


def touched_worktrees(w: Worker, calls_by_session: dict[str, list[Call]], roots: Roots) -> list[str]:
    """Worktrees the worker owned, best evidence first: the worktree its goal
    names, then the ones it wrote into (patch/write_file, git writes), then
    worktree paths its log mentions only if it wrote nowhere."""
    named: list[str] = []
    m = re.search(r"Worktree (/\S+?)[\s,.)]", w.task.goal + " ")
    if m:
        named.append(m.group(1))
    wrote: list[str] = []
    for c in calls_by_session.get(w.task.session or "", []):
        if c.name in ("patch", "write_file"):
            wrote.append(str(c.args.get("path") or ""))
        elif c.name == "terminal":
            cmd = command_of(c)
            for g in GIT_WRITE.finditer(cmd):
                wrote.append(g.group(1) or str(c.args.get("workdir") or ""))

    if not w.task.session:  # no state.db session: read the paths off the log's tool lines
        try:
            wrote += re.findall(r"-> (?:patch|write_file)\((/[^)\s]+)\)", Path(w.task.log).read_text(errors="replace"))
        except OSError:
            pass

    def tops(paths: list[str]) -> list[str]:
        out: list[str] = []
        for p in paths:
            if not p.startswith("/"):
                continue
            top = roots.find(p) or toplevel(roots.norm(p))
            if top and top not in SKIP_TOPS and not top.startswith("/tmp") and top not in out:
                out.append(top)
        return out

    ranked = tops(named) + [t for t in tops(wrote) if t not in tops(named)]
    if not ranked:
        ranked = tops(list(w.task.worktrees))
    # a worktree on one of the worker's own branches goes first
    on_branch = [t for t in ranked + tops(list(w.task.worktrees))
                 if (git(t, "rev-parse", "--abbrev-ref", "HEAD", timeout=10) or "") in w.task.branches]
    return list(dict.fromkeys(on_branch + ranked))


COMMIT_OUT = re.compile(r"\[([\w./+-]+)(?: \(root-commit\))? ([0-9a-f]{7,40})\] ")


def own_commits(w: Worker, calls_by_session: dict[str, list[Call]], repos: list[str]) -> list[dict[str, Any]]:
    """Commits the worker made (git's '[branch sha] subject' lines in its log
    and command results), located in its repos, with the remote refs that hold them."""
    text = ""
    try:
        text = Path(w.task.log).read_text(errors="replace")
    except OSError:
        pass
    text += "\n".join(c.result[:4000] for c in calls_by_session.get(w.task.session or "", []) if c.name == "terminal")
    shas = list(dict.fromkeys(m.group(2) for m in COMMIT_OUT.finditer(text)))[:20]
    out = []
    for sha in shas:
        for repo in repos:
            if git(repo, "cat-file", "-e", sha + "^{commit}", timeout=10) is None:
                continue
            refs = (git(repo, "for-each-ref", "--contains", sha, "--format=%(refname:short)", "refs/remotes",
                        timeout=60) or "").split()
            out.append({"sha": sha, "repo": repo, "remote_refs": sorted(refs)[:5], "on_remote": bool(refs)})
            break
    return out


def written_paths(w: Worker, calls_by_session: dict[str, list[Call]]) -> set[str]:
    out = {str(c.args.get("path") or "") for c in calls_by_session.get(w.task.session or "", [])
           if c.name in ("patch", "write_file")}
    if not w.task.session:
        try:
            out |= set(re.findall(r"-> (?:patch|write_file)\((/[^)\s]+)\)", Path(w.task.log).read_text(errors="replace")))
        except OSError:
            pass
    return {os.path.realpath(p) for p in out if p.startswith("/")}


def worktree_state(path: str, mine: set[str] | None = None) -> dict[str, Any]:
    p = Path(path)
    if not p.exists():
        return {"path": path, "exists": False}
    top = git(path, "rev-parse", "--show-toplevel", timeout=20)
    if not top:
        return {"path": path, "exists": True, "git": False}
    st = git(top, "status", "--porcelain", "--untracked-files=normal", timeout=60) or ""
    head = git(top, "rev-parse", "HEAD", timeout=10)
    br = git(top, "rev-parse", "--abbrev-ref", "HEAD", timeout=10)
    remote_has = git(top, "for-each-ref", "--contains", head or "HEAD", "--format=%(refname:short)", "refs/remotes",
                     timeout=60) if head else None
    up = git(top, "rev-parse", "--abbrev-ref", "@{upstream}", timeout=10)
    default = git(top, "symbolic-ref", "--short", "refs/remotes/origin/HEAD", timeout=10)
    merged = None
    if head and default:
        rc, _ = sh(["git", "-C", top, "merge-base", "--is-ancestor", head, default])
        merged = {0: True, 1: False}.get(rc)
    lines = [l for l in st.splitlines() if l.strip()]
    rtop = os.path.realpath(top)
    changed = {os.path.join(rtop, l[3:].split(" -> ")[-1].strip('"')) for l in lines}
    dirty_mine = sorted(p for p in changed if mine and p in mine)
    return {"path": top, "exists": True, "git": True, "branch": br, "head": head, "upstream": up,
            "dirty_by_this_worker": len(dirty_mine), "dirty_by_this_worker_files": dirty_mine[:10],
            "dirty": sum(1 for l in lines if not l.startswith("??")), "untracked": sum(1 for l in lines if l.startswith("??")),
            "on_remote_refs": sorted((remote_has or "").split())[:6], "on_any_remote": bool(remote_has),
            "default_ref": default, "merged_into_default": merged}


def recommend(w: Worker, wts: list[dict[str, Any]], later: list[Task],
              commits: list[dict[str, Any]] | None = None) -> tuple[str, str]:
    """resume | abandon | already integrated, with the deciding evidence. Only
    the worker's own uncommitted files count as its unique work: shared
    checkouts carry other sessions' edits."""
    live = [x for x in wts[:3] if x.get("exists") and x.get("git")]
    own_dirty = [x for x in live if x["dirty_by_this_worker"]]
    unpushed = [x for x in live if not x["on_any_remote"] and x.get("branch") in w.task.branches + ["HEAD"]
                and (x.get("branch") != "HEAD" or x["path"] in w.task.worktrees)]
    integrated = [x for x in live if x.get("merged_into_default") and not x["dirty_by_this_worker"]]
    ow = words(w.task.goal)
    sup = [o for o in later if o.end == "completed" and (o.started or 0) >= w.task.started - 3600
           and ow and len(ow & words(o.goal)) / len(ow) >= 0.35]
    if own_dirty:
        x = own_dirty[0]
        return "resume", (f"{x['dirty_by_this_worker']} of its own file(s) uncommitted in {x['path']} "
                          f"({x.get('branch')}); resume from {w.task.log}")
    commits = commits or []
    local_only = [c for c in commits if not c["on_remote"]]
    if local_only:
        c = local_only[0]
        return "resume", f"its commit {c['sha'][:10]} in {c['repo']} is on no remote branch; push or resume"
    if commits:
        refs = sorted({r for c in commits for r in c["remote_refs"]})
        return "already integrated", (f"its {len(commits)} commit(s) are on {', '.join(refs[:3])}; nothing of its own "
                                      "left uncommitted")
    if unpushed:
        x = unpushed[0]
        return "resume", f"its commits on {x.get('branch')} in {x['path']} are on no remote branch; push or resume"
    if integrated:
        return "already integrated", f"{integrated[0]['path']} head is on {integrated[0]['default_ref']}, no own edits left"
    if sup:
        return "abandon", f"superseded: later completed task {sup[0].id} has the same goal; confirm it covers this"
    if live:
        return "abandon", "no own uncommitted edits and no unpushed branch of its own in its worktrees"
    return "abandon", "no worktree, branch or commit left to recover; re-dispatch only if the goal is still wanted"


# ---------------------------------------------------------------- report

def build(args: argparse.Namespace) -> dict[str, Any]:
    now = (dt.datetime.fromisoformat(args.now.replace("Z", "+00:00")).timestamp() if args.now
           else dt.datetime.now(dt.timezone.utc).timestamp())
    lim_s = int(args.limit_hours * 3600)
    tasks = read_tasks(Path(args.delegation_dir))
    db = open_db(Path(args.state_db))
    sessions = read_sessions(db)
    delegations = read_delegations(db)
    calls = read_calls(db)
    notices = child_notices(db)
    db.close()
    match_sessions(tasks, sessions)
    by_sess: dict[str, list[Call]] = defaultdict(list)
    for c in calls:
        by_sess[c.session].append(c)

    commons = find_repos(args.roots)
    inv = worktree_inventory(commons)
    roots = Roots([r["path"] for r in inv])

    groot = heavy_jobs_groot(calls)
    estimate_waits(groot)
    fleet = fleet_jobs(Path(args.fleet_runs))
    stats = wait_stats(groot, fleet, calls)
    stats["lslocks_observations"] = lock_observations(calls)
    stats["lslocks_max_waiters_seen"] = max((o["waiters"] for o in stats["lslocks_observations"]), default=0)

    bg = background(calls + notices, sessions, Path(args.processes), now)
    ws = writes(calls, roots)
    lease_f, holders = lease_findings(ws, sessions)
    phone_f, phone_stats = phone_findings(calls)
    workers = worker_traces(tasks, by_sess, bg, load_decisions(args.decisions), now, lim_s,
                            session_successors(tasks, by_sess, sessions))

    ordered = sorted(tasks, key=lambda t: (t.started or 0, t.id))
    pos = {t.id: i for i, t in enumerate(ordered)}
    loop1 = loop1_dropped(args.loop1_report)
    batch = []
    for wk in workers:
        if not wk.final or wk.final.phase in (Phase.RESUMED, Phase.INTEGRATED, Phase.ABANDONED) or not (
                needs_kevin(wk.final) or wk.task.id in loop1):
            continue
        wt_paths = touched_worktrees(wk, by_sess, roots)
        mine = written_paths(wk, by_sess)
        wts = [worktree_state(p, mine) for p in wt_paths[:8]]
        repos = [x["path"] for x in wts if x.get("git")]
        commits = own_commits(wk, by_sess, repos)
        action, why = recommend(wk, wts, ordered[pos[wk.task.id] + 1:], commits)
        batch.append({"worker": wk.task.id, "goal": wk.task.goal[:140], "end": wk.task.end or "vanished",
                      "last_write": iso(max(wk.task.last_write, wk.task.last_activity or 0)),
                      "recommended": action, "why": why, "worktrees": wts, "own_commits": commits,
                      "branches": wk.task.branches, "log": wk.task.log, "phase": wk.final.phase.value,
                      "in_loop1_dropped": wk.task.id in loop1, "successor": wk.meta.get("successor"),
                      "resumed_from_log": wk.meta.get("resumed_from_log"),
                      "decision_line": json.dumps({"worker": wk.task.id, "action": action, "by": "kevin"})})

    findings = (lease_f + branch_findings(inv) + placement_findings(groot) + bg["findings"] + phone_f
                + [f for wk in workers for f in wk.findings])
    summary: dict[str, dict[str, int]] = {}
    for f in findings:
        key = f"L{f.law}:{f.code}" if f.law else f.code
        summary.setdefault(key, {"violation": 0, "candidate": 0, "info": 0})[f.severity] += 1
    phases: dict[str, int] = defaultdict(int)
    for wk in workers:
        phases[wk.final.phase.value if wk.final else "?"] += 1
    live = [{"worker": wk.task.id, "phase": wk.final.phase.value, "last_write": wk.meta["last_write"],
             "goal": wk.task.goal[:100]} for wk in workers
            if wk.final and wk.final.phase in (Phase.RUNNING, Phase.STALLED, Phase.INTERRUPTED)]
    return {
        "schema": "z0.fsm.worker-supervision.replay/v0",
        "generated_at": iso(now), "limit_hours": args.limit_hours,
        "sources": {"delegation_dir": str(args.delegation_dir), "state_db": str(args.state_db),
                    "processes": str(args.processes), "roots": list(args.roots), "fleet_runs": str(args.fleet_runs),
                    "tasks": len(tasks), "tool_calls": len(calls), "delegate_task_lists": len(delegations),
                    "repos": len(commons), "worktrees": len(inv), "sessions_matched": sum(1 for t in tasks if t.session)},
        "phases": dict(sorted(phases.items())), "live": live,
        "summary": dict(sorted(summary.items())),
        "needs_kevin": {"size": len(batch), "overdue": sum(1 for x in batch if x["phase"] == "Overdue"),
                        "loop1_dropped": sorted(loop1), "items": batch,
                        "how_to_answer": f"append decision lines to {DEFAULT_DECISIONS}; the next run replays them"},
        "groot_vs_fleet": stats,
        "background": {k: v for k, v in bg.items() if not k.startswith("_") and k != "findings"},
        "phone": phone_stats,
        "leases": {"worktrees_written": len(holders),
                   "worktrees_with_two_owners": len({f.evidence["worktree"] for f in lease_f})},
        "worktree_inventory": {"total": len(inv), "prunable": sum(1 for r in inv if r["prunable"]),
                               "detached": sum(1 for r in inv if not r["branch"] and not r["bare"])},
        "findings": [asdict(f) for f in findings],
        "workers": [{"id": wk.task.id, "phase": wk.final.phase.value if wk.final else None, "meta": wk.meta,
                     "findings": [asdict(f) for f in wk.findings],
                     "events": [{"kind": e.kind, "t": iso(e.t) if e.t else None, "log": e.log}
                                for e in wk.events]} for wk in workers],
        "scope": "replay of observed evidence through the v0 worker statechart; findings are evidence, "
                 "not authorization or verified_success; nothing was resumed, killed or deleted",
    }


def dispatch_check(rep: dict[str, Any], worktree: str | None, heavy: bool, exception: str | None) -> list[str]:
    """Reasons not to dispatch now (empty: go)."""
    out = []
    if worktree:
        wt = worktree.rstrip("/")
        for wk in rep["workers"]:
            if wk["phase"] in ("Running", "Stalled", "Interrupted", "Overdue") and \
                    any(wt == p.rstrip("/") or wt.startswith(p.rstrip("/") + "/") for p in wk["meta"]["worktrees"]):
                out.append(f"L1 lease: {wt} is held by {wk['id']} ({wk['phase']}); resume or abandon it first")
    if heavy and not exception:
        out.append("L4 placement: heavy work goes to the fleet (scripts/fleet run --node mbp ...) unless you "
                   "declare Z0_HEAVY_EXCEPTION=gpu|toolchain")
    return out


def render(rep: dict[str, Any]) -> str:
    g = rep["groot_vs_fleet"]
    lines = [f"worker supervision @ {rep['generated_at']}  (limit {rep['limit_hours']:g} h)",
             f"sources: {rep['sources']['tasks']} delegation tasks, {rep['sources']['tool_calls']} tool calls, "
             f"{rep['sources']['worktrees']} worktrees in {rep['sources']['repos']} repos",
             "phases: " + ", ".join(f"{k} {v}" for k, v in rep["phases"].items()),
             "", "violations by law:"]
    for k, v in rep["summary"].items():
        lines.append(f"  {k:44} violation {v['violation']:4}  candidate {v['candidate']:3}  info {v['info']:3}")
    lines += ["", f"live workers ({len(rep['live'])}):"]
    lines += [f"  {x['phase']:11} {x['worker']:26} last {x['last_write']}  {x['goal'][:60]}" for x in rep["live"][:20]]
    lines += ["", f"needs Kevin ({rep['needs_kevin']['size']}):"]
    for x in rep["needs_kevin"]["items"]:
        tag = "L1" if x["in_loop1_dropped"] else "  "
        lines.append(f"  {x['recommended']:18} {tag} {x['phase']:11} {x['worker']:26} {x['goal'][:50]}")
        lines.append(f"  {'':18} why: {x['why'][:150]}")
    ga, gs = g["groot_all"], g["groot_since_fleet"]
    nodes = ", ".join(f"{k} {v['jobs']}" for k, v in g["fleet_by_node"].items())
    lines += ["", "groot vs fleet:",
              f"  groot heavy jobs {g['groot_heavy_jobs']} (violations {g['groot_heavy_violations']}, "
              f"{g['groot_heavy_violations_since_fleet']} since the fleet came up {g['fleet_available_from']}; "
              f"excepted gpu {g['groot_heavy_excepted']['gpu']} / bend {g['groot_heavy_excepted']['toolchain']})",
              f"  heavy.lock: {ga['heavy_lock_jobs']} jobs, {ga['jobs_that_waited']} waited, total "
              f"{ga['wait_total_s'] / 3600:.1f} h, median {ga['wait_median_s'] / 60:.1f} min, max {ga['wait_max_s'] / 60:.0f} min "
              f"(since fleet: {gs['jobs_that_waited']} waited, {gs['wait_total_s'] / 3600:.1f} h); up to "
              f"{g.get('lslocks_max_waiters_seen', 0)} waiters seen in one lslocks snapshot",
              f"  fleet jobs {g['fleet_jobs']} ({nodes}), "
              f"run {g['fleet_run_s'] / 60:.0f} min, busy retries {g['fleet_busy_retries_seen']}"]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="supervise", description=(__doc__ or "").split("\n\n")[0])
    ap.add_argument("--delegation-dir", default=str(DEFAULT_LIVE))
    ap.add_argument("--state-db", default=str(DEFAULT_DB))
    ap.add_argument("--processes", default=str(DEFAULT_PROCS))
    ap.add_argument("--roots", nargs="*", default=DEFAULT_ROOTS)
    ap.add_argument("--fleet-runs", default=str(DEFAULT_FLEET))
    ap.add_argument("--decisions", type=Path, default=DEFAULT_DECISIONS)
    ap.add_argument("--loop1-report", type=Path,
                    default=Path("/mnt/zer0models/workspace/zer0/data/operations/fsm/replay-20261021.json"),
                    help="loop-1 replay report; its dropped workers are cross-checked")
    ap.add_argument("--limit-hours", type=float, default=LIMIT_HOURS)
    ap.add_argument("--now", help="ISO time to judge ages against (default: now); fixes the output")
    ap.add_argument("--json", type=Path, help="write the full report here")
    ap.add_argument("--live-dir", type=Path, help="publish atomic state and append-only needs-Kevin ledger here")
    ap.add_argument("--worktree", help="dispatch check: is this worktree's lease free?")
    ap.add_argument("--heavy", action="store_true", help="dispatch check: the new work is heavy")
    ap.add_argument("--exception", choices=["gpu", "toolchain"], help="declared groot exception for --heavy")
    ap.add_argument("--mermaid", type=Path, help="write the statechart diagram (Mermaid) here and exit")
    args = ap.parse_args(argv)
    if args.mermaid:
        from .worker_supervision import mermaid
        args.mermaid.write_text(mermaid())
        return 0
    rep = build(args)
    if args.json:
        args.json.write_text(json.dumps(rep, indent=1, default=str) + "\n")
    if args.live_dir:
        from .live import publish
        publish(rep, args.live_dir)
    print(render(rep))
    if args.worktree or args.heavy:
        reasons = dispatch_check(rep, args.worktree, args.heavy, args.exception)
        print("\ndispatch: " + ("GO" if not reasons else "HOLD\n  " + "\n  ".join(reasons)))
        return 3 if reasons else 0
    return 0 if args.live_dir else (1 if any(v["violation"] for v in rep["summary"].values()) else 0)


if __name__ == "__main__":
    raise SystemExit(main())
