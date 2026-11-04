"""O19 spend gate: classifier, decision table, hard stop and the Hermes plugin."""
from __future__ import annotations

import importlib.util
import json
import os
import stat
import sys
from pathlib import Path

import pytest

from z0int.fsm import ops as P
from z0int.fsm import spend_gate as S
from z0int.fsm.ops_replay import replay

ROOT = Path(__file__).resolve().parents[1]
F = P.Fund


@pytest.fixture(autouse=True)
def iso(tmp_path, monkeypatch):
    monkeypatch.setattr(S, "CONFIG", tmp_path / "cfg")
    monkeypatch.setattr(S, "FUNDING_FILE", tmp_path / "cfg/spend-funding.json")
    monkeypatch.setattr(S, "APPROVALS_FILE", tmp_path / "cfg/spend-approvals.json")
    monkeypatch.setattr(S, "STATE", tmp_path / "state")
    monkeypatch.setattr(S, "RECEIPTS", tmp_path / "state/receipts.jsonl")
    monkeypatch.delenv("Z0_GUARD_O19", raising=False)
    yield


# ------------------------------------------------------------------ decision table (twin of ops.bend O19)

def test_table():
    B = (False, True)
    for a in B:
        for f in B:
            for r in B:
                assert P.spend_allows(F.FREE, a, f, r) and P.spend_allows(F.SUB, a, f, r)
                assert P.spend_allows(F.METERED, a, f, r) is a
                assert P.spend_allows(F.UNKNOWN, a, f, r) is a
                assert P.spend_allows(F.CREDIT, a, f, r) is (a or (f and r))


def test_decision_names_the_law():
    assert S.decision("m", F.CREDIT, False, True, False)["law"] == "o19_credit_unguarded_refused"
    assert S.decision("m", F.CREDIT, False, False, True)["law"] == "o19_credit_over_cap_refused"
    assert S.decision("m", F.CREDIT, False, True, True)["law"] == "o19_credit_guarded_allowed"
    assert S.decision("m", F.METERED, False, True, True)["law"] == "o19_metered_needs_approval"
    assert S.decision("m", F.METERED, True, False, False)["law"] == "o19_approval_allows"


# ------------------------------------------------------------------ classification

@pytest.mark.parametrize("cmd,want", [
    ("modal run --detach x.py::main --step evaluate", {"modal"}),
    ("cd d && modal deploy app.py", {"modal"}),
    ("modal app list && modal billing report --json", set()),
    ("modal app stop -y ap-1", set()),
    ("hf jobs run --flavor a100-large img cmd", {"hf-compute"}),
    ("curl -s https://api.openai.com/v1/responses -d @r.json", {"openai-api"}),
    ("grep -r api.openai.com src/", set()),
    ("sky launch -c c task.yaml", {"skypilot"}),
    ("aws ec2 run-instances --instance-type p5.48xlarge", {"aws"}),
    ("git push && gh pr create", set()),
])
def test_command_surfaces(cmd, want):
    assert {s for s, _, _ in S.command_surfaces(cmd)} == want


def test_python_program_launching_modal(tmp_path):
    prog = tmp_path / "launch.py"
    prog.write_text("import modal\nfn = modal.Function.from_name('a', 'b')\nprint(fn.remote(1))\n")
    assert [s for s, _, _ in S.command_surfaces(f"python3 {prog}")] == ["modal"]
    prog.write_text("import modal\nprint(modal.__version__)\n")
    assert S.command_surfaces(f"python3 {prog}") == []


def test_openrouter_free_only_for_free_models():
    free = 'curl https://openrouter.ai/api/v1/chat/completions -d \'{"model": "x/y:free"}\''
    paid = 'curl https://openrouter.ai/api/v1/chat/completions -d \'{"model": "x/y"}\''
    assert S.command_surfaces(free) == []
    assert [f for _, f, _ in S.command_surfaces(paid)] == [F.METERED]


