"""Offline shadow slot (z0int#56 M2, minimal): registered challengers decide counterfactually on recorded opportunities.

The slot never runs on a hook: ``z0int loop tick`` replays every opportunity_record.v0 that has no decision yet, so it
costs the hot path nothing, and what a challenger answers reaches no model, user or tool. Each decision is appended
to ``state/<harness>/shadow_decisions.jsonl`` as ``z0int.<harness>.shadow_decision.v0`` (opportunity_id, turn_key,
challenger id + sha256, decision, distribution, latency_ms, status; ``y`` null) and nowhere else.

The challenger registry (``$Z0INT_HOME/loop/challengers.json``, default ``default_registry()``) is hash-pinned: a
built-in challenger's sha256 is the sha256 of its source, a learned gate's the sha256 of its artifact file, a backend's
the sha256 of its spec. The simple controls (deterministic_gate = the champion, always_escalate, base_rate) are always
in it. Model backends are off by default; an enabled backend is asked only when it reports itself served, at most once
per tick (``backend_unavailable`` otherwise, no retries), and the slot never starts or loads one.
"""

from __future__ import annotations

import hashlib
import inspect
import json
import math
import time
from pathlib import Path
from typing import Any, Callable, Mapping

from . import harness_capture as hc
from . import loop_export as le
from .decision_opportunity import ACTIONS, deterministic_gate

REGISTRY_SCHEMA = 'z0int.loop.challenger_registry.v0'
CONTROLS = ('deterministic_gate', 'always_escalate', 'base_rate')
FALLBACK = ('ASK', 'OBSERVE', 'ABSTAIN')  # evolution-lab verified-loop v0: what a gate does when it does not ACT


def _legal(opp: Mapping[str, Any]) -> set[str]:
    return {a['kind'] for a in opp.get('action_space') or [] if a.get('legal')}


def _fallback(legal: set[str]) -> str:
    return next((a for a in FALLBACK if a in legal), 'ABSTAIN')


def _onehot(action: str) -> dict[str, float]:
    return {action: 1.0}


# ----------------------------------------------------------------------------- built-in challengers
def deterministic_gate_challenger(rec: Mapping[str, Any], ctx: Mapping[str, Any]) -> tuple[str, dict[str, float]]:
    """The champion: the #55 deterministic gate (first legal action by fixed priority)."""
    action = deterministic_gate(rec['opportunity'])
    return action, _onehot(action)


def always_escalate(rec: Mapping[str, Any], ctx: Mapping[str, Any]) -> tuple[str, dict[str, float]]:
    """Control: always ESCALATE, legal or not (a floor no learned gate may lose to)."""
    return 'ESCALATE', _onehot('ESCALATE')


def base_rate(rec: Mapping[str, Any], ctx: Mapping[str, Any]) -> tuple[str, dict[str, float]]:
    """Control: ACT with the harness's verified success rate, when ACT is legal; otherwise the fallback."""
    legal, p = _legal(rec['opportunity']), ctx['base_rate']
    fb = _fallback(legal)
    q = p if p is not None and 'ACT' in legal else 0.0
    return ('ACT' if q >= 0.5 else fb), {'ACT': q, fb: 1.0 - q}


def routine_shadow(rec: Mapping[str, Any], ctx: Mapping[str, Any]) -> tuple[str | None, dict[str, float]]:
    """RoutineRegistry.decide_shadow over the prompt-time features (capability = harness); None: no routine matched."""
    decision = ctx['routines'].decide_shadow(rec['harness'], ctx['features'])
    if not decision.matched or decision.output not in ACTIONS:
        return None, {}
    return decision.output, _onehot(decision.output)


BUILTINS: dict[str, Callable[..., tuple[str | None, dict[str, float]]]] = {
    'deterministic_gate': deterministic_gate_challenger, 'always_escalate': always_escalate, 'base_rate': base_rate,
    'routine_shadow': routine_shadow}


def _sha_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def builtin_sha(cid: str) -> str:
    return _sha_bytes(inspect.getsource(BUILTINS[cid]).encode())


def backend_challenger(backend: str, *, enabled: bool = False) -> dict[str, Any]:
    spec = {'kind': 'backend', 'backend': backend}
    return {'id': f'backend:{backend}', **spec, 'enabled': enabled,
            'sha256': _sha_bytes(json.dumps(spec, sort_keys=True).encode())}


def learned_gate_challenger(cid: str, artifact: Path) -> dict[str, Any]:
    """Register an evolution-lab learned gate by the sha256 of its artifact (logistic weights + tau, JSON)."""
    return {'id': cid, 'kind': 'el_learned_gate', 'path': str(artifact),
            'sha256': _sha_bytes(Path(artifact).read_bytes())}


