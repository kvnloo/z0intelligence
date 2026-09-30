"""z0int#53 acceptance criteria for DecisionOpportunity v0."""
import copy
import subprocess

import pytest

from z0int import decision_opportunity as do

PACKET = {
    "schema": "z0int.state_packet.v0", "packet_id": "p1", "built_at": "2026-09-30T00:00:00Z",
    "current_claims": [
        {"key": "git.branch", "value": "main", "status": "observed", "evidence": ["e:1"], "observed_at": "t0"},
        {"key": "docs.priority", "value": "State Packet", "status": "observed", "evidence": ["e:2"], "observed_at": "t0"},
    ],
    "superseded_claims": [{"key": "git.branch", "old_value": "dev", "current_value": "main"}],
    "contradictions": [],
    "blocking_unknowns": [],
    "unknowns": [],
    "allowed_transitions": [{"kind": "OBSERVE", "action": "refresh_packet", "why": "always legal"}],
    "source_revisions": {"git": {"head": "abc"}},
    "evidence": [{"id": "e:1"}, {"id": "e:2"}],
}


def packet(**changes):
    p = copy.deepcopy(PACKET)
    p.update(changes)
    return p


def build(p=None, **kw):
    return do.build_decision_opportunity("/nonexistent", kw.pop("request", "what branch am I on?"), packet=p or packet(), **kw)


def kinds(opp, legal=True):
    return {a["kind"] for a in opp["action_space"] if a["legal"] is legal}


def test_represents_current_superseded_and_next_legal_action():
    opp = build()
    assert {c["key"] for c in opp["state"]["claims"]} == {"git.branch", "docs.priority"}
    assert opp["state"]["superseded"][0]["old_value"] == "dev"
    assert do.deterministic_gate(opp) == "ACT" and "ACT" in kinds(opp)


def test_contradiction_blocks_act_and_makes_escalate_legal():
    opp = build(packet(contradictions=[{"key": "priority", "kind": "priority_conflict", "contests": "docs.priority",
                                        "claims": [{"value": "A"}, {"value": "B"}]}]), request="what is the current priority?")
    assert "ACT" in kinds(opp, legal=False) and "ESCALATE" in kinds(opp)
    assert do.deterministic_gate(opp) == "ESCALATE"


def test_unknown_is_distinct_from_false_or_absent():
    opp = build(packet(blocking_unknowns=[
        {"key": "conv.latest_session", "reason": "history unavailable", "source_status": "source_unavailable"},
        {"key": "conv.worktree_session", "reason": "none recorded", "source_status": "no_match"}]),
        request="which Claude Code session last worked here?")
    statuses = {u["key"]: u["status"] for u in opp["state"]["unknowns"]}
    assert statuses == {"conv.latest_session": "source_unavailable", "conv.worktree_session": "no_match"}
    ask = next(a for a in opp["action_space"] if a["kind"] == "ASK")
    # a complete index that recorded nothing is an answer, not a question for the user
    assert ask["about"] == ["unknown:conv.latest_session"]
    assert do.deterministic_gate(opp) == "ASK"


def test_deterministic_reconstruction_and_harness_independence():
    a = build(harness="claude-code", trace_id="t1")
    b = build(harness="hermes", trace_id="t2", attempt=3)
    assert a["semantic_id"] == b["semantic_id"]
    assert a["trace"]["opportunity_id"] != b["trace"]["opportunity_id"]
    assert build()["semantic_id"] == build()["semantic_id"]


def test_provenance_and_invalidation():
    base = build()
    moved = build(packet(source_revisions={"git": {"head": "def"}}))
    assert base["semantic_id"] != moved["semantic_id"]
    assert all(c["evidence"] for c in base["state"]["claims"])
    assert base["provenance"]["packet_id"] == "p1"


def test_authority_cannot_be_minted_from_confidence():
    opp = build(effects=["write"])
    assert opp["authority"]["grants"] == ["read"]
    act = next(a for a in opp["action_space"] if a["kind"] == "ACT")
    assert not act["legal"] and "authority:write" in act["blocked_by"]
    ask = next(a for a in opp["action_space"] if a["kind"] == "ASK")
    assert ask["legal"] and "authority:write" in ask["about"]
    # no parameter accepts model confidence; the builder rejects stray keywords
    with pytest.raises(TypeError):
        do.build_decision_opportunity("/x", "q", packet=packet(), confidence=0.99)
    with pytest.raises(ValueError):
        do.with_authority_grant(opp, granted_by_user="", effect="write")
    granted = do.with_authority_grant(opp, granted_by_user="user:kvn", effect="write")
    assert "ACT" in kinds(granted) and granted["semantic_id"] != opp["semantic_id"]
    assert granted["authority"]["granted"] == [{"effect": "write", "by": "user:kvn"}]


def test_invalid_aodl_contract_fails_closed():
    opp = build(effects=["privileged"], aodl_doc={"specVersion": "0.2", "intentGraph": {"nodes": [
        {"id": "n", "authorityCeiling": ["privileged"]}]}})
    assert opp["authority"]["grants"] == ["read"] and opp["authority"]["source"] == "harness-default"
    assert "ACT" in kinds(opp, legal=False)


def test_unknown_effect_class_is_rejected():
    with pytest.raises(ValueError):
        build(effects=["delete_everything"])


def test_real_packet_builder_end_to_end(tmp_path):
    repo = tmp_path / "r"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main", str(repo)], check=True)
    (repo / "README.md").write_text("# r\n")
    subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "i"], check=True)
    empty = tmp_path / "projects"
    empty.mkdir()
    a = do.build_decision_opportunity(repo, "what branch?", projects_root=empty)
    b = do.build_decision_opportunity(repo, "what branch?", projects_root=empty)
    assert a["schema"] == do.SCHEMA and a["semantic_id"] == b["semantic_id"]
    assert any(c["key"] == "git.branch" and c["value"] == "main" for c in a["state"]["claims"])


CONFLICT = [{"key": "priority", "kind": "priority_conflict", "contests": "docs.priority", "claims": [{"value": "A"}, {"value": "B"}]}]


def test_question_scoping_ignores_unrelated_blockers():
    p = packet(contradictions=CONFLICT, blocking_unknowns=[{"key": "conv.latest_session", "reason": "x"}])
    scoped = build(p, request="what branch am I on?")
    repo = build(p, request="what branch am I on?", scoped=False)
    assert scoped["scope"]["families"] == ["git.branch"] and do.deterministic_gate(scoped) == "ACT"
    assert repo["scope"]["mode"] == "repo" and do.deterministic_gate(repo) == "ESCALATE"


def test_required_family_without_claims_becomes_explicit_unknown():
    p = packet(coverage={"git": "full", "docs": "full", "claude_code": "full"})
    opp = build(p, request="what is the CI status of the open PR #12?")
    missing = [u for u in opp["state"]["unknowns"] if u["key"] == "gh"]
    assert missing and missing[0]["status"] == "source_unavailable" and missing[0]["blocking"]
    assert do.deterministic_gate(opp) == "ASK"


def test_unmatched_request_falls_back_to_repo_scope():
    assert build(request="hello there")["scope"]["mode"] == "repo"
