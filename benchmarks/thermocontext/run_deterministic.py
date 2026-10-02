"""Retain exact-component masks and measured baselines for attempted cohorts."""
import argparse,json,time
from pathlib import Path
from benchmarks.thermocontext import phase_a_prototype as p
from benchmarks.thermocontext import thrml_experiment as e

def run(path):
    with path.open('x') as stream:
        for n,seeds in [(8,range(12)),(12,range(12)),(16,range(12)),(32,range(10))]:
            for seed in seeds:
                task=p.make_task(n,seed)
                start=time.perf_counter();problem=e.public_problem(task);assembly=(time.perf_counter()-start)*1000
                arms=[('champion',lambda:p.champion(task)),('topk',lambda:p.topk(task)),('greedy',lambda:p.greedy(task)),('component_dp',lambda:e.component_dp(problem))]
                for name,fn in arms:
                    start=time.perf_counter();mask=fn();solve=(time.perf_counter()-start)*1000
                    start=time.perf_counter();task.verify(mask);verify=(time.perf_counter()-start)*1000
                    row=p.record(task,name,mask,extra={'assembly_ms':assembly,'optimizer_ms':solve,'final_verifier_ms':verify,'decision_wall_ms':assembly+solve+verify,'budget_feasible':task.tokens(mask)<=task.budget,**e.graph_stats(task.J)})
                    stream.write(json.dumps(row)+'\n')
if __name__=='__main__':
    ap=argparse.ArgumentParser();ap.add_argument('--output',required=True,type=Path);a=ap.parse_args();run(a.output)
