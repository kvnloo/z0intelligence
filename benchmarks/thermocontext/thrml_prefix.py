"""Local prefix constraints, with actual THRML and independent JAX Gibbs kernels."""
import time
import numpy as np
import jax
import jax.numpy as jnp
import equinox as eqx
from thrml import CategoricalNode, Block, BlockGibbsSpec, FactorSamplingProgram, SamplingSchedule, sample_states
from thrml.models.discrete_ebm import CategoricalEBMFactor, CategoricalGibbsConditional
from benchmarks.thermocontext import phase_a_prototype as p
from benchmarks.thermocontext import thrml_experiment as e

_CACHE={}
FRACTIONS=jnp.asarray([.02,.05,.1,.2,.35,.5,.75,1.])

def structure(problem,cardinality):
    n=len(problem.unary); D=n+1 if cardinality else problem.budget+1
    count=2*n+1+(not cardinality); adjacency=np.zeros((count,count),dtype=np.int8)
    adjacency[:n,:n]=problem.interactions!=0
    for i in range(n):
        ids=[i,n+i,n+i+1]
        for a in ids:
            for b in ids:
                if a!=b:adjacency[a,b]=1
    if not cardinality:adjacency[2*n,2*n+1]=adjacency[2*n+1,2*n]=1
    clamps=[n,2*n] if cardinality else [n]
    groups=[]
    for group in p.coloring(adjacency):
        for item in (True,False):
            ids=[int(i) for i in group if i not in clamps and (i<n)==item]
            if ids:groups.append(ids)
    entries=n*2*D*D+2*n+int(np.count_nonzero(np.triu(problem.interactions,1)))*4
    if not cardinality:entries+=D*D+D
    stats={**e.graph_stats(adjacency),'original_component_sizes':[len(g) for g in e.components(problem.interactions)],
           'auxiliary_nodes':count-n,'categorical_domain':D,'factor_arity':3,'factor_table_entries':entries,'factor_table_bytes':entries*4}
    if stats['max_degree']>6 or D>255 or entries*4>67108864:raise ValueError(f'preregistered resource cap exceeded: {stats}')
    return n,D,count,groups,clamps,stats

def conditional_energies(state,group,n,D,u,J,step,budget,penalty,cardinality):
    g=jnp.asarray(group);itemvals=jnp.arange(2);domains=jnp.arange(D)
    if group[0]<n:  # resolved in Python while tracing
        v=itemvals[None,None,:]
        delta=u[g][None,:]+state[:,:n].astype(jnp.float32)@J[:,g]
        energy=delta[:,:,None]*v+penalty*(state[:,n+g+1,None].astype(jnp.int32)!=state[:,n+g,None].astype(jnp.int32)+step[g][None,:,None]*v)
    else:
        v=domains[None,None,:]; energy=jnp.zeros((state.shape[0],len(g),D))
        for col,node in enumerate(group):
            if node==2*n+1:
                local=penalty*(state[:,2*n,None].astype(jnp.int32)+domains[None,:]!=budget)
            else:
                j=node-n;local=jnp.zeros((state.shape[0],D))
                if j>0:local+=penalty*(domains[None,:]!=state[:,node-1,None].astype(jnp.int32)+step[j-1]*state[:,j-1,None])
                if j<n:local+=penalty*(state[:,node+1,None].astype(jnp.int32)!=domains[None,:]+step[j]*state[:,j,None])
                elif not cardinality:local+=penalty*((domains[None,:]+state[:,2*n+1,None].astype(jnp.int32)!=budget)+(domains[None,:]==0).astype(jnp.int32))
            energy=energy.at[:,col,:].set(local)
    return energy

