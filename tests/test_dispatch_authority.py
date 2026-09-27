import concurrent.futures
import hashlib
import json
import secrets
import pytest
from z0int import dispatch_authority as a
from z0int.receipt import DecisionReceipt, receipts_path, append_receipt


def request(**kw):
    return dict(harness='dsh',trace_id='race',parent_agent='parent',function='cheap_bounded_worker',task='Rewrite public text',provider='cerebras',model='qwen-3.8-27b',reason='bounded authority test',**kw)


@pytest.fixture(autouse=True)
def isolated(tmp_path,monkeypatch):monkeypatch.setenv('Z0INT_HOME',str(tmp_path))


def authorize(req,owner):
    return a.rpc('acquire',{'protocol_version':2,'request':req,'owner':owner,'call_id':'physical-call'})


def event(req,status='started'):
    return DecisionReceipt(trace_id='physical-call',session_id='parent',capability_id='codex.delegated_text',provider=req['provider'],model=req['model'],execution='live',input_tokens=10,output_tokens=1,
        extra={'admission_token':'admission-'+hashlib.sha256((req['provider']+'\0physical-call').encode()).hexdigest(),'harness':req['harness'],'caller_trace_id':req['trace_id'],'status':status,'output_sha256':hashlib.sha256(b'ok').hexdigest()}).to_dict()


def test_race_claim_is_single_owner():
    req=request()
    with concurrent.futures.ThreadPoolExecutor(8) as pool:
        results=list(pool.map(lambda _:a.claim(req,secrets.token_hex(32)),range(8)))
    assert sum(r['claimed'] for r in results)==1
    assert all(r['result']['execution_status']=='uncertain' for r in results if not r['claimed'])
    assert len(receipts_path().read_text().splitlines())==1


def test_completion_replay_conflict_and_event_cas():
    req=request();owner=secrets.token_hex(32)
    assert a.claim(req,owner)['claimed']
    permit=authorize(req,owner)
    start=event(req)
    saved=a.emit(req,owner,0,start)
    assert a.emit(req,owner,0,start)==saved
    with pytest.raises(ValueError):a.emit(req,owner,0,{**start,'model':'different'})
    with pytest.raises(ValueError):a.emit(req,owner,2,event(req,'completed'))
    a.rpc('release',{'protocol_version':2,'request':req,'owner':owner,'token':permit['token'],'http_status':200,'latency_ms':1})
    terminal=a.emit(req,owner,1,event(req,'completed'))
    result={'ok':True,'output':'ok','attempts':[terminal]}
    complete=a.complete(req,owner,result)
    assert a.claim(req,secrets.token_hex(32))['result']=={**complete,'replayed':True}
    assert a.complete(req,owner,result)['replayed']
    with pytest.raises(ValueError):a.claim({**req,'task':'different'},secrets.token_hex(32))
    with pytest.raises(ValueError):a.complete(req,owner,{**result,'output':'different'})
    assert len(receipts_path().read_text().splitlines())==6


def test_uncertain_has_no_takeover_even_same_owner():
    req=request();owner=secrets.token_hex(32)
    a.claim(req,owner);authorize(req,owner);a.emit(req,owner,0,event(req))
    assert a.claim(req,owner)['result']['execution_status']=='uncertain'
    assert a.claim(req,secrets.token_hex(32))['result']['execution_status']=='uncertain'
    with pytest.raises(ValueError):a.complete(req,owner,{'ok':True,'attempts':[]})
    with pytest.raises(ValueError):a.emit(req,secrets.token_hex(32),1,event(req,'completed'))


def test_byte_and_schema_compatibility():
    req=request();owner=secrets.token_hex(32);a.claim(req,owner)
    authorize(req,owner)
    saved=a.emit(req,owner,0,event(req))
    assert receipts_path().read_bytes().splitlines(keepends=True)[-1]==(json.dumps(saved,default=str)+'\n').encode()
    assert saved['schema']=='z0int.decision_receipt.v1'
    assert all(json.loads(line)['schema']=='z0int.decision_receipt.v1' for line in receipts_path().read_text().splitlines())


def test_legacy_completed_and_uncertain_rows_keep_fingerprints():
    req=request();key=a.identity(req)
    append_receipt({'trace_id':'dispatch-'+key,'extra':{'request_sha256':a.fingerprint(req),'status':'started'}})
    assert a.claim(req,secrets.token_hex(32))['result']['execution_status']=='uncertain'
    append_receipt({'trace_id':'dispatch-'+key,'extra':{'request_sha256':a.fingerprint(req),'status':'completed','result':{'ok':True,'output':'legacy'}}})
    assert a.claim(req,secrets.token_hex(32))['result']['output']=='legacy'


