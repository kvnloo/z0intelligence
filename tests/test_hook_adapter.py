"""Lean hook adapter for the Claude-compatible hook family (Claude Code, Codex, Grok) and normalized events.

Synthetic payloads only. The detached opportunity build is run in-process (same argv/job contract) so that
rows exist when the hook returns; nothing here starts a real harness or reaches a network.
"""
import fcntl
import json
import os
import socket
import subprocess
import sys
import time

import pytest

from z0int import automatic
from z0int import harness_capture as hc
from z0int import hook_entry as he
from z0int import loop_export as le
from z0int.harness_id import turn_key

HOOK_FAMILY = ('claude-code', 'codex', 'grok')
EVENTS = ('session-start', 'prompt', 'subagent-start', 'subagent-stop', 'stop')
CLEAN = ('Z0INT_CAPTURE', 'Z0INT_CAPTURE_PRIVACY', 'Z0INT_CLAUDE_CODE_PACKET', 'Z0INT_CLAUDE_CODE_SHADOW',
         'Z0INT_CLAUDE_CODE_OPPORTUNITIES', 'GROK_HOOK_EVENT', 'GROK_SESSION_ID', 'CLAUDECODE', 'Z0INT_HARNESS_ID')


@pytest.fixture
def home(monkeypatch, tmp_path):
    h = tmp_path / 'z0'
    monkeypatch.setenv('Z0INT_HOME', str(h))
    for name in CLEAN:
        monkeypatch.delenv(name, raising=False)
    return h


@pytest.fixture
def inline(monkeypatch):
    """Run the detached opportunity child in-process through the same argv + job contract."""
    calls = []

    def run(argv, job):
        calls.append(argv)
        assert argv[-1] == 'opportunity' and '--harness' in argv
        he.handle('opportunity', json.dumps(job), argv[argv.index('--harness') + 1])

    monkeypatch.setattr(hc, 'spawn_detached', run)
    return calls


def rows(home, harness, name):
    p = home / 'state' / harness / name
    return [json.loads(line) for line in p.read_text().splitlines()] if p.exists() else []


def hook(event, payload, harness=None, env=None):
    return he.handle(event, json.dumps(payload), harness, env={} if env is None else env)


def transcript(path, *texts, model='m-1', tools=0):
    lines = []
    for i, text in enumerate(texts):
        content = [{'type': 'tool_use', 'name': 'Bash', 'input': {}}] * tools + [{'type': 'text', 'text': text}]
        lines.append({'type': 'assistant', 'message': {'id': f'{path.stem}-m{i}', 'model': model, 'content': content,
                                                       'usage': {'input_tokens': 1, 'output_tokens': 1}}})
    path.write_text(''.join(json.dumps(r) + '\n' for r in lines))
    return path


def payload(h, n=1, *, cwd, prompt='summarise the release notes', session=None, **extra):
    s = session or f'{h}-s'
    if h == 'claude-code':
        p = {'hook_event_name': 'UserPromptSubmit', 'session_id': s, 'prompt_id': f'p{n}', 'transcript_path': None}
    elif h == 'codex':
        p = {'hook_event_name': 'UserPromptSubmit', 'session_id': s, 'turn_id': f't{n}', 'model': 'm-1',
             'transcript_path': None}
    elif h == 'grok':
        p = {'hook_event_name': 'UserPromptSubmit', 'hookEventName': 'user_prompt_submit', 'session_id': s,
             'sessionId': s, 'modelId': 'm-1', 'timestamp': f'2026-10-03T00:00:{n % 60:02d}Z'}
    else:
        p = {'session_id': s, 'turn_id': f't{n}', 'model': 'm-1'}
    p.update(prompt=prompt, cwd=str(cwd), **extra)
    return p


