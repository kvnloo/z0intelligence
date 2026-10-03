"""Offline shadow slot (C6 red tests 6-8): counterfactual decisions on recorded opportunity records only."""
import json

import pytest

from test_loop_export import SECRET, verified
from test_loop_tables import opp, put
from z0int import harness_capture as hc
from z0int import loop_export as le


def slot():
    from z0int import shadow_slot
    return shadow_slot


def seed(home):
    """Two harnesses, three recorded opportunities, one labelled turn (the base rate's only evidence)."""
    put(home, 'hermes', 'opportunities.jsonl', [opp('hermes', 'hs-1', 't1'), opp('hermes', 'hs-1', 't2')])
    put(home, 'codex', 'opportunities.jsonl', [opp('codex', 'cx-1', 'a1')])
    put(home, 'hermes', 'outcomes_verified.jsonl', [
        dict(verified('hs-1', 't1', 'verified_success'), schema=hc.schema('hermes', 'turn_outcome_verified'),
             harness='hermes', cohort='interactive')])
    return {r['opportunity']['trace']['opportunity_id']
            for h in ('hermes', 'codex') for r in hc._read_jsonl(hc.state_dir(h, home) / 'opportunities.jsonl')}


def files(root):
    return {str(p.relative_to(root)): p.read_bytes() for p in sorted(root.rglob('*')) if p.is_file()}


# ----------------------------------------------------------------------------- 6. decisions from records only
def test_shadow_decisions_come_only_from_recorded_opportunities_and_land_only_in_shadow_decisions(tmp_path):
    ss = slot()
    home = tmp_path / 'z0'
    recorded = seed(home)
    before = files(home)
    rep = ss.replay(home)
    after = files(home)
    changed = sorted(k for k in after if before.get(k) != after[k])
    assert changed == ['state/codex/shadow_decisions.jsonl', 'state/hermes/shadow_decisions.jsonl']
    rows = [r for h in ('hermes', 'codex') for r in hc._read_jsonl(hc.state_dir(h, home) / 'shadow_decisions.jsonl')]
    challengers = {c['id'] for c in ss.default_registry()['challengers']}
    assert {'deterministic_gate', 'always_escalate', 'base_rate', 'routine_shadow'} <= challengers
    assert len(rows) == 3 * len(challengers) == rep['decisions_added']
    assert {r['opportunity_id'] for r in rows} == recorded
    for r in rows:
        assert r['schema'] == hc.schema(r['harness'], 'shadow_decision')
        for k in ('opportunity_id', 'turn_key', 'challenger', 'decision', 'distribution', 'latency_ms', 'status'):
            assert k in r, k
        assert set(r['challenger']) >= {'id', 'sha256'} and len(r['challenger']['sha256']) == 64
        assert r['y'] is None and r['label'] is None
    assert {r['turn_key'] for r in rows} == {hc.turn_key('hermes', 'hs-1', 't1'), hc.turn_key('hermes', 'hs-1', 't2'),
                                             hc.turn_key('codex', 'cx-1', 'a1')}
    champion = [r for r in rows if r['challenger']['id'] == 'deterministic_gate']
    assert {r['decision'] for r in champion} == {'ACT'}  # the gate the capture recorded
    assert all(r['decision'] == 'ESCALATE' for r in rows if r['challenger']['id'] == 'always_escalate')
    le.assert_private(rows)
    blob = b''.join(after[k] for k in changed)
    for needle in (b'hs-1', b'cx-1', SECRET.encode()):
        assert needle not in blob
    # a re-run decides nothing twice
    assert slot().replay(home)['decisions_added'] == 0
    assert files(home) == after


def test_shadow_tables_carry_the_slot_decisions_per_harness_and_cohort(tmp_path):
    home = tmp_path / 'z0'
    seed(home)
    slot().replay(home)
    tables = le.shadow_tables(home)
    # the cohort its training rows have: the verified row's cohort for hermes, unknown for the unlabelled codex turn
    assert sorted(tables) == [('codex', 'unknown'), ('hermes', 'interactive')]
    assert all(len(t.rows) == 4 * (2 if t.harness == 'hermes' else 1) for t in tables.values())


def test_shadow_rows_take_the_cohort_of_their_training_rows(tmp_path):
    """A cron session the capture wrote as interactive but the verifier classified automated: both tables agree."""
    home = tmp_path / 'z0'
    rec = dict(opp('hermes', 'cron_1', 'c1'), cohort='interactive')
    put(home, 'hermes', 'opportunities.jsonl', [rec, dict(opp('hermes', 'cli_1', 'i1'), cohort='interactive')])
    put(home, 'hermes', 'outcomes_verified.jsonl', [
        dict(verified('cron_1', 'c1', 'verified_success'), schema=hc.schema('hermes', 'turn_outcome_verified'),
             harness='hermes', cohort='automated'),
        dict(verified('cli_1', 'i1', 'verified_failure'), schema=hc.schema('hermes', 'turn_outcome_verified'),
             harness='hermes', cohort='interactive')])
    put(home, 'claude-code', 'opportunities.jsonl', [dict(opp('claude-code', 'cc-1', 'p1'),
                                                          schema='z0int.claude_code.opportunity_record.v0')])
    slot().replay(home)
    train = {k: {r['turn_key'] for r in t.rows} for k, t in le.build_tables(home).items()}
    shadow = {k: {r['turn_key'] for r in t.rows} for k, t in le.shadow_tables(home).items()}
    assert sorted(shadow) == sorted(train) == [('claude-code', 'unknown'), ('hermes', 'automated'),
                                               ('hermes', 'interactive')]
    assert shadow == train


