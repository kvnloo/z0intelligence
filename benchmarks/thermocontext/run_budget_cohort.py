"""Fixed ceilings and fail-fast scale gate, with incremental create-only receipts."""
import argparse,json,time
from pathlib import Path
import numpy as np
from benchmarks.thermocontext import phase_a_prototype as p
from benchmarks.thermocontext import thrml_experiment as e
from benchmarks.thermocontext.thrml_prefix import sample_prefix

def run(output,formulation):
    output=Path(output);original=json.loads((output.parent/'original-reproduction.json').read_text())
    exact={r['task_id']:r for r in original['rows'] if r['arm']=='exact'}
    gaps={n:np.mean([r['energy']-exact[r['task_id']]['energy'] for r in original['rows'] if r['N']==n and r['arm']=='thermo']) for n in [8,12,16]}
    allrows=[];stops=[]
    with output.open('x') as stream:
        for n in [8,12,16,32,64,128]:
            size=[];immediate=False
            for ts in range(12):
                task=p.make_task(n,ts);problem=e.public_problem(task)
                optimum=exact[task.task_id]['energy'] if n<=16 else task.energy(e.component_dp(problem))
                taskrows=[]
                for si in range(8):
                    for backend in ['prototype_jax','thrml']:
                        start=time.perf_counter();seed=10000+100*ts+si
                        if formulation=='adaptive_price':samples,metrics=e.sample_ising(problem,seed,backend,True);admissible=None
                        else:samples,admissible,metrics=sample_prefix(problem,seed,backend,formulation=='prefix_cardinality_buckets')
                        mask,score=e.select_and_score(task,samples,nonempty=True,admissible=admissible)
                        elapsed=(time.perf_counter()-start)*1000
                        row=p.record(task,backend,mask,extra={'formulation':formulation,'seed':si,'sampler_seed':seed,'exact_energy':optimum,'energy_gap':task.energy(mask)-optimum,**metrics,**score,'decision_wall_ms':elapsed-score['diagnostic_verifier_ms'],'total_wall_ms':elapsed})
                        stream.write(json.dumps(row)+'\n');stream.flush();size.append(row);taskrows.append(row)
                if any(all(not r['verified'] or r['selected_empty'] or not r['selected_budget_feasible'] for r in taskrows if r['arm']==b) for b in ['prototype_jax','thrml']):
                    stops.append({'N':n,'task_seed':ts,'reason':'one backend has all8 selected packs invalid/empty/infeasible'});immediate=True;break
            allrows.extend(size)
            healthy=all(r['verified'] and not r['selected_empty'] and r['selected_budget_feasible'] for r in size)
            tolerance=gaps.get(n,0.)+1e-5
            energy_ok=all(np.mean([r['energy_gap'] for r in size if r['arm']==b])<=tolerance for b in ['prototype_jax','thrml'])
            print(json.dumps({'formulation':formulation,'N':n,'rows':len(size),'healthy':healthy,'energy_ok':energy_ok,'immediate':immediate}),flush=True)
            if immediate or not healthy or not energy_ok:
                stops.append({'N':n,'reason':'first failing size; no sample increase or retuning','healthy':healthy,'energy_ok':energy_ok});break
    receipt={'formulation':formulation,'rows':len(allrows),'stops':stops,'completed_N':sorted(set(r['N'] for r in allrows))}
    with output.with_suffix('.stop.json').open('x') as f:json.dump(receipt,f,indent=2)
if __name__=='__main__':
    ap=argparse.ArgumentParser();ap.add_argument('--output',required=True);ap.add_argument('--formulation',required=True);a=ap.parse_args();run(a.output,a.formulation)
