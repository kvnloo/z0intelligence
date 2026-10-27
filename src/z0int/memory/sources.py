"""`z0int memory sources`: every harness's history must reach every memory index, without a hand-kept list.

Each memory index used to keep its own hand-written list of where harness history lives (ctx ``config.toml``
roots, AgentsView config, the memory MCP project grants, several hard-coded defaults in this package). Those
lists drift: a new harness, profile, or path layout is silently invisible until the owner notices an agent
forgot something. This module derives the source list from what is actually used and checks every index
against it.

* **Discovery.** AgentsView auto-discovers harness session stores, so the ``file_path`` of each indexed session
  names a real source root. Required harnesses come from :data:`z0int.harness_capture.HARNESSES` (the harness
  adapter); a required harness with recent files on disk but nothing in AgentsView is a gap, not a pass.
* **Checks.** Per harness root: AgentsView sync lag (disk mtime vs newest indexed session), ctx registration,
  and ctx's own per-root status (a registered root that ctx reports ``missing`` is a wrong path shape).
  Memory MCP grants: projects with recent owner sessions that no operator grant covers.
* **Repair.** ``--apply`` registers missing ctx roots with ``ctx sources add`` (additive, real paths, stable
  ``auto-<agent>-<hash>`` names). Grants are privacy policy and are reported, never widened here.

Read-only unless ``apply=True``. Stdlib only.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping

from .. import agentsview_ro
from ..harness_capture import HARNESSES

SCHEMA = 'z0int.memory.sources.v0'
ACTIVE_DAYS = 7.0
LAG_HOURS = 6.0
GRANT_DAYS = 30.0
# harness_capture name -> AgentsView agent name (only where they differ).
AV_AGENT = {'claude-code': 'claude', 'dsh': 'deepseek-harness'}
# Fallback roots for a required harness AgentsView has never seen. Discovery, not configuration.
DEFAULT_ROOTS = {
    'claude': ['~/.claude/projects'], 'codex': ['~/.codex/sessions'], 'grok': ['~/.grok/sessions'],
    'omp': ['~/.omp/agent/sessions'], 'omo': ['~/.omo/agent/sessions'], 'deepseek-harness': ['~/.dsh/sessions'],
    'hermes': ['~/.hermes/state.db', '~/.hermes/profiles/*/state.db'],
}


def _before(path: str, marker: str, suffix: str = '') -> str | None:
    i = path.find(marker)
    return None if i < 0 else path[:i] + suffix


def _db(path: str) -> str | None:
    head = path.split('#', 1)[0]
    return head if head.endswith('.db') else None


# AgentsView agent -> (ctx provider, function(file_path) -> ctx root). ctx roots are provider-shaped: Claude and
# Codex take the *home* (ctx appends projects/ or sessions/), pi/grok/dsh take the sessions dir, Hermes and
# OpenCode take the database file.
CTX_RULES: dict[str, tuple[str, Callable[[str], str | None]]] = {
    'omp': ('pi', lambda p: _before(p, '/sessions/', '/sessions')),
    'omo': ('pi', lambda p: _before(p, '/sessions/', '/sessions')),
    'codex': ('codex', lambda p: _before(p, '/sessions/')),
    'claude': ('claude', lambda p: _before(p, '/projects/')),
    'grok': ('grok_build', lambda p: _before(p, '/sessions/', '/sessions')),
    'deepseek-harness': ('deepseek_harness', lambda p: _before(p, '/sessions/', '/sessions')),
    'hermes': ('hermes', _db),
    'opencode': ('opencode', _db),
}
# Where session files live under a ctx root (homes hold unrelated, frequently written files).
SESSION_SUBDIR = {'claude': 'projects', 'codex': 'sessions'}
SESSION_SUFFIXES = ('.jsonl', '.jsonl.zstd', '.json')


def _real(path: str | Path) -> str:
    return os.path.realpath(os.path.expanduser(str(path)))


def _ts(value: Any) -> float | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace('Z', '+00:00')[:32]).timestamp()
    except ValueError:
        try:
            return datetime.fromisoformat(str(value)[:19] + '+00:00').timestamp()
        except ValueError:
            return None


def _hermes_newest(db: str) -> float | None:
    try:
        conn = sqlite3.connect(f'file:{db}?mode=ro', uri=True, timeout=2.0)
        try:
            # Automated sessions (cron, subagents, tools) are not owner history; their churn is not a lag signal.
            cols = {r[1] for r in conn.execute('pragma table_info(sessions)')}
            # Empty sessions (an ACP/cron open that never got a message) are never indexed; not a lag signal.
            where = " where exists (select 1 from messages m where m.session_id = sessions.id)"
            if 'source' in cols:
                where += " and coalesce(source, '') not in ('cron', 'subagent', 'tool', 'fx-worker')"
            value = conn.execute('select max(started_at) from sessions' + where).fetchone()[0]
        finally:
            conn.close()
        return float(value) if value is not None else None
    except (sqlite3.Error, TypeError, ValueError):
        return None


def newest_source(provider: str, root: str | Path, *, budget: int = 200_000) -> float | None:
    """Newest session activity under a ctx root, by the provider's own notion of a session. Bounded walk."""
    root = Path(root)
    if provider == 'hermes':
        return _hermes_newest(str(root))
    try:
        if root.is_file():
            wal = root.with_name(root.name + '-wal')
            return max(root.stat().st_mtime, wal.stat().st_mtime if wal.exists() else 0.0)
    except OSError:
        return None
    sub = SESSION_SUBDIR.get(provider)
    base = root / sub if sub else root
    best: float | None = None
    seen = 0
    stack = [str(base)]
    while stack and seen < budget:
        try:
            with os.scandir(stack.pop()) as it:
                for e in it:
                    seen += 1
                    try:
                        if e.is_dir(follow_symlinks=False):
                            stack.append(e.path)
                        elif e.name.endswith(SESSION_SUFFIXES) and e.is_file(follow_symlinks=False):
                            m = e.stat(follow_symlinks=False).st_mtime
                            best = m if best is None or m > best else best
                    except OSError:
                        continue
        except OSError:
            continue
    return best


