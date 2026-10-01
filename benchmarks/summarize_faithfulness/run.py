"""Run one arm of summarize-faithfulness v0 over the frozen set (see PREREG.md). Resumable; one JSONL row per call.

  python benchmarks/summarize_faithfulness/run.py --arm local8b             # groot qwen3-8b-q4km, one at a time
  python benchmarks/summarize_faithfulness/run.py --arm local14b            # groot qwen3-14b-q4km (exploratory)
  python benchmarks/summarize_faithfulness/run.py --arm haiku --jobs 3      # claude -p headless, <= 3 concurrent
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import threading
import time
import urllib.request
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from z0int.worker_routing import SYSTEM

CACHE = Path('~/.cache/z0-summ-faith').expanduser()
ITEMS, RUNS = CACHE / 'sets' / 'items.jsonl', CACHE / 'runs'
LOCAL_URL = 'http://100.113.138.100:11530/v1/chat/completions'
LOCAL = {'local8b': 'qwen3-8b-q4km', 'local14b': 'qwen3-14b-q4km'}
CLAUDE = {'haiku': 'haiku', 'sonnet': 'sonnet'}
TIMEOUT_S = 180
QUOTA_RE = re.compile(r"(hit your (session|usage|weekly) limit|usage limit|rate limit|429|overloaded)", re.I)


def user_text(it):
    return it['task'] + '\n\nContext supplied by parent:\n' + it['context']


def clean(text):
    """The only normalisation applied to every arm before scoring/judging: drop <think> blocks, strip."""
    return re.sub(r'<think>.*?</think>', '', text or '', flags=re.S).strip()


def call_local(model):
    def call(it):
        body = {'model': model, 'temperature': 0, 'max_tokens': it['max_tokens'],
                'messages': [{'role': 'system', 'content': SYSTEM}, {'role': 'user', 'content': user_text(it)}]}
        t = time.perf_counter()
        req = urllib.request.Request(LOCAL_URL, json.dumps(body).encode(), {'Content-Type': 'application/json'})
        with urllib.request.urlopen(req, timeout=TIMEOUT_S) as resp:
            r = json.load(resp)
        wall = (time.perf_counter() - t) * 1000
        ch = r['choices'][0]
        return {'raw': ch['message']['content'], 'output': clean(ch['message']['content']), 'wall_ms': wall,
                'api_ms': None, 'finish_reason': ch.get('finish_reason'), 'usage': r.get('usage'), 'model': model}
    return call


def call_claude(model):
    def call(it):
        cmd = ['claude', '-p', '--model', model, '--output-format', 'json', '--setting-sources', '',
               '--strict-mcp-config', '--tools', '', '--no-session-persistence', '--system-prompt', SYSTEM]
        t = time.perf_counter()
        p = subprocess.run(cmd, input=user_text(it), capture_output=True, text=True, timeout=TIMEOUT_S, cwd=CACHE)
        wall = (time.perf_counter() - t) * 1000
        try:
            d = json.loads(p.stdout)
        except ValueError:
            raise RuntimeError(f'rc={p.returncode} stdout={p.stdout[:150]!r} stderr={p.stderr[:150]!r}')
        if d.get('is_error'):
            raise RuntimeError(str(d.get('result'))[:200])
        return {'raw': d.get('result') or '', 'output': clean(d.get('result')), 'wall_ms': wall,
                'api_ms': d.get('duration_api_ms'), 'finish_reason': d.get('stop_reason'), 'usage': d.get('usage'),
                'model': ','.join(d.get('modelUsage') or {}) or model, 'cost_usd': d.get('total_cost_usd')}
    return call


def gpu_sample():
    try:
        out = subprocess.run(['ssh', '-o', 'ConnectTimeout=5', '0', 'nvidia-smi --query-gpu=utilization.gpu,memory.used '
                              '--format=csv,noheader,nounits'], capture_output=True, text=True, timeout=20).stdout
        util, mem = [int(x) for x in out.strip().split(',')]
        return {'gpu_util_pct': util, 'gpu_mem_mib': mem, 'ts': time.time()}
    except Exception as exc:  # observability only
        return {'error': type(exc).__name__, 'ts': time.time()}


def is_quota(err):
    return bool(err) and err.startswith('RuntimeError') and bool(QUOTA_RE.search(err))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--arm', choices=[*LOCAL, *CLAUDE], required=True)
    ap.add_argument('--jobs', type=int, default=1)
    ap.add_argument('--limit', type=int)
    a = ap.parse_args()
    local = a.arm in LOCAL
    if local and a.jobs != 1:
        raise SystemExit('local arms run one request at a time (shared GPU)')
    jobs = min(a.jobs, 3)
    RUNS.mkdir(parents=True, exist_ok=True)
    out = RUNS / f'{a.arm}.jsonl'
    rows = [json.loads(l) for l in out.read_text().splitlines() if l.strip()] if out.exists() else []
    latest = {r['item_id']: r for r in rows}
    done = {i for i, r in latest.items() if not is_quota(r['error'])}
    items = [json.loads(l) for l in ITEMS.read_text().splitlines() if l.strip()]
    todo = [it for it in items if it['id'] not in done][:a.limit]
    run_id = f'{a.arm}-{time.strftime("%Y%m%dT%H%M%S")}-{uuid.uuid4().hex[:6]}'
    fn = call_local(LOCAL[a.arm]) if local else call_claude(CLAUDE[a.arm])
    lock = threading.Lock()
    meta = RUNS / f'{a.arm}.meta.jsonl'
    with meta.open('a') as fh:
        fh.write(json.dumps({'run_id': run_id, 'phase': 'start', 'todo': len(todo), **(gpu_sample() if local else {'ts': time.time()})}) + '\n')
    quota_hit = threading.Event()

    def one(pair):
        seq, it = pair
        if quota_hit.is_set():  # stop spending calls once a usage limit is hit; rerun after reset
            return
        row = {'run_id': run_id, 'seq': seq, 'receipt_id': hashlib.sha256(f'{run_id}:{it["id"]}'.encode()).hexdigest()[:16],
               'item_id': it['id'], 'kind': it['kind'], 'arm': a.arm, 'ts': time.time()}
        try:
            row.update(fn(it), error=None)
        except Exception as exc:
            row.update(output=None, wall_ms=None, api_ms=None, error=f'{type(exc).__name__}: {str(exc)[:200]}')
            if is_quota(row['error']):
                quota_hit.set()
        with lock, out.open('a') as fh:
            fh.write(json.dumps(row) + '\n')
        print(a.arm, seq, it['kind'], it['id'], 'ERR ' + row['error'] if row['error'] else round(row['wall_ms']), flush=True)
        if local and seq % 40 == 39:
            with lock, meta.open('a') as fh:
                fh.write(json.dumps({'run_id': run_id, 'phase': f'seq{seq}', **gpu_sample()}) + '\n')

    with ThreadPoolExecutor(max_workers=jobs) as ex:
        list(ex.map(one, enumerate(todo)))
    with meta.open('a') as fh:
        fh.write(json.dumps({'run_id': run_id, 'phase': 'end', 'quota_hit': quota_hit.is_set(),
                             **(gpu_sample() if local else {'ts': time.time()})}) + '\n')
    if quota_hit.is_set():
        raise SystemExit('stopped: usage limit hit (recorded); rerun after reset')


if __name__ == '__main__':
    main()
