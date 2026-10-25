"""Executable deterministic lifecycle tests; inference proof is a separate real run."""
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import struct
from types import SimpleNamespace

import pytest

from z0int.policy.autoeval import discover, due, needs_more, scope_for, tick, wilson


def gguf(path, extra=b''):
    def text(value):
        data = value.encode()
        return struct.pack('<Q',len(data))+data
    path.write_bytes(b'GGUF'+struct.pack('<IQQ',3,0,2)+text('general.architecture')+
                     struct.pack('<I',8)+text('qwen3')+text('general.file_type')+struct.pack('<II',4,2)+extra)
    return path


def setup(tmp_path):
    model = gguf(tmp_path/'weights.gguf')
    registry = tmp_path/'serving.json'
    registry.write_text(json.dumps({'endpoints':[{'model_id':'alias1','model_path':str(model)}]}))
    config = {'registry':str(registry),'model_dirs':[],'dispatcher':'process',
              'lane':{'backend':'cuda','device':'groot','engine':'test-build','server_sha256':'0'*64},'toolkit':'/synthetic/toolkit'}
    return config,model


def test_discovery_uses_weight_identity_not_alias(tmp_path):
    config,path = setup(tmp_path)
    first = discover(config)[0]
    registry = Path(config['registry'])
    registry.write_text(json.dumps({'endpoints':[{'model_id':'different alias','model_path':str(path)}]}))
    assert discover(config)[0]['id']==first['id']
    gguf(path,b'distinct tensors')
    assert discover(config)[0]['id']!=first['id']
    assert first['quantization']=='Q4_0'


def test_adaptive_sample_gate_and_cap():
    assert needs_more(98,100)
    assert not needs_more(94,100)
    assert not needs_more(100,100)
    assert not needs_more(392,400)
    n,correct = 100,98
    while needs_more(correct,n):
        n+=1
        correct+=1
    assert 100<n<=400
    assert wilson(correct,n)[0]>=.95


def test_due_requires_all_tasks_and_refreshes_before_expiry(tmp_path):
    config,_ = setup(tmp_path)
    model = discover(config)[0]
    now = datetime(2026,10,24,tzinfo=timezone.utc)
    rows = []
    for task in ('T1','T2','T3','T4'):
        rows.append({'subject':{'family':'choice.option_capacity','task':task},
                     'metric':{'name':'choice_accuracy'},'evidence_class':'measured',
                     'arm':{**scope_for(model,config['lane']),'model_sha256':model['sha256']},
                     'ts':now.isoformat(),'expires_at':(now+timedelta(days=14)).isoformat()})
    assert not due(model,config['lane'],rows,now,48)
    assert due(model,config['lane'],rows[:-1],now,48)
    assert due(model,config['lane'],rows,now+timedelta(days=13),48)
    changed = {**model,'sha256':'f'*64}
    assert due(changed,config['lane'],rows,now,48)


def test_tick_enqueues_once_and_retries_after_dead_worker(tmp_path,monkeypatch):
    config,_ = setup(tmp_path)
    calls = []
    def launch(command,**kwargs):
        calls.append(command)
        return SimpleNamespace(pid=123456)
    now = datetime(2026,10,24,tzinfo=timezone.utc)
    first = tick(tmp_path,config,now,launch)
    assert first[0]['status']=='enqueued'
    request = json.loads(Path(first[0]['request']).read_text())
    assert request['scope']['model'].startswith('sha256:')
    monkeypatch.setattr('z0int.policy.autoeval.os.kill',lambda *_: None)
    assert tick(tmp_path,config,now,launch)==[]
    def dead(*_):
        raise ProcessLookupError()
    monkeypatch.setattr('z0int.policy.autoeval.os.kill',dead)
    assert tick(tmp_path,config,now+timedelta(hours=1),launch)==[]
    assert len(tick(tmp_path,config,now+timedelta(hours=7),launch))==1
    assert len(calls)==2


def test_invalid_gguf_fails_closed(tmp_path):
    config,path = setup(tmp_path)
    path.write_bytes(b'not GGUF')
    with pytest.raises(ValueError):
        discover(config)


def test_systemd_dispatch_survives_oneshot_and_deduplicates_active_unit(tmp_path,monkeypatch):
    config,_ = setup(tmp_path)
    config['dispatcher'] = 'systemd'
    commands = []
    def launch(command,**kwargs):
        commands.append(command)
        return SimpleNamespace(pid=123456)
    now = datetime(2026,10,24,tzinfo=timezone.utc)
    event = tick(tmp_path,config,now,launch)[0]
    assert commands[0][:3]==['systemd-run','--user','--collect']
    assert event['unit'].startswith('z0int-choice-')
    monkeypatch.setattr('z0int.policy.autoeval.subprocess.run',lambda *args,**kwargs: SimpleNamespace(returncode=0))
    assert tick(tmp_path,config,now+timedelta(hours=7),launch)==[]
    assert len(commands)==1


