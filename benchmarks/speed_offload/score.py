"""Score speed-offload v0 (rules fixed in PREREG.md) and write the evidence file.

  python benchmarks/speed_offload/score.py [--write-evidence]

The evidence file (~/.z0int/evidence/speed-offload-v0.json) holds aggregates, receipt-id ranges and the
sha256 of every run file; manifests/task_classes.v0.json cites it by sha256. No item text leaves the cache.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import random
import re
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
CACHE = Path('~/.cache/z0-speed-offload').expanduser()
SETS, RUNS = CACHE / 'sets', CACHE / 'runs'
EVIDENCE = Path('~/.z0int/evidence/speed-offload-v0.json').expanduser()
CLASSES = ['evidence_sufficiency', 'extract_json', 'classify_file_type', 'summarize_tool_output', 'short_rewrite']
FRONTIER = ['haiku', 'sonnet']
DELTA, FLOOR, MAX_ERR = 0.10, 0.80, 0.05
COST_DELTA, COST_FLOOR = 0.15, 0.70
WORDS = {w: i for i, w in enumerate('zero one two three four five six seven eight nine ten eleven twelve'.split())}


def normalise(text):
    text = re.sub(r'<think>.*?</think>', '', text or '', flags=re.S).strip()
    m = re.fullmatch(r'```[\w-]*\n?(.*?)\n?```', text, flags=re.S)
    return (m.group(1) if m else text).strip()


def words(text):
    return len(text.split())


def check(it, raw):
    if raw is None:
        return False
    out, gold, cls = normalise(raw), it['gold'], it['cls']
    if cls in ('evidence_sufficiency', 'classify_file_type'):
        return out.lower() == gold
    if cls == 'extract_json':
        try:
            obj = json.loads(out)
        except ValueError:
            return False
        if not isinstance(obj, dict) or set(obj) != set(gold):
            return False
        for k, g in gold.items():
            v = obj[k]
            if isinstance(g, bool) or g is None or isinstance(v, bool) or v is None:
                if v is not g:
                    return False
            elif isinstance(g, (int, float)):
                if not isinstance(v, (int, float)) or abs(v - g) > 1e-6:
                    return False
            elif v != g:
                return False
        return True
    if cls == 'summarize_tool_output':
        n = str(gold['files_changed'])
        has_n = re.search(rf'(?<!\d){n}(?!\d)', out) is not None
        names = gold['basenames'] + [Path(b).stem for b in gold['basenames'] if Path(b).stem]
        return words(out) <= 40 and has_n and any(b in out for b in names)
    if cls == 'short_rewrite':
        return (words(out) <= gold['limit'] and all(s in out for s in gold['spans']) and out != it['context'].strip()
                and '\n' not in out)
    raise KeyError(cls)


def pct(xs, q):
    xs = sorted(xs)
    if not xs:
        return None
    k = (len(xs) - 1) * q
    lo, hi = math.floor(k), math.ceil(k)
    return xs[lo] + (xs[hi] - xs[lo]) * (k - lo)


def lower_bound(d, resamples=10_000, seed=0):
    rng = random.Random(seed)
    n = len(d)
    means = sorted(sum(d[rng.randrange(n)] for _ in range(n)) / n for _ in range(resamples))
    return means[int(0.05 * resamples)]


def load(arm):
    path = RUNS / f'{arm}.jsonl'
    rows = {}
    for l in path.read_text().splitlines():
        if l.strip():
            r = json.loads(l)
            rows[r['item_id']] = r  # a resumed run never repeats an item; last write wins defensively
    return rows, hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--write-evidence', action='store_true')
    ap.add_argument('--write-registry', action='store_true', help='also record equivalence in manifests/task_classes.v0.json')
    a = ap.parse_args()
    arms = {arm: load(arm) for arm in ['local'] + FRONTIER}
    report = {'schema': 'z0int.speed_offload.evidence.v0', 'prereg': 'benchmarks/speed_offload/PREREG.md',
              'local_route': 'groot/qwen3-8b-q4km', 'rules': {'delta': DELTA, 'floor': FLOOR, 'max_err': MAX_ERR,
                                                               'cost_delta': COST_DELTA, 'cost_floor': COST_FLOOR},
              'run_files': {arm: {'sha256': h, 'run_ids': sorted({r['run_id'] for r in rows.values()})}
                            for arm, (rows, h) in arms.items()},
              'classes': {}}
    for cls in CLASSES:
        items = [json.loads(l) for l in (SETS / f'{cls}.jsonl').read_text().splitlines() if l.strip()]
        per = {}
        for arm, (rows, _) in arms.items():
            got = [rows.get(it['id']) for it in items]
            passes = [int(r is not None and r['error'] is None and check(it, r['output'])) for it, r in zip(items, got)]
            ok = [r for r in got if r is not None and r['error'] is None]
            if arm == 'local':
                lat = [r['wall_ms'] for r in ok if r['seq'] != 0]
                cold = [r['wall_ms'] for r in ok if r['seq'] == 0]
            else:
                lat = [r['api_ms'] for r in ok if r.get('api_ms') is not None]
                cold = []
            per[arm] = {'n': len(items), 'missing': sum(r is None for r in got),
                        'errors': sum(r is not None and r['error'] is not None for r in got),
                        'pass': sum(passes), 'pass_rate': sum(passes) / len(items), 'passes': passes,
                        'p50_ms': pct(lat, .5), 'p95_ms': pct(lat, .95),
                        'wall_p50_ms': pct([r['wall_ms'] for r in ok], .5), 'wall_p95_ms': pct([r['wall_ms'] for r in ok], .95),
                        'cold_ms': cold, 'receipt_ids': [r['receipt_id'] for r in ok][:3] + ['...'] if ok else []}
        q_ref = max(FRONTIER, key=lambda f: (per[f]['pass_rate'], f == 'sonnet'))
        l_ref = min(FRONTIER, key=lambda f: per[f]['p95_ms'] if per[f]['p95_ms'] is not None else math.inf)
        d = [x - y for x, y in zip(per['local']['passes'], per[q_ref]['passes'])]
        lb = lower_bound(d)
        loc = per['local']
        err_rate = (loc['errors'] + loc['missing']) / loc['n']
        faster = loc['p95_ms'] is not None and per[l_ref]['p95_ms'] is not None and loc['p95_ms'] < per[l_ref]['p95_ms']
        speed = lb > -DELTA and loc['pass_rate'] >= FLOOR and faster and err_rate <= MAX_ERR
        cost = lb > -COST_DELTA and loc['pass_rate'] >= COST_FLOOR and err_rate <= MAX_ERR
        status = 'speed_qualified' if speed else 'cost_eligible' if cost else 'not_equivalent'
        for arm in per:
            per[arm].pop('passes')
        report['classes'][cls] = {
            'status': status, 'quality_comparator': q_ref, 'latency_comparator': l_ref,
            'diff_mean': sum(d) / len(d), 'diff_lower_bound_95': lb,
            'discordant': {'local_only': d.count(1), 'frontier_only': d.count(-1)},
            'local_faster_at_p95': faster, 'local_error_rate': err_rate,
            'speedup_p95': (per[l_ref]['p95_ms'] / loc['p95_ms']) if faster else None, 'arms': per}
        r = report['classes'][cls]
        print(f"{cls:22s} {status:16s} local {loc['pass']}/{loc['n']} haiku {per['haiku']['pass']} sonnet "
              f"{per['sonnet']['pass']} | lb {lb:+.3f} | p95 local {loc['p95_ms'] or 0:.0f} ms vs {l_ref} "
              f"{per[l_ref]['p95_ms'] or 0:.0f} ms | err {err_rate:.2f}")
    text = json.dumps(report, indent=2, sort_keys=True) + '\n'
    (CACHE / 'score.json').write_text(text)
    if a.write_evidence:
        EVIDENCE.parent.mkdir(parents=True, exist_ok=True)
        EVIDENCE.write_text(text)
        print('evidence', EVIDENCE, hashlib.sha256(text.encode()).hexdigest())
    if a.write_registry:
        write_registry(report, hashlib.sha256(text.encode()).hexdigest())


def write_registry(report, evidence_sha256):
    path = ROOT / 'manifests/task_classes.v0.json'
    reg = json.loads(path.read_text())
    sets = json.loads((HERE / 'sets_manifest.json').read_text())['classes']
    reg['revision'] = 'v0-' + evidence_sha256[:12]
    reg['evidence'] = {'path': str(EVIDENCE).replace(str(Path.home()), '~'), 'sha256': evidence_sha256}
    for cls, r in report['classes'].items():
        arms = r['arms']
        q, l = r['quality_comparator'], r['latency_comparator']
        reg['classes'][cls]['equivalence'] = {
            'status': r['status'], 'prereg': report['prereg'], 'n': arms['local']['n'],
            'set_sha256': sets[cls]['sha256'], 'max_context_chars': sets[cls]['max_context_chars'],
            'local': {'route': report['local_route'], 'pass': arms['local']['pass'], 'errors': arms['local']['errors'],
                      'p50_ms': round(arms['local']['p50_ms']), 'p95_ms': round(arms['local']['p95_ms'])},
            'frontier': {f: {'pass': arms[f]['pass'], 'errors': arms[f]['errors'], 'p50_api_ms': round(arms[f]['p50_ms']),
                             'p95_api_ms': round(arms[f]['p95_ms']), 'p95_wall_ms': round(arms[f]['wall_p95_ms'])}
                         for f in FRONTIER},
            'quality_comparator': q, 'latency_comparator': l,
            'diff_lower_bound_95': round(r['diff_lower_bound_95'], 4), 'margin': DELTA,
            'speedup_p95': round(r['speedup_p95'], 1) if r['speedup_p95'] else None,
            'receipts': {arm: arms[arm]['receipt_ids'][:3] for arm in arms},
            'run_ids': {arm: report['run_files'][arm]['run_ids'] for arm in report['run_files']}}
    path.write_text(json.dumps(reg, indent=2) + '\n')
    print('registry', path, reg['revision'])


if __name__ == '__main__':
    main()
