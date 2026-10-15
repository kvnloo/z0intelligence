"""Routing episode compiler v0 (z0int#143 first vertical slice; z0evals#92; z0int#144).

ONE compiler, many consumers. It reads the stores that already exist and never writes them:

  receipts/decisions.jsonl + receipts/outcomes.jsonl  decision_receipt.v1 / outcome_join.v1 (route_worker attempts,
                                                     intelligence.dispatch, the OMP v1 spine ``coding.*``)
  state/<harness>/opportunities|outcomes|outcomes_verified.jsonl   DecisionOpportunity records + verified outcomes
  state/<harness>/shadow_decisions.jsonl             challenger / teacher answers (always synthetic)
  memory/events.jsonl                                the canonical EventLog, opened read-only (refs + independent
                                                     work.outcome / work.verification events)

and emits canonical ``z0int.routing_episode.v0`` rows: a derived, rebuildable projection that keeps every source id,
so it is not a second memory or replay authority. Consumers (SFT dataset, CapabilityProfile mining, z0evals replay)
read the same rows.

Each episode keeps the #143 fields: immutable source/event ids + timestamps, the evidence visible at decision time
(hashed features and refs, never raw text), the exact candidate set (with ``exact`` saying whether it was recorded at
decision time or reconstructed), the historical action, the execution receipt, the independently observed outcome,
provenance (observed vs synthetic) and lineage. Request/prompt/output text is read in memory only to derive closed
keyword booleans and content hashes; it is never written.

Invariant: a synthetic (teacher / challenger / shadow) row can never carry an observed outcome. ``validate`` fails
closed on any episode that would let a synthetic label pass as observed truth.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import socket
import time
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Iterator, Mapping

SCHEMA = 'z0int.routing_episode.v0'
COMPILER_VERSION = '0.1.0'
RECEIPT_SCHEMA = 'z0int.episode_dataset_receipt.v0'
FAMILIES = ('worker_route', 'omp_route', 'decision_opportunity', 'shadow')
# reconstructed: features rebuilt from history after the fact (replay_reconstruct); its labels carry their own class
# namespace and it is never observed truth for a live consumer (``is_labelled`` is observed-only).
PROVENANCE = ('observed', 'synthetic', 'reconstructed')
RECONSTRUCTED_LABEL_NS = 'reconstructed/'
PARENT_ONLY = 'PARENT_ONLY'
# Escalation-class actions per family: the expensive / human / frontier choice the utility charges when unnecessary.
ESCALATION = {
    'decision_opportunity': frozenset({'ESCALATE', 'ASK'}),
    'worker_route': frozenset({PARENT_ONLY}),
    'omp_route': frozenset({'frontier/paid', 'escalate'}),
}
# Keys an episode must never carry (text-bearing or raw ids); loop_export's list plus the bodies of EventLog.
FORBIDDEN_KEYS = ('request', 'prompt', 'text', 'cwd', 'path', 'value', 'session_id', 'trace_id', 'locator', 'output',
                  'content', 'subtask', 'message', 'response', 'body', 'reason', 'command', 'transcript')
_ACTION_RE = re.compile(r'^[A-Za-z0-9_.:/@+-]{1,64}$')
_ID_KEYS = ('trace_id', 'task_id', 'control_trace_id', 'verified_trace_id', 'original_task_id',
            'negative_control_trace_id', 'caller_trace_id')


# ----------------------------------------------------------------------------- small helpers
def _h(obj: Any, n: int = 20) -> str:
    raw = obj if isinstance(obj, (bytes, bytearray)) else json.dumps(
        obj, sort_keys=True, separators=(',', ':'), ensure_ascii=False, default=str).encode('utf-8')
    return hashlib.sha256(raw).hexdigest()[:n]


def _norm_text(text: str) -> str:
    """The semantic-dedup normal form: case-folded, digits and whitespace collapsed."""
    return re.sub(r'\s+', ' ', re.sub(r'\d+', '0', text.casefold())).strip()


def content_hashes(text: str | None) -> dict[str, Any]:
    """Exact and normalised content hashes plus a length bucket; the text itself is dropped by the caller."""
    if not isinstance(text, str) or not text:
        return {'content_sha': None, 'content_norm_sha': None, 'len_bucket': None}
    return {'content_sha': _h(text.encode('utf-8'), 32), 'content_norm_sha': _h(_norm_text(text).encode('utf-8'), 32),
            'len_bucket': int(math.log2(len(text) + 1))}


def _iso(ts: float | None) -> str | None:
    return None if ts is None else time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime(ts))


def _ts_of(value: Any) -> float | None:
    """Unix seconds from a float/int or an ISO-8601 string (UTC assumed when no offset)."""
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value) / (1000.0 if value > 1e12 else 1.0)
    if isinstance(value, str) and value:
        s = value.strip().replace('Z', '+00:00')
        from datetime import datetime, timezone
        try:
            d = datetime.fromisoformat(s)
        except ValueError:
            return None
        if d.tzinfo is None:
            d = d.replace(tzinfo=timezone.utc)
        return d.timestamp()
    return None


def _action_id(value: Any) -> str | None:
    """A closed-vocabulary action id as-is; anything longer or free-form becomes a hash id (never text)."""
    if value is None:
        return None
    s = str(value)
    return s if _ACTION_RE.match(s) else 'h:' + _h(s, 16)


@dataclass
class SourceRow:
    store: str          # path relative to the source home (never absolute)
    line_no: int
    line_sha: str
    row: dict[str, Any]


def read_rows(home: Path, rel: str, counts: Counter | None = None) -> Iterator[SourceRow]:
    """Read-only JSONL iteration with a per-line sha256 (the immutable source ref); malformed lines are counted."""
    p = home / rel
    try:
        fh = p.open('rb')
    except OSError:
        return
    with fh:
        for i, raw in enumerate(fh, 1):
            if not raw.strip():
                continue
            try:
                row = json.loads(raw)
            except (json.JSONDecodeError, UnicodeDecodeError):
                if counts is not None:
                    counts[f'malformed:{rel}'] += 1
                continue
            if isinstance(row, dict):
                yield SourceRow(rel, i, hashlib.sha256(raw.rstrip(b'\n')).hexdigest()[:32], row)


def _ref(sr: SourceRow, ident: Any = None, ts: float | None = None) -> dict[str, Any]:
    return {'store': sr.store, 'line_no': sr.line_no, 'line_sha256': sr.line_sha,
            'id_hash': None if ident is None else _h({'id': ident}, 20), 'ts': _iso(ts)}


# ----------------------------------------------------------------------------- keyword features (in memory only)
def _routing_rules() -> list[tuple[str, re.Pattern]]:
    try:
        from .worker_routing import POLICY_PATH
        policy = json.loads(POLICY_PATH.read_text())
        return [(r['category'], re.compile(r['pattern'], re.I)) for r in policy.get('rules') or []]
    except (OSError, ValueError, KeyError, ImportError):
        return []


_RULES: list[tuple[str, re.Pattern]] | None = None


def keyword_features(text: str | None) -> dict[str, int]:
    """Booleans ``kw.<category>`` for each worker_routing rule (the production keyword vocabulary)."""
    global _RULES
    if _RULES is None:
        _RULES = _routing_rules()
    t = text if isinstance(text, str) else ''
    return {f'kw.{cat}': int(bool(rx.search(t))) for cat, rx in _RULES}


# ----------------------------------------------------------------------------- the episode
@dataclass
class Draft:
    """An episode under construction; ``ids`` are raw join keys kept in memory only (never serialised)."""
    family: str
    source: str
    decided_ts: float | None
    source_refs: list[dict[str, Any]]
    evidence: dict[str, Any]
    candidates: dict[str, Any]
    action: dict[str, Any]
    receipt: dict[str, Any]
    outcome: dict[str, Any]
    provenance: dict[str, Any]
    lineage: dict[str, Any]
    policy_replays: dict[str, Any] = field(default_factory=dict)
    event_refs: list[dict[str, Any]] = field(default_factory=list)
    ids: set = field(default_factory=set)
    key: str = ''       # immutable source identity (hashed) -> episode_id

    @property
    def episode_id(self) -> str:
        return _h({'family': self.family, 'source': self.source, 'key': self.key}, 24)

    def finish(self, host: str) -> dict[str, Any]:
        ep = {
            'schema': SCHEMA, 'compiler_version': COMPILER_VERSION,
            'episode_id': self.episode_id,
            'family': self.family, 'source': self.source, 'host': host,
            'decided_at': _iso(self.decided_ts), 'day': (_iso(self.decided_ts) or '')[:10] or None,
            'source_refs': self.source_refs, 'event_refs': sorted(self.event_refs, key=lambda r: r['event_id']),
            'evidence': self.evidence, 'candidates': self.candidates, 'action': self.action, 'receipt': self.receipt,
            'outcome': self.outcome, 'provenance': self.provenance, 'lineage': self.lineage,
            'policy_replays': self.policy_replays,
        }
        ep['hashes'] = episode_hashes(ep)
        return ep


def no_outcome(reason: str) -> dict[str, Any]:
    return {'observed': False, 'y_success': None, 'state': 'unobserved', 'independent': False, 'source': None,
            'why_missing': reason}


def episode_hashes(ep: Mapping[str, Any]) -> dict[str, str]:
    """exact: the whole decision record minus where/when it was read.

    semantic: the same content identity in the same candidate set with the same choice and provenance. The content
    identity is the adapter's ``semantic_key`` (normalised full text, a full-content sha, a DecisionOpportunity
    semantic id, or for a teacher row its parent + producer). Without one there is no honest near-duplicate notion,
    so the semantic hash falls back to the exact hash (``semantic_basis: exact_fallback``)."""
    exact = _h({k: ep.get(k) for k in ('family', 'decided_at', 'evidence', 'candidates', 'action', 'receipt',
                                       'outcome', 'provenance')}, 32)
    key = (ep.get('evidence') or {}).get('semantic_key')
    if not key:
        return {'exact': exact, 'semantic': exact, 'semantic_basis': 'exact_fallback'}
    sem = _h({'family': ep.get('family'), 'content': key, 'candidates': (ep.get('candidates') or {}).get('set'),
              'action': (ep.get('action') or {}).get('chosen'),
              'provenance': (ep.get('provenance') or {}).get('kind')}, 32)
    return {'exact': exact, 'semantic': sem, 'semantic_basis': 'content'}


class EpisodeInvalid(ValueError):
    pass


def validate(ep: Mapping[str, Any]) -> None:
    """Fail closed on schema, privacy and provenance violations."""
    if ep.get('schema') != SCHEMA or ep.get('family') not in FAMILIES:
        raise EpisodeInvalid(f"bad schema/family {ep.get('schema')}/{ep.get('family')}")
    prov = ep.get('provenance') or {}
    if prov.get('kind') not in PROVENANCE:
        raise EpisodeInvalid('provenance.kind must be observed|synthetic|reconstructed')
    out = ep.get('outcome') or {}
    if prov['kind'] == 'reconstructed' and out.get('observed') and \
            not str(out.get('label_class') or '').startswith(RECONSTRUCTED_LABEL_NS):
        raise EpisodeInvalid('reconstructed outcome outside its own label class namespace: never pooled with observed')
    if prov['kind'] != 'reconstructed' and str(out.get('label_class') or '').startswith(RECONSTRUCTED_LABEL_NS):
        raise EpisodeInvalid('reconstructed label class on a non-reconstructed episode')
    if prov['kind'] == 'synthetic' and (out.get('observed') or out.get('y_success') is not None):
        raise EpisodeInvalid('synthetic episode carries an observed outcome: a teacher label never becomes truth')
    if out.get('observed') and not out.get('independent'):
        raise EpisodeInvalid('observed outcome must come from an independent verifier/join')
    if out.get('y_success') not in (None, 0, 1) or (out.get('y_success') is None) == bool(out.get('observed')):
        raise EpisodeInvalid('outcome.observed must be true exactly when y_success is 0/1')
    if not ep.get('source_refs'):
        raise EpisodeInvalid('episode without an immutable source ref')
    cands = (ep.get('candidates') or {}).get('set') or []
    chosen = (ep.get('action') or {}).get('chosen')
    if chosen is not None and cands and chosen not in cands:
        raise EpisodeInvalid('historical action outside its own candidate set')
    _assert_private(ep)


def _assert_private(o: Any, where: str = '') -> None:
    if isinstance(o, Mapping):
        for k, v in o.items():
            if k in FORBIDDEN_KEYS:
                raise EpisodeInvalid(f'private key {k!r} at {where or "<root>"}')
            _assert_private(v, f'{where}.{k}')
    elif isinstance(o, list):
        for v in o:
            _assert_private(v, where)


# ----------------------------------------------------------------------------- EventLog (read-only)
@dataclass
class EventIndex:
    by_id: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    outcomes: dict[str, dict[str, Any]] = field(default_factory=dict)
    counts: Counter = field(default_factory=Counter)


_CLASS_Y = {'verified_success': 1, 'success': 1, 'pass': 1, 'passed': 1,
            'verified_failure': 0, 'failure': 0, 'fail': 0, 'failed': 0}


def event_outcome(ev_type: str, payload: Mapping[str, Any]) -> int | None:
    """y from an independent work.verification / work.outcome event; None unless the signal is explicit."""
    if ev_type not in ('work.verification', 'work.outcome'):
        return None
    if isinstance(payload.get('tests_passed'), bool):
        return int(payload['tests_passed'])
    cls = str(payload.get('classification') or '').lower()
    if cls in _CLASS_Y:
        return _CLASS_Y[cls]
    return None


def index_eventlog(home: Path) -> EventIndex:
    from .memory.event_log import EventLog, EventLogCorruption
    idx = EventIndex()
    root = home / 'memory'
    if not (root / 'events.jsonl').is_file():
        idx.counts['missing'] = 1
        return idx
    log = EventLog(root, read_only=True)
    try:
        for ev in log.iter_events():
            idx.counts['events'] += 1
            idx.counts[f'type:{ev.event_type}'] += 1
            payload = ev.payload if isinstance(ev.payload, Mapping) else {}
            ref = {'event_id': ev.event_id, 'event_type': ev.event_type, 'ts': _iso(ev.ts),
                   'event_uid': (ev.identity or {}).get('event_uid')}
            for k in _ID_KEYS:
                v = payload.get(k)
                if isinstance(v, str) and v:
                    idx.by_id.setdefault(v, []).append(ref)
            y = event_outcome(ev.event_type, payload)
            if y is not None:
                for k in ('trace_id', 'original_task_id', 'verified_trace_id', 'control_trace_id'):
                    v = payload.get(k)
                    if isinstance(v, str) and v:
                        idx.outcomes[v] = {'y': y, 'event_id': ev.event_id, 'event_type': ev.event_type}
    except EventLogCorruption as exc:
        idx.counts[f'corrupt:{type(exc).__name__}'] += 1
    return idx


def attach_events(d: Draft, idx: EventIndex) -> None:
    seen = {r['event_id'] for r in d.event_refs}
    for i in d.ids:
        for ref in idx.by_id.get(i, ()):
            if ref['event_id'] not in seen:
                d.event_refs.append(ref)
                seen.add(ref['event_id'])
    if d.provenance['kind'] != 'observed' or d.outcome.get('observed'):
        return
    for i in sorted(d.ids):
        hit = idx.outcomes.get(i)
        if hit:
            d.outcome = {'observed': True, 'y_success': hit['y'],
                         'state': 'verified_success' if hit['y'] else 'verified_failure', 'independent': True,
                         'source': f"eventlog.{hit['event_type']}", 'label_ref': {'event_id': hit['event_id']}}
            return


# ----------------------------------------------------------------------------- receipts: shared loaders
def _outcome_joins(home: Path, counts: Counter) -> dict[str, dict[str, Any]]:
    """trace_id -> latest outcome_join.v1 row (append-only: the latest row wins)."""
    out: dict[str, dict[str, Any]] = {}
    for sr in read_rows(home, 'receipts/outcomes.jsonl', counts):
        r = sr.row
        if r.get('schema') == 'z0int.outcome_join.v1' and r.get('trace_id'):
            out[r['trace_id']] = dict(r, _ref=_ref(sr, r['trace_id'], _ts_of(r.get('ts'))))
    return out


def _tier_outcome(outcome: Any, stored: Any, ref: dict[str, Any] | None) -> dict[str, Any]:
    """The receipt effective tier (an ambient close never mints gold): gold -> 1, negative -> 0, else unobserved."""
    from .receipt import effective_tier
    tier = effective_tier(outcome if isinstance(outcome, dict) else None,
                          stored_tier=stored if isinstance(stored, str) else None)
    if tier == 'gold':
        y = 1
    elif tier == 'negative':
        y = 0
    else:
        return dict(no_outcome(f'tier_{tier}'), tier=tier)
    return {'observed': True, 'y_success': y, 'state': 'verified_success' if y else 'verified_failure',
            'independent': True, 'source': 'outcome_join.effective_tier', 'tier': tier, 'label_ref': ref}


def _num(v: Any) -> float | None:
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else None


# ----------------------------------------------------------------------------- adapter: route_worker / dispatch
def worker_route_drafts(home: Path, counts: Counter, joins: Mapping[str, Mapping[str, Any]]) -> list[Draft]:
    """One episode per route_worker plan (a subagent route), or per PARENT_ONLY dispatch.

    The candidate set is the plan receipt's ``candidates`` + ``skipped`` providers + PARENT_ONLY (exact: recorded at
    decision time). A PARENT_ONLY native route records no provider list: its set is the policy registry's providers
    (``exact: false``). The historical action is the first provider the plan tried, else PARENT_ONLY.
    """
    attempts: dict[str, list[tuple[SourceRow, dict]]] = {}
    dispatch: dict[str, tuple[SourceRow, dict]] = {}
    for sr in read_rows(home, 'receipts/decisions.jsonl', counts):
        r = sr.row
        cap, e = r.get('capability_id'), r.get('extra') or {}
        if cap == 'codex.delegated_text' and e.get('subagent_id'):
            attempts.setdefault(e['subagent_id'], []).append((sr, r))
        elif cap == 'intelligence.dispatch' and e.get('status') in ('completed', 'delivered_to_hook') \
                and isinstance(e.get('result'), dict):
            dispatch[r.get('trace_id')] = (sr, r)  # latest row per dispatch id wins
    drafts = []
    by_sub = {}
    for tid, (sr, r) in dispatch.items():
        res = r['extra']['result']
        if res.get('subagent_id'):
            by_sub[res['subagent_id']] = (sr, r)
        elif (res.get('route') or {}).get('kind') == PARENT_ONLY:
            drafts.append(_parent_only_draft(sr, r))
        else:
            counts[f"worker_route:dispatch_not_a_route:{_action_id(r['extra'].get('function'))}"] += 1
    policy, providers = production_policy(home)
    for sub, rows in attempts.items():
        rows.sort(key=lambda x: (x[1]['extra'].get('attempt_index') or 0, x[1].get('ts') or 0))
        latest: dict[int, dict] = {}
        refs = []
        for sr, r in rows:
            latest[r['extra'].get('attempt_index') or 0] = r
            refs.append(_ref(sr, r.get('trace_id'), _ts_of(r.get('ts'))))
        first = rows[0][1]
        e0 = first['extra']
        disp = by_sub.get(sub)
        plan = ((disp[1]['extra']['result'].get('route') if disp else None) or {})
        cands_rec = [c.get('provider') for c in plan.get('candidates') or [] if isinstance(c, dict)]
        skipped = [s.get('provider') for s in plan.get('skipped') or [] if isinstance(s, dict)]
        tried = [latest[i].get('provider') for i in sorted(latest)]
        cset = sorted({p for p in cands_rec + skipped + tried if p} | {PARENT_ONLY})
        exact = bool(plan)
        text = e0.get('subtask') if isinstance(e0.get('subtask'), str) else None  # 120-char prefix, memory only
        hashes = content_hashes(text)
        final = latest[max(latest)]
        fe = final['extra']
        ok = fe.get('status') == 'completed'
        lat = sum(_num(r.get('latency_ms')) or 0.0 for r in latest.values())
        it = sum(int(r.get('input_tokens') or 0) for r in latest.values())
        ot = sum(int(r.get('output_tokens') or 0) for r in latest.values())
        posture = (e0.get('resource_posture') or {}) if isinstance(e0.get('resource_posture'), dict) else {}
        features = {'category': _action_id(e0.get('category')), 'harness': _action_id(e0.get('harness')),
                    'function': _action_id((disp[1]['extra'].get('function') if disp else None)),
                    'route_source': _action_id(e0.get('route_source')), 'free_only': bool(e0.get('free_only')),
                    'posture': _action_id(posture.get('posture') or posture.get('factory')),
                    'len_bucket': hashes['len_bucket'], **keyword_features(text)}
        d = Draft(
            family='worker_route', source='receipts.route_worker', decided_ts=_ts_of(first.get('ts')),
            source_refs=refs + ([_ref(disp[0], disp[1].get('trace_id'), _ts_of(disp[1].get('ts')))] if disp else []),
            evidence={'kind': 'hashed_features', 'features': features,
                      'request_sha256': e0.get('task_sha256'), 'context_sha256': e0.get('context_sha256'),
                      'content_sha': hashes['content_sha'], 'content_norm_sha': hashes['content_norm_sha'],
                      'content_scope': 'subtask_prefix_120' if text else None, 'state_ref': None,
                      'semantic_key': _h({'task': e0.get('task_sha256'), 'context': e0.get('context_sha256')}, 32)
                      if e0.get('task_sha256') else None},
            candidates={'set': cset, 'source': 'plan_receipt' if exact else 'attempt_receipts', 'exact': exact,
                        'legal': sorted({p for p in cands_rec + tried if p} | {PARENT_ONLY}),
                        'skipped': sorted({p for p in skipped if p})},
            action={'chosen': _action_id(tried[0]) if tried else PARENT_ONLY, 'decided_by': plan.get('source')
                    or e0.get('route_source'), 'fallbacks': len(latest) - 1,
                    'model': _action_id(first.get('model'))},
            receipt={'execution_completed': ok, 'status': _action_id(fe.get('status')), 'attempts': len(latest),
                     'latency_ms': round(lat, 3), 'input_tokens': it, 'output_tokens': ot,
                     'cost_usd': _num(fe.get('provider_reported_cost_usd')) or _num(fe.get('validated_price_usd')),
                     'physical_call_attempted': bool(fe.get('physical_call_attempted')),
                     'output_sha256': fe.get('output_sha256')},
            outcome=no_outcome('execution_is_not_verification'),
            provenance={'kind': 'observed', 'producer': 'z0int.worker_routing'},
            lineage={'group': _h({'session': first.get('session_id') or ''}, 12),
                     'policy_revision': e0.get('policy_revision'), 'policy_sha256': e0.get('policy_sha256'),
                     'provider': _action_id(final.get('provider')), 'model': _action_id(final.get('model')),
                     'parent_episode_ids': [], 'subagent_hash': _h({'sub': sub}, 16)},
            key=f'subagent:{sub}')
        # an independent join on any attempt trace id (outcome_join.v1 effective tier)
        for r in latest.values():
            j = joins.get(r.get('trace_id'))
            if j:
                oc = _tier_outcome(j.get('outcome'), j.get('outcome_tier'), j.get('_ref'))
                if oc['observed']:
                    d.outcome = oc
                    break
        d.policy_replays = _replay_plan_route(text, plan, cset, policy, providers, e0)
        d.ids |= {r.get('trace_id') for r in latest.values()} | {e0.get('caller_trace_id'), sub}
        if disp:
            d.ids |= {disp[1].get('trace_id')}
        d.ids.discard(None)
        drafts.append(d)
    counts['worker_route:dispatch_rows'] = len(dispatch)
    return drafts


def _parent_only_draft(sr: SourceRow, r: Mapping[str, Any]) -> Draft:
    e = r['extra']
    res = e['result']
    try:
        from .worker_routing import POLICY_PATH
        providers = sorted(json.loads(POLICY_PATH.read_text()).get('providers') or {})
    except (OSError, ValueError, ImportError):
        providers = []
    return Draft(
        family='worker_route', source='receipts.dispatch_parent_only', decided_ts=_ts_of(r.get('ts')),
        source_refs=[_ref(sr, r.get('trace_id'), _ts_of(r.get('ts')))],
        evidence={'kind': 'hashed_features', 'features': {
            'function': _action_id(e.get('function')), 'harness': _action_id(e.get('harness')),
            'automatic': bool(e.get('automatic')), 'chosen_kind': _action_id(e.get('chosen_kind'))},
            'request_sha256': e.get('request_sha256'), 'content_sha': None, 'content_norm_sha': None,
            'registry_sha256': (res.get('route') or {}).get('registry_sha256'), 'state_ref': None,
            'semantic_key': e.get('request_sha256')},
        candidates={'set': sorted(set(providers) | {PARENT_ONLY}), 'source': 'policy_registry', 'exact': False,
                    'legal': sorted(set(providers) | {PARENT_ONLY}), 'skipped': []},
        action={'chosen': PARENT_ONLY, 'decided_by': 'dispatch.native', 'fallbacks': 0, 'model': None},
        receipt={'execution_completed': bool(res.get('executed')), 'status': _action_id(e.get('status')),
                 'attempts': 0, 'latency_ms': None, 'input_tokens': 0, 'output_tokens': 0, 'cost_usd': None,
                 'physical_call_attempted': False, 'output_sha256': None},
        outcome=no_outcome('parent_absorbed_unverified'),
        provenance={'kind': 'observed', 'producer': 'z0int.dispatch_authority'},
        lineage={'group': _h({'session': r.get('session_id') or ''}, 12), 'policy_revision': None,
                 'policy_sha256': None, 'provider': None, 'model': None, 'parent_episode_ids': []},
        ids={x for x in (r.get('trace_id'), e.get('caller_trace_id')) if x},
        key=f"dispatch:{r.get('trace_id')}")


def production_policy(home: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    """The production worker-routing policy as that host runs it: the manifest plus the source home's
    ``config/worker_routing.local.json`` (worker_routing.configuration, read with Z0INT_HOME pointed at the source
    home for the duration of the call; it only reads)."""
    import os
    from . import worker_routing
    prev = os.environ.get('Z0INT_HOME')
    os.environ['Z0INT_HOME'] = str(home)
    try:
        return worker_routing.configuration()
    except (OSError, ValueError, KeyError):
        return {}, {}
    finally:
        if prev is None:
            os.environ.pop('Z0INT_HOME', None)
        else:
            os.environ['Z0INT_HOME'] = prev


def _replay_plan_route(text: str | None, plan: Mapping[str, Any], cset: list[str], policy: Mapping[str, Any],
                       providers: Mapping[str, Any], e0: Mapping[str, Any]) -> dict[str, Any]:
    """Replay of the production automatic policy (worker_routing.plan_route) for this decision.

    Uses the text prefix in memory, the host's production policy and, as availability, the providers the receipt
    shows as reachable. ``revision_match`` is true only when the policy revision equals the receipt's."""
    if not policy or not text:
        return {'worker_routing.plan_route': {'choice': None, 'why': 'no_text_or_policy'}}
    from .worker_routing import plan_route
    avail = [p for p in cset if p != PARENT_ONLY]
    try:
        out = plan_route(text, json.loads(json.dumps(policy)), providers, available_providers=avail)
    except Exception as exc:  # replay must never break the build
        return {'worker_routing.plan_route': {'choice': None, 'why': f'error:{type(exc).__name__}'}}
    choice = out['candidates'][0]['provider'] if out.get('candidates') else PARENT_ONLY
    return {'worker_routing.plan_route': {
        'choice': choice if choice in cset else PARENT_ONLY, 'category': _action_id(out.get('category')),
        'revision': policy.get('policy_revision'),
        'revision_match': policy.get('policy_revision') == e0.get('policy_revision'),
        'recorded_was_explicit': plan.get('category') == 'explicit'}}


