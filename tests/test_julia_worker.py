"""Hermetic JSONL protocol tests for the out-of-process Julia worker.

The worker must never be imported in-process: importing it redirects stdout.
Each test starts a fresh interpreter with only fake julia and torch packages.
"""

from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest


WORKER = importlib.util.find_spec("z0int.backends.julia_worker").origin


@pytest.fixture
def fake_dir(tmp_path: Path) -> Path:
    """Provide stand-in packages without importing Julia or torch in the test."""
    root = tmp_path / "fake"
    julia = root / "julia"
    torch = root / "torch"
    julia.mkdir(parents=True)
    torch.mkdir()

    (julia / "__init__.py").write_text(
        """\
import os

COUNT = 0


def load_model(model_dir, **kw):
    global COUNT
    print("chatter")
    if os.environ.get("FAKE_JULIA_FAIL") == "1":
        raise RuntimeError("no weights")
    COUNT += 1

    class Engine:
        def predict(self, *, state, questions):
            import torch
            if state == "empty":
                return {}
            return {
                "answers": {
                    "state": state,
                    "questions": questions,
                    "kw": kw,
                    "md": model_dir,
                    "loads": COUNT,
                    "ac": torch.autocast.__name__,
                }
            }

    return Engine()
""",
        encoding="utf-8",
    )
    (torch / "__init__.py").write_text(
        "class autocast:\n    pass\n",
        encoding="utf-8",
    )
    return root


@pytest.fixture
def run(fake_dir: Path):
    """Send complete JSONL input and collect only the worker's protocol lines."""

    def _run(
        lines: list[str],
        *flags: str,
        fail: bool = False,
        model_dir: bool = True,
    ):
        args = [sys.executable, WORKER]
        if model_dir:
            args.extend(["--model-dir", "mdir"])
        args.extend(flags)
        env = {"PATH": os.environ["PATH"], "PYTHONPATH": str(fake_dir)}
        if fail:
            env["FAKE_JULIA_FAIL"] = "1"
        proc = subprocess.run(
            args,
            input="\n".join(lines) + ("\n" if lines else ""),
            capture_output=True,
            text=True,
            timeout=30,
            env=env,
        )
        replies = [json.loads(line) for line in proc.stdout.splitlines()]
        return proc, replies

    return _run


def test_status_before_load(run):
    proc, replies = run(
        [json.dumps({"op": "status"})],
        "--device", "cuda:0", "--strict",
        "--max-length", "100", "--head-length", "7",
    )
    assert proc.returncode == 0, proc.stderr
    assert len(replies) == 1
    status = replies[0]
    assert status["op"] == "status"
    assert status["ok"] is True
    assert status["loaded"] is False
    assert status["load_ms"] is None
    assert status["load_error"] is None
    assert status["device"] == "cuda:0"
    assert status["model_dir"] == "mdir"
    assert status["strict_encoding"] is True
    assert status["max_length"] == 100
    assert status["head_length"] == 7
    assert status["autocast"] == "auto"
    assert isinstance(status["pid"], int) and status["pid"] > 0


def test_predict_round_trip(run):
    proc, replies = run(
        [json.dumps({"id": "r1", "state": "s", "questions": {"q": 1}})]
    )
    assert proc.returncode == 0, proc.stderr
    assert len(replies) == 1
    reply = replies[0]
    assert reply["id"] == "r1"
    assert reply["ok"] is True
    assert reply["answers"]["state"] == "s"
    assert reply["answers"]["questions"] == {"q": 1}
    assert reply["answers"]["md"] == "mdir"
    assert reply["answers"]["kw"] == {
        "device": "cpu",
        "strict_encoding": False,
        "max_length": 8192,
        "head_length": 512,
    }
    assert isinstance(reply["latency_ms"], float)
    assert isinstance(reply["load_ms"], float)
    assert reply["device"] == "cpu"


def test_warm_then_predict_loads_once(run):
    proc, replies = run([
        json.dumps({"op": "warm"}),
        json.dumps({"op": "status"}),
        json.dumps({"id": "r1", "state": "first", "questions": {"q": 1}}),
        json.dumps({"id": "r2", "state": "second", "questions": {"q": 2}}),
    ])
    assert proc.returncode == 0, proc.stderr
    assert len(replies) == 4
    warm, status, first, second = replies
    assert warm["op"] == "warm"
    assert warm["ok"] is True
    assert warm["device"] == "cpu"
    assert isinstance(warm["load_ms"], float)
    assert status["op"] == "status"
    assert status["loaded"] is True
    assert status["load_error"] is None
    assert isinstance(status["load_ms"], float)
    assert status["load_ms"] == warm["load_ms"]
    assert [first["id"], second["id"]] == ["r1", "r2"]
    assert first["ok"] is True and second["ok"] is True
    assert first["answers"]["loads"] == 1
    assert second["answers"]["loads"] == 1
    assert first["load_ms"] == second["load_ms"] == warm["load_ms"]


