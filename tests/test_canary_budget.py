import copy
import io
import json
from concurrent.futures import ProcessPoolExecutor

import pytest

from z0int.canary_budget import migrate_legacy_403, reserve, settle


MODEL = 'anthropic/claude-haiku-5.5'


def policy(tmp_path):
    return {'paid_canary': {'models': [MODEL],
        'max_price': {'prompt': 2, 'completion': 10, 'request': 0},
        'total_cap_usd': 1, 'attempt_reservation_usd': 0.1,
        'max_attempts': 3, 'ledger': str(tmp_path / 'budget.json')}}


def state(p):
    with open(p['paid_canary']['ledger'], encoding='utf-8') as stream:
        return json.load(stream)


def reserve_one(p):
    return reserve(p, 'openrouter', MODEL, [], 2048)


def reserve_competing(p):
    try:
        return reserve_one(p)
    except ValueError:
        return None


def legacy(tmp_path):
    p = policy(tmp_path)
    models = [MODEL, 'openai/gpt-6-luna', 'openai/gpt-6.1-sol',
              MODEL, 'openai/gpt-6-luna', 'openai/gpt-5.6-terra']
    rows = [{'provider': 'openrouter' if index < 3 else 'vercel',
             'model': model, 'reserved_usd': 0.1} for index, model in enumerate(models)]
    (tmp_path / 'budget.json').write_text(json.dumps({'reserved_usd': 0.6, 'attempts': rows}))
    facts = [{'attempt_index': index, 'provider': row['provider'], 'model': row['model'],
              'http_status': 403, 'usage_present': False} for index, row in enumerate(rows)]
    return p, facts


def test_global_default_has_no_paid_reservation(tmp_path):
    assert reserve({}, 'openrouter', 'free', [], 2048) is None
    assert settle({}, None) is None
    assert not list(tmp_path.iterdir())


def test_pending_reservations_share_one_dollar_cap_across_runs(tmp_path):
    p = policy(tmp_path)
    ids = [reserve_one(copy.deepcopy(p)) for _ in range(10)]
    assert len(set(ids)) == 10
    with pytest.raises(ValueError, match='cap reached'):
        reserve_one(copy.deepcopy(p))
    ledger = state(p)
    assert ledger['reserved_usd'] == 1
    assert ledger['spent_usd'] == 0
    assert ledger['usage_bearing_calls'] == 0
    assert len(ledger['attempts']) == 10


def test_concurrent_runs_cannot_overreserve(tmp_path):
    p = policy(tmp_path)
    with ProcessPoolExecutor(max_workers=4) as pool:
        ids = list(pool.map(reserve_competing, [p] * 14))
    assert len([value for value in ids if value is not None]) == 10
    assert state(p)['reserved_usd'] == 1


def test_known_actual_charge_replaces_reservation_and_is_idempotent(tmp_path):
    p = policy(tmp_path)
    reservation_id = reserve_one(p)
    result = settle(p, reservation_id, http_status=200, usage_present=True, actual_cost_usd=0.025)
    assert result['status'] == 'settled'
    assert state(p)['spent_usd'] == 0.025
    assert state(p)['reserved_usd'] == 0
    assert state(p)['usage_bearing_calls'] == 1
    settle(p, reservation_id, http_status=200, usage_present=True, actual_cost_usd=0.025)
    assert state(p)['spent_usd'] == 0.025
    with pytest.raises(ValueError, match='conflicting settlement'):
        settle(p, reservation_id, http_status=200, usage_present=True, actual_cost_usd=0)
    assert state(p)['spent_usd'] == 0.025


def test_actual_spend_and_pending_reservations_jointly_limit_admission(tmp_path):
    p = policy(tmp_path)
    p['paid_canary']['max_attempts'] = 30
    for _ in range(9):
        settle(p, reserve_one(p), http_status=200, usage_present=True, actual_cost_usd=0.1)
    reserve_one(p)
    assert state(p)['spent_usd'] == 0.9
    assert state(p)['reserved_usd'] == 0.1
    with pytest.raises(ValueError, match='cap reached'):
        reserve_one(p)


