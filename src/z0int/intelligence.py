"""Capability routing and receipt-backed idempotent execution on the host."""
import hashlib
import json
import math
import copy
import os
from pathlib import Path
import threading
import time
from . import paths
from .worker_routing import configuration, plan_route, execute_plan, validate_request, available
from .backends import create_backend, request_from_mapping, result_to_dict
from .functions.jev import assess_claim, ensure_credential

REGISTRY = Path(__file__).resolve().parents[2] / 'manifests/capabilities.v1.json'
_LAYA_LOCK = threading.Lock()
_LAYA = None


def validate(args):
    allowed={'harness','trace_id','parent_agent','function','task','context','state','expected_parent_tokens','expected_parent_ms','allow_remote','experimental','max_tokens','automatic','integration_instance','free_only'}
    if not isinstance(args,dict) or set(args)-allowed:
        raise ValueError('Unknown request fields')
    for key in ['harness','trace_id','parent_agent','function','task']:
        v=args.get(key)
        if not isinstance(v,str) or not v.strip() or len(v)>(24000 if key=='task' else 200):raise ValueError('Invalid '+key)
    for key in ['allow_remote','experimental','automatic','free_only']:
        if key in args and type(args[key]) is not bool:raise ValueError('Invalid '+key)
    for key in ['expected_parent_tokens','expected_parent_ms']:
        value=args.get(key,0)
        if type(value) not in (int,float) or not math.isfinite(value) or value<0:raise ValueError('Invalid '+key)
    if 'integration_instance' in args and (not isinstance(args['integration_instance'],str) or not 1<=len(args['integration_instance'])<=200):raise ValueError('Invalid integration instance')
    validate_request({'task':args['task'],'context':args.get('context',''),'parent_agent':args['parent_agent'],'max_tokens':args.get('max_tokens',1024)})
    if len(json.dumps(args,allow_nan=False))>40000:raise ValueError('Request too large')


def routing_snapshot(args):
    """Resolve configuration/availability outside the pure routing function."""
    from .automatic import evidenced
    from .provider_saturation import snapshot as provider_snapshot
    registry=json.loads(REGISTRY.read_text())
    policy,providers=configuration()
    return {'registry':registry,'registry_sha256':hashlib.sha256(REGISTRY.read_bytes()).hexdigest(),
        'policy':policy,'providers':providers,'provider_health':{p:provider_snapshot(p,policy) for p in ('jev','local')},
        'available_providers':{p for p,c in providers.items() if available(p,c)},
        'jev_available':bool(ensure_credential()) if args['function']=='evidence_sufficiency' else False,
        'evidenced_indices':{n for n,e in enumerate(registry['entries']) if evidenced(e)}}


def paid_jev_exception(policy, args, entry):
    """Operator authorization is limited to the existing typed Jev verifier."""
    return (
        policy.get('allow_paid_jev_verification') is True
        and args['function'] == 'evidence_sufficiency'
        and entry.get('function') == 'evidence_sufficiency'
        and entry.get('provider') == 'typesafe'
        and entry.get('model') == 'jev-1.13.0'
        and entry.get('route') == 'JEV_FUNCTION'
    )


