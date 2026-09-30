"""Programmatic verifiers for claude-code-savings-v1 repo tasks (independent of the arm under test).

Kinds:
  pytest       restore the listed test files from the pinned source (so edits to tests cannot pass
               the check), then run pytest on ``targets`` (``{task}/...`` = hidden tests in the task dir).
  answer_text  compare a file in the work dir with ``expect`` after ``normalize``.
  answer_json  every key in ``expect`` must match the JSON file (numbers compared as floats,
               lists compared exactly unless ``unordered`` lists the key).
  argv         legacy: run an argv (``python3`` -> bench venv python); exit 0 = verified.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PY = str(ROOT / '.venv' / 'bin' / 'python')
WORKSPACE = Path(os.environ.get('Z0_BENCH_WORKSPACE', '~/workspace')).expanduser()


def source_repo(name: str) -> Path:
    return ROOT if name == 'z0intelligence' else WORKSPACE / name


def _sub(s: str, task_dir: Path, work: Path) -> str:
    return s.replace('{task}', str(task_dir)).replace('{work}', str(work))


def _norm(text: str, mode: str):
    if mode == 'strip':
        return text.strip()
    if mode == 'tokens':
        return text.split()
    if mode == 'tokens_sorted':
        return sorted(text.split())
    if mode == 'lower_tokens':
        return text.lower().split()
    raise ValueError(mode)


def _eq(a, e) -> bool:
    if isinstance(e, bool) or isinstance(a, bool):
        return a == e
    if isinstance(e, (int, float)) and isinstance(a, (int, float)):
        return float(a) == float(e)
    return a == e


def verify(task: dict, work: Path, env: dict | None = None) -> tuple[bool, str]:
    c = task['check']
    task_dir = Path(task['_dir'])
    env = dict(env or os.environ)
    if isinstance(c, list):  # legacy argv form
        c = {'kind': 'argv', 'argv': c}
    kind = c['kind']
    if kind == 'pytest':
        src = task['source']
        pre = src['prefix'].strip('/') + '/' if src.get('prefix') else ''
        for rel in c.get('restore', []):  # work-relative; strip source.prefix to get the repo path
            repo_rel = rel[len(pre):] if pre and rel.startswith(pre) else rel
            blob = subprocess.run(['git', '-C', str(source_repo(src['repo'])), 'show', f"{src['sha']}:{repo_rel}"],
                                  capture_output=True, check=True).stdout
            (work / rel).parent.mkdir(parents=True, exist_ok=True)
            (work / rel).write_bytes(blob)
        pp = os.pathsep.join(str(work / p) for p in c.get('pythonpath', ['.']))
        argv = [PY, '-m', 'pytest', '-q', '-p', 'no:cacheprovider', f'--rootdir={work}',
                *[_sub(t, task_dir, work) for t in c['targets']]]
        r = subprocess.run(argv, cwd=work, capture_output=True, text=True, timeout=c.get('timeout', 300),
                           env={**env, 'PYTHONPATH': pp, 'PYTHONDONTWRITEBYTECODE': '1'})
        return r.returncode == 0, (r.stdout + r.stderr)[-600:]
    if kind == 'answer_text':
        f = work / c['file']
        if not f.is_file():
            return False, 'missing ' + c['file']
        got = _norm(f.read_text(errors='replace'), c.get('normalize', 'strip'))
        want = _norm(c['expect'], c.get('normalize', 'strip'))
        return got == want, f'got={got!r}'[:300]
    if kind == 'answer_json':
        f = work / c['file']
        try:
            a = json.loads(f.read_text())
        except (OSError, ValueError) as exc:
            return False, f'unreadable: {exc}'
        unordered = set(c.get('unordered', []))
        bad = []
        for k, e in c['expect'].items():
            v = a.get(k) if isinstance(a, dict) else None
            if k in unordered and isinstance(v, list) and isinstance(e, list):
                ok = sorted(map(str, v)) == sorted(map(str, e))
            else:
                ok = _eq(v, e)
            if not ok:
                bad.append(k)
        return not bad, f'bad={bad}'
    if kind == 'argv':
        argv = [PY if a == 'python3' else _sub(a, task_dir, work) for a in c['argv']]
        r = subprocess.run(argv, cwd=work, capture_output=True, text=True, timeout=300,
                           env={**env, 'PYTHONPATH': str(work / 'src')})
        return r.returncode == 0, (r.stdout + r.stderr)[-600:]
    raise ValueError(kind)


if __name__ == '__main__':
    t = json.loads(Path(sys.argv[1]).read_text())
    t['_dir'] = str(Path(sys.argv[1]).parent)
    ok, why = verify(t, Path(sys.argv[2]))
    print(ok, why)
    sys.exit(0 if ok else 1)
