"""The one memory inject seam every harness shim calls (C8): ``memory_inject`` off | shadow | canary | on.

* ``off``     nothing runs, nothing is written.
* ``shadow``  the brief is computed in a DETACHED child (the hook never waits, nothing reaches the model); the
              child writes a ``z0int.memory_seam.v0`` row with ``would_inject`` and the MemoryUseReceipt.
* ``canary`` / ``on``  the brief (C7 ``memory_brief``: bounded, scrubbed, snapshot-cached) is returned as
              model-visible context within a hard deadline (300 ms, counted from the job start the shim stamps);
              past it, or on any error, the turn keeps its native context and the row says why.

Gates, in order, before anything model-visible: a replayed ``turn_key`` is a no-op (no row, no spawn); a turn
without a task project (no usable ``cwd``) gets nothing in every mode (``no_scope``: a brief is always scoped to one
project, never built from all of them); a shadow turn past MAX_CHILDREN running briefs is ``queue_saturated``; a
non-loopback model endpoint needs ``allow_cloud_injection`` for that harness in ``$Z0INT_HOME/config/memory.json``
(default false, owner opt-in per harness); one injection owner per turn (``surface.claim_injection``, shared with
``context_resolve``) or ``double_inject_guard``. Rows are content-free (ids, outcome, receipt), scrubbed anyway.

Every terminal row of a turn also settles that turn's receipt (``state/memory/receipts``, keyed by harness +
``turn_key``): the capture side's detached opportunity build reads it into ``opportunity_record.memory`` (D3),
waiting up to RECEIPT_WAIT_S for a turn whose brief is still running. The capture kill switch (``Z0INT_CAPTURE=0`` or
``config/capture.json`` ``{"enabled": false}``) keeps this seam off too: one switch makes every hook native.

Shims outside Python call ``python -m z0int.memory.seam {turn,shadow} --harness <h>`` with one JSON job on stdin
(``session_id``/``turn_id`` or ``turn_key``, ``query``, ``mode``, ``endpoint`` (the harness's own model setting),
``cwd``, ``injector``, ``exclude_layers``, ``started_at``); ``turn`` prints ``{"context", "outcome", "turn_key", ...}``.
"""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import ipaddress
import json
import os
import sys
import threading
import time
from collections import Counter
from pathlib import Path
from typing import Any, Iterable, Mapping
from urllib.parse import urlsplit

from .. import paths
from .scrub import scrub_obj, scrub_text

SCHEMA = 'z0int.memory_seam.v0'
MODES = ('off', 'shadow', 'canary', 'on')
DEFAULT_MODE = 'shadow'
DEADLINE_MS = 300
MAX_TOKENS = 600
MAX_QUERY_CHARS = 2000
SLOT_POOL = 'memory-slots'  # harness_capture.try_slot pool: at most MAX_CHILDREN shadow briefs at once
RECEIPT_WAIT_S = 10.0  # an opportunity build (detached) waits this long for a turn's brief that is still running
RECEIPT_GRACE_S = 1.0  # ... and this long for a seam that has run before but has not marked this turn yet


def _config() -> dict[str, Any]:
    from .surface import load_config
    return load_config()


def settings(harness: str, *, mode: str | None = None, env: Mapping[str, str] | None = None) -> dict[str, Any]:
    """Mode: the shim's value, else ``Z0INT_MEMORY_INJECT``, else ``memory.json`` inject.<harness>.mode, else shadow.
    ``allow_cloud_injection`` comes from ``memory.json`` only (an owner decision per harness, never an env var)."""
    env = os.environ if env is None else env
    from ..harness_capture import enabled
    own = ((_config().get('inject') or {}).get(harness) or {})
    own = own if isinstance(own, dict) else {}
    chosen = mode or env.get('Z0INT_MEMORY_INJECT') or own.get('mode') or DEFAULT_MODE
    if not enabled(env):  # the capture kill switch keeps memory native as well
        chosen = 'off'
    return {'mode': chosen if chosen in MODES else 'off', 'allow_cloud_injection': own.get('allow_cloud_injection') is True,
            'deadline_ms': int(own.get('deadline_ms') or DEADLINE_MS), 'max_tokens': int(own.get('max_tokens') or MAX_TOKENS)}


def is_loopback(url: Any) -> bool:
    """A model endpoint on this host. None, unparsable or any other host counts as cloud."""
    try:
        host = urlsplit(str(url)).hostname if url else None
    except ValueError:
        return False
    if not host:
        return False
    if host == 'localhost':
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


# ----------------------------------------------------------------------------- rows
def _seam_dir() -> Path:
    return paths.home() / 'state' / 'memory' / 'seam'


