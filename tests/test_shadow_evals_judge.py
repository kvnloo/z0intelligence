"""Offline behavior tests. No model calls, credentials, tools, or policy writes."""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from z0int import shadow_evals_judge as judge


def judgment(*, dimension=None, score=0, unknown=None):
    dimensions = {
        name: {"score": 2, "evidence": ["packet:1 supports this dimension or makes it inapplicable"], "fact_ids": []}
        for name in judge.DIMENSIONS
    }
    if dimension:
        dimensions[dimension] = {"score": score, "evidence": ["policy:3 requirement is absent from the draft"],
                                 "fact_ids": ["fact-phone-target"]}
    if unknown:
        dimensions[unknown] = {"score": None, "evidence": ["Required historical evidence is unavailable"], "fact_ids": []}
    miss = True if dimension and score < 2 else None if unknown else False
    return {"dimensions": dimensions, "miss_detected": miss,
            "category": dimension if miss else "unknown" if unknown else "none", "task_category": "operations"}


def calibration_rows():
    return [{"case_id": f"case-{index}", "gold_miss": index < 103, "status": "ready", "missing_gold": [],
             "judgment": judgment(dimension="coverage") if index < 103 else judgment()}
            for index in range(153)]


def dump_jsonl(path, rows):
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")


@pytest.fixture
def audit(tmp_path):
    labels, contexts, dialogue = [], [], []
    for index in range(163):
        positive = index < 103
        session = f"session-{index}"
        common = {"harness": "omp", "session": session}
        ask = {**common, "entry_id": f"ask-{index}", "role": "user",
               "ts": "2026-10-01T00:00:00+00:00", "text": f"original ask {index}"}
        answer = {**common, "entry_id": f"answer-{index}", "role": "assistant",
                  "ts": "2026-10-01T00:00:01+00:00", "text": f"original answer {index}"}
        correction = {**common, "entry_id": f"correction-{index}", "role": "user",
                      "ts": "2026-10-01T00:00:02+00:00", "text": f"FORBIDDEN_CORRECTION_{index}"}
        future = {**common, "entry_id": f"future-{index}", "role": "assistant",
                  "ts": "2026-10-01T00:00:03+00:00", "text": f"FORBIDDEN_FUTURE_{index}"}
        target = correction if positive else ask
        label = {**common, "entry_id": target["entry_id"], "ts": target["ts"], "review_id": index,
                 "outcome": "ignored" if positive else "not-a-miss", "cause": "test-case",
                 "text": target["text"], "reason": "FORBIDDEN_LABEL_REASON", "evidence": [
                     {"kind": "ask", "entry_id": ask["entry_id"], "session": session, "ts": ask["ts"]},
                     # Some real labels cite acknowledgments after the correction.
                     {"kind": "agent", "entry_id": future["entry_id"], "session": session, "ts": future["ts"]},
                 ]}
        labels.append(label)
        contexts.append({"review_id": index, "target": {**target, "outcome": "FORBIDDEN_OUTCOME"},
                         "before": [ask, answer] if positive else [],
                         "after": [future] if positive else [answer, correction, future]})
        dialogue.extend([ask, answer, correction, future])
    dump_jsonl(tmp_path / "labels.jsonl", labels)
    dump_jsonl(tmp_path / "final_review_contexts.jsonl", contexts)
    dump_jsonl(tmp_path / "dialogue.jsonl", dialogue)
    return tmp_path, labels, contexts, dialogue


def test_rubric_has_seven_explicit_zero_to_two_anchors():
    assert set(judge.RUBRIC) == {"coverage", "context", "constraints", "conflicts", "verifier", "delegation", "hallucination"}
    for anchors in judge.RUBRIC.values():
        assert set(anchors) == {0, 1, 2}
        assert all(isinstance(anchor, str) and anchor for anchor in anchors.values())


