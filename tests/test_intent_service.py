import asyncio
import copy
import json
from pathlib import Path

import pytest

from z0int.intent_service import IntentService, identity, validate_draft


class Memory:
    async def retrieve(self, *args):
        return {"records": [], "sources": [{"source": "test", "status": "answered"}], "latency_ms": 1}


def proposal(classification="new_ask"):
    return {"classification": classification, "sentences": [{"entryId": "e1", "sentenceId": "s0.0.10", "classification": "add", "reason": "ask"}],
        "tasks": [{"entryId": "e1", "sentenceId": "s0.0.10", "target": "requested artifact", "interpretation": "Create it", "executor": "self", "assumptions": [], "conflicts": [], "verifier": {"type": "human_review", "target": "requested artifact"}}],
        "amendments": [], "assumptions": [], "conflicts": [], "delegation_plan": []}


async def bridge(request):
    if request["op"] == "hash":
        return "contract-hash"
    if request["op"] == "prepare":
        return {"revision": 1, "provenance": {"sourceHash": "source-hash"}, "intentGraph": {"nodes": []},
            "plan": {"bindings": {"sessionId": request["session_id"], "sources": {request["entry_id"]: {"texts": [request["text"]], "sentences": []}}}}}
    doc = copy.deepcopy(request["doc"])
    b = doc["plan"]["bindings"]
    b["compiler"] = {"model_id": request["model_id"], "assumptions": [], "conflicts": [], "delegation_plan": []}
    b["approval"] = {"status": "pending", "session_id": b["sessionId"], "contract_id": request["contract_id"], "sourceHash": "source-hash", "contractHash": "contract-hash"}
    return doc


def service(tmp_path, draft=None):
    async def model(request):
        return {"draft": draft or proposal(), "model_id": "openai-codex/test"}
    return IntentService(tmp_path, Memory(), model, bridge, lambda doc: None)


def request():
    return {"session_id": "test", "entry_id": "e1", "text": "Create it.", "recent_user_msgs": []}


def test_pending_not_binding_and_explicit_approval(tmp_path):
    srv = service(tmp_path)
    result = asyncio.run(srv.reconcile(request()))
    contract_id = result["contract_id"]
    assert not (tmp_path / "intent" / "test" / f"{contract_id}.json").exists()
    decision = {"session_id": "test", **{k: result[k] for k in ("contract_id", "sourceHash", "contractHash", "revision")}}
    approved = asyncio.run(srv.decide(decision, True))
    assert Path(approved["path"]).is_file()
    assert approved["contract"]["plan"]["bindings"]["approval"]["status"] == "approved"
    assert not (Path(approved["path"]).stat().st_mode & 0o077)
    with pytest.raises(ValueError, match="Only pending"):
        asyncio.run(srv.decide(decision, True))


def test_stale_pending_and_hash_change_rejected(tmp_path):
    srv = service(tmp_path)
    first = asyncio.run(srv.reconcile(request()))
    second = asyncio.run(srv.reconcile({**request(), "entry_id": "e2"}))
    decision = {"session_id": "test", **{k: first[k] for k in ("contract_id", "sourceHash", "contractHash", "revision")}}
    with pytest.raises(ValueError, match="Superseded"):
        asyncio.run(srv.decide(decision, True))
    decision.update({k: second[k] for k in ("contract_id", "sourceHash", "revision")}, contractHash="tampered")
    with pytest.raises(ValueError, match="mismatch"):
        asyncio.run(srv.decide(decision, True))


def test_question_has_ledger_without_binding(tmp_path):
    draft = proposal("question")
    draft["tasks"] = []
    draft["sentences"][0].update(classification="not-an-ask", reason="question")
    srv = service(tmp_path, draft)
    result = asyncio.run(srv.reconcile(request()))
    assert result["answerable"] is True
    assert "contract" not in result
    assert result["sources"]["e1"]["texts"] == ["Create it."]
    assert not (tmp_path / "intent").exists()


def test_reject_never_writes_binding(tmp_path):
    srv = service(tmp_path)
    result = asyncio.run(srv.reconcile(request()))
    decision = {"session_id": "test", **{k: result[k] for k in ("contract_id", "sourceHash", "contractHash", "revision")}}
    rejected = asyncio.run(srv.decide(decision, False))
    assert ".pending" in rejected["path"]
    assert asyncio.run(srv.current({"session_id": "test"})) == {"contract": None}


