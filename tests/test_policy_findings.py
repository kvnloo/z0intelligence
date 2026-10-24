"""Synthetic evidence only. Do not read operational traces."""
import hashlib
import json

import pytest

from z0int.policy.cli import main
from z0int.policy.findings import (
    Finding, FindingsError, append_findings, canonical, finding_id, ledger_path, read_findings,
)
from z0int.policy.ingest import ingest_sources


def finding(**changes):
    data = dict(ts="2026-10-24T00:00:00Z", source={"kind": "manual", "path": "/synthetic/evidence.json",
        "sha256": "a" * 64, "run_id": "test"}, subject={"family": "omp.tool", "job": "next-action"},
        arm={"kind": "slm", "model": "test@rev"}, metric={"name": "acc_at_conf", "value": 1., "n": 100,
        "ci_low": .96, "ci_high": 1., "ci_method": "wilson95", "higher_is_better": True},
        evidence_class="measured", scope_note="Synthetic job only.", expires_at="2026-11-07T00:00:00Z")
    data.update(changes)
    return Finding(**data).to_dict()


def test_hash_chain_and_canonical_unicode(tmp_path):
    path = tmp_path / "findings.jsonl"
    first = finding(scope_note="Synthetic café only.")
    second = finding(ts="2026-10-24T01:00:00Z")
    rows = append_findings([first, second], path)
    assert rows[0]["prev"] == ""
    assert rows[1]["prev"] == hashlib.sha256(canonical(rows[0])).hexdigest()
    assert rows[0]["id"] == finding_id({**rows[0], "prev": "ignored"})
    assert b"caf\xc3\xa9" in path.read_bytes()
    assert read_findings(path) == rows
    assert append_findings([first], path) == []


@pytest.mark.parametrize("attack", ["value", "prev", "reorder", "truncate"])
def test_tampering_refuses_reads_and_writes(tmp_path, attack):
    path = tmp_path / "findings.jsonl"
    rows = append_findings([finding(), finding(ts="2026-10-24T01:00:00Z")], path)
    if attack == "value":
        rows[0]["metric"]["value"] = .2
    elif attack == "prev":
        rows[1]["prev"] = "f" * 64
    elif attack == "reorder":
        rows.reverse()
    raw = b"".join(canonical(row) + b"\n" for row in rows)
    path.write_bytes(raw[:-1] if attack == "truncate" else raw)
    with pytest.raises(FindingsError):
        read_findings(path)
    with pytest.raises(FindingsError):
        append_findings([finding(ts="2026-10-24T02:00:00Z")], path)


@pytest.mark.parametrize("changes", [
    {"source": {"path": "", "sha256": "a" * 64}},
    {"source": {"path": "/synthetic/x", "sha256": "bad"}},
    {"metric": {"name": "accuracy", "value": 1., "ci_low": 1., "ci_high": 1.}},
    {"metric": {"name": "accuracy", "value": 1., "ci_low": 1., "ci_high": 1., "n": 0}},
])
def test_missing_provenance_is_refused(changes):
    with pytest.raises(FindingsError):
        finding(**changes)


def test_shadow_ingest_is_idempotent_and_keeps_best_arm(tmp_path):
    source = tmp_path / "status.json"
    source.write_text(json.dumps({"schema": "z0int.shadow.learning.v1", "ts": 1792800000,
        "families": {"omp.tool": {"rules": {"n": 100, "correct": 90},
                                  "slm:synthetic@rev": {"n": 100, "correct": 100}}}}))
    spec = [{"kind": "shadow_learner", "path": str(source)}]
    assert ingest_sources(spec, tmp_path)["added"] == 1
    before = ledger_path(tmp_path).read_bytes()
    result = ingest_sources(spec, tmp_path)
    assert result["added"] == 0
    assert result["sources"][0]["reason"] == "source_sha256_already_ingested"
    assert ledger_path(tmp_path).read_bytes() == before
    row = read_findings(ledger_path(tmp_path))[0]
    assert row["arm"]["model"] == "synthetic@rev"
    assert .96 < row["metric"]["ci_low"] < 1
    assert row["metric"]["live_shadow"] is True


def test_unknown_and_prose_sources_record_reason(tmp_path):
    source = tmp_path / "REPORT.md"
    source.write_text("All models are 100% accurate, n=100000. This is prose.")
    result = ingest_sources([{"kind": "engine_bench", "path": str(source)},
                             {"kind": "unknown", "path": str(source)}], tmp_path)
    assert result["added"] == 0
    assert all(row["status"] == "skipped" and row["reason"] for row in result["sources"])
    assert not ledger_path(tmp_path).exists()
    assert (tmp_path / "findings" / "ingest-status.json").exists()