def test_router_uses_only_snapshot(monkeypatch):
    from z0int import intelligence as i
    from pathlib import Path
    args={'harness':'test','trace_id':'pure','parent_agent':'parent','function':'cheap_bounded_worker','task':'2+2'}
    snapshot=i.routing_snapshot(args)
    monkeypatch.setattr(Path,'read_text',lambda *a,**k:pytest.fail('router performed I/O'))
    monkeypatch.setattr(i,'configuration',lambda:pytest.fail('router loaded config'))
    assert i.route(args,snapshot)['kind']=='PARENT_ONLY'


def test_corrupt_ledger_fails_closed():
    req=request();owner=secrets.token_hex(32)
    a.claim(req,owner)
    with receipts_path().open('a') as stream:stream.write('{"trace_id":')
    with pytest.raises(ValueError):a.claim(req,secrets.token_hex(32))


def test_receipt_failure_prevents_physical_call(monkeypatch):
    from z0int import worker_routing as w
    policy,providers=w.configuration()
    req=request()
    worker={k:v for k,v in req.items() if k not in ('harness','trace_id','function')}
    plan=w.explicit_plan(worker,policy,providers)
    def fail_receipt(row):raise OSError('authority unavailable')
    monkeypatch.setattr(w.OpenAICompatTransport,'chat',lambda *a,**k:pytest.fail('physical call before receipt acknowledgement'))
    with pytest.raises(OSError):w.execute_plan(worker,policy,providers,plan,receipt_sink=fail_receipt,receipt_location='authority')


@pytest.fixture(autouse=True)
def legacy_paid_policy(monkeypatch):
    # These tests cover the pre-existing general contract; free-only has its own suite.
    from z0int import worker_routing as routing, intelligence
    policy,providers=routing.configuration()
    policy['free_only']=False
    policy['provider_caps']['openrouter']=0
    policy['provider_caps']['vercel']=0
    monkeypatch.setattr(routing,'configuration',lambda:(policy,providers))
    monkeypatch.setattr(intelligence,'configuration',lambda:(policy,providers))


def test_stream_cannot_override_authority(tmp_path):
    req=request();a.run(req,lambda sink:{'ok':True,'output':'authority'})
    path=tmp_path/"stream/bridge.jsonl";path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps({'trace_id':'dispatch-'+a.identity(req),'extra':{'request_sha256':'wrong','result':{'output':'stream'}}})+'\n')
    assert a.claim(req,secrets.token_hex(32))['result']['output']=='authority'


def test_replay_precedes_current_policy(monkeypatch):
    req=request();a.run(req,lambda sink:{'ok':True,'output':'saved'})
    monkeypatch.setattr(a,'validate_remote',lambda *x,**k:pytest.fail('replay consulted mutable policy'))
    assert a.claim(req,secrets.token_hex(32))['result']['output']=='saved'
    with pytest.raises(ValueError,match='trace_id reused'):a.claim({**req,'task':'different'},secrets.token_hex(32))


def test_executor_replay_needs_no_credential(monkeypatch):
    from z0int import remote_executor as executor
    monkeypatch.setattr(executor,'request',lambda op,payload:{'claimed':False,'result':{'ok':True,'replayed':True}})
    monkeypatch.setattr(executor,'validate_remote',lambda *x:pytest.fail('replay consulted executor config'))
    assert executor.execute(request())['replayed']


def test_dispatch_cannot_acquire_second_permit():
    req=request();owner=secrets.token_hex(32);a.claim(req,owner);authorize(req,owner)
    with pytest.raises(ValueError,match='already decided'):
        a.rpc('acquire',{'protocol_version':2,'request':req,'owner':owner,'call_id':'second'})


def test_terminal_receipt_survives_policy_change(monkeypatch):
    from z0int import worker_routing as w
    req=request();owner=secrets.token_hex(32);a.claim(req,owner);permit=authorize(req,owner)
    a.emit(req,owner,0,event(req))
    a.rpc('release',{'protocol_version':2,'request':req,'owner':owner,'token':permit['token'],'http_status':200,'latency_ms':1})
    monkeypatch.setattr(w,'require_free_route',lambda *x:pytest.fail('terminal receipt rechecked mutable free policy'))
    row=a.emit(req,owner,1,event(req,'completed'))
    assert row['extra']['status']=='completed'
