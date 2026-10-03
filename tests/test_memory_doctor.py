"""`z0int memory doctor`: read-only health of the memory substrate; never prints a config secret."""
from __future__ import annotations

import json
import stat

import pytest

from memory_fixture import build_av_db
from z0int.memory import doctor

AUTH_VALUE = 'FAKE-AGENTSVIEW-AUTH-VALUE-0001'
ENV_VALUE = 'FAKE-TDB-ENV-VALUE-0002'


def fake_bin(path, version='0.39.0'):
    path.write_text(f'#!/bin/sh\necho "agentsview v{version} (commit fake, built fake)"\n')
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return path


@pytest.fixture
def env(tmp_path, monkeypatch):
    av = tmp_path / 'av'
    build_av_db(av / 'sessions.db')
    claude_home = tmp_path / 'home' / '.claude-home'
    claude_home.mkdir(parents=True)
    (av / 'config.toml').write_text(f'require_auth = true\nauth_token = "{AUTH_VALUE}"\n'
                                    f'[agents.claude]\nhomes = ["~/.claude", "{claude_home}"]\n')
    monkeypatch.setenv('Z0INT_HOME', str(tmp_path / 'z0'))
    monkeypatch.setenv('Z0_FAKE_TDB_ENV', ENV_VALUE)
    return {'av_dir': av, 'agentsview_bin': fake_bin(tmp_path / 'agentsview'), 'claude_home': claude_home,
            'config': {}}


def checks(report):
    return {c['name']: c for c in report['checks']}


def test_passes_on_a_fresh_fixture_and_reports_the_semantic_layer_honestly(env):
    report = doctor.run(**env)
    assert report['ok'] is True, report
    c = checks(report)
    assert c['tencentdb']['status'] == 'not_configured' and c['tencentdb']['ok'] is True
    assert c['require_auth']['ok'] is True and c['data_version']['ok'] is True
    assert c['recall_layer']['status'] in {'present', 'absent'}


def test_fails_on_a_stale_db(env, tmp_path):
    build_av_db(tmp_path / 'stale' / 'sessions.db', age_hours=96)
    (tmp_path / 'stale' / 'config.toml').write_text((env['av_dir'] / 'config.toml').read_text())
    report = doctor.run(**{**env, 'av_dir': tmp_path / 'stale'})
    assert report['ok'] is False and checks(report)['freshness']['ok'] is False


def test_fails_without_a_deepseek_harness_agent(env, tmp_path):
    from memory_fixture import SESSIONS
    build_av_db(tmp_path / 'nodsh' / 'sessions.db', sessions=[s for s in SESSIONS if s[1] != 'deepseek-harness'])
    (tmp_path / 'nodsh' / 'config.toml').write_text((env['av_dir'] / 'config.toml').read_text())
    report = doctor.run(**{**env, 'av_dir': tmp_path / 'nodsh'})
    assert report['ok'] is False and checks(report)['deepseek_harness_agent']['ok'] is False


def test_fails_on_a_missing_claude_home(env, tmp_path):
    report = doctor.run(**{**env, 'claude_home': tmp_path / 'home' / '.claude-gone'})
    assert report['ok'] is False and checks(report)['claude_home_indexed']['ok'] is False


def test_fails_on_a_user_version_data_version_mismatch(env, tmp_path):
    report = doctor.run(**{**env, 'agentsview_bin': fake_bin(tmp_path / 'av44', '0.44.0')})
    assert report['ok'] is False
    assert checks(report)['data_version']['ok'] is False
    assert '74' in checks(report)['data_version']['detail'] and '113' in checks(report)['data_version']['detail']


def test_reports_an_unreachable_gateway(env):
    cfg = {'tencentdb': {'url': 'http://127.0.0.1:9', 'auth_env': 'Z0_FAKE_TDB_ENV', 'deadline_ms': 200}}
    report = doctor.run(**{**env, 'config': cfg})
    assert checks(report)['tencentdb']['status'] == 'unreachable'


def test_output_never_contains_config_secret_values(env, capsys, monkeypatch):
    cfg = {'tencentdb': {'url': 'http://127.0.0.1:9', 'auth_env': 'Z0_FAKE_TDB_ENV', 'deadline_ms': 200}}
    report = doctor.run(**{**env, 'config': cfg})
    from z0int.memory import cli
    monkeypatch.setenv('AGENTSVIEW_DATA_DIR', str(env['av_dir']))
    rc = cli.main(['doctor', '--json', '--agentsview-bin', str(env['agentsview_bin']),
                   '--claude-home', str(env['claude_home'])])
    out = capsys.readouterr()
    assert rc == 0
    for blob in (json.dumps(report), out.out, out.err):
        assert AUTH_VALUE not in blob and ENV_VALUE not in blob
    assert json.loads(out.out)['ok'] is True
