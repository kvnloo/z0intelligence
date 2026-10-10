"""Deterministic bounded text-worker execution. A plan never counts as execution."""
from __future__ import annotations
import hashlib
import json
import math
import os
from pathlib import Path
import re
import sys
import subprocess
import time
import uuid
from .cognition.adapters.transport import OpenAICompatTransport, ServerConfig
from .receipt import DecisionReceipt, receipts_path

ROOT = Path(__file__).resolve().parents[2]
POLICY_PATH = ROOT / 'manifests/worker_routing.v1.json'
REGISTRY_PATH = POLICY_PATH
HERMES_ROOT = os.environ.get('Z0INT_HERMES_ROOT', '')
SYSTEM = 'Complete the bounded task using supplied context. Return your answer to the Codex parent. You have no filesystem, shell, or external tools.'


def _host_config():
    try:
        from . import paths
        return json.loads((paths.home() / 'config' / 'worker_routing.local.json').read_text())
    except (OSError, ValueError, ImportError):
        return {}


def _host_overrides():
    """Per-host endpoint for keyless local providers: ~/.z0int/config/worker_routing.local.json.

    Only providers the manifest marks cohort=local and auth=none may be repointed, so a
    host file can never redirect a keyed provider (and its credential) to another URL.
    """
    return _host_config().get('providers') or {}


def _host_local_order(providers):
    """Host-chosen order for local-only work (e.g. a faster tailnet GPU box before this host's
    own model). Only keyless local providers are kept, so it can never add a remote fallback."""
    order = _host_config().get('local_order')
    if not isinstance(order, list):
        return None
    kept = [n for n in order if isinstance(n, str) and (providers.get(n) or {}).get('cohort') == 'local'
            and (providers.get(n) or {}).get('auth') == 'none']
    return list(dict.fromkeys(kept)) or None


HOST_PROVIDER_NAME = re.compile(r'^[a-z][a-z0-9_-]{0,31}$')
HOST_PROVIDER_MAX_CAP = 8


def _host_local_provider(name, override):
    """A keyless local provider the manifest does not know (e.g. a tailnet GPU box).

    The host file must declare it cohort=local and auth=none; the result is built from
    whitelisted keys only, so it never carries a credential reference. Its cap is taken
    from the override (default 1, clamped to HOST_PROVIDER_MAX_CAP).
    """
    if (not HOST_PROVIDER_NAME.match(name) or override.get('cohort') != 'local' or override.get('auth') != 'none'
            or not isinstance(override.get('base_url'), str) or not override['base_url'].startswith('http')
            or not isinstance(override.get('worker_default_model'), str) or not isinstance(override.get('models'), list)):
        return None
    cap = override.get('cap', 1)
    cap = min(cap, HOST_PROVIDER_MAX_CAP) if type(cap) is int and cap > 0 else 1
    return {'cohort': 'local', 'auth': 'none', 'base_url': override['base_url'], 'models': override['models'],
            'worker_default_model': override['worker_default_model'], 'host_defined': True}, cap


def configuration():
    policy = json.loads(POLICY_PATH.read_text())
    providers = policy['providers']
    for name, override in _host_overrides().items():
        if not isinstance(override, dict):
            continue
        if name not in providers:
            added = _host_local_provider(name, override)
            if not added:
                continue
            providers[name], cap = added
            policy.setdefault('provider_caps', {})[name] = cap
            policy.setdefault('defaults', {})[name] = override['worker_default_model']
            policy.setdefault('host_local_providers', []).append(name)
        base = providers.get(name)
        if not base or base.get('cohort') != 'local' or base.get('auth') != 'none':
            continue
        merged = {**base, **{k: v for k, v in override.items() if k in ('base_url', 'models', 'worker_default_model')}}
        providers[name] = merged
        if 'worker_default_model' in override and isinstance(policy.get('defaults'), dict):
            policy['defaults'][name] = override['worker_default_model']
        # Host-validated $0 routes for local hardware: evidence file must exist and match its sha256.
        for route in override.get('validated_free_routes') or []:
            if _local_route_evidenced(name, route):
                policy.setdefault('validated_free_routes', []).append({**route, 'provider': name})
    local_order = _host_local_order(providers)
    if local_order:
        policy['local_order'] = local_order
    return policy, providers


