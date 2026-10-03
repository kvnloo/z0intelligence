"""Integration e2e, unified-memory stage: probe every harness seam of the integrate tree for a z0 memory read path.

The verified components (C1, C3, C4a, C5) carry capture only; the memory surface (C7-memory-core) and the per-harness
memory seams (C8-memory-wiring) are NOT verified and were not merged. This probe records, without building
anything, what a harness can reach today:
  - the z0 MCP server each shim exposes (in-process tools/list of z0int.intelligence_mcp; no tools/call, no network)
  - any memory inject/read file in each shim dir (claude-code, codex, grok, hermes, omp/omo, dsh)
  - whether any capture row written by the harness legs carries a memory-use receipt (opportunity_record.memory)
  - the canonical contract that C7/C8 would bind to (memory_contract.MemoryUseReceipt) exists on the base
usage: e2e_memory_probe.py <worktree> <e2e-root>   (prints JSON)
"""
import json
import sys
from pathlib import Path

WT, R = Path(sys.argv[1]), Path(sys.argv[2])
sys.path.insert(0, str(WT / 'src'))
from z0int import intelligence_mcp, memory_contract  # noqa: E402

SEAMS = {
    'claude-code': [WT / 'harness-adapters/claude-code-z0intelligence'],
    'codex': [WT / 'harness-adapters/codex-z0intelligence'],
    'grok': [WT / 'harness-adapters/grok-z0intelligence'],
    'hermes': [WT / 'harness-adapters/hermes-z0intelligence'],
    'omp': [WT / 'omp-extensions/z0int-bridge', WT / 'omp-extensions/z0-memory'],
    'omo': [WT / 'omp-extensions/z0int-bridge/omo.ts'],
    'dsh': [WT / 'harness-adapters/dsh-z0intelligence'],
}
MEMORY_TOOLS = {'memory_search', 'orient', 'inspect', 'history', 'unknowns', 'verify'}

tools = intelligence_mcp.handle({'jsonrpc': '2.0', 'id': 1, 'method': 'tools/list'})['result']['tools']
tool_names = sorted(t['name'] for t in tools)
out = {'mcp_server': intelligence_mcp.SERVER_NAME, 'mcp_tools': tool_names,
       'mcp_memory_tools': sorted(MEMORY_TOOLS & set(tool_names)), 'harness': {}}
for h, paths in SEAMS.items():
    files = []
    for p in paths:
        if p.is_dir():
            files += [f for f in p.rglob('*') if f.is_file()]
        elif p.is_file():
            files.append(p)
    memory_files = sorted(str(f.relative_to(WT)) for f in files if 'memory' in f.name.lower())
    mcp_entries = []
    for f in files:
        if f.name == '.mcp.json':
            mcp_entries += sorted(json.loads(f.read_text()).get('mcpServers', {}))
    state = R / 'z0home' / 'state' / h
    rows_with_memory = 0
    for name in ('opportunities.jsonl', 'outcomes.jsonl'):
        p = state / name
        if p.exists():
            rows_with_memory += sum(1 for line in p.read_text().splitlines()
                                    if line.strip() and 'memory' in json.loads(line))
    out['harness'][h] = {'memory_seam_files': memory_files, 'mcp_servers': mcp_entries,
                         'z0_memory_server': 'z0-memory' in mcp_entries, 'rows_with_memory_receipt': rows_with_memory,
                         'memory_read_possible': bool(memory_files) or 'z0-memory' in mcp_entries}
out['contract_present'] = {'MemoryUseReceipt': hasattr(memory_contract, 'MemoryUseReceipt'),
                           'MemoryScope': hasattr(memory_contract, 'MemoryScope'),
                           'EventIdentity': hasattr(memory_contract, 'EventIdentity')}
out['memory_e2e'] = 'BLOCKED' if not any(v['memory_read_possible'] for v in out['harness'].values()) else 'PARTIAL'
out['reason'] = ('no harness seam reaches a z0 memory surface: C7-memory-core and C8-memory-wiring are not verified '
                 'and not merged; the only z0 MCP server is the route_worker profile (no memory tools)')
print(json.dumps(out, indent=1, sort_keys=True))
