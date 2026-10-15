from __future__ import annotations

import json
from pathlib import Path

import pytest

from z0int import governed_worker as g
from z0int.receipt import append_receipt


ROOT = Path(__file__).resolve().parents[1]
CONTRACT = ROOT / "contracts" / "aodl" / "governed-worker-v1.json"


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("Z0INT_HOME", str(tmp_path / "z0"))
    monkeypatch.setenv("Z0INT_AODL_CONTRACT_PATH", str(CONTRACT))
    monkeypatch.setenv("Z0INT_GOVERNED_REMOTE", "1")
    monkeypatch.delenv("Z0INT_GOVERNED_PROVIDER", raising=False)
    monkeypatch.delenv("Z0INT_AODL_PARENT_DEPTH", raising=False)


def request(**kw):
    base = {
        "harness": "omp",
        "trace_id": "governed-trace",
        "parent_agent": "omp-session",
        "task": "Rewrite this public sentence more clearly.",
        "context": "Public text only.",
        "max_tokens": 256,
        "allow_remote": True,
    }
    base.update(kw)
    return base


def test_checked_in_contract_is_valid_and_builds_protocol_v3_request():
    remote = g.build_remote_request(request())
    assert remote["harness"] == "omp"
    assert remote["trace_id"] == "governed-trace"
    assert remote["function"] == "cheap_bounded_worker"
    assert remote["provider"] == "openrouter"
    assert remote["model"] == "nvidia/nemotron-3-super-120b-a12b:free"
    assert remote["free_only"] is True
    assert remote["aodl"]["document"]["graphId"] == "z0int-governed-worker-canary"
    spawn = remote["aodl"]["spawn"]
    assert spawn["request_revision"] == 1
    assert spawn["parent_node_id"] == "parent"
    assert spawn["live_children"] == 0
    assert spawn["parent_depth"] == 0
    assert spawn["observed"] == {"tokens": 0}
    assert spawn["requested"] == ["execute"]
    assert 256 < spawn["proposed"]["tokens"] < 4096


def test_host_rejects_missing_remote_consent_and_wrong_harness():
    with pytest.raises(ValueError, match="allow_remote"):
        g.build_remote_request({k:v for k,v in request().items() if k!="allow_remote"})
    with pytest.raises(ValueError, match="not enabled for this harness"):
        g.build_remote_request(request(harness="dsh"))


def test_harness_cannot_supply_provider_model_or_authority():
    for field, value in (
        ("provider", "deepseek"),
        ("model", "paid-model"),
        ("aodl", {"fake": True}),
        ("observed", {"tokens": 0}),
    ):
        with pytest.raises(ValueError, match="unknown governed worker fields"):
            g.build_remote_request(request(**{field: value}))


def test_explicit_governed_provider_must_have_zero_cost_evidence(monkeypatch):
    monkeypatch.setenv("Z0INT_GOVERNED_PROVIDER", "deepseek")
    with pytest.raises(ValueError, match="zero-cost"):
        g.build_remote_request(request())


def test_observed_state_counts_live_children_and_terminal_tokens():
    doc = json.loads(CONTRACT.read_text())
    source_hash = doc["provenance"]["sourceHash"]

    admission_a = "aodl-admission-a"
    admission_b = "aodl-admission-b"
    append_receipt({
        "trace_id": admission_a,
        "capability_id": "aodl.structural_admission",
        "extra": {"aodl_admission": {
            "allowed": True,
            "aodl_intent_source_hash": source_hash,
            "parent_node_id": "parent",
        }},
    })
    append_receipt({
        "trace_id": admission_b,
        "capability_id": "aodl.structural_admission",
        "extra": {"aodl_admission": {
            "allowed": True,
            "aodl_intent_source_hash": source_hash,
            "parent_node_id": "parent",
        }},
    })

    append_receipt({
        "trace_id": "dispatch-a",
        "capability_id": "intelligence.dispatch",
        "extra": {"status": "started", "aodl_admission_receipt_id": admission_a},
    })
    append_receipt({
        "trace_id": "dispatch-a",
        "capability_id": "intelligence.dispatch",
        "extra": {"status": "completed", "aodl_admission_receipt_id": admission_a, "result": {"ok": True}},
    })
    append_receipt({
        "trace_id": "physical-a",
        "provider": "openrouter",
        "model": "free",
        "input_tokens": 100,
        "output_tokens": 20,
        "extra": {"status": "completed", "authority_dispatch_id": "dispatch-a"},
    })

    append_receipt({
        "trace_id": "dispatch-b",
        "capability_id": "intelligence.dispatch",
        "extra": {"status": "started", "aodl_admission_receipt_id": admission_b},
    })

    state = g.observed_spawn_state(doc, "parent")
    assert state["live_children"] == 1
    assert state["observed"] == {"tokens": 120}
    assert state["parent_depth"] == 0


