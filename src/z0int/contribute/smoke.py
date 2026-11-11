"""Smoke eval (spec §1.4): offline, CPU only, no HF download, no evolution-lab.

Parts:
1. ``python -m z0int doctor --json`` exits 0.
2. ``python -m pytest -q -m 'contrib_smoke and not live' <subset>`` (target <= 60 s).
3. One real eval on a fixture: ``python -m z0int contrastive eval --json`` (the
   built-in project-status contrast family; it needs no weights). Its canonical
   output hash must equal ``tests/fixtures/contrib_smoke.golden.json``. The
   ``laya_421m`` backend was checked and needs the laya package and weights, so it
   is not used here.
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Callable

SMOKE_TESTS = (
    "tests/test_contribute_schemas.py",
    "tests/test_contribute_priorities.py",
    "tests/test_contribute_align.py",
    "tests/test_contribute_state.py",
)
GOLDEN = Path("tests/fixtures/contrib_smoke.golden.json")
EVAL_ARGV = ("-m", "z0int", "contrastive", "eval", "--json")
VOLATILE_KEYS = ("evaluated_at",)

Runner = Callable[[list[str], Path, dict[str, str]], tuple[int, str]]


def offline_env(root: Path) -> dict[str, str]:
    env = dict(os.environ)
    env.update({"HF_HUB_OFFLINE": "1", "TRANSFORMERS_OFFLINE": "1", "CUDA_VISIBLE_DEVICES": "",
                "SKIP_EVOLUTION_LAB": "1"})
    src = str(Path(root) / "src")
    env["PYTHONPATH"] = src + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
    bindir = str(Path(sys.executable).parent)
    env["PATH"] = bindir + os.pathsep + env.get("PATH", "")
    return env


def default_runner(argv: list[str], cwd: Path, env: dict[str, str], timeout: int = 300) -> tuple[int, str]:
    try:
        p = subprocess.run(argv, cwd=cwd, env=env, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        return 124, f"timeout after {timeout}s: {exc}"
    except OSError as exc:
        return 127, f"{type(exc).__name__}: {exc}"
    return p.returncode, p.stdout + p.stderr


def canonical_eval_sha256(stdout: str) -> str | None:
    """Hash of the eval JSON with volatile keys removed (sorted keys, compact)."""
    try:
        obj = json.loads(stdout)
    except ValueError:
        start = stdout.find("{")
        if start < 0:
            return None
        try:
            obj = json.JSONDecoder().raw_decode(stdout[start:])[0]
        except ValueError:
            return None
    if not isinstance(obj, dict):
        return None
    for k in VOLATILE_KEYS:
        obj.pop(k, None)
    return hashlib.sha256(json.dumps(obj, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def golden_sha256(root: Path) -> str | None:
    try:
        return json.loads((Path(root) / GOLDEN).read_text(encoding="utf-8"))["output_sha256"]
    except (OSError, ValueError, KeyError):
        return None


def _part(name: str, argv: list[str], code: int, out: str, **extra: Any) -> dict[str, Any]:
    return {"name": name, "command": " ".join(argv), "exit": code,
            "output_sha256": hashlib.sha256(out.encode()).hexdigest(), **extra}


def product_smoke(root: Path, runner: Runner = default_runner) -> list[dict[str, Any]]:
    """Doctor + fixture eval with golden hash (parts 1 and 3)."""
    root = Path(root)
    env = offline_env(root)
    py = sys.executable
    parts = []
    doctor = [py, "-m", "z0int", "doctor", "--json"]
    code, out = runner(doctor, root, env)
    parts.append(_part("doctor", ["python", *doctor[1:]], code, out))
    ev = [py, *EVAL_ARGV]
    code, out = runner(ev, root, env)
    got = canonical_eval_sha256(out) if code == 0 else None
    want = golden_sha256(root)
    parts.append(_part("eval_fixture", ["python", *EVAL_ARGV], code, out,
                       golden_sha256=want, golden_match=(got is not None and got == want)))
    return parts


def run_smoke(root: Path, runner: Runner = default_runner) -> tuple[bool, list[dict[str, Any]]]:
    root = Path(root)
    env = offline_env(root)
    parts = product_smoke(root, runner)
    tests = [t for t in SMOKE_TESTS if (root / t).exists()]
    argv = [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "-m", "contrib_smoke and not live", *tests]
    code, out = runner(argv, root, env)
    parts.insert(1, _part("pytest_subset", ["python", *argv[1:]], code, out))
    return smoke_passed(parts), parts


def smoke_passed(parts: list[dict[str, Any]]) -> bool:
    if not parts:
        return False
    for p in parts:
        if p["exit"] != 0:
            return False
        if p["name"] == "eval_fixture" and not p.get("golden_match"):
            return False
    return True
