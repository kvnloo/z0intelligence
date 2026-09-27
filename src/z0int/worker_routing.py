"""Deterministic bounded text-worker execution. A plan never counts as execution."""
from __future__ import annotations
import hashlib
import json
import math
import os
from pathlib import Path
import re
import sys
import time
import uuid
from .cognition.adapters.transport import OpenAICompatTransport, ServerConfig
from .receipt import DecisionReceipt, receipts_path

ROOT = Path(__file__).resolve().parents[2]
POLICY_PATH = ROOT / 'manifests/worker_routing.v1.json'
REGISTRY_PATH = POLICY_PATH
HERMES_ROOT = os.environ.get('Z0INT_HERMES_ROOT', '')
SYSTEM = 'Complete the bounded task using supplied context. Return your answer to the Codex parent. You have no filesystem, shell, or external tools.'


def configuration():
    policy = json.loads(POLICY_PATH.read_text())
    providers = policy['providers']
    return policy, providers


def oauth_module():
    if HERMES_ROOT and HERMES_ROOT not in sys.path:
        sys.path.insert(0, HERMES_ROOT)
    from hermes_cli import auth
    return auth


def credential_available(provider, config):
    if config.get('auth') == 'hermes-oauth':
        try:
            return bool(oauth_module().get_provider_auth_state('xai-oauth' if provider == 'grok' else 'nous'))
        except Exception:
            return False
    if config.get('auth') == 'none':
        return True
    return bool(os.environ.get(config['api_key_env']))


def available(provider, config):
    from .provider_saturation import snapshot
    policy,_=configuration()
    return credential_available(provider,config) and snapshot(provider)['available'] and (not free_required(policy) or free_route(policy,provider,policy['defaults'].get(provider)) is not None)


def free_route(policy, provider, model):
    """Exact measured route allowlist; credits and model-name guesses are not $0."""
    return next((entry for entry in policy.get('validated_free_routes', [])
                 if entry.get('provider') == provider and entry.get('model') == model
                 and entry.get('validated') is True and entry.get('price_usd') == 0
                 and entry.get('evidence_sha256')), None)


def free_required(policy, args=None):
    return policy.get('free_only') is True or (args or {}).get('free_only') is True


def require_free_route(policy, provider, model, args=None):
    entry = free_route(policy, provider, model)
    if free_required(policy, args) and entry is None:
        raise ValueError('free_only: provider/model lacks validated zero-cost evidence')
    return entry


def request_constraints(policy, provider, model, args=None):
    entry = require_free_route(policy, provider, model, args)
    return entry.get('request_constraints', {}) if entry else {}


def list_models():
    policy, providers = configuration()
    return {'policy_revision': policy['policy_revision'], 'registry': str(REGISTRY_PATH), 'policy': str(POLICY_PATH),
            'models': [{'provider': p, 'model': m['id'], 'credential_available': credential_available(p, c), 'available': available(p,c),
                        'default': m['id'] == policy['defaults'].get(p), 'execution_verified': False}
                       for p, c in providers.items() for m in c['models']]}


def validate_request(args, *, automatic=True):
    allowed = {'task', 'context', 'parent_agent', 'max_tokens', 'free_only'}
    if not automatic:
        allowed |= {'provider', 'model', 'reason'}
    if not isinstance(args, dict) or set(args) - allowed:
        raise ValueError('Unknown arguments; automatic routing accepts no provider/model overrides')
    for name, limit in [('task', 24000), ('parent_agent', 200)]:
        value = args.get(name)
        if not isinstance(value, str) or not value.strip() or len(value) > limit:
            raise ValueError(f'{name} must be nonempty text of at most {limit} characters')
    if 'free_only' in args and type(args['free_only']) is not bool:
        raise ValueError('free_only must be boolean')
    context = args.get('context', '')
    if not isinstance(context, str) or len(context) > 24000:
        raise ValueError('context must be text of at most 24000 characters')
    budget = args.get('max_tokens', 512)
    if type(budget) is not int or not 1 <= budget <= 2048:
        raise ValueError('max_tokens must be an integer from 1 to 2048')


