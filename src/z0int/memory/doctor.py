"""`z0int memory doctor`: read-only health of the memory substrate.

Fails on: a missing/locked/unknown AgentsView DB, a stale index, no ``deepseek-harness`` agent, the Claude home
(``CLAUDE_CONFIG_DIR`` or ``~/.claude-home``) missing or not indexed, a DB ``user_version`` that differs from the
installed binary's dataVersion, ``require_auth`` off, a local history source that is active on disk but behind
in AgentsView (per source, not just the newest session), a remote host whose newest indexed session is stale,
and an installed ctx whose status times out, errors or is not ready. Binaries resolve the same way under any
PATH (explicit flag, ``Z0INT_AGENTSVIEW_BIN``/``Z0INT_CTX_BIN``, PATH, then ``~/.local/bin``), so a cron or
systemd PATH gives the same verdict as a login shell. Reports (without failing) the TencentDB gateway as
not_configured / unreachable / reachable. The AgentsView recall layer is off by design (``recall extract`` would
be a second semantic store), so ``absent`` is the expected state. Values from config files are never echoed:
``require_auth`` is reported as a boolean only.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import sqlite3
import subprocess
import time
from pathlib import Path
from typing import Any, Mapping

from .. import agentsview_ro
from . import sources
from .scrub import scrub_obj
from .surface import TencentDBClient, load_config

SCHEMA = 'z0int.memory.doctor.v0'
#: AgentsView release -> sessions.db dataVersion (``internal/db/db.go`` const dataVersion).
KNOWN_DATA_VERSIONS = {(0, 39): 74, (0, 44): 113}
STALE_HOURS = 24.0
#: A remote host (pulled by AgentsView over SSH) may be idle for a day; past this its sync is broken.
REMOTE_STALE_HOURS = 48.0
#: Remote hosts with no session in this window are retired, not stale.
REMOTE_ACTIVE_DAYS = 30.0
CTX_TIMEOUT = 15.0


def resolve_bin(name: str, explicit: str | Path | None = None) -> str | None:
    """Absolute path of a tool, independent of the caller's PATH; None when it is not installed."""
    candidates = [explicit, os.environ.get(f'Z0INT_{name.upper()}_BIN'), shutil.which(name),
                  Path.home() / '.local/bin' / name, Path('/usr/local/bin') / name]
    for c in candidates:
        if c and os.path.isfile(str(c)) and os.access(str(c), os.X_OK):
            return os.path.abspath(str(c))
    return None


def _check(name: str, ok: bool, detail: str = '', **kw: Any) -> dict[str, Any]:
    return {'name': name, 'ok': bool(ok), 'detail': detail, **kw}


def _av_config(av_dir: Path) -> dict[str, Any] | None:
    try:
        import tomllib
        return tomllib.loads((av_dir / 'config.toml').read_text(encoding='utf-8'))
    except (ImportError, OSError, ValueError):
        return None


def _binary_data_version(binary: str | Path | None) -> tuple[int | None, str]:
    path = resolve_bin('agentsview', binary)
    if not path:
        return None, 'agentsview binary not found (flag, Z0INT_AGENTSVIEW_BIN, PATH, ~/.local/bin)'
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