def agentsview_roots(conn: sqlite3.Connection, *, since_days: float = 365.0, now: float | None = None,
                     machines: Iterable[str] | None = None) -> dict[tuple[str, str], dict[str, Any]]:
    """{(agent, ctx_root): {provider, sessions, av_newest}} from indexed session file paths.

    ``machines`` limits the rows to those machine keys (e.g. this host's, so a remote host's identical
    ``~/.claude`` path is not compared with this host's disk). None keeps every row.
    """
    now = time.time() if now is None else now
    since = datetime.fromtimestamp(now - since_days * 86400, timezone.utc).strftime('%Y-%m-%dT%H:%M:%S')
    out: dict[tuple[str, str], dict[str, Any]] = {}
    keys = None if machines is None else sorted(set(machines))
    where = '' if keys is None else f" and machine in ({','.join('?' * len(keys))})"
    rows = conn.execute('select agent, file_path, max(started_at), count(*) from sessions '
                        'where file_path is not null and started_at >= ?' + where + ' group by agent, file_path',
                        (since, *(keys or [])))
    for agent, file_path, newest, n in rows:
        rule = CTX_RULES.get(agent)
        root = rule[1](file_path) if rule else None
        if rule is None or not root:
            continue
        key = (agent, _real(root))
        row = out.setdefault(key, {'agent': agent, 'root': key[1], 'provider': rule[0], 'sessions': 0,
                                   'av_newest': None})
        row['sessions'] += n
        t = _ts(newest)
        if t and (row['av_newest'] is None or t > row['av_newest']):
            row['av_newest'] = t
    return out


