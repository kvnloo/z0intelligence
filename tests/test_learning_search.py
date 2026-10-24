import copy
import hashlib
import json
import sqlite3

import pytest

from z0int.cognition.learning_search import (
    DEFAULT_STRATEGIES, SearchConfig, emit_findings, initialize, search_tick,
    wilson_interval,
)


@pytest.fixture
def db():
    connection = sqlite3.connect(':memory:')
    connection.row_factory = sqlite3.Row
    yield connection
    connection.close()


def observation(arm='rules', *, family='omp.tool', strategy='history-1', correct=50, n=100):
    return {'family': family, 'strategy': strategy, 'arm': arm,
            'correct': correct, 'n': n, 'total': n}


def tick(db, number, rows, **kwargs):
    return search_tick(db, tick_id=str(number), now=1_792_800_000 + number,
                       observations=rows, **kwargs)


def trial_bodies(db):
    return [json.loads(r[0]) for r in db.execute('SELECT body FROM learning_search_trials ORDER BY rowid')]


def test_every_arm_must_be_flat_not_just_family_best(db):
    # Best arm is unchanged. The weaker arm is still learning, so no switch.
    for i, correct in enumerate((10, 20, 30), 1):
        report = tick(db, i, [observation('best', correct=90), observation('growing', correct=correct)])
        assert report['families']['omp.tool']['current_strategy'] == 'history-1'
    for i in (4, 5):
        report = tick(db, i, [observation('best', correct=90), observation('growing', correct=30)])
    assert report['families']['omp.tool']['current_strategy'] == 'history-3'
    trigger = next(e for e in report['events'] if e['type'] == 'plateau')
    assert set(trigger['arms']) == {'best', 'growing'}
    assert trigger['plateau_ticks'] == 3
    assert trial_bodies(db)[0]['status'] == 'plateau'
    selected = trial_bodies(db)[1]
    assert selected['status'] == 'selected'
    assert 'started_at' not in selected


def test_families_progress_independently(db):
    for i in (1, 2, 3):
        report = tick(db, i, [observation(), observation(family='omp.skill', correct=i*10)])
    assert report['families']['omp.tool']['current_strategy'] == 'history-3'
    assert report['families']['omp.skill']['current_strategy'] == 'history-1'


def test_missing_arm_or_insufficient_evaluations_is_not_a_plateau(db):
    tick(db, 1, [observation('one'), observation('two')])
    tick(db, 2, [observation('one')])
    report = tick(db, 3, [observation('one'), observation('two')])
    assert not report['events']
    assert report['families']['omp.tool']['current_strategy'] == 'history-1'
    for i in (4, 5, 6):
        report = tick(db, i, [observation('one'), observation('two', n=1, correct=1)],
                      config=SearchConfig(min_observations=5))
    assert not report['events']

def test_abstention_only_arm_still_advances_after_real_evaluations(db):
    abstentions = {**observation(n=0, correct=0), 'total': 100}
    for i in (1, 2, 3):
        report = tick(db, i, [abstentions])
    assert report['families']['omp.tool']['current_strategy'] == 'history-3'
    assert trial_bodies(db)[0]['status'] == 'plateau'



def test_tick_and_trials_never_repeat_and_pending_selection_does_not_run(db):
    strategies = DEFAULT_STRATEGIES[:2]
    for i in (1, 2, 3):
        first = tick(db, i, [observation()], strategies=strategies)
    assert tick(db, 3, [observation()], strategies=strategies) == first
    # No measurements from the newly selected config, so do not claim a trial ran.
    report = tick(db, 4, [], strategies=strategies)
    assert report['families']['omp.tool']['status'] == 'selected'
    assert len(trial_bodies(db)) == 2
    assert 'started_at' not in trial_bodies(db)[1]
    for i in (5, 6, 7, 8, 9):
        report = tick(db, i, [observation(strategy='history-3')], strategies=strategies)
    assert report['families']['omp.tool']['status'] == 'awaiting_family'
    assert len(trial_bodies(db)) == 2
    assert all(t['status'] == 'plateau' for t in trial_bodies(db))
    events = [json.loads(r[0]) for r in db.execute('SELECT body FROM learning_search_events')]
    assert sum(e['type'] == 'plateau' for e in events) == 2
    assert sum(e['type'] == 'strategy_selected' for e in events) == 1


