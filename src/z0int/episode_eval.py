"""Utility scoring + baselines over routing episodes (z0int#143 eval contract; judged later by z0evals#92).

    utility(a) = task_success - latency_cost - token_cost - compute_cost - unnecessary_escalation - safety_penalty

The same candidate set is used for every policy. Two estimators, never mixed:

  replay   labelled held-out episodes where the policy picks the historical action: observed outcome and the
           episode's own receipt costs (unbiased on that subset; ``coverage`` says how big it is)
  dm       direct method over every held-out observed episode with a candidate set: success from the observed label
           when the policy matches history, else a train-fitted Laplace estimate p(success | family, bucket, action);
           costs from the receipt when matched, else train medians per (family, action)

Families are scored separately (a route utility and a DecisionOpportunity utility are not comparable). Synthetic
episodes never enter scoring. Weights are declared below and written into every report.
"""

from __future__ import annotations

import statistics
from collections import Counter, defaultdict
from typing import Any, Callable, Iterable, Mapping

from .episodes import ESCALATION, PARENT_ONLY, is_labelled

SCHEMA = 'z0int.episode_baseline_report.v0'
# Cost terms are bounded so a verified success (1.0) stays the dominant term: latency and tokens saturate at their caps.
WEIGHTS = {
    'task_success': 1.0,
    'latency_per_min': 0.1, 'latency_cap': 0.5,
    'tokens_per_1k': 0.002, 'tokens_cap': 0.5,
    'compute_per_usd': 1.0,
    'unnecessary_escalation': 0.25,
    'safety_violation': 2.0,
    'escalation_margin': 0.05,  # a non-escalation candidate within this success margin makes escalating unnecessary
}
# Declared price classes (USD per 1k tokens) for actions without a measured cost; local and validated-free routes are 0.
PRICE_PER_1K = {'free': 0.0, 'cheap': 0.002, 'paid': 0.01}
_FREE_MARKERS = ('local', 'groot', 'free', 'tiny', 'mb', 'ABSTAIN', 'OBSERVE')
_PAID_MARKERS = ('paid', 'frontier', 'PARENT_ONLY', 'escalate', 'ESCALATE', 'openai', 'xai', 'cursor')


def price_class(action: str | None) -> str:
    a = str(action or '')
    if any(m in a for m in _PAID_MARKERS):
        return 'paid'
    if any(m in a for m in _FREE_MARKERS):
        return 'free'
    return 'cheap'


def bucket(e: Mapping[str, Any]) -> tuple:
    f = e['evidence'].get('features') or {}
    fam = e['family']
    if fam == 'decision_opportunity':
        gate = next((k[5:] for k, v in f.items() if k.startswith('gate=') and v), None)
        return (gate, bool(f.get('n_unknowns_blocking')), bool(f.get('n_contradictions')))
    if fam == 'worker_route':
        return (f.get('category'), f.get('function'))
    if fam == 'omp_route':
        return (f.get('capability'),)
    return ()


