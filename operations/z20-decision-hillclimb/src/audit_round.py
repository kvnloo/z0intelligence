"""Z20 cached-round audit and CPU baselines. No API calls or routing writes.

Raw public records stay outside git. Outputs are create-only. D-B TEST is never
opened by this audit. D-A legacy test is diagnostic only because inputs leak gold.
"""
import argparse
from collections import Counter, defaultdict
import hashlib
import importlib.util
import json
import math
from pathlib import Path
import platform
import sys
import time

import numpy as np
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import LogisticRegression

# Reuse the repository's strict probability and unique-ID contracts.
SPEC = importlib.util.spec_from_file_location(
    'z20_benchmark_contract', Path(__file__).resolve().parents[3] / 'benchmarks/evaluate.py')
CONTRACT = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(CONTRACT)
indexed, read_jsonl, vector = CONTRACT.indexed, CONTRACT.read_jsonl, CONTRACT.vector


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def create_output(path):
    Path(path).mkdir(parents=True, exist_ok=False)


def vocabulary_hash(vocabulary):
    return hashlib.sha256(json.dumps({k: int(v) for k, v in vocabulary.items()}, sort_keys=True).encode()).hexdigest()


def require_clean_state(item):
    forbidden = {'candidate_outcomes', 'objective_gold', 'gold', 'correct', 'outcomes', 'selected'}
    def visit(value):
        if isinstance(value, dict):
            for key, child in value.items():
                if key in forbidden or key.startswith('train_quality'):
                    raise ValueError(f'Leaked outcome field: {key}')
                visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)
    visit(item['state'])


def require_permutation(a, b):
    if a['id'] != b['id'] or a['state'] != b['state'] or a['questions'].keys() != b['questions'].keys():
        raise ValueError('Permutation changed item/state/questions')
    for q, qa in a['questions'].items():
        qb = b['questions'][q]
        if qa['criteria'] != qb['criteria']:
            raise ValueError('Permutation changed option set or descriptions')
        if qa.get('instructions') != qb.get('instructions'):
            raise ValueError('Permutation changed instructions')
        if list(qa['criteria']) == list(qb['criteria']):
            raise ValueError('Option order did not change')


def validate_db(rows):
    indexed(rows, 'D-B items')
    if any(r['split'] not in ('train', 'val') for r in rows):
        raise ValueError('Audit accepts train/val only, not sealed test')
    for row in rows:
        require_clean_state(row)


def flips(a, b, questions):
    left, right = indexed(a, 'pass1'), indexed(b, 'pass2')
    if left.keys() != right.keys() or not left:
        raise ValueError('A/A coverage differs')
    return {q: {'flips': sum(left[k]['answers'][q]['choice'] != right[k]['answers'][q]['choice'] for k in left),
                'n': len(left), 'rate': sum(left[k]['answers'][q]['choice'] != right[k]['answers'][q]['choice']
                                          for k in left) / len(left)} for q in questions}


def probability_vector(ps, options):
    try:
        return vector(ps, options)
    except (ValueError, TypeError):
        return None


def bt(wins):
    """MM fit, centered log-strengths; half-win pseudo-count per direction/pair."""
    w = np.asarray(wins, dtype=float) + .5 * (1 - np.eye(len(wins)))
    totals, strength = w + w.T, np.ones(len(w))
    for _ in range(500):
        denom = (totals / (strength[:, None] + strength[None, :])).sum(axis=1)
        updated = w.sum(axis=1) / denom
        updated /= np.exp(np.log(updated).mean())
        if np.max(np.abs(np.log(updated / strength))) < 1e-8:
            strength = updated
            break
        strength = updated
    return np.log(strength)


def pair_wins(correct):
    x = np.asarray(correct, dtype=float)
    wins = x.T @ (1 - x)
    ties = x.T @ x + (1 - x).T @ (1 - x)
    result = wins + .5 * ties
    np.fill_diagonal(result, 0)
    return result