def test_prompt_is_blind_and_contains_exact_flat_evidence_once():
    fact = {"id": "F123", "source": "policy", "source_id": "standing", "line": 3,
            "text": "EXACT_POLICY_SENTENCE", "text_sha256": "a" * 64}
    bundle = {"ask": "EXACT_ORIGINAL_ASK", "recent_user_msgs": ["earlier user fact"],
              "facts": [fact], "sources": [{"source": "memory", "status": "unavailable"}],
              "privacy": {"allowed": True}, "bundle_hash": "b" * 64,
              "packet": [{"text": "DUPLICATE_PACKET"}], "memory": [fact],
              "fact_catalog": {"F123": fact}, "label": "FORBIDDEN_LABEL"}
    prompt = judge.judge_prompt(
        {"text": "DUPLICATE_JOB_ASK", "gold_miss": True, "correction_text": "FORBIDDEN_CORRECTION",
         "actual_outcome": "FORBIDDEN_PARENT_SUCCESS", "after": ["FORBIDDEN_FUTURE"]}, bundle,
        {"provider": "FORBIDDEN_PROVIDER", "model": "FORBIDDEN_MODEL", "content": {
            "interpretation": "draft", "intent_contract": {"deliverables": ["keep this exact target"]},
            "plan": {"success": "I ran an invented check"}}},
    )
    data = json.loads(prompt.split("\nBLIND_INPUT:\n", 1)[1])
    assert data["bundle"]["facts"] == [fact]
    assert data["bundle"]["sources"][0]["status"] == "unavailable"
    assert data["candidate"]["intent_contract"]["deliverables"] == ["keep this exact target"]
    assert data["candidate"]["plan"]["success"] == "I ran an invented check"
    assert prompt.count("EXACT_POLICY_SENTENCE") == 1
    assert prompt.count("EXACT_ORIGINAL_ASK") == 1
    assert "DUPLICATE" not in prompt
    assert "FORBIDDEN" not in prompt
    assert "Do not call tools" in prompt


def test_prompt_strips_nested_gold_metadata_but_preserves_numbered_context():
    prompt = judge.judge_prompt({"payload": {"text": "original ask", "recent_messages": [
        {"role": "user", "text": "prior fact", "labels": ["LEAK"]}]}},
        {"packet": {"lines": [{"number": 4, "text": "context fact", "correction": "LEAK"}]},
         "memory": {"status": "unavailable"}, "inventory": {"status": "stale"},
         "gold": "LEAK"}, {"content": "draft"})
    data = json.loads(prompt.split("\nBLIND_INPUT:\n", 1)[1])
    assert data["bundle"]["packet"]["lines"] == [{"number": 4, "text": "context fact"}]
    assert "LEAK" not in prompt


def test_parser_accepts_complete_json_and_fenced_json():
    expected = judge.parse_judgment(judgment())
    assert expected["valid"] and expected["complete"]
    assert expected["miss_detected"] is False
    assert judge.parse_judgment("```json\n" + json.dumps(judgment()) + "\n```") == expected
    assert judge.parse_judgment(json.dumps(expected)) == expected


@pytest.mark.parametrize("dimension", judge.DIMENSIONS)
@pytest.mark.parametrize("score", [0, 1])
def test_each_missed_dimension_is_a_failure_not_compensated_by_other_scores(dimension, score):
    parsed = judge.parse_judgment(judgment(dimension=dimension, score=score))
    assert parsed["valid"] and parsed["complete"]
    assert parsed["miss_detected"] is True
    assert parsed["category"] == dimension
    assert "total" not in parsed


def test_unknown_evidence_is_not_no_miss():
    parsed = judge.parse_judgment(judgment(unknown="context"))
    assert parsed["valid"] and not parsed["complete"]
    assert parsed["miss_detected"] is None
    with_known_miss = judge.parse_judgment(judgment(dimension="coverage", unknown="context"))
    assert with_known_miss["miss_detected"] is True
    assert not with_known_miss["complete"]


@pytest.mark.parametrize("alter", [
    lambda row: row["dimensions"].pop("coverage"),
    lambda row: row["dimensions"]["coverage"].update(score=True),
    lambda row: row["dimensions"]["coverage"].update(score=3),
    lambda row: row["dimensions"]["coverage"].update(score=1.0),
    lambda row: row["dimensions"]["coverage"].update(evidence=[]),
    lambda row: row["dimensions"]["coverage"].update(fact_ids=["same", "same"]),
    lambda row: row.update(miss_detected=True),
    lambda row: row.update(category="coverage"),
    lambda row: row.update(autoapply=True),
])
def test_parser_rejects_invalid_or_self_contradictory_scores(alter):
    row = judgment()
    alter(row)
    parsed = judge.parse_judgment(row)
    assert parsed["valid"] is False
    assert parsed["miss_detected"] is None
    assert parsed["dimensions"] == {}


