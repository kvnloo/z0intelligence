"""Authority-owned account admission and health, reconstructed from canonical receipts."""
import hashlib
import json
import math
import time
import uuid
import fcntl
from .dispatch_authority import locked
from .receipt import append_receipt, receipts_path

CAPABILITY='intelligence.provider_admission'
BUCKETS=(.1,.25,.5,1,2,5,10,30,60)


def policy():
    from .worker_routing import configuration
    return configuration()[0]


def name(provider):return 'jev' if provider=='typesafe' else provider


def _rows():
    path=receipts_path()
    if not path.exists():return []
    with path.open() as stream:
        fcntl.flock(stream,fcntl.LOCK_SH)
        return [row for line in stream if (row:=json.loads(line)).get('capability_id')==CAPABILITY]


def _state(provider,rows,config,now):
    provider=name(provider)
    events=[r for r in rows if r.get('provider')==provider]
    acquired={r['trace_id']:r for r in events if r['extra']['status']=='acquired'}
    released={r['trace_id']:r for r in events if r['extra']['status']=='released'}
    health=[r for r in events if r['extra']['status'] in ('released','health_reset')]
    blocked=False;until=0;last=None
    for row in health:
        extra=row['extra']
        if extra['status']=='health_reset':blocked=False;until=0;last=None;continue
        if not extra.get('physical_call_attempted',True):continue
        status=extra.get('http_status');last=status
        if status in (401,403):blocked=True
        elif status in (402,429):until=max(until,row['ts']+extra.get('cooldown_seconds',config.get('health_cooldown_seconds',60)))
        elif status==0 or (isinstance(status,int) and status>=500):
            until=max(until,row['ts']+config.get('health_error_ttl_seconds',30))
        # A late success from another in-flight call cannot erase a 403 or active cooldown.
    cap=config.get('provider_caps',{}).get(provider)
    inflight=len(set(acquired)-set(released))
    reason='eligible_unprobed' if last is None else 'healthy'
    if cap is None:reason='unmeasured_cap'
    elif cap<=0:reason='cap_zero'
    elif blocked:reason='account_blocked'
    elif until>now:reason='funds_exhausted' if last==402 else 'cooldown'
    elif inflight>=cap:reason='inflight_cap'
    return {'provider':provider,'cap':cap,'inflight':inflight,'available':reason in ('healthy','eligible_unprobed'),
        'reason':reason,'unhealthy':blocked or until>now,'cooldown_until':until,
        'last_http_status':last,'held_tokens':sorted(set(acquired)-set(released))}


def snapshot(provider,config=None):
    return _state(name(provider),_rows(),config or policy(),time.time())


def acquire(provider,call_id,context=None):
    provider=name(provider);config=policy();context=context or {}
    if provider not in config['provider_caps'] or not isinstance(call_id,str) or not 1<=len(call_id)<=200:raise ValueError('Invalid admission identity')
    with locked('provider-'+provider):
        rows=_rows();state=_state(provider,rows,config,time.time())
        from .quota_budget import project
        quota=project(provider,context.get('quota_model'),config,context.get('quota_reserved_tokens'))
        if quota is not None and not quota['allowed']:
            state.update(available=False,reason=quota['reason'])
        if context.get('sidestep') is True and state['reason']=='unmeasured_cap':
            state.update(available=True,reason='sidestep_unmeasured_cap',cap=1)
        token='admission-'+hashlib.sha256((provider+'\0'+call_id).encode()).hexdigest()
        # No permit replay: a second caller cannot reuse another caller's admission.
        if any(r['trace_id']==token for r in rows):raise ValueError('Admission identity already used')
        capped=state['reason'] in ('cap_zero','inflight_cap')
        extra={'status':'acquired' if state['available'] else 'capped' if capped else 'rejected',
            'call_id':call_id,'inflight_at_admission':state['inflight']+(1 if state['available'] else 0),
            'cap':state['cap'],'capped':capped,'reason':state['reason'],**context}
        if quota is not None:extra['quota_budget']=quota
        append_receipt({'trace_id':token,'capability_id':CAPABILITY,'execution':'orchestration',
            'provider':provider,'extra':extra})
        return {'admitted':state['available'],'token':token,'provider':provider,
            'inflight_at_admission':extra['inflight_at_admission'],'cap':state['cap'],
            'capped':capped,'reason':state['reason'],'quota_budget':quota}