def test_selected_strategy_needs_its_own_measurements_and_config_id(db):
    for i in (1, 2, 3):
        tick(db, i, [observation()])
    with pytest.raises(ValueError, match='does not match selected'):
        tick(db, 4, [observation()])
    changed = copy.deepcopy(DEFAULT_STRATEGIES)
    changed[1]['config']['history_width'] = 4
    with pytest.raises(ValueError, match='configuration changed'):
        tick(db, 5, [observation(strategy='history-3')], strategies=changed)


def test_exhausted_below_bar_proposes_next_exact_census_family(db, tmp_path):
    census = [
        {'family': 'file_ranking', 'exact_labels': 0, 'label_source': 'UNKNOWN', 'source_path': str(tmp_path)},
        {'family': 'outcome_error', 'exact_labels': 10, 'label_source': 'regex',
         'source_path': str(tmp_path), 'inferred': True},
        {'family': 'skill_choice', 'exact_labels': 8747, 'label_source': 'actual_skill_arg',
         'source_path': str(tmp_path/'census.json')},
        {'family': 'model_choice', 'exact_labels': 178, 'label_source': 'args.model',
         'source_path': str(tmp_path/'census.json')},
    ]
    for i in (1, 2, 3):
        report = tick(db, i, [observation()], strategies=DEFAULT_STRATEGIES[:1], census_queue=census)
    assert report['families']['omp.tool']['status'] == 'awaiting_family'
    assert report['proposals'][0]['family'] == 'skill_choice'
    assert report['proposals'][0]['status'] == 'proposed'
    report = tick(db, 4, [observation()], strategies=DEFAULT_STRATEGIES[:1], census_queue=census)
    assert report['proposals'][0]['family'] == 'model_choice'
    assert db.execute('SELECT COUNT(*) FROM learning_search_proposals').fetchone()[0] == 2
    assert len(trial_bodies(db)) == 1
    # The scheduler remains alive after exhausting today's queue.
    report = tick(db, 5, [observation()], strategies=DEFAULT_STRATEGIES[:1], census_queue=census)
    assert report['proposals'] == []
    assert report['families']['omp.tool']['status'] == 'awaiting_family'


def test_above_bar_does_not_propose_unrelated_family(db):
    for i in (1, 2, 3):
        report = tick(db, i, [observation(correct=100)], strategies=DEFAULT_STRATEGIES[:1],
                      census_queue=[{'family': 'skill', 'exact_labels': 10,
                                     'label_source': 'exact', 'source_path': '/private/census.json'}])
    assert report['proposals'] == []
    assert report['families']['omp.tool']['status'] == 'awaiting_strategy'


def larger():
    return {'id': 'larger-explicit', 'kind': 'larger_model', 'endpoint': 'configured-large',
            'config': {'history_width': 8, 'candidate_k': 3, 'prompt_bytes': 3000}}


def test_unavailable_larger_model_records_block_and_tries_alternative(db):
    strategies = (DEFAULT_STRATEGIES[0], larger(), DEFAULT_STRATEGIES[-1])
    for i in (1, 2, 3):
        report = tick(db, i, [observation()], strategies=strategies)
    blocked = next(e for e in report['events'] if e['type'] == 'blocked')
    assert blocked['candidate'] == 'larger-explicit'
    assert blocked['reason'] == 'larger_model_endpoint_not_explicitly_configured'
    assert report['families']['omp.tool']['current_strategy'] == 'few-shot-3'
    assert 'larger-explicit' not in {t['strategy'] for t in trial_bodies(db)}
    for i in (4, 5, 6):
        report = tick(db, i, [observation(strategy='few-shot-3')], strategies=strategies)
    # Same block is durable, not emitted again on every tick.
    assert not any(e['type'] == 'blocked' for e in report['events'])
    endpoint = {'configured': True, 'url': 'http://explicit.private:11530', 'healthy': False,
                'model': 'explicit-model@revision', 'quant': 'Q8_0', 'engine': 'llama.cpp@build',
                'backend': 'cpu', 'device': 'mbp'}
    report = tick(db, 7, [], strategies=strategies, endpoints={'configured-large': endpoint})
    assert next(e for e in report['events'] if e['type'] == 'blocked')['reason'].endswith('not_healthy')
    endpoint['healthy'] = True
    report = tick(db, 8, [], strategies=strategies, endpoints={'configured-large': endpoint})
    assert report['families']['omp.tool']['current_strategy'] == 'larger-explicit'
    assert report['families']['omp.tool']['status'] == 'selected'
    assert 'started_at' not in trial_bodies(db)[-1]


