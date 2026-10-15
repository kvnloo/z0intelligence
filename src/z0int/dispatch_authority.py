"""Single host authority for dispatch claims and canonical receipt persistence.

The ledger is the durable state. A started claim is never reassigned, including
following process death. No lease expiry can authorize a second execution.
"""
from contextlib import contextmanager
import fcntl
import hashlib
import json
import secrets
from . import paths
from .receipt import append_receipt, find_receipt, receipts_path, DecisionReceipt


def digest(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,allow_nan=False,separators=(',',':')).encode()).hexdigest()


def identity(request):
    for name in ('harness','trace_id','parent_agent','function'):
        if not isinstance(request.get(name),str) or not 1<=len(request[name])<=200:
            raise ValueError('Invalid '+name)
    return hashlib.sha256((request['harness']+'\0'+request['trace_id']).encode()).hexdigest()


def fingerprint(request):
    # Keep the exact fingerprint serialization used before this extraction.
    return hashlib.sha256(json.dumps(request,sort_keys=True,allow_nan=False).encode()).hexdigest()


@contextmanager
def locked(key):
    directory=paths.home()/'run/intelligence'
    directory.mkdir(parents=True,exist_ok=True)
    with (directory/(key+'.lock')).open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX)
        yield


def previous(request,key):
    ledger=receipts_path()
    row=None
    if ledger.exists():
        with ledger.open() as stream:
            fcntl.flock(stream,fcntl.LOCK_SH)
            for line in stream:
                candidate=json.loads(line)  # Corruption fails closed.
                if candidate.get('trace_id')=='dispatch-'+key:row=candidate
    if row and row['extra']['request_sha256']!=fingerprint(request):
        raise ValueError('trace_id reused for different request')
    return row


def replay(row):
    if row['extra'].get('status')=='completed' and 'result' in row['extra']:
        return {**row['extra']['result'],'replayed':True}
    return {'ok':False,'executed':False,'requires_parent':True,
        'execution_status':'uncertain',
        'reason':'Previous dispatch interrupted or uncertain; not re-executed'}


def started(request,key,owner=None,admission_id=None):
    row={'trace_id':'dispatch-'+key,'session_id':request['parent_agent'],
        'capability_id':'intelligence.dispatch','execution':'orchestration',
        'extra':{'harness':request['harness'],'caller_trace_id':request['trace_id'],
                 'request_sha256':fingerprint(request),'status':'started'}}
    if request.get('caller_request_sha256'):
        row['extra']['caller_request_sha256']=request['caller_request_sha256']
    if owner:row['extra']['authority_owner_sha256']=hashlib.sha256(owner.encode()).hexdigest()
    if admission_id:row['extra']['aodl_admission_receipt_id']=admission_id
    return append_receipt(row)


def finished(row,result,request):
    result={**result,'dispatch_receipt_id':row['trace_id']}
    selected=result.get('route',{})
    entry=selected.get('entry',{})
    row={**row,'extra':{**row['extra'],'status':'completed','result':result,
        'function':request['function'],'automatic':request.get('automatic',False),
        'integration_instance':request.get('integration_instance'),
        'chosen_kind':selected.get('kind'),'routing_reason':selected.get('reason'),
        'evidence':entry.get('eval_source')}}
    append_receipt(row)
    return result


def run(request, work):
    """In-process compatibility path: preserve blocking same-key serialization."""
    key=identity(request)
    with locked(key):
        row=previous(request,key)
        if row:return replay(row)
        row=started(request,key)
        # BaseException or persistence failure leaves the durable started row.
        result=work(append_receipt)
        return finished(row,result,request)


def validate_remote(request, *, enforce_free=False):
    from .worker_routing import validate_request, configuration, explicit_plan
    allowed={'harness','trace_id','function','parent_agent','task','context','provider','model','reason','max_tokens','free_only','aodl'}
    if not isinstance(request,dict) or set(request)-allowed:raise ValueError('Invalid remote request fields')
    identity(request)
    if request['function']!='cheap_bounded_worker':raise ValueError('Remote executor only supports bounded text workers')
    worker={k:v for k,v in request.items() if k not in ('harness','trace_id','function','aodl')}
    validate_request(worker,automatic=False)
    policy,providers=configuration()
    plan=explicit_plan(worker,policy,providers)
    config=providers[request['provider']]
    if config.get('auth','environment')!='environment' or not isinstance(config.get('api_key_env'),str) or not config['base_url'].startswith('https://'):
        raise ValueError('Only remote environment-credential providers are supported')
    if enforce_free:
        from .worker_routing import require_free_route
        require_free_route(policy,request['provider'],request['model'],request)
    return worker,policy,providers,plan


