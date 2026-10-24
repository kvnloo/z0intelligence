"""Synthetic routing-quota JSONL only. Never probe a live provider."""
import importlib.util
import json
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace


MODULE_PATH = Path(__file__).resolve().parents[1] / 'operations/model-routing/generate_catalog.py'
SPEC = importlib.util.spec_from_file_location('model_routing_quota_catalog', MODULE_PATH)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)
NOW = datetime.fromisoformat('2026-10-24T12:00:00+00:00').timestamp()


def event(**changes):
    row = {'kind': 'routing_quota', 'provider': 'opencode-go',
           'model': 'deepseek-v4.1-flash', 'trace_id': 'fixture-attempt',
           'observed_at': '2026-10-24T10:00:00Z', 'http_status': 200,
           'input_tokens': 10, 'output_tokens': 5, 'quota_group': 'fixture-group',
           'quota_headers': {}, 'scope_note': 'Fixture interval, not complete daily coverage'}
    row.update(changes)
    return row


def catalog_from_events(tmp_path, rows):
    path = tmp_path / 'observed.jsonl'
    path.write_text(''.join(json.dumps(row) + '\n' for row in rows))
    before = path.read_bytes()
    catalog = MODULE.Catalog(NOW)
    catalog.quota_observations(SimpleNamespace(observed=path))
    assert path.read_bytes() == before
    return catalog, path


def test_no_actual_go_deepseek_429_remains_pending(tmp_path):
    catalog, _ = catalog_from_events(tmp_path, [
        event(http_status=200),
        event(provider='opencode-zen', http_status=429),
        event(provider='opencode', http_status=429),
        event(model='other-model', http_status=429),
        event(http_status='429'),
        event(http_status=429, synthetic=True),
        event(http_status=429, observed_at='2026-10-24T13:00:00Z'),
        event(http_status=429, observed_at='2026-10-24T10:00:00'),
    ])
    constraint = catalog.constraints['opencode-go']
    assert constraint['pending_measurement']['status'] == 'awaiting_first_actual_go_deepseek_429'
    assert 'observed_first_429' not in constraint
    assert catalog.rows == {}


def test_first_429_uses_only_deduplicated_known_group_recorded_interval(tmp_path):
    first = event(trace_id='first', input_tokens=10, output_tokens=5)
    duplicate = dict(first)
    shared_model = event(trace_id='shared-model', model='other-model', input_tokens=20,
                         output_tokens=8, observed_at='2026-10-24T10:30:00Z')
    cap = event(trace_id='cap', http_status=429, input_tokens=0, output_tokens=0,
                observed_at='2026-10-24T11:00:00Z', quota_headers={
                    'x-ratelimit-limit-requests-day': 100,
                    'x-ratelimit-remaining-requests-day': 0,
                    'x-ratelimit-reset-requests-day': '2026-10-25T00:00:00Z',
                    'x-ratelimit-reset-tokens': '2m59.56s',
                    'authorization': 'fixture-not-a-real-secret', 'retry-after': 30})
    catalog, path = catalog_from_events(tmp_path, [
        cap, first, duplicate, shared_model,
        event(trace_id='different-group', quota_group='another-group'),
        event(trace_id='later', observed_at='2026-10-24T11:30:00Z', http_status=429),
    ])
    observed = catalog.constraints['opencode-go']['observed_first_429']
    assert observed['trace_id'] == 'cap'
    assert observed['http_status']['value'] == 429
    assert observed['observed_at'] == '2026-10-24T11:00:00+00:00'
    assert catalog.sources[observed['source']]['citation'] == f'{path}:1'
    assert observed['request_day_reset_at']['value'] == '2026-10-25T00:00:00+00:00'
    assert 'authorization' not in observed['quota_headers']['value']
    assert observed['quota_headers']['value']['x-ratelimit-reset-tokens'] == '2m59.56s'
    interval = observed['recorded_interval']
    assert interval['requests']['value'] == 3
    assert interval['input_tokens']['value'] == 30
    assert interval['output_tokens']['value'] == 13
    assert interval['tokens']['value'] == 43
    assert interval['complete_daily_coverage'] is False
    assert observed['daily_cap']['value'] is None
    assert catalog.rows == {}


def test_unknown_group_does_not_invent_request_or_token_counts(tmp_path):
    catalog, _ = catalog_from_events(tmp_path, [
        event(trace_id='earlier'), event(trace_id='cap', http_status=429, quota_group=None)])
    observed = catalog.constraints['opencode-go']['observed_first_429']
    for key in ('requests', 'input_tokens', 'output_tokens', 'tokens'):
        assert observed['recorded_interval'][key]['value'] is None
    assert observed['recorded_interval']['started_at'] is None
    assert observed['request_day_reset_at']['value'] is None


def test_missing_token_fields_and_ambiguous_reset_remain_unknown(tmp_path):
    catalog, _ = catalog_from_events(tmp_path, [
        event(trace_id='earlier', input_tokens=None),
        event(trace_id='cap', http_status=429, quota_headers={
            'x-ratelimit-reset-requests': '2026-10-25T00:00:00Z',
            'x-ratelimit-reset-requests-day': 3600,
            'retry-after': 3600})])
    observed = catalog.constraints['opencode-go']['observed_first_429']
    assert observed['recorded_interval']['requests']['value'] == 2
    assert observed['recorded_interval']['input_tokens']['value'] is None
    assert observed['recorded_interval']['output_tokens']['value'] == 10
    assert observed['recorded_interval']['tokens']['value'] is None
    assert observed['request_day_reset_at']['value'] is None


def test_routing_quota_records_never_become_capability_observations(tmp_path):
    catalog, path = catalog_from_events(tmp_path, [
        event(http_status=429),
        {'provider': 'opencode-go', 'model': 'deepseek-v4.1-flash', 'task_type': 'coding',
         'completed': True, 'actual_cost': 0.0, 'verified_success': True},
    ])
    catalog.outcomes(SimpleNamespace(observed=path, outcomes_db=tmp_path / 'missing.sqlite'))
    tasks = catalog.rows[('opencode-go', 'deepseek-v4.1-flash')]['task_classes']
    assert set(tasks) == {'coding'}
    assert tasks['coding']['kerdoios']['samples']['value'] == 1
    assert tasks['coding']['p_accept']['value'] is None
