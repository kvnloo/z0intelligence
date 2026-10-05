"""Read-only AgentsView access shared by the turn readers (labels) and the memory surface.

One way to open ``sessions.db``: sqlite ``mode=ro`` (a write raises), a guard on ``pragma user_version`` for
the two data versions this code knows (74 = v0.39, 113 = v0.44), and staleness from the newest session. Any
problem comes back as an ``Unavailable`` value, never as an exception into the caller.
"""

from __future__ import annotations

import calendar
import os
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote

SUPPORTED_USER_VERSIONS = (74, 113)


@dataclass(frozen=True)
class Unavailable:
    reason: str  # missing | locked | schema | error
    detail: str = ''

    def __bool__(self) -> bool:
        return False


def data_dir() -> Path:
    return Path(os.environ.get('AGENTSVIEW_DATA_DIR') or Path.home() / '.agentsview').expanduser()


def db_path(directory: str | Path | None = None) -> Path:
    return Path(directory).expanduser() / 'sessions.db' if directory else data_dir() / 'sessions.db'


def connect(path: str | Path | None = None, *, timeout: float = 2.0) -> sqlite3.Connection | Unavailable:
    p = Path(path) if path else db_path()
    if not p.is_file():
        return Unavailable('missing', str(p.name))
    try:
        conn = sqlite3.connect(f'file:{quote(str(p))}?mode=ro', uri=True, timeout=timeout)
    except sqlite3.Error as exc:
        return Unavailable('error', type(exc).__name__)
    try:
        version = conn.execute('pragma user_version').fetchone()[0]
    except sqlite3.OperationalError as exc:
        conn.close()
        return Unavailable('locked' if 'locked' in str(exc) or 'busy' in str(exc) else 'error', type(exc).__name__)
    except sqlite3.Error as exc:
        conn.close()
        return Unavailable('error', type(exc).__name__)
    if version not in SUPPORTED_USER_VERSIONS:
        conn.close()
        return Unavailable('schema', f'user_version {version} not in {SUPPORTED_USER_VERSIONS}')
    return conn


def staleness_hours(conn: sqlite3.Connection, now: float | None = None) -> float | None:
    """Hours since the newest session started; None when there is no session to measure from."""
    try:
        newest = conn.execute('select max(started_at) from sessions').fetchone()[0]
        t = calendar.timegm(time.strptime(str(newest)[:19], '%Y-%m-%dT%H:%M:%S'))
    except (sqlite3.Error, ValueError, TypeError):
        return None
    return ((time.time() if now is None else now) - t) / 3600.0
