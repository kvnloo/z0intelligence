"""`z0int episodes build|split|baseline|sft-plan` -- the routing episode compiler CLI (z0int#143).

Reads the source home read-only (default $Z0INT_HOME, override --source-home) and writes ONLY under
$Z0INT_HOME/episodes/routing/ (refused inside a git worktree: nothing compiled from private history is committed):

  builds/<build_id>/episodes.jsonl + receipt.json   machine-readable dataset receipt (sources, code sha, counts)
  locks/eval_window.json                            the locked eval window (fixed by date, first split writes it)
  splits/<split_id>/{train,dev,eval,recent}.ids + manifest.json
  splits/<split_id>/baseline.json                   utilities of production / keyword / majority / cheapest
  splits/<split_id>/sft/<backend>/plan.json         backend-neutral SFT plan (no training unless the gate passes)

Printed output is counts only.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from . import episodes as E

RECEIPT_SCHEMA = E.RECEIPT_SCHEMA
SOURCE_STORES = ('receipts/decisions.jsonl', 'receipts/outcomes.jsonl', 'memory/events.jsonl')


def in_git_worktree(path: str | Path) -> bool:
    p = Path(os.path.abspath(path))
    return any((q / '.git').exists() for q in (p, *p.parents))


def out_root(home: str | Path | None = None) -> Path:
    from . import paths
    base = Path(home).expanduser().resolve() if home else paths.home()
    root = base / 'episodes' / 'routing'
    if in_git_worktree(root):
        raise SystemExit(f'refused: output root {root} is inside a git worktree (compiled history is never committed)')
    return root


def _fingerprint(home: Path) -> dict[str, Any]:
    stores = list(SOURCE_STORES)
    for d in sorted((home / 'state').glob('*/')) if (home / 'state').is_dir() else []:
        for name in ('opportunities.jsonl', 'outcomes.jsonl', 'outcomes_verified.jsonl', 'shadow_decisions.jsonl'):
            stores.append(f'state/{d.name}/{name}')
    out = {}
    for rel in stores:
        p = home / rel
        if not p.is_file():
            continue
        h, n, lines = hashlib.sha256(), 0, 0
        with p.open('rb') as fh:
            for chunk in iter(lambda: fh.read(1 << 20), b''):
                h.update(chunk)
                n += len(chunk)
                lines += chunk.count(b'\n')
        out[rel] = {'bytes': n, 'lines': lines, 'sha256': h.hexdigest()}
    return out


def _code() -> dict[str, Any]:
    here = Path(__file__).resolve().parent
    env = dict(os.environ, GIT_OPTIONAL_LOCKS='0')
    try:
        sha = subprocess.run(['git', '-C', str(here), 'rev-parse', 'HEAD'], capture_output=True, text=True,
                             env=env, timeout=10).stdout.strip() or None
        dirty = bool(subprocess.run(['git', '-C', str(here), 'status', '--porcelain', '--', '.'], capture_output=True,
                                    text=True, env=env, timeout=10).stdout.strip())
    except (OSError, subprocess.TimeoutExpired):
        sha, dirty = None, None
    return {'git_sha': sha, 'dirty': dirty, 'compiler_version': E.COMPILER_VERSION}


def _write(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, indent=1, sort_keys=True) + '\n')


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    with path.open(encoding='utf-8') as fh:
        return [json.loads(line) for line in fh if line.strip()]


def _latest(root: Path, kind: str, given: str | None) -> str:
    if given:
        return given
    p = root / f'LATEST_{kind.upper()}'
    if not p.is_file():
        raise SystemExit(f'no {kind} yet: run `z0int episodes {"build" if kind == "build" else "split"}` first')
    return p.read_text().strip()


# ----------------------------------------------------------------------------- build
def build(source_home: Path, root: Path, *, host: str | None = None, agentsview: str | None = None) -> dict[str, Any]:
    host = host or socket.gethostname()
    fp = _fingerprint(source_home)
    code = _code()
    eps, stats = E.compile_episodes(source_home, host=host, agentsview=agentsview)
    body = ''.join(json.dumps(e, sort_keys=True) + '\n' for e in eps)
    build_id = E._h({'sources': fp, 'code': code, 'host': host, 'episodes': hashlib.sha256(body.encode()).hexdigest()},
                    16)
    d = root / 'builds' / build_id
    d.mkdir(parents=True, exist_ok=True)
    (d / 'episodes.jsonl').write_text(body)
    receipt = {'schema': RECEIPT_SCHEMA, 'build_id': build_id, 'host': host,
               'built_at': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()), 'code': code,
               'episode_schema': E.SCHEMA, 'source_home': str(source_home).replace(str(Path.home()), '~'),
               'sources': fp, 'episodes_sha256': hashlib.sha256(body.encode()).hexdigest(),
               'summary': E.summarize(eps), 'stats': stats,
               'privacy': 'no request/prompt/output text; content as sha256; ids hashed except ledger event ids',
               'authority': 'derived projection; canonical stores stay EventLog + receipts + state (read-only here)',
               'outputs': {'episodes': f'builds/{build_id}/episodes.jsonl'}}
    _write(d / 'receipt.json', receipt)
    (root / 'LATEST_BUILD').write_text(build_id + '\n')
    return receipt


# ----------------------------------------------------------------------------- split
def do_split(root: Path, *, build_id: str | None, eval_start: str | None, eval_end: str | None,
             dev_days: int) -> dict[str, Any]:
    from .episode_split import resolve_lock, split
    bid = _latest(root, 'build', build_id)
    eps = _read_jsonl(root / 'builds' / bid / 'episodes.jsonl')
    lock = resolve_lock(root / 'locks' / 'eval_window.json', eval_start, eval_end)
    ids, man = split(eps, eval_start=lock['eval_start'], eval_end=lock['eval_end'], dev_days=dev_days)
    split_id = E._h({'build': bid, 'lock': [lock['eval_start'], lock['eval_end']], 'dev_days': dev_days,
                     'ids': man['ids_sha256']}, 16)
    d = root / 'splits' / split_id
    for s, xs in ids.items():
        d.mkdir(parents=True, exist_ok=True)
        (d / f'{s}.ids').write_text(''.join(x + '\n' for x in xs))
    man.update(split_id=split_id, build_id=bid, dev_days=dev_days, lock=lock,
               created_at=time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()))
    _write(d / 'manifest.json', man)
    (root / 'LATEST_SPLIT').write_text(split_id + '\n')
    return man


def _load_split(root: Path, split_id: str | None) -> tuple[str, dict[str, Any], dict[str, list[dict[str, Any]]]]:
    sid = _latest(root, 'split', split_id)
    d = root / 'splits' / sid
    man = json.loads((d / 'manifest.json').read_text())
    eps = {e['episode_id']: e for e in _read_jsonl(root / 'builds' / man['build_id'] / 'episodes.jsonl')}
    by = {s: [eps[i] for i in (d / f'{s}.ids').read_text().split()] for s in ('train', 'dev', 'eval', 'recent')}
    return sid, man, by


def do_baseline(root: Path, *, split_id: str | None) -> dict[str, Any]:
    from .episode_eval import baseline_report
    sid, man, by = _load_split(root, split_id)
    rep = baseline_report(by)
    rep.update(split_id=sid, build_id=man['build_id'], locked_eval_window=man['locked_eval_window'])
    _write(root / 'splits' / sid / 'baseline.json', rep)
    return rep


def do_sft_plan(root: Path, home: Path, *, split_id: str | None, backend: str) -> dict[str, Any]:
    from .sft_adapter import plan
    sid, man, by = _load_split(root, split_id)
    receipt = json.loads((root / 'builds' / man['build_id'] / 'receipt.json').read_text())
    p, body = plan(backend, by['train'], home=home, dataset_receipt=receipt, split_manifest=man)
    d = root / 'splits' / sid / 'sft' / backend
    _write(d / 'plan.json', p)
    (d / 'train_examples.jsonl').write_text(body)
    return p


# ----------------------------------------------------------------------------- CLI
def _main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog='z0int episodes', description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--home', type=Path, default=None, help='output Z0INT_HOME (default $Z0INT_HOME or ~/.z0int)')
    sub = ap.add_subparsers(dest='cmd', required=True)
    b = sub.add_parser('build', help='compile canonical routing episodes (read-only over the source home)')
    b.add_argument('--source-home', type=Path, default=None, help='store to read (default: the output home)')
    b.add_argument('--agentsview', default=None, help='AgentsView data dir for counts-only coverage (mode=ro)')
    b.add_argument('--host', default=None)
    s = sub.add_parser('split', help='temporal + lineage-aware split with the locked eval window')
    s.add_argument('--build', default=None)
    s.add_argument('--eval-start', default=None, help='YYYY-MM-DD (first split only; then locked)')
    s.add_argument('--eval-end', default=None, help='YYYY-MM-DD, exclusive')
    s.add_argument('--dev-days', type=int, default=7)
    bl = sub.add_parser('baseline', help='utility of production / keyword / majority / cheapest per family')
    bl.add_argument('--split', default=None)
    sp = sub.add_parser('sft-plan', help='backend-neutral SFT plan (data gate; remote needs owner approval)')
    sp.add_argument('--split', default=None)
    sp.add_argument('--backend', default='stub', choices=('stub', 'unsloth-lora', 'tinker'))
    for p in (b, s, bl, sp):
        p.add_argument('--json', action='store_true')
    args = ap.parse_args(sys.argv[1:] if argv is None else argv)
    from . import paths
    home = args.home.expanduser().resolve() if args.home else paths.home()
    root = out_root(home)
    if args.cmd == 'build':
        rep = build((args.source_home or home).expanduser().resolve(), root, host=args.host,
                    agentsview=args.agentsview)
        view = {'build_id': rep['build_id'], 'summary': rep['summary'], 'dedup': rep['stats']['dedup'],
                'lineage': rep['stats']['lineage'], 'eventlog': rep['stats']['eventlog'],
                'agentsview': rep['stats'].get('agentsview')}
    elif args.cmd == 'split':
        from .episode_split import LockConflict
        try:
            rep = do_split(root, build_id=args.build, eval_start=args.eval_start, eval_end=args.eval_end,
                           dev_days=args.dev_days)
        except LockConflict as exc:
            print(f'refused: {exc}')
            return 2
        view = {k: rep[k] for k in ('split_id', 'locked_eval_window', 'dev_window', 'counts', 'removed',
                                    'residual_overlap')}
    elif args.cmd == 'baseline':
        rep = do_baseline(root, split_id=args.split)
        view = rep
    else:
        rep = do_sft_plan(root, home, split_id=args.split, backend=args.backend)
        view = {k: rep[k] for k in ('backend', 'remote', 'available', 'train_allowed', 'reason', 'examples',
                                    'sufficiency')}
    print(json.dumps(view, indent=1, sort_keys=True, default=str))
    return 0


if __name__ == '__main__':
    raise SystemExit(_main())
