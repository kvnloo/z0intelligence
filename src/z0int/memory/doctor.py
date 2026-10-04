"""`z0int memory doctor`: read-only health of the memory substrate.

Fails on: a missing/locked/unknown AgentsView DB, a stale index, no ``deepseek-harness`` agent, the Claude home
(``CLAUDE_CONFIG_DIR`` or ``~/.claude-home``) missing or not indexed, a DB ``user_version`` that differs from the
installed binary's dataVersion, and ``require_auth`` off. Reports (without failing) the TencentDB gateway as
not_configured / unreachable / reachable and whether the AgentsView recall layer is present. Values from config
files are never echoed: ``require_auth`` is reported as a boolean only.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import sqlite3
import subprocess
from pathlib import Path
from typing import Any, Mapping

from .. import agentsview_ro
from .scrub import scrub_obj
from .surface import TencentDBClient, load_config

SCHEMA = 'z0int.memory.doctor.v0'
#: AgentsView release -> sessions.db dataVersion (``internal/db/db.go`` const dataVersion).
KNOWN_DATA_VERSIONS = {(0, 39): 74, (0, 44): 113}
STALE_HOURS = 24.0


def _check(name: str, ok: bool, detail: str = '', **kw: Any) -> dict[str, Any]:
    return {'name': name, 'ok': bool(ok), 'detail': detail, **kw}


def _av_config(av_dir: Path) -> dict[str, Any] | None:
    try:
        import tomllib
        return tomllib.loads((av_dir / 'config.toml').read_text(encoding='utf-8'))
    except (ImportError, OSError, ValueError):
        return None


def _binary_data_version(binary: str | Path | None) -> tuple[int | None, str]:
    path = binary or shutil.which('agentsview')
    if not path:
        return None, 'agentsview binary not found'
    try:
        proc = subprocess.run([str(path), 'version'], capture_output=True, text=True, timeout=10, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return None, f'agentsview version failed ({type(exc).__name__})'
    m = re.search(r'v?(\d+)\.(\d+)\.\d+', proc.stdout)
    if not m:
        return None, 'agentsview version output not understood'
    release = (int(m.group(1)), int(m.group(2)))
    version = KNOWN_DATA_VERSIONS.get(release)
    return version, f'agentsview {release[0]}.{release[1]}' + ('' if version else ' (dataVersion unknown)')

def _ctx_lexical(command: str | None = None) -> dict[str, Any]:
    """Read-only. A missing binary or a failed status is an explicit unavailable, never an omitted check."""
    cmd = command if command is not None else shutil.which('ctx')
    if not cmd:
        return _check('ctx_lexical', True, 'unavailable (ctx not on PATH)', status='unavailable')
    try:
        proc = subprocess.run([cmd, 'status', '--format', 'json', '--quiet'], capture_output=True, text=True,
                              timeout=8, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return _check('ctx_lexical', True, f'unavailable ({type(exc).__name__})', status='unavailable')
    try:
        payload = json.loads(proc.stdout)
    except (json.JSONDecodeError, TypeError):
        detail = (proc.stderr or proc.stdout or 'status not json').strip().splitlines()
        return _check('ctx_lexical', True, 'unavailable (' + (detail[-1] if detail else 'no status') + ')',
                      status='unavailable')
    lexical = payload.get('lexical') if isinstance(payload, dict) else None
    epoch = payload.get('history_epoch') if isinstance(payload, dict) else None
    lexical_status = lexical.get('status') if isinstance(lexical, dict) else None
    reason = lexical.get('reason') if isinstance(lexical, dict) else None
    generation = epoch.get('generation_path') if isinstance(epoch, dict) else None
    if lexical_status == 'ready':
        return _check('ctx_lexical', True, f'ready generation {generation or "unnamed"}', status='ready',
                      generation=generation)
    return _check('ctx_lexical', True,
                  f'unavailable ({lexical_status or "missing"}{": " + reason if reason else ""})',
                  status='unavailable', generation=generation)



def run(*, av_dir: str | Path | None = None, agentsview_bin: str | Path | None = None,
        claude_home: str | Path | None = None, config: Mapping[str, Any] | None = None,
        stale_hours: float = STALE_HOURS, now: float | None = None) -> dict[str, Any]:
    av_dir = Path(av_dir).expanduser() if av_dir else agentsview_ro.data_dir()
    claude_home = Path(claude_home or os.environ.get('CLAUDE_CONFIG_DIR') or Path.home() / '.claude-home').expanduser()
    checks: list[dict[str, Any]] = []
    conn = agentsview_ro.connect(agentsview_ro.db_path(av_dir), timeout=1.0)
    if not conn:
        checks.append(_check('agentsview_db', False, f'unavailable ({conn.reason})'))
    else:
        err = None
        try:
            user_version = conn.execute('pragma user_version').fetchone()[0]
            lag = agentsview_ro.staleness_hours(conn, now=now)
            dsh = conn.execute("select 1 from sessions where agent = 'deepseek-harness' limit 1").fetchone()
            recall = conn.execute("select 1 from sqlite_master where name = 'recall_entries'").fetchone()
            recall_n = conn.execute('select count(*) from recall_entries').fetchone()[0] if recall else 0
        except sqlite3.Error as exc:
            err = type(exc).__name__
        finally:
            conn.close()
        if err:
            checks.append(_check('agentsview_db', False, f'unreadable ({err})'))
        else:
            checks.append(_check('agentsview_db', True, f'user_version {user_version}'))
            checks.append(_check('freshness', lag is not None and lag <= stale_hours,
                                 'no session to measure from' if lag is None else f'newest session {lag:.1f} h old '
                                 f'(limit {stale_hours:g} h)', lag_hours=None if lag is None else round(lag, 2)))
            checks.append(_check('deepseek_harness_agent', bool(dsh),
                                 'present' if dsh else 'no deepseek-harness sessions indexed'))
            binary_version, detail = _binary_data_version(agentsview_bin)
            checks.append(_check('data_version', binary_version == user_version,
                                 f'DB user_version {user_version}, {detail}'
                                 + (f' dataVersion {binary_version}' if binary_version else '')))
            checks.append(_check('recall_layer', True, f'{recall_n} recall entries' if recall else 'no recall table',
                                 status='present' if recall_n else 'absent'))
    cfg = _av_config(av_dir)
    claude = (cfg or {}).get('agents', {}).get('claude', {}) if isinstance((cfg or {}).get('agents'), dict) else {}
    dirs = [*(claude.get('homes') or []), *((cfg or {}).get('claude_project_dirs') or [])]
    listed = any(Path(str(d)).expanduser().resolve() in (claude_home.resolve(), (claude_home / 'projects').resolve())
                 for d in dirs)
    checks.append(_check('claude_home_indexed', claude_home.is_dir() and listed,
                         ('present' if claude_home.is_dir() else 'directory missing')
                         + (', indexed by agentsview config' if listed else ', not in agentsview config')))
    checks.append(_check('require_auth', bool(cfg) and cfg.get('require_auth') is True,
                         'config unreadable' if cfg is None else f"require_auth={cfg.get('require_auth') is True}"))
    gateway = TencentDBClient(load_config() if config is None else config)
    rev = gateway.revision()
    status = rev.split(':', 1)[1] if rev.startswith('unavailable:') else 'reachable'
    checks.append(_check('tencentdb', True, 'semantic layer ' + (f'available ({rev})' if status == 'reachable' else
                                                                  f'UNAVAILABLE ({status})'), status=status))
    checks.append(_ctx_lexical())
    report = {'schema': SCHEMA, 'ok': all(c['ok'] for c in checks), 'checks': checks,
              'agentsview_dir': str(av_dir), 'claude_home': str(claude_home)}
    return scrub_obj(report)[0]