# ----------------------------------------------------------------------------- adapter: OMP v1 spine (coding.*)
def omp_route_drafts(home: Path, counts: Counter, joins: Mapping[str, Mapping[str, Any]]) -> list[Draft]:
    """One episode per OMP v1 ``coding.*`` decision receipt (task class -> provider/model offer).

    With a kerdoios plan the candidate set is its placements + rejections (offer ids, recorded at decision time) and
    the action is the placed offer; without one the set is reconstructed (``exact: false``). Evidence is the preflight
    work requirement (numbers and closed labels) and the prompt's content hashes; the outcome is the effective tier of
    the joined outcome (gold/negative only)."""
    from .harness_id import turn_key
    rows: dict[str, tuple[SourceRow, dict]] = {}
    for sr in read_rows(home, 'receipts/decisions.jsonl', counts):
        r = sr.row
        if str(r.get('capability_id') or '').startswith('coding.') and r.get('trace_id'):
            rows[r['trace_id']] = (sr, r)
    drafts = []
    for tid, (sr, r) in rows.items():
        kp = r.get('kerdoios_plan') if isinstance(r.get('kerdoios_plan'), dict) else None
        pf = r.get('preflight') if isinstance(r.get('preflight'), dict) else {}
        wr = pf.get('work_requirement') if isinstance(pf.get('work_requirement'), dict) else {}
        prov, model = r.get('provider'), r.get('model')
        if kp and kp.get('placements'):
            placed = [p.get('offer_id') for p in kp['placements'] if isinstance(p, dict) and p.get('offer_id')]
            rejected = [p.get('offer_id') for p in kp.get('rejections') or [] if isinstance(p, dict)]
            cset = sorted({_action_id(x) for x in placed + rejected if x})
            chosen = _action_id(placed[0]) if placed else None
            csrc, exact = 'kerdoios_plan', True
            est_cost = _num(kp.get('estimated_cost'))
        else:
            chosen = _action_id(f'{prov}/{model}' if model else prov) if prov else None
            alt = {'escalate'} if r.get('route') != 'escalate' else set()
            cset = sorted({c for c in {chosen} | alt if c})
            csrc, exact, est_cost = 'reconstructed', False, None
            if r.get('route') == 'escalate':
                chosen = 'escalate'
                cset = sorted(set(cset) | {'escalate'})
        prompt = r.get('prompt') if isinstance(r.get('prompt'), str) else None  # memory only
        hashes = content_hashes(prompt)
        dec = pf.get('decision') if isinstance(pf.get('decision'), dict) else {}
        features = {'capability': _action_id(r.get('capability_id')),
                    'label': _action_id(pf.get('label')), 'confidence': _num(pf.get('confidence') or r.get('confidence')),
                    'margin': _num(pf.get('margin')), 'evidence_level': _action_id(pf.get('evidence_level')),
                    'pf_route': _action_id(dec.get('route') or pf.get('route')),
                    'pf_escalate_to': _action_id(dec.get('escalate_to')),
                    'wr.coding': _num(wr.get('coding')), 'wr.reasoning': _num(wr.get('reasoning')),
                    'wr.vision': bool(wr.get('vision')), 'wr.tool_use': bool(wr.get('tool_use')),
                    'wr.in_tokens': wr.get('estimated_input_tokens'), 'wr.out_tokens': wr.get('estimated_output_tokens'),
                    'wr.mode': _action_id(wr.get('mode')), 'len_bucket': hashes['len_bucket'],
                    **keyword_features(prompt)}
        j = joins.get(tid)
        if j:
            outcome = _tier_outcome(j.get('outcome'), j.get('outcome_tier'), j.get('_ref'))
        elif r.get('outcome') or r.get('outcome_tier'):
            outcome = _tier_outcome(r.get('outcome'), r.get('outcome_tier'), _ref(sr, tid))
        else:
            outcome = no_outcome('no_outcome_join')
        ts = _ts_of(r.get('ts'))
        refs = [_ref(sr, tid, ts)] + ([j['_ref']] if j else [])
        d = Draft(
            family='omp_route', source='receipts.omp_v1', decided_ts=ts, source_refs=refs,
            evidence={'kind': 'hashed_features', 'features': features, 'request_sha256': r.get('prompt_hash'),
                      'content_sha': hashes['content_sha'], 'content_norm_sha': hashes['content_norm_sha'],
                      'content_scope': 'prompt' if prompt else None, 'state_ref': None,
                      'semantic_key': hashes['content_norm_sha'] or r.get('prompt_hash')},
            candidates={'set': cset, 'source': csrc, 'exact': exact, 'legal': cset, 'skipped': []},
            action={'chosen': chosen, 'decided_by': 'kerdoios' if kp else _action_id(r.get('route')),
                    'fallbacks': int(r.get('fallbacks') or 0), 'model': _action_id(model)},
            receipt={'execution_completed': (r.get('outcome') or {}).get('execution_completed')
                     if isinstance(r.get('outcome'), dict) else None,
                     'status': _action_id(r.get('execution')), 'attempts': 1 + int(r.get('fallbacks') or 0),
                     'latency_ms': _num(r.get('latency_ms')), 'input_tokens': int(r.get('input_tokens') or 0),
                     'output_tokens': int(r.get('output_tokens') or 0),
                     'measured_frontier_tokens': r.get('measured_frontier_tokens'), 'cost_usd': est_cost,
                     'physical_call_attempted': None, 'output_sha256': None},
            outcome=outcome,
            provenance={'kind': 'observed', 'producer': 'omp.v1_spine'},
            lineage={'group': _h({'session': r.get('session_id') or ''}, 12),
                     'turn_key': turn_key('omp', r.get('session_id') or '', tid),
                     'policy_revision': None, 'provider': _action_id(prov), 'model': _action_id(model),
                     'bridge_build': _action_id(r.get('bridge_build_id')), 'parent_episode_ids': []},
            key=f'omp:{tid}')
        d.policy_replays = {'kerdoios.recorded': {'choice': chosen, 'revision_match': True}}
        d.ids |= {tid, (r.get('extra') or {}).get('parent_trace_id')}
        d.ids.discard(None)
        drafts.append(d)
    return drafts


