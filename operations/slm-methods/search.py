"""Matched, stratified pipeline search with separate development and holdout.

A proposes category/stage/margin variants; B measures batched DAG execution.
Selection uses development only. Export additionally needs separated holdout
expected-cost intervals against the incumbent, never manual promotion.
"""
import argparse
import hashlib
from concurrent.futures import ThreadPoolExecutor
import itertools
import json
from pathlib import Path
import random
import sqlite3
import time
import urllib.request

from z0int.cognition.slm_context_reconstruct import reconstruct
from z0int.cognition.slm_pipeline import cluster_tools,pipeline
from z0int.cognition.adapters.transport import OpenAICompatTransport,ServerConfig
from z0int.cognition.slm_statistics import frozen_sample,frozen_cutoff,expected_cost,expected_cost_interval
from z0int.cognition.learning_search import wilson_interval
from z0int.cognition.slm_candidates import publish_candidate


def training_scope(db, evaluation_rows):
    """Build categories exclusively from records preceding the frozen sample."""
    cutoff=frozen_cutoff(db,evaluation_rows)
    rows=list(db.execute("""SELECT l.id,l.ts,l.action,l.features FROM labels l
        JOIN calls c ON c.session=l.session AND c.call=l.call
        WHERE l.family='omp.tool' AND l.ts>0 AND l.ts<? AND c.ts>0 AND c.ts<?
        ORDER BY l.ts,l.id""",(cutoff,cutoff)))
    training=[{'label':r['action'],'features':json.loads(r['features'])} for r in rows]
    digest=hashlib.sha256(json.dumps(
        [{'id':r['id'], **example} for r,example in zip(rows,training)],
        sort_keys=True,separators=(',',':')).encode()).hexdigest()
    return cutoff,training,digest,{count:cluster_tools(training,count) for count in (3,5,8)}


