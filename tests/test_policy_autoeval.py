"""Executable deterministic lifecycle tests; inference proof is a separate real run."""
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import struct
from types import SimpleNamespace

import pytest

from z0int.policy.autoeval import discover, due, needs_more, scope_for, tick, wilson


@pytest.fixture(autouse=True)
def isolated_identity_cache(tmp_path, monkeypatch):
    monkeypatch.setattr('z0int.policy.autoeval.paths.home', lambda: tmp_path)


def test_model_identity_cache_avoids_reads_and_invalidates_stat_changes(tmp_path, monkeypatch):
    import os
    import z0int.policy.autoeval as autoeval
    config, path = setup(tmp_path)
    real_hash = autoeval.subprocess.check_output
    calls = []
    def hash_model(command, **kwargs):
        assert command[:6] == ['ionice', '-c', '3', 'nice', '-n', '19']
        calls.append(command)
        return real_hash(command, **kwargs)
    monkeypatch.setattr(autoeval.subprocess, 'check_output', hash_model)
    first = discover(config)
    assert len(calls) == 1
    real_metadata = autoeval.gguf_metadata
    def unexpected_metadata(*_):
        raise AssertionError('Unchanged GGUF was reopened for metadata')
    monkeypatch.setattr(autoeval, 'gguf_metadata', unexpected_metadata)
    assert discover(config) == first
    assert len(calls) == 1
    monkeypatch.setattr(autoeval, 'gguf_metadata', real_metadata)
    stat = path.stat()
    os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1_000_000))
    assert discover(config) == first
    assert len(calls) == 2
    gguf(path, b'new tensor bytes')
    assert discover(config)[0]['sha256'] != first[0]['sha256']
    assert len(calls) == 3


def test_model_identity_rehashes_same_size_restored_mtime(tmp_path):
    import os
    config, path = setup(tmp_path)
    gguf(path, b'aaaa')
    first = discover(config)[0]['sha256']
    stat = path.stat()
    gguf(path, b'bbbb')
    os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns))
    assert discover(config)[0]['sha256'] != first


def test_model_identity_rejects_change_during_hash(tmp_path, monkeypatch):
    import z0int.policy.autoeval as autoeval
    config, path = setup(tmp_path)
    def changed_model(*args, **kwargs):
        gguf(path, b'changed during hashing')
        return '0' * 64
    monkeypatch.setattr(autoeval.subprocess, 'check_output', changed_model)
    with pytest.raises(ValueError, match='changed while computing'):
        discover(config)
    assert not list((tmp_path / 'policy' / 'model-identities').glob('*.json'))



def test_tick_never_reads_uncached_weights_and_resolves_after_hasher(tmp_path, monkeypatch):
    import z0int.policy.autoeval as autoeval
    from z0int.policy.compile import CompilerConfig, compile_policy
    config, path = setup(tmp_path)
    real_metadata = autoeval.gguf_metadata
    real_hash = autoeval.subprocess.check_output
    def forbidden(*args, **kwargs):
        raise AssertionError('Tick attempted to read GGUF bytes')
    monkeypatch.setattr(autoeval, 'gguf_metadata', forbidden)
    monkeypatch.setattr(autoeval.subprocess, 'check_output', forbidden)
    events = tick(tmp_path, config)
    assert events == [{'status': 'identity_pending', 'path': str(path), 'allowed_k': 0}]
    policy = compile_policy([], None, datetime.now(timezone.utc), CompilerConfig(compiler_revision='test'))
    assert policy['option_capacity'] == {}
    monkeypatch.setattr(autoeval, 'gguf_metadata', real_metadata)
    monkeypatch.setattr(autoeval.subprocess, 'check_output', real_hash)
    discover(config)
    monkeypatch.setattr(autoeval, 'gguf_metadata', forbidden)
    monkeypatch.setattr(autoeval.subprocess, 'check_output', forbidden)
    events = tick(tmp_path, config, launch=lambda *args, **kwargs: SimpleNamespace(pid=123456))
    assert len(events) == 1 and events[0]['status'] == 'enqueued'

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
        rows.append({'subject':{'family':'choice.option_capacity','job':'ChoiceLadder','task':task},
                     'metric':{'name':'choice_accuracy','option_count':2, 'value':1., 'n':100,
                               'ci_low':.95, 'ci_method':'wilson95', 'higher_is_better':True},
                     'source':{}, 'evidence_class':'measured',
                     'arm':{**scope_for(model,config['lane']),'model_sha256':model['sha256']},
                     'ts':now.isoformat(),'expires_at':(now+timedelta(days=14)).isoformat()})
    assert not due(model,config['lane'],rows,now,48)
    assert due(model,config['lane'],rows[:-1],now,48)
    assert due(model,config['lane'],rows,now+timedelta(days=13),48)
    changed = {**model,'sha256':'f'*64}
    assert due(changed,config['lane'],rows,now,48)
    for change in ({'job': 'other'}, {'job': None}):
        invalid = [dict(row) for row in rows]
        invalid[-1] = {**invalid[-1], 'subject': {**invalid[-1]['subject'], **change}}
        assert due(model, config['lane'], invalid, now, 48)
    for change in ({'option_count': None}, {'ci_method': 'none'}, {'promotion_eligible': False}):
        invalid = [dict(row) for row in rows]
        invalid[-1] = {**invalid[-1], 'metric': {**invalid[-1]['metric'], **change}}
        assert due(model, config['lane'], invalid, now, 48)


