"""Claude Code engagement: get measured z0 mechanisms into the sessions the user actually runs.

The 2026-09-30 diagnosis (docs/engagement.md) found the State Packet keyed to a git repo
(the user's interactive session starts in ``~/workspace``, which is not one), never reaching
subagents, ``route_worker`` never mentioned to the model, and lean only reachable through the
launcher. This module holds the fixes; every one fails open and none claims a saving.

* ``session_context``  SessionStart/SubagentStart payload: the repo State Packet when cwd is a
                       repo, else a bounded multi-repo workspace packet; plus a posture-aware
                       ``route_worker`` hint.
* ``offload_hint``     one line derived from the factory resource posture.
* ``MCP_INSTRUCTIONS`` static server instructions for the canonical MCP server.
* ``lean_settings``    the documented settings.json keys that approximate the lean profile
                       (printed, never applied).
* ``shell_init``       a shell function for the lean launcher (user decision; never installed).
* ``report``           count-only cohort table over transcripts: the before/after instrument.

  z0int claude-code engagement report [--days 7] [--json]
  z0int claude-code engagement lean-settings
  z0int claude-code engagement shell-init fish|bash|zsh [--name claude-lean]
"""
from __future__ import annotations

import argparse
import collections
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time
from typing import Any

ROUTE_TOOL = 'mcp__plugin_z0intelligence_z0intelligence__route_worker'
WORKSPACE_MAX_REPOS = 12

MCP_INSTRUCTIONS = (
    'z0intelligence route_worker offloads a bounded, self-contained subtask (summarize/extract/'
    'classify a supplied text, answer a narrow question over given content, mechanical rewrite) '
    'to a cheaper or local worker and returns a receipt; it answers PARENT_ONLY when delegation '
    'is unwarranted. Do not use it for work needing this session\'s tools, files or judgement. '
    'Whether offloading is wanted right now follows the resource posture in the z0 State Packet '
    '(BURN: frontier capacity would otherwise expire, keep work here unless it must stay local; '
    'OFFLOAD/RESERVE: prefer route_worker for eligible subtasks). Pass allow_remote only when '
    'the user has authorized sending that content off-host.'
)


def _cfg_flag(cfg: dict[str, Any], key: str, env: str, default: bool) -> bool:
    raw = os.environ.get(env)
    if raw is not None:
        return raw == '1'
    value = cfg.get(key)
    return default if value is None else value is True


# --------------------------------------------------------------------------- offload hint

def offload_hint(factory: dict[str, Any] | None) -> str:
    """One posture-aware line telling the model whether and how to use route_worker."""
    if not factory or not factory.get('posture'):
        return ''
    posture = factory['posture']
    load = f'(load it with ToolSearch "select:{ROUTE_TOOL}")'
    if posture in ('OFFLOAD', 'RESERVE'):
        return (f'<z0-offload posture={posture}> Frontier capacity is tight: for bounded self-contained '
                f'subtasks (summarize/extract/classify given text, narrow Q&A over supplied content) call '
                f'route_worker {load} before doing them inline; it returns PARENT_ONLY when not worth it.')
    if posture == 'BURN':
        return (f'<z0-offload posture=BURN> Frontier capacity would otherwise expire: do the work here. '
                f'Use route_worker {load} only for work that must stay on local workers.')
    return (f'<z0-offload posture={posture}> route_worker {load} is available for bounded '
            f'self-contained subtasks; it returns PARENT_ONLY when delegation is not worth it.')


def _factory() -> dict[str, Any] | None:
    try:
        from .posture import current_posture
        return current_posture()['factory']
    except Exception:
        return None


# --------------------------------------------------------------------------- workspace packet

def _git(path: Path, *args: str, timeout: float = 5.0) -> str | None:
    try:
        proc = subprocess.run(['git', '--no-optional-locks', '-C', str(path), *args], capture_output=True,
                              text=True, timeout=timeout, env=dict(os.environ, GIT_OPTIONAL_LOCKS='0'))
    except (OSError, subprocess.TimeoutExpired):
        return None
    return proc.stdout if proc.returncode == 0 else None


