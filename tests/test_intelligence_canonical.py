"""Canonical ownership and routing boundary regressions; no live model calls."""
import json
import pytest
from z0int.functions import jev
from z0int import automatic, intelligence

@pytest.mark.parametrize('choice,status',[('supported','SUPPORTED'),('insufficient','UNKNOWN'),('contradicted','UNSUPPORTED')])
def test_jev_uses_canonical_client_once(monkeypatch,choice,status):
    import httpx2
    calls=[]
    def send(request):
        calls.append(json.loads(request.content))
        return httpx2.Response(200,json={
            'model':'jev-1.13.0', 'usage':{'input_tokens':10,'output_tokens':1},
            'answers':{'decision':{'type':'choice','choice':choice,'confidence':0.8,
                'probabilities':{'supported':.8,'insufficient':.1,'contradicted':.1}}}})
    real_ask=jev.ask_choice
    transport=httpx2.MockTransport(send)
    monkeypatch.setattr(jev,'ask_choice',lambda *args,**kwargs:real_ask(*args,transport=transport,**kwargs))
    monkeypatch.setattr(jev,'ensure_credential',lambda:'test-only')
    monkeypatch.setattr(jev,'credential_source',lambda:'test')
    out=jev.assess_claim({'question':'public claim','evidence':'public evidence'})
    assert out['status']==status and len(calls)==1
    assert calls[0]['model']=='jev-1.13.0'
    assert calls[0]['questions']['decision']['type']=='choice'


def test_unmeasured_or_excluded_capability_is_not_automatic():
    registry=json.loads(intelligence.REGISTRY.read_text())
    for entry in registry['entries']:
        if entry['provider']=='typesafe':assert automatic.evidenced(entry)
        if 'nanojev' in entry['model'].lower() or entry.get('quality_metric') is None:
            assert not automatic.evidenced(entry)


def test_zero_call_integration_is_unhealthy(tmp_path,monkeypatch):
    monkeypatch.setenv('Z0INT_HOME',str(tmp_path))
    assert not automatic.health('omp','never-called')['ok']


def test_delegate_keeps_identity_and_replays(tmp_path,monkeypatch):
    from z0int import worker_routing as w
    monkeypatch.setenv('Z0INT_HOME',str(tmp_path))
    calls=[]
    monkeypatch.setattr(w,'execute_plan',lambda *a,**k:(calls.append(1) or {'ok':True,'output':'once'}))
    req={'trace_id':'stable','task':'Public bounded task','parent_agent':'test','provider':'openrouter','model':'nvidia/nemotron-3-super-120b-a12b:free','reason':'test'}
    first=w.delegate_worker(req);second=w.delegate_worker(req)
    assert calls==[1] and second['replayed']
    assert first['dispatch_receipt_id']==second['dispatch_receipt_id']


def test_mcp_delegate_requires_identity_and_timeout_preserves_it(monkeypatch):
    from z0int import intelligence_mcp as m
    tool=next(t for t in m.TOOLS if t['name']=='delegate_worker')
    assert 'trace_id' in tool['inputSchema']['required']
    monkeypatch.setattr(m.urllib.request,'urlopen',lambda *a,**k:(_ for _ in ()).throw(TimeoutError()))
    result=m.delegate_worker({'trace_id':'stable'})
    assert result['execution_status']=='uncertain' and result['trace_id']=='stable'
