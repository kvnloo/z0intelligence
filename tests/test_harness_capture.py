"""Cross-harness capture core (z0int#62): one record family, work items, F-case failure rows, spool, freeze.

Synthetic payloads only; every test writes under its own Z0INT_HOME.
"""
import inspect
import json
import os
import subprocess
import sys
import threading
import time

import pytest

from z0int import harness_capture as hc
from z0int import loop_export as le
from z0int.decision_opportunity import deterministic_gate, with_authority_grant
from z0int.harness_id import turn_key

SEVEN = ('claude-code', 'codex', 'grok', 'hermes', 'omp', 'omo', 'dsh')


@pytest.fixture
def home(monkeypatch, tmp_path):
    h = tmp_path / 'z0'
    monkeypatch.setenv('Z0INT_HOME', str(h))
    for name in ('Z0INT_CAPTURE', 'Z0INT_CAPTURE_PRIVACY', 'GROK_HOOK_EVENT', 'GROK_SESSION_ID'):
        monkeypatch.delenv(name, raising=False)
    return h


@pytest.fixture
def plain(tmp_path):
    d = tmp_path / 'plain'
    d.mkdir()
    return d


def git(repo, *args):
    subprocess.run(['git', '-C', str(repo), '-c', 'user.name=t', '-c', 'user.email=t@t', *args], check=True,
                   capture_output=True)


def git_repo(path, branch='main'):
    path.mkdir(parents=True)
    subprocess.run(['git', 'init', '-q', '-b', branch, str(path)], check=True)
    (path / 'f').write_text('x')
    git(path, 'add', '-A')
    git(path, 'commit', '-qm', 'init')
    return path


def rows(home, harness, name):
    p = home / 'state' / harness / name
    return [json.loads(line) for line in p.read_text().splitlines()] if p.exists() else []


def payload(h, n=1, *, cwd, prompt='summarise the release notes', session=None, **extra):
    s = session or f'{h}-s'
    if h == 'claude-code':
        p = {'hook_event_name': 'UserPromptSubmit', 'session_id': s, 'prompt_id': f'p{n}'}
    elif h == 'codex':
        p = {'hook_event_name': 'UserPromptSubmit', 'session_id': s, 'turn_id': f't{n}', 'model': 'm-1'}
    elif h == 'grok':
        p = {'hook_event_name': 'UserPromptSubmit', 'hookEventName': 'user_prompt_submit', 'session_id': s,
             'sessionId': s, 'modelId': 'm-1', 'timestamp': f'2026-10-03T00:00:0{n}Z'}
    else:  # normalized events from the in-process bridges (hermes, omp, omo, dsh)
        p = {'session_id': s, 'turn_id': f't{n}', 'model': 'm-1'}
    p.update(prompt=prompt, cwd=None if cwd is None else str(cwd), **extra)
    return p


def capture(h, p, **kw):
    ctx = hc.begin_turn(h, p)
    return ctx, hc.record_opportunity(h, p, ctx, **kw)


# ----------------------------------------------------------------------------- record family
def test_record_family_for_all_seven_harnesses(home, plain):
    for h in SEVEN:
        ctx, rec = capture(h, payload(h, cwd=plain))
        (row,) = rows(home, h, 'opportunities.jsonl')
        assert row['schema'] == hc.schema(h, 'opportunity_record') == f"z0int.{h.replace('-', '_')}.opportunity_record.v0"
        assert row['harness'] == h and row['turn_key'] == turn_key(h, f'{h}-s', ctx['trace_id'])
        assert row['work_item_id'] and row['attempt_id'] == 0
        assert isinstance(row['recorded_at'], str) and row['recorded_at'].endswith('Z')
        for field in ('model_id', 'policy_revision'):
            assert field in row and row[field]  # a value or the explicit string "unknown"
        assert row['policy_revision'] == 'unknown'
        assert row['model_id'] == ('unknown' if h == 'claude-code' else 'm-1')
        assert row['opportunity']['provenance']['built_at'] is None  # a non-repo turn still has recorded_at
        assert row['privacy_class'] == 'content_free' and row['cohort'] == 'interactive'
        assert hc.parse_schema(row['schema']) == (h, 'opportunity_record', 'v0')


