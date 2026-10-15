"""Build drift-proof pinned fixtures for the State Packet benchmark (Lane E).

Live repos under ~/workspace and live ~/.claude/projects keep changing, so current-work
answer keys drift. This script freezes:

  <pin>/<repo>/            git clone --no-hardlinks of the local repo, with ALL local branches,
                           the source's origin/* remote-tracking refs, branch upstream config,
                           and a deterministic worktree/branch situation recreated inside it.
  <pin>/projects/          a SMALL copy of Claude Code transcripts whose cwd is inside the
                           original repos, with /home/<u>/workspace paths rewritten to <pin>
                           (so the fixture paths and transcript cwds agree).
  <pin>/MANIFEST.json      HEAD SHAs + worktrees + transcript list (local only).

Everything lands under ~/.z0int (never in git). The origin URL of every fixture is set to a
non-existent local path so nothing (git fetch, gh, SessionStart hooks) can pull live state in.
The script refuses to overwrite an existing pin dir (create-only).

    .venv/bin/python benchmarks/state_packet/pin_fixtures.py --pin ~/.z0int/research/claude-code-overnight/pinned \
        --session 20af362c-a466-4c7c-8591-cc5a1696205f \
        --agents a547f71cc200a849f a5a881e0f9d9b6456 abc30d057d833a884 a1a025ce725db145d
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
from pathlib import Path

HOME = Path.home()
LIVE = HOME / "workspace"

# Deterministic fixture layout: which ref each worktree gets, pinned by SHA from the source.
SPEC = {
    "z0intelligence": {
        "main": "feat/claude-code-harness",
        "worktrees": [  # (path relative to fixture repo or "../<name>" for external, branch)
            (".claude/worktrees/agent-a5a881e0f9d9b6456", "exp/bend-aodl-gate"),
            (".claude/worktrees/agent-abc30d057d833a884", "feat/state-packet-v0"),
            (".claude/worktrees/agent-a1a025ce725db145d", "exp/semantic-skill-select"),
            ("../z0intelligence-lab", "feat/portable-lab"),
        ],
        "exclude": [".claude/"],
    },
    "aodl": {
        "main": "ai-native/oss-loop",
        "worktrees": [(".worktrees/dash-wired", "catalog/dash-pi-fx")],
        "stash": {"file": "README.md", "append": "\n<!-- pinned-fixture: stashed wip note -->\n",
                  "message": "pinned-fixture wip: readme note"},
    },
    "z0evals": {
        "main": "main",
        "worktrees": [("../z0evals-cc", "study/claude-code-savings-v0")],
        "dirty": {"file": "README.md", "append": "\n<!-- pinned-fixture: uncommitted local edit -->\n"},
    },
}

FIXED_ENV = {
    "GIT_AUTHOR_NAME": "pinned-fixture", "GIT_AUTHOR_EMAIL": "fixture@invalid",
    "GIT_COMMITTER_NAME": "pinned-fixture", "GIT_COMMITTER_EMAIL": "fixture@invalid",
    "GIT_AUTHOR_DATE": "2026-09-30T12:00:00Z", "GIT_COMMITTER_DATE": "2026-09-30T12:00:00Z",
    "GIT_OPTIONAL_LOCKS": "0",
}


def git(repo: Path, *a: str, check: bool = True) -> str:
    import os
    p = subprocess.run(["git", "-C", str(repo), *a], capture_output=True, text=True,
                       env=dict(os.environ, **FIXED_ENV))
    if check and p.returncode:
        raise SystemExit(f"git {' '.join(a)} in {repo} failed: {p.stderr}")
    return p.stdout.strip()


def build_repo(name: str, pin: Path) -> dict:
    src, dst = LIVE / name, pin / name
    spec = SPEC[name]
    subprocess.run(["git", "clone", "--no-hardlinks", "--no-checkout", "--quiet", str(src), str(dst)], check=True)
    # replace clone-made origin/* (= source local heads) with the source's own origin/* refs + all local heads
    for ref in git(dst, "for-each-ref", "--format=%(refname)", "refs/remotes/origin").splitlines():
        git(dst, "update-ref", "-d", ref, check=False)
    git(dst, "fetch", "--quiet", "--no-tags", "--update-head-ok", str(src),
        "+refs/remotes/origin/*:refs/remotes/origin/*", "+refs/heads/*:refs/heads/*", "+refs/tags/*:refs/tags/*")
    git(dst, "symbolic-ref", "refs/remotes/origin/HEAD", git(src, "symbolic-ref", "refs/remotes/origin/HEAD"))
    # branch upstream config, exactly as in the source
    for line in git(src, "config", "--get-regexp", r"^branch\.", check=False).splitlines():
        k, _, v = line.partition(" ")
        git(dst, "config", k, v)
    git(dst, "remote", "set-url", "origin", f"/nonexistent/pinned-remote/{name}.git")
    (dst / ".git" / "FETCH_HEAD").unlink(missing_ok=True)
    for pat in spec.get("exclude", []):
        with (dst / ".git" / "info" / "exclude").open("a") as fh:
            fh.write(pat + "\n")
    git(dst, "checkout", "--quiet", spec["main"])
    wts = []
    for rel, branch in spec["worktrees"]:
        path = (dst / rel).resolve() if not rel.startswith("../") else pin / rel[3:]
        git(dst, "worktree", "add", "--quiet", str(path), branch)
        wts.append({"path": str(path), "branch": branch, "sha": git(path, "rev-parse", "HEAD")})
    if "stash" in spec:
        s = spec["stash"]
        with (dst / s["file"]).open("a") as fh:
            fh.write(s["append"])
        git(dst, "stash", "push", "--quiet", "-m", s["message"])
    if "dirty" in spec:
        d = spec["dirty"]
        with (dst / d["file"]).open("a") as fh:
            fh.write(d["append"])
    return {
        "source": str(src), "head": git(dst, "rev-parse", "HEAD"), "branch": git(dst, "branch", "--show-current"),
        "local_branches": len(git(dst, "for-each-ref", "refs/heads").splitlines()),
        "remote_refs": len(git(dst, "for-each-ref", "refs/remotes").splitlines()),
        "worktrees": wts, "status_porcelain": git(dst, "status", "--porcelain"),
        "stash_count": len(git(dst, "stash", "list").splitlines()),
    }


def enc(p: Path) -> str:
    return re.sub(r"[^A-Za-z0-9]", "-", str(p))


def rewrite(text: str, pin: Path) -> str:
    tilde_pin = "~/" + str(pin.relative_to(HOME))
    return (text.replace(str(LIVE), str(pin)).replace("~/workspace", tilde_pin)
            .replace(enc(LIVE), enc(pin)))


def copy_transcripts(pin: Path, session: str, agents: list[str]) -> list[dict]:
    src_dir = HOME / ".claude" / "projects" / enc(LIVE) / session / "subagents"
    dst_dir = pin / "projects" / enc(pin) / session / "subagents"
    dst_dir.mkdir(parents=True, exist_ok=False)
    out = []
    for a in agents:
        for suffix in (".jsonl", ".meta.json"):
            f = src_dir / f"agent-{a}{suffix}"
            if not f.is_file():
                continue
            data = f.read_text(encoding="utf-8")
            if suffix == ".jsonl":  # drop a trailing partial line, if any
                data = data[: data.rfind("\n") + 1]
            (dst_dir / f.name).write_text(rewrite(data, pin), encoding="utf-8")
        lines = (dst_dir / f"agent-{a}.jsonl").read_text().splitlines()
        cwds = sorted({json.loads(ln).get("cwd") for ln in lines if ln.strip()} - {None})
        out.append({"agent": a, "lines": len(lines), "cwds": cwds,
                    "first_ts": json.loads(lines[0]).get("timestamp"), "last_ts": json.loads(lines[-1]).get("timestamp")})
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pin", required=True)
    ap.add_argument("--session", required=True)
    ap.add_argument("--agents", nargs="+", required=True)
    a = ap.parse_args()
    pin = Path(a.pin).expanduser()
    if pin.exists():
        raise SystemExit(f"{pin} exists (create-only)")
    pin.mkdir(parents=True)
    manifest = {"pin": str(pin), "repos": {n: build_repo(n, pin) for n in SPEC},
                "transcripts": copy_transcripts(pin, a.session, a.agents)}
    (pin / "MANIFEST.json").write_text(json.dumps(manifest, indent=1))
    print(json.dumps({n: {k: r[k] for k in ("head", "branch", "stash_count")} | {"wts": len(r["worktrees"])}
                      for n, r in manifest["repos"].items()}, indent=1))


if __name__ == "__main__":
    main()