def _local_route_evidenced(provider, route):
    import hashlib
    try:
        path = Path(route['evidence_path']).expanduser()
        ok = hashlib.sha256(path.read_bytes()).hexdigest() == route['evidence_sha256']
    except (KeyError, OSError, TypeError):
        return False
    return (ok and route.get('validated') is True and route.get('price_usd') == 0
            and isinstance(route.get('model'), str) and route.get('provider', provider) == provider)


def hermes_root() -> Path | None:
    env = os.environ.get('Z0INT_HERMES_ROOT') or os.environ.get('HERMES_ROOT') or HERMES_ROOT
    if env:
        path = Path(env).expanduser()
        if (path / 'hermes_cli').is_dir():
            return path
    home = Path.home() / '.hermes' / 'hermes-agent'
    if (home / 'hermes_cli').is_dir():
        return home
    return None


def hermes_python() -> Path | None:
    env = os.environ.get('Z0INT_HERMES_PYTHON')
    if env and Path(env).expanduser().is_file():
        return Path(env).expanduser()
    root = hermes_root()
    if root is None:
        return None
    candidate = root / 'venv' / 'bin' / 'python'
    return candidate if candidate.is_file() else None


_OAUTH_CACHE: dict[str, tuple[float, dict]] = {}
_OAUTH_SCRIPT = (
    'import json,sys\n'
    'sys.path.insert(0, sys.argv[1])\n'
    'from hermes_cli.auth_nous import resolve_nous_runtime_credentials\n'
    'from hermes_cli.auth_xai import resolve_xai_oauth_runtime_credentials\n'
    'provider=sys.argv[2]\n'
    'data=resolve_xai_oauth_runtime_credentials() if provider=="grok" else resolve_nous_runtime_credentials()\n'
    'print(json.dumps({"ok":True,"api_key":data.get("api_key") or "","base_url":data.get("base_url") or ""}))\n'
)


def resolve_hermes_oauth(provider: str) -> dict:
    """Ask the Hermes venv for OAuth credentials. Do not import hermes_cli here.

    hermes_cli.auth pulls ruamel through hermes_yaml. The bridge interpreter does
    not ship that package; the Hermes venv does.
    """
    cached = _OAUTH_CACHE.get(provider)
    if cached and time.time() - cached[0] < 60:
        return cached[1]
    py, root = hermes_python(), hermes_root()
    if py is None or root is None:
        raise RuntimeError('hermes oauth runtime missing; set Z0INT_HERMES_PYTHON to the Hermes venv')
    proc = subprocess.run(
        [str(py), '-c', _OAUTH_SCRIPT, str(root), 'grok' if provider == 'grok' else 'nous'],
        capture_output=True, text=True, timeout=20, check=False,
    )
    if proc.returncode != 0 or not proc.stdout.strip():
        detail = (proc.stderr or '').strip().splitlines()
        tail = detail[-1][:180] if detail else f'exit_{proc.returncode}'
        raise RuntimeError('hermes oauth resolve failed: ' + tail)
    data = json.loads(proc.stdout.strip().splitlines()[-1])
    if not data.get('api_key') or not data.get('base_url'):
        raise RuntimeError('hermes oauth resolve returned no credential')
    _OAUTH_CACHE[provider] = (time.time(), data)
    return data


def oauth_module():
    raise RuntimeError('import hermes_cli.auth in-process; use resolve_hermes_oauth')


def credential_available(provider, config):
    if config.get('auth') == 'hermes-oauth':
        try:
            return bool(resolve_hermes_oauth('grok' if provider == 'grok' else 'nous').get('api_key'))
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
    if provider in (policy.get('free_policy_exclusions') or {}):
        return None
    return next((entry for entry in policy.get('validated_free_routes', [])
                 if entry.get('provider') == provider and entry.get('model') == model
                 and entry.get('validated') is True and entry.get('price_usd') == 0
                 and entry.get('evidence_sha256')), None)


def free_required(policy, args=None):
    return policy.get('free_only') is True or (args or {}).get('free_only') is True

