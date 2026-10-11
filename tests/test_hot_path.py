import json
import time

import pytest

from z0int.bridge import runtime as bridge_runtime
from z0int.quota_budget import project
from pathlib import Path

from z0int import worker_routing as routing


def test_missing_kerdoios_interpreter_does_not_spawn(monkeypatch):
    calls = []
    monkeypatch.setenv("KERDOIOS_PYTHON", "/workspace/evolution-lab/.venv/bin/python")
    monkeypatch.setattr(bridge_runtime.subprocess, "run", lambda *args, **kwargs: calls.append(args))
    result = bridge_runtime.kerdoios_plan("coding.generic", {"mode": "balanced", "coding": 1})
    assert result["spawned"] is False
    assert result["error"].startswith("interpreter_missing:")
    assert calls == []
    bridge_runtime.kerdoios_record(
        provider="groq",
        model="openai/gpt-oss-20b",
        capability_id="coding.generic",
        completed=False,
        input_tokens=None,
        output_tokens=None,
    )
    assert calls == []


def test_missing_interpreter_returns_in_milliseconds(monkeypatch):
    monkeypatch.setenv("KERDOIOS_PYTHON", "/workspace/evolution-lab/.venv/bin/python")
    started = time.perf_counter()
    result = bridge_runtime.kerdoios_plan("coding.generic", {"mode": "cheap"})
    elapsed_ms = (time.perf_counter() - started) * 1000
    assert result["spawned"] is False
    assert elapsed_ms < 50


_DEFAULT_KERDOIOS_PYTHON = "/workspace/evolution-lab/.venv/bin/python"
_RECORD_KWARGS = dict(
    provider="groq",
    model="openai/gpt-oss-20b",
    capability_id="coding.generic",
    completed=True,
    input_tokens=7,
    output_tokens=3,
    measurement_state="complete",
    state_reason="synthetic",
)
_RECORD_TAIL = [
    "-m", "kerdoios", "record",
    "--provider", "groq",
    "--model", "openai/gpt-oss-20b",
    "--task-type", "coding",
    "--capability-id", "coding.generic",
    "--cost", "0",
    "--completed",
    "--input-tokens", "7",
    "--output-tokens", "3",
    "--measurement-state", "complete",
    "--state-reason", "synthetic",
]


def _fake_kerdoios_root(tmp_path, monkeypatch, *, runnable=True):
    """Synthetic Kerdoios checkout whose `record` writes its argv next to itself."""
    root = tmp_path / "kerdoios-root"
    root.mkdir()
    if runnable:
        pkg = root / "kerdoios"
        pkg.mkdir()
        (pkg / "__init__.py").write_text("")
        (pkg / "__main__.py").write_text(
            "import json, sys\n"
            "open('recorded.json', 'w').write(json.dumps(sys.argv[1:]))\n"
            "print(json.dumps({'ok': True}))\n"
        )
    monkeypatch.setenv("KERDOIOS_ROOT", str(root))
    monkeypatch.delenv("KERDOIOS_PYTHON", raising=False)
    monkeypatch.delenv("EVOLUTION_LAB_PYTHON", raising=False)
    # The hard-coded default venv is absent on CI and on the measured host; pin that.
    real_ready = bridge_runtime._interpreter_ready
    monkeypatch.setattr(
        bridge_runtime,
        "_interpreter_ready",
        lambda path: path != _DEFAULT_KERDOIOS_PYTHON and real_ready(path),
    )
    return root


def _capture_spawns(monkeypatch):
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append((list(cmd), kwargs.get("cwd")))
        return type("Proc", (), {"stdout": '{"ok": true}', "stderr": "", "returncode": 0})()

    monkeypatch.setattr(bridge_runtime.subprocess, "run", fake_run)
    return calls


def test_record_falls_back_to_running_interpreter_and_records(tmp_path, monkeypatch):
    # Real spawn, no subprocess mock: the record must actually land.
    import sys

    root = _fake_kerdoios_root(tmp_path, monkeypatch)
    bridge_runtime.kerdoios_record(**_RECORD_KWARGS)
    recorded = root / "recorded.json"
    assert recorded.is_file(), "kerdoios_record was a silent no-op"
    assert json.loads(recorded.read_text()) == _RECORD_TAIL[2:]

    calls = _capture_spawns(monkeypatch)
    bridge_runtime.kerdoios_record(**_RECORD_KWARGS)
    assert calls == [([sys.executable, *_RECORD_TAIL], str(root))]


