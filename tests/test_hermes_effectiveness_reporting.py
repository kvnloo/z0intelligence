"""Hermes-owned evidence accounting; UNIT fixtures are not native runs."""
from pathlib import Path
import importlib.util


def module():
    path = Path(__file__).parents[1] / 'reports/hermes/effectiveness.py'
    assert path.is_file(), 'Hermes source-backed reporting module is missing'
    spec = importlib.util.spec_from_file_location('hermes_effectiveness', path)
    assert spec is not None and spec.loader is not None
    obj = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(obj)
    return obj


def test_receipt_revisions_are_not_model_calls():
    report = module().audit_decisions([
        {'trace_id': 'unit-plan', 'provider': 'kerdoios_plan', 'route': 'model', 'latency_ms': 38},
        {'trace_id': 'unit-plan', 'provider': 'kerdoios_plan', 'route': 'model', 'latency_ms': 40},
    ], [])
    assert report['raw_rows'] == 2
    assert report['unique_traces'] == 1
    assert report['compiler_planner_traces'] == 1
    assert report['actual_model_invocation_count'] is None
    assert report['independent_gold_traces'] == 0


def test_missing_reasoning_usage_is_unknown_not_zero():
    report = module().audit_native([{'message': {'role': 'assistant', 'responseId': 'unit-r', 'usage': {'input': 4, 'output': 2}, 'stopReason': 'stop'}}])
    assert report['token_sums']['reasoningTokens'] is None
    assert report['token_coverage']['reasoningTokens'] == 0


def test_unknown_provider_counters_are_json_sortable():
    import json
    report = module().audit_decisions([{'trace_id': 'unit-a'}, {'trace_id': 'unit-b', 'provider': 'unit'}], [])
    assert report['providers'].get('<unknown>') == 1
    json.dumps(report, sort_keys=True)


def test_native_response_identity_deduplicates_calls():
    m = {'role': 'assistant', 'responseId': 'unit-response', 'provider': 'unit',
         'model': 'unit-model', 'stopReason': 'stop', 'content': [],
         'usage': {'input': 10, 'output': 2, 'cost': {'total': 0}}, 'duration': 4}
    report = module().audit_native([{'id': 'a', 'message': m}, {'id': 'copy', 'message': m}])
    assert report['completed_response_count'] == 1
    assert report['duplicate_response_rows'] == 1
    assert report['provider_reported_cost_usd'] == 0
    assert report['invoice_verified_cost_usd'] is None


def test_estimated_baseline_is_not_measured_savings():
    report = module().audit_decisions([
        {'trace_id': 'unit-a', 'estimated_frontier_tokens_avoided': 50,
         'baseline_input_tokens': 70, 'input_tokens': 20, 'actual_tokens_saved': 50}
    ], [])
    assert report.get('estimated_tokens_avoided') == 50
    assert report['measured_paired_savings'] is None


def test_shadow_selection_is_not_execution_or_attested_model_use():
    report = module().audit_shadow([
        {'deterministic_solution': 'read', 'shadow': []},
        {'shadow': [{'selected_action': None, 'parse_error': 'transport_error: refused'}]},
        {'shadow': [{'selected_action': 'read', 'parse_error': None}], 'executed_action': None},
    ])
    assert report['compiler_only_rows'] == 1
    assert report['failed_backend_attempts'] == 1
    assert report['selection_shaped_returns'] == 1
    assert report['observed_executions'] == 0
    assert report['independently_attested_model_invocations'] is None
