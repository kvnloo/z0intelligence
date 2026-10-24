"""C8: every harness gets one memory-only MCP server named ``z0-memory`` (profile memory; never route_worker),
and the push seams are separate hook entries from capture (a capture failure cannot cancel memory)."""
from __future__ import annotations

import json
import os
import shlex
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
HA = ROOT / 'harness-adapters'
MEMORY_TOOLS = {'memory_search', 'orient', 'inspect', 'history', 'unknowns', 'verify'}
EXISTING = {'z0intelligence', 'intelligence-z0intelligence', 'agentsview', 'memory-agentsview', 'z0int-intelligence'}


def cc_entries():
    servers = json.loads((HA / 'claude-code-z0intelligence' / '.mcp.json').read_text())['mcpServers']
    return servers


def entries():
    """(harness, server name, command, args) for every shipped z0-memory entry."""
    out = []
    cc = cc_entries()
    out += [('claude-code', name, s['command'], s.get('args', [])) for name, s in cc.items() if name != 'z0intelligence']
    codex = json.loads((HA / 'codex-z0intelligence' / '.mcp.json').read_text())['mcpServers']
    out += [('codex', name, s['command'], s.get('args', [])) for name, s in codex.items()]
    grok = tomllib.loads((HA / 'grok-z0intelligence' / 'mcp' / 'z0-memory.toml').read_text())['mcp_servers']
    out += [('grok', name, s['command'], s.get('args', [])) for name, s in grok.items()]
    for harness in ('omp', 'omo'):
        cfg = json.loads((ROOT / 'omp-extensions' / 'z0-memory' / 'mcp.json').read_text())['mcpServers']
        out += [(harness, name, s['command'], s.get('args', [])) for name, s in cfg.items()]
    yaml = pytest.importorskip('yaml')
    patch = yaml.safe_load((HA / 'dsh-z0intelligence' / 'z0-memory.cordis.yml').read_text())
    rows = [row for op in patch for row in op.get('insert', [])]
    out += [('dsh', r['config']['serverName'], r['config']['command'], r['config'].get('args', [])) for r in rows]
    assert {h for h, *_ in out} == {'claude-code', 'codex', 'grok', 'omp', 'omo', 'dsh'}
    return out


def test_every_harness_has_exactly_one_z0_memory_server_on_the_memory_profile():
    found = entries()
    by_harness = {}
    for harness, name, command, args in found:
        by_harness.setdefault(harness, []).append(name)
        line = ' '.join([command, *args])
        assert '--profile memory' in line and ('z0int.intelligence_mcp' in line or command.endswith('/bin/z0int-mcp')), line
    assert all(names == ['z0-memory'] for names in by_harness.values()), by_harness
    assert not EXISTING & {name for _, name, *_ in found}


def test_claude_code_keeps_its_route_worker_server_unchanged():
    cc = cc_entries()
    assert cc['z0intelligence']['env'] == {'Z0INT_HARNESS': 'claude-code', 'Z0INT_SHARED_ONLY': '1'}
    assert cc['z0-memory']['args'] == ['--profile', 'memory']


def test_codex_plugin_manifest_points_at_its_mcp_file():
    manifest = json.loads((HA / 'codex-z0intelligence' / '.codex-plugin' / 'plugin.json').read_text())
    assert manifest['mcpServers'] == './.mcp.json'


def tools_list(command, args, env):
    msgs = [{'jsonrpc': '2.0', 'id': 1, 'method': 'initialize', 'params': {}},
            {'jsonrpc': '2.0', 'id': 2, 'method': 'tools/list', 'params': {}}]
    proc = subprocess.run([command, *args], input=''.join(json.dumps(m) + '\n' for m in msgs), capture_output=True,
                          text=True, timeout=60, env=env)
    replies = [json.loads(line) for line in proc.stdout.splitlines() if line.strip()]
    init, listed = replies[0], replies[1]
    return init['result']['serverInfo']['name'], {t['name'] for t in listed['result']['tools']}


def test_each_entry_serves_the_memory_tools_only_over_stdio(tmp_path):
    env = dict(os.environ, Z0INT_PYTHON=sys.executable, Z0INT_HOME=str(tmp_path / 'z0'), HOME=str(tmp_path),
               CLAUDE_PLUGIN_ROOT=str(HA / 'claude-code-z0intelligence'),
               PYTHONPATH=os.pathsep.join(p for p in (str(ROOT / 'src'), os.environ.get('PYTHONPATH', '')) if p))
    for harness, name, command, args in entries():
        command = command.replace('${CLAUDE_PLUGIN_ROOT}', env['CLAUDE_PLUGIN_ROOT'])
        server, tools = tools_list(command, args, env)
        assert server == 'z0-memory' and tools == MEMORY_TOOLS, (harness, tools)
        assert 'route_worker' not in tools


def hook_commands(path):
    hooks = json.loads(path.read_text())['hooks']
    return {event: [h['command'] for group in groups for h in group['hooks']] for event, groups in hooks.items()}


@pytest.mark.parametrize('harness', ['claude-code', 'codex'])
def test_memory_push_seam_is_its_own_user_prompt_submit_entry(harness):
    cmds = hook_commands(HA / f'{harness}-z0intelligence' / 'hooks' / 'hooks.json')['UserPromptSubmit']
    memory = [c for c in cmds if 'z0int.memory.hook' in c]
    capture = [c for c in cmds if 'z0int.hook_adapter' in c]
    assert len(memory) == 1 and len(capture) == 1
    argv = shlex.split(memory[0].split('||')[0])
    assert argv[-3:] == ['--harness', harness, 'prompt']
    assert memory[0].rstrip().endswith('|| true')


def test_grok_registers_no_memory_push_hook():
    text = (HA / 'grok-z0intelligence' / 'hooks' / 'z0-capture.json').read_text()
    assert 'z0int.memory' not in text