@pytest.mark.parametrize("var", ["KERDOIOS_PYTHON", "EVOLUTION_LAB_PYTHON"])
def test_record_explicit_interpreter_env_always_wins(tmp_path, monkeypatch, var):
    _fake_kerdoios_root(tmp_path, monkeypatch)
    calls = _capture_spawns(monkeypatch)
    # Explicit but missing: never second-guessed, so still no spawn.
    monkeypatch.setenv(var, str(tmp_path / "no-such-python"))
    bridge_runtime.kerdoios_record(**_RECORD_KWARGS)
    assert calls == []
    # Explicit and present: that interpreter is used, not sys.executable.
    chosen = tmp_path / "chosen-python"
    chosen.write_text("")
    monkeypatch.setenv(var, str(chosen))
    bridge_runtime.kerdoios_record(**_RECORD_KWARGS)
    assert [cmd[0] for cmd, _ in calls] == [str(chosen)]


def test_record_does_not_spawn_when_kerdoios_is_not_runnable(tmp_path, monkeypatch):
    _fake_kerdoios_root(tmp_path, monkeypatch, runnable=False)
    calls = _capture_spawns(monkeypatch)
    assert bridge_runtime.kerdoios_record(**_RECORD_KWARGS) is None
    assert calls == []


def test_plan_still_fails_fast_without_interpreter(tmp_path, monkeypatch):
    _fake_kerdoios_root(tmp_path, monkeypatch)
    calls = _capture_spawns(monkeypatch)
    result = bridge_runtime.kerdoios_plan("coding.generic", {"mode": "balanced", "coding": 1})
    assert result == {
        "ok": False,
        "error": f"interpreter_missing:{_DEFAULT_KERDOIOS_PYTHON}",
        "spawned": False,
    }
    assert calls == []


def test_groq_quota_projects_without_kerdoios_package(monkeypatch):
    import builtins

    real_import = builtins.__import__

    def blocked(name, *args, **kwargs):
        if name.startswith("kerdoios.quota"):
            raise ImportError("no kerdoios.quota")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", blocked)
    policy = {
        "quota_budgets": {
            "groq": {
                "model": "openai/gpt-oss-20b",
                "limits": {"rpm": 30, "rpd": 1000, "tpm": 8000, "tpd": 200000},
            }
        }
    }
    result = project("groq", "openai/gpt-oss-20b", policy, 32, events=[], now=1_000_000.0)
    assert result["allowed"] is True
    assert result["quota_source"] == "local_receipts"
    assert result["quota"]["provider"] == "groq"
    assert result["quota"]["dimensions"]["tpm"]["source"] == "locally_reconstructed"
    assert result["reason"] != "quota_backend_unavailable"


def test_omp_hot_path_does_not_spawn():
    import subprocess

    module_url = (Path(__file__).resolve().parents[1] / "harness-adapters" / "automatic-client.mjs").as_uri()
    script = """
import { ompHotPath, scheduleOmpAutomatic } from __Z0_CLIENT_URL__;
const decision = ompHotPath();
if (decision.spawn !== false || decision.action !== 'native') process.exit(2);
const started = Date.now();
const scheduled = scheduleOmpAutomatic({sessionId:'s', turnId:'t', text:'ping'});
if (scheduled.spawn !== false || scheduled.blocked_ms > 50) process.exit(3);
if (Date.now() - started > 50) process.exit(4);
console.log(JSON.stringify({blocked_ms: scheduled.blocked_ms, spawn: scheduled.spawn}));
""".replace("__Z0_CLIENT_URL__", json.dumps(module_url))
    proc = subprocess.run(
        ["node", "--input-type=module", "-e", script],
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert proc.returncode == 0, proc.stderr
    assert '"spawn": false' in proc.stdout or '"spawn":false' in proc.stdout


def test_nous_oauth_does_not_import_hermes_cli_auth(monkeypatch):
    calls = []

    class Proc:
        returncode = 0
        stdout = '{"ok":true,"api_key":"test-key","base_url":"https://inference-api.nousresearch.com/v1"}\n'
        stderr = ""

    monkeypatch.setattr(routing.subprocess, "run", lambda *args, **kwargs: calls.append(args) or Proc())
    monkeypatch.setattr(routing, "hermes_python", lambda: Path("/usr/bin/python3"))
    monkeypatch.setattr(routing, "hermes_root", lambda: Path("/home/kvn/.hermes/hermes-agent"))
    routing._OAUTH_CACHE.clear()
    key, base = routing.credentials("nous", {"auth": "hermes-oauth"})
    assert key == "test-key"
    assert base == "https://inference-api.nousresearch.com"
    script = calls[0][0][2]
    assert "from hermes_cli import auth" not in script
    assert "resolve_nous_runtime_credentials" in script