def _ctx_lexical(command: str | None = None, timeout: float = CTX_TIMEOUT) -> dict[str, Any]:
    """Read-only. Not installed is fine (ctx is opt-in); an installed ctx that times out, errors or is not ready
    fails, because harnesses (Hermes ``clean``'s ctx MCP) call it and would hang or get nothing."""
    cmd = resolve_bin('ctx', command)
    if not cmd:
        return _check('ctx_lexical', True, 'not installed (opt-in lexical pull)', status='not_installed')
    try:
        proc = subprocess.run([cmd, 'status', '--format', 'json', '--quiet'], capture_output=True, text=True,
                              timeout=timeout, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return _check('ctx_lexical', False, f'installed but status failed ({type(exc).__name__} after '
                      f'{timeout:g} s)', status='unavailable', path=cmd)
    try:
        payload = json.loads(proc.stdout)
    except (json.JSONDecodeError, TypeError):
        detail = (proc.stderr or proc.stdout or 'status not json').strip().splitlines()
        return _check('ctx_lexical', False, 'installed but unavailable (' + (detail[-1][:200] if detail else
                      'no status') + ')', status='unavailable', path=cmd)
    lexical = payload.get('lexical') if isinstance(payload, dict) else None
    epoch = payload.get('history_epoch') if isinstance(payload, dict) else None
    lexical_status = lexical.get('status') if isinstance(lexical, dict) else None
    reason = lexical.get('reason') if isinstance(lexical, dict) else None
    generation = epoch.get('generation_path') if isinstance(epoch, dict) else None
    if lexical_status == 'ready':
        return _check('ctx_lexical', True, f'ready generation {generation or "unnamed"}', status='ready',
                      generation=generation, path=cmd)
    history = payload.get('history') if isinstance(payload, dict) else None
    hstatus = history.get('status') if isinstance(history, dict) else None
    return _check('ctx_lexical', False,
                  f'installed but not ready (lexical {lexical_status or "missing"}'
                  f'{": " + str(reason)[:200] if reason else ""}; history {hstatus or "missing"})',
                  status='unavailable', generation=generation, path=cmd)


def _local_machines(conn: sqlite3.Connection) -> set[str]:
    """Machine keys this host's own sync writes: the installation id plus legacy hostname/local keys."""
    keys = {'local', '', os.uname().nodename.split('.')[0]}
    try:
        keys |= {r[0] for r in conn.execute("select value from pg_sync_state "
                                            "where key = 'artifact_local_installation_id'") if r[0]}
        keys |= {r[0][len('machine_alias:'):] for r in conn.execute(
            "select key from pg_sync_state where key like 'machine_alias:%'")}
    except sqlite3.Error:
        pass
    return keys


def _labels(conn: sqlite3.Connection) -> dict[str, str]:
    try:
        return {k[len('machine_label:'):]: v for k, v in conn.execute(
            "select key, value from pg_sync_state where key like 'machine_label:%'")}
    except sqlite3.Error:
        return {}


def _source_freshness(conn: sqlite3.Connection, now: float) -> list[dict[str, Any]]:
    """Per source, not newest-session: each local history root active on disk must be caught up in AgentsView,
    and each remote host AgentsView pulls must have a recent session."""
    local = _local_machines(conn)
    labels = _labels(conn)
    behind, active = [], 0
    for (agent, root), row in sorted(sources.agentsview_roots(conn, now=now, machines=local).items()):
        disk = sources.newest_source(row['provider'], root)
        if disk is None or now - disk > sources.ACTIVE_DAYS * 86400:
            continue
        active += 1
        lag = None if row['av_newest'] is None else max(0.0, (disk - row['av_newest']) / 3600)
        if lag is None or lag > sources.LAG_HOURS:
            behind.append(f'{agent} {root}: ' + ('not in AgentsView' if lag is None else f'{lag:.0f} h behind disk'))
    out = [_check('source_freshness', not behind,
                  f'{active} active local sources, all within {sources.LAG_HOURS:g} h' if not behind else
                  f'{len(behind)} of {active} active local sources behind: ' + '; '.join(behind[:6]),
                  behind=behind)]
    since = time.strftime('%Y-%m-%dT%H:%M:%S', time.gmtime(now - REMOTE_ACTIVE_DAYS * 86400))
    stale = []
    hosts = 0
    for machine, newest in conn.execute('select machine, max(started_at) from sessions where started_at >= ? '
                                        'group by machine', (since,)):
        if machine in local:
            continue
        hosts += 1
        t = sources._ts(newest)
        lag = None if t is None else (now - t) / 3600
        if lag is None or lag > REMOTE_STALE_HOURS:
            stale.append(f"{labels.get(machine, machine)}: newest session "
                         + ('unknown' if lag is None else f'{lag:.0f} h old'))
    out.append(_check('remote_host_freshness', not stale,
                      f'{hosts} remote hosts, all within {REMOTE_STALE_HOURS:g} h' if not stale else
                      '; '.join(stale), stale=stale))
    return out



def run(*, av_dir: str | Path | None = None, agentsview_bin: str | Path | None = None,
        claude_home: str | Path | None = None, config: Mapping[str, Any] | None = None,
        stale_hours: float = STALE_HOURS, now: float | None = None, ctx_bin: str | Path | None = None,
        ctx_timeout: float = CTX_TIMEOUT) -> dict[str, Any]:
    now = time.time() if now is None else now
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
            freshness = _source_freshness(conn, now)
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
            checks.extend(freshness)
            checks.append(_check('deepseek_harness_agent', bool(dsh),
                                 'present' if dsh else 'no deepseek-harness sessions indexed'))
            binary_version, detail = _binary_data_version(agentsview_bin)
            checks.append(_check('data_version', binary_version == user_version,
                                 f'DB user_version {user_version}, {detail}'
                                 + (f' dataVersion {binary_version}' if binary_version else '')))
            checks.append(_check('recall_layer', True, (f'{recall_n} recall entries' if recall else 'no recall table')
                                 + ('' if recall_n else ' (expected: `recall extract` stays off by design)'),
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
    checks.append(_ctx_lexical(None if ctx_bin is None else str(ctx_bin), ctx_timeout))
    report = {'schema': SCHEMA, 'ok': all(c['ok'] for c in checks), 'checks': checks,
              'agentsview_dir': str(av_dir), 'claude_home': str(claude_home)}
    return scrub_obj(report)[0]
