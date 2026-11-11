"""Offline regressions. No APIs, history stores, models, or GPU."""
import copy
import importlib.util
import hashlib
import json
from pathlib import Path
import subprocess
import sys

import numpy as np
import pytest

SPEC = importlib.util.spec_from_file_location('audit_round', Path(__file__).with_name('audit_round.py'))
audit = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(audit)


def item():
    return {'id': 'x', 'split': 'train', 'state': {'title': 'hello'},
            'questions': {'q': {'criteria': {'a': 'A', 'b': 'B'}, 'instructions': 'pick'}}}


def test_leaked_outcomes_fail_closed():
    row = item()
    audit.require_clean_state(row)
    row['state']['candidate_outcomes'] = {'a': {'correct': True}}
    with pytest.raises(ValueError, match='candidate_outcomes'):
        audit.require_clean_state(row)


def test_permutation_requires_real_order_and_unchanged_instructions():
    a = item()
    b = copy.deepcopy(a)
    with pytest.raises(ValueError, match='order did not change'):
        audit.require_permutation(a, b)
    b['questions']['q']['criteria'] = {'b': 'B', 'a': 'A'}
    audit.require_permutation(a, b)
    b['questions']['q']['instructions'] += ' (option order reversed)'
    with pytest.raises(ValueError, match='instructions'):
        audit.require_permutation(a, b)


def test_permutation_rejects_description_mismatch():
    a = item()
    b = copy.deepcopy(a)
    b['questions']['q']['criteria'] = {'b': 'changed', 'a': 'A'}
    with pytest.raises(ValueError, match='option set'):
        audit.require_permutation(a, b)


def test_bt_symmetric_and_dominating():
    np.testing.assert_allclose(audit.bt(np.array([[0, 10], [10, 0.]])), [0, 0])
    rating = audit.bt(np.array([[0, 10], [0, 0.]]))
    assert np.isfinite(rating).all() and rating[0] > rating[1]
    assert abs(rating.sum()) < 1e-9
    np.testing.assert_allclose(rating, -rating[::-1])


def test_bt_counts_half_ties_once():
    wins = audit.pair_wins(np.array([[True, False], [False, False], [True, True]]))
    np.testing.assert_allclose(wins, [[0, 2], [1, 0]])


def test_identical_candidate_bootstrap_difference_zero():
    r = audit.arena(['a', 'b'], np.array([[1, 1], [0, 0], [1, 1]], dtype=bool),
                    ['task1', 'task1', 'task2'], samples=40)
    assert r['clusters'] == 2
    assert r['leaderboard']['a']['rating_ci95'] == [0.0, 0.0]
    assert r['leaderboard']['a']['accuracy_ci95'] == r['leaderboard']['b']['accuracy_ci95']


def test_bad_probabilities_do_not_become_calibration_success():
    assert audit.probability_vector({'a': .4, 'b': .7}, ['a', 'b']) is None
    assert audit.probability_vector({'a': float('nan'), 'b': 1}, ['a', 'b']) is None
    assert audit.probability_vector({'a': .4, 'b': .6}, ['a', 'b']) == [.4, .6]


def test_cascade_ties_are_not_split():
    rows = [{'correct': True, 'confidence': .8}, {'correct': False, 'confidence': .8}]
    curve = audit.cascade(rows, [True, True])
    assert {x['coverage'] for x in curve} == {0, 1}
    assert curve[-1]['accuracy'] == .5


def test_feature_fit_ignores_validation_words():
    train = [dict(item(), id='1', state={'title': 'cat common'}),
             dict(item(), id='2', state={'title': 'dog common'})]
    vocab, _, _ = audit.features(train, [dict(item(), state={'title': 'sealedword'})])
    assert 'sealedword' not in vocab.vocabulary_


def test_vocabulary_hash_accepts_numpy_indices():
    assert audit.vocabulary_hash({'cat': np.int64(1)}) == audit.vocabulary_hash({'cat': 1})


def test_split_and_id_guards():
    a = item()
    with pytest.raises(ValueError, match='Duplicate'):
        audit.indexed([a, a], 'test')
    with pytest.raises(ValueError, match='train/val'):
        audit.validate_db([dict(a, split='test')])


def test_output_is_create_only(tmp_path):
    out = tmp_path / 'result'
    audit.create_output(out)
    with pytest.raises(FileExistsError):
        audit.create_output(out)