# ----------------------------------------------------------------------------- adapter: DecisionOpportunity records
def decision_opportunity_drafts(home: Path, counts: Counter) -> list[Draft]:
    """One episode per captured DecisionOpportunity (harness turn), joined with its turn outcome and the latest
    independent verification. Candidate set = the record's action space (exact, with legality); historical action =
    what the agent did (ASK when it asked the user, else ACT); the production policy is the recorded gate."""
    from . import harness_capture as hc
    from . import loop_export as le
    from .decision_opportunity import ACTIONS, deterministic_gate
    drafts = []
    for harness in hc.HARNESSES:
        state_rel = f'state/{harness}'
        if not (home / state_rel).is_dir():
            continue
        opps: dict[tuple, tuple[SourceRow, dict]] = {}
        for sr in read_rows(home, f'{state_rel}/opportunities.jsonl', counts):
            r = sr.row
            if r.get('schema') == hc.schema(harness, 'opportunity_record'):
                opp = r.get('opportunity') or {}
                opps[(r.get('session_id'), (opp.get('trace') or {}).get('trace_id') or r.get('trace_id'))] = (sr, r)
        observed = {(sr.row.get('session_id'), sr.row.get('trace_id')): sr
                    for sr in read_rows(home, f'{state_rel}/outcomes.jsonl', counts)
                    if sr.row.get('schema') == hc.schema(harness, 'turn_outcome')}
        verified: dict[tuple, SourceRow] = {}
        for sr in read_rows(home, f'{state_rel}/outcomes_verified.jsonl', counts):
            if sr.row.get('schema') == hc.schema(harness, 'turn_outcome_verified'):
                verified[(sr.row.get('session_id'), sr.row.get('trace_id'))] = sr
        counts[f'do:{harness}:verified_rows'] = len(verified)
        counts[f'do:{harness}:verified_without_opportunity'] = sum(1 for k in verified if k not in opps)
        for (sid, tid), (sr, rec) in opps.items():
            opp = rec.get('opportunity') or {}
            obs_sr, ver_sr = observed.get((sid, tid)), verified.get((sid, tid))
            obs, ver = (obs_sr.row if obs_sr else None), (ver_sr.row if ver_sr else None)
            cohort = le.capture_cohort(rec) or (ver or {}).get('cohort') or rec.get('cohort') or 'unknown'
            space = [a for a in opp.get('action_space') or [] if isinstance(a, dict)]
            kinds = sorted({a.get('kind') for a in space if a.get('kind') in ACTIONS}, key=ACTIONS.index)
            legal = sorted({a.get('kind') for a in space if a.get('legal') and a.get('kind') in ACTIONS},
                           key=ACTIONS.index)
            try:
                gate_replay = deterministic_gate(opp) if space else None
            except (KeyError, TypeError):
                gate_replay = None
            turn = (ver or {}).get('turn') or {}
            started, ended = _ts_of(turn.get('started_at')), _ts_of(turn.get('ended_at'))
            decided = _ts_of((opp.get('provenance') or {}).get('built_at')) or _ts_of(rec.get('recorded_at')) or started
            lab = le.label_of(ver)
            if lab['resolved']:
                outcome = {'observed': True, 'y_success': lab['y_success'], 'state': lab['state'], 'independent': True,
                           'source': f'outcome_verifier:{harness}', 'label_class': lab['label_class'],
                           'label_confidence': lab['label_confidence'], 'oracles': lab['oracles'],
                           'label_ref': _ref(ver_sr, tid, _ts_of(ver.get('verified_at')))}
            else:
                outcome = no_outcome('unverified' if ver else 'no_verification_row')
            refs = [_ref(sr, tid, decided)] + [_ref(x, tid) for x in (obs_sr, ver_sr) if x]
            feats = le.opportunity_features(rec, cohort if cohort in le.COHORTS else 'unknown')
            intent = opp.get('intent') or {}
            d = Draft(
                family='decision_opportunity', source=f'state.{harness}.opportunity', decided_ts=decided,
                source_refs=refs,
                evidence={'kind': 'hashed_features', 'features': feats, 'feature_schema_sha': le.FEATURE_SCHEMA_SHA,
                          'request_sha256': intent.get('revision'), 'content_sha': None, 'content_norm_sha': None,
                          'state_ref': {'semantic_id': opp.get('semantic_id'),
                                        'opportunity_id': (opp.get('trace') or {}).get('opportunity_id'),
                                        'packet_id': (opp.get('provenance') or {}).get('packet_id'),
                                        'packet_schema': (opp.get('provenance') or {}).get('packet_schema'),
                                        'memory_snapshot_id': opp.get('memory_snapshot_id')},
                          'semantic_key': opp.get('semantic_id')},
                candidates={'set': kinds, 'source': 'action_space', 'exact': bool(kinds), 'legal': legal,
                            'skipped': []},
                action={'chosen': None if obs is None else ('ASK' if obs.get('asked_user') else 'ACT'),
                        'decided_by': 'agent', 'fallbacks': 0, 'model': _action_id(rec.get('model_id'))},
                receipt={'execution_completed': None if obs is None else obs.get('execution_completed', True),
                         'status': None, 'attempts': 1,
                         'latency_ms': round((ended - started) * 1000, 3) if started and ended and ended >= started
                         else None,
                         'input_tokens': int((obs or {}).get('input_tokens') or 0),
                         'output_tokens': int((obs or {}).get('output_tokens') or 0),
                         'tool_calls': int((obs or {}).get('tool_calls') or turn.get('tool_calls') or 0),
                         'assistant_messages': int((obs or {}).get('assistant_messages') or 0), 'cost_usd': None,
                         'physical_call_attempted': None, 'output_sha256': None},
                outcome=outcome,
                provenance={'kind': 'observed', 'producer': f'harness_capture:{harness}'},
                lineage={'group': _h({'session': sid}, 12), 'turn_key': le.row_turn_key(harness, sid, tid),
                         'harness': harness, 'cohort': cohort, 'policy_revision': _action_id(rec.get('policy_revision')),
                         'verifier': ((ver or {}).get('verifier') or {}).get('version'), 'model': _action_id(
                             rec.get('model_id')), 'parent_episode_ids': []},
                key=f'do:{harness}:{sid}:{tid}')
            if d.action['chosen'] is not None and d.action['chosen'] not in kinds and kinds:
                counts['do:observed_action_outside_space'] += 1
                d.candidates['set'] = sorted(set(kinds) | {d.action['chosen']}, key=ACTIONS.index)
                d.candidates['exact'] = False
            d.policy_replays = {'deterministic_gate': {'choice': rec.get('gate'), 'replayed': gate_replay,
                                                       'revision_match': rec.get('gate') == gate_replay}}
            d.ids |= {tid, (ver or {}).get('trace_id')}
            d.ids.discard(None)
            drafts.append(d)
    return drafts


