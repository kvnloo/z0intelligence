"""scripts/scoreboard.py joins harness sessions to merged PRs by branch; fixtures only, no gh or network."""
import importlib.util
import calendar
import json
import os
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("scoreboard", ROOT / "scripts" / "scoreboard.py")
sb = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sb)

HEAD = "a" * 40
NOW = time.time()


def _iso(ts):
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(ts))


def _hermes_db(path, rows):
    con = sqlite3.connect(path)
    con.execute("create table sessions (id text, parent_session_id text, git_branch text, cwd text, started_at real,"
                " input_tokens int, cache_write_tokens int, output_tokens int, cache_read_tokens int)")
    con.executemany("insert into sessions values (?,?,?,?,?,?,?,?,?)", rows)
    con.commit(); con.close()


def _git_worktree(path, branch):
    subprocess.run(["git", "init", "-q", "-b", branch, str(path)], check=True)
    subprocess.run(["git", "-C", str(path), "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "--allow-empty", "-m", "x"], check=True)


def test_sessions_attribute_by_branch_cwd_and_parent(tmp_path, capsys):
    wt = tmp_path / "wt"; _git_worktree(wt, "salvage/hermes/h1")
    home = tmp_path / "hermes"; (home / "profiles" / "p").mkdir(parents=True)
    _hermes_db(home / "state.db", [
        ("s1", None, "salvage/hermes/h1", None, NOW - 60, 100, 10, 5, 1000),   # branch recorded
        ("s2", None, None, str(wt), NOW - 60, 200, 0, 20, 0),                   # branch from cwd
        ("s3", "s1", None, None, NOW - 60, 50, 0, 5, 0),                        # child inherits parent
        ("s4", None, "master", None, NOW - 60, 9999, 0, 0, 0),                  # default branch: unattributed
        ("s5", None, "salvage/hermes/h1", None, NOW - 30 * 86400, 7777, 0, 0, 0),  # outside window
    ])
    _hermes_db(home / "profiles" / "p" / "state.db", [("p1", None, "salvage/claude/c2", None, NOW - 60, 40, 0, 4, 0)])
    codex = tmp_path / "codex" / "2026" / "10"; codex.mkdir(parents=True)
    (codex / "r.jsonl").write_text("\n".join(json.dumps(x) for x in [
        {"type": "session_meta", "payload": {"id": "c1", "cwd": str(wt), "git": {}}},
        {"type": "event_msg", "payload": {"type": "token_count", "info": {"total_token_usage": {"input_tokens": 300, "cached_input_tokens": 100, "output_tokens": 30}}}},
    ]) + "\n")
    claude = tmp_path / "claude" / "projects" / "x"; claude.mkdir(parents=True)
    usage = {"input_tokens": 7, "cache_creation_input_tokens": 3, "output_tokens": 2, "cache_read_input_tokens": 50}
    (claude / "s.jsonl").write_text("\n".join(json.dumps(x) for x in [
        {"gitBranch": "salvage/claude/c2", "cwd": "/nowhere", "message": {"id": "m1", "usage": usage}},
        {"gitBranch": "salvage/claude/c2", "cwd": "/nowhere", "message": {"id": "m1", "usage": usage}},  # streamed duplicate
        {"gitBranch": "HEAD", "cwd": str(wt), "message": {"id": "m2", "usage": usage}},                  # detached: cwd decides
    ]) + "\n")
    prs = {"prs": [
        {"number": 153, "headRefName": "salvage/hermes/h1", "headRefOid": HEAD, "state": "MERGED", "mergedAt": _iso(NOW - 10), "closedAt": _iso(NOW - 10)},
        {"number": 152, "headRefName": "salvage/claude/c2", "headRefOid": "b" * 40, "state": "MERGED", "mergedAt": _iso(NOW - 10), "closedAt": _iso(NOW - 10)},
        {"number": 141, "headRefName": "fix/e2e-hot-path", "headRefOid": "c" * 40, "state": "CLOSED", "mergedAt": None, "closedAt": _iso(NOW - 10)},
        {"number": 150, "headRefName": "factory/codex/54-receipt-replay", "headRefOid": "d" * 40, "state": "OPEN", "mergedAt": None, "closedAt": None},
        # a fork PR whose head branch is literally "master" must not absorb default-branch sessions
        {"number": 99, "headRefName": "master", "headRefOid": "f" * 40, "state": "MERGED", "mergedAt": _iso(NOW - 10), "closedAt": _iso(NOW - 10)},
    ], "comments": {
        "153": [{"body": f"VERDICT: ACCEPT\nhead: {HEAD}\nchecked: x"}],
        "152": [{"body": f"VERDICT: ACCEPT\nhead: {'e' * 40}\nchecked: stale head"}],
    }}
    pj = tmp_path / "prs.json"; pj.write_text(json.dumps(prs))
    assert sb.main(["--days", "7", "--hermes-home", str(home), "--codex-sessions", str(tmp_path / "codex"),
                    "--claude-projects", str(tmp_path / "claude" / "projects"), "--prs-json", str(pj), "--json"]) == 0
    out = json.loads(capsys.readouterr().out)
    by = {r["number"]: r for r in out["prs"]}
    # h1: s1 115 + s2 220 + s3 55 + codex (200 uncached + 30) + claude m2 12 = 632
    assert by[153]["fresh"] == 632 and by[153]["sessions"] == 5 and by[153]["cross_checked"] is True
    # c2: profile session 44 + claude m1 once 12 = 56; verdict names another head
    assert by[152]["fresh"] == 56 and by[152]["cross_checked"] is False
    assert by[99]["fresh"] == 0
    assert out["merged"] == 3 and out["cross_checked"] == 1 and out["open_prs"] == 1
    assert out["closed_unmerged"] == 1 and out["closed_unmerged_share"] == 0.25
    assert out["fresh_tokens_per_cross_checked_pr"] == 632
    assert out["fresh_tokens_total"] == 632 + 56 + 9999
    assert out["fresh_tokens_per_merged_pr"] == 229
    assert out["fresh_tokens_attributed_share"] == 0.064


def _claude_row(mid, branch, output, **usage):
    return {"type": "assistant", "gitBranch": branch,
            "message": {"id": mid, "usage": {"output_tokens": output, **usage}}}


def test_claude_discovers_nested_workflows_and_deduplicates_root_aliases(tmp_path):
    projects = tmp_path / "projects"
    fixtures = {
        "project/root.jsonl": ("root", 100),
        "project/root/subagents/child.jsonl": ("child", 200),
        "project/root/subagents/workflow/deep.jsonl": ("deep", 300),
        "project/missing-parent/subagents/orphan.jsonl": ("orphan", 400),
        "project/root/subagents/old.jsonl": ("old", 999),
    }
    for relative, (mid, output) in fixtures.items():
        path = projects / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(_claude_row(mid, "fix/nested", output)) + "\n")
        os.utime(path, (NOW - (1000 if mid == "old" else 10),) * 2)
    alias = tmp_path / "alias"
    alias.symlink_to(projects, target_is_directory=True)
    rows = sb.claude_sessions([str(projects), str(alias)], NOW - 100)
    assert len(rows) == 4
    assert {row["branch"] for row in rows} == {"fix/nested"}
    assert sum(row["fresh"] for row in rows) == 1000


def test_claude_streamed_usage_keeps_last_observation_per_message(tmp_path):
    project = tmp_path / "projects" / "project"
    project.mkdir(parents=True)
    rows = [
        _claude_row("message-1", "fix/stream", 2, input_tokens=7,
                    cache_creation_input_tokens=3, cache_read_input_tokens=50),
        _claude_row("message-1", "fix/stream", 200, input_tokens=70,
                    cache_creation_input_tokens=30, cache_read_input_tokens=500),
        _claude_row("message-1", "fix/stream", 200, input_tokens=70,
                    cache_creation_input_tokens=30, cache_read_input_tokens=500),
        _claude_row("message-2", "fix/other", 10),
    ]
    (project / "session.jsonl").write_text("\n".join(map(json.dumps, rows)) + "\n")
    result = sb.claude_sessions([str(project.parent)], 0)
    by = {row["branch"]: row for row in result}
    assert by["fix/stream"]["fresh"] == 300
    assert by["fix/stream"]["cached"] == 500
    assert by["fix/other"]["fresh"] == 10


def test_bad_claude_rows_do_not_discard_valid_usage(tmp_path):
    project = tmp_path / "projects" / "project"
    project.mkdir(parents=True)
    rows = [
        json.dumps(_claude_row("before", "fix/partial", 11)),
        '{"message":{"usage":',
        '["usage"]',
        '{"message":["usage"]}',
        '{"message":{"id":"invalid","usage":[1]}}',
        json.dumps(_claude_row("invalid-count", "fix/partial", "bad")),
        json.dumps(_claude_row("invalid-branch", ["bad"], 999)),
        json.dumps(_claude_row(["invalid-id"], "fix/partial", 999)),
        json.dumps(_claude_row("after", "fix/partial", 22)),
    ]
    (project / "session.jsonl").write_text("\n".join(rows) + "\n")
    result = sb.claude_sessions([str(project.parent)], 0)
    assert len(result) == 1
    assert result[0]["fresh"] == 33


def test_pr_window_uses_utc_during_daylight_saving_time(monkeypatch):
    if not hasattr(time, "tzset"):
        return
    previous = os.environ.get("TZ")
    since = calendar.timegm(time.strptime("2026-07-01T11:30:00", "%Y-%m-%dT%H:%M:%S"))
    prs = [{"number": 1, "headRefName": "fix/utc", "headRefOid": HEAD,
            "state": "MERGED", "mergedAt": "2026-07-01T12:00:00Z"}]
    try:
        for timezone in ("UTC0", "EST5EDT", "PST8PDT"):
            monkeypatch.setenv("TZ", timezone)
            time.tzset()
            assert sb.score(prs, [], {}, since)["merged"] == 1, timezone
    finally:
        if previous is None:
            monkeypatch.delenv("TZ", raising=False)
        else:
            monkeypatch.setenv("TZ", previous)
        time.tzset()


def test_native_cli_counts_nested_final_usage_from_owned_fixtures(tmp_path):
    project = tmp_path / "projects" / "project"
    nested = project / "session" / "subagents"
    nested.mkdir(parents=True)
    initial = _claude_row("stream", "fix/cli", 2, input_tokens=7,
                          cache_creation_input_tokens=3, cache_read_input_tokens=50)
    final = _claude_row("stream", "fix/cli", 200, input_tokens=70,
                        cache_creation_input_tokens=30, cache_read_input_tokens=500)
    (project / "session.jsonl").write_text(json.dumps(initial) + "\n" + json.dumps(final) + "\n")
    (nested / "worker.jsonl").write_text(json.dumps(_claude_row("child", "fix/cli", 500)) + "\n")
    prs = {"prs": [{"number": 1, "headRefName": "fix/cli", "headRefOid": HEAD,
                     "state": "MERGED", "mergedAt": _iso(time.time())}],
           "comments": {"1": [{"body": f"VERDICT: ACCEPT\nhead: {HEAD}"}]}}
    pr_path = tmp_path / "prs.json"
    pr_path.write_text(json.dumps(prs))
    env = dict(os.environ, Z0INT_HOME=str(tmp_path / "state"),
               HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1")
    env.pop("PYTHONPATH", None)  # exercise the standalone repository script
    result = subprocess.run([
        sys.executable, str(ROOT / "scripts" / "scoreboard.py"), "--days", "7",
        "--hermes-home", str(tmp_path / "empty-hermes"),
        "--codex-sessions", str(tmp_path / "empty-codex"),
        "--claude-projects", str(project.parent), "--prs-json", str(pr_path), "--json",
    ], env=env, text=True, capture_output=True, check=True)
    out = json.loads(result.stdout)
    assert out["fresh_tokens_total"] == 800
    assert out["fresh_tokens_per_merged_pr"] == 800
    assert out["fresh_tokens_per_cross_checked_pr"] == 800
    assert out["fresh_tokens_attributed_share"] == 1.0
    assert out["prs"][0]["cached"] == 500
    assert out["prs"][0]["sessions"] == 2
