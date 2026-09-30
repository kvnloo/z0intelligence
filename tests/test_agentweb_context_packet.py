import hashlib
import json

import pytest

from z0int.agentweb_context_packet import (
    compile_agentweb_context_packet,
    validate_agentweb_context_pack,
)


def source(label: str, content: str, need_ids, source_kind="private-knowledge-base"):
    digest = hashlib.sha256(label.encode()).hexdigest()
    version = hashlib.sha256(content.encode()).hexdigest()
    return {
        "source_id": "agentweb-kb:" + digest,
        "source_version": "sha256:" + version,
        "locator": "agentweb-kb://" + digest,
        "content": content,
        "need_ids": list(need_ids),
        "source_kind": source_kind,
    }


def request():
    return {
        "harness": "agentweb",
        "trace_id": "ctx-op-1",
        "parent_agent": "agentweb:" + "a" * 24,
        "task_id": "task-ctx-1",
        "needs": [
            {"id": "brand", "description": "brand positioning", "required": True},
            {"id": "market", "description": "market strategy", "required": True},
            {"id": "optional", "description": "nice to have", "required": False},
        ],
        "evidence": [
            source("brand-a", "Brand: fast, practical, technical. " + "x" * 2500, ["brand"]),
            source("market-a", "Market: AI agents for GTM teams. " + "y" * 2500, ["market"]),
            source("optional-a", "Optional background. " + "z" * 2500, ["optional"]),
        ],
        "scan_incomplete": False,
        "max_packet_bytes": 5000,
    }


def test_packet_is_bounded_and_preserves_required_needs():
    result = compile_agentweb_context_packet(request())
    assert result["ok"] is True
    assert result["mode"] == "shadow"
    assert result["applied"] is False
    packet = result["packet"]
    assert packet["schema"] == "z0int.context_resolve.v1"
    assert packet["measurements"]["packet_bytes"] <= 5000
    actual_bytes = len(json.dumps(
        packet, sort_keys=True, ensure_ascii=False, separators=(",", ":")
    ).encode("utf-8"))
    assert actual_bytes <= 5000
    assert packet["measurements"]["network_model_calls"] == 0
    assert packet["measurements"]["gpu_loaded"] is False
    assert packet["measurements"]["private_text_persisted"] is False
    assert packet["unresolved_gaps"] == []

    evidence_ids = {row["source_id"] for row in packet["evidence"]}
    assert source("brand-a", "x", ["brand"])["source_id"] in evidence_ids
    assert source("market-a", "x", ["market"])["source_id"] in evidence_ids
    assert all(row["trust_class"] == "index_hit" for row in packet["evidence"])


def test_duplicate_source_is_deduplicated_without_losing_required_coverage():
    args = request()
    args["evidence"].append(dict(args["evidence"][0]))
    result = compile_agentweb_context_packet(args)
    packet = result["packet"]
    assert packet["measurements"]["input_evidence_count"] == 4
    assert packet["measurements"]["deduped_evidence_count"] == 3
    assert packet["unresolved_gaps"] == []


def test_missing_required_need_stays_explicit_gap():
    args = request()
    args["evidence"] = [args["evidence"][0]]
    result = compile_agentweb_context_packet(args)
    assert any(gap.startswith("market:") for gap in result["packet"]["unresolved_gaps"])


def test_incomplete_scan_prevents_exhaustive_absence_claim():
    args = request()
    args["scan_incomplete"] = True
    result = compile_agentweb_context_packet(args)
    assert any(
        "source scan incomplete" in gap
        for gap in result["packet"]["unresolved_gaps"]
    )


def test_private_source_names_are_not_accepted_as_identifiers():
    args = request()
    args["evidence"][0]["source_id"] = "private-knowledge-base/Secret Strategy.docx"
    with pytest.raises(ValueError, match="pseudonymous source_id"):
        validate_agentweb_context_pack(args)


def test_raw_session_id_is_rejected():
    args = request()
    args["parent_agent"] = "raw-user-session-123"
    with pytest.raises(ValueError, match="pseudonymous"):
        validate_agentweb_context_pack(args)


def test_caller_cannot_self_assign_trust_or_verification():
    args = request()
    args["evidence"][0]["trust_class"] = "authoritative_task"
    with pytest.raises(ValueError, match="unknown evidence fields"):
        validate_agentweb_context_pack(args)


def test_packet_does_not_touch_z0_home(tmp_path, monkeypatch):
    monkeypatch.setenv("Z0INT_HOME", str(tmp_path))
    result = compile_agentweb_context_packet(request())
    assert result["ok"] is True
    assert not tmp_path.exists() or not any(tmp_path.rglob("*"))


def test_impossibly_small_budget_fails_instead_of_dropping_required_evidence():
    args = request()
    args["max_packet_bytes"] = 4096
    # Inflate need descriptions so fixed metadata + two required sources cannot
    # silently be made to fit by discarding required evidence.
    args["needs"][0]["description"] = "brand " + "a" * 900
    args["needs"][1]["description"] = "market " + "b" * 900
    try:
        result = compile_agentweb_context_packet(args)
    except ValueError as exc:
        assert "too small" in str(exc)
    else:
        packet = result["packet"]
        assert packet["measurements"]["packet_bytes"] <= 4096
        assert packet["unresolved_gaps"] == []


def test_query_focused_excerpt_preserves_relevant_tail_fact():
    args = request()
    args["needs"] = [
        {"id": "q0", "description": "deployment rollback token", "required": True},
    ]
    content = "irrelevant " * 300 + "deployment rollback token = KEEP-ME-42 " + "tail " * 120
    args["evidence"] = [
        source("focused", content, ["q0"]),
    ]
    args["max_packet_bytes"] = 5000
    result = compile_agentweb_context_packet(args)
    excerpts = [row.get("excerpt", "") for row in result["packet"]["evidence"]]
    assert any("KEEP-ME-42" in excerpt for excerpt in excerpts)


def test_packet_preserves_top_three_ranked_sources_when_budgeted():
    args = request()
    args["needs"] = [
        {"id": "q0", "description": "market agent positioning", "required": True},
    ]
    args["evidence"] = [
        source(f"rank-{i}", f"market agent positioning source {i} " + chr(97 + i) * 3000, ["q0"])
        for i in range(6)
    ]
    args["max_packet_bytes"] = 6000
    result = compile_agentweb_context_packet(args)
    packet = result["packet"]
    assert packet["measurements"]["retained_evidence_count"] >= 3
    retained = {row["source_id"] for row in packet["evidence"]}
    for i in range(3):
        assert source(f"rank-{i}", "x", ["q0"])["source_id"] in retained


def test_aodl_projection_does_not_duplicate_private_excerpt():
    args = request()
    args["needs"] = [
        {"id": "q0", "description": "brand positioning", "required": True},
    ]
    secret = "brand positioning PRIVATE-EXCERPT-ONCE " + "x" * 2500
    args["evidence"] = [source("secret", secret, ["q0"])]
    args["max_packet_bytes"] = 5000
    result = compile_agentweb_context_packet(args)
    packet = result["packet"]
    serialized = json.dumps(packet)
    assert "PRIVATE-EXCERPT-ONCE" in serialized
    assert "PRIVATE-EXCERPT-ONCE" not in json.dumps(packet["aodl_projection"])
    assert "evidence" not in packet["aodl_projection"]
    assert packet["aodl_projection"]["contextEvidence"][0]["source_id"].startswith("agentweb-kb:")