def plan_route(task, policy, providers, available_providers=None, function=None):
    rule = next((r for r in policy['rules'] if re.search(r['pattern'], task, re.I)), None)
    category = rule['category'] if rule else 'general'
    if category=='local':
        primary='local';order=['local']
    else:
        family='structured' if function in policy.get('structured_functions',[]) or category=='structured' else 'text'
        order=list(policy.get('function_orders',{}).get(family,policy['fallback_order']))
        if policy.get('free_only'):
            order=list(policy.get('free_provider_order', []))
        primary=(order[0] if order else policy['default_provider']) if policy.get('free_only') else policy['default_provider'] if category=='general' else order[0]
        order=[primary]+order
    candidates, skipped = [], []
    for provider in dict.fromkeys(order):
        config = providers.get(provider)
        if free_required(policy) and not free_route(policy,provider,policy['defaults'].get(provider)):
            skipped.append({'provider':provider,'reason':'not_validated_free_route'});continue
        if policy.get('provider_caps',{}).get(provider,0) in (None,0):
            skipped.append({'provider':provider,'reason':'cap_zero_or_unmeasured'});continue
        if config and (provider in available_providers if available_providers is not None else available(provider, config)):
            candidates.append({'provider': provider, 'model': policy['defaults'][provider]})
        else:
            skipped.append({'provider': provider, 'reason': 'credentials_unavailable_or_provider_unhealthy_or_capped'})
    return {'category': category, 'primary_provider': primary, 'candidates': candidates[:policy['max_attempts']],
            'skipped': skipped, 'reason': f'task category {category}', 'policy_revision': policy['policy_revision']}


def messages_for(args):
    context = args.get('context', '')
    return [{'role': 'system', 'content': SYSTEM}, {'role': 'user', 'content': args['task'] +
            ('\n\nContext supplied by parent:\n' + context if context else '')}]


def estimate(text):
    return math.ceil(len(text.encode('utf-8')) / 4)


def credentials(provider, config):
    if config.get('auth') == 'hermes-oauth':
        auth = oauth_module()
        data = (auth.resolve_xai_oauth_runtime_credentials() if provider == 'grok'
                else auth.resolve_nous_runtime_credentials())
        return data['api_key'], data['base_url'].removesuffix('/v1')
    if config.get('auth') == 'none':
        return None, config['base_url']
    return os.environ[config['api_key_env']], config['base_url']


def token(usage, name):
    value = usage.get(name)
    return value if type(value) is int and value >= 0 else None


def error_status(exc):
    current = exc
    while current:
        code = getattr(current, 'code', None)
        if type(code) is int:
            return code
        current = current.__cause__
    return None


