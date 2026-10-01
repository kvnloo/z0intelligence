"""Build the frozen item set for the tool-calling portfolio bench (see PREREG.md).

Private text (real user requests, rendered State Packets) is written only to
~/.z0int/research/tool_calling_portfolio/items_v0.jsonl. The committed
items_public_v0.json carries ids, suites, gold labels and sha256 digests only.

  python benchmarks/tool_calling_portfolio/build.py
"""
from __future__ import annotations

import hashlib
import json
import random
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from common import ITEMS_PRIVATE, PRIVATE, bfcl_tools  # noqa: E402

ROOT = HERE.parents[1]
Z0 = Path('~/.z0int').expanduser()
BFCL_COMMIT = '6ea57973c7a6097fd7c5915698c54c17c5b1b6c8'
BFCL_CATS = ('simple_python', 'multiple', 'parallel', 'parallel_multiple', 'irrelevance')
BFCL_N = 25
SEED = 20
PARENT = 'claude-code'

FUNCTION_MENU = {
    'coding_implementation': 'write or change code, fix bugs, make failing tests/builds pass',
    'code_review': 'review or audit existing code/config without changing it',
    'research': 'investigate: search the repo, issues, web or a host to find facts',
    'summarization': 'summarize status, progress or given text',
    'planning': 'decide priorities, next steps, strategy or a work plan',
    'long_context_reasoning': 'reason over very large inputs (many files, long logs, whole repos)',
    'tool_selection': 'choose which tool/command/agent should handle something',
    'cheap_bounded_worker': 'trivial bounded reply: one-liners, yes/no, echo, arithmetic, acknowledgements',
}

# Author gold (acceptable sets), keyed by sha256(request)[:12]. Frozen in the pre-registration commit,
# labelled before any candidate ran. Requests come from ~/.z0int/research/action_authority_v0 and
# action_authority_v1_dev turns.json (real Claude Code session turns); text is never committed.
ROUTE_GOLD = {
    '038478e42fb7': ['planning', 'research'],
    '0c95af72919f': ['research'],
    '16bf6a754366': ['cheap_bounded_worker', 'summarization'],
    '1e76fafb5f89': ['coding_implementation', 'planning'],
    '20fe6acedb16': ['planning', 'summarization'],
    '363622f4e67e': ['code_review', 'research'],
    '3a7f9365df1f': ['research'],
    '3b2761ccdefd': ['cheap_bounded_worker', 'planning', 'tool_selection'],
    '3e3535389492': ['planning', 'research'],
    '3ee6ff4aed93': ['cheap_bounded_worker'],
    '418c9b91dfae': ['planning'],
    '45a41927fbc8': ['coding_implementation'],
    '4758536a8dec': ['planning'],
    '485acf7ef1be': ['cheap_bounded_worker'],
    '4b9cbed9afe5': ['planning', 'research'],
    '4daa88a146ac': ['research'],
    '510a2e9eaf3d': ['cheap_bounded_worker'],
    '51452811bb6c': ['cheap_bounded_worker'],
    '5388b42c9f2d': ['cheap_bounded_worker'],
    '68c99181b2aa': ['research', 'planning', 'summarization'],
    '712f4281de7b': ['research', 'code_review'],
    '71afe901bf1d': ['cheap_bounded_worker'],
    '7991c992f873': ['summarization', 'planning'],
    '7be1d1df025f': ['cheap_bounded_worker', 'research', 'summarization'],
    '88208a756642': ['cheap_bounded_worker', 'planning'],
    '8908ba0741b3': ['research', 'code_review'],
    '8d74c837a979': ['research', 'cheap_bounded_worker'],
    '90b6cfe4cedb': ['coding_implementation'],
    '968f7429d6ca': ['cheap_bounded_worker', 'tool_selection'],
    '98d334d46e5a': ['planning'],
    '9a4f5c5c2ae8': ['coding_implementation'],
    '9a7dec2e7a17': ['research', 'code_review', 'summarization'],
    '9e39489ea856': ['research', 'planning', 'long_context_reasoning'],
    '9ee5d65d4f19': ['research'],
    'b2306f05fd3f': ['coding_implementation'],
    'c925f5222e41': ['coding_implementation', 'code_review'],
    'cc3136e1395a': ['code_review', 'summarization', 'long_context_reasoning'],
    'd0a750e28819': ['summarization'],
    'd8fc37cb1300': ['planning', 'coding_implementation', 'research'],
    'd91414e8670e': ['planning', 'long_context_reasoning'],
    'f18d347b9fbc': ['coding_implementation', 'code_review'],
    'f74b6f65b5fc': ['coding_implementation'],
    'f755373d666c': ['research', 'planning'],
}
# Turns with no task content; excluded before labelling.
ROUTE_EXCLUDE = {'219402692457', '929260ad9b9e', '86d815e1de60'}


