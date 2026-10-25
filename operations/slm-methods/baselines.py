"""Training-only deterministic controls on the exact powered tool holdout."""
from collections import Counter
import json
from pathlib import Path
import sqlite3
import time

from z0int.cognition.slm_statistics import frozen_sample,expected_cost,expected_cost_interval
from z0int.cognition.learning_search import wilson_interval


def main():
    root=Path('operations/slm-methods')
    search=json.loads((root/'leaderboard-power.json').read_text())
    db=sqlite3.connect('file:/home/kvn/.z0int/loop/shadow-learning.sqlite?mode=ro',uri=True)
    db.row_factory=sqlite3.Row
    labels=[r for r in frozen_sample(db,search['manifest'],800) if r['family']=='omp.tool' and r['_split']=='holdout']
    cutoff=search['training']['cutoff_exclusive']
    counts=Counter({r['action']:r['n'] for r in db.execute("SELECT action,count(*) n FROM labels WHERE family='omp.tool' AND ts<? GROUP BY action",(cutoff,))})
    majority=min(counts,key=lambda tool:(-counts[tool],tool))
    results={name:[] for name in ('training_majority','narrowing_frequency_top1','previous_tool_repeats')}
    for row in labels:
        prior=list(db.execute("SELECT action FROM labels WHERE family='omp.tool' AND ts<? ORDER BY ts DESC LIMIT 64",(row['ts'],)))
        frequency=Counter(r['action'] for r in prior)
        history=json.loads(row['features'])
        for name in results:
            started=time.monotonic()
            if name=='training_majority':
                answer=majority
            elif name=='narrowing_frequency_top1':
                narrowed=sorted(frequency,key=lambda tool:(-frequency[tool],tool))[:3]
                answer=narrowed[0] if narrowed else None
            else:
                answer=history[-1] if isinstance(history,list) and history else None
            results[name].append({'id':row['id'],'session':row['session'],'correct':answer==row['label'],
                'abstain':answer is None,'wall_ms':1000*(time.monotonic()-started)})
    summaries=[]
    for name,rows in results.items():
        correct=sum(r['correct'] for r in rows)
        summaries.append({'method':name,'n':len(rows),'correct':correct,'accuracy':correct/len(rows),
            'wilson95':wilson_interval(correct,len(rows)),
            'abstain_rate':sum(r['abstain'] for r in rows)/len(rows),
            'wall_ms_per_decision':sum(r['wall_ms'] for r in rows)/len(rows),
            'expected_proxy_ms':expected_cost(rows),'expected_proxy_ci95_ms':expected_cost_interval(rows)})
    selected=search['leaderboard'][0]
    best=min(summaries,key=lambda row:row['expected_proxy_ms'])
    output={'status':'complete','manifest':search['manifest'],'evidence_class':'replayed_L1',
        'training_cutoff_exclusive':cutoff,'training_majority_tool':majority,
        'cost_proxy':{'error_ms':6034,'frontier_ms':7999,'causal_error_cost':False},
        'risk_eligibility':{'read':'shadow_only_until_output_quality_ABAB',
                            'mutating_reversible':'k=0_frontier','destructive':'k=0_frontier'},
        'baselines':summaries,'rows':results,
        'selected_slm_holdout':selected['metrics']['holdout'],
        'best_baseline':best['method'],
        'baseline_beats_slm_expected_cost':best['expected_proxy_ms']<selected['metrics']['holdout']['expected_proxy_ms']}
    (root/'baselines-power.json').write_text(json.dumps(output,indent=2))
    print(json.dumps({key:output[key] for key in ('baselines','best_baseline','baseline_beats_slm_expected_cost')},indent=2))


if __name__=='__main__':
    main()
