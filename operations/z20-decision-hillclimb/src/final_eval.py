"""One logged sealed D-B read for the frozen CPU baselines, never a model gate.

Requires a completed validation audit with this exact source revision. Cached
teacher labels cover train/validation only, so NO Jev test comparison is claimed.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path

import numpy as np
from audit_round import (arena, create_output, db_baselines, digest, indexed,
                         metrics, read_jsonl, require_clean_state, validate_db)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data-root', type=Path, required=True)
    parser.add_argument('--test', type=Path, required=True)
    parser.add_argument('--validation', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--reads', type=Path, required=True)
    args = parser.parse_args()
    validation = json.loads(args.validation.read_text())
    if validation['source_revision'] != digest(Path(__file__).with_name('audit_round.py')):
        raise ValueError('Audit source changed after validation freeze')
    candidates = ['majority_gold_train', 'tfidf_gold', 'tfidf_jev_hard']
    winners = {q: max(candidates, key=lambda n: validation['metrics'][f'D-B:{q}:VALIDATION'][n]['accuracy'])
               for q in ('component', 'priority')}
    freeze = json.loads((args.data_root / 'data/freeze.json').read_text())
    create_output(args.out)
    # Exclusive creation prevents a second test access under a different output path.
    with args.reads.open('x') as stream:
        stream.write(json.dumps({'event': 'test_read_started', 'utc': datetime.now(timezone.utc).isoformat(),
            'split_sha256': freeze['db_items_test_jsonl'], 'candidate_ids': candidates,
            'validation_winners': winners, 'validation_sha256': digest(args.validation),
            'source_sha256': digest(__file__), 'path': str(args.test)}) + '\n')
    blob = args.test.read_bytes()  # The only sealed-file open in this command.
    if hashlib.sha256(blob).hexdigest() != freeze['db_items_test_jsonl']:
        raise ValueError('Sealed test hash mismatch')
    test = [json.loads(line) for line in blob.splitlines() if line.strip()]
    indexed(test, 'sealed test')
    if len(test) != freeze['db_items_test_n'] or any(r['split'] != 'test' for r in test):
        raise ValueError('Sealed test size/split mismatch')
    for row in test:
        require_clean_state(row)
    td = args.data_root / 'data/teacher'
    items = read_jsonl(td / 'db_items.jsonl')
    validate_db(items)
    if {r['id'] for r in items} & {r['id'] for r in test}:
        raise ValueError('Train/validation overlaps test')
    teacher = indexed(read_jsonl(td / 'db_labels_pass1.jsonl'), 'teacher')
    _, results, aa, training = db_baselines(items, teacher, sealed_evaluation=test)
    for key in ('vocabulary_sha256', 'train_ids_sha256', 'n_train'):
        if training[key] != validation['training'][key]:
            raise ValueError(f'Training freeze changed: {key}')
    summary = {'promotion_allowed': False, 'paid_calls': 0, 'sealed_reads': 1,
               'jev_test_comparison': 'NOT RUN: no cached labels; no paid calls authorized',
               'student_type': 'TF-IDF logistic CPU proxy, NOT an SLM', 'validation_winners': winners,
               'training': training, 'aa': aa, 'arena': {}, 'metrics': {}}
    with (args.out / 'predictions.jsonl').open('x') as stream:
        for q, arms in results.items():
            names = list(arms)
            summary['arena'][q] = arena(names, np.array([[arms[n][i]['correct'] for n in names] for i in range(len(test))]),
                                         [r['id'] for r in test], 2000)
            summary['metrics'][q] = {n: metrics(rows) for n, rows in arms.items()}
            for name, rows in arms.items():
                for row in rows:
                    stream.write(json.dumps({'question': q, 'candidate': name, **row}) + '\n')
    (args.out / 'summary.json').write_text(json.dumps(summary, indent=2, allow_nan=False) + '\n')
    with args.reads.open('a') as stream:
        stream.write(json.dumps({'event': 'test_read_completed', 'rows': len(test), 'summary_sha256': digest(args.out / 'summary.json')}) + '\n')
    print(json.dumps({'rows': len(test), 'sealed_reads': 1, 'validation_winners': winners, 'promotion_allowed': False}))


if __name__ == '__main__':
    main()