def blocked_provider(provider):
    name=str(provider or '').lower()
    blocked={p.lower() for p in (configuration()[0].get('sidestep_blocked_providers') or [])}
    return name in blocked or name.startswith('cursor') or name in {'cursor','paid-api','openai-codex','codex','xai','xai-oauth','grok'}


def sidestep_candidates(policy, tried, args=None):
    out=[]
    for item in policy.get('sidestep_order') or []:
        provider, model = item.get('provider'), item.get('model')
        if not isinstance(provider, str) or not isinstance(model, str) or provider in tried or blocked_provider(provider):
            continue
        # Free-only: a 402/429 never opens a route without validated zero-cost evidence.
        if free_required(policy, args) and free_route(policy, provider, model) is None:
            continue
        out.append({'provider': provider, 'model': model, 'sidestep': True})
    return out



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
        # Host-defined keyless local providers (e.g. a tailnet GPU box) are the offload
        # tier behind this host's own local model; still no remote fallback.
        order=policy.get('local_order') or ['local']+list(policy.get('host_local_providers',[]))
        primary=order[0]
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
        data = resolve_hermes_oauth(provider)
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


def record_reported_cost(row, body, free_only):
    """Read usage.cost off a 200 response body and judge it, before any content-shape check.

    Under free-only a cost field that is present must be exactly a finite zero. Null, negative,
    non-numeric, boolean, NaN and Infinity are violations. An absent cost field (or no usage
    object to hold one) is not a violation. Returns True on a violation.
    """
    usage = body.get('usage') if isinstance(body, dict) else None
    present = isinstance(usage, dict) and 'cost' in usage
    cost = usage['cost'] if present else None
    number = type(cost) is int or (type(cost) is float and math.isfinite(cost))  # bool is not a number here
    # The receipt stays strict JSON and never stores a provider-chosen non-number.
    row.extra['provider_reported_cost_usd'] = cost if number or not present else (
        'unreadable:' + (repr(cost) if type(cost) is float else type(cost).__name__))
    violation = bool(free_only) and present and not (number and cost == 0)
    if violation:
        row.extra['free_only_violation'] = 'provider_reported_nonzero_cost'
    return violation


def execute_attempt(args, candidate, config, policy, plan, route_id, attempt_index, *, receipt_sink, admission, sidestep=False):
    provider, model = candidate['provider'], candidate['model']
    if blocked_provider(provider):
        raise ValueError('paid parent is not a test or sidestep route: '+provider)
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
               'resource_posture': plan.get('resource_posture'),
               'task_sha256': hashlib.sha256(args['task'].encode()).hexdigest(),
               'context_sha256': hashlib.sha256(args.get('context', '').encode()).hexdigest(),
               'usage_source': 'unknown', 'expected_cost': None})
    row.extra.update(free_only=free_required(policy,args),free_tier_validated=free is not None,
                     free_tier_evidence=free.get('evidence_sha256') if free else None,
                     validated_price_usd=free.get('price_usd') if free else None,
                     sidestep=sidestep)
    from .quota_budget import reservation
    permit=admission.acquire(provider,call_id,{'harness':plan.get('harness','codex'),'caller_trace_id':plan.get('caller_trace_id'),'quota_model':model,'quota_reserved_tokens':reservation(args),'sidestep':sidestep})
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
        constraints = request_constraints(policy, provider, model, args)
        response = transport.chat(messages, max_tokens=args.get('max_tokens', 512), temperature=0, seed=None, extra_body=permit.get('request_constraints', constraints))
        transport_status=200
        cost_violation = record_reported_cost(row, {'usage': response.usage}, free_required(policy,args) or row.extra.get('free_only'))
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
        # A 200 body the transport parsed and then rejected for its shape still reports its cost.
        body = getattr(transport, 'last_body', None)
        if body is not None and 'provider_reported_cost_usd' not in row.extra:
            record_reported_cost(row, body, free_required(policy,args) or row.extra.get('free_only'))
    finally:
        if transport is not None:row.extra['quota_headers']=getattr(transport,'quota_headers',{})
        if row.extra.get('physical_call_attempted'):row.extra['physical_finished_at']=time.time()
        admission.release(permit,transport_status,(time.monotonic()-start)*1000,retry_after=retry_after,attempted=row.extra.get('physical_call_attempted',False))
    row.latency_ms = (time.monotonic() - start) * 1000
    # Do not return success if canonical receipt persistence fails.
    receipt = receipt_sink(row)
    return {'ok': ok, 'output': output, 'receipt': receipt, 'cost_violation': bool(row.extra.get('free_only_violation'))}


