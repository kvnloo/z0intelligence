"""Verified-loop training table v0 (z0#15 steps 7-9; z0int#54/#55/#56; evolution-lab#24).

Joins, per Claude Code turn ``(session_id, trace_id)``:

  opportunity_record.v0  (prompt time: DecisionOpportunity + deterministic gate)
  turn_outcome.v0        (Stop time: what the agent did -- post-decision, never a feature)
  turn_outcome_verified.v0 (after the fact: independent verification state)

into one privacy-safe row per turn with a fixed, versioned feature vocabulary. Rows carry
counts, booleans, small closed-vocabulary categoricals and hashed ids. No prompt, response,
command, path or claim value is ever written, and request text is never read: harness-injected
prompts and subagent turns are recognised from the capture-time cohort and flags (``harness_capture``).

Feature columns are prompt-time only (what the gate saw). ``observed`` is post-decision and is
exported so an offline evaluator can restrict to turns where the agent actually acted; a learner
must not use it as an input. ``label`` is the verified outcome; ``unverified`` is missing, not
negative.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import time
from collections import Counter
from pathlib import Path
from typing import Any, Iterable, Mapping

from .decision_opportunity import ACTIONS, EFFECTS, FACT_FAMILIES

SCHEMA = 'z0int.loop.training_row.v0'
MANIFEST_SCHEMA = 'z0int.loop.training_table_manifest.v0'
TABLE_VERSION = '0.1.0'
OPP_SCHEMA = 'z0int.claude_code.opportunity_record.v0'
OBSERVED_SCHEMA = 'z0int.claude_code.turn_outcome.v0'
VERIFIED_SCHEMA = 'z0int.claude_code.turn_outcome_verified.v0'
HARNESS = 'claude-code'

FAMILIES = tuple(sorted(FACT_FAMILIES))
SCOPE_MODES = ('question', 'unscoped', 'repo')
UNKNOWN_STATUSES = ('unknown', 'source_unavailable', 'no_match')
POSTURE_FACTORY = ('BURN', 'BALANCED', 'CONSERVE', 'other', 'absent')
AUTHORITY_SOURCES = ('aodl', 'harness-default')
COHORTS = ('interactive', 'agent', 'harness', 'unknown')
STATES = ('verified_success', 'verified_failure', 'contested', 'unverified')

# Ordered feature vocabulary. Changing it is a TABLE_VERSION bump.
FEATURES: tuple[str, ...] = (
    *(f'scope_mode={m}' for m in SCOPE_MODES),
    *(f'family.{f}' for f in FAMILIES),
    'n_families',
    'n_claims', 'n_superseded',
    'n_unknowns', 'n_unknowns_blocking',
    *(f'n_unknowns_status={s}' for s in UNKNOWN_STATUSES),
    *(f'unknown_family.{f}' for f in FAMILIES),
    'n_contradictions',
    *(f'contradiction_family.{f}' for f in FAMILIES),
    *(f'effect.{e}' for e in EFFECTS),
    *(f'authority_source={a}' for a in AUTHORITY_SOURCES),
    *(f'grant.{e}' for e in EFFECTS),
    'authority_fingerprinted',
    'n_missing_authority',
    *(f'legal.{a}' for a in ACTIONS),
    *(f'gate={a}' for a in ACTIONS),
    *(f'posture_factory={p}' for p in POSTURE_FACTORY),
    *(f'cohort={c}' for c in COHORTS),
)
FEATURE_SCHEMA_SHA = hashlib.sha256(json.dumps(FEATURES).encode()).hexdigest()[:16]


def _sha(obj: Any, n: int = 16) -> str:
    raw = json.dumps(obj, sort_keys=True, separators=(',', ':'), ensure_ascii=False, default=str)
    return hashlib.sha256(raw.encode('utf-8')).hexdigest()[:n]


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    out = []
    try:
        fh = path.open(encoding='utf-8', errors='replace')
    except OSError:
        return out
    with fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(row, dict):
                out.append(row)
    return out


def family_of(key: str | None) -> str | None:
    """Map a claim/unknown key to its FACT_FAMILIES name (closed vocabulary) or None."""
    if not key:
        return None
    for fam in FAMILIES:
        for p in FACT_FAMILIES[fam][0]:
            p0 = p.rstrip('.')
            if key == p0 or key.startswith(p0 + '.') or key.startswith(p0 + '[') or (p.endswith('.') and key.startswith(p)):
                return fam
    return None


def opportunity_features(record: Mapping[str, Any], cohort: str = 'unknown') -> dict[str, int]:
    """Prompt-time feature vector (ints) from one opportunity_record.v0. Reads no text."""
    opp = record.get('opportunity') or {}
    f = dict.fromkeys(FEATURES, 0)
    scope = opp.get('scope') or {}
    mode = scope.get('mode')
    if mode in SCOPE_MODES:
        f[f'scope_mode={mode}'] = 1
    fams = [x for x in scope.get('families') or [] if x in FACT_FAMILIES]
    for fam in fams:
        f[f'family.{fam}'] = 1
    f['n_families'] = len(fams)
    state = opp.get('state') or {}
    f['n_claims'] = len(state.get('claims') or [])
    f['n_superseded'] = len(state.get('superseded') or [])
    unknowns = state.get('unknowns') or []
    f['n_unknowns'] = len(unknowns)
    f['n_unknowns_blocking'] = sum(1 for u in unknowns if u.get('blocking'))
    for u in unknowns:
        st = u.get('status')
        if st in UNKNOWN_STATUSES:
            f[f'n_unknowns_status={st}'] += 1
        fam = family_of(u.get('key'))
        if fam:
            f[f'unknown_family.{fam}'] = 1
    contradictions = state.get('contradictions') or []
    f['n_contradictions'] = len(contradictions)
    for c in contradictions:
        fam = family_of(c.get('contests') or c.get('key'))
        if fam:
            f[f'contradiction_family.{fam}'] = 1
    intent = opp.get('intent') or {}
    effects = [e for e in intent.get('effects') or [] if e in EFFECTS]
    for e in effects:
        f[f'effect.{e}'] = 1
    auth = opp.get('authority') or {}
    if auth.get('source') in AUTHORITY_SOURCES:
        f[f'authority_source={auth["source"]}'] = 1
    grants = [g for g in auth.get('grants') or [] if g in EFFECTS]
    for g in grants:
        f[f'grant.{g}'] = 1
    f['authority_fingerprinted'] = int(bool(auth.get('fingerprint')))
    f['n_missing_authority'] = sum(1 for e in effects if e not in grants)
    for a in opp.get('action_space') or []:
        if a.get('legal') and a.get('kind') in ACTIONS:
            f[f'legal.{a["kind"]}'] = 1
    gate = record.get('gate')
    if gate in ACTIONS:
        f[f'gate={gate}'] = 1
    resource = ((opp.get('invalidation') or {}).get('source_revisions') or {}).get('resource')
    factory = (resource or {}).get('factory') if isinstance(resource, Mapping) else None
    f[f'posture_factory={factory if factory in POSTURE_FACTORY else ("absent" if not factory else "other")}'] = 1
    f[f'cohort={cohort if cohort in COHORTS else "unknown"}'] = 1
    return f


def transcript_cohort(session_id: str | None, projects: Path | None = None) -> str:
    """interactive / agent / harness from the transcript's entrypoint+cwd; neither is exported."""
    if not session_id:
        return 'unknown'
    from .claude_code_engagement import cohort
    from .outcome_verifier import find_transcript
    path = find_transcript(session_id, projects)
    if path is None:
        return 'unknown'
    try:
        with path.open(encoding='utf-8', errors='replace') as fh:
            for i, line in enumerate(fh):
                if i > 400:
                    break
                try:
                    d = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(d, dict) and d.get('cwd'):
                    return cohort(d.get('entrypoint'), d.get('cwd'))
    except OSError:
        pass
    return 'unknown'