def release(token,provider,http_status,latency_ms,context=None,retry_after=None,attempted=True):
    provider=name(provider);context=context or {}
    if type(attempted) is not bool:raise ValueError('Invalid attempt flag')
    if type(http_status) is not int or http_status<0 or http_status>599:raise ValueError('Invalid HTTP status')
    if type(latency_ms) not in (int,float) or not math.isfinite(latency_ms) or latency_ms<0:raise ValueError('Invalid latency')
    with locked('provider-'+provider):
        rows=_rows();matches=[r for r in rows if r['trace_id']==token]
        if not matches or matches[0]['provider']!=provider or matches[0]['extra']['status']!='acquired':raise ValueError('No acquired permit')
        row=matches[0]
        for key in ('permit_dispatch_id','permit_owner_sha256'):
            if row['extra'].get(key)!=context.get(key):raise ValueError('Permit owner mismatch')
        data={'http_status':http_status,'latency_ms':latency_ms,'physical_call_attempted':attempted}
        if matches[-1]['extra']['status']=='released':
            if any(matches[-1]['extra'].get(k)!=v for k,v in data.items()):raise ValueError('Release payload changed')
            return matches[-1]
        cooldown=policy().get('health_cooldown_seconds',60)
        if type(retry_after) in (int,float) and math.isfinite(retry_after):cooldown=max(cooldown,min(3600,retry_after))
        row={**row,'ts':time.time(),'extra':{**row['extra'],**data,'status':'released','cooldown_seconds':cooldown}}
        return append_receipt(row)


class LocalAdmission:
    def acquire(self,provider,call_id,context):return acquire(provider,call_id,context)
    def release(self,permit,status,latency_ms,retry_after=None,attempted=True):
        return release(permit['token'],permit['provider'],status,latency_ms,retry_after=retry_after,attempted=attempted)


def reset_health(provider):
    provider=name(provider)
    with locked('provider-'+provider):
        return append_receipt({'trace_id':'health-reset-'+uuid.uuid4().hex,'capability_id':CAPABILITY,
            'execution':'orchestration','provider':provider,'extra':{'status':'health_reset','source':'operator'}})


def metrics_text():
    config=policy();rows=_rows();out=[];now=time.time()
    types={'inflight':'gauge','cap':'gauge','capped_total':'counter','ratelimited_total':'counter','error_total':'counter','unhealthy':'gauge','latency_seconds':'histogram'}
    for metric,kind in types.items():out.append(f'# TYPE z0intelligence_provider_{metric} {kind}')
    for provider in config['provider_caps']:
        state=_state(provider,rows,config,now);label='provider='+json.dumps(provider)
        events=[r for r in rows if r.get('provider')==provider]
        releases=[r for r in events if r['extra']['status']=='released' and r['extra'].get('physical_call_attempted',True)]
        values={'inflight':state['inflight'],'cap':state['cap'],'unhealthy':int(state['unhealthy']),
            'capped_total':sum(r['extra']['status']=='capped' for r in events),
            'ratelimited_total':sum(r['extra'].get('http_status')==429 for r in releases)}
        for key,value in values.items():
            if value is not None:out.append(f'z0intelligence_provider_{key}{{{label}}} {value}')
        statuses=sorted({r['extra']['http_status'] for r in releases if not 200<=r['extra']['http_status']<300})
        for status in statuses:
            count=sum(r['extra']['http_status']==status for r in releases)
            out.append(f'z0intelligence_provider_error_total{{{label},status="{status}"}} {count}')
        durations=[r['extra']['latency_ms']/1000 for r in releases]
        for bucket in (*BUCKETS,float('inf')):
            boundary='+Inf' if math.isinf(bucket) else str(bucket)
            out.append(f'z0intelligence_provider_latency_seconds_bucket{{{label},le="{boundary}"}} {sum(d<=bucket for d in durations)}')
        out.append(f'z0intelligence_provider_latency_seconds_count{{{label}}} {len(durations)}')
        out.append(f'z0intelligence_provider_latency_seconds_sum{{{label}}} {sum(durations)}')
    return '\n'.join(out)+'\n'


def startup_probe():
    """Readiness metadata only; every physical call belongs to dispatch authority."""
    from .worker_routing import configuration, credential_available, free_required, free_route
    config,providers=configuration()
    provider=(config.get('free_provider_order') or [config['default_provider']])[0] if free_required(config) else config['default_provider']
    state=snapshot(provider,config)
    permitted=not free_required(config) or free_route(config,provider,config['defaults'][provider]) is not None
    return {'ok':bool(state['available'] and permitted and credential_available(provider,providers[provider])),
            'provider':provider,'reason':state['reason'],'scope':'credential/policy/admission check; no inference'}


def main():
    import argparse
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('operation',choices=['status','reset-health'])
    parser.add_argument('provider')
    args=parser.parse_args()
    print(json.dumps(snapshot(args.provider) if args.operation=='status' else reset_health(args.provider)))

if __name__=='__main__':main()
