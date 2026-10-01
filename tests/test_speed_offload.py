"""Speed-first offload (shadow): class registry, decision table, guards, route_worker annotation."""
import hashlib
import json
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

from z0int import speed_offload as S
from z0int import worker_routing as r

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'benchmarks/speed_offload'))
import build_sets  # noqa: E402

POLICY = {'validated_free_routes': [{'provider': 'groot', 'model': 'qwen3-8b-q4km', 'validated': True,
                                     'price_usd': 0, 'evidence_sha256': 'x'}],
          'provider_caps': {'groot': 2}, 'defaults': {'groot': 'qwen3-8b-q4km'}}
PROVIDERS = {'groot': {'cohort': 'local', 'auth': 'none'}}
UP = lambda p, c: True  # noqa: E731
DOWN = lambda p, c: False  # noqa: E731


def registry(tmp_path, statuses=None, envelope=5000, tamper=False):
    ev = tmp_path / 'ev.json'
    ev.write_text('{"x": 1}')
    reg = S.load_registry()
    reg['evidence'] = {'path': str(ev), 'sha256': hashlib.sha256(b'{"x": 1}').hexdigest() + ('0' if tamper else '')}
    for name, st in (statuses or {}).items():
        reg['classes'][name]['equivalence'] = {'status': st, 'max_context_chars': envelope, 'speedup_p95': 10.0}
    return reg


def ann(posture='BURN', action='return_to_parent'):
    return {'available': True, 'factory_posture': posture, 'would': {'action': action}}


def test_bench_task_texts_classify_to_their_own_class():
    reg = S.load_registry()
    for cls, text in build_sets.TASKS.items():
        assert S.classify_task(text.format(question='Q?', options='a = b', limit=9), reg) == (cls, 'pattern_match')


@pytest.mark.parametrize('task', ['Write a haiku about routers', '', 'Summarize this output and extract json from it'])
def test_unknown_or_ambiguous_task_goes_to_parent(task, tmp_path):
    d = S.decide(task, '', ann(), POLICY, PROVIDERS, UP, registry(tmp_path))
    assert d['task_class'] == 'unknown' and d['action'] == 'parent' and not d['would_offload_for_speed']


def test_speed_qualified_offloads_even_under_burn(tmp_path):
    reg = registry(tmp_path, {'classify_file_type': 'speed_qualified'})
    d = S.decide(build_sets.TASKS['classify_file_type'], 'x' * 100, ann('BURN'), POLICY, PROVIDERS, UP, reg)
    assert d['action'] == 'offload_for_speed' and d['would_offload_for_speed'] is True
    assert d['candidates'] == [{'provider': 'groot', 'model': 'qwen3-8b-q4km'}]
    assert d['posture_action'] == 'return_to_parent'


@pytest.mark.parametrize('status', ['not_equivalent', 'unmeasured'])
def test_non_equivalent_defers_to_posture(status, tmp_path):
    reg = registry(tmp_path, {'classify_file_type': status})
    d = S.decide(build_sets.TASKS['classify_file_type'], '', ann('BURN'), POLICY, PROVIDERS, UP, reg)
    assert d['action'] == 'defer_to_posture' and not d['would_offload_for_speed']


@pytest.mark.parametrize('posture,action', [('OFFLOAD', 'offload_for_cost'), ('BURN', 'defer_to_posture'),
                                            ('BALANCED', 'defer_to_posture')])
def test_cost_eligible_only_widens_offload(posture, action, tmp_path):
    reg = registry(tmp_path, {'short_rewrite': 'cost_eligible'})
    task = build_sets.TASKS['short_rewrite'].format(limit=9)
    d = S.decide(task, 'x', ann(posture), POLICY, PROVIDERS, UP, reg)
    assert d['action'] == action and d['would_offload_for_speed'] is False


@pytest.mark.parametrize('kw,ctx,avail,reason', [
    ({'tamper': True}, 'x', UP, 'evidence_sha256_mismatch'),
    ({'envelope': 10}, 'x' * 11, UP, 'context_outside_measured_envelope'),
    ({}, 'x', DOWN, 'route_unavailable'),
])
def test_guards_fail_closed_to_posture(kw, ctx, avail, reason, tmp_path):
    reg = registry(tmp_path, {'classify_file_type': 'speed_qualified'}, **kw)
    d = S.decide(build_sets.TASKS['classify_file_type'], ctx, ann(), POLICY, PROVIDERS, avail, reg)
    assert (d['action'], d['reason']) == ('defer_to_posture', reason)


def test_route_must_still_be_validated_free(tmp_path):
    reg = registry(tmp_path, {'classify_file_type': 'speed_qualified'})
    pol = {**POLICY, 'validated_free_routes': []}
    d = S.decide(build_sets.TASKS['classify_file_type'], 'x', ann(), pol, PROVIDERS, UP, reg)
    assert d['reason'] == 'route_not_validated_free'


def test_decide_never_raises():
    d = S.decide('classify the file type', '', None, POLICY, PROVIDERS, UP, {})
    assert d['action'] == 'defer_to_posture' and d['reason'].startswith('error:')


def test_annotation_is_shadow_only(tmp_path):
    reg = registry(tmp_path, {'classify_file_type': 'speed_qualified'})
    plan = {'candidates': [{'provider': 'cerebras', 'model': 'm'}], 'skipped': [], 'resource_posture': ann()}
    S.annotate(plan, build_sets.TASKS['classify_file_type'], 'x', POLICY, PROVIDERS, UP, reg)
    assert plan['candidates'] == [{'provider': 'cerebras', 'model': 'm'}]
    so = plan['speed_offload']
    assert so['shadow'] is True and so['would_offload_for_speed'] is True and so['changes_plan'] is True
    assert so['candidates'] == ['groot/qwen3-8b-q4km']


