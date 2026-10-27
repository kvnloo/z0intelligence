"""`z0int memory doctor`: read-only health of the memory substrate; never prints a config secret."""
from __future__ import annotations

import json
import stat

import pytest

from memory_fixture import FakeTencentDB, build_av_db
from z0int.memory import doctor

AUTH_VALUE = 'FAKE-AGENTSVIEW-AUTH-VALUE-0001'
ENV_VALUE = 'FAKE-TDB-ENV-VALUE-0002'


def fake_bin(path, version='0.39.0'):
    path.write_text(f'#!/bin/sh\necho "agentsview v{version} (commit fake, built fake)"\n')
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return path


def fake_ctx(path, body=None):
    ready = json.dumps({'lexical': {'status': 'ready'}, 'history_epoch': {'generation_path': 'g1'}})
    path.write_text('#!/bin/sh\n' + (body if body is not None else f"echo '{ready}'\n"))
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
            'config': {}, 'ctx_bin': fake_ctx(tmp_path / 'ctx')}


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


def test_reports_a_reachable_gateway_as_available_and_unversioned(env):
    """The real gateway's /health carries only its software version: reachable, with no content revision."""
    with FakeTencentDB(version='0.9.1') as gw:
        report = doctor.run(**{**env, 'config': gw.config(auth_env='Z0_FAKE_TDB_ENV')})
    c = checks(report)['tencentdb']
    assert (c['status'], c['ok']) == ('reachable', True)
    assert 'available' in c['detail'] and 'UNAVAILABLE' not in c['detail'] and 'unversioned' in c['detail']


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


def test_ctx_timeout_fails_instead_of_passing(env, tmp_path):
    slow = fake_ctx(tmp_path / 'ctx-slow', 'sleep 5\n')
    report = doctor.run(**{**env, 'ctx_bin': slow, 'ctx_timeout': 0.5})
    c = checks(report)['ctx_lexical']
    assert report['ok'] is False and c['ok'] is False and 'TimeoutExpired' in c['detail']


def test_ctx_not_ready_fails(env, tmp_path):
    body = "echo '" + json.dumps({'lexical': {'status': 'stale', 'reason': 'stale_lock'}, 'history': {'status': 'unavailable'}}) + "'\n"
    report = doctor.run(**{**env, 'ctx_bin': fake_ctx(tmp_path / 'ctx-stale', body)})
    c = checks(report)['ctx_lexical']
    assert c['ok'] is False and 'stale_lock' in c['detail']


def test_ctx_not_installed_is_fine(env, tmp_path, monkeypatch):
    monkeypatch.setattr(doctor, 'resolve_bin', lambda name, explicit=None: None if name == 'ctx' else str(env['agentsview_bin']))
    report = doctor.run(**{**env, 'ctx_bin': None})
    assert checks(report)['ctx_lexical'] == {**checks(report)['ctx_lexical'], 'ok': True, 'status': 'not_installed'}


def test_binaries_resolve_without_path(env, tmp_path, monkeypatch):
    home = tmp_path / 'h'
    (home / '.local/bin').mkdir(parents=True)
    fake_bin(home / '.local/bin' / 'agentsview', '0.39.0')
    fake_ctx(home / '.local/bin' / 'ctx')
    monkeypatch.setenv('HOME', str(home))
    monkeypatch.setenv('PATH', '/nonexistent')
    monkeypatch.delenv('Z0INT_AGENTSVIEW_BIN', raising=False)
    monkeypatch.delenv('Z0INT_CTX_BIN', raising=False)
    report = doctor.run(**{**env, 'agentsview_bin': None, 'ctx_bin': None})
    c = checks(report)
    assert c['data_version']['ok'] is True, c['data_version']
    assert c['ctx_lexical']['status'] == 'ready' and c['ctx_lexical']['path'] == str(home / '.local/bin' / 'ctx')


def _with_sources(env, tmp_path, rows, labels=()):
    import sqlite3
    db = env['av_dir'] / 'sessions.db'
    conn = sqlite3.connect(db)
    conn.execute('create table if not exists pg_sync_state (key text primary key, value text)')
    conn.execute("insert or replace into pg_sync_state values ('artifact_local_installation_id', 'inst1')")
    for k, v in labels:
        conn.execute('insert or replace into pg_sync_state values (?, ?)', (k, v))
    for sid, agent, machine, started, path in rows:
        conn.execute('insert into sessions (id, project, agent, machine, started_at, file_path) values (?,?,?,?,?,?)',
                     (sid, 'z0', agent, machine, started, path))
    conn.commit()
    conn.close()


def test_a_local_source_behind_disk_fails_even_when_newest_session_is_fresh(env, tmp_path):
    import os, time
    from memory_fixture import iso
    now = time.time()
    proj = tmp_path / 'cl' / 'projects' / 'p'
    proj.mkdir(parents=True)
    f = proj / 's.jsonl'
    f.write_text('{}\n')
    os.utime(f, (now - 60, now - 60))
    _with_sources(env, tmp_path, [('s-old', 'claude', 'inst1', iso(now - 30 * 3600), str(f))])
    report = doctor.run(**env, now=now)
    c = checks(report)
    assert c['freshness']['ok'] is True          # the newest session overall is fresh
    assert c['source_freshness']['ok'] is False  # but this source is 30 h behind its disk
    assert report['ok'] is False


def test_remote_host_staleness_is_per_host_and_uses_labels(env, tmp_path):
    import time
    from memory_fixture import iso
    now = time.time()
    _with_sources(env, tmp_path, [('m1', 'claude', 'far-laptop', iso(now - 72 * 3600), '/x/m1.jsonl'),
                                  ('m2', 'claude', 'peer', iso(now - 2 * 3600), '/x/m2.jsonl'),
                                  ('g1', 'claude', 'inst1', iso(now - 3600), '/nonexistent/g1.jsonl')],
                  labels=[('machine_label:inst1', 'groot')])
    c = checks(doctor.run(**env, now=now))
    assert c['remote_host_freshness']['ok'] is False
    assert c['remote_host_freshness']['stale'] == ['far-laptop: newest session 72 h old']


def test_recall_layer_absent_is_expected(env):
    c = checks(doctor.run(**env))['recall_layer']
    assert c['ok'] is True and 'by design' in c['detail']