def ctx_sources(ctx_bin: str | None = None, *, timeout: float = 120) -> list[dict[str, Any]] | None:
    """ctx's own view of its configured roots: [{provider, path, status}], or None when ctx is unusable."""
    cmd = ctx_bin or shutil.which('ctx')
    if not cmd:
        return None
    try:
        proc = subprocess.run([cmd, 'sources', '--all', '--format', 'json', '--quiet'], capture_output=True,
                              text=True, timeout=timeout, check=False)
        payload = json.loads(proc.stdout)
    except (OSError, subprocess.TimeoutExpired, json.JSONDecodeError):
        return None
    rows = payload if isinstance(payload, list) else payload.get('sources', []) if isinstance(payload, dict) else []
    return [r for r in rows if isinstance(r, dict) and r.get('provider') and r.get('path')]


def ctx_config_roots(data_root: str | Path | None = None) -> dict[str, dict[str, str]]:
    """{name: {provider, path}} from ctx's config.toml (CTX_DATA_ROOT or ~/.ctx)."""
    root = Path(data_root or os.environ.get('CTX_DATA_ROOT') or '~/.ctx').expanduser()
    try:
        import tomllib
        roots = tomllib.loads((root / 'config.toml').read_text())['sources']['roots']
    except (ImportError, OSError, ValueError, KeyError, TypeError):
        return {}
    return {n: {'provider': str(v.get('provider')), 'path': str(v.get('path'))} for n, v in roots.items()
            if isinstance(v, dict)}


def pi_session_id(path: str | Path) -> str | None:
    """Session id of a pi/OMP transcript (a `session` header within the first two records; OMP writes `title`
    first), or None for anything else.

    ctx's pi provider rejects the *whole* root on one bad file, so the mirror must hold only transcripts
    (OMP keeps non-transcript JSONL artefacts under `<session>/local/`) and each session id once (a forked
    OMP session copies its subagent transcripts: 598 repeated ids on groot, 2026-10-25).
    """
    try:
        with open(path, 'rb') as fh:
            for _ in range(2):
                line = fh.readline(1 << 16)
                if not line:
                    return None
                try:
                    record = json.loads(line)
                except ValueError:
                    return None
                if isinstance(record, dict) and record.get('type') == 'session':
                    return str(record.get('id') or '') or None
    except OSError:
        return None
    return None


def is_pi_session(path: str | Path) -> bool:
    return pi_session_id(path) is not None


def pi_mirror_path(root: str | Path) -> Path:
    """Session-only mirror beside the agent dir (same filesystem, so hard links work): ~/.omp/.z0int-ctx-mirror/sessions."""
    root = Path(_real(root))
    return root.parent.parent / '.z0int-ctx-mirror' / root.name


def sync_pi_mirror(root: str | Path, mirror: str | Path | None = None) -> dict[str, Any]:
    """Hard-link every pi transcript under root into mirror; drop links whose source is gone.

    Hard links share the inode, so in-place appends are visible to ctx without copying; a rewritten file
    (new inode) is relinked on the next sync. Idempotent.
    """
    root = Path(_real(root))
    mirror = Path(mirror) if mirror else pi_mirror_path(root)
    best: dict[str, tuple[tuple[int, float], Path, os.stat_result]] = {}
    skipped = duplicates = 0
    for dirpath, _dirs, files in os.walk(root):
        for name in files:
            if not name.endswith('.jsonl'):
                continue
            src = Path(dirpath) / name
            sid = pi_session_id(src)
            if sid is None:
                skipped += 1
                continue
            try:
                st = src.stat()
            except OSError:
                continue
            rank = (st.st_size, st.st_mtime)  # the fullest copy of a repeated session wins
            if sid in best:
                duplicates += 1
                if rank <= best[sid][0]:
                    continue
            best[sid] = (rank, src.relative_to(root), st)
    want: dict[Path, os.stat_result] = {rel: st for _rank, rel, st in best.values()}
    linked = removed = 0
    for rel, st in want.items():
        dst = mirror / rel
        try:
            if dst.stat().st_ino == st.st_ino:
                continue
            dst.unlink()
        except FileNotFoundError:
            pass
        dst.parent.mkdir(parents=True, exist_ok=True)
        try:
            os.link(root / rel, dst)
            linked += 1
        except OSError:
            continue
    if mirror.exists():
        for dirpath, dirs, files in os.walk(mirror, topdown=False):
            for name in files:
                p = Path(dirpath) / name
                if p.relative_to(mirror) not in want:
                    p.unlink(missing_ok=True)
                    removed += 1
            if not os.listdir(dirpath) and Path(dirpath) != mirror:
                os.rmdir(dirpath)
    return {'root': str(root), 'mirror': str(mirror), 'sessions': len(want), 'linked': linked,
            'removed': removed, 'skipped_non_sessions': skipped, 'duplicate_ids': duplicates}


