"""Synthetic product receipts and frozen paired protocols only."""
import hashlib
from copy import deepcopy
import json
from datetime import datetime, timezone

import pytest

from z0int.policy.compile import CompilerConfig, _outcome_cohorts
from z0int.policy.findings import canonical, ledger_path, read_findings
from z0int.policy.ingest import ingest_sources


@pytest.mark.parametrize("adapter", ["agent_outcomes", "z0eval_result"])
def test_receipts_form_only_bound_independent_pairs(tmp_path, adapter):
    subject = {"family": "agent.outcome.fixture", "job": "fixture"}
    candidate = {"kind": "llm", "model": "candidate"}
    reference = {"kind": "llm", "model": "reference"}
    protocol = {"schema": "z0.policy.paired_outcome_protocol.v1", "subject": subject,
        "candidate_arm": candidate, "reference_arm": reference, "experiment_id": "exp",
        "frozen_at": "2026-10-23T00:00:00Z", "expires_at": "2026-10-26T00:00:00Z",
        "noninferiority_margin": .9, "expected_roles": ["candidate", "reference"],
        "expected_pairs": [{"pair_id": f"pair-{i}", "task_snapshot_id": f"snapshot-{i}"}
                           for i in range(2)]}
    path = tmp_path / "protocol.json"
    raw = canonical(protocol)
    path.write_bytes(raw)
    sha = hashlib.sha256(raw).hexdigest()
    rows = []
    for pair in range(2):
        for role, arm in (("candidate", candidate), ("reference", reference)):
            trace = f"{role}-{pair}"
            ts = "2026-10-24T00:00:00Z"
            evaluation = {"protocol_sha256": sha, "subject": subject, "arm": arm,
                "experiment_id": "exp", "pair_id": f"pair-{pair}",
                "task_snapshot_id": f"snapshot-{pair}", "arm_id": role, "observed_at": ts}
            document = {"schema": "z0.product_check.v1", "id": trace, "trace_id": trace,
                "verified": True, "verifier": "fixture", "success": True,
                "observed_target": {"kind": "product", "id": "fixture", "observed": True,
                                    "state": {"ok": True}}, "evaluation": evaluation}
            receipt = tmp_path / (trace + ".json")
            receipt_raw = json.dumps(document).encode()
            receipt.write_bytes(receipt_raw)
            rows.append({"run_id": trace, "ts": ts, "subject": subject, "arm": arm,
                "evidence_class": "canary", "scope_note": "Synthetic fixture.",
                "metric": {"name": "task_success", "value": 1., "n": 999, "ci_low": 1.,
                           "ci_high": 1., "ci_method": "wilson95", "higher_is_better": True},
                "evidence_kind": "product_outcome",
                "verifier_receipt": {"path": str(receipt), "sha256": hashlib.sha256(receipt_raw).hexdigest()},
                "outcome_protocol": {"path": str(path), "sha256": sha}})
    data = {"rows": rows} if adapter == "agent_outcomes" else {
        "schema": "z0eval.result.v1", "ts": rows[0]["ts"], "verdict": "KEEP",
        "gates": {k: "pass" for k in ("contract", "authority", "quality_noninferiority", "latency_tail")},
        "findings": rows}
    source = tmp_path / "source.json"
    source.write_text(json.dumps(data))
    assert ingest_sources([{"kind": adapter, "path": str(source), "synthetic": True}], tmp_path)["added"] == 4
    findings = read_findings(ledger_path(tmp_path))
    assert all(row["metric"]["n"] == 1 for row in findings)
    now = datetime(2026, 10, 24, 12, tzinfo=timezone.utc)
    config = CompilerConfig(compiler_revision="test", n_live=2, bar=.1)
    cohort = _outcome_cohorts(findings, now, config, subject, candidate)
    assert len(cohort) == 1 and {r["id"] for r in cohort[0]} == {r["id"] for r in findings}
    assert len(_outcome_cohorts(findings + [findings[0]], now, config, subject, candidate)) == 1
    assert not _outcome_cohorts(findings[:-1], now, config, subject, candidate)
    assert not _outcome_cohorts(findings, datetime(2026, 10, 26, tzinfo=timezone.utc), config, subject, candidate)
    assert not _outcome_cohorts(findings, datetime(2026, 10, 23, tzinfo=timezone.utc), config, subject, candidate)
    assert not _outcome_cohorts(findings, now, config, {**subject, "job": "other"}, candidate)
    assert not _outcome_cohorts(findings, now, config, subject, {**candidate, "model": "other"})

    for change in ("trace", "receipt", "snapshot", "protocol", "proxy"):
        altered = deepcopy(findings)
        if change == "trace":
            altered[2]["verifier_receipt"]["trace_id"] = altered[0]["verifier_receipt"]["trace_id"]
        elif change == "receipt":
            altered[2]["verifier_receipt"]["receipt_id"] = altered[0]["verifier_receipt"]["receipt_id"]
        elif change == "snapshot":
            altered[2]["verifier_receipt"]["evaluation"]["task_snapshot_id"] = "different"
        elif change == "protocol":
            altered[2]["verifier_receipt"]["evaluation"]["protocol_sha256"] = "0" * 64
        else:
            altered[2]["evidence_kind"] = "proxy"
        assert not _outcome_cohorts(altered, now, config, subject, candidate)
    altered = deepcopy(findings)
    for row in altered:
        row["baseline"]["outcome_protocol"]["document"]["noninferiority_margin"] = .99
    assert not _outcome_cohorts(altered, now, config, subject, candidate)

    extra = deepcopy(findings[0])
    extra["verifier_receipt"]["trace_id"] = "unknown-trace"
    extra["verifier_receipt"]["receipt_id"] = "unknown-receipt"
    extra["verifier_receipt"]["sha256"] = "f" * 64
    extra["verifier_receipt"]["evaluation"]["pair_id"] = "unknown-pair"
    extra["verifier_receipt"]["evaluation"]["task_snapshot_id"] = "unknown-snapshot"
    assert not _outcome_cohorts(findings + [extra], now, config, subject, candidate)

    duplicate = deepcopy(extra)
    duplicate["verifier_receipt"]["evaluation"]["pair_id"] = "pair-0"
    duplicate["verifier_receipt"]["evaluation"]["task_snapshot_id"] = "snapshot-0"
    assert not _outcome_cohorts(findings + [duplicate], now, config, subject, candidate)