def test_usage_bearing_unknown_cost_retains_hold_and_counts_attempt(tmp_path):
    p = policy(tmp_path)
    p['paid_canary']['max_attempts'] = 1
    reservation_id = reserve_one(p)
    settle(p, reservation_id, http_status=200, usage_present=True)
    assert state(p)['reserved_usd'] == 0.1
    assert state(p)['spent_usd'] == 0
    assert state(p)['usage_bearing_calls'] == 1
    with pytest.raises(ValueError, match='cap reached'):
        reserve_one(p)
    settle(p, reservation_id, http_status=200, usage_present=True, actual_cost_usd=0.003)
    assert state(p)['reserved_usd'] == 0
    assert state(p)['spent_usd'] == 0.003
    assert state(p)['usage_bearing_calls'] == 1


def test_no_usage_403_releases_zero_cost_and_does_not_count_attempt(tmp_path):
    p = policy(tmp_path)
    p['paid_canary']['max_attempts'] = 1
    for _ in range(12):
        reservation_id = reserve_one(p)
        settle(p, reservation_id, http_status=403, usage_present=False)
        settle(p, reservation_id, http_status=403, usage_present=False, actual_cost_usd=0)
    assert state(p)['reserved_usd'] == 0
    assert state(p)['spent_usd'] == 0
    assert state(p)['usage_bearing_calls'] == 0
    reservation_id = reserve_one(p)
    settle(p, reservation_id, http_status=200, usage_present=True, actual_cost_usd=0)
    with pytest.raises(ValueError, match='cap reached'):
        reserve_one(p)


@pytest.mark.parametrize('status,usage', [(403, None), (403, True), (500, False), (None, None)])
def test_unknown_or_non403_failure_never_silently_refunds(tmp_path, status, usage):
    p = policy(tmp_path)
    settle(p, reserve_one(p), http_status=status, usage_present=usage)
    assert state(p)['reserved_usd'] == 0.1
    assert state(p)['spent_usd'] == 0


def test_usage_bearing_403_is_charged_not_released_as_unexecuted(tmp_path):
    p = policy(tmp_path)
    result = settle(p, reserve_one(p), http_status=403, usage_present=True, actual_cost_usd=0.01)
    assert result['status'] == 'settled'
    assert state(p)['spent_usd'] == 0.01
    assert state(p)['usage_bearing_calls'] == 1


def test_observed_usage_cannot_be_discarded(tmp_path):
    p = policy(tmp_path)
    reservation_id = reserve_one(p)
    settle(p, reservation_id, http_status=403, usage_present=True)
    with pytest.raises(ValueError, match='discard observed usage'):
        settle(p, reservation_id, http_status=403, usage_present=False)
    assert state(p)['reserved_usd'] == 0.1


@pytest.mark.parametrize('cost', [-1, float('nan'), float('inf'), True, 'bad'])
def test_invalid_actual_cost_fails_closed(tmp_path, cost):
    p = policy(tmp_path)
    reservation_id = reserve_one(p)
    with pytest.raises(ValueError):
        settle(p, reservation_id, http_status=200, usage_present=True, actual_cost_usd=cost)
    assert state(p)['reserved_usd'] == 0.1
    assert state(p)['spent_usd'] == 0


def test_cost_requires_usage_evidence_and_403_cost_must_not_contradict(tmp_path):
    p = policy(tmp_path)
    reservation_id = reserve_one(p)
    with pytest.raises(ValueError, match='requires observed usage'):
        settle(p, reservation_id, http_status=200, actual_cost_usd=0)
    with pytest.raises(ValueError, match='contradicts'):
        settle(p, reservation_id, http_status=403, usage_present=False, actual_cost_usd=0.01)
    assert state(p)['reserved_usd'] == 0.1