def candidate_repos(root: Path, *, depth: int = 2, max_dirs: int = 500) -> list[Path]:
    """Git checkouts/worktrees under ``root`` (a ``.git`` dir or file), no deeper than ``depth``.

    Stat-only and capped at ``max_dirs`` visited directories so a session started in ``/`` or
    ``$HOME`` stays cheap; unreadable entries are skipped."""
    found: list[Path] = []
    frontier, visited = [root], 0
    for _ in range(depth):
        nxt: list[Path] = []
        for d in frontier:
            try:
                kids = sorted(d.iterdir())
            except OSError:
                continue
            for k in kids:
                if visited >= max_dirs:
                    return found
                try:
                    if k.name.startswith('.') or k.is_symlink() or not k.is_dir():
                        continue
                    visited += 1
                    (found if (k / '.git').exists() else nxt).append(k)
                except OSError:
                    continue
        frontier = nxt
    return found


def _recency(repo: Path) -> float:
    # Cheap ranking before any git call: worktree .git files and checkout dirs both move on use.
    best = 0.0
    for p in (repo, repo / '.git', repo / '.git' / 'index', repo / '.git' / 'HEAD'):
        try:
            best = max(best, p.stat().st_mtime)
        except OSError:
            pass
    return best


def _age(seconds: float) -> str:
    seconds = max(0, int(seconds))
    for unit, n in (('d', 86400), ('h', 3600), ('m', 60)):
        if seconds >= n:
            return f'{seconds // n}{unit}'
    return f'{seconds}s'


def workspace_repos(root: Path, *, max_repos: int = WORKSPACE_MAX_REPOS, now: float | None = None) -> tuple[list[dict[str, Any]], int]:
    now = now or time.time()
    repos = sorted(candidate_repos(root), key=_recency, reverse=True)
    rows = []
    for repo in repos[:max_repos]:
        status = _git(repo, 'status', '--porcelain=v1', '-b')
        if status is None:
            continue
        lines = status.splitlines()
        head = lines[0][3:] if lines and lines[0].startswith('## ') else '?'
        branch = head.split('...')[0]
        ahead = re.search(r'ahead (\d+)', head)
        last = (_git(repo, 'log', '-1', '--format=%ct') or '').strip()
        rows.append({'repo': str(repo.relative_to(root)), 'branch': branch, 'dirty': len(lines) - 1,
                     'ahead': int(ahead.group(1)) if ahead else 0,
                     'last_commit_age': _age(now - int(last)) if last.isdigit() else None})
    return rows, len(repos)


def render_workspace_packet(root: Path, rows: list[dict[str, Any]], total: int) -> str:
    if not rows:
        return ''
    home = str(Path.home())
    shown = str(root).replace(home, '~', 1) if str(root).startswith(home) else str(root)
    out = [f'<z0-workspace-packet root="{shown}" repos={total} shown={len(rows)}>',
           'cwd is not a git repo; most recently touched repos under it (branch, uncommitted entries, unpushed commits, last commit):']
    for r in rows:
        bits = [r['branch']]
        if r['dirty']:
            bits.append(f"dirty={r['dirty']}")
        if r['ahead']:
            bits.append(f"ahead={r['ahead']}")
        if r['last_commit_age']:
            bits.append(f"last={r['last_commit_age']}")
        out.append(f"- {r['repo']} [{' '.join(bits)}]")
    out.append('Full per-repo State Packet: python -m z0int.state_packet --repo <dir> --render')
    out.append('</z0-workspace-packet>')
    return '\n'.join(out)


# --------------------------------------------------------------------------- hook payload