def test_tick_enqueues_once_and_retries_after_dead_worker(tmp_path,monkeypatch):
    config,_ = setup(tmp_path)
    discover(config)
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
    resumed = json.loads(Path(first[0]['request']).read_text())
    assert resumed['run_id'] == request['run_id']
    state_path = Path(first[0]['request'].removesuffix('.request.json') + '.json')
    state = json.loads(state_path.read_text())
    state.update(status='completed', pid=None, retry_after=now.isoformat())
    state_path.write_text(json.dumps(state))
    fresh = tick(tmp_path,config,now+timedelta(hours=8),launch)[0]
    assert json.loads(Path(fresh['request']).read_text())['run_id'] != request['run_id']


@pytest.mark.parametrize('changed', ['legacy', 'lane', 'model_path', 'corpus', 'runner'])
def test_retry_resumes_only_unchanged_complete_inputs(tmp_path, monkeypatch, changed):
    import z0int.policy.autoeval as autoeval
    config, path = setup(tmp_path)
    toolkit = tmp_path/'toolkit'
    toolkit.mkdir()
    (toolkit/'items.jsonl').write_text('first')
    config['toolkit'] = str(toolkit)
    discover(config)
    now = datetime(2026,10,24,tzinfo=timezone.utc)
    launch = lambda *args, **kwargs: SimpleNamespace(pid=123456)
    first = tick(tmp_path, config, now, launch)[0]
    request_path = Path(first['request'])
    original = json.loads(request_path.read_text())
    monkeypatch.setattr(autoeval.os, 'kill', lambda *_: (_ for _ in ()).throw(ProcessLookupError()))
    if changed == 'legacy':
        del original['run_id']
        request_path.write_text(json.dumps(original))
    elif changed == 'lane':
        config['lane']['server_sha256'] = 'b'*64
    elif changed == 'model_path':
        other = gguf(tmp_path/'different.gguf')
        Path(config['registry']).write_text(json.dumps({'endpoints':[{'model_path':str(other)}]}))
        discover(config)
    elif changed == 'corpus':
        (toolkit/'items.jsonl').write_text('second')
    else:
        real = autoeval.run_inputs
        monkeypatch.setattr(autoeval, 'run_inputs', lambda cfg: {**real(cfg), 'runner':'b'*64})
    tick(tmp_path, config, now+timedelta(hours=7), launch)
    assert json.loads(request_path.read_text())['run_id'] != original.get('run_id')
    if changed == 'lane':
        assert json.loads(request_path.read_text())['lane']['server_sha256'] == 'b'*64


def test_invalid_gguf_fails_closed(tmp_path):
    config,path = setup(tmp_path)
    path.write_bytes(b'not GGUF')
    with pytest.raises(ValueError):
        discover(config)


def test_systemd_dispatch_survives_oneshot_and_deduplicates_active_unit(tmp_path,monkeypatch):
    config,_ = setup(tmp_path)
    discover(config)
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


