import json
import time
from pathlib import Path
from types import SimpleNamespace

from z0int.cognition.learning import connect
from z0int.cognition.adapters.transport import ChatOutcome, TransportError
from z0int.cognition import learning_runtime as runtime


def outcome(candidate, positive=True):
    yes, no = (-.1, -2.352168461) if positive else (-2.352168461, -.1)
    raw = {'choices': [{'message': {'content': 'yes' if positive else 'no'},
        'logprobs': {'content': [{'token': 'yes' if positive else 'no',
            'logprob': yes if positive else no,
            'top_logprobs': [{'token': 'yes', 'logprob': yes}, {'token': 'no', 'logprob': no}]}]}}]}
    return ChatOutcome('yes' if positive else 'no', None, {'prompt_tokens': 100}, {}, raw, 1)


IDENTITY = {'kind': 'slm', 'model': 'qwen3-0.6b-q8', 'quant': 'Q8_0',
            'engine': 'llama.cpp@measured', 'backend': 'cuda', 'device': 'groot'}
SCOPE = {'base_url': 'http://fixture', 'served_model': 'qwen3-0.6b-q8', 'n_ctx': 4096, 'slots': 2}
ENDPOINT = SimpleNamespace(base_url='http://fixture', served_model='qwen3-0.6b-q8',
                           timeout_s=3, runtime='llama.cpp-router', quantization='Q8_0')


def test_binary_predictor_records_actual_rpc_failure_and_no_multiclass_prompt(tmp_path, monkeypatch):
    requests = []
    class Transport:
        def __init__(self, config): pass
        def chat(self, messages, **kwargs):
            body = json.loads(messages[-1]['content'])
            assert 'choices' not in body['context']
            assert kwargs['max_tokens'] == 1 and kwargs['logprobs'] is True
            requests.append(body['candidate']['id'])
            if body['candidate']['id'] == 'broken':
                raise TransportError('HTTP 503 queue full')
            return outcome(body['candidate']['id'])
    monkeypatch.setattr(runtime, 'OpenAICompatTransport', Transport)
    db = connect(tmp_path)
    predict = runtime.choice_predictor(db, ENDPOINT, IDENTITY, SCOPE)
    answer = predict('{"task":"classify","choices":["ok","broken"]}', ['ok', 'broken'],
                     {'family': 'omp.path_kind', 'strategy_id': 'history-1'})
    assert answer['selected_action'] is None
    rows = [json.loads(r[0]) for r in db.execute('SELECT body FROM learning_rpc ORDER BY id')]
    assert requests == ['ok', 'broken']
    assert len(rows) == 2 and rows[0]['raw_response']['choices']
    assert rows[1]['failure_class'] == 'queue_full'
    assert 'HTTP 503' in rows[1]['error']
    db.close()


def test_runtime_derives_three_families_emits_canonical_findings_and_scoped_search(tmp_path, monkeypatch):
    class Transport:
        def __init__(self, config): pass
        def chat(self, messages, **kwargs):
            candidate = json.loads(messages[-1]['content'])['candidate']['id']
            return outcome(candidate, candidate in ('skill_uri', 'wrapper_ok', 'ctx'))
    monkeypatch.setattr(runtime, 'OpenAICompatTransport', Transport)
    monkeypatch.setattr(runtime, 'load_serving', lambda path: {'qwen3_06b': ENDPOINT})
    monkeypatch.setattr(runtime, 'serving_identity', lambda endpoint: (IDENTITY, SCOPE))
    db = connect(tmp_path)
    for i in range(4):
        db.execute('''INSERT INTO calls(session,call,tool,features,outcome,ts,arguments,outcome_ts)
                   VALUES(?,?,?,?,?,?,?,?)''', ('s', str(i), 'read', '["read"]',
                   '{"tool_error":false}', 100+i*2, '{"path":"skill://ctx"}', 101+i*2))
    report = runtime.runtime_tick(db, tmp_path, now=120, deadline=time.monotonic()+5,
                                 config={'jobs_per_tick': 12})
    for family in ('omp.skill_choice', 'omp.path_kind', 'omp.wrapper_result'):
        assert report['families'][family]['labels_day'] == 4
        assert report['families'][family]['arms']
        assert report['search']['families'][family]['current_strategy'] == 'history-1'
    ledger = [json.loads(line) for line in (tmp_path/'findings'/'findings.jsonl').read_text().splitlines()]
    assert ledger and all(row['synthetic'] is False for row in ledger)
    assert all(row['evidence_class'] == 'replayed_L1' for row in ledger)
    assert all(row['metric']['n'] > 0 and row['metric']['total'] >= row['metric']['n'] for row in ledger)
    assert all(row['metric']['projection']['candidate_k'] <= 3 for row in ledger)
    assert report['transport_tick']['errors'] == 0
    # The first tick persists the selected strategy; the next must consume it.
    db.execute('''INSERT INTO calls(session,call,tool,features,outcome,ts,arguments,outcome_ts)
               VALUES(?,?,?,?,?,?,?,?)''', ('s', 'new', 'read', '["read"]',
               '{"tool_error":false}', 121, '{"path":"skill://ctx"}', 122))
    second = runtime.runtime_tick(db, tmp_path, now=130, deadline=time.monotonic()+5,
                                  config={'jobs_per_tick': 12})
    assert second['jobs_evaluated'] > 0
    stored = [json.loads(r[0]) for r in db.execute(
        "SELECT config FROM family_strategies WHERE strategy_id='history-1'")]
    assert any(row.get('decision_method') == 'per_candidate_binary_logit' for row in stored)
    db.close()