def route(args, snapshot):
    """Pure decision: caller supplies immutable configuration and availability facts."""
    validate(args)
    registry=copy.deepcopy(snapshot['registry'])
    candidates=[e for e in registry['entries'] if e['function']==args['function']]
    policy=registry['delegation_policy']
    parent=lambda reason:{'kind':'PARENT_ONLY','reason':reason,'executed':False,'registry_sha256':snapshot['registry_sha256']}
    if args.get('automatic'):
        candidates=[entry for n,entry in enumerate(registry['entries']) if entry['function']==args['function'] and n in snapshot['evidenced_indices']]
        if not candidates:return parent('Automatic routing withheld: no eligible, integrity-checked function quality evidence')
    from .worker_routing import free_required, free_route
    if free_required(snapshot['policy'],args):
        candidates=[e for e in candidates if free_route(snapshot['policy'],e['provider'],e['model']) or paid_jev_exception(snapshot['policy'],args,e)]
        if not candidates:return parent('free_only: no validated zero-cost candidate with required function evidence')
    if not candidates:return parent('No evidenced capability registered')
    if args['function']=='evidence_sufficiency':
        if not args.get('allow_remote',False):return parent('Remote context transmission not authorized')
        if not isinstance(args.get('state'),dict) or set(args['state'])!={'question','evidence'} or not all(isinstance(v,str) and v for v in args['state'].values()):raise ValueError('Evidence contract needs question and evidence strings')
        if not snapshot['provider_health']['jev']['available']:return parent('Jev admission unavailable: '+snapshot['provider_health']['jev']['reason'])
        if not snapshot['jev_available']:return parent('Existing TypeSafe credential unavailable')
        selected=candidates[0]
    elif args['function']=='verification_needed':
        if not snapshot['provider_health']['local']['available']:return parent('Local admission unavailable: '+snapshot['provider_health']['local']['reason'])
        if not args.get('experimental',False):return parent('Laya n=2 provisional evidence; experimental opt-in required')
        if not isinstance(args.get('state'),dict) or not {'files_changed','tests_run','user_asked_ship'} <= set(args['state']):raise ValueError('Laya requires the evaluated structured verifier state')
        selected=candidates[0]
    else:
        if args.get('expected_parent_tokens',0)<=policy['tiny_parent_tokens_max']:
            return parent('DO_NOT_DELEGATE: tiny/unknown task size; observed tiny-task offload added 1347 uncached parent tokens')
        overhead=max(policy['default_overhead_ms'],policy['observed_workflow_overhead_ms'])
        if args.get('expected_parent_ms',0)<=overhead:
            return parent('DO_NOT_DELEGATE: expected parent latency does not exceed observed workflow overhead (30804ms); no measured benefit')
        policy_workers, providers=snapshot['policy'],snapshot['providers']
        task=args['task'] if args.get('allow_remote',False) else 'local-only '+args['task']
        plan=plan_route(task,policy_workers,providers,snapshot['available_providers'],function=args['function'])
        eligible=[e for e in candidates if e['eligible'] and (args.get('allow_remote',False) or e['local'])]
        plan['candidates']=[c for c in plan['candidates'] if any(e['provider']==c['provider'] and e['model']==c['model'] for e in eligible)]
        if not plan['candidates']:return parent('No available eligible execution candidate')
        selected=next(e for e in eligible if e['provider']==plan['candidates'][0]['provider'])
        return {'kind':selected['route'],'reason':'Task exceeds tiny threshold and estimated parent latency exceeds observed overhead; benefit remains unproven','entry':selected,'worker_plan':plan,'executed':False}
    return {'kind':selected['route'],'reason':('Exact evaluated choice contract; operator-authorized paid Jev verification exception' if paid_jev_exception(snapshot['policy'],args,selected) else 'Exact evaluated choice contract') if selected['route']=='JEV_FUNCTION' else 'Opt-in provisional verification_needed n=2; host-local serialized execution','entry':selected,'executed':False}


def _decision(args, selected):
    global _LAYA
    if selected['kind']=='JEV_FUNCTION':
        return assess_claim(args['state'])
    request=request_from_mapping({'state':args.get('state') or args['task'],'questions':[{'id':'decision','type':'boolean','instructions':'Does this turn require an explicit verification pass before responding?','criteria':{'false':'Safe to respond without extra verification','true':'Run verification before responding'}}]})
    with _LAYA_LOCK:
        if _LAYA is None:_LAYA=create_backend('laya_421m')
        return result_to_dict(_LAYA.evaluate(request))


