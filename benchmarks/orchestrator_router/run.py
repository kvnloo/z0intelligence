"""Shadow router eval: Nemotron-Orchestrator-8B vs deterministic z0 routing (see PREREG.md).

Policy filters -> router proposes -> gate disposes. Nothing here touches a live route.

  .venv/bin/python benchmarks/orchestrator_router/run.py            # all arms (resumable)
  .venv/bin/python benchmarks/orchestrator_router/run.py --score    # recompute results only
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import statistics
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path

from z0int import worker_routing as wr

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
Z0 = Path('~/.z0int').expanduser()
BENCH = Z0 / 'research/factory/groot-bench'
ACTION_RUN = Z0 / 'research/claude-code-overnight/runs/action-selector-pinned.jsonl'
RECEIPTS = Z0 / 'receipts/decisions.jsonl'
FARM = 'http://100.113.138.100:11530'
RAW = HERE / 'raw_v0.jsonl'
OUT = HERE / 'results_v0.json'

ORCH = 'nemotron-orchestrator-8b-q4km'
QWEN = 'qwen3-8b-q4km'
LLM_ARMS = {  # execution order is fixed by PREREG
    'orch_nothink': (ORCH, False),
    'orch_think': (ORCH, True),
    'qwen3_8b_nothink': (QWEN, False),
    'qwen3_8b_think': (QWEN, True),
}

# S1: outcome file + non-outcome description per legal (provider, model)
S1_OUTCOMES = {
    ('local', 'qwen3-0.6b-q8'): ('mbp-radeon-qwen3-0.6b-q8.jsonl', 'Qwen3-0.6B Q8_0 on this laptop (Radeon R9 M370X, Vulkan)', 0.6),
    ('groot', 'qwen3-0.6b-q8'): ('groot_qwen3_06b_q8.jsonl', 'Qwen3-0.6B Q8_0 on the groot RTX 3080 Ti (CUDA)', 0.6),
    ('groot', 'qwen3-1.7b-q4km'): ('groot_qwen3_17b_q4.jsonl', 'Qwen3-1.7B Q4_K_M on the groot RTX 3080 Ti (CUDA)', 1.7),
    ('groot', 'qwen3-4b-q4km'): ('groot_qwen3_4b_q4.jsonl', 'Qwen3-4B Q4_K_M on the groot RTX 3080 Ti (CUDA)', 4.0),
    ('groot', 'qwen3-8b-q4km'): ('groot_qwen3_8b_q4.jsonl', 'Qwen3-8B Q4_K_M on the groot RTX 3080 Ti (CUDA)', 8.0),
}
S2_DESC = {
    'rule_always_act': 'deterministic rule: always returns ACT, never anything else',
    'julia_1': 'Julia-1: small learned decision model for ACT / OBSERVE_MORE / ESCALATE choices',
    'laya_421m': 'Laya 421M: small learned decision model for ACT / OBSERVE_MORE / ESCALATE choices',
}
PREFERENCE = 'Preference: use the lowest-latency model that will still answer correctly.'


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def jl(path):
    return [json.loads(l) for l in Path(path).read_text().splitlines() if l.strip()]


# ---------------------------------------------------------------- policy filter

def legal_local_routes(policy, providers):
    """Policy filter for the `local` category: local cohort, cap > 0, validated $0 route with good evidence."""
    out = []
    for name in ['local', *policy.get('host_local_providers', [])]:
        cfg = providers.get(name)
        if not cfg or cfg.get('cohort') != 'local':
            continue
        if policy.get('provider_caps', {}).get(name, 0) in (None, 0):
            continue
        for m in cfg.get('models', []):
            if wr.free_route(policy, name, m['id']):
                out.append((name, m['id']))
    return out


def gold_set(cands, passes, cost):
    passing = [c for c in cands if passes[c]]
    if not passing:
        return None
    best = min(cost[c] for c in passing)
    return sorted(c for c in passing if cost[c] <= best * 1.05 + 1e-9)


# ---------------------------------------------------------------- sets

def build_s1(policy, providers):
    legal = sorted(legal_local_routes(policy, providers), key=list(S1_OUTCOMES).index)  # PREREG alias order
    missing = [c for c in legal if c not in S1_OUTCOMES]
    assert not missing, f'legal route without outcomes: {missing}'
    outcomes = {c: {r['id']: r for r in jl(BENCH / S1_OUTCOMES[c][0])} for c in legal}
    cost = {c: statistics.median(r['ms'] for r in outcomes[c].values()) for c in legal}
    rows = [r for r in jl(ROOT / 'benchmarks/data/authored144.jsonl')
            if r['family'] == 'evidence_interpretation' and r['split'] == 'test']
    items = []
    for r in rows:
        opts = '\n'.join(f"- {o['id']}: {o['description']}" for o in r['options'])
        task = f"local-only: {r['question']}\n\nEvidence: {r['state']}\n\nAnswer with exactly one option id:\n{opts}"
        plan = wr.plan_route(task, policy, providers, available_providers=set(providers))
        det = (plan['candidates'][0]['provider'], plan['candidates'][0]['model'])
        passes = {c: outcomes[c][r['id']]['pred_id'] == outcomes[c][r['id']]['gold_id'] for c in legal}
        items.append({'set': 'S1', 'id': r['id'], 'task': task, 'cands': legal, 'cost': cost, 'passes': passes,
                      'gold': gold_set(legal, passes, cost), 'det': det,
                      'plan_candidates': [(c['provider'], c['model']) for c in plan['candidates']]})
    desc = {c: S1_OUTCOMES[c][1] for c in legal}
    size = {c: S1_OUTCOMES[c][2] for c in legal}
    return items, desc, size, {f'{p}/{m}': S1_OUTCOMES[(p, m)][0] for p, m in legal}


def build_s2():
    from z0int.state_packet import build_state_packet, render_additional_context
    sp = ROOT / 'benchmarks/state_packet'
    pinned = Z0 / 'research/claude-code-overnight/pinned'
    gold_map = {'answer': 'ACT', 'abstain': 'OBSERVE_MORE', 'conflict': 'ESCALATE'}
    run = jl(ACTION_RUN)
    avail = [b for b in S2_DESC if any(r['backend'] == b and r.get('pred') for r in run)]
    cost = {}
    for b in avail:
        ss = [r['s'] for r in run if r['backend'] == b and r.get('s') is not None]
        cost[b] = statistics.median(ss) if ss else 0.0
    res = {(r['backend'], r['id']): r for r in run}
    qs = json.loads((sp / 'questions_pinned.json').read_text())['questions']
    packets, items = {}, []
    for q in qs:
        if q['repo'] not in packets:
            pkt = build_state_packet(pinned / q['repo'], projects_root=pinned / 'projects', adapters=('git', 'docs', 'claude_code'))
            packets[q['repo']] = render_additional_context(pkt, max_tokens=1500)
        gold_action = gold_map[q['key']['action']]
        task = (f"{packets[q['repo']]}\n\nUser question: {q['prompt']}\n\n"
                'Decide which next move is correct: ACT (the supplied state is sufficient: answer directly), '
                'OBSERVE_MORE (a required fact is missing or unavailable), or ESCALATE (sources conflict about the answer).')
        passes = {b: res[(b, q['id'])].get('pred') == gold_action for b in avail}
        items.append({'set': 'S2', 'id': q['id'], 'task': task, 'cands': avail, 'cost': cost, 'passes': passes,
                      'gold': gold_set(avail, passes, cost), 'det': 'rule_always_act', 'gold_action': gold_action})
    return items, dict(S2_DESC), cost


def build_s3(policy, providers):
    subs = []
    for r in jl(RECEIPTS):
        s = (r.get('extra') or {}).get('subtask')
        if r.get('capability_id') == 'codex.delegated_text' and s and s not in subs:
            subs.append(s)
    tasks = [t for s in subs for t in (s, 'local-only: ' + s)] + ['Extract JSON', 'write python code', 'local-only: summarize this']
    items = []
    for i, t in enumerate(tasks):
        plan = wr.plan_route(t, policy, providers, available_providers=set(providers))
        cands = [(c['provider'], c['model']) for c in plan['candidates']]
        items.append({'set': 'S3', 'id': f's3-{i:02d}', 'task': t, 'cands': cands, 'gold': None,
                      'det': cands[0] if cands else None, 'category': plan['category']})
    return items


def s3_desc(c):
    p, m = c
    if (p, m) in S1_OUTCOMES:
        return S1_OUTCOMES[(p, m)][1]
    return f'{m} via the {p} API (free tier, remote)'


# ---------------------------------------------------------------- router prompt / gate

def key(c):
    return c if isinstance(c, str) else f'{c[0]}/{c[1]}'


def tools_for(cands, desc, cost_ms):
    alias = {f'answer-{i + 1}': c for i, c in enumerate(cands)}
    lines = ' '.join(f'{a} is {desc[c]}.' for a, c in alias.items())
    table = '\n'.join(f"{a} | $0 | $0 | {cost_ms[c] / 1000:.2f}s" if cost_ms[c] >= 1000 else f"{a} | $0 | $0 | {cost_ms[c]:.0f}ms"
                      for a, c in alias.items())
    d = (f"The model used to answer. Choices: {list(alias)}. {lines} The table below shows the pricing and latency of each model:\n"
         f"Model | price per million input tokens | price per million output tokens | average latency\n{table}")
    tool = {'type': 'function', 'function': {'name': 'answer', 'description': 'give the final answer.',
            'parameters': {'properties': {'model': {'description': d, 'type': 'string'}},
                           'required': ['model'], 'title': 'parameters', 'type': 'object'}}}
    return [tool], alias


def messages_for(task):
    return [{'role': 'system', 'content': 'You are good at using tools.'},
            {'role': 'user', 'content': f"Problem: {task}\n\n{PREFERENCE}\n\nChoose an appropriate tool.'"}]


def call(model, messages, tools, think, timeout=600):
    body = {'model': model, 'messages': messages, 'tools': tools, 'temperature': 0, 'seed': 0, 'max_tokens': 2048,
            'chat_template_kwargs': {'enable_thinking': think}}
    req = urllib.request.Request(f'{FARM}/v1/chat/completions', data=json.dumps(body).encode(),
                                 headers={'Content-Type': 'application/json'})
    t = time.perf_counter()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = json.loads(resp.read())
        err = None
    except (urllib.error.URLError, OSError, ValueError) as exc:
        raw, err = None, f'{type(exc).__name__}: {exc}'[:300]
    return raw, err, (time.perf_counter() - t) * 1000


def gate(raw, err, alias):
    """Returns (proposal_alias_or_None, illegal_reason_or_None)."""
    if err or not raw:
        return None, 'transport_error'
    msg = (raw.get('choices') or [{}])[0].get('message') or {}
    tcs = msg.get('tool_calls') or []
    if not tcs:
        return None, 'no_tool_call'
    fn = tcs[0].get('function') or {}
    if fn.get('name') != 'answer':
        return None, f"wrong_tool:{fn.get('name')}"
    try:
        args = json.loads(fn.get('arguments') or '')
    except ValueError:
        return None, 'unparsable_arguments'
    m = args.get('model') if isinstance(args, dict) else None
    if m not in alias:
        return None, f'unknown_alias:{str(m)[:40]}'
    return m, None


# ---------------------------------------------------------------- groot probes

def vram_mib():
    try:
        out = subprocess.run(['ssh', '0', 'nvidia-smi --query-gpu=memory.used --format=csv,noheader,nounits'],
                             capture_output=True, text=True, timeout=30).stdout.strip()
        return int(out.splitlines()[0])
    except Exception:
        return None


def unload(model):
    req = urllib.request.Request(f'{FARM}/models/unload', data=json.dumps({'model': model}).encode(),
                                 headers={'Content-Type': 'application/json'})
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            return resp.status
    except (urllib.error.URLError, OSError) as exc:
        return f'{type(exc).__name__}'


# ---------------------------------------------------------------- run

def run_llm_arms(items, descs):
    done = {(r['arm'], r['set'], r['id']) for r in jl(RAW)} if RAW.exists() else set()
    meta_path = HERE / 'vram_v0.json'
    meta = json.loads(meta_path.read_text()) if meta_path.exists() else {}
    with RAW.open('a') as fh:
        for arm, (model, think) in LLM_ARMS.items():
            todo = [it for it in items if (arm, it['set'], it['id']) not in done]
            if not todo:
                continue
            if model not in meta:  # VRAM delta measured once per model, from an unloaded state
                for m in (ORCH, QWEN):
                    unload(m)
                time.sleep(5)
                before = vram_mib()
                meta[model] = {'before_mib': before}
            first = True
            for it in todo:
                tools, alias = tools_for(it['cands'], descs[it['set']], it['cost_ms'])
                raw, err, ms = call(model, messages_for(it['task']), tools, think)
                prop, illegal = gate(raw, err, alias)
                msg = ((raw or {}).get('choices') or [{}])[0].get('message') or {}
                row = {'arm': arm, 'model': model, 'think': think, 'set': it['set'], 'id': it['id'],
                       'proposal_alias': prop, 'proposal': key(alias[prop]) if prop else None, 'illegal': illegal,
                       'wall_ms': ms, 'cold': first, 'error': err, 'tool_calls': msg.get('tool_calls'),
                       'content': (msg.get('content') or '')[:500], 'reasoning': (msg.get('reasoning_content') or '')[:1500],
                       'usage': (raw or {}).get('usage'), 'timings': (raw or {}).get('timings')}
                fh.write(json.dumps(row) + '\n')
                fh.flush()
                if first and 'after_mib' not in meta[model]:
                    meta[model]['after_mib'] = vram_mib()
                    meta_path.write_text(json.dumps(meta, indent=1))
                first = False
                print(arm, it['set'], it['id'], prop, illegal, round(ms), flush=True)
    return meta


def mcnemar(a, b):
    """Exact two-sided McNemar on paired booleans."""
    n01 = sum(1 for x, y in zip(a, b) if x and not y)
    n10 = sum(1 for x, y in zip(a, b) if y and not x)
    n = n01 + n10
    if n == 0:
        return {'a_only': 0, 'b_only': 0, 'p': 1.0}
    k = min(n01, n10)
    p = min(1.0, 2 * sum(math.comb(n, i) for i in range(k + 1)) / 2 ** n)
    return {'a_only': n01, 'b_only': n10, 'p': p}


def pct(v, q):
    v = sorted(v)
    if not v:
        return None
    i = (len(v) - 1) * q
    lo, hi = math.floor(i), math.ceil(i)
    return v[lo] + (v[hi] - v[lo]) * (i - lo)


def score(items, sizes, vram, inputs):
    raw = {(r['arm'], r['set'], r['id']): r for r in jl(RAW)} if RAW.exists() else {}
    by = {(it['set'], it['id']): it for it in items}
    labeled = [it for it in items if it['gold']]

    def choice(arm, it):
        if arm == 'deterministic':
            return key(it['det']), None
        if arm == 'cheapest':
            return key(min(it['cands'], key=lambda c: it['cost_ms'][c])), None
        if arm == 'strongest':
            if it['set'] != 'S1':
                return None, None
            return key(max(it['cands'], key=lambda c: (sizes[c], -it['cost_ms'][c]))), None
        if arm == 'oracle':
            return (key(it['gold'][0]) if it['gold'] else None), None
        if arm == 'majority_loo':
            counts = {}
            for o in labeled:
                if o['set'] == it['set'] and o['id'] != it['id']:
                    for g in o['gold']:
                        counts[key(g)] = counts.get(key(g), 0) + 1
            kc = {key(c): c for c in it['cands']}
            best = max(kc, key=lambda k: (counts.get(k, 0), -it['cost_ms'][kc[k]]))
            return best, None
        r = raw.get((arm, it['set'], it['id']))
        if r is None:
            return None, 'missing'
        return (r['proposal'] if r['proposal'] else key(it['det'])), r['illegal']  # gate: illegal -> deterministic

    arms = ['deterministic', *LLM_ARMS, 'cheapest', 'strongest', 'majority_loo', 'oracle']
    results, per_item = {}, []
    for arm in arms:
        res = {}
        for scope in ('S1', 'S2', 'pooled_labeled', 'S3'):
            its = [it for it in items if (it['set'] == scope if scope in ('S1', 'S2', 'S3') else it['gold'])]
            if scope != 'S3':
                its = [it for it in its if it['gold']]
            acc, passed, regret, illegal, gated_illegal, agree, n = [], [], [], 0, 0, [], 0
            for it in its:
                ch, ill = choice(arm, it)
                if ch is None:
                    continue
                n += 1
                illegal += bool(ill and ill != 'missing')
                cands = {key(c): c for c in it['cands']}
                gated_illegal += ch not in cands
                agree.append(ch == key(it['det']))
                if it['gold']:
                    c = cands[ch]
                    mx = max(it['cost_ms'].values()) or 1.0
                    norm = {k: it['cost_ms'][k] / mx for k in it['cands']}
                    g = it['gold'][0]
                    ok = it['passes'][c]
                    acc.append(c in it['gold'])
                    passed.append(ok)
                    regret.append(norm[c] - norm[g] if ok else norm[c] + 1 - norm[g])
            if n == 0:
                continue
            res[scope] = {'n': n, 'choice_accuracy': sum(acc) / len(acc) if acc else None, 'correct': sum(acc),
                          'pass_rate': sum(passed) / len(passed) if passed else None,
                          'mean_regret': sum(regret) / len(regret) if regret else None,
                          'raw_illegal': illegal, 'raw_illegal_rate': illegal / n, 'post_gate_illegal': gated_illegal,
                          'agree_with_deterministic': sum(agree) / len(agree)}
        if arm in LLM_ARMS:
            rows = [r for r in raw.values() if r['arm'] == arm]
            warm = [r['wall_ms'] for r in rows if not r['cold'] and not r['error']]
            cold = [r['wall_ms'] for r in rows if r['cold']]
            dec = [r['timings'].get('predicted_per_second') for r in rows if r.get('timings')]
            toks = [(r.get('usage') or {}).get('completion_tokens') for r in rows if r.get('usage')]
            res['latency'] = {'n_warm': len(warm), 'p50_ms': pct(warm, .5), 'p95_ms': pct(warm, .95), 'cold_ms': cold,
                              'median_completion_tokens': statistics.median(toks) if toks else None,
                              'median_decode_tok_s': statistics.median([d for d in dec if d]) if dec else None}
            reasons = {}
            for r in rows:
                if r['illegal']:
                    reasons[r['illegal'].split(':')[0]] = reasons.get(r['illegal'].split(':')[0], 0) + 1
            res['illegal_reasons'] = reasons
            dist = {}
            for r in rows:
                if r['set'] != 'S3':
                    dist.setdefault(r['set'], {})
                    dist[r['set']][r['proposal']] = dist[r['set']].get(r['proposal'], 0) + 1
            res['proposal_distribution'] = dist
        results[arm] = res

    def correct_vec(arm):
        return [key(choice(arm, it)[0]) in [key(g) for g in it['gold']] if choice(arm, it)[0] else False for it in labeled]

    tests = {}
    if all(any(k[0] == a for k in raw) for a in ('orch_think', 'qwen3_8b_think')):
        o = correct_vec('orch_think')
        tests['orch_think_vs_deterministic'] = mcnemar(o, correct_vec('deterministic'))
        tests['orch_think_vs_qwen3_8b_think'] = mcnemar(o, correct_vec('qwen3_8b_think'))
    if tests:
        o, d, q = (results[a]['pooled_labeled'] for a in ('orch_think', 'deterministic', 'qwen3_8b_think'))
        a_ok = o['raw_illegal_rate'] <= 0.05 and o['post_gate_illegal'] == 0
        b_ok = tests['orch_think_vs_deterministic']['p'] < 0.05 and o['correct'] > d['correct'] or (
            o['mean_regret'] < d['mean_regret'] and o['pass_rate'] >= d['pass_rate'])
        c_ok = o['correct'] > q['correct'] or o['mean_regret'] < q['mean_regret']
        tests['decision_rule'] = {'a_legality': a_ok, 'b_beats_deterministic': b_ok, 'c_beats_generic_llm': c_ok,
                                  'continue_in_shadow': a_ok and b_ok and c_ok}
    for it in items:
        row = {'set': it['set'], 'id': it['id'], 'gold': [key(g) for g in it['gold']] if it['gold'] else None,
               'passes': {key(c): v for c, v in it['passes'].items()} if it.get('passes') else None,
               'deterministic': key(it['det']) if it['det'] else None}
        for arm in LLM_ARMS:
            r = raw.get((arm, it['set'], it['id']))
            if r:
                row[arm] = {'proposal': r['proposal'], 'illegal': r['illegal'], 'wall_ms': round(r['wall_ms'], 1)}
        per_item.append(row)
    labeled_counts = {s: {'items': sum(it['set'] == s for it in items), 'labeled': sum(it['set'] == s and bool(it['gold']) for it in items)}
                      for s in ('S1', 'S2', 'S3')}
    return {'schema': 'z0int.orchestrator_router.results.v0', 'prereg': 'benchmarks/orchestrator_router/PREREG.md',
            'inputs': inputs, 'sets': labeled_counts, 'vram_mib': vram, 'arms': results, 'tests': tests, 'items': per_item}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--score', action='store_true', help='score existing raw rows only')
    ap.add_argument('--build-only', action='store_true')
    a = ap.parse_args()
    policy, providers = wr.configuration()
    s1, d1, sizes, s1_files = build_s1(policy, providers)
    s2, d2, _ = build_s2()
    s3 = build_s3(policy, providers)
    for it in s1 + s2 + s3:
        if it['set'] == 'S3':
            it['cost_ms'] = {c: (S1_OUTCOMES[c] and statistics.median(r['ms'] for r in jl(BENCH / S1_OUTCOMES[c][0])))
                             if c in S1_OUTCOMES else 1000.0 for c in it['cands']}
        elif it['set'] == 'S2':
            it['cost_ms'] = {c: it['cost'][c] * 1000 for c in it['cands']}
        else:
            it['cost_ms'] = dict(it['cost'])
    descs = {'S1': d1, 'S2': d2, 'S3': {c: s3_desc(c) for it in s3 for c in it['cands']}}
    items = s1 + s2 + s3
    inputs = {'authored144': sha(ROOT / 'benchmarks/data/authored144.jsonl'),
              'questions_pinned': sha(ROOT / 'benchmarks/state_packet/questions_pinned.json'),
              'worker_routing_policy': sha(wr.POLICY_PATH),
              'host_override': sha(Z0 / 'config/worker_routing.local.json'),
              'action_selector_run': sha(ACTION_RUN), 'receipts': sha(RECEIPTS),
              **{f's1_outcomes:{k}': sha(BENCH / f) for k, f in s1_files.items()},
              'gguf': {'repo': 'bartowski/nvidia_Orchestrator-8B-GGUF', 'revision': 'b4bbbc08d2b475fe529428e6f358c932881aa0b5',
                       'file': 'nvidia_Orchestrator-8B-Q4_K_M.gguf',
                       'sha256': '1cc7077e20b3339d1a46bc72e29959cdd4c7249ebbd73e6977e76f23625995c7'},
              's1_legal_candidates': [key(c) for c in s1[0]['cands']], 's1_cost_ms': {key(c): v for c, v in s1[0]['cost_ms'].items()},
              's2_legal_candidates': s2[0]['cands'], 's2_cost_ms': s2[0]['cost_ms']}
    if a.build_only:
        print(json.dumps({k: v for k, v in inputs.items()}, indent=1))
        print('S1 labeled', sum(bool(i['gold']) for i in s1), 'S2 labeled', sum(bool(i['gold']) for i in s2), 'S3', len(s3))
        print('S1 det', {key(i['det']) for i in s1}, 'S3 cats', [(i['task'][:30], i['category'], [key(c) for c in i['cands']]) for i in s3])
        print(messages_for(s2[0]['task'])[1]['content'][:3000])
        print(json.dumps(tools_for(s1[0]['cands'], d1, s1[0]['cost_ms'])[0], indent=1))
        return
    vram = json.loads((HERE / 'vram_v0.json').read_text()) if a.score and (HERE / 'vram_v0.json').exists() else None
    if not a.score:
        vram = run_llm_arms(items, descs)
    OUT.write_text(json.dumps(score(items, sizes, vram, inputs), indent=1) + '\n')
    print('wrote', OUT)


if __name__ == '__main__':
    main()
