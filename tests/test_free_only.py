import copy,json,secrets
from unittest.mock import Mock
import pytest
from z0int import worker_routing as w,dispatch_authority as a,provider_saturation as p,intelligence as i
from z0int.receipt import receipts_path,append_receipt

@pytest.fixture(autouse=True)
def isolated(tmp_path,monkeypatch):monkeypatch.setenv('Z0INT_HOME',str(tmp_path))

def req(**kw):
 policy,_=w.configuration()
 return dict(harness='codex',trace_id=secrets.token_hex(8),parent_agent='test',function='cheap_bounded_worker',task='Reply OK',provider='openrouter',model=policy['defaults']['openrouter'],reason='public acceptance',free_only=True,**kw)

def test_actual_policy_only_measured_model():
 policy,providers=w.configuration()
 assert policy['free_only'] is True
 plan=w.plan_route('Anything',policy,providers,set(providers))
 assert plan['candidates']==[{'provider':'openrouter','model':'nvidia/nemotron-3-super-120b-a12b:free'}]
 assert w.free_route(policy,'vercel',policy['defaults']['vercel']) is None
 assert w.free_route(policy,'openrouter','qwen/qwen3.8-27b:free') is None
 assert policy['provider_caps']['openrouter']==1

@pytest.mark.parametrize('provider,model',[('deepseek','deepseek-chat'),('vercel','openai/gpt-oss-20b'),('openrouter','qwen/qwen3.8-27b')])
def test_authority_rejects_paid_even_request_false(provider,model):
 request={**req(),'provider':provider,'model':model,'free_only':False}
 with pytest.raises(ValueError,match='free_only'):a.claim(request,secrets.token_hex(32))
 assert not receipts_path().exists()

def test_execution_cannot_bypass_free_gate(monkeypatch):
 policy,providers=w.configuration();call=Mock();monkeypatch.setattr(w.OpenAICompatTransport,'chat',call)
 with pytest.raises(ValueError,match='free_only'):
  w.execute_plan({'task':'x','parent_agent':'test','free_only':False},policy,providers,{'candidates':[{'provider':'deepseek','model':'deepseek-chat'}]},receipt_sink=append_receipt)
 call.assert_not_called()

def test_all_free_capped_no_paid_overflow(monkeypatch):
 policy,providers=w.configuration();monkeypatch.setenv('OPENROUTER_API_KEY','fixture')
 permit=p.acquire('openrouter','one')
 assert permit['admitted']
 plan=w.plan_route('Public work',policy,providers)
 assert plan['candidates']==[]
 result=w.execute_plan({'task':'x','parent_agent':'test'},policy,providers,plan,receipt_sink=append_receipt)
 assert not result['ok'] and result['requires_parent'] and result['attempts']==[]

def test_legacy_paid_replay_survives_free_policy():
 request={**req(),'provider':'deepseek','model':'deepseek-chat'};key=a.identity(request)
 append_receipt({'trace_id':'dispatch-'+key,'extra':{'request_sha256':a.fingerprint(request),'status':'completed','result':{'ok':True,'output':'previous'}}})
 assert a.claim(request,secrets.token_hex(32))['result']['replayed']
 with pytest.raises(ValueError,match='trace_id reused'):a.claim({**request,'task':'changed'},secrets.token_hex(32))

def test_free_claim_uncertain_no_reexecution():
 request=req();owner=secrets.token_hex(32)
 assert a.claim(request,owner)['claimed']
 assert a.claim(request,secrets.token_hex(32))['result']['execution_status']=='uncertain'

def test_zero_price_body_constraints():
 policy,_=w.configuration();body=w.request_constraints(policy,'openrouter',policy['defaults']['openrouter'])
 assert body['provider']['max_price']=={'prompt':0,'completion':0,'request':0}
 assert body['provider']['allow_fallbacks'] is False

def test_auto_missing_quality_remains_parent():
 request={'harness':'omp','trace_id':'auto','parent_agent':'test','function':'summarization','task':'Summarize public text','automatic':True,'allow_remote':True,'expected_parent_tokens':10000,'expected_parent_ms':120000}
 result=i.route(request,i.routing_snapshot(request))
 assert result['kind']=='PARENT_ONLY' and 'evidence' in result['reason']


def test_conflict_is_client_rejection_not_uncertainty(monkeypatch):
 import urllib.error
 from z0int import remote_executor as remote
 def conflict(*a,**k):raise urllib.error.HTTPError('http://fixture',400,'conflict',{},None)
 monkeypatch.setattr(remote.urllib.request,'urlopen',conflict)
 with pytest.raises(ValueError,match='Authority rejected'):remote.request('claim',{})


def test_authority_exports_constraints_and_rejects_paid_completion():
 from test_dispatch_authority import event
 request=req();owner=secrets.token_hex(32);a.claim(request,owner)
 common={'protocol_version':2,'request':request,'owner':owner}
 permit=a.rpc('acquire',{**common,'call_id':'physical-call'})
 assert permit['execution_policy']['free_only']
 assert permit['request_constraints']['provider']['max_price']['completion']==0
 a.emit(request,owner,0,event(request))
 a.rpc('release',{**common,'token':permit['token'],'http_status':200,'latency_ms':1})
 payload=event(request,'completed');payload['extra']['provider_reported_cost_usd']=.01
 saved=a.emit(request,owner,1,payload)
 assert saved['extra']['status']=='failed' and saved['extra']['cost_policy_violation']
 with pytest.raises(ValueError):a.complete(request,owner,{'ok':True,'output':'ok','attempts':[saved]})
