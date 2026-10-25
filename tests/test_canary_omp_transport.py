from unittest.mock import Mock
import pytest

from z0int import canary_budget, shadow_evals_omp, worker_routing


@pytest.mark.parametrize('failure', [None, {'code': 'omp_provider_error', 'http_status': 429}])
@pytest.mark.parametrize('cache_write', [0, 3379])
@pytest.mark.parametrize('cache_read', [0, 100])
def test_subscription_uses_shared_omp_and_preserves_actual_cost_unknown(monkeypatch, tmp_path, failure, cache_write, cache_read):
    monkeypatch.setenv('Z0INT_HOME', str(tmp_path))
    monkeypatch.setattr(canary_budget, 'reserve', lambda *a: 'reservation')
    settle = Mock(return_value={'status': 'pending'})
    monkeypatch.setattr(canary_budget, 'settle', settle)
    completion = dict(content='CANONICAL_OK', input_tokens=10, output_tokens=3,
                      cached_input_tokens=cache_read, cost=None, list_price_usd=.003, error=failure,
                      cache_write_input_tokens=cache_write, total_tokens=13 + cache_write + cache_read,
                      reported_model='gpt-6-luna', response_id='fixture',
                      finish_reason='stop', tool_call=False,
                      output_limit_enforced=False, quota_headers={}, receipt_path='/private/fixture')
    execute = Mock(return_value=completion)
    monkeypatch.setattr(shadow_evals_omp, 'execute', execute)
    admission = Mock()
    admission.acquire.return_value = dict(admitted=True, token='fixture', inflight_at_admission=1, cap=1, capped=False)
    policy = dict(free_only=False, baseline={}, policy_revision='fixture', timeout_s=120)
    result = worker_routing.execute_attempt(
        dict(parent_agent='fixture', task='Return CANONICAL_OK', max_tokens=20),
        dict(provider='openai-codex', model='gpt-6-luna'),
        dict(auth='omp-oauth', api_key_env=None), policy,
        dict(source='fixture', category='fixture', reason='fixture'), 'route', 0,
        receipt_sink=lambda row: row.to_dict(), admission=admission)
    assert result['ok'] is (failure is None)
    extra = result['receipt']['extra']
    assert extra['subscription_transport'] == 'omp-headless'
    if failure is None:
        assert extra['provider_reported_cost_usd'] is None
    else:
        assert extra['subscription_failure_reason'] == 'omp_provider_error'
        assert result['receipt']['input_tokens'] == 10 + cache_write + cache_read
        assert settle.call_args.kwargs['usage_present'] is True
        assert settle.call_args.kwargs['http_status'] == 429
    assert extra['catalog_estimated_cost_usd'] == .003
    assert extra['output_limit_enforced'] is False
    assert result['receipt']['input_tokens'] == 10 + cache_write + cache_read
    assert extra['subscription_usage_components']['cache_write_input_tokens'] == cache_write
    assert settle.call_args.kwargs['actual_cost_usd'] is None
    execute.assert_called_once()
