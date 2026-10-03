"""Verified-loop training table v0: synthetic state only (no real data)."""
import json

import pytest

from z0int import loop_export as le
from z0int.decision_opportunity import build_decision_opportunity, deterministic_gate

SECRET = 'please refactor the payments module in /home/someone/secret-repo'


def packet(**over):
    p = {'schema': 'z0int.state_packet.v0', 'packet_id': 'p1', 'built_at': '2026-09-30T10:00:00Z',
         'current_claims': [{'key': 'git.branch', 'value': 'feat/secret-name', 'evidence': ['e:1'], 'status': 'observed'}],
         'superseded_claims': [], 'contradictions': [], 'unknowns': [], 'blocking_unknowns': [],
         'coverage': {'git': 'full', 'docs': 'full'}, 'evidence': {},
         'source_revisions': {'resource': {'posture': 'abc', 'factory': 'BURN'}}, 'allowed_transitions': []}
    p.update(over)
    return p


def legacy_record(session, trace, request=SECRET, **pkt):
    """The pre-capture-flag shape (integrate/claude-code-z0-stack era): request text kept, no ``capture``."""
    opp = build_decision_opportunity('/nonexistent', request, packet=packet(**pkt), harness='claude-code', trace_id=trace)
    return {'schema': le.OPP_SCHEMA, 'session_id': session, 'gate': deterministic_gate(opp), 'opportunity': opp}


def opp_record(session, trace, request=SECRET, **pkt):
    """A user prompt as the capture core writes it: content-free capture flags."""
    return dict(legacy_record(session, trace, request, **pkt), capture={'is_harness_message': False})


def harness_record(session, trace, request):
    """A harness-injected prompt as the capture core writes it: flagged at capture, request text not stored."""
    rec = legacy_record(session, trace, request=request)
    rec['opportunity']['intent']['request'] = None
    rec['capture'] = {'is_harness_message': True}
    return rec


def verified(session, trace, state, oracle='test_runner'):
    sig = [] if state == 'unverified' else [{'kind': 'tests_in_turn', 'polarity': 1 if state == 'verified_success' else -1,
                                             'confidence': 'medium', 'label_class': 'deterministic_gold', 'oracle': oracle}]
    return {'schema': le.VERIFIED_SCHEMA, 'session_id': session, 'trace_id': trace, 'verification_state': state,
            'label_class': 'unknown' if state == 'unverified' else 'deterministic_gold', 'label_confidence': None,
            'signals': sig, 'turn': {'started_at': '2026-09-30T10:00:05Z'}, 'verifier': {'id': 'x', 'version': '0.1.0'}}


def write(path, rows):
    path.write_text(''.join(json.dumps(r) + '\n' for r in rows))


@pytest.fixture
def state(tmp_path):
    s = tmp_path / 'state'
    s.mkdir()
    write(s / 'opportunities.jsonl', [
        opp_record('s1', 't1'),
        opp_record('s1', 't2', request='which branch is checked out?',
                   contradictions=[{'key': 'priority', 'contests': 'git.branch'}]),
        harness_record('s2', 't3', request='<task-notification>done</task-notification>'),
        opp_record('s2', 't4', request='is the CI status green on the pull request?'),
    ])
    write(s / 'outcomes.jsonl', [
        {'schema': le.OBSERVED_SCHEMA, 'session_id': 's1', 'trace_id': 't1', 'asked_user': False, 'tool_calls': 4},
        {'schema': le.OBSERVED_SCHEMA, 'session_id': 's1', 'trace_id': 't2', 'asked_user': True, 'asked_via_tool': True},
    ])
    write(s / 'outcomes_verified.jsonl', [
        verified('s1', 't1', 'unverified'),
        verified('s1', 't1', 'verified_success'),  # a later, matured row wins
        verified('s1', 't2', 'verified_failure'),
        verified('s2', 't9', 'contested'),          # no opportunity record
    ])
    return s


def table(state, **kw):
    return le.build_table(state, cohort_fn=lambda sid: 'interactive', **kw)


def test_feature_vocabulary_is_stable_and_versioned():
    assert len(le.FEATURES) == len(set(le.FEATURES))
    assert len(le.FEATURE_SCHEMA_SHA) == 16
    f = le.opportunity_features(opp_record('s', 't'))
    assert list(f) == list(le.FEATURES) and all(isinstance(v, int) for v in f.values())


def test_join_keeps_harness_messages_in_their_own_cohort_and_latest_verified_row_wins(state):
    rows = {r['turn_key']: r for r in table(state)}
    assert len(rows) == 4
    (harness,) = [r for r in rows.values() if r['cohort'] == 'harness']  # t3: flagged at capture, not dropped
    assert harness['features']['cohort=harness'] == 1 and harness['label']['present'] is False
    by = {(r['observed'] or {}).get('action'): r for r in rows.values() if r['cohort'] != 'harness'}
    assert by['ACT']['label']['state'] == 'verified_success' and by['ACT']['label']['y_success'] == 1
    assert by['ASK']['label']['y_success'] == 0 and by['ASK']['observed']['post_decision'] is True
    unlabeled = by[None]
    assert unlabeled['label']['present'] is False and unlabeled['label']['y_success'] is None