def arena(names, correct, groups, samples=2000):
    units = defaultdict(list)
    for i, group in enumerate(groups):
        units[group].append(i)
    clusters = list(units.values())
    rng = np.random.default_rng(20)
    ratings, accuracies = [], []
    for _ in range(samples):
        ids = [i for k in rng.integers(len(clusters), size=len(clusters)) for i in clusters[k]]
        draw = correct[ids]
        ratings.append(bt(pair_wins(draw)))
        accuracies.append(draw.mean(axis=0))
    estimates = bt(pair_wins(correct))
    ratings, accuracies = np.asarray(ratings), np.asarray(accuracies)
    return {'n': len(correct), 'clusters': len(clusters), 'bootstrap_samples': samples, 'seed': 20,
            'method': 'correctness-derived BT, ties half-win, 0.5 pseudo-win per direction/pair; centered log strength',
            'pair_wins_including_half_ties': pair_wins(correct).tolist(),
            'leaderboard': {name: {'rating': float(estimates[i]),
                'rating_se': float(ratings[:, i].std(ddof=1)),
                'rating_ci95': np.quantile(ratings[:, i], [.025, .975]).tolist(),
                'accuracy': float(correct[:, i].mean()),
                'accuracy_ci95': np.quantile(accuracies[:, i], [.025, .975]).tolist()} for i, name in enumerate(names)}}


def metrics(rows):
    valid = [r for r in rows if r['probabilities'] is not None]
    bins = defaultdict(list)
    for row in valid:
        bins[min(9, int(row['confidence'] * 10))].append(row)
    ece = sum(len(b) * abs(np.mean([r['correct'] for r in b]) -
                          np.mean([r['confidence'] for r in b])) for b in bins.values()) / len(valid) if valid else None
    brier = float(np.mean([sum((p - (i == r['gold_index'])) ** 2 for i, p in enumerate(r['probabilities']))
                           for r in valid])) if valid else None
    latency = [r['latency_ms'] for r in rows if r['latency_ms'] is not None]
    return {'n': len(rows), 'accuracy': float(np.mean([r['correct'] for r in rows])),
            'probability_coverage': len(valid) / len(rows),
            'ece_10_equal_width': ece if len(valid) == len(rows) else None,
            'brier_multiclass': brier if len(valid) == len(rows) else None,
            'ece_valid_only': ece, 'brier_valid_only': brier,
            'p50_ms': float(np.quantile(latency, .5)) if latency else None,
            'p95_ms': float(np.quantile(latency, .95)) if latency else None}


def prediction(item, question, answer, gold, latency=None):
    options = list(item['questions'][question]['criteria'])
    if answer.get('choice') not in options:
        raise ValueError(f'Invalid native choice {item["id"]}:{question}')
    probabilities = probability_vector(answer.get('probabilities'), options)
    choice = answer['choice']
    return {'id': item['id'], 'choice': choice, 'correct': choice == gold,
            'gold_index': options.index(gold), 'probabilities': probabilities,
            'confidence': probabilities[options.index(choice)] if probabilities else None,
            'latency_ms': latency}


def cascade(student, teacher_correct):
    """Fixed thresholds, including abstain-all. Validation descriptive, not a gate."""
    curve = []
    for threshold in [1.01, .99, .95, .9, .8, .7, .6, .5, .4, .3, .2, .1, 0]:
        accept = [r['confidence'] is not None and r['confidence'] >= threshold for r in student]
        correct = [r['correct'] if a else t for r, a, t in zip(student, accept, teacher_correct)]
        curve.append({'threshold': threshold, 'coverage': sum(accept) / len(accept),
                      'accuracy': sum(correct) / len(correct), 'decisions_saved': sum(accept)})
    return curve


def features(train, evaluation):
    def texts(rows):
        return [r['state'].get('title', '') + '\n' + r['state'].get('body', '') for r in rows]
    vocab = TfidfVectorizer(ngram_range=(1, 2), min_df=1, max_features=30000, sublinear_tf=True)
    return vocab, vocab.fit_transform(texts(train)), vocab.transform(texts(evaluation))


