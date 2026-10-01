"""Blinded frontier judge for summarize-faithfulness v0 (see PREREG.md). Resumable; one JSONL row per call.

The judge (Claude Sonnet via `claude -p`) sees the tool output, one summary and the item's gold critical facts.
It never sees the arm, the model, the run id or latencies. Summaries from all arms are pooled and judged in a
seeded random order; the arm is re-attached only in score.py through an opaque judge id.

  python benchmarks/summarize_faithfulness/judge.py --arms local8b haiku --jobs 3
"""
from __future__ import annotations

import argparse
import hashlib
import json
import random
import re
import subprocess
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from run import CACHE, ITEMS, RUNS, is_quota

JUDGE_MODEL = 'sonnet'
SEED = 20261001
TIMEOUT_S = 240
JUDGE_SYSTEM = ('You are a strict, careful fact-checker for short summaries of developer tool output. You check '
                'only whether each statement is backed by the tool output. Answer with JSON only.')
JUDGE_PROMPT = '''TOOL OUTPUT:
<<<OUTPUT
{context}
OUTPUT>>>

SUMMARY TO CHECK:
<<<SUMMARY
{summary}
SUMMARY>>>

CRITICAL FACTS (taken from the tool output):
{facts}

Task 1. Split the SUMMARY into atomic factual claims about the tool output or what it shows. Label each claim:
- SUPPORTED: stated in the tool output, or follows from it by simple counting, arithmetic or aggregation. Accurate
  paraphrase and accurate vague characterisation ("several tests failed") are SUPPORTED.
- UNSUPPORTED: not stated and not derivable from the tool output: invented names, numbers, files, outcomes,
  causes, fixes or details. A cause or explanation that the output does not show is UNSUPPORTED even if hedged.
- CONTRADICTED: conflicts with the tool output (wrong number, wrong outcome, wrong name, wrong status).
Ignore style, length, wording and anything the summary leaves out; omissions are handled in Task 2.

Task 2. For each critical fact, say whether the SUMMARY conveys it (paraphrase counts; for "at least one of",
naming any one counts).

Return only this JSON object, no prose and no code fence:
{{"claims": [{{"claim": "...", "label": "SUPPORTED|UNSUPPORTED|CONTRADICTED", "why": "<=15 words"}}], "facts": {{{fact_keys}}}}}'''


def fact_text(f):
    label = f['label'].replace('_any', '').replace('_', ' ')
    if f['type'] == 'outcome':
        return ('The command/run failed or reported failures/errors' if f['value'] == 'fail'
                else 'The command/run succeeded with no failures or errors')
    if f['type'] == 'number':
        return f'{label}: {f["value"]}'
    alts = f['value']
    if f['label'].endswith('_any'):
        return f'{label}: at least one of ' + ', '.join(sorted({a.split("/")[-1] for a in alts})[:12])
    return f'{label}: ' + max(alts, key=len)


def judge_id(arm, item_id):
    return hashlib.sha256(f'judge:{arm}:{item_id}'.encode()).hexdigest()[:16]


def build_prompt(it, summary):
    crit = [f for f in it['facts'] if f['tier'] == 'critical']
    facts = '\n'.join(f'F{i + 1}: {fact_text(f)}' for i, f in enumerate(crit))
    keys = ', '.join(f'"F{i + 1}": true|false' for i in range(len(crit)))
    return JUDGE_PROMPT.format(context=it['context'], summary=summary or '(empty)', facts=facts, fact_keys=keys), len(crit)


def parse(text, n_facts):
    m = re.search(r'\{.*\}', text or '', re.S)
    if not m:
        raise ValueError('no json')
    d = json.loads(m.group(0))
    claims = d['claims']
    if not isinstance(claims, list) or any(c.get('label') not in ('SUPPORTED', 'UNSUPPORTED', 'CONTRADICTED') for c in claims):
        raise ValueError('bad claims')
    facts = d['facts']
    if set(facts) != {f'F{i + 1}' for i in range(n_facts)} or any(type(v) is not bool for v in facts.values()):
        raise ValueError('bad facts')
    return claims, facts


