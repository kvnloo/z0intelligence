"""Read-only O10 source scanner. Parsing is evidence, not a proof of runtime behavior.

``phone_caps_findings(root, paths=None)`` scans a .files root and the known
agent-facing policy files that exist. Explicit paths replace discovery (including
external files), so fixtures and hooks never need to modify a live checkout.
Python uses AST control-flow scopes, YAML uses its mapping tree; shell and prose
use bounded lexical rules. Missing explicit paths and malformed inputs fail shut.
"""
from __future__ import annotations

import ast
import re
import shlex
from pathlib import Path

import yaml

OPS = Path('/mnt/zer0models/workspace/zer0/data/operations')
KNOWN_PATHS = (
    OPS / 'phone-fleet-20261022/fleet/REPORT.md',
    OPS / 'chatgpt-driver/README.md',
    OPS / 'chatgpt-driver/tern-01/launch_queue.py',
    Path('/workspace/hermes-home/profiles/clean/skills/devops/home-k3s-fleet/SKILL.md'),
    Path('/workspace/hermes-home/profiles/clean/skills/devops/home-k3s-fleet/references/android-phone-workers.md'),
    Path('/home/kvn/.omp/agent/managed-skills/kevin-phones/SKILL.md'),
)
RESOURCE = re.compile(r'(?:^|_)(?:mem(?:ory|avail|_available)?|rss|cpu(?:s)?|load1|batt(?:ery)?|thermal|temp(?:erature)?)(?:_|$)', re.I)
LIMIT = re.compile(r'default_(?:mem|cpu)|(?:mem|memory|cpu|batt|battery|thermal|temp).*?(?:floor|max|min|quota|limit|cap)|(?:floor|max|min|quota|limit|cap).*?(?:mem|cpu|batt|thermal|temp)|^cpus$', re.I)
PHONE = re.compile(r'phone|s22|s25|omarchy|polite', re.I)


def _policy_key(key: str) -> bool:
    return bool(RESOURCE.search(key) and LIMIT.search(key))


def phone_caps_paths(root: Path, paths: list[Path] | None = None) -> list[Path]:
    root = Path(root).resolve()
    if paths is not None:
        return sorted({(p if p.is_absolute() else root / p).resolve() for p in map(Path, paths)})
    candidates = [*(root / 'scripts' / name for name in ('fleet', 'fleet-build', 'fleet-polite-run')),
                  *sorted((root / 'scripts').glob('fleet-phone*')),
                  root / 'config/fleet/nodes.yaml',
                  *sorted((root / 'docs/fleet').glob('*.md')),
                  *KNOWN_PATHS]
    return sorted({p.resolve() for p in candidates if p.is_file()})


def _finding(path: Path, line: int, rule: str, detail: str) -> dict:
    return {'path': str(path), 'line': line, 'rule': rule, 'detail': detail}


def _source(node: ast.AST) -> str:
    return ast.unparse(node)


def _nonphone(test: ast.AST) -> bool | None:
    """True: test proves non-phone, False: proves phone, None: unknown."""
    if isinstance(test, ast.UnaryOp) and isinstance(test.op, ast.Not):
        fact = _nonphone(test.operand)
        return None if fact is None else not fact
    if isinstance(test, ast.Name) and test.id in ('is_phone', 'phone'):
        return False
    if isinstance(test, ast.BoolOp):
        facts = [_nonphone(v) for v in test.values]
        if isinstance(test.op, ast.And) and True in facts:
            return True
        if isinstance(test.op, ast.Or) and all(f is True for f in facts):
            return True
    text = _source(test)
    if isinstance(test, (ast.Subscript, ast.Call)) and re.search(r"['\"]k8s['\"]", text):
        return True
    if isinstance(test, ast.Compare) and len(test.ops) == 1:
        if re.search(r"(?:['\"]kind['\"]|\bmode\b)", text) and re.search(r"['\"]phone['\"]", text):
            if isinstance(test.ops[0], ast.Eq):
                return False
            if isinstance(test.ops[0], ast.NotEq):
                return True
        if re.search(r"['\"]kind['\"]", text) and re.search(r"['\"]linux['\"]", text) and isinstance(test.ops[0], ast.Eq):
            return True
    return None