def claim(request,owner,*,require_aodl=False):
    if not isinstance(owner,str) or len(owner)!=64:raise ValueError('Invalid owner capability')
    key=identity(request)
    with locked(key):
        row=previous(request,key)
        if row:return {'claimed':False,'result':replay(row)}
        validate_remote(request,enforce_free=True)
        from . import aodl_dispatch
        admission=aodl_dispatch.ensure(request,key,fingerprint(request),required=require_aodl)
        if admission is not None and not aodl_dispatch.is_allowed(admission):
            return {'claimed':False,'result':aodl_dispatch.denied_result(admission)}
        row=started(request,key,owner,admission['trace_id'] if admission is not None else None)
        out={'claimed':True,'dispatch_receipt_id':row['trace_id']}
        if admission is not None:out['aodl_admission_receipt_id']=admission['trace_id']
        return out


def owned(request,owner):
    row=previous(request,identity(request))
    if not row or not isinstance(owner,str) or not secrets.compare_digest(row['extra'].get('authority_owner_sha256',''),hashlib.sha256(owner.encode()).hexdigest()):
        raise ValueError('Not the claim owner')
    return row


def events(dispatch_id):
    rows=[]
    with receipts_path().open() as stream:
        fcntl.flock(stream,fcntl.LOCK_SH)
        for line in stream:
            # Corruption must fail closed; never silently lose an execution marker.
            row=json.loads(line)
            if row.get('extra',{}).get('authority_dispatch_id')==dispatch_id:rows.append(row)
    return rows


def emit(request,owner,index,payload):
    if type(index) is not int or index<0 or not isinstance(payload,dict):raise ValueError('Invalid receipt event')
    key=identity(request)
    with locked(key):
        dispatch=owned(request,owner)
        existing=events(dispatch['trace_id'])
        submitted=digest(payload)
        if index<len(existing):
            prior=existing[index]
            if prior['extra']['authority_payload_sha256']!=submitted:raise ValueError('Receipt event reused with different payload')
            return prior
        if index!=len(existing) or dispatch['extra']['status']!='started':raise ValueError('Out-of-order or completed dispatch')
        extra=payload.get('extra',{})
        status=extra.get('status')
        trace=payload.get('trace_id')
        if not isinstance(trace,str) or trace.startswith(('dispatch-','consume-')):raise ValueError('Invalid physical receipt identity')
        if payload.get('provider')!=request['provider'] or payload.get('model')!=request['model']:raise ValueError('Provider/model mismatch')
        if extra.get('harness')!=request['harness'] or extra.get('caller_trace_id')!=request['trace_id']:raise ValueError('Harness identity mismatch')
        prior=[r for r in existing if r['trace_id']==trace]
        from .provider_saturation import _rows
        permit_rows=[r for r in _rows() if r['trace_id']==extra.get('admission_token')]
        if not permit_rows or permit_rows[0]['extra'].get('permit_dispatch_id')!=dispatch['trace_id'] or permit_rows[0]['extra'].get('permit_owner_sha256')!=dispatch['extra']['authority_owner_sha256']:
            raise ValueError('Physical receipt requires an authority-owned admission')
        if permit_rows[0]['provider']!=request['provider']:raise ValueError('Admission provider mismatch')
        if status=='started' and permit_rows[-1]['extra']['status']!='acquired':raise ValueError('No active provider admission')
        if status in ('capped','rejected'):
            if prior or existing or permit_rows[0]['extra']['status'] not in ('capped','rejected'):raise ValueError('Invalid rejected admission receipt')
        elif status=='started':
            if prior or existing:raise ValueError('Explicit worker permits one physical attempt')
        elif status in ('completed','failed','incomplete','completed_unmetered_or_unidentified'):
            if permit_rows[-1]['extra']['status']!='released':raise ValueError('Admission release is not durable')
            if len(prior)!=1 or prior[0]['extra']['status']!='started':raise ValueError('Physical call has no unique start')
            if extra.get('admission_token')!=prior[0]['extra'].get('admission_token'):raise ValueError('Physical admission changed')
        else:raise ValueError('Invalid physical status')
        if payload.get('schema')!='z0int.decision_receipt.v1':raise ValueError('Noncanonical receipt schema')
        from .worker_routing import configuration, require_free_route, free_required
        if status=='started':
            policy,_=configuration()
            require_free_route(policy,request['provider'],request['model'],request)
        extra={**extra,**permit_rows[0]['extra']['execution_policy']}
        cost=extra.get('provider_reported_cost_usd')
        if status=='completed' and extra.get('free_only') and cost is not None and (type(cost) not in (int,float) or cost!=0):
            extra.update(status='failed',cost_policy_violation=True)
        row={**payload,'extra':{**extra,'authority_dispatch_id':dispatch['trace_id'],
            'authority_event_index':index,'authority_payload_sha256':submitted}}
        return append_receipt(row)


