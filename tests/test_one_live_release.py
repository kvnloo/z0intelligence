"""Release archive/pointer contracts, isolated from real user services."""
from pathlib import Path
import os
import subprocess

import pytest

SCRIPTS = Path(__file__).resolve().parents[1]/"scripts"


def run(*args, env=None, check=True):
    return subprocess.run(args, env=env, text=True, capture_output=True, check=check)


@pytest.fixture
def staged(tmp_path):
    repo = tmp_path/"repo"
    repo.mkdir()
    env = {**os.environ, "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1", "GIT_AUTHOR_NAME": "fixture", "GIT_AUTHOR_EMAIL": "fixture@example.invalid", "GIT_COMMITTER_NAME": "fixture", "GIT_COMMITTER_EMAIL": "fixture@example.invalid"}
    run("git", "-C", str(repo), "init", "-q", env=env)
    marker = repo/"marker"
    marker.write_text("first\n")
    run("git", "-C", str(repo), "add", "marker", env=env)
    run("git", "-C", str(repo), "commit", "-qm", "first", env=env)
    first = run("git", "-C", str(repo), "rev-parse", "HEAD", env=env).stdout.strip()
    marker.write_text("second\n")
    run("git", "-C", str(repo), "commit", "-qam", "second", env=env)
    second = run("git", "-C", str(repo), "rev-parse", "HEAD", env=env).stdout.strip()
    marker.write_text("uncommitted and never deployed\n")
    runtime = tmp_path/"runtime"
    (runtime/".venv/bin").mkdir(parents=True)
    python = runtime/".venv/bin/python"
    python.write_text("#!/bin/sh\nexit 0\n")
    python.chmod(0o755)
    (runtime/"RUNTIME.json").write_text("{}\n")
    base = tmp_path/"releases"
    env.update(Z0INT_RELEASE_SOURCE=str(repo), Z0INT_RELEASE_BASE=str(base))
    for sha in (first, second):
        run("bash", str(SCRIPTS/"stage-z0int-release.sh"), sha, str(runtime), env=env)
    commands = tmp_path/"commands"
    commands.mkdir()
    systemctl = commands/"systemctl"
    systemctl.write_text('#!/bin/sh\nprintf "%s\\n" "$*" >> "$CALL_LOG"\n')
    systemctl.chmod(0o755)
    env.update(PATH=str(commands)+os.pathsep+env["PATH"], CALL_LOG=str(tmp_path/"calls"))
    try:
        yield base, runtime, first, second, env
    finally:
        # Only these fixture-owned directories are unsealed. Production releases
        # and the referenced runtime are never traversed or modified.
        for directory, _, _ in os.walk(base, followlinks=False):
            path = Path(directory)
            path.chmod(path.stat().st_mode | 0o700)


def test_archive_is_committed_read_only_and_idempotent(staged):
    base, runtime, first, _, env = staged
    release = base/first
    assert (release/"marker").read_text() == "first\n"
    assert (release/"DEPLOYED_SHA").read_text().strip() == first
    assert (release/".venv").resolve() == runtime/".venv"
    assert (release/"marker").stat().st_mode & 0o222 == 0
    before = (release/"marker").stat().st_mtime_ns
    run("bash", str(SCRIPTS/"stage-z0int-release.sh"), first, str(runtime), env=env)
    assert (release/"marker").stat().st_mtime_ns == before
    assert not (base/"current").exists()


def test_switch_and_rollback_restart_only_declared_units(staged):
    base, _, first, second, env = staged
    script = str(SCRIPTS/"switch-z0int-release.sh")
    run("bash", script, first, "owned-proof.service", env=env)
    assert (base/"current/marker").read_text() == "first\n"
    run("bash", script, second, "owned-proof.service", env=env)
    assert (base/"current/marker").read_text() == "second\n"
    run("bash", script, first, "owned-proof.service", env=env)
    assert (base/"current/marker").read_text() == "first\n"
    assert Path(env["CALL_LOG"]).read_text().splitlines() == ["--user restart owned-proof.service"]*3


def test_unstaged_switch_preserves_current_and_does_not_restart(staged):
    base, _, first, _, env = staged
    (base/"current").symlink_to(base/first)
    result = run("bash", str(SCRIPTS/"switch-z0int-release.sh"), "0"*40, env=env, check=False)
    assert result.returncode != 0
    assert (base/"current").resolve() == base/first
    assert not Path(env["CALL_LOG"]).exists()
