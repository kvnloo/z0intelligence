"""In-process fake GitHub for contributor-layer tests (no network, no token).

``FakeGitHub.cli(login)`` returns a ``gh``-compatible runner for one user. Every
write verb is logged in ``writes``. When ``bot`` is on, each ``/claim`` comment
triggers the real claim bot (``.github/scripts/claim-bot.py``) for that event,
serialized per repository like the workflow's ``concurrency`` group.
"""
from __future__ import annotations

import datetime as _dt
import importlib.util
import json
import threading
from pathlib import Path
from typing import Any, Callable

ROOT = Path(__file__).resolve().parents[1]
REPO = "kvnloo/z0intelligence"


def load_claim_bot():
    spec = importlib.util.spec_from_file_location("claim_bot", ROOT / ".github" / "scripts" / "claim-bot.py")
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


class FakeGitHub:
    def __init__(self, tasks: list[dict[str, Any]], *, now: Callable[[], _dt.datetime], bot: bool = True,
                 issues: dict[int, dict[str, Any]] | None = None):
        self.tasks = tasks
        self.now = now
        self.bot = bot
        self.lock = threading.Lock()          # GitHub's own write serialization
        self.bot_lock = threading.Lock()      # workflow concurrency group
        self.issues: dict[int, dict[str, Any]] = issues or {}
        self.writes: list[tuple[str, list[str]]] = []
        self.bot_results: list[dict[str, Any]] = []
        self._claim_bot = load_claim_bot()

    def add_issue(self, n: int, *, labels=("claimable",), state="OPEN", title="t") -> None:
        self.issues[n] = {"state": state, "labels": list(labels), "comments": [], "title": title}

    # ------------------------------------------------------------ gh surface
    def cli(self, login: str) -> Callable[[list[str]], tuple[int, str, str]]:
        def run(args: list[str]) -> tuple[int, str, str]:
            try:
                return 0, self._dispatch(login, list(args)), ""
            except KeyError as exc:
                return 1, "", f"not found: {exc}"
        return run

    def _flag(self, args: list[str], name: str) -> str | None:
        return args[args.index(name) + 1] if name in args else None

    def _dispatch(self, login: str, args: list[str]) -> str:
        if args[:2] == ["api", "user"]:
            return login + "\n"
        if args[:2] == ["issue", "view"]:
            iss = self.issues[int(args[2])]
            with self.lock:
                return json.dumps({
                    "state": iss["state"], "labels": [{"name": n} for n in iss["labels"]],
                    "comments": [dict(c) for c in iss["comments"]],
                })
        if args[:2] == ["issue", "list"]:
            label = self._flag(args, "--label")
            state = (self._flag(args, "--state") or "open").upper()
            rows = [{"number": n, "title": i["title"], "state": i["state"],
                     "labels": [{"name": x} for x in i["labels"]]}
                    for n, i in sorted(self.issues.items())
                    if (label is None or label in i["labels"]) and (state == "ALL" or i["state"] == state)]
            return json.dumps(rows)
        if args[:2] == ["issue", "comment"]:
            n, body = int(args[2]), self._flag(args, "--body") or ""
            self.writes.append((login, args))
            with self.lock:
                self.issues[n]["comments"].append({"author": {"login": login}, "body": body,
                                                    "createdAt": self.now().strftime("%Y-%m-%dT%H:%M:%SZ")})
            if self.bot and login != "github-actions[bot]" and body.startswith("/claim"):
                self._run_bot(n, login, body)
            return f"https://github.com/{REPO}/issues/{n}#comment\n"
        if args[:2] == ["issue", "edit"]:
            n = int(args[2])
            self.writes.append((login, args))
            with self.lock:
                add = self._flag(args, "--add-label")
                if add and add not in self.issues[n]["labels"]:
                    self.issues[n]["labels"].append(add)
            return ""
        if args[:2] == ["pr", "create"]:
            self.writes.append((login, args))
            return f"https://github.com/{REPO}/pull/9999\n"
        if args and args[0] == "api" and "/issues/" in args[1] and args[1].endswith("/comments"):
            n = int(args[1].split("/issues/")[1].split("/")[0])
            with self.lock:
                page = [{"user": {"login": c["author"]["login"]}, "body": c["body"]}
                        for c in self.issues[n]["comments"]]
            return json.dumps([page])
        if args and args[0] == "api" and "/commits" in args[1]:
            return "[]"
        raise KeyError(" ".join(args[:3]))

    def _run_bot(self, n: int, login: str, body: str) -> None:
        event = {"comment": {"user": {"login": login}, "body": body},
                 "issue": {"number": n, "labels": [{"name": x} for x in self.issues[n]["labels"]]}}

        def bot_gh(args: list[str]) -> str:
            code, out, err = self.cli("github-actions[bot]")(args)
            if code != 0:
                raise RuntimeError(err)
            return out

        with self.bot_lock:
            res = self._claim_bot.handle(event, gh=bot_gh, repo=REPO, tasks=self.tasks, now=self.now())
            self.bot_results.append({"login": login, **res})
