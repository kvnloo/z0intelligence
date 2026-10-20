"""Gateway ownership tests: no live provider, transport, credentials or fallback."""

import asyncio
from contextlib import asynccontextmanager
import json
import sys
import time
from types import ModuleType, SimpleNamespace
from unittest.mock import patch

import pytest

from z0int.turbo import adapters, hermes_gateway, runtime


def test_native_command_uses_installation_bound_runtime(tmp_path):
    script = tmp_path / "helper.py"
    script.write_text("import json,sys;print(json.dumps(sys.argv[1:]))")
    bootstrap = (
        "import sys,runpy;runpy.run_module('runpy',run_name='__main__',alter_sys=True)"
    )
    emitted = [sys.executable, "-I", "-c", bootstrap, str(script), "literal;argument"]
    real_run = adapters.subprocess.run
    with patch.object(
        adapters.subprocess,
        "run",
        return_value=SimpleNamespace(stdout=json.dumps(emitted)),
    ) as run:
        command = adapters.native_command(
            "anthropic", "locked", tmp_path, tmp_path / "q", tmp_path / "s", 10
        )
    argv = run.call_args.args[0]
    assert "--print-runtime-command" in argv and "--run-budget" not in argv
    assert argv[argv.index("--model") + 1] == "locked"
    assert run.call_args.kwargs["timeout"] == 15
    # Execute the transformed bootstrap; source-string assertions would miss the
    # real runpy CLI's module-vs-file failure.
    observed = real_run(command, capture_output=True, text=True, timeout=5, check=True)
    assert json.loads(observed.stdout) == ["literal;argument"]


@pytest.mark.parametrize(
    "command", [["python"], ["python", "-c", "print('not a bootstrap')"]]
)
def test_unknown_native_bootstrap_fails_closed(command):
    with pytest.raises(ValueError):
        adapters.script_command(command)


@pytest.mark.parametrize("stop_reason", [None, "cancelled", "deadline"])
def test_gateway_owns_stop_and_reads_terminal_receipt(
    tmp_path, monkeypatch, capsys, stop_reason
):
    calls = []
    completed = asyncio.Event()

    class Client:
        async def rpc(self, method, **kw):
            calls.append((method, kw))
            if method == "runtime.describe":
                return {
                    "session_create": {
                        "parameters": [
                            "provider",
                            "model",
                            "cwd",
                            "safe_mode",
                            "max_turns",
                            "run_budget",
                        ]
                    }
                }
            if method == "session.create":
                return {
                    "stored_session_id": "owned-session",
                    "info": {"model": "locked"},
                }
            if method == "prompt.receipt":
                return {"result": {"output_tokens": 7}}

    @asynccontextmanager
    async def connect():
        yield Client()

    class View:
        def __init__(self, client, snapshot, **kw):
            self.session_id = snapshot["stored_session_id"]
            self.finite_admission = None
            self.completions = {}

        async def run(self, query, oneshot):
            await asyncio.sleep(0.01)
            self.finite_admission = "owned-admission"
            if stop_reason == "cancelled":
                (tmp_path / "stop").touch()
            if stop_reason:
                await completed.wait()
            self.completions["owned-admission"] = {
                "outcome": "cancelled" if stop_reason else "completed"
            }
            return 130 if stop_reason else 0

        async def command(self, command):
            calls.append((command, self.session_id))
            completed.set()

    class Emitter:
        def __init__(self, **kw):
            pass

    for name, attrs in {
        "hermes_cli.gateway_client": {"connect_gateway": connect},
        "hermes_cli.gateway_chat_view": {"GatewayChatView": View},
        "hermes_cli.stream_json": {"StreamJsonEmitter": Emitter},
    }.items():
        module = ModuleType(name)
        module.__dict__.update(attrs)
        monkeypatch.setitem(sys.modules, name, module)
    monkeypatch.setitem(sys.modules, "hermes_cli", ModuleType("hermes_cli"))
    query = tmp_path / "query"
    query.write_text("public test")
    args = SimpleNamespace(
        stop=str(tmp_path / "stop"),
        seconds=0.15 if stop_reason == "deadline" else 3,
        provider="test",
        model="locked",
        cwd=str(tmp_path),
        query_file=str(query),
    )
    code = asyncio.run(hermes_gateway.execute(args))
    rows = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    receipt = next(row for row in rows if row["type"] == "native_receipt")
    assert receipt["input_tokens"] is None and receipt["output_tokens"] == 7
    assert receipt["session_id"] == "owned-session"
    if stop_reason:
        assert code == 130 and ("/stop", "owned-session") in calls
        assert rows[-1]["terminal_confirmed"] is True
        assert rows[-1]["reason"] == stop_reason
    else:
        assert code == 0 and not any(c[0] == "/stop" for c in calls)
    create = next(c[1] for c in calls if c[0] == "session.create")
    assert create["safe_mode"] is True and create["model"] == "locked"
    assert 0 < create["run_budget"] <= args.seconds