def test_recent_sessions_and_retrieval_are_bounded_proposals(db):
    recent = next(s for s in DEFAULT_STRATEGIES if s['config'].get('candidate_source') == 'recent_sessions')
    assert recent['config']['score_full_legal_set'] is True
    assert recent['config']['recent_sessions_n'] == 5
    for i in (1, 2, 3):
        report = tick(db, i, [observation()], strategies=(DEFAULT_STRATEGIES[0], recent))
    assert report['families']['omp.tool']['strategy'] == recent
    unsafe = copy.deepcopy(DEFAULT_STRATEGIES[-1])
    unsafe['config']['previous_only'] = False
    with pytest.raises(ValueError, match='exclude current and future'):
        tick(db, 4, [], strategies=(unsafe,))


def test_small_model_candidate_caps_default_three_and_explicit_17b_five(db):
    assert [s['id'] for s in DEFAULT_STRATEGIES] == [
        'history-1', 'history-3', 'projection-recent-sessions', 'few-shot-3']
    assert all(s['config']['candidate_k'] <= 3 and
               s['config']['decision_method'] == 'per_candidate_binary_logit' and
               s['config']['narrowing'] == 'deterministic' and
               s['config']['abstain'] == 'top_margin' for s in DEFAULT_STRATEGIES)
    for options in (4, 24, 64):
        invalid = copy.deepcopy(DEFAULT_STRATEGIES[0])
        invalid['config'].update(candidate_k=options, max_options=options, model_size_b=.6)
        with pytest.raises(ValueError, match='max_options cap'):
            tick(db, options, [], strategies=(invalid,))
    implicit = copy.deepcopy(DEFAULT_STRATEGIES[0])
    implicit['config'].update(candidate_k=5, max_options=5)
    with pytest.raises(ValueError, match='explicit 1.7B'):
        tick(db, 100, [], strategies=(implicit,))
    explicit = copy.deepcopy(implicit)
    explicit['config']['model_size_b'] = 1.7
    report = tick(db, 101, [observation()], strategies=(explicit,))
    assert report['families']['omp.tool']['strategy']['config']['candidate_k'] == 5


def finding_observation(**kwargs):
    return {**observation(**kwargs), 'identity': {
        'kind': 'slm', 'model': 'qwen3-0.6b-q8@revision', 'quant': 'Q8_0',
        'engine': 'llama.cpp@b1-748d422', 'backend': 'cpu', 'device': 'z0-farm'},
        'projection': {'candidate_k': 3, 'prompt_bytes': 3000},
        'scope': {'project': 'private-project', 'model_scope': 'explicit-model'},
        'scope_note': 'Exact joined tool choices only; not task-success evidence',
        'evidence_class': 'replayed_L1'}


def emit(db, tmp_path, rows, *, tick_id='tick-1', now=1_792_800_000):
    source = tmp_path/'metrics.json'
    if not source.exists():
        source.write_text(json.dumps({'private': 'real per-tick metrics artifact'}))
    return emit_findings(db, tick_id=tick_id, now=now, observations=rows,
                         source_path=source, run_id='private-run', home=tmp_path/'home')


