"""Deterministic noisy build: ~2500 log lines, many warnings, one real error."""
import ast, pathlib, random, sys
random.seed(7)
mods = sorted(pathlib.Path('app').glob('*.py'))
errors = []
for step in range(60):
    for m in mods:
        print(f'[{step:02d}] compiling {m} ... ok ({random.randint(3, 90)} ms)')
    for _ in range(35):
        print(f'[{step:02d}] warning W{random.randint(100, 999)}: deprecated option --opt-{random.randint(1, 40)} in {random.choice(mods)}:{random.randint(1, 80)} (ignored)')
for m in mods:
    src = m.read_text()
    try:
        tree = ast.parse(src)
    except SyntaxError as e:
        errors.append(f'error E001: syntax error in {m}:{e.lineno}: {e.msg}'); continue
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and getattr(node.func, 'id', '') == 'compute_totl':
            errors.append(f'error E204: undefined symbol compute_totl referenced at {m}:{node.lineno}')
for e in errors:
    print(e)
print('BUILD FAILED' if errors else 'BUILD OK')
sys.exit(1 if errors else 0)
