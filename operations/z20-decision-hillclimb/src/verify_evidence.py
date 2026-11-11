"""Check saved aggregates and read receipts without opening sealed test inputs.

This checks row consistency, not independent held-out gold or measured latency.
"""
import argparse
from collections import defaultdict
import hashlib
import json
import math
from pathlib import Path


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def require(condition, message):
    if not condition:
        raise ValueError(message)


def check_rows(rows, expected):
    require(len({r['id'] for r in rows}) == len(rows), 'Duplicate prediction ID')
    require(rows and len(rows) == expected['n'], 'Prediction count mismatch')
    require(all(type(r['correct']) is bool for r in rows), 'Invalid correctness value')
    actual = {'n': len(rows), 'accuracy': sum(r['correct'] for r in rows) / len(rows)}
    valid, bins, brier = [], defaultdict(list), []
    for row in rows:
        ps = row['probabilities']
        if ps is None:
            continue
        require(isinstance(ps, list) and ps and all(type(p) in (int, float) and math.isfinite(p)
                and 0 <= p <= 1 for p in ps) and abs(sum(ps) - 1) <= 1e-4, 'Invalid probability vector')
        confidence, gold = row['confidence'], row['gold_index']
        require(type(confidence) in (int, float) and math.isfinite(confidence) and 0 <= confidence <= 1,
                'Invalid probability confidence')
        require(type(gold) is int and 0 <= gold < len(ps), 'Invalid gold index')
        valid.append(row)
        bins[min(9, int(confidence * 10))].append(row)
        brier.append(sum((p - (i == gold)) ** 2 for i, p in enumerate(ps)))
    actual['probability_coverage'] = len(valid) / len(rows)
    actual['ece_10_equal_width'] = (sum(abs(sum(r['correct'] for r in b) - sum(r['confidence'] for r in b))
                                         for b in bins.values()) / len(valid)) if len(valid) == len(rows) else None
    actual['brier_multiclass'] = sum(brier) / len(brier) if len(valid) == len(rows) else None
    for key, value in actual.items():
        if key in expected:
            require(value is expected[key] if value is None else expected[key] is not None and
                    math.isclose(value, expected[key], rel_tol=1e-10, abs_tol=1e-10), f'Metric mismatch: {key}')
    return actual


def check_receipt(reads, summary, split_sha, n):
    require(len(reads) == 2 and [r.get('event') for r in reads] == ['test_read_started', 'test_read_completed'],
            'Expected exactly one started and completed test read')
    require(reads[0]['split_sha256'] == split_sha, 'Test split hash mismatch')
    require(reads[1]['summary_sha256'] == digest(summary), 'Test summary hash mismatch')
    require(reads[1]['rows'] == n, 'Test row count mismatch')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--execution', type=Path, required=True)
    args = parser.parse_args()
    root = args.execution
    validation_path = root / 'artifacts/validation-final/summary.json'
    test_path = root / 'artifacts/test-final/summary.json'
    validation, test = [json.loads(p.read_text()) for p in (validation_path, test_path)]
    frozen = json.loads((root / 'sources/owner/data/freeze.json').read_text())
    manifest = json.loads((root / 'artifacts/validation-final/input-sha256.json').read_text())
    for name, sha in manifest.items():
        require(digest(root / 'sources/owner' / name) == sha, f'Input changed: {name}')
    require(validation['source_revision'] == digest(Path(__file__).with_name('audit_round.py')), 'Audit source changed')
    reads = [json.loads(line) for line in (root / 'artifacts/test-reads.jsonl').read_text().splitlines()]
    check_receipt(reads, test_path, frozen['db_items_test_jsonl'], frozen['db_items_test_n'])
    require(reads[0]['validation_sha256'] == digest(validation_path), 'Validation hash mismatch')
    require(reads[0]['source_sha256'] == digest(Path(__file__).with_name('final_eval.py')), 'Final-eval source changed')
    for key in ('vocabulary_sha256', 'train_ids_sha256', 'n_train'):
        require(validation['training'][key] == test['training'][key], f'Training freeze mismatch: {key}')
    require(validation['promotion_allowed'] is False and test['promotion_allowed'] is False, 'Unsafe promotion claim')
    results = {}
    for stage, summary in [('validation-final', validation), ('test-final', test)]:
        arms = defaultdict(list)
        for line in (root / 'artifacts' / stage / 'predictions.jsonl').read_text().splitlines():
            row = json.loads(line)
            arms[row['question'], row['candidate']].append(row)
        expected = {(q, name): metrics for q in ('component', 'priority') for name, metrics in
                    summary['metrics'][f'D-B:{q}:VALIDATION' if stage == 'validation-final' else q].items()}
        require(set(arms) == set(expected), 'Candidate set mismatch')
        results[stage] = {f'{q}:{name}': check_rows(arms[q, name], metrics) for (q, name), metrics in expected.items()}
        require(all({r['id'] for r in rows} == {r['id'] for r in next(iter(arms.values()))}
                    for rows in arms.values()), 'Candidate coverage mismatch')
    print(json.dumps({'status': 'PASS', 'input_hashes_checked': len(manifest), 'test_source_opened': False,
                      'scope': 'saved row aggregates and receipts, not independent gold regrading', 'metrics': results}, indent=2))


if __name__ == '__main__':
    main()