def default_registry() -> dict[str, Any]:
    return {'schema': REGISTRY_SCHEMA, 'champion': 'deterministic_gate',
            'challengers': [{'id': cid, 'kind': 'builtin', 'sha256': builtin_sha(cid)} for cid in BUILTINS]}


def validate_registry(reg: Mapping[str, Any]) -> None:
    """The simple controls are always present, and every built-in is pinned to the code that runs."""
    ids = [c.get('id') for c in reg.get('challengers') or []]
    missing = [c for c in CONTROLS if c not in ids]
    if missing:
        raise ValueError(f'challenger registry lacks the simple control(s) {missing}')
    if len(ids) != len(set(ids)):
        raise ValueError('challenger registry has duplicate ids')
    for c in reg['challengers']:
        if c.get('kind') == 'builtin' and (c['id'] not in BUILTINS or c.get('sha256') != builtin_sha(c['id'])):
            raise ValueError(f'challenger {c["id"]!r}: sha256 does not match the code that would run')


def load_registry(root: str | Path | None = None) -> dict[str, Any]:
    path = Path(root or hc.home()) / 'loop' / 'challengers.json'
    reg = json.loads(path.read_text()) if path.exists() else default_registry()
    validate_registry(reg)
    return reg


def _learned_gate(c: Mapping[str, Any]) -> Callable[..., tuple[str, dict[str, float]]] | None:
    """The artifact's decide function, or None when the file is not the one registered (hash_mismatch)."""
    try:
        data = Path(c['path']).read_bytes()
    except OSError:
        return None
    if _sha_bytes(data) != c.get('sha256'):
        return None
    m = json.loads(data)

    def decide(rec: Mapping[str, Any], ctx: Mapping[str, Any]) -> tuple[str, dict[str, float]]:
        z = m['b'] + sum(w * ((ctx['features'].get(n, 0) - mu) / sd if sd else 0.0)
                         for n, mu, sd, w in zip(m['feature_names'], m['mu'], m['sd'], m['w']))
        p = 1.0 / (1.0 + math.exp(-max(-60.0, min(60.0, z))))
        legal = _legal(rec['opportunity'])
        fb = _fallback(legal)
        q = p if 'ACT' in legal else 0.0  # a learned gate can withhold ACT, never grant it
        return ('ACT' if q >= m['tau'] and 'ACT' in legal else fb), {'ACT': q, fb: 1.0 - q}
    return decide


def _backend_decide(backend: Any) -> Callable[..., tuple[str, dict[str, float]]]:
    from .backends.base import request_from_mapping

    def decide(rec: Mapping[str, Any], ctx: Mapping[str, Any]) -> tuple[str, dict[str, float]]:
        legal = sorted(_legal(rec['opportunity']), key=ACTIONS.index) or ['ABSTAIN']
        if len(legal) == 1:  # nothing to choose: the backend is not asked
            return legal[0], _onehot(legal[0])
        # content-free state: the prompt-time feature counts only, never request or packet text
        answer = backend.evaluate(request_from_mapping({
            'state': {k: v for k, v in ctx['features'].items() if v},
            'questions': [{'id': 'action', 'type': 'choice', 'options': [{'id': a, 'description': a} for a in legal],
                           'instructions': 'Pick the next action for this decision opportunity.'}]})).answers[0]
        return str(answer.value), dict(answer.probabilities)
    return decide


def _base_rates(root: str | Path | None) -> dict[str, float | None]:
    out = {}
    for h in hc.HARNESSES:
        latest = {}
        for r in hc._read_jsonl(hc.state_dir(h, root) / 'outcomes_verified.jsonl'):
            latest[(r.get('session_id'), r.get('trace_id'))] = le.label_of(r)['y_success']
        ys = [y for y in latest.values() if y is not None]
        out[h] = sum(ys) / len(ys) if ys else None
    return out


def _cohort_fn(root: str | Path | None, harness: str, projects: Path | None) -> Callable[[Mapping[str, Any]], str]:
    """The cohort the turn's training row gets (loop_export.build_table): the capture-time cohort when it is not the
    session's, else the verified row's (the turn's, then its session's), else the transcript's (Claude Code)."""
    by_turn, by_session = {}, {}
    for r in hc._read_jsonl(hc.state_dir(harness, root) / 'outcomes_verified.jsonl'):
        if r.get('cohort') in le.TABLE_COHORTS:
            by_turn[(r.get('session_id'), r.get('trace_id'))] = by_session[r.get('session_id')] = r['cohort']
    sessions: dict[Any, str] = {}

    def cohort(rec: Mapping[str, Any]) -> str:
        sid = rec.get('session_id')
        tid = ((rec.get('opportunity') or {}).get('trace') or {}).get('trace_id')
        if harness == le.HARNESS and sid not in by_session and sid not in sessions:
            sessions[sid] = le.transcript_cohort(sid, projects)
        return le.capture_cohort(rec) or by_turn.get((sid, tid)) or by_session.get(sid) or sessions.get(sid, 'unknown')
    return cohort


