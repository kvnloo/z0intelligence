"""Shared read-only AgentsView connection (consumed by the turn readers and the memory surface)."""
import calendar
import sqlite3
import time

import pytest

from z0int import agentsview_ro as av


def fixture_db(path, user_version=74, started_at='2026-10-03T00:00:00Z'):
    conn = sqlite3.connect(path)
    conn.execute('create table sessions (id text primary key, agent text, started_at text)')
    conn.execute('insert into sessions values (?, ?, ?)', ('hermes:s1', 'hermes', started_at))
    conn.execute(f'pragma user_version = {int(user_version)}')
    conn.commit()
    conn.close()
    return path


@pytest.mark.parametrize('version', av.SUPPORTED_USER_VERSIONS)
def test_connect_is_read_only_for_supported_versions(tmp_path, version):
    assert set(av.SUPPORTED_USER_VERSIONS) == {74, 113}
    conn = av.connect(fixture_db(tmp_path / 'sessions.db', version))
    assert isinstance(conn, sqlite3.Connection)
    assert conn.execute('select count(*) from sessions').fetchone()[0] == 1
    with pytest.raises(sqlite3.OperationalError):
        conn.execute("insert into sessions values ('x', 'codex', '2026-10-03T00:00:00Z')")
    conn.close()


def test_unsupported_schema_is_unavailable_not_an_exception(tmp_path):
    got = av.connect(fixture_db(tmp_path / 'sessions.db', 75))
    assert isinstance(got, av.Unavailable) and got.reason == 'schema' and not got
    assert '75' in got.detail


def test_missing_and_locked_databases_are_unavailable(tmp_path):
    missing = av.connect(tmp_path / 'nope' / 'sessions.db')
    assert isinstance(missing, av.Unavailable) and missing.reason == 'missing'
    path = fixture_db(tmp_path / 'sessions.db')
    holder = sqlite3.connect(path, isolation_level=None)
    holder.execute('begin exclusive')
    try:
        locked = av.connect(path, timeout=0.1)
        assert isinstance(locked, av.Unavailable) and locked.reason == 'locked'
    finally:
        holder.execute('rollback')
        holder.close()


def test_default_path_follows_agentsview_data_dir(tmp_path, monkeypatch):
    monkeypatch.setenv('AGENTSVIEW_DATA_DIR', str(tmp_path))
    assert av.db_path() == tmp_path / 'sessions.db'
    assert isinstance(av.connect(), av.Unavailable)


def test_staleness_is_measured_from_the_newest_session(tmp_path):
    conn = av.connect(fixture_db(tmp_path / 'sessions.db', started_at='2026-10-01T00:00:00Z'))
    now = calendar.timegm(time.strptime('2026-10-03T00:00:00', '%Y-%m-%dT%H:%M:%S'))
    assert av.staleness_hours(conn, now=now) == pytest.approx(48.0)
    conn.close()