def test_two_sessions_whose_opportunity_ids_collide_each_get_their_decisions(tmp_path):
    """Grok's C1 turn id hashes a payload session the CLI never sends: equal prompt + time collide across sessions."""
    home = tmp_path / 'z0'
    recs = [opp('grok', sid, 'same-turn') for sid in ('grok-a', 'grok-b')]
    for r in recs:
        r['opportunity']['trace']['opportunity_id'] = 'collided-opportunity'
    put(home, 'grok', 'opportunities.jsonl', recs)
    put(home, 'grok', 'outcomes_verified.jsonl', [
        dict(verified(sid, 'same-turn', 'verified_success'), schema=hc.schema('grok', 'turn_outcome_verified'),
             harness='grok', cohort='interactive') for sid in ('grok-a', 'grok-b')])
    rep = slot().replay(home)
    n = len(slot().default_registry()['challengers'])
    assert rep['decisions_added'] == 2 * n
    train = {k: sorted(r['turn_key'] for r in t.rows) for k, t in le.build_tables(home).items()}
    shadow = {k: sorted({r['turn_key'] for r in t.rows}) for k, t in le.shadow_tables(home).items()}
    assert shadow == train and len(train[('grok', 'interactive')]) == 2
    assert all(len(t.rows) == 2 * n for t in le.shadow_tables(home).values())
    assert slot().replay(home)['decisions_added'] == 0  # still idempotent


def test_shadow_cohorts_follow_build_table_in_its_edge_cases(tmp_path):
    """One cohort rule for both tables: a claude-code turn with no verified row of its own takes the transcript's
    cohort (not another turn's), and a re-verified turn takes its latest verified row's cohort."""
    home, projects = tmp_path / 'z0', tmp_path / 'projects'
    projects.mkdir()
    cc = lambda s, t: dict(opp('claude-code', s, t), schema='z0int.claude_code.opportunity_record.v0')  # noqa: E731
    put(home, 'claude-code', 'opportunities.jsonl', [cc('cc-1', 'p1'), cc('cc-1', 'p2')])
    put(home, 'claude-code', 'outcomes_verified.jsonl', [  # p1 only; p2 has no verified row yet
        dict(verified('cc-1', 'p1', 'verified_success'), schema='z0int.claude_code.turn_outcome_verified.v0',
             cohort='agent')])
    put(home, 'hermes', 'opportunities.jsonl', [opp('hermes', 'hs-1', 't1'), opp('hermes', 'hs-1', 't2')])
    vr = lambda t, **kw: dict(verified('hs-1', t, 'verified_success'),  # noqa: E731
                              schema=hc.schema('hermes', 'turn_outcome_verified'), harness='hermes', **kw)
    put(home, 'hermes', 'outcomes_verified.jsonl', [vr('t1', cohort='automated'), vr('t2', cohort='interactive'),
                                                    vr('t1')])  # t1 re-verified by a row without a cohort
    slot().replay(home, projects=projects)
    train = {k: sorted(r['turn_key'] for r in t.rows) for k, t in le.build_tables(home, projects=projects).items()}
    shadow = {k: sorted({r['turn_key'] for r in t.rows}) for k, t in le.shadow_tables(home).items()}
    assert shadow == train


# ----------------------------------------------------------------------------- 7. fail-open, no retry storm
class FakeBackend:
    def __init__(self, calls, ready):
        self.calls, self.ready = calls, ready

    def health(self, *, load=False):
        self.calls['health'] += 1
        from z0int.backends.base import BackendHealth
        return BackendHealth(id='fake', configured=True, ready=self.ready, detail='not served')

    def evaluate(self, request):
        self.calls['evaluate'] += 1
        from z0int.backends.base import DecisionAnswer, DecisionResult
        opts = [o.id for o in request.questions[0].options]
        probs = {o: 1.0 / len(opts) for o in opts}
        return DecisionResult(backend='fake', model='m', revision='r', latency_ms=1.0,
                              answers=(DecisionAnswer(question_id=request.questions[0].id, type='choice',
                                                      probabilities=probs, value=opts[0]),))