def test_invalid_json_does_not_kill_worker(run):
    proc, replies = run(["not json", "", json.dumps({"op": "status"})])
    assert proc.returncode == 0, proc.stderr
    assert len(replies) == 2
    assert replies[0]["id"] is None
    assert replies[0]["ok"] is False
    assert replies[0]["error"].startswith("invalid JSON:")
    assert replies[1]["op"] == "status"
    assert replies[1]["ok"] is True
    assert replies[1]["loaded"] is False


def test_shutdown_stops_processing(run):
    proc, replies = run([
        json.dumps({"op": "shutdown"}),
        json.dumps({"op": "status"}),
    ])
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout == ""
    assert replies == []


def test_request_error_is_reported_per_line(run):
    proc, replies = run([
        json.dumps({"id": "r2", "state": "s"}),
        json.dumps({"id": "r3", "state": "s", "questions": {"q": 1}}),
    ])
    assert proc.returncode == 0, proc.stderr
    assert len(replies) == 2
    bad, good = replies
    assert bad["id"] == "r2"
    assert bad["ok"] is False
    assert bad["error"] == "KeyError: 'questions'"
    assert "traceback" in bad
    assert 0 < len(bad["traceback"]) <= 800
    assert good["id"] == "r3"
    assert good["ok"] is True
    assert good["answers"]["loads"] == 1
    assert good["answers"]["questions"] == {"q": 1}


def test_missing_answers_become_empty_dict(run):
    proc, replies = run([
        json.dumps({"id": "empty-1", "state": "empty", "questions": {}})
    ])
    assert proc.returncode == 0, proc.stderr
    assert len(replies) == 1
    assert replies[0]["id"] == "empty-1"
    assert replies[0]["ok"] is True
    assert replies[0]["answers"] == {}


def test_stdout_carries_only_protocol_lines(run):
    proc, replies = run([
        json.dumps({"id": "r1", "state": "s", "questions": {}})
    ])
    assert proc.returncode == 0, proc.stderr
    assert len(replies) == 1
    assert all(json.loads(line) == reply
               for line, reply in zip(proc.stdout.splitlines(), replies))
    assert "chatter" in proc.stderr
    assert "chatter" not in proc.stdout
    assert replies[0]["ok"] is True


def test_autocast_off_replaces_torch_autocast(run):
    request = json.dumps({"id": "ac", "state": "s", "questions": {}})
    off_proc, off_replies = run([request], "--autocast", "off")
    auto_proc, auto_replies = run([request])
    assert off_proc.returncode == 0, off_proc.stderr
    assert auto_proc.returncode == 0, auto_proc.stderr
    assert len(off_replies) == len(auto_replies) == 1
    assert off_replies[0]["ok"] is True
    assert auto_replies[0]["ok"] is True
    assert off_replies[0]["answers"]["ac"] == "_NoAutocast"
    assert auto_replies[0]["answers"]["ac"] == "autocast"


def test_warm_on_start_failure_is_recorded(run):
    proc, replies = run([
        json.dumps({"op": "status"}),
        json.dumps({"id": "bad", "state": "s", "questions": {}}),
    ], "--warm-on-start", fail=True)
    assert proc.returncode == 0, proc.stderr
    assert len(replies) == 2
    status, prediction = replies
    assert status["op"] == "status"
    assert status["ok"] is True
    assert status["loaded"] is False
    assert status["load_ms"] is None
    assert status["load_error"] == "RuntimeError: no weights"
    assert prediction["id"] == "bad"
    assert prediction["ok"] is False
    assert prediction["error"] == "RuntimeError: no weights"
    assert "traceback" in prediction
    assert len(prediction["traceback"]) <= 800


def test_missing_model_dir_exits_2(run):
    missing_proc, missing_replies = run([], model_dir=False)
    bogus_proc, bogus_replies = run([], "--autocast", "bogus")
    assert missing_proc.returncode == 2
    assert missing_replies == []
    assert "usage:" in missing_proc.stderr.lower()
    assert "--model-dir" in missing_proc.stderr
    assert bogus_proc.returncode == 2
    assert bogus_replies == []
    assert "usage:" in bogus_proc.stderr.lower()
    assert "invalid choice" in bogus_proc.stderr
    assert "--autocast" in bogus_proc.stderr