def turn_events(h, tmp_path, *, prompt='Fix the flaky date parser', reply='Which file did you mean?', session=None):
    """One synthetic turn as each harness would deliver it: session start, prompt, a subagent, stop."""
    plain = tmp_path / 'work'
    plain.mkdir(exist_ok=True)
    s = session or f'{h}-turn'
    main_t = transcript(tmp_path / f'{h}-{s}.jsonl', reply)
    agent_t = transcript(tmp_path / f'{h}-{s}-agent.jsonl', 'agent done', tools=1)
    if h == 'grok':
        base = {'session_id': s, 'sessionId': s, 'cwd': str(plain), 'workspaceRoot': str(plain), 'modelId': 'm-1'}
        names = {'session-start': ('SessionStart', 'session_start'), 'prompt': ('UserPromptSubmit', 'user_prompt_submit'),
                 'subagent-start': ('SubagentStart', 'subagent_start'), 'subagent-stop': ('SubagentStop', 'subagent_stop'),
                 'stop': ('Stop', 'stop')}
        def ev(event, **kw):
            return dict(base, hook_event_name=names[event][0], hookEventName=names[event][1], **kw)
        return [('session-start', ev('session-start')),
                ('prompt', ev('prompt', prompt=prompt, timestamp='2026-10-03T00:00:01Z')),
                ('subagent-start', ev('subagent-start', subagentId='a1', agent_type='general')),
                ('subagent-stop', ev('subagent-stop', subagentId='a1', lastAssistantMessage='agent done')),
                ('stop', ev('stop', lastAssistantMessage=reply, last_assistant_message=reply))]
    base = {'session_id': s, 'cwd': str(plain), 'transcript_path': str(main_t) if h == 'claude-code' else None}
    ids = {'prompt_id': 'p1'} if h == 'claude-code' else {'turn_id': 't1', 'model': 'm-1'}
    return [('session-start', dict(base, hook_event_name='SessionStart', source='startup', model='m-1')),
            ('prompt', dict(base, hook_event_name='UserPromptSubmit', prompt=prompt, **ids)),
            ('subagent-start', dict(base, hook_event_name='SubagentStart', agent_id='a1', agent_type='general',
                                    **({} if h == 'claude-code' else ids))),
            ('subagent-stop', dict(base, hook_event_name='SubagentStop', agent_id='a1', agent_type='general',
                                   agent_transcript_path=str(agent_t), last_assistant_message='agent done',
                                   stop_hook_active=False, **({} if h == 'claude-code' else ids))),
            ('stop', dict(base, hook_event_name='Stop', stop_hook_active=False, last_assistant_message=reply, **ids))]


def grok_env(event_payload):
    return {'GROK_HOOK_EVENT': event_payload['hookEventName'], 'GROK_SESSION_ID': event_payload['sessionId']}


def run_turn(h, tmp_path, harness=None, **kw):
    for event, p in turn_events(h, tmp_path, **kw):
        hook(event, p, harness if harness is not None else h, env=grok_env(p) if h == 'grok' else {})


# ----------------------------------------------------------------------------- hot path
def test_prompt_handler_returns_under_5ms_p99_with_the_build_detached(home, monkeypatch, tmp_path):
    spawned = []
    monkeypatch.setattr(hc, 'spawn_detached', lambda argv, job: spawned.append(argv))
    monkeypatch.setattr(automatic, 'handle_event', lambda e: {'action': 'native', 'disabled': True})
    for h in HOOK_FAMILY:
        raws = [json.dumps(payload(h, i, cwd=tmp_path, prompt=f'do thing {i}')) for i in range(200)]
        samples = []
        for raw in raws:
            t0 = time.perf_counter()
            assert he.handle('prompt', raw, h, env={}) is None
            samples.append(time.perf_counter() - t0)
        samples.sort()
        assert samples[int(len(samples) * 0.99) - 1] < 0.005, (h, samples[-3:])
    assert len(spawned) == 600 and all(argv[-1] == 'opportunity' for argv in spawned)


def test_importing_the_hook_entry_loads_only_stdlib_and_the_capture_core():
    code = ('import sys, json; before = set(sys.modules); import z0int.hook_adapter; '
            'print(json.dumps(sorted(set(sys.modules) - before)))')
    new = json.loads(subprocess.run([sys.executable, '-c', code], capture_output=True, text=True, check=True).stdout)
    ours = {'z0int', 'z0int.hook_adapter', 'z0int.hook_entry', 'z0int.harness_capture', 'z0int.harness_id'}
    assert 'z0int.hook_entry' in new and 'z0int.harness_capture' in new
    foreign = [m for m in new if m not in ours and m.split('.')[0] not in sys.stdlib_module_names]
    assert not foreign, foreign