def execute_attempt(args, candidate, config, policy, plan, route_id, attempt_index, *, receipt_sink, admission):
    provider, model = candidate['provider'], candidate['model']
    free = require_free_route(policy, provider, model, args)
    call_id = uuid.uuid4().hex
    messages = messages_for(args)
    row = DecisionReceipt(trace_id=call_id, session_id=args['parent_agent'], capability_id='codex.delegated_text',
        provider=provider, model=model, route='model', execution='live', fallbacks=attempt_index,
        extra={'subtask': args['task'][:120], 'harness': plan.get('harness', 'codex'), 'caller_trace_id': plan.get('caller_trace_id'), 'parent_agent': args['parent_agent'], 'subagent_id': route_id,
               'task_trace_id': route_id, 'attempt_index': attempt_index, 'status': 'started',
               'route_source': plan['source'], 'execution_source': 'z0intelligence',
               'policy_revision': policy['policy_revision'], 'policy_sha256': hashlib.sha256(POLICY_PATH.read_bytes()).hexdigest(),
               'category': plan['category'], 'reason': plan['reason'], 'physical_call_attempted': False,
               'task_sha256': hashlib.sha256(args['task'].encode()).hexdigest(),
               'context_sha256': hashlib.sha256(args.get('context', '').encode()).hexdigest(),
               'usage_source': 'unknown', 'expected_cost': None})
    row.extra.update(free_only=free_required(policy,args),free_tier_validated=free is not None,
                     free_tier_evidence=free.get('evidence_sha256') if free else None,
                     validated_price_usd=free.get('price_usd') if free else None)
    from .quota_budget import reservation
    permit=admission.acquire(provider,call_id,{'harness':plan.get('harness','codex'),'caller_trace_id':plan.get('caller_trace_id'),'quota_model':model,'quota_reserved_tokens':reservation(args)})
    if permit.get('execution_policy'):
        row.extra.update(permit['execution_policy'])
    if permit.get('quota_budget'):row.extra['quota_budget']=permit['quota_budget']
    row.extra.update(admission_token=permit['token'],inflight_at_admission=permit['inflight_at_admission'],cap=permit['cap'],capped=permit['capped'])
    if not permit['admitted']:
        row.extra.update(status='capped' if permit['capped'] else 'rejected',admission_reason=permit['reason'])
        return {'ok':False,'output':'','receipt':receipt_sink(row)}
    try:receipt_sink(row)
    except BaseException:
        admission.release(permit,0,0,attempted=False)
        raise
    start = time.monotonic()
    transport_status=0
    retry_after=None
    output = ''
    transport = None
    try:
        key, base = credentials(provider, config)
        transport = OpenAICompatTransport(ServerConfig(base_url=base, model=model, api_key=key,
            timeout_s=policy['timeout_s'], runtime='api', extra_headers={'User-Agent': 'z0int-api-eval/1.0 (+https://github.com/kvnloo/z0intelligence)'}))
        row.extra['physical_call_attempted'] = True
        row.extra['physical_started_at'] = time.time()
        response = transport.chat(messages, max_tokens=args.get('max_tokens', 512), temperature=0, seed=None, extra_body=permit.get('request_constraints',request_constraints(policy,provider,model,args)))
        transport_status=200
        output = response.content
        if not isinstance(output, str):
            raise ValueError('Provider returned non-text content')
        finish = (response.raw.get('choices') or [{}])[0].get('finish_reason')
        complete = bool(output.strip()) and finish == 'stop' and not response.tool_call
        row.input_tokens = token(response.usage, 'prompt_tokens')
        row.output_tokens = token(response.usage, 'completion_tokens')
        row.cached_input_tokens = token(response.usage.get('prompt_tokens_details') or {}, 'cached_tokens')
        metered = row.input_tokens is not None and row.output_tokens is not None
        identified = bool(response.raw.get('model'))
        cost = response.usage.get('cost')
        row.extra['provider_reported_cost_usd'] = cost
        cost_violation = (free_required(policy,args) or row.extra.get('free_only')) and cost is not None and (type(cost) not in (int,float) or cost != 0)
        ok = complete and metered and identified and not cost_violation
        row.outcome = {'execution_completed': complete, 'source': 'codex_plugin'}
        row.extra.update(status='completed' if ok else ('failed' if cost_violation else 'completed_unmetered_or_unidentified' if complete else 'incomplete'),
                         response_model=response.raw.get('model'), response_id=response.raw.get('id'), finish_reason=finish,
                         usage_source='provider_response' if metered else 'missing',
                         output_sha256=hashlib.sha256(output.encode()).hexdigest())
        if ok:
            row.baseline_input_tokens = estimate(json.dumps(messages, ensure_ascii=False, separators=(',', ':')))
            row.baseline_output_tokens = estimate(output)
            row.estimated_frontier_tokens_avoided = row.baseline_input_tokens + row.baseline_output_tokens
            row.measured_frontier_tokens = 0  # This adapter never calls the Codex parent model.
            row.extra.update(baseline=policy['baseline'], savings_counted_on_winning_attempt_only=True,
                             delivery='output_in_mcp_result_not_independent_parent_ack')
    except Exception as exc:
        transport_status=error_status(exc) or 0
        cause=exc
        while cause:
            try:retry_after=float(cause.headers.get('Retry-After'))
            except (AttributeError,TypeError,ValueError):pass
            cause=cause.__cause__
        ok = False
        row.outcome = {'execution_completed': False, 'source': 'codex_plugin'}
        row.extra.update(status='failed', error_type=type(exc).__name__, http_status=error_status(exc))
    finally:
        if transport is not None:row.extra['quota_headers']=getattr(transport,'quota_headers',{})
        if row.extra.get('physical_call_attempted'):row.extra['physical_finished_at']=time.time()
        admission.release(permit,transport_status,(time.monotonic()-start)*1000,retry_after=retry_after,attempted=row.extra.get('physical_call_attempted',False))
    row.latency_ms = (time.monotonic() - start) * 1000
    # Do not return success if canonical receipt persistence fails.
    receipt = receipt_sink(row)
    return {'ok': ok, 'output': output, 'receipt': receipt}


