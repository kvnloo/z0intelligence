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
