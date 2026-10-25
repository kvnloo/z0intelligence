"""Offline snapshots and native JSON parsing; never invoke a real provider."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sqlite3
import sys
import time

import pytest

from z0int import shadow_evals_omp as omp

FIXTURE = Path(__file__).parent / 'fixtures/omp-print-18.12.6-sol.jsonl'


@pytest.fixture
def config(tmp_path):
    agent = tmp_path / 'source'
    agent.mkdir()
    db = sqlite3.connect(agent / 'agent.db')
    db.executescript('''CREATE TABLE auth_credentials(id INTEGER PRIMARY KEY AUTOINCREMENT,
        provider TEXT NOT NULL,credential_type TEXT NOT NULL,data TEXT NOT NULL,
        disabled_cause TEXT DEFAULT NULL,identity_key TEXT DEFAULT NULL,
        created_at INTEGER NOT NULL DEFAULT 0,updated_at INTEGER NOT NULL DEFAULT 0);''')
    for provider in ('openai-codex', 'anthropic'):
        value = {'access': 'sk-ant-oat-synthetic' if provider == 'anthropic' else 'synthetic-access',
                 'refresh': 'must-not-copy-refresh', 'refreshToken': 'must-not-copy-refresh-alias',
                 'expires': (time.time() + 3600) * 1000, 'accountId': 'synthetic-account',
                 'email': 'synthetic@invalid', 'apiKey': 'must-not-copy-api-key'}
        db.execute('INSERT INTO auth_credentials(provider,credential_type,data,updated_at) VALUES(?,?,?,?)',
                   (provider, 'oauth', json.dumps(value), 100))
        db.execute('INSERT INTO auth_credentials(provider,credential_type,data,updated_at) VALUES(?,?,?,?)',
                   (provider, 'api_key', json.dumps({'key': 'must-not-copy-paid-key'}), 200))
    db.commit()
    db.close()
    db = sqlite3.connect(agent / 'models.db')
    db.executescript('''CREATE TABLE model_cache(provider_id TEXT PRIMARY KEY,version INTEGER NOT NULL,
        updated_at INTEGER NOT NULL,authoritative INTEGER NOT NULL DEFAULT 0,
        static_fingerprint TEXT NOT NULL DEFAULT '',models TEXT NOT NULL,
        models_hash TEXT,installed_extra_column TEXT);''')
    for provider, model in (('openai-codex:0.159.0', 'gpt-6.1-sol'), ('anthropic', 'claude-haiku-5-5'),
                            ('unrelated', 'unrelated-model')):
        db.execute('INSERT INTO model_cache VALUES(?,?,?,?,?,?,?,?)',
                   (provider, 14, 1234, 1, 'synthetic-fingerprint', json.dumps([{'id': model}]),
                    'preserve-exact-installed-hash', 'preserve-future-installed-column'))
    db.commit()
    db.close()
    return {'root': str(tmp_path / 'private'), 'omp_auth_db': str(agent / 'agent.db'),
            'omp_models_db': str(agent / 'models.db'), 'timeout_s': 10}


def fixture_events():
    return [json.loads(line) for line in FIXTURE.read_text().splitlines() if line.strip()]


def test_snapshot_is_access_only_exact_provider_and_originals_unchanged(config, monkeypatch):
    source_hashes = {key: hashlib.sha256(Path(config[key]).read_bytes()).hexdigest()
                     for key in ('omp_auth_db', 'omp_models_db')}
    monkeypatch.setenv('OPENAI_API_KEY', 'must-not-inherit-paid-key')
    monkeypatch.setenv('PI_PROFILE', 'must-not-inherit-profile')
    prepared = omp.prepare_agent_dir({'provider': 'openai-codex', 'model': 'gpt-6.1-sol'}, config)
    target = Path(prepared['agent_dir'])
    db = sqlite3.connect(target / 'agent.db')
    rows = db.execute('SELECT provider,credential_type,data FROM auth_credentials').fetchall()
    assert len(rows) == 1
    assert rows[0][:2] == ('openai-codex', 'oauth')
    data = json.loads(rows[0][2])
    assert data['access'] == 'synthetic-access'
    assert data['refresh'] == ''
    assert 'refreshToken' not in data and 'apiKey' not in data
    db.close()
    db = sqlite3.connect(target / 'models.db')
    rows = db.execute('SELECT provider_id,models_hash,installed_extra_column FROM model_cache').fetchall()
    assert rows == [('openai-codex:0.159.0', 'preserve-exact-installed-hash', 'preserve-future-installed-column')]
    db.close()
    assert all(hashlib.sha256(Path(config[key]).read_bytes()).hexdigest() == digest
               for key, digest in source_hashes.items())
    assert not list(Path(prepared['cwd']).iterdir())
    assert 'OPENAI_API_KEY' not in prepared['env'] and 'PI_PROFILE' not in prepared['env']
    for key in ('HOME', 'PI_CODING_AGENT_DIR', 'OMP_AGENT_DIR', 'TMPDIR', 'XDG_DATA_HOME',
                'XDG_STATE_HOME', 'XDG_CACHE_HOME', 'XDG_CONFIG_HOME', 'XDG_RUNTIME_DIR'):
        assert Path(prepared['env'][key]).is_relative_to(Path(config['root']))
    assert (target / 'agent.db').stat().st_mode & 0o077 == 0
    settings = json.loads((target / 'config.yml').read_text())
    for key in ('enabled', 'modelFallback', 'usageAwareFallback', 'waitForUsageReset'):
        assert settings['retry'][key] is False
    assert settings['providers']['anthropic']['serverSideFallback'] is False
    assert settings['telemetry']['otlpExportEnabled'] is False
    assert all(flag in prepared['argv'] for flag in omp.FLAGS)
    assert prepared['selector'] == 'openai-codex/gpt-6.1-sol:low'


def test_expiry_and_exact_model_never_fall_back(config):
    with pytest.raises(omp.OmpError, match='exact_native_model_cache_unavailable'):
        omp.prepare_agent_dir({'provider': 'openai-codex', 'model': 'unknown-model'}, config)
    db = sqlite3.connect(config['omp_auth_db'])
    db.execute("UPDATE auth_credentials SET data=? WHERE provider='openai-codex' AND credential_type='oauth'",
               (json.dumps({'access': 'synthetic', 'expires': time.time() * 1000 + 500,
                            'accountId': 'synthetic-account'}),))
    db.commit()
    db.close()
    assert not omp.credential_available('openai-codex', config, timeout_s=10)
    with pytest.raises(omp.OmpError, match='oauth_expired_or_insufficient_lifetime'):
        omp.prepare_agent_dir({'provider': 'openai-codex', 'model': 'gpt-6.1-sol'}, config)


def test_account_binding_change_is_rejected(config):
    config['omp_account_id_hash'] = hashlib.sha256(b'another-account').hexdigest()
    with pytest.raises(omp.OmpError, match='credential_account_changed'):
        omp.prepare_agent_dir({'provider': 'openai-codex', 'model': 'gpt-6.1-sol'}, config)


def test_real_installed_fixture_counts_terminal_receipt_once():
    accumulator = omp.ReceiptAccumulator('openai-codex', 'gpt-6.1-sol', 4096)
    for event in fixture_events():
        assert not accumulator.feed(event)
    value = accumulator.result()
    assert value['content'] == 'OK'
    assert value['reported_model'] == 'gpt-6.1-sol'
    assert value['input_tokens'] == 1922
    assert value['output_tokens'] == 5
    assert value['total_tokens'] == 1927
    assert value['terminal_assistant_receipts'] == 1
    assert value['ttft_ms'] == pytest.approx(921.0853129999996)
    assert value['duration_ms'] == pytest.approx(957.0442080000003)
    assert value['gen_ms'] == pytest.approx(957.0442080000003 - 921.0853129999996)
    assert value['generation_basis'] == 'native_duration_minus_native_ttft'
    assert value['list_price_usd'] == pytest.approx(.003894)
    assert value['cost'] is None and value['error'] is None


def test_placeholder_start_does_not_fabricate_usage():
    accumulator = omp.ReceiptAccumulator('openai-codex', 'gpt-6.1-sol', 4096)
    for event in fixture_events()[:7]:
        accumulator.feed(event)
    value = accumulator.result()
    assert value['input_tokens'] is None and value['output_tokens'] is None
    assert value['ttft_ms'] is None
    assert value['error']['code'] == 'omp_missing_terminal_receipt'


def test_stream_byte_stop_is_not_a_token_count():
    accumulator = omp.ReceiptAccumulator('openai-codex', 'gpt-6.1-sol', 4)
    assert accumulator.feed({'type': 'message_update', 'assistantMessageEvent':
                             {'type': 'text_delta', 'delta': 'abcdef'}})
    value = accumulator.result()
    assert value['content'] == 'abcdef' and value['output_bytes_observed'] == 6
    assert value['input_tokens'] is None and value['output_tokens'] is None
    assert value['ttft_ms'] is None and value['server_overrun_tokens'] is None
    assert value['error']['code'] == 'client_byte_bound_stop'
    assert value['client_limit_overrun_bytes'] == 2
    assert value['output_limit_enforced'] is False


@pytest.mark.parametrize('status', [403, 429])
def test_native_http_errors_preserve_status_without_provider_text(status):
    accumulator = omp.ReceiptAccumulator('anthropic', 'claude-haiku-5-5', 100)
    event = {'type': 'message_end', 'message': {'role': 'assistant', 'provider': 'anthropic',
        'model': 'claude-haiku-5-5', 'content': [], 'usage': {'input': 0, 'output': 0},
        'stopReason': 'error', 'errorStatus': status, 'errorMessage': 'must-not-log-secret'}}
    accumulator.feed(event)
    value = accumulator.result()
    assert value['error']['http_status'] == status
    assert value['input_tokens'] is None
    assert 'must-not-log-secret' not in json.dumps(omp._safe_receipt_event(event))
    assert 'must-not-log-secret' not in json.dumps(value)


def test_tool_and_identity_mismatch_are_not_success():
    accumulator = omp.ReceiptAccumulator('anthropic', 'claude-haiku-5-5', 100)
    assert accumulator.feed({'type': 'message_update', 'assistantMessageEvent': {'type': 'toolcall_start'}})
    assert accumulator.result()['tool_call']
    accumulator = omp.ReceiptAccumulator('anthropic', 'claude-haiku-5-5', 100)
    assert accumulator.feed({'type': 'message_end', 'message': {'role': 'assistant',
        'provider': 'another-provider', 'model': 'another-model', 'content': [], 'stopReason': 'stop'}})
    assert accumulator.result()['error']['code'] == 'native_model_identity_mismatch'


def test_headless_runner_uses_private_stdio_and_native_receipts(config, tmp_path):
    script = tmp_path / 'synthetic_cli.py'
    script.write_text('import json,os,pathlib,sys\n'
        'prompt=sys.stdin.read()\n'
        'assert "--no-tools" in sys.argv and "--no-extensions" in sys.argv\n'
        'assert "OPENAI_API_KEY" not in os.environ\n'
        'assert not list(pathlib.Path.cwd().iterdir())\n'
        'for line in pathlib.Path(' + repr(str(FIXTURE)) + ').read_text().splitlines():\n'
        ' print(line,flush=True)\n')
    # Parent verifies this synthetic process only. No real OMP/provider executes.
    launcher = tmp_path / 'synthetic-omp'
    launcher.write_text('#!' + sys.executable + '\n' + script.read_text())
    launcher.chmod(0o700)
    config['omp_binary'] = str(launcher)
    value = omp.execute('openai-codex', 'gpt-6.1-sol', [{'role': 'user', 'content': 'identical packet'}],
                        4096, timeout_s=10, config=config)
    assert value['error'] is None and value['exit_code'] == 0
    assert value['input_tokens'] == 1922 and value['output_tokens'] == 5
    assert value['cost'] is None and value['list_price_usd'] == pytest.approx(.003894)
    assert Path(value['receipt_path']).is_relative_to(Path(config['root']))
    assert (Path(value['call_dir']) / 'prompt.txt').read_text() == 'identical packet'


def test_account_org_binding_cannot_drift_between_admission_and_snapshot(config):
    account_key = 'oauth|account:synthetic-account|email:synthetic@invalid|org:another-org'
    config['omp_account_hash'] = hashlib.sha256(account_key.encode()).hexdigest()
    with pytest.raises(omp.OmpError, match='credential_account_changed'):
        omp.prepare_agent_dir({'provider': 'openai-codex', 'model': 'gpt-6.1-sol'}, config)
