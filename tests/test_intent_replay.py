"""Private replay scoring fixtures. These tests never call a live model or service."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from urllib.error import HTTPError
import io

import pytest

_SPEC = importlib.util.spec_from_file_location(
    "intent_replay", Path(__file__).parents[1] / "scripts" / "replay-intent-service.py"
)
assert _SPEC and _SPEC.loader
replay = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(replay)


def case(text="Read this file. Why?", classes=("add", "not-an-ask"), **extra):
    sentences = replay.source_sentences(text)
    assert len(sentences) == len(classes)
    return {"case_id": "fixture", "cohort": "confirmed_failure", "text": text,
            "source": {"entry_id": "original"}, "expected_classification": "new_ask",
            "sentences": [{**s, "classification": c} for s, c in zip(sentences, classes)], **extra}


def response(row, entry="entry"):
    bindings = {"sources": {entry: {"texts": [row["text"]], "sentences": row["sentences"]}},
                "compiler": {"memory": [], "assumptions": [], "conflicts": []},
                "approval": {"status": "pending"}}
    for index, sentence in enumerate(row["sentences"]):
        if sentence["classification"] == "add":
            bindings[f"task.{index}"] = {"entryId": entry, "sentenceId": sentence["id"],
                                         "text": sentence["text"], "assumptions": []}
    return {"classification": row["expected_classification"],
            "contract": {"plan": {"bindings": bindings}}, "contract_id": "pending"}


def expected():
    return {"case_id": "fixture", "facts": [{"fact_id": "worker-mode",
            "citation_aliases": ["decision-17"],
            "text_alias_groups": [["s22"], ["worker mode"], ["old", "obsolete"]]}]}


def add_memory(reply, *, cite=True, claim="S22 worker mode replaces the old cap."):
    compiler = reply["contract"]["plan"]["bindings"]["compiler"]
    compiler["memory"] = [{"source": "history", "id": "decision-17", "evidence_kind": "claimed",
                           "text": "S22 worker mode replaces the old cap."}]
    if cite:
        compiler["assumptions"] = [{"claim": claim,
                                    "citations": [{"source": "history", "id": "decision-17", "evidence_kind": "claimed"}]}]
        bindings = reply["contract"]["plan"]["bindings"]
        bindings["precondition.1"] = {"type": "human_review", "target": "current S22 worker mode", "claim": claim}
        for task in bindings.values():
            if isinstance(task, dict) and "entryId" in task:
                task["preconditions"] = ["precondition.1"]


def test_splitter_preserves_edge_whitespace_and_punctuation():
    result = replay.source_sentences("  One!?  Two.\n\n last  ")
    assert [s["text"] for s in result] == ["  One!?", "  Two.", " last  "]
    assert [s["id"] for s in result] == ["s0.0.7", "s0.7.13", "s0.15.22"]


def test_splitter_uses_javascript_utf16_offsets():
    result = replay.source_sentences("😀 Go. Next")
    assert result[0] == {"id": "s0.0.6", "text": "😀 Go.", "block": 0, "start": 0, "end": 6}
    assert result[1]["id"] == "s0.6.11"


def test_empty_whitespace_has_no_canonical_sentences():
    assert replay.source_sentences("\n \t\n") == []


def test_input_rejects_changed_gold_text_and_duplicate_cases():
    row = case()
    row["sentences"][0]["text"] = "Read file."
    with pytest.raises(ValueError, match="Gold source spans"):
        replay.check_inputs([row], [{"case_id": "fixture"}])
    row = case()
    with pytest.raises(ValueError, match="Duplicate or unjudged"):
        replay.check_inputs([row, row], [{"case_id": "fixture"}])


def test_every_exact_sentence_has_the_expected_task_binding():
    row = case()
    result = replay.judge_response(row, {"facts": []}, response(row), "entry")
    assert result["pass"]
    assert all(s["pass"] for s in result["sentences"])


@pytest.mark.parametrize("mutation", ["missing", "duplicate", "paraphrase", "classification", "wrong_entry", "extra_task"])
def test_sentence_judge_rejects_coverage_gaps(mutation):
    row = case()
    reply = response(row)
    bindings = reply["contract"]["plan"]["bindings"]
    source = bindings["sources"]["entry"]
    # Do not mutate the shared gold list from the fixture.
    source["sentences"] = [dict(s) for s in source["sentences"]]
    if mutation == "missing":
        source["sentences"].pop()
    elif mutation == "duplicate":
        source["sentences"].append(dict(source["sentences"][0]))
    elif mutation == "paraphrase":
        source["sentences"][0]["text"] = "Read file."
    elif mutation == "classification":
        source["sentences"][1]["classification"] = "add"
    elif mutation == "wrong_entry":
        bindings["task.0"]["entryId"] = "another-entry"
    else:
        bindings["task.extra"] = {"entryId": "entry", "sentenceId": "invented", "text": "invented"}
    assert not replay.judge_response(row, {"facts": []}, reply, "entry")["sentence_coverage_ok"]


def test_retrieved_but_uncited_memory_is_not_recall():
    row = case()
    reply = response(row)
    add_memory(reply, cite=False)
    result = replay.judge_response(row, expected(), reply, "entry")
    assert result["facts_recalled"] == 0
    assert not result["facts"][0]["cited"]


def test_citation_and_supported_claim_count_as_lexical_recall():
    row = case()
    reply = response(row)
    add_memory(reply)
    result = replay.judge_response(row, expected(), reply, "entry")
    assert result["facts_recalled"] == 1
    assert result["cited_memory_ids"] == ["decision-17"]


def test_citation_without_fact_claim_does_not_count_as_recall():
    row = case()
    reply = response(row)
    add_memory(reply, claim="See the cited historical record.")
    assert replay.judge_response(row, expected(), reply, "entry")["facts_recalled"] == 0


def test_citation_must_match_source_as_well_as_id():
    row = case()
    reply = response(row)
    add_memory(reply)
    reply["contract"]["plan"]["bindings"]["compiler"]["assumptions"][0]["citations"][0]["source"] = "invented"
    result = replay.judge_response(row, expected(), reply, "entry")
    assert result["invalid_citations"]
    assert result["facts_recalled"] == 0


def test_fact_cannot_borrow_another_assumptions_citation():
    row = case()
    reply = response(row)
    add_memory(reply, claim="This only cites an unrelated detail.")
    compiler = reply["contract"]["plan"]["bindings"]["compiler"]
    compiler["memory"].append({"source": "history", "id": "different", "evidence_kind": "claimed", "text": "Other detail"})
    compiler["assumptions"].append({"claim": "S22 worker mode replaces the old cap.",
                                    "citations": [{"source": "history", "id": "different", "evidence_kind": "claimed"}]})
    assert replay.judge_response(row, expected(), reply, "entry")["facts_recalled"] == 0


def test_alias_matching_respects_token_boundaries():
    assert not replay.contains_alias("decision-170", ["decision-17"])
    assert replay.contains_alias("[decision-17]", ["decision-17"])


def test_question_ledger_is_scored_without_creating_contract():
    row = case("Why?", ("not-an-ask",), expected_classification="question", question_only=True)
    reply = {"classification": "question", "sources": {"entry": {"texts": [row["text"]], "sentences": row["sentences"]}}}
    assert replay.judge_response(row, {"facts": []}, reply, "entry")["pass"]
    reply["contract"] = {"plan": {"bindings": {"sources": reply["sources"]}}}
    assert replay.judge_response(row, {"facts": []}, reply, "entry")["question_false_contract"]


def test_destructive_contract_requires_conflict_and_stays_unapproved():
    row = case(requires_conflict=True)
    reply = response(row)
    assert replay.judge_response(row, {"facts": []}, reply, "entry")["safety_errors"]
    bindings = reply["contract"]["plan"]["bindings"]
    bindings["compiler"]["conflicts"] = ["Destruction conflicts with ongoing work. Human review required."]
    assert replay.judge_response(row, {"facts": []}, reply, "entry")["pass"]
    bindings["approval"]["status"] = "approved"
    assert not replay.judge_response(row, {"facts": []}, reply, "entry")["pass"]


def test_amendment_requires_dropped_prior_sentence():
    row = case(requires_drop=True)
    reply = response(row)
    assert replay.judge_response(row, {"facts": []}, reply, "entry")["safety_errors"]
    reply["contract"]["plan"]["bindings"]["sources"]["prior"] = {"sentences": [{"classification": "drop"}]}
    assert not replay.judge_response(row, {"facts": []}, reply, "entry")["safety_errors"]


def test_driver_preserves_errors_unique_requests_and_unapproved_flow(tmp_path, monkeypatch, capsys):
    first = case(flow_id="flow")
    second = case(flow_id="flow", follows_case="fixture")
    second["case_id"] = "amend"
    third = case()
    third["case_id"] = "error"
    requests = []

    def fake_post(url, payload, timeout):
        requests.append(dict(payload))
        if len(requests) == 3:
            raise HTTPError(url, 503, "unavailable", {}, io.BytesIO(b'{"error":"offline"}'))
        return 200, response(first, payload["entry_id"])

    monkeypatch.setattr(replay, "post_reconcile", fake_post)
    gold = {key: {"facts": []} for key in ("fixture", "amend", "error")}
    summary = replay.run([first, second, third], gold, "http://example.invalid/reconcile", tmp_path, 1)
    rows = replay.load_jsonl(tmp_path / "results.jsonl")
    assert summary["attempted"] == 3 and summary["errors"] == 1
    assert len({p["entry_id"] for p in requests}) == 3
    assert requests[0]["session_id"] == requests[1]["session_id"]
    assert requests[2]["session_id"] != requests[0]["session_id"]
    assert requests[1]["open_contract"] == "pending"
    assert all(p["text"] == first["text"] and p["recent_user_msgs"] == [] for p in requests)
    assert all(set(p) <= {"session_id", "entry_id", "text", "recent_user_msgs", "mode", "open_contract"} for p in requests)
    assert rows[2]["http_status"] == 503 and rows[2]["error_response"] == '{"error":"offline"}'
    assert all(r["latency_ms"] >= 0 for r in rows)
    assert (tmp_path / "results.jsonl").stat().st_mode & 0o777 == 0o600
    assert (tmp_path / "summary.json").stat().st_mode & 0o777 == 0o600
    assert "Lexical matches do not prove" in (tmp_path / "report.md").read_text()
    assert "Private intent replay" in capsys.readouterr().out
    assert json.loads((tmp_path / "summary.json").read_text())["errors"] == 1


def test_driver_does_not_overwrite_existing_receipts(tmp_path, monkeypatch):
    (tmp_path / "summary.json").write_text("existing")
    monkeypatch.setattr(replay, "post_reconcile", lambda *_: pytest.fail("Unexpected network request"))
    with pytest.raises(ValueError, match="Output already exists"):
        replay.run([], {}, "http://example.invalid/reconcile", tmp_path, 1)


def test_claimed_citations_are_counted_and_need_prerequisites():
    row = case()
    reply = response(row)
    add_memory(reply)
    result = replay.judge_response(row, expected(), reply, "entry")
    assert result["citation_evidence_kinds"] == {"observed": 0, "claimed": 1, "unknown": 0}
    assert result["assumption_prerequisites"][0]["has_prerequisite_verifier"]
    reply["contract"]["plan"]["bindings"]["task.0"]["preconditions"] = []
    result = replay.judge_response(row, expected(), reply, "entry")
    assert "nonobserved assumption lacks a prerequisite verifier" in result["safety_errors"]


def test_citation_cannot_upgrade_claimed_memory_to_observed():
    row = case()
    reply = response(row)
    add_memory(reply)
    reply["contract"]["plan"]["bindings"]["compiler"]["assumptions"][0]["citations"][0]["evidence_kind"] = "observed"
    result = replay.judge_response(row, expected(), reply, "entry")
    assert result["invalid_citations"]
    assert result["facts_recalled"] == 0
