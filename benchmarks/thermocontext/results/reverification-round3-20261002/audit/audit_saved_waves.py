"""Portable saved-evidence audit. No inference, sampling, or source mutation."""
from __future__ import annotations
import argparse, collections, hashlib, importlib.util, json, math, sys
from pathlib import Path
import numpy as np


def digest(path): return hashlib.sha256(Path(path).read_bytes()).hexdigest()
def read(path): return json.loads(Path(path).read_text())
def rows(path):
    path=Path(path)
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()] if path.suffix=='.jsonl' else read(path)['rows']
def save(path, value):
    with Path(path).open('x') as f: json.dump(value,f,indent=2,sort_keys=True,allow_nan=False);f.write('\n')
def load_auditor(repo):
    path=repo/'benchmarks/thermocontext/audit_next_wave.py'
    spec=importlib.util.spec_from_file_location('independent_saved_wave_math',path)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    return module, module.load_frozen(repo/'benchmarks/thermocontext/phase_a_prototype.py')
def identity(row): return row['task_id'], row['arm'], row.get('seed')
def compare(left,right):
    old={identity(r):r for r in left};new={identity(r):r for r in right}
    if len(old)!=len(left) or len(new)!=len(right): raise ValueError('duplicate comparison row')
    fields=('N','mask','ids','count','energy','tokens','budget','verified','energy_gap','exact_energy',
            'first_valid_sample','sample_count','feasible','empty','no_feasible','selected_sample',
            'feasible_sample_count','empty_sample_count')
    mismatches=[]
    for key in sorted(set(old)&set(new),key=str):
        for field in fields:
            if field in old[key] and field in new[key] and old[key][field]!=new[key][field]:
                mismatches.append({'identity':key,'field':field,'left':old[key][field],'right':new[key][field]})
    return {'prior_rows':len(left),'new_rows':len(right),'same_identity_grid':old.keys()==new.keys(),
            'compared_fields':fields,'exact_non_timing_equality':not mismatches and old.keys()==new.keys(),
            'mismatches':mismatches,'timing_equality_tested':False}

def independent_component_optimum(task, arithmetic):
    """Enumeration within every connected component plus integer budget DP."""
    n=len(task.items);remaining=set(range(n));components=[]
    while remaining:
        stack=[min(remaining)];component=[]
        while stack:
            i=stack.pop()
            if i not in remaining: continue
            remaining.remove(i);component.append(i)
            stack.extend(j for j in remaining if task.J[i,j]!=0 or task.J[j,i]!=0)
        components.append(component)
    if max(map(len,components))>16: raise ValueError('unsupported large connected oracle')
    frontier={0:0.0}
    for group in components:
        masks=np.zeros((1<<len(group),n),dtype=bool)
        for bits in range(len(masks)):
            masks[bits,group]=[(bits>>i)&1 for i in range(len(group))]
        metrics=arithmetic(task,masks);options={}
        for cost,energy in zip(metrics['tokens'],metrics['energy']):
            if cost<=task.budget: options[int(cost)]=min(options.get(int(cost),math.inf),float(energy))
        updated={}
        for consumed,value in frontier.items():
            for cost,energy in options.items():
                if consumed+cost<=task.budget:
                    key=consumed+cost;updated[key]=min(updated.get(key,math.inf),value+energy)
        frontier=updated
    return min(frontier.values())

def selected_audit(sources,auditor,frozen):
    tasks={};references={};groups=collections.defaultdict(list);failures=[];count=0
    for source,data in sources.items():
        seen=set()
        for row in data:
            key=identity(row)
            if key in seen: failures.append([source,key,'duplicate_row'])
            seen.add(key);task_seed=int(row['task_id'].split('-s')[1]);task_key=row['N'],task_seed
            if task_key not in tasks:
                task=frozen.make_task(*task_key);tasks[task_key]=task
                references[task_key]=(auditor.independent_exact(task)[1] if row['N']<=16 else
                    independent_component_optimum(task,auditor.independent_metrics))
            task=tasks[task_key];ids=row['ids'];mask=np.array([it.id in ids for it in task.items])
            metrics=auditor.independent_metrics(task,mask);issues=[]
            if len(ids)!=len(set(ids)) or set(ids)-{it.id for it in task.items} or row['count']!=int(mask.sum()):issues.append('ids_or_count')
            if 'mask' in row and list(map(int,mask))!=row['mask']:issues.append('mask')
            if row['budget']!=task.budget:issues.append('budget')
            for field in ('tokens','verified'):
                if row[field]!=metrics[field][0]:issues.append(field)
            if abs(row['energy']-metrics['energy'][0])>1e-5:issues.append('energy')
            for field,value in [('exact_energy',references[task_key]),('energy_gap',row['energy']-references[task_key])]:
                if field in row and abs(row[field]-value)>1e-5:issues.append(field)
            if 'sampler_seed' in row and row['sampler_seed']!=10000+task_seed*100+row['seed']:issues.append('seed_mapping')
            if 'candidate_count' in row and row['candidate_count']!=5120:issues.append('candidate_count')
            if 'factor_arity' in row and not(row['max_degree']<=6 and row['factor_arity']==3 and row['categorical_domain']<=255 and row['factor_table_bytes']<=67108864):issues.append('declared_resource_caps')
            if issues:failures.append([source,key,issues])
            groups[source,row['N'],row['arm']].append(row);count+=1
    summary=[]
    for (source,n,arm),group in sorted(groups.items()):
        summary.append({'source':source,'N':n,'arm':arm,'rows':len(group),'tasks':len({r['task_id'] for r in group}),
            'verified':sum(r['verified'] for r in group),'verified_feasible':sum(r['verified'] and r['tokens']<=r['budget'] for r in group),
            'empty':sum(r['count']==0 for r in group),'overbudget':sum(r['tokens']>r['budget'] for r in group),
            'exact_hits':sum(abs(r.get('energy_gap',r['energy']-references[(r['N'],int(r['task_id'].split('-s')[1]))]))<=1e-5 for r in group),
            'mean_energy_gap':float(np.mean([r['energy']-references[(r['N'],int(r['task_id'].split('-s')[1]))] for r in group]))})
    return {'rows_audited':count,'tasks_reconstructed':len(tasks),'small_tasks_independently_enumerated':sum(n<=16 for n,_ in tasks),
        'large_tasks_component_enumerated':sum(n>16 for n,_ in tasks),'all_selected_checks_pass':not failures,'failures':failures,'groups':summary}

