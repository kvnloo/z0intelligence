import json
import os
import sqlite3

import pytest

from z0int import utilization as util

from test_claude_code_tokenomics import OBS_TEXT, T0, WORKER, assistant, iso, packet, prompt, result, write

SECRET = 'PROMPT-TEXT-MUST-NOT-LEAK'


def cli_session(cwd='/home/u/repo', entrypoint='cli', sid='S'):
    """Four frontier turns: packet carried, ObservationPack shortened, worker displaced, plain."""
    head = {'type': 'user', 'promptId': 'p0', 'timestamp': iso(-100), 'cwd': cwd, 'entrypoint': entrypoint,
            'sessionId': sid, 'message': {'role': 'user', 'content': SECRET}}
    return [
        head,
        assistant('m0', -99, input_tokens=5, output_tokens=5),          # before z0 was live: unseen
        packet(0),
        prompt('p1', 1, SECRET),
        assistant('m1', 2, tools=[('t1', 'Bash', {'command': 'pytest -q'})], input_tokens=10, output_tokens=5),
        result('t1', 3, OBS_TEXT),
        assistant('m2', 4, input_tokens=1, cache_read_input_tokens=1200, output_tokens=9),
        prompt('p2', 10, SECRET),
        assistant('m3', 11, tools=[('t3', WORKER, {'task': 'classify', 'context': 'c' * 796})], output_tokens=210),
        result('t3', 12, json.dumps({'ok': True, 'subagent_id': 'sub1', 'provider': 'groot', 'model': 'q',
                                     'input_tokens': 220, 'output_tokens': 40, 'output': 'z' * 160}), pid='p2'),
        assistant('m4', 13, input_tokens=1, output_tokens=4),
        prompt('p3', 20, SECRET),
        assistant('m5', 21, input_tokens=1, output_tokens=4),
    ]


def receipt(subagent_id, caller, ts=T0 + 12, status='completed'):
    return {'trace_id': 'r-' + caller, 'provider': 'groot', 'model': 'q', 'input_tokens': 220, 'output_tokens': 40,
            'baseline_input_tokens': 234, 'baseline_output_tokens': 40, 'ts': ts,
            'extra': {'harness': 'claude-code', 'status': status, 'physical_call_attempted': True,
                      'subagent_id': subagent_id, 'caller_trace_id': caller,
                      'baseline': {'method': 'ceil_utf8_bytes_div_4_v1'}}}


def hermes_db(path, rows):
    con = sqlite3.connect(path)
    con.execute('create table sessions (id text, source text, parent_session_id text, cwd text, api_call_count int,'
                ' started_at real, ended_at real, last_activity_at real)')
    con.execute('create table messages (id integer primary key, session_id text, role text, content text, timestamp real)')
    for sid, source, parent, msgs in rows:
        con.execute('insert into sessions values (?,?,?,?,?,?,?,?)', (sid, source, parent, '/home/u', len(msgs), T0, None, T0 + 5))
        for i, role in enumerate(msgs):
            con.execute('insert into messages (session_id, role, content, timestamp) values (?,?,?,?)',
                        (sid, role, SECRET, T0 + i))
    con.commit()
    con.close()


