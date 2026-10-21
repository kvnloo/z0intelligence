import json
import os
from pathlib import Path

import pytest
from z0int.fsm import supervise as S


@pytest.mark.parametrize("cmd", [
    "echo 'cargo build'", "rg 'npm test' docs", "git log --grep='cargo build'",
    "cd /repo && ssh mbp 'cargo build'", "timeout 20 ssh mbp 'npm test'",
    "cd /repo; fleet-build -- bun test", "python -c 'print(\"cargo build\")'",
    "cat <<'EOF'\ncargo build\nEOF", "git show HEAD:package.json",
])
def test_mentions_and_remote_are_not_local_heavy(cmd):
    assert not S.local_heavy(cmd)


@pytest.mark.parametrize("cmd", ["cargo build", "cd /r && npm run build", 
    "flock /x/heavy.lock nice -n 10 cargo test", "ssh mbp 'true'; cargo build",
    "fleet-build -- true && bun test", "bash -c 'cargo build'"])
def test_actual_local_heavy(cmd):
    assert S.local_heavy(cmd)


def task():
    return S.Task("deleg_old/task-0", "recover widget", 1, "interrupted", 10, "/old/task-0.log")


@pytest.mark.parametrize("action,phase", [("abandon", "Abandoned"), ("resume", "Resumed"),
    ("resumed", "Resumed"), ("integrated", "Integrated"), ("already integrated", "Integrated")])
def test_decisions_clear_overdue(action, phase):
    t = task()
    w = S.worker_traces([t], {}, {"_spawned": {}, "_reaped": {}},
                         {t.id: {"action": action}}, 10000, 100)[0]
    assert w.final.phase.value == phase
    assert not any(f.code == "interrupted_past_limit" for f in w.findings)


@pytest.mark.parametrize("source", ["cli", "fleet"])
def test_non_delegated_successor(source):
    t = task()
    c = S.Call("next", 20, "read_file", {"path": t.log}, t.log)
    calls = {"next": [c]}
    sessions = {"next": {"source": source, "title": "Resume widget"}}
    successors = S.session_successors([t], calls, sessions)
    w = S.worker_traces([t], calls, {"_spawned": {}, "_reaped": {}}, {}, 10000, 100, successors)[0]
    assert w.final.phase.value == "Resumed"
    assert w.meta["resumed_from_log"]
    assert not S.session_successors([t], calls, {"next": {"title": "audit logs"}})


def test_read_only_git_mentions_do_not_take_lease():
    calls = [S.Call("s", i, "terminal", {"command": cmd, "workdir": "/r"}, "")
             for i, cmd in enumerate(["git status", "git tag", "git tag --list", 
                 "echo 'git commit'", "rg 'git push'", "git log --grep='git commit'", "git show HEAD",
                 "git stash list", "git stash show", "git tag --sort=-creatordate", "git push --dry-run"])]
    assert S.writes(calls, S.Roots(["/r"])) == []
    assert len(S.writes([S.Call("s", 1, "terminal", {"command": "git -C /r commit -m fix"}, "")], S.Roots(["/r"]))) == 1