def _write(harness: str, row: Mapping[str, Any]) -> None:
    path = _seam_dir() / f'{harness}.jsonl'
    path.parent.mkdir(parents=True, exist_ok=True)
    clean, _ = scrub_obj(dict(row, schema=SCHEMA, harness=harness,
                              recorded_at=time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())))
    with open(path, 'a', encoding='utf-8') as fh:
        fcntl.flock(fh, fcntl.LOCK_EX)
        fh.write(json.dumps(clean, ensure_ascii=False) + '\n')
    if clean.get('turn_key'):
        _settle(harness, clean['turn_key'], clean.get('memory'))


# ----------------------------------------------------------------------------- the turn's receipt, for capture
def _receipt_path(harness: str, turn_key: str, suffix: str) -> Path:
    name = hashlib.sha256('\0'.join((harness, str(turn_key))).encode()).hexdigest()[:32]
    return _seam_dir().parent / 'receipts' / f'{name}.{suffix}'


def _expect(harness: str, turn_key: str) -> None:
    """Mark the turn's receipt as pending, before anything can take time."""
    from .surface import prune_markers
    path = _receipt_path(harness, turn_key, 'pending')
    path.parent.mkdir(parents=True, exist_ok=True)
    path.touch()
    prune_markers(path.parent, path)


def _settle(harness: str, turn_key: str, receipt: Any) -> None:
    """The turn's receipt (None: the turn used no memory). The first receipt wins: a later row of the same turn
    (a second injector's guard, a replayed shim) never replaces it."""
    path = _receipt_path(harness, turn_key, 'json')
    try:
        if json.loads(path.read_text(encoding='utf-8')).get('memory') is not None or receipt is None:
            return
    except (OSError, ValueError, AttributeError):
        pass
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f'{path.name}.{os.getpid()}.tmp')
    tmp.write_text(json.dumps({'memory': receipt}, ensure_ascii=False), encoding='utf-8')
    os.replace(tmp, path)


def receipt_for(harness: str, turn_key: Any) -> dict[str, Any] | None:
    """The MemoryUseReceipt the seam settled for this turn, or None. Called from the detached opportunity build:
    a pending turn is waited for up to RECEIPT_WAIT_S; an unmarked turn of a harness whose seam has run here before
    gets RECEIPT_GRACE_S (the two hooks of one turn start together); otherwise it returns at once. Never raises."""
    try:
        if not turn_key:
            return None
        final, pending = _receipt_path(harness, turn_key, 'json'), _receipt_path(harness, turn_key, 'pending')
        t0 = time.monotonic()
        seen = (_seam_dir() / f'{harness}.jsonl').exists()
        while True:
            if final.exists():
                return json.loads(final.read_text(encoding='utf-8')).get('memory')
            waited = time.monotonic() - t0
            if waited >= (RECEIPT_WAIT_S if pending.exists() else RECEIPT_GRACE_S if seen else 0):
                return None
            time.sleep(0.05)
    except Exception:  # noqa: BLE001 - the opportunity is recorded without memory rather than lost
        return None


def rows(harness: str) -> list[dict[str, Any]]:
    try:
        lines = (_seam_dir() / f'{harness}.jsonl').read_text(encoding='utf-8').splitlines()
    except OSError:
        return []
    return [json.loads(line) for line in lines if line.strip()]


def counts(harness: str) -> Counter:
    return Counter(r.get('outcome') for r in rows(harness))


def _first_time(harness: str, injector: str, turn_key: str) -> bool:
    """Idempotency marker per (harness, injector, turn): only the first call of a turn does anything. Markers are
    pruned after the same TTL as the injection-owner markers."""
    from .surface import prune_markers
    d = _seam_dir() / 'turns'
    d.mkdir(parents=True, exist_ok=True)
    path = d / hashlib.sha256('\0'.join((harness, injector, turn_key)).encode()).hexdigest()[:32]
    try:
        os.close(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600))
    except FileExistsError:
        return False
    prune_markers(d, path)
    return True


def project_of(cwd: Any) -> str | None:
    """The task's project as AgentsView names it (parser.ExtractProjectFromCwd): the git repo root's name, a linked
    worktree's main checkout, else the directory's own name; '-' becomes '_'. None without an absolute cwd."""
    if not cwd or not str(cwd).strip():
        return None
    p = Path(str(cwd)).expanduser()
    if not p.is_absolute():
        return None
    root = p
    for d in (p, *p.parents):
        git = d / '.git'
        if git.is_dir():
            root = d
            break
        if git.is_file():
            root = d
            try:
                target = git.read_text(encoding='utf-8').strip()
            except OSError:
                break
            gitdir = Path(target[len('gitdir:'):].strip()) if target.startswith('gitdir:') else None
            if gitdir is not None:
                gitdir = gitdir if gitdir.is_absolute() else d / gitdir
                if gitdir.parent.name == 'worktrees' and gitdir.parent.parent.name == '.git':
                    root = gitdir.parent.parent.parent  # <main>/.git/worktrees/<name>
            break
    return root.name.replace('-', '_') or None


