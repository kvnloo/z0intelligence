"""Private exact-join replay with frozen stratified labels and cost intervals."""
import argparse
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import random
import sqlite3
import time

from z0int.cognition.slm_methods import METHODS,predict_method
from z0int.cognition.learning_families import _prior,project_prompt,_baseline
from z0int.cognition.adapters.transport import OpenAICompatTransport,ServerConfig
from z0int.cognition.slm_context_reconstruct import reconstruct
from z0int.cognition.slm_statistics import frozen_sample,expected_cost,expected_cost_interval
from z0int.cognition.learning_search import wilson_interval


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--db',default='/home/kvn/.z0int/loop/shadow-learning.sqlite')
    parser.add_argument('--output',required=True)
    parser.add_argument('--n',type=int,default=24)
    parser.add_argument('--manifest')
    parser.add_argument('--methods',default=','.join(METHODS)+',history_bigram')
    parser.add_argument('--workers',type=int,default=1)
    args=parser.parse_args()
    db=sqlite3.connect('file:'+args.db+'?mode=ro',uri=True);db.row_factory=sqlite3.Row
    if args.manifest:
        selected=frozen_sample(db,args.manifest,args.n)
    else:
        selected=[]
        for family,table in (('omp.tool','labels'),('omp.path_kind','family_jobs')):
            rows=list(db.execute(f'SELECT * FROM {table} WHERE family=? '+('AND label IS NOT NULL ' if table=='family_jobs' else '')+'ORDER BY ts,id',(family,)))
            selected.extend(dict(r,**({'label':r['action']} if family=='omp.tool' else {})) for r in random.Random(20261025).sample(rows,min(args.n,len(rows))))
    contexts=reconstruct('/home/kvn/.omp/agent/sessions',[(r['session'],r['call']) for r in selected])
    prepared=[]
    for row in selected:
        if row['family']=='omp.tool':
            examples=[dict(label=e['action'],features=json.loads(e['features'])) for e in db.execute("SELECT action,features FROM labels WHERE family=? AND ts<? ORDER BY ts DESC LIMIT 64",(row['family'],row['ts']))][::-1]
            counts=Counter(e['label'] for e in examples)
            choices=sorted(counts,key=lambda v:(-counts[v],v))[:3]
            body={'task':'Predict next tool','examples':examples[-3:]}
        else:
            examples=_prior(db,row,64)
            prompt,choices=project_prompt(row['family'],row['features'],examples,history_width=1)
            body=json.loads(prompt)
        native_context=contexts.get((row['session'],row['call']))
        if native_context is not None:
            body.update(native_context)
        else:
            body=None
        prepared.append((row,examples,json.dumps(body) if body is not None else None,choices))
    methods=args.methods.split(',')
    transport=OpenAICompatTransport(ServerConfig('http://100.113.138.100:11530','qwen3-0.6b-q8',timeout_s=30))
    def evaluate(item):
        row,examples,prompt,choices=item; order=list(methods);random.Random(row['id']).shuffle(order);out=[]
        for method in order:
            calls=[]
            def chat(*a,**kw):
                kw['extra_body']={**kw.get('extra_body',{}),'cache_prompt':True}
                account={'tokens':0,'failed':True}
                try:
                    outcome=transport.chat(*a,**kw)
                    account.update(tokens=outcome.usage.get('total_tokens',0),failed=False)
                    return outcome
                finally:
                    calls.append(account)
            started=time.monotonic();error=None
            try:
                if prompt is None:
                    raise ValueError('exact_native_context_unavailable')
                if method=='history_bigram':
                    action,confidence=_baseline(row['features'],examples,choices,1)
                    action=action if confidence>=.8 else None
                else:
                    prediction=predict_method(prompt,choices,chat,method=method,
                                              seed=random.Random(row['id']).randrange(2**31))
                    action=prediction.get('selected_action')
                    error='predictor_error' if prediction.get('error') or prediction.get('parse_error') else None
            except Exception as exc:
                action=None;error=type(exc).__name__
            out.append({'id':row['id'],'session':row['session'],'split':row.get('_split','all'),'family':row['family'],'method':method,
                        'correct':action==row['label'],'abstain':action is None,'wall_ms':1000*(time.monotonic()-started),
                        'tokens':sum(c['tokens'] for c in calls),'rpc_count':len(calls),'failed_rpc_count':sum(c['failed'] for c in calls),'error':error})
        return out
    results=[]
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        for completed in pool.map(evaluate,prepared):
            results.extend(completed)
            if len(results)%(50*len(methods))==0:
                Path(args.output).write_text(json.dumps({'status':'running','rows':results}))
    summary=[]
    for family in sorted({r['family'] for r in results}):
        for method in methods:
            for split in sorted({'all'}|{r['split'] for r in results}):
                rows=[r for r in results if r['family']==family and r['method']==method and (split=='all' or r['split']==split)]
                if not rows:continue
                n=len(rows);k=sum(r['correct'] for r in rows)
                summary.append(dict(family=family,method=method,split=split,n=n,correct=k,accuracy=k/n,wilson95=wilson_interval(k,n),
                                    abstain_rate=sum(r['abstain'] for r in rows)/n,tokens=sum(r['tokens'] for r in rows),wall_ms=sum(r['wall_ms'] for r in rows),
                                    expected_proxy_ms=expected_cost(rows),expected_proxy_ci95_ms=expected_cost_interval(rows),
                                    errors=sum(r['error'] is not None for r in rows),failed_rpcs=sum(r['failed_rpc_count'] for r in rows)))
    Path(args.output).write_text(json.dumps({'status':'complete','model':'qwen3-0.6b-q8','evidence_class':'replayed_L1','manifest':args.manifest,
                                           'context':'exact_native_ancestor_chain','rows':results,'summary':summary},indent=2))

if __name__=='__main__':main()