def test_tool_comparison_keeps_abstentions_out_of_accuracy_denominator(tmp_path):
    db = connect(tmp_path)
    for i, selected in enumerate(('read', None)):
        receipt = {'identity_contract': 'session-tool-call.v1', 'projection_strategy': 'history-1',
                   'projection_config': {'candidate_k': 3}, 'shadow': [{
                       'model': IDENTITY['model'], 'physical_identity': IDENTITY,
                       'arm': 'goal-binary', 'selected_action': selected}]}
        db.execute('INSERT INTO receipts VALUES(?,?,?,?,?,?)',
                   (str(i), 's', str(i), 100+i, 'omp.tool', json.dumps(receipt)))
        db.execute('INSERT INTO labels VALUES(?,?,?,?,?,?,?,?,?)',
                   (str(i), 's', str(i), 'omp.tool', 100+i, 'read', '[]', None, 120))
    observations, comparison = runtime.tool_observations(db, IDENTITY, SCOPE)
    arm = next(iter(comparison['arms'].values()))
    assert (arm['model']['total'], arm['model']['n'], arm['model']['correct']) == (2, 1, 1)
    assert arm['model']['coverage'] == .5
    assert all(row['promotion_eligible'] is False for row in observations)
    assert comparison['binding']['model_credit'] is False
    db.close()


def test_tool_comparison_reports_all_unattested_attempts_without_credit(tmp_path):
    db = connect(tmp_path)
    for i, physical in enumerate((None, {**IDENTITY, 'backend':'cpu'}, IDENTITY)):
        shadow={'model':IDENTITY['model'],'arm':'slm-method:scope',
                'method_config':{'decision_method':'scoped'},'selected_action':'read','rpc_count':1}
        if physical is not None:
            shadow['physical_identity']=physical
        receipt={'shadow':[shadow], 'projection_config':{'candidate_k':3}}
        db.execute('INSERT INTO receipts VALUES(?,?,?,?,?,?)',
                   (str(i),'s',str(i),100+i,'omp.tool',json.dumps(receipt)))
        db.execute('INSERT INTO labels VALUES(?,?,?,?,?,?,?,?,?)',
                   (str(i),'s',str(i),'omp.tool',100+i,'read','[]',None,120))
    observations, comparison=runtime.tool_observations(db,IDENTITY,SCOPE)
    assert len(observations) == 2
    assert next(iter(comparison['arms'].values()))['model']['total'] == 3
    assert comparison['binding']['attested_attempts']==0
    db.close()



def test_shadow_receipts_keep_zero_rpc_abstentions_in_denominator(tmp_path):
    db=connect(tmp_path)
    for i in range(10):
        shadow={'model':IDENTITY['model'],'physical_identity':IDENTITY,
                'arm':'slm-method:scope','method_config':{'decision_method':'scoped'},
                'selected_action':'read','rpc_count':int(i == 0)}
        db.execute('INSERT INTO receipts VALUES(?,?,?,?,?,?)',
                   (str(i),'s',str(i),100+i,'omp.tool',json.dumps({'shadow':[shadow]})))
        db.execute('INSERT INTO labels VALUES(?,?,?,?,?,?,?,?,?)',
                   (str(i),'s',str(i),'omp.tool',100+i,'read','[]',None,120))
    observations,comparison=runtime.tool_observations(db,IDENTITY,SCOPE)
    assert len(observations) == 2
    arm = next(iter(comparison['arms'].values()))
    assert (arm['model']['n'], arm['model']['total'], arm['model']['correct']) == (1, 10, 1)
    assert arm['model']['coverage'] == .1
    assert arm['baseline']['total'] == 10
    db.close()


