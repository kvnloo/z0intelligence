"""Build the frozen speed-offload v0 sets from z0's own artefacts (see PREREG.md).

Items go to ~/.cache/z0-speed-offload/sets/<class>.jsonl (never committed); only counts and sha256 values
are written to benchmarks/speed_offload/sets_manifest.json.

  python benchmarks/speed_offload/build_sets.py
"""
from __future__ import annotations

import hashlib
import json
import math
import random
import re
import subprocess
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
CACHE = Path('~/.cache/z0-speed-offload').expanduser()
SETS = CACHE / 'sets'
RECEIPTS = Path('~/.z0int/receipts/decisions.jsonl').expanduser()
SEED = 20260930

# Task texts. Each must be recognised by z0int.speed_offload.classify_task as its own class (tested).
TASKS = {
    'evidence_sufficiency': ('Judge whether the evidence is sufficient for the claim. {question} Answer with exactly one '
                             'option id and nothing else. Options: {options}'),
    'extract_json': ('Extract these fields from the receipt below and return only a JSON object with exactly the keys '
                     'capability_id, status, provider, model, latency_ms, ok. status is extra.status; ok is '
                     'extra.result.ok; provider, model and latency_ms are the top-level fields. Use null for any field '
                     'that is absent. Keep numbers as numbers and booleans as booleans.'),
    'classify_file_type': ('Classify the file type of this excerpt. Answer with exactly one of: python, markdown, json, '
                           'yaml, shell, typescript. Answer with the single word only.'),
    'summarize_tool_output': ('Summarize this git commit output in at most 40 words. State how many files changed and '
                              'name at least one changed file. Return only the summary.'),
    'short_rewrite': ('Rewrite the sentence below in at most {limit} words. Keep every `backticked` span exactly as '
                      'written, including the backticks. Return only the rewritten sentence on one line.'),
}
MAX_TOKENS = {'evidence_sufficiency': 16, 'extract_json': 200, 'classify_file_type': 8,
              'summarize_tool_output': 120, 'short_rewrite': 160}


def sha(text: str) -> str:
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


def item(cls, key, task, context, gold):
    return {'id': sha(f'{cls}:{key}')[:16], 'cls': cls, 'task': task, 'context': context, 'gold': gold,
            'max_tokens': MAX_TOKENS[cls]}


def git(*args):
    return subprocess.run(['git', '-C', str(ROOT), *args], check=True, capture_output=True, text=True).stdout


# --- evidence_sufficiency -------------------------------------------------------------------------------

def evidence_sufficiency(rng):
    rows = [json.loads(l) for l in (ROOT / 'benchmarks/data/authored144.jsonl').read_text().splitlines() if l.strip()]
    rows = [r for r in rows if r['family'] == 'evidence_interpretation' and r['split'] == 'test']
    out = []
    for r in rows:
        opts = '; '.join(f"{o['id']} = {o['description']}" for o in r['options'])
        state = r['state'] if isinstance(r['state'], str) else json.dumps(r['state'], sort_keys=True)
        out.append(item('evidence_sufficiency', r['id'],
                        TASKS['evidence_sufficiency'].format(question=r['question'], options=opts),
                        state, r['options'][r['label']]['id']))
    return out


# --- extract_json ---------------------------------------------------------------------------------------

def flatten(value, prefix=''):
    if isinstance(value, dict):
        for k, v in value.items():
            yield from flatten(v, f'{prefix}.{k}' if prefix else k)
    elif isinstance(value, list):
        if not value:
            yield prefix, '[]'
        for i, v in enumerate(value):
            yield from flatten(v, f'{prefix}[{i}]')
    else:
        text = json.dumps(value) if not isinstance(value, str) else value
        yield prefix, text if len(text) <= 160 else text[:157] + '...'


def receipt_gold(r):
    extra = r.get('extra') or {}
    result = extra.get('result')
    return {'capability_id': r.get('capability_id'), 'status': extra.get('status'), 'provider': r.get('provider'),
            'model': r.get('model'), 'latency_ms': r.get('latency_ms'),
            'ok': result.get('ok') if isinstance(result, dict) and 'ok' in result else None}


def extract_json(rng):
    frozen = CACHE / 'sources' / 'decisions.frozen.jsonl'
    if not frozen.exists():
        frozen.parent.mkdir(parents=True, exist_ok=True)
        frozen.write_bytes(RECEIPTS.read_bytes())
    rows = [json.loads(l) for l in frozen.read_text().splitlines() if l.strip()]
    strata = {}
    for i, r in enumerate(rows):
        g = receipt_gold(r)
        rendered = '\n'.join(f'{k} = {v}' for k, v in flatten(r))
        if len(rendered) > 8000:
            continue
        strata.setdefault((g['capability_id'], g['status'], g['ok']), []).append((i, rendered, g))
    small = [k for k, v in strata.items() if len(v) <= 6]
    big = sorted(k for k in strata if k not in small)
    chosen = [x for k in small for x in strata[k]]
    per = math.ceil((60 - len(chosen)) / max(len(big), 1))
    for k in big:
        chosen += rng.sample(strata[k], min(per, len(strata[k])))
    chosen = chosen[:60]
    return [item('extract_json', f'receipt:{i}:{sha(rendered)[:12]}', TASKS['extract_json'], rendered, g)
            for i, rendered, g in chosen], {'source_sha256': sha(frozen.read_text()), 'strata': len(strata)}


