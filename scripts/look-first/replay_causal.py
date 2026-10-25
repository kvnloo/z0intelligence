#!/usr/bin/env python3
"""Replay IntentService's actual causal cases through offline LookFirst only."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'src'))
from z0int.look_first import block, query


def normalized(value):
    return ' '.join(re.findall(r'\w+', value.lower()))


def replay(index, source_map, selection):
    cases = json.loads(source_map.read_text())
    inputs = {row['case_id']: row for row in (json.loads(line) for line in selection.read_text().splitlines() if line.strip())}
    rows = []
    for case in cases:
        user = inputs[case['case_id']]
        if user['cohort'] != 'confirmed_failure':
            continue
        started = time.monotonic()
        try:
            hits = query(index, user['text'])
            error = None
        except Exception as exc:
            hits, error = [], str(exc)
        injected = block(hits)
        aliases = [alias for source in case['causal_sources'] for alias in source.get('aliases', [])]
        aliases.extend(case.get('identified_document_aliases', []))
        aliases = [alias for alias in aliases if isinstance(alias, str) and alias]
        joined = normalized(injected)
        exact_facts = [fact for fact in case['expected_facts'] if normalized(fact) in joined]
        causal_ids = [alias for alias in aliases if any(alias in hit['source'] for hit in hits)]
        # Source recall and fact containment are separate. An identifier or a
        # shared topic alone is not proof that the causal fact was injected.
        excerpts = [source.get('knowledge_excerpt', '') for source in case['causal_sources']]
        excerpt_hits = [excerpt for excerpt in excerpts if len(normalized(excerpt).split()) >= 8 and normalized(excerpt) in joined]
        rows.append({'case_id': case['case_id'], 'query': user['text'], 'expected_facts': case['expected_facts'],
                     'causal_source_hit': bool(causal_ids), 'causal_ids': causal_ids,
                     'exact_fact_hit': bool(exact_facts or excerpt_hits), 'matched_facts': exact_facts,
                     'matched_full_excerpt': bool(excerpt_hits), 'latency_ms': round((time.monotonic() - started) * 1000, 3),
                     'hits': hits, 'injected_block': injected, 'error': error})
    return {'schema': 'z0.look-first.causal-replay.v1', 'index': str(index),
            'map_sha256': hashlib.sha256(source_map.read_bytes()).hexdigest(),
            'selection_sha256': hashlib.sha256(selection.read_bytes()).hexdigest(), 'cases': len(rows),
            'cohort': 'confirmed_failure',
            'causal_source_hit_at_12': sum(row['causal_source_hit'] for row in rows),
            'exact_fact_hit_at_12': sum(row['exact_fact_hit'] for row in rows),
            'query_errors': sum(row['error'] is not None for row in rows),
            'limitation': 'Exact normalized fact/full-excerpt containment is conservative, not a semantic judge. Source identity is reported separately. No provider calls, no training, no source-map facts added to the index. Retrospective retrieval is not a causal prevention estimate.',
            'results': rows}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--index', type=Path, default=Path.home() / '.local/state/z0int/capability-index.sqlite3')
    root = Path('/mnt/zer0models/workspace/zer0/data/operations/intent-service')
    parser.add_argument('--source-map', type=Path, default=root / 'causal-fact-source-map.json')
    parser.add_argument('--selection', type=Path, default=root / 'eval-selection.jsonl')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    result = replay(args.index, args.source_map, args.selection)
    args.output.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    args.output.write_text(json.dumps(result, sort_keys=True, indent=2) + '\n')
    args.output.chmod(0o600)
    print(json.dumps({key: value for key, value in result.items() if key != 'results'}, sort_keys=True))


if __name__ == '__main__':
    main()
