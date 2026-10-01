"""Deterministic synthetic CI run log. `gen.py <workdir>` writes ci/; `gen.py --key` prints the answer key."""
import json, random, sys
from pathlib import Path

MODS = ['routing', 'posture', 'receipts', 'packet', 'cache', 'billing', 'scheduler', 'adapters', 'cli', 'storage', 'auth', 'metrics']

def build():
    rng = random.Random(9090)
    L = []; t = 0.0
    def step(name):
        L.append(f'##[group]Run {name}')
    def end(code):
        L.append(f'##[endgroup] exit_code={code}')
    step('actions/checkout@v4'); L += [f'Fetching ref {rng.getrandbits(64):016x}', 'HEAD is now at 3f9c2d1 merge: posture routing'] ; end(0)
    step('pip install -e .[test]')
    pkgs = {}
    for p in ['numpy', 'pydantic', 'httpx', 'pytest', 'pytest-xdist', 'rich', 'orjson', 'anyio', 'uvloop', 'sqlalchemy', 'jsonschema', 'tenacity']:
        v = f'{rng.randrange(1, 3)}.{rng.randrange(0, 30)}.{rng.randrange(0, 12)}'; pkgs[p] = v
        L.append(f'Collecting {p}'); L.append(f'  Downloading {p}-{v}-py3-none-any.whl ({rng.randrange(50, 9000)} kB)')
    L.append('Successfully installed ' + ' '.join(f'{k}-{v}' for k, v in pkgs.items())); end(0)
    step('ruff check .')
    lint = []
    for i in range(140):
        f = f'src/app/{rng.choice(MODS)}.py'; code = rng.choice(['E501', 'F401', 'B008', 'UP035', 'SIM108'])
        lint.append(code); L.append(f'{f}:{rng.randrange(1, 900)}:{rng.randrange(1, 100)}: {code} lint finding')
    L.append(f'Found {len(lint)} errors.'); end(1)
    step('pytest -v --durations=15 -p no:randomly')
    tests, fails, skips, durs, depr = [], {}, 0, {}, 0
    for m in MODS:
        for j in range(rng.randrange(40, 70)):
            nid = f'tests/test_{m}.py::test_{m}_{j:02d}_{rng.choice(["ok", "edge", "roundtrip", "limits", "empty", "unicode"])}'
            d = round(rng.lognormvariate(-3, 1.4), 3); durs[nid] = d
            r = rng.random()
            if r < 0.012:
                fails[nid] = f'AssertionError: expected {rng.randrange(100)} got {rng.randrange(100, 200)}'
                L.append(f'{nid} FAILED')
            elif r < 0.05:
                skips += 1; L.append(f'{nid} SKIPPED (requires gpu)')
            else:
                L.append(f'{nid} PASSED')
            tests.append(nid)
            if rng.random() < 0.03:
                depr += 1; L.append(f'  DeprecationWarning: datetime.datetime.utcnow() is deprecated (from {nid.split("::")[0]})')
    slow = 'tests/test_storage.py::test_storage_31_roundtrip' if 'tests/test_storage.py::test_storage_31_roundtrip' in durs else sorted(durs)[len(durs) // 2]
    durs[slow] = 41.837
    L.append('=================================== FAILURES ===================================')
    for nid, msg in fails.items():
        L.append(f'___ {nid.split("::")[1]} ___'); L += [f'    def {nid.split("::")[1]}():', '        result = run_case()', f'>       assert result == expected', f'E       {msg}', f'{nid.split("::")[0]}:{rng.randrange(10, 400)}: AssertionError', '']
    L.append('============================= slowest 15 durations =============================')
    for nid in sorted(durs, key=durs.get, reverse=True)[:15]:
        L.append(f'{durs[nid]:.2f}s call     {nid}')
    passed = len(tests) - len(fails) - skips
    total = round(sum(durs.values()), 2)
    L.append(f'========== {len(fails)} failed, {passed} passed, {skips} skipped, {depr} warnings in {total}s ==========')
    end(1)
    step('upload coverage'); L += ['Coverage 81.4% (target 80%)']; end(0)
    first = next(iter(fails))
    key = {'q1': sorted(fails), 'q2': passed, 'q3': skips, 'q4': slow, 'q5': pkgs['tenacity'], 'q6': depr,
           'q7': lint.count('F401'), 'q8': fails[first].split(': ', 1)[1], 'q9': len(tests)}
    return L, key, first

if __name__ == '__main__':
    L, key, first = build()
    if sys.argv[1] == '--key':
        print(json.dumps(key)); sys.exit()
    out = Path(sys.argv[1]) / 'ci'; out.mkdir(parents=True, exist_ok=True)
    (out / 'run-8812.log').write_text('\n'.join(L) + '\n')
