"""C8 e2e, pull seams: every shipped z0-memory MCP entry (CC, Codex, Grok, OMP, OMO, DSH) started exactly as its
config says, over stdio: initialize, tools/list, then orient on a cohort question and on the secret probe.
usage: e2e_mcp.py <worktree> <out.json>   (Z0INT_PYTHON, Z0INT_HOME, AGENTSVIEW_DATA_DIR from the environment)"""
import json
import os
import subprocess
import sys
import tomllib
from pathlib import Path

import yaml

wt, out = Path(sys.argv[1]), Path(sys.argv[2])
HA = wt / 'harness-adapters'
entries = []
for name, s in json.loads((HA / 'claude-code-z0intelligence/.mcp.json').read_text())['mcpServers'].items():
    entries.append(('claude-code', name, s['command'].replace('${CLAUDE_PLUGIN_ROOT}', str(HA / 'claude-code-z0intelligence')), s.get('args', [])))
for name, s in json.loads((HA / 'codex-z0intelligence/.mcp.json').read_text())['mcpServers'].items():
    entries.append(('codex', name, s['command'], s.get('args', [])))
for name, s in tomllib.loads((HA / 'grok-z0intelligence/mcp/z0-memory.toml').read_text())['mcp_servers'].items():
    entries.append(('grok', name, s['command'], s.get('args', [])))
for h in ('omp', 'omo'):
    for name, s in json.loads((wt / 'omp-extensions/z0-memory/mcp.json').read_text())['mcpServers'].items():
        entries.append((h, name, s['command'], s.get('args', [])))
for op in yaml.safe_load((HA / 'dsh-z0intelligence/z0-memory.cordis.yml').read_text()):
    for row in op.get('insert', []):
        entries.append(('dsh', row['config']['serverName'], row['config']['command'], row['config'].get('args', [])))

QUESTION = 'What schema id does z0int context resolve publish?'
PROBE = 'quokka deploy notes tool output'
results = []
for harness, name, command, args in entries:
    msgs = [{'jsonrpc': '2.0', 'id': 1, 'method': 'initialize', 'params': {}},
            {'jsonrpc': '2.0', 'id': 2, 'method': 'tools/list', 'params': {}},
            {'jsonrpc': '2.0', 'id': 3, 'method': 'tools/call', 'params': {'name': 'orient', 'arguments': {'query': QUESTION}}},
            {'jsonrpc': '2.0', 'id': 4, 'method': 'tools/call', 'params': {'name': 'memory_search', 'arguments': {'query': PROBE}}},
            {'jsonrpc': '2.0', 'id': 5, 'method': 'tools/call', 'params': {'name': 'route_worker', 'arguments': {}}}]
    proc = subprocess.run([command, *args], input=''.join(json.dumps(m) + '\n' for m in msgs), capture_output=True,
                          text=True, timeout=120, env=os.environ.copy())
    replies = {r.get('id'): r for r in (json.loads(line) for line in proc.stdout.splitlines() if line.strip())}
    if harness == 'claude-code' and name != 'z0-memory':
        continue
    orient = json.loads(replies[3]['result']['content'][0]['text'])
    results.append({'harness': harness, 'server': replies[1]['result']['serverInfo']['name'],
                    'tools': sorted(t['name'] for t in replies[2]['result']['tools']),
                    'orient_evidence': orient.get('evidence'), 'orient_text': orient.get('text'),
                    'probe_text': replies[4]['result']['content'][0]['text'],
                    'route_worker_is_error': bool(replies[5].get('result', {}).get('isError') or replies[5].get('error'))})
out.write_text(json.dumps(results, indent=1))
print(json.dumps([(r['harness'], r['server'], len(r['tools']), len(r['orient_evidence'] or [])) for r in results]))
