"""The Claude Code harness is wired end to end, from what Claude Code actually runs.

Roots are the three entrypoints an installed plugin exposes: the plugin hook commands
(both plugins' ``hooks/hooks.json``), the plugin MCP server (``.mcp.json`` ->
``bin/z0int-mcp``) and the ``z0int claude-code`` CLI. Hooks and the MCP server are run
exactly as Claude Code would run them (the command strings, through ``sh``), against a
throwaway git repo and ``Z0INT_HOME``. All inputs are synthetic.
"""
import ast
import json
import os
from pathlib import Path
import subprocess
import sys
import time

import pytest

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / 'src'
PLUGINS = ROOT / 'harness-adapters'
Z0 = PLUGINS / 'claude-code-z0intelligence'
OBS = PLUGINS / 'claude-code-z0-obspack'

pytestmark = pytest.mark.skipif(sys.platform == 'win32', reason='plugin commands are POSIX sh')


@pytest.fixture
def host(tmp_path):
    repo = tmp_path / 'repo'
    repo.mkdir()
    subprocess.run(['git', 'init', '-q', '-b', 'main', str(repo)], check=True)
    (repo / 'README.md').write_text('synthetic\n')
    subprocess.run(['git', '-C', str(repo), 'add', '-A'], check=True)
    subprocess.run(['git', '-C', str(repo), '-c', 'user.name=t', '-c', 'user.email=t@t', 'commit', '-qm', 'init'], check=True)
    home = tmp_path / 'home'
    home.mkdir()
    env = {k: v for k, v in os.environ.items() if not k.startswith(('Z0INT_', 'CLAUDE'))}
    env.update(HOME=str(home), Z0INT_HOME=str(home / '.z0int'), Z0INT_PYTHON=sys.executable,
               PYTHONPATH=os.pathsep.join(filter(None, [str(SRC), os.environ.get('PYTHONPATH')])))
    return repo, home, env


def hook_commands(plugin):
    spec = json.loads((plugin / 'hooks' / 'hooks.json').read_text())['hooks']
    return {event: [h['command'] for group in groups for h in group['hooks']] for event, groups in spec.items()}


def run_hook(command, payload, env, cwd):
    return subprocess.run(['sh', '-c', command], input=json.dumps(payload), capture_output=True,
                          text=True, env=env, cwd=cwd, timeout=60)


def test_marketplace_lists_both_plugins():
    market = json.loads((ROOT / '.claude-plugin' / 'marketplace.json').read_text())
    sources = {p['name']: (ROOT / p['source']).resolve() for p in market['plugins']}
    assert sources == {'z0intelligence': Z0.resolve(), 'z0-obspack': OBS.resolve()}
    for name, path in sources.items():
        assert json.loads((path / '.claude-plugin' / 'plugin.json').read_text())['name'] == name
        assert (path / 'hooks' / 'hooks.json').is_file()


def test_plugin_hooks_run_and_fail_open(host):
    repo, home, env = host
    hooks = hook_commands(Z0)
    assert set(hooks) >= {'UserPromptSubmit', 'Stop', 'SessionEnd', 'SessionStart'}
    base = {'session_id': 'synthetic-session', 'cwd': str(repo), 'transcript_path': str(repo / 'none.jsonl')}
    for event, commands in hooks.items():
        for command in commands:
            for payload in (base, 'not json'):
                out = subprocess.run(['sh', '-c', command], input=payload if isinstance(payload, str) else json.dumps(payload),
                                     capture_output=True, text=True, env=env, cwd=repo, timeout=60)
                assert out.returncode == 0, (event, out.stderr)
                assert 'Traceback' not in out.stderr, (event, out.stderr)


def test_session_start_hook_injects_state_packet_with_posture(host):
    repo, home, env = host
    [command] = hook_commands(Z0)['SessionStart']
    out = run_hook(command, {'session_id': 'synthetic-session', 'cwd': str(repo)}, {**env, 'Z0INT_CLAUDE_CODE_PACKET': '1'}, repo)
    assert out.returncode == 0, out.stderr
    ctx = json.loads(out.stdout)['hookSpecificOutput']
    assert ctx['hookEventName'] == 'SessionStart'
    assert ctx['additionalContext'].startswith('<z0-state-packet repo=repo ')
    # resource posture is a packet fact source (shadow, fail-open)
    assert 'resource' in ctx['additionalContext'].splitlines()[1]
    # off by default: no injection without opt-in
    assert run_hook(command, {'session_id': 'synthetic-session', 'cwd': str(repo)}, env, repo).stdout == ''