def test_hook_subprocess_wall_time_is_within_bare_python_plus_40ms(tmp_path):
    """p95 hook wall time <= p95 bare-python wall time + 40 ms, both measured in this run, interleaved.

    The detached builds wait on held build slots while timing runs, so the test does not load the host
    itself; on a shared host a burst from another tenant can still inflate one round, so the bound must hold
    in at least one of three rounds, each against its own baseline.
    """
    env = {k: v for k, v in os.environ.items() if k not in CLEAN}
    env['Z0INT_HOME'] = str(tmp_path / 'z0')
    plain = tmp_path / 'plain'
    plain.mkdir()

    def wall(argv, stdin=''):
        t0 = time.perf_counter()
        subprocess.run(argv, input=stdin, text=True, env=env, capture_output=True, check=True)
        return time.perf_counter() - t0

    def p95(xs):
        return sorted(xs)[int(len(xs) * 0.95) - 1]

    held = []
    for i in range(hc.MAX_CHILDREN):
        path = hc.slot_path(i, root=tmp_path / 'z0')
        path.parent.mkdir(parents=True, exist_ok=True)
        held.append(open(path, 'a'))
        fcntl.flock(held[-1], fcntl.LOCK_EX)
    rounds = []
    try:
        for r in range(3):
            bare, timed = [], {h: [] for h in HOOK_FAMILY}
            for i in range(20):  # interleaved, so host load hits the baseline and the hook alike
                bare.append(wall([sys.executable, '-c', 'pass']))
                for h in HOOK_FAMILY:
                    raw = json.dumps(payload(h, 100 * r + i, cwd=plain, prompt=f'question {r}.{i}'))
                    timed[h].append(wall([sys.executable, '-m', 'z0int.hook_adapter', '--harness', h, 'prompt'], raw))
            rounds.append({'bare_ms': round(p95(bare) * 1000, 1),
                           **{h: round(p95(xs) * 1000, 1) for h, xs in timed.items()}})
            if all(p95(xs) <= p95(bare) + 0.040 for xs in timed.values()):
                break
    finally:
        for fh in held:
            fh.close()
    assert all(rounds[-1][h] <= rounds[-1]['bare_ms'] + 40.0 for h in HOOK_FAMILY), rounds


# ----------------------------------------------------------------------------- inertness
def _stdout_per_event(h, tmp_path, env):
    out = []
    for event, p in turn_events(h, tmp_path, session=f'{h}-inert'):
        e = dict(env, **(grok_env(p) if h == 'grok' else {}))
        r = subprocess.run([sys.executable, '-m', 'z0int.hook_adapter', '--harness', h, event], input=json.dumps(p),
                           text=True, env=e, capture_output=True, timeout=60)
        assert r.returncode == 0
        out.append((event, r.stdout))
    return out


def test_hook_stdout_is_byte_identical_with_capture_on_and_off(tmp_path):
    base = {k: v for k, v in os.environ.items() if k not in CLEAN}
    for h in HOOK_FAMILY:
        runs = {}
        for cap in ('1', '0'):
            work = tmp_path / f'{h}-work-{cap}'
            work.mkdir()
            runs[cap] = _stdout_per_event(h, work, dict(base, Z0INT_HOME=str(tmp_path / f'{h}-{cap}'), Z0INT_CAPTURE=cap))
        assert runs['1'] == runs['0'] and all(stdout == '' for _, stdout in runs['1'])
        assert (tmp_path / f'{h}-1' / 'state' / h / 'outcomes.jsonl').exists()
        off = tmp_path / f'{h}-0' / 'state' / h
        assert not any((off / n).exists() for n in ('opportunities.jsonl', 'outcomes.jsonl', 'failures.jsonl'))
    # the existing opt-in SessionStart packet stays the only output, with capture on or off
    repo = tmp_path / 'repo'
    repo.mkdir()
    subprocess.run(['git', 'init', '-q', '-b', 'main', str(repo)], check=True)
    subprocess.run(['git', '-C', str(repo), '-c', 'user.name=t', '-c', 'user.email=t@t', 'commit', '-q',
                    '--allow-empty', '-m', 'init'], check=True)
    for cap in ('1', '0'):
        env = dict(base, Z0INT_HOME=str(tmp_path / f'p{cap}'), Z0INT_CAPTURE=cap, Z0INT_CLAUDE_CODE_PACKET='1')
        r = subprocess.run([sys.executable, '-m', 'z0int.hook_adapter', '--harness', 'claude-code', 'session-start'],
                           input=json.dumps({'hook_event_name': 'SessionStart', 'session_id': 's', 'cwd': str(repo),
                                             'source': 'startup'}), text=True, env=env, capture_output=True, timeout=60)
        assert json.loads(r.stdout)['hookSpecificOutput']['hookEventName'] == 'SessionStart'


