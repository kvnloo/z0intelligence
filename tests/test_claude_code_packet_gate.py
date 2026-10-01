"""Prompt-gated State Packet (`packet: "gated"`) and the lean profile's explicit z0 MCP server."""
import json
import subprocess

import pytest

from z0int import claude_code
from z0int import claude_code_engagement as eng
from z0int import claude_code_launch as L
from z0int import state_packet as sp


@pytest.fixture
def env(monkeypatch, tmp_path):
    monkeypatch.setenv('Z0INT_HOME', str(tmp_path / 'home'))  # never the host's claude-code.json
    for k in ('Z0INT_CLAUDE_CODE_PACKET', 'Z0INT_CLAUDE_CODE_WORKSPACE_PACKET', 'Z0INT_CLAUDE_CODE_ROUTE_TOOL'):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv('Z0INT_CLAUDE_CODE_OFFLOAD_HINT', '0')
    monkeypatch.setenv('Z0INT_CLAUDE_CODE_OPPORTUNITIES', '0')
    (tmp_path / 'projects').mkdir()
    monkeypatch.setenv('Z0INT_CLAUDE_PROJECTS', str(tmp_path / 'projects'))
    return tmp_path


def make_repo(tmp_path, name='r'):
    repo = tmp_path / name
    repo.mkdir()
    subprocess.run(['git', 'init', '-q', '-b', 'main', str(repo)], check=True)
    (repo / 'f').write_text('x')
    subprocess.run(['git', '-C', str(repo), 'add', '-A'], check=True)
    subprocess.run(['git', '-C', str(repo), '-c', 'user.name=t', '-c', 'user.email=t@t', 'commit', '-qm', 'init'],
                   check=True)
    return repo


def test_packet_mode_parsing(env, monkeypatch):
    assert eng.packet_mode({}) == 'off'
    assert eng.packet_mode({'packet': True}) == 'session'
    assert eng.packet_mode({'packet': 'gated'}) == 'gated'
    assert eng.packet_mode({'packet': False}) == 'off'
    monkeypatch.setenv('Z0INT_CLAUDE_CODE_PACKET', 'gated')
    assert eng.packet_mode({'packet': True}) == 'gated'  # env overrides config
    monkeypatch.setenv('Z0INT_CLAUDE_CODE_PACKET', '1')
    assert eng.packet_mode({}) == 'session'


def test_gated_mode_keeps_session_start_silent(env, monkeypatch):
    repo = make_repo(env)
    monkeypatch.setenv('Z0INT_CLAUDE_CODE_PACKET', 'gated')
    assert claude_code.on_session_start(json.dumps({'cwd': str(repo)})) is None
    monkeypatch.setenv('Z0INT_CLAUDE_CODE_PACKET', '1')  # v1 behaviour unchanged
    out = claude_code.on_session_start(json.dumps({'cwd': str(repo)}))
    assert '<z0-state-packet' in out['hookSpecificOutput']['additionalContext']


def test_gate_injects_only_required_families_once_per_session(env):
    repo = make_repo(env)
    hook = {'session_id': 's1', 'cwd': str(repo), 'prompt': 'which branch am I on?'}
    text = eng.gated_packet(hook)
    assert text.startswith('<z0-state-packet scope="git.branch"')
    assert '- git.branch: main' in text and 'git.head' not in text and 'resource.posture' not in text
    assert eng.gated_packet(hook) is None  # same family-set, same session: nothing new
    both = eng.gated_packet({**hook, 'prompt': 'what branch, and is the working tree dirty?'})
    assert 'scope="git.dirty"' in both and '- git.branch' not in both  # only the not-yet-delivered family
    assert eng.gated_packet({**hook, 'session_id': 's2'}).startswith('<z0-state-packet scope="git.branch"')
    rows = [json.loads(l) for l in (env / 'home' / 'state' / 'claude-code' / 'packet-gate.jsonl').read_text().splitlines()]
    assert [r['injected'] for r in rows] == [['git.branch'], [], ['git.dirty'], ['git.branch']]
    assert all('prompt' not in r for r in rows)  # count-only ledger


