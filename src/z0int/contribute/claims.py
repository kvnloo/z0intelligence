"""Public claim protocol (spec §2.4): command parsing, lease markers, claim-bot decisions.

Pure functions, shared by the claim bot (``.github/scripts/claim-bot.py``), by
``expire-claims.sh`` tests and by the ``z0int contribute`` collision check, so all
three agree on what an active claim is.

A lease marker is trusted only in a comment written by a trusted author (the
workflow bot, or the repo owner who handles claims by hand until the bot is
enabled). A marker pasted by anyone else is ignored.
"""
from __future__ import annotations

import datetime as _dt
import json
import re
from dataclasses import dataclass, field
from typing import Any, Iterable

BOT_LOGIN = "github-actions[bot]"
MAX_LEASE_H = 72
BLOCKING_LABELS = ("blocked", "parked", "hcl:backlog", "needs-discussion")
MARKER_RE = re.compile(r"<!--\s*z0-claim\s+(\{.*?\})\s*-->", re.S)
REJECT_RE = re.compile(r"^/claim rejected: @([A-Za-z0-9-]+) (.+)$", re.M)
_CMD_RE = re.compile(r"^/claim(?:\s+(.*))?$")
_TASK_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")
_LOGIN_RE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})$")


def _iso(ts: _dt.datetime) -> str:
    return ts.astimezone(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_iso(s: str) -> _dt.datetime | None:
    try:
        return _dt.datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=_dt.timezone.utc)
    except (TypeError, ValueError):
        return None


def trusted_authors(repo: str, extra: Iterable[str] = ()) -> set[str]:
    owner = repo.split("/", 1)[0] if "/" in repo else ""
    return {BOT_LOGIN, *([owner] if owner else []), *extra}


def claim_text(task: str, *, harness: str, hw: str, lease_h: int = MAX_LEASE_H) -> str:
    """The exact comment the human posts (or approves) to claim a task."""
    return f"/claim {task} lease={int(lease_h)}h harness={harness} hw={hw}"


@dataclass(frozen=True)
class Command:
    verb: str                     # claim | renew | release
    task: str | None
    lease_h: int = MAX_LEASE_H
    params: dict[str, str] = field(default_factory=dict)


def parse_command(body: str) -> Command | None:
    """First line only. ``/claim <task> [lease=Nh] [k=v ...]`` / ``/claim renew|release [task]``."""
    first = (body or "").strip().splitlines()[0].strip() if (body or "").strip() else ""
    m = _CMD_RE.match(first)
    if not m:
        return None
    if first.startswith("/claim rejected:"):
        return None
    words = (m.group(1) or "").split()
    if not words:
        return None
    verb = "claim"
    if words[0] in ("renew", "release"):
        verb, words = words[0], words[1:]
    task = None
    params: dict[str, str] = {}
    for w in words:
        if "=" in w:
            k, _, v = w.partition("=")
            params[k] = v
        elif task is None and _TASK_RE.match(w):
            task = w
    if verb == "claim" and task is None:
        return None
    lease_h = MAX_LEASE_H
    lm = re.fullmatch(r"([0-9]{1,3})h", params.get("lease", ""))
    if lm:
        lease_h = max(1, min(MAX_LEASE_H, int(lm.group(1))))
    return Command(verb=verb, task=task, lease_h=lease_h, params=params)


def format_marker(task: str, login: str, expires: _dt.datetime, status: str = "active") -> str:
    payload = json.dumps({"task": task, "login": login, "expires": _iso(expires), "status": status},
                         sort_keys=True, separators=(",", ":"))
    return f"<!-- z0-claim {payload} -->"


def _author(c: dict[str, Any]) -> str:
    a = c.get("author") or c.get("user") or {}
    return str(a.get("login", "")) if isinstance(a, dict) else str(a)


def markers(comments: list[dict[str, Any]], trusted: set[str]) -> list[dict[str, Any]]:
    """Lease markers from trusted authors, in comment order."""
    out = []
    for c in comments:
        if _author(c) not in trusted:
            continue
        for m in MARKER_RE.finditer(c.get("body") or ""):
            try:
                d = json.loads(m.group(1))
            except ValueError:
                continue
            if isinstance(d, dict) and {"task", "login", "expires"} <= set(d):
                out.append(d)
    return out


def leases(comments: list[dict[str, Any]], trusted: set[str], task: str | None = None) -> dict[str, dict[str, Any]]:
    """Latest marker per login (a renewal or release supersedes the earlier marker)."""
    out: dict[str, dict[str, Any]] = {}
    for d in markers(comments, trusted):
        if task is not None and d.get("task") != task:
            continue
        out[str(d["login"]).lower()] = d
    return out