def test_legacy_rows_without_capture_flags_are_never_user_rows(tmp_path):
    """Rows written before capture flags (6fee859 dropped the harness ones by reading the request at export):
    the export reads no request text, so an unflagged row is cohort unknown until the capture-side backfill
    (harness_capture.backfill_capture) flags it; it never becomes an interactive or agent training row."""
    s = tmp_path / 'state'
    s.mkdir()
    write(s / 'opportunities.jsonl', [legacy_record('s1', 't1'),
                                      legacy_record('s1', 't2', request='<task-notification>done</task-notification>'),
                                      opp_record('s1', 't3')])
    rows = {r['turn_key']: r for r in le.build_table(s, cohort_fn=lambda sid: 'interactive')}
    by_trace = {t: rows[le._sha({'session': 's1', 'trace': t})] for t in ('t1', 't2', 't3')}
    assert by_trace['t1']['cohort'] == by_trace['t2']['cohort'] == 'unknown'
    assert by_trace['t1']['features']['cohort=unknown'] == 1 and by_trace['t3']['cohort'] == 'interactive'


def test_features_reflect_scope_contradiction_gate_posture(state):
    rows = table(state)
    esc = [r for r in rows if r['gate'] == 'ESCALATE']
    assert len(esc) == 1
    f = esc[0]['features']
    assert f['scope_mode=question'] == 1 and f['family.git.branch'] == 1
    assert f['n_contradictions'] == 1 and f['contradiction_family.git.branch'] == 1
    assert f['legal.ESCALATE'] == 1 and f['legal.ACT'] == 0 and f['gate=ESCALATE'] == 1
    assert f['posture_factory=BURN'] == 1 and f['cohort=interactive'] == 1
    gh = [r for r in rows if r['features']['family.gh']][0]['features']
    assert gh['n_unknowns_blocking'] == 1 and gh['unknown_family.gh'] == 1


def test_unverified_is_missing_not_negative():
    lab = le.label_of({'verification_state': 'unverified', 'signals': []})
    assert lab['y_success'] is None and lab['resolved'] is False
    assert le.label_of({'verification_state': 'contested', 'signals': []})['y_success'] == 0


def test_include_unjoined_adds_feature_less_rows(state):
    rows = table(state, include_unjoined=True)
    extra = [r for r in rows if not r['has_opportunity']]
    assert len(extra) == 1 and extra[0]['features'] is None and extra[0]['label']['state'] == 'contested'


def test_no_private_text_reaches_the_table(state, tmp_path):
    man = le.export(tmp_path / 'out' / 't.jsonl', state=state, include_unjoined=True)
    blob = (tmp_path / 'out' / 't.jsonl').read_text() + (tmp_path / 'out' / 't.manifest.json').read_text()
    for needle in ('payments', 'secret', 'branch is checked', 'task-notification', 's1', 't1', '/home/'):
        assert needle not in blob, needle
    assert man['counts']['with_features'] == 4 and man['counts']['resolved'] == 2
    assert man['feature_schema_sha'] == le.FEATURE_SCHEMA_SHA and man['features'] == list(le.FEATURES)


def test_assert_private_fails_closed():
    with pytest.raises(ValueError):
        le.assert_private([{'turn_key': 'x', 'observed': {'request': 'hi'}}])


def test_cli_dispatch(state, tmp_path, capsys):
    from z0int.outcome_verifier import _main
    assert _main(['export', '--state-dir', str(state), '--out', str(tmp_path / 'x.jsonl')]) == 0
    assert 'with_features=' in capsys.readouterr().out


def test_sweep_counts_are_counts_only(state):
    rows = [{'session_id': 's1', 'trace_id': 't1', 'verification_state': 'verified_success',
             'turn': {'started_at': '2026-09-30T10:00:00Z', 'harness_message': False}},
            {'session_id': 's9', 'trace_id': 'x', 'verification_state': 'unverified',
             'turn': {'started_at': '2026-09-29T10:00:00Z', 'harness_message': False}},
            {'session_id': 's9', 'trace_id': 'y', 'verification_state': 'verified_failure',
             'turn': {'started_at': '2026-09-29T11:00:00Z', 'harness_message': True}}]
    out = le.sweep_counts(rows, state, since_spec='7d', now=0)
    assert out['first_turn_at'] == '2026-09-29T10:00:00Z' and out['sessions'] == 2
    assert out['total']['turns_after_first_opportunity'] == 1  # fixture packets are built 10:00Z
    assert out['total']['with_opportunity_after_first_opportunity'] == 1
    assert {k: v for k, v in out['total'].items() if 'after' not in k} == {'turns': 2, 'verified_success': 1, 'unverified': 1, 'with_opportunity': 1,
                            'with_observed': 1, 'with_opportunity_and_resolved': 1}
    assert set(out['by_day']) == {'2026-09-29', '2026-09-30'}
    assert 's1' not in json.dumps(out)