def test_work_item_survives_a_retry_and_attempt_increments(home, plain):
    first, _ = capture('codex', payload('codex', 1, cwd=plain, prompt='fix the flaky test'))
    retry = payload('codex', 2, cwd=plain, prompt='fix the flaky test', retry=True)
    # the retry arrives in a fresh hook process: the work item comes from persisted state, not memory
    code = ('import json, sys; from z0int import harness_capture as hc; '
            'print(json.dumps(hc.begin_turn("codex", json.loads(sys.stdin.read()))))')
    second = json.loads(subprocess.run([sys.executable, '-c', code], input=json.dumps(retry), capture_output=True,
                                       text=True, check=True, env=dict(os.environ)).stdout)
    assert second['work_item_id'] == first['work_item_id'] and second['attempt_id'] == first['attempt_id'] + 1
    assert second['turn_key'] != first['turn_key']
    third = hc.begin_turn('codex', payload('codex', 3, cwd=plain, prompt='now update the docs'))
    assert third['work_item_id'] != first['work_item_id'] and third['attempt_id'] == 0
    other = hc.begin_turn('codex', payload('codex', 1, cwd=plain, session='other-session'))
    assert other['work_item_id'] != first['work_item_id']
    rec = hc.record_opportunity('codex', retry, second)
    assert rec['attempt_id'] == 1 and rec['opportunity']['trace']['attempt'] == 1


# ----------------------------------------------------------------------------- explicit failure rows
def test_unknown_harness_or_schema_version_is_a_failure_row_never_a_silent_drop(home, plain):
    assert hc.begin_turn('zed', payload('codex', cwd=plain)) is None
    (fail,) = rows(home, '_unsupported', 'failures.jsonl')
    assert fail['kind'] == 'unsupported_harness' and fail['detail']['harness'] == 'zed'
    assert not (home / 'state' / 'zed').exists()
    ctx, rec = capture('codex', payload('codex', cwd=plain))
    future = dict(rec, schema='z0int.codex.opportunity_record.v9', turn_key='k-future')
    assert hc.append('codex', 'opportunity_record', future) is None
    fails = [f for f in rows(home, 'codex', 'failures.jsonl') if f['kind'] == 'unsupported_schema']
    assert len(fails) == 1 and fails[0]['detail']['schema_version'] == 'v9'
    assert len(rows(home, 'codex', 'opportunities.jsonl')) == 1


def test_f_case_failure_rows(home, plain, tmp_path):
    repo = git_repo(tmp_path / 'repo')
    p = payload('codex', cwd=repo, prompt='what branch am I on?')
    ctx, rec = capture('codex', p)
    assert rec['opportunity']['invalidation']['source_revisions']['git']['head']
    # duplicate_event: the same turn_key + kind is a counted no-op
    assert hc.record_opportunity('codex', p, ctx) is None
    assert len(rows(home, 'codex', 'opportunities.jsonl')) == 1
    # stale_evidence: HEAD moved between the opportunity and the outcome
    (repo / 'g').write_text('y')
    git(repo, 'add', '-A')
    git(repo, 'commit', '-qm', 'second')
    octx = hc.outcome_context('codex', {'session_id': p['session_id'], 'turn_id': p['turn_id']})
    out = hc.record_outcome('codex', octx, {'asked_user': False, 'tool_calls': None, 'assistant_messages': None},
                            seen_revisions=hc.observed_revisions(repo))
    assert out['turn_key'] == ctx['turn_key']
    assert hc.record_outcome('codex', octx, {'asked_user': False}) is None  # duplicate outcome
    kinds = {}
    for f in rows(home, 'codex', 'failures.jsonl'):
        kinds.setdefault(f['kind'], []).append(f)
    assert kinds['stale_evidence'][0]['detail'] == {'sources': ['git']}
    assert kinds['missing_verifier'][0]['turn_key'] == ctx['turn_key']  # no verifier registered for codex yet
    assert set(kinds['partial_measurement'][0]['detail']['missing']) == {'assistant_messages', 'tool_calls'}
    assert sorted(f['detail']['record'] for f in kinds['duplicate_event']) == ['opportunity_record', 'turn_outcome']
    # uncertain_execution: a turn that failed mid-way has an unknown effect (mutation-outcome/v0 semantics)
    g = payload('grok', cwd=plain)
    gctx, _ = capture('grok', g)
    hc.record_outcome('grok', hc.outcome_context('grok', {'session_id': g['session_id']}),
                      {'asked_user': False, 'tool_calls': 2, 'assistant_messages': 1}, ended='failed')
    (unc,) = [f for f in rows(home, 'grok', 'failures.jsonl') if f['kind'] == 'uncertain_execution']
    mo = unc['detail']['mutation_outcome']
    assert mo['receiptKind'] == 'mutation-outcome/v0' and mo['effect'] == 'unknown'
    assert mo['retryDisposition'] == 'observe' and mo['verification'] == 'unverified'
    assert unc['turn_key'] == gctx['turn_key']
    # misattribution is written through the same writer
    hc.record_failure('codex', 'misattribution', ctx=ctx, detail={'explicit': 'codex', 'detected': 'grok'})
    assert any(f['kind'] == 'misattribution' for f in rows(home, 'codex', 'failures.jsonl'))
    assert set(hc.FAILURE_KINDS) >= {'unsupported_harness', 'unsupported_schema', 'stale_evidence', 'missing_verifier',
                                     'partial_measurement', 'uncertain_execution', 'duplicate_event', 'misattribution'}


