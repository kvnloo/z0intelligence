#!/usr/bin/env python3
"""Opt-in live public-text acceptance; preserve output in a new external directory.

Uses the installed canonical provider credential lane. Never prints credentials.
No source code, private history or existing receipts are sent to a provider.
"""
import argparse,concurrent.futures,json,os,secrets,subprocess,sys,threading
from pathlib import Path

parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument('--output',type=Path,required=True)
parser.add_argument('--omp-root',type=Path,required=True)
args=parser.parse_args();args.output.mkdir(parents=True,exist_ok=False)
os.environ['Z0INT_HOME']=str(args.output/'state')
os.environ['Z0INT_EXECUTOR_PROVIDERS']='openrouter'
os.environ['Z0INT_PYTHON']=sys.executable
os.environ['Z0INT_AUTO_ALLOW_REMOTE']='1'
root=Path(__file__).resolve().parents[1]
os.environ['PYTHONPATH']=str(root/'src')+os.pathsep+os.environ.get('PYTHONPATH','')
from z0int.intelligence_service import Service,Handler
from z0int import dispatch_authority as authority,provider_saturation as caps,intelligence
from z0int.receipt import receipts_path
server=Service(('127.0.0.1',0),Handler)
thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
os.environ['Z0INT_AUTHORITY_URL']=os.environ['Z0INT_SERVICE_URL']=f'http://127.0.0.1:{server.server_address[1]}'
request={'harness':'codex','trace_id':'remote-public-proof','parent_agent':'canonical-proof','function':'cheap_bounded_worker','task':'Public diagnostic. Return the single word CANONICAL_OK.','provider':'openrouter','model':'nvidia/nemotron-3-super-120b-a12b:free','reason':'Canonical authority acceptance on public text','max_tokens':1024,'free_only':True}
def once(r):
 p=subprocess.run([sys.executable,'-m','z0int.remote_executor','once'],input=json.dumps(r),text=True,capture_output=True,timeout=90)
 if p.returncode:raise RuntimeError('Executor failed: '+p.stderr[-500:])
 return json.loads(p.stdout)
def save(name,value):(args.output/name).write_text(json.dumps(value,indent=2)+'\n')
try:
 import urllib.request
 health={}
 for path in ['/healthz','/readyz']:
  with urllib.request.urlopen(os.environ['Z0INT_SERVICE_URL']+path) as r:health[path]=json.load(r)
 save('health.json',health)
 tiny=dict(harness='codex',trace_id='tiny',parent_agent='canonical-proof',function='cheap_bounded_worker',task='2+2',expected_parent_tokens=4,expected_parent_ms=10,automatic=True)
 assert intelligence.dispatch(tiny)['route']['kind']=='PARENT_ONLY'
 assert intelligence.dispatch({**tiny,'trace_id':'unavailable','function':'unregistered_function'})['route']['kind']=='PARENT_ONLY'
 with concurrent.futures.ThreadPoolExecutor(2) as pool:race=list(pool.map(once,[request,request]))
 assert any(r.get('ok') for r in race),race
 replay=once(request);assert replay.get('ok') and replay.get('replayed'),replay
 save('race.json',race);save('replay.json',replay)
 try:once({**request,'task':'conflicting payload'})
 except RuntimeError:conflict=True
 else:raise AssertionError('Conflicting fingerprint was accepted')
 unknown={**request,'trace_id':'uncertain'};authority.claim(unknown,secrets.token_hex(32))
 assert once(unknown)['execution_status']=='uncertain'
 permit=caps.acquire('openrouter','cap-proof')
 assert permit['admitted']
 try:
  capped=once({**request,'trace_id':'capped'});assert not capped['ok'] and capped['requires_parent'];save('capped.json',capped)
 finally:caps.release(permit['token'],'openrouter',0,0,attempted=False)
 config=Path(os.environ['Z0INT_HOME'])/'config';config.mkdir(exist_ok=True)
 (config/'automatic.json').write_text(json.dumps({'omp':{'enabled':True}}))
 env={**os.environ,'Z0INT_OMP_ROOT':str(args.omp_root),'Z0INT_PROOF_OUTPUT':str(args.output),'Z0INT_ROOT':str(root)}
 subprocess.run(['bun',str(root/'scripts/prove-intelligence-omp.mjs')],env=env,check=True,timeout=100)
 rows=[json.loads(line) for line in receipts_path().read_text().splitlines()];latest={r['trace_id']:r for r in rows}
 physical=[r for r in latest.values() if r.get('execution')=='live' and r.get('extra',{}).get('physical_call_attempted')]
 free=[r for r in physical if r.get('provider')=='openrouter'];jev=[r for r in physical if r.get('provider')=='typesafe']
 assert len(free)==1 and free[0]['extra']['status']=='completed',free
 assert free[0]['extra']['free_tier_validated'] and free[0]['extra']['authority_dispatch_id']
 assert len(jev)==1 and jev[0]['extra']['status']=='completed'
 save('summary.json',{'head':subprocess.check_output(['git','rev-parse','HEAD'],cwd=root,text=True).strip(),'python':sys.version,'source':str(intelligence.__file__),'parent_only':True,'unavailable_native':True,'two_executor_processes':True,'one_remote_physical_call':True,'restart_replay':True,'conflict_rejected':conflict,'uncertain_refused':True,'cap_refused':True,'free_receipt':free[0]['trace_id'],'jev_receipt':jev[0]['trace_id'],'authority_owned':True,'omp_consumed':True})
 print((args.output/'summary.json').read_text())
finally:
 server.shutdown();server.server_close();thread.join()