def label_of(verified: Mapping[str, Any] | None) -> dict[str, Any]:
    if verified is None:
        return {'present': False, 'state': None, 'label_class': None, 'label_confidence': None,
                'y_success': None, 'resolved': False, 'oracles': []}
    state = verified.get('verification_state')
    y = 1 if state == 'verified_success' else 0 if state in ('verified_failure', 'contested') else None
    return {'present': True, 'state': state, 'label_class': verified.get('label_class'),
            'label_confidence': verified.get('label_confidence'), 'y_success': y, 'resolved': y is not None,
            'oracles': sorted({s.get('oracle') for s in verified.get('signals') or [] if s.get('polarity')} - {None})}


def capture_cohort(record: Mapping[str, Any] | None) -> str | None:
    """The cohort fixed at capture when it is not the session's (harness-injected prompt, subagent turn).

    A row without capture flags predates them (its request text is the only clue, and the export never reads
    it): cohort unknown, never a user row, until ``z0int outcomes backfill-capture`` flags it.
    """
    if not record:
        return None
    if not isinstance(record.get('capture'), Mapping):
        return 'unknown'
    if record['capture'].get('is_harness_message'):
        return 'harness'
    return record.get('cohort') if record.get('cohort') in ('agent', 'harness') else None


def build_table(state: Path, *, projects: Path | None = None, include_unjoined: bool = False,
                cohort_fn=None) -> list[dict[str, Any]]:
    """One row per turn that has an opportunity record (or, with include_unjoined, any verified turn)."""
    cohort_fn = cohort_fn or (lambda sid: transcript_cohort(sid, projects))
    opps: dict[tuple, dict[str, Any]] = {}
    for r in _read_jsonl(state / 'opportunities.jsonl'):
        if r.get('schema') != OPP_SCHEMA:
            continue
        opp = r.get('opportunity') or {}
        opps[(r.get('session_id'), (opp.get('trace') or {}).get('trace_id'))] = r  # latest wins
    observed = {(r.get('session_id'), r.get('trace_id')): r for r in _read_jsonl(state / 'outcomes.jsonl')
                if r.get('schema') == OBSERVED_SCHEMA}
    verified: dict[tuple, dict[str, Any]] = {}
    for r in _read_jsonl(state / 'outcomes_verified.jsonl'):
        if r.get('schema') == VERIFIED_SCHEMA:
            verified[(r.get('session_id'), r.get('trace_id'))] = r  # append-only: latest row wins
    keys = list(opps)
    if include_unjoined:
        keys += [k for k in verified if k not in opps]
    cohorts: dict[str, str] = {}
    rows = []
    for key in keys:
        sid, tid = key
        if sid not in cohorts:
            cohorts[sid] = cohort_fn(sid)
        rec, obs, ver = opps.get(key), observed.get(key), verified.get(key)
        cohort = capture_cohort(rec) or cohorts[sid]
        opp = (rec or {}).get('opportunity') or {}
        started = ((ver or {}).get('turn') or {}).get('started_at') or (opp.get('provenance') or {}).get('built_at')
        rows.append({
            'schema': SCHEMA, 'table_version': TABLE_VERSION, 'feature_schema_sha': FEATURE_SCHEMA_SHA,
            'turn_key': _sha({'session': sid, 'trace': tid}),
            'group': _sha({'session': sid}, 12),
            'day': started[:10] if isinstance(started, str) else None,
            'harness': (opp.get('trace') or {}).get('harness') or HARNESS,
            'cohort': cohort,
            'has_opportunity': rec is not None,
            'gate': (rec or {}).get('gate'),
            'features': opportunity_features(rec, cohort) if rec is not None else None,
            'observed': None if obs is None else {
                'post_decision': True, 'action': 'ASK' if obs.get('asked_user') else 'ACT',
                'asked_via_tool': bool(obs.get('asked_via_tool')), 'tool_calls': int(obs.get('tool_calls') or 0)},
            'label': label_of(ver),
            'verifier': (ver or {}).get('verifier', {}).get('version') if ver else None,
            'privacy': 'features_counts_and_hashed_ids_only',
        })
    rows.sort(key=lambda r: (r['day'] or '', r['turn_key']))
    return rows