def test_no_stale_evidence_when_the_revision_is_unchanged(home, tmp_path):
    repo = git_repo(tmp_path / 'repo')
    p = payload('codex', cwd=repo, prompt='what branch am I on?')
    ctx, _ = capture('codex', p)
    hc.record_outcome('codex', hc.outcome_context('codex', p), {'asked_user': False, 'tool_calls': 1,
                                                               'assistant_messages': 1},
                      seen_revisions=hc.observed_revisions(repo))
    assert not [f for f in rows(home, 'codex', 'failures.jsonl') if f['kind'] == 'stale_evidence']


# ----------------------------------------------------------------------------- spool (P-2 / P-3)
def test_spool_drops_are_counted_persisted_and_visible_to_a_fresh_process(home):
    gate = threading.Event()
    spool = hc.Spool('codex', maxsize=2, write=lambda kind, row: gate.wait(10))
    accepted = [spool.put('turn_outcome', {'turn_key': f'k{i}'}) for i in range(10)]
    assert spool.dropped == accepted.count(False) >= 7
    code = 'from z0int.harness_capture import drop_count; print(drop_count("codex"))'
    fresh = subprocess.run([sys.executable, '-c', code], capture_output=True, text=True, check=True,
                           env=dict(os.environ)).stdout.strip()
    assert int(fresh) == spool.dropped
    drops = rows(home, 'codex', 'drops.jsonl')
    assert all(r['reason'] == 'queue_full' and r['kind'] == 'turn_outcome' for r in drops)
    gate.set()
    spool.close()


def test_spool_close_is_bounded_with_a_full_queue_and_nothing_is_written_after(home):
    written = []

    def slow(kind, row):
        time.sleep(0.2)
        written.append(row['turn_key'])

    spool = hc.Spool('codex', maxsize=50, write=slow, close_timeout=2.0)
    for i in range(50):
        spool.put('turn_outcome', {'turn_key': f'k{i}'})
    t0 = time.monotonic()
    spool.close()
    assert time.monotonic() - t0 <= 2.1
    n = len(written)
    time.sleep(0.6)
    assert len(written) == n and n < 50
    assert hc.drop_count('codex') == 50 - n  # what close() could not write is counted, not lost silently
    assert spool.put('turn_outcome', {'turn_key': 'late'}) is False and len(written) == n


