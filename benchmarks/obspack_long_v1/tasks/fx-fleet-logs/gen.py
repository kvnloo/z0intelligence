"""Deterministic synthetic worker-fleet logs. `gen.py <workdir>` writes logs/; `gen.py --key` prints the answer key."""
import hashlib, json, random, sys
from pathlib import Path

def build():
    rng = random.Random(4242)
    comps = ['router', 'worker-gpu0', 'worker-gpu1', 'worker-gpu2', 'worker-gpu3', 'scheduler']
    pids = {c: 1000 + 37 * i for i, c in enumerate(comps)}
    files = {c: [] for c in comps}
    t = 1759028400.0  # 2025-09-28T03:00:00Z
    open_reqs, errors, latencies, oom_sessions, first_oom, or429 = {}, {}, {}, set(), None, 0
    restart_pid, cfg = None, None
    def ts(x):
        import datetime
        return datetime.datetime.fromtimestamp(x, datetime.timezone.utc).strftime('%Y-%m-%dT%H:%M:%S.') + f'{int(x * 1000) % 1000:03d}Z'
    def log(c, lvl, msg):
        files[c].append(f'{ts(t)} {lvl:5} {c}[{pids[c]}] {msg}')
        if lvl == 'ERROR':
            errors[c] = errors.get(c, 0) + 1
    for i in range(9000):
        t += rng.uniform(0.05, 0.9)
        rid = f'r{i:05d}'
        sid = f's{rng.randrange(400):03d}'
        w = f'worker-gpu{rng.randrange(4)}'
        if i == 5200:
            w = 'worker-gpu2'
        log('router', 'INFO', f'req={rid} session={sid} route={w} model=qwen2.5-7b tokens_in={rng.randrange(80, 4000)}')
        r = rng.random()
        if w == 'worker-gpu2' and i == 5200:
            log(w, 'ERROR', f'req={rid} session={sid} fatal: CUDA error: device-side assert triggered; worker exiting')
            pids[w] = 48113; restart_pid = pids[w]
            log(w, 'INFO', 'worker restarted cold; loading weights shard 1/4')
            continue
        if r < 0.012:
            log(w, 'ERROR', f'req={rid} session={sid} RuntimeError: CUDA out of memory. Tried to allocate {rng.randrange(200, 2000)} MiB')
            oom_sessions.add(sid); first_oom = first_oom or ts(t)
            continue
        if r < 0.03:
            prov = rng.choice(['openrouter', 'groq', 'together'])
            log('router', 'WARN', f'req={rid} session={sid} upstream provider={prov} status=429 retry_after={rng.randrange(1, 30)}s')
            or429 += prov == 'openrouter'
        if r < 0.045:
            log(w, 'ERROR', f'req={rid} session={sid} TimeoutError: decode step exceeded 30000ms')
            continue
        if r < 0.05:
            open_reqs[rid] = 1  # started, never completed
            log(w, 'DEBUG', f'req={rid} session={sid} prefill start')
            continue
        lat = int(rng.lognormvariate(6.2, 0.6))
        if i == 7311:
            lat = 48211
        latencies[rid] = lat
        log(w, 'DEBUG', f'req={rid} session={sid} prefill start')
        log(w, 'INFO', f'req={rid} session={sid} done latency_ms={lat} tokens_out={rng.randrange(10, 900)}')
        if rng.random() < 0.004:
            log('scheduler', 'ERROR', f'queue depth {rng.randrange(200, 900)} exceeds high-water mark; shedding session={sid}')
        if i % 1500 == 0 and i:
            cfg = hashlib.sha256(f'cfg{i}'.encode()).hexdigest()[:10]
            log('scheduler', 'INFO', f'config reload ok hash={cfg} workers=4')
        if rng.random() < 0.02:
            log('scheduler', 'INFO', f'heartbeat workers=4 queue={rng.randrange(0, 120)}')
    top = max(latencies, key=lambda k: latencies[k])
    key = {'q1': sum(errors.values()), 'q2': max(errors, key=errors.get), 'q3': top, 'q4': latencies[top],
           'q5': len(oom_sessions), 'q6': first_oom, 'q7': len(open_reqs), 'q8': restart_pid, 'q9': or429, 'q10': cfg}
    return files, key

if __name__ == '__main__':
    files, key = build()
    if sys.argv[1] == '--key':
        print(json.dumps(key)); sys.exit()
    out = Path(sys.argv[1]) / 'logs'; out.mkdir(parents=True, exist_ok=True)
    for c, lines in files.items():
        (out / f'{c}.log').write_text('\n'.join(lines) + '\n')