def _count(values: Iterable[Any]) -> dict[str, int]:
    return {str(k): n for k, n in sorted(Counter('none' if v is None else v for v in values).items())}


def manifest(rows: list[dict[str, Any]], *, sources: Mapping[str, Path], generated_at: float) -> dict[str, Any]:
    def src(p: Path) -> dict[str, Any]:
        try:
            data = p.read_bytes()
            return {'rows': data.count(b'\n'), 'sha256': hashlib.sha256(data).hexdigest()[:16]}
        except OSError:
            return {'rows': 0, 'sha256': None}
    feat = [r for r in rows if r['has_opportunity']]
    return {
        'schema': MANIFEST_SCHEMA, 'row_schema': SCHEMA, 'table_version': TABLE_VERSION,
        'feature_schema_sha': FEATURE_SCHEMA_SHA, 'features': list(FEATURES),
        'label': {'y_success': '1 = verified_success; 0 = verified_failure or contested; null = unverified (missing, not negative)'},
        'post_decision_columns': ['observed'],
        'generated_at': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime(generated_at)),
        'sources': {k: src(p) for k, p in sources.items()},
        'counts': {
            'rows': len(rows), 'with_features': len(feat),
            'with_label_row': sum(1 for r in feat if r['label']['present']),
            'resolved': sum(1 for r in feat if r['label']['resolved']),
            'states': _count(r['label']['state'] for r in feat),
            'groups': len({r['group'] for r in feat}),
            'groups_with_resolved': len({r['group'] for r in feat if r['label']['resolved']}),
            'gate': _count(r['gate'] for r in feat),
            'observed_action': _count((r['observed'] or {}).get('action') for r in feat),
            'cohort': _count(r['cohort'] for r in feat),
            'days': _count(r['day'] for r in feat),
        },
        'privacy': 'no prompt/response/command/path/claim text; ids are sha256 prefixes',
    }