def test_unexpected_overrun_records_incurred_cost_then_fails_closed(tmp_path):
    p = policy(tmp_path)
    reservation_id = reserve_one(p)
    with pytest.raises(ValueError, match='recorded spend retained'):
        settle(p, reservation_id, http_status=200, usage_present=True, actual_cost_usd=1.1)
    assert state(p)['spent_usd'] == 1.1
    assert state(p)['reserved_usd'] == 0
    with pytest.raises(ValueError, match='worst-case reservation'):
        reserve_one(p)


def test_unknown_id_cannot_modify_ledger(tmp_path):
    p = policy(tmp_path)
    reserve_one(p)
    before = state(p)
    with pytest.raises(ValueError, match='unknown canary reservation'):
        settle(p, 'not-an-id', http_status=403, usage_present=False)
    assert state(p) == before


@pytest.mark.parametrize('provider', ['opencode-zen', 'unapproved-adapter'])
def test_opencode_and_unknown_adapters_are_not_authorized_even_if_configured(tmp_path, provider):
    p = policy(tmp_path)
    p['paid_canary']['routes'] = [{'provider': provider, 'model': 'claude-haiku-4-5'}]
    with pytest.raises(ValueError, match='spend envelope'):
        reserve(p, provider, 'claude-haiku-4-5', [], 2048)
    assert not (tmp_path / 'budget.json').exists()


@pytest.mark.parametrize('provider,model', [
    ('openai-codex', 'gpt-6-luna'),
    ('anthropic', 'claude-haiku-4-5'),
])
def test_explicit_adapter_route_is_exact_and_does_not_change_global_policy(tmp_path, provider, model):
    p = policy(tmp_path)
    p['free_only'] = True
    p['paid_canary']['routes'] = [{'provider': provider, 'model': model}]
    before = copy.deepcopy(p)
    assert reserve(p, provider, model, [], 2048)
    assert p == before
    for wrong_provider, wrong_model in [('other', model), (provider, model + '-other')]:
        with pytest.raises(ValueError, match='spend envelope'):
            reserve(p, wrong_provider, wrong_model, [], 2048)
    with pytest.raises(ValueError, match='spend envelope'):
        reserve_one(p)  # routes replaces, not broadens, the legacy model list.


@pytest.mark.parametrize('provider,model,messages,tokens', [
    ('other', MODEL, [], 2048), ('openrouter', 'unapproved', [], 2048),
    ('openrouter', MODEL, ['x' * 32769], 2048),
    ('openrouter', MODEL, [], 2049), ('openrouter', MODEL, [], 0),
    ('openrouter', MODEL, [], True),
])
def test_out_of_envelope_fails_before_ledger(tmp_path, provider, model, messages, tokens):
    with pytest.raises(ValueError, match='spend envelope'):
        reserve(policy(tmp_path), provider, model, messages, tokens)
    assert not (tmp_path / 'budget.json').exists()


@pytest.mark.parametrize('key,value', [('total_cap_usd', 2), ('attempt_reservation_usd', 0),
                                      ('max_attempts', 0), ('max_attempts', True)])
def test_fixed_envelope_cannot_be_expanded_or_disabled(tmp_path, key, value):
    p = policy(tmp_path)
    p['paid_canary'][key] = value
    with pytest.raises(ValueError, match='spend envelope'):
        reserve_one(p)


def test_corrupt_ledger_fails_closed(tmp_path):
    p = policy(tmp_path)
    (tmp_path / 'budget.json').write_text('{broken')
    with pytest.raises(json.JSONDecodeError):
        reserve_one(p)


def test_aggregate_tampering_cannot_free_budget(tmp_path):
    p = policy(tmp_path)
    reserve_one(p)
    ledger = state(p)
    ledger['reserved_usd'] = 0
    (tmp_path / 'budget.json').write_text(json.dumps(ledger))
    with pytest.raises(ValueError, match='inconsistent canary ledger totals'):
        reserve_one(p)