def _python(path: Path, text: str) -> list[dict]:
    tree = ast.parse(text)
    found = []

    def add(node, rule, detail):
        found.append(_finding(path, node.lineno, rule, detail))

    def resource(node, tainted):
        # A dictionary named memory can be prompt memory, not RAM: use
        # accessed field semantics instead of tainting all its metadata.
        if isinstance(node, ast.Subscript) and isinstance(node.slice, ast.Constant):
            return bool(RESOURCE.search(str(node.slice.value)))
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == 'get' and node.args and isinstance(node.args[0], ast.Constant):
            return bool(RESOURCE.search(str(node.args[0].value)))
        if isinstance(node, ast.Name):
            return bool(RESOURCE.search(node.id) or node.id in tainted)
        if isinstance(node, ast.Attribute):
            return bool(RESOURCE.search(node.attr))
        return any(resource(child, tainted) for child in ast.iter_child_nodes(node))

    def expr(node, safe, tainted):
        if isinstance(node, ast.IfExp):
            fact = _nonphone(node.test)
            expr(node.test, safe or fact is not None, tainted)
            expr(node.body, safe or fact is True, tainted)
            expr(node.orelse, safe or fact is False, tainted)
            return
        # Generated systemd service limits apply to the Linux controller, not
        # the PRoot phone job. Phones have no systemd.
        if isinstance(node, (ast.JoinedStr, ast.Constant)) and '[Service]' in _source(node):
            return
        if not safe:
            if isinstance(node, ast.Call) and _source(node.func).endswith('merge') and any(isinstance(n, ast.Subscript) and isinstance(n.slice, ast.Constant) and n.slice.value == 'defaults' for n in ast.walk(node)):
                add(node, 'python-inherited-default', 'unguarded registry defaults may reach phone nodes')
            if isinstance(node, ast.Subscript) and isinstance(node.slice, ast.Constant) and _policy_key(str(node.slice.value)) and _source(node.value) not in ('p', 'probe', 'readings'):
                add(node, 'python-resource-default', f'phone-visible resource policy key {node.slice.value}')
            if isinstance(node, ast.Call):
                name = _source(node.func)
                if name.endswith(('.get', '.setdefault')) and node.args and isinstance(node.args[0], ast.Constant) and _policy_key(str(node.args[0].value)) and _source(node.func.value) not in ('p', 'probe', 'readings'):
                    add(node, 'python-resource-default', f'phone-visible resource policy key {node.args[0].value}')
                if name.endswith(('.get', '.setdefault')) and len(node.args) > 1 and isinstance(node.args[0], ast.Constant) and node.args[0].value in ('mem_mb', 'mem', 'memory', 'cpus'):
                    default = node.args[1]
                    if not (isinstance(default, ast.Constant) and default.value in (None, '')):
                        add(node, 'python-resource-default', 'resource metadata has an agent-chosen default')
                if name.endswith('add_argument') and any(isinstance(a, ast.Constant) and a.value in ('--mem', '--cpus', '--mem-mb', '--floor-mb') for a in node.args):
                    if any(k.arg == 'default' and not (isinstance(k.value, ast.Constant) and k.value.value in (None, '')) for k in node.keywords):
                        add(node, 'python-resource-default', 'resource CLI argument has an agent-chosen default')
            optional = isinstance(node, ast.Compare) and any(isinstance(v, ast.Constant) and v.value is None for v in [node.left, *node.comparators]) and all(isinstance(op, (ast.Is, ast.IsNot)) for op in node.ops)
            ranking = isinstance(node, ast.Compare) and any(isinstance(v, ast.Subscript) and isinstance(v.slice, ast.Constant) and isinstance(v.slice.value, int) for v in [node.left, *node.comparators])
            if isinstance(node, ast.Compare) and resource(node, tainted) and not optional and not ranking:
                add(node, 'python-resource-comparison', 'resource comparison outside a non-phone-only scope')
            if isinstance(node, ast.Constant) and isinstance(node.value, str) and re.search(r'\b(?:taskset|ulimit|CPUQuota|MemoryMax)\b', node.value):
                add(node, 'python-resource-enforcement', 'phone-visible resource enforcement command')
        for child in ast.iter_child_nodes(node):
            expr(child, safe, tainted)

    def block(nodes, safe=False, tainted=None):
        tainted = set() if tainted is None else set(tainted)
        for node in nodes:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and not safe:
                    for arg, default in zip(node.args.args[-len(node.args.defaults):], node.args.defaults):
                        if RESOURCE.search(arg.arg) and not (isinstance(default, ast.Constant) and default.value in (None, '')):
                            add(default, 'python-resource-default', 'resource function argument has an agent-chosen default')
                block(node.body, safe)
                continue
            if isinstance(node, ast.If):
                fact = _nonphone(node.test)
                expr(node.test, safe or fact is not None, tainted)
                block(node.body, safe or fact is True, tainted)
                block(node.orelse, safe or fact is False, tainted)
                if fact is False and any(isinstance(n, (ast.Return, ast.Raise, ast.Continue)) for n in node.body):
                    safe = True
                continue
            if isinstance(node, (ast.For, ast.While, ast.With, ast.Try)):
                if isinstance(node, ast.While):
                    expr(node.test, safe, tainted)
                block(node.body, safe, tainted)
                block(getattr(node, 'orelse', []), safe, tainted)
                block(getattr(node, 'finalbody', []), safe, tainted)
                for handler in getattr(node, 'handlers', []):
                    block(handler.body, safe, tainted)
                continue
            if isinstance(node, (ast.Assign, ast.AnnAssign)) and node.value is not None:
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                names = {t.id for t in targets if isinstance(t, ast.Name)}
                if not safe and isinstance(node.value, ast.Constant) and node.value.value not in (None, '') and any(RESOURCE.search(name) for name in names):
                    add(node, 'python-resource-default', 'resource variable has an agent-chosen default')
                if resource(node.value, tainted):
                    tainted.update(names)
                else:
                    tainted.difference_update(names)
            expr(node, safe, tainted)
    block(tree.body)
    return found