# ----------------------------------------------------------------------------- adapter: shadow / teacher (synthetic)
def shadow_drafts(home: Path, counts: Counter) -> list[Draft]:
    """Challenger / teacher answers: always ``provenance.synthetic``, never an outcome. A teacher-claimed label
    (``y``/``label`` on the row) is kept as ``teacher_claim`` only. Parents are linked by turn key in ``link``."""
    from . import harness_capture as hc
    drafts = []
    for harness in hc.HARNESSES:
        rel = f'state/{harness}/shadow_decisions.jsonl'
        for sr in read_rows(home, rel, counts):
            r = sr.row
            if r.get('schema') != hc.schema(harness, 'shadow_decision'):
                continue
            ch = r.get('challenger') if isinstance(r.get('challenger'), dict) else {'id': r.get('challenger')}
            ts = _ts_of(r.get('day')) if r.get('day') else None
            dist = r.get('distribution') if isinstance(r.get('distribution'), dict) else {}
            d = Draft(
                family='shadow', source=f"state.{harness}.shadow.{_action_id(r.get('source')) or 'unknown'}",
                decided_ts=ts, source_refs=[_ref(sr, r.get('decision_id'), ts)],
                evidence={'kind': 'parent_ref', 'features': {'candidate_action_count': r.get('candidate_action_count')},
                          'request_sha256': None, 'content_sha': r.get('content_sha'), 'content_norm_sha': None,
                          'state_ref': {'opportunity_id': r.get('opportunity_id')}, 'semantic_key': None},
                candidates={'set': sorted({_action_id(k) for k in dist} | ({_action_id(r.get('decision'))} - {None})),
                            'source': 'challenger_distribution', 'exact': False, 'legal': [], 'skipped': []},
                action={'chosen': _action_id(r.get('decision')), 'decided_by': 'challenger', 'fallbacks': 0,
                        'model': _action_id(ch.get('model'))},
                receipt={'execution_completed': None, 'status': _action_id(r.get('status')), 'attempts': 0,
                         'latency_ms': _num(r.get('latency_ms')), 'input_tokens': 0, 'output_tokens': 0,
                         'cost_usd': None, 'physical_call_attempted': None, 'output_sha256': None},
                outcome=no_outcome('synthetic_never_observed'),
                provenance={'kind': 'synthetic', 'producer': _action_id(ch.get('id')),
                            'producer_sha256': ch.get('sha256'), 'backend': _action_id(ch.get('backend')),
                            'teacher_claim': r.get('y') if r.get('y') in (0, 1) else None,
                            'observed_tool': _action_id(r.get('actual_tool'))},
                lineage={'group': r.get('group'), 'turn_key': r.get('turn_key'), 'harness': harness,
                         'cohort': r.get('cohort') or 'unknown', 'checkpoint': ch.get('sha256'),
                         'parent_episode_ids': []},
                key=f"shadow:{harness}:{r.get('decision_id') or sr.line_sha}")
            drafts.append(d)
    return drafts