@pytest.mark.parametrize("prov,url,model,fund", [
    ("anthropic", "", "claude-opus-5-5", F.SUB),
    ("openai-codex", "", "gpt-6.1-sol", F.SUB),
    ("nous", "", "openai/gpt-6.1-sol", F.SUB),
    ("openrouter", "", "openrouter/free", F.FREE),
    ("openrouter", "", "nvidia/nemotron:free", F.FREE),
    ("openrouter", "", "anthropic/claude-opus-5-5", F.METERED),
    ("custom:vllm-sr", "http://10.43.216.55:8899/v1", "vllm-sr/paid", F.SUB),
    ("custom:vllm-sr", "http://10.43.216.55:8899/v1", "vllm-sr/turbo", F.FREE),
    ("groq", "https://api.groq.com/openai/v1", "openai/gpt-oss-120b", F.FREE),
    ("custom", "https://api.groq.com/openai/v1", "openai/gpt-oss-120b", F.FREE),
    ("custom:kilo-free", "https://api.kilo.ai/api/gateway", "nvidia/nemotron:free", F.FREE),
    ("deepseek", "", "deepseek-chat", F.METERED),
    ("brand-new", "https://api.brand.ai/v1", "m", F.UNKNOWN),
    ("", "https://api.openai.com/v1", "gpt-6", F.METERED),
])
def test_llm_surface(prov, url, model, fund):
    assert S.llm_surface(prov, url, model)[1] is fund


def test_owner_override_reclassifies(tmp_path):
    S.CONFIG.mkdir(parents=True)
    S.FUNDING_FILE.write_text(json.dumps({"llm": {"groq": "metered"}}))
    assert S.llm_surface("groq", "", "x")[1] is F.METERED


# ------------------------------------------------------------------ host facts

def test_credit_needs_cap_and_armed_stop(monkeypatch):
    monkeypatch.setattr(S, "month_spend", lambda s, max_age=60.0: 12.0)
    monkeypatch.setattr(S, "armed", lambda s: False)
    res = S.evaluate(S.command_surfaces("modal run x.py"))
    assert not res["allowed"] and "hard stop is not armed" in S.refusal_text(res)
    monkeypatch.setattr(S, "armed", lambda s: True)
    assert S.evaluate(S.command_surfaces("modal run x.py"))["allowed"]
    assert not S.evaluate(S.command_surfaces("modal run x.py"), estimate=14.5)["allowed"]
    monkeypatch.setattr(S, "month_spend", lambda s, max_age=60.0: None)  # billing unreadable
    assert not S.evaluate(S.command_surfaces("modal run x.py"))["allowed"]


def test_cap_never_exceeds_credit():
    S.CONFIG.mkdir(parents=True)
    S.FUNDING_FILE.write_text(json.dumps({"credit": {"modal": {"cap_usd": 500}}}))
    assert S.cap("modal") == 30.0


def test_approval_expires():
    S.CONFIG.mkdir(parents=True)
    S.APPROVALS_FILE.write_text(json.dumps([
        {"surface": "openai-api", "cap_usd": 5, "expires": "2099-01-01T00:00:00+00:00"},
        {"surface": "hf-compute", "cap_usd": 5, "expires": "2001-01-01T00:00:00+00:00"}]))
    assert S.evaluate(S.command_surfaces("curl https://api.openai.com/v1/x"))["allowed"]
    assert not S.evaluate(S.command_surfaces("hf jobs run img cmd"))["allowed"]


def test_approve_needs_owner_terminal(capsys):
    assert S.main(["approve", "--surface", "openai-api", "--cap-usd", "5"]) == S.EXIT_REFUSED
    assert not S.APPROVALS_FILE.exists()


def test_report_mode_allows_but_logs(monkeypatch):
    monkeypatch.setenv("Z0_GUARD_O19", "report")
    assert S.main(["check-cmd", "--cmd", "hf jobs run img cmd"]) == 0
    rows = [json.loads(x) for x in S.RECEIPTS.read_text().splitlines()]
    assert [(r["event"], r["allowed"], r["mode"]) for r in rows] == [("check", False, "report")]


def test_cli_exit_codes():
    assert S.main(["check-cmd", "--cmd", "hf jobs run img cmd"]) == S.EXIT_REFUSED
    assert S.main(["check-cmd", "--cmd", "ls -la"]) == 0
    assert S.main(["check-llm", "--provider", "anthropic", "--model", "claude-opus-5-5"]) == 0
    assert S.main(["check-llm", "--provider", "deepseek", "--model", "deepseek-chat"]) == S.EXIT_REFUSED


