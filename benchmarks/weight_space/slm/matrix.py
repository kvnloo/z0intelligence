"""Bounded SLM matrix: 3 targets x 5 editors, 4 feedback passes each, then held-out evaluation.

Runs one cell at a time; all GPU jobs share a lock and wait for memory. Resume skips completed cells.
  bws-env-exec --scope hermes -- <deepseek env wrapper> .venv-wsp-gpu/bin/python .../matrix.py --run-id ID
Never changes services, the OOM guard, or other users' GPU jobs.
"""
import argparse
import json
import subprocess
import sys
import os
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import common as C


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--run-id', required=True)
    ap.add_argument('--passes', type=int, default=4)
    ap.add_argument('--editors', default='astra,argon,deepseek,opus,fable')
    ap.add_argument('--targets', default=','.join(C.TARGETS))
    a = ap.parse_args()
    root = C.STATE / a.run_id
    root.mkdir(parents=True, exist_ok=True)
    events = root / 'matrix.jsonl'
    cells = [(t,e) for t in a.targets.split(',') for e in a.editors.split(',')]
    for target, editor in cells:
        cell = root / f'{target}__{editor}'
        state = cell / 'state.json'
        if state.exists() and json.loads(state.read_text()).get('done'):
            continue
        cmd = [sys.executable, str(HERE/'session.py'), '--run-id', a.run_id, '--target', target,
               '--editor', editor, '--passes', str(a.passes), '--effort', 'high']
        # Admission precedes subprocess imports as well as GPU work: importing the client/model stacks can
        # itself exceed the host's headroom. After a bounded wait, exit with resumable capacity status.
        need = int(os.environ.get('WSP_ADMIT_MB', 1500 if os.environ.get('WSP_BACKEND') == 'modal' else 6000))
        mem = 0
        for _ in range(360):
            mem = next(int(ln.split()[1]) // 1024 for ln in Path('/proc/meminfo').read_text().splitlines()
                       if ln.startswith('MemAvailable:'))
            if mem >= need:
                break
            time.sleep(10)
        if mem < need:
            print(f'CAPACITY_BLOCKED: <{need} MB available for one hour; resume this matrix later', flush=True)
            return 75
        print(time.strftime('%F %T'), 'START', target, editor, flush=True)
        with open(root/f'{target}__{editor}.log', 'a') as f:
            try:
                p = subprocess.run(cmd, check=False, stdout=f, stderr=subprocess.STDOUT, timeout=8*3600)
                code = p.returncode
            except subprocess.TimeoutExpired:
                code = 124
        with open(events,'a') as f:
            f.write(json.dumps({'time':time.strftime('%FT%T%z'),'target':target,'editor':editor,'exit_code':code})+'\n')
        print(time.strftime('%F %T'), 'END', target, editor, code, flush=True)
        if code in (-9, -15, 137, 143):
            print('CAPACITY_ABORT: signalled cell; stopping instead of cascading through all remaining cells', flush=True)
            return 75
    cmd = [sys.executable,str(HERE/'evaluate.py'),'--run-id',a.run_id]
    with open(root/'evaluation.log','a') as f:
        p = subprocess.run(cmd,check=False,stdout=f,stderr=subprocess.STDOUT,timeout=24*3600)
    print('EVALUATION exit',p.returncode,flush=True)
    return p.returncode


if __name__ == '__main__':
    raise SystemExit(main())
