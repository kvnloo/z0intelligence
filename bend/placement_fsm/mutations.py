#!/usr/bin/env python3
"""Require reference proof success and semantic rejection of each placement mutant."""
import argparse
import json
import runpy
import shutil
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
check = runpy.run_path(str(HERE.parent / 'root_cause_fsm/mutations.py'))['check']


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--verdict', action='store_true')
    args = parser.parse_args()
    source = (HERE / 'placement.bend').read_text()
    mutants = {
        'unavailable_pass': ('case False{}:\n      Unknown{}', 'case False{}:\n      Pass{}'),
        'unscoped_pass': ('case False{}:\n              Violation{}', 'case False{}:\n              Pass{}'),
        'scoped_violation': ('case True{}:\n              Pass{}', 'case True{}:\n              Violation{}'),
        'non_agent_violation': ('case False{}:\n          Pass{}', 'case False{}:\n          Violation{}'),
    }
    with tempfile.TemporaryDirectory() as temp:
        work = Path(temp)
        for name in ('placement.bend', 'LAWS.bend', 'PROOF.bend'):
            shutil.copy(HERE / name, work / name)
        baseline = check(work, args.verdict)
        rows = []
        if baseline['proofs_check'] is True:
            for name, (old, new) in mutants.items():
                assert source.count(old) == 1
                (work / 'placement.bend').write_text(source.replace(old, new))
                rows.append({'mutation': name, **check(work, args.verdict)})
    receipt = dict(reference=baseline, mutants=rows, expected=len(mutants), rejected=sum(row['rejected'] for row in rows))
    print(json.dumps(receipt, indent=2))
    return 0 if baseline['proofs_check'] is True and receipt['rejected'] == len(mutants) else 1


if __name__ == '__main__':
    raise SystemExit(main())
