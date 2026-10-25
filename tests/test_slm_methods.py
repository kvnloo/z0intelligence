import json

from z0int.cognition.slm_methods import bt_fit
from z0int.cognition.slm_pipeline import cluster_tools
from z0int.cognition.slm_context_reconstruct import reconstruct


def test_bt_fit_orders_fractional_wins():
    strengths=bt_fit(3,[(0,1,.9),(0,2,.9),(1,2,.8)])
    assert strengths[0]>strengths[1]>strengths[2]


def test_clusters_are_disjoint_and_training_only():
    examples=[{'label':'read','features':['find']},{'label':'find','features':['read']},
              {'label':'edit','features':['read']},{'label':'bash','features':['edit']}]
    groups=cluster_tools(examples,3)
    assert sorted(v for group in groups for v in group)==['bash','edit','find','read']
    assert groups==cluster_tools(examples,3)
    assert all(len(group)<=2 for group in groups)


def test_reconstructor_excludes_sibling_and_current_args(tmp_path):
    rows=[{'type':'session','id':'s'},
          {'id':'u','parentId':None,'message':{'role':'user','content':[{'type':'text','text':'original goal'}]}},
          {'id':'other','parentId':'u','message':{'role':'user','content':[{'type':'text','text':'sibling secret'}]}},
          {'id':'a','parentId':'u','message':{'role':'assistant','content':[{'type':'toolCall','id':'before','name':'read','arguments':{'i':'Inspecting source'}}]}},
          {'id':'b','parentId':'a','message':{'role':'assistant','content':[{'type':'text','text':'Inspect source. Next action.'},{'type':'toolCall','id':'target','name':'edit','arguments':{'i':'GOLD hidden'}}]}}]
    (tmp_path/'trace.jsonl').write_text('\n'.join(json.dumps(r) for r in rows))
    context=reconstruct(tmp_path,[('s','target')])[('s','target')]
    assert context['latest_user_message']=='original goal'
    assert context['last_assistant_sentence']=='Next action.'
    assert context['previous_tools']==[{'tool':'read','intent':'Inspecting source'}]
    assert 'GOLD' not in json.dumps(context)
    assert 'sibling' not in json.dumps(context)


def test_scoped_identity_changes_with_frozen_categories():
    from z0int.cognition.slm_scoped import scope_digest
    config={'decision_method':'slm_pipeline_v1','frozen_categories':[['read'],['edit']],
            'training_digest':'first'}
    changed={**config,'frozen_categories':[['edit'],['read']]}
    assert scope_digest(config)!=scope_digest(changed)
    assert scope_digest(config)==scope_digest(dict(reversed(list(config.items()))))


def test_unseen_tool_never_reaches_model():
    from types import SimpleNamespace
    from z0int.cognition.slm_scoped import run_scoped
    def forbidden(*args,**kwargs):
        raise AssertionError('unseen tools must route before inference')
    backend=SimpleNamespace(_transport=SimpleNamespace(chat=forbidden),
                            config=SimpleNamespace(model='test'),backend_id='test')
    legal=SimpleNamespace(ids=lambda:('read','new_tool'))
    config={'decision_method':'slm_pipeline_v1','frozen_categories':[['read']],
            'training_digest':'fixed'}
    row=run_scoped({},legal,backend,config,requested_tool='new_tool')
    assert row['selected_action'] is None
    assert row['frontier_reason']=='tool_not_in_observed_vocab'
    assert row['arm'].startswith('slm-method:')


def test_reconstructor_fails_closed_on_missing_ancestor(tmp_path):
    rows=[{'type':'session','id':'s'},
          {'id':'a','parentId':'missing','message':{'role':'assistant','content':[
              {'type':'toolCall','id':'target','name':'read','arguments':{}}]}}]
    (tmp_path/'trace.jsonl').write_text('\n'.join(json.dumps(r) for r in rows))
    assert reconstruct(tmp_path,[('s','target')])=={}