def hashes(root):
    path=root/'SHA256SUMS.json'
    if not path.exists(): return {'present':False}
    manifest=read(path)
    if 'files' in manifest: manifest=manifest['files']
    checks={}
    for key,value in manifest.items():
        expected=value['sha256'] if isinstance(value,dict) else value
        checks[key]=digest(root/key)==expected
    return {'present':True,'checked':len(checks),'all_pass':all(checks.values()),'failures':[k for k,v in checks.items() if not v]}

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--repo',type=Path,required=True);ap.add_argument('--mode',choices=['reproduction','small','independent'],required=True)
    ap.add_argument('--root',type=Path,required=True);ap.add_argument('--prior',type=Path);ap.add_argument('--exported-source',type=Path);ap.add_argument('--out',type=Path,required=True);args=ap.parse_args()
    auditor,frozen=load_auditor(args.repo)
    report={'mode':args.mode,'no_new_sampling':True,'no_provider_calls':True,'timing_equality_tested':False,
        'auditor_sha256':digest(Path(__file__)),'frozen_prototype_sha256':auditor.FROZEN_SHA256}
    if args.mode in ('reproduction','small'):
        name='result.json' if args.mode=='reproduction' else 'rows.jsonl'
        current=rows(args.root/name);previous=rows(args.prior/name)
        report['comparison']=compare(previous,current);report['artifact_hashes']=hashes(args.root)
        report['selected_arithmetic']=selected_audit({'rerun':current},auditor,frozen)
        if args.mode=='small':
            if not (args.root/'summary.json').exists():raise ValueError('small-N run still incomplete; do not audit it as final')
            report['retained_trace_audit']=auditor.audit_results({'rows':current},args.root,frozen)
            report['source_bindings_match_prior']=read(args.root/'provenance.json')['source_sha256']==read(args.prior/'provenance.json')['source_sha256']
        report['result_sha256']=digest(args.root/name)
    else:
        sources={'unchanged_original':rows(args.root/'original-reproduction.json')}
        sources.update({name:rows(args.root/(name+'.jsonl')) for name in ['fixed-thrml','adaptive-price','prefix-token','prefix-cardinality','deterministic-frontier']})
        report['selected_arithmetic']=selected_audit(sources,auditor,frozen)
        expected={'unchanged_original':432,'fixed-thrml':324,'adaptive-price':736,'prefix-token':192,'prefix-cardinality':192,'deterministic-frontier':184}
        report['expected_source_grids']={name:len(values)==expected[name] for name,values in sources.items()}
        manifest=read(args.root/'executed-source-manifest.json')['files']
        report['executed_source_bindings']={path:digest(args.exported_source/path)==value for path,value in manifest.items()}
        timeline=read(args.root/'source-timeline.json')
        report['fixed_adapter_snapshot_matches']=digest(args.root/timeline['snapshot'])==timeline['fixed_cohort_adapter_sha256']
        report['original_reproduction_comparison']=compare(rows(args.prior/'result.json'),sources['unchanged_original'])
        report['stops']={path.name:read(path) for path in args.root.glob('*.stop.json')}
        report['sampled_N']=sorted({r['N'] for values in sources.values() for r in values})
        report['retained_trajectory_audit']='UNAVAILABLE: archive preserves selected rows, not retained masks. First-valid ordinals and sample-count claims cannot be independently replayed from this archive.'
        report['not_comparable_as_same_wave']='Different adaptive schedule, auxiliary formulations, gates and N32 cohort completeness; do not pool with next-wave.'
        report['source_manifest_qualified']='executed-source-manifest + preserved fixed adapter snapshot; final docs may differ at integration commit'
    save(args.out,report)
    print(json.dumps({'mode':args.mode,'rows':report['selected_arithmetic']['rows_audited'],'selected_checks':report['selected_arithmetic']['all_selected_checks_pass'],
        'exact_comparison':report.get('comparison',report.get('original_reproduction_comparison',{})).get('exact_non_timing_equality'),'output':str(args.out)}))
if __name__=='__main__':main()