def test_native_missing_accounting_is_not_zero(tmp_path):
    rows = [
        {"type": "system", "subtype": "init", "model": "claude-opus-5-5"},
        {"type": "result", "text": "answer", "tokens": {"input": 0, "output": 0}},
        {"type": "native_receipt", "input_tokens": None, "output_tokens": None},
    ]
    with (
        patch.object(adapters, "native_command", return_value=["gateway"]),
        patch.object(
            adapters, "process", return_value="\n".join(map(json.dumps, rows))
        ),
    ):
        got = adapters.native(
            "public",
            "planner",
            tmp_path,
            tmp_path,
            time.monotonic() + 2,
            tmp_path / "stop",
        )
    assert got["input_tokens"] is None and got["output_tokens"] is None


def test_terminal_stream_delivers_answer_and_fits_voice_line(tmp_path, monkeypatch):
    monkeypatch.setenv("Z0INT_HOME", str(tmp_path))
    events = []
    answer = "Verified screenshot evidence. " + "🌿" * 20000
    response = {
        "provider": "openai-codex",
        "model": "test",
        "text": json.dumps({"verified": True, "reason": "test only", "answer": answer}),
        "input_tokens": None,
        "output_tokens": None,
    }
    runner = runtime.Runner("fixture", normal=True, emit=events.append)
    with (
        patch.object(adapters, "native", return_value=response),
        patch.object(adapters, "load_keys"),
    ):
        receipt = runner.execute()
    final = events[-1]
    assert final["event"] == "completed" and final["quality"]["verified"] is True
    assert final["quality"]["answer"].startswith("Verified screenshot evidence.")
    assert final["summary"].startswith(
        "Verified result ready: Verified screenshot evidence."
    )
    assert len(json.dumps(final)) < 65536
    assert (
        receipt["quality"]["answer"] == answer
    )  # Full answer remains in private status receipt.


def test_private_author_is_declared_before_independent_verifier():
    from z0int.turbo.graph import compile_graph

    plan = {
        "jobs": [
            {
                "id": "design",
                "lane": "fast",
                "prompt": "public design",
                "depends_on": [],
            }
        ],
        "acceptance": ["real screenshot"],
    }
    public = compile_graph(plan, "turbo-1234567890abcdef", 1000, 30)
    private = compile_graph(plan, "turbo-1234567890abcdef", 1000, 30, native_code=True)
    assert "native-implementer" in {n["id"] for n in private["intentGraph"]["nodes"]}
    assert {(e["from"], e["to"]) for e in private["intentGraph"]["edges"]} == {
        ("intent", "work-design"),
        ("work-design", "native-implementer"),
        ("native-implementer", "verify"),
    }
    assert public["provenance"]["sourceHash"] != private["provenance"]["sourceHash"]


def test_private_repo_guard_rejects_code_before_dispatch(tmp_path, monkeypatch):
    monkeypatch.setenv("Z0INT_HOME", str(tmp_path))
    plan = {
        "jobs": [
            {
                "id": "private",
                "lane": "code",
                "prompt": "must not run",
                "depends_on": [],
            }
        ],
        "acceptance": ["test"],
    }
    result = {
        "provider": "anthropic",
        "model": "test",
        "text": json.dumps(plan),
        "input_tokens": None,
        "output_tokens": None,
    }
    runner = runtime.Runner("public design only", no_code_workers=True)
    with (
        patch("kerdoios.turbo.discover_turbo_offers", return_value=[]),
        patch.object(adapters, "load_keys"),
        patch.object(adapters, "native", return_value=result),
        patch.object(adapters, "opencode") as code,
    ):
        receipt = runner.execute()
    code.assert_not_called()
    assert receipt["state"] == "failed"
    assert receipt["quality"]["reason"] == "code_workers_disabled_for_private_source"
