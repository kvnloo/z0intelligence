#!/usr/bin/env python3
"""Require real dependent-proof rejection for each Law G bypass mutant.

Uses the existing state_fsm verdict parser; parser/setup failures never count.
Only scratch copies change. No artifacts are removed by this audit harness.
"""
from __future__ import annotations

import argparse
import json
import runpy
import shutil
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
FIELDS = ('expired', 'allowlisted', 'known_nonlive', 'no_symlink', 'archive_ready')
MUTANTS = ('unexpired', 'outside_allowlist', 'LIVE', 'symlink', 'unarchived_worktree')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--verdict', action='store_true')
    parser.add_argument('--out', type=Path)
    args = parser.parse_args()
    check = runpy.run_path(str(HERE.parent / 'state_fsm' / 'mutations.py'))['check']
    reference = (HERE / 'lease.bend').read_text()
    expression = 'Bool.and(expired, Bool.and(allowlisted, Bool.and(known_nonlive, Bool.and(no_symlink, archive_ready))))'
    rows = []
    with tempfile.TemporaryDirectory() as directory:
        work = Path(directory)
        for name in ('lease.bend', 'LAWS.bend', 'PROOF.bend'):
            shutil.copyfile(HERE / name, work / name)
        baseline = check(work, args.verdict)
        if baseline['proofs_check'] is True:
            for field, mutant in zip(FIELDS, MUTANTS):
                replacement = expression.replace(field, 'True{}')
                if reference.count(expression) != 1:
                    rows.append({'mutant': mutant, 'rejected': False, 'failure_kind': 'mutation_did_not_apply'})
                    continue
                (work / 'lease.bend').write_text(reference.replace(expression, replacement))
                rows.append({'mutant': mutant, **check(work, args.verdict)})
    result = {'reference': baseline, 'mutants': rows,
              'all_rejected': len(rows) == len(MUTANTS) and all(row['rejected'] for row in rows)}
    print(json.dumps(result, indent=2))
    if args.out:
        args.out.write_text(json.dumps(result, indent=2) + '\n')
    return 0 if baseline['proofs_check'] is True and result['all_rejected'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