def db_baselines(items, teacher, sealed_evaluation=None):
    train = [r for r in items if r['split'] == 'train']
    val = [r for r in items if r['split'] == 'val'] if sealed_evaluation is None else sealed_evaluation
    vocab, x_train, x_val = features(train, val)
    results, aa = {}, {}
    for q in ('component', 'priority'):
        options = list(train[0]['questions'][q]['criteria'])
        if any(r['questions'][q]['criteria'] != train[0]['questions'][q]['criteria'] for r in items + val):
            raise ValueError('Option schema drift')
        truth = [r['gold'][q] for r in train]
        counts = Counter(truth)
        majority = max(options, key=lambda k: counts[k])
        prior = {k: counts[k] / len(train) for k in options}
        candidates = {'majority_gold_train': [prediction(r, q, {'choice': majority, 'probabilities': prior}, r['gold'][q]) for r in val]}
        for name, labels in [('tfidf_gold', truth), ('tfidf_jev_hard', [teacher[r['id']]['answers'][q]['choice'] for r in train])]:
            model = LogisticRegression(C=1.0, max_iter=300, random_state=20)
            model.fit(x_train, labels)
            rows = []
            repeats = 0
            for i, r in enumerate(val):
                t0 = time.perf_counter()
                ps = model.predict_proba(x_val[i])[0]
                ms = (time.perf_counter() - t0) * 1000
                again = model.predict_proba(x_val[i])[0]
                repeats += int(ps.argmax() != again.argmax())
                probabilities = {k: 0.0 for k in options}
                probabilities.update(zip(model.classes_, map(float, ps)))
                choice = max(probabilities, key=probabilities.get)
                rows.append(prediction(r, q, {'choice': choice, 'probabilities': probabilities}, r['gold'][q], ms))
            candidates[name] = rows
            aa[f'{q}:{name}'] = {'flips': repeats, 'n': len(val), 'kind': 'same fitted deterministic model, not two training seeds'}
        if sealed_evaluation is None:
            candidates['jev_cached'] = [prediction(r, q, teacher[r['id']]['answers'][q], r['gold'][q], teacher[r['id']]['latency_ms']) for r in val]
        results[q] = candidates
    return val, results, aa, {'vocabulary_sha256': vocabulary_hash(vocab.vocabulary_),
                             'train_ids_sha256': hashlib.sha256('\n'.join(r['id'] for r in train).encode()).hexdigest(),
                             'n_train': len(train), 'n_val': len(val), 'vocabulary_size': len(vocab.vocabulary_)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data-root', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--bootstrap', type=int, default=2000)
    args = parser.parse_args()
    create_output(args.out)
    root, td = args.data_root, args.data_root / 'data/teacher'
    freeze = json.loads((root / 'data/freeze.json').read_text())
    hashes = {str(p.relative_to(root)): digest(p) for p in sorted(root.rglob('*')) if p.is_file()}
    expected = {'data/da_gold.json': freeze['frozen']['da_gold.json'],
                'data/teacher/da_items.jsonl': freeze['da_items_jsonl'],
                'data/teacher/da_items_aa.jsonl': freeze['da_aa_items_jsonl'],
                'data/teacher/da_items_reversed.jsonl': freeze['da_reversed_items_jsonl'],
                'data/teacher/db_items.jsonl': freeze['db_items_jsonl'],
                'data/teacher/db_items_reversed.jsonl': freeze['db_items_reversed_jsonl']}
    if any(hashes[k] != value for k, value in expected.items()):
        raise ValueError('Frozen input hash mismatch')
    (args.out / 'input-sha256.json').write_text(json.dumps(hashes, indent=2) + '\n')
    report = {'promotion_allowed': False, 'paid_calls_this_execution': 0, 'frozen_hash_checks': expected,
              'scope': 'D-B validation only; D-A legacy test diagnostic, NOT a clean held-out result',
              'source_revision': digest(__file__), 'python': platform.python_version(), 'audit': {}, 'aa': {}, 'arena': {},
              'metrics': {}, 'cascade': {}, 'test_accesses': [{'decision': 'D-A', 'reason': 'legacy contaminated diagnostic'}],
              'db_test_read': False}
    sets, labels = {}, {}
    for decision in ('da', 'db'):
        sets[decision] = read_jsonl(td / f'{decision}_items.jsonl')
        labels[decision] = {suffix: read_jsonl(td / f'{decision}_labels_{suffix}.jsonl') for suffix in ('pass1', 'pass2', 'reversed')}
        qs = list(sets[decision][0]['questions'])
        report['aa'][decision] = flips(labels[decision]['pass1'], labels[decision]['pass2'], qs)
        report['aa'][decision + '_instruction_change_NOT_order_test'] = flips(labels[decision]['pass1'], labels[decision]['reversed'], qs)
        reversed_items = indexed(read_jsonl(td / f'{decision}_items_reversed.jsonl'), 'reversed')
        run_indices = [indexed(run, 'labels') for run in labels[decision].values()]
        item_ids = {item['id'] for item in sets[decision]}
        if any(set(run) != item_ids for run in [reversed_items, *run_indices]):
            raise ValueError('Item/label coverage mismatch')
        clean, changed_order, unchanged_instructions = 0, 0, 0
        for item in sets[decision]:
            try:
                require_clean_state(item)
                clean += 1
            except ValueError:
                pass
            other = reversed_items[item['id']]
            changed_order += any(list(item['questions'][q]['criteria']) != list(other['questions'][q]['criteria']) for q in qs)
            unchanged_instructions += all(item['questions'][q]['instructions'] == other['questions'][q]['instructions'] for q in qs)
            for run in run_indices:
                answer = run[item['id']]
                for q in qs:
                    if set(answer['answers'][q]['probabilities']) != set(item['questions'][q]['criteria']):
                        raise ValueError('Native probability option set mismatch')
        report['audit'][decision] = {'items': len(sets[decision]), 'clean_states': clean,
                                    'actual_order_changed_items': changed_order, 'unchanged_instruction_items': unchanged_instructions}
    report['cached_jev_decisions'] = sum(len(r['answers']) for runs in labels.values() for rows in runs.values() for r in rows)
    validate_db(sets['db'])
    val, db, aa, training = db_baselines(sets['db'], indexed(labels['db']['pass1'], 'teacher'))
    report['deterministic_proxy_aa'], report['training'] = aa, training
    for q, candidates in db.items():
        key = f'D-B:{q}:VALIDATION'
        names = list(candidates)
        correctness = np.array([[candidates[n][i]['correct'] for n in names] for i in range(len(val))])
        report['arena'][key] = arena(names, correctness, [r['id'] for r in val], args.bootstrap)
        report['metrics'][key] = {name: metrics(rows) for name, rows in candidates.items()}
        report['cascade'][key] = {n: cascade(rows, [r['correct'] for r in candidates['jev_cached']]) for n, rows in candidates.items() if n != 'jev_cached'}
    gold = json.loads((root / 'data/da_gold.json').read_text())
    da = [r for r in sets['da'] if r['split'] == 'test']
    train_gold = Counter(r['objective_gold'] for r in sets['da'] if r['split'] == 'train')
    best_pool = train_gold.most_common(1)[0][0]
    runs = {'jev_cached': indexed(labels['da']['pass1'], 'jev'),
            'd1_3B_cached_UNTRAINED_LEAKED': indexed(read_jsonl(td / 'd1_d1-3B_da.jsonl'), 'd1')}
    candidates = {name: [prediction(r, 'route_pick', run[r['id']]['answers']['route_pick'], r['objective_gold'], run[r['id']]['latency_ms']) for r in da] for name, run in runs.items()}
    candidates['best_single_pool_train'] = [prediction(r, 'route_pick', {'choice': best_pool}, r['objective_gold']) for r in da]
    key, names = 'D-A:LEAKED_LEGACY_TEST', list(candidates)
    report['arena'][key] = arena(names, np.array([[candidates[n][i]['correct'] for n in names] for i in range(len(da))]), [gold[r['id']]['task'] for r in da], args.bootstrap)
    report['metrics'][key] = {n: metrics(rows) for n, rows in candidates.items()}
    report['cascade'][key] = {'d1_3B_cached_UNTRAINED_LEAKED': cascade(candidates['d1_3B_cached_UNTRAINED_LEAKED'], [r['correct'] for r in candidates['jev_cached']])}
    with (args.out / 'predictions.jsonl').open('x') as stream:
        for q, arms in db.items():
            for name, rows in arms.items():
                for row in rows:
                    stream.write(json.dumps({'question': q, 'candidate': name, **row}) + '\n')
    (args.out / 'summary.json').write_text(json.dumps(report, indent=2, allow_nan=False) + '\n')
    print(json.dumps({'out': str(args.out), 'cached_jev_decisions': report['cached_jev_decisions'],
                      'promotion_allowed': False, 'db_test_read': False, 'audit': report['audit']}, indent=2))


if __name__ == '__main__':
    main()