# ----------------------------------------------------------------------------- train-fitted model
class Model:
    """Laplace success estimates with backoff (bucket,action) -> (action) -> family prior, and cost medians."""

    def __init__(self, train: Iterable[Mapping[str, Any]]):
        self.s = defaultdict(lambda: [0, 0])     # key -> [successes, n]
        self.lat = defaultdict(list)
        self.tok = defaultdict(list)
        self.usd = defaultdict(list)
        self.actions = defaultdict(Counter)
        for e in train:
            if e['provenance']['kind'] != 'observed' or e['action'].get('chosen') is None:
                continue
            fam, a = e['family'], e['action']['chosen']
            self.actions[fam][a] += 1
            r = e['receipt']
            if r.get('latency_ms') is not None:
                self.lat[(fam, a)].append(r['latency_ms'])
            self.tok[(fam, a)].append((r.get('input_tokens') or 0) + (r.get('output_tokens') or 0))
            if r.get('cost_usd') is not None:
                self.usd[(fam, a)].append(r['cost_usd'])
            if is_labelled(e):
                y = e['outcome']['y_success']
                for key in ((fam, bucket(e), a), (fam, a), (fam,)):
                    self.s[key][0] += y
                    self.s[key][1] += 1

    def p_success(self, e: Mapping[str, Any], a: str) -> float:
        fam = e['family']
        prior_s, prior_n = self.s[(fam,)]
        prior = (prior_s + 1) / (prior_n + 2)
        for key in ((fam, bucket(e), a), (fam, a)):
            s, n = self.s[key]
            if n:
                return (s + 2 * prior) / (n + 2)  # shrink toward the family prior
        return prior

    def _med(self, table, fam: str, a: str) -> float | None:
        vals = table.get((fam, a)) or [v for (f, _), xs in table.items() if f == fam for v in xs]
        return statistics.median(vals) if vals else None

    def cost(self, e: Mapping[str, Any], a: str, matched: bool) -> dict[str, float]:
        fam, r = e['family'], e['receipt']
        if matched:
            lat, tok, usd = r.get('latency_ms'), (r.get('input_tokens') or 0) + (r.get('output_tokens') or 0), \
                r.get('cost_usd')
        else:
            lat, tok, usd = self._med(self.lat, fam, a), self._med(self.tok, fam, a), self._med(self.usd, fam, a)
        tok = tok or 0.0
        if usd is None:
            usd = PRICE_PER_1K[price_class(a)] * tok / 1000.0
        return {'latency_s': (lat or 0.0) / 1000.0, 'tokens': float(tok), 'usd': float(usd)}

    def majority(self, fam: str) -> str | None:
        return self.actions[fam].most_common(1)[0][0] if self.actions[fam] else None


# ----------------------------------------------------------------------------- utility
def utility(e: Mapping[str, Any], a: str | None, model: Model, *, w: Mapping[str, float] = WEIGHTS) -> dict[str, Any]:
    cands = e['candidates'].get('set') or []
    legal = e['candidates'].get('legal') or cands
    hist = e['action'].get('chosen')
    matched = a is not None and a == hist
    violation = a is None or (bool(cands) and a not in cands) or (bool(legal) and a not in legal)
    if matched and is_labelled(e):
        p, observed = float(e['outcome']['y_success']), True
    else:
        p, observed = (0.0 if violation else model.p_success(e, a)), False
    c = model.cost(e, a, matched) if a else {'latency_s': 0.0, 'tokens': 0.0, 'usd': 0.0}
    esc = False
    if a in ESCALATION.get(e['family'], ()) and not violation:
        alts = [b for b in legal if b not in ESCALATION.get(e['family'], ())]
        esc = any(model.p_success(e, b) >= model.p_success(e, a) - w['escalation_margin'] for b in alts)
    lat_cost = min(w['latency_cap'], w['latency_per_min'] * c['latency_s'] / 60.0)
    tok_cost = min(w['tokens_cap'], w['tokens_per_1k'] * c['tokens'] / 1000.0)
    u = (w['task_success'] * p - lat_cost - tok_cost - w['compute_per_usd'] * c['usd']
         - w['unnecessary_escalation'] * esc - w['safety_violation'] * violation)
    return {'u': u, 'success': p, 'observed_success': observed, 'matched': matched, 'violation': violation,
            'unnecessary_escalation': esc, 'escalated': a in ESCALATION.get(e['family'], ()), **c}


# ----------------------------------------------------------------------------- baselines
MIN_DECISION_LABELS = 30  # below this the success term is mostly the prior: utilities are descriptive, not a verdict
Policy = Callable[[Mapping[str, Any], Model], 'str | None']


def _first_legal(e: Mapping[str, Any]) -> str | None:
    legal = e['candidates'].get('legal') or e['candidates'].get('set') or []
    return legal[0] if legal else None


def historical(e, m):
    return e['action'].get('chosen')


def production(e, m):
    """The production policy as recorded/replayed at build time: the DecisionOpportunity gate, a worker_routing
    plan_route replay, the recorded kerdoios placement. No replay available -> the recorded decision."""
    for name in ('deterministic_gate', 'worker_routing.plan_route', 'kerdoios.recorded'):
        r = (e.get('policy_replays') or {}).get(name)
        if r and r.get('choice'):
            return r['choice']
    return e['action'].get('chosen')