def test_unknown_attempted_token_usage_blocks_next_spawn():
    doc = json.loads(CONTRACT.read_text())
    source_hash = doc["provenance"]["sourceHash"]
    append_receipt({
        "trace_id": "aodl-admission-unknown",
        "capability_id": "aodl.structural_admission",
        "extra": {"aodl_admission": {
            "allowed": True,
            "aodl_intent_source_hash": source_hash,
            "parent_node_id": "parent",
        }},
    })
    append_receipt({
        "trace_id": "dispatch-unknown",
        "capability_id": "intelligence.dispatch",
        "extra": {"status": "completed", "aodl_admission_receipt_id": "aodl-admission-unknown"},
    })
    append_receipt({
        "trace_id": "physical-unknown",
        "provider": "openrouter",
        "model": "free",
        "extra": {
            "status": "completed_unmetered_or_unidentified",
            "authority_dispatch_id": "dispatch-unknown",
            "physical_call_attempted": True,
        },
    })
    state = g.observed_spawn_state(doc, "parent")
    assert state["unknown_token_usage_attempts"] == 1
    with pytest.raises(ValueError, match="token spend is unknown"):
        g.build_remote_request(request(trace_id="next"))


def test_unattempted_unknown_usage_does_not_invent_spend():
    doc = json.loads(CONTRACT.read_text())
    source_hash = doc["provenance"]["sourceHash"]
    append_receipt({
        "trace_id": "aodl-admission-noattempt",
        "capability_id": "aodl.structural_admission",
        "extra": {"aodl_admission": {
            "allowed": True,
            "aodl_intent_source_hash": source_hash,
            "parent_node_id": "parent",
        }},
    })
    append_receipt({
        "trace_id": "dispatch-noattempt",
        "capability_id": "intelligence.dispatch",
        "extra": {"status": "completed", "aodl_admission_receipt_id": "aodl-admission-noattempt"},
    })
    append_receipt({
        "trace_id": "physical-noattempt",
        "extra": {
            "status": "failed",
            "authority_dispatch_id": "dispatch-noattempt",
            "physical_call_attempted": False,
        },
    })
    state = g.observed_spawn_state(doc, "parent")
    assert state["unknown_token_usage_attempts"] == 0
    assert state["observed"]["tokens"] == 0


def test_other_contract_and_other_parent_do_not_contaminate_state():
    doc = json.loads(CONTRACT.read_text())
    append_receipt({
        "trace_id": "aodl-admission-other",
        "capability_id": "aodl.structural_admission",
        "extra": {"aodl_admission": {
            "allowed": True,
            "aodl_intent_source_hash": "f" * 64,
            "parent_node_id": "parent",
        }},
    })
    append_receipt({
        "trace_id": "dispatch-other",
        "capability_id": "intelligence.dispatch",
        "extra": {"status": "started", "aodl_admission_receipt_id": "aodl-admission-other"},
    })
    assert g.observed_spawn_state(doc, "parent")["live_children"] == 0