def route_worker(args):
    from .dispatch_authority import run,identity
    request={**args,'harness':args.get('harness','codex'),'function':'cheap_bounded_worker'}
    identity(request)
    args={k:v for k,v in args.items() if k not in ('harness','trace_id')}
    validate_request(args)
    def work(sink):
        policy,providers=configuration()
        plan={**plan_route(args['task'],policy,providers),'source':'z0intelligence.task_rules',
              'harness':request['harness'],'caller_trace_id':request['trace_id']}
        return execute_plan(args,policy,providers,plan,receipt_sink=sink)
    return run(request,work)


def explicit_plan(args, policy, providers):
    validate_request(args, automatic=False)
    provider, model = args.get('provider'), args.get('model')
    if provider not in providers or model not in [m['id'] for m in providers[provider]['models']]:
        raise ValueError('Unknown provider/model')
    reason = args.get('reason')
    if not isinstance(reason, str) or not reason.strip() or len(reason) > 1000:
        raise ValueError('reason must be nonempty text of at most 1000 characters')
    return {'source': 'codex_explicit_selection', 'category': 'explicit', 'reason': reason,
            'primary_provider': provider, 'candidates': [{'provider': provider, 'model': model}], 'skipped': [],
            'policy_revision': policy['policy_revision']}


def dispatch_worker(request):
    from .dispatch_authority import run,identity
    identity(request)
    worker={k:v for k,v in request.items() if k not in ('harness','trace_id','function')}
    validate_request(worker,automatic=False)
    def work(sink):
        policy,providers=configuration()
        plan={**explicit_plan(worker,policy,providers),'harness':request['harness'],'caller_trace_id':request['trace_id']}
        return execute_plan(worker,policy,providers,plan,receipt_sink=sink)
    return run(request,work)


def delegate_worker(args):
    return dispatch_worker({**args,'harness':args.get('harness','codex'),'function':'cheap_bounded_worker'})


def execute_plan(args, policy, providers, plan, *, receipt_sink, receipt_location=None, admission=None):
    if admission is None:
        from .provider_saturation import LocalAdmission
        admission=LocalAdmission()
    route_id = uuid.uuid4().hex
    started = time.monotonic()
    attempts = []
    result = {'ok': False, 'output': ''}
    for index, candidate in enumerate(plan['candidates']):
        # Recheck at execution: stale or forged plans cannot introduce paid fallback.
        require_free_route(policy,candidate['provider'],candidate['model'],args)
        result = execute_attempt(args, candidate, providers[candidate['provider']], policy, plan, route_id, index, receipt_sink=receipt_sink, admission=admission)
        attempts.append(result['receipt'])
        if result['ok']:
            break
    return {'ok': result['ok'], 'subagent_id': route_id, 'route': plan, 'output': result['output'],
            'provider': attempts[-1]['provider'] if attempts else None,
            'model': attempts[-1]['model'] if attempts else None,
            'free_only':free_required(policy,args),'requires_parent':not result['ok'],
            'refusal_reason':None if result['ok'] else 'No candidate completed; no paid overflow',
            'attempts': attempts, 'receipt_path': receipt_location if receipt_location is not None else str(receipts_path()),
            'latency_ms': (time.monotonic() - started) * 1000,
            'input_tokens': sum(a.get('input_tokens') or 0 for a in attempts),
            'output_tokens': sum(a.get('output_tokens') or 0 for a in attempts),
            'unmetered_attempts': sum(a.get('input_tokens') is None or a.get('output_tokens') is None for a in attempts),
            'estimated_frontier_tokens_avoided': (attempts[-1].get('estimated_frontier_tokens_avoided') if result['ok'] else None),
            'baseline': policy['baseline'], 'note': 'Execution is not quality verification; savings use a declared text-size estimate, not a measured counterfactual.'}
