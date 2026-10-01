"""Score summarize-faithfulness v0 (see PREREG.md) and apply the pre-registered decision rule.

  python benchmarks/summarize_faithfulness/score.py            # writes ~/.cache/z0-summ-faith/score.json
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

import numpy as np

from judge import judge_id
from run import CACHE, ITEMS, RUNS

WORD_LIMIT = 40
B, SEED = 10000, 20261001
H_MARGIN, R_MARGIN = 0.02, 0.05
PRIMARY, COMPARATOR = 'local8b', 'haiku'
ARMS = ['local8b', 'local14b', 'haiku', 'sonnet']
WORDS = {w: i for i, w in enumerate('zero one two three four five six seven eight nine ten eleven twelve thirteen '
                                     'fourteen fifteen sixteen seventeen eighteen nineteen twenty'.split())}
FAIL_RE = re.compile(r'\b(fail\w*|error\w*|broke\w*|break\w*|crash\w*|unsuccessful|non-?zero|abort\w*|red|'
                     r'exit(ed)? (with )?(code|status) [1-9]\d*|did not (pass|succeed|build|complete)|'
                     r'not (pass|succeed)\w*)\b', re.I)
PASS_RE = re.compile(r'\b(pass\w*|succe\w+|clean|green|ok|no (errors?|failures?|issues|problems)|all checks passed|'
                     r'exit(ed)? (with )?(code|status) 0|built|merged|no issues found)\b', re.I)
FILE_RE = re.compile(r'[\w./-]+\.(?:py|pyi|md|json|jsonl|toml|ya?ml|ts|tsx|js|mjs|rs|sh|txt|lock|whl|cfg|ini|html|css)\b')
TEST_RE = re.compile(r'\btest_\w+')


def norm(s):
    return re.sub(r'[`*"\']', '', s or '')


def has_number(summary, n):
    s = norm(summary).replace(',', '')
    if re.search(rf'(?<![\w.]){n}(?!\d|\.\d)', s):
        return True
    words = [w for w, v in WORDS.items() if v == n] + (['a single', 'single'] if n == 1 else [])
    return any(re.search(rf'\b{w}\b', s, re.I) for w in words)


STOP = {'with', 'from', 'that', 'this', 'into', 'over', 'when', 'have', 'been', 'were', 'they', 'then', 'than'}


def has_name(summary, alts):
    s = norm(summary).lower()
    for a in alts:
        a = a.lower().strip()
        if a and a in s:
            return True
        words = [w for w in re.findall(r'[a-z0-9]+', a) if len(w) >= 4 and w not in STOP]
        if len(a.split()) >= 3 and words and sum(w in s for w in words) / len(words) >= 0.6:
            return True
    return False


def fact_hit(summary, f):
    if f['type'] == 'outcome':
        return bool((FAIL_RE if f['value'] == 'fail' else PASS_RE).search(norm(summary)))
    if f['type'] == 'number':
        return has_number(summary, f['value'])
    return has_name(summary, f['value'])


def det_unsupported(summary, context):
    """Deterministic secondary check: numbers / file names / test names in the summary absent from the output."""
    s = norm(summary)
    ctx_l = context.lower()
    flags = []
    hexes = re.findall(r'\b[0-9a-f]{7,40}\b', s)
    for h in hexes:
        if h.lower() not in ctx_l:
            flags.append(('hash', h))
    s2 = re.sub(r'\b[0-9a-f]{7,40}\b', ' ', s)
    ctx_nums = set(re.findall(r'\d+', context.replace(',', '')))
    for n in re.findall(r'\d+', s2.replace(',', '')):
        if n not in ctx_nums and n != str(WORD_LIMIT):
            flags.append(('number', n))
    for f in FILE_RE.findall(s):
        if f.lower() not in ctx_l and Path(f).name.lower() not in ctx_l:
            flags.append(('file', f))
    for t in TEST_RE.findall(s):
        if t.lower() not in ctx_l:
            flags.append(('test', t))
    return flags


def latest_rows(path, key):
    rows = [json.loads(l) for l in path.read_text().splitlines() if l.strip()] if path.exists() else []
    return {r[key]: r for r in rows}


def pct(xs, q):
    return round(float(np.percentile(xs, q))) if len(xs) else None


def paired(a, b, rng):
    a, b = np.asarray(a, float), np.asarray(b, float)
    d = a - b
    idx = rng.integers(0, len(d), (B, len(d)))
    boots = d[idx].mean(1)
    return {'n': int(len(d)), 'diff': round(float(d.mean()), 4), 'lo95_one_sided': round(float(np.percentile(boots, 5)), 4),
            'hi95_one_sided': round(float(np.percentile(boots, 95)), 4)}


def main():
    items = {json.loads(l)['id']: json.loads(l) for l in ITEMS.read_text().splitlines() if l.strip()}
    judge = latest_rows(RUNS / 'judge.jsonl', 'judge_id')
    per = {}   # arm -> item -> metrics
    arms_report = {}
    for arm in ARMS:
        runs = latest_rows(RUNS / f'{arm}.jsonl', 'item_id')
        if not runs:
            continue
        per[arm] = {}
        lat = []
        for iid, it in items.items():
            r = runs.get(iid)
            if r is None:
                continue
            crit = [f for f in it['facts'] if f['tier'] == 'critical']
            sec = [f for f in it['facts'] if f['tier'] == 'secondary']
            m = {'error': r['error'], 'kind': it['kind']}
            if r['error'] is None:
                s = r['output'] or ''
                hits = [fact_hit(s, f) for f in crit]
                m.update(words=len(s.split()),
                         format_ok=bool(s.strip()) and len(s.split()) <= WORD_LIMIT and r.get('finish_reason') not in ('length', 'max_tokens'),
                         recall=sum(hits) / len(crit), all_critical=all(hits), crit_hits=sum(hits), crit_n=len(crit),
                         sec_recall=(sum(fact_hit(s, f) for f in sec) / len(sec)) if sec else None,
                         det_flags=det_unsupported(s, it['context']))
                lat.append(r['api_ms'] if r.get('api_ms') is not None else r['wall_ms'])
                j = judge.get(judge_id(arm, iid))
                if j and j['error'] is None:
                    bad = [c for c in j['claims'] if c['label'] != 'SUPPORTED']
                    m.update(judged=True, hallucinated=bool(bad), n_claims=len(j['claims']), n_bad=len(bad),
                             contradicted=any(c['label'] == 'CONTRADICTED' for c in bad),
                             judge_recall=sum(j['facts'].values()) / len(j['facts']) if j['facts'] else None,
                             bad_claims=[c['claim'] for c in bad])
                else:
                    m.update(judged=False, judge_error=(j or {}).get('error'))
            per[arm][iid] = m
        ok = [m for m in per[arm].values() if m['error'] is None]
        jd = [m for m in ok if m.get('judged')]
        arms_report[arm] = {
            'rows': len(per[arm]), 'errors': len(per[arm]) - len(ok),
            'format_pass': sum(m['format_ok'] for m in ok),
            'critical_recall_mean': round(float(np.mean([m['recall'] for m in ok])), 4) if ok else None,
            'critical_facts_micro': f"{sum(m['crit_hits'] for m in ok)}/{sum(m['crit_n'] for m in ok)}",
            'all_critical_items': sum(m['all_critical'] for m in ok),
            'judged': len(jd),
            'hallucinated_items': sum(m['hallucinated'] for m in jd),
            'hallucination_rate': round(sum(m['hallucinated'] for m in jd) / len(jd), 4) if jd else None,
            'contradicted_items': sum(m['contradicted'] for m in jd),
            'unsupported_claims': f"{sum(m['n_bad'] for m in jd)}/{sum(m['n_claims'] for m in jd)}",
            'judge_recall_mean': round(float(np.mean([m['judge_recall'] for m in jd if m['judge_recall'] is not None])), 4) if jd else None,
            'det_flagged_items': sum(bool(m['det_flags']) for m in ok),
            'words_p50': pct([m['words'] for m in ok], 50),
            'p50_ms': pct(lat, 50), 'p95_ms': pct(lat, 95),
            'latency_basis': 'wall (local HTTP)' if arm.startswith('local') else 'duration_api_ms (claude -p)',
            'by_kind': {k: {'n': sum(1 for m in ok if m['kind'] == k),
                            'recall': round(float(np.mean([m['recall'] for m in ok if m['kind'] == k])), 3),
                            'halluc': f"{sum(m['hallucinated'] for m in jd if m['kind'] == k)}/{sum(1 for m in jd if m['kind'] == k)}"}
                        for k in sorted({m['kind'] for m in ok})},
        }

    def compare(x, y):
        """Paired x - y. Errors in x count as failures (recall 0, hallucinated); items where y errored or either
        side is unjudged are dropped from the hallucination comparison (pre-registered, conservative for x)."""
        rng = np.random.default_rng(SEED)
        common = [i for i in items if i in per.get(x, {}) and i in per.get(y, {}) and per[y][i]['error'] is None]
        rx = [0.0 if per[x][i]['error'] else per[x][i]['recall'] for i in common]
        ry = [per[y][i]['recall'] for i in common]
        hc = [i for i in common if per[y][i].get('judged') and (per[x][i]['error'] or per[x][i].get('judged'))]
        hx = [1.0 if per[x][i]['error'] else float(per[x][i]['hallucinated']) for i in hc]
        hy = [float(per[y][i]['hallucinated']) for i in hc]
        jrc = [i for i in hc if not per[x][i]['error'] and per[x][i].get('judge_recall') is not None and per[y][i].get('judge_recall') is not None]
        res = {'recall': paired(rx, ry, rng) if common else None,
               'hallucination': paired(hx, hy, rng) if hc else None,
               'judge_recall_sensitivity': paired([per[x][i]['judge_recall'] for i in jrc], [per[y][i]['judge_recall'] for i in jrc], rng) if jrc else None,
               'dropped_for_comparator_error': sum(1 for i in items if i in per.get(y, {}) and per[y][i]['error'])}
        if res['recall'] and res['hallucination']:
            res['halluc_noninferior'] = res['hallucination']['hi95_one_sided'] < H_MARGIN
            res['recall_noninferior'] = res['recall']['lo95_one_sided'] > -R_MARGIN
            res['qualifies'] = res['halluc_noninferior'] and res['recall_noninferior']
        return res

    comparisons = {f'{x}_vs_{y}': compare(x, y) for x, y in
                   [('local8b', 'haiku'), ('local8b', 'sonnet'), ('local14b', 'haiku'), ('sonnet', 'haiku')]
                   if x in per and y in per}
    primary = comparisons.get(f'{PRIMARY}_vs_{COMPARATOR}', {})
    n_items = len(items)
    used = min((primary.get(k) or {}).get('n', 0) for k in ('recall', 'hallucination')) if primary else 0
    primary['dropped_items'] = n_items - used
    complete = used >= 0.9 * n_items
    verdict = ('incomplete' if not complete else 'qualifies' if primary.get('qualifies') else 'does_not_qualify')
    examples = {arm: [{'item': i, 'kind': m['kind'], 'bad_claims': m['bad_claims']} for i, m in per[arm].items()
                      if m.get('hallucinated')] for arm in per}
    det_examples = {arm: [{'item': i, 'flags': m['det_flags']} for i, m in per[arm].items() if m.get('det_flags')] for arm in per}
    res = {'schema': 'z0int.summarize_faithfulness.score.v0', 'n_items': n_items,
           'items_sha256': hashlib.sha256(ITEMS.read_bytes()).hexdigest(),
           'run_files_sha256': {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(RUNS.glob('*.jsonl'))},
           'arms': arms_report, 'comparisons': comparisons,
           'rule': {'primary': PRIMARY, 'comparator': COMPARATOR, 'halluc_margin': H_MARGIN, 'recall_margin': R_MARGIN,
                    'bootstrap': B, 'seed': SEED},
           'verdict': verdict}
    (CACHE / 'score.json').write_text(json.dumps(res, indent=1) + '\n')
    (CACHE / 'examples.json').write_text(json.dumps({'judge': examples, 'deterministic': det_examples}, indent=1) + '\n')
    print(json.dumps({k: v for k, v in res.items() if k not in ('run_files_sha256',)}, indent=1))


if __name__ == '__main__':
    main()
