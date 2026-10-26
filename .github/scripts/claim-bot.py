#!/usr/bin/env python3
"""Claim bot for `/claim` issue comments (docs/contrib/CONTRIBUTOR-LAYER.md §2.4).

Owner decision O-4: this is the only origin write of the contributor layer. Per
`/claim` comment it writes at most the `claimed` label and one comment (the lease
record with a hidden `<!-- z0-claim {...} -->` marker, or `/claim rejected: ...`).
It never assigns, closes, merges or edits code. Expiry stays in expire-claims.sh.

The decision logic is `z0int.contribute.claims.decide` (pure, tested). This file is
only I/O through `gh`. The workflow runs one job per issue at a time
(`concurrency: claim-<issue>`), and every run re-reads the comments, so two
contributors who claim at once cannot both win.

Usage (Actions): GITHUB_EVENT_PATH, GITHUB_REPOSITORY and GH_TOKEN set;
PYTHONPATH=src python3 .github/scripts/claim-bot.py
"""
from __future__ import annotations

import datetime as _dt
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Callable

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from z0int.contribute import claims as C  # noqa: E402
from z0int.contribute import priorities as P  # noqa: E402

GhRunner = Callable[[list[str]], str]


def gh_cli(args: list[str]) -> str:
    p = subprocess.run(["gh", *args], capture_output=True, text=True, timeout=60)
    if p.returncode != 0:
        raise RuntimeError(f"gh {' '.join(args[:2])} failed: {p.stderr.strip()[:300]}")
    return p.stdout


def _comments(gh: GhRunner, repo: str, number: int) -> list[dict[str, Any]]:
    pages = json.loads(gh(["api", f"repos/{repo}/issues/{number}/comments", "--paginate", "--slurp"]) or "[]")
    flat = [c for page in pages for c in (page if isinstance(page, list) else [page])]
    return [{"author": {"login": (c.get("user") or {}).get("login", "")}, "body": c.get("body") or ""} for c in flat]


def _labels(gh: GhRunner, repo: str, number: int) -> list[str]:
    d = json.loads(gh(["issue", "view", str(number), "-R", repo, "--json", "labels"]) or "{}")
    return [lb.get("name", "") for lb in d.get("labels") or []]


def _recent_commit(gh: GhRunner, repo: str, login: str, task: str, now: _dt.datetime) -> bool:
    name = repo.split("/", 1)[1]
    since = (now - _dt.timedelta(hours=C.MAX_LEASE_H)).strftime("%Y-%m-%dT%H:%M:%SZ")
    branch = f"contrib/{task}-{login.lower()}"
    try:
        rows = json.loads(gh(["api", f"repos/{login}/{name}/commits?sha={branch}&since={since}&per_page=1"]) or "[]")
    except (RuntimeError, ValueError):
        return False
    return isinstance(rows, list) and len(rows) > 0


def handle(event: dict[str, Any], *, gh: GhRunner, repo: str, tasks: list[dict[str, Any]],
           now: _dt.datetime) -> dict[str, Any]:
    comment = event.get("comment") or {}
    issue = event.get("issue") or {}
    login = (comment.get("user") or {}).get("login", "")
    if issue.get("pull_request") is not None or login == C.BOT_LOGIN:
        return {"action": "ignored", "reason": "not an issue claim"}
    cmd = C.parse_command(comment.get("body") or "")
    if cmd is None:
        return {"action": "ignored", "reason": "no /claim command"}
    number = int(issue["number"])
    comments = _comments(gh, repo, number)
    labels = _labels(gh, repo, number)
    trusted = C.trusted_authors(repo)
    task_id = cmd.task or next((t["id"] for t in tasks if t.get("issue") == number), "")
    renewal_ok = cmd.verb == "renew" and _recent_commit(gh, repo, login, task_id, now)
    d = C.decide(cmd, login=login, issue=number, labels=labels, comments=comments, tasks=tasks, now=now,
                 trusted=trusted, renewal_has_recent_commit=renewal_ok)
    if d.add_label and d.add_label not in labels:
        gh(["issue", "edit", str(number), "-R", repo, "--add-label", d.add_label])
    gh(["issue", "comment", str(number), "-R", repo, "--body", d.comment])
    return {"action": "accepted" if d.accept else "rejected", "reason": d.reason}


def main() -> int:
    event = json.loads(Path(os.environ["GITHUB_EVENT_PATH"]).read_text(encoding="utf-8"))
    repo = os.environ["GITHUB_REPOSITORY"]
    try:
        tasks = P.load_priorities(ROOT)["tasks"]
    except Exception as exc:  # noqa: BLE001
        print(f"claim-bot: cannot read .z0/priorities.yaml ({exc}); no action", file=sys.stderr)
        return 1
    now = _dt.datetime.now(_dt.timezone.utc).replace(microsecond=0)
    print(json.dumps(handle(event, gh=gh_cli, repo=repo, tasks=tasks, now=now)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
