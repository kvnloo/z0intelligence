"""O16 executable regressions, synthetic private stores only; no OMP signals."""
import datetime as dt
import itertools
import json
import os
import sqlite3
from pathlib import Path

import pytest

from z0int.fsm import guard as G, ops as P, sqlite_guard as S


def store(path, count=9, wal=False):
    con = sqlite3.connect(path)
    if wal:
        con.execute("PRAGMA journal_mode=WAL")
    con.execute("CREATE TABLE auth_credentials (secret TEXT)")
    con.executemany("INSERT INTO auth_credentials VALUES (?)", [("NEVER_PRINT_THIS",)] * count)
    con.commit()
    return con


def baseline(path, database, count=9, **extra):
    rec = {"schema": S.SCHEMA, "db": str(database), "auth_credentials_count": count,
           "ts": dt.datetime.now(dt.timezone.utc).isoformat(), **extra}
    path.write_text(json.dumps(rec))
    return path


def test_boolean_tables_exhaustive():
    for complete, flag in itertools.product((False, True), repeat=2):
        assert P.sqlite_replace_allows(complete, flag) == (complete and flag)
        assert P.sqlite_holders_ok(complete, flag) == (complete and not flag)
    for known, before, after in itertools.product((False, True), range(11), range(11)):
        assert P.sqlite_auth_ok(known, before, after) == (known and before <= after)
    assert not P.sqlite_sidecar_allows(False)
    assert P.sqlite_sidecar_allows(True)


def test_real_deleted_fd_is_flagged_without_signals(tmp_path):
    db = tmp_path / "agent.db"
    db.write_bytes(b"old")
    with db.open("rb") as old:
        db.unlink()
        db.write_bytes(b"new")
        scan = S.scan_holders(db)
        h = next(h for h in scan["holders"] if h["pid"] == os.getpid() and h["fd"] == old.fileno())
        assert h["deleted"] and h["inode_mismatch"] and h["ghost"]
        d = S.sqlite_decision(complete=True, holders=scan["holders"], before=9, after=9, db=str(db))
        assert os.getpid() in d["ghost_pids"]
        assert d["violations"][0]["law"] == "o16_sqlite_no_ghost_holders"


@pytest.mark.parametrize("suffix", ["", "-wal", "-shm"])
def test_each_deleted_guarded_artifact(tmp_path, suffix):
    db = tmp_path / "agent.db"
    db.write_bytes(b"db")
    target = Path(str(db) + suffix)
    if suffix:
        target.write_bytes(b"sidecar")
    with target.open("rb") as old:
        target.unlink()
        scan = S.scan_holders(db)
        assert any(h["pid"] == os.getpid() and h["fd"] == old.fileno() and h["ghost"] for h in scan["holders"])


def test_mismatched_non_deleted_fd(tmp_path, monkeypatch):
    db, old, proc = tmp_path / "agent.db", tmp_path / "old", tmp_path / "proc"
    db.write_bytes(b"new"); old.write_bytes(b"old")
    fd = proc / "42/fd/7"
    fd.parent.mkdir(parents=True); fd.symlink_to(old)
    original = os.readlink
    monkeypatch.setattr(os, "readlink", lambda p: str(db) if Path(p) == fd else original(p))
    scan = S.scan_holders(db, proc)
    assert scan["complete"] and scan["holders"][0]["inode_mismatch"]
    assert not scan["holders"][0]["deleted"]


def test_current_inode_hardlink_alias_is_holder(tmp_path):
    db, alias, proc = tmp_path / "agent.db", tmp_path / "alias", tmp_path / "proc"
    db.write_bytes(b"new"); alias.hardlink_to(db)
    fd = proc / "42/fd/7"
    fd.parent.mkdir(parents=True); fd.symlink_to(alias)
    scan = S.scan_holders(db, proc)
    assert scan["complete"] and len(scan["holders"]) == 1 and not scan["holders"][0]["ghost"]


def test_unreadable_proc_fails_closed(tmp_path, monkeypatch):
    db, proc = tmp_path / "agent.db", tmp_path / "proc"
    db.write_bytes(b"db"); (proc / "42/fd").mkdir(parents=True)
    original = Path.iterdir
    def denied(path):
        if path == proc / "42/fd":
            raise PermissionError()
        return original(path)
    monkeypatch.setattr(Path, "iterdir", denied)
    scan = S.scan_holders(db, proc)
    assert not scan["complete"] and scan["unreadable_pids"] == [42]
    assert not P.sqlite_replace_allows(scan["complete"], not scan["holders"])


def test_missing_proc_is_unknown(tmp_path):
    db = tmp_path / "agent.db"
    db.write_bytes(b"db")
    assert not S.scan_holders(db, tmp_path / "missing")["complete"]


def test_wal_auth_count_and_no_live_side_effects(tmp_path):
    db = tmp_path / "agent.db"
    con = store(db, wal=True)
    try:
        paths = [db, Path(str(db) + "-wal"), Path(str(db) + "-shm")]
        before = [(p.read_bytes(), S._stamp(p)) for p in paths]
        assert S.auth_count(db, tmp_path) == (9, None)
        assert [(p.read_bytes(), S._stamp(p)) for p in paths] == before
        assert not list(tmp_path.glob("sqlite-guard-*"))
    finally:
        con.close()