def session_context(stdin_text: str | None, *, cfg: dict[str, Any] | None = None, packet: bool = True,
                    factory: dict[str, Any] | None | bool = True, max_tokens: int = 1500) -> dict[str, Any] | None:
    """SessionStart / SubagentStart payload, or None when there is nothing to add. Never raises."""
    cfg = cfg or {}
    try:
        hook = json.loads(stdin_text) if stdin_text else {}
    except ValueError:
        hook = {}
    if not isinstance(hook, dict):
        hook = {}
    event = hook.get('hook_event_name') or 'SessionStart'
    if event == 'SubagentStart' and not _cfg_flag(cfg, 'subagent_packet', 'Z0INT_CLAUDE_CODE_SUBAGENT_PACKET', False):
        return None
    cwd = hook.get('cwd') or os.getcwd()
    parts: list[str] = []
    try:
        from .state_packet import repo_root, session_start_hook
        if not packet:
            pass
        elif repo_root(cwd) is not None:
            parts.append(session_start_hook(json.dumps({'cwd': cwd}), max_tokens=max_tokens)
                         ['hookSpecificOutput']['additionalContext'])
        elif _cfg_flag(cfg, 'workspace_packet', 'Z0INT_CLAUDE_CODE_WORKSPACE_PACKET', True):
            root = Path(cwd).expanduser().resolve()
            rows, total = workspace_repos(root)
            parts.append(render_workspace_packet(root, rows, total))
    except Exception as exc:
        parts.append(f'<z0-state-packet error="{type(exc).__name__}"/>')
    if _cfg_flag(cfg, 'offload_hint', 'Z0INT_CLAUDE_CODE_OFFLOAD_HINT', True):
        fac = _factory() if factory is True else (factory or None)
        parts.append(offload_hint(fac))
    ctx = '\n'.join(p for p in parts if p)
    if not ctx:
        return None
    return {'hookSpecificOutput': {'hookEventName': event, 'additionalContext': ctx[:9500]}}


# --------------------------------------------------------------------------- lean via settings / shell

def lean_settings() -> dict[str, Any]:
    """Documented settings.json keys approximating lean (code.claude.com/docs/en/settings-reference).

    Not equivalent: there is no settings key for --setting-sources/--strict-mcp-config/
    --disable-slash-commands. Values here are proposals to measure, never applied by z0.
    """
    return {
        'settings': {
            'skillListingMaxDescChars': 80,        # skill_listing was ~32.7k chars in the live session
            'skillListingBudgetFraction': 0.005,
            'disableClaudeAiConnectors': True,     # USER DECISION: drops claude.ai connector MCP servers
        },
        'remove_user_hooks': ['SessionStart:lavish-axi (~9.4k chars of stdout per session start)'],
        'not_expressible': ['--setting-sources project', '--strict-mcp-config', '--disable-slash-commands'],
    }


def shell_init(shell: str, name: str = 'claude-lean') -> str:
    """A shell function running the lean launcher. Naming it ``claude`` shadows the binary: user decision."""
    launch = 'z0int claude-code launch --profile lean --'
    if shell == 'fish':
        return f'function {name} --wraps claude --description "claude, z0 lean profile"\n    {launch} $argv\nend\n'
    return f'{name}() {{ {launch} "$@"; }}\n'


# --------------------------------------------------------------------------- measurement

EVAL_CWD = re.compile(r'^/tmp/(cc-|tmp\.)|/\.cache/z0-savings|^/tmp/claude-\d+/')


def cohort(entrypoint: str | None, cwd: str | None) -> str:
    """interactive (a human at `claude`), harness (eval/probe arms), agent (SDK-driven, real repo)."""
    if cwd and EVAL_CWD.search(cwd):
        return 'harness'
    if entrypoint == 'cli':
        return 'interactive'
    return 'agent'