def active_leases(comments: list[dict[str, Any]], trusted: set[str], *, now: _dt.datetime,
                  task: str | None = None) -> dict[str, dict[str, Any]]:
    res = {}
    for login, d in leases(comments, trusted, task).items():
        exp = _parse_iso(str(d.get("expires")))
        if d.get("status", "active") == "active" and exp is not None and exp > now:
            res[login] = d
    return res


def rejections(comments: list[dict[str, Any]], trusted: set[str]) -> list[tuple[str, str]]:
    out = []
    for c in comments:
        if _author(c) in trusted:
            out += [(m.group(1).lower(), m.group(2).strip()) for m in REJECT_RE.finditer(c.get("body") or "")]
    return out


def claim_limit(claim: str) -> int | None:
    if claim == "exclusive":
        return 1
    m = re.fullmatch(r"parallel\((\d+)\)", claim or "")
    return int(m.group(1)) if m else None


@dataclass(frozen=True)
class Decision:
    accept: bool
    reason: str
    add_label: str | None
    comment: str


def _reject(login: str, reason: str) -> Decision:
    return Decision(False, reason, None, f"/claim rejected: @{login} {reason}")


def decide(cmd: Command, *, login: str, issue: int, labels: Iterable[str], comments: list[dict[str, Any]],
           tasks: list[dict[str, Any]], now: _dt.datetime, trusted: set[str],
           renewal_has_recent_commit: bool = False) -> Decision:
    """The claim bot's whole decision for one ``/claim`` comment. At most one label + one comment."""
    labels = set(labels)
    if not _LOGIN_RE.match(login or ""):
        return _reject("unknown", "commenter login is not a GitHub handle")
    on_issue = [t for t in tasks if t.get("issue") == issue]
    task_id = cmd.task or (on_issue[0]["id"] if len(on_issue) == 1 else None)
    task = next((t for t in tasks if t["id"] == task_id), None)
    if task is None:
        return _reject(login, f"unknown task {task_id!r} (see .z0/priorities.yaml)")
    if task.get("issue") != issue:
        return _reject(login, f"task {task['id']} belongs to issue #{task.get('issue')}")
    mine = active_leases(comments, trusted, now=now, task=task["id"]).get(login.lower())
    if cmd.verb == "release":
        if not mine:
            return _reject(login, "you hold no active lease on this task")
        return Decision(True, "released", None,
                        f"Claim released by @{login} for `{task['id']}`.\n"
                        f"{format_marker(task['id'], login, now, status='released')}")
    if cmd.verb == "renew":
        if not mine:
            return _reject(login, "you hold no active lease to renew")
        if not renewal_has_recent_commit:
            return _reject(login, f"renewal needs a commit on contrib/{task['id']}-{login} in your fork within 72 h")
        exp = now + _dt.timedelta(hours=MAX_LEASE_H)
        return Decision(True, "renewed", None,
                        f"Claim renewed: @{login} holds `{task['id']}` until {_iso(exp)}.\n"
                        f"{format_marker(task['id'], login, exp)}")
    if not task.get("newcomer") or task.get("needs_owner_approval"):
        return _reject(login, f"task {task['id']} is not open to new contributors")
    if task.get("status") != "open":
        return _reject(login, f"task {task['id']} is {task.get('status')}")
    limit = claim_limit(task.get("claim", "none"))
    if limit is None:
        return _reject(login, f"task {task['id']} needs no claim (claim: none); just start")
    if "claimable" not in labels:
        return _reject(login, "issue is not labelled claimable")
    bad = sorted(labels & set(BLOCKING_LABELS))
    if bad:
        return _reject(login, f"issue carries {', '.join(bad)}")
    if mine:
        return _reject(login, "you already hold this claim; use /claim renew")
    active = active_leases(comments, trusted, now=now, task=task["id"])
    if len(active) >= limit:
        return _reject(login, f"claimed to capacity ({len(active)}/{limit})")
    exp = now + _dt.timedelta(hours=cmd.lease_h)
    return Decision(True, "accepted", "claimed",
                    f"Claim accepted: @{login} holds `{task['id']}` until {_iso(exp)} "
                    f"({len(active) + 1}/{limit}). Renew with `/claim renew`; it expires on its own.\n"
                    f"{format_marker(task['id'], login, exp)}")


def collision_outcome(comments: list[dict[str, Any]], *, login: str, task: str, trusted: set[str],
                      now: _dt.datetime, after_index: int = 0) -> str:
    """CLI view after posting: 'won', 'lost', or 'pending' (no bot answer yet)."""
    if login.lower() in active_leases(comments, trusted, now=now, task=task):
        return "won"
    for who, _reason in rejections(comments[after_index:], trusted):
        if who == login.lower():
            return "lost"
    return "pending"
