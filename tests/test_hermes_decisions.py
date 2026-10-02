import importlib.util
import json
import statistics
import subprocess
import time
from pathlib import Path

import pytest

from z0int import hermes_decisions as H

PLUGIN_DIR = Path(__file__).resolve().parents[1] / 'harness-adapters' / 'hermes-z0int-decisions'

CASES = [
    ('fix the failing test in auth.py', 'cli', None),
    ('what branch am I on?', 'telegram', None),
    ('daily digest please', 'cron', 'platform:cron'),
    ('Summarize the diff', 'subagent', 'platform:subagent'),
    ('[System: Your previous response was truncated; continue]', 'cli', 'system_message'),
    ('[IMPORTANT: Background process 12 exited with code 0]', 'discord', 'system_message'),
    ('[SYSTEM NOTICE — iteration budget checkpoint] wrap up', 'cli', 'system_message'),
    ("[System note: This is the user's very first message.]\n\ndeploy the api", 'telegram', None),
    ('[System note: only a note]', 'cli', 'system_message'),
    ('   ', 'cli', 'empty'),
    ([{'type': 'text', 'text': 'describe this image'}, {'type': 'image_url'}], 'cli', None),
]


@pytest.fixture
def plugin(monkeypatch, tmp_path):
    monkeypatch.setenv('Z0INT_HOME', str(tmp_path / 'z0'))
    spec = importlib.util.spec_from_file_location('z0int_decisions_plugin_under_test', PLUGIN_DIR / '__init__.py')
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    spawned = []
    monkeypatch.setattr(mod, '_spawn', lambda argv, payload: spawned.append((argv, payload)))
    monkeypatch.setattr(mod, 'child_argv', lambda: ['z0int', 'hermes', 'opportunity'])
    mod.spawned = spawned
    yield mod
    mod.drain(timeout=30)  # the worker must not write into the next test's Z0INT_HOME


def rows(tmp_path, name):
    p = tmp_path / 'z0' / 'state' / 'hermes' / name
    return [json.loads(l) for l in p.read_text().splitlines()] if p.exists() else []


def test_plugin_and_module_classify_identically(plugin):
    for text, platform, _ in CASES:
        assert plugin.classify(text, platform) == H.classify(text, platform)
    assert plugin.NON_USER_PLATFORMS == H.NON_USER_PLATFORMS and plugin.SYSTEM_PREFIXES == H.SYSTEM_PREFIXES


@pytest.mark.parametrize('text,platform,reason', CASES)
def test_non_user_traffic_is_excluded_and_notes_are_stripped(text, platform, reason):
    got_reason, intent = H.classify(text, platform)
    assert got_reason == reason
    if reason is None:
        assert intent and not intent.startswith('[System note')


def test_user_turn_emits_opportunity_off_the_hot_path_and_never_injects(plugin):
    assert plugin.on_pre_llm_call(session_id='s1', turn_id='t1', user_message='fix the build', platform='cli') is None
    assert plugin.on_pre_llm_call(session_id='s1', turn_id='t2', user_message='digest', platform='cron') is None
    plugin.drain()
    assert len(plugin.spawned) == 1
    argv, payload = plugin.spawned[0]
    assert argv[-1] == 'opportunity' and payload['trace_id'] == 's1:t1' and payload['user_message'] == 'fix the build'


def test_hook_stays_under_5ms_even_when_the_child_is_slow(plugin, monkeypatch):
    monkeypatch.setattr(plugin, '_spawn', lambda argv, payload: time.sleep(0.05))
    history = [{'role': 'user', 'content': 'x'}] + [{'role': 'assistant', 'content': 'y'}] * 200
    samples = []
    for i in range(50):
        t0 = time.perf_counter()
        plugin.on_pre_llm_call(session_id='s', turn_id=str(i), user_message=f'do thing {i}', platform='cli')
        plugin.on_post_llm_call(session_id='s', turn_id=str(i), assistant_response='done', conversation_history=history)
        samples.append(time.perf_counter() - t0)
    assert statistics.median(samples) < 0.005 and max(samples) < 0.05


def test_outcome_records_tools_ask_and_escalation(plugin, tmp_path):
    history = [
        {'role': 'user', 'content': 'old'}, {'role': 'assistant', 'tool_calls': [{'function': {'name': 'terminal'}}]},
        {'role': 'user', 'content': 'ship it'},
        {'role': 'assistant', 'tool_calls': [{'function': {'name': 'terminal'}}, {'function': {'name': 'delegate_task'}}]},
        {'role': 'tool', 'content': 'ok'},
        {'role': 'assistant', 'tool_calls': [{'function': {'name': 'clarify'}}]}, {'role': 'tool', 'content': 'a'},
        {'role': 'assistant', 'content': 'Done.'},
    ]
    plugin.on_pre_llm_call(session_id='s', turn_id='t', user_message='ship it', platform='cli')
    plugin.on_approval_request(session_key='s', command='rm -rf build')
    plugin.on_post_llm_call(session_id='s', turn_id='t', assistant_response='Done.', conversation_history=history,
                            platform='cli', model='stub')
    plugin.on_session_end(session_id='s', turn_id='t', completed=True, failed=False, interrupted=False)
    plugin.drain()
    out = rows(tmp_path, 'outcomes.jsonl')
    assert len(out) == 1  # session_end does not duplicate a closed turn
    o = out[0]
    assert o['trace_id'] == 's:t' and o['tool_calls'] == 3 and o['asked_user'] and o['asked_via_tool']
    assert o['delegated'] and o['approval_requested'] and o['escalated'] and o['excluded'] is None


