"""`z0int loop tick`: one scheduled, offline pass of the verified learning loop (z0int#56 stages 2-5, M2 minimal).

    verify -> import -> shadow -> export -> sufficiency -> learn -> promote, then a counts-only report

The tick takes flock(2) LOCK_SH on the quiet-lane lock in-process (the same kernel lock ``flock -s`` takes; a POSIX
fcntl/lockf record lock would not interact with a quiet-timed ``flock -x``). When an exclusive window holds the lock
past ``--lock-wait-s`` it writes a ``skipped_exclusive_window`` report and exits 0. The hold is bounded by
``--budget-s``: an exhausted budget stops between steps, releases the lock and reports ``partial_measurement``.

Steps (each reuses its module; nothing here re-derives a label or a threshold the learner owns):
  verify       outcome_verifier.verify_harness per captured harness (Claude transcripts from CLAUDE_CONFIG_DIR,
               AgentsView mode=ro from AGENTSVIEW_DATA_DIR), rows appended idempotently, no GitHub/network lookups
  import       legacy_import (OMP v1 spine, OMP cognition-shadow, DSH jev receipts) incrementally by watermark
  shadow       shadow_slot.replay: registered challengers over every new recorded opportunity
  export       loop_export.export_tables: one table per harness x cohort, never pooled
  sufficiency  per stratum, the evolution-lab verified-loop v0 gate (MIN_ROWS/MIN_NEG/MIN_GROUPS/K) + a projection
  learn        `python -m evolution_lab.verified_loop` per stratum covered by a registered prereg (v0: claude-code),
               from the learner pinned by learner_sha in $Z0INT_HOME/config/loop.json; cohort legacy never
  promote      a SHADOW_CANDIDATE writes promotion_request.v0 for the owner and nothing else

Writes only under $Z0INT_HOME/state and the out root ($Z0INT_HOME/loop by default; refused inside a git worktree, so
nothing learned from personal data can be committed). Never promotes, never writes harness config, never connects
to a service.
"""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import resource
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Mapping

from . import harness_capture as hc
from . import loop_export as le
from . import shadow_slot

STEPS = ('verify', 'import', 'shadow', 'export', 'sufficiency', 'learn', 'promote')
SUMMARY_SCHEMA = 'z0int.loop.tick_summary.v0'
REQUEST_SCHEMA = 'z0int.loop.promotion_request.v0'
COST_SCHEMA = 'z0int.loop_tick.cost.v0'
QUIET_LANE_LOCK = '/mnt/zer0models/cua-lane-tmp/locks/quiet-lane.lock'
# The sufficiency gate of evolution-lab docs/prereg/verified-loop-v0.md, reported here for every stratum (with a
# projection) so strata without a learner run still show how far they are; the learner's own run decides.
MIN_ROWS, MIN_NEG, MIN_GROUPS, K = 300, 30, 10, 5
PREREGS = {'claude-code': 'evolution-lab docs/prereg/verified-loop-v0.md'}  # others after C11 (G-PREREG)
LIFECYCLE = {'ref': 'kvnloo/z0 registry/lifecycles.yaml#evolution-lab-promotion',
             'stages': ['sanity', 'replay', 'shadow', 'promoted']}
CONTRACT_FIELDS = ('family_identity', 'evidence_state_version', 'cohorts', 'operating_region', 'calibration',
                   'verifier_contract', 'cost_vector', 'invalidators', 'drift_monitors', 'fallback', 'demotion_reason',
                   'lineage')  # z0int#56: the owner fills these before anything goes past shadow
EXIT_REFUSED, EXIT_DEGRADED = 2, 3
LEARNER_PROBE = ('import json, os, importlib.metadata as md\nimport evolution_lab.verified_loop as m\nsha = None\n'
                 'try:\n    sha = json.loads(md.distribution("evolution-lab").read_text("direct_url.json") or "{}")'
                 '.get("vcs_info", {}).get("commit_id")\nexcept Exception:\n    pass\n'
                 'print(json.dumps({"dir": os.path.dirname(os.path.abspath(m.__file__)), "dist_sha": sha}))')


def _iso(t: float) -> str:
    return time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime(t))


def in_git_worktree(path: str | Path) -> bool:
    p = Path(os.path.abspath(path))
    return any((q / '.git').exists() for q in (p, *p.parents))


def load_config(home: Path) -> dict[str, Any]:
    try:
        cfg = json.loads((home / 'config' / 'loop.json').read_text())
        return cfg if isinstance(cfg, dict) else {}
    except (OSError, ValueError):
        return {}


