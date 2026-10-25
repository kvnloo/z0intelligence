"""Versioned candidate publication/loading; never writes promotion or policy."""
import json
from pathlib import Path

from .slm_scoped import scope_digest


def candidate_catalogue(home, defaults):
    catalogue=list(defaults)
    folder=Path(home)/'loop'/'slm-methods-candidates'
    for path in sorted(folder.glob('*.json')):
        try:
            row=json.loads(path.read_text())
            config=row['config']
            digest=scope_digest(config)
            if row.get('id')!='methods-pipeline-'+digest or path.stem!=digest:
                continue
            if row.get('families')!=['omp.tool'] or config.get('decision_method')!='slm_pipeline_v1':
                continue
            catalogue.append(row)
        except (OSError,ValueError,KeyError,TypeError):
            continue
    return tuple(catalogue)


def publish_candidate(leaderboard_path, home, *, incumbent_path):
    """Freeze the dev winner only after an independent holdout cost gate."""
    report=json.loads(Path(leaderboard_path).read_text())
    if report.get('status')!='complete':
        raise ValueError('incomplete_method_search')
    winner=min(report['leaderboard'],key=lambda row:row['metrics']['dev']['expected_proxy_ms'])
    variant=winner['variant']
    incumbent=json.loads(Path(incumbent_path).read_text())
    if incumbent.get('status')!='complete' or incumbent.get('manifest')!=report.get('manifest'):
        raise ValueError('missing_matched_complete_incumbent')
    baseline=next((row for row in incumbent['summary'] if row['family']=='omp.tool'
                   and row['method']=='goal_binary' and row['split']=='holdout'),None)
    holdout=winner['metrics']['holdout']
    if baseline is None or holdout['n']<400 or baseline['n']!=holdout['n']:
        raise ValueError('insufficient_matched_holdout')
    expected_ids={row['id'] for row in incumbent['rows'] if row['family']=='omp.tool'
                  and row['method']=='goal_binary' and row['split']=='holdout'}
    actual_ids={row['id'] for row in winner['rows'] if row['split']=='holdout'}
    if actual_ids!=expected_ids or len(actual_ids)!=holdout['n']:
        raise ValueError('mismatched_holdout_labels')
    if holdout['expected_proxy_ci95_ms'][1]>=baseline['expected_proxy_ci95_ms'][0]:
        raise ValueError('holdout_cost_intervals_overlap_or_lose')
    if variant['first_model']!=variant['final_model']:
        raise ValueError('mixed_stage_model_requires_distinct_bound_backends')
    training=report.get('training',{})
    categories=training.get('frozen_categories',{}).get(str(variant['categories']))
    if not categories or not training.get('digest') or not training.get('rows'):
        raise ValueError('missing_evaluated_frozen_training_scope')
    config={'history_width':1,'candidate_k':3,'max_options':3,'prompt_bytes':3000,
            'decision_method':'slm_pipeline_v1','first':variant['first'],'final':variant['final'],
            'margin':variant['margin'],'first_model':variant['first_model'],'final_model':variant['final_model'],
            'frozen_categories':categories,
            'training_digest':training['digest'],'training_rows':training['rows']}
    digest=scope_digest(config)
    candidate={'id':'methods-pipeline-'+digest,'kind':'bounded_projection','families':['omp.tool'],
               'config':config,'selection':'minimum_dev_expected_recovery_proxy_cost',
               'evidence_class':'replayed_L1','holdout':winner['metrics']['holdout']}
    folder=Path(home)/'loop'/'slm-methods-candidates'
    folder.mkdir(parents=True,exist_ok=True)
    target=folder/(digest+'.json')
    # Exclusive creation preserves immutable scope when parallel ticks finish.
    try:
        with target.open('x') as stream:
            json.dump(candidate,stream,sort_keys=True,indent=2)
    except FileExistsError:
        if json.loads(target.read_text())!=candidate:
            raise ValueError('candidate_scope_collision')
    return candidate