def test_sanitized_builder_preserves_frozen_files_and_reverses_order(tmp_path):
    data = tmp_path / 'data'
    data.mkdir()
    states = {'corpus': [{'node_id': 'x', 'pool': ['b', 'a'], 'split': 'train',
                         'features': {}, 'task_family': 'broad', 'candidate_outcomes': {'a': {'correct': True}}}]}
    gold = {'x': {'any_correct': True, 'selected': 'a', 'task': 'real-task-id'}}
    (data / 'da_states.json').write_text(json.dumps(states))
    (data / 'da_gold.json').write_text(json.dumps(gold))
    freeze = {p.name: audit.digest(p) for p in data.iterdir()}
    (data / 'freeze.json').write_text(json.dumps({'frozen': freeze}))
    frozen_hash = audit.digest(data / 'freeze.json')
    out = tmp_path / 'sanitized'
    cmd = [sys.executable, str(Path(__file__).with_name('build_da_items.py')),
           '--root', str(tmp_path), '--output-dir', str(out)]
    subprocess.run(cmd, check=True, capture_output=True, text=True)
    a = audit.read_jsonl(out / 'da_items.jsonl')[0]
    b = audit.read_jsonl(out / 'da_items_reversed.jsonl')[0]
    audit.require_clean_state(a)
    audit.require_permutation(a, b)
    assert a['task'] == 'real-task-id'
    assert list(a['questions']['route_pick']['criteria']) == ['b', 'a']
    assert audit.digest(data / 'freeze.json') == frozen_hash
    assert subprocess.run(cmd, capture_output=True).returncode != 0


def test_final_eval_runs_once_without_teacher_test_labels(tmp_path):
    td = tmp_path / 'data/teacher'
    td.mkdir(parents=True)
    def row(i, split):
        label = 'a' if i % 2 else 'b'
        return {'id': str(i), 'split': split, 'state': {'title': label + ' topic'},
                'gold': {q: label for q in ('component', 'priority')},
                'questions': {q: {'criteria': {'a': 'A', 'b': 'B'}} for q in ('component', 'priority')}}
    train = [row(i, 'train') for i in range(10)]
    val = [row(i, 'val') for i in range(10, 14)]
    test = [row(i, 'test') for i in range(14, 18)]
    teacher = [{'id': r['id'], 'answers': {q: {'choice': r['gold'][q]} for q in ('component', 'priority')}} for r in train]
    (td / 'db_items.jsonl').write_text(''.join(json.dumps(r) + '\n' for r in train + val))
    (td / 'db_labels_pass1.jsonl').write_text(''.join(json.dumps(r) + '\n' for r in teacher))
    sealed = tmp_path / 'sealed.jsonl'
    sealed.write_text(''.join(json.dumps(r) + '\n' for r in test))
    (tmp_path / 'data/freeze.json').write_text(json.dumps({'db_items_test_jsonl': audit.digest(sealed), 'db_items_test_n': 4}))
    _, _, _, training = audit.db_baselines(train + val, audit.indexed(teacher, 'teacher'), sealed_evaluation=test)
    validation = {'source_revision': audit.digest(Path(__file__).with_name('audit_round.py')),
                  'training': training, 'metrics': {f'D-B:{q}:VALIDATION': {n: {'accuracy': .5} for n in
                    ('majority_gold_train', 'tfidf_gold', 'tfidf_jev_hard')} for q in ('component', 'priority')}}
    vf = tmp_path / 'validation.json'
    vf.write_text(json.dumps(validation))
    reads = tmp_path / 'reads.jsonl'
    out = tmp_path / 'final'
    cmd = [sys.executable, str(Path(__file__).with_name('final_eval.py')), '--data-root', str(tmp_path),
           '--test', str(sealed), '--validation', str(vf), '--reads', str(reads), '--out', str(out)]
    subprocess.run(cmd, check=True, capture_output=True, text=True)
    summary = json.loads((out / 'summary.json').read_text())
    assert summary['sealed_reads'] == 1 and summary['paid_calls'] == 0
    assert all('jev_cached' not in a for a in summary['metrics'].values())
    assert len(reads.read_text().splitlines()) == 2
    cmd[-1] = str(tmp_path / 'second-attempt')
    assert subprocess.run(cmd, capture_output=True).returncode != 0
    assert len(reads.read_text().splitlines()) == 2


def test_aa_missing_ids_is_not_zero_flips():
    a = [{'id': 'x', 'answers': {'q': {'choice': 'a'}}}]
    with pytest.raises(ValueError, match='coverage'):
        audit.flips(a, [], ['q'])
    assert audit.flips(a, a, ['q'])['q'] == {'flips': 0, 'n': 1, 'rate': 0.0}