def test_parser_rejects_duplicate_keys_and_unstructured_prose():
    assert not judge.parse_judgment('{"dimensions":{},"dimensions":{}}')["valid"]
    assert not judge.parse_judgment("looks good")["valid"]
    assert not judge.parse_judgment(None)["valid"]


def test_calibration_selection_is_exact_deterministic_and_blind(audit):
    root, labels, _, _ = audit
    cases = judge.calibration_cases(root)
    assert len(cases) == 153
    assert sum(case["gold_miss"] for case in cases) == 103
    assert sum(not case["gold_miss"] for case in cases) == 50
    assert all(case["status"] == "ready" for case in cases)
    assert all(case["candidate_success"] is None for case in cases)
    assert all(case["gold_scope"] == "reviewed_failure_label_not_task_success" for case in cases)
    for case in cases:
        prompt = judge.judge_prompt(case["job"], case["bundle"], case["candidate"])
        assert "FORBIDDEN" not in prompt
        assert "original answer" in prompt
        assert case["candidate"]["content"].startswith("original answer")
    dump_jsonl(root / "labels.jsonl", list(reversed(labels)))
    reordered = judge.calibration_cases(root)
    assert [case["case_id"] for case in reordered] == [case["case_id"] for case in cases]


def test_later_acknowledgment_cannot_replace_missing_original_answer(audit):
    root, _, contexts, dialogue = audit
    dialogue = [row for row in dialogue if row["entry_id"] != "answer-0"]
    contexts[0]["before"] = [row for row in contexts[0]["before"] if row["entry_id"] != "answer-0"]
    dump_jsonl(root / "dialogue.jsonl", dialogue)
    dump_jsonl(root / "final_review_contexts.jsonl", contexts)
    case = next(case for case in judge.calibration_cases(root) if case["review_id"] == 0)
    assert case["status"] == "missing_gold"
    assert case["candidate"] == {}
    assert any("later acknowledgments are excluded" in reason for reason in case["missing_gold"])


def test_missing_context_is_reported_not_silently_replaced(audit):
    root, _, contexts, _ = audit
    dump_jsonl(root / "final_review_contexts.jsonl", contexts[1:])
    case = next(case for case in judge.calibration_cases(root) if case["review_id"] == 0)
    assert case["status"] == "missing_gold"
    assert "Missing selected review context" in case["missing_gold"]


def test_unknown_outcomes_are_not_treated_as_failures_or_successes(audit):
    root, labels, _, _ = audit
    labels[0]["outcome"] = "silence"
    dump_jsonl(root / "labels.jsonl", labels)
    with pytest.raises(judge.JudgeError, match="Unknown audit outcomes"):
        judge.calibration_cases(root)


def test_missing_failure_gold_fails_exact_selection(audit):
    root, labels, _, _ = audit
    dump_jsonl(root / "labels.jsonl", labels[1:])
    with pytest.raises(judge.JudgeError, match="Missing calibration gold"):
        judge.calibration_cases(root)


def test_duplicate_audit_labels_are_not_independent_cases(audit):
    root, labels, _, _ = audit
    dump_jsonl(root / "labels.jsonl", labels + [labels[0]])
    with pytest.raises(judge.JudgeError, match="unique"):
        judge.calibration_cases(root)


def test_complete_calibration_reports_counts_precision_recall_and_enables_scores():
    metrics = judge.calibration_metrics(calibration_rows())
    assert metrics["measurement_complete"] is True and metrics["scores_enabled"] is True
    assert (metrics["tp"], metrics["fp"], metrics["fn"], metrics["tn"]) == (103, 0, 0, 50)
    assert metrics["precision"] == metrics["recall"] == 1
    assert metrics["unknown_gold"] == metrics["unknown_prediction"] == 0
    assert len(metrics["calibration_id"]) == 64


def test_precision_recall_use_failure_as_positive_class():
    rows = calibration_rows()
    rows[0]["judgment"] = judgment()
    rows[-1]["judgment"] = judgment(dimension="context")
    metrics = judge.calibration_metrics(rows)
    assert (metrics["tp"], metrics["fp"], metrics["fn"], metrics["tn"]) == (102, 1, 1, 49)
    assert metrics["precision"] == metrics["recall"] == 102 / 103


