#!/usr/bin/env python3
"""Verify a staged Z14 review packet. No services, inference, or UI input."""
import copy
import hashlib
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(sys.argv[1]).resolve()
sys.path.insert(0, str(ROOT / 'owner/src'))
from z0int.intent_service import validate_draft


def load(name):
    return json.loads((ROOT / 'artifacts' / name).read_text())


def check(condition, message):
    if not condition:
        raise AssertionError(message)
    print('PASS ' + message, flush=True)


checks = []
packet = load('review-packet.json')
board = load('intent-board.json')
draft = load('intent-draft.json')
contract = load('intent-contract.json')
records = load('memory-catalog.json')
for row in json.loads((ROOT / 'stage-manifest.json').read_text()):
    check(hashlib.sha256((ROOT / row['path']).read_bytes()).hexdigest() == row['sha256'], 'staged bytes ' + row['path'])
catalog = {s['id']: s for s in packet['sources']}
for source in catalog.values():
    check(hashlib.sha256(source['excerpt'].encode()).hexdigest() == source['excerpt_sha256'], 'frozen excerpt ' + source['id'])
for quote in packet['quotes']:
    check(quote['text'] in catalog[quote['source']]['excerpt'], 'exact owner quote ' + quote['id'])
check(packet['execution_enabled'] is False and packet['approval'] == 'pending', 'review packet is not execution authority')
check(len(packet['mappings']) == 4 and {m['id'] for m in packet['mappings']} == {'Z12-ONTOLOGY','Z13-NETWORK','MEMORY','Z14-CARD'}, 'four required concept relations')
for mapping in packet['mappings']:
    check(all(mapping[k] for k in ['product','implementation','evaluation','memory','sources']), 'complete relation ' + mapping['id'])
    check(all(s in catalog for s in mapping['sources']), 'cited relation ' + mapping['id'])
validate_draft(draft, records)
check(True, 'existing IntentService validate_draft accepts catalog and prerequisites')
for assumption in packet['assumptions']:
    check(assumption['kind'] in {'unknown','claimed'}, 'no unsupported inference upgraded ' + assumption['id'])
    gate = next(g for g in board['contract']['preconditions'] if assumption['id'] + ':' in g['label'])
    check(assumption['kind'].upper() in gate['label'] and gate['state'] == 'unknown' and assumption['observe'] in gate['label'], 'visible unknown prerequisite ' + assumption['id'])
    prerequisite = next(p for p in draft['tasks'][0]['preconditions'] if p['claim'] == assumption['claim'])
    check(prerequisite['verifier'] == {'type':'human_review','target':assumption['observation_target']}, 'exact observation target ' + assumption['id'])
for name, mutate in [
    ('missing observation', lambda d: d['tasks'][0].update(preconditions=[])),
    ('invented citation', lambda d: d['assumptions'][0]['citations'][0].update(id='invented')),
    ('unknown upgraded to observed', lambda d: d['assumptions'][0]['citations'][0].update(evidence_kind='observed')),
]:
    bad = copy.deepcopy(draft)
    mutate(bad)
    try:
        validate_draft(bad, records)
    except ValueError:
        print('PASS rejects ' + name, flush=True)
    else:
        raise AssertionError('Accepted ' + name)
bindings = contract['plan']['bindings']
check(bindings['approval']['status'] == 'pending', 'compiled canonical contract remains pending')
task_nodes = [n for n in contract['intentGraph']['nodes'] if n['kind'] == 'task']
check(len(task_nodes) == 1, 'bounded excerpt has exactly one review task')
task = bindings[task_nodes[0]['id']]
check(task['target'] == draft['tasks'][0]['target'] == bindings[task['acceptance']]['target'], 'canonical acceptance target byte equality')
check(board['contract']['contractHash'] == bindings['approval']['contractHash'] and board['intent']['sourceHash'] == contract['provenance']['sourceHash'], 'native board binds canonical source and contract hashes')
check(board['contract']['approval'] == 'pending' and board['contract']['acceptance']['receipt'] == 'missing', 'native presentation cannot imply approval or acceptance')
subprocess.run(['node', str(ROOT / 'verify-native.mjs'), str(ROOT)], check=True)
print('PASS Z14 packet checks. Native live pixels and owner acceptance NOT exercised.', flush=True)