def sha(s):
    return hashlib.sha256(s.encode()).hexdigest()


def mcp_tools():
    sys.path.insert(0, str(ROOT / 'src'))
    from z0int.intelligence_mcp import TOOLS  # the real MCP schemas, unchanged
    return [{'type': 'function', 'function': {'name': t['name'], 'description': t['description'],
                                              'parameters': t['inputSchema']}} for t in TOOLS]


def route_system(trace_id):
    menu = '\n'.join(f'- {k}: {v}' for k, v in FUNCTION_MENU.items())
    return (f'You are the parent coding agent "{PARENT}" in the z0 stack. Hand every user request to '
            'z0intelligence with exactly one tool call.\n'
            'Use delegate_worker only when the user explicitly names a provider and a model; otherwise use route_worker.\n'
            f'Always set task to the user\'s request verbatim, parent_agent to "{PARENT}" and trace_id to "{trace_id}".\n'
            'For route_worker, set function to the single best-fitting capability from this list:\n'
            f'{menu}\n'
            'Never set allow_remote to true unless the user explicitly authorizes remote providers. '
            'For delegate_worker, give a short reason.')


def z0_route_items():
    texts = {}
    for f in ('action_authority_v0', 'action_authority_v1_dev'):
        for turns in json.loads((Z0 / 'research' / f / 'turns.json').read_text()).values():
            for t in turns:
                texts.setdefault(t['text'], f)
    tools = mcp_tools()
    items = []
    for text in sorted(texts, key=sha):
        h = sha(text)[:12]
        if h in ROUTE_EXCLUDE:
            continue
        if h not in ROUTE_GOLD:
            raise SystemExit(f'unlabelled request {h}: label it before freezing')
        tid = f'tc-{h}'
        items.append({'id': f'z0route_{h}', 'suite': 'z0_route', 'category': 'route_worker', 'request': text,
                      'messages': [{'role': 'system', 'content': route_system(tid)}, {'role': 'user', 'content': text}],
                      'tools': tools, 'source': f'~/.z0int/research/{texts[text]}/turns.json',
                      'gold': {'tool': 'route_worker', 'functions': ROUTE_GOLD[h], 'trace_id': tid, 'parent_agent': PARENT}})
    # Explicit delegations recorded in ~/.z0int/receipts/decisions.jsonl (codex.delegated_text, untruncated subtasks).
    rows = [json.loads(line) for line in (Z0 / 'receipts/decisions.jsonl').read_text().splitlines() if line.strip()]
    seen = {}
    for r in rows:
        if r.get('capability_id') != 'codex.delegated_text':
            continue
        sub = r['extra'].get('subtask') or ''
        if sub.startswith('Classify') or len(sub) >= 120:  # 120 = receipt truncation; keep only complete strings
            continue
        seen.setdefault((sub, r['provider'], r['model']), r['trace_id'])
    for (sub, prov, model), _ in sorted(seen.items()):
        h = sha(f'{prov}|{model}|{sub}')[:12]
        tid = f'tc-{h}'
        request = f'Use provider "{prov}" with model "{model}" for this: {sub}'
        items.append({'id': f'z0deleg_{h}', 'suite': 'z0_route', 'category': 'delegate_worker', 'request': request,
                      'messages': [{'role': 'system', 'content': route_system(tid)}, {'role': 'user', 'content': request}],
                      'tools': tools, 'source': '~/.z0int/receipts/decisions.jsonl (codex.delegated_text)',
                      'gold': {'tool': 'delegate_worker', 'provider': prov, 'model': model, 'trace_id': tid,
                               'parent_agent': PARENT}})
    return items


