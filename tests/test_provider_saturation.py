from z0int.dispatch_authority import append_receipt
import concurrent.futures
import copy
import json
import multiprocessing as mp
import secrets
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
import pytest
from z0int import provider_saturation as p
from z0int import worker_routing as w
from z0int.receipt import receipts_path


@pytest.fixture(autouse=True)
def isolated(tmp_path,monkeypatch):monkeypatch.setenv('Z0INT_HOME',str(tmp_path))


def _ramp(queue,release,url,authority_url):
    import os
    os.environ['Z0INT_HOME']='/nonexistent-executor-ledger'
    os.environ['Z0INT_AUTHORITY_URL']=authority_url
    from z0int.remote_executor import request as rpc
    permits=[];threads=[]
    for n in range(32):
        req={'harness':'replica-test','trace_id':secrets.token_hex(16),'parent_agent':'test','function':'cheap_bounded_worker','task':'Public admission fixture','provider':'cerebras','model':'qwen-3.8-27b','reason':'local HTTP fixture'}
        common={'protocol_version':2,'request':req,'owner':secrets.token_hex(32)}
        assert rpc('claim',common)['claimed']
        permit=rpc('acquire',{**common,'call_id':secrets.token_hex(16)})
        permits.append(permit)
        if permit['admitted']:
            def http_call(token=permit,identity=common):
                with urllib.request.urlopen(url,timeout=20) as response:response.read()
                rpc('release',{**identity,'token':token['token'],'http_status':200,'latency_ms':1})
            thread=threading.Thread(target=http_call);thread.start();threads.append(thread)
    queue.put(permits)
    release.wait(20)
    for thread in threads:thread.join(20)


def test_cluster_cap_two_processes_64_requests(tmp_path):
    provider_requests=[];gate=threading.Event()
    class H(BaseHTTPRequestHandler):
        def log_message(self,*a):pass
        def do_GET(self):
            provider_requests.append(time.monotonic());gate.wait(20)
            self.send_response(200);self.end_headers();self.wfile.write(b'OK')
    class S(ThreadingHTTPServer):request_queue_size=128
    server=S(('127.0.0.1',0),H)
    thread=threading.Thread(target=server.serve_forever);thread.start()
    from z0int.intelligence_service import Service,Handler
    authority=Service(('127.0.0.1',0),Handler)
    authority_thread=threading.Thread(target=authority.serve_forever);authority_thread.start()
    context=mp.get_context('spawn');queue=context.Queue();release=context.Event()
    workers=[context.Process(target=_ramp,args=(queue,release,f'http://127.0.0.1:{server.server_port}',f'http://127.0.0.1:{authority.server_port}')) for _ in range(2)]
    try:
        for worker in workers:worker.start()
        admitted=[permit for _ in workers for permit in queue.get(timeout=20)]
        assert sum(x['admitted'] for x in admitted)==32
        assert sum(x['capped'] for x in admitted)==32
        assert max(x['inflight_at_admission'] for x in admitted)==32
        assert p.snapshot('cerebras')['inflight']==32
        deadline=time.monotonic()+5
        while len(provider_requests)<32 and time.monotonic()<deadline:time.sleep(.01)
        assert len(provider_requests)==32 # 32 denied admissions made ZERO HTTP calls.
        assert 'z0intelligence_provider_capped_total{provider="cerebras"} 32' in p.metrics_text()
    finally:
        gate.set();release.set()
        for worker in workers:worker.join(20)
        authority.shutdown();authority.server_close();authority_thread.join()
        server.shutdown();server.server_close();thread.join()
    assert all(worker.exitcode==0 for worker in workers)
    assert p.snapshot('cerebras')['inflight']==0


def test_cap_zero_default_never_selected(monkeypatch):
    policy,providers=w.configuration()
    monkeypatch.setattr(w,'available',lambda *a:True)
    for default in ('openrouter','vercel'):
        policy['default_provider']=default
        plan=w.plan_route('A plain task',policy,providers)
        assert plan['candidates'][0]['provider']=='cerebras'
        assert not any(c['provider'] in ('openrouter','vercel') for c in plan['candidates'])


def test_local_singleton_and_no_permit_expiry(monkeypatch):
    first=p.acquire('local','one');second=p.acquire('local','two')
    assert first['admitted'] and second['capped']
    now=time.time();monkeypatch.setattr(p.time,'time',lambda:now+100000)
    assert p.snapshot('local')['inflight']==1 # executor death does not free uncertain work
    assert not p.acquire('local','three')['admitted']
    p.release(first['token'],'local',200,1)
    assert p.acquire('local','four')['admitted']