def _yaml(path: Path, text: str) -> list[dict]:
    # Compose preserves mapping structure, anchors, scalar types and line numbers.
    tree = yaml.compose(text)
    found = []
    visited = set()
    if tree is None:
        return found

    def walk(node, phone=False):
        if (id(node), phone) in visited:
            return
        visited.add((id(node), phone))
        if isinstance(node, yaml.MappingNode):
            values = {k.value: v for k, v in node.value}
            phone = phone or getattr(values.get('kind'), 'value', '') == 'phone' or getattr(values.get('mode'), 'value', '') == 'phone'
            for key, value in node.value:
                if phone and _policy_key(key.value) and getattr(value, 'value', None) not in ('', 'null', '~'):
                    found.append(_finding(path, key.start_mark.line + 1, 'registry-phone-cap', f'phone resource policy {key.value}'))
                walk(value, phone or key.value in ('phone', 's22', 's25', 'omarchy-phone', 'galaxy-s22plus'))
        elif isinstance(node, yaml.SequenceNode):
            for value in node.value:
                walk(value, phone)
    walk(tree)
    return found


def _shell(path: Path, text: str) -> list[dict]:
    found = []
    # Track mode case arms so Linux/MBP-only limits remain legal.
    mode = None
    for number, raw in enumerate(text.splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith('#'):
            continue
        arm = re.match(r'(android|build|light|phone)(?:\|[\w-]+)*\)', line)
        if arm:
            mode = arm.group(1)
        safe = mode in ('android', 'build')
        if line.startswith('esac'):
            mode = None
        if safe:
            continue
        try:
            tokens = shlex.split(line, comments=True)
        except ValueError:
            tokens = [line]  # multiline shell constructs still get lexical checks
        code = ' '.join(tokens)
        if re.search(r'\b(taskset|ulimit|nice|ionice)\b|CPUQuota|MemoryMax', code):
            found.append(_finding(path, number, 'shell-resource-enforcement', 'agent resource enforcement in phone-capable shell scope'))
        if re.search(r'\b(?:mem(?:ory|_mb)?|cpus|floor_mb|hard_floor_mb|min_batt|max_batt_c|max_thermal)=(?![\"\']?(?:\$|[\"\']?;|$))[^;\s]+', code, re.I):
            found.append(_finding(path, number, 'shell-resource-default', 'agent-chosen phone resource default'))
        if re.search(r'\$\{(?:mem|cpus)[^}]*:-[^}]+\}|--(?:mem|cpus|floor-mb|mem-mb)(?:=|\s+)[\"\']?\d', code):
            found.append(_finding(path, number, 'shell-resource-default', 'phone-capable mode supplies a resource default'))
        if any(RESOURCE.search(name) for name in re.findall(r'[A-Za-z_][A-Za-z_0-9]*', code)) and re.search(r'\s-(?:gt|ge|lt|le)\s|[<>]=?', code) and not re.search(r'\b(?:echo|printf)\b', code):
            found.append(_finding(path, number, 'shell-resource-gate', 'resource-dependent phone gate'))
    return found


def _prose(path: Path, text: str) -> list[dict]:
    found = []
    context = bool(PHONE.search(path.name))
    for number, line in enumerate(text.splitlines(), 1):
        if line.startswith('#'):
            context = bool(PHONE.search(line)) or bool(PHONE.search(path.name))
        phone = context or bool(PHONE.search(line)) or '--light' in line
        if not phone:
            continue
        # Normative prohibition is not an instruction to reintroduce the cap.
        if re.search(r'no agent resource caps|no (?:battery|thermal|memory|cpu).*?(?:gate|cap|limit)|never (?:gate|cap|limit)|Android manages', line, re.I):
            continue
        bad = re.search(r'(?:half (?:the )?cores|nice\s+19|taskset|\b(?:MEM_MB|FLOOR_MB|MIN_BATT|MAX_THERMAL)=\d|(?:memory|ram|cpu|battery|thermal|temperature).{0,55}(?:[<>≤≥]=?\s*\d|(?:floor|cap|limit|ceiling|default).{0,20}\d)|\d+(?:\.\d+)?\s*(?:MiB|GiB|MB|GB|%).{0,20}(?:floor|cap|ceiling)|(?:default|floor|cap|ceiling).{0,25}\d+\s*(?:MiB|GiB|MB|GB)|--(?:mem|cpus)\s+\d+)', line, re.I)
        if bad and not re.search(r'caller[- ](?:explicit|provided)|explicit (?:caller|per-job)|MBP|laptop|Gradle|k3s|kubelet', line, re.I):
            found.append(_finding(path, number, 'agent-facing-phone-cap', 'agent-facing phone resource cap/default instruction'))
    return found


def phone_caps_findings(root: Path, paths: list[Path] | None = None) -> list[dict]:
    """Return stable findings; root is mandatory, explicit paths replace defaults."""
    root = Path(root)
    if not root.is_dir():
        return [_finding(root, 0, 'scan-unavailable', 'root is not a directory')]
    selected = phone_caps_paths(root, paths)
    if not selected:
        return [_finding(root, 0, 'scan-unavailable', 'no policy sources found')]
    found = []
    for path in selected:
        try:
            text = path.read_text()
            if path.suffix in ('.yaml', '.yml', '.json'):
                found.extend(_yaml(path, text))
            elif path.suffix == '.py' or text.startswith('#!/usr/bin/env python') or text.startswith('#!/usr/bin/python'):
                found.extend(_python(path, text))
            elif path.suffix == '.md':
                found.extend(_prose(path, text))
            else:
                found.extend(_shell(path, text))
        except (OSError, UnicodeError, SyntaxError, ValueError, yaml.YAMLError) as exc:
            found.append(_finding(path, 0, 'scan-unavailable', type(exc).__name__))
    return sorted({(f['path'], f['line'], f['rule']): f for f in found}.values(), key=lambda f: (f['path'], f['line'], f['rule']))
