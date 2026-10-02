"""Offline rejection controls using the real OMP/HTTP integration fixture.

The provider response in this fixture is synthetic. No test calls a provider,
loads credentials, changes the fixture, or promotes it to live evidence.
"""
from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

import independent_study_check as checker

FIXTURE = Path(__file__).parent / "offline-integration-final" / "run-01"


@pytest.fixture
def data():
    return checker.load_artifacts(FIXTURE)


def set_path(value, path, replacement):
    for part in path[:-1]:
        value = value[part]
    value[path[-1]] = replacement


def root_row(data, capability, *, first=False):
    rows = [
        row for row in data["receipts"]
        if row["capability_id"] == capability
        and row["extra"].get("caller_trace_id") == checker.ROOT_TRACE
    ]
    return rows[0] if first else rows[-1]


def physical_rows(data):
    identity = data["summary"]["physical_receipt"]
    return [row for row in data["receipts"] if row["trace_id"] == identity]


def reject(data, match=None):
    with pytest.raises(checker.CheckError, match=match):
        checker.validate_artifacts(data)


def test_real_offline_integration_satisfies_contract_without_live_promotion(data):
    before = copy.deepcopy(data)
    result = checker.validate_artifacts(data)
    assert result["passed"] is True
    assert result["live_evidence"] is False
    assert result["evidence_mode"] == "offline_fixture"
    assert result["task_a_pass"] is False
    assert result["task_b_import_allowed"] is False
    assert result["physical_call_count"] == 1
    assert data == before
    assert data["summary"]["head"] == "293129b57ed6cc2b8ddab9d5a8b44bcb213d4be4"


@pytest.mark.parametrize(("path", "value", "error"), [
    (("request", "endpoint"), "https://example.invalid/v1/chat/completions", "endpoint"),
    (("request", "payload", "model"), "other-model", "requested model"),
    (("request", "payload", "max_tokens"), 129, "completion cap"),
    (("request", "payload", "max_tokens"), 128.0, "exact nonnegative integer"),
    (("request", "payload", "max_tokens"), True, "exact nonnegative integer"),
    (("request", "payload", "temperature"), 0.5, "temperature"),
    (("request", "payload", "temperature"), False, "temperature"),
    (("request", "payload", "messages", 1, "content"), "Do a different task", "messages differ"),
    (("response", "choices", 0, "message", "content"), "WRONG", "exact output"),
    (("response", "choices", 0, "finish_reason"), "length", "stop normally"),
    (("response", "choices", 0, "message", "tool_calls"), [{"id": "tool"}], "tool calls"),
    (("response", "choices", 0, "message", "function_call"), {"name": "tool"}, "legacy function"),
    (("response", "choices", 0, "message", "refusal"), "refused", "refusal"),
    (("response", "model"), "other-model", "response model"),
    (("response", "id"), "", "response id"),
    (("response", "usage", "prompt_tokens"), True, "exact nonnegative integer"),
    (("response", "usage", "completion_tokens"), False, "exact nonnegative integer"),
    (("response", "usage", "prompt_tokens"), -1, "exact nonnegative integer"),
    (("response", "usage", "completion_tokens"), -1, "exact nonnegative integer"),
    (("response", "usage", "prompt_tokens"), 67.0, "exact nonnegative integer"),
    (("response", "usage", "completion_tokens"), 129, "exceeds cap"),
    (("response", "usage", "total_tokens"), 73, "total token"),
    (("response", "usage", "total_tokens"), True, "exact nonnegative integer"),
    (("response", "usage", "cost"), None, "response cost"),
    (("response", "usage", "cost"), True, "response cost"),
    (("response", "usage", "cost"), -0.001, "response cost"),
    (("response", "usage", "cost"), 0.001001, "cost bound"),
    (("response", "usage", "cost"), float("nan"), "response cost"),
    (("response", "usage", "cost"), float("inf"), "response cost"),
    (("outcomes", 0, "outcome", "verified_success"), False, "verified_success must be true"),
    (("outcomes", 0, "outcome", "verified_success"), 1, "verified_success must be true"),
    (("outcomes", 0, "outcome", "verified"), False, "verified must be true"),
    (("outcomes", 0, "outcome", "verification_source"), "unrelated_verifier", "verifier identity"),
    (("outcomes", 0, "outcome_tier"), "execution", "gold outcome tier"),
    (("outcomes", 0, "trace_id"), checker.ROOT_TRACE, "physical gold outcome"),
    (("summary", "cap_refused"), False, "summary control"),
    (("summary", "restart_replay"), 1, "summary control"),
    (("summary", "parent_model_called"), True, "parent model"),
    (("summary", "task_b_import_allowed"), True, "must not promote"),
    (("replay", "output"), "WRONG", "replay output"),
    (("replay", "replayed"), False, "replay not successful"),
    (("capped", "ok"), True, "cap refusal"),
    (("uncertain", "executed"), True, "uncertain response"),
])
def test_request_response_outcome_and_control_mutations_are_rejected(data, path, value, error):
    set_path(data, path, value)
    reject(data, error)


@pytest.mark.parametrize("field", ["response_format", "seed", "tools", "extra_body"])
def test_no_extra_generation_modes_are_accepted(data, field):
    data["request"]["payload"][field] = {}
    reject(data, "payload keys")


