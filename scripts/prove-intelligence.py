#!/usr/bin/env python3
"""Opt-in live governed-worker acceptance on public text.

Runs the real host authority HTTP service and the real remote-executor HTTP
service on loopback. The single physical provider call is initiated through the
real registered OMP z0int_route_worker tool. No parent model is called.
"""
import argparse,json,os,secrets,subprocess,sys,threading
import urllib.error,urllib.request
from pathlib import Path

parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument('--output',type=Path,required=True)
parser.add_argument('--omp-root',type=Path,required=True)
args=parser.parse_args();args.output.mkdir(parents=True,exist_ok=False)

root=Path(__file__).resolve().parents[1]
os.environ['Z0INT_HOME']=str(args.output/'state')
os.environ['Z0INT_EXECUTOR_PROVIDERS']='openrouter'
os.environ['Z0INT_PYTHON']=sys.executable
os.environ['Z0INT_GOVERNED_REMOTE']='1'
os.environ['Z0INT_GOVERNED_PROVIDER']='openrouter'
os.environ['Z0INT_GOVERNED_HARNESS']='omp'
os.environ['Z0INT_AODL_CONTRACT_PATH']=str(root/'contracts/aodl/governed-worker-v1.json')
os.environ['PYTHONPATH']=str(root/'src')+os.pathsep+os.environ.get('PYTHONPATH','')

from z0int.intelligence_service import Service as AuthorityService,Handler as AuthorityHandler
from z0int.remote_executor import Server as ExecutorService,Handler as ExecutorHandler
from z0int import dispatch_authority as authority,provider_saturation as caps,intelligence
from z0int.governed_worker import prepare_remote_request
from z0int.receipt import receipts_path,join_outcome

authority_server=AuthorityService(('127.0.0.1',0),AuthorityHandler)
authority_thread=threading.Thread(target=authority_server.serve_forever,daemon=True);authority_thread.start()
os.environ['Z0INT_AUTHORITY_URL']=os.environ['Z0INT_SERVICE_URL']=f'http://127.0.0.1:{authority_server.server_address[1]}'

executor_server=ExecutorService(('127.0.0.1',0),ExecutorHandler)
executor_thread=threading.Thread(target=executor_server.serve_forever,daemon=True);executor_thread.start()
os.environ['Z0INT_REMOTE_EXECUTOR_URL']=f'http://127.0.0.1:{executor_server.server_address[1]}'

def save(name,value):(args.output/name).write_text(json.dumps(value,indent=2)+'\n')

def post(path,value,timeout=120):
    req=urllib.request.Request(
        os.environ['Z0INT_SERVICE_URL']+path,
        data=json.dumps(value).encode(),
        headers={'Content-Type':'application/json'},
    )
    with urllib.request.urlopen(req,timeout=timeout) as response:return json.load(response)

def governed(*,parent_agent,trace='remote-public-proof',task='Public synthetic diagnostic. Return exactly CANONICAL_OK and nothing else.'):
    return {
        'harness':'omp',
        'trace_id':trace,
        'parent_agent':parent_agent,
        'task':task,
        'context':'Public synthetic diagnostic only.',
        'max_tokens':16,
        'allow_remote':True,
    }