# ----------------------------------------------------------------------------- resolve
def resolve(job: Mapping[str, Any]) -> dict[str, Any]:
    """The brief for one job: C7 ``memory_brief`` scoped to the turn's project (cross-harness recall). A job without
    a project never gets an unscoped brief."""
    from ..memory_contract import MemoryScope
    from .surface import DEFAULT_USER, LAYERS, ScopePolicy, memory_brief
    if not job.get('project'):
        raise ValueError('no task project')
    scope = MemoryScope(user=DEFAULT_USER, project=job['project'])
    policy = ScopePolicy(scope=scope, requester=job['harness'], cross_harness=True)
    layers = [layer for layer in LAYERS if layer not in set(job.get('exclude_layers') or ())]
    return memory_brief(str(job['query'])[:MAX_QUERY_CHARS], policy, max_tokens=int(job.get('max_tokens') or MAX_TOKENS),
                        layers=layers)


def _would_inject(brief: Mapping[str, Any]) -> bool:
    return bool(brief.get('evidence') or brief.get('abstained'))


def _brief_row(brief: Mapping[str, Any]) -> dict[str, Any]:
    return {'memory_snapshot_id': brief.get('memory_snapshot_id'), 'memory': brief.get('receipt'),
            'cache': brief.get('cache'), 'abstained': brief.get('abstained'),
            'evidence_count': len(brief.get('evidence') or ())}


def _bounded(job: Mapping[str, Any], remaining: float) -> tuple[str, dict[str, Any] | None]:
    """('ok', brief) | ('timeout', None) | ('error', None): the resolver runs on a daemon thread joined with the
    remaining budget, so a hung source never holds the turn."""
    box: dict[str, Any] = {}

    def run() -> None:
        try:
            box['brief'] = resolve(job)
        except Exception as exc:  # noqa: BLE001 - fail open: counted, never raised into the turn
            box['error'] = type(exc).__name__

    th = threading.Thread(target=run, name='z0-memory-resolve', daemon=True)
    th.start()
    th.join(max(0.0, remaining))
    if th.is_alive():
        return 'timeout', None
    if 'error' in box or not isinstance(box.get('brief'), dict):
        return 'error', None
    return 'ok', box['brief']


def run_shadow(job: Mapping[str, Any]) -> None:
    """The detached half of shadow mode: compute the brief, record what would have been injected."""
    base = {'mode': 'shadow', 'turn_key': job['turn_key'], 'injector': job['injector'], 'injected': False}
    try:
        brief = resolve(job)
    except Exception as exc:  # noqa: BLE001
        _write(job['harness'], {**base, 'outcome': 'error', 'would_inject': False, 'error': type(exc).__name__})
        return
    _write(job['harness'], {**base, 'outcome': 'shadow', 'would_inject': _would_inject(brief), **_brief_row(brief)})


def _spawn_shadow(job: Mapping[str, Any], slot: Any) -> None:
    """The detached child inherits the locked ``slot`` file, so the slot stays held until it exits."""
    from ..harness_capture import spawn_detached
    spawn_detached([sys.executable, '-m', 'z0int.memory.seam', 'child', '--harness', job['harness']], job,
                   pass_fds=(slot.fileno(),))