@pytest.mark.parametrize('tamper', [None, 'runtime_sha256', 'rows_sha256', 'items_sha256', 'run_id', 'counts', 'duplicate', 'missing_id'])
def test_mbp_dispatch_stages_importable_package_and_compiles_receipt(tmp_path,monkeypatch,tamper):
    from z0int.policy.autoeval import dispatch, file_sha, run_inputs
    from z0int.policy.compile import option_capacity_key
    import subprocess
    import sys
    config,_ = setup(tmp_path)
    toolkit = tmp_path/'toolkit'
    toolkit.mkdir()
    (toolkit/'benchmark.py').write_text('KS = (2,3,5,8,12,16,24,32,64)\n')
    cells = [(task,k) for task in ('T1','T2') for k in (2,3,5,8,12,16,24,32,64)]
    cells += [('T3',k) for k in (2,3,5,8,12,16)] + [('T4',10)]
    items = [{'id':f'{task}-{k}-{i}', 'task':task, 'k':k, 'answer':0}
             for task,k in cells for i in range(100)]
    (toolkit/'items.jsonl').write_text(''.join(json.dumps(item)+'\n' for item in items))
    for name in ('router.json','oracle.py'):
        (toolkit/name).write_text('{}')
    config.update(toolkit=str(toolkit),router_corpus=str(toolkit/'router.json'),
                  router_harness=str(toolkit/'oracle.py'),compiler_revision='test',
                  benchmark_sha256=file_sha(toolkit/'benchmark.py'),cap=100)
    monkeypatch.setattr('z0int.policy.autoeval.router_supplements', lambda *_: [])
    config['lane'].update(backend='cpu',device='mbp')
    model = discover(config)[0]
    scope = scope_for(model,config['lane'])
    request = tmp_path/'run.request.json'
    request.write_text(json.dumps({'config':config,'model':model,'lane':config['lane'],
                                   'scope':scope,'home':str(tmp_path/'home'),
                                   'input_sha256':run_inputs(config),
                                   'run_id':'a'*32,'requested_at':(datetime.now(timezone.utc)-timedelta(seconds=5)).isoformat()}))
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
        rows = destination/'rows.jsonl'
        observations = [{**item, 'correct': True} for item in items]
        if tamper == 'duplicate':
            observations[1] = dict(observations[0])
        elif tamper == 'missing_id':
            del observations[1]['id']
        rows.write_text(''.join(json.dumps(row)+'\n' for row in observations))
        (destination/'checkpoint-header.json').write_text(json.dumps({'run_id':'a'*32,'started_at':now.isoformat()}))
        result = {'scope':scope, 'run_id':'a'*32, 'runtime_sha256':config['lane']['server_sha256'],
            'rows_sha256':file_sha(rows), 'items_sha256':file_sha(toolkit/'items.jsonl'),
            'model_sha256':model['sha256'],'started_at':now.isoformat(),
            'expires_at':(now+timedelta(days=14)).isoformat(),'cells':cells}
        if tamper == 'counts':
            cells[0]['correct'] = 99
        elif tamper and tamper not in ('duplicate', 'missing_id'):
            result[tamper] = 'bad'
        (destination/'measurement.json').write_text(json.dumps(result))
        return SimpleNamespace(returncode=0)
    monkeypatch.setattr('z0int.policy.autoeval.subprocess.run',worker)
    if tamper:
        with pytest.raises(ValueError):
            dispatch(request)
        assert not (tmp_path/'home/policy/findings.jsonl').exists()
        return
    dispatch(request)
    policy = json.loads((tmp_path/'home/policy/decision-policy.json').read_text())
    capacities = policy['option_capacity'][option_capacity_key(scope)]
    assert set(capacities) == {'read','mutating_reversible','destructive'}
    assert capacities['destructive']['allowed_k'] == 0
    assert all(row['allowed_k'] == 0 for row in capacities.values())


def test_fresh_run_never_reuses_prior_cell_checkpoint(tmp_path):
    from z0int.policy.autoeval import sample_cell
    items = [{'id': str(i), 'task': 'T1', 'k': 2, 'answer': 0} for i in range(100)]
    first, fresh = tmp_path/'old.json', tmp_path/'fresh.json'
    calls = []
    def score(item):
        calls.append(item['id'])
        return {'correct': False}
    sample_cell(items, score, first, 'old-run')
    sample_cell(items, score, first, 'old-run')
    assert len(calls) == 100
    sample_cell(items, score, fresh, 'new-run')
    assert len(calls) == 200
    with pytest.raises(ValueError, match='identity'):
        sample_cell(items, score, first, 'new-run')


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
