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

def aodl_document(*, tokens=1000, max_children=4, max_depth=2, allowed=True, ceiling=None, plan=None):
    doc={
        'specVersion':'0.2','graphId':'dispatch-gate','revision':3,
        'intentGraph':{'nodes':[{
            'id':'parent','kind':'task',
            'ports':[{'id':'out','direction':'out','schema':'Task'}],
            'capabilities':['execute'],
            'authorityCeiling':list(ceiling or ['execute','verify']),
            'lifecycle':'declared',
        }],'edges':[]},
        'policies':{'kinds':['sequence'],'fanIn':'all','dynamic':{
            'allowed':allowed,'maxChildren':max_children,'maxDepth':max_depth}},
        'constraints':{'budgets':{'tokens':tokens},'termination':{'on':'done'}},
        'provenance':{'source':'test','sourceHash':'0'*64},
    }
    if plan is not None:doc['plan']=plan
    return doc


def governed(*, trace='race', doc=None, **spawn):
    req=request()
    req['trace_id']=trace
    proposal={
        'request_revision':3,'parent_node_id':'parent','live_children':0,'parent_depth':0,
        'observed':{'tokens':0},'proposed':{'tokens':1},'requested':['execute'],
    }
    proposal.update(spawn)
    req['aodl']={'document':doc or aodl_document(),'spawn':proposal}
    return req


def protocol3(req, owner):
    return a.rpc('claim',{'protocol_version':3,'request':req,'owner':owner})


def test_protocol_v3_missing_aodl_is_durable_denial():
    req=request();owner=secrets.token_hex(32)
    first=protocol3(req,owner)
    assert not first['claimed']
    assert first['result']['execution_status']=='denied'
    assert first['result']['aodl_admission']['codes']==['aodl-required']
    rows=[json.loads(x) for x in receipts_path().read_text().splitlines()]
    assert len(rows)==1
    assert rows[0]['capability_id']=='aodl.structural_admission'
    assert rows[0]['schema']=='z0int.decision_receipt.v1'
    assert 'success' not in rows[0] and 'verified_success' not in rows[0]
    again=protocol3(req,secrets.token_hex(32))
    assert again['result']==first['result']
    assert len(receipts_path().read_text().splitlines())==1


def test_protocol_v3_allow_is_fsynced_before_dispatch_start():
    req=governed();owner=secrets.token_hex(32)
    claimed=protocol3(req,owner)
    assert claimed['claimed']
    rows=[json.loads(x) for x in receipts_path().read_text().splitlines()]
    assert [row['capability_id'] for row in rows]==['aodl.structural_admission','intelligence.dispatch']
    admission,dispatch=rows
    assert admission['extra']['aodl_admission']['allowed'] is True
    assert dispatch['extra']['aodl_admission_receipt_id']==admission['trace_id']
    assert claimed['aodl_admission_receipt_id']==admission['trace_id']
    replayed=protocol3(req,secrets.token_hex(32))
    assert replayed['result']['execution_status']=='uncertain'
    assert len(receipts_path().read_text().splitlines())==2


def test_protocol_v3_projects_gate_latency_once_to_tokenomics():
    from z0int.tokenomics_emit import events_path
    req=governed();owner=secrets.token_hex(32)
    first=protocol3(req,owner)
    assert first['claimed']
    rows=[json.loads(line) for line in events_path().read_text().splitlines()]
    assert len(rows)==1
    assert rows[0]['schema']=='z0int.aodl_gate_latency.v1'
    assert rows[0]['allowed'] is True
    assert rows[0]['task_success'] is None
    assert rows[0]['verified_success'] is None
    protocol3(req,secrets.token_hex(32))
    assert len(events_path().read_text().splitlines())==1


def test_protocol_v3_denied_budget_never_creates_dispatch():
    req=governed(doc=aodl_document(tokens=10),observed={'tokens':10},proposed={'tokens':1})
    owner=secrets.token_hex(32)
    denied=protocol3(req,owner)
    assert not denied['claimed']
    assert denied['result']['aodl_admission']['numeric_codes']==[105]
    rows=[json.loads(x) for x in receipts_path().read_text().splitlines()]
    assert len(rows)==1
    assert rows[0]['capability_id']=='aodl.structural_admission'
    assert not any(row['trace_id'].startswith('dispatch-') for row in rows)
    with pytest.raises(ValueError,match='Not the claim owner'):
        authorize(req,owner)


def test_protocol_v3_malformed_envelope_is_durable_denial():
    req=request();req['aodl']={'document':aodl_document()}
    first=protocol3(req,secrets.token_hex(32))
    assert not first['claimed']
    assert first['result']['aodl_admission']['codes']==['aodl-envelope-invalid']
    assert len(receipts_path().read_text().splitlines())==1
    second=protocol3(req,secrets.token_hex(32))
    assert second['result']==first['result']
    assert len(receipts_path().read_text().splitlines())==1


def test_restart_reuses_allowed_admission_before_dispatch_start():
    from z0int import aodl_dispatch
    req=governed();key=a.identity(req)
    with a.locked(key):
        admission=aodl_dispatch.ensure(req,key,a.fingerprint(req),required=True)
    assert admission is not None and admission['extra']['aodl_admission']['allowed']
    assert len(receipts_path().read_text().splitlines())==1
    claimed=protocol3(req,secrets.token_hex(32))
    assert claimed['claimed']
    rows=[json.loads(x) for x in receipts_path().read_text().splitlines()]
    assert len(rows)==2
    assert rows[0]['trace_id']==claimed['aodl_admission_receipt_id']
    assert rows[1]['extra']['aodl_admission_receipt_id']==rows[0]['trace_id']


def test_denied_admission_trace_conflict_fails_closed():
    req=governed(doc=aodl_document(tokens=0),proposed={'tokens':1})
    assert not protocol3(req,secrets.token_hex(32))['claimed']
    changed={**req,'aodl':{'document':aodl_document(tokens=1),'spawn':req['aodl']['spawn']}}
    with pytest.raises(ValueError,match='different AODL admission request'):
        protocol3(changed,secrets.token_hex(32))


def test_runtime_semantic_drift_does_not_rewrite_intent_lineage():
    first=governed(trace='drift-a',doc=aodl_document(plan={'generation':1}))
    second=governed(trace='drift-b',doc=aodl_document(plan={'generation':2}))
    assert protocol3(first,secrets.token_hex(32))['claimed']
    assert protocol3(second,secrets.token_hex(32))['claimed']
    rows=[json.loads(x) for x in receipts_path().read_text().splitlines()]
    admissions=[row['extra']['aodl_admission'] for row in rows if row.get('capability_id')=='aodl.structural_admission']
    assert len(admissions)==2
    assert admissions[0]['aodl_semantic_fingerprint']!=admissions[1]['aodl_semantic_fingerprint']
    assert admissions[0]['aodl_intent_source_hash']==admissions[1]['aodl_intent_source_hash']=='0'*64


def test_validate_remote_strips_aodl_before_worker_contract():
    req=governed()
    worker,_,_,_=a.validate_remote(req)
    assert 'aodl' not in worker
    assert worker['task']==req['task']


def test_protocol_v2_remains_legacy_compatible_without_aodl():
    req=request();owner=secrets.token_hex(32)
    result=a.rpc('claim',{'protocol_version':2,'request':req,'owner':owner})
    assert result['claimed']
    rows=[json.loads(x) for x in receipts_path().read_text().splitlines()]
    assert [row['capability_id'] for row in rows]==['intelligence.dispatch']

