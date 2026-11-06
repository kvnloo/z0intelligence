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


def load_plugin_module():
    """Import the adapter file directly: stdlib only, no Hermes SDK or service."""
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "hermes_file_search_under_test", PLUGIN / "__init__.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_guidance_names_the_admitted_root_contract():
    """The 007 harm: agents passed roots the service always rejects.

    The guidance must state the admission contract itself, so a call is either
    admitted or never made, instead of burning a turn on a rejected_root.
    """
    module = load_plugin_module()
    section = module.PRIORITY
    for token in ("read_file", "z0_file_search", "repository_root", "Git toplevel",
                  "Git worktree", "du", "fall back", "search_files"):
        assert token in section, token
    for rejected in ("/mnt/zer0models", "/mnt/zer0models/workspace/zer0", "/workspace",
                     "/home/kvn", "filesystem root"):
        assert rejected in section, rejected
    assert "200,000 files" in section
    # The registered prompt section is capped; the guidance must fit inside it.
    assert len(section) <= module.SECTION_MAX_CHARS
    assert module.SCHEMA["description"].endswith(section)


def test_failure_instruction_carries_the_same_contract():
    """A rejected root must tell the caller what to retry with, and the fallback."""
    module = load_plugin_module()
    instruction = module.FAILURE_INSTRUCTION
    assert "fallback" in instruction.lower()
    assert "search_files" in instruction
    assert "/mnt/zer0models/workspace/zer0" in instruction
    assert "Git toplevel" in instruction
    assert "no evidence was returned" in instruction


def _handler(module, service_url):
    """Register against a stub context pointed at service_url; return the handler."""
    class StubCtx:
        def __init__(self):
            self.config = {"service_url": service_url}
            self.tools = {}
            self.sections = {}

        def get_config(self, key, default=None):
            return os.environ.get("Z0INT_SERVICE_URL", self.config.get(key, default))

        def register_tool(self, name=None, toolset=None, schema=None, handler=None, **_):
            self.tools[name] = handler

        def register_system_prompt_section(self, name, content, max_chars=None, **_):
            self.sections[name] = (content, max_chars)

    ctx = StubCtx()
    module.register(ctx)
    return ctx.tools["z0_file_search"]


def test_nonexistent_root_fails_locally_without_a_request():
    """007: 7 calls/wk spent a round trip on a root that does not exist."""
    module = load_plugin_module()
    with socket.socket() as dead:
        dead.bind(("127.0.0.1", 0))  # any request here would raise, not answer
        handler = _handler(module, f"http://127.0.0.1:{dead.getsockname()[1]}")
        body = json.loads(handler({"repository_root": "/mnt/zer0models/github/oss-factory",
                                   "query": "downstream experiments registry", "limit": 5}))
    assert body["ok"] is False
    assert body["error"] == "repository_root_not_found_on_service_host"
    assert body["repository_root"] == "/mnt/zer0models/github/oss-factory"
    assert "http_status" not in body  # no round trip was made
    assert "ls" in body["instruction"]


def test_remote_service_origin_skips_the_local_existence_probe():
    """A stat is only valid when the service shares this filesystem."""
    module = load_plugin_module()
    assert module._same_host("http://127.0.0.1:11501") is True
    assert module._same_host("http://localhost:11501") is True
    assert module._same_host("http://groot:11501") is False
    assert module._same_host("http://172.19.0.1:11501") is False


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
assert "read_file" in section and "z0_file_search" in section
assert "repository_root" in section and "Git toplevel" in section
assert "fall back" in section and "Git worktree" in section and "du" in section
assert "/mnt/zer0models/workspace/zer0" in section and "200,000 files" in section
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