def complete(request,owner,result):
    key=identity(request)
    with locked(key):
        row=owned(request,owner)
        if row['extra']['status']=='completed':
            saved=row['extra']['result']
            if {k:v for k,v in saved.items() if k!='dispatch_receipt_id'}!=result:raise ValueError('Completion reused with different result')
            return {**saved,'replayed':True}
        physical=events(row['trace_id'])
        if not (len(physical)==2 and physical[-1]['extra']['status']!='started' or len(physical)==1 and physical[0]['extra']['status'] in ('capped','rejected')):raise ValueError('Physical execution is incomplete or uncertain')
        if result.get('ok') and hashlib.sha256(result.get('output','').encode()).hexdigest()!=physical[-1]['extra'].get('output_sha256'):raise ValueError('Output does not match physical receipt')
        if result.get('attempts')!=[physical[-1]]:raise ValueError('Result must reference canonical authority receipt')
        if result.get('ok') is not (physical[-1]['extra']['status']=='completed'):raise ValueError('Execution status mismatch')
        return finished(row,result,request)


def rpc(operation,args):
    if not isinstance(args,dict):raise ValueError('Invalid authority request')
    version=args.get('protocol_version')
    if version not in (2,3):raise ValueError('Executor protocol v2 or v3 is required')
    request=args['request'];owner=args['owner']
    if operation=='claim':return claim(request,owner,require_aodl=version==3)
    if operation in ('acquire','release'):
        from .provider_saturation import acquire,release
        with locked(identity(request)):
            row=owned(request,owner)
            if row['extra']['status']!='started':raise ValueError('Dispatch is not active')
            context={'permit_dispatch_id':row['trace_id'],'permit_owner_sha256':row['extra']['authority_owner_sha256'],
                'harness':request['harness'],'caller_trace_id':request['trace_id']}
            if operation=='acquire':
                _,policy,_,_=validate_remote(request,enforce_free=True)
                from .provider_saturation import _rows
                if any(r.get('extra',{}).get('permit_dispatch_id')==row['trace_id'] for r in _rows()):
                    raise ValueError('Dispatch admission already decided')
                from .worker_routing import free_route,free_required
                free=free_route(policy,request['provider'],request['model'])
                context['execution_policy']={'free_only':free_required(policy,request),'free_tier_validated':free is not None,
                    'free_tier_evidence':free.get('evidence_sha256') if free else None,
                    'validated_price_usd':free.get('price_usd') if free else None}
                from .quota_budget import reservation
                context.update(quota_model=request['model'],quota_reserved_tokens=reservation(request))
                from .worker_routing import request_constraints
                context['request_constraints']=request_constraints(policy,request['provider'],request['model'],request)
                permit=acquire(request['provider'],args['call_id'],context)
                return {**permit,'execution_policy':context['execution_policy'],'request_constraints':context['request_constraints']}
            return release(args['token'],request['provider'],args['http_status'],args['latency_ms'],context=context,retry_after=args.get('retry_after'),attempted=args.get('attempted',True))
    if operation=='receipt':return emit(request,owner,args['index'],args['receipt'])
    if operation=='complete':return complete(request,owner,args['result'])
    raise ValueError('Unknown authority operation')
