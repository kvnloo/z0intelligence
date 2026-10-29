import json
from pathlib import Path
import time

from z0int.cognition.learning import learn, wilson, connect, STRATEGIES, predict
from z0int.cognition.shadow import run_shadow
from z0int.cognition.registry import LocalModelRegistry, ServingEndpoint
from z0int.cognition.manifest import load_local_cognition


def fixture(tmp_path, n=60):
    home = tmp_path/'home'
    sessions = tmp_path/'sessions'
    sessions.mkdir()
    (home/'shadow').mkdir(parents=True)
    events = [{'type':'session', 'id':'session'}]
    receipts = []
    now = time.time()
    for i in range(n):
        events.extend([
            {'type':'message','message':{'role':'assistant','content':[{'type':'toolCall','id':str(i),'name':'read'}]}},
            {'type':'message','message':{'role':'toolResult','toolCallId':str(i),'isError':i==2,'details':{'exitCode':1 if i==2 else 0}}},
        ])
        receipts.append({'schema':'z0int.cognition.shadow.receipt.v1','trace_id':str(i),
                         'session_id':'session','tool_call_id':str(i),'ts':now-100+i,
                         'shadow':[{'model':'test','selected_action':'read','confidence':.95}],
                         'executed_action':None})
    (sessions/'session.jsonl').write_text(''.join(json.dumps(e)+'\n' for e in events))
    (home/'shadow'/'cognition-shadow.jsonl').write_text(''.join(json.dumps(r)+'\n' for r in receipts))
    return home, sessions, now


def test_scoped_arms_remain_distinct_and_zero_rpc_cannot_receive_credit(tmp_path):
    from z0int.cognition.learning import score, arm_scores
    db=connect(tmp_path)
    for i in range(3):
        shadow=[
            {'model':'m','arm':'slm-method:alpha','selected_action':'read','rpc_count':1},
            {'model':'m','arm':'slm-method:beta','selected_action':'edit','rpc_count':1},
            {'model':'m','arm':'slm-method:zero','selected_action':'read','rpc_count':0}]
        db.execute('INSERT INTO receipts VALUES(?,?,?,?,?,?)',
                   (str(i),'s',str(i),100+i,'omp.tool',json.dumps({'shadow':shadow})))
        db.execute('INSERT INTO labels VALUES(?,?,?,?,?,?,?,?,?)',
                   (str(i),'s',str(i),'omp.tool',100+i,'read','[]',None,120))
    assert score(db,.8,time.monotonic()+30)
    arms=arm_scores(db)['omp.tool']
    assert arms['slm:m:slm-method:alpha']['correct']==3
    assert arms['slm:m:slm-method:beta']['correct']==0
    assert arms['slm:m:slm-method:zero']['n']==0
    db.close()


def test_preupgrade_pooled_scoped_scores_rebuild_from_receipts_idempotently(tmp_path):
    from z0int.cognition.learning import score, arm_scores
    db=connect(tmp_path)
    for i, method in enumerate(('alpha','beta','alpha')):
        shadow={'model':'m','arm':'slm-method:'+method,'rpc_count':1,
                'selected_action':'read' if method=='alpha' else 'edit'}
        db.execute('INSERT INTO receipts VALUES(?,?,?,?,?,?)',
                   (str(i),'s',str(i),100+i,'omp.tool',json.dumps({'shadow':[shadow]})))
        db.execute('INSERT INTO labels VALUES(?,?,?,?,?,?,?,?,?)',
                   (str(i),'s',str(i),'omp.tool',100+i,'read','[]',None,120))
        db.execute('INSERT INTO examples VALUES(?,?,?,?,?)',
                   (str(i),'omp.tool','read','[]',100+i))
        db.execute('INSERT INTO evaluated VALUES(?,?)',(str(i),'slm:m'))
    db.execute('INSERT INTO scores VALUES(?,?,?,?,?)',('omp.tool','slm:m',3,3,3))
    db.execute('INSERT INTO scores VALUES(?,?,?,?,?)',('omp.tool','slm:other',4,4,4))
    assert score(db,.8,time.monotonic()+30)
    arms=arm_scores(db)['omp.tool']
    assert 'slm:m' not in arms
    assert (arms['slm:m:slm-method:alpha']['total'],arms['slm:m:slm-method:alpha']['correct'])==(2,2)
    assert (arms['slm:m:slm-method:beta']['total'],arms['slm:m:slm-method:beta']['correct'])==(1,0)
    assert arms['slm:other']['correct']==4
    assert score(db,.8,time.monotonic()+30)
    assert arm_scores(db)['omp.tool']==arms
    db.close()