def test_unknown_prediction_stays_in_recall_denominator_and_disables_scores():
    rows = calibration_rows()
    rows[0]["judgment"] = judgment(unknown="context")
    metrics = judge.calibration_metrics(rows)
    assert metrics["table"]["failure"]["unknown"] == 1
    assert metrics["recall"] == 102 / 103
    assert metrics["recall_on_scored"] == 1
    assert not metrics["scores_enabled"]


def test_empty_metrics_do_not_fabricate_perfect_precision_or_recall():
    metrics = judge.calibration_metrics([])
    assert metrics["precision"] is None and metrics["recall"] is None
    assert metrics["scores_enabled"] is False


@pytest.mark.parametrize("alter", [
    lambda rows: rows.pop(),
    lambda rows: rows[0].update(gold_miss=None),
    lambda rows: rows[0].update(status="missing_gold", missing_gold=["missing original ask"]),
    lambda rows: rows[0].update(case_id=rows[1]["case_id"]),
    lambda rows: rows[0].update(judgment={}),
    lambda rows: rows[0].update(judgment=judgment(dimension="coverage", unknown="context")),
])
def test_any_incomplete_calibration_disables_scores(alter):
    rows = calibration_rows()
    alter(rows)
    assert not judge.calibration_metrics(rows)["scores_enabled"]


def real_row(job_id="job-1", model="a", *, miss=True, context_hash="a" * 64):
    return {"provider": "subscription", "model": model, "task": "CONTRACT", "job_id": job_id,
            "real_job": True, "synthetic": False, "calibrated": True,
            "context_hash": context_hash, "category": "operations",
            "judgment": judgment(dimension="coverage") if miss else judgment(),
            "compared_models": ["subscription:a", "subscription:b", "subscription:c"],
            "fact_catalog": {"fact-phone-target": {"text": "Use the exact authorized phone target.",
                                                     "source": "/policy/phones:3"}}}


@pytest.fixture
def finding_schema(monkeypatch):
    """Record canonical schema calls without copying the deployed schema here."""
    constructed, validated = [], []

    class CanonicalFinding:
        def __init__(self, **kwargs):
            constructed.append(kwargs)
            self.kwargs = kwargs

        def to_dict(self):
            return {**self.kwargs, "schema": "z0.finding.v1", "prev": ""}

    def finding_id(row):
        content = {key: value for key, value in row.items() if key not in {"id", "prev"}}
        return hashlib.sha256(json.dumps(content, sort_keys=True).encode()).hexdigest()

    def validate(row):
        assert row["id"] == finding_id(row)
        validated.append(deepcopy(row))

    schema = SimpleNamespace(Finding=CanonicalFinding, finding_id=finding_id, validate=validate)
    monkeypatch.setattr(judge.importlib, "import_module", lambda name: schema)
    return constructed, validated


def scope(tmp_path):
    source = tmp_path / "results.jsonl"
    source.write_text('{"real_result_reference":"job-1"}\n', encoding="utf-8")
    return {"source_path": str(source), "ts": "2026-10-24T00:00:00+00:00",
            "calibration": judge.calibration_metrics(calibration_rows())}


def test_findings_require_complete_calibration_before_schema_loading(tmp_path, monkeypatch):
    monkeypatch.setattr(judge.importlib, "import_module", lambda name: pytest.fail("must not load schema"))
    with pytest.raises(judge.JudgeError, match="Scores disabled"):
        judge.findings([real_row()], {"calibration": judge.calibration_metrics([])})


def test_missing_canonical_dependency_is_actionable_and_does_not_break_import(tmp_path, monkeypatch):
    def missing(name):
        raise ModuleNotFoundError("No module named 'z0int.policy'", name="z0int.policy")
    monkeypatch.setattr(judge.importlib, "import_module", missing)
    with pytest.raises(judge.JudgeError, match="integrate that dependency"):
        judge.findings([real_row()], scope(tmp_path))
    assert judge.parse_judgment(judgment())["valid"]