def test_tool_comparison_pairs_each_configured_arm_with_its_own_calls(tmp_path):
    db = connect(tmp_path)
    db.execute('INSERT INTO examples VALUES(?,?,?,?,?)',
               ('prior', 'omp.tool', 'read', '["a","b"]', 1))
    scope_a = {'base_url': 'http://a', 'model': 'alias', 'runtime': 'cpu',
               'quantization': 'q8', 'revision': 'one', 'identity_binding': 'unattested'}
    scope_b = {**scope_a, 'runtime': 'cuda', 'revision': 'two'}
    def shadow(scope, model='alias', arm='goal-binary', **extra):
        return {'request_scope': scope, 'model': model, 'arm': arm, **extra}
    receipts = [
        ('read', [shadow(scope_a, selected_action='read'),
                  shadow(scope_b, selected_action='read'),
                  shadow(scope_a, model='other', selected_action='read')]),
        ('edit', [shadow(scope_a, selected_action='edit'),
                  shadow(scope_b, selected_action='read', error='timeout')]),
        ('read', [shadow(scope_b, arm='slm-method:digest', selected_action='read', rpc_count=0),
                  shadow(None, model='unavailable', error='unavailable')]),
    ]
    for i, (label, shadows) in enumerate(receipts):
        body = {'projection_strategy': 'history-1', 'projection_config': {'candidate_k': 3},
                'shadow': shadows}
        db.execute('INSERT INTO receipts VALUES(?,?,?,?,?,?)',
                   (str(i), 's', str(i), 100+i, 'omp.tool', json.dumps(body)))
        db.execute('INSERT INTO labels VALUES(?,?,?,?,?,?,?,?,?)',
                   (str(i), 's', str(i), 'omp.tool', 100+i, label, '["a","b"]', None, 120))
    observations, comparison = runtime.tool_observations(db, IDENTITY, SCOPE)
    arms = list(comparison['arms'].values())
    assert len(arms) == 5 and len(observations) == 10
    def group(scope, model='alias', method='goal-binary'):
        return next(a for a in arms if a['provenance']['request_scope'] == scope
                    and a['provenance']['model'] == model and a['provenance']['method'] == method)
    a, b = group(scope_a), group(scope_b)
    assert (a['model']['total'], a['model']['n'], a['model']['correct']) == (2, 2, 2)
    assert (a['baseline']['total'], a['baseline']['n'], a['baseline']['correct']) == (2, 2, 1)
    assert (b['model']['total'], b['model']['n'], b['model']['correct']) == (2, 1, 1)
    assert (b['baseline']['total'], b['baseline']['n'], b['baseline']['correct']) == (2, 2, 1)
    assert group(scope_a, 'other')['baseline']['total'] == 1
    assert group(scope_b, method='slm-method:digest')['model']['n'] == 0
    assert group(None, 'unavailable')['model']['total'] == 1
    assert comparison['binding']['attempts'] == 7
    assert all(row['evidence_kind'] == 'proxy' and not row['promotion_eligible'] for row in observations)
    db.close()


