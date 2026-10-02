from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "run-aodl-golden-canary.py"
SPEC = importlib.util.spec_from_file_location("run_aodl_golden_canary", SCRIPT)
W = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(W)

OMP_REQUIRED = (
    "src/extensibility/extensions/loader.ts",
    "src/extensibility/extensions/runner.ts",
    "src/session/session-manager.ts",
    "src/session/auth-storage.ts",
    "src/config/model-registry.ts",
)


def fake_roots(tmp_path, pinned):
    omp = tmp_path / "omp"
    for rel in OMP_REQUIRED:
        path = omp / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("// fixture\n")

    evals = tmp_path / "z0evals"
    study = evals / "studies" / "aodl-admission-v1"
    study.mkdir(parents=True)
    (study / "collect_golden.py").write_text("# fixture\n")
    (study / "golden-canary-bundle.schema.json").write_text("{}\n")
    (study / "golden-trace-manifest.yaml").write_text(
        "sources:\n"
        "  - repo: kvnloo/z0intelligence\n"
        f"    commit: {pinned}\n"
    )
    return omp, evals


def test_manifest_parser_requires_exact_revision(tmp_path):
    path = tmp_path / "manifest.yaml"
    path.write_text(
        "sources:\n"
        "  - repo: kvnloo/z0intelligence\n"
        "    commit: " + "a" * 40 + "\n"
    )
    assert W.pinned_z0int_revision(path) == "a" * 40


def test_missing_credential_stops_before_any_live_work(tmp_path, monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.setattr(
        sys,
        "argv",
        ["run-aodl-golden-canary.py", "--omp-root", str(tmp_path), "--z0evals-root", str(tmp_path), "--output", str(tmp_path / "out")],
    )
    called = []
    monkeypatch.setattr(W, "run", lambda *a, **k: called.append((a, k)))
    with pytest.raises(SystemExit):
        W.main()
    assert called == []


def test_revision_mismatch_stops_before_live_proof(tmp_path, monkeypatch):
    omp, evals = fake_roots(tmp_path, "f" * 40)
    out = tmp_path / "out"
    monkeypatch.setenv("OPENROUTER_API_KEY", "fixture")
    monkeypatch.setattr(W.shutil, "which", lambda name: "/fixture/bun" if name == "bun" else None)
    monkeypatch.setattr(W, "git_clean", lambda path: True)
    monkeypatch.setattr(W, "git_head", lambda path: "a" * 40)
    monkeypatch.setattr(
        sys,
        "argv",
        ["run-aodl-golden-canary.py", "--omp-root", str(omp), "--z0evals-root", str(evals), "--output", str(out)],
    )
    called = []
    monkeypatch.setattr(W, "run", lambda *a, **k: called.append((a, k)))
    with pytest.raises(SystemExit):
        W.main()
    assert called == []
    assert not out.exists()


def test_valid_preflight_reaches_live_proof_only_after_checks(tmp_path, monkeypatch):
    omp, evals = fake_roots(tmp_path, "a" * 40)
    out = tmp_path / "out"
    monkeypatch.setenv("OPENROUTER_API_KEY", "fixture")
    monkeypatch.setattr(W.shutil, "which", lambda name: "/fixture/bun" if name == "bun" else None)
    monkeypatch.setattr(W, "git_clean", lambda path: True)
    monkeypatch.setattr(W, "git_head", lambda path: "a" * 40 if path == W.ROOT else "b" * 40)
    monkeypatch.setattr(
        sys,
        "argv",
        ["run-aodl-golden-canary.py", "--omp-root", str(omp), "--z0evals-root", str(evals), "--output", str(out)],
    )

    class StopLive(Exception):
        pass

    calls = []
    def stop(cmd, *, cwd, env):
        calls.append((cmd, cwd))
        raise StopLive

    monkeypatch.setattr(W, "run", stop)
    with pytest.raises(StopLive):
        W.main()
    assert len(calls) == 1
    assert calls[0][0][1].endswith("scripts/prove-intelligence.py")
    assert out.is_dir()
    assert not (out / "raw").exists()