def test_findings_use_canonical_schema_exact_grouping_wilson_and_explicit_safety_marks(tmp_path, finding_schema):
    spec = scope(tmp_path)
    rows = [real_row("job-1"), real_row("job-2"), real_row("job-3", "b"),
            real_row("job-4", context_hash="b" * 64), real_row("job-5", miss=False)]
    rows.append(deepcopy(rows[0]))
    before = Path(spec["source_path"]).read_bytes()
    exported = judge.findings(rows, spec)
    assert len(exported) == 3
    target = next(row for row in exported if row["metric"]["n"] == 3)
    assert target["subject"]["job_ids"] == ["job-1", "job-2", "job-5"]
    assert target["arm"] == {"kind": "frontier", "provider": "subscription", "model": "a"}
    assert target["subject"]["family"] == "model.routing.acceptance"
    assert target["subject"]["job"] == "ShadowEvals"
    assert target["subject"]["task"] == "intent.shadow.operations"
    assert target["subject"]["miss_categories"] == ["coverage", "none"]
    assert target["subject"]["task_class"] == "CONTRACT"
    assert target["metric"]["value"] == 1 / 3
    assert target["metric"]["accepted"] == 1
    assert 0 < target["metric"]["ci_low"] < target["metric"]["value"] < target["metric"]["ci_high"] < 1
    assert target["source"]["sha256"] == hashlib.sha256(before).hexdigest()
    assert len(finding_schema[0]) == len(finding_schema[1]) == 3
    for row in exported:
        assert row["schema"] == "z0.finding.v1"
        assert row["evidence_kind"] == "proxy"
        assert row["source"]["kind"] == "shadow_offload"
        assert row["metric"]["name"] == "acceptance_rate"
        assert row["metric"]["acceptance_kind"] == "rubric_draft_quality"
        assert row["metric"]["accepted"] == row["metric"]["value"] * row["metric"]["n"]
        assert row["synthetic"] is False
        assert row["metric"]["promotion_eligible"] is False
        assert row["metric"]["candidate_execution_success"] is None
        assert row["metric"]["ci_method"] == "wilson95"
    assert Path(spec["source_path"]).read_bytes() == before


def test_parent_success_and_unknown_outcomes_never_credit_candidates(tmp_path, finding_schema):
    row = real_row()
    row["actual_outcome"] = {"verified_success": True}
    exported = judge.findings([row], scope(tmp_path))
    assert exported[0]["metric"]["value"] == 0
    assert exported[0]["metric"]["candidate_execution_success"] is None


@pytest.mark.parametrize("override", [
    {"synthetic": True}, {"real_job": False}, {"synthetic": None}, {"calibrated": False},
    {"judgment": {}}, {"judgment": judgment(unknown="context")},
])
def test_ineligible_rows_never_produce_findings(tmp_path, finding_schema, override):
    row = real_row()
    row.update(override)
    assert judge.findings([row], scope(tmp_path)) == []


def test_conflicting_duplicate_measurements_are_not_double_counted(tmp_path, finding_schema):
    first, second = real_row(), real_row(miss=False)
    with pytest.raises(judge.JudgeError, match="Conflicting repeated judgment"):
        judge.findings([first, second], scope(tmp_path))


def test_exact_category_mismatch_fails_export(tmp_path, finding_schema):
    row = real_row()
    row["category"] = "context"
    with pytest.raises(judge.JudgeError, match="category differs"):
        judge.findings([row], scope(tmp_path))


def law_rows():
    return [real_row(job, model, context_hash=hashlib.sha256(job.encode()).hexdigest())
            for job in ("real-job-1", "real-job-2") for model in ("a", "b", "c")]


def test_repeated_all_model_same_fact_dimension_misses_create_review_only_proposal():
    report = judge.law_candidates(law_rows())
    assert "## coverage /" in report
    assert "Repeated real jobs: 2" in report
    assert "Proposed packet change:" in report
    assert "Proposed law change:" in report
    assert "Uncertainty:" in report
    assert "Parent review only" in report
    assert "subscription:a" in report and "subscription:b" in report and "subscription:c" in report