def test_protected_paths():
    assert S.touches_protected("echo '[]' > ~/.config/z0-guard/spend-approvals.json")
    assert S.touches_protected("export Z0_GUARD_O19=off")
    assert S.touches_protected('python3 -c "json.dump({\\"O19\\": \\"off\\"}, open(\'modes.json\', \'w\'))"')
    assert not S.touches_protected("cat README.md")


# ------------------------------------------------------------------ the hard stop

def _fake_modal(tmp_path, spend: float) -> Path:
    log = tmp_path / "modal.log"
    exe = tmp_path / "modal"
    exe.write_text(f"""#!/usr/bin/env python3
import json, sys
open({str(log)!r}, "a").write(" ".join(sys.argv[1:]) + "\\n")
if sys.argv[1:3] == ["billing", "report"]:
    print(json.dumps([{{"cost": "{spend}"}}]))
elif sys.argv[1:3] == ["app", "list"]:
    print(json.dumps([{{"app_id": "ap-run", "state": "ephemeral (detached)"}},
                      {{"app_id": "ap-dep", "state": "deployed"}},
                      {{"app_id": "ap-old", "state": "stopped"}}]))
""")
    exe.chmod(exe.stat().st_mode | stat.S_IEXEC)
    return exe


def test_stop_tick_stops_everything_at_cap(tmp_path, monkeypatch):
    exe = _fake_modal(tmp_path, 26.5)
    monkeypatch.setenv("Z0_SPEND_MODAL_CLI", str(exe))
    row = S.stop_tick("modal")
    assert row["stopped"] == ["ap-run", "ap-dep"]


def test_stop_tick_below_cap_stops_nothing(tmp_path, monkeypatch):
    exe = _fake_modal(tmp_path, 12.0)
    monkeypatch.setenv("Z0_SPEND_MODAL_CLI", str(exe))
    assert S.stop_tick("modal")["stopped"] == []
    assert "app stop" not in (tmp_path / "modal.log").read_text()


# ------------------------------------------------------------------ incident tape

def test_tape():
    rep = replay(str(ROOT / "tests/fixtures/ops_tapes/o19-spend.json"))
    assert rep["summary"]["ok"], rep


# ------------------------------------------------------------------ Hermes plugin

def _plugin(monkeypatch, tmp_path):
    home = tmp_path / "hh"
    home.mkdir()
    (home / "spend-gate.json").write_text(json.dumps({"kernel": str(ROOT / "src")}))
    monkeypatch.setenv("HERMES_HOME", str(home))
    spec = importlib.util.spec_from_file_location("spend_gate_plugin", ROOT / "adapters/hermes-spend-gate/__init__.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    mod.S = S  # the isolated module under test (fixture paths)
    return mod


def test_plugin_blocks_unguarded_modal_and_forged_approval(monkeypatch, tmp_path):
    mod = _plugin(monkeypatch, tmp_path)
    monkeypatch.setattr(S, "month_spend", lambda s, max_age=60.0: 12.0)
    monkeypatch.setattr(S, "armed", lambda s: False)
    r = mod.on_pre_tool_call("terminal", {"command": "modal run --detach x.py"})
    assert r and r["action"] == "block" and "O19" in r["message"]
    assert mod.on_pre_tool_call("terminal", {"command": "ls"}) is None
    r = mod.on_pre_tool_call("write_file", {"path": "~/.config/z0-guard/spend-approvals.json", "content": "[]"})
    assert r and r["action"] == "block"
    r = mod.on_pre_tool_call("execute_code", {"code": "import modal\nmodal.Function.from_name('a','b').remote()"})
    assert r and r["action"] == "block"


def test_plugin_rewrites_metered_llm_requests(monkeypatch, tmp_path):
    mod = _plugin(monkeypatch, tmp_path)
    req = {"model": "deepseek-chat", "messages": [{"role": "user", "content": "secret plan"}], "max_tokens": 4000,
           "tools": [{"x": 1}]}
    out = mod.on_llm_request(request=req, provider="deepseek", base_url="https://api.deepseek.com/v1")
    assert out["request"]["model"] == mod._REFUSED_MODEL
    assert out["request"]["max_tokens"] == 1 and "tools" not in out["request"]
    assert out["request"]["messages"] == [{"role": "user", "content": "."}]
    assert mod.on_llm_request(request={"model": "claude-opus-5-5"}, provider="anthropic") is None
    assert mod.on_llm_request(request={"model": "openrouter/free"}, provider="openrouter") is None
