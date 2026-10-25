#!/usr/bin/env python3
"""Replay frozen literal expectations against the actual bounded lookup injection."""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import sqlite3
import re
import time
from pathlib import Path


def main() -> int:
    repo = Path(__file__).resolve().parents[2]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--index', type=Path, required=True)
    parser.add_argument('--cases', type=Path, default=repo / 'operations/look-first/cases.json')
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    case_bytes = args.cases.read_bytes()
    document = json.loads(case_bytes)
    cases = document['cases']
    labels = {row['review_id']: row for row in (json.loads(line) for line in Path(document['selection']['labels_source']).read_text().splitlines() if line.strip())}
    sessions = {}
    def original_query(case):
        provenance = case['provenance']
        if case['cohort'] == 'ask-audit':
            return labels[provenance['review_id']]['text']
        match = re.fullmatch(r'(.*\.jsonl):\d+', provenance['correction_source'])
        if not match:
            raise ValueError('Missing original correction transcript path')
        path = match.group(1)
        if path not in sessions:
            sessions[path] = {row.get('id'): row for row in (json.loads(line) for line in Path(path).read_text().splitlines() if line.strip())}
        entry = provenance.get('correction_entry_id', provenance.get('entry_id'))
        message = sessions[path][entry]['message']
        if message['role'] != 'user':
            raise ValueError('Correction query must bind an actual user message')
        return '\n'.join(part['text'] for part in message['content'] if part['type'] == 'text')
    ids = [case['id'] for case in cases]
    if len(ids) != len(set(ids)):
        parser.error('Duplicate case IDs')
    for case in cases:
        alternatives = case['expected_substrings']
        if not alternatives or any(not isinstance(value, str) or not value.strip() for value in alternatives):
            parser.error(f"Invalid predeclared literal alternatives: {case['id']}")
    spec = importlib.util.spec_from_file_location('look_first_replay_target', repo / 'src/z0int/look_first.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    results = []
    for case in cases:
        query_text = original_query(case)
        started = time.perf_counter()
        error = None
        hits = []
        injection = ''
        try:
            hits = module.query(args.index, query_text, limit=12)
            injection = module.block(hits)
            # Wrapper tags are not evidence lines. Never silently trim an oversized block.
            lines = injection.splitlines()
            if lines and lines[0] == '<z0-lookup>' and lines[-1] == '</z0-lookup>':
                evidence_lines = lines[1:-1]
            else:
                evidence_lines = lines
            if len(hits) > 12 or len(evidence_lines) > 12:
                raise ValueError('Lookup exceeded twelve evidence lines')
        except (OSError, sqlite3.Error, TimeoutError, ValueError) as exc:
            error = f'{type(exc).__name__}: {exc}'
            evidence_lines = []
        latency_ms = round((time.perf_counter() - started) * 1000, 3)
        # Exact, case-sensitive substrings in one actual injected evidence line.
        matches = [{'literal': literal, 'line': number, 'text': line}
                   for literal in case['expected_substrings']
                   for number, line in enumerate(evidence_lines, 1) if literal in line]
        results.append({'id': case['id'], 'cohort': case['cohort'], 'query': query_text,
                        'expected_fact': case['expected_fact'],
                        'expected_substrings': case['expected_substrings'],
                        'hits': hits, 'injected_block': injection,
                        'evidence_line_count': len(evidence_lines), 'matched_literals': matches,
                        'hit_at_12': bool(matches) and error is None,
                        'latency_ms': latency_ms, 'error': error})
    summaries = {}
    for cohort in sorted({case['cohort'] for case in cases}):
        group = [row for row in results if row['cohort'] == cohort]
        count = sum(row['hit_at_12'] for row in group)
        summaries[cohort] = {'cases': len(group), 'hits_at_12': count,
                             'hit_at_12': count / len(group)}
    report = {'schema': 'z0.look-first.replay.v1', 'index': str(args.index.resolve()),
              'cases_file': str(args.cases.resolve()),
              'cases_sha256': hashlib.sha256(case_bytes).hexdigest(),
              'query_protocol': 'Original user correction text read from the bound audit/session entry. Hindsight diagnostic rewrites in cases.json are not used.',
              'matching': 'Predeclared exact case-sensitive literal substring in a single injected evidence line; wrapper tags excluded',
              'summary': summaries, 'results': results}
    rendered = json.dumps(report, ensure_ascii=False, indent=2) + '\n'
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        args.output.write_text(rendered)
        args.output.chmod(0o600)
    else:
        print(rendered, end='')
    return 2 if any(row['error'] for row in results) else 0


if __name__ == '__main__':
    raise SystemExit(main())