def ctx_failures(ctx_bin: str | None = None, *, timeout: float = 120) -> list[dict[str, str]] | None:
    """Per-source import failures from ctx's last refresh. Registered is not indexed: this is the indexed check."""
    cmd = ctx_bin or shutil.which('ctx')
    if not cmd:
        return None
    try:
        proc = subprocess.run([cmd, 'status', '--format', 'json', '--quiet'], capture_output=True, text=True,
                              timeout=timeout, check=False)
        payload = json.loads(proc.stdout)
    except (OSError, subprocess.TimeoutExpired, json.JSONDecodeError):
        return None
    rows = ((payload.get('refresh') or {}).get('diagnostics') or {}).get('source_failures') or []
    return [{'provider': str(r.get('provider')), 'selector': str(r.get('source_selector')),
             'detail': str(r.get('detail') or r.get('class'))} for r in rows if isinstance(r, dict)]


def _ctx_covers(ctx_rows: Iterable[Mapping[str, Any]], provider: str, root: str) -> bool:
    for r in ctx_rows:
        if r.get('provider') != provider or r.get('status') == 'missing':
            continue
        path = _real(r['path'])
        if provider == 'pi' and path == _real(pi_mirror_path(root)):
            return True
        # ctx lists a provider home as its expanded children (claude -> projects/, codex -> sessions/ ...).
        if root == path or root.startswith(path.rstrip('/') + '/') or path.startswith(root.rstrip('/') + '/'):
            return True
    return False


def _benign_missing(row: Mapping[str, Any], rows: Iterable[Mapping[str, Any]]) -> bool:
    """A missing optional child of an expanded provider home (e.g. codex archived_sessions) is not a gap."""
    parent = os.path.dirname(_real(row['path']))
    return any(r is not row and r.get('provider') == row.get('provider') and r.get('status') == 'available'
               and os.path.dirname(_real(r['path'])) == parent for r in rows)


def ungranted_projects(conn: sqlite3.Connection, policy_path: str | Path, *, principal: str = 'operator',
                       days: float = GRANT_DAYS, now: float | None = None) -> list[dict[str, Any]] | None:
    try:
        policy = json.loads(Path(policy_path).expanduser().read_text())
        grants = {g.get('scope') for g in policy['principals'][principal] if g.get('source') == 'agentsview'}
    except (OSError, ValueError, KeyError, TypeError):
        return None
    now = time.time() if now is None else now
    since = datetime.fromtimestamp(now - days * 86400, timezone.utc).strftime('%Y-%m-%dT%H:%M:%S')
    agents = sorted({AV_AGENT.get(h, h) for h in HARNESSES} | {'opencode'})
    try:
        rows = conn.execute(
            f"select project, count(*), sum(user_message_count) from sessions where started_at >= ? "
            f"and agent in ({','.join('?' * len(agents))}) and coalesce(user_message_count, 0) > 0 "
            f"and project not like 'hermes-cron' and project not like 'hermes-subagent' "
            f"and project not like 'hermes-oneshot' group by project order by 3 desc", (since, *agents)).fetchall()
    except sqlite3.Error:
        return None
    return [{'project': p, 'sessions': n, 'user_messages': u} for p, n, u in rows if p and p not in grants]