def test_preupgrade_partial_scoped_pool_preserves_original_unverified_snapshot(tmp_path):
    from z0int.cognition.learning import score, arm_scores
    db = connect(tmp_path)
    shadow = {'model': 'm', 'arm': 'slm-method:alpha', 'rpc_count': 1, 'selected_action': 'read'}
    db.execute('INSERT INTO receipts VALUES(?,?,?,?,?,?)',
               ('retained', 's', 'c', 100, 'omp.tool', json.dumps({'shadow': [shadow]})))
    db.execute('INSERT INTO labels VALUES(?,?,?,?,?,?,?,?,?)',
               ('retained', 's', 'c', 'omp.tool', 100, 'read', '[]', None, 120))
    db.execute('INSERT INTO examples VALUES(?,?,?,?,?)', ('retained', 'omp.tool', 'read', '[]', 100))
    db.execute('INSERT INTO evaluated VALUES(?,?)', ('retained', 'slm:m'))
    db.execute('INSERT INTO scores VALUES(?,?,?,?,?)', ('omp.tool', 'slm:m', 100, 80, 60))
    assert score(db, .8, time.monotonic()+30)
    arms = arm_scores(db)['omp.tool']
    snapshot = arms['slm:m:legacy-pooled-snapshot']
    assert (snapshot['total'], snapshot['n'], snapshot['correct']) == (100, 80, 60)
    assert arms['slm:m:slm-method:alpha']['total'] == 1
    assert db.execute('SELECT 1 FROM learning_unverified_arms WHERE family=? AND arm=?',
                      ('omp.tool', 'slm:m:legacy-pooled-snapshot')).fetchone()
    assert score(db, .8, time.monotonic()+30)
    assert arm_scores(db)['omp.tool'] == arms
    db.close()


def test_preupgrade_lost_pooled_history_remains_unverified(tmp_path):
    from z0int.cognition.learning import score, arm_scores
    db = connect(tmp_path)
    db.execute('INSERT INTO scores VALUES(?,?,?,?,?)', ('omp.tool', 'slm:m', 100, 80, 60))
    assert score(db, .8, time.monotonic()+30)
    arms = arm_scores(db)['omp.tool']
    assert (arms['slm:m']['total'], arms['slm:m']['n'], arms['slm:m']['correct']) == (100, 80, 60)
    assert db.execute('SELECT 1 FROM learning_unverified_arms WHERE family=? AND arm=?',
                      ('omp.tool', 'slm:m')).fetchone()
    assert score(db, .8, time.monotonic()+30)
    assert arm_scores(db)['omp.tool'] == arms
    db.close()


def test_prequential_join_credit_and_incremental_stall(tmp_path):
    home, sessions, now = fixture(tmp_path)
    first = learn(home, sessions=sessions, now=now)
    assert first['labelled_rows_added'] == 60
    assert first['labelled_per_family'] == {'omp.tool':60}
    assert first['families']['omp.tool']['history-1']['n'] == 58
    assert first['families']['omp.tool']['slm:test']['n'] == 60
    assert first['credited_families'] == ['omp.tool']
    assert first['status'] == 'LEARNING'
    db = connect(home)
    error = json.loads(db.execute("SELECT outcome FROM labels WHERE id='2'").fetchone()[0])
    assert error['tool_error'] is True and error['exit_code'] == 1
    db.close()
    second = learn(home, sessions=sessions, now=now+86401)
    assert second['status'] == 'DEGRADED'
    assert 'labels_not_growing' in second['degraded_by']
    assert 'plateau:omp.tool' in second['degraded_by']
    assert second['evolution']['id'] == 'history-1'
    third = learn(home, sessions=sessions, now=now+86402)
    assert third['evolution']['id'] == 'history-3'
    ledger = json.loads((home/'loop'/'strategy-ledger.json').read_text())
    assert ledger['trials'][0]['result'] == 'failed'
    assert len({t['id'] for t in ledger['trials']}) == len(ledger['trials'])


