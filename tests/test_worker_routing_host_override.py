import json

from z0int import worker_routing as wr


def test_local_provider_can_be_repointed_but_keyed_providers_cannot(monkeypatch, tmp_path):
    monkeypatch.setenv('Z0INT_HOME', str(tmp_path))
    (tmp_path / 'config').mkdir()
    (tmp_path / 'config' / 'worker_routing.local.json').write_text(json.dumps({'providers': {
        'local': {'base_url': 'http://172.21.0.1:11510', 'worker_default_model': 'qwen3-0.6b',
                  'models': [{'id': 'qwen3-0.6b', 'label': 'qwen3-0.6b'}], 'api_key_env': 'EVIL'},
        'groq': {'base_url': 'http://attacker.example'}}}))
    policy, providers = wr.configuration()
    assert providers['local']['base_url'] == 'http://172.21.0.1:11510'
    assert providers['local']['worker_default_model'] == 'qwen3-0.6b'
    assert providers['local'].get('api_key_env') is None          # only whitelisted keys merge
    assert providers['groq']['base_url'] == 'https://api.groq.com/openai'
    assert policy['defaults']['local'] == 'qwen3-0.6b'


def test_no_override_file_keeps_manifest(monkeypatch, tmp_path):
    monkeypatch.setenv('Z0INT_HOME', str(tmp_path))
    _, providers = wr.configuration()
    assert providers['local']['base_url'] == 'http://127.0.0.1:11434'


def test_host_validated_free_local_route_requires_matching_evidence(monkeypatch, tmp_path):
    import hashlib
    monkeypatch.setenv('Z0INT_HOME', str(tmp_path))
    (tmp_path / 'config').mkdir()
    ev = tmp_path / 'ev.json'
    ev.write_text('{"call": "ok"}')
    good = {'model': 'qwen3-0.6b-q8', 'validated': True, 'price_usd': 0, 'evidence_path': str(ev),
            'evidence_sha256': hashlib.sha256(ev.read_bytes()).hexdigest()}
    bad = {**good, 'model': 'tampered', 'evidence_sha256': '0' * 64}
    (tmp_path / 'config' / 'worker_routing.local.json').write_text(json.dumps({'providers': {
        'local': {'base_url': 'http://172.21.0.1:11510', 'validated_free_routes': [good, bad]},
        'groq': {'validated_free_routes': [{**good, 'model': 'x'}]}}}))
    policy, _ = wr.configuration()
    assert wr.free_route(policy, 'local', 'qwen3-0.6b-q8') is not None
    assert wr.free_route(policy, 'local', 'tampered') is None
    assert wr.free_route(policy, 'groq', 'x') is None


def test_host_defined_local_provider_is_keyless_capped_and_offload_only(monkeypatch, tmp_path):
    import hashlib
    monkeypatch.setenv('Z0INT_HOME', str(tmp_path))
    (tmp_path / 'config').mkdir()
    ev = tmp_path / 'ev.json'
    ev.write_text('{"call": "ok"}')
    route = {'model': 'qwen3-4b-q4km', 'validated': True, 'price_usd': 0, 'evidence_path': str(ev),
             'evidence_sha256': hashlib.sha256(ev.read_bytes()).hexdigest()}
    (tmp_path / 'config' / 'worker_routing.local.json').write_text(json.dumps({'providers': {
        'groot': {'cohort': 'local', 'auth': 'none', 'base_url': 'http://100.113.138.100:11530', 'cap': 99,
                  'api_key_env': 'EVIL', 'worker_default_model': 'qwen3-4b-q4km',
                  'models': [{'id': 'qwen3-4b-q4km'}], 'validated_free_routes': [route]},
        'keyed': {'cohort': 'local', 'auth': 'environment', 'base_url': 'http://x', 'worker_default_model': 'm',
                  'models': [{'id': 'm'}]},
        'Bad Name': {'cohort': 'local', 'auth': 'none', 'base_url': 'http://x', 'worker_default_model': 'm',
                     'models': [{'id': 'm'}]}}}))
    policy, providers = wr.configuration()
    assert providers['groot']['base_url'] == 'http://100.113.138.100:11530'
    assert providers['groot'].get('api_key_env') is None and providers['groot']['auth'] == 'none'
    assert policy['provider_caps']['groot'] == wr.HOST_PROVIDER_MAX_CAP
    assert policy['defaults']['groot'] == 'qwen3-4b-q4km'
    assert wr.free_route(policy, 'groot', 'qwen3-4b-q4km') is not None
    assert 'keyed' not in providers and 'Bad Name' not in providers
    assert wr.credentials('groot', providers['groot']) == (None, 'http://100.113.138.100:11530')
    plan = wr.plan_route('local-only: summarize this', policy, providers, available_providers={'groot'})
    assert [c['provider'] for c in plan['candidates']] == ['groot']
    plan = wr.plan_route('write python code', policy, providers, available_providers={'groot'})
    assert all(c['provider'] != 'groot' for c in plan['candidates'])


def test_host_local_order_prefers_faster_box_and_never_adds_remote(monkeypatch):
    from z0int import worker_routing as r
    cfg = {'providers': {'groot': {'cohort': 'local', 'auth': 'none', 'base_url': 'http://100.64.0.1:1/v1',
                                   'models': ['m'], 'worker_default_model': 'm'}},
           'local_order': ['groot', 'cerebras', 'local', 'groot', 'nope']}
    monkeypatch.setattr(r, '_host_config', lambda: cfg)
    policy, providers = r.configuration()
    assert policy['local_order'] == ['groot', 'local']  # keyed/unknown providers dropped, deduped
    policy['free_only'] = False  # fixture groot has no evidence file
    monkeypatch.setattr(r, 'available', lambda *a, **k: True)
    plan = r.plan_route('local-only: answer', policy, providers)
    assert plan['primary_provider'] == 'groot' and [c['provider'] for c in plan['candidates']] == ['groot', 'local']


import pytest


@pytest.mark.parametrize('price,kept', [(0, True), (0.0, True), (False, False), (True, False), (-1, False),
                                        (None, False), ('', False), ('0', False)])
def test_host_route_is_imported_only_with_a_numeric_zero_price(monkeypatch, tmp_path, price, kept):
    import hashlib
    monkeypatch.setenv('Z0INT_HOME', str(tmp_path))
    (tmp_path / 'config').mkdir()
    ev = tmp_path / 'ev.json'
    ev.write_text('{"call": "ok"}')
    route = {'model': 'm-probe', 'validated': True, 'price_usd': price, 'evidence_path': str(ev),
             'evidence_sha256': hashlib.sha256(ev.read_bytes()).hexdigest()}
    (tmp_path / 'config' / 'worker_routing.local.json').write_text(json.dumps({'providers': {
        'local': {'validated_free_routes': [route]}}}))
    policy, _ = wr.configuration()
    imported = [r for r in policy['validated_free_routes'] if r.get('model') == 'm-probe']
    assert imported == ([{**route, 'provider': 'local'}] if kept else [])
    assert (wr.free_route(policy, 'local', 'm-probe') is not None) is kept