def test_worker_routing_speed_annotate_fails_open():
    plan = {'candidates': [{'provider': 'cerebras', 'model': 'm'}]}
    with patch.object(S, 'annotate', side_effect=RuntimeError('boom')):
        r.speed_annotate(plan, 'classify the file type', '', POLICY, PROVIDERS)
    assert plan['candidates'] == [{'provider': 'cerebras', 'model': 'm'}]
    assert plan['speed_offload']['reason'] == 'error:RuntimeError'


def test_registry_shape():
    reg = S.load_registry()
    assert reg['local_route'] == {'provider': 'groot', 'model': 'qwen3-8b-q4km'}
    for name, c in reg['classes'].items():
        assert c['patterns'] and c['equivalence']['status'] in S.STATUSES
        if c['equivalence']['status'] != 'unmeasured':
            e = c['equivalence']
            assert e['margin'] == 0.10 and isinstance(e['max_context_chars'], int) and e['receipts']['local']


# --- why general work never reaches groot today (coordinator report: route_worker -> PARENT_ONLY) -----------

def groot_snapshot():
    """Manifest policy + a host-defined groot provider with a validated $0 route, as on the mbp."""
    import json as _json
    from z0int import intelligence as I
    policy = _json.loads(r.POLICY_PATH.read_text())
    providers = policy['providers']
    providers['groot'] = {'cohort': 'local', 'auth': 'none', 'base_url': 'http://x', 'host_defined': True,
                          'models': [{'id': 'qwen3-8b-q4km'}], 'worker_default_model': 'qwen3-8b-q4km'}
    policy['provider_caps']['groot'] = 2
    policy['defaults']['groot'] = 'qwen3-8b-q4km'
    policy['host_local_providers'] = ['groot']
    policy['local_order'] = ['groot', 'local']
    policy['validated_free_routes'].append({'provider': 'groot', 'model': 'qwen3-8b-q4km', 'validated': True,
                                            'price_usd': 0, 'evidence_sha256': 'x'})
    registry = _json.loads(I.REGISTRY.read_text())
    return I, {'registry': registry, 'registry_sha256': 'r', 'policy': policy, 'providers': providers,
               'provider_health': {}, 'available_providers': {'groot', 'local'}, 'jev_available': False,
               'evidenced_indices': set(range(len(registry['entries'])))}


def test_cause_capability_registry_has_no_groot_entry_so_free_only_returns_parent():
    I, snap = groot_snapshot()
    assert snap['policy']['free_only'] is True
    assert not any(e['provider'] == 'groot' for e in snap['registry']['entries'])
    base = {'harness': 'claude-code', 'trace_id': 't', 'parent_agent': 'p',
            'task': build_sets.TASKS['summarize_tool_output'], 'expected_parent_tokens': 5000,
            'expected_parent_ms': 60000}
    # The observed reason: a function name the registry does not know (empty candidate list hits the free_only
    # check first) or a function with no validated $0 entry.
    for extra in ({'function': 'verification_needed'}, {'function': 'summarize'}, {'function': 'text_worker'}):
        sel = I.route({**base, **extra}, snap)
        assert sel['kind'] == 'PARENT_ONLY' and 'no validated zero-cost candidate' in sel['reason'], extra
    # A known text function gets past free_only (manifest groq route) but no entry is eligible, and none is groot.
    sel = I.route({**base, 'function': 'summarization'}, snap)
    assert sel['kind'] == 'PARENT_ONLY' and sel['reason'] == 'No available eligible execution candidate'


def test_cause_plan_route_only_uses_local_order_for_local_only_tasks():
    _, snap = groot_snapshot()
    pol = {**snap['policy'], 'free_only': False}
    general = r.plan_route('Summarize this git commit output', pol, snap['providers'], {'groot', 'local', 'cerebras'})
    assert general['category'] != 'local' and all(c['provider'] != 'groot' for c in general['candidates'])
    local = r.plan_route('local-only summarize', pol, snap['providers'], {'groot', 'local'})
    assert local['candidates'][0]['provider'] == 'groot'


def test_speed_annotation_reaches_groot_regardless_of_function_or_category(tmp_path):
    I, snap = groot_snapshot()
    args = {'task': build_sets.TASKS['summarize_tool_output'], 'context': 'x'}
    sel = {'kind': 'PARENT_ONLY', 'reason': 'free_only: no validated zero-cost candidate'}
    reg = registry(tmp_path, {'summarize_tool_output': 'speed_qualified'})
    with patch.object(S, 'load_registry', return_value=reg):
        I.speed_annotate_route(args, sel, snap)
    assert sel['kind'] == 'PARENT_ONLY'  # shadow: route unchanged
    so = sel['speed_offload']
    assert so['would_offload_for_speed'] is True and so['candidates'] == ['groot/qwen3-8b-q4km'] and so['shadow']
    # same for the worker_routing path, whose general-category plan has no groot candidate
    plan = {**r.plan_route(args['task'], {**snap['policy'], 'free_only': False}, snap['providers'], {'cerebras'}),
            'resource_posture': ann('BURN')}
    S.annotate(plan, args['task'], 'x', snap['policy'], snap['providers'], lambda p, c: True, reg)
    assert plan['speed_offload']['would_offload_for_speed'] and plan['speed_offload']['changes_plan']
