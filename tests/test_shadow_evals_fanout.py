"""Synthetic-only contracts; parent runs these on its verification host."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import sqlite3
import time
import threading

import pytest

from z0int import shadow_evals_fanout as fanout


@pytest.fixture
def config(tmp_path):
    now = time.time()
    models, routes, metrics = [], {}, {}
    for index, tier in enumerate(('local', 'local', 'fast', 'subscription')):
        model = f'qwen-synthetic-{index}'
        identity = 'local:' + model
        models.append({'provider': 'local', 'model': model,
                       'privacy': {'allowed': ['confidential', 'local_only']}})
        routes[identity] = {'kind': 'local_http', 'url': 'http://127.0.0.1:11530/v1/chat/completions',
                           'tier': tier, 'context_window': 8192, 'compute': 'gpu',
                           'entitlement': {'source': 'synthetic owner', 'expires_at': now + 3600,
                                           'marginal_cost_usd': 0}}
        metrics[identity] = {'ttft_seconds': index + 1, 'tokens_per_second': 100,
            'burn': .001, 'remaining': 1000, 'time_to_reset_hours': 1, 'p_accept': .5,
            'quota_unit': 'synthetic units', 'quota_group': 'pool-' + str(index),
            'source': 'synthetic verified fixture', 'timestamp': now, 'task_class': 'intent.shadow'}
    catalog = tmp_path / 'catalog.json'
    catalog.write_text(json.dumps({'models': models}))
    return {'root': str(tmp_path / 'private'), 'catalog_path': str(catalog),
            'routes': routes, 'metrics': metrics, 'count': 3, 'estimated_input_tokens': 4000,
            'max_tokens': 1000, 'max_pace': 100, 'max_cost_usd': 0, 'privacy': 'confidential',
            'authorization': {'expires_at': now + 3600}, 'live_enabled': True}


@pytest.fixture
def routing(config):
    root = Path(os.environ.get('KERDOIOS_TEST_ROOT', fanout.DEFAULT_KERDOIOS))
    if not (root / 'kerdoios/routing_objective.py').exists():
        pytest.skip('Parent must stage real Kerdoios source and set KERDOIOS_TEST_ROOT')
    config['kerdoios_root'] = str(root)
    return config


def test_bundle_reuses_retriever_and_numbers_exact_lines(config, tmp_path, monkeypatch):
    calls = []
    class Retriever:
        def __init__(self, **kwargs):
            calls.append(kwargs)
        async def retrieve(self, ask, recent):
            calls.append((ask, recent))
            return {'records': [
                {'source': 'state_packet', 'id': 'packet', 'text': 'alpha\n\nbeta\n'},
                {'source': 'hermes', 'id': 'stable', 'text': 'memory evidence', 'evidence_kind': 'claimed'}],
                'sources': [{'source': 'hermes', 'status': 'answered'}]}
    monkeypatch.setattr(fanout, 'MemoryRetriever', Retriever)
    inventory = tmp_path / 'inventory.json'
    inventory.write_text('{"gpu":"owned"}\n')
    config.update(inventory_paths=[str(inventory), str(tmp_path / 'absent')], memory={'timeout_s': 1})
    job = {'payload': {'text': 'do the real ask', 'recent_user_msgs': ['earlier ask']},
           'correction': 'must not leak', 'label': 'failure'}
    bundle = fanout.build_bundle(job, config)
    assert calls == [{'timeout_s': 1}, ('do the real ask', ['earlier ask'])]
    assert [r['text'] for r in bundle['packet'][0]['lines']] == ['alpha', '', 'beta', '']
    assert [r['number'] for r in bundle['facts']] == list(range(1, len(bundle['facts']) + 1))
    assert bundle['fact_catalog'] == {r['id']: r for r in bundle['facts']}
    assert bundle['packet'][0]['text_sha256'] == hashlib.sha256(b'alpha\n\nbeta\n').hexdigest()
    assert any(s['status'] == 'unavailable' for s in bundle['sources'])
    prompt = fanout.candidate_prompt(job, bundle)
    assert prompt.count('memory evidence') == 1
    assert 'must not leak' not in prompt
    assert 'fact_catalog' not in prompt
    other = fanout.build_bundle({'payload': {'text': 'different ask'}}, config)
    assert bundle['facts'][0]['id'] == other['facts'][0]['id']
    assert bundle['bundle_hash'] != other['bundle_hash']


def test_unknown_acceptance_is_abstention_not_a_prior(routing):
    for value in routing['metrics'].values():
        value['p_accept'] = None
    result = fanout.select_offers(routing, {})
    assert result['offers'] == result['eligible_offers'] == []
    assert all('p_accept' in row.get('missing_metrics', []) for row in result['exclusions'] if 'offer_id' in row)
    assert result['status'] == 'abstained'


def test_real_kerdoios_formula_and_tier_spread(routing):
    result = fanout.select_offers(routing, {})
    assert result['formula_id'] == fanout.FORMULA_ID
    assert result['status'] == 'selected'
    selected = result['offers']
    assert [row['tier'] for row in selected] == ['local', 'fast', 'subscription']
    best = result['eligible_offers'][0]
    expected_pace = .001 * 5000 / (1000 / 1)
    assert best['pace'] == pytest.approx(expected_pace)
    assert best['score'] == pytest.approx((1 + 1000 / 100) * (1 + expected_pace) / .5)
    assert best['objective_inputs']['formula_id'] == fanout.FORMULA_ID


@pytest.mark.parametrize('change,reason', [
    ('stale', 'metrics_missing_or_stale'), ('cost', 'paid_api_or_unknown_cost'),
    ('context', 'context'), ('pace', 'pace_or_remaining_budget')])
def test_exclusions_do_not_relax_constraints(routing, change, reason):
    if change == 'stale':
        for m in routing['metrics'].values():
            m['timestamp'] = time.time() - 3600
    elif change == 'cost':
        for r in routing['routes'].values():
            r['entitlement']['marginal_cost_usd'] = .01
    elif change == 'context':
        for r in routing['routes'].values():
            r['context_window'] = 512
    else:
        routing['max_pace'] = .00001
    result = fanout.select_offers(routing, {})
    assert not result['offers']
    assert any(reason in row['reason'] for row in result['exclusions'])


def make_usage_db(path, providers=('anthropic', 'openai-codex')):
    now = time.time()
    db = sqlite3.connect(path)
    db.executescript('''CREATE TABLE usage_history(provider TEXT,account_key TEXT,limit_id TEXT,
        recorded_at REAL,used_fraction REAL,resets_at REAL,status TEXT);
        CREATE TABLE model_usage(model_key TEXT,last_used_at REAL);
        CREATE TABLE auth_credentials(id INTEGER,provider TEXT,credential_type TEXT,data TEXT,
        disabled_cause TEXT,updated_at REAL);''')
    for index, provider in enumerate(providers):
        account, email = 'synthetic-account-' + str(index), 'synthetic-' + str(index) + '@invalid'
        data = {'accountId': account, 'email': email, 'access': 'never-log-synthetic-token'}
        key = f'oauth|account:{account}|email:{email}'
        if provider == 'anthropic':
            data['orgId'] = 'synthetic-org'
            key += '|org:synthetic-org'
        db.execute('INSERT INTO auth_credentials VALUES(?,?,?,?,?,?)',
                   (index, provider, 'oauth', json.dumps(data), None, now * 1000))
        for offset, used in ((60, .1), (30, .2)):
            db.execute('INSERT INTO usage_history VALUES(?,?,?,?,?,?,?)',
                       (provider, key, 'primary', (now - offset) * 1000, used, (now + 3600) * 1000, 'ok'))
        db.execute('INSERT INTO model_usage VALUES(?,?)', (provider + '/synthetic', now * 1000))
    db.commit()
    db.close()
    return path


@pytest.fixture
def bootstrap(config, tmp_path):
    config.update(phase='bootstrap', usage_db=str(make_usage_db(tmp_path / 'usage.sqlite')),
                  bootstrap_candidate_limit=30, bootstrap_judge_limit=153,
                  subscription_model_pins={'anthropic': ['claude-synthetic-2']})
    now = time.time()
    models = []
    ids = []
    for provider, model in (('anthropic', 'claude-synthetic-2'), ('anthropic', 'claude-haiku-5-5')):
        identity = provider + ':' + model
        ids.append(identity)
        models.append({'provider': provider, 'model': model})
        config['routes'][identity] = {'kind': 'subscription', 'tier': 'subscription',
            'context_window': 128000, 'privacy': {'allowed': ['confidential']},
            'entitlement': {'source': 'synthetic subscription', 'expires_at': now + 3600,
                            'marginal_cost_usd': 0}}
    Path(config['catalog_path']).write_text(json.dumps({'models': models}))
    config['bootstrap_offers'] = ids
    return config


def test_usage_windows_reads_exact_account_and_never_token_attribution(bootstrap):
    result = fanout.usage_windows(bootstrap)
    assert result['status'] == 'answered'
    assert len(result['windows']) == 2
    for window in result['windows']:
        assert window['remaining'] == pytest.approx(.8)
        assert window['quota_fraction_per_hour'] == pytest.approx(12)
        assert window['account_hash'] == result['credential_bindings'][window['provider']]['account_hash']
        assert window['pace'] > 0
    serialized = json.dumps(result)
    assert 'never-log-synthetic-token' not in serialized
    assert '@invalid' not in serialized
    assert 'oauth|account:' not in serialized
    assert 'tokens' not in result['model_usage'][0]


def test_bootstrap_is_unscored_and_allows_two_honest_offers(bootstrap):
    result = fanout.select_offers(bootstrap, {})
    assert result['status'] == 'bootstrap_unscored'
    assert result['formula_id'] is None
    assert len(result['offers']) == 2
    assert all(r['score'] is None and r['noO14credit'] for r in result['offers'])
    assert all(r['objective_inputs']['burn'] is None for r in result['offers'])


def test_bootstrap_wrong_account_cannot_be_bound_by_provider_alone(bootstrap):
    provider = 'anthropic'
    db = sqlite3.connect(bootstrap['usage_db'])
    db.execute('UPDATE usage_history SET account_key=? WHERE provider=?', ('another-private-account', provider))
    db.commit()
    db.close()
    selection = fanout.select_offers(bootstrap, {})
    assert selection['offers'] == []
    assert any(r['reason'] == 'account_window_binding_missing' for r in selection['exclusions'])


def test_bootstrap_gpu_context_exclusion_does_not_truncate(bootstrap):
    catalog = json.loads(Path(bootstrap['catalog_path']).read_text())
    catalog['models'].append({'provider': 'local', 'model': 'qwen-gpu',
                              'privacy': {'allowed': ['confidential']}})
    Path(bootstrap['catalog_path']).write_text(json.dumps(catalog))
    identity = 'local:qwen-gpu'
    bootstrap['bootstrap_offers'].append(identity)
    bootstrap['routes'][identity] = {'kind': 'local_http', 'tier': 'local', 'compute': 'gpu',
        'url': 'http://100.113.138.100:11530/v1/chat/completions', 'context_window': 4096,
        'entitlement': {'source': 'synthetic props', 'expires_at': time.time() + 3600, 'marginal_cost_usd': 0}}
    bootstrap['metrics'][identity] = {'source': 'synthetic props', 'timestamp': time.time(),
        'task_class': 'intent.shadow', 'quota_unit': 'local_request', 'quota_group': 'gpu'}
    result = fanout.select_offers(bootstrap, {})
    assert any(r['reason'] == 'context_does_not_fit_identical_bundle' for r in result['exclusions'])
    assert len(result['offers']) == 2


def test_atomic_bootstrap_candidate_and_judge_caps_share_private_ledger(bootstrap):
    offer = fanout.select_offers(bootstrap, {})['offers'][1]
    for job in range(10):
        for _ in range(3):
            fanout._reserve({**bootstrap, 'call_job_id': 'job-' + str(job)}, offer)
    with pytest.raises(ValueError, match='bootstrap_call_cap'):
        fanout._reserve({**bootstrap, 'call_job_id': 'eleventh'}, offer)
    for _ in range(153):
        fanout._reserve({**bootstrap, 'call_role': 'judge'}, offer)
    with pytest.raises(ValueError, match='bootstrap_call_cap'):
        fanout._reserve({**bootstrap, 'call_role': 'judge'}, offer)
    root = Path(bootstrap['root'])
    assert root.stat().st_mode & 0o077 == 0
    ledger = json.loads((root / 'budget-reservations.json').read_text())
    assert ledger['__calls__']['candidate'] == 30
    assert ledger['__calls__']['judge'] == 153
    assert len(ledger['__calls__']['jobs']) == 10




@pytest.mark.parametrize('change,code', [('disabled', 'live_authorization_missing_or_expired'),
                                         ('tools', 'invalid_text_only_messages')])
def test_completion_has_no_unguarded_provider_path(config, monkeypatch, change, code):
    def forbidden(*args, **kwargs):
        raise AssertionError('provider must not be reached')
    monkeypatch.setattr(fanout, 'build_opener', forbidden)
    messages = [{'role': 'user', 'content': 'synthetic'}]
    if change == 'disabled':
        config['live_enabled'] = False
    else:
        messages[0]['tools'] = []
    result = fanout.complete({'provider': 'local', 'model': 'synthetic'}, messages, config)
    assert result['error']['code'] == code


def test_judge_also_rechecks_privacy_and_input_bound(bootstrap):
    offer = fanout.select_offers(bootstrap, {})['offers'][1]
    messages = [{'role': 'user', 'content': 'x' * 5000}]
    assert fanout.eligible_offer(bootstrap, offer, messages)['reason'] == 'input_token_bound_exceeded'
    bootstrap['routes'][offer['id']]['privacy']['allowed'] = []
    assert not fanout.eligible_offer(bootstrap, offer)['eligible']




@pytest.mark.parametrize('status', [403, 429])
def test_http_errors_are_honest_sanitized_and_never_retried(bootstrap, monkeypatch, status):
    offer = fanout.select_offers(bootstrap, {})['offers'][1]
    monkeypatch.setattr(fanout.shadow_evals_omp, 'credential_available', lambda *args, **kwargs: True)
    calls = []
    def fail(*args, **kwargs):
        calls.append(1)
        raise fanout.shadow_evals_omp.OmpError('omp_provider_error', code=status)
    monkeypatch.setattr(fanout.shadow_evals_omp, 'execute', fail)
    monkeypatch.setattr(fanout, '_observation', lambda *args: None)
    result = fanout.complete(offer, [{'role': 'user', 'content': 'synthetic'}],
                             {**bootstrap, 'call_job_id': 'first'})
    assert result['error']['http_status'] == status
    assert len(calls) == 1
    assert 'secret' not in json.dumps(result)
    assert result['metrics']['ttft_ms'] is None
    assert result['metrics']['tokens_per_second'] is None
    assert result['metrics']['cost_usd'] is None
    if status == 429:
        fanout.complete(offer, [{'role': 'user', 'content': 'synthetic'}],
                        {**bootstrap, 'call_job_id': 'second'})
        assert len(calls) == 1


@pytest.mark.parametrize('reason,expected', [
    ('bootstrap_call_cap', 'bootstrap_call_cap'),
    ('credential_account_changed', 'credential_account_changed'),
    ('private-response-with-secret', 'completion_failed'),
])
def test_transport_invariants_are_identifiable_without_private_error_text(bootstrap, monkeypatch, reason, expected):
    offer = fanout.select_offers(bootstrap, {})['offers'][1]
    monkeypatch.setattr(fanout.shadow_evals_omp, 'credential_available', lambda *args, **kwargs: True)
    def fail(*args, **kwargs):
        raise ValueError(reason)
    monkeypatch.setattr(fanout.shadow_evals_omp, 'execute', fail)
    monkeypatch.setattr(fanout, '_observation', lambda *args: None)
    result = fanout.complete(offer, [{'role': 'user', 'content': 'synthetic'}],
                             {**bootstrap, 'call_job_id': 'invariant'})
    assert result['error']['code'] == expected
    assert 'secret' not in json.dumps(result)


def test_candidate_fanout_reuses_identical_numbered_context(bootstrap, monkeypatch):
    calls = []
    def completion(offer, messages, config):
        calls.append((messages, config['call_job_id']))
        return {'provider': offer['provider'], 'model': offer['model'], 'content': '{}', 'metrics': {}, 'error': None}
    monkeypatch.setattr(fanout, 'complete', completion)
    bundle = {'ask': 'synthetic ask', 'facts': [{'id': 'Fstable', 'number': 1, 'text': 'evidence'}],
              'sources': [], 'recent_user_msgs': [], 'bundle_hash': 'synthetic-hash'}
    rows = fanout.run_candidates({'job_id': 'same-message'}, bundle, bootstrap)
    assert len(rows) == 2
    assert calls[0] == calls[1]
    assert all(row['bundle_hash'] == 'synthetic-hash' and row['noO14credit'] for row in rows)


def test_expanded_bootstrap_envelope_and_exact_sol_pin(bootstrap):
    bootstrap.update(bootstrap_candidate_limit=40, bootstrap_judge_limit=80)
    bootstrap['subscription_model_pins']['openai-codex'] = ['gpt-6-luna', 'gpt-6.1-sol']
    catalog = json.loads(Path(bootstrap['catalog_path']).read_text())
    for model in ('gpt-6-luna', 'gpt-6.1-sol'):
        identity = 'openai-codex:' + model
        catalog['models'].append({'provider': 'openai-codex', 'model': model})
        bootstrap['bootstrap_offers'].append(identity)
        bootstrap['routes'][identity] = {
            'kind': 'subscription', 'tier': 'subscription', 'context_window': 128000,
            'privacy': {'allowed': ['confidential']},
            'entitlement': {'source': 'synthetic subscription', 'expires_at': time.time() + 3600,
                            'marginal_cost_usd': 0}}
    Path(bootstrap['catalog_path']).write_text(json.dumps(catalog))
    selection = fanout.select_offers(bootstrap, {})
    assert len(selection['offers']) == 4
    assert any(r['model'] == 'gpt-6.1-sol' for r in selection['offers'])
    assert all(r['score'] is None and r['noO14credit'] for r in selection['offers'])
    bootstrap['subscription_model_pins']['openai-codex'] = ['gpt-6-luna']
    without_pin = fanout.select_offers(bootstrap, {})
    assert any(r.get('offer_id') == 'openai-codex:gpt-6.1-sol'
               and r['reason'] == 'unsupported_subscription_pin' for r in without_pin['exclusions'])
    offer = selection['offers'][0]
    for job in range(10):
        for _ in range(4):
            fanout._reserve({**bootstrap, 'call_job_id': str(job)}, offer)
    with pytest.raises(ValueError, match='bootstrap_call_cap'):
        fanout._reserve({**bootstrap, 'call_job_id': 'extra'}, offer)
    for _ in range(80):
        fanout._reserve({**bootstrap, 'call_role': 'judge'}, offer)
    with pytest.raises(ValueError, match='bootstrap_call_cap'):
        fanout._reserve({**bootstrap, 'call_role': 'judge'}, offer)


def test_free_route_requires_catalog_privacy_not_config_claims(config):
    model = {'provider': 'groq', 'model': 'synthetic', 'free_status': {'value': True}}
    route = {'kind': 'free_http', 'privacy': {'allowed': ['confidential'], 'no_training': True,
             'no_retention': True, 'source_urls': ['https://example.invalid/policy']}}
    values = {'timestamp': time.time(), 'source': 'synthetic', 'task_class': 'intent.shadow',
              'quota_unit': 'requests', 'quota_group': 'synthetic'}
    assert fanout._route_reason(model, route, values, config) == 'privacy_not_authorized'
    model['privacy'] = route['privacy']
    model['privacy']['source_urls'] = []
    assert fanout._route_reason(model, route, values, config) == 'privacy_evidence_missing'


def test_bootstrap_output_bound_does_not_silently_relax(bootstrap):
    bootstrap['max_tokens'] = 4097
    result = fanout.select_offers(bootstrap, {})
    assert result['offers'] == []
    assert result['exclusions'][0]['reason'] == 'invalid_bootstrap_output_or_call_limit'


def test_candidate_requests_actually_overlap_and_preserve_offer_order(bootstrap, monkeypatch):
    offers = fanout.select_offers(bootstrap, {})['offers']
    entered = set()
    lock = threading.Lock()
    barrier = threading.Barrier(len(offers))
    def completion(offer, messages, config):
        with lock:
            entered.add(offer['id'])
        barrier.wait(timeout=5)
        assert len(entered) == len(offers)
        return {'provider': offer['provider'], 'model': offer['model'], 'content': '{}',
                'metrics': {}, 'error': None}
    monkeypatch.setattr(fanout, 'complete', completion)
    bundle = {'ask': 'parallel synthetic ask', 'facts': [], 'sources': [],
              'recent_user_msgs': [], 'bundle_hash': 'parallel-bundle'}
    rows = fanout.run_candidates({'job_id': 'parallel-job'}, bundle, bootstrap)
    assert [row['offer_id'] for row in rows] == [offer['id'] for offer in offers]
