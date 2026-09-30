import json

import pytest

from z0int import decision_opportunity as do
from z0int.effect_inference import infer_effects


def pkt(**facts):
    return {'current_claims': [{'key': k.replace('_', '.', 1), 'value': v} for k, v in facts.items()]}


@pytest.mark.parametrize('text,cls', [
    ('what does this function return?', 'read'),
    ('show me the last commit', 'read'),
    ('how should I deploy this?', 'read'),                  # question about a privileged action
    ('add logging to the fetcher', 'write'),
    ("don't push, just fix the typo", 'write'),              # negation stays in its clause
    ('commit it', 'write'),
    ('git push origin feat/x', 'privileged'),
    ('please open a pull request', 'privileged'),
    ('can you deploy this to prod?', 'privileged'),          # "can you ...?" is a request, not a question
    ('ship it', 'privileged'),                               # ambiguous write/privileged -> privileged
    ('we should push harder on caching - thoughts?', 'read'),  # idiom, not git push
])
def test_effect_class(text, cls):
    assert infer_effects(text)['effect_class'] == cls


def test_evidence_names_rule_and_phrase():
    out = infer_effects('fix the parser and git push')
    applied = {(e['rule'], e['effect']) for e in out['evidence'] if e['applied']}
    assert ('git_push', 'privileged') in applied and ('edit', 'write') in applied
    assert out['effects'] == ['read', 'write', 'privileged']


def test_commit_on_default_branch_is_privileged_by_packet_fact():
    feat = infer_effects('commit this', pkt(git_branch='feat/x', git_default_branch='origin/main'))
    main = infer_effects('commit this', pkt(git_branch='main', git_default_branch='origin/main'))
    assert feat['effect_class'] == 'write' and main['effect_class'] == 'privileged'
    assert any(e.get('fact', '').startswith('git.branch=main') for e in main['evidence'])


def test_user_grant_phrases_are_recorded_not_applied():
    out = infer_effects('merge it into main, you have my permission')
    assert out['effect_class'] == 'privileged' and out['user_grant_phrases']


def test_gate_asks_on_privileged_and_acts_on_write_with_session_authority():
    p = {'current_claims': []}
    def gate(text, grants):
        opp = do.build_decision_opportunity('/x', text, effects=infer_effects(text)['effects'], packet=p, scoped=False,
                                            harness_grants=grants)
        return do.deterministic_gate(opp)
    assert gate('push this branch', ('read', 'write')) == 'ASK'
    assert gate('fix the typo', ('read', 'write')) == 'ACT'
    assert gate('fix the typo', ('read',)) == 'ASK'
    assert gate('what is this?', ('read',)) == 'ACT'


def test_privileged_is_never_standing_authority():
    with pytest.raises(ValueError):
        do.build_decision_opportunity('/x', 'q', packet={'current_claims': []}, harness_grants=('read', 'privileged'))
    opp = do.build_decision_opportunity('/x', 'q', packet={'current_claims': []}, harness_grants=('write',))
    assert opp['authority'] == {'source': 'harness-standing', 'grants': ['read', 'write'], 'fingerprint': None}


def test_slm_is_shadow_only(monkeypatch):
    from z0int import effect_inference as ei
    monkeypatch.setattr(ei, 'slm_label', lambda text: {'class': 'read', 'ok': True})
    out = infer_effects('push this branch', slm=True)
    assert out['effect_class'] == 'privileged' and out['slm'] == {'class': 'read', 'ok': True, 'agrees': False}


def test_decisions_report_rescores_recorded_state(tmp_path):
    from z0int import claude_code_launch as L
    base = tmp_path / 'state' / 'claude-code'
    base.mkdir(parents=True)
    def rec(tid, request):
        opp = do.build_decision_opportunity('/x', request, packet={'current_claims': []}, trace_id=tid, scoped=False)
        return {'gate': do.deterministic_gate(opp), 'opportunity': opp}
    (base / 'opportunities.jsonl').write_text('\n'.join(json.dumps(r) for r in [
        rec('a', 'commit and push the feature branches'), rec('b', 'what is this?'), rec('c', 'fix the typo')]) + '\n')
    (base / 'outcomes.jsonl').write_text('\n'.join(json.dumps(r) for r in [
        {'trace_id': 'a', 'asked_user': True}, {'trace_id': 'b', 'asked_user': False}, {'trace_id': 'c', 'asked_user': False}]) + '\n')
    rep = L.decisions_report(root=tmp_path)
    assert rep['table'] == {'ACT->answered': 2, 'ACT->asked': 1}
    assert rep['rescore_inferred_effects']['session_read_write'] == {'ACT->answered': 2, 'ASK->asked': 1}
    assert rep['rescore_inferred_effects']['default_read'] == {'ACT->answered': 1, 'ASK->answered': 1, 'ASK->asked': 1}