def link_synthetic(drafts: list[Draft], host: str) -> None:
    """Point every synthetic draft at its observed parent episode(s) by turn key / opportunity id, and inherit the
    parent's decision time and group so the split keeps teacher rows with the observed episode they shadow."""
    by_turn: dict[str, list[Draft]] = {}
    by_opp: dict[str, list[Draft]] = {}
    for d in drafts:
        if d.provenance['kind'] != 'observed':
            continue
        if d.lineage.get('turn_key'):
            by_turn.setdefault(d.lineage['turn_key'], []).append(d)
        oid = ((d.evidence.get('state_ref') or {}) or {}).get('opportunity_id')
        if oid:
            by_opp.setdefault(oid, []).append(d)
    for d in drafts:
        if d.provenance['kind'] != 'synthetic':
            continue
        parents = by_turn.get(d.lineage.get('turn_key') or '') or by_opp.get(
            (d.evidence.get('state_ref') or {}).get('opportunity_id') or '') or []
        d.lineage['parent_episode_ids'] = sorted({p.episode_id for p in parents})
        if parents:
            d.evidence['semantic_key'] = _h({'parents': d.lineage['parent_episode_ids'],
                                             'producer': d.provenance.get('producer')}, 32)
            p = min(parents, key=lambda x: x.decided_ts or 0)
            d.decided_ts = d.decided_ts or p.decided_ts
            d.lineage['group'] = p.lineage.get('group') or d.lineage.get('group')
            if p.candidates.get('set'):
                d.candidates['parent_set'] = p.candidates['set']


