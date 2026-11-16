"""HC-2 equivalence: the memory-search speed fixes (benchmarks/memory_search/) must not change results.

``fixtures/golden.json`` was captured by ``capture_golden.py`` against the unmodified
``src/z0int/memory/{event_log,surface}.py``, before the scrub single-pass and temporal mtime/size
cache changes. This test reruns the same 50 frozen queries against the current code and asserts the
ordered event_uid list for every query is unchanged.
"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

import pytest

BENCH_DIR = Path(__file__).resolve().parents[1] / 'benchmarks' / 'memory_search'
if str(BENCH_DIR) not in sys.path:
    sys.path.insert(0, str(BENCH_DIR))

import bench_lib  # noqa: E402

GOLDEN = json.loads((BENCH_DIR / 'fixtures' / 'golden.json').read_text(encoding='utf-8'))


@pytest.fixture(scope='module')
def rows():
    fixtures = bench_lib.load_fixtures()
    with tempfile.TemporaryDirectory(prefix='z0int-hc2-equiv-') as tmp:
        paths = bench_lib.build_env(Path(tmp), fixtures)
        return bench_lib.run_queries(paths['av_db'], paths['ledger_root'], fixtures['queries'])


def test_all_frozen_queries_return_identical_ordered_event_ids(rows):
    assert len(rows) == len(GOLDEN) == 50
    for row, golden in zip(rows, GOLDEN):
        assert row['query'] == golden['query']
        assert row['event_uids'] == golden['event_uids'], f"result order changed for query {row['query']!r}"
        assert row['hash'] == golden['hash']