@pytest.fixture
def world(tmp_path):
    projects = tmp_path / 'projects'
    write(projects / 'proj' / 'S.jsonl', cli_session())
    write(projects / 'proj' / 'S' / 'subagents' / 'agent-a.jsonl', [
        {**prompt('p1', 5, SECRET), 'isSidechain': True}, assistant('sa1', 6, input_tokens=3, output_tokens=3)])
    write(projects / 'evalproj' / 'E.jsonl', cli_session(cwd='/tmp/cc-arm1', entrypoint='sdk-cli', sid='E'))
    write(projects / 'agentproj' / 'A.jsonl', [
        {**prompt('pa', 1, SECRET), 'cwd': '/home/u/repo', 'entrypoint': 'sdk-cli', 'sessionId': 'A'},
        assistant('ma', 2, output_tokens=3)])
    for p in projects.glob('**/*.jsonl'):
        os.utime(p, (T0 + 30, T0 + 30))
    z0 = tmp_path / 'z0'
    state = z0 / 'state' / 'claude-code'
    write(state / 'opportunities.jsonl', [
        {'schema': 'z0int.claude_code.opportunity_record.v0', 'session_id': 'S', 'gate': 'ACT',
         'opportunity': {'intent': {'request': SECRET}, 'scope': {}, 'trace': {'trace_id': 'p3'},
                         'provenance': {'built_at': iso(20)}}},
        {'schema': 'z0int.claude_code.opportunity_record.v0', 'session_id': 'S', 'gate': 'ASK',
         'opportunity': {'intent': {'request': SECRET}, 'scope': {}, 'trace': {'trace_id': 'p2'},
                         'provenance': {'built_at': iso(10)}}},
    ])
    write(state / 'outcomes.jsonl', [
        {'session_id': 'S', 'trace_id': 'p3', 'asked_user': False},
        {'session_id': 'S', 'trace_id': 'p2', 'asked_user': False},
    ])
    (state / 'S.json').write_text(json.dumps({'message_ids': ['m0', 'm1']}))
    write(z0 / 'receipts' / 'decisions.jsonl', [receipt('sub1', 'linked'), receipt('probe', 'effects-1')])
    hermes = tmp_path / 'hermes'
    hermes.mkdir()
    hermes_db(hermes / 'state.db', [('h1', 'cli', None, ['user', 'assistant', 'user', 'assistant']),
                                    ('h2', 'cron', None, ['user'])])
    omp = tmp_path / 'omp'
    write(omp / 'agent' / 'sessions' / '-home-u' / 's1.jsonl', [
        {'type': 'session', 'cwd': '/home/u/repo'},
        {'type': 'message', 'timestamp': iso(1), 'message': {'role': 'user', 'content': SECRET}},
        {'type': 'message', 'timestamp': iso(2), 'message': {'role': 'assistant'}},
        {'type': 'message', 'timestamp': iso(3), 'message': {'role': 'assistant'}}])
    os.utime(omp / 'agent' / 'sessions' / '-home-u' / 's1.jsonl', (T0 + 30, T0 + 30))
    return dict(projects=projects, z0_home=z0, hermes_home=hermes, omp_home=omp, codex_home=tmp_path / 'codex')


def compute(world, **kw):
    return util.compute('1d', now=T0 + 3600, **world, **kw)


def test_cohorts():
    assert util.cohort_for('claude-code', cwd='/home/u/x', entrypoint='cli') == 'interactive'
    assert util.cohort_for('claude-code', cwd='/home/u/x', entrypoint='sdk-cli') == 'agent'
    assert util.cohort_for('claude-code', cwd='/tmp/cc-1', entrypoint='cli') == 'eval'
    assert util.cohort_for('claude-code', cwd='/home/u/.cache/sandbox/w', entrypoint='cli') == 'eval'
    assert util.cohort_for('hermes', source='cron') == 'agent'
    assert util.cohort_for('hermes', source='telegram') == 'interactive'
    assert util.cohort_for('hermes', source='cli', parent=True) == 'agent'
    assert util.cohort_for('codex', originator='codex_exec') == 'agent'
    assert util.cohort_for('omp', cwd='/tmp') == 'eval'


def test_units_from_roles():
    assert util._units_from_roles(['user', 'assistant', 'assistant', 'user', 'user', 'assistant', 'user']) == (2, 3)


def test_claude_code_interactive_funnel(world):
    rep = compute(world)
    row = rep['by_harness']['claude-code']['by_cohort']['interactive']
    # 5 root turns (m0 pre-z0, p1, p2, p3 ... m0's turn is the head prompt) + 1 subagent turn
    assert row['units'] == 5 and row['units_root'] == 4 and row['units_subagent'] == 1
    assert row['seen_units'] == 4          # m0's turn predates every z0 signal; billing it later is not "seen"
    assert row['breakdown']['unseen_but_billed_after_the_fact'] == {'stop_hook_backfill': 1}
    assert row['context_carried_units'] == 3  # root turns after the SessionStart packet (subagents get none)
    assert row['utilized_shortened'] == 1 and row['utilized_displaced'] == 1 and row['utilized_units'] == 2
    assert row['offloads_attempted'] == 1 and row['offloads_completed'] == 1 and row['offloads_verified'] == 0
    # inline counterfactual: worker output (40) minus the delegation args the parent wrote (~207): negative, kept honest
    assert row['breakdown']['displacement_counterfactual'] == {'frontier_agent_inline': 1}
    assert row['frontier_tokens_displaced_est'] < 0 and row['frontier_tokens_displaced_measured'] is None
    assert row['decisions_shadow'] == 2 and row['decisions_enforced'] == 0
    assert row['decisions_linked_to_outcome'] == 2 and row['decisions_gate_agrees_with_observed'] == 1
    assert row['breakdown']['decisions_by_gate'] == {'ACT': 1, 'ASK': 1}


