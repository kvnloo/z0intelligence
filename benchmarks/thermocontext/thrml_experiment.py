"""Frozen-cohort, CPU-only THRML qualification. Selection never sees verifier labels."""
from __future__ import annotations
import argparse, dataclasses, hashlib, json, math, time
from pathlib import Path
import equinox as eqx
import jax
import jax.numpy as jnp
import numpy as np
from thrml import Block, SpinNode, SamplingSchedule, sample_states
from thrml.models import IsingEBM, IsingSamplingProgram
from benchmarks.thermocontext import phase_a_prototype as p

@dataclasses.dataclass(frozen=True)
class PublicProblem:
    unary: np.ndarray
    interactions: np.ndarray
    costs: np.ndarray
    budget: int


def public_problem(task):
    return PublicProblem(task.unary(), task.J.copy(), np.asarray([i.tokens for i in task.items]), task.budget)


def components(J):
    remaining=set(range(len(J))); result=[]
    while remaining:
        pending=[min(remaining)]; group=[]
        while pending:
            i=pending.pop()
            if i not in remaining: continue
            remaining.remove(i);group.append(i)
            pending.extend(int(j) for j in np.flatnonzero(J[i]) if int(j) in remaining)
        result.append(sorted(group))
    return result


def component_dp(problem):
    """Exact component knapsack; exploits the frozen cohort's disconnected graph."""
    n=len(problem.unary); dp={0:(0.0,0)}
    for group in components(problem.interactions):
        options={}
        for bits in range(1<<len(group)):
            x=np.array([(bits>>k)&1 for k in range(len(group))],dtype=np.float32)
            cost=int(problem.costs[group]@x)
            if cost>problem.budget: continue
            J=problem.interactions[np.ix_(group,group)]
            e=float(problem.unary[group]@x+0.5*x@J@x)
            mask=sum((1<<i) for i,take in zip(group,x) if take)
            if cost not in options or e<options[cost][0]:options[cost]=(e,mask)
        new={}
        for c,(e,m) in dp.items():
            for d,(f,k) in options.items():
                if c+d<=problem.budget and (c+d not in new or e+f<new[c+d][0]):new[c+d]=(e+f,m|k)
        dp=new
    _,mask=min(dp.values(),key=lambda z:z[0])
    return np.array([(mask>>i)&1 for i in range(n)],dtype=bool)


def ising_parameters(unary,J):
    """For x=(s+1)/2: E(x)=constant - b.s - sum(w_ij*s_i*s_j)."""
    return -(unary/2+jnp.sum(J,axis=1)/4), -J/4


_CACHE={}