def sweep_counts(verified_rows: Iterable[Mapping[str, Any]], state: Path, *, since_spec: str,
                 now: float, cohort_fn=None) -> dict[str, Any]:
    """Counts-only volume measurement over transcript turns (for the data-volume projection).

    ``verified_rows`` come from ``outcome_verifier.verify(all_turns=True)`` in memory; nothing is
    appended. Per UTC day: turns, verification states, and whether each turn also has an
    opportunity record / an observed row (the feature coverage of the live hooks).
    """
    opp_keys = {(r.get('session_id'), ((r.get('opportunity') or {}).get('trace') or {}).get('trace_id'))
                for r in _read_jsonl(state / 'opportunities.jsonl') if r.get('schema') == OPP_SCHEMA}
    obs_keys = {(r.get('session_id'), r.get('trace_id')) for r in _read_jsonl(state / 'outcomes.jsonl')
                if r.get('schema') == OBSERVED_SCHEMA}
    first_opp = min((((r.get('opportunity') or {}).get('provenance') or {}).get('built_at') or '~')
                    for r in _read_jsonl(state / 'opportunities.jsonl')) if opp_keys else None
    cohort_fn = cohort_fn or (lambda sid: 'unknown')
    days: dict[str, Counter] = {}
    cohorts: dict[str, Counter] = {}
    seen_cohort: dict[str, str] = {}
    total: Counter = Counter()
    stamps = []
    for r in verified_rows:
        if (r.get('turn') or {}).get('harness_message'):
            continue
        key = (r.get('session_id'), r.get('trace_id'))
        started = (r.get('turn') or {}).get('started_at') or ''
        if started:
            stamps.append(started)
        day = started[:10] or 'unknown'
        sid = r.get('session_id')
        if sid not in seen_cohort:
            seen_cohort[sid] = cohort_fn(sid)
        c = days.setdefault(day, Counter())
        after = bool(first_opp) and started >= first_opp
        for cc in (c, total, cohorts.setdefault(seen_cohort[sid], Counter())):
            cc['turns_after_first_opportunity'] += after
            cc['with_opportunity_after_first_opportunity'] += after and key in opp_keys
            cc['turns'] += 1
            cc[r.get('verification_state') or 'unverified'] += 1
            cc['with_opportunity'] += key in opp_keys
            cc['with_observed'] += key in obs_keys
            cc['with_opportunity_and_resolved'] += key in opp_keys and r.get('verification_state') != 'unverified'
    return {'since': since_spec, 'measured_at': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime(now)),
            'first_opportunity_at': first_opp, 'first_turn_at': min(stamps) if stamps else None,
            'last_turn_at': max(stamps) if stamps else None, 'total': dict(total),
            'sessions': len(seen_cohort),
            'sessions_by_cohort': dict(Counter(seen_cohort.values())),
            'by_cohort': {k: dict(c) for k, c in sorted(cohorts.items())},
            'by_day': {d: dict(c) for d, c in sorted(days.items())}, 'privacy': 'counts only'}