def test_mbp_dispatch_stages_importable_package_and_compiles_receipt(tmp_path,monkeypatch):
    from z0int.policy.autoeval import dispatch
    from z0int.policy.compile import option_capacity_key
    import subprocess
    import sys
    config,_ = setup(tmp_path)
    toolkit = tmp_path/'toolkit'
    toolkit.mkdir()
    for name in ('benchmark.py','items.jsonl','router.json','oracle.py'):
        (toolkit/name).write_text('{}')
    config.update(toolkit=str(toolkit),router_corpus=str(toolkit/'router.json'),
                  router_harness=str(toolkit/'oracle.py'),compiler_revision='test')
    config['lane'].update(backend='cpu',device='mbp')
    model = discover(config)[0]
    scope = scope_for(model,config['lane'])
    request = tmp_path/'run.request.json'
    request.write_text(json.dumps({'config':config,'model':model,'lane':config['lane'],
                                   'scope':scope,'home':str(tmp_path/'home')}))
    real_run = subprocess.run
    def worker(command,**kwargs):
        assert command[:7]==['fleet-build','--node','mbp','--class','bench','--mem','4G']
        stage = Path(command[command.index('--src')+1])
        result = real_run([sys.executable,'-c','import z0int.policy.autoeval'],cwd=stage,check=False)
        assert result.returncode==0
        cells = [{'task':task,'k':k,'n':100,'correct':100} for task in ('T1','T2')
                 for k in (2,3,5,8,12,16,24,32,64)]
        cells += [{'task':'T3','k':k,'n':100,'correct':100} for k in (2,3,5,8,12,16)]
        cells += [{'task':'T4','k':10,'n':100,'correct':100}]
        destination = stage/'fetched'
        destination.mkdir()
        now = datetime.now(timezone.utc)
        (destination/'measurement.json').write_text(json.dumps({'scope':scope,
            'model_sha256':model['sha256'],'started_at':now.isoformat(),
            'expires_at':(now+timedelta(days=14)).isoformat(),'cells':cells}))
        return SimpleNamespace(returncode=0)
    monkeypatch.setattr('z0int.policy.autoeval.subprocess.run',worker)
    dispatch(request)
    policy = json.loads((tmp_path/'home/policy/decision-policy.json').read_text())
    assert policy['option_capacity'][option_capacity_key(scope)]['allowed_k']==10


def test_adaptive_cell_resumes_atomic_checkpoint_without_rescoring(tmp_path):
    from z0int.policy.autoeval import sample_cell
    items = [{'id':str(i),'task':'T1','k':2,'answer':0} for i in range(400)]
    scored = []
    def interrupted(item):
        index = int(item['id'])
        if index==101:
            raise RuntimeError('preempted')
        scored.append(index)
        return {'correct':index not in (0,1)}
    checkpoint = tmp_path/'cell.json'
    with pytest.raises(RuntimeError,match='preempted'):
        sample_cell(items,interrupted,checkpoint,'identity')
    assert len(json.loads(checkpoint.read_text())['rows'])==101
    def resume(item):
        index = int(item['id'])
        scored.append(index)
        return {'correct':True}
    result = sample_cell(items,resume,checkpoint,'identity')
    assert len(result)>101
    assert len(scored)==len(set(scored))==len(result)
    assert wilson(sum(row['correct'] for row in result),len(result))[0]>=.95
    with pytest.raises(ValueError,match='identity'):
        sample_cell(items,resume,checkpoint,'different weights')


def test_native_template_renderer_uses_exact_option_interface():
    from z0int.policy.autoeval import runtime_prompt
    calls = []
    def post(base,path,payload):
        calls.append((base,path,payload))
        return {'prompt':'model-native-rendered-prompt'}
    benchmark = SimpleNamespace(SLOTS='ABC',post=post)
    assert runtime_prompt(benchmark,'http://localhost',{'question':'pick','options':['yes','no']})=='model-native-rendered-prompt'
    base,path,payload = calls[0]
    assert path=='/apply-template'
    assert payload['messages'][1]['content']=='pick\n\nOptions:\nA: yes\nB: no'
    assert payload['chat_template_kwargs']=={'enable_thinking':False}


def test_router_supplements_stop_at_distinct_corpus_bound(tmp_path):
    from z0int.policy.autoeval import file_sha,router_supplements
    corpus = tmp_path/'corpus.json'
    oracle = tmp_path/'oracle.py'
    oracle.write_text('def oracle(task,initial,state,unused):\n    return False\n')
    nodes = [{'id':str(i),'user':'Open app '+str(i),'snapshot':{},'initial_snapshot':{},
              'task_spec':{'oracle':{'kind':'app'}}} for i in range(3)]
    corpus.write_text(json.dumps({'nodes':nodes}))
    config = {'router_corpus':str(corpus),'router_harness':str(oracle),
              'router_corpus_sha256':file_sha(corpus),'router_harness_sha256':file_sha(oracle),'cap':400}
    benchmark = SimpleNamespace(DESCRIPTIONS=['OPEN','DONE'],ACTIONS=['OPEN','DONE'])
    rows = router_supplements(benchmark,config,[{'source_node':'0'}])
    assert {row['source_node'] for row in rows}=={'1','2'}
    assert all(row['options'][row['answer']]=='OPEN' for row in rows)