def ising_runner(problem, backend, adaptive):
    n=len(problem.unary);groups=p.coloring(problem.interactions)
    edges=np.argwhere(np.triu(problem.interactions,1)!=0)
    cachekey=(backend,adaptive,n,tuple(map(tuple,edges)),tuple(tuple(map(int,g)) for g in groups))
    if cachekey in _CACHE:return _CACHE[cachekey]
    nodes=[SpinNode() for _ in range(n)]; all_block=Block(nodes)
    blocks=[Block([nodes[int(i)] for i in group]) for group in groups]
    pairs=[(nodes[int(i)],nodes[int(j)]) for i,j in edges]
    gi=[jnp.asarray(g) for g in groups]
    schedule=SamplingSchedule(n_warmup=11 if adaptive else 81,n_samples=10 if adaptive else 80,steps_per_sample=1)

    def thrml_phase(key,x,u,J):
        b,w=ising_parameters(u,J)
        weights=jnp.asarray([w[int(i),int(j)] for i,j in edges])
        model=IsingEBM(nodes,pairs,b,weights,jnp.asarray(p.BETA))
        program=IsingSamplingProgram(model,blocks,[])
        init=[x[:,g].astype(jnp.bool_) for g in gi]
        keys=jax.random.split(key,p.THERMO_CHAINS)
        samples=jax.vmap(lambda k,states:sample_states(k,program,schedule,states,[],[all_block])[0])(keys,init)
        return jnp.swapaxes(samples,0,1).astype(jnp.float32)

    def prototype_phase(key,x,u,J):
        def sweep(carry,_):
            key,x=carry
            for g in gi:
                key,k=jax.random.split(key)
                delta=u[g][None,:]+x@J[:,g]
                x=x.at[:,g].set(jax.random.bernoulli(k,jax.nn.sigmoid(-p.BETA*delta)).astype(jnp.float32))
            return (key,x),x
        (_,x),trajectory=jax.lax.scan(sweep,(key,x),None,length=20)
        return trajectory[10:]

    phase=thrml_phase if backend=='thrml' else prototype_phase
    @eqx.filter_jit
    def run(key,u,J,costs,budget):
        key,k=jax.random.split(key)
        x=jax.random.bernoulli(k,.35,(p.THERMO_CHAINS,n)).astype(jnp.float32)
        if not adaptive:
            trajectory=thrml_phase(key,x,u,J)
            return trajectory,jnp.zeros((1,)),jnp.zeros((1,))
        def epoch(carry,index):
            key,x,price=carry;key,k=jax.random.split(key)
            trajectory=phase(k,x,u+price*costs/budget,J)
            last=trajectory[-1];mean_tokens=jnp.mean(last@costs);mean_count=jnp.mean(jnp.sum(last,axis=1))
            new_price=jnp.clip(price+(mean_tokens/budget-1)/jnp.sqrt(index+1.),0.,4.)
            new_price=jnp.where(mean_count<1.,new_price*.5,new_price)
            return (key,last,new_price),(trajectory,price,mean_tokens)
        _,(samples,prices,loads)=jax.lax.scan(epoch,(key,x,jnp.array(0.)),jnp.arange(8))
        return samples.reshape((80,p.THERMO_CHAINS,n)),prices,loads
    _CACHE[cachekey]={'fn':run,'compiled':None,'groups':groups,'edges':edges}
    return _CACHE[cachekey]


