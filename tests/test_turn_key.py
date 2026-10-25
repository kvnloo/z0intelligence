"""Canonical cross-harness turn key, its alias table and hook-harness detection (z0int#62 R1/F1/A1)."""
import hashlib
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

from z0int import harness_id
from z0int.harness_id import detect_hook_harness, turn_key, turn_key_from_alias

PLUGIN_DIR = Path(__file__).resolve().parents[1] / 'harness-adapters' / 'hermes-z0intelligence'  # z0int-decisions moved here (C4a)


def sha(*parts):
    return hashlib.sha256('\0'.join(parts).encode()).hexdigest()


def hermes_plugin(monkeypatch, tmp_path):
    monkeypatch.setenv('Z0INT_HOME', str(tmp_path / 'z0'))
    spec = importlib.util.spec_from_file_location('z0int_decisions_plugin_turn_key', PLUGIN_DIR / '__init__.py')
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_turn_key_is_a_pure_function_of_harness_session_and_turn():
    key = turn_key('codex', 's-1', 't-1')
    assert key == turn_key('codex', 's-1', 't-1') and len(key) >= 32
    # another process (a replay, a different vehicle) derives the same key
    code = 'from z0int.harness_id import turn_key; print(turn_key("codex", "s-1", "t-1"))'
    out = subprocess.run([sys.executable, '-c', code], capture_output=True, text=True, check=True).stdout.strip()
    assert out == key
    # different harnesses never collide on the same raw ids
    keys = {h: turn_key(h, 's-1', 't-1') for h in ('claude-code', 'codex', 'grok', 'hermes', 'omp', 'omo', 'dsh')}
    assert len(set(keys.values())) == 7
    assert turn_key('codex', 's-1', 't-2') != key and turn_key('codex', 's-2', 't-1') != key


def test_ledger_order_is_not_part_of_the_key():
    turns = [('claude-code', 's', f'p{i}') for i in range(5)]
    forward = [turn_key(*t) for t in turns]
    backward = [turn_key(*t) for t in reversed(turns)]
    assert forward == list(reversed(backward))


def test_aliases_map_every_trace_convention_to_the_canonical_key(monkeypatch, tmp_path):
    assert set(harness_id.TRACE_ALIASES) >= {'claude-code.prompt_id', 'hermes.session_turn', 'hermes.bend_sha256',
                                              'automatic.sha256', 'dsh.lineage_turn_key', 'omp.bridge_trace'}
    # Claude Code: prompt_id is the turn id
    assert turn_key_from_alias('claude-code.prompt_id', 'p-1', session_id='cs') == turn_key('claude-code', 'cs', 'p-1')
    # automatic.normalize: sha256(parent_agent \0 turn_id), parent_agent = the session id
    assert turn_key_from_alias('automatic.sha256', sha('o1', 't7'), session_id='o1', turn_id='t7',
                               harness='omp') == turn_key('omp', 'o1', 't7')
    # DSH jev lineage turn_key and the OMP bridge per-turn trace are the turn ids of their harness
    assert turn_key_from_alias('dsh.lineage_turn_key', 'msg_42', session_id='d1') == turn_key('dsh', 'd1', 'msg_42')
    assert turn_key_from_alias('omp.bridge_trace', 'tr-9', session_id='o1') == turn_key('omp', 'o1', 'tr-9')
    # Hermes: the decisions plugin's raw "session:turn" and bend-native sha256(session \0 turn) agree
    plugin = hermes_plugin(monkeypatch, tmp_path)
    for session, turn in (('h1', 'abc'), ('h1', 'h1:task:abc')):
        raw = plugin._key(session, turn)
        a = turn_key_from_alias('hermes.session_turn', raw, session_id=session)
        b = turn_key_from_alias('hermes.bend_sha256', sha(session, turn), session_id=session, turn_id=turn)
        assert a == b == turn_key('hermes', session, turn)
    assert turn_key('hermes', 'h1', 'abc') == turn_key('hermes', 'h1', 'h1:abc')


def test_a_hashed_alias_that_does_not_reproduce_is_refused_not_guessed():
    assert turn_key_from_alias('hermes.bend_sha256', 'f' * 64, session_id='h1', turn_id='abc') is None
    assert turn_key_from_alias('automatic.sha256', sha('o1', 'other'), session_id='o1', turn_id='t7', harness='omp') is None
    assert turn_key_from_alias('no.such_convention', 'x', session_id='s') is None


CC = {'hook_event_name': 'UserPromptSubmit', 'session_id': 's', 'prompt_id': 'p-1', 'prompt': 'hi', 'cwd': '/w',
      'transcript_path': '/w/t.jsonl'}
CODEX = {'hook_event_name': 'UserPromptSubmit', 'session_id': 's', 'turn_id': 't-1', 'prompt': 'hi', 'cwd': '/w',
         'model': 'm', 'transcript_path': None}
GROK_ENV = {'GROK_HOOK_EVENT': 'user_prompt_submit', 'GROK_SESSION_ID': 'g-1', 'CLAUDE_PROJECT_DIR': '/w'}


def test_hook_harness_comes_from_payload_and_env_never_the_hook_file():
    # Grok fires ~/.claude/settings.json hooks with a Claude-shaped payload; its runner env says grok
    assert detect_hook_harness(CC, env=GROK_ENV) == ('grok', None)
    assert detect_hook_harness(dict(CC, hookEventName='user_prompt_submit'), env={}) == ('grok', None)
    assert detect_hook_harness(CODEX, env={}) == ('codex', None)
    assert detect_hook_harness(CC, env={}) == ('claude-code', None)
    # normalized in-process events carry no hook-family signal: the explicit harness is all there is
    assert detect_hook_harness({'session_id': 's', 'turn_id': 't', 'prompt': 'x'}, env={}) == ('unknown', None)
    assert detect_hook_harness({'session_id': 's', 'turn_id': 't'}, env={}, explicit='hermes') == ('hermes', None)


def test_explicit_harness_wins_and_a_conflict_is_reported():
    harness, conflict = detect_hook_harness(CODEX, env={}, explicit='grok')
    assert harness == 'grok'
    assert conflict == {'explicit': 'grok', 'detected': 'codex'}
    assert detect_hook_harness(CC, env=GROK_ENV, explicit='claude-code')[1] == {'explicit': 'claude-code',
                                                                               'detected': 'grok'}
    assert detect_hook_harness(CC, env={}, explicit='claude-code') == ('claude-code', None)
    json.dumps(conflict)  # content-free and serialisable
