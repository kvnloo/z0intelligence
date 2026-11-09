"""Shared harness for the HC-2 memory-search benchmark and its equivalence tests.

Builds an ephemeral AgentsView db and EventLog ledger from the frozen fixtures in ``fixtures/``,
then runs ``z0int.memory.surface.search`` for each of the 50 frozen queries and reports latency and
a hash of the ordered result event ids (the thing equivalence must preserve across the HC-2 change).
"""
from __future__ import annotations

import hashlib
import json
import sys
import time
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parent.parent
FIXTURES = HERE / 'fixtures'

for p in (str(REPO_ROOT / 'src'), str(REPO_ROOT / 'tests')):
    if p not in sys.path:
        sys.path.insert(0, p)

from memory_fixture import build_av_db  # noqa: E402 - path set up above
from z0int.memory import surface as ms  # noqa: E402
from z0int.memory.event_log import EventLog  # noqa: E402

PROJECT = 'z0'


def load_fixtures() -> dict[str, Any]:
    return {name: json.loads((FIXTURES / f'{name}.json').read_text(encoding='utf-8'))
            for name in ('corpus', 'queries', 'event_payloads', 'av_messages')}


def build_env(root: Path, fixtures: dict[str, Any] | None = None) -> dict[str, Path]:
    """Materialize the fixtures into a fresh AgentsView db and EventLog ledger under ``root``."""
    fixtures = fixtures or load_fixtures()
    av_db = root / 'av' / 'sessions.db'
    sessions = [(m['session_id'], m['agent'], m['project'], m['cwd'], [tuple(pair) for pair in m['messages']])
                for m in fixtures['av_messages']]
    build_av_db(av_db, sessions=sessions)
    ledger_root = root / 'memory'
    log = EventLog(ledger_root)
    for spec in fixtures['event_payloads']:
        log.append(spec['event_type'], spec['payload'], source=spec['source'])
    return {'av_db': av_db, 'ledger_root': ledger_root}


def _result_hash(res: dict[str, Any]) -> str:
    ids = [e['event_uid'] for e in res['evidence']]
    return hashlib.sha256(json.dumps(ids, separators=(',', ':')).encode('utf-8')).hexdigest()


def run_queries(av_db: Path, ledger_root: Path, queries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows = []
    for q in queries:
        policy = ms.ScopePolicy(scope=ms.MemoryScope(user=ms.DEFAULT_USER, project=q['project'])
                                 if q.get('project') else None)
        t0 = time.perf_counter()
        res = ms.search(q['query'], policy, layers=('temporal', 'lexical'), required=(), av_db=av_db,
                        ledger_root=ledger_root, config={})
        latency_ms = (time.perf_counter() - t0) * 1000.0
        rows.append({'query': q['query'], 'latency_ms': latency_ms, 'hash': _result_hash(res),
                     'event_uids': [e['event_uid'] for e in res['evidence']]})
    return rows


def percentile(values: list[float], pct: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    k = (len(ordered) - 1) * pct
    f, c = int(k), min(int(k) + 1, len(ordered) - 1)
    if f == c:
        return ordered[f]
    return ordered[f] + (ordered[c] - ordered[f]) * (k - f)


def summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    latencies = [r['latency_ms'] for r in rows]
    return {
        'n': len(rows),
        'p50_ms': round(percentile(latencies, 0.50), 3),
        'p90_ms': round(percentile(latencies, 0.90), 3),
        'max_ms': round(max(latencies), 3),
        'combined_hash': hashlib.sha256(
            json.dumps([r['hash'] for r in rows], separators=(',', ':')).encode('utf-8')).hexdigest(),
    }
