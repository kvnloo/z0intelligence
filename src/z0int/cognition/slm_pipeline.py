"""Training-only co-occurrence categories and bounded shadow pipeline variants."""
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
import math
import json

from .slm_methods import predict_method
from .shadow_binary import score_choices


def cluster_tools(examples, count):
    """Balanced greedy graph partition using actual adjacent tool co-occurrence.

    No semantic hand labels, target labels, or future examples enter clustering.
    """
    frequency = Counter(ex['label'] for ex in examples)
    edges = Counter()
    for ex in examples:
        history = ex.get('features', [])
        if isinstance(history, list) and history:
            edges[tuple(sorted((history[-1], ex['label'])))] += 1
    tools = sorted(frequency, key=lambda v: (-frequency[v], v))
    groups = [[] for _ in range(min(count, len(tools)))]
    if not groups:
        return []
    for tool in tools:
        limit = (len(tools)+len(groups)-1)//len(groups)
        eligible = [i for i,g in enumerate(groups) if len(g)<limit]
        chosen = min(eligible, key=lambda i: (-sum(edges[tuple(sorted((tool,other)))] for other in groups[i]),len(groups[i]),i))
        groups[chosen].append(tool)
    return groups


def pipeline(prompt, groups, chat_first, chat_final, *, first='binary', final='bt', margin=.25, seed=42):
    """Category stage uses independent binary questions, or batched BT duels."""
    context = json.loads(prompt)
    context.pop('choices',None)
    descriptions = ['category '+str(i)+': '+', '.join(group) for i,group in enumerate(groups)]
    if not descriptions:
        return {'selected_action':None,'abstained':True,'category':None}
    if first == 'bt':
        first_row = predict_method(json.dumps(context), descriptions, chat_first, method='bt',seed=seed)
        category = first_row.get('selected_action')
        strengths = sorted(first_row.get('bt_strengths', []), reverse=True)
        if len(strengths) > 1 and math.log(strengths[0]/strengths[1]) < margin:
            category = None
    else:
        def binary(description):
            result = score_choices(context,[{'id':description,'description':description}],chat_first,min_margin=0)
            scores=result.get('candidate_scores',[])
            return scores[0]['log_odds'] if scores else None
        with ThreadPoolExecutor(max_workers=len(descriptions)) as pool:
            scores=list(pool.map(binary,descriptions))
        if any(score is None for score in scores):
            return {'selected_action':None,'abstained':True,'category':None}
        ranked=sorted(range(len(scores)),key=lambda i:-scores[i])
        category=descriptions[ranked[0]] if len(ranked)==1 or scores[ranked[0]]-scores[ranked[1]]>=margin else None
    if category is None:
        return {'selected_action':None,'abstained':True,'category':None}
    index=descriptions.index(category)
    members=groups[index]
    if final=='binary':
        # Independent one-option questions permit full membership without a k>N prompt.
        def member(value):
            row=score_choices(context,[{'id':value,'description':value}],chat_final,min_margin=0)
            scores=row.get('candidate_scores',[])
            return scores[0]['log_odds'] if scores else None
        with ThreadPoolExecutor(max_workers=max(1,len(members))) as pool:
            scores=list(pool.map(member,members))
        if any(v is None for v in scores):
            action=None
        else:
            ranked=sorted(range(len(scores)),key=lambda i:-scores[i])
            action=members[ranked[0]] if len(ranked)==1 or scores[ranked[0]]-scores[ranked[1]]>=margin else None
        row={'selected_action':action,'abstained':action is None}
    else:
        row=predict_method(json.dumps(context),members,chat_final,method='bt' if final=='bt' else 'tournament3',seed=seed+1)
        strengths = sorted(row.get('bt_strengths', []), reverse=True)
        if len(strengths) > 1 and math.log(strengths[0]/strengths[1]) < margin:
            row.update(selected_action=None, abstained=True)
    row['category']=index
    return row