def scan(path: Path) -> dict[str, Any]:
    s: dict[str, Any] = {'cwd': None, 'entrypoint': None, 'packet': False, 'workspace_packet': False,
                         'offload_hint': False, 'z0_session_start': False, 'skill_listing': False,
                         'route_worker_calls': 0, 'obspack': 0, 'assistant_msgs': 0}
    try:
        fh = open(path, errors='replace')
    except OSError:
        return s
    with fh:
        for line in fh:
            try:
                d = json.loads(line)
            except ValueError:
                continue
            if s['cwd'] is None and d.get('cwd'):
                s['cwd'], s['entrypoint'] = d['cwd'], d.get('entrypoint')
            t = d.get('type')
            if t == 'attachment':
                a = d.get('attachment') or {}
                if a.get('type') == 'skill_listing':
                    s['skill_listing'] = True
                if a.get('hookEvent') in ('SessionStart', 'SubagentStart') and 'z0int.claude_code' in (a.get('command') or ''):
                    s['z0_session_start'] = True
                if a.get('type') == 'hook_additional_context':
                    s['packet'] |= '<z0-state-packet' in line
                    s['workspace_packet'] |= '<z0-workspace-packet' in line
                    s['offload_hint'] |= '<z0-offload' in line
            elif t == 'assistant':
                s['assistant_msgs'] += 1
                for b in (d.get('message') or {}).get('content') or []:
                    if isinstance(b, dict) and b.get('type') == 'tool_use' and 'route_worker' in (b.get('name') or ''):
                        s['route_worker_calls'] += 1
            elif t == 'user' and '[z0 ObservationPack]' in line:
                s['obspack'] += 1
    return s


def report(days: float = 7, base: Path | None = None, now: float | None = None) -> dict[str, Any]:
    base = base or Path.home() / '.claude' / 'projects'
    since = (now or time.time()) - days * 86400
    table: dict[str, collections.Counter] = collections.defaultdict(collections.Counter)
    for root_t in base.glob('*/*.jsonl'):
        try:
            if root_t.stat().st_mtime < since:
                continue
        except OSError:
            continue
        sessions = [('root', root_t)] + [('subagent', p) for p in sorted((root_t.with_suffix('') / 'subagents').glob('*.jsonl'))]
        parent = None
        for role, p in sessions:
            s = scan(p)
            if role == 'root':
                parent = cohort(s['entrypoint'], s['cwd'])
            c = table[f'{parent}/{role}']
            c['sessions'] += 1
            c['assistant_msgs'] += s['assistant_msgs']
            for k in ('packet', 'workspace_packet', 'offload_hint', 'z0_session_start', 'skill_listing'):
                c[k] += bool(s[k])
            c['route_worker_calls'] += s['route_worker_calls']
            c['obspack_results'] += s['obspack']
    return {'schema': 'z0int.claude_code.engagement.v0', 'days': days,
            'cohorts': {k: dict(v) for k, v in sorted(table.items())}}


def _main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog='z0int claude-code engagement', description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest='cmd', required=True)
    rp = sub.add_parser('report', help='count-only cohort table over Claude Code transcripts')
    rp.add_argument('--days', type=float, default=7)
    rp.add_argument('--json', action='store_true')
    sub.add_parser('lean-settings', help='documented settings keys approximating lean (printed, not applied)')
    si = sub.add_parser('shell-init', help='print a shell function for the lean launcher (not installed)')
    si.add_argument('shell', choices=['fish', 'bash', 'zsh'])
    si.add_argument('--name', default='claude-lean')
    args = ap.parse_args(argv)
    if args.cmd == 'lean-settings':
        print(json.dumps(lean_settings(), indent=1))
    elif args.cmd == 'shell-init':
        print(shell_init(args.shell, args.name), end='')
    else:
        rep = report(args.days)
        if args.json:
            print(json.dumps(rep, indent=1))
        else:
            keys = ['sessions', 'assistant_msgs', 'z0_session_start', 'packet', 'workspace_packet', 'offload_hint',
                    'skill_listing', 'route_worker_calls', 'obspack_results']
            print('cohort/role'.ljust(22) + ' '.join(k[:12].rjust(12) for k in keys))
            for name, c in rep['cohorts'].items():
                print(name.ljust(22) + ' '.join(str(c.get(k, 0)).rjust(12) for k in keys))
    return 0


if __name__ == '__main__':
    sys.exit(_main())