def score_prediction(answer, label, groups):
    """Score what the pipeline returned; gold never changes its decision."""
    selected=answer.get('selected_action')
    category=answer.get('category')
    recall=any(label in group for group in groups)
    return {'correct': selected == label, 'abstain': selected is None,
            'category_correct': category is not None and label in groups[category],
            'candidate_recall': recall,
            'frontier_reason_if_requested_tool_unseen': None if recall else 'tool_not_in_observed_vocab'}


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--db',required=True)
    parser.add_argument('--sessions',required=True)
    parser.add_argument('--endpoint',required=True)
    parser.add_argument('--candidate-home',required=True)
    parser.add_argument('--manifest',required=True)
    parser.add_argument('--output',required=True)
    parser.add_argument('--incumbent',required=True)
    parser.add_argument('--n',type=int,default=800)
    parser.add_argument('--workers',type=int,default=4)
    args=parser.parse_args();out=Path(args.output)
    db=sqlite3.connect('file:'+args.db+'?mode=ro',uri=True);db.row_factory=sqlite3.Row
    rows=[r for r in frozen_sample(db,args.manifest,args.n) if r['family']=='omp.tool']
    contexts=reconstruct(args.sessions,[(r['session'],r['call']) for r in rows])
    cutoff,training,training_digest,groups=training_scope(db,rows)
    supported_tools={r['label'] for r in training}
    prepared=[]
    for row in rows:
        native_context=contexts.get((row['session'],row['call']))
        context=dict(native_context,task='Predict next tool') if native_context is not None else None
        prepared.append((row,groups,json.dumps(context) if context is not None else None))
    models=['qwen3-0.6b-q8']
    with urllib.request.urlopen(args.endpoint.rstrip('/')+'/models') as response:
        served=json.load(response)
    for item in served.get('data',[]):
        if item['id']=='qwen3-1.7b-q4km' and item.get('status',{}).get('value')=='loaded':
            models.append(item['id'])
    transports={model:OpenAICompatTransport(ServerConfig(args.endpoint,model,timeout_s=30)) for model in models}
    variants=list(itertools.product((3,5,8),('binary','bt'),('binary','bt','threeway'),(0,.25,.75),models))
    random.Random(20261025).shuffle(variants)
    leaderboard=[]
    def document(status):
        return {'status':status,'evidence_class':'replayed_L1','models':models,'manifest':'sha256:'+hashlib.sha256(Path(args.manifest).read_bytes()).hexdigest(),
                'winner_qualified':False,'multiple_comparison_note':'54 or more exploratory variants; Wilson/bootstrap intervals are not simultaneous and selection is dev-only; row-stratified splits do not establish independent sessions or work items',
                'training':{'cutoff_exclusive':cutoff,'digest':training_digest,'rows':len(training),
                            'frozen_categories':groups,
                            'supported_tools':sorted(supported_tools)},
                'cost_proxy':{'read_recovery_ms':6034,'frontier_ms':7999,'causal_error_cost':False},'leaderboard':leaderboard}
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        for category_count,first,final,margin,last_model in variants:
            def evaluate(item):
                row,by_count,prompt=item;groups=by_count[category_count];account=[]
                def chat_for(model):
                    def chat(messages,**kwargs):
                        kwargs['extra_body']={**kwargs.get('extra_body',{}),'cache_prompt':True}
                        call={'tokens':0,'failed':True}
                        try:
                            result=transports[model].chat(messages,**kwargs)
                            call.update(tokens=result.usage.get('total_tokens',0),failed=False)
                            return result
                        finally:
                            account.append(call)
                    return chat
                started=time.monotonic()
                try:
                    if prompt is None:
                        raise ValueError('exact_native_context_unavailable')
                    answer=pipeline(prompt,groups,chat_for(models[0]),chat_for(last_model),first=first,final=final,margin=margin,
                                    seed=random.Random(row['id']).randrange(2**31))
                    error=None
                except Exception as exc:
                    answer={'selected_action':None,'category':None};error=type(exc).__name__
                return {'id':row['id'],'session':row['session'],'split':row['_split'],
                        **score_prediction(answer,row['label'],groups), 'tools':sum(map(len,groups)),
                        'wall_ms':1000*(time.monotonic()-started),'tokens':sum(r['tokens'] for r in account),
                        'failed_rpcs':sum(r['failed'] for r in account),'error':error}
            outcomes=list(pool.map(evaluate,prepared))
            variant={'categories':category_count,'first':first,'final':final,'margin':margin,'first_model':models[0],'final_model':last_model}
            summaries={}
            for split in ('dev','holdout'):
                subset=[r for r in outcomes if r['split']==split];n=len(subset);covered=sum(not r['abstain'] for r in subset);correct=sum(r['correct'] for r in subset)
                summaries[split]={'n':n,'correct':correct,'covered':covered,'accuracy':correct/n,'wilson95':wilson_interval(correct,n),
                                  'category_accuracy':sum(r['category_correct'] for r in subset)/n,
                                  'category_wilson95':wilson_interval(sum(r['category_correct'] for r in subset),n),
                                  'abstain_rate':1-covered/n,'tokens_per_decision':sum(r['tokens'] for r in subset)/n,
                                  'wall_ms':sum(r['wall_ms'] for r in subset)/n,'expected_proxy_ms':expected_cost(subset),
                                  'expected_proxy_ci95_ms':expected_cost_interval(subset),
                                  'failed_decisions':sum(r['error'] is not None for r in subset),'failed_rpcs':sum(r['failed_rpcs'] for r in subset)}
            leaderboard.append({'variant':variant,'metrics':summaries,'rows':outcomes})
            out.write_text(json.dumps(document('running'),indent=2))
    leaderboard.sort(key=lambda r:r['metrics']['dev']['expected_proxy_ms'])
    for i,row in enumerate(leaderboard):
        row['dev_point_estimate_order']=i+1
        row['dev_ci_separated_from_next']=i+1<len(leaderboard) and row['metrics']['dev']['expected_proxy_ci95_ms'][1]<leaderboard[i+1]['metrics']['dev']['expected_proxy_ci95_ms'][0]
    report=document('complete');out.write_text(json.dumps(report,indent=2))
    try:
        candidate=publish_candidate(out,Path(args.candidate_home),incumbent_path=args.incumbent)
        report.update(winner_qualified=True,candidate_id=candidate['id'])
        (out.parent/'candidate.json').write_text(json.dumps(candidate,indent=2,sort_keys=True))
    except ValueError as exc:
        report['export_blocked_reason']=str(exc)
    out.write_text(json.dumps(report,indent=2))


if __name__=='__main__':main()