# ----------------------------------------------------------------------------- P-7: task cwd, never the process cwd
def test_opportunity_uses_the_payload_cwd_never_the_process_cwd(home, tmp_path, monkeypatch):
    proc_repo = git_repo(tmp_path / 'proc', branch='proc-branch')
    task_repo = git_repo(tmp_path / 'task', branch='task-branch')
    monkeypatch.chdir(proc_repo)
    _, rec = capture('codex', payload('codex', 1, cwd=task_repo, prompt='what branch am I on?'))
    claims = {c['key']: c['value'] for c in rec['opportunity']['state']['claims']}
    assert claims.get('git.branch') == 'task-branch'
    assert 'proc-branch' not in json.dumps(rec)
    # no cwd in the payload: the process repo is NOT used; every git fact is unknown, so never ACT
    _, rec = capture('codex', payload('codex', 2, cwd=None, prompt='what branch am I on?'))
    assert 'proc-branch' not in json.dumps(rec) and not rec['opportunity']['state']['claims']
    assert rec['gate'] != 'ACT' and rec['gate'] == deterministic_gate(rec['opportunity'])
    assert any(u['key'] == 'git' and u['blocking'] for u in rec['opportunity']['state']['unknowns'])
    _, rec = capture('codex', payload('codex', 3, cwd=None, prompt='summarise the release notes'))
    assert rec['gate'] != 'ACT'


def test_every_git_fact_unknown_never_acts(home, tmp_path):
    broken = tmp_path / 'broken'
    broken.mkdir()
    (broken / '.git').write_text('gitdir: /nonexistent/never\n')  # a repo marker whose state cannot be read
    for i, prompt in enumerate(('summarise the release notes', 'what branch am I on?')):
        _, rec = capture('codex', payload('codex', i, cwd=broken, prompt=prompt))
        assert rec['gate'] != 'ACT' and not any(a['legal'] for a in rec['opportunity']['action_space']
                                                if a['kind'] == 'ACT')


def test_every_git_fact_unknown_in_a_built_packet_never_acts(home, tmp_path, monkeypatch):
    from z0int import state_packet as sp
    repo = git_repo(tmp_path / 'repo')
    unreadable = {'schema': 'z0int.state_packet.v0', 'packet_id': 'p', 'built_at': '2026-10-03T00:00:00Z',
                  'current_claims': [], 'superseded_claims': [], 'contradictions': [], 'blocking_unknowns': [],
                  'unknowns': [{'key': 'git.head', 'reason': 'not a readable git repository'}],
                  'coverage': {'git': 'none'}, 'evidence': {}, 'source_revisions': {}, 'allowed_transitions': []}
    monkeypatch.setattr(sp, 'build_state_packet', lambda *a, **k: dict(unreadable))
    _, rec = capture('codex', payload('codex', cwd=repo, prompt='summarise the release notes'))
    assert rec['gate'] != 'ACT'


# ----------------------------------------------------------------------------- capture-time flags + export
HARNESS_TEXT = '<task-notification>agent a1 finished</task-notification>'


def test_harness_injected_prompt_is_flagged_at_capture_and_lands_in_cohort_harness(home, plain, tmp_path):
    ctx, rec = capture('claude-code', payload('claude-code', cwd=plain, prompt=HARNESS_TEXT))
    assert rec['capture']['is_harness_message'] is True and rec['cohort'] == 'harness'
    assert rec['opportunity']['intent'].get('request') is None and 'task-notification' not in json.dumps(rec)
    _, user = capture('claude-code', payload('claude-code', 2, cwd=plain, prompt='what changed today?'))
    assert user['capture']['is_harness_message'] is False
    table = le.build_table(home / 'state' / 'claude-code', cohort_fn=lambda sid: 'interactive')
    by_cohort = sorted(r['cohort'] for r in table)
    assert by_cohort == ['harness', 'interactive']
    assert [r for r in table if r['cohort'] == 'harness'][0]['features']['cohort=harness'] == 1


def test_no_export_time_code_path_reads_the_request(tmp_path):
    state = tmp_path / 'state'
    state.mkdir()
    from z0int.decision_opportunity import build_decision_opportunity
    opp = build_decision_opportunity('.', HARNESS_TEXT, packet={}, harness='claude-code', trace_id='t1')
    # the request says "harness message" but the capture flag says otherwise: export follows the flag
    rec = {'schema': le.OPP_SCHEMA, 'session_id': 's', 'gate': deterministic_gate(opp), 'opportunity': opp,
           'capture': {'is_harness_message': False}}
    (state / 'opportunities.jsonl').write_text(json.dumps(rec) + '\n')
    (row,) = le.build_table(state, cohort_fn=lambda sid: 'interactive')
    assert row['cohort'] == 'interactive'
    src = inspect.getsource(le)
    assert 'is_harness_message(' not in src and "get('request')" not in src