@pytest.mark.parametrize("value", ["../escape", "/tmp", "..", "", None])
def test_path_traversal_rejected(value):
    with pytest.raises(ValueError):
        identity(value, "session_id")


def test_missing_memory_id_rejected():
    draft = proposal()
    draft["assumptions"] = [{"claim": "A remembered decision", "citations": [{"source": "hermes", "id": "invented", "evidence_kind": "claimed"}]}]
    with pytest.raises(ValueError, match="memory"):
        validate_draft(draft, [])


def test_no_client_graph_accepted(tmp_path):
    with pytest.raises(ValueError, match="stored contract id"):
        asyncio.run(service(tmp_path).reconcile({**request(), "open_contract": {"graph": "untrusted"}}))


def test_claimed_memory_requires_precondition_and_cannot_upgrade_to_observed():
    record = {"source": "hermes", "id": "fact", "text": "Worker is active", "evidence_kind": "claimed"}
    citation = {"source": "hermes", "id": "fact", "evidence_kind": "claimed"}
    draft = proposal()
    draft["tasks"][0]["assumptions"] = [{"claim": "Worker is active", "citations": [citation]}]
    with pytest.raises(ValueError, match="prior observation"):
        validate_draft(draft, [record])
    draft["tasks"][0]["preconditions"] = [{"claim": "Worker is active", "verifier": {"type": "service_active", "target": "worker.service", "unit": "worker.service"}}]
    validate_draft(draft, [record])
    citation["evidence_kind"] = "observed"
    with pytest.raises(ValueError, match="memory"):
        validate_draft(draft, [record])


@pytest.mark.parametrize("damage", [None, "approval", "hash", "json"])
def test_runtime_clone_preserves_receipts_and_fails_closed(tmp_path, damage):
    srv = service(tmp_path)
    result = asyncio.run(srv.reconcile(request()))
    decision = {"session_id": "test", **{k: result[k] for k in ("contract_id", "sourceHash", "contractHash", "revision")}}
    approved = asyncio.run(srv.decide(decision, True))
    runtime = copy.deepcopy(approved["contract"])
    runtime["events"] = [{"type": "observed", "receipt": "kept"}]
    if damage == "approval":
        runtime["plan"]["bindings"]["approval"]["approved_by"] = "intruder"
    if damage == "hash":
        async def bad_hash(_):
            return "different-hash"
        srv.bridge = bad_hash
    path = Path(approved["path"]).parent / "runtime" / Path(approved["path"]).name
    path.parent.mkdir()
    path.write_text("{" if damage == "json" else json.dumps(runtime))
    if damage:
        with pytest.raises((ValueError, json.JSONDecodeError)):
            asyncio.run(srv._load("test", result["contract_id"]))
    else:
        assert asyncio.run(srv._load("test", result["contract_id"]))["events"] == runtime["events"]


def test_cancellation_without_proposal_is_durable_and_never_calls_model(tmp_path):
    srv = service(tmp_path)
    async def forbidden(_):
        pytest.fail("Cancellation must not call the model or compiler")
    srv.model = srv.bridge = forbidden
    raw = "dont actually delete anything btw"
    result = asyncio.run(srv.reconcile({**request(), "text": raw}))
    assert result["classification"] == "cancellation"
    assert "contract" not in result
    receipt = result["cancellation_receipt"]
    assert receipt["text"] == raw and receipt["status"] == "cancelled_no_open_proposal"
    path = tmp_path / "intent/test/cancellations/e1.json"
    assert json.loads(path.read_text()) == receipt
    assert not path.stat().st_mode & 0o077
    assert not (tmp_path / "intent/test/.pending").exists()


def test_destructive_question_requires_contract_not_question_bypass(tmp_path):
    draft = proposal("question")
    draft["tasks"] = []
    srv = service(tmp_path, draft)
    with pytest.raises(ValueError, match="Destructive scope requires"):
        asyncio.run(srv.reconcile({**request(), "text": "Should I wipe all repos?"}))
