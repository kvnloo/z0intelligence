"""Unit tests for decision benchmark run-manifest provenance and JSON output."""

from __future__ import annotations

import hashlib
import json
import platform
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from z0int.backends.bench import run_manifest as rm


def _stub_external_provenance(monkeypatch: pytest.MonkeyPatch) -> None:
    """Avoid subprocesses, hardware discovery and optional package imports."""
    monkeypatch.setattr(rm, "_git_sha", lambda repo_root: "sha-z0")
    monkeypatch.setattr(rm, "tokenomics_git_sha", lambda: "sha-tok")
    monkeypatch.setattr(rm, "_gpu_name", lambda: "GPU One")
    monkeypatch.setattr(
        rm, "_pkg_version", lambda name: {"torch": "2.9", "transformers": "5.1"}.get(name)
    )


def test_git_sha_success_and_args(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    calls = []

    def fake(args, **kwargs):
        calls.append((args, kwargs))
        return "abc123\n"

    monkeypatch.setattr(rm.subprocess, "check_output", fake)

    assert rm._git_sha(tmp_path) == "abc123"
    assert calls == [
        (
            ["git", "rev-parse", "HEAD"],
            {"cwd": str(tmp_path), "text": True, "stderr": subprocess.DEVNULL},
        )
    ]


def test_git_sha_failures_return_none(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(rm.subprocess, "check_output", lambda *args, **kwargs: "\n")
    assert rm._git_sha(tmp_path) is None

    for error in (subprocess.CalledProcessError(128, "git"), FileNotFoundError("git")):
        def fail(*args, **kwargs):
            raise error

        monkeypatch.setattr(rm.subprocess, "check_output", fail)
        assert rm._git_sha(tmp_path) is None


def test_gpu_name_no_binary(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(rm.shutil, "which", lambda name: None)

    def unexpected(*args, **kwargs):
        raise AssertionError("nvidia-smi must not be invoked when absent")

    monkeypatch.setattr(rm.subprocess, "check_output", unexpected)
    assert rm._gpu_name() is None


def test_gpu_name_parses_first_line(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(rm.shutil, "which", lambda name: "/x/nvidia-smi")
    calls = []

    def fake(args, **kwargs):
        calls.append((args, kwargs))
        return "GPU One\nGPU Two\n"

    monkeypatch.setattr(rm.subprocess, "check_output", fake)
    assert rm._gpu_name() == "GPU One"
    assert calls == [
        (
            ["nvidia-smi", "--query-gpu=name", "--format=csv,noheader"],
            {"text": True, "timeout": 5},
        )
    ]

    monkeypatch.setattr(rm.subprocess, "check_output", lambda *args, **kwargs: "\n")
    assert rm._gpu_name() is None


def test_gpu_name_errors_return_none(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(rm.shutil, "which", lambda name: "/x/nvidia-smi")

    for error in (subprocess.TimeoutExpired("nvidia-smi", 5), OSError("gpu unavailable")):
        def fail(*args, **kwargs):
            raise error

        monkeypatch.setattr(rm.subprocess, "check_output", fail)
        assert rm._gpu_name() is None


def test_pkg_version() -> None:
    assert rm._pkg_version("json") == json.__version__
    assert rm._pkg_version("z0int_no_such_module_xyz") is None
    assert rm._pkg_version("string") is None


def test_build_run_manifest_fields(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _stub_external_provenance(monkeypatch)
    monkeypatch.setattr(rm, "dataset_hash", lambda fixtures_path: "hash-1")
    started = datetime(2026, 1, 2, 3, 4, 5, tzinfo=timezone.utc)
    finished = started + timedelta(minutes=1)
    fixtures = tmp_path / "f.jsonl"

    manifest = rm.build_run_manifest(
        repo_root=tmp_path,
        contract="decision-capability-v1",
        fixtures_path=fixtures,
        candidates=["a", "b"],
        run_id="run-1",
        started_at=started,
        finished_at=finished,
    )

    assert set(manifest) == {
        "schema",
        "run_id",
        "contract",
        "dataset_hash",
        "fixtures_path",
        "candidates",
        "z0int_git_sha",
        "tokenomics_git_sha",
        "python_version",
        "torch_version",
        "transformers_version",
        "cuda_version",
        "gpu_name",
        "platform",
        "device_env",
        "model_revisions",
        "started_at",
        "finished_at",
    }
    assert manifest["schema"] == "z0int.backends_bench.run_manifest.v1"
    assert manifest["run_id"] == "run-1"
    assert manifest["contract"] == "decision-capability-v1"
    assert manifest["dataset_hash"] == "hash-1"
    assert manifest["fixtures_path"] == str(fixtures)
    assert manifest["candidates"] == ["a", "b"]
    assert manifest["z0int_git_sha"] == "sha-z0"
    assert manifest["tokenomics_git_sha"] == "sha-tok"
    assert manifest["python_version"] == platform.python_version()
    assert manifest["torch_version"] == "2.9"
    assert manifest["transformers_version"] == "5.1"
    assert manifest["cuda_version"] is None
    assert manifest["gpu_name"] == "GPU One"
    assert manifest["platform"] == platform.platform()
    assert manifest["device_env"] == {}
    assert manifest["model_revisions"] == {}
    assert manifest["started_at"] == "2026-01-02T03:04:05+00:00"
    assert manifest["finished_at"] == "2026-01-02T03:05:05+00:00"


def test_build_run_manifest_passthrough_and_copy(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _stub_external_provenance(monkeypatch)
    monkeypatch.setattr(rm, "dataset_hash", lambda fixtures_path: "hash-1")
    candidates = ["a", "b"]
    device_env = {"K": "V"}
    model_revisions = {"m": "r1"}
    started = datetime(2026, 1, 2, tzinfo=timezone.utc)

    manifest = rm.build_run_manifest(
        repo_root=tmp_path,
        contract="decision-capability-v1",
        fixtures_path=tmp_path / "f.jsonl",
        candidates=candidates,
        run_id="run-1",
        started_at=started,
        finished_at=started + timedelta(minutes=1),
        device_env=device_env,
        model_revisions=model_revisions,
    )

    assert manifest["device_env"] == {"K": "V"}
    assert manifest["model_revisions"] == {"m": "r1"}
    assert manifest["candidates"] == ["a", "b"]
    assert manifest["candidates"] is not candidates
    candidates.append("c")
    assert manifest["candidates"] == ["a", "b"]


def test_build_run_manifest_real_dataset_hash(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _stub_external_provenance(monkeypatch)
    fixtures = tmp_path / "f.jsonl"
    fixtures.write_bytes(b"abc")
    started = datetime(2026, 1, 2, tzinfo=timezone.utc)
    kwargs = {
        "repo_root": tmp_path,
        "contract": "decision-capability-v1",
        "fixtures_path": fixtures,
        "candidates": ["a", "b"],
        "run_id": "run-1",
        "started_at": started,
        "finished_at": started + timedelta(minutes=1),
    }

    assert rm.build_run_manifest(**kwargs)["dataset_hash"] == hashlib.sha256(
        b"abc"
    ).hexdigest()

    kwargs["fixtures_path"] = tmp_path / "missing.jsonl"
    with pytest.raises(FileNotFoundError):
        rm.build_run_manifest(**kwargs)


def test_write_run_manifest(tmp_path: Path) -> None:
    path = tmp_path / "run-manifest.json"
    manifest = {"schema": "example.v1", "name": "café"}

    rm.write_run_manifest(path, manifest)

    contents = path.read_text(encoding="utf-8")
    assert contents == json.dumps(manifest, indent=2, ensure_ascii=False) + "\n"
    assert '\n  "name": "café"' in contents
    assert "\\u00e9" not in contents
    assert contents.endswith("\n")
    assert not contents.endswith("\n\n")
    assert json.loads(contents) == manifest
