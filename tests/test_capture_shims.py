"""Thin per-harness capture shims in harness-adapters/: every command goes through the one hook adapter."""
import json
import re
import shlex
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ADAPTERS = ROOT / 'harness-adapters'
CODEX = ADAPTERS / 'codex-z0intelligence'
GROK_HOOKS = ADAPTERS / 'grok-z0intelligence' / 'hooks' / 'z0-capture.json'
CC_HOOKS = ADAPTERS / 'claude-code-z0intelligence' / 'hooks' / 'hooks.json'
FORBIDDEN = ('route_worker', 'intelligence_mcp', 'z0int-mcp', 'HERMES_HOME', 'hermes-home', '.work/',
             '/home/kvn/plugins', 'dsh-env-exec', 'refs:', 'plugins/cache/personal', 'API_KEY', 'TOKEN')


def commands(hooks_doc):
    """{event: [adapter argv tail]} for every command hook in a Claude-compatible hooks document."""
    out = {}
    for event, groups in hooks_doc['hooks'].items():
        for group in groups:
            for h in group['hooks']:
                assert h['type'] == 'command'
                m = re.search(r'-m z0int\.hook_adapter (.*?) \|\| true$', h['command'])
                assert m, h['command']
                out.setdefault(event, []).append(shlex.split(m.group(1)))
    return out


def test_claude_code_plugin_routes_every_hook_through_the_adapter_and_adds_subagents():
    got = commands(json.loads(CC_HOOKS.read_text()))
    assert got == {
        'UserPromptSubmit': [['--harness', 'claude-code', 'prompt']],
        'Stop': [['--harness', 'claude-code', 'stop']],
        'SessionEnd': [['--harness', 'claude-code', 'stop']],
        'SessionStart': [['--harness', 'claude-code', 'session-start']],
        'SubagentStart': [['--harness', 'claude-code', 'subagent-start']],
        'SubagentStop': [['--harness', 'claude-code', 'subagent-stop']],
    }


def test_codex_shim_is_hooks_only():
    manifest = json.loads((CODEX / '.codex-plugin' / 'plugin.json').read_text())
    assert manifest['name'] == 'codex-z0intelligence'  # never shadows or replaces z0intelligence@personal
    assert not {'mcpServers', 'skills', 'apps'} & set(manifest)
    assert not (CODEX / '.mcp.json').exists() and not (CODEX / 'skills').exists()
    got = commands(json.loads((CODEX / 'hooks' / 'hooks.json').read_text()))
    assert got == {
        'UserPromptSubmit': [['--harness', 'codex', 'prompt']],
        'Stop': [['--harness', 'codex', 'stop']],
        'SubagentStart': [['--harness', 'codex', 'subagent-start']],
        'SubagentStop': [['--harness', 'codex', 'subagent-stop']],
    }
    for path in [p for p in CODEX.rglob('*') if p.is_file()]:
        text = path.read_text()
        for needle in FORBIDDEN:
            assert needle not in text, (path, needle)
        assert '"env"' not in text, path  # no credential or home plumbing through hook env


def test_codex_local_marketplace_lists_only_the_hooks_shim():
    market = json.loads((ROOT / '.agents' / 'plugins' / 'marketplace.json').read_text())
    entries = {p['name']: p for p in market['plugins']}
    assert set(entries) == {'codex-z0intelligence'}
    assert entries['codex-z0intelligence']['source'] == {'source': 'local', 'path': './harness-adapters/codex-z0intelligence'}


def test_grok_hooks_file_is_capture_only_and_names_its_harness():
    doc = json.loads(GROK_HOOKS.read_text())
    got = commands(doc)
    assert got == {
        'UserPromptSubmit': [['--harness', 'grok', 'prompt']],
        'Stop': [['--harness', 'grok', 'stop']],
        'StopFailure': [['--harness', 'grok', 'stop']],
        'StopCancelled': [['--harness', 'grok', 'stop']],
        'SubagentStart': [['--harness', 'grok', 'subagent-start']],
        'SubagentStop': [['--harness', 'grok', 'subagent-stop']],
    }
    text = GROK_HOOKS.read_text()
    for needle in FORBIDDEN:
        assert needle not in text, needle
    assert sorted(p.name for p in GROK_HOOKS.parent.parent.rglob('*') if p.is_file()) == ['README.md', 'z0-capture.json']