def prefix_runner(problem,backend,cardinality):
    n,D,count,groups,clamps,stats=structure(problem,cardinality)
    edges=np.argwhere(np.triu(problem.interactions,1)!=0)
    cachekey=(backend,cardinality,n,D,tuple(map(tuple,edges)),tuple(map(tuple,groups)))
    if cachekey in _CACHE:return _CACHE[cachekey]
    nodes=[CategoricalNode() for _ in range(count)];free=[Block([nodes[i] for i in g]) for g in groups]
    clampblocks=[Block([nodes[i] for i in clamps])];spec=BlockGibbsSpec(free,clampblocks)
    samplers=[CategoricalGibbsConditional(2 if g[0]<n else D) for g in groups]
    schedule=SamplingSchedule(n_warmup=11,n_samples=10,steps_per_sample=1)
    gs=[jnp.asarray(g) for g in groups]; allblock=Block(nodes)
    itemvals=jnp.arange(2); domains=jnp.arange(D)
    def tables(u,J,costs,budget,penalty):
        step=jnp.ones_like(costs) if cardinality else costs
        invalid=domains[None,:,None,None]+step[:,None,None,None]*itemvals[None,None,None,:] != domains[None,None,:,None]
        factors=[CategoricalEBMFactor([Block(nodes[:n])],-p.BETA*u[:,None]*itemvals[None,:]),
            CategoricalEBMFactor([Block([nodes[int(i)] for i,j in edges]),Block([nodes[int(j)] for i,j in edges])],-p.BETA*jnp.asarray([J[int(i),int(j)] for i,j in edges])[:,None,None]*itemvals[None,:,None]*itemvals[None,None,:]),
            CategoricalEBMFactor([Block(nodes[n:2*n]),Block(nodes[n+1:2*n+1]),Block(nodes[:n])],-p.BETA*penalty*invalid)]
        if not cardinality:
            factors.extend([CategoricalEBMFactor([Block([nodes[2*n]]),Block([nodes[2*n+1]])],(-p.BETA*penalty*(domains[:,None]+domains[None,:]!=budget))[None,:,:]),
                CategoricalEBMFactor([Block([nodes[2*n]])],(-p.BETA*penalty*(domains==0))[None,:])])
        return factors
    def thrml_phase(key,state,u,J,costs,budget,penalty):
        program=FactorSamplingProgram(spec,samplers,tables(u,J,costs,budget,penalty),[])
        keys=jax.random.split(key,p.THERMO_CHAINS)
        init=[state[:,g] for g in gs];clamped=[state[:,jnp.asarray(clamps)]]
        values=jax.vmap(lambda k,a,c:sample_states(k,program,schedule,a,c,[allblock])[0])(keys,init,clamped)
        return jnp.swapaxes(values,0,1)
    def prototype_phase(key,state,u,J,costs,budget,penalty):
        step=jnp.ones_like(costs) if cardinality else costs
        def sweep(carry,_):
            key,state=carry
            for group,g in zip(groups,gs):
                key,k=jax.random.split(key)
                if int(g.shape[0])==0:continue
                # Each independent block has a homogeneous domain.
                energy=conditional_energies(state,group,n,D,u,J,step,budget,penalty,cardinality)
                draw=jax.random.categorical(k,-p.BETA*energy,axis=-1).astype(jnp.uint8)
                state=state.at[:,g].set(draw)
            return (key,state),state
        _,trajectory=jax.lax.scan(sweep,(key,state),None,length=20)
        return trajectory[10:]
    phase=thrml_phase if backend=='thrml' else prototype_phase
    @eqx.filter_jit
    def run(key,u,J,costs,budget):
        key,k=jax.random.split(key)
        if cardinality:
            maxk=jnp.minimum(n,budget//jnp.min(costs)).astype(jnp.int32)
            buckets=jnp.arange(p.THERMO_CHAINS)%maxk+1
            ranks=jnp.argsort(jnp.argsort(jax.random.uniform(k,(p.THERMO_CHAINS,n)),axis=1),axis=1)
            x=(ranks<buckets[:,None]).astype(jnp.uint8);prefix=jnp.cumsum(x,axis=1)
        else:
            x=jax.random.bernoulli(k,.35,(p.THERMO_CHAINS,n)).astype(jnp.uint8)
            prefix=jnp.minimum(jnp.cumsum(x*costs,axis=1),budget).astype(jnp.uint8)
        state=jnp.concatenate([x,jnp.zeros((p.THERMO_CHAINS,1),dtype=jnp.uint8),prefix],axis=1)
        if not cardinality:state=jnp.concatenate([state,(budget-prefix[:,-1,None]).astype(jnp.uint8)],axis=1)
        penalty=1+jnp.sum(jnp.abs(u))+jnp.sum(jnp.abs(jnp.triu(J,1)))
        def epoch(carry,fraction):
            key,state=carry;key,k=jax.random.split(key)
            trajectory=phase(k,state,u,J,costs,budget,penalty*fraction)
            return (key,trajectory[-1]),trajectory
        _,samples=jax.lax.scan(epoch,(key,state),FRACTIONS)
        return samples.reshape(80,p.THERMO_CHAINS,count)
    entry={'fn':run,'compiled':None,'stats':stats,'factors':tables,'nodes':nodes};_CACHE[cachekey]=entry;return entry

def sample_prefix(problem,seed,backend,cardinality):
    start=time.perf_counter();entry=prefix_runner(problem,backend,cardinality);assembly=(time.perf_counter()-start)*1000
    args=(jax.random.key(seed),jnp.asarray(problem.unary),jnp.asarray(problem.interactions),jnp.asarray(problem.costs,dtype=jnp.int32),jnp.asarray(problem.budget,dtype=jnp.int32))
    cold=entry['compiled'] is None;lower_ms=compile_ms=0
    if cold:
        t=time.perf_counter();lowered=entry['fn'].lower(*args);lower_ms=(time.perf_counter()-t)*1000
        t=time.perf_counter();entry['compiled']=lowered.compile();compile_ms=(time.perf_counter()-t)*1000
    t=time.perf_counter();samples=np.asarray(entry['compiled'](*args));sampling_ms=(time.perf_counter()-t)*1000
    n=len(problem.unary);x=samples[:,:,:n].astype(bool);y=samples[:,:,n:2*n+1].astype(np.int32)
    step=np.ones(n,dtype=int) if cardinality else problem.costs
    valid=np.all(y[:,:,1:]==y[:,:,:-1]+x*step,axis=-1)&(y[:,:,0]==0)&(y[:,:,-1]>0)
    if not cardinality:valid &= y[:,:,-1]+samples[:,:,2*n+1]==problem.budget
    return x,valid,{'construction_ms':assembly,'lower_ms':lower_ms,'compile_ms':compile_ms,'sampling_ms':sampling_ms,'cold_shape':cold,'aux_consistent_fraction':float(valid.mean()),**entry['stats']}
