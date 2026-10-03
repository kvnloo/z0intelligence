"""`z0int memory bench` (z0int#22 measurement): per-query cold/warm latency, raw reads, tokens and hit rate."""
from __future__ import annotations

import json

import pytest

from memory_fixture import build_av_db
from z0int.memory import surface as ms
from z0int.memory import cli

QUERIES = [{'query': 'quokka gateway', 'project': 'z0', 'expect': 'agentsview:h1#'},
           {'query': 'quokka bench', 'project': 'z0', 'expect': 'agentsview:x1#'},
           {'query': 'zebra nothing matches', 'project': 'z0', 'expect': 'agentsview:'}]


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv('Z0INT_HOME', str(tmp_path / 'z0'))
    monkeypatch.setenv('AGENTSVIEW_DATA_DIR', str(tmp_path / 'av'))
    build_av_db(tmp_path / 'av' / 'sessions.db')
    return tmp_path


def test_bench_reports_per_query_measurements(env):
    report = ms.bench(QUERIES, config={})
    rows = report['queries']
    assert [r['query'] for r in rows] == [q['query'] for q in QUERIES]
    for r in rows:
        assert r['cold_ms'] > 0 and r['warm_ms'] >= 0 and r['raw_reads'] >= 0 and r['tokens'] > 0
    assert [r['hit'] for r in rows] == [True, True, False]
    assert rows[0]['warm_cache'] == 'hit' and rows[0]['raw_reads'] > 0
    assert report['hit_rate'] == pytest.approx(2 / 3)


def test_bench_cli_reads_a_query_file(env, capsys):
    qf = env / 'queries.json'
    qf.write_text(json.dumps(QUERIES))
    assert cli.main(['bench', '--queries', str(qf), '--json']) == 0
    out = json.loads(capsys.readouterr().out)
    assert len(out['queries']) == 3 and 'hit_rate' in out
