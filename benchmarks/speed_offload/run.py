"""Run one arm of speed-offload v0 over the frozen sets (see PREREG.md). Resumable; one JSONL row per call.

  python benchmarks/speed_offload/run.py --arm local            # groot qwen3-8b-q4km, one request at a time
  python benchmarks/speed_offload/run.py --arm haiku --jobs 3   # claude -p headless, <= 3 concurrent
"""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import threading
import time
import urllib.request
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from z0int.worker_routing import SYSTEM

CACHE = Path('~/.cache/z0-speed-offload').expanduser()
SETS, RUNS = CACHE / 'sets', CACHE / 'runs'
CLASSES = ['evidence_sufficiency', 'extract_json', 'classify_file_type', 'summarize_tool_output', 'short_rewrite']
LOCAL_URL = 'http://100.113.138.100:11530/v1/chat/completions'
LOCAL_MODEL = 'qwen3-8b-q4km'
TIMEOUT_S = 120


def user_text(it):
    return it['task'] + ('\n\nContext supplied by parent:\n' + it['context'] if it['context'] else '')


def call_local(it):
    body = {'model': LOCAL_MODEL, 'temperature': 0, 'max_tokens': it['max_tokens'],
            'messages': [{'role': 'system', 'content': SYSTEM}, {'role': 'user', 'content': user_text(it)}]}
    t = time.perf_counter()
    req = urllib.request.Request(LOCAL_URL, json.dumps(body).encode(), {'Content-Type': 'application/json'})
    with urllib.request.urlopen(req, timeout=TIMEOUT_S) as resp:
        r = json.load(resp)
    wall = (time.perf_counter() - t) * 1000
    return {'output': r['choices'][0]['message']['content'], 'wall_ms': wall, 'api_ms': None,
            'usage': r.get('usage'), 'model': LOCAL_MODEL}


def call_claude(model):
    def call(it):
        cmd = ['claude', '-p', '--model', model, '--output-format', 'json', '--setting-sources', '',
               '--strict-mcp-config', '--tools', '', '--no-session-persistence', '--system-prompt', SYSTEM]
        t = time.perf_counter()
        p = subprocess.run(cmd, input=user_text(it), capture_output=True, text=True, timeout=TIMEOUT_S, cwd=CACHE)
        wall = (time.perf_counter() - t) * 1000
        d = json.loads(p.stdout)
        if d.get('is_error'):
            raise RuntimeError(str(d.get('result'))[:200])
        return {'output': d.get('result') or '', 'wall_ms': wall, 'api_ms': d.get('duration_api_ms'),
                'usage': d.get('usage'), 'model': ','.join(d.get('modelUsage') or {}) or model,
                'cost_usd': d.get('total_cost_usd')}
    return call


def gpu_sample():
    try:
        out = subprocess.run(['ssh', '-o', 'ConnectTimeout=5', '0', 'nvidia-smi --query-gpu=utilization.gpu,memory.used '
                              '--format=csv,noheader,nounits'], capture_output=True, text=True, timeout=20).stdout
        util, mem = [int(x) for x in out.strip().split(',')]
        return {'gpu_util_pct': util, 'gpu_mem_mib': mem, 'ts': time.time()}
    except Exception as exc:  # observability only
        return {'error': type(exc).__name__, 'ts': time.time()}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--arm', choices=['local', 'haiku', 'sonnet'], required=True)
    ap.add_argument('--jobs', type=int, default=1)
    ap.add_argument('--classes', nargs='*', default=CLASSES)
    ap.add_argument('--limit', type=int)
    a = ap.parse_args()
    if a.arm == 'local' and a.jobs != 1:
        raise SystemExit('local arm runs one request at a time (shared GPU)')
    jobs = min(a.jobs, 3)
    RUNS.mkdir(parents=True, exist_ok=True)
    out = RUNS / f'{a.arm}.jsonl'
    done = {json.loads(l)['item_id'] for l in out.read_text().splitlines() if l.strip()} if out.exists() else set()
    items = [json.loads(l) for c in a.classes for l in (SETS / f'{c}.jsonl').read_text().splitlines() if l.strip()]
    todo = [it for it in items if it['id'] not in done][:a.limit]
    run_id = f'{a.arm}-{time.strftime("%Y%m%dT%H%M%S")}-{uuid.uuid4().hex[:6]}'
    fn = call_local if a.arm == 'local' else call_claude(a.arm)
    lock = threading.Lock()
    meta = RUNS / f'{a.arm}.meta.jsonl'
    if a.arm == 'local':
        with meta.open('a') as fh:
            fh.write(json.dumps({'run_id': run_id, 'phase': 'start', **gpu_sample()}) + '\n')

    def one(pair):
        seq, it = pair
        row = {'run_id': run_id, 'seq': seq, 'receipt_id': hashlib.sha256(f'{run_id}:{it["id"]}'.encode()).hexdigest()[:16],
               'item_id': it['id'], 'cls': it['cls'], 'arm': a.arm, 'ts': time.time()}
        try:
            row.update(fn(it), error=None)
        except Exception as exc:
            row.update(output=None, wall_ms=None, api_ms=None, error=f'{type(exc).__name__}: {str(exc)[:200]}')
        with lock, out.open('a') as fh:
            fh.write(json.dumps(row) + '\n')
        print(a.arm, seq, it['cls'], it['id'], 'ERR' if row['error'] else round(row['wall_ms']), flush=True)
        if a.arm == 'local' and seq % 50 == 49:
            with meta.open('a') as fh:
                fh.write(json.dumps({'run_id': run_id, 'phase': f'seq{seq}', **gpu_sample()}) + '\n')

    with ThreadPoolExecutor(max_workers=jobs) as ex:
        list(ex.map(one, enumerate(todo)))
    if a.arm == 'local':
        with meta.open('a') as fh:
            fh.write(json.dumps({'run_id': run_id, 'phase': 'end', **gpu_sample()}) + '\n')


if __name__ == '__main__':
    main()