def replay(root: str | Path | None = None, registry: Mapping[str, Any] | None = None, *,
           backend_factory: Callable[[str], Any] | None = None, deadline: float | None = None,
           projects: Path | None = None) -> dict[str, Any]:
    """Decide every registered challenger on every recorded opportunity it has not decided yet.

    ``deadline`` (time.monotonic) stops the replay between opportunities; the rest is decided on the next tick.
    """
    from .routines import RoutineRegistry
    reg = load_registry(root) if registry is None else registry
    validate_registry(reg)
    rates = _base_rates(root)
    routines = RoutineRegistry.from_jsonl(Path(root or hc.home()) / 'loop' / 'routines.jsonl')
    status: dict[str, str] = {}
    unavailable: dict[str, int] = {}
    deciders: list[tuple[Mapping[str, Any], Callable[..., Any]]] = []
    for c in reg['challengers']:
        if c.get('kind') == 'builtin':
            deciders.append((c, BUILTINS[c['id']]))
        elif c.get('kind') == 'el_learned_gate':
            fn = _learned_gate(c)
            if fn is None:
                status[c['id']] = 'hash_mismatch'
                continue
            deciders.append((c, fn))
        elif c.get('kind') == 'backend':
            if c.get('enabled') is not True:
                status[c['id']] = 'disabled'
                continue
            if backend_factory is None:
                from .backends.registry import create_backend as backend_factory
            try:  # the one probe this tick: a dead backend is counted, never retried
                backend = backend_factory(c['backend'])
                served = bool(backend.health(load=False).ready)
            except Exception:
                served = False
            if not served:
                status[c['id']] = 'backend_unavailable'
                unavailable[c['backend']] = 1
                continue
            deciders.append((c, _backend_decide(backend)))
        else:
            status[c.get('id', '?')] = 'unknown_kind'
    added: dict[str, int] = {}
    partial = False
    for h in hc.HARNESSES:
        out_path = hc.state_dir(h, root) / 'shadow_decisions.jsonl'
        records = [r for r in hc._read_jsonl(hc.state_dir(h, root) / 'opportunities.jsonl')
                   if r.get('schema') == hc.schema(h, 'opportunity_record') and isinstance(r.get('opportunity'), dict)]
        if not records:
            continue
        done = {r.get('decision_id') for r in hc._read_jsonl(out_path)}
        cohort_of = _cohort_fn(root, h, projects)
        rows = []
        for rec in records:
            if deadline is not None and time.monotonic() >= deadline:
                partial = True
                break
            opp = rec['opportunity']
            trace = opp.get('trace') or {}
            sid, tid = rec.get('session_id'), trace.get('trace_id')
            cohort = cohort_of(rec)
            ctx = {'base_rate': rates[h], 'routines': routines, 'features': le.opportunity_features(rec, cohort)}
            rec = dict(rec, harness=h)
            for c, fn in deciders:
                decision_id = le._sha({'opportunity': trace.get('opportunity_id'), 'challenger': c['sha256']}, 20)
                if decision_id in done:
                    continue
                t0 = time.perf_counter()
                try:
                    action, dist = fn(rec, ctx)
                    st = 'ok' if action is not None else 'no_match'
                except Exception as exc:  # a failing challenger is a counted status, never a tick failure
                    action, dist, st = None, {}, f'error:{type(exc).__name__}'
                rows.append({'schema': hc.schema(h, 'shadow_decision'), 'harness': h, 'cohort': cohort,
                             'source': 'shadow_slot', 'decision_id': decision_id,
                             'opportunity_id': trace.get('opportunity_id'),
                             'turn_key': rec.get('turn_key') or hc.turn_key(h, sid, tid),
                             'group': le._sha({'session': sid}, 12), 'day': (rec.get('recorded_at') or '')[:10] or None,
                             'challenger': {'id': c['id'], 'kind': c['kind'], 'sha256': c['sha256']},
                             'decision': action, 'distribution': dist,
                             'latency_ms': round((time.perf_counter() - t0) * 1000, 3), 'status': st,
                             'y': None, 'label': None, 'privacy': 'hashed_ids_and_closed_vocabulary_only'})
                done.add(decision_id)
        if rows:
            le.assert_private(rows)
            with open(out_path, 'a', encoding='utf-8') as fh:
                fh.write(''.join(json.dumps(r, sort_keys=True) + '\n' for r in rows))
        added[h] = len(rows)
        if partial:
            break
    for c, _ in deciders:
        status.setdefault(c['id'], 'ok')
    return {'decisions_added': sum(added.values()), 'by_harness': added, 'challengers': status,
            'backend_unavailable': unavailable, 'partial': partial}