# ----------------------------------------------------------------------------- harness detection
def test_harness_is_attributed_from_payload_and_env(home, inline, monkeypatch, tmp_path):
    monkeypatch.setattr(automatic, 'handle_event', lambda e: {'action': 'native'})
    cc_shaped = payload('claude-code', cwd=tmp_path, session='g1', prompt='hello grok')
    # a hook in ~/.claude/settings.json fired by Grok (compat scan): no --harness, Grok's runner env
    hook('prompt', cc_shaped, None, env={'GROK_HOOK_EVENT': 'user_prompt_submit', 'GROK_SESSION_ID': 'g1'})
    assert len(rows(home, 'grok', 'opportunities.jsonl')) == 1 and not (home / 'state' / 'claude-code').exists()
    hook('prompt', payload('codex', cwd=tmp_path), None)
    assert len(rows(home, 'codex', 'opportunities.jsonl')) == 1
    hook('prompt', payload('claude-code', cwd=tmp_path), None)
    assert len(rows(home, 'claude-code', 'opportunities.jsonl')) == 1
    # explicit --harness wins; the disagreement is written down
    hook('prompt', payload('codex', 2, cwd=tmp_path, session='cx'), 'grok')
    assert any(r['session_id'] == 'cx' for r in rows(home, 'grok', 'opportunities.jsonl'))
    (mis,) = [f for f in rows(home, 'grok', 'failures.jsonl') if f['kind'] == 'misattribution']
    assert mis['detail'] == {'explicit': 'grok', 'detected': 'codex'}
    # no signal and no --harness: an explicit unsupported_harness row, never a silent drop
    hook('prompt', {'session_id': 's', 'prompt': 'x', 'cwd': str(tmp_path)}, None)
    assert rows(home, '_unsupported', 'failures.jsonl')[-1]['kind'] == 'unsupported_harness'


