"""Build D-A teacher items from the frozen slm-router corpus + gold labels.
One item per unique node where any pool model is correct (objective gold exists).
Public/synthetic only: slm-router synthetic decision states.
Output: data/teacher/da_items.jsonl  (id, state, questions)
Questions: choice "pick fastest correct model from the pool", options = pool models."""
import argparse, copy, hashlib, json, pathlib
from audit_round import require_clean_state, require_permutation

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--root', type=pathlib.Path, required=True)
parser.add_argument('--output-dir', type=pathlib.Path, required=True)
args = parser.parse_args()
D = args.root
# Never overwrite historical labels, inputs, or freeze.json.
out = args.output_dir
out.mkdir(parents=True, exist_ok=False)
source_freeze = json.loads((D / 'data/freeze.json').read_text())
for name in ('da_states.json', 'da_gold.json'):
    if hashlib.sha256((D / 'data' / name).read_bytes()).hexdigest() != source_freeze['frozen'][name]:
        raise ValueError(f'Frozen source mismatch: {name}')
da = json.load(open(D / 'data/da_states.json'))
gold = json.load(open(D / 'data/da_gold.json'))

# da['corpus'] is a list (326 entries: 163 unique nodes x 2 pool prefixes).
# Index states by node_id; prefer an entry with any_correct=True.
by_node = {}
for st in da['corpus']:
    nid = st['node_id']
    if nid not in by_node or (st.get('any_correct') and not by_node[nid].get('any_correct')):
        by_node[nid] = st

SPLIT_MAP = {'train': 'train', 'heldout': 'test'}
items = []
for nid, g in gold.items():
    if not g.get('any_correct'):
        continue
    st = by_node[nid]
    criteria = {m: m for m in st['pool']}
    if len(criteria) < 2:
        continue
    items.append({
        'id': nid,
        'split': SPLIT_MAP.get(st.get('split'), st.get('split', 'unknown')),
        'task': g['task'],
        'state': {
            'features': st.get('features', {}),
            'device_profiles': st.get('device_profiles', {}),
            'decision_type': st.get('decision_type'),
            'node_id': nid,
            'pool': list(st['pool']),
            'pool_size': st.get('pool_size'),
        },
        'questions': {
            'route_pick': {
                'type': 'choice',
                'instructions': ('Given this slm-router decision state, choose the model from the pool '
                                 'that should route this decision. Return exactly one option.'),
                'criteria': criteria,
            }
        },
        'objective_gold': g['selected'],
    })

items.sort(key=lambda it: it['id'])
reversed_items = copy.deepcopy(items)
for it, rev in zip(items, reversed_items):
    require_clean_state(it)
    for question in rev['questions'].values():
        question['criteria'] = dict(reversed(list(question['criteria'].items())))
    require_permutation(it, rev)
manifest = {'status': 'sanitized inputs only, NOT relabelled or scored',
            'source_freeze_sha256': hashlib.sha256((D / 'data/freeze.json').read_bytes()).hexdigest(),
            'n': len(items), 'by_split': {s: sum(it['split'] == s for it in items) for s in ('train', 'test')},
            'files': {}}
for name, rows in [('da_items.jsonl', items), ('da_items_reversed.jsonl', reversed_items)]:
    p = out / name
    with p.open('x') as f:
        for it in rows:
            # sort_keys=True would silently undo the option-order test.
            f.write(json.dumps(it) + '\n')
    manifest['files'][name] = hashlib.sha256(p.read_bytes()).hexdigest()
(out / 'freeze.da-sanitized.json').write_text(json.dumps(manifest, indent=2) + '\n')
print(json.dumps(manifest, indent=2))
