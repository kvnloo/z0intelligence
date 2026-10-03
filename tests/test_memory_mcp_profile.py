"""intelligence_mcp profile "memory": memory tools only, stdio and in-process, no service and no network."""
from __future__ import annotations

import json
import os
import subprocess
import sys

import pytest

from memory_fixture import build_av_db
from z0int import intelligence_mcp as mcp

MEMORY_TOOLS = {'memory_search', 'orient', 'inspect', 'history', 'unknowns', 'verify'}


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv('Z0INT_HOME', str(tmp_path / 'z0'))
    monkeypatch.setenv('AGENTSVIEW_DATA_DIR', str(tmp_path / 'av'))
    build_av_db(tmp_path / 'av' / 'sessions.db')
    return tmp_path


def call(name, args, profile='memory'):
    resp = mcp.handle({'jsonrpc': '2.0', 'id': 7, 'method': 'tools/call', 'params': {'name': name, 'arguments': args}},
                      profile=profile)
    body = resp['result']
    return body, json.loads(body['content'][0]['text']) if not body['isError'] else body['content'][0]['text']


def test_tools_list_is_exactly_the_memory_tools(env):
    listed = mcp.handle({'jsonrpc': '2.0', 'id': 1, 'method': 'tools/list'}, profile='memory')
    assert {t['name'] for t in listed['result']['tools']} == MEMORY_TOOLS
    init = mcp.handle({'jsonrpc': '2.0', 'id': 0, 'method': 'initialize'}, profile='memory')
    assert init['result']['serverInfo']['name'] == 'z0-memory'
    body, _ = call('route_worker', {'task': 'x'})
    assert body['isError'] is True


def test_default_profile_is_unchanged(env):
    listed = mcp.handle({'jsonrpc': '2.0', 'id': 1, 'method': 'tools/list'})
    assert {'route_worker', 'list_models', 'delegate_worker'} <= {t['name'] for t in listed['result']['tools']}


def test_tools_answer_from_the_fixture(env):
    body, out = call('memory_search', {'query': 'quokka gateway', 'project': 'z0'})
    assert body['isError'] is False and out['evidence']
    body, out = call('orient', {'query': 'quokka gateway', 'project': 'z0'})
    assert body['isError'] is False and out['text'] and out['memory_snapshot_id'].startswith('mem_')
    body, out = call('verify', {'query': 'quokka gateway', 'requires': ['systemd']})
    assert out['verdict'] == 'USE'
    body, out = call('verify', {'query': 'quokka gateway', 'requires': ['kubernetes']})
    assert out['verdict'] == 'FALLBACK' and out['missing'] == ['kubernetes']
    body, out = call('history', {'subject': 'nothing recorded'})
    assert body['isError'] is False and out['history'] == []


def test_tool_errors_are_is_error_never_empty_results(env):
    (env / 'av' / 'sessions.db').unlink()
    body, text = call('memory_search', {'query': 'quokka'})
    assert body['isError'] is True and 'unavailable' in text and 'missing' in text
    body, text = call('inspect', {'locator': 'not-a-locator'})
    assert body['isError'] is True
    body, text = call('memory_search', {})
    assert body['isError'] is True
    body, text = call('unknowns', {'query': 'quokka'})
    assert body['isError'] is False and 'lexical' in json.dumps(text)


def test_works_with_the_network_disabled(env, monkeypatch):
    def no_network(*a, **k):
        raise OSError('network disabled')

    monkeypatch.setattr('socket.socket.connect', no_network)
    body, out = call('memory_search', {'query': 'quokka routing'})
    assert body['isError'] is False and out['evidence']
    assert out['layers']['semantic']['reason'] == 'not_configured'


def test_stdio_server_speaks_the_memory_profile(env):
    lines = [{'jsonrpc': '2.0', 'id': 1, 'method': 'initialize', 'params': {}},
             {'jsonrpc': '2.0', 'method': 'notifications/initialized'},
             {'jsonrpc': '2.0', 'id': 2, 'method': 'tools/list'},
             {'jsonrpc': '2.0', 'id': 3, 'method': 'tools/call',
              'params': {'name': 'memory_search', 'arguments': {'query': 'quokka bench'}}}]
    proc = subprocess.run([sys.executable, '-m', 'z0int.intelligence_mcp', '--profile', 'memory'],
                          input=''.join(json.dumps(x) + '\n' for x in lines), capture_output=True, text=True,
                          timeout=60, env={**os.environ, 'PYTHONPATH': os.pathsep.join(p for p in sys.path if p)})
    assert proc.returncode == 0, proc.stderr
    out = [json.loads(line) for line in proc.stdout.splitlines()]
    assert [o['id'] for o in out] == [1, 2, 3]
    assert {t['name'] for t in out[1]['result']['tools']} == MEMORY_TOOLS
    assert out[2]['result']['isError'] is False


def test_an_unscoped_call_spans_every_project_and_says_so(env):
    """Pinned: without "project" a memory tool is unscoped (all projects); the tool schema says so, so the
    caller (the per-harness shim) knows to pass its project."""
    listed = mcp.handle({'jsonrpc': '2.0', 'id': 1, 'method': 'tools/list'}, profile='memory')
    for tool in listed['result']['tools']:
        project = tool['inputSchema']['properties'].get('project')
        if project is not None:
            assert 'all projects' in project.get('description', ''), tool['name']
    _, out = call('memory_search', {'query': 'quokka sibling', 'limit': 20})
    assert 'sib' in {e['session_id'] for e in out['evidence']}
    _, out = call('memory_search', {'query': 'quokka sibling', 'limit': 20, 'project': 'z0'})
    assert 'sib' not in {e['session_id'] for e in out['evidence']}