# ----------------------------------------------------------------------------- dedup + contamination
def dedup(episodes: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Drop exact duplicates (same decision record read twice, e.g. a re-appended row or a merged host) keeping the
    earliest; mark semantic duplicates (``dup_of`` = first episode with the same semantic hash) without dropping
    them -- the split decides what a near-duplicate may do (it may never straddle train and eval)."""
    episodes = sorted(episodes, key=lambda e: (e.get('decided_at') or '~', e['episode_id']))
    seen_exact: dict[str, str] = {}
    seen_ids: set = set()
    first_sem: dict[str, str] = {}
    out, report = [], Counter()
    for e in episodes:
        if e['episode_id'] in seen_ids:
            report['duplicate_episode_id'] += 1
            continue
        hx, hs = e['hashes']['exact'], e['hashes']['semantic']
        if hx in seen_exact:
            report['exact_dropped'] += 1
            continue
        seen_exact[hx] = e['episode_id']
        seen_ids.add(e['episode_id'])
        if hs in first_sem:
            e['dup_of'] = first_sem[hs]
            report['semantic_marked'] += 1
        else:
            first_sem[hs] = e['episode_id']
            e['dup_of'] = None
        out.append(e)
    return out, dict(report)


def lineage_check(episodes: list[dict[str, Any]]) -> dict[str, Any]:
    """Provenance/lineage audit of a built set: synthetic rows with observed outcomes (fatal), synthetic rows without
    an observed parent, observed outcomes sourced from a synthetic producer (fatal), parent ids that do not exist."""
    ids = {e['episode_id'] for e in episodes}
    rep = Counter()
    fatal = []
    for e in episodes:
        prov, out = e['provenance'], e['outcome']
        if prov['kind'] == 'synthetic':
            if out.get('observed') or out.get('y_success') is not None:
                fatal.append(e['episode_id'])
            pids = e['lineage'].get('parent_episode_ids') or []
            rep['synthetic_with_parent' if pids else 'synthetic_orphan'] += 1
            rep['dangling_parent_ids'] += sum(1 for p in pids if p not in ids)
        elif out.get('observed') and str(out.get('source') or '').startswith(('shadow', 'teacher', 'synthetic')):
            fatal.append(e['episode_id'])
        if prov['kind'] == 'reconstructed':
            rep['reconstructed'] += 1
            if out.get('observed') and not str(out.get('label_class') or '').startswith(RECONSTRUCTED_LABEL_NS):
                fatal.append(e['episode_id'])
    return {'counts': dict(rep), 'fatal': len(fatal), 'fatal_ids': fatal[:20]}


# ----------------------------------------------------------------------------- AgentsView (counts only, mode=ro)
def agentsview_coverage(directory: str | Path | None) -> dict[str, Any]:
    from . import agentsview_ro as av
    conn = av.connect(av.db_path(directory) if directory else None)
    if not conn:
        return {'available': False, 'reason': conn.reason}
    try:
        rows = conn.execute('select agent, count(*), min(started_at), max(started_at) from sessions '
                            'group by agent').fetchall()
        return {'available': True, 'sessions_by_agent': {str(a): int(n) for a, n, _, _ in rows},
                'first_day': min((str(lo)[:10] for _, _, lo, _ in rows if lo), default=None),
                'last_day': max((str(hi)[:10] for _, _, _, hi in rows if hi), default=None),
                'staleness_hours': av.staleness_hours(conn)}
    except Exception as exc:
        return {'available': False, 'reason': f'error:{type(exc).__name__}'}
    finally:
        conn.close()


# ----------------------------------------------------------------------------- build
def compile_episodes(source_home: str | Path, *, host: str | None = None,
                     agentsview: str | Path | None = None) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Compile every episode from ``source_home`` (read-only). Returns (episodes, stats)."""
    home = Path(source_home).expanduser().resolve()
    host = host or socket.gethostname()
    counts: Counter = Counter()
    joins = _outcome_joins(home, counts)
    drafts: list[Draft] = []
    drafts += worker_route_drafts(home, counts, joins)
    drafts += omp_route_drafts(home, counts, joins)
    drafts += decision_opportunity_drafts(home, counts)
    drafts += shadow_drafts(home, counts)
    link_synthetic(drafts, host)
    events = index_eventlog(home)
    for d in drafts:
        attach_events(d, events)
    episodes, invalid = [], Counter()
    for d in drafts:
        ep = d.finish(host)
        try:
            validate(ep)
        except EpisodeInvalid as exc:
            invalid[str(exc).split(':')[0][:60]] += 1
            continue
        episodes.append(ep)
    episodes, dd = dedup(episodes)
    stats = {'read': dict(sorted((k, v) for k, v in counts.items())), 'invalid': dict(invalid), 'dedup': dd,
             'eventlog': {'events': events.counts.get('events', 0), 'missing': bool(events.counts.get('missing')),
                          'by_type': {k[5:]: v for k, v in sorted(events.counts.items()) if k.startswith('type:')},
                          'episodes_with_event_refs': sum(1 for e in episodes if e['event_refs']),
                          'outcomes_from_eventlog': sum(1 for e in episodes if str(e['outcome'].get('source') or '')
                                                        .startswith('eventlog.'))},
             'lineage': lineage_check(episodes)}
    if agentsview is not None:
        stats['agentsview'] = agentsview_coverage(agentsview)
    return episodes, stats


def is_labelled(e: Mapping[str, Any]) -> bool:
    """Usable as observed truth: observed provenance, an independent outcome, and a historical action."""
    return (e['provenance']['kind'] == 'observed' and bool(e['outcome'].get('observed'))
            and bool(e['outcome'].get('independent')) and e['action'].get('chosen') is not None)


def summarize(episodes: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    eps = list(episodes)
    by_source: dict[str, Counter] = {}
    for e in eps:
        c = by_source.setdefault(e['source'], Counter())
        c['episodes'] += 1
        c[e['provenance']['kind']] += 1
        c['labelled'] += is_labelled(e)
        c['y1'] += e['outcome'].get('y_success') == 1
        c['y0'] += e['outcome'].get('y_success') == 0
        c['exact_candidates'] += bool(e['candidates'].get('exact'))
        c['semantic_dups'] += bool(e.get('dup_of'))
    observed = [e for e in eps if e['provenance']['kind'] == 'observed']
    days = sorted(e['day'] for e in eps if e.get('day'))
    return {'episodes': len(eps), 'observed': len(observed), 'synthetic': len(eps) - len(observed),
            'labelled': sum(map(is_labelled, eps)),
            'labelled_share_of_observed': round(sum(map(is_labelled, eps)) / len(observed), 4) if observed else None,
            'by_family': dict(Counter(e['family'] for e in eps)),
            'by_source': {k: dict(v) for k, v in sorted(by_source.items())},
            'first_day': days[0] if days else None, 'last_day': days[-1] if days else None}
