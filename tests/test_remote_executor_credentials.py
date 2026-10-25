import io
import json
from unittest.mock import Mock

import pytest

from z0int import remote_executor as remote
from z0int import worker_routing


@pytest.mark.parametrize('available', [True, False])
def test_subscription_readiness_uses_credential_helper(monkeypatch, available):
    config = {'auth': 'omp-oauth', 'api_key_env': None}
    monkeypatch.setenv('Z0INT_EXECUTOR_PROVIDERS', 'openai-codex')
    monkeypatch.setenv('Z0INT_AUTHORITY_URL', 'http://authority.invalid')
    monkeypatch.setattr(worker_routing, 'configuration', lambda: (
        {'free_only': False, 'defaults': {'openai-codex': 'gpt-6-luna'}},
        {'openai-codex': config}))
    credential = Mock(return_value=available)
    monkeypatch.setattr(remote, 'credential_available', credential)
    state = {'authority_protocol_version': 3, 'aodl_admission_ready': True,
             'providers': {'openai-codex': {'available': True}}}
    monkeypatch.setattr(remote.urllib.request, 'urlopen', lambda *a, **k: io.BytesIO(json.dumps(state).encode()))
    handler = Mock(path='/readyz')
    remote.Handler.do_GET(handler)
    assert handler.reply.call_args.args[0] == (200 if available else 503)
    credential.assert_called_once_with('openai-codex', config)


def test_subscription_execute_checks_credentials_not_null_env(monkeypatch):
    config = {'auth': 'omp-oauth', 'api_key_env': None}
    args = {'provider': 'openai-codex', 'harness': 'test', 'trace_id': 'test'}
    monkeypatch.setenv('Z0INT_EXECUTOR_PROVIDERS', 'openai-codex')
    monkeypatch.setattr(remote, 'request', lambda op, payload: {'claimed': True} if op == 'claim' else payload['result'])
    monkeypatch.setattr(remote, 'validate_remote', lambda args: ({}, {}, {'openai-codex': config}, {}))
    credential = Mock(return_value=True)
    monkeypatch.setattr(remote, 'credential_available', credential)
    monkeypatch.setattr(remote, 'execute_plan', lambda *a, **k: {'ok': True})
    assert remote.execute(args)['ok'] is True
    credential.assert_called_once_with('openai-codex', config)


@pytest.mark.parametrize('authorized', [True, False])
def test_authority_requires_explicit_subscription_route(monkeypatch, authorized):
    from z0int import dispatch_authority
    config = {'auth': 'omp-oauth', 'api_key_env': None}
    policy = {'paid_canary': {'routes': [
        {'provider': 'openai-codex', 'model': 'gpt-6-luna'}] if authorized else []}}
    monkeypatch.setattr(worker_routing, 'configuration', lambda: (policy, {'openai-codex': config}))
    monkeypatch.setattr(worker_routing, 'explicit_plan', lambda *a: {})
    request = dict(harness='omp', trace_id='fixture', function='cheap_bounded_worker',
                   parent_agent='fixture', task='Public fixture', provider='openai-codex',
                   model='gpt-6-luna', reason='fixture', max_tokens=20)
    if authorized:
        assert dispatch_authority.validate_remote(request)[1] is policy
    else:
        with pytest.raises(ValueError, match='explicitly authorized'):
            dispatch_authority.validate_remote(request)
