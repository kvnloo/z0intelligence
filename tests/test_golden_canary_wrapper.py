from __future__ import annotations

import importlib.util
import json
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


def positive_golden_proof():
    return {
        "root_trace_id": "remote-public-proof",
        "structural_execution_complete": True,
        "verified_outcome_complete": True,
        "stages": {
            "physical_execution": {"receipt_id": "physical-x"},
            "verified_outcome": {
                "present": True,
                "trace_id": "physical-x",
                "outcome_tier": "gold",
                "outcome": {
                    "verified_success": True,
                    "verified": True,
                    "verification_source": "exact_string_CANONICAL_OK",
                },
            },
        },
    }


def run_with_collected_proof(tmp_path, monkeypatch, proof):
    """Exercise the bundle decision without executing a provider or collector."""
    omp, evals = fake_roots(tmp_path, "a" * 40)
    out = tmp_path / "out"
    monkeypatch.setenv("OPENROUTER_API_KEY", "fixture")
    monkeypatch.setattr(W.shutil, "which", lambda name: "/fixture/bun")
    monkeypatch.setattr(W, "git_clean", lambda path: True)
    monkeypatch.setattr(W, "git_head", lambda path: "a" * 40 if path == W.ROOT else "b" * 40)
    monkeypatch.setattr(sys, "argv", [
        "run-aodl-golden-canary.py", "--omp-root", str(omp),
        "--z0evals-root", str(evals), "--output", str(out),
    ])

    def fake_run(cmd, *, cwd, env):
        if cmd[1].endswith("prove-intelligence.py"):
            (out / "raw").mkdir()
        else:
            (out / "golden-trace.json").write_text(json.dumps(proof))

    monkeypatch.setattr(W, "run", fake_run)
    return W.main()


@pytest.mark.parametrize(("path", "value"), [
    (("structural_execution_complete",), 1),
    (("verified_outcome_complete",), 1),
    (("root_trace_id",), "different-root"),
    (("stages", "verified_outcome", "present"), False),
    (("stages", "verified_outcome", "outcome_tier"), "silver"),
    (("stages", "verified_outcome", "trace_id"), "unrelated-physical"),
    (("stages", "verified_outcome", "outcome", "verified_success"), False),
    (("stages", "verified_outcome", "outcome", "verified_success"), 1),
    (("stages", "verified_outcome", "outcome", "verified"), False),
    (("stages", "verified_outcome", "outcome", "verified"), None),
    (("stages", "verified_outcome", "outcome", "verification_source"), "unrelated_verifier"),
    (("stages", "verified_outcome", "outcome", "verification_source"), None),
])
def test_complete_evidence_does_not_automatically_make_canary_success(tmp_path, monkeypatch, path, value):
    proof = positive_golden_proof()
    target = proof
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = value
    with pytest.raises(RuntimeError, match="CANONICAL_OK"):
        run_with_collected_proof(tmp_path, monkeypatch, proof)
    # Preserve even a negative complete proof, but do not mint a positive bundle.
    assert json.loads((tmp_path / "out" / "golden-trace.json").read_text()) == proof
    assert not (tmp_path / "out" / "bundle.json").exists()


def test_exact_positive_gold_canary_produces_bundle(tmp_path, monkeypatch):
    assert run_with_collected_proof(tmp_path, monkeypatch, positive_golden_proof()) == 0
    bundle = json.loads((tmp_path / "out" / "bundle.json").read_text())
    assert bundle["verified_outcome"] is True
    assert bundle["synthetic_fixture"] == "CANONICAL_OK"