def test_eval_excluded_and_external_estimated(world):
    rep = compute(world)
    h = rep['headline']
    cc = rep['by_harness']['claude-code']['by_cohort']
    assert cc['eval']['units'] == 4 and h['eval_units_excluded'] == 4
    assert cc['agent']['units'] == 1
    # programmatic probe receipt is eval, never a user unit
    assert cc['eval']['breakdown']['worker_receipts'] == {'programmatic_unlinked': 1}
    assert cc['interactive']['breakdown']['worker_receipts'] == {'transcript_linked': 1}
    hermes = rep['by_harness']['hermes']['by_cohort']
    assert hermes['interactive']['units'] == 2 and hermes['agent']['units'] == 0 and hermes['interactive']['seen_units'] == 0
    assert rep['by_harness']['omp']['by_cohort']['interactive']['units'] == 1
    n = 5 + 1 + 2 + 1
    assert h['denominator_frontier_units'] == n and h['numerator_utilized_units'] == 2
    assert h['z0_utilization'] == round(2 / n, 4)
    assert h['measurement_state'] == 'partial'
    assert h["coverage"] == round(4 / n, 4)  # agent A shows no z0 evidence


def test_complete_without_external(world):
    rep = compute(world, external=False)
    assert rep['headline']['measurement_state'] == 'complete'
    assert rep['headline']['denominator_frontier_units'] == 6


def test_tokenomics_compatible_and_count_only(world):
    rep = compute(world)
    t = rep['totals']
    assert t['measured_tokens_avoided'] == 0 and t['authoritative'] is False
    assert set(t['estimated_tokens_avoided_by_mechanism']) == {'worker_offload', 'obspack_net_first_exposure'}
    assert rep['period']['end_ts'] == T0 + 3600 and rep['range'] == '1d'
    f = rep['tokenomics_fields']
    assert f['utilization.z0'] == rep['headline']['z0_utilization']
    assert 'utilization.by_harness.hermes.units' in f
    blob = json.dumps(rep)
    assert SECRET not in blob and 'c' * 50 not in blob
    assert 'z0 utilization' in util.format_text(rep)


def test_enforced_decision_with_verified_outcome_counts_as_improved(tmp_path):
    projects = tmp_path / 'projects'
    rows = cli_session()[:2] + [
        prompt('p9', 1, SECRET),
        {'type': 'attachment', 'timestamp': iso(1.5), 'attachment': {
            'type': 'hook_additional_context', 'hookEvent': 'UserPromptSubmit', 'content': ['<z0-route kind=x>ctx']}},
        assistant('m9', 2, output_tokens=3)]
    write(projects / 'p' / 'S.jsonl', rows)
    os.utime(projects / 'p' / 'S.jsonl', (T0 + 30, T0 + 30))
    z0 = tmp_path / 'z0'
    write(z0 / 'state' / 'claude-code' / 'outcomes.jsonl', [{'trace_id': 'p9', 'asked_user': False, 'verified': True}])
    rep = util.compute('1d', now=T0 + 3600, projects=projects, z0_home=z0, external=False)
    row = rep['by_harness']['claude-code']['by_cohort']['interactive']
    assert row['decisions_enforced'] == 1 and row['prompt_context_injections'] == 1
    assert row['utilized_improved'] == 1 and row['utilized_units'] == 1


def test_compaction_resets_carried_context():
    inj = [{'ts': 10.0, 'event': 'SessionStart'}]
    assert util._carried(20.0, inj, [])
    assert not util._carried(20.0, inj, [16.0])
    assert util._carried(20.0, inj, [14.0]) is True          # 4 s after injection: same compact event
    assert not util._carried(5.0, inj, [])


def test_cli_wiring(world, monkeypatch, capsys):
    from z0int import cli
    monkeypatch.setattr(util, 'compute', lambda *a, **k: util.build_report(
        util.Table(), range_spec='7d', since=T0, now=T0 + 1, sources={}, shadow=True) | {'tokenomics_fields': {}})
    assert cli.main(['utilization', '--range', '7d', '--json']) == 0
    out = json.loads(capsys.readouterr().out)
    assert out['schema'] == util.SCHEMA and out['headline']['z0_utilization'] is None
