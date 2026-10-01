"""Run the frozen tool-calling item set against each arm on groot (see PREREG.md).

The models run on groot in a bench-only llama.cpp router (port 11541, --models-max 1),
started with the same serving flags as z0-farm-llama so the farm baselines are comparable.
The farm service itself is never reconfigured or restarted. This script only sends HTTP.

  python benchmarks/tool_calling_portfolio/run.py [--arms ...] [--smoke]
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from common import PRIVATE, load_items  # noqa: E402

BENCH = 'http://100.113.138.100:11541'
RAW = PRIVATE / 'raw_v0.jsonl'
META = PRIVATE / 'serving_v0.json'

# Execution order fixed by PREREG. id -> (router model id, role)
ARMS = {
    'functiongemma_270m': 'functiongemma-270m-bf16',
    'hammer2.1_3b': 'hammer2.1-3b-q4km',
    'qwen3.5_4b': 'qwen3.5-4b-q4km',
    'hammer2.1_7b': 'hammer2.1-7b-q4km',
    'qwen3_8b': 'qwen3-8b-q4km',
    'qwen3.5_9b': 'qwen3.5-9b-q4km',
    'qwen3_14b': 'qwen3-14b-q4km',
    'nemotron_orchestrator_8b': 'nemotron-orchestrator-8b-q4km',
}


def post(path, body, timeout=300):
    req = urllib.request.Request(BENCH + path, data=json.dumps(body).encode(), headers={'Content-Type': 'application/json'})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r)


def gpu():
    """Total used MiB and MiB held by the bench router's processes (shared GPU: Ollama/farm may hold some)."""
    cmd = ("nvidia-smi --query-gpu=memory.used --format=csv,noheader,nounits; echo ---; "
           "nvidia-smi --query-compute-apps=pid,used_memory --format=csv,noheader,nounits | while IFS=', ' read p m; do "
           "grep -qa toolcall/router /proc/$p/cmdline 2>/dev/null && echo $m; done")
    try:
        out = subprocess.run(['ssh', '0', cmd], capture_output=True, text=True, timeout=30).stdout
        total, _, mine = out.partition('---')
        return {'total_mib': int(total.split()[0]), 'bench_mib': sum(int(x) for x in mine.split())}
    except Exception as exc:  # noqa: BLE001
        return {'error': f'{type(exc).__name__}'}


def chat(model, item, max_tokens=1024):
    body = {'model': model, 'messages': item['messages'], 'tools': item['tools'], 'tool_choice': 'auto',
            'parallel_tool_calls': True, 'temperature': 0, 'seed': 0, 'max_tokens': max_tokens,
            'chat_template_kwargs': {'enable_thinking': False}}
    t = time.perf_counter()
    try:
        resp = post('/v1/chat/completions', body)
        err = None
    except urllib.error.HTTPError as exc:
        resp, err = None, f'HTTP {exc.code}: {exc.read()[:300]!r}'
    except Exception as exc:  # noqa: BLE001
        resp, err = None, f'{type(exc).__name__}: {exc}'[:300]
    return resp, err, (time.perf_counter() - t) * 1000


def unload(model):
    try:
        post('/models/unload', {'model': model}, timeout=60)
    except Exception:  # noqa: BLE001
        pass


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--arms', nargs='+', default=list(ARMS))
    ap.add_argument('--smoke', action='store_true', help='one item per suite/category, printed, not saved')
    args = ap.parse_args()
    items = load_items()
    if args.smoke:
        pick = {}
        for it in items:
            pick.setdefault((it['suite'], it['category']), it)
        items = list(pick.values())
    meta = json.loads(META.read_text()) if META.exists() and not args.smoke else {}
    done = set()
    if RAW.exists() and not args.smoke:
        done = {(r['arm'], r['id']) for r in map(json.loads, RAW.read_text().splitlines())}
    for arm in args.arms:
        model = ARMS[arm]
        for other in ARMS.values():
            unload(other)
        time.sleep(3)
        before = gpu()
        warm = {'messages': [{'role': 'user', 'content': 'Say OK.'}], 'tools': []}
        body_t = time.perf_counter()
        _, werr, _ = chat(model, {**warm, 'tools': [{'type': 'function', 'function': {
            'name': 'noop', 'description': 'unused', 'parameters': {'type': 'object', 'properties': {}}}}]}, 8)
        cold_ms = (time.perf_counter() - body_t) * 1000
        loaded = gpu()
        print(f'== {arm} cold {cold_ms:.0f} ms err={werr} gpu {before} -> {loaded}', flush=True)
        peak = loaded.get('bench_mib', 0)
        with (open(RAW, 'a') if not args.smoke else open('/dev/null', 'w')) as fh:
            for n, it in enumerate(items):
                if (arm, it['id']) in done:
                    continue
                resp, err, wall = chat(model, it)
                msg = ((resp or {}).get('choices') or [{}])[0].get('message') if resp else None
                row = {'arm': arm, 'model': model, 'id': it['id'], 'suite': it['suite'], 'category': it['category'],
                       'error': err, 'wall_ms': round(wall, 1), 'message': msg,
                       'finish_reason': ((resp or {}).get('choices') or [{}])[0].get('finish_reason') if resp else None,
                       'timings': (resp or {}).get('timings'), 'usage': (resp or {}).get('usage')}
                if args.smoke:
                    print(it['id'], err, json.dumps(msg)[:600], flush=True)
                else:
                    fh.write(json.dumps(row) + '\n')
                    fh.flush()
                if n % 40 == 39:
                    g = gpu()
                    peak = max(peak, g.get('bench_mib', 0))
                    print(f'  {arm} {n + 1}/{len(items)} gpu {g}', flush=True)
        end = gpu()
        peak = max(peak, end.get('bench_mib', 0))
        if not args.smoke:
            meta[arm] = {'model': model, 'cold_start_ms': round(cold_ms, 1), 'warm_error': werr, 'gpu_before': before,
                         'gpu_loaded': loaded, 'gpu_end': end, 'bench_peak_mib_sampled': peak,
                         'measured_at': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}
            META.write_text(json.dumps(meta, indent=1))
        unload(model)
    print('done', flush=True)


if __name__ == '__main__':
    main()