def test_unattested_shadow_accuracy_does_not_credit_family(tmp_path):
    home, sessions, now = fixture(tmp_path, 1)
    report=learn(home,sessions=sessions,now=now,
                 config={'min_observations':1,'credit_bar':0})
    assert report['families']['omp.tool']['slm:test']['correct']==1
    assert report['credited_families']==[]


def test_dead_endpoint_degrades_and_evolves(tmp_path, monkeypatch):
    home, sessions, now = fixture(tmp_path, 1)
    registry = LocalModelRegistry(load_local_cognition(), {'qwen3_06b': ServingEndpoint(
        'qwen3_06b','http://127.0.0.1:1','qwen3-0.6b-q8','llama.cpp',timeout_s=.1)})
    monkeypatch.setenv('Z0INT_HOME', str(home))
    result = run_shadow({'session_id':'session','facts':{'tool_call_id':'0'},
        'actions':[{'action_id':'read','tool':'read','family':'omp.tool'}], 'authority':['read'],
        'shadows':['qwen3_06b'], 'state':'previous tools: none'}, registry=registry)
    assert result['selected_action'] is None and result['executed_action'] is None
    assert result['shadow'][0]['failure_class'] == 'connection_refused'
    assert result['shadow'][0].get('parse_error') is None
    report = learn(home, sessions=sessions, now=now+1)
    assert report['status'] == 'DEGRADED'
    assert report['transport_error_rate'] > .05
    assert report['evolution']['result'] == 'running'
    assert report['evolution']['hypothesis']


def test_wilson_and_no_current_label_leak():
    assert wilson(0, 0) == 0
    assert wilson(5, 5) < .8
    assert wilson(60, 60) > .9
    assert predict(['read'], [], STRATEGIES[0]) == (None, 0)


def test_tick_surfaces_cognition_degradation(tmp_path, monkeypatch):
    from z0int import loop_tick as tick
    home, sessions, now = fixture(tmp_path, 0)
    monkeypatch.setenv('Z0INT_OMP_SESSIONS', str(sessions))
    monkeypatch.setattr(tick, 'STEPS', ('cognition',))
    monkeypatch.setattr(tick.le, 'scan_records', lambda _: {})
    report = tick.run_tick(home, lock=tmp_path/'lock', budget_s=10)
    assert report['status'] == 'degraded'
    assert report['exit'] == tick.EXIT_DEGRADED
    assert report['cognition']['evolution']['result'] == 'running'


def test_failure_retention_preserves_backfill(tmp_path, monkeypatch):
    from z0int.cognition.shadow import append_shadow_receipt
    monkeypatch.setenv('Z0INT_HOME', str(tmp_path))
    folder = tmp_path/'shadow'
    folder.mkdir()
    original = folder/'cognition-shadow.jsonl'
    original.write_text('historical backfill input\n')
    for index in range(20):
        append_shadow_receipt({'state':'x'*220000, 'shadow':[
            {'backend':None,'parse_error':'transport_error: refused'}]})
    assert original.read_text() == 'historical backfill input\n'
    health = json.loads((folder/'cognition-shadow-health.json').read_text())
    assert health['failed_calls'] == 20 and health['successful_calls'] == 0
    files = list(folder.glob('cognition-shadow-failures.jsonl*'))
    assert len(files) == 4
    assert all(p.stat().st_size < 1_048_576+221000 for p in files)