# --- classify_file_type ---------------------------------------------------------------------------------

EXT = {'.py': 'python', '.md': 'markdown', '.json': 'json', '.yml': 'yaml', '.yaml': 'yaml', '.sh': 'shell',
       '.ts': 'typescript'}


def classify_file_type(rng):
    by_label = {}
    for path in git('ls-files').splitlines():
        label = EXT.get(Path(path).suffix)
        p = ROOT / path
        if not label or not p.is_file() or p.stat().st_size > 2_000_000:
            continue
        lines = p.read_text(errors='replace').splitlines()
        if len(lines) >= 30:
            by_label.setdefault(label, []).append((path, lines))
    out = []
    for label in sorted(set(EXT.values())):
        files = sorted(by_label.get(label, []))
        rng.shuffle(files)
        picks, used = [], set()
        # one excerpt per file first; long files give a second, non-overlapping excerpt if a label is short
        for round_ in range(3):
            for path, lines in files:
                if len(picks) == 10:
                    break
                for _ in range(20):
                    start = rng.randint(5, len(lines) - 20)
                    if any(abs(start - s) < 20 for p_, s in used if p_ == path) or (round_ == 0 and any(p_ == path for p_, _ in used)):
                        continue
                    excerpt = '\n'.join(lines[start:start + 20])
                    if len(re.sub(r'\s', '', excerpt)) >= 200:
                        used.add((path, start))
                        picks.append(item('classify_file_type', f'{path}:{start}', TASKS['classify_file_type'], excerpt, label))
                        break
        out += picks
    return out


# --- summarize_tool_output ------------------------------------------------------------------------------

def summarize_tool_output(rng):
    shas = git('log', '--no-merges', '--format=%H', '-n', '2000').split()
    rng.shuffle(shas)
    out = []
    for h in shas:
        names = [n for n in git('show', '--name-only', '--format=', h).splitlines() if n.strip()]
        if not 1 <= len(names) <= 25:
            continue
        text = git('show', '--stat=200', '--format=medium', h)
        m = re.search(r'(\d+) files? changed', text)
        if not m or int(m.group(1)) != len(names) or len(text) > 6000:
            continue
        out.append(item('summarize_tool_output', h, TASKS['summarize_tool_output'], text,
                        {'files_changed': len(names), 'basenames': sorted({Path(n).name for n in names})}))
        if len(out) == 60:
            break
    return out


# --- short_rewrite --------------------------------------------------------------------------------------

def short_rewrite(rng):
    pool = set()
    for p in sorted(ROOT / f for f in git('ls-files', '*.md').splitlines()):
        if not p.is_file():
            continue
        in_code = False
        for line in p.read_text(errors='replace').splitlines():
            if line.strip().startswith('```'):
                in_code = not in_code
                continue
            if in_code or not line.strip() or line.lstrip().startswith(('#', '|', '>')):
                continue
            text = re.sub(r'^\s*([-*]|\d+\.)\s+', '', line).strip()
            for s in re.split(r'(?<=[.!?])\s+(?=[A-Z`])', text):
                words = len(s.split())
                if '`' in s and s.count('`') % 2 == 0 and 18 <= words <= 70 and s.endswith(('.', '!', '?')):
                    pool.add(s)
    pool = sorted(pool)
    rng.shuffle(pool)
    out = []
    for s in pool:
        limit = math.ceil(0.6 * len(s.split()))
        spans = re.findall(r'`[^`]+`', s)
        if sum(len(x.split()) for x in spans) + 2 > limit:  # infeasible: the kept spans alone exceed the limit
            continue
        if len(out) == 60:
            break
        out.append(item('short_rewrite', s, TASKS['short_rewrite'].format(limit=limit), s,
                        {'limit': limit, 'spans': re.findall(r'`[^`]+`', s)}))
    return out


def main():
    SETS.mkdir(parents=True, exist_ok=True)
    manifest = {'schema': 'z0int.speed_offload.sets.v0', 'seed': SEED, 'built_from_commit': git('rev-parse', 'HEAD').strip(),
                'classes': {}}
    builders = [('evidence_sufficiency', evidence_sufficiency), ('extract_json', extract_json),
                ('classify_file_type', classify_file_type), ('summarize_tool_output', summarize_tool_output),
                ('short_rewrite', short_rewrite)]
    for cls, fn in builders:
        rng = random.Random(f'{SEED}:{cls}')
        res = fn(rng)
        items, extra = res if isinstance(res, tuple) else (res, {})
        body = ''.join(json.dumps(x, sort_keys=True) + '\n' for x in items)
        (SETS / f'{cls}.jsonl').write_text(body)
        labels = {}
        for x in items:
            if isinstance(x['gold'], str):
                labels[x['gold']] = labels.get(x['gold'], 0) + 1
        manifest['classes'][cls] = {'n': len(items), 'sha256': sha(body), 'task_sha256': sha(TASKS[cls]),
                                    'max_context_chars': max(len(x['context']) for x in items),
                                    **({'label_counts': dict(sorted(labels.items()))} if labels else {}), **extra}
        print(cls, len(items), sha(body)[:12])
    (HERE / 'sets_manifest.json').write_text(json.dumps(manifest, indent=2, sort_keys=True) + '\n')


if __name__ == '__main__':
    main()
