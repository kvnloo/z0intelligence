import json
from pathlib import Path
import numpy as np
import jax
import jax.numpy as jnp
from thrml import SpinNode, Block
from thrml.models import IsingEBM
from benchmarks.thermocontext import phase_a_prototype as p
from benchmarks.thermocontext import thrml_experiment as e

def test_real_thrml_energy_matches_all_n8_states():
    task=p.make_task(8,0); problem=e.public_problem(task)
    nodes=[SpinNode() for _ in range(8)]; edges=np.argwhere(np.triu(task.J,1)!=0)
    b,w=e.ising_parameters(jnp.asarray(problem.unary),jnp.asarray(task.J))
    model=IsingEBM(nodes,[(nodes[i],nodes[j]) for i,j in edges],b,jnp.asarray([w[i,j] for i,j in edges]),jnp.asarray(p.BETA))
    states=np.array([[(bits>>i)&1 for i in range(8)] for bits in range(256)],dtype=bool)
    energies=np.asarray(jax.vmap(lambda x:model.energy([x],[Block(nodes)]))(jnp.asarray(states)))/p.BETA
    constant=.5*problem.unary.sum()+.25*np.triu(task.J,1).sum()
    np.testing.assert_allclose(energies+constant,[task.energy(x) for x in states],rtol=0,atol=1e-5)

def test_component_dp_matches_frozen_exact_cohort():
    source=Path(__file__).parents[2]/'results/thermocontext/2026-10-02-thrml-wave1/original-reproduction.json'
    exact={r['task_id']:r for r in json.loads(source.read_text())['rows'] if r['arm']=='exact'}
    for n in (8,12,16):
        for seed in range(12):
            task=p.make_task(n,seed);mask=e.component_dp(e.public_problem(task))
            assert abs(task.energy(mask)-exact[task.task_id]['energy'])<1e-5
            assert task.tokens(mask)<=task.budget

def test_public_problem_excludes_oracle_labels():
    assert set(e.PublicProblem.__dataclass_fields__)=={'unary','interactions','costs','budget'}
    for n in (8,12,16,32,64,128):
        task=p.make_task(n,0)
        for group in p.coloring(task.J):
            assert not np.any(task.J[np.ix_(group,group)])
        assert max(map(len,e.components(task.J)))==6

def test_selection_uses_energy_not_verifier():
    task=p.make_task(8,0); states=np.array([[(bits>>i)&1 for i in range(8)] for bits in range(256)],dtype=bool)
    winner,score=e.select_and_score(task,states,nonempty=False)
    eligible=[x for x in states if task.tokens(x)<=task.budget]
    assert abs(task.energy(winner)-min(map(task.energy,eligible)))<1e-5
    assert score['decode_rank_ms']>=0 and score['diagnostic_verifier_ms']>=0

def test_prefix_factor_energy_and_declared_sparsity():
    from thrml.models.ebm import FactorizedEBM
    from benchmarks.thermocontext.thrml_prefix import prefix_runner,structure
    task=p.make_task(8,0);problem=e.public_problem(task);n=8
    for cardinality in (False,True):
        entry=prefix_runner(problem,'thrml',cardinality)
        factors=entry['factors'](jnp.asarray(problem.unary),jnp.asarray(problem.interactions),jnp.asarray(problem.costs),jnp.asarray(problem.budget),jnp.asarray(1.))
        model=FactorizedEBM(factors);states=[];expected=[]
        for bits in range(256):
            x=np.array([(bits>>i)&1 for i in range(n)],dtype=np.uint8)
            y=np.concatenate([[0],np.cumsum(x*(1 if cardinality else problem.costs))])
            if not cardinality:y=np.minimum(y,problem.budget)
            state=np.concatenate([x,y] if cardinality else [x,y,[problem.budget-y[-1]]]).astype(np.uint8)
            violations=np.count_nonzero(y[1:]!=y[:-1]+x*(1 if cardinality else problem.costs))
            if not cardinality:violations+=int(y[-1]==0)
            states.append(state);expected.append(task.energy(x)+violations)
        actual=jax.vmap(lambda state:model.energy([state],[Block(entry['nodes'])]))(jnp.asarray(states))/p.BETA
        np.testing.assert_allclose(actual,expected,atol=1e-5,rtol=0)
        for size in (8,12,16,32,64,128):
            stats=structure(e.public_problem(p.make_task(size,0)),cardinality)[-1]
            assert stats['max_degree']<=6 and stats['factor_arity']==3
            assert stats['factor_table_bytes']<=67108864

def test_prefix_prototype_conditionals_match_full_energy_differences():
    from benchmarks.thermocontext.thrml_prefix import conditional_energies,structure
    task=p.make_task(8,0);problem=e.public_problem(task);n=8;rng=np.random.default_rng(42)
    for cardinality in (False,True):
        _,D,count,groups,clamps,_=structure(problem,cardinality)
        state=rng.integers(0,D,(2,count),dtype=np.uint8);state[:,:n]%=2;state[:,n]=0
        step=np.ones(n,dtype=int) if cardinality else problem.costs
        def energy(s):
            x=s[:n].astype(np.int32);y=s[n:2*n+1].astype(np.int32)
            violation=np.count_nonzero(y[1:]!=y[:-1]+step*x)
            if not cardinality:violation+=int(y[-1]+int(s[-1])!=problem.budget)+int(y[-1]==0)
            return task.energy(x)+violation
        for group in groups:
            values=np.asarray(conditional_energies(jnp.asarray(state),group,n,D,jnp.asarray(problem.unary),jnp.asarray(problem.interactions),jnp.asarray(step),problem.budget,1.,cardinality))
            for chain in range(2):
                for col,node in enumerate(group):
                    expected=[]
                    for v in range(2 if node<n else D):
                        changed=state[chain].copy();changed[node]=v;expected.append(energy(changed))
                    np.testing.assert_allclose(values[chain,col]-values[chain,col,0],np.array(expected)-expected[0],atol=1e-5,rtol=0)