# ----------------------------------------------------------------------------- lock
def acquire_shared(lock: Path, wait_s: float) -> Any:
    """The open lock file holding LOCK_SH, or None when an exclusive holder outlasted ``wait_s``."""
    fh = open(lock, 'a')
    deadline = time.monotonic() + wait_s
    while True:
        try:
            fcntl.flock(fh, fcntl.LOCK_SH | fcntl.LOCK_NB)
            return fh
        except BlockingIOError:
            left = deadline - time.monotonic()
            if left <= 0:
                fh.close()
                return None
            time.sleep(min(0.25, left))


# ----------------------------------------------------------------------------- learner pin
def learner_status(cfg: Mapping[str, Any], python: str) -> dict[str, Any]:
    """ok only when evolution_lab.verified_loop imports from the tick venv at the commit loop.json pins."""
    pinned = cfg.get('learner_sha')
    try:
        p = subprocess.run([python, '-c', LEARNER_PROBE], capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.TimeoutExpired):
        p = None
    if p is None or p.returncode != 0:
        return {'status': 'learner_missing', 'reason': 'not_importable'}
    info = json.loads(p.stdout.strip().splitlines()[-1])
    g = subprocess.run(['git', '--no-optional-locks', '-C', info['dir'], 'rev-parse', 'HEAD'], capture_output=True,
                       text=True, env=dict(os.environ, GIT_OPTIONAL_LOCKS='0'))
    found = g.stdout.strip() if g.returncode == 0 and g.stdout.strip() else info.get('dist_sha')
    if not pinned or not found or not (found.startswith(pinned) or pinned.startswith(found)):
        reason = 'no_pin' if not pinned else 'sha_unknown' if not found else 'sha_mismatch'
        return {'status': 'learner_missing', 'reason': reason, 'pinned': pinned, 'found': found}
    return {'status': 'ok', 'sha': found}


# ----------------------------------------------------------------------------- sufficiency
def analysis_rows(rows: list[Mapping[str, Any]]) -> list[Mapping[str, Any]]:
    """The prereg's analysis set: an opportunity, a resolved label and an observed ACT."""
    return [r for r in rows if r.get('has_opportunity') and r.get('features') is not None
            and (r.get('label') or {}).get('resolved') and (r.get('observed') or {}).get('action') == 'ACT']


def sufficiency(ana: list[Mapping[str, Any]]) -> dict[str, Any]:
    y = [int(r['label']['y_success']) for r in ana]
    groups = sorted({r['group'] for r in ana})
    k = min(K, len(groups))
    fold = {g: i % k for i, g in enumerate(groups)} if k else {}
    classes: dict[int, set[int]] = {}
    for r, yi in zip(ana, y):
        classes.setdefault(fold[r['group']], set()).add(yi)
    observed = {'rows': len(ana), 'not_success': y.count(0), 'groups': len(groups)}
    checks = {'rows>=300': observed['rows'] >= MIN_ROWS, 'not_success>=30': observed['not_success'] >= MIN_NEG,
              'groups>=10': observed['groups'] >= MIN_GROUPS,
              'k==5_and_every_test_fold_has_both_classes': k == K and all(c == {0, 1} for c in classes.values())}
    days = sorted({r['day'] for r in ana if r.get('day')})
    proj: dict[str, Any] = {'days_observed': 0, 'days_to_sufficiency': None, 'binding': None}
    if days:
        span = (time.mktime(time.strptime(days[-1], '%Y-%m-%d')) - time.mktime(time.strptime(days[0], '%Y-%m-%d')))
        proj['days_observed'] = int(round(span / 86400)) + 1
        need = {'rows>=300': (MIN_ROWS, 'rows'), 'not_success>=30': (MIN_NEG, 'not_success'),
                'groups>=10': (MIN_GROUPS, 'groups')}
        waits = {}
        for name, (target, key) in need.items():
            rate = observed[key] / proj['days_observed']
            waits[name] = 0.0 if observed[key] >= target else (target - observed[key]) / rate if rate else float('inf')
        proj['rates_per_day'] = {key: observed[key] / proj['days_observed'] for _, key in need.values()}
        proj['binding'] = max(waits, key=waits.get)
        proj['days_to_sufficiency'] = None if waits[proj['binding']] == float('inf') else waits[proj['binding']]
    return {'decision': 'sufficient' if all(checks.values()) else 'INSUFFICIENT_DATA', 'checks': checks,
            'observed': observed, 'projection': proj,
            'thresholds': {'MIN_ROWS': MIN_ROWS, 'MIN_NEG': MIN_NEG, 'MIN_GROUPS': MIN_GROUPS, 'K': K}}


