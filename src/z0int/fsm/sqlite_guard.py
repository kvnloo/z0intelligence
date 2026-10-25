"""Read-only O16 observations. Never open the live store through SQLite.

Only /proc links, inode metadata, and ordinary O_RDONLY file reads touch the
live store. SQLite runs on a private, stability-checked DB+WAL copy (not SHM).
This is a bounded census, not an atomic snapshot, cross-process lock, or repair.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import shutil
import sqlite3
import tempfile
from pathlib import Path

from . import ops as P

SCHEMA = "z0.sqlite_guard.v1"


def identity(path: Path) -> dict | None:
    try:
        s = path.stat()
        return {"device": s.st_dev, "inode": s.st_ino}
    except FileNotFoundError:
        return None


def _stamp(path: Path):
    try:
        s = path.stat()
        return s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns, s.st_ctime_ns
    except FileNotFoundError:
        return None


def scan_holders(db: Path, proc: Path = Path("/proc")) -> dict:
    targets = {str(db) + s: identity(Path(str(db) + s)) for s in ("", "-wal", "-shm")}
    holders, unavailable = [], set()
    try:
        processes = sorted((p for p in proc.iterdir() if p.name.isdigit()), key=lambda p: int(p.name))
    except OSError:
        return {"holders": [], "complete": False, "unreadable_pids": [], "error": "proc-unreadable"}
    for process in processes:
        try:
            fds = list((process / "fd").iterdir())
        except FileNotFoundError:
            continue  # exited between directory enumeration and fd scan
        except OSError:
            unavailable.add(int(process.name))
            continue
        for fd in fds:
            try:
                link = os.readlink(fd)
                stat = fd.stat()
            except FileNotFoundError:
                continue  # fd closed while scanning
            except OSError:
                unavailable.add(int(process.name))
                continue
            observed = {"device": stat.st_dev, "inode": stat.st_ino}
            target = link.removesuffix(" (deleted)")
            # Also find hard-link aliases to a current guarded inode.
            if target not in targets:
                target = next((p for p, ident in targets.items() if ident == observed), None)
            if target is None:
                continue
            deleted = link.endswith(" (deleted)")
            mismatch = targets[target] != observed
            holders.append({"pid": int(process.name), "fd": int(fd.name), "path": target,
                            **observed, "deleted": deleted, "inode_mismatch": mismatch,
                            "ghost": deleted or mismatch})
    stable = targets == {p: identity(Path(p)) for p in targets}
    return {"holders": sorted(holders, key=lambda h: (h["pid"], h["fd"])),
            "complete": not unavailable and stable, "unreadable_pids": sorted(unavailable),
            "paths_stable": stable}


def auth_count(db: Path, scratch: Path | None = None) -> tuple[int | None, str | None]:
    """Count only, on a private DB+WAL copy. Concurrent mutation means unknown.

    Stat stability is tested before/after copying AND after querying the copy.
    No SHM is copied: SQLite reconstructs it only in the private directory.
    This deliberately avoids mode=ro against live SQLite, which can write SHM.
    """
    sources = [db, Path(str(db) + "-wal")]
    try:
        before = [_stamp(p) for p in sources]
        if before[0] is None:
            return None, "database-missing"
        if sum(s[2] for s in before if s) > 256 * 1024 * 1024:
            return None, "snapshot-size-limit"
        with tempfile.TemporaryDirectory(prefix="sqlite-guard-", dir=scratch or os.environ.get("TMPDIR")) as td:
            copy = Path(td) / "agent.db"
            for src, stamp in zip(sources, before):
                if stamp is not None:
                    dst = Path(str(copy) + ("-wal" if src != db else ""))
                    with src.open("rb") as inp, dst.open("xb") as out:
                        os.chmod(dst, 0o600)
                        shutil.copyfileobj(inp, out)
            if before != [_stamp(p) for p in sources]:
                return None, "store-changed-during-copy"
            # The copied directory may be modified; never the source store.
            con = sqlite3.connect(copy, timeout=1)
            try:
                con.execute("PRAGMA query_only=ON")
                count = con.execute("SELECT count(*) FROM auth_credentials NOT INDEXED").fetchone()[0]
            finally:
                con.close()
            if before != [_stamp(p) for p in sources]:
                return None, "store-changed-during-count"
            return count, None
    except (OSError, sqlite3.Error):
        # Errors are labels only: never leak SQL, schema details or row contents.
        return None, "auth-count-unavailable"


def baseline_count(path: Path | None, db: Path) -> tuple[int | None, str | None]:
    if path is None:
        return None, "baseline-missing"
    try:
        rec = json.loads(path.read_text())
        count = rec.get("auth_credentials_count")
        ts = dt.datetime.fromisoformat(rec["ts"])
        valid = (rec.get("schema") == SCHEMA and rec.get("db") == str(db)
                 and type(count) is int and count >= 0 and ts.tzinfo is not None
                 and ts <= dt.datetime.now(dt.timezone.utc))
        if valid:
            return count, None
    except FileNotFoundError:
        return None, "baseline-missing"
    except (OSError, ValueError, TypeError, KeyError, AttributeError):
        pass
    return None, "baseline-invalid"


def sqlite_decision(*, complete: bool, holders: list[dict], before: int | None,
                    after: int | None, operation: str = "inspect", actor_pid: int | None = None,
                    db: str) -> dict:
    ghosts = [h for h in holders if h["ghost"]]
    pids = sorted({h["pid"] for h in holders})
    violations = []
    if ghosts:
        violations.append({"law": "o16_sqlite_no_ghost_holders", "pids": sorted({h["pid"] for h in ghosts})})
    known = before is not None and after is not None
    if known and not P.sqlite_auth_ok(True, before, after):
        violations.append({"law": "o16_sqlite_auth_non_decreasing", "before": before, "after": after})
    empty = not holders
    replace = P.sqlite_replace_allows(complete, empty)
    current = (actor_pid is not None and any(h["pid"] == actor_pid and h["path"] == db and not h["ghost"]
                                          for h in holders)
               and not any(h["pid"] == actor_pid for h in ghosts))
    sidecars = P.sqlite_sidecar_allows(current)
    if operation in ("replace", "unlink", "recreate") and not replace:
        violations.append({"law": "o16_sqlite_replace_no_holders" if not empty else
                           "o16_sqlite_replace_scan_complete", "pids": pids})
    if operation == "recreate" and not P.sqlite_auth_ok(known, before or 0, after or 0):
        if not known:
            violations.append({"law": "o16_sqlite_auth_known", "pids": []})
    if operation == "remove-sidecars" and not sidecars:
        violations.append({"law": "o16_sqlite_sidecar_current_inode", "pids": [actor_pid] if actor_pid else []})
    return {"violations": violations, "pids": pids, "ghost_pids": sorted({h["pid"] for h in ghosts}),
            "requirements_met": {"replacement": replace, "holder_health": P.sqlite_holders_ok(complete, bool(ghosts)),
                                 "auth_preserved": P.sqlite_auth_ok(known, before or 0, after or 0),
                                 "sidecar_actor_current": sidecars},
            "authorization": False}


def sqlite_receipt(db: Path, last_receipt: Path | None = None, *, proc: Path = Path("/proc"),
                   operation: str = "inspect", actor_pid: int | None = None) -> dict:
    db = db.expanduser().resolve()
    errors = []
    try:
        current = {str(db) + s: identity(Path(str(db) + s)) for s in ("", "-wal", "-shm")}
        scan = scan_holders(db, proc)
        before, baseline_error = baseline_count(last_receipt, db)
        after, count_error = auth_count(db)
        final = {p: identity(Path(p)) for p in current}
        if current != final:
            scan["complete"] = False
            errors.append("store-identity-changed")
    except OSError:
        current = {}
        scan = {"holders": [], "complete": False, "unreadable_pids": []}
        before = after = None
        baseline_error = count_error = "observation-unavailable"
    errors += [x for x in (baseline_error, count_error) if x]
    if not scan["complete"]:
        errors.append("holder-census-incomplete")
    d = sqlite_decision(complete=scan["complete"], holders=scan["holders"], before=before,
                        after=after, operation=operation, actor_pid=actor_pid, db=str(db))
    return {"schema": SCHEMA, "ts": dt.datetime.now(dt.timezone.utc).isoformat(), "db": str(db),
            "gate": "O16", "operation": operation, "read_only": True, "current": current,
            "scan": scan, "auth_credentials_count": after, "previous_auth_credentials_count": before,
            "last_receipt": str(last_receipt) if last_receipt else None,
            "available": not errors, "unavailable_reasons": errors, **d,
            "recommended_action": ("Do not replace/unlink/recreate the store or remove sidecars. "
                                   "Preserve evidence; request owner-approved quiescence and coordinated repair. "
                                   "No signals, restarts, checkpoints or repairs were performed."),
            "limitations": ["Point-in-time /proc census; not a lock or mutation authorization.",
                            "Inaccessible processes and concurrent store changes fail closed.",
                            "Only FD holders in this proc namespace are observable; mmap-only holders are outside scope.",
                            "Auth count is from a stability-checked private DB+WAL copy, not a transactional backup.",
                            "Count preservation cannot prove credential identity or validity."]}


def receipt_key(db: Path) -> str:
    return hashlib.sha256(str(db.expanduser().resolve()).encode()).hexdigest()