# ----------------------------------------------------------------------------- subagents (#56 M1)
def test_subagent_turns_are_agent_cohort_and_join_on_turn_key(home, inline, tmp_path):
    run_turn('claude-code', tmp_path, session='s1')
    opps = {r['turn_key']: r for r in rows(home, 'claude-code', 'opportunities.jsonl')}
    outs = {r['turn_key']: r for r in rows(home, 'claude-code', 'outcomes.jsonl')}
    agent = turn_key('claude-code', 's1', 'agent:a1')
    assert opps[agent]['cohort'] == outs[agent]['cohort'] == 'agent'
    assert outs[agent]['tool_calls'] == 1  # measured from the agent transcript
    assert opps[turn_key('claude-code', 's1', 'p1')]['cohort'] == 'interactive'
    # Fallback pair for Claude Code builds without SubagentStart/SubagentStop: PreToolUse(Agent|Task) opens and
    # PostToolUse(Agent|Task) closes, both keyed by the tool_use id (real hook payload shapes, no agent id).
    base = {'session_id': 's2', 'cwd': str(tmp_path), 'transcript_path': str(tmp_path / 's2.jsonl'),
            'permission_mode': 'default', 'tool_name': 'Task', 'tool_use_id': 'tu-1',
            'tool_input': {'description': 'd', 'prompt': 'look into it', 'subagent_type': 'g'}}
    hook('subagent-start', dict(base, hook_event_name='PreToolUse'), 'claude-code')
    hook('subagent-start', dict(base, hook_event_name='PreToolUse', tool_name='Bash', tool_use_id='tu-2'), 'claude-code')
    hook('subagent-stop', dict(base, hook_event_name='PostToolUse',
                               tool_response={'content': [{'type': 'text', 'text': 'done'}], 'totalDurationMs': 5}),
         'claude-code')
    fallback = turn_key('claude-code', 's2', 'agent:tu-1')
    assert {r['turn_key'] for r in rows(home, 'claude-code', 'opportunities.jsonl')} >= {fallback}
    assert turn_key('claude-code', 's2', 'agent:tu-2') not in {r['turn_key'] for r in
                                                                 rows(home, 'claude-code', 'opportunities.jsonl')}
    assert any(r['turn_key'] == fallback and r['cohort'] == 'agent' for r in rows(home, 'claude-code', 'outcomes.jsonl'))
    # a real SubagentStop names the agent id only: it closes that agent's turn, never a tool_use-keyed one
    hook('subagent-stop', {'hook_event_name': 'SubagentStop', 'session_id': 's2', 'agent_id': 'a9', 'cwd': str(tmp_path),
                           'last_assistant_message': 'ok', 'stop_hook_active': False}, 'claude-code')
    assert turn_key('claude-code', 's2', 'agent:a9') in {r['turn_key'] for r in rows(home, 'claude-code', 'outcomes.jsonl')}
    table = le.build_table(home / 'state' / 'claude-code', cohort_fn=lambda sid: 'interactive')
    by_trace = {r['turn_key']: r['cohort'] for r in table}
    assert sorted(by_trace.values()) == ['agent', 'agent', 'interactive']
    # Codex subagents take the same path
    run_turn('codex', tmp_path, session='x1')
    assert any(r['cohort'] == 'agent' and r['turn_key'] == turn_key('codex', 'x1', 'agent:a1')
               for r in rows(home, 'codex', 'outcomes.jsonl'))


# ----------------------------------------------------------------------------- #62 A1
def test_the_same_turn_round_trips_to_the_same_semantic_record(home, inline, monkeypatch, tmp_path):
    monkeypatch.setattr(automatic, 'handle_event', lambda e: {'action': 'native'})
    views = {}
    for h in HOOK_FAMILY:
        (tmp_path / h).mkdir()
        run_turn(h, tmp_path / h, session='same')
        opps = rows(home, h, 'opportunities.jsonl')
        outs = rows(home, h, 'outcomes.jsonl')
        (opp,) = [r for r in opps if r['cohort'] == 'interactive']
        (out,) = [r for r in outs if r['cohort'] == 'interactive']
        assert opp['turn_key'] == out['turn_key'] and opp['harness'] == out['harness'] == h
        views[h] = (hc.semantic_view(opp), hc.semantic_view(out))
    assert views['claude-code'] == views['codex'] == views['grok']
    assert views['codex'][0]['model_id'] == 'm-1' and views['codex'][1]['asked_user'] is True


# ----------------------------------------------------------------------------- privacy
def test_outcome_and_failure_rows_hold_no_prompt_response_command_or_path_text(home, inline, monkeypatch, tmp_path):
    monkeypatch.setattr(automatic, 'handle_event', lambda e: {'action': 'native'})
    secret_dir = tmp_path / 'secret-repo-xyz'
    secret_dir.mkdir()
    secret = 'refactor the payments module for /home/someone/private'
    for h in HOOK_FAMILY:
        for event, p in turn_events(h, secret_dir, prompt=secret, reply=secret + '?', session=f'{h}-priv'):
            hook(event, p, h, env=grok_env(p) if h == 'grok' else {})
        hook('subagent-start', {'hook_event_name': 'PreToolUse', 'session_id': f'{h}-priv', 'cwd': str(secret_dir),
                                'tool_name': 'Task', 'tool_use_id': 'tu-9',
                                'tool_input': {'prompt': secret, 'command': 'rm -rf /home/someone/private'}}, h)
        hook('prompt', payload('codex', 7, cwd=secret_dir, prompt=secret, session=f'{h}-mis'), h)  # misattributed
    blob = ''
    for h in HOOK_FAMILY:
        for name in ('outcomes.jsonl', 'failures.jsonl'):
            p = home / 'state' / h / name
            blob += p.read_text() if p.exists() else ''
    assert 'misattribution' in blob and 'turn_outcome' in blob
    for needle in ('payments', '/home/someone', 'secret-repo-xyz', 'rm -rf', str(tmp_path)):
        assert needle not in blob, needle
    opps = ''.join((home / 'state' / h / 'opportunities.jsonl').read_text() for h in HOOK_FAMILY)
    assert 'payments' not in opps