def call_judge(prompt):
    cmd = ['claude', '-p', '--model', JUDGE_MODEL, '--output-format', 'json', '--setting-sources', '',
           '--strict-mcp-config', '--tools', '', '--no-session-persistence', '--system-prompt', JUDGE_SYSTEM]
    t = time.perf_counter()
    p = subprocess.run(cmd, input=prompt, capture_output=True, text=True, timeout=TIMEOUT_S, cwd=CACHE)
    try:
        d = json.loads(p.stdout)
    except ValueError:
        raise RuntimeError(f'rc={p.returncode} stdout={p.stdout[:150]!r} stderr={p.stderr[:150]!r}')
    if d.get('is_error'):
        raise RuntimeError(str(d.get('result'))[:200])
    return d.get('result') or '', (time.perf_counter() - t) * 1000, ','.join(d.get('modelUsage') or {})


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--arms', nargs='+', required=True)
    ap.add_argument('--jobs', type=int, default=3)
    ap.add_argument('--limit', type=int)
    ap.add_argument('--out', default='judge.jsonl')
    a = ap.parse_args()
    items = {json.loads(l)['id']: json.loads(l) for l in ITEMS.read_text().splitlines() if l.strip()}
    out = RUNS / a.out
    prev = [json.loads(l) for l in out.read_text().splitlines() if l.strip()] if out.exists() else []
    done = {r['judge_id'] for r in prev if r.get('error') is None or not (is_quota(r['error']) or r.get('retryable'))}
    work = []
    for arm in a.arms:
        rows = [json.loads(l) for l in (RUNS / f'{arm}.jsonl').read_text().splitlines() if l.strip()]
        latest = {r['item_id']: r for r in rows}
        for iid, r in latest.items():
            if r['error'] is None and judge_id(arm, iid) not in done:
                work.append((judge_id(arm, iid), iid, r['output']))
    random.Random(f'{SEED}:{",".join(sorted(a.arms))}').shuffle(work)
    work = work[:a.limit]
    run_id = f'judge-{time.strftime("%Y%m%dT%H%M%S")}-{uuid.uuid4().hex[:6]}'
    lock, quota_hit = threading.Lock(), threading.Event()

    def one(w):
        jid, iid, summary = w
        if quota_hit.is_set():
            return
        prompt, n = build_prompt(items[iid], summary)
        row = {'run_id': run_id, 'judge_id': jid, 'item_id': iid, 'judge_model': JUDGE_MODEL,
               'prompt_sha256': hashlib.sha256(prompt.encode()).hexdigest(), 'ts': time.time()}
        err = None
        for attempt in range(3):
            try:
                text, ms, model = call_judge(prompt)
                claims, facts = parse(text, n)
                row.update(claims=claims, facts=facts, raw=text, wall_ms=ms, model=model, attempts=attempt + 1, error=None)
                break
            except Exception as exc:
                err = f'{type(exc).__name__}: {str(exc)[:200]}'
                if is_quota(err):
                    quota_hit.set()
                    break
        else:
            row.update(error=err, retryable=False, attempts=3)
        if 'error' not in row:
            row.update(error=err, retryable=is_quota(err))
        with lock, out.open('a') as fh:
            fh.write(json.dumps(row) + '\n')
        print('judge', jid, 'ERR ' + row['error'] if row['error'] else
              sum(c['label'] != 'SUPPORTED' for c in row['claims']), flush=True)

    with ThreadPoolExecutor(max_workers=min(a.jobs, 3)) as ex:
        list(ex.map(one, work))
    if quota_hit.is_set():
        raise SystemExit('stopped: usage limit hit (recorded); rerun after reset')


if __name__ == '__main__':
    main()