def test_prompt_hook_reaches_decision_opportunity(host):
    repo, home, env = host
    [command] = hook_commands(Z0)['UserPromptSubmit']
    payload = {'session_id': 'synthetic-session', 'prompt_id': 'p-1', 'cwd': str(repo),
               'prompt': 'synthetic wiring check: what branch is checked out?'}
    out = run_hook(command, payload, env, repo)
    assert out.returncode == 0 and out.stdout == '', out.stderr  # shadow: never injected
    record = home / '.z0int' / 'state' / 'claude-code' / 'opportunities.jsonl'
    deadline = time.monotonic() + 45  # emitted by a detached child, off the hot path
    while not record.is_file() and time.monotonic() < deadline:
        time.sleep(0.2)
    assert record.is_file()


def test_obspack_hook_packs_long_output(host):
    repo, home, env = host
    [command] = hook_commands(OBS)['PostToolUse']
    long = '\n'.join(f'synthetic line {i}' for i in range(5000))
    payload = {'session_id': 'synthetic-session', 'cwd': str(repo), 'tool_name': 'Bash',
               'tool_input': {'command': 'seq 5000'}, 'tool_response': {'stdout': long, 'stderr': ''}}
    out = run_hook(command, payload, env, repo)
    assert out.returncode == 0, out.stderr
    assert 'ObservationPack' in out.stdout and len(out.stdout) < len(long)


def test_plugin_mcp_server_lists_route_worker(host):
    repo, home, env = host
    [(name, server)] = json.loads((Z0 / '.mcp.json').read_text())['mcpServers'].items()
    assert name == 'z0intelligence'
    command = server['command'].replace('${CLAUDE_PLUGIN_ROOT}', str(Z0))
    requests = [{'jsonrpc': '2.0', 'id': 1, 'method': 'initialize', 'params': {}},
                {'jsonrpc': '2.0', 'id': 2, 'method': 'tools/list', 'params': {}}]
    out = subprocess.run([command, *server['args']], input=''.join(json.dumps(r) + '\n' for r in requests),
                         capture_output=True, text=True, env={**env, **server['env']}, cwd=repo, timeout=60)
    assert out.returncode == 0, out.stderr
    replies = {r['id']: r for r in map(json.loads, out.stdout.splitlines())}
    assert replies[1]['result']['serverInfo']['name'] == 'z0intelligence'
    assert [t['name'] for t in replies[2]['result']['tools']] == ['route_worker']


def test_cli_dispatches_claude_code(capsys):
    from z0int.cli import main
    assert main(['claude-code', 'profile', 'lean']) == 0
    assert '--strict-mcp-config' in capsys.readouterr().out
    assert main(['claude-code', 'launch', '--profile', 'lean', '--dry-run', '--', '-p', 'x']) == 0
    argv = json.loads(capsys.readouterr().out)
    assert '--plugin-dir' in argv and '--mcp-config' in argv


def _imports(module):
    """z0int modules a module imports anywhere (lazy imports included), resolved to files."""
    path = SRC / (module.replace('.', '/') + '.py')
    if not path.is_file():
        path = SRC / module.replace('.', '/') / '__init__.py'
    package = module if path.name == '__init__.py' else module.rpartition('.')[0]
    found = set()
    for node in ast.walk(ast.parse(path.read_text(encoding='utf-8'))):
        names = []
        if isinstance(node, ast.Import):
            names = [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom):
            base = node.module or ''
            if node.level:
                parts = package.split('.')
                parts = parts[:len(parts) - node.level + 1]
                base = '.'.join(parts + ([base] if base else []))
            names = [base] + [f'{base}.{a.name}' for a in node.names]
        for name in names:
            while name.startswith('z0int'):
                if (SRC / (name.replace('.', '/') + '.py')).is_file() or (SRC / name.replace('.', '/') / '__init__.py').is_file():
                    found.add(name)
                    break
                name = name.rpartition('.')[0]
    return found


def _closure(roots):
    seen, todo = set(), list(roots)
    while todo:
        module = todo.pop()
        if module not in seen:
            seen.add(module)
            todo.extend(_imports(module) - seen)
    return seen


def test_entrypoints_reach_the_harness_modules():
    hooks = {c.split(' -m ', 1)[1].split()[0] for p in (Z0, OBS) for cs in hook_commands(p).values() for c in cs}
    assert hooks == {'z0int.claude_code', 'z0int.claude_code_obs'}
    mcp = (Z0 / 'bin' / 'z0int-mcp').read_text().split(' -m ', 1)[1].split()[0]
    assert mcp == 'z0int.intelligence_mcp'
    reach = _closure(hooks | {mcp, 'z0int.cli'})
    for module in ('z0int.claude_code_launch', 'z0int.state_packet', 'z0int.decision_opportunity',
                   'z0int.posture', 'z0int.automatic', 'z0int.worker_routing'):
        assert module in reach, module
    assert 'z0int.worker_routing' in _closure({mcp})