def test_shadow_does_not_block_bridge_requests(monkeypatch):
    import io
    import threading
    from z0int.bridge import worker
    release = threading.Event()
    done = threading.Event()
    class Runtime:
        def __init__(self, **kw):
            pass
        def identity(self):
            return {'kind':'fixture'}
        def cognition_shadow(self, payload):
            release.wait(2)
            return {'ok':True}
    output = io.StringIO()
    monkeypatch.setattr(worker, 'BridgeRuntime', Runtime)
    monkeypatch.setattr(worker.sys, 'stdin', io.StringIO(
        '{"id":"slow","op":"cognition_shadow"}\n{"id":"hello","op":"hello"}\n'))
    monkeypatch.setattr(worker.sys, 'stdout', output)
    original = worker._shadow_response
    def tracked(*args):
        try:
            original(*args)
        finally:
            done.set()
    monkeypatch.setattr(worker, '_shadow_response', tracked)
    worker.serve()
    assert [json.loads(x)['id'] for x in output.getvalue().splitlines()] == ['hello']
    release.set()
    assert done.wait(2)


def test_missing_new_identity_is_quarantined_and_degrades(tmp_path, monkeypatch):
    from z0int.cognition.shadow import append_shadow_receipt
    home, sessions, now = fixture(tmp_path, 3)
    monkeypatch.setenv('Z0INT_HOME', str(home))
    path = append_shadow_receipt({'schema':'z0int.cognition.shadow.receipt.v1',
        'trace_id':'missing-native-id','ts':now,'identity_contract':'session-tool-call.v1',
        'session_id':None,'tool_call_id':None,
        'shadow':[{'backend':'fake','model':'fake','selected_action':'read','confidence':1}]})
    assert path.name == 'cognition-shadow-failures.jsonl'
    report = learn(home, sessions=sessions, now=now+1)
    assert report['new_writer_health']['identity_failures_day'] == 1
    assert 'labels_identity_invariant' in report['degraded_by']
    assert report['labelled_per_family'] == {'omp.tool':3}


def large_omp_payload():
    names = ["read", "bash"] + [
        f"mcp__t3_groot_long_collaborative_browser_and_thread_operation_{i:03}" for i in range(117)
    ] + ["zz_observed_tool"]
    return {'session_id':'real-sized-session', 'trace_id':'real-sized-trace',
        'facts':{'tool_call_id':'actual-call', 'tool_name':'zz_observed_tool'},
        'state':'OMP session. Previous tool calls, oldest first: read, bash, read',
        'recent_tools':['read','bash','read'], 'session_tools':['read','bash'],
        'goal_context':{'latest_user_message':'Inspect the parser and its configuration.',
                        'last_assistant_sentence':'I will read the parser.',
                        'previous_tools':[{'tool':'read','intent':'Inspect parser context'}]},
        'actions':[{'action_id':name,'tool':name,'family':'omp.tool',
                    'description':f'Call the OMP tool {name}',
                    'arguments_schema':{'type':'object','description':'schema text '*100}}
                   for name in names],
        'authority':['read'], 'budget_units':len(names), 'shadows':['qwen3_06b']}


def sized_backend(transport):
    from z0int.cognition.adapters.local_slm import LocalSLMBackend
    from z0int.cognition.adapters.transport import ServerConfig
    from z0int.cognition.adapters.dialects import PLAIN_JSON_DIALECT
    return LocalSLMBackend(backend_id='qwen3_06b',
        config=ServerConfig('http://fixture','qwen3-0.6b-q8'),
        dialect=PLAIN_JSON_DIALECT, transport=transport)


