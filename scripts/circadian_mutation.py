#!/usr/bin/env python3
"""Mutation score for src/z0int/human_posture.py (spec §5.2: target >= 85).

AST mutants: comparison flips (< <=, > >=, == !=, in / not in), and/or swaps,
`not` removal, integer constants +1 (0 -> 1), True/False, and swaps between
phase / class / action string literals. Each mutant runs the circadian suites
(Bend parity included when bend is installed) in a scratch copy of the repo; a failing suite kills it.

  python3 scripts/circadian_mutation.py [--jobs 4] [--limit N] [--out receipt.json]
"""
from __future__ import annotations

import argparse
import ast
import copy
import json
import os
import shutil
import subprocess
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TARGET = ROOT / "src/z0int/human_posture.py"
SWAP = {ast.Lt: ast.LtE, ast.LtE: ast.Lt, ast.Gt: ast.GtE, ast.GtE: ast.Gt, ast.Eq: ast.NotEq,
        ast.NotEq: ast.Eq, ast.In: ast.NotIn, ast.NotIn: ast.In}
WORDS = ["SLEEP", "INERTIA", "PEAK", "DIP", "SECOND", "WIND_DOWN", "PRE_SLEEP", "DISENGAGED",
         "critical", "decision", "report", "digest", "deliver", "defer", "dash-focus", "dash-updates", "dash-quiet",
         "none", "all", "measured", "proxy", "default"]
SKIP_FUNCS = {"canonical", "_no_floats", "parse_ts", "_hhmm"}  # error paths, not policy


class Sites(ast.NodeVisitor):
    def __init__(self) -> None:
        self.sites: list[tuple[int, str]] = []
        self.func: list[str] = []

    def visit_FunctionDef(self, node):  # noqa: N802
        self.func.append(node.name)
        self.generic_visit(node)
        self.func.pop()

    def _ok(self) -> bool:
        return not (self.func and self.func[-1] in SKIP_FUNCS)

    def generic_visit(self, node):
        if self._ok():
            if isinstance(node, ast.Compare):
                for j, op in enumerate(node.ops):
                    if type(op) in SWAP:
                        self.sites.append((id(node), f"cmp:{j}"))
            elif isinstance(node, ast.BoolOp):
                self.sites.append((id(node), "boolop"))
            elif isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Not):
                self.sites.append((id(node), "not"))
            elif isinstance(node, ast.Constant) and type(node.value) is bool:
                self.sites.append((id(node), "bool"))
            elif isinstance(node, ast.Constant) and type(node.value) is int:
                self.sites.append((id(node), "int"))
            elif isinstance(node, ast.Constant) and isinstance(node.value, str) and node.value in WORDS:
                self.sites.append((id(node), "word"))
        super().generic_visit(node)


def mutants(src: str):
    tree = ast.parse(src)
    finder = Sites()
    finder.visit(tree)
    nodes = {id(n): n for n in ast.walk(tree)}
    for nid, kind in finder.sites:
        node = nodes[nid]
        variants = []
        if kind.startswith("cmp:"):
            j = int(kind.split(":")[1])
            variants.append(("cmp", lambda n, j=j: n.ops.__setitem__(j, SWAP[type(n.ops[j])]())))
        elif kind == "boolop":
            variants.append(("boolop", lambda n: setattr(n, "op", ast.Or() if isinstance(n.op, ast.And) else ast.And())))
        elif kind == "bool":
            variants.append(("bool", lambda n: setattr(n, "value", not n.value)))
        elif kind == "int":
            variants.append(("int", lambda n: setattr(n, "value", n.value + 1)))
        elif kind == "word":
            variants.append(("word", lambda n: setattr(n, "value", WORDS[(WORDS.index(n.value) + 1) % len(WORDS)])))
        for label, apply in variants:
            t2 = copy.deepcopy(tree)
            m = {id(a): b for a, b in zip(ast.walk(tree), ast.walk(t2))}[nid]
            if kind == "not":
                pass
            apply(m)
            yield f"{label}@{node.lineno}:{node.col_offset}", ast.unparse(t2)
        if kind == "not":
            t2 = copy.deepcopy(tree)
            m = {id(a): b for a, b in zip(ast.walk(tree), ast.walk(t2))}[nid]

            class Drop(ast.NodeTransformer):
                def visit_UnaryOp(self, n):  # noqa: N802
                    return n.operand if n is m else self.generic_visit(n)
            yield f"not@{node.lineno}:{node.col_offset}", ast.unparse(Drop().visit(t2))


def run_one(work: Path, code: str) -> bool:
    (work / "src/z0int/human_posture.py").write_text(code)
    r = subprocess.run([sys.executable, "-m", "pytest", "-x", "-q", "-p", "no:cacheprovider",
                        "tests/test_human_posture.py", "tests/test_circadian_service.py"],
                       cwd=work, capture_output=True, text=True, timeout=600,
                       env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"})
    return r.returncode != 0  # killed


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--jobs", type=int, default=2)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--out", default=None)
    args = ap.parse_args()
    src = TARGET.read_text()
    ms = list(mutants(src))
    if args.limit:
        ms = ms[: args.limit]
    tmp = Path(tempfile.mkdtemp(prefix="circ-mut-"))
    works = []
    for k in range(args.jobs):
        w = tmp / f"w{k}"
        for part in ("src/z0int", "tests/fixtures/human_posture", "scripts", "bend/circadian"):
            shutil.copytree(ROOT / part, w / part, dirs_exist_ok=True,
                            ignore=shutil.ignore_patterns("__pycache__"))
        for f in ("tests/test_human_posture.py", "tests/test_circadian_service.py"):
            shutil.copy(ROOT / f, w / f)
        works.append(w)
    results: dict[str, bool] = {}

    def worker(k: int) -> None:
        for i in range(k, len(ms), args.jobs):
            name, code = ms[i]
            results[name] = run_one(works[k], code)

    with ThreadPoolExecutor(args.jobs) as ex:
        list(ex.map(worker, range(args.jobs)))
    shutil.rmtree(tmp, ignore_errors=True)
    killed = sum(results.values())
    score = round(100 * killed / max(1, len(results)), 1)
    survivors = sorted(n for n, k in results.items() if not k)
    receipt = {"target": "src/z0int/human_posture.py", "mutants": len(results), "killed": killed,
               "score": score, "threshold": 85, "survivors": survivors}
    print(json.dumps(receipt, indent=1))
    if args.out:
        Path(args.out).write_text(json.dumps(receipt, indent=1) + "\n")
    return 0 if score >= 85 else 1


if __name__ == "__main__":
    sys.exit(main())
