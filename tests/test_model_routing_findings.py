"""Owned synthetic fixtures only. No operational ledgers or provider calls."""
import hashlib
import importlib.util
import json
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from z0int.policy.findings import Finding, append_findings, canonical
from z0int.policy.ingest import wilson


MODULE_PATH = Path(__file__).resolve().parents[1] / 'operations/model-routing/generate_catalog.py'
SPEC = importlib.util.spec_from_file_location('model_routing_catalog_findings', MODULE_PATH)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)
NOW = datetime.fromisoformat('2026-10-24T12:00:00+00:00').timestamp()


def acceptance_finding(tmp_path, *, subject=None, arm=None, metric=None, **changes):
    fixture = tmp_path / 'synthetic_fixture.json'
    fixture.write_text('{"fixture":true}\n')
    low, high = wilson(8, 10)
    fields = dict(
        ts='2026-10-24T10:00:00Z',
        source={'kind': 'manual', 'path': str(fixture),
                'sha256': hashlib.sha256(fixture.read_bytes()).hexdigest(),
                'run_id': 'fixture-run', 'synthetic_fixture': True},
        subject={'family': 'model.routing.acceptance', 'job': 'ShadowEvals',
                 'task': 'coding.bugfix'},
        arm={'kind': 'frontier', 'provider': 'vercel', 'model': 'fixture-model'},
        metric={'name': 'acceptance_rate', 'value': .8, 'n': 10, 'accepted': 8,
                'ci_low': low, 'ci_high': high, 'ci_method': 'wilson95',
                'higher_is_better': True, 'acceptance_kind': 'rubric_draft_quality',
                'promotion_eligible': False},
        evidence_class='measured', evidence_kind='proxy',
        scope_note='Synthetic fixture tests draft quality, NOT product execution.',
        expires_at='2026-10-25T10:00:00Z', synthetic=False)
    for name, patch in (('subject', subject), ('arm', arm), ('metric', metric)):
        fields[name].update(patch or {})
    fields.update(changes)
    return Finding(**fields)


def import_catalog(path):
    catalog = MODULE.Catalog(NOW)
    catalog.findings(SimpleNamespace(findings=path))
    return catalog


def test_real_canonical_chain_imports_only_exact_task_prior(tmp_path):
    path = tmp_path / 'findings.jsonl'
    emitted = append_findings([acceptance_finding(tmp_path)], path)
    assert path.read_bytes() == canonical(emitted[0]) + b'\n'
    assert emitted[0].get('synthetic', False) is False
    before = path.read_bytes()
    path.with_suffix('.lock').unlink()
    catalog = import_catalog(path)
    assert path.read_bytes() == before
    assert not path.with_suffix('.lock').exists()
    tasks = catalog.rows[('vercel', 'fixture-model')]['task_classes']
    assert set(tasks) == {'coding.bugfix'}
    prior = tasks['coding.bugfix']['p_accept']
    assert prior['value'] == .8
    assert prior['basis'] == 'measured_rubric_acceptance_not_product_success'
    assert prior['finding_id'] == emitted[0]['id']
    assert prior['observed_at'] == emitted[0]['ts']
    assert prior['expires_at'] == emitted[0]['expires_at']
    assert prior['n'] == 10 and prior['accepted'] == 8
    assert prior['promotion_eligible'] is False
    assert prior['scope_note'] == emitted[0]['scope_note']
    assert catalog.sources[prior['source']]['original_source_sha256'] == emitted[0]['source']['sha256']


@pytest.mark.parametrize('attack', ['content', 'chain', 'noncanonical', 'truncated'])
def test_invalid_canonical_chain_imports_no_prefix(tmp_path, attack):
    path = tmp_path / 'findings.jsonl'
    rows = append_findings([
        acceptance_finding(tmp_path),
        acceptance_finding(tmp_path, ts='2026-10-24T11:00:00Z')], path)
    if attack == 'content':
        rows[1]['metric']['value'] = .7
    elif attack == 'chain':
        rows[1]['prev'] = 'f' * 64
    raw = b''.join(canonical(row) + b'\n' for row in rows)
    if attack == 'noncanonical':
        raw = json.dumps(rows[0]).encode() + b'\n' + canonical(rows[1]) + b'\n'
    elif attack == 'truncated':
        raw = raw[:-1]
    path.write_bytes(raw)
    catalog = import_catalog(path)
    assert catalog.rows == {}
    assert any(item['issue'] == 'invalid_canonical_findings_chain_no_acceptance_imported'
               for item in catalog.issues)
    assert path.read_bytes() == raw