def test_real_sized_omp_prompt_is_bounded_without_losing_scoring_actions(tmp_path, monkeypatch):
    from z0int.cognition.adapters.transport import ChatOutcome
    monkeypatch.setenv('Z0INT_HOME',str(tmp_path))
    class Transport:
        def __init__(self):
            self.requests=[]
        def chat(self,messages,**kw):
            self.requests.append((messages,kw))
            encoded=len(json.dumps({'messages':messages,'max_tokens':kw['max_tokens'],
                'temperature':kw['temperature'],'seed':kw['seed'],'logprobs':kw['logprobs'],
                'top_logprobs':kw['top_logprobs'],**kw['extra_body']}).encode())+512
            assert encoded + 1 <= 4096
            assert kw['max_tokens'] == 1 and kw['top_logprobs'] == 20
            assert 'tools' not in kw and 'response_format' not in kw
            candidate=json.loads(messages[1]['content'])['candidate']['id']
            positive=candidate=='read'
            yes,no=(-.2,-2.) if positive else (-2.,-.2)
            token,value=('yes',yes) if positive else ('no',no)
            raw={'choices':[{'logprobs':{'content':[{'token':token,'logprob':value,
                'top_logprobs':[{'token':'yes','logprob':yes},{'token':'no','logprob':no}]}]}}]}
            return ChatOutcome(token,None,{'prompt_tokens':700,'completion_tokens':1},{},raw,1)
    transport=Transport()
    backend=sized_backend(transport)
    class Registry:
        def served(self): return ('qwen3_06b',)
        def backend_for(self,*a,**kw): return backend
    payload=large_omp_payload()
    result=run_shadow(payload,registry=Registry())
    assert len(result['legal_ids']) == 120
    assert 'zz_observed_tool' in result['legal_ids']
    model=result['shadow'][0]
    assert model['selected_action'] == 'read'
    assert model['label'] == 'goal-binary'
    assert model['requested_model'] == 'qwen3_06b'
    assert len(model['model_candidate_ids']) == 3
    assert 'zz_observed_tool' not in model['model_candidate_ids']
    assert len(model['candidate_scores']) == 3
    assert all(call['prompt_tokens'] == 700 for call in model['diagnostics']['calls'])
    # Changing the observed action cannot alter model inputs or candidate rank.
    payload['facts']['tool_name']='bash'
    run_shadow(payload,registry=Registry(),write=False)
    assert transport.requests[:3] == transport.requests[3:]


def test_context_overflow_has_distinct_failure_counter(tmp_path, monkeypatch):
    from z0int.cognition.adapters.transport import TransportError
    monkeypatch.setenv('Z0INT_HOME',str(tmp_path))
    class Transport:
        def chat(self,*a,**kw):
            raise TransportError('HTTP 400 request (4811 tokens) exceeds the available context size (4096 tokens)')
    backend=sized_backend(Transport())
    class Registry:
        def served(self): return ('qwen3_06b',)
        def backend_for(self,*a,**kw): return backend
    result=run_shadow(large_omp_payload(),registry=Registry())
    assert result['shadow'][0]['failure_class'] == 'context_overflow'
    assert 'exceeds the available context size' in result['shadow'][0]['error']
    assert result['shadow'][0].get('parse_error') is None
    assert Path(result['receipts_path']).name == 'cognition-shadow-failures.jsonl'
    health=json.loads((tmp_path/'shadow'/'cognition-shadow-health.json').read_text())
    today=time.strftime('%Y-%m-%d',time.gmtime())
    assert health['days'][today]['failure_classes']['context_overflow'] == 1


def test_low_candidate_recall_never_expands_the_small_model_option_cap(tmp_path):
    home,sessions,now=fixture(tmp_path,20)
    path=home/'shadow'/'cognition-shadow.jsonl'
    receipts=[json.loads(line) for line in path.read_text().splitlines()]
    for row in receipts:
        row['shadow'][0]['model_candidate_ids']=['bash']
        row['projection_config']={'candidate_k':3,'max_options':3,'prompt_bytes':3000}
    path.write_text(''.join(json.dumps(r)+'\n' for r in receipts))
    result=learn(home,sessions=sessions,now=now)
    metric=result['projection_recall']['omp.tool']
    assert metric['n'] == 20 and metric['hits'] == 0 and metric['recall'] == 0
    assert result['labelled_per_family'] == {'omp.tool':20}
    assert result['evolution']['id'] == 'history-1'
    assert result['projection_config'] == {'candidate_k':3,'max_options':3,'prompt_bytes':3000}
    assert result['evolution']['config']['candidate_k'] == 3
    assert result['evolution']['config']['max_options'] == 3


def test_local_abstention_cannot_mask_slm_failure_retention(tmp_path, monkeypatch):
    from z0int.cognition.shadow import append_shadow_receipt
    monkeypatch.setenv('Z0INT_HOME',str(tmp_path))
    path=append_shadow_receipt({'shadow':[
        {'backend':'slm','parse_error':'context_overflow: oversized'},
        {'backend':'shadow-portfolio','selected_action':None,'abstained':True}]})
    assert path.name == 'cognition-shadow-failures.jsonl'