def test_tool_research_initializes_and_advances_without_canonical_credit(tmp_path, monkeypatch):
    monkeypatch.setattr(runtime, 'load_serving', lambda path: {'qwen3_06b': ENDPOINT})
    monkeypatch.setattr(runtime, 'serving_identity', lambda endpoint: (IDENTITY, SCOPE))
    from z0int.cognition import slm_candidates
    candidate = {'id': 'methods-pipeline-fixture', 'kind': 'bounded_projection',
                 'families': ['omp.tool'], 'config': {
                     **runtime.DEFAULT_STRATEGIES[0]['config'], 'decision_method': 'slm_pipeline_v1'}}
    monkeypatch.setattr(slm_candidates, 'candidate_catalogue',
                        lambda home, defaults: (*defaults, candidate))
    db = connect(tmp_path)
    def add_receipt(index, strategy):
        scoped = strategy.startswith('methods-')
        body = {'projection_strategy': strategy, 'projection_config': {'candidate_k': 3},
                'shadow': [{'arm': 'slm-method:digest' if scoped else 'goal-binary',
                            'model': 'alias', 'selected_action': 'read', 'rpc_count': 1,
                            'request_scope': {'base_url': 'http://fixture', 'identity_binding': 'unattested'}}]}
        db.execute('INSERT INTO receipts VALUES(?,?,?,?,?,?)',
                   (str(index), 's', str(index), 100+index, 'omp.tool', json.dumps(body)))
        db.execute('INSERT INTO labels VALUES(?,?,?,?,?,?,?,?,?)',
                   (str(index), 's', str(index), 'omp.tool', 100+index, 'read', '[]', None, 120))
    add_receipt(0, 'history-1')
    cfg = {'jobs_per_tick': 0, 'plateau_ticks': 2, 'min_observations': 1}
    first = runtime.runtime_tick(db, tmp_path, now=120, deadline=time.monotonic()+30, config=cfg)
    assert first['search']['families']['omp.tool']['current_strategy'] == 'history-1'
    second = runtime.runtime_tick(db, tmp_path, now=121, deadline=time.monotonic()+30, config=cfg)
    assert second['search']['families']['omp.tool']['current_strategy'] == 'history-3'
    add_receipt(1, 'history-3')
    third = runtime.runtime_tick(db, tmp_path, now=122, deadline=time.monotonic()+30, config=cfg)
    assert third['search']['families']['omp.tool']['trial']['status'] == 'running'
    fourth = runtime.runtime_tick(db, tmp_path, now=123, deadline=time.monotonic()+30, config=cfg)
    assert fourth['search']['families']['omp.tool']['current_strategy'] == 'projection-recent-sessions'
    reports = [first, second, third, fourth]
    now = 124
    for index, strategy in enumerate(('projection-recent-sessions', 'few-shot-3',
                                      'methods-threeway-v1', 'methods-hierarchy-v1'), start=2):
        add_receipt(index, strategy)
        running = runtime.runtime_tick(db, tmp_path, now=now, deadline=time.monotonic()+30, config=cfg)
        assert running['search']['families']['omp.tool']['trial']['status'] == 'running'
        selected = runtime.runtime_tick(db, tmp_path, now=now+1, deadline=time.monotonic()+30, config=cfg)
        reports.extend((running, selected))
        now += 2
    assert selected['search']['families']['omp.tool']['current_strategy'] == candidate['id']
    add_receipt(6, candidate['id'])
    pipeline = runtime.runtime_tick(db, tmp_path, now=now, deadline=time.monotonic()+30, config=cfg)
    assert pipeline['search']['families']['omp.tool']['trial']['status'] == 'running'
    assert all(not report['findings'] for report in (*reports, pipeline))
    source = json.loads(Path(pipeline['findings_source']).read_text())
    assert source['observations'] == []
    rows, _ = runtime.tool_observations(db, IDENTITY, SCOPE)
    blocked = runtime.emit_findings(db, tick_id='explicit', now=now+1, observations=rows,
                                    source_path=Path(pipeline['findings_source']), run_id='explicit', home=tmp_path)
    assert all(row['reason'] == 'nonpromotable_research_observation' for row in blocked)
    assert not (tmp_path/'findings'/'findings.jsonl').exists()
    db.close()


def test_shadow_request_scope_never_uses_metadata_rpc_or_claims_identity(monkeypatch):
    from z0int.cognition import shadow, slm_scoped
    config=SimpleNamespace(base_url='http://user:secret@fixture:123/v1?token=secret',
                           model=IDENTITY['model'],runtime='llama.cpp',quantization='Q8_0',revision=None)
    class Transport:
        def __init__(self):
            self.config=config
        def _get(self,path):
            raise AssertionError('metadata RPC must not block prediction')
    backend=SimpleNamespace(config=config,_transport=Transport())
    calls=[]
    def predict(*args,**kwargs):
        calls.append(True)
        return {'model':IDENTITY['model'],'arm':'slm-method:scope',
                'selected_action':'read','rpc_count':1}
    monkeypatch.setattr(slm_scoped,'run_scoped',predict)
    result=shadow._decision_rows([(SimpleNamespace(model_id='alias'),backend,None)],None,{},
        timeout_s=5,decision_config={'decision_method':'slm_bt_v1'})[0]
    assert calls == [True]
    assert result['selected_action']=='read' and 'physical_identity' not in result
    assert result['request_scope']=={'base_url':'http://fixture:123/v1',
        'model':IDENTITY['model'],'runtime':'llama.cpp','quantization':'Q8_0',
        'revision':None,'identity_binding':'unattested'}

def test_census_queue_keeps_only_exact_new_families_and_frozen_provenance(tmp_path):
    folder = tmp_path/'loop'
    folder.mkdir()
    (folder/'shadow-census.json').write_text(json.dumps({'jobs': [
        {'job': 'compaction_keep_drop', 'full_corpus_extraction': {'eligible_within_shard_unique_sum': 12},
         'label_source': {'observed_literal_or_field': True, 'exact_trace_join': 'source+session+message.compacted',
                          'quality_caveat': 'lifecycle flag only'}},
        {'job': 'model_choice', 'full_corpus_extraction': {'eligible_within_shard_unique_sum': 3},
         'label_source': {'observed_literal_or_field': False}},
        {'job': 'outcome_error', 'label_source': {'observed_literal_or_field': False}},
    ]}))
    rows = runtime.census_queue(tmp_path, {})
    assert len(rows) == 1
    assert rows[0]['family'] == 'hermes.compaction_flag'
    assert rows[0]['exact_labels'] == 12 and rows[0]['census_frozen'] is True
    assert Path(rows[0]['source_path']).is_file()
