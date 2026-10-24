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
