#!/usr/bin/env python3
"""Portable, standard-library-only replay of the sealed synthetic canary.

From any directory: python3 -B /path/to/repo/studies/aodl-nous-solar-completion-v1/replay.py
No credential, network, provider call, inference, or runtime import is needed.
The optional 80 mutation tests additionally require pytest; see REPLAY.json.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sys

sys.dont_write_bytecode = True
import independent_study_check as checker

BUNDLE = Path(__file__).resolve().parent
REPO = BUNDLE.parents[1]


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def contained(base: Path, relative: str) -> Path:
    path = (base / relative).resolve()
    if not path.is_relative_to(base.resolve()):
        raise ValueError('manifest path escapes its root')
    return path


def replay() -> dict:
    manifest = json.loads((BUNDLE / 'ARTIFACT_MANIFEST.json').read_text())
    for row in manifest['artifacts']:
        if sha(contained(BUNDLE, row['path'])) != row['sha256']:
            raise ValueError('artifact hash mismatch: ' + row['path'])
    actual = {
        p.relative_to(BUNDLE).as_posix() for p in BUNDLE.rglob('*')
        if p.is_file() and '__pycache__' not in p.parts and '.pytest_cache' not in p.parts
        and p.name != 'ARTIFACT_MANIFEST.json'
    }
    if actual != {row['path'] for row in manifest['artifacts']}:
        raise ValueError('artifact inventory differs from sealed allowlist')
    for row in manifest['implementation_source_binding']:
        base = REPO if row['root'] == 'repo' else BUNDLE
        if sha(contained(base, row['path'])) != row['sha256']:
            raise ValueError('implementation source mismatch: ' + row['path'])
    reviewed = json.loads((BUNDLE / 'ROOT_REVIEW.json').read_text())
    if sha(BUNDLE / 'PROTOCOL.json') != reviewed['protocol_sha256']:
        raise ValueError('reviewed protocol binding differs')
    preflight = json.loads((BUNDLE / 'root-launch-preflight-01.json').read_text())
    if preflight['physical_posts'] != 0 or preflight['fuse_created'] is not False:
        raise ValueError('zero-call preflight evidence changed')
    old = json.loads((BUNDLE / 'frozen-original/LIVE_RESULT.json').read_text())
    if old['status'] != 'FAIL_FROZEN_COMPLETION_CAP' or old['task_b_imported'] is not False:
        raise ValueError('old frozen failure was promoted')
    live = checker.check_directory(BUNDLE / 'live', require_live=True)
    expected = json.loads((BUNDLE / 'provenance/strict-independent-check.json').read_text())
    if not live['passed'] or live != expected:
        raise ValueError('live independent replay differs from sealed result')
    fixture = checker.check_directory(BUNDLE / 'offline-integration-final/run-01')
    denied = checker.check_directory(BUNDLE / 'offline-integration-final/run-01', require_live=True)
    if not fixture['passed'] or fixture['live_evidence'] or denied['passed']:
        raise ValueError('offline fixture promotion boundary failed')
    return {
        'schema': 'aodl.portable_sealed_replay.v1',
        'passed': True,
        'artifact_count': len(manifest['artifacts']) + 1,
        'implementation_source_files_checked': len(manifest['implementation_source_binding']),
        'physical_calls_in_this_replay': 0,
        'historical_physical_calls': live['physical_call_count'],
        'historical_input_tokens': live['input_tokens'],
        'historical_output_tokens': live['output_tokens'],
        'historical_provider_reported_cost_usd': live['provider_reported_cost_usd'],
        'zero_call_local_preflight_preserved': True,
        'frozen_task_a_status': old['status'],
        'task_b_import_allowed': False,
        'offline_fixture_live_promotion_rejected': True,
        'limitations': live['limitations'] + [
            'Sanitized derivatives remove account quota headers and local paths; retained authority payload digests refer to original local raw payloads.',
            'This bundle does not authenticate remote provider origin or establish performance savings.',
        ],
    }


def main() -> int:
    try:
        result = replay()
    except (OSError, ValueError, KeyError, TypeError) as exc:
        result = {'schema': 'aodl.portable_sealed_replay.v1', 'passed': False,
                  'physical_calls_in_this_replay': 0, 'error': str(exc)}
    print(json.dumps(result, indent=2, allow_nan=False))
    return 0 if result['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
