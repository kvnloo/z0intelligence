#!/usr/bin/env python3
"""Compare the complete finite Law F domain against the actual Bend evaluator."""
import argparse
import itertools
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'src'))
from z0int.fsm.consultation_law import evaluate


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', type=Path)
    args = parser.parse_args()
    fields = ('available', 'applicable', 'consulted')
    cases = [dict(zip(fields, values)) for values in itertools.product((None, False, True), repeat=3)]
    for key in fields:
        for invalid in (0, 1, 'true', [], {}):
            cases.append({**dict.fromkeys(fields, True), key: invalid})
    def evidence(value):
        return 'S.SBool{True{}}' if value is True else 'S.SBool{False{}}' if value is False else 'S.SAbsent{}'
    expressions = ['code(F.f(' + ', '.join(evidence(row[key]) for key in fields) + '))' for row in cases]
    source = '''import Base
import ./consultation.bend as F
import ../state_fsm/state.bend as S

def code(v: S.Verdict) -> Nat:
  match v:
    case S.SUnknown{}:
      0n
    case S.SPass{}:
      1n
    case S.SViolation{}:
      2n

def main() -> List<&2, Nat>:
  [''' + ',\n   '.join(expressions) + ']\n'
    binary = os.environ.get('BEND', str(Path.home() / '.bend/bin/bend'))
    env = {**os.environ, 'PATH': '/usr/bin:' + os.environ.get('PATH', '') + ':' + str(Path.home() / '.elan/bin')}
    with tempfile.TemporaryDirectory() as temporary:
        target = Path(temporary)
        shutil.copytree(ROOT / 'bend/state_fsm', target / 'state_fsm')
        shutil.copytree(ROOT / 'bend/consultation_fsm', target / 'consultation_fsm')
        (target / 'consultation_fsm/parity.bend').write_text(source)
        process = subprocess.run([binary, 'parity.bend'], cwd=target / 'consultation_fsm', capture_output=True, text=True, timeout=600, env=env)
    codes = {'unknown': '0n', 'pass': '1n', 'violation': '2n'}
    expected = '[' + ', '.join(codes[evaluate(row)] for row in cases) + ']'
    actual = next((line for line in process.stdout.splitlines() if line.startswith('[')), '')
    result = {'cases': len(cases), 'equal': process.returncode == 0 and expected == actual, 'returncode': process.returncode, 'actual': actual, 'expected': expected, 'stderr': process.stderr}
    print(json.dumps(result, sort_keys=True))
    if args.out:
        args.out.write_text(json.dumps(result, sort_keys=True, indent=2) + '\n')
    return 0 if result['equal'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