def test_prepare_freezes_governance_but_not_task_context():
    first = g.prepare_remote_request(request())
    rows = [json.loads(line) for line in g.receipts_path().read_text().splitlines()]
    prep = next(row for row in rows if row.get("capability_id") == g.PREPARE_CAPABILITY)
    blob = json.dumps(prep)
    assert "Rewrite this public sentence more clearly." not in blob
    assert "Public text only." not in blob
    frozen = prep["extra"]["frozen_governance"]
    assert frozen["provider"] == "openrouter"
    assert frozen["aodl"]["spawn"]["observed"] == {"tokens": 0}

    # Controller state changes after preparation, but replay of the same trace
    # must reuse the frozen authority envelope.
    append_receipt({
        "trace_id": "aodl-admission-live",
        "capability_id": "aodl.structural_admission",
        "extra": {"aodl_admission": {
            "allowed": True,
            "aodl_intent_source_hash": first["aodl"]["document"]["provenance"]["sourceHash"],
            "parent_node_id": "parent",
        }},
    })
    append_receipt({
        "trace_id": "dispatch-live",
        "capability_id": "intelligence.dispatch",
        "extra": {"status": "started", "aodl_admission_receipt_id": "aodl-admission-live"},
    })
    second = g.prepare_remote_request(request())
    assert second["aodl"] == first["aodl"]
    assert second["provider"] == first["provider"]
    assert second["model"] == first["model"]


def test_prepare_same_trace_different_caller_request_conflicts():
    g.prepare_remote_request(request())
    with pytest.raises(ValueError, match="trace_id reused"):
        g.prepare_remote_request(request(task="different task"))


def test_execute_is_kill_switched_before_network(monkeypatch):
    monkeypatch.setenv("Z0INT_GOVERNED_REMOTE", "0")
    monkeypatch.setattr(g.urllib.request, "urlopen", lambda *a, **k: pytest.fail("network reached"))
    with pytest.raises(ValueError, match="disabled"):
        g.execute(request())


def test_execute_forwards_same_trace_and_host_built_envelope(monkeypatch):
    captured = {}

    class Response:
        def __enter__(self):
            return self
        def __exit__(self, *args):
            return None
        def read(self):
            return b'{"ok":true,"output":"done"}'

    def fake_urlopen(req, timeout):
        captured["url"] = req.full_url
        captured["body"] = json.loads(req.data)
        captured["timeout"] = timeout
        return Response()

    monkeypatch.setenv("Z0INT_REMOTE_EXECUTOR_URL", "http://executor.test:11503")
    monkeypatch.setattr(g.urllib.request, "urlopen", fake_urlopen)
    result = g.execute(request())
    assert result["ok"] is True
    assert result["governed_remote"] is True
    assert result["trace_id"] == "governed-trace"
    assert captured["url"] == "http://executor.test:11503/v1/execute"
    assert captured["body"]["trace_id"] == "governed-trace"
    assert captured["body"]["aodl"]["spawn"]["parent_node_id"] == "parent"
    assert captured["timeout"] == 120

def test_remote_executor_result_carries_root_trace_and_admission(monkeypatch):
    import z0int.remote_executor as remote

    req = g.build_remote_request(request())
    monkeypatch.setenv("Z0INT_EXECUTOR_PROVIDERS", "openrouter")
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")

    calls = []
    def fake_authority(operation, payload):
        calls.append((operation, payload))
        if operation == "claim":
            return {"claimed": True, "aodl_admission_receipt_id": "aodl-admission-123"}
        if operation == "complete":
            return payload["result"]
        raise AssertionError("unexpected authority operation " + operation)

    monkeypatch.setattr(remote, "request", fake_authority)
    monkeypatch.setattr(remote, "validate_remote", lambda args: (
        {
            "task": args["task"],
            "context": args.get("context", ""),
            "parent_agent": args["parent_agent"],
            "provider": args["provider"],
            "model": args["model"],
            "reason": args["reason"],
            "max_tokens": args["max_tokens"],
            "free_only": True,
        },
        {"baseline": {}, "free_only": True},
        {"openrouter": {"api_key_env": "OPENROUTER_API_KEY"}},
        {"candidates": [{"provider": "openrouter", "model": req["model"]}]},
    ))
    monkeypatch.setattr(remote, "execute_plan", lambda *a, **k: {
        "ok": True,
        "output": "done",
        "attempts": [],
        "requires_parent": False,
    })

    result = remote.execute(req)
    assert result["trace_id"] == req["trace_id"]
    assert result["aodl_admission_receipt_id"] == "aodl-admission-123"
    complete = next(payload for op, payload in calls if op == "complete")
    assert complete["result"]["trace_id"] == req["trace_id"]
    assert complete["result"]["aodl_admission_receipt_id"] == "aodl-admission-123"