def reconcile(*, av_dir: str | Path | None = None, ctx_bin: str | None = None,
              policy_path: str | Path = '~/.config/z0int/memory-mcp-policy.json', apply: bool = False,
              now: float | None = None, active_days: float = ACTIVE_DAYS, lag_hours: float = LAG_HOURS,
              runner: Callable[..., subprocess.CompletedProcess] = subprocess.run) -> dict[str, Any]:
    now = time.time() if now is None else now
    av_dir = Path(av_dir).expanduser() if av_dir else agentsview_ro.data_dir()
    conn = agentsview_ro.connect(agentsview_ro.db_path(av_dir), timeout=5.0)
    if not isinstance(conn, sqlite3.Connection):
        return {'schema': SCHEMA, 'ok': False, 'error': f'agentsview unavailable ({conn.reason})', 'roots': []}
    try:
        roots = agentsview_roots(conn, now=now)
        grants_gap = ungranted_projects(conn, policy_path, now=now)
        seen_agents = {a for a, _ in roots} | {r[0] for r in conn.execute('select distinct agent from sessions')}
    finally:
        conn.close()
    # Required harnesses AgentsView has never indexed: look on disk at the defaults.
    for harness in HARNESSES:
        agent = AV_AGENT.get(harness, harness)
        if agent in seen_agents or agent not in CTX_RULES:
            continue
        import glob
        for pattern in DEFAULT_ROOTS.get(agent, []):
            for p in glob.glob(os.path.expanduser(pattern)):
                roots[(agent, _real(p))] = {'agent': agent, 'root': _real(p), 'provider': CTX_RULES[agent][0],
                                            'sessions': 0, 'av_newest': None}
    ctx_rows = ctx_sources(ctx_bin)
    failures = ctx_failures(ctx_bin) if ctx_rows is not None else None
    report_roots, problems, applied = [], [], []
    ctx_cmd = ctx_bin or shutil.which('ctx') or 'ctx'
    config = ctx_config_roots()
    for (agent, root), row in sorted(roots.items()):
        disk = newest_source(row['provider'], root)
        active = disk is not None and now - disk <= active_days * 86400
        lag = None if disk is None or row['av_newest'] is None else max(0.0, (disk - row['av_newest']) / 3600)
        in_ctx = None if ctx_rows is None else _ctx_covers(ctx_rows, row['provider'], root)
        entry = {**row, 'disk_newest': disk, 'active': active, 'av_lag_hours': None if lag is None else round(lag, 1),
                 'in_ctx': in_ctx}
        if active and row['av_newest'] is None:
            problems.append(f'{agent} {root}: active on disk, not in AgentsView')
        elif active and lag is not None and lag > lag_hours:
            problems.append(f'{agent} {root}: AgentsView {lag:.0f} h behind disk')
        if active and in_ctx is False:
            problems.append(f'{agent} {root}: not indexed by ctx ({row["provider"]})')
            if apply:
                name = f'auto-{agent}-{hashlib.sha256(root.encode()).hexdigest()[:8]}'
                cmd = [ctx_bin or shutil.which('ctx') or 'ctx', 'sources', 'add', '--provider', row['provider'],
                       '--root', root, '--quiet', name]
                proc = runner(cmd, capture_output=True, text=True, timeout=120, check=False)
                ok = proc.returncode == 0
                applied.append({'agent': agent, 'root': root, 'name': name, 'ok': ok,
                                'detail': ((proc.stdout or proc.stderr or '').strip().splitlines() or [''])[-1]})
                entry['in_ctx'] = ok
                # A same-provider root registered *inside* this one is the wrong path shape (ctx wants the
                # provider home): once the right root is in, the broken one indexes nothing and is removed.
                if ok:
                    for name, cfg in ctx_config_roots().items():
                        path = _real(cfg['path'])
                        if (cfg['provider'] == row['provider'] and path != root
                                and path.startswith(root.rstrip('/') + '/')
                                and any(r.get('status') == 'missing' and _real(r['path']).startswith(path)
                                        for r in ctx_rows or [])):
                            rm = runner([cmd[0], 'sources', 'remove', '--quiet', name], capture_output=True,
                                        text=True, timeout=120, check=False)
                            applied.append({'agent': agent, 'root': path, 'name': name, 'removed': True,
                                            'ok': rm.returncode == 0,
                                            'detail': ((rm.stdout or rm.stderr or '').strip().splitlines()
                                                       or [''])[-1]})
        if row['provider'] == 'pi':
            mirror = _real(pi_mirror_path(root))
            mirrored = [n for n, c in config.items() if c['provider'] == 'pi' and _real(c['path']) == mirror]
            failing = [f for f in failures or [] if f['provider'] == 'pi'
                       and _real(f['selector']) in (root, mirror)]
            if failing:
                problems.append(f"{agent} {root}: ctx import fails ({failing[0]['detail']})")
            if apply and (mirrored or (failing and 'session header' in failing[0]['detail'])):
                sync = sync_pi_mirror(root, mirror)
                entry['mirror'] = sync
                if not mirrored:
                    direct = [n for n, c in config.items() if c['provider'] == 'pi' and _real(c['path']) == root]
                    name = direct[0] if direct else f'auto-{agent}-{hashlib.sha256(root.encode()).hexdigest()[:8]}'
                    cmd = [ctx_cmd, 'sources', 'add', '--provider', 'pi', '--root', mirror, '--quiet']
                    cmd += ['--replace', name] if direct else [name]
                    proc = runner(cmd, capture_output=True, text=True, timeout=120, check=False)
                    applied.append({'agent': agent, 'root': mirror, 'name': name, 'ok': proc.returncode == 0,
                                    'mirror_for': root, 'fixes': f'{root}: ctx import fails',
                                    'detail': ((proc.stdout or proc.stderr or '').strip().splitlines() or [''])[-1]})
                elif failing:
                    applied.append({'agent': agent, 'root': mirror, 'name': mirrored[0], 'ok': True,
                                    'mirror_for': root, 'fixes': f'{root}: ctx import fails',
                                    'detail': f"resynced {sync['sessions']} sessions"})
        report_roots.append(entry)
    removed = {_real(a['root']) for a in applied if a.get('removed') and a['ok']}
    for r in ctx_rows or []:
        if (r.get('status') == 'missing' and not any(_real(r['path']).startswith(x) for x in removed)
                and not _benign_missing(r, ctx_rows or [])):
            problems.append(f'ctx root {r["provider"]} {r["path"]}: registered but missing (wrong path shape?)')
    warnings = []
    if grants_gap:
        top = ', '.join(f"{g['project']} ({g['user_messages']} msgs)" for g in grants_gap[:8])
        warnings.append(f'memory MCP grants: {len(grants_gap)} projects with recent sessions are not granted to '
                        f'operator, so search cannot see them: {top}')
    unresolved = [p for p in problems
                  if not any(a['ok'] and not a.get('removed') and (a['root'] in p and 'ctx (' in p
                                                                  or a.get('fixes', '\0') in p) for a in applied)]
    return {'schema': SCHEMA, 'ok': not unresolved, 'problems': problems, 'unresolved': unresolved, 'warnings': warnings,
            'applied': applied, 'roots': report_roots, 'ungranted_projects': grants_gap,
            'ctx': 'unavailable' if ctx_rows is None else f'{len(ctx_rows)} roots'}


def main(argv: list[str] | None = None) -> int:
    import argparse
    p = argparse.ArgumentParser(prog='z0int memory sources', description=(__doc__ or '').split('\n', 1)[0])
    p.add_argument('--apply', action='store_true', help='register missing ctx roots (additive)')
    p.add_argument('--av-dir')
    p.add_argument('--policy', default='~/.config/z0int/memory-mcp-policy.json')
    p.add_argument('--json', action='store_true')
    a = p.parse_args(argv)
    out = reconcile(av_dir=a.av_dir, policy_path=a.policy, apply=a.apply)
    if a.json:
        print(json.dumps(out, indent=1, default=str))
    else:
        print(f"memory sources: {'OK' if out['ok'] else 'FAIL'} (ctx {out.get('ctx')})")
        for line in out.get('problems', []):
            print('  -', line)
        for line in out.get('warnings', []):
            print('  ! ', line)
        for x in out.get('applied', []):
            verb = 'remove' if x.get('removed') else 'add'
            print(f"  applied ctx sources {verb} {x['name']} {x['root']}: {'ok' if x['ok'] else 'FAILED ' + x['detail']}")
    return 0 if out['ok'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
