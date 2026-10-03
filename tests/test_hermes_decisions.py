import importlib.util
import json
import os
import statistics
import subprocess
import sys
import time
from pathlib import Path

import pytest

from z0int import harness_capture as hc
from z0int import hermes_capture as hcap
from z0int import hermes_decisions as H

# The z0int-decisions capture moved into the one Hermes plugin (C4a); these behaviours guard the move.
PLUGIN_DIR = Path(__file__).resolve().parents[1] / 'harness-adapters' / 'hermes-z0intelligence'

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


class Ctx:
    def __init__(self, settings):
        self.settings, self.hooks = settings, {}

    def get_config(self, key, default=None):
        return self.settings.get(key, default)

    def register_hook(self, name, callback):
        self.hooks[name] = callback

    def on_unload(self, callback):
        pass


@pytest.fixture
def plugin(monkeypatch, tmp_path):
    """The plugin in mode shadow; its batch child runs in-process and projections are recorded, not built."""
    monkeypatch.setenv('Z0INT_HOME', str(tmp_path / 'z0'))
    spec = importlib.util.spec_from_file_location('z0int_decisions_plugin_under_test', PLUGIN_DIR / '__init__.py')
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    monkeypatch.setattr(mod, '_hermes_workspace_root', lambda task_id: None)
    monkeypatch.setattr(mod, '_profile_config', lambda: {})
    spawned = []
    monkeypatch.setattr(hcap, '_spawn_project', lambda job, slot: spawned.append(job))
    cap = mod.register(Ctx({'mode': 'shadow', 'z0int_python': sys.executable}))
    monkeypatch.setattr(cap, '_deliver', lambda jobs: [hcap.process(j) for j in jobs] and len(jobs))
    for name in ('on_pre_llm_call', 'on_post_llm_call', 'on_session_end', 'on_approval_request'):
        setattr(mod, name, getattr(cap, name))
    mod.drain = cap.flush
    mod.spawned = spawned
    yield mod
    cap.flush(timeout=30)  # the worker must not write into the next test's Z0INT_HOME
    cap.close()


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
    job = plugin.spawned[0]
    assert job['ctx']['trace_id'] == 's1:t1' and job['payload']['user_message'] == 'fix the build'


def test_hook_stays_under_5ms_even_when_the_child_is_slow(plugin, monkeypatch):
    monkeypatch.setattr(hcap, '_spawn_project', lambda job, slot: time.sleep(0.05))
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


def test_opportunity_record_for_repo_and_non_repo(plugin, tmp_path, monkeypatch):
    monkeypatch.setattr(hcap, '_spawn_project', lambda job, slot: hcap.project(job))
    repo = _git_repo(tmp_path / 'repo')
    outside = tmp_path / 'plain'
    outside.mkdir()
    plugin.on_pre_llm_call(session_id='s', turn_id='1', user_message='what branch am I on?', platform='cli',
                           cwd=str(repo))
    plugin.on_pre_llm_call(session_id='s', turn_id='2', user_message='draft an email', platform='cli',
                           cwd=str(outside))
    plugin.on_pre_llm_call(session_id='s', turn_id='3', user_message='x', platform='cron', cwd=str(repo))
    plugin.drain()
    rec, rec2 = rows(tmp_path, 'opportunities.jsonl')  # the cron turn has no opportunity
    assert rec['opportunity']['trace']['harness'] == 'hermes' and rec['repo'] == str(repo.resolve())
    assert rec['opportunity']['trace']['trace_id'] == 's:1' and rec['gate'] in ('ACT', 'ASK', 'ESCALATE', 'OBSERVE', 'ABSTAIN')
    assert rec2['repo'] is None


def test_the_retired_opportunity_command_writes_no_opportunity(tmp_path):
    """`z0int hermes opportunity` (the retired z0int-decisions child) records nothing but a retired_vehicle row:
    a stale z0int-decisions install cannot write a second, unredacted opportunity stream."""
    repo = _git_repo(tmp_path / 'repo')
    canary = 'c4a-retired-canary-5d6e'
    payload = json.dumps({'session_id': 's', 'trace_id': 's:1', 'user_message': f'what branch am I on? {canary}',
                          'platform': 'cli', 'cwd': str(repo)})
    env = dict(os.environ, Z0INT_HOME=str(tmp_path / 'z0'))
    for argv in (['-m', 'z0int.hermes_decisions', 'opportunity'], ['-m', 'z0int.cli', 'hermes', 'opportunity']):
        done = subprocess.run([sys.executable, *argv], input=payload, text=True, capture_output=True, env=env,
                              timeout=60)
        assert done.returncode == 0, done.stderr
    assert rows(tmp_path, 'opportunities.jsonl') == []
    retired = [r for r in rows(tmp_path, 'failures.jsonl') if r['kind'] == 'retired_vehicle']
    assert len(retired) == 2 and all(r['detail'] == {'vehicle': 'z0int-decisions'} for r in retired)
    assert canary not in ''.join(p.read_text() for p in (tmp_path / 'z0').rglob('*') if p.is_file())
    assert not hasattr(H, 'on_opportunity')


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
    started, held = [], []

    def slow(job, slot):  # a build that is still running keeps its slot
        started.append(job)
        held.append(os.dup(slot.fileno()))

    monkeypatch.setattr(hcap, '_spawn_project', slow)
    for i in range(10):
        hcap.process({'kind': 'turn', 'session_id': 's', 'turn_id': str(i), 'text': f'job {i}', 'cohort': 'interactive',
                      'opportunity': True})
    for fd in held:
        os.close(fd)
    assert len(started) == hc.MAX_CHILDREN
    assert hc.drop_count('hermes') == 10 - hc.MAX_CHILDREN
