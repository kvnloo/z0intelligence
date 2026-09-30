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