def discrimination(ana: list[Mapping[str, Any]]) -> dict[str, Any] | None:
    """The champion gate's Brier and AUROC on the analysis set, next to the base-rate constant predictor."""
    if not ana:
        return None
    y = [int(r['label']['y_success']) for r in ana]
    p = [1.0 if r.get('gate') == 'ACT' else 0.0 for r in ana]
    base = sum(y) / len(y)
    pos = [pi for pi, yi in zip(p, y) if yi == 1]
    neg = [pi for pi, yi in zip(p, y) if yi == 0]
    auroc = (sum(1.0 if a > b else 0.5 if a == b else 0.0 for a in pos for b in neg) / (len(pos) * len(neg))
             if pos and neg else None)
    return {'predictor': 'deterministic_gate', 'n': len(y),
            'brier': sum((pi - yi) ** 2 for pi, yi in zip(p, y)) / len(y),
            'auroc': auroc, 'base_rate': base, 'base_rate_brier': sum((base - yi) ** 2 for yi in y) / len(y),
            'reference': 'base-rate constant predictor (AUROC 0.5)'}


# ----------------------------------------------------------------------------- the tick
def _legacy_sources(home: Path, cfg: Mapping[str, Any]) -> dict[str, Any]:
    src = {'omp-v1': {'decisions': home / 'receipts' / 'decisions.jsonl',
                      'outcomes': home / 'receipts' / 'outcomes.jsonl'},
           'omp-cognition-shadow': home / 'shadow' / 'cognition-shadow.jsonl',
           'dsh-jev': Path.home() / '.dsh' / 'jev' / 'receipts.jsonl'}
    for k, v in (cfg.get('legacy_sources') or {}).items():
        src[k] = {kk: Path(vv) for kk, vv in v.items()} if isinstance(v, dict) else Path(v)
    return src


def _captured(home: Path, harness: str) -> bool:
    s = hc.state_dir(harness, home)
    return (s / 'opportunities.jsonl').exists() or (s / 'outcomes.jsonl').exists()


def _harness_counts(home: Path, harness: str, verify: Mapping[str, Any] | None,
                    records: Mapping[str, Any]) -> dict[str, Any]:
    s = hc.state_dir(harness, home)
    latest = {}
    for r in hc._read_jsonl(s / 'outcomes_verified.jsonl'):
        if r.get('schema') == hc.schema(harness, 'turn_outcome_verified'):
            latest[(r.get('session_id'), r.get('trace_id'))] = le.label_of(r)['y_success']
    join = (verify or {}).get('join') or {}
    tried = sum(join.values())
    return {'captured': sum(1 for r in hc._read_jsonl(s / 'opportunities.jsonl')
                            if r.get('schema') == hc.schema(harness, 'opportunity_record')),
            'verified': len(latest), 'labelled': sum(1 for y in latest.values() if y is not None),
            'join': dict(join), 'join_rate': join.get('joined', 0) / tried if tried else None,
            'drops': hc.drop_count(harness, home), 'failures': (records.get(harness) or {}).get('failures', {}),
            'verify_status': (verify or {}).get('status')}


