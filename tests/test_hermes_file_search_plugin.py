"""Native Hermes contract probe; opt in to the installed host SDK, never its profile."""
import json
import os
import socket
from pathlib import Path
import subprocess

import pytest

from test_shared_file_search import (  # Shared owned HTTP fixture, native FFF backend.
    assert_content_packet, mcp_exchange, repository, request_for, service,
)
from z0int.memory.event_log import EventLog

PLUGIN = Path(__file__).resolve().parents[1] / "harness-adapters" / "hermes-file-search"

# Executed in a separate Hermes interpreter: no z0int or adapters on PYTHONPATH.
NATIVE_PROBE = r'''
import importlib.util
import json
from pathlib import Path
import sys
from hermes_cli.plugins import PluginContext, PluginManager
from hermes_cli.plugins_manifest import parse_manifest_file
from tools.registry import registry

plugin = Path(sys.argv[1])
assert (plugin / "plugin.yaml").is_file(), "native Hermes file-search plugin is missing"
manifest = parse_manifest_file(plugin / "plugin.yaml", plugin, "user", "")
assert manifest is not None
assert manifest.name == "hermes-file-search"
assert manifest.provides_tools == ["z0_file_search"]
assert manifest.provides_hooks == []
manager = PluginManager()
ctx = PluginContext(manifest, manager)
spec = importlib.util.spec_from_file_location("file_search_plugin", plugin / "__init__.py")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
module.register(ctx)
assert manager._hooks == {}
assert manager._middleware == {}
assert manager._plugin_tool_names == {"z0_file_search"}
entry = registry.get_entry("z0_file_search", scope=manager.scope_key)
assert entry is not None
assert entry.schema["name"] == "z0_file_search"
assert entry.schema["parameters"]["required"] == ["repository_root", "query"]
assert "harness" not in entry.schema["parameters"]["properties"]
section = manager._system_prompt_sections["hermes-file-search"].content
assert "read_file" in section and "prefer z0_file_search" in section
assert "fallback" in section and "Git worktree" in section and "du" in section
results = []
for arguments in json.loads(sys.argv[2]):
    raw = registry.dispatch("z0_file_search", arguments, scope=manager.scope_key,
                            task_id="native-trace", session_id="native-session")
    assert isinstance(raw, str)
    results.append(json.loads(raw))
assert not any(name == "z0int" or name.startswith("z0int.") or name == "fff" or
               name == "adapters" or name.startswith("adapters.") for name in sys.modules)
print(json.dumps({"responses": results, "tool": entry.schema, "priority": section}))
manager.unload()
'''


def native_call(url, arguments, tmp_path):
    python = os.environ.get("HERMES_PLUGIN_TEST_PYTHON")
    source = os.environ.get("HERMES_PLUGIN_TEST_ROOT")
    if not python or not source:
        pytest.skip("Set HERMES_PLUGIN_TEST_PYTHON and HERMES_PLUGIN_TEST_ROOT for the real Hermes SDK probe")
    home = tmp_path / "native-home"
    home.mkdir(exist_ok=True)
    run = subprocess.run(
        [python, "-B", "-c", NATIVE_PROBE, str(PLUGIN), json.dumps(arguments)],
        cwd=tmp_path, text=True, capture_output=True, timeout=30,
        env={"PATH": "/usr/bin:/bin", "HOME": str(home), "HERMES_HOME": str(home / "hermes"),
             "TMPDIR": os.environ["TMPDIR"], "PYTHONPATH": source,
             "PYTHONDONTWRITEBYTECODE": "1", "Z0INT_SERVICE_URL": url,
             "Z0INT_HARNESS": "spoofed-env"},
    )
    assert run.returncode == 0, run.stdout + run.stderr
    return json.loads(run.stdout)


def test_native_hermes_register_tool_handler_reaches_shared_fff(service, repository, tmp_path):
    _, first = mcp_exchange(service, "codex", True, request_for(repository))
    assert first["isError"] is False
    proof = native_call(service, [request_for(repository, harness="spoofed-model")], tmp_path)
    body = proof["responses"][0]
    packet = assert_content_packet(body, repository)
    assert body["harness_id"] == "hermes"
    assert packet["task_id"] == "native-trace"
    assert next(op for op in packet["recipe"]["operations"] if op["op"] == "fff_symbol")["reused"] is True
    event = EventLog().get(body["event_id"])
    assert event.source == "harness:hermes"
    assert event.session_id == "native-session"
    assert isinstance(event.payload, dict)
    assert event.payload["task_id"] == "native-trace"
    assert event.payload["execution_completed"] is False
    assert event.payload["verified_success"] is None
    print("HERMES_NATIVE_PROOF", json.dumps(proof))


def test_native_hermes_errors_are_json_with_explicit_fallback(service, repository, tmp_path):
    proof = native_call(service, [request_for(repository, limit=99), []], tmp_path)
    for body in proof["responses"]:
        assert body["ok"] is False
        assert body["error"] == "invalid_file_search_request"
        assert "packet" not in body
    assert proof["responses"][0]["http_status"] == 400
    assert not EventLog().events_path.exists()
    with socket.socket() as reserved:
        reserved.bind(("127.0.0.1", 0))
        url = f"http://127.0.0.1:{reserved.getsockname()[1]}"
        body = native_call(url, [request_for(repository)], tmp_path)["responses"][0]
    assert body["ok"] is False
    assert body["error"] == "file_search_unavailable"
    assert "fallback" in body["instruction"].lower()
    assert "packet" not in body