def test_transport_classification_counts_legacy_errors_without_relabelling(tmp_path):
    from z0int.cognition.learning import transport_metrics
    from z0int.cognition.shadow import failure_class
    db = connect(tmp_path)
    failures = [
        ('transport_error: [Errno 111] Connection refused', 'connection_refused'),
        ('shadow_timeout', 'timeout'),
        ('transport_error: HTTP 503 temporarily unavailable', 'http_5xx'),
        ('transport_error: queue full', 'queue_full'),
        ('request exceeds context size 4096', 'context_overflow'),
        ('RuntimeError: functiongemma_270m is not served', 'model_not_served'),
        ('unparseable_response', 'parse_error'),
        ('non_binary_response', 'parse_error'),
        ('invalid_call:rm', 'invalid_call'),
    ]
    for index, (error, kind) in enumerate(failures):
        assert failure_class(error) == kind
        body = json.dumps({'shadow': [{'model': 'slm', 'parse_error': error}]})
        db.execute('INSERT INTO receipts VALUES(?,?,?,?,?,?)',
                   (str(index), 's', str(index), 100, 'omp.tool', body))
    metrics = transport_metrics(db, 0)
    assert metrics['attempts'] == 9
    assert metrics['errors'] == 6
    assert metrics['parse_errors'] == 2
    assert metrics['failure_classes']['connection_refused'] == 1
    assert json.loads(db.execute("SELECT body FROM receipts WHERE id='0'").fetchone()[0])['shadow'][0]['parse_error']
    db.close()


def test_argument_and_result_timestamp_join_is_exact(tmp_path):
    from z0int.cognition.learning import ingest_sessions
    home, sessions, now = fixture(tmp_path, 0)
    events = [
        {'type': 'session', 'id': 's'},
        {'timestamp': '2026-10-24T22:00:00Z', 'message': {'role': 'assistant',
         'content': [{'type': 'toolCall', 'id': 'one', 'name': 'read',
                      'arguments': {'path': 'skill://ctx'}}]}},
        {'timestamp': '2026-10-24T22:00:01Z', 'message': {'role': 'toolResult',
         'toolCallId': 'other', 'isError': True}},
        {'timestamp': '2026-10-24T22:00:02Z', 'message': {'role': 'toolResult',
         'toolCallId': 'one', 'isError': False}},
    ]
    (sessions/'exact.jsonl').write_text(''.join(json.dumps(e)+'\n' for e in events))
    db = connect(home)
    assert ingest_sessions(db, sessions, time.monotonic()+5)
    row = db.execute("SELECT * FROM calls WHERE session='s' AND call='one'").fetchone()
    assert json.loads(row['arguments']) == {'path': 'skill://ctx'}
    assert json.loads(row['outcome'])['tool_error'] is False
    assert row['outcome_ts'] - row['ts'] == 2
    assert json.loads(row['features']) == []
    db.close()


def test_binary_margin_coverage_does_not_reapply_multiclass_confidence_gate(tmp_path):
    home, sessions, now = fixture(tmp_path, 2)
    path = home/'shadow'/'cognition-shadow.jsonl'
    receipts = [json.loads(line) for line in path.read_text().splitlines()]
    for index, row in enumerate(receipts):
        row['projection_strategy'] = 'history-1'
        row['projection_config'] = {'candidate_k': 3}
        row['shadow'] = [{'arm': 'goal-binary', 'model': 'actual-slm',
            'backend': 'actual-backend', 'selected_action': 'read' if index == 0 else None,
            'confidence': .6 if index == 0 else None, 'abstained': index == 1},
            {'model': 'history-1', 'backend': 'shadow-portfolio', 'selected_action': 'read', 'confidence': 1}]
    path.write_text(''.join(json.dumps(row)+'\n' for row in receipts))
    report = learn(home, sessions=sessions, now=now)
    arms = report['families']['omp.tool']
    binary = next(value for name, value in arms.items() if name.startswith('slm:actual-slm:goal-binary:'))
    assert binary['n'] == binary['correct'] == 1
    assert binary['total'] == 2 and binary['coverage'] == .5 and binary['accuracy'] == 1
    assert 'slm:history-1' not in arms
