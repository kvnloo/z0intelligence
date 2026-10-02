"""Recompute compact summaries and independently validate every selected mask."""
import argparse,json,math
from collections import defaultdict
from pathlib import Path
import numpy as np
from benchmarks.thermocontext import phase_a_prototype as p

def summarize(root):
    groups=defaultdict(list);checks=0
    original=json.loads((root/'original-reproduction.json').read_text())
    sources={'unchanged_original':original['rows']}
    for name in ['fixed-thrml','adaptive-price','prefix-token','prefix-cardinality','deterministic-frontier']:
        sources[name]=[json.loads(line) for line in (root/f'{name}.jsonl').read_text().splitlines()]
    for source,rows in sources.items():
        for row in rows:
            seed=int(row['task_id'].split('-s')[1]);task=p.make_task(row['N'],seed)
            mask=np.array([item.id in row['ids'] for item in task.items])
            assert len(set(row['ids']))==len(row['ids'])==row['count']
            assert task.tokens(mask)==row['tokens']
            assert task.verify(mask)==row['verified']
            assert abs(task.energy(mask)-row['energy'])<=1e-5
            if source!='unchanged_original' and row['arm'] not in ['champion']:
                assert row['tokens']<=row['budget']
            checks+=1;groups[(source,row['N'],row['arm'])].append(row)
    result=[]
    for (source,n,arm),rows in groups.items():
        warm=[r for r in rows if not r.get('cold_shape',False)]
        item={'source':source,'N':n,'arm':arm,'rows':len(rows),'tasks':len(set(r['task_id'] for r in rows)),
              'verified':sum(r['verified'] for r in rows),'verified_budget_feasible':sum(r['verified'] and r['tokens']<=r['budget'] for r in rows),
              'empty':sum(r['count']==0 for r in rows),'overbudget':sum(r['tokens']>r['budget'] for r in rows),
              'mean_tokens':float(np.mean([r['tokens'] for r in rows])),'tokens_std':float(np.std([r['tokens'] for r in rows])),
              'cold_shapes':sum(r.get('cold_shape',False) for r in rows)}
        for key in ['energy_gap','decision_wall_ms','total_wall_ms','sampling_ms','decode_rank_ms','diagnostic_verifier_ms','final_verifier_ms','aux_consistent_fraction']:
            values=[r[key] for r in rows if key in r]
            if values:item[key]={'mean':float(np.mean(values)),'p50':float(np.median(values)),'p95':float(np.percentile(values,95)),'max':float(max(values))}
        if all('energy_gap' in r for r in rows):item['exact_hits']=sum(abs(r['energy_gap'])<=1e-5 for r in rows)
        for key in ['decision_wall_ms','sampling_ms']:
            values=[r[key] for r in warm if key in r]
            if values:item['warm_'+key]={'p50':float(np.median(values)),'p95':float(np.percentile(values,95))}
        item['record_audit_verifier_calls']=len(rows)
        if all('verifier_calls' in r for r in rows):item['actual_verifier_calls_including_record']=sum(r['verifier_calls']+1 for r in rows)
        item['compile_ms_total']=sum(r.get('compile_ms',0) for r in rows)
        item['lower_ms_total']=sum(r.get('lower_ms',0) for r in rows)
        samples=[r['first_valid_sample'] for r in rows if r.get('first_valid_sample') is not None]
        if any('first_valid_sample' in r for r in rows):item['first_valid_sample']={'observed':len(samples),'none':len(rows)-len(samples),'p50':float(np.median(samples)) if samples else None,'p95':float(np.percentile(samples,95)) if samples else None}
        item['per_seed']=[{'seed':s,'verified':sum(r['verified'] for r in rows if r.get('seed')==s),'rows':sum(r.get('seed')==s for r in rows),'mean_gap':float(np.mean([r.get('energy_gap',0) for r in rows if r.get('seed')==s]))} for s in sorted(set(r['seed'] for r in rows if 'seed' in r))]
        item['seed_success_rate_std']=float(np.std([s['verified']/s['rows'] for s in item['per_seed']])) if item['per_seed'] else None
        for key in ['max_degree','factor_arity','auxiliary_nodes','categorical_domain','factor_table_entries','factor_table_bytes']:
            vals=[r[key] for r in rows if key in r]
            if vals:item[key]={'min':min(vals),'max':max(vals)}
        result.append(item)
    return {'decision':'KILL sampling investment on this engineered disconnected cohort; retain code and exact DP comparator','selected_mask_checks':checks,'groups':result,
            'stops':[json.loads(path.read_text()) for path in sorted(root.glob('*.stop.json'))],
            'limits':['CPU/JAX only; no TSU or energy claim','No formal statistical noninferiority claim','N32 adaptive cohort is partial tasks0..9','Shared paired-backend stop: token THRML itself is exact atN8; paired prototype misses one','Wall times are local shared-machine observations, not isolated hardware benchmarks','Diagnostic verifier scans excluded from decision cost; full row wall retains them','Original kernel-only timings exclude selection and are not end-to-end comparators']}
if __name__=='__main__':
    ap=argparse.ArgumentParser();ap.add_argument('root',type=Path);ap.add_argument('--output',required=True,type=Path);a=ap.parse_args()
    result=summarize(a.root)
    with a.output.open('x') as f:json.dump(result,f,indent=2,allow_nan=False)
    print('Validated',result['selected_mask_checks'],'selected masks')