@pytest.mark.parametrize('changes', [
    {'synthetic': True},
    {'expires_at': '2026-10-24T11:00:00Z'},
    {'ts': '2026-10-24T13:00:00Z'},
    {'subject': {'family': 'agent.outcome'}},
    {'subject': {'job': 'generic-ledger'}},
    {'subject': {'task': '*'}},
    {'subject': {'task': 'unknown'}},
    {'arm': {'kind': 'llm'}},
    {'arm': {'provider': 'unapproved-provider'}},
    {'arm': {'model': '*'}},
    {'evidence_class': 'published'},
    {'metric': {'acceptance_kind': 'generic_proxy'}},
    {'metric': {'acceptance_kind': 'verified_product_outcome'}},
    {'metric': {'name': 'task_success'}},
    {'metric': {'promotion_eligible': True}},
    {'metric': {'higher_is_better': False}},
    {'metric': {'accepted': 11}},
    {'metric': {'accepted': 8.0}},
    {'metric': {'accepted': 7}},
    {'metric': {'ci_low': .1}},
    {'metric': {'ci_method': 'bootstrap95'}},
])
def test_expired_wrong_family_generic_proxy_and_invalid_counts_are_ignored(tmp_path, changes):
    path = tmp_path / 'findings.jsonl'
    append_findings([acceptance_finding(tmp_path, **changes)], path)
    assert import_catalog(path).rows == {}


def test_latest_fresh_prior_retains_history_without_task_or_provider_fallback(tmp_path):
    path = tmp_path / 'findings.jsonl'
    low, high = wilson(9, 10)
    append_findings([
        acceptance_finding(tmp_path),
        acceptance_finding(tmp_path, ts='2026-10-24T11:00:00Z',
                           metric={'accepted': 9, 'value': .9, 'ci_low': low, 'ci_high': high}),
        acceptance_finding(tmp_path, subject={'task': 'research.sources'}),
        acceptance_finding(tmp_path, arm={'provider': 'nous'})], path)
    catalog = import_catalog(path)
    tasks = catalog.rows[('vercel', 'fixture-model')]['task_classes']
    assert tasks['coding.bugfix']['p_accept']['value'] == .9
    assert len(tasks['coding.bugfix']['acceptance_findings']) == 2
    assert tasks['research.sources']['p_accept']['value'] == .8
    assert 'coding' not in tasks
    assert catalog.rows[('nous', 'fixture-model')]['task_classes']['coding.bugfix']['p_accept']['value'] == .8


def product_finding(tmp_path, **changes):
    receipt = tmp_path / 'product-receipt.json'
    receipt.write_text(json.dumps({
        'schema': 'z0.product_check.v1', 'verified': True, 'trace_id': 'fixture-run',
        'id': 'fixture-receipt', 'verifier': 'fixture-verifier', 'success': True,
        'observed_target': {'kind': 'api', 'id': 'fixture-target', 'observed': True,
                            'state': {'fixture_result': 'accepted'}}}))
    low, high = wilson(1, 1)
    fields = dict(evidence_kind='product_outcome',
        verifier_receipt={'path': str(receipt), 'sha256': hashlib.sha256(receipt.read_bytes()).hexdigest()},
        metric={'acceptance_kind': 'verified_product_outcome', 'value': 1., 'n': 1,
                'accepted': 1, 'ci_low': low, 'ci_high': high})
    fields.update(changes)
    return acceptance_finding(tmp_path, **fields)


def test_product_acceptance_requires_rechecked_bound_receipt(tmp_path):
    path = tmp_path / 'findings.jsonl'
    append_findings([product_finding(tmp_path)], path)
    prior = import_catalog(path).rows[('vercel', 'fixture-model')]['task_classes']['coding.bugfix']['p_accept']
    assert prior['basis'] == 'verified_product_outcome_acceptance'
    assert prior['verifier_receipt']['receipt_id'] == 'fixture-receipt'
    assert 'observed_target' not in prior['verifier_receipt']
    (tmp_path / 'product-receipt.json').write_text('{}')
    assert import_catalog(path).rows == {}


def test_one_receipt_does_not_prove_aggregate_product_acceptance(tmp_path):
    path = tmp_path / 'findings.jsonl'
    low, high = wilson(10, 10)
    append_findings([product_finding(tmp_path, metric={
        'acceptance_kind': 'verified_product_outcome', 'value': 1., 'n': 10,
        'accepted': 10, 'ci_low': low, 'ci_high': high})], path)
    assert import_catalog(path).rows == {}