def test_candidate_rejects_overlapping_holdout_cost(tmp_path):
    from z0int.cognition.slm_candidates import publish_candidate
    import pytest
    leaderboard=tmp_path/'leaderboard.json'
    incumbent=tmp_path/'incumbent.json'
    metrics={'n':400,'expected_proxy_ci95_ms':[100,200]}
    leaderboard.write_text(json.dumps({'status':'complete','manifest':'frozen',
        'leaderboard':[{'variant':{},'metrics':{'dev':{'expected_proxy_ms':150},
            'holdout':metrics},'rows':[{'id':str(i),'split':'holdout'} for i in range(400)]}]}))
    incumbent.write_text(json.dumps({'status':'complete','manifest':'frozen',
        'summary':[dict(metrics,family='omp.tool',method='goal_binary',split='holdout')],
        'rows':[{'id':str(i),'family':'omp.tool','method':'goal_binary','split':'holdout'}
                for i in range(400)]}))
    with pytest.raises(ValueError,match='holdout_cost_intervals_overlap_or_lose'):
        publish_candidate(leaderboard,tmp_path,incumbent_path=incumbent)


def test_candidate_keeps_evaluated_categories(tmp_path):
    from z0int.cognition.slm_candidates import publish_candidate
    leaderboard=tmp_path/'leaderboard.json'
    incumbent=tmp_path/'incumbent.json'
    categories=[['read'],['edit']]
    holdout={'n':400,'expected_proxy_ci95_ms':[100,200]}
    variant={'categories':2,'first':'binary','final':'bt','margin':.25,
             'first_model':'fixed','final_model':'fixed'}
    leaderboard.write_text(json.dumps({'status':'complete','manifest':'frozen',
        'training':{'digest':'fixed-training','rows':50,'frozen_categories':{'2':categories}},
        'leaderboard':[{'variant':variant,'metrics':{'dev':{'expected_proxy_ms':150},
            'holdout':holdout},'rows':[{'id':str(i),'split':'holdout'} for i in range(400)]}]}))
    incumbent.write_text(json.dumps({'status':'complete','manifest':'frozen',
        'summary':[{'family':'omp.tool','method':'goal_binary','split':'holdout',
                    'n':400,'expected_proxy_ci95_ms':[300,400]}],
        'rows':[{'id':str(i),'family':'omp.tool','method':'goal_binary','split':'holdout'}
                for i in range(400)]}))
    candidate=publish_candidate(leaderboard,tmp_path,incumbent_path=incumbent)
    assert candidate['config']['frozen_categories']==categories
    assert candidate['config']['training_digest']=='fixed-training'


def test_pipeline_portfolio_retains_scope_and_dispatches(monkeypatch):
    import sqlite3
    from types import SimpleNamespace
    from z0int.cognition import learning_runtime,slm_scoped
    config={'history_width':1,'candidate_k':3,'prompt_bytes':3000,
            'decision_method':'slm_pipeline_v1','frozen_categories':[['read'],['edit']],
            'training_digest':'fixed','first_model':'fixed','final_model':'fixed'}
    catalogue=[{'id':'history-1','config':config},{'id':'methods-pipeline-test','config':config}]
    strategy=learning_runtime._configs({'omp.tool':'methods-pipeline-test'},catalogue)['omp.tool']
    assert strategy['method_config']==config
    captured=[]
    def scoped(payload,legal,backend,method_config,**kwargs):
        captured.append((payload,legal.ids(),method_config))
        return {'selected_action':'edit','arm_scope':slm_scoped.scope_digest(method_config)}
    monkeypatch.setattr(slm_scoped,'run_scoped',scoped)
    monkeypatch.setattr(learning_runtime,'OpenAICompatTransport',lambda *args,**kwargs:SimpleNamespace())
    endpoint=SimpleNamespace(base_url='http://localhost',served_model='fixed',
        timeout_s=1,runtime=None,quantization=None)
    db=sqlite3.connect(':memory:')
    predict=learning_runtime.choice_predictor(db,endpoint,{}, {'n_ctx':4096})
    answer=predict(json.dumps({'latest_user_message':'inspect then change'}),['read'],strategy)
    assert answer['selected_action']=='edit'
    assert answer['arm_scope']==slm_scoped.scope_digest(config)
    assert captured==[({'goal_context':{'latest_user_message':'inspect then change'}},
                       ('read','edit'),config)]