def graph_stats(J):
    d=np.count_nonzero(J,axis=1)
    return {'nodes':len(J),'edges':int(d.sum()//2),'mean_degree':float(d.mean()),'max_degree':int(d.max(initial=0)),
            'component_sizes':[len(c) for c in components(J)]}


def sample_ising(problem,seed,backend='thrml',adaptive=False):
    start=time.perf_counter();entry=ising_runner(problem,backend,adaptive);construction=(time.perf_counter()-start)*1000
    args=(jax.random.key(seed),jnp.asarray(problem.unary),jnp.asarray(problem.interactions),jnp.asarray(problem.costs,dtype=jnp.float32),jnp.asarray(problem.budget,dtype=jnp.float32))
    compile_ms=lower_ms=0.; cold_shape=entry['compiled'] is None
    if entry['compiled'] is None:
        t=time.perf_counter();lowered=entry['fn'].lower(*args);lower_ms=(time.perf_counter()-t)*1000
        t=time.perf_counter();entry['compiled']=lowered.compile();compile_ms=(time.perf_counter()-t)*1000
    t=time.perf_counter();samples,prices,loads=entry['compiled'](*args);samples=np.asarray(samples,dtype=bool);prices=np.asarray(prices);loads=np.asarray(loads);warm_ms=(time.perf_counter()-t)*1000
    return samples,{'construction_ms':construction,'lower_ms':lower_ms,'compile_ms':compile_ms,'sampling_ms':warm_ms,'cold_shape':cold_shape,'prices':prices.tolist(),'epoch_mean_tokens':loads.tolist(),**graph_stats(problem.interactions)}


def select_and_score(task,samples,*,nonempty,admissible=None):
    """Rank by frozen energy/budget ONLY. Verify is used after proposals, for metrics."""
    selection_start=time.perf_counter();flat=samples.reshape(-1,len(task.items));cost=np.asarray([i.tokens for i in task.items]);unary=task.unary()
    tokens=flat@cost;energy=flat.astype(np.float32)@unary+.5*np.einsum('bi,ij,bj->b',flat.astype(np.float32),task.J,flat.astype(np.float32))
    feasible=tokens<=task.budget
    if nonempty:feasible &= flat.any(axis=1)
    if admissible is not None:feasible &= admissible.reshape(-1)
    indices=np.flatnonzero(feasible)
    winner=flat[indices[np.argmin(energy[indices])]].copy() if len(indices) else np.zeros(len(task.items),dtype=bool)
    selection_ms=(time.perf_counter()-selection_start)*1000
    t=time.perf_counter();first=None;verifier_calls=0
    for index in indices:
        verifier_calls+=1
        if task.verify(flat[index]):first=int(index)+1;break
    diagnostic_ms=(time.perf_counter()-t)*1000
    final_start=time.perf_counter();selected_verified=task.verify(winner);final_ms=(time.perf_counter()-final_start)*1000;verifier_calls+=1
    return winner,{'first_valid_sample':first,'verifier_calls':verifier_calls,'diagnostic_verifier_ms':diagnostic_ms,'final_verifier_ms':final_ms,'decode_rank_ms':selection_ms,
                  'candidate_count':len(flat),'budget_feasible_fraction':float(np.mean(tokens<=task.budget)),
                  'empty_fraction':float(np.mean(~flat.any(axis=1))),'admissible_candidates':int(len(indices)),
                  'selected_verifier_pass':bool(selected_verified),'selected_budget_feasible':bool(task.tokens(winner)<=task.budget),
                  'selected_empty':bool(not winner.any())}


def run_ising_cohort(output,formulation):
    output=Path(output);output.parent.mkdir(parents=True,exist_ok=True)
    original=json.loads((output.parent/'original-reproduction.json').read_text())
    oracle={r['task_id']:r for r in original['rows'] if r['arm']=='exact'}
    with output.open('x') as stream:
        for N in (8,12,16):
            for task_seed in range(12):
                task=p.make_task(N,task_seed);problem=public_problem(task)
                if formulation=='fixed_original':
                    t=time.perf_counter();mask=component_dp(problem);elapsed=(time.perf_counter()-t)*1000
                    vt=time.perf_counter();task.verify(mask);final_ms=(time.perf_counter()-vt)*1000
                    row=p.record(task,'component_dp',mask,extra={'optimizer_ms':elapsed,'final_verifier_ms':final_ms,'decision_wall_ms':elapsed+final_ms,'total_wall_ms':elapsed+final_ms,**graph_stats(task.J),'energy_gap':task.energy(mask)-oracle[task.task_id]['energy']})
                    stream.write(json.dumps(row)+'\n');stream.flush()
                for seed_index in range(8):
                    for backend in (['thrml'] if formulation=='fixed_original' else ['prototype_jax','thrml']):
                        seed=10000+task_seed*100+seed_index;t=time.perf_counter()
                        samples,metrics=sample_ising(problem,seed,backend,formulation=='adaptive_price')
                        mask,score=select_and_score(task,samples,nonempty=formulation!='fixed_original')
                        row=p.record(task,backend,mask,extra={'formulation':formulation,'seed':seed_index,'sampler_seed':seed,
                            'exact_energy':oracle[task.task_id]['energy'],'energy_gap':task.energy(mask)-oracle[task.task_id]['energy'],
                            **metrics,**score,'decision_wall_ms':(time.perf_counter()-t)*1000-score['diagnostic_verifier_ms'],'total_wall_ms':(time.perf_counter()-t)*1000})
                        stream.write(json.dumps(row)+'\n');stream.flush()
            print(f'completed {formulation} N={N}',flush=True)


if __name__=='__main__':
    ap=argparse.ArgumentParser();ap.add_argument('--output',required=True);ap.add_argument('--formulation',choices=['fixed_original','adaptive_price'],default='fixed_original');args=ap.parse_args();run_ising_cohort(args.output,args.formulation)
