"""KERD quota projection over authority receipts; no second ledger or paid spill."""
import fcntl
import json
import sys
import time
from .receipt import receipts_path

WINDOWS={'rpm':60,'tpm':60,'rpd':86400,'tpd':86400}


def reservation(args):
    from .worker_routing import messages_for
    # Byte bound plus framing is intentionally conservative, not measured usage.
    return len(json.dumps(messages_for(args),ensure_ascii=False).encode())+args.get('max_tokens',512)+512


def rows():
    path=receipts_path()
    if not path.exists():return []
    with path.open() as stream:
        fcntl.flock(stream,fcntl.LOCK_SH)
        return [json.loads(line) for line in stream]


def project(provider,model,config,needed,events=None,now=None):
    rule=config.get('quota_budgets',{}).get(provider)
    if not rule:return None
    if model!=rule['model'] or type(needed) is not int or needed<=0:
        return {'allowed':False,'reason':'quota_contract_missing','next_eligible_at':None}
    import os
    root=os.environ.get('Z0INT_KERDOIOS_ROOT',rule.get('kerdoios_root'))
    added=bool(root and root not in sys.path)
    if added:sys.path.append(root)
    try:
        from kerdoios.quota.model import QuotaState,QuotaDimension
        from kerdoios.quota.parse import quota_state_from_headers
    except ImportError:
        return {'allowed':False,'reason':'quota_backend_unavailable','next_eligible_at':None}
    finally:
        if added:sys.path.remove(root)
    now=time.time() if now is None else now
    events=rows() if events is None else events
    permits={};physical={}
    for row in events:
        if row.get('provider')!=provider:continue
        extra=row.get('extra',{})
        if row.get('capability_id')=='intelligence.provider_admission' and extra.get('status')=='acquired':
            permits[row['trace_id']]=row
        if row.get('model')==model and row.get('execution')=='live':physical[row['trace_id']]=row
    calls=[];headers=[];matched=set()
    for row in physical.values():
        e=row.get('extra',{});permit=e.get('admission_token');matched.add(permit)
        if e.get('status') in ('rejected','capped'):continue
        start=e.get('physical_started_at',permits.get(permit,{}).get('ts',row.get('ts',now)))
        metered=all(type(row.get(k)) is int for k in ('input_tokens','output_tokens'))
        count=sum(row[k] for k in ('input_tokens','output_tokens')) if metered else permits.get(permit,{}).get('extra',{}).get('quota_reserved_tokens',rule['limits']['tpm'])
        calls.append((start,count))
        if e.get('quota_headers'):headers.append((e.get('physical_finished_at',row.get('ts',now)),e['quota_headers']))
    for key,row in permits.items():
        if key not in matched and row['extra'].get('quota_model')==model:
            calls.append((row['ts'],row['extra']['quota_reserved_tokens']))
    dims={};next_times=[]
    for dim,limit in rule['limits'].items():
        window=WINDOWS[dim];recent=[(ts,t) for ts,t in calls if ts>now-window]
        used=sum(1 if dim.startswith('r') else t for _,t in recent)
        required=1 if dim.startswith('r') else needed
        reset=min((ts+window for ts,_ in recent),default=now)
        dims[dim]=QuotaDimension(limit=limit,remaining=max(0,limit-used),reset_at=reset,source='locally_reconstructed')
        if limit-used<required:next_times.append(reset)
    if headers:
        observed,raw=max(headers,key=lambda pair:pair[0])
        # KERD's legacy generic parser maps unsuffixed requests to RPM. Groq
        # documents these as RPD; normalize the input, retaining raw receipts.
        normalized={k.replace('-requests','-requests-day') if provider=='groq' and k.endswith('-requests') else k:v for k,v in raw.items()}
        reported=quota_state_from_headers(normalized,provider=provider,model=model,now=observed)
        for dim,d in reported.dimensions.items():
            if d.remaining is None:continue
            expiry=d.reset_at or observed+WINDOWS.get(dim,86400)
            if now>=expiry:continue
            after=[(ts,t) for ts,t in calls if ts>observed]
            remaining=max(0,d.remaining-sum(1 if dim.startswith('r') else t for _,t in after))
            if dim in dims and remaining<dims[dim].remaining:
                dims[dim]=QuotaDimension(limit=d.limit,remaining=remaining,reset_at=expiry,source='provider_header')
            if remaining<(1 if dim.startswith('r') else needed):next_times.append(expiry)
    state=QuotaState(provider=provider,model=model,dimensions=dims,observed_at=now)
    return {'allowed':not next_times and state.has_free_capacity(),'reason':'quota_available' if not next_times else 'quota_boundary',
        'quota_bucket':provider+'/'+model,'quota':state.to_dict(),'request_count_24h':sum(ts>now-86400 for ts,_ in calls),
        'tokens_24h':sum(t for ts,t in calls if ts>now-86400),'reserved_tokens':needed,
        'next_eligible_at':max(next_times) if next_times else None,'reset_rule':'rolling conservative windows plus provider header reset; never midnight refill',
        'reset_cohort_date':time.strftime('%Y-%m-%d',time.gmtime(now)),'no_paid_spill':True}