def run_tick(z0int_home: str | Path, *, lock: str | Path = QUIET_LANE_LOCK, lock_wait_s: float = 1800.0,
             budget_s: float = 600.0, out_root: str | Path | None = None, agentsview: str | Path | None = None,
             projects: str | Path | None = None, ledger: str | Path | None = None) -> dict[str, Any]:
    home = Path(z0int_home).expanduser().resolve()
    out = Path(out_root).expanduser().resolve() if out_root else home / 'loop'
    if in_git_worktree(out):
        raise ValueError(f'refusing out root {out}: it is inside a git worktree (learned artifacts are never '
                         'committed)')
    t_start, wall0 = time.monotonic(), time.time()
    ru0 = [resource.getrusage(w) for w in (resource.RUSAGE_SELF, resource.RUSAGE_CHILDREN)]
    day = time.strftime('%Y-%m-%d', time.gmtime(wall0))
    summary: dict[str, Any] = {'schema': SUMMARY_SCHEMA, 'tick_id': le._sha({'t': wall0, 'pid': os.getpid()}, 16),
                               'day': day, 'started_at': _iso(wall0), 'budget_s': budget_s, 'steps': [],
                               'degraded_by': [], 'privacy': 'counts, hashes and closed vocabulary only'}
    fh = acquire_shared(Path(lock), lock_wait_s)
    summary['lock'] = {'path': str(lock), 'mode': 'flock(2) LOCK_SH', 'waited_s': round(time.monotonic() - t_start, 3)}
    if fh is None:
        summary.update(status='skipped_exclusive_window', exit=0, ended_at=_iso(time.time()))
        return _write_summary(out, summary)
    acquired = time.time()
    deadline = time.monotonic() + budget_s
    cfg = load_config(home)
    state: dict[str, Any] = {'day': day, 'verify': {}, 'imports': {}, 'shadow': {}, 'index': None, 'strata': {},
                             'results': {}, 'learner': {'status': 'not_needed'}, 'requests': []}
    try:
        for name in STEPS:
            t0 = time.monotonic()
            entry = {'name': name, 'started_at': round(t0 - t_start, 6)}
            summary['steps'].append(entry)
            if t0 >= deadline:
                entry.update(status='skipped_budget', seconds=0.0)
                continue
            try:
                _STEP[name](home, out, cfg, state, deadline=deadline, agentsview=agentsview, projects=projects)
                entry['status'] = 'partial' if state.get(f'{name}_partial') else 'done'
            except Exception as exc:  # one failing step is reported; the others still run
                entry['status'] = f'error:{type(exc).__name__}'
                summary['degraded_by'].append(f'step_error:{name}')
            entry['seconds'] = round(time.monotonic() - t0, 3)
    finally:
        fcntl.flock(fh, fcntl.LOCK_UN)
        fh.close()
        released = time.time()
        if ledger:
            _ledger(Path(ledger), acquired, released)
    records = le.scan_records(home)
    summary['harnesses'] = {h: _harness_counts(home, h, state['verify'].get(h), records)
                            for h in hc.HARNESSES if hc.state_dir(h, home).is_dir()}
    for rep in state['verify'].values():
        summary['degraded_by'] += sorted({f['kind'] for f in rep['failures'] if f['kind'] in _degrading()})
    if state['learner']['status'] == 'learner_missing':
        summary['degraded_by'].append('learner_missing')
    summary['degraded_by'] = sorted(set(summary['degraded_by']))
    summary['rows_added'] = {'verified': sum(r['appended'] for r in state['verify'].values()),
                             'imported': sum(m.get('rows_added', 0) for m in state['imports'].values()),
                             'shadow_decisions': state['shadow'].get('decisions_added', 0)}
    index = state['index'] or {}
    summary.update(imports={k: {'rows_added': m.get('rows_added', 0), 'counts': m.get('counts', {})}
                            for k, m in state['imports'].items()},
                   shadow={k: v for k, v in state['shadow'].items() if k != 'decisions_added'},
                   tables={'manifest_sha256': index.get('manifest_sha256'), 'tables': index.get('tables', {}),
                           'shadow_tables': index.get('shadow_tables', {})},
                   strata=state['strata'], learner=state['learner'], results=state['results'],
                   promotion_requests=state['requests'])
    partial = any(s['status'] in ('skipped_budget', 'partial') for s in summary['steps'])
    summary['status'] = 'partial_measurement' if partial else 'degraded' if summary['degraded_by'] else 'success'
    summary['exit'] = 0 if summary['status'] == 'success' else EXIT_DEGRADED
    summary['ended_at'] = _iso(time.time())
    ru1 = [resource.getrusage(w) for w in (resource.RUSAGE_SELF, resource.RUSAGE_CHILDREN)]
    event = {'schema': COST_SCHEMA, 'harness': 'z0int-loop', 'event_kind': 'loop_tick', 'tick_id': summary['tick_id'],
             'wall_s': round(time.monotonic() - t_start, 3), 'lock_held_s': round(released - acquired, 3),
             'cpu_s': round(sum(b.ru_utime + b.ru_stime - a.ru_utime - a.ru_stime for a, b in zip(ru0, ru1)), 3),
             'usage': {'input_tokens': 0, 'output_tokens': 0}, 'cost_usd': 0.0, 'gpu': False,
             'measurement_state': 'partial' if partial else 'complete', 'task_success': None}
    from .tokenomics_emit import emit_raw
    emit_raw(event, path=out / 'tokenomics' / 'events.jsonl')
    summary['tokenomics'] = event
    return _write_summary(out, summary)


def _degrading() -> tuple[str, ...]:
    from .outcome_verifier import DEGRADING
    return DEGRADING