@pytest.mark.parametrize("alter", [
    lambda rows: rows.pop(),
    lambda rows: rows[0].update(judgment=judgment(), category="none"),
    lambda rows: rows[0].update(judgment=judgment(dimension="context"), category="context"),
    lambda rows: rows[0].update(judgment=judgment(unknown="context")),
    lambda rows: rows[0].update(fact_catalog={}),
    lambda rows: rows[0]["fact_catalog"]["fact-phone-target"].update(text="A different fact"),
    lambda rows: rows[0].update(synthetic=True),
    lambda rows: rows[0].update(calibrated=False),
    lambda rows: rows[0].update(compared_models=["subscription:a", "subscription:b"]),
])
def test_incomplete_or_nonunanimous_comparisons_cannot_propose_a_law(alter):
    rows = law_rows()
    alter(rows)
    report = judge.law_candidates(rows)
    assert "## coverage /" not in report
    assert "No evidence-supported law candidates" in report


def test_one_real_job_or_replayed_job_identity_is_not_repeated_evidence():
    assert "No evidence-supported" in judge.law_candidates(law_rows()[:3])
    rows = law_rows()
    for row in rows:
        row["real_job_id"] = "one-original-job"
    assert "No evidence-supported" in judge.law_candidates(rows)


def test_same_fact_ids_with_different_definitions_do_not_establish_recurrence():
    rows = law_rows()
    for row in rows[3:]:
        row["fact_catalog"]["fact-phone-target"]["text"] = "Use a different requirement."
    assert "No evidence-supported" in judge.law_candidates(rows)


def test_fact_ids_can_change_when_the_exact_sourced_fact_is_the_same():
    rows = law_rows()
    for row in rows[3:]:
        row["fact_catalog"]["new-fact-id"] = row["fact_catalog"].pop("fact-phone-target")
        row["judgment"]["dimensions"]["coverage"]["fact_ids"] = ["new-fact-id"]
    assert "## coverage /" in judge.law_candidates(rows)


def test_canonical_policy_compatibility_when_dependency_is_installed(tmp_path):
    canonical = pytest.importorskip("z0int.policy.findings")
    exported = judge.findings([real_row()], scope(tmp_path))
    assert len(exported) == 1
    canonical.validate(exported[0])
    assert exported[0]["id"] == canonical.finding_id(exported[0])
    assert exported[0]["synthetic"] is False
    assert exported[0]["evidence_kind"] == "proxy"
    assert exported[0]["metric"]["promotion_eligible"] is False


def bootstrap_rows():
    rows = calibration_rows()
    return rows[:27] + rows[103:116]


def test_approved_subset40_has_explicit_cohort_and_truthful_interval_widths():
    receipt = judge.calibration_metrics(bootstrap_rows(), expected_positive=27, expected_negative=13)
    assert receipt["cohort"] == "bootstrap_subset40"
    assert receipt["scores_enabled"] and receipt["measurement_complete"]
    assert receipt["n"] == 40
    assert (receipt["failures"], receipt["nonfailures"]) == (27, 13)
    assert receipt["precision_ci95"][0] < receipt["precision"] <= receipt["precision_ci95"][1]
    assert receipt["recall_ci95"][0] < receipt["recall"] <= receipt["recall_ci95"][1]
    assert receipt["precision_ci_width"] > 0
    assert receipt["recall_ci_width"] > 0
    assert receipt["calibration_id"] != judge.calibration_metrics(calibration_rows())["calibration_id"]


def test_subset_does_not_silently_satisfy_default_full153_gate():
    assert not judge.calibration_metrics(bootstrap_rows())["scores_enabled"]


@pytest.mark.parametrize("positive,negative", [(1, 1), (0, 0), (103, 49), (True, 13)])
def test_unapproved_calibration_counts_do_not_weaken_gate(positive, negative):
    with pytest.raises(judge.JudgeError, match="cohort"):
        judge.calibration_metrics([], expected_positive=positive, expected_negative=negative)


def test_findings_accept_only_complete_explicit_subset_receipt(tmp_path, finding_schema):
    spec = scope(tmp_path)
    spec["calibration"] = judge.calibration_metrics(bootstrap_rows(), expected_positive=27, expected_negative=13)
    exported = judge.findings([real_row()], spec)
    assert exported[0]["metric"]["calibration_id"] == spec["calibration"]["calibration_id"]
    spec["calibration"]["cohort"] = "full153"
    with pytest.raises(judge.JudgeError, match="Scores disabled"):
        judge.findings([real_row()], spec)