def execute(args, selected, *, receipt_sink):
    if selected['kind']=='PARENT_ONLY':return {'ok':True,'route':selected,'executed':False,'output':None,'requires_parent':True}
    if selected['kind'] in ['LOCAL_MODEL','REMOTE_MODEL']:
        policy, providers=configuration();plan=selected['worker_plan'];plan['source']='z0intelligence.capabilities.v1'
        worker={k:args[k] for k in ['task','context','parent_agent','max_tokens','free_only'] if k in args}
        plan['harness']=args['harness'];plan['caller_trace_id']=args['trace_id']
        result=execute_plan(worker,policy,providers,plan,receipt_sink=receipt_sink)
        selected['executed']=result['ok']
        return {'ok':result['ok'],'route':selected,'executed':result['ok'],'worker':result,'output':result['output'],'requires_parent':not result['ok']}
    from .worker_routing import free_required, free_route
    policy, _ = configuration()
    paid_exception = paid_jev_exception(policy, args, selected['entry'])
    if free_required(policy, args) and not (paid_exception or free_route(policy, selected['entry']['provider'], selected['entry']['model'])):
        raise ValueError('free_only: typed function is not authorized')
    if paid_exception and not args.get('allow_remote', False):
        raise ValueError('Remote context transmission not authorized')
    call=hashlib.sha256((args['harness']+'\0'+args['trace_id']+'\0function').encode()).hexdigest()
    row={'trace_id':call,'session_id':args['parent_agent'],'capability_id':args['function'],'provider':selected['entry']['provider'],'model':selected['entry']['model'],'execution':'live','route':selected['kind'],'extra':{'harness':args['harness'],'parent_agent':args['parent_agent'],'caller_trace_id':args['trace_id'],'subagent_id':call,'subtask':args['task'][:120],'attempt_index':0,'status':'started','route_source':'z0intelligence.capabilities.v1','reason':selected['reason'],'physical_call_attempted':False}}
    if paid_exception:
        row['extra'].update(cost_policy='paid_jev_verification_exception', paid_execution_authorized=True, free_tier_validated=False)
    from .provider_saturation import LocalAdmission
    from .worker_routing import error_status
    admission=LocalAdmission()
    provider='jev' if selected['kind']=='JEV_FUNCTION' else 'local'
    permit=admission.acquire(provider,call,{'harness':args['harness'],'caller_trace_id':args['trace_id']})
    row['extra'].update(admission_token=permit['token'],inflight_at_admission=permit['inflight_at_admission'],cap=permit['cap'],capped=permit['capped'])
    if not permit['admitted']:
        row['extra'].update(status='capped' if permit['capped'] else 'rejected',admission_reason=permit['reason'])
        receipt_sink(row)
        return {'ok':False,'route':selected,'executed':False,'requires_parent':True,'fallback':'PARENT_ONLY','receipt':row}
    try:receipt_sink(row)
    except BaseException:
        admission.release(permit,0,0,attempted=False)
        raise
    start=time.monotonic();status=0
    try:
        row['extra']['physical_call_attempted']=True
        result=_decision(args,selected)
        status=200
        row['extra'].update(status='completed',physical_call_attempted=True,response_model=result.get('model'))
        row['latency_ms']=(time.monotonic()-start)*1000
        usage=result.get('diagnostics',{})
        for key in ['input_tokens','output_tokens']:
            if type(usage.get(key)) is int:row[key]=usage[key]
        row['extra']['output_sha256']=hashlib.sha256(json.dumps(result,sort_keys=True).encode()).hexdigest()
        receipt_sink(row);selected['executed']=True
        return {'ok':True,'route':selected,'executed':True,'output':result,'receipt':row}
    except Exception as exc:
        status=error_status(exc) or 0
        row['latency_ms']=(time.monotonic()-start)*1000;row['extra'].update(status='failed',error_type=type(exc).__name__)
        receipt_sink(row)
        return {'ok':False,'route':selected,'executed':False,'requires_parent':True,'fallback':'PARENT_ONLY','receipt':row}
    finally:
        admission.release(permit,status,(time.monotonic()-start)*1000)


def speed_annotate_route(args, selected, snapshot):
    """Shadow speed-first annotation on a capability route (z0int.speed_offload); never changes the route.

    The capability registry has no entry for host-local tailnet routes (groot), and plan_route only consults
    local_order for local-only tasks, so general work can never reach groot here. The speed annotation is
    keyed by task class, not by function/category, and records what speed-first routing would do."""
    try:
        from .posture import shadow_annotation
        try:
            ann=shadow_annotation('offload')
        except Exception as exc:
            ann={'available':False,'error':type(exc).__name__}
        from .speed_offload import decide
        avail=snapshot.get('available_providers') or set()
        d=decide(args['task'],args.get('context',''),ann,snapshot['policy'],snapshot['providers'],lambda p,c:p in avail)
        d['candidates']=[f"{c['provider']}/{c['model']}" for c in d['candidates']]
        d['route_kind']=selected.get('kind');d['shadow']=True
        selected['speed_offload']=d
    except Exception as exc:
        selected['speed_offload']={'action':'defer_to_posture','would_offload_for_speed':False,'shadow':True,'reason':f'error:{type(exc).__name__}'}
    return selected


def dispatch(args):
    """Execution and routing delegate all claim/ledger ownership to the authority."""
    from .dispatch_authority import run
    validate(args)
    def work(receipt_sink):
        snapshot=routing_snapshot(args)
        selected=route(args,snapshot)
        speed_annotate_route(args,selected,snapshot)
        return execute(args,selected,receipt_sink=receipt_sink)
    return run(args,work)
