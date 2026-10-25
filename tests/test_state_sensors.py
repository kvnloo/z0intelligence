"""Real file adapters with isolated runtime observations; no host probes."""
import hashlib
import json
from argparse import Namespace

import pytest

from z0int.fsm import state_laws as S


def digest(data):
    return hashlib.sha256(data).hexdigest()


def put(path, value):
    path.write_text(json.dumps(value))
    return str(path)


@pytest.fixture(autouse=True)
def prohibit_host_probes(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("unexpected host runtime probe")
    monkeypatch.setattr(S, "_unit", forbidden)
    monkeypatch.setattr(S, "_proc", forbidden)
    monkeypatch.setattr(S.socket, "gethostname", lambda: "test-host")


@pytest.mark.parametrize("change,status", [(None, "pass"), ("hash", "violation"), ("scope", "violation")])
def test_file_claim_binding(tmp_path, change, status):
    path = tmp_path / "claim"
    path.write_bytes(b"observed")
    spec = {"id": "claim", "kind": "file", "path": str(path),
            "host": "test-host", "expected": digest(b"observed")}
    if change == "hash":
        spec["expected"] = digest(b"other")
    if change == "scope":
        spec["host"] = "another-host"
    result = S.sensor_claim(spec)
    assert result["status"] == status
    assert result["evidence"]["binding"]["sha256"] == digest(b"observed")
    assert result["evidence"]["binding"]["path"] == str(path.resolve())


def test_file_claim_changed_identity_is_stale(tmp_path, monkeypatch):
    path = tmp_path / "claim"
    path.write_bytes(b"observed")
    original = S._read
    count = 0
    def changing(value):
        nonlocal count
        data, binding = original(value)
        count += 1
        return data, {**binding, "observation": count}
    monkeypatch.setattr(S, "_read", changing)
    row = S.sensor_claim({"id": "claim", "kind": "file", "path": str(path),
                          "host": "test-host", "expected": digest(b"observed")})
    assert row["status"] == "violation"
    assert row["facts"]["current"] is False


@pytest.mark.parametrize("changed", [False, True])
def test_unit_claim_invocation_identity(monkeypatch, changed):
    observations = iter([
        {"InvocationID": "first", "MainPID": "42", "process": {"pid": 42, "proc_starttime_ticks": 1, "boot_id": "boot"}, "ActiveState": "active"},
        {"InvocationID": "second" if changed else "first", "MainPID": "42", "process": {"pid": 42, "proc_starttime_ticks": 1, "boot_id": "boot"}, "ActiveState": "active"},
    ])
    monkeypatch.setattr(S, "_unit", lambda name: next(observations))
    row = S.sensor_claim({"id": "unit", "kind": "unit", "unit": "owned.service",
                          "host": "test-host", "expected": {"ActiveState": "active"}})
    assert row["status"] == ("violation" if changed else "pass")
    assert row["evidence"]["process"]["boot_id"] == "boot"


@pytest.mark.parametrize("change,status", [
    (None, "pass"), ("schema", "violation"), ("hash", "violation"),
    ("zero-work", "violation"), ("boolean-work", "violation"),
    ("missing-output", "unknown"),
])
def test_typed_outcome_adapter(tmp_path, change, status):
    output = tmp_path / "output"
    output.write_bytes(b"verified work")
    receipt = {"schema": "z0.state_outcome.v1", "outcome": "success",
               "output_path": str(output), "output_sha256": digest(output.read_bytes()),
               "attempted": 1, "valid_decisions": 1, "transport_ok": 1,
               "exit_code": 0, "target_retired": False}
    if change == "schema":
        receipt["schema"] = "untyped"
    elif change == "hash":
        receipt["output_sha256"] = digest(b"different")
    elif change == "zero-work":
        receipt["valid_decisions"] = 0
    elif change == "boolean-work":
        receipt["attempted"] = True
    elif change == "missing-output":
        receipt["output_path"] = str(tmp_path / "absent")
    path = put(tmp_path / "receipt.json", receipt)
    assert S.sensor_outcome({"id": "outcome", "path": path})["status"] == status


@pytest.mark.parametrize("change,status", [
    (None, "pass"), ("source", "violation"), ("scope", "violation"),
    ("units", "violation"), ("guess", "violation"), ("missing", "unknown"),
    ("actual-constant", "violation"),
])
def test_measurement_adapter(tmp_path, change, status):
    source = tmp_path / "source.py"
    source.write_bytes(b"LIMIT = 13\n" if change == "actual-constant" else b"LIMIT = 12\n")
    measurement = {"schema": "z0.measurement.v1", "measured_at": "2026-10-24T00:00:00Z",
                   "command": "owned measurement", "kind": "measurement", "value": 12,
                   "source_sha256": digest(source.read_bytes()), "scope": "s22", "units": "ms"}
    if change == "source":
        measurement["source_sha256"] = digest(b"old source")
    elif change in ("scope", "units"):
        measurement[change] = "different"
    elif change == "guess":
        measurement["kind"] = "guess"
    path = put(tmp_path / "measurement.json", measurement)
    if change == "missing":
        path = str(tmp_path / "absent")
    row = S.sensor_constant({"id": "constant", "measurement": path, "path": str(source),
                             "scope": "s22", "units": "ms", "value": 12, "name": "LIMIT"})
    assert row["status"] == status


def revocation_spec(tmp_path, root):
    ledger = put(tmp_path / "ledger.json", {
        "schema": "z0.revocations.v1", "policy_epoch": 1812,
        "revocations": [{"id": rule} for rule in S.REVOKED],
    })
    return {"roots": [str(root)], "policy_ledger": ledger,
            "policy_epoch": 1812, "inventory_complete": True}


@pytest.mark.parametrize("kind,status", [("phone", "violation"), ("host", "pass")])
def test_yaml_disk_floor_is_phone_scoped(tmp_path, kind, status):
    path = tmp_path / "fleet.yaml"
    path.write_text(f"nodes:\n  device:\n    kind: {kind}\n    min_disk_free_gb: 5\n")
    rows = S.sensor_revocations(revocation_spec(tmp_path, path))
    assert rows[-1]["status"] == status
    assert any(row["status"] == "violation" for row in rows) is (kind == "phone")


@pytest.mark.parametrize("marker,end", [("historical:", "end-historical"),
                                         ("platform-observation:", "end-platform-observation")])
def test_explicit_nonpolicy_blocks_exempt_only_enclosed_lines(tmp_path, marker, end):
    path = tmp_path / "phone-policy.md"
    path.write_text(f"{marker}\nmin_disk_free_gb: 5\n{end}\nAndroid platform constraint only.\n")
    spec = revocation_spec(tmp_path, path)
    assert S.sensor_revocations(spec)[-1]["status"] == "pass"
    path.write_text(path.read_text() + "min_disk_free_gb: 5\n")
    assert S.sensor_revocations(spec)[-1]["status"] == "violation"


def test_missing_inventory_is_unknown(tmp_path):
    rows = S.sensor_revocations(revocation_spec(tmp_path, tmp_path / "missing"))
    assert rows[-1]["status"] == "unknown"


@pytest.mark.parametrize("text", ["{", "null", "[]", '{"checks":null}', '{"checks":[null]}'])
def test_malformed_input_reports_unknown(tmp_path, capsys, text):
    path = tmp_path / "input.json"
    path.write_text(text)
    assert S.command(Namespace(input=str(path), config=None)) == 4
    report = json.loads(capsys.readouterr().out)
    assert report["status"] == "unknown"
    assert report["enforcement_enabled"] is False


def test_malformed_yaml_inventory_is_unknown(tmp_path):
    path = tmp_path / "fleet.yaml"
    path.write_text("nodes: [unterminated\n")
    rows = S.sensor_revocations(revocation_spec(tmp_path, path))
    assert rows[-1]["status"] == "unknown"


@pytest.mark.parametrize("change,status", [("epoch", "violation"), ("missing-ledger", "unknown")])
def test_revocation_epoch_requires_current_ledger(tmp_path, change, status):
    path = tmp_path / "phone-policy.md"
    path.write_text("No active restrictions.\n")
    spec = revocation_spec(tmp_path, path)
    if change == "epoch":
        spec["policy_epoch"] = 1811
    else:
        spec["policy_ledger"] = str(tmp_path / "absent-ledger.json")
    assert S.sensor_revocations(spec)[-1]["status"] == status


@pytest.mark.parametrize("change,status", [
    (None, "pass"), ("artifact", "violation"), ("worktree", "violation"),
    ("pointer-swap", "unknown"), ("missing-hashes", "unknown"),
])
def test_release_manifest_binds_approved_artifacts(tmp_path, monkeypatch, change, status):
    source = tmp_path / "worktrees" / "release" if change == "worktree" else tmp_path / "release"
    source.mkdir(parents=True)
    artifact = source / "worker.py"
    artifact.write_bytes(b"VALUE = 1\n")
    (source / "DEPLOYED_SHA").write_text("approved-sha\n")
    pointer = tmp_path / "current"
    pointer.symlink_to(source, target_is_directory=True)
    manifest = put(tmp_path / "manifest.json", {
        "source_root": str(source), "integration_sha": "approved-sha",
        "current_reference": str(pointer), "cutover_performed": True,
    })
    hashes = put(tmp_path / "hashes.json", {
        "source_sha": "approved-sha", "sha256": {"worker.py": digest(artifact.read_bytes())},
    })
    if change == "artifact":
        artifact.write_bytes(b"VALUE = 2\n")
    elif change == "missing-hashes":
        hashes = str(tmp_path / "absent-hashes.json")

    original_iterdir = S.Path.iterdir
    def isolated_iterdir(path):
        if str(path) == "/proc":
            return iter(())
        return original_iterdir(path)
    monkeypatch.setattr(S.Path, "iterdir", isolated_iterdir)

    if change == "pointer-swap":
        replacement = tmp_path / "replacement"
        replacement.mkdir()
        original_read = S._read
        def swap_after_artifact(value):
            result = original_read(value)
            if S.Path(value) == artifact and pointer.resolve() == source:
                pointer.unlink()
                pointer.symlink_to(replacement, target_is_directory=True)
            return result
        monkeypatch.setattr(S, "_read", swap_after_artifact)

    rows = S.sensor_manifest({"manifest": manifest, "hash_manifest": hashes,
                              "units": [], "entrypoints": []})
    assert len(rows) == 1
    assert rows[0]["id"] == "manifest-generation"
    assert rows[0]["status"] == status


@pytest.mark.parametrize("native_status,exit_code,expected", [
    ("degraded", 3, "violation"),
    ("partial_measurement", 3, "violation"),
    ("success", 0, "unknown"),
])
def test_native_tick_summary_cannot_promote_transport_to_success(tmp_path, native_status, exit_code, expected):
    path = tmp_path / "tick.json"
    put(path, {"schema": "z0int.loop.tick_summary.v0",
               "status": native_status, "exit": exit_code, "rows_added": 2,
               "cognition": {"transport_tick": True}})
    row = S.sensor_outcome({"id": "native-tick", "path": str(path)})
    assert row["status"] == expected
    assert row["blocked"] is True
    evidence = row["evidence"]
    assert evidence["receipt"]["path"] == str(path.resolve())
    assert evidence["receipt"]["sha256"] == digest(path.read_bytes())
    assert evidence["observed_status"] == native_status
    assert evidence["observed_exit"] == exit_code
    assert evidence["rows_added"] == 2
    assert evidence["transport_tick"] is True


def test_generic_host_disk_example_does_not_become_phone_policy(tmp_path):
    path = tmp_path / "fleet.md"
    path.write_text("# Host health\nForce-test with --set min_disk_free_gb=99999.\n")
    rows = S.sensor_revocations(revocation_spec(tmp_path, path))
    assert rows[-1]["status"] == "pass"


@pytest.mark.parametrize("argv,expected", [
    (["python", "-m", "z0int.bridge.worker"], True),
    (["python3", "-m", "z0int.bridge.worker", "--label", "fleet-build-state_laws"], True),
    (["bash", "-lc", "python -m z0int.bridge.worker"], False),
    (["python3", "-c", "print('z0int')"], False),
    (["socat", "exec:python -m z0int.memory_mcp"], False),
    (["python", "/release/src/z0int/bridge/worker.py"], True),
])
def test_runtime_subject_uses_executable_identity_not_substring(argv, expected):
    assert S.runtime_subject(argv) is expected