def test_canonical_findings_full_scope_chain_and_each_arm_every_tick(db, tmp_path):
    from z0int.policy.findings import canonical, digest, read_findings

    replay = finding_observation(arm='replayed')
    live = {**finding_observation(arm='prospective'), 'evidence_class': 'live_shadow', 'prospective': True}
    first = emit(db, tmp_path, [replay, live])
    assert len(first) == 2
    second = emit(db, tmp_path, [replay, live], tick_id='tick-2', now=1_792_800_001)
    assert len(second) == 2
    ledger = tmp_path/'home'/'findings'/'findings.jsonl'
    rows = read_findings(ledger)
    assert len(rows) == 4
    assert rows[0]['prev'] == ''
    for previous, row in zip(rows, rows[1:]):
        assert row['prev'] == digest(previous)
    assert ledger.read_bytes() == b''.join(canonical(row)+b'\n' for row in rows)
    for row in rows:
        assert row['schema'] == 'z0.finding.v1'
        assert row['synthetic'] is False
        assert row['arm'] == replay['identity']
        assert row['metric']['strategy'] == 'history-1'
        assert row['metric']['projection'] == replay['projection']
        assert row['metric']['scope'] == replay['scope']
        assert row['metric']['n'] == 100
        assert row['metric']['ci_method'] == 'wilson95'
        assert row['source']['path'] == str((tmp_path/'metrics.json').resolve())
        assert row['source']['sha256'] == hashlib.sha256((tmp_path/'metrics.json').read_bytes()).hexdigest()
        assert row['source']['run_id'] == 'private-run'
        assert 'private-project' in row['scope_note']
    assert rows[0]['evidence_class'] == 'replayed_L1'
    assert rows[0]['metric']['live_shadow'] is False
    assert rows[1]['evidence_class'] == 'measured'
    assert rows[1]['metric']['live_shadow'] is True
    assert emit(db, tmp_path, [replay, live]) == []


def test_canonical_dedup_survives_sqlite_rollback_without_misassigning_ids(db, tmp_path):
    from z0int.policy.findings import read_findings

    initialize(db)
    db.commit()
    first = emit(db, tmp_path, [finding_observation(arm='one')])
    db.rollback()
    # One existing canonical ID and one new ID: filtered writer output must not
    # pair arm one's key with arm two's ID.
    second = emit(db, tmp_path, [finding_observation(arm='one'), finding_observation(arm='two')])
    assert len(second) == 1
    recorded = dict(db.execute('SELECT arm,finding_id FROM learning_search_findings'))
    assert recorded == {'one': first[0]['id'], 'two': second[0]['id']}
    assert len(read_findings(tmp_path/'home'/'findings'/'findings.jsonl')) == 2


def test_no_live_upgrade_no_missing_identity_and_no_zero_n_fabrication(db, tmp_path):
    with pytest.raises(ValueError, match='synthetic observations'):
        emit(db, tmp_path, [{**finding_observation(), 'synthetic': True}])
    fake_live = {**finding_observation(), 'evidence_class': 'live_shadow'}
    with pytest.raises(ValueError, match='prospective'):
        emit(db, tmp_path, [fake_live])
    missing_identity = finding_observation()
    missing_identity['identity'].pop('quant')
    with pytest.raises(ValueError, match='full measured arm identity'):
        emit(db, tmp_path, [missing_identity])
    assert emit(db, tmp_path, [finding_observation(n=0, correct=0)]) == [
        {'family': 'omp.tool', 'arm': 'rules', 'status': 'blocked', 'reason': 'n_zero'}]
    assert not (tmp_path/'home'/'findings'/'findings.jsonl').exists()


def test_whole_findings_batch_rejected_before_any_append(db, tmp_path):
    invalid = {**finding_observation(arm='invalid'), 'evidence_class': 'measured'}
    with pytest.raises(ValueError, match='explicit replayed'):
        emit(db, tmp_path, [finding_observation(), invalid])
    assert not (tmp_path/'home'/'findings'/'findings.jsonl').exists()


def test_wilson_interval_and_config_validation():
    assert wilson_interval(0, 0) == (0., 1.)
    low, high = wilson_interval(50, 100)
    assert .4 < low < .5 < high < .6
    assert wilson_interval(100, 100)[0] > .9
    with pytest.raises(ValueError):
        wilson_interval(101, 100)
    with pytest.raises(ValueError):
        SearchConfig(plateau_ticks=1)
    with pytest.raises(ValueError):
        SearchConfig(plateau_ticks=3.5)
