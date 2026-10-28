"""Frozen exact-label samples and session-cluster expected-cost intervals."""
from collections import defaultdict
import json
from pathlib import Path
import random


def frozen_sample(db, path, n=400):
    path=Path(path)
    if path.exists():
        manifest=json.loads(path.read_text())
        if manifest['n_per_family']!=n:
            raise ValueError('frozen sample size mismatch')
    else:
        rng=random.Random(20261025)
        training_cutoff=db.execute("SELECT max(first_ts) FROM (SELECT min(ts) first_ts FROM labels WHERE family='omp.tool' GROUP BY action)").fetchone()[0]
        if training_cutoff is None:
            raise ValueError('missing_observed_tool_vocabulary')
        manifest={'n_per_family':n,'stratification':'proportional exact action strata; disjoint development/holdout','families':{},'splits':{}}
        manifest['training_cutoff_inclusive']=training_cutoff
        for family,table in (('omp.tool','labels'),('omp.path_kind','family_jobs')):
            label='action' if table=='labels' else 'label'
            rows=list(db.execute(f'SELECT id,{label} AS label FROM {table} WHERE family=? AND ts>? '+('AND label IS NOT NULL ' if table=='family_jobs' else '')+'ORDER BY ts,id',(family,training_cutoff)))
            if len(rows)<n:
                raise ValueError('insufficient exact labels')
            buckets=defaultdict(list)
            for row in rows:
                buckets[row['label']].append(row['id'])
            quotas={key:min(len(values),max(1,int(n*len(values)/len(rows)))) for key,values in buckets.items()}
            while sum(quotas.values())<n:
                key=max((k for k in buckets if quotas[k]<len(buckets[k])),key=lambda k:(n*len(buckets[k])/len(rows)-quotas[k],k))
                quotas[key]+=1
            while sum(quotas.values())>n:
                key=max((k for k in buckets if quotas[k]>1),key=lambda k:(quotas[k]-n*len(buckets[k])/len(rows),k))
                quotas[key]-=1
            sampled={key:rng.sample(buckets[key],quotas[key]) for key in sorted(buckets)}
            dev_quota={key:len(values)//2 for key,values in sampled.items()}
            for key in sorted(sampled,key=lambda k:(-(len(sampled[k])%2),k)):
                if sum(dev_quota.values())>=n//2:break
                if dev_quota[key]<len(sampled[key]):
                    dev_quota[key]+=1
            ids=[]
            for key,values in sampled.items():
                for i,identity in enumerate(values):
                    ids.append(identity)
                    manifest['splits'][identity]='dev' if i<dev_quota[key] else 'holdout'
            rng.shuffle(ids)
            manifest['families'][family]=ids
        path.parent.mkdir(parents=True,exist_ok=True)
        with path.open('x') as stream:
            json.dump(manifest,stream,sort_keys=True,indent=2)
    result=[]
    for family,ids in manifest['families'].items():
        table='labels' if family=='omp.tool' else 'family_jobs'
        for identity in ids:
            row=db.execute(f'SELECT * FROM {table} WHERE id=?',(identity,)).fetchone()
            if row is None:
                raise ValueError('frozen exact label missing')
            result.append(dict(row,_split=manifest.get('splits',{}).get(identity,'all'),
                               **({'label':row['action']} if family=='omp.tool' else {})))
    return result


def expected_cost(rows):
    n=len(rows)
    covered=sum(not row['abstain'] for row in rows)
    correct=sum(row['correct'] for row in rows)
    wall=sum(row['wall_ms'] for row in rows)/n
    return wall + ((1-correct/covered)*6034+(1-covered/n)*7999 if covered else 7999)


def expected_cost_interval(rows, repeats=1000):
    groups=defaultdict(list)
    for row in rows:
        groups[row.get('session',row['id'])].append(row)
    groups=list(groups.values())
    rng=random.Random(20261025)
    estimates=[]
    for _ in range(repeats):
        draw=[row for group in rng.choices(groups,k=len(groups)) for row in group]
        estimates.append(expected_cost(draw))
    estimates.sort()
    return [estimates[int(.025*(repeats-1))],estimates[int(.975*(repeats-1))]]
