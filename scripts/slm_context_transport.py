#!/usr/bin/env python3
"""JSONL decisions via existing MBP llama.cpp; no model downloads or dependencies.

Run ONLY inside a fleet-build MBP admission. Labels/experiment metadata never enter
model prompts. Probabilities are the grammar-conditioned single-token distribution,
not calibrated correctness. No missing probabilities or token counts are invented.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import socket
import subprocess
import sys
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

LETTERS = 'ABCDEFGHIJKLMNOP'
SYSTEM = ('Apply the supplied criterion to the supplied evidence. Choose exactly one listed option. '
          'Respond with only its uppercase letter, with no explanation or reasoning.')
MODELS = {
    'qwen25-q6k': ('Qwen2.5-0.5B-Q6_K.gguf', 'Qwen/Qwen2.5-0.5B-Instruct',
                    '7ae557604adf67be50417f59c2c2f167def9a775',
                    '857693b7312bb54829a6302d4f08e3639816fc25d273314cfae9b80e1e8e2964'),
    'qwen3-q6k': ('Qwen3-0.6B-Q6_K.gguf', 'Qwen/Qwen3-0.6B', None,
                  '5cb0ef800c7ab448e60399949f484ca077582629ddac4ced9e3958676c9e0eec'),
}


def sha256_file(path):
    h = hashlib.sha256()
    with path.open('rb') as f:
        for chunk in iter(lambda: f.read(1048576), b''):
            h.update(chunk)
    return h.hexdigest()


def post(base, endpoint, payload):
    req = Request(base + endpoint, data=json.dumps(payload, ensure_ascii=False, allow_nan=False).encode(),
                  headers={'Content-Type': 'application/json'})
    with urlopen(req, timeout=300) as response:
        return json.load(response)


def prompt_for(row, model_key):
    # Deliberate allowlist: no request id, labels, split, or experiment metadata.
    state, question, options = row['state'], row['question'], row['options']
    if not isinstance(question, str) or not question or not 2 <= len(options) <= 16:
        raise ValueError('Require nonempty question and 2-16 options')
    if len({o['id'] for o in options}) != len(options):
        raise ValueError('Option IDs must be unique')
    payload = {'evidence': state, 'criterion': question,
               'options': [{'letter': LETTERS[i], 'id': o['id'], 'description': o['description']}
                           for i, o in enumerate(options)]}
    user = json.dumps(payload, ensure_ascii=False, allow_nan=False)
    text = ('<|im_start|>system\n' + SYSTEM + '<|im_end|>\n'
            '<|im_start|>user\n' + user + '<|im_end|>\n<|im_start|>assistant\n')
    if model_key == 'qwen3-q6k':
        text += '<think>\n\n</think>\n\n'
    return text


def tokenize(base, text):
    result = post(base, '/tokenize', {'content': text, 'add_special': False, 'parse_special': True})
    return result['tokens']


def score(base, row, model_key, context):
    started = time.perf_counter()
    prompt = prompt_for(row, model_key)
    tokens = tokenize(base, prompt)
    if len(tokens) + 1 > context:
        raise ValueError(f'Prompt {len(tokens)} plus answer exceeds context {context}; no truncation')
    letters = list(LETTERS[:len(row['options'])])
    slots = []
    for letter in letters:
        ids = tokenize(base, letter)
        if len(ids) != 1 or tokenize(base, prompt + letter) != tokens + ids:
            raise ValueError(f'Slot {letter} is not one exact boundary-stable token')
        slots.append(ids[0])
    grammar = 'root ::= ' + ' | '.join(json.dumps(x) for x in letters)
    request = {'prompt': tokens, 'n_predict': 1, 'temperature': 1.0, 'top_k': 0,
               'top_p': 1.0, 'min_p': 0.0, 'typical_p': 1.0, 'repeat_penalty': 1.0,
               'grammar': grammar, 'n_probs': 256, 'post_sampling_probs': True,
               'cache_prompt': False, 'seed': 0, 'stream': False}
    infer_start = time.perf_counter()
    raw = post(base, '/completion', request)
    infer_ms = (time.perf_counter() - infer_start) * 1000
    entries = raw.get('completion_probabilities') or []
    if len(entries) != 1:
        raise ValueError('Expected one generated-token probability record: ' + json.dumps(raw))
    entry = entries[0]
    probs = {}
    if 'top_logprobs' in entry:
        for item in entry['top_logprobs']:
            if item.get('id') in slots:
                probs[slots.index(item['id'])] = math.exp(item['logprob'])
    elif 'top_probs' in entry or 'probs' in entry:
        for item in entry.get('top_probs', entry.get('probs', [])):
            token_id = item.get('id', item.get('tok_id'))
            text = item.get('tok_str', item.get('token'))
            if token_id in slots:
                probs[slots.index(token_id)] = item['prob']
            elif text in letters:
                probs[letters.index(text)] = item['prob']
    if set(probs) != set(range(len(letters))):
        raise ValueError('Runtime omitted option probabilities: ' + json.dumps(raw))
    values = [probs[i] for i in range(len(letters))]
    if any(not math.isfinite(p) or p < 0 for p in values) or sum(values) <= 0:
        raise ValueError('Runtime option probabilities are not finite and nonzero')
    total = sum(values)
    values = [p / total for p in values]
    best = max(range(len(values)), key=values.__getitem__)
    entropy = -sum(p * math.log(p) for p in values if p > 0)
    _, model, revision, model_hash = MODELS[model_key]
    return {'id': row.get('id'), 'ok': True, 'choice': row['options'][best]['id'],
            'probabilities': {o['id']: p for o, p in zip(row['options'], values)},
            'option_probability_mass': total,
            'confidence': 1 - entropy / math.log(len(values)),
            'confidence_definition': 'one minus normalized option entropy; uncalibrated',
            'prompt_tokens': len(tokens), 'prompt_sha256': hashlib.sha256(prompt.encode()).hexdigest(),
            'latency_ms': (time.perf_counter() - started) * 1000, 'inference_latency_ms': infer_ms,
            'model': model, 'revision': revision, 'model_sha256': model_hash,
            'quantization': 'Q6_K', 'runtime_revision': '21ec3071467b168991e223f2e5b09e571a11eb96',
            'readout': 'post-sampling probabilities restricted and renormalized to exact option token IDs; grammar-constrained, temperature=1',
            'prompt_version': 'direct-options-v2-option-ids-qwen-raw', 'raw_completion': raw}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--model', required=True, choices=MODELS)
    p.add_argument('--runtime-root', type=Path, default=Path.home() / 'slm-opt')
    p.add_argument('--context', type=int, default=4096)
    p.add_argument('--input', type=Path)
    p.add_argument('--output', type=Path)
    p.add_argument('--server-log', type=Path, default=Path('transport-server.log'))
    p.add_argument('--smoke', action='store_true', help='One non-quality transport availability request')
    a = p.parse_args()
    if os.uname().nodename != 'mbp':
        p.error('Execute only on admitted MBP via fleet-build, never Groot')
    model_path = a.runtime_root / 'models' / MODELS[a.model][0]
    if sha256_file(model_path) != MODELS[a.model][3]:
        raise ValueError('Model checksum differs from recorded finalist artifact')
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
    base = f'http://127.0.0.1:{port}'
    command = [str(a.runtime_root / 'runtime/build/bin/llama-server'), '-m', str(model_path),
               '-t', '4', '-tb', '4', '-b', '128', '-ub', '128', '-c', str(a.context),
               '-ngl', '0', '-lm', 'mmap', '--host', '127.0.0.1', '--port', str(port),
               '--parallel', '1', '--no-webui']
    log = a.server_log.open('w')
    proc = subprocess.Popen(command, stdout=log, stderr=subprocess.STDOUT)
    try:
        deadline = time.monotonic() + 180
        while True:
            if proc.poll() is not None:
                raise RuntimeError(f'Server exited {proc.returncode}; see {a.server_log}')
            try:
                with urlopen(base + '/health', timeout=2) as r:
                    if r.status == 200:
                        break
            except (URLError, HTTPError, TimeoutError):
                pass
            if time.monotonic() > deadline:
                raise TimeoutError('Model server readiness deadline expired')
            time.sleep(0.25)
        source = a.input.open() if a.input else sys.stdin
        sink = a.output.open('x') if a.output else sys.stdout
        try:
            requests = [json.dumps({'id': 'transport-smoke', 'state': {'value': 2},
                                   'question': 'Which option equals the supplied value?',
                                   'options': [{'id': 'two', 'description': '2'},
                                               {'id': 'three', 'description': '3'}]})] if a.smoke else source
            failed = False
            for line in requests:
                if not line.strip():
                    continue
                row = json.loads(line)
                try:
                    result = score(base, row, a.model, a.context)
                except Exception as e:
                    failed = True
                    result = {'id': row.get('id'), 'ok': False, 'model': MODELS[a.model][1],
                              'revision': MODELS[a.model][2], 'error': str(e),
                              'choice': None, 'probabilities': None, 'confidence': None,
                              'prompt_tokens': None, 'latency_ms': None}
                sink.write(json.dumps(result, ensure_ascii=False, allow_nan=False) + '\n')
                sink.flush()
            return 1 if failed else 0
        finally:
            if source is not sys.stdin:
                source.close()
            if sink is not sys.stdout:
                sink.close()
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
        log.close()


if __name__ == '__main__':
    raise SystemExit(main())