def turn(harness: str, *, turn_key: str, query: str, mode: str | None = None, endpoint: str | None = None,
         cwd: str | None = None, injector: str | None = None, exclude_layers: Iterable[str] = (),
         started_at: float | None = None, detach: bool = True) -> dict[str, Any]:
    """One turn through the seam. Returns ``{'context': str | None, 'outcome': ...}``; never raises for a turn."""
    t0 = time.time() if started_at is None else float(started_at)
    cfg = settings(harness, mode=mode)
    injector = injector or f'z0-memory:{harness}'
    out: dict[str, Any] = {'context': None, 'outcome': cfg['mode'], 'turn_key': turn_key}
    if cfg['mode'] == 'off' or not str(query or '').strip():
        return {**out, 'outcome': 'off' if cfg['mode'] == 'off' else 'no_query'}
    if not _first_time(harness, injector, turn_key):
        return {**out, 'outcome': 'replay'}
    _expect(harness, turn_key)
    project = project_of(cwd)
    if project is None:  # fail closed: never a brief built from every project
        _write(harness, {'mode': cfg['mode'], 'turn_key': turn_key, 'injector': injector, 'outcome': 'no_scope',
                         'injected': False, 'would_inject': False})
        return {**out, 'outcome': 'no_scope'}
    job = {'harness': harness, 'turn_key': turn_key, 'query': str(query)[:MAX_QUERY_CHARS], 'project': project,
           'injector': injector, 'exclude_layers': list(exclude_layers), 'max_tokens': cfg['max_tokens']}
    if cfg['mode'] == 'shadow':
        from ..harness_capture import try_slot
        try:
            slot = try_slot(pool=SLOT_POOL)
            if slot is None:
                _write(harness, {'mode': 'shadow', 'turn_key': turn_key, 'injector': injector,
                                 'outcome': 'queue_saturated', 'injected': False, 'would_inject': False})
                return {**out, 'outcome': 'queue_saturated'}
            with slot:
                _spawn_shadow(job, slot) if detach else run_shadow(job)
        except Exception as exc:  # noqa: BLE001 - fail open: counted, never raised into the turn
            _write(harness, {'mode': 'shadow', 'turn_key': turn_key, 'injector': injector, 'outcome': 'error',
                             'injected': False, 'would_inject': False, 'error': type(exc).__name__})
            return {**out, 'outcome': 'error'}
        return out
    base = {'mode': cfg['mode'], 'turn_key': turn_key, 'injector': injector, 'endpoint_loopback': is_loopback(endpoint)}
    if not base['endpoint_loopback'] and not cfg['allow_cloud_injection']:
        _write(harness, {**base, 'outcome': 'cloud_injection_blocked', 'injected': False, 'would_inject': False})
        return {**out, 'outcome': 'cloud_injection_blocked'}
    from .surface import claim_injection
    if not claim_injection(turn_key, injector):
        _write(harness, {**base, 'outcome': 'double_inject_guard', 'injected': False, 'would_inject': False})
        return {**out, 'outcome': 'double_inject_guard'}
    remaining = cfg['deadline_ms'] / 1000.0 - (time.time() - t0)
    status, brief = _bounded(job, remaining)
    latency = round((time.time() - t0) * 1000, 2)
    if brief is None:
        _write(harness, {**base, 'outcome': status, 'injected': False, 'would_inject': False, 'latency_ms': latency})
        return {**out, 'outcome': status}
    inject = _would_inject(brief)
    context = scrub_text(brief['text'])[0] if inject else None
    row = {**base, 'outcome': 'injected' if inject else 'empty', 'injected': inject, 'would_inject': inject,
           'latency_ms': latency, **_brief_row(brief)}
    _write(harness, row)
    return {**out, 'context': context, 'outcome': row['outcome'], 'cache': brief.get('cache'),
            'memory_snapshot_id': brief.get('memory_snapshot_id'), 'memory': brief.get('receipt'),
            'brief': brief}


# ----------------------------------------------------------------------------- CLI (non-Python shims)
def run_job(command: str, harness: str, job: Mapping[str, Any]) -> dict[str, Any]:
    from ..harness_id import turn_key as canonical
    key = job.get('turn_key') or canonical(harness, job.get('session_id'), job.get('turn_id'))
    out = turn(harness, turn_key=key, query=str(job.get('query') or ''), mode=job.get('mode'),
               endpoint=job.get('endpoint'), cwd=job.get('cwd'), injector=job.get('injector'),
               exclude_layers=job.get('exclude_layers') or (), started_at=job.get('started_at'),
               detach=command == 'turn')
    out.pop('brief', None)
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog='python -m z0int.memory.seam', description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('command', choices=['turn', 'shadow', 'child'],
                    help='turn: the shim waits for {"context"}; shadow: the shim already detached, run the shadow '
                         'turn inline; child: internal, the detached half of an in-process shadow turn')
    ap.add_argument('--harness', required=True)
    args = ap.parse_args(argv)
    raw = sys.stdin.read()
    try:
        job = json.loads(raw)
        if not isinstance(job, dict):
            raise ValueError('job must be an object')
        if args.command == 'child':
            run_shadow({**job, 'harness': args.harness})
            out: dict[str, Any] = {'context': None, 'outcome': 'shadow'}
        else:
            out = run_job(args.command, args.harness, dict(job, mode='shadow') if args.command == 'shadow' else job)
    except Exception as exc:  # noqa: BLE001 - a shim gets native context, and the failure is counted
        try:
            _write(args.harness, {'outcome': 'error', 'injected': False, 'would_inject': False,
                                  'error': type(exc).__name__})
        except OSError:
            pass
        out = {'context': None, 'outcome': 'error'}
    sys.stdout.write(json.dumps(out, ensure_ascii=False) + '\n')
    sys.stdout.flush()
    os._exit(0)  # a resolver thread past its deadline must not hold the shim's process open


if __name__ == '__main__':
    main()