def registry_with_backend(ss, enabled):
    reg = ss.default_registry()
    reg['challengers'].append(ss.backend_challenger('fake', enabled=enabled))
    return reg


def test_unserved_backend_is_counted_once_per_tick_and_never_retried(tmp_path):
    ss = slot()
    home = tmp_path / 'z0'
    seed(home)
    calls = {'factory': 0, 'health': 0, 'evaluate': 0}

    def factory(name):
        calls['factory'] += 1
        return FakeBackend(calls, ready=False)
    for tick in (1, 2):
        rep = ss.replay(home, registry_with_backend(ss, True), backend_factory=factory)
        assert rep['backend_unavailable'] == {'fake': 1}
        assert calls == {'factory': tick, 'health': tick, 'evaluate': 0}
    rows = [r for h in ('hermes', 'codex') for r in hc._read_jsonl(hc.state_dir(h, home) / 'shadow_decisions.jsonl')]
    assert not [r for r in rows if r['challenger']['id'] == 'backend:fake']


def test_disabled_backend_is_never_built_or_invoked(tmp_path):
    ss = slot()
    home = tmp_path / 'z0'
    seed(home)
    calls = {'factory': 0, 'health': 0, 'evaluate': 0}

    def factory(name):
        calls['factory'] += 1
        return FakeBackend(calls, ready=True)
    rep = ss.replay(home, registry_with_backend(ss, False), backend_factory=factory)
    assert calls == {'factory': 0, 'health': 0, 'evaluate': 0}
    assert rep['backend_unavailable'] == {}
    assert all(c.get('enabled') is not True for c in ss.default_registry()['challengers'] if c['kind'] == 'backend')


def test_served_enabled_backend_answers_over_the_legal_actions(tmp_path):
    ss = slot()
    home = tmp_path / 'z0'
    seed(home)
    calls = {'factory': 0, 'health': 0, 'evaluate': 0}
    rep = ss.replay(home, registry_with_backend(ss, True), backend_factory=lambda n: FakeBackend(calls, ready=True))
    assert calls['evaluate'] == 3 and rep['backend_unavailable'] == {}
    rows = [r for h in ('hermes', 'codex') for r in hc._read_jsonl(hc.state_dir(h, home) / 'shadow_decisions.jsonl')
            if r['challenger']['id'] == 'backend:fake']
    assert len(rows) == 3 and all(abs(sum(r['distribution'].values()) - 1) < 1e-6 for r in rows)


# ----------------------------------------------------------------------------- 8. simple controls always present
@pytest.mark.parametrize('control', ['deterministic_gate', 'always_escalate', 'base_rate'])
def test_removing_a_simple_control_fails_registry_validation(control):
    ss = slot()
    reg = ss.default_registry()
    ss.validate_registry(reg)  # the default passes
    reg['challengers'] = [c for c in reg['challengers'] if c['id'] != control]
    with pytest.raises(ValueError, match=control):
        ss.validate_registry(reg)


def test_registry_file_is_hash_pinned(tmp_path):
    ss = slot()
    home = tmp_path / 'z0'
    seed(home)
    reg = ss.default_registry()
    reg['challengers'][0] = dict(reg['challengers'][0], sha256='0' * 64)
    (home / 'loop').mkdir(parents=True)
    (home / 'loop' / 'challengers.json').write_text(json.dumps(reg))
    with pytest.raises(ValueError, match='sha256'):
        ss.load_registry(home)
    reg = ss.default_registry()
    (home / 'loop' / 'challengers.json').write_text(json.dumps(reg))
    removed = dict(reg, challengers=[c for c in reg['challengers'] if c['id'] != 'base_rate'])
    (home / 'loop' / 'challengers.json').write_text(json.dumps(removed))
    with pytest.raises(ValueError, match='base_rate'):
        ss.load_registry(home)


def test_learned_gate_loads_only_when_registered_by_hash(tmp_path):
    ss = slot()
    home = tmp_path / 'z0'
    seed(home)
    art = home / 'loop' / 'artifacts' / 'gate.json'
    art.parent.mkdir(parents=True)
    art.write_text(json.dumps({'feature_names': ['legal.ACT'], 'mu': [0.0], 'sd': [1.0], 'w': [5.0], 'b': 0.0,
                               'tau': 0.5}))
    good = ss.learned_gate_challenger('el-gate-1', art)
    reg = ss.default_registry()
    reg['challengers'] += [good, dict(ss.learned_gate_challenger('el-gate-2', art), sha256='f' * 64)]
    rep = ss.replay(home, reg)
    assert rep['challengers']['el-gate-2'] == 'hash_mismatch'
    rows = [r for h in ('hermes', 'codex') for r in hc._read_jsonl(hc.state_dir(h, home) / 'shadow_decisions.jsonl')]
    assert {r['decision'] for r in rows if r['challenger']['id'] == 'el-gate-1'} == {'ACT'}
    assert not [r for r in rows if r['challenger']['id'] == 'el-gate-2']