def posture_annotate(plan, route_kind='offload'):
    """Shadow resource posture on a routing decision (z0int.posture). Annotates, never changes the plan,
    unless ~/.z0int/config/posture.local.json sets posture_enforce: true (default false). Fail-open."""
    try:
        from .posture import shadow_annotation
        ann = shadow_annotation(route_kind)
    except Exception as exc:
        ann = {'available': False, 'error': type(exc).__name__, 'enforce': False}
    plan['resource_posture'] = ann
    if ann.get('enforce') is True and ann.get('available') and not ann.get('agrees') and route_kind == 'offload':
        # Enforced BURN: frontier surplus perishes, so hand bounded work back to the parent instead of offloading.
        plan['skipped'] = plan.get('skipped', []) + [{'provider': c['provider'], 'reason': 'posture_burn_parent_should_absorb'}
                                                     for c in plan.get('candidates', [])]
        plan['candidates'] = []
        ann['enforced'] = True
    return plan


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
        posture_annotate(plan)
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
    # Only a 402/429 inside this call makes a sidestep; a plan cannot mark its own candidates.
    queue=[{k:v for k,v in candidate.items() if k!='sidestep'} for candidate in plan['candidates']]
    tried=set()
    limit=int(policy.get('max_attempts',3))+len(policy.get('sidestep_order') or [])
    sidestep_http=set(policy.get('sidestep_http') or [402,429])
    while queue and len(attempts)<limit:
        candidate=queue.pop(0)
        if candidate['provider'] in tried or candidate['provider'] not in providers:
            continue
        sidestep=candidate.get('sidestep') is True
        if blocked_provider(candidate['provider']):
            # Never called. A plan that names one under free-only is still refused as a
            # whole, exactly as before the provider was blocked.
            if not sidestep:
                require_free_route(policy,candidate['provider'],candidate['model'],args)
            continue
        tried.add(candidate['provider'])
        require_free_route(policy,candidate['provider'],candidate['model'],args)
        result = execute_attempt(args, candidate, providers[candidate['provider']], policy, plan, route_id, len(attempts), receipt_sink=receipt_sink, admission=admission, sidestep=sidestep)
        attempts.append(result['receipt'])
        if result['ok'] or result.get('cost_violation'):
            # A reported cost under free-only ends the call: no retry after money was spent.
            break
        status=(result['receipt'].get('extra') or {}).get('http_status')
        if status in sidestep_http:
            queued={item['provider'] for item in queue}
            for extra in sidestep_candidates(policy, tried|queued, args):
                queue.append(extra)
    return {'ok': result['ok'], 'subagent_id': route_id, 'route': plan, 'output': result['output'],
            'provider': attempts[-1]['provider'] if attempts else None,
            'model': attempts[-1]['model'] if attempts else None,
            'free_only':free_required(policy,args),'requires_parent':not result['ok'],
            'refusal_reason':None if result['ok'] else ('free_only violated: provider reported nonzero cost; call failed closed' if result.get('cost_violation')
                                                        else 'Resource posture BURN (enforced): frontier surplus perishes; parent should absorb'
                                                        if (plan.get('resource_posture') or {}).get('enforced') else 'No candidate completed; no paid overflow'),
            'attempts': attempts, 'receipt_path': receipt_location if receipt_location is not None else str(receipts_path()),
            'latency_ms': (time.monotonic() - started) * 1000,
            'input_tokens': sum(a.get('input_tokens') or 0 for a in attempts),
            'output_tokens': sum(a.get('output_tokens') or 0 for a in attempts),
            'unmetered_attempts': sum(a.get('input_tokens') is None or a.get('output_tokens') is None for a in attempts),
            'estimated_frontier_tokens_avoided': (attempts[-1].get('estimated_frontier_tokens_avoided') if result['ok'] else None),
            'baseline': policy['baseline'], 'note': 'Execution is not quality verification; savings use a declared text-size estimate, not a measured counterfactual.'}
