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
    assert comparison['goal_binary'] == {'total': 2, 'n': 1, 'correct': 1,
        'accuracy': 1, 'coverage': .5, 'wilson95_lower': comparison['goal_binary']['wilson95_lower']}
    slm = next(row for row in observations if row['identity']['kind'] == 'slm')
    assert slm['n'] == 1 and slm['total'] == 2
    db.close()


def test_tool_comparison_excludes_alias_only_and_other_physical_identity(tmp_path):
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
    slm=next(row for row in observations if row['identity']['kind']=='slm')
    assert slm['total']==1 and slm['arm']=='slm:'+IDENTITY['model']+':slm-method:scope'
    assert slm['projection']=={'decision_method':'scoped'}
    assert comparison['slm-method:scope']['total']==1
    db.close()


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