@pytest.mark.parametrize(("field", "value"), [
    ("output_sha256", "0" * 64),
    ("response_id", "different-response"),
    ("response_model", "different-model"),
    ("provider_reported_cost_usd", None),
    ("provider_reported_cost_usd", -1),
    ("provider_reported_cost_usd", True),
    ("authority_dispatch_id", "different-dispatch"),
])
def test_mismatched_terminal_receipt_is_rejected(data, field, value):
    for row in physical_rows(data)[1:]:
        row["extra"][field] = value
    reject(data)


@pytest.mark.parametrize("value", [True, -1, 67.0])
def test_receipt_usage_requires_exact_nonnegative_integer(data, value):
    for row in physical_rows(data)[1:]:
        row["input_tokens"] = value
    reject(data, "receipt input_tokens")


@pytest.mark.parametrize(("field", "value"), [
    ("model", "different-model"), ("provider", "different-provider"), ("free_only", True),
])
def test_frozen_preparation_controls_provider_and_model(data, field, value):
    root_row(data, "aodl.governed_request")["extra"]["frozen_governance"][field] = value
    reject(data, "frozen provider/model")


def test_coherently_changed_intent_lineage_is_rejected(data):
    changed = "f" * 64

    def alter(value):
        if isinstance(value, dict):
            for key, item in value.items():
                if key in ("sourceHash", "aodl_intent_source_hash"):
                    value[key] = changed
                else:
                    alter(item)
        elif isinstance(value, list):
            for item in value:
                alter(item)
    alter(data)
    reject(data, "pinned contract")


def test_requested_authority_escalation_is_rejected_even_with_allow_marker(data):
    frozen = root_row(data, "aodl.governed_request")["extra"]["frozen_governance"]
    frozen["aodl"]["spawn"]["requested"] = ["execute", "verify"]
    assert root_row(data, "aodl.structural_admission")["extra"]["aodl_admission"]["allowed"] is True
    reject(data, "spawn authority")


@pytest.mark.parametrize(("capability", "field"), [
    ("aodl.structural_admission", "request_sha256"),
    ("aodl.governed_request", "caller_request_sha256"),
])
def test_authority_request_fingerprints_are_bound(data, capability, field):
    root_row(data, capability)["extra"][field] = "f" * 64
    reject(data, "fingerprint")


def test_physical_start_cannot_change_permit(data):
    physical_rows(data)[0]["extra"]["admission_token"] = "different-permit"
    reject(data, "physical permit changed")


def test_dispatch_start_cannot_change_owner(data):
    root_row(data, "intelligence.dispatch", first=True)["extra"]["authority_owner_sha256"] = "f" * 64
    reject(data, "dispatch identity changed")


def test_uncompleted_physical_receipt_does_not_mint_success(data):
    physical_rows(data)[1]["outcome"]["execution_completed"] = False
    reject(data, "execution was not completed")


def test_result_cannot_reference_mismatched_receipt(data):
    result = root_row(data, "intelligence.dispatch")["extra"]["result"]
    result["attempts"][0]["extra"]["output_sha256"] = "f" * 64
    reject(data, "result receipt mismatch")


def test_wrong_provider_permit_dispatch_is_rejected(data):
    permit = root_row(data, "intelligence.provider_admission")
    permit["extra"]["permit_dispatch_id"] = "wrong-dispatch"
    reject(data, "permit links")


def test_any_second_physical_attempt_is_rejected_even_outside_root(data):
    additional = copy.deepcopy(physical_rows(data)[1])
    additional["trace_id"] = "other-call"
    additional["extra"]["caller_trace_id"] = "other-root"
    data["receipts"].append(additional)
    reject(data, "exactly one physical call")


def test_later_negative_gold_cannot_hide_prior_success(data):
    later = copy.deepcopy(data["outcomes"][0])
    later["outcome"]["verified_success"] = False
    data["outcomes"].append(later)
    reject(data, "physical gold outcome")


def test_offline_marker_cannot_be_promoted(data):
    with pytest.raises(checker.CheckError, match="offline fixture"):
        checker.validate_artifacts(data, require_live=True)


def test_offline_response_id_still_blocks_promotion_after_marker_removal(data):
    data["offline_marker"] = None
    with pytest.raises(checker.CheckError, match="offline fixture"):
        checker.validate_artifacts(data, require_live=True)


def test_directory_entry_point_reports_offline_and_failure():
    result = checker.check_directory(FIXTURE)
    assert result["passed"] and not result["live_evidence"]
    failed = checker.check_directory(FIXTURE, require_live=True)
    assert failed["passed"] is False
    assert failed["live_evidence"] is False


@pytest.mark.parametrize("text", ['{"cost":NaN}', '{"cost":Infinity}', '{"x":1,"x":2}'])
def test_ambiguous_json_is_rejected(text):
    with pytest.raises(checker.CheckError):
        checker.decode_json(text)


def test_truncated_ledger_is_rejected(tmp_path):
    path = tmp_path / "decisions.jsonl"
    path.write_text('{"trace_id":"truncated"}')
    with pytest.raises(checker.CheckError, match="truncated"):
        checker.read_jsonl(path)


def test_missing_artifacts_are_a_failed_check(tmp_path):
    report = checker.check_directory(tmp_path)
    assert report["passed"] is False
    assert report["live_evidence"] is False

