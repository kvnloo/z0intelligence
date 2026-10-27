"""DSH observe-only capture (harness-adapters/dsh-z0intelligence) through the C1 hook adapter.

A node driver applies the plugin to a fake cordis ctx with a mocked spawn and prints the hook events it would
send; each event then goes through ``hook_adapter --harness dsh`` (the detached opportunity build is run
in-process, as in test_hook_adapter). Synthetic prompts only; nothing starts DSH or opens a socket.
"""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

from z0int import harness_capture as hc
from z0int import hook_entry as he
from z0int.harness_id import turn_key, turn_key_from_alias

ROOT = Path(__file__).resolve().parents[1]
INDEX = (ROOT / 'harness-adapters' / 'dsh-z0intelligence' / 'index.mjs').as_uri()
NODE = shutil.which('node')
pytestmark = pytest.mark.skipif(NODE is None, reason='node is not installed')

DRIVER = """
const p = await import(%(index)s)
const calls = []
const spawn = (cmd, args, opts) => {
  let input = ''
  return {unref() {}, on() {}, stdin: {on() {}, write(s) { input += s },
          end(s) { if (s) input += s; calls.push({cmd, args, detached: opts.detached, payload: JSON.parse(input)}) }}}
}
const hs = {}
p.apply({on: (e, fn) => { (hs[e] ??= []).push(fn) }}, {capture: true},
        {spawn, env: {Z0INT_HOME: %(home)s, Z0INT_PYTHON: 'python3'}})
const req = (agent, turn, step) => hs['agent/request'][0]({agent, turn, step}, async () => ({provider: 'deepseek-official', model: 'deepseek-flash'}))
const stop = async (agent, turn) => { for (const fn of hs['agent/turn-stopping']) await fn({agent, turn}) }
const root = (text) => ({id: 'session-d1', parentId: null, session: {header: {id: 'd1', cwd: null}},
                         frozenMessages: [{role: 'user', content: [{type: 'text', text}]}]})
const child = {id: 'c0ffee-child', parentId: null, session: {header: {id: 'c1', parentSession: 'd1', origin: 'subagent'}},
               frozenMessages: [{role: 'user', content: 'sub task'}]}
await req(root('summarise the release notes'), 1, 1)
await req(root('summarise the release notes'), 1, 2)
await req(child, 1, 1)
await stop(child, 1)
await stop(root('x'), 1)
await req(root('now update the changelog'), 2, 1)
await stop(root('x'), 2)
console.log(JSON.stringify({calls, keys: [p.canonicalTurnKey('d1', 'session-d1:1'), p.canonicalTurnKey('d1', 'session-d1:2')]}))
"""


@pytest.fixture
def home(monkeypatch, tmp_path):
    h = tmp_path / 'z0'
    monkeypatch.setenv('Z0INT_HOME', str(h))
    for name in ('Z0INT_CAPTURE', 'Z0INT_CAPTURE_PRIVACY', 'GROK_HOOK_EVENT', 'GROK_SESSION_ID'):
        monkeypatch.delenv(name, raising=False)
    return h


@pytest.fixture
def inline(monkeypatch):
    def run(argv, job):
        he.handle('opportunity', json.dumps(job), argv[argv.index('--harness') + 1])
    monkeypatch.setattr(hc, 'spawn_detached', run)


def rows(home, name):
    p = home / 'state' / 'dsh' / name
    return [json.loads(line) for line in p.read_text().splitlines()] if p.exists() else []


def drive(home):
    script = DRIVER % {'index': json.dumps(INDEX), 'home': json.dumps(str(home))}
    proc = subprocess.run([NODE, '--unhandled-rejections=strict', '--input-type=module', '-e', script],
                          capture_output=True, text=True, timeout=60, check=False)
    assert proc.returncode == 0, proc.stderr[-600:]
    return json.loads(proc.stdout.strip().splitlines()[-1])


def test_a_user_turn_gives_one_joined_opportunity_and_outcome(home, inline):
    driven = drive(home)
    calls = driven['calls']
    assert [c['args'] for c in calls] == [['-m', 'z0int.hook_adapter', '--harness', 'dsh', e]
                                         for e in ('prompt', 'stop', 'prompt', 'stop')]
    assert all(c['detached'] is True for c in calls)
    for c in calls:  # exactly what the shim would pipe to the CLI
        assert he.handle(c['args'][-1], json.dumps(c['payload']), c['args'][3]) is None
    opps, outs = rows(home, 'opportunities.jsonl'), rows(home, 'outcomes.jsonl')
    assert [r['schema'] for r in opps] == ['z0int.dsh.opportunity_record.v0'] * 2
    assert [r['schema'] for r in outs] == ['z0int.dsh.turn_outcome.v0'] * 2
    want = [turn_key_from_alias('dsh.lineage_turn_key', f'session-d1:{n}', session_id='d1') for n in (1, 2)]
    assert want == [turn_key('dsh', 'd1', f'session-d1:{n}') for n in (1, 2)]
    assert [r['turn_key'] for r in opps] == want == [r['turn_key'] for r in outs]
    assert driven['keys'] == want, 'the plugin computes the same canonical key as harness_id.turn_key'
    assert [r['cohort'] for r in opps] == ['interactive'] * 2 and opps[0]['work_item_id'] != opps[1]['work_item_id']
    assert all(r['model_id'] == 'deepseek-flash' for r in opps)
    assert not [f for f in rows(home, 'failures.jsonl') if f['kind'] in ('misattribution', 'unsupported_schema')]
    text = (home / 'state' / 'dsh' / 'opportunities.jsonl').read_text() + (home / 'state' / 'dsh' / 'outcomes.jsonl').read_text()
    assert 'release notes' not in text and 'changelog' not in text