@pytest.mark.parametrize('price,allowed', [('0.0000005', True), ('0.000010', True),
                                         ('0.000011', False), ('NaN', False)])
def test_vercel_live_catalog_price_admission(tmp_path, monkeypatch, price, allowed):
    import urllib.request
    monkeypatch.setenv('AI_GATEWAY_API_KEY', 'synthetic-test-key')
    catalog = {'data': [{'id': MODEL, 'pricing': {'input': '0.000002', 'output': price},
                         'supported_parameters': ['tools']}]}
    monkeypatch.setattr(urllib.request, 'urlopen', lambda request, timeout: io.StringIO(json.dumps(catalog)))
    p = policy(tmp_path)
    if allowed:
        assert reserve(p, 'vercel', MODEL, [], 2048)
        assert state(p)['reserved_usd'] == 0.1
    else:
        with pytest.raises(ValueError):
            reserve(p, 'vercel', MODEL, [], 2048)
        assert not (tmp_path / 'budget.json').exists()


def test_vercel_missing_catalog_price_cannot_admit(tmp_path, monkeypatch):
    import urllib.request
    monkeypatch.setenv('AI_GATEWAY_API_KEY', 'synthetic-test-key')
    catalog = {'data': [{'id': MODEL, 'pricing': {'input': '0.000002'},
                         'supported_parameters': ['tools']}]}
    monkeypatch.setattr(urllib.request, 'urlopen', lambda request, timeout: io.StringIO(json.dumps(catalog)))
    with pytest.raises(KeyError):
        reserve(policy(tmp_path), 'vercel', MODEL, [], 2048)
    assert not (tmp_path / 'budget.json').exists()


def test_ordinary_legacy_load_retains_all_six_unknown_reservations(tmp_path):
    p, _ = legacy(tmp_path)
    reserve_one(p)
    ledger = state(p)
    assert ledger['version'] == 2
    assert ledger['reserved_usd'] == 0.7
    assert ledger['spent_usd'] == 0
    assert all(row['status'] == 'reserved' for row in ledger['attempts'])


def test_explicit_six_403_migration_releases_only_observed_holds(tmp_path):
    p, facts = legacy(tmp_path)
    ids = migrate_legacy_403(p, facts)
    assert ids == [f'legacy-{index}' for index in range(6)]
    assert state(p)['reserved_usd'] == 0
    assert state(p)['spent_usd'] == 0
    assert state(p)['usage_bearing_calls'] == 0
    assert all(row['status'] == 'unexecuted' for row in state(p)['attempts'])
    assert migrate_legacy_403(p, facts) == ids
    assert reserve_one(p)


def test_partial_migration_keeps_unobserved_reservations(tmp_path):
    p, facts = legacy(tmp_path)
    migrate_legacy_403(p, facts[:1])
    assert state(p)['reserved_usd'] == 0.5
    assert state(p)['attempts'][1]['status'] == 'reserved'


@pytest.mark.parametrize('change', ['unknown_usage', 'wrong_status', 'wrong_model', 'duplicate', 'empty'])
def test_legacy_migration_rejects_unsupported_facts_atomically(tmp_path, change):
    p, facts = legacy(tmp_path)
    before = (tmp_path / 'budget.json').read_text()
    if change == 'unknown_usage':
        facts[-1]['usage_present'] = None
    elif change == 'wrong_status':
        facts[-1]['http_status'] = 500
    elif change == 'wrong_model':
        facts[-1]['model'] = 'different-model'
    elif change == 'duplicate':
        facts.append(dict(facts[0]))
    else:
        facts = []
    with pytest.raises(ValueError):
        migrate_legacy_403(p, facts)
    assert (tmp_path / 'budget.json').read_text() == before