def _write_summary(out: Path, summary: dict[str, Any]) -> dict[str, Any]:
    d = out / 'reports' / summary['day']
    d.mkdir(parents=True, exist_ok=True)
    body = json.dumps(summary, indent=1, sort_keys=True) + '\n'
    (d / 'summary.json').write_text(body)
    with open(d / 'ticks.jsonl', 'a', encoding='utf-8') as fh:
        fh.write(json.dumps(summary, sort_keys=True) + '\n')
    return json.loads(body)


def _ledger(path: Path, acquired: float, released: float) -> None:
    """The quiet-lane ledger convention: one line per shared hold."""
    row = {'lane': 'z0int-loop-tick', 'label': 'z0int-loop-tick', 'mode': 'shared', 'pid': os.getpid(),
           'acquired': _iso(acquired), 'released': _iso(released), 'rc': 0,
           'loadavg_at_acquire': ' '.join(f'{x:.2f}' for x in os.getloadavg())}
    try:
        with open(path, 'a', encoding='utf-8') as fh:
            fh.write(json.dumps(row) + '\n')
    except OSError:
        pass


# ----------------------------------------------------------------------------- steps
def _verify(home: Path, out: Path, cfg: Mapping, st: dict, *, agentsview: Any, projects: Any, **_: Any) -> None:
    from . import outcome_verifier as ov
    for h in hc.HARNESSES:
        if _captured(home, h):
            st['verify'][h] = ov.verify_harness(h, root=home, agentsview=agentsview,
                                                projects=Path(projects) if projects else None, gh=ov.GitHub(False),
                                                write=True)


def _import(home: Path, out: Path, cfg: Mapping, st: dict, **_: Any) -> None:
    from . import legacy_import as li
    src = _legacy_sources(home, cfg)
    if Path(src['omp-v1']['decisions']).exists():
        st['imports']['omp-v1'] = li.import_omp_v1(src['omp-v1']['decisions'], src['omp-v1']['outcomes'], root=home)
    if Path(src['omp-cognition-shadow']).exists():
        st['imports']['omp-cognition-shadow'] = li.import_cognition_shadow(src['omp-cognition-shadow'], root=home)
    if Path(src['dsh-jev']).exists():
        st['imports']['dsh-jev'] = li.import_dsh_jev(src['dsh-jev'], root=home)


def _shadow(home: Path, out: Path, cfg: Mapping, st: dict, *, deadline: float, **_: Any) -> None:
    st['shadow'] = shadow_slot.replay(home, deadline=deadline)
    st['shadow_partial'] = st['shadow'].get('partial')


def _export(home: Path, out: Path, cfg: Mapping, st: dict, *, projects: Any, **_: Any) -> None:
    st['index'] = le.export_tables(out / 'tables', root=home, projects=Path(projects) if projects else None)


def _sufficiency(home: Path, out: Path, cfg: Mapping, st: dict, **_: Any) -> None:
    preregs = cfg.get('preregs') or PREREGS
    for key, entry in (st['index'] or {}).get('tables', {}).items():
        harness, cohort = key.split('/')
        rows = le._read_jsonl(out / 'tables' / harness / f'{cohort}.jsonl')
        ana = analysis_rows(rows)
        st['strata'][key] = {'harness': harness, 'cohort': cohort, 'rows': len(rows), 'analysis_rows': len(ana),
                             'sufficiency': sufficiency(ana), 'discrimination': discrimination(ana),
                             'prereg': preregs.get(harness) if cohort != 'legacy' else None,
                             'learn': 'not_learnable' if cohort == 'legacy' else
                             'pending' if harness in preregs else 'prereg_missing'}


def _learn(home: Path, out: Path, cfg: Mapping, st: dict, *, deadline: float, **_: Any) -> None:
    todo = [k for k, s in st['strata'].items() if s['learn'] == 'pending']
    if not todo:
        return
    python = cfg.get('learner_python') or sys.executable
    st['learner'] = learner_status(cfg, python)
    for key in todo:
        s = st['strata'][key]
        if st['learner']['status'] != 'ok':
            s['learn'] = 'learner_missing'
            continue
        left = deadline - time.monotonic()
        if left <= 0:
            s['learn'] = 'skipped_budget'
            st['learn_partial'] = True
            continue
        res = out / 'results' / st['day'] / s['harness'] / f"{s['cohort']}.json"
        res.parent.mkdir(parents=True, exist_ok=True)
        table = out / 'tables' / s['harness'] / f"{s['cohort']}.jsonl"
        try:
            p = subprocess.run([python, '-m', 'evolution_lab.verified_loop', '--table', str(table), '--out', str(res),
                                '--md', str(res.with_suffix('.md'))], capture_output=True, text=True, timeout=left)
            decision = json.loads(res.read_text())['primary']['decision'] if p.returncode == 0 else None
        except subprocess.TimeoutExpired:
            s['learn'] = 'skipped_budget'
            st['learn_partial'] = True
            continue
        except (OSError, ValueError, KeyError):
            decision = None
        s['learn'] = decision or 'learner_error'
        if decision:
            st['results'][key] = {'decision': decision, 'prereg': s['prereg'], 'learner_sha': st['learner']['sha'],
                                  'result': os.path.relpath(res, home),
                                  'table_manifest_sha256': (st['index']['tables'][key] or {}).get('manifest_sha256')}


