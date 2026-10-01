import concurrent.futures
import json
import threading
import time
import pytest
from z0int import intelligence as i
from z0int.receipt import append_receipt, receipts_path


def route(a):
    return i.route(a,i.routing_snapshot(a))


def args(**values):
    return {'harness':'codex','trace_id':'t','parent_agent':'p','function':'cheap_bounded_worker','task':'two plus two','expected_parent_tokens':10,'expected_parent_ms':100,**values}


def test_tiny_and_unknown_stay_parent():
    assert route(args())['kind']=='PARENT_ONLY'
    assert '1347' in route(args())['reason']
    assert route(args(expected_parent_tokens=500,expected_parent_ms=100))['kind']=='PARENT_ONLY'


def test_jev_requires_explicit_remote_and_exact_state(monkeypatch):
    from types import SimpleNamespace
    monkeypatch.setattr(i, "create_backend", lambda name: SimpleNamespace(health=lambda: SimpleNamespace(configured=True)))
    # The gate under test is remote opt-in + exact state, not whether this host holds a Jev key.
    monkeypatch.setattr(i, "ensure_credential", lambda: "test-key")
    assert route(args(function='evidence_sufficiency'))['kind']=='PARENT_ONLY'
    with pytest.raises(ValueError):route(args(function='evidence_sufficiency',allow_remote=True))
    assert route(args(function='evidence_sufficiency',allow_remote=True,state={'question':'claim','evidence':'text'}))['kind']=='JEV_FUNCTION'


def test_laya_not_promoted_and_nan_rejected():
    assert route(args(function='verification_needed'))['kind']=='PARENT_ONLY'
    with pytest.raises(ValueError):route(args(expected_parent_ms=float('nan')))
    with pytest.raises(ValueError):route(args(function='verification_needed',experimental=True,state='paraphrase'))


def test_concurrent_same_trace_executes_once_and_conflict_fails(tmp_path,monkeypatch):
    monkeypatch.setenv('Z0INT_HOME',str(tmp_path))
    calls=[]
    def execute(a,r,**kwargs):
        calls.append(a);time.sleep(.08);return {'ok':True,'executed':False,'requires_parent':True}
    monkeypatch.setattr(i,'execute',execute)
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        results=list(pool.map(i.dispatch,[args()]*4))
    assert len(calls)==1
    assert sum(r.get('replayed',False) for r in results)==3
    with pytest.raises(ValueError):i.dispatch(args(task='different'))
    assert len([json.loads(l) for l in receipts_path().read_text().splitlines()])==2


def test_uncertain_dispatch_never_reexecutes(tmp_path,monkeypatch):
    monkeypatch.setenv('Z0INT_HOME',str(tmp_path))
    def crash(*a,**kwargs):raise KeyboardInterrupt()
    monkeypatch.setattr(i,'execute',crash)
    with pytest.raises(KeyboardInterrupt):i.dispatch(args())
    assert 'uncertain' in i.dispatch(args())['reason']


def test_receipt_concurrent_large_rows_are_parseable(tmp_path,monkeypatch):
    monkeypatch.setenv('Z0INT_HOME',str(tmp_path))
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(lambda n:append_receipt({'trace_id':str(n),'extra':{'data':'x'*20000}}),range(30)))
    rows=[json.loads(l) for l in receipts_path().read_text().splitlines()]
    assert len(rows)==len({r['trace_id'] for r in rows})==30


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