def test_capture_flags_are_content_free():
    flags = hc.capture_flags('  <agent-message from="a1">[Subagent hand-back] done')
    assert flags['is_harness_message'] is True
    assert all(isinstance(v, (bool, int, str)) for v in flags.values())
    assert 'hand-back' not in json.dumps(hc.capture_flags('please refactor the hand-back parser'))


# ----------------------------------------------------------------------------- #62 freeze + authority
def test_freeze_reads_every_harness_with_one_reader_and_is_reproducible(home, plain):
    for h in SEVEN:
        p = payload(h, cwd=plain)
        capture(h, p)
        hc.record_outcome(h, hc.outcome_context(h, p), {'asked_user': False, 'tool_calls': 1, 'assistant_messages': 1})
    first = hc.freeze()
    kinds = sorted(hc.parse_schema(r['schema'])[1] for r in first['rows'])
    assert kinds.count('opportunity_record') == kinds.count('turn_outcome') == 7  # + the F-case failure rows
    assert first['manifest']['rows'] == len(first['rows'])
    assert {r['harness'] for r in first['rows']} == set(SEVEN)
    assert hc.freeze()['manifest']['bundle_sha256'] == first['manifest']['bundle_sha256']
    le.assert_private(first['rows'])
    # an eighth harness id with the same fields is read without any reader change
    src, zed = home / 'state' / 'codex', home / 'state' / 'zed'
    zed.mkdir()
    for name in ('opportunities.jsonl', 'outcomes.jsonl'):
        out = []
        for line in (src / name).read_text().splitlines():
            r = json.loads(line)
            r.update(schema=r['schema'].replace('z0int.codex.', 'z0int.zed.'), harness='zed')
            out.append(json.dumps(r) + '\n')
        (zed / name).write_text(''.join(out))
    third = hc.freeze()
    assert {r['harness'] for r in third['rows']} == set(SEVEN) | {'zed'}
    assert third['manifest']['rows'] == first['manifest']['rows'] + 2
    assert third['manifest']['bundle_sha256'] != first['manifest']['bundle_sha256']
    reader = inspect.getsource(hc.freeze)
    assert not any(h in reader for h in SEVEN)


def test_confidence_never_grants_act(home, plain, tmp_path):
    noisy = {'confidence': 0.99, 'backend': {'confidence': 1.0, 'decision': 'ACT'}}
    _, rec = capture('codex', payload('codex', 1, cwd=None, prompt='summarise the release notes', **noisy))
    assert rec['gate'] == deterministic_gate(rec['opportunity']) != 'ACT'
    assert 'confidence' not in json.dumps(rec)
    repo = git_repo(tmp_path / 'repo')
    _, granted = capture('codex', payload('codex', 2, cwd=repo, prompt='what branch am I on?', **noisy))
    assert granted['gate'] == deterministic_gate(granted['opportunity']) == 'ACT'  # granted by the gate alone
    opp = rec['opportunity']
    loud = {**opp, 'confidence': 1.0, 'backend': {'confidence': 0.99},
            'action_space': [dict(a, confidence=1.0, legal=True) for a in opp['action_space']]}
    a = with_authority_grant(opp, granted_by_user='owner', effect='write')
    b = with_authority_grant(loud, granted_by_user='owner', effect='write')
    assert a['action_space'] == b['action_space'] and a['semantic_id'] == b['semantic_id']


def test_request_text_is_stored_only_with_request_opt_in(home, plain, monkeypatch):
    secret = 'refactor the payments module in /home/someone/secret-repo'
    _, rec = capture('codex', payload('codex', 1, cwd=plain, prompt=secret))
    assert 'payments' not in json.dumps(rec) and rec['privacy_class'] == 'content_free'
    monkeypatch.setenv('Z0INT_CAPTURE_PRIVACY', 'request_opt_in')
    _, rec = capture('codex', payload('codex', 2, cwd=plain, prompt=secret))
    assert rec['opportunity']['intent']['request'] == secret and rec['privacy_class'] == 'request_opt_in'
    assert 'payments' not in json.dumps(rows(home, 'codex', 'failures.jsonl'))
