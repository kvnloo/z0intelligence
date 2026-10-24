import concurrent.futures
import json
import os
import sys
from pathlib import Path
import subprocess

from z0int.fsm import gate


def repo(tmp_path):
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    return str(tmp_path)


def test_lease_lifecycle_dead_reclaim_and_pid_reuse(tmp_path):
    wt = repo(tmp_path)
    result, code = gate.lease("acquire", wt, "a", os.getpid())
    assert code == 0 and result["status"] == "acquired"
    assert gate.lease("acquire", wt, "b", os.getpid())[1] == 3
    assert gate.lease("release", wt, "b", os.getpid())[1] == 3
    assert gate.lease("status", wt, "b", os.getpid())[0]["status"] == "held"
    assert gate.lease("release", wt, "a", os.getpid())[1] == 0
    child = subprocess.Popen(["sleep", "20"])
    try:
        assert gate.lease("acquire", wt, "dead", child.pid)[1] == 0
    finally:
        child.terminate()
        child.wait()
    assert gate.lease("acquire", wt, "b", os.getpid())[0]["status"] == "acquired"
    path, _ = gate.lease_path(wt)
    record = json.loads(path.read_text())
    record["process_start"] = "old-boot:1"
    path.write_text(json.dumps(record))
    assert gate.lease("acquire", wt, "new", os.getpid())[1] == 0
    record["host"] = "another-host"
    path.write_text(json.dumps(record))
    assert gate.lease("acquire", wt, "new", os.getpid())[1] == 3
    path.write_text("{")
    assert gate.lease("acquire", wt, "new", os.getpid())[1] == 3


def test_lease_atomic_acquire(tmp_path):
    wt = repo(tmp_path)
    def acquire(i):
        result = subprocess.run([sys.executable, "-m", "z0int.fsm.gate", "lease", "acquire",
            "--worktree", wt, "--holder", str(i), "--pid", str(os.getpid())], capture_output=True, text=True)
        return result.returncode
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        codes = list(pool.map(acquire, range(8)))
    assert codes.count(0) == 1 and codes.count(3) == 7


def test_heavy_gate():
    assert gate.heavy("cargo build")[1] == 3
    assert gate.heavy("fleet-build -- cargo build")[1] == 0
    assert gate.heavy("cd /x; ssh mbp 'npm test'")[1] == 0
    assert gate.heavy("echo 'cargo build'")[1] == 0
    assert gate.heavy("echo cuda; cargo build")[1] == 3
    assert gate.heavy("hermes bend verify /x")[1] == 0