def test_changed_snapshot_is_unknown(tmp_path, monkeypatch):
    db = tmp_path / "agent.db"
    store(db).close()
    original = S.shutil.copyfileobj
    def changing(inp, out):
        original(inp, out)
        os.utime(db, ns=(1, 1))
    monkeypatch.setattr(S.shutil, "copyfileobj", changing)
    assert S.auth_count(db, tmp_path) == (None, "store-changed-during-copy")


@pytest.mark.parametrize("kind", ["missing", "corrupt", "no-table"])
def test_count_failure_is_not_zero(tmp_path, kind):
    db = tmp_path / "agent.db"
    if kind == "corrupt":
        db.write_bytes(b"not sqlite")
    elif kind == "no-table":
        sqlite3.connect(db).close()
    count, error = S.auth_count(db, tmp_path)
    assert count is None and error


@pytest.mark.parametrize("extra", [{"db": "/other"}, {"schema": "other"}, {"auth_credentials_count": True},
                                  {"auth_credentials_count": -1}, {"ts": "2099-01-01T00:00:00+00:00"},
                                  {"ts": "2026-01-01"}])
def test_invalid_baselines_are_not_credit(tmp_path, extra):
    db = tmp_path / "agent.db"
    b = baseline(tmp_path / "last.json", db, **extra)
    assert S.baseline_count(b, db) == (None, "baseline-invalid")


def test_auth_regression_and_no_credentials_in_receipt(tmp_path):
    db, proc = tmp_path / "agent.db", tmp_path / "proc"
    store(db, 0).close(); proc.mkdir()
    b = baseline(tmp_path / "last.json", db)
    rec = S.sqlite_receipt(db, b, proc=proc)
    assert rec["available"] and rec["violations"] == [
        {"law": "o16_sqlite_auth_non_decreasing", "before": 9, "after": 0}]
    assert "NEVER_PRINT_THIS" not in json.dumps(rec)
    assert rec["authorization"] is False


def test_healthy_read_has_no_state_writes(tmp_path):
    db, proc = tmp_path / "agent.db", tmp_path / "proc"
    store(db).close(); proc.mkdir()
    b = baseline(tmp_path / "last.json", db)
    before = {p.name: p.read_bytes() for p in (db, b)}
    rec = S.sqlite_receipt(db, b, proc=proc)
    assert rec["available"] and not rec["violations"]
    assert rec["requirements_met"]["replacement"]
    assert {p.name: p.read_bytes() for p in (db, b)} == before


@pytest.mark.parametrize("operation", ["replace", "unlink", "recreate"])
def test_live_holders_refuse_proposed_replacement(operation):
    h = [{"pid": 1826151, "path": "/store", "ghost": True}]
    rec = S.sqlite_decision(complete=True, holders=h, before=9, after=9, db="/store", operation=operation)
    assert not rec["requirements_met"]["replacement"]
    assert {v["law"] for v in rec["violations"]} == {"o16_sqlite_no_ghost_holders", "o16_sqlite_replace_no_holders"}


def test_sidecar_actor_must_hold_current_db_not_just_wal():
    for holders, expected in [([], False), ([{"pid": 1, "path": "/store-wal", "ghost": False}], False),
                              ([{"pid": 1, "path": "/store", "ghost": True}], False),
                              ([{"pid": 1, "path": "/store", "ghost": False}], True)]:
        d = S.sqlite_decision(complete=True, holders=holders, before=9, after=9, db="/store",
                              operation="remove-sidecars", actor_pid=1)
        assert d["requirements_met"]["sidecar_actor_current"] == expected


def test_unknown_auth_recreation_refused():
    d = S.sqlite_decision(complete=True, holders=[], before=None, after=0, db="/store", operation="recreate")
    assert d["violations"] == [{"law": "o16_sqlite_auth_known", "pids": []}]


def test_cli_read_only_and_violation_exit(tmp_path, monkeypatch, capsys):
    db, proc = tmp_path / "agent.db", tmp_path / "proc"
    store(db, 1).close(); proc.mkdir()
    b = baseline(tmp_path / "last.json", db)
    original = S.sqlite_receipt
    monkeypatch.setattr(S, "sqlite_receipt", lambda *a, **k: original(*a, **k, proc=proc))
    monkeypatch.setattr(G, "STATE", tmp_path / "state-must-not-exist")
    monkeypatch.setattr(G, "CONFIG", tmp_path / "config")
    monkeypatch.setenv("Z0_GUARD_O16", "enforce")
    assert G.main(["sqlite-ghosts", "--db", str(db), "--last-receipt", str(b)]) == 3
    out = capsys.readouterr().out
    assert "NEVER_PRINT_THIS" not in out and json.loads(out)["auth_credentials_count"] == 1
    assert not G.STATE.exists()
    assert G.main(["sqlite-ghosts", "--db", str(db)]) == 4