FORBIDDEN_KEYS = ('request', 'prompt', 'text', 'cwd', 'path', 'value', 'session_id', 'trace_id', 'locator')


def assert_private(rows: Iterable[Mapping[str, Any]]) -> None:
    """Fail closed if any row carries a text-bearing key."""
    def walk(o: Any, where: str) -> None:
        if isinstance(o, Mapping):
            for k, v in o.items():
                if k in FORBIDDEN_KEYS:
                    raise ValueError(f'private key {k!r} at {where}')
                walk(v, f'{where}.{k}')
        elif isinstance(o, list):
            for v in o:
                walk(v, where)
    for r in rows:
        walk(r, r.get('turn_key', '?'))


def export(out: Path, *, state: Path | None = None, projects: Path | None = None,
           include_unjoined: bool = False, sweep_since: str | None = None, gh: bool = True) -> dict[str, Any]:
    from .outcome_verifier import state_dir
    state = state or state_dir()
    now = time.time()
    rows = build_table(state, projects=projects, include_unjoined=include_unjoined)
    assert_private(rows)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(''.join(json.dumps(r, sort_keys=True) + '\n' for r in rows))
    man = manifest(rows, sources={n: state / n for n in ('opportunities.jsonl', 'outcomes.jsonl',
                                                         'outcomes_verified.jsonl')}, generated_at=now)
    if sweep_since:
        from .outcome_verifier import GitHub, parse_since, verify
        swept = verify(root=state.parent.parent,  # state = <root>/state/claude-code
                       since=parse_since(sweep_since, now), all_turns=True, gh=GitHub(gh), projects=projects, now=now)
        man['sweep'] = sweep_counts(swept, state, since_spec=sweep_since, now=now,
                                    cohort_fn=lambda sid: transcript_cohort(sid, projects))
    out.with_suffix('.manifest.json').write_text(json.dumps(man, indent=1, sort_keys=True) + '\n')
    return man


def _main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog='z0int outcomes export', description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--out', type=Path, required=True, help='training table JSONL (manifest written beside it)')
    ap.add_argument('--state-dir', type=Path, default=None)
    ap.add_argument('--projects-dir', type=Path, default=None)
    ap.add_argument('--include-unjoined', action='store_true',
                    help='also emit verified turns without an opportunity record (features=null; volume only)')
    ap.add_argument('--sweep-since', default=None,
                    help="also verify every transcript turn since this ('7d') in memory and add counts-only "
                         "volume (manifest.sweep); nothing is appended")
    ap.add_argument('--no-gh', action='store_true', help='sweep without read-only GitHub lookups')
    ap.add_argument('--json', action='store_true')
    args = ap.parse_args(argv)
    man = export(args.out, state=args.state_dir, projects=args.projects_dir, include_unjoined=args.include_unjoined,
                 sweep_since=args.sweep_since, gh=not args.no_gh)
    c = man['counts']
    print(json.dumps(c, indent=1) if args.json else
          f"rows={c['rows']} with_features={c['with_features']} resolved={c['resolved']} "
          f"groups={c['groups']} states={c['states']} -> {args.out}")
    return 0


if __name__ == '__main__':
    raise SystemExit(_main())