def test_different_comparison_panels_do_not_establish_all_model_recurrence():
    rows = law_rows()
    rows = [row for row in rows if not (row["job_id"] == "real-job-1" and row["model"] == "c")]
    for row in rows:
        if row["job_id"] == "real-job-1":
            row["compared_models"] = ["subscription:a", "subscription:b"]
    assert "No evidence-supported" in judge.law_candidates(rows)


def test_calibration_context_hash_includes_the_original_negative_ask(audit):
    root, _, _, _ = audit
    negatives = [case for case in judge.calibration_cases(root) if not case["gold_miss"]]
    assert len({case["bundle"]["context_hash"] for case in negatives}) == len(negatives)


@pytest.mark.parametrize("task_category", [None, "coverage", "unbounded-new-class", True])
def test_parser_requires_bounded_work_category_separate_from_miss_dimension(task_category):
    row = judgment(dimension="coverage")
    row["task_category"] = task_category
    assert not judge.parse_judgment(row)["valid"]
    row.pop("task_category")
    assert not judge.parse_judgment(row)["valid"]


def reference_rows():
    rows = []
    for job_id in ("job-1", "job-2"):
        context_hash = hashlib.sha256(job_id.encode()).hexdigest()
        for model in ("sol", "a", "b"):
            row = real_row(job_id, model, miss=model != "sol", context_hash=context_hash)
            row["compared_models"] = ["subscription:sol", "subscription:a", "subscription:b"]
            if model == "sol":
                row["judgment"]["task_category"] = "coding"
                row["category"] = "coding"
            if model == "a":
                row["judgment"]["dimensions"]["coverage"]["score"] = 1
            rows.append(row)
    return rows


def test_reference_comparison_reports_per_task_per_dimension_rubric_deltas_only():
    result = judge.reference_comparison(reference_rows(), "subscription:sol")
    assert len(result["comparisons"]) == 4
    assert result["candidate_execution_success"] is None
    assert result["promotion_eligible"] is False and result["evidence_kind"] == "proxy"
    candidate = next(row for row in result["summary"]
                     if row["candidate_model"] == "subscription:a" and row["dimension"] == "coverage")
    assert candidate["task_class"] == "intent.shadow.coding"
    assert candidate["paired_n"] == 2 and candidate["unknown_n"] == 0
    assert candidate["candidate_mean_score"] == 1
    assert candidate["reference_mean_score"] == 2 and candidate["mean_delta"] == -1
    assert all(row["task_category_agreement"] is False for row in result["comparisons"])


def test_missing_local_completion_is_unknown_not_a_rubric_score():
    rows = [row for row in reference_rows() if row["model"] != "b"]
    result = judge.reference_comparison(rows, "subscription:sol")
    missing = [row for row in result["comparisons"] if row["candidate_model"] == "subscription:b"]
    assert len(missing) == 2
    assert all(row["status"] == "unknown" and not row["dimensions"] for row in missing)
    summary = next(row for row in result["summary"]
                   if row["candidate_model"] == "subscription:b" and row["dimension"] == "coverage")
    assert summary["paired_n"] == 0 and summary["unknown_n"] == 2
    assert summary["mean_delta"] is None
    assert "No evidence-supported" in judge.law_candidates(rows)


def test_missing_or_mismatched_reference_does_not_produce_comparative_scores():
    rows = [row for row in reference_rows() if row["model"] != "sol"]
    result = judge.reference_comparison(rows, "subscription:sol")
    assert result["comparisons"] and all(row["status"] == "unknown" for row in result["comparisons"])
    rows = reference_rows()
    for row in rows:
        if row["model"] == "sol":
            row["context_hash"] = "c" * 64
    result = judge.reference_comparison(rows, "subscription:sol")
    assert all(row["status"] == "unknown" for row in result["comparisons"])


def test_reference_is_not_automatically_correct_or_maximum_scored():
    rows = reference_rows()
    for row in rows:
        if row["model"] == "sol":
            row["judgment"] = judgment(dimension="coverage")
            row["judgment"]["task_category"] = "coding"
    result = judge.reference_comparison(rows, "subscription:sol")
    compared = next(row for row in result["comparisons"] if row["candidate_model"] == "subscription:a")
    assert compared["dimensions"]["coverage"]["reference_score"] == 0
    assert compared["dimensions"]["coverage"]["delta"] == 1
    assert result["candidate_execution_success"] is None