def _promote(home: Path, out: Path, cfg: Mapping, st: dict, **_: Any) -> None:
    for key, r in sorted(st['results'].items()):
        if r['decision'] != 'SHADOW_CANDIDATE':
            continue
        result = home / r['result']
        cand = hashlib.sha256(result.read_bytes()).hexdigest()
        path = out / 'requests' / f'promotion_request-{cand[:16]}.json'
        if not path.exists():  # one request per candidate; a re-run never rewrites or re-requests
            harness, cohort = key.split('/')
            body = {'schema': REQUEST_SCHEMA, 'candidate_sha256': cand, 'family': 'act_gate',
                    'stratum': {'harness': harness, 'cohort': cohort}, 'prereg': r['prereg'],
                    'learner_sha': r['learner_sha'],
                    'evidence': {'result': r['result'], 'table_manifest_sha256': r['table_manifest_sha256']},
                    'lifecycle': dict(LIFECYCLE, requested_stage='shadow'), 'approved': False,
                    'contract_fields_to_fill': dict.fromkeys(CONTRACT_FIELDS),
                    'note': 'A SHADOW_CANDIDATE earns shadow status only. Nothing was promoted or configured; going '
                            'past shadow needs online shadow evidence, the #56 contract and explicit owner approval.',
                    'requested_at': _iso(time.time())}
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(body, indent=1, sort_keys=True) + '\n')
        st['requests'].append(os.path.relpath(path, home))


_STEP = {'verify': _verify, 'import': _import, 'shadow': _shadow, 'export': _export, 'sufficiency': _sufficiency,
         'learn': _learn, 'promote': _promote}


# ----------------------------------------------------------------------------- CLI
def _main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog='z0int loop tick', description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--z0int-home', type=Path, default=None, help='default: $Z0INT_HOME or ~/.z0int')
    ap.add_argument('--lock', type=Path, default=Path(QUIET_LANE_LOCK), help='quiet-lane lock, held LOCK_SH')
    ap.add_argument('--lock-wait-s', type=float, default=1800.0)
    ap.add_argument('--budget-s', type=float, default=600.0)
    ap.add_argument('--out-root', type=Path, default=None, help='default: <z0int-home>/loop (never in a git tree)')
    ap.add_argument('--agentsview-db', type=Path, default=None, help='default $AGENTSVIEW_DATA_DIR/sessions.db')
    ap.add_argument('--projects-dir', type=Path, default=None, help='default $CLAUDE_CONFIG_DIR/projects')
    ap.add_argument('--ledger', type=Path, default=None, help='append the hold to this quiet-lane ledger')
    ap.add_argument('--json', action='store_true')
    args = ap.parse_args(sys.argv[1:] if argv is None else argv)
    try:
        rep = run_tick(args.z0int_home or hc.home(), lock=args.lock, lock_wait_s=args.lock_wait_s,
                       budget_s=args.budget_s, out_root=args.out_root, agentsview=args.agentsview_db,
                       projects=args.projects_dir, ledger=args.ledger)
    except ValueError as exc:
        print(f'refused: {exc}')
        return EXIT_REFUSED
    if args.json:
        print(json.dumps(rep, indent=1, sort_keys=True))
    else:
        steps = ' '.join(f"{s['name']}={s['status']}" for s in rep['steps'])
        print(f"tick {rep['status']} ({', '.join(rep['degraded_by']) or 'no degradation'}); {steps}; "
              f"rows_added={rep.get('rows_added', {})}; results="
              f"{ {k: v['decision'] for k, v in rep.get('results', {}).items()} }")
    return rep['exit']


if __name__ == '__main__':
    raise SystemExit(_main())