# ----------------------------------------------------------------------------- capture-only seam
def test_only_claude_code_reaches_the_automatic_path(home, inline, monkeypatch, tmp_path):
    calls, connects = [], []
    monkeypatch.setattr(automatic, 'handle_event', lambda e: calls.append(('handle_event', e['harness'])) or
                        {'action': 'native'})
    monkeypatch.setattr(automatic, 'post', lambda *a: calls.append(('post',)) or {})

    def guard(self, address):
        connects.append(address)
        raise ConnectionRefusedError(111, 'socket guard')

    monkeypatch.setattr(socket.socket, 'connect', guard)
    monkeypatch.setattr(socket.socket, 'connect_ex', lambda self, address: connects.append(address) or 111)
    for h in ('codex', 'grok'):
        run_turn(h, tmp_path, session=f'{h}-seam')
    for h in ('hermes', 'omp', 'omo', 'dsh'):
        hook('prompt', payload(h, cwd=tmp_path), h)
        hook('stop', {'session_id': f'{h}-s', 'turn_id': 't1', 'last_assistant_message': 'done'}, h)
    assert calls == [] and connects == []
    for h in ('codex', 'grok', 'hermes', 'omp', 'omo', 'dsh'):
        assert rows(home, h, 'opportunities.jsonl') and rows(home, h, 'outcomes.jsonl'), h
    hook('prompt', payload('claude-code', cwd=tmp_path), 'claude-code')  # positive control
    assert calls == [('handle_event', 'claude-code')] and connects == []


# ----------------------------------------------------------------------------- detached build fan-out (MAX_CHILDREN)
def test_detached_builds_share_a_bounded_set_of_slots(home, monkeypatch, tmp_path):
    monkeypatch.setattr(hc, 'SLOT_WAIT_S', 0.3)
    p = payload('codex', cwd=tmp_path)
    job = {'payload': p, 'ctx': hc.begin_turn('codex', p)}
    held = []
    for i in range(hc.MAX_CHILDREN):
        path = hc.slot_path(i)
        path.parent.mkdir(parents=True, exist_ok=True)
        fh = open(path, 'a')
        fcntl.flock(fh, fcntl.LOCK_EX)
        held.append(fh)
    he.handle('opportunity', json.dumps(job), 'codex')
    assert rows(home, 'codex', 'opportunities.jsonl') == []
    assert [r['reason'] for r in rows(home, 'codex', 'drops.jsonl')] == ['fanout_cap']
    for fh in held:
        fh.close()
    he.handle('opportunity', json.dumps(job), 'codex')
    assert len(rows(home, 'codex', 'opportunities.jsonl')) == 1


# ----------------------------------------------------------------------------- Claude Code Stop: measured per prompt
def cc_line(path, row):
    with path.open('a') as fh:
        fh.write(json.dumps(row) + '\n')


def cc_user(path, pid, content):
    cc_line(path, {'type': 'user', 'promptId': pid, 'message': {'role': 'user', 'content': content}})


def cc_assistant(path, mid, text='', tools=0):
    content = [{'type': 'tool_use', 'id': f'tu-{mid}', 'name': 'Agent', 'input': {}}] * tools
    content += [{'type': 'text', 'text': text}] if text else []
    cc_line(path, {'type': 'assistant', 'message': {'id': mid, 'model': 'm-1', 'content': content,
                                                    'usage': {'input_tokens': 1, 'output_tokens': 1}}})


