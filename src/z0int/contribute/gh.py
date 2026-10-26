"""Thin GitHub access through the ``gh`` CLI with an injectable runner.

Reads are always allowed (claim state is re-read at S3 and S7, never cached).
The write verbs (:meth:`GH.comment`, :meth:`GH.pr_create`) are only called by the
CLI after the human's explicit yes for that exact post.
"""
from __future__ import annotations

import json
import shutil
import subprocess
from typing import Any, Callable

Runner = Callable[[list[str]], tuple[int, str, str]]
DEFAULT_REPO = "kvnloo/z0intelligence"


def _default_runner(args: list[str]) -> tuple[int, str, str]:
    try:
        p = subprocess.run(["gh", *args], capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return 127, "", f"{type(exc).__name__}: {exc}"
    return p.returncode, p.stdout, p.stderr


class GHError(RuntimeError):
    pass


class GH:
    def __init__(self, repo: str = DEFAULT_REPO, runner: Runner | None = None):
        self.repo = repo
        self._runner = runner
        self.writes: list[list[str]] = []  # audit trail of write verbs actually run

    def available(self) -> bool:
        return self._runner is not None or shutil.which("gh") is not None

    def _run(self, args: list[str]) -> str:
        code, out, err = (self._runner or _default_runner)(args)
        if code != 0:
            raise GHError(f"gh {' '.join(args[:2])} failed ({code}): {err.strip()[:200]}")
        return out

    def _json(self, args: list[str]) -> Any:
        out = self._run(args)
        return json.loads(out) if out.strip() else None

    # ---- reads
    def login(self) -> str | None:
        try:
            out = self._run(["api", "user", "--jq", ".login"]).strip()
        except GHError:
            return None
        return out or None

    def issue(self, number: int) -> dict[str, Any]:
        d = self._json(["issue", "view", str(number), "-R", self.repo, "--json", "state,labels,comments"]) or {}
        return {
            "state": d.get("state", ""),
            "labels": [lb.get("name", "") for lb in d.get("labels") or []],
            "comments": d.get("comments") or [],
        }

    def issues(self, *, label: str | None = None, state: str = "all") -> list[dict[str, Any]]:
        args = ["issue", "list", "-R", self.repo, "--state", state, "--limit", "500", "--json", "number,title,state,labels"]
        if label:
            args += ["--label", label]
        rows = self._json(args) or []
        return [{"number": int(r["number"]), "title": r.get("title", ""), "state": r.get("state", ""),
                 "labels": [lb.get("name", "") for lb in r.get("labels") or []]} for r in rows]

    # ---- writes (human-approved only)
    def comment(self, number: int, body: str) -> None:
        args = ["issue", "comment", str(number), "-R", self.repo, "--body", body]
        self.writes.append(args)
        self._run(args)

    def pr_create(self, *, base: str, head: str, title: str, body_file: str) -> str:
        args = ["pr", "create", "-R", self.repo, "--draft", "--base", base, "--head", head,
                "--title", title, "--body-file", body_file]
        self.writes.append(args)
        return self._run(args).strip()