def test_health_429_ttl_403_operator_only(monkeypatch):
    first=p.acquire('groq','one',{'quota_model':'openai/gpt-oss-20b','quota_reserved_tokens':100});p.release(first['token'],'groq',429,100)
    assert not p.snapshot('groq')['available']
    now=time.time();monkeypatch.setattr(p.time,'time',lambda:now+61)
    assert p.snapshot('groq')['available']
    second=p.acquire('groq','two',{'quota_model':'openai/gpt-oss-20b','quota_reserved_tokens':100});p.release(second['token'],'groq',403,100)
    monkeypatch.setattr(p.time,'time',lambda:now+100000)
    assert not p.snapshot('groq')['available']
    p.reset_health('groq');assert p.snapshot('groq')['available']
    metrics=p.metrics_text()
    assert 'z0intelligence_provider_ratelimited_total{provider="groq"} 1' in metrics
    assert 'z0intelligence_provider_error_total{provider="groq",status="403"} 1' in metrics


def test_function_orders_and_unmeasured_excluded(monkeypatch):
    policy,providers=w.configuration();monkeypatch.setattr(w,'available',lambda *a:True)
    structured=w.plan_route('Choose a tool',policy,providers,function='tool_selection')
    text=w.plan_route('Summarize this',policy,providers,function='summarization')
    assert [c['provider'] for c in structured['candidates']]==['cerebras','groq','deepseek']
    assert [c['provider'] for c in text['candidates']]==['cerebras','deepseek','groq']
    assert not p.snapshot('grok')['available'] and p.snapshot('grok')['cap'] is None


def test_release_idempotency_and_receipt_admission_fields():
    admission=p.acquire('cerebras','fields')
    a=p.release(admission['token'],'cerebras',200,12)
    assert p.release(admission['token'],'cerebras',200,12)==a
    rows=[json.loads(x) for x in receipts_path().read_text().splitlines()]
    assert len(rows)==2
    assert all(r['schema']=='z0int.decision_receipt.v1' for r in rows)
    assert rows[0]['extra']['cap']==32 and rows[0]['extra']['inflight_at_admission']==1
    assert rows[0]['extra']['capped'] is False


def test_real_http_429_falls_back_without_retry_storm(monkeypatch):
    calls=[]
    class H(BaseHTTPRequestHandler):
        def log_message(self,*a):pass
        def do_POST(self):
            body=json.loads(self.rfile.read(int(self.headers['Content-Length'])));calls.append(body['model'])
            if body['model']=='qwen-3.8-27b':
                self.send_response(429);self.send_header('Retry-After','60');self.end_headers();self.wfile.write(b'{}');return
            self.send_response(200);self.end_headers();self.wfile.write(json.dumps({'model':body['model'],'choices':[{'message':{'content':'OK'},'finish_reason':'stop'}],'usage':{'prompt_tokens':2,'completion_tokens':1}}).encode())
    server=ThreadingHTTPServer(('127.0.0.1',0),H);thread=threading.Thread(target=server.serve_forever);thread.start()
    policy,providers=w.configuration();providers=copy.deepcopy(providers)
    for provider in ('cerebras','deepseek','groq'):
        providers[provider].update(base_url=f'http://127.0.0.1:{server.server_port}',api_key_env='TEST_PROVIDER_KEY',auth='environment')
    monkeypatch.setenv('TEST_PROVIDER_KEY','local-test-only')
    try:
        plan=w.plan_route('Plain task',policy,providers);plan['source']='local_http_fixture'
        result=w.execute_plan({'task':'Reply OK','parent_agent':'test'},policy,providers,plan,receipt_sink=append_receipt)
        assert result['ok'] and result['provider']=='deepseek'
        assert calls==['qwen-3.8-27b','deepseek-chat']
        assert w.plan_route('Plain task',policy,providers)['candidates'][0]['provider']=='deepseek'
        assert result['attempts'][0]['extra']['cap']==32
    finally:server.shutdown();server.server_close();thread.join()


def test_startup_metadata_never_executes_unreceipted_call(monkeypatch):
    from z0int.cognition.adapters.transport import OpenAICompatTransport
    policy,providers=w.configuration()
    monkeypatch.setenv(providers['cerebras']['api_key_env'],'test-not-a-real-key')
    monkeypatch.setattr(OpenAICompatTransport,'chat',lambda *a,**k:pytest.fail('unreceipted startup inference'))
    result=p.startup_probe()
    assert result['ok'] and 'no inference' in result['scope']



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