def keyword(e, m):
    """A rule baseline over closed keyword features only (no learning, no legality check)."""
    f = e['evidence'].get('features') or {}
    cands = e['candidates'].get('set') or []
    fam = e['family']
    if fam == 'decision_opportunity':
        fams = int(f.get('n_families') or 0)
        if fams and f.get('n_unknowns_blocking'):
            return 'ASK'
        if f.get('n_contradictions'):
            return 'ESCALATE'
        return 'ACT'
    if f.get('kw.local') and any('local' in c or 'groot' in c for c in cands):
        return next(c for c in cands if 'local' in c or 'groot' in c)
    if f.get('kw.code') or f.get('wr.tool_use'):
        paid = [c for c in cands if price_class(c) == 'paid' and c != PARENT_ONLY] or \
            [c for c in cands if price_class(c) == 'paid']
        if paid:
            return paid[0]
    return cheapest(e, m)


def majority(e, m):
    a = m.majority(e['family'])
    cands = e['candidates'].get('set') or []
    return a if a in cands or not cands else _first_legal(e)


def cheapest(e, m):
    legal = e['candidates'].get('legal') or e['candidates'].get('set') or []
    if not legal:
        return None
    esc = ESCALATION.get(e['family'], ())
    rank = {'free': 0, 'cheap': 1, 'paid': 2}
    return min(legal, key=lambda a: (m.cost(e, a, False)['usd'], rank[price_class(a)], a in esc,
                                     m.cost(e, a, False)['tokens'], a))


BASELINES: dict[str, Policy] = {'historical': historical, 'production': production, 'keyword': keyword,
                                'majority': majority, 'cheapest': cheapest}


def _mean(xs: list[float]) -> float | None:
    return round(sum(xs) / len(xs), 4) if xs else None


def score(policy: Policy, eps: list[Mapping[str, Any]], model: Model) -> dict[str, Any]:
    dm, rep, rows = [], [], []
    for e in eps:
        a = policy(e, model)
        r = utility(e, a, model)
        rows.append(r)
        dm.append(r['u'])
        if r['matched'] and r['observed_success']:
            rep.append(r['u'])
    n = len(rows)
    labelled = sum(1 for e in eps if is_labelled(e))
    return {'n': n, 'dm_utility': _mean(dm), 'replay_utility': _mean(rep), 'replay_n': len(rep),
            'replay_coverage': round(len(rep) / labelled, 4) if labelled else None,
            'match_rate': _mean([float(r['matched']) for r in rows]),
            'safety_violation_rate': _mean([float(r['violation']) for r in rows]),
            'escalation_rate': _mean([float(r['escalated']) for r in rows]),
            'unnecessary_escalation_rate': _mean([float(r['unnecessary_escalation']) for r in rows]),
            'mean_success_estimate': _mean([r['success'] for r in rows])}


def baseline_report(by_split: Mapping[str, list[Mapping[str, Any]]], *, eval_splits=('dev', 'eval', 'recent'),
                    policies: Mapping[str, Policy] = BASELINES) -> dict[str, Any]:
    model = Model(by_split.get('train', []))
    out: dict[str, Any] = {'schema': SCHEMA, 'weights': dict(WEIGHTS), 'price_per_1k': dict(PRICE_PER_1K),
                           'estimators': {'replay': 'labelled & policy==historical; observed outcome + receipt costs',
                                          'dm': 'train-fitted Laplace success (bucket->action->family backoff) + '
                                                'train cost medians when unmatched'},
                           'train_labelled': sum(1 for e in by_split.get('train', []) if is_labelled(e)),
                           'splits': {}}
    for s in eval_splits:
        eps = [e for e in by_split.get(s, []) if e['provenance']['kind'] == 'observed'
               and (e['candidates'].get('set') or [])]
        fams: dict[str, list] = defaultdict(list)
        for e in eps:
            fams[e['family']].append(e)
        out['splits'][s] = {}
        for fam, fe in sorted(fams.items()):
            lab = sum(1 for e in fe if is_labelled(e))
            train_lab = sum(1 for e in by_split.get('train', []) if e['family'] == fam and is_labelled(e))
            out['splits'][s][fam] = {
                'episodes': len(fe), 'labelled': lab, 'train_labelled': train_lab,
                'decision_grade': lab >= MIN_DECISION_LABELS and train_lab >= MIN_DECISION_LABELS,
                'policies': {name: score(p, fe, model) for name, p in policies.items()}}
    return out