def test_compile_and_ingest_fail_closed_on_corrupt_ledger(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("Z0INT_HOME", str(tmp_path))
    path = ledger_path(tmp_path)
    path.parent.mkdir(parents=True)
    path.write_bytes(b'{"broken":true}\n')
    with pytest.raises(FindingsError):
        ingest_sources([], tmp_path)
    assert main(["compile", "--json"]) == 1
    assert json.loads(capsys.readouterr().out)["stage"] == "none"
    assert not (tmp_path / "policy" / "decision-policy.json").exists()


def test_edge_probe_metrics_have_exact_explicit_arm(tmp_path):
    source = tmp_path / "results.json"
    source.write_text(json.dumps({"ts": "2026-10-24T00:00:00Z", "n": 3,
        "models": [{"id": "test@rev", "pp_tok_s": 50., "decode_tok_s": 10.,
                    "classification_correct": 8, "classification_total": 8}]}))
    spec = {"kind": "edge_probe", "path": str(source),
            "arm": {"device": "synthetic", "engine": "engine@rev", "backend": "cpu", "quant": "Q4_0"}}
    assert ingest_sources([spec], tmp_path)["added"] == 3
    rows = read_findings(ledger_path(tmp_path))
    assert {r["metric"]["name"] for r in rows} == {"pp_tok_s", "decode_tok_s", "correctness"}
    assert all(r["arm"]["quant"] == "Q4_0" and r["arm"]["device"] == "synthetic" for r in rows)


def test_z0eval_discard_cannot_promote(tmp_path):
    source = tmp_path / "eval.json"
    row = finding()
    source.write_text(json.dumps({"schema": "z0eval.result.v1", "ts": row["ts"],
        "verdict": "DISCARD", "gates": {"authority": "fail"}, "findings": [row]}))
    assert ingest_sources([{"kind": "z0eval_result", "path": str(source)}], tmp_path)["added"] == 1
    assert read_findings(ledger_path(tmp_path))[0]["metric"]["promotion_eligible"] is False


def test_labelled_shadow_ingest_requires_exact_join_and_confident_predictions(tmp_path):
    source = tmp_path / "labelled.jsonl"
    rows = [{"schema": "z0int.shadow.labelled.v1", "ts": 1792800000,
             "family": "omp.tool", "label_source": "exact_omp_session_tool_call_join",
             "executed_action": "read", "shadow": [
                 {"model": "synthetic@rev", "selected_action": "read", "confidence": .99},
                 {"model": "abstainer@rev", "selected_action": None, "confidence": .99},
                 {"model": "uncertain@rev", "selected_action": "write", "confidence": .1}]}]
    source.write_bytes(b"".join(canonical(row) + b"\n" for row in rows))
    spec = [{"kind": "shadow_learner", "path": str(source)}]
    assert ingest_sources(spec, tmp_path)["added"] == 1
    row = read_findings(ledger_path(tmp_path))[0]
    assert row["metric"]["n"] == 1 and row["metric"]["value"] == 1.
    assert row["arm"]["model"] == "synthetic@rev"
    assert ingest_sources(spec, tmp_path)["added"] == 0


def test_structured_engine_bench_and_offload_adapters(tmp_path):
    bench = tmp_path / "gpu.json"
    bench.write_text(json.dumps({"ts": "2026-10-24T00:00:00Z", "results": [
        {"device": "mbp", "engine": "synthetic@rev", "backend": "metal", "model": "model@rev",
         "quant": "Q4_0", "n": 3, "decode_tok_s": 20.}]}))
    offload = tmp_path / "opportunities.json"
    offload.write_text(json.dumps({"ts": "2026-10-24T00:00:00Z", "opportunities": [
        {"subject": {"family": "tool.trim.read", "job": "read-context"},
         "arm": {"kind": "rules", "model": "synthetic-rule@rev"}, "keep_recall": .99,
         "n": 100, "ci_low": .96, "ci_high": 1., "retained_fraction": .2,
         "evidence_class": "measured", "scope_note": "Synthetic read-context only."}]}))
    specs = [{"kind": "engine_bench", "path": str(bench)},
             {"kind": "shadow_offload", "path": str(offload)}]
    assert ingest_sources(specs, tmp_path)["added"] == 2
    assert ingest_sources(specs, tmp_path)["added"] == 0
    rows = read_findings(ledger_path(tmp_path))
    assert {row["metric"]["name"] for row in rows} == {"decode_tok_s", "keep_recall_at_20pct"}


def test_root_cli_compile_show_explain_and_json_changelog(tmp_path, monkeypatch, capsys):
    from z0int.cli import main as root_main

    monkeypatch.setenv("Z0INT_HOME", str(tmp_path))
    append_findings([finding()], ledger_path(tmp_path))
    config = tmp_path / "compiler.json"
    config.write_text(json.dumps({"compiler_revision": "a" * 40}))
    assert root_main(["policy", "compile", "--json", "--now", "2026-10-24T12:00:00Z",
                      "--config", str(config)]) == 0
    policy = json.loads(capsys.readouterr().out)
    assert policy["schema"] == "z0.decision_policy.v1"
    assert root_main(["policy", "show", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["version"] == policy["version"]
    assert root_main(["policy", "explain", "omp.tool", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["findings"]
    assert root_main(["policy", "changelog", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["changes"][0]["to"] == "shadow"


def test_noncanonical_whitespace_is_tampering(tmp_path):
    path = tmp_path / "findings.jsonl"
    rows = append_findings([finding()], path)
    path.write_text(json.dumps(rows[0], indent=None) + "\n")
    with pytest.raises(FindingsError, match="Noncanonical"):
        read_findings(path)


def test_synthetic_finding_is_optional_hashed_and_chain_preserved(tmp_path):
    real = finding()
    synthetic = finding(synthetic=True)
    assert "synthetic" not in real
    assert synthetic["synthetic"] is True
    assert real["id"] != synthetic["id"]
    path = tmp_path / "findings.jsonl"
    rows = append_findings([real, synthetic], path)
    assert read_findings(path) == rows
    rows[1]["synthetic"] = False
    path.write_bytes(b"".join(canonical(row) + b"\n" for row in rows))
    with pytest.raises(FindingsError):
        read_findings(path)
    with pytest.raises(FindingsError, match="boolean"):
        finding(synthetic="true")


def test_ingest_preserves_synthetic_receipt_and_endpoint_identity(tmp_path):
    source = tmp_path / "eval.json"
    row = finding(synthetic=True)
    source.write_text(json.dumps({"schema": "z0eval.result.v1", "ts": row["ts"],
        "verdict": "KEEP", "gates": {key: "pass" for key in
            ("contract", "authority", "quality_noninferiority", "latency_tail")},
        "findings": [row]}))
    metadata = {"expected_model_id": "synthetic-alias", "engine_build": "synthetic-build",
                "model_path": "/synthetic/alias.gguf", "model_resolved_path": "/synthetic/model.gguf",
                "model_sha256": "f" * 64}
    spec = {"kind": "z0eval_result", "path": str(source), **metadata}
    assert ingest_sources([spec], tmp_path)["added"] == 1
    emitted = read_findings(ledger_path(tmp_path))[0]
    assert emitted["synthetic"] is True
    assert {key: emitted["source"][key] for key in metadata} == metadata


def test_eval_identity_metadata_does_not_override_receipt_provenance(tmp_path):
    source = tmp_path / "receipt.json"
    row = finding(synthetic=True)
    row["source"].update(url="http://127.0.0.1:9000/v1",
                         expected_model_id="synthetic-alias",
                         model_sha256="f" * 64)
    source.write_text(json.dumps({"schema": "z0eval.result.v1", "ts": row["ts"],
        "verdict": "KEEP", "gates": {key: "pass" for key in
            ("contract", "authority", "quality_noninferiority", "latency_tail")},
        "source": {"engine_build": "synthetic-build", "model_path": "/synthetic/model.gguf"},
        "findings": [row]}))
    assert ingest_sources([{"kind": "z0eval_result", "path": str(source)}], tmp_path)["added"] == 1
    emitted = read_findings(ledger_path(tmp_path))[0]
    assert emitted["source"]["path"] == str(source.resolve())
    assert emitted["source"]["sha256"] == hashlib.sha256(source.read_bytes()).hexdigest()
    assert emitted["source"]["expected_model_id"] == "synthetic-alias"
    assert emitted["source"]["engine_build"] == "synthetic-build"
    assert emitted["source"]["model_sha256"] == "f" * 64


def test_push_only_agent_outcome_is_proxy_and_cannot_promote(tmp_path):
    from z0int.policy.compile import CompilerConfig, compile_policy

    row = finding(synthetic=True)
    row.update(run_id="fixture-run", outcome="verified_success", evidence="pushed",
               pr_url="https://example.invalid/pull/1")
    row["metric"].update(n=1000, ci_low=.99, value=1., live_shadow=True,
                         outcome_ledger_verified=True)
    source = tmp_path / "agent-outcomes.json"
    source.write_text(json.dumps({"rows": [row]}))
    assert ingest_sources([{"kind": "agent_outcomes", "path": str(source)}], tmp_path)["added"] == 1
    rows = read_findings(ledger_path(tmp_path))
    assert rows[0]["evidence_kind"] == "proxy"
    assert rows[0]["metric"]["promotion_eligible"] is False
    assert rows[0]["metric"]["outcome_ledger_verified"] is False
    policy = compile_policy(rows, None, "2026-10-24T12:00:00Z",
                            CompilerConfig(compiler_revision="a" * 40))
    assert next(iter(policy["families"].values()))["stage"] == "none"


def product_receipt(tmp_path, *, trace="fixture-run", success=True, aodl=False):
    target = tmp_path / "product-state.json"
    target.write_text(json.dumps({"feature_enabled": success}))
    observed = {"kind": "filesystem", "id": str(target), "observed": True,
                "state": json.loads(target.read_text())}
    if aodl:
        document = {"eventLog": [{"eventId": "state-update-fixture", "type": "stateUpdate",
            "sourceHash": "d" * 64, "payload": {"traceId": trace, "verified": True,
                "success": success, "verifier": "fixture-product-observer", "observed_target": observed}}]}
    else:
        document = {"schema": "z0.product_check.v1", "id": "product-check-fixture",
            "run_id": trace, "verified": True, "success": success,
            "verifier": "fixture-product-observer", "observed_target": observed}
    path = tmp_path / "verifier.json"
    path.write_bytes(canonical(document))
    reference = {"path": str(path), "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
    if aodl:
        reference["event_id"] = "state-update-fixture"
    return reference, document


@pytest.mark.parametrize("aodl", [False, True])
def test_real_target_verifier_receipt_is_bound_to_product_outcome(tmp_path, aodl):
    reference, _ = product_receipt(tmp_path, aodl=aodl)
    source = tmp_path / "agent-outcomes.json"
    row = {"run_id": "fixture-run", "ts": "2026-10-24T00:00:00Z",
           "model": "fixture-model", "task_kind": "code", "outcome": "verified_success",
           "synthetic": True, "verifier_receipt": reference}
    source.write_text(json.dumps({"rows": [row]}))
    assert ingest_sources([{"kind": "agent_outcomes", "path": str(source)}], tmp_path)["added"] == 1
    emitted = read_findings(ledger_path(tmp_path))[0]
    assert emitted["evidence_kind"] == "product_outcome"
    assert emitted["metric"]["outcome_ledger_verified"] is True
    assert emitted["metric"]["n"] == 1
    assert emitted["verifier_receipt"]["observed_target"]["state"] == {"feature_enabled": True}
    assert emitted["verifier_receipt"]["sha256"] == reference["sha256"]


@pytest.mark.parametrize("attack", ["wrong_run", "wrong_hash", "missing_observation", "test_only", "push_only"])
def test_proxy_receipts_never_mint_product_truth(tmp_path, attack):
    reference, document = product_receipt(tmp_path)
    if attack == "wrong_run":
        document["run_id"] = "unrelated-run"
    elif attack == "missing_observation":
        del document["observed_target"]
    elif attack in {"test_only", "push_only"}:
        document["observed_target"]["kind"] = "test" if attack == "test_only" else "push"
    verifier = tmp_path / "verifier.json"
    verifier.write_bytes(canonical(document))
    if attack != "wrong_hash":
        reference["sha256"] = hashlib.sha256(verifier.read_bytes()).hexdigest()
    else:
        reference["sha256"] = "0" * 64
    source = tmp_path / "agent-outcomes.json"
    row = finding(synthetic=True)
    row.update(run_id="fixture-run", verifier_receipt=reference, outcome="verified_success")
    source.write_text(json.dumps({"rows": [row]}))
    assert ingest_sources([{"kind": "agent_outcomes", "path": str(source)}], tmp_path)["added"] == 1
    emitted = read_findings(ledger_path(tmp_path))[0]
    assert emitted["evidence_kind"] == "proxy"
    assert emitted["metric"]["promotion_eligible"] is False


def test_aodl_pytest_state_update_without_product_observation_is_proxy(tmp_path):
    reference, document = product_receipt(tmp_path, aodl=True)
    payload = document["eventLog"][0]["payload"]
    payload["verifier"] = "pytest"
    del payload["observed_target"]
    verifier = tmp_path / "verifier.json"
    verifier.write_bytes(canonical(document))
    reference["sha256"] = hashlib.sha256(verifier.read_bytes()).hexdigest()
    row = finding(synthetic=True)
    row.update(run_id="fixture-run", verifier_receipt=reference)
    source = tmp_path / "agent-outcomes.json"
    source.write_text(json.dumps({"rows": [row]}))
    assert ingest_sources([{"kind": "agent_outcomes", "path": str(source)}], tmp_path)["added"] == 1
    assert read_findings(ledger_path(tmp_path))[0]["evidence_kind"] == "proxy"


def test_z0eval_export_cannot_launder_push_only_outcomes(tmp_path):
    row = finding(synthetic=True)
    row.update(run_id="fixture-run", outcome="verified_success", evidence="pushed")
    source = tmp_path / "receipt.json"
    source.write_text(json.dumps({"schema": "z0eval.result.v1", "ts": row["ts"],
        "source": {"adapter": "agent_outcomes"}, "verdict": "KEEP",
        "gates": {key: "pass" for key in
            ("contract", "authority", "quality_noninferiority", "latency_tail")},
        "findings": [row]}))
    assert ingest_sources([{"kind": "z0eval_result", "path": str(source)}], tmp_path)["added"] == 1
    assert read_findings(ledger_path(tmp_path))[0]["evidence_kind"] == "proxy"


def test_sqlite_outcome_ledger_reads_frozen_snapshot_and_ignores_duplicates(tmp_path):
    import sqlite3

    source = tmp_path / "ledger.sqlite"
    connection = sqlite3.connect(source)
    connection.execute("CREATE TABLE runs (run_id TEXT, model TEXT, task_kind TEXT, ended_at REAL, outcome TEXT, evidence TEXT, dup_of TEXT)")
    connection.executemany("INSERT INTO runs VALUES (?,?,?,?,?,?,?)", [
        ("fixture-run", "fixture-model", "code", 1792800000., "verified_success", "pushed", None),
        ("duplicate", "fixture-model", "code", 1792800000., "verified_success", "tests_pass", "fixture-run")])
    connection.commit()
    connection.close()
    spec = [{"kind": "agent_outcomes", "path": str(source), "synthetic": True}]
    before = source.read_bytes()
    assert ingest_sources(spec, tmp_path)["added"] == 1
    emitted = read_findings(ledger_path(tmp_path))[0]
    assert emitted["evidence_kind"] == "proxy"
    assert emitted["source"]["sha256"] == hashlib.sha256(before).hexdigest()
    assert source.read_bytes() == before
    assert ingest_sources(spec, tmp_path)["added"] == 0


@pytest.mark.parametrize("aodl", [False, True])
def test_product_failure_overrides_claimed_aggregate_success(tmp_path, aodl):
    reference, _ = product_receipt(tmp_path, success=False, aodl=aodl)
    row = finding(synthetic=True)
    row.update(run_id="fixture-run", outcome="verified_success", verifier_receipt=reference)
    row["metric"].update(value=1., ci_low=.99, n=1000)
    source = tmp_path / "outcomes.json"
    source.write_text(json.dumps({"rows": [row]}))
    assert ingest_sources([{"kind": "agent_outcomes", "path": str(source)}], tmp_path)["added"] == 1
    emitted = read_findings(ledger_path(tmp_path))[0]
    assert emitted["evidence_kind"] == "product_outcome"
    assert emitted["metric"]["name"] == "task_success"
    assert emitted["metric"]["value"] == 0.
    assert emitted["metric"]["n"] == 1
    assert emitted["verifier_receipt"]["success"] is False


def test_sqlite_outcome_ledger_with_wal_is_rejected(tmp_path):
    source = tmp_path / "ledger.sqlite"
    source.write_bytes(b"not read while WAL exists")
    tmp_path.joinpath("ledger.sqlite-wal").write_bytes(b"pending writes")
    report = ingest_sources([{"kind": "agent_outcomes", "path": str(source)}], tmp_path)
    assert report["added"] == 0
    assert report["sources"][0]["status"] == "skipped"
    assert "live WAL" in report["sources"][0]["reason"]
