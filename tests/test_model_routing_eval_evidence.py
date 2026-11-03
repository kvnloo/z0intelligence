"""z0evals free-taskfit receipts and the Hermes Index prior reach the O14 catalog.

Chain under test (no provider calls, no operational ledgers):
  z0eval.result.v1 (z0evals studies/free-taskfit-v0 certify.py)
    -> z0int policy ingest (source kind z0eval_result) -> canonical findings chain
    -> generate_catalog.findings -> models[].task_classes[taskfit.*].p_accept
                                    models[].performance.{ttft_p50_ms,tokens_per_second}
  Hermes Index benchmark rows (z0evals studies/hermes-index-prior-v0)
    -> generate_catalog.benchmarks -> models[].benchmarks only, never p_accept

The receipt fixture is a trimmed copy of a real certified pilot receipt; only the
row-file hashes and acceptance counts it carries are asserted.
"""
import importlib.util
import json
import shutil
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

from z0int.policy.findings import ledger_path, read_findings
from z0int.policy.ingest import ingest_sources

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / 'tests/fixtures/z0evals'
SPEC = importlib.util.spec_from_file_location('routing_catalog_eval_evidence',
                                              ROOT / 'operations/model-routing/generate_catalog.py')
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def _receipt(tmp_path):
    target = tmp_path / 'free-taskfit-result.json'
    shutil.copy(FIXTURES / 'free-taskfit-result.json', target)
    return target, json.loads(target.read_text())


def _now(receipt):
    return datetime.fromisoformat(receipt['ts'].replace('Z', '+00:00')).timestamp() + 3600


def _ingest(tmp_path):
    path, receipt = _receipt(tmp_path)
    home = tmp_path / 'z0int-home'
    report = ingest_sources([{'kind': 'z0eval_result', 'path': str(path)}], home)
    return home, receipt, report


def test_certified_receipt_becomes_exact_task_class_p_accept(tmp_path):
    home, receipt, report = _ingest(tmp_path)
    assert report['sources'][0]['status'] == 'ingested', report
    ledger = ledger_path(home)
    rows = read_findings(ledger)
    assert len(rows) == len(receipt['findings'])
    assert all(r['metric'].get('promotion_eligible') is not True for r in rows)
    catalog = MODULE.Catalog(_now(receipt))
    catalog.findings(SimpleNamespace(findings=ledger))
    expected = [f for f in receipt['findings'] if f['metric']['name'] == 'acceptance_rate'
                and f['arm']['provider'] in MODULE.PROVIDERS]
    assert expected, 'fixture must carry in-scope acceptance findings'
    for finding in expected:
        row = catalog.rows[(finding['arm']['provider'], finding['arm']['model'])]
        prior = row['task_classes'][finding['subject']['task']]['p_accept']
        assert prior['value'] == finding['metric']['value']
        assert (prior['n'], prior['accepted']) == (finding['metric']['n'], finding['metric']['accepted'])
        assert prior['basis'] == 'measured_rubric_acceptance_not_product_success'
        assert prior['promotion_eligible'] is False
        # No task-class fallback: other task classes stay unknown.
        assert 'coding' not in row['task_classes']


def test_out_of_scope_provider_is_dropped_not_relabelled(tmp_path):
    home, receipt, _ = _ingest(tmp_path)
    catalog = MODULE.Catalog(_now(receipt))
    catalog.findings(SimpleNamespace(findings=ledger_path(home)))
    nim = [f for f in receipt['findings'] if f['arm']['provider'] not in MODULE.PROVIDERS]
    for finding in nim:
        assert (finding['arm']['provider'], finding['arm']['model']) not in catalog.rows
        assert not any(key[1] == finding['arm']['model'] for key in catalog.rows)


def test_latency_findings_fill_unknown_performance_only(tmp_path):
    home, receipt, _ = _ingest(tmp_path)
    perf = [f for f in receipt['findings'] if f['subject']['family'] == 'model.routing.performance'
            and f['arm']['provider'] in MODULE.PROVIDERS]
    assert len(perf) >= 2
    catalog = MODULE.Catalog(_now(receipt))
    kept = perf[0]
    kept_key = (kept['arm']['provider'], kept['arm']['model'], kept['metric']['name'])
    catalog.row(*kept_key[:2])['performance'][kept_key[2]] = MODULE.metric(
        123.0, 'ms', 'omp:retained', '2026-10-24T00:00:00+00:00', 'linear_quantile_of_recorded_ttft_field')
    catalog.findings(SimpleNamespace(findings=ledger_path(home)))
    assert catalog.rows[kept_key[:2]]['performance'][kept_key[2]]['value'] == 123.0
    for finding in perf[1:]:
        value = catalog.rows[(finding['arm']['provider'], finding['arm']['model'])]['performance'][finding['metric']['name']]
        assert value['value'] == finding['metric']['value']
        assert value['basis'] == 'z0eval_certified_battery_median'
    scope = [i for i in catalog.issues if i.get('issue') == 'performance_finding_provider_not_in_catalog_scope']
    assert {i['provider'] for i in scope} == {f['arm']['provider'] for f in receipt['findings']
                                              if f['arm']['provider'] not in MODULE.PROVIDERS}


def test_expired_receipt_contributes_nothing(tmp_path):
    home, receipt, _ = _ingest(tmp_path)
    late = datetime.fromisoformat(receipt['findings'][0]['expires_at'].replace('Z', '+00:00')).timestamp() + 1
    catalog = MODULE.Catalog(late)
    catalog.findings(SimpleNamespace(findings=ledger_path(home)))
    assert not any(row['task_classes'] for row in catalog.rows.values())
    assert not any(v.get('basis') == 'z0eval_certified_battery_median'
                   for row in catalog.rows.values() for v in row['performance'].values())


def test_hermes_index_rows_are_benchmarks_never_p_accept(tmp_path):
    rows = FIXTURES / 'hermes-index-benchmark-rows.json'
    data = json.loads(rows.read_text())
    catalog = MODULE.Catalog(datetime.fromisoformat('2026-11-03T21:00:00+00:00').timestamp())
    catalog.benchmarks(SimpleNamespace(benchmark=[rows], provider_benchmark=tmp_path / 'absent.json',
                                       capability_matrix=tmp_path / 'absent2.json'))
    opus = catalog.rows[('anthropic', 'claude-opus-5-5')]
    names = {b['benchmark'] for b in opus['benchmarks']}
    assert 'Hermes Index (Hermes Agent harness, pass@1, source-reported)' in names
    assert 'Hermes Index cost per task [provisional]' in names
    index = next(b for b in opus['benchmarks'] if b['task_class'] == 'external.hermes_index.mean'
                 and b['score']['unit'] == 'percent')
    assert index['score']['basis'] == 'external_benchmark'
    assert index['original_source'].startswith('https://portal.nousresearch.com/bench#captured=')
    assert index['floor']['value'] is None
    assert not any(row['task_classes'] for row in catalog.rows.values())
    assert not [i for i in catalog.issues if i.get('issue') == 'benchmark_missing_identity_score_or_provenance'
                and data['rows']]