def test_claude_code_stop_measures_its_own_prompt_and_closes_a_lagging_turn_explicitly(home, inline, tmp_path):
    """Real CC 2.1.288 race: the Stop for p1 fires before its reply is flushed, and the next prompt (a
    task-notification) arrives before that. p1 still gets its outcome (from the payload, with a
    partial_measurement row); its late messages are not credited to p2, they close p1 with an explicit row."""
    plain = tmp_path / 'work'
    plain.mkdir()
    t = tmp_path / 'cc.jsonl'
    base = {'session_id': 's1', 'cwd': str(plain), 'transcript_path': str(t)}
    hook('prompt', dict(base, hook_event_name='UserPromptSubmit', prompt_id='p1', prompt='hand one subtask on'),
         'claude-code')
    cc_user(t, 'p1', 'hand one subtask on')
    hook('stop', dict(base, hook_event_name='Stop', prompt_id='p1', stop_hook_active=False,
                      last_assistant_message='final: the subagent finished.'), 'claude-code')
    cc_assistant(t, 'm1', tools=1)
    cc_user(t, 'p1', [{'type': 'tool_result', 'tool_use_id': 'tu-m1', 'content': 'ok'}])
    cc_assistant(t, 'm2', text='final: the subagent finished.')
    note = '<task-notification><task-id>a1</task-id></task-notification>'
    hook('prompt', dict(base, hook_event_name='UserPromptSubmit', prompt_id='p2', prompt=note), 'claude-code')
    cc_user(t, 'p2', note)
    cc_assistant(t, 'm3', text='Noted.')
    hook('stop', dict(base, hook_event_name='Stop', prompt_id='p2', stop_hook_active=False,
                      last_assistant_message='Noted.'), 'claude-code')

    outs = {r['trace_id']: r for r in rows(home, 'claude-code', 'outcomes.jsonl')}
    assert set(outs) == {'p1', 'p2'}
    p1, p2 = outs['p1'], outs['p2']
    assert p1['cohort'] == 'interactive' and p1['work_item_id'] and p1['asked_user'] is False
    assert p1['tool_calls'] is None and p1['assistant_messages'] is None  # nothing measurable at its Stop
    # the harness-injected prompt has no opportunity (6fee859 rule) but its outcome is cohort harness, own work item
    assert p2['cohort'] == 'harness' and p2['work_item_id'] not in (None, p1['work_item_id'])
    assert (p2['tool_calls'], p2['assistant_messages']) == (0, 1)  # only its own message, nothing of p1's
    assert not [r for r in rows(home, 'claude-code', 'opportunities.jsonl') if r['trace_id'] == 'p2']
    fails = rows(home, 'claude-code', 'failures.jsonl')
    k1 = turn_key('claude-code', 's1', 'p1')
    assert any(f['kind'] == 'partial_measurement' and f['turn_key'] == k1 and f['detail'].get('missing') for f in fails)
    (late,) = [f for f in fails if 'late_messages' in f['detail']]
    assert late['kind'] == 'partial_measurement' and late['turn_key'] == k1 and late['detail']['late_messages'] == 2
    assert late['work_item_id'] == p1['work_item_id'] and late['cohort'] == 'interactive'
    assert not [f for f in fails if f['turn_key'] == p2['turn_key']]


def test_claude_code_stop_measurement_errors_are_failure_rows_not_silent(home, inline, tmp_path, monkeypatch):
    from z0int import claude_code
    t = tmp_path / 'cc.jsonl'
    base = {'session_id': 's1', 'cwd': str(tmp_path), 'transcript_path': str(t)}
    hook('prompt', dict(base, hook_event_name='UserPromptSubmit', prompt_id='p1', prompt='fix it'), 'claude-code')
    cc_user(t, 'p1', 'fix it')
    cc_assistant(t, 'm1', text='Done?')

    def broken(fresh):
        raise RuntimeError('unexpected transcript shape')
    monkeypatch.setattr(claude_code, 'turn_behaviour', broken)
    hook('stop', dict(base, hook_event_name='Stop', prompt_id='p1', stop_hook_active=False,
                      last_assistant_message='Done?'), 'claude-code')
    (out,) = rows(home, 'claude-code', 'outcomes.jsonl')
    assert out['trace_id'] == 'p1' and out['asked_user'] is True and out['tool_calls'] is None
    (err,) = [f for f in rows(home, 'claude-code', 'failures.jsonl') if 'error' in f['detail']]
    assert err['kind'] == 'partial_measurement' and err['detail']['error'] == 'RuntimeError'
    assert err['turn_key'] == out['turn_key'] and 'unexpected' not in json.dumps(err)