def test_interrupted_turn_without_post_hook_still_closes(plugin, tmp_path):
    plugin.on_pre_llm_call(session_id='s', turn_id='t9', user_message='long job', platform='cli')
    plugin.on_session_end(session_id='s', turn_id='t9', completed=False, interrupted=True, failed=False)
    plugin.drain()
    (o,) = rows(tmp_path, 'outcomes.jsonl')
    assert o['ended'] == 'interrupted' and o['trace_id'] == 's:t9'


def test_cron_outcome_is_tagged_excluded(plugin, tmp_path):
    plugin.on_pre_llm_call(session_id='c', turn_id='1', user_message='digest', platform='cron')
    plugin.on_post_llm_call(session_id='c', turn_id='1', assistant_response='ok', conversation_history=[])
    plugin.drain()
    assert rows(tmp_path, 'outcomes.jsonl')[0]['excluded'] == 'platform:cron'


def test_hooks_fail_open(plugin, monkeypatch):
    monkeypatch.setattr(plugin, 'classify', lambda *a, **k: 1 / 0)
    assert plugin.on_pre_llm_call(session_id='s', turn_id='t', user_message='x') is None
    assert plugin.on_post_llm_call(session_id='s', turn_id='t', conversation_history=None) is None


def _git_repo(path):
    path.mkdir()
    subprocess.run(['git', 'init', '-q', '-b', 'main', str(path)], check=True)
    subprocess.run(['git', '-C', str(path), '-c', 'user.name=t', '-c', 'user.email=t@t', 'commit', '-q',
                    '--allow-empty', '-m', 'init'], check=True)
    return path


def test_opportunity_record_for_repo_and_non_repo(tmp_path):
    repo = _git_repo(tmp_path / 'repo')
    rec = H.on_opportunity({'session_id': 's', 'trace_id': 's:1', 'user_message': 'what branch am I on?',
                            'platform': 'cli', 'cwd': str(repo)}, root=tmp_path / 'home')
    assert rec['opportunity']['trace']['harness'] == 'hermes' and rec['repo'] == str(repo.resolve())
    assert rec['opportunity']['trace']['trace_id'] == 's:1' and rec['gate'] in ('ACT', 'ASK', 'ESCALATE', 'OBSERVE', 'ABSTAIN')
    outside = tmp_path / 'plain'
    outside.mkdir()
    rec2 = H.on_opportunity({'trace_id': 's:2', 'user_message': 'draft an email', 'cwd': str(outside)},
                            root=tmp_path / 'home')
    assert rec2['repo'] is None
    assert H.on_opportunity({'trace_id': 's:3', 'user_message': 'x', 'platform': 'cron'}, root=tmp_path / 'home') is None
    lines = (tmp_path / 'home' / 'state' / 'hermes' / 'opportunities.jsonl').read_text().splitlines()
    assert len(lines) == 2


def test_decisions_report_joins_by_trace_and_counts_exclusions(tmp_path):
    base = tmp_path / 'state' / 'hermes'
    base.mkdir(parents=True)
    opp = lambda tid, gate: {'gate': gate, 'platform': 'cli', 'opportunity': {
        'trace': {'trace_id': tid}, 'scope': {'mode': 'unscoped', 'families': []}, 'intent': {'request': f'req {tid}'}}}
    (base / 'opportunities.jsonl').write_text(
        '\n'.join(json.dumps(r) for r in [opp('a', 'ACT'), opp('b', 'ACT'), opp('c', 'ASK'), opp('d', 'ACT')]) + '\n')
    outs = [{'trace_id': 'a', 'asked_user': False, 'tool_calls': 2, 'hook_ms': 0.2},
            {'trace_id': 'b', 'asked_user': True, 'tool_calls': 1, 'hook_ms': 0.4},
            {'trace_id': 'c', 'asked_user': False, 'escalated': True, 'tool_calls': 0},
            {'trace_id': 'z', 'excluded': 'platform:cron'}]
    (base / 'outcomes.jsonl').write_text('\n'.join(json.dumps(r) for r in outs) + '\n')
    rep = H.decisions_report(root=tmp_path)
    assert rep['linked'] == 3 and rep['unlinked'] == 1
    assert rep['table'] == {'ACT->answered': 1, 'ACT->asked': 1, 'ASK->escalated': 1}
    assert rep['non_user_turns_excluded'] == {'platform:cron': 1} and rep['tool_calls_on_linked_turns'] == 3
    assert {d['observed'] for d in rep['disagreements']} == {'asked', 'escalated'}


def test_cli_dispatches_hermes_decisions(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv('Z0INT_HOME', str(tmp_path))
    from z0int.cli import main
    assert main(['hermes', 'decisions']) == 0
    assert json.loads(capsys.readouterr().out)['linked'] == 0


def test_trace_key_is_stable_between_hooks_and_not_double_prefixed(plugin):
    assert plugin._key('s1', 's1:task:abc') == 's1:task:abc'
    assert plugin._key('s1', 'abc') == 's1:abc' and plugin._key('s1', '') is None


def test_child_fanout_is_bounded(monkeypatch, tmp_path):
    monkeypatch.setenv('Z0INT_HOME', str(tmp_path / 'z0'))
    spec = importlib.util.spec_from_file_location('z0int_decisions_plugin_bound', PLUGIN_DIR / '__init__.py')
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    started = []

    class Slow:
        def __init__(self, argv, **kw):
            started.append(argv)
            self.stdin = open(tmp_path / f'stdin{len(started)}', 'wb')

        def poll(self):
            return None  # still running

    monkeypatch.setattr(mod.subprocess, 'Popen', Slow)
    for i in range(10):
        mod._spawn(['x'], {'i': i})
    assert len(started) == mod.MAX_CHILDREN
