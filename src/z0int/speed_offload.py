"""Speed-first offload (v0, shadow only).

Resource posture (z0int.posture) reasons about cost: under BURN it returns bounded work to the parent frontier
because that frontier's quota perishes at reset. That is wrong for a task class where the host-local model is
verified **as good and faster**: offloading it returns the same answer sooner, whatever the posture.

This module holds the policy; the evidence is the task-class registry (manifests/task_classes.v0.json), each
class carrying an equivalence record measured under benchmarks/speed_offload/PREREG.md.

  * speed_qualified class  -> offload for speed under every posture (BURN included)
  * cost_eligible class    -> offload for cost only under OFFLOAD (widens what OFFLOAD sends local)
  * not_equivalent class   -> the posture decision stands (BURN still returns it to the parent)
  * unknown class          -> parent

A speed/cost decision also needs: evidence file present with the registered sha256, the recorded route still a
validated $0 route with a nonzero cap and available, and the task context within the size envelope the class
was measured on. Any miss defers to the posture decision. v0 only annotates ``plan['speed_offload']``.
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
REGISTRY_PATH = ROOT / 'manifests/task_classes.v0.json'
SCHEMA = 'z0int.speed_offload.v0'
STATUSES = ('speed_qualified', 'cost_eligible', 'not_equivalent', 'unmeasured')


def load_registry(path=None):
    return json.loads(Path(path or REGISTRY_PATH).read_text())


def classify_task(task, registry):
    """(class, reason). Conservative: no pattern or more than one class matching -> 'unknown'."""
    if not isinstance(task, str) or not task.strip():
        return 'unknown', 'empty_task'
    hits = [name for name, c in (registry.get('classes') or {}).items()
            if all(re.search(p, task, re.I | re.S) for p in c.get('patterns') or [])
            and c.get('patterns')]
    if len(hits) == 1:
        return hits[0], 'pattern_match'
    return 'unknown', 'no_class_matched' if not hits else 'ambiguous:' + ','.join(sorted(hits))


def evidence_ok(registry):
    ev = registry.get('evidence') or {}
    try:
        data = Path(ev['path']).expanduser().read_bytes()
    except (KeyError, OSError, TypeError):
        return False, 'evidence_missing'
    if hashlib.sha256(data).hexdigest() != ev.get('sha256'):
        return False, 'evidence_sha256_mismatch'
    return True, 'evidence_ok'


def _route_usable(route, policy, providers, available_fn):
    from . import worker_routing as wr
    provider, model = route.get('provider'), route.get('model')
    if wr.free_route(policy, provider, model) is None:
        return False, 'route_not_validated_free'
    if policy.get('provider_caps', {}).get(provider, 0) in (None, 0):
        return False, 'route_cap_zero'
    if (policy.get('defaults') or {}).get(provider) != model:
        return False, 'route_not_provider_default'  # available()/execute re-check the default model only
    config = providers.get(provider)
    if not config or not (available_fn or wr.available)(provider, config):
        return False, 'route_unavailable'
    return True, 'route_ok'


def decide(task, context, posture_ann, policy, providers, available_fn=None, registry=None):
    """The speed-first decision for one route_worker task (pure given inputs; never raises)."""
    try:
        registry = registry if registry is not None else load_registry()
        posture = posture_ann.get('factory_posture') if posture_ann.get('available') else None
        posture_action = (posture_ann.get('would') or {}).get('action', 'none')
        base = {'schema': SCHEMA, 'posture': posture, 'posture_action': posture_action,
                'registry_revision': registry.get('revision'), 'would_offload_for_speed': False, 'candidates': []}
        cls, why = classify_task(task, registry)
        base['task_class'] = cls
        if cls == 'unknown':
            return {**base, 'class_status': None, 'action': 'parent', 'reason': f'unknown_class:{why}'}
        rec = registry['classes'][cls].get('equivalence') or {}
        status = rec.get('status', 'unmeasured')
        base['class_status'] = status
        speed = status == 'speed_qualified'
        cost = status == 'cost_eligible' and posture == 'OFFLOAD'
        if not (speed or cost):
            why = 'cost_eligible_outside_offload' if status == 'cost_eligible' else f'class_{status}'
            return {**base, 'action': 'defer_to_posture', 'reason': why}
        ok, why = evidence_ok(registry)
        if not ok:
            return {**base, 'action': 'defer_to_posture', 'reason': why}
        envelope = rec.get('max_context_chars')
        if not isinstance(envelope, int) or len(context or '') > envelope:
            return {**base, 'action': 'defer_to_posture', 'reason': 'context_outside_measured_envelope'}
        route = registry.get('local_route') or {}
        ok, why = _route_usable(route, policy, providers, available_fn)
        if not ok:
            return {**base, 'action': 'defer_to_posture', 'reason': why}
        candidate = {'provider': route['provider'], 'model': route['model']}
        if speed:
            return {**base, 'action': 'offload_for_speed', 'would_offload_for_speed': True, 'candidates': [candidate],
                    'reason': 'local_non_inferior_and_faster_p95', 'speedup_p95': rec.get('speedup_p95')}
        return {**base, 'action': 'offload_for_cost', 'candidates': [candidate], 'reason': 'offload_posture_cost_eligible'}
    except Exception as exc:  # advisory only: fail open to the posture decision
        return {'schema': SCHEMA, 'action': 'defer_to_posture', 'would_offload_for_speed': False, 'candidates': [],
                'reason': f'error:{type(exc).__name__}'}


def annotate(plan, task, context, policy, providers, available_fn=None, registry=None):
    """Shadow: record what speed-first routing would do in ``plan['speed_offload']``; never changes the plan."""
    d = decide(task, context, plan.get('resource_posture') or {}, policy, providers, available_fn, registry)
    current = [f"{c['provider']}/{c['model']}" for c in plan.get('candidates') or []]
    d['candidates'] = [f"{c['provider']}/{c['model']}" for c in d['candidates']]
    if d['action'] == 'parent':
        d['changes_plan'] = bool(current)
    else:
        d['changes_plan'] = d['action'] in ('offload_for_speed', 'offload_for_cost') and d['candidates'] != current
    d['shadow'] = True
    plan['speed_offload'] = d
    return plan