def test_gate_is_silent_for_prompts_without_fact_families(env):
    repo = make_repo(env)
    for prompt in ('Fix the failing test in parser.py', 'Add a --weeks option to the range command', ''):
        assert eng.gated_packet({'session_id': 's', 'cwd': str(repo), 'prompt': prompt}) is None


def test_gate_outside_a_repo_injects_workspace_packet_once(env, monkeypatch):
    ws = env / 'ws'
    ws.mkdir()
    make_repo(ws, 'a')
    hook = {'session_id': 's', 'cwd': str(ws), 'prompt': 'which repos have uncommitted work?'}
    assert '<z0-workspace-packet' in eng.gated_packet(hook)
    assert eng.gated_packet(hook) is None
    monkeypatch.setenv('Z0INT_CLAUDE_CODE_WORKSPACE_PACKET', '0')
    assert eng.gated_packet({**hook, 'session_id': 'other'}) is None


def test_prompt_hook_delivers_scoped_packet_in_shadow(env, monkeypatch):
    repo = make_repo(env)
    from z0int import automatic
    monkeypatch.setattr(automatic, 'handle_event', lambda event: {'action': 'native'})
    monkeypatch.setenv('Z0INT_CLAUDE_CODE_PACKET', 'gated')
    out = claude_code.on_prompt({'session_id': 's', 'cwd': str(repo), 'prompt': 'what is the last commit?'})
    ctx = out['hookSpecificOutput']['additionalContext']
    assert out['hookSpecificOutput']['hookEventName'] == 'UserPromptSubmit' and '- git.head:' in ctx
    assert claude_code.on_prompt({'session_id': 's', 'cwd': str(repo), 'prompt': 'refactor foo()'}) is None
    harness_msg = '<task-notification>which branch</task-notification>'
    assert claude_code.on_prompt({'session_id': 's', 'cwd': str(repo), 'prompt': harness_msg}) is None
    monkeypatch.setenv('Z0INT_CLAUDE_CODE_PACKET', '1')  # session mode: prompt hook stays inert
    assert claude_code.on_prompt({'session_id': 's9', 'cwd': str(repo), 'prompt': 'which branch?'}) is None


def test_scoped_render_matches_family_prefixes(env):
    repo = make_repo(env)
    pkt = sp.build_state_packet(repo)
    text = sp.render_scoped_context(pkt, ['git.recent_commits'], families=['git.history'])
    assert 'git.recent_commits' in text and 'git.branch:' not in text
    assert 'no current claim for' not in text
    missing = sp.render_scoped_context(pkt, ['gh.'], families=['gh'])
    assert 'no current claim for: gh' in missing


def test_lean_launch_keeps_only_the_z0_mcp_server(monkeypatch):
    argv = L.build_argv('lean', ['-p', 'hi'])
    assert '--strict-mcp-config' in argv
    cfg = json.loads(argv[argv.index('--mcp-config') + 1])
    assert list(cfg['mcpServers']) == ['z0intelligence']
    assert cfg['mcpServers']['z0intelligence']['command'].endswith('claude-code-z0intelligence/bin/z0int-mcp')
    assert '--mcp-config' not in L.build_argv('stock', [])  # stock loads the plugin's own server
    assert '--mcp-config' not in L.build_argv('lean', [], plugin=False)
    env = L.launch_env('lean', base={})
    assert env['Z0INT_CLAUDE_CODE_ROUTE_TOOL'] == 'mcp__z0intelligence__route_worker'
    assert 'Z0INT_CLAUDE_CODE_ROUTE_TOOL' not in L.launch_env('stock', base={})
    monkeypatch.setenv('Z0INT_CLAUDE_CODE_ROUTE_TOOL', eng.LEAN_ROUTE_TOOL)
    assert eng.LEAN_ROUTE_TOOL in eng.offload_hint({'posture': 'OFFLOAD'})