def bfcl_items():
    rng = random.Random(SEED)
    src = HERE / 'data/bfcl/src'
    items = []
    for cat in BFCL_CATS:
        qs = [json.loads(line) for line in (src / f'BFCL_v4_{cat}.json').read_text().splitlines() if line.strip()]
        ans = {}
        if cat != 'irrelevance':
            ans = {a['id']: a['ground_truth'] for a in (json.loads(line) for line in
                   (src / f'possible_answer_BFCL_v4_{cat}.json').read_text().splitlines() if line.strip())}
        for q in sorted(rng.sample(qs, BFCL_N), key=lambda q: q['id']):
            tools, back = bfcl_tools(q['function'])
            items.append({'id': f"bfcl_{q['id']}", 'suite': 'bfcl', 'category': cat, 'messages': q['question'][0],
                          'tools': tools, 'name_back': back, 'functions': q['function'],
                          'ground_truth': ans.get(q['id'], [])})
    return items


ACTION_TOOLS = [
    {'type': 'function', 'function': {'name': n, 'description': d, 'parameters': {
        'type': 'object', 'properties': {'reason': {'type': 'string', 'description': 'one short sentence'}},
        'required': [], 'additionalProperties': False}}}
    for n, d in (('act', 'The supplied state is sufficient: answer directly from it.'),
                 ('observe_more', 'A required fact is missing or unavailable: say it is unknown or observe more before answering.'),
                 ('escalate', 'Sources conflict about the answer: surface the conflict instead of picking a winner.'))]
ACTION_GOLD = {'answer': 'act', 'abstain': 'observe_more', 'conflict': 'escalate'}


def action_items():
    sys.path.insert(0, str(ROOT / 'src'))
    sys.path.insert(0, str(ROOT / 'benchmarks/state_packet'))
    import action_selector as A  # pinned cohort + State Packet rendering, unchanged
    items = []
    for q, ctx in A.cohort():
        items.append({'id': f"action_{q['id']}", 'suite': 'action_selector', 'category': q['key']['action'],
                      'messages': [{'role': 'system', 'content': 'Choose the correct next move by calling exactly one tool.'},
                                   {'role': 'user', 'content': f"{ctx}\n\nUser question: {q['prompt']}\n\n{A.QUESTION}"}],
                      'tools': ACTION_TOOLS, 'gold': ACTION_GOLD[q['key']['action']]})
    return items


def main():
    items = z0_route_items() + bfcl_items() + action_items()
    PRIVATE.mkdir(parents=True, exist_ok=True)
    blob = ''.join(json.dumps(i, sort_keys=True) + '\n' for i in items)
    ITEMS_PRIVATE.write_text(blob)
    public = {
        'schema': 'z0int.tool_calling_portfolio.items.v0',
        'items_sha256': sha(blob),
        'bfcl_source': {'repo': 'https://github.com/ShishirPatil/gorilla', 'commit': BFCL_COMMIT,
                        'path': 'berkeley-function-call-leaderboard/bfcl_eval/data', 'license': 'Apache-2.0',
                        'categories': list(BFCL_CATS), 'per_category': BFCL_N, 'seed': SEED},
        'counts': {},
        'items': [],
    }
    for i in items:
        key = f"{i['suite']}/{i['category']}"
        public['counts'][key] = public['counts'].get(key, 0) + 1
        row = {'id': i['id'], 'suite': i['suite'], 'category': i['category'],
               'prompt_sha256': sha(json.dumps(i['messages'], sort_keys=True))}
        if i['suite'] == 'z0_route':
            row['gold'] = {k: v for k, v in i['gold'].items() if k != 'trace_id'}
            row['source'] = i['source']
        elif i['suite'] == 'action_selector':
            row['gold'] = i['gold']
        public['items'].append(row)
    (HERE / 'items_public_v0.json').write_text(json.dumps(public, indent=1) + '\n')
    print(json.dumps(public['counts'], indent=1), public['items_sha256'])


if __name__ == '__main__':
    main()
