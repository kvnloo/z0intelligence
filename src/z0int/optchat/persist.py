"""Persist the log. The reference commits the directory with git."""
from __future__ import annotations

import subprocess
from pathlib import Path


def persist(root: Path) -> str:
    root.mkdir(parents=True, exist_ok=True)
    git = ["git", "-C", str(root)]
    subprocess.run([*git, "init", "-q"], check=True)
    subprocess.run([*git, "add", "-A"], check=True)
    commit = subprocess.run(
        [
            *git,
            "-c", "user.email=optchat@local",
            "-c", "user.name=OptChat",
            "commit", "-q", "-m", "optchat", "--allow-empty",
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    rev = subprocess.run([*git, "rev-parse", "--short", "HEAD"], check=True, capture_output=True, text=True)
    return rev.stdout.strip() or commit.stdout.strip()