try:
    health={}
    for path in ['/healthz','/readyz','/v1/providers']:
        with urllib.request.urlopen(os.environ['Z0INT_SERVICE_URL']+path) as r:health[path]=json.load(r)
    with urllib.request.urlopen(os.environ['Z0INT_REMOTE_EXECUTOR_URL']+'/readyz') as r:health['executor_readyz']=json.load(r)
    assert health['/readyz']['authority_protocol_version']==3
    assert health['/readyz']['aodl_admission_ready'] is True
    assert health['/readyz']['governed_remote_ready'] is True
    assert health['executor_readyz']['ok'] is True
    save('health.json',health)

    tiny=dict(harness='codex',trace_id='tiny',parent_agent='canonical-proof',function='cheap_bounded_worker',task='2+2',expected_parent_tokens=4,expected_parent_ms=10,automatic=True)
    assert intelligence.dispatch(tiny)['route']['kind']=='PARENT_ONLY'
    assert intelligence.dispatch({**tiny,'trace_id':'unavailable','function':'unregistered_function'})['route']['kind']=='PARENT_ONLY'

    # The one physical provider call in this proof comes through OMP's actual
    # registered z0int_route_worker tool, not a synthetic direct host POST.
    env={**os.environ,'Z0INT_OMP_ROOT':str(args.omp_root),'Z0INT_PROOF_OUTPUT':str(args.output),'Z0INT_ROOT':str(root)}
    subprocess.run(['bun',str(root/'scripts/prove-governed-omp-tool.mjs')],env=env,check=True,timeout=120)
    omp=json.loads((args.output/'omp-governed.json').read_text())
    assert omp['parent_model_called'] is False
    assert omp['root_trace_id']=='remote-public-proof'
    assert omp['output_verified_exact'] is True
    request=governed(parent_agent=omp['session_id'])

    # Identical host request replays the result with no second physical call.
    replay=post('/v1/governed-worker',request)
    assert replay.get('ok') and replay.get('replayed'),replay
    assert replay.get('trace_id')==request['trace_id']
    assert replay.get('aodl_admission_receipt_id')
    assert replay.get('output','').strip()=='CANONICAL_OK',replay.get('output')
    save('replay.json',replay)

    # Same trace + changed task is rejected before a second remote call.
    try:
        post('/v1/governed-worker',governed(parent_agent=omp['session_id'],task='conflicting payload'))
    except urllib.error.HTTPError as exc:
        assert exc.code==400
        conflict=True
    else:
        raise AssertionError('Conflicting governed request was accepted')

    # Provider cap refusal happens after structural admission but before physical call.
    permit=caps.acquire('openrouter','cap-proof')
    assert permit['admitted']
    try:
        capped=post('/v1/governed-worker',governed(parent_agent=omp['session_id'],trace='capped'))
        assert not capped['ok'] and capped['requires_parent']
        save('capped.json',capped)
    finally:
        caps.release(permit['token'],'openrouter',0,0,attempted=False)

    # Freeze the exact host-built envelope, create only dispatch-start, then
    # prove replay refuses takeover instead of minting another execution.
    uncertain_args=governed(parent_agent=omp['session_id'],trace='uncertain')
    uncertain_remote=prepare_remote_request(uncertain_args)
    authority.claim(uncertain_remote,secrets.token_hex(32),require_aodl=True)
    uncertain=post('/v1/governed-worker',uncertain_args)
    assert uncertain['execution_status']=='uncertain'

    rows=[json.loads(line) for line in receipts_path().read_text().splitlines()]
    latest={r['trace_id']:r for r in rows}
    physical=[r for r in latest.values() if r.get('execution')=='live' and r.get('extra',{}).get('physical_call_attempted')]
    free=[r for r in physical if r.get('provider')=='openrouter']
    admissions=[r for r in latest.values() if r.get('capability_id')=='aodl.structural_admission']
    preparations=[r for r in latest.values() if r.get('capability_id')=='aodl.governed_request']

    assert len(free)==1 and free[0]['extra']['status']=='completed',free
    assert free[0]['extra']['free_tier_validated'] and free[0]['extra']['authority_dispatch_id']
    joined=join_outcome(free[0]['trace_id'],{
        'verified_success':True,
        'verified':True,
        'source':'prove_intelligence_exact_public_fixture',
        'verification_source':'exact_string_CANONICAL_OK',
        'note':'Synthetic public diagnostic only; not a general model-quality claim.',
    })
    assert joined and joined['outcome_tier']=='gold'
    assert any(a.get('extra',{}).get('aodl_admission',{}).get('allowed') is True for a in admissions)
    assert preparations and all('remote_request' not in p.get('extra',{}) for p in preparations)
    assert all('Public diagnostic' not in json.dumps(p) for p in preparations)

    save('summary.json',{
        'head':subprocess.check_output(['git','rev-parse','HEAD'],cwd=root,text=True).strip(),
        'python':sys.version,
        'source':str(intelligence.__file__),
        'authority_protocol_version':3,
        'aodl_canon_version':'aodl-canon-1',
        'parent_only':True,
        'unavailable_native':True,
        'host_governed_endpoint':True,
        'remote_executor_http':True,
        'omp_registered_tool_execution':True,
        'parent_model_called':False,
        'one_remote_physical_call':True,
        'same_root_trace':replay['trace_id'],
        'aodl_admission_receipt':replay['aodl_admission_receipt_id'],
        'restart_replay':True,
        'conflict_rejected':conflict,
        'uncertain_refused':True,
        'cap_refused':True,
        'free_receipt':free[0]['trace_id'],
        'authority_owned':True,
        'governance_snapshot_prompt_free':True,
        'synthetic_verified_outcome':True,
        'synthetic_verifier':'exact_string_CANONICAL_OK',
    })
    print((args.output/'summary.json').read_text())
finally:
    executor_server.shutdown();executor_server.server_close();executor_thread.join()
    authority_server.shutdown();authority_server.server_close();authority_thread.join()
