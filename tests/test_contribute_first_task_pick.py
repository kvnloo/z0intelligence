"""CW6/E2: real alignment and CLI, fake provider, no network/model calls."""
import copy
import json
from pathlib import Path

import pytest

from z0int.contribute import align as A, cli as K, priorities as P, state as St, schemas
from z0int.contribute import first_task_provider as transport

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def first_task_case():
    doc = P.load_priorities(ROOT)
    base = next(t for t in doc["tasks"] if t["newcomer"] and t["difficulty"] == "S")
    tasks = []
    for i in range(7):
        task = copy.deepcopy(base)
        task.update(id=f"task-{i}", title="Test task", kind="docs", tier="P1", rank=i + 1,
                    skills=["python"], difficulty="S", impact_note="Improves tests", claim="none",
                    newcomer=True, needs_owner_approval=False, status="open", gate=None,
                    runtime="repo_only", network="none", expected_hours=1, toolchains=[])
        task["hardware"] = {"gpu_vram_gib": 0, "ram_gib": 0, "disk_gib": 0,
                            "os": ["linux"], "arch": ["x86_64"]}
        tasks.append(task)
    doc["tasks"] = tasks
    doc["alignment"]["tie_epsilon"] = 0.05
    doc["alignment"]["weights"] = {"impact": 0, "fit": 1, "interest": 0, "ramp": 0, "uniq_hw": 0}
    profile = {"tier": "T1", "toolchains": ["python"], "hw": {"os": "linux", "arch": "x86_64"}}
    return doc, P.load_vocab(ROOT), profile


class FakeProvider:
    def __init__(self, answer):
        self.answer = answer
        self.calls = []

    def __call__(self, payload):
        self.calls.append(copy.deepcopy(payload))
        if isinstance(self.answer, Exception):
            raise self.answer
        return self.answer


def run(first_task_case, provider, mode="local"):
    doc, vocab, profile = first_task_case
    return A.align(doc, vocab, profile, live_claims={}, issue_states={}, llm=mode, provider=provider)


@pytest.mark.parametrize("answer,reason", [
    ({"pick": "invented", "reason_codes": ["tier:P1"]}, "out_of_set"),
    ({"pick": "task-1", "reason_codes": ["invented:code"]}, "verifier_failed"),
    ({"pick": "task-1", "reason_codes": ["skill_match:rust"]}, "verifier_failed"),
    (TimeoutError("private detail"), "timeout"),
])
def test_first_task_pick_failures_use_deterministic_top(first_task_case, answer, reason):
    fake = FakeProvider(answer)
    out = run(first_task_case, fake)
    assert out["pick"]["task"] == "task-0"
    assert out["first_task_pick"]["reason"] == reason
    assert len(fake.calls) == out["first_task_pick"]["calls"] == 1
    assert "private detail" not in json.dumps(out)


@pytest.mark.parametrize("code", ["tier:P0", "interest:python", "first_task_size:M", "size:S"])
def test_first_task_pick_every_code_agrees_with_picked_card(first_task_case, code):
    out = run(first_task_case, FakeProvider({"pick": "task-1", "reason_codes": [code]}))
    assert out["pick"]["task"] == "task-0"
    assert out["first_task_pick"]["reason"] == "verifier_failed"


def test_first_task_pick_valid_bounded_pick_preserves_deterministic_text(first_task_case):
    answer = {"pick": "task-1", "reason_codes": ["skill_match:python", "tier:P1", "first_task_size:S"]}
    fake = FakeProvider(answer)
    original = copy.deepcopy(first_task_case)
    out = run(first_task_case, fake)
    assert out["pick"]["task"] == "task-1"
    assert out["first_task_pick"]["accepted"] is True
    assert out["ranked"][0]["task"] == "task-0"
    assert len(fake.calls[0]["candidates"]) == 5
    assert first_task_case == original
    assert out["pick"]["title"] == "Test task"
    assert all(c["task"] != "task-1" for c in out["alternatives"])


def test_first_task_pick_none_makes_zero_calls(first_task_case, monkeypatch):
    fake = FakeProvider(AssertionError("no send"))
    monkeypatch.setattr(transport, "safe_payload", lambda p: pytest.fail("no prompt"))
    assert run(first_task_case, fake, "none")["pick"]["task"] == "task-0"
    assert fake.calls == []


def test_first_task_pick_filtered_task_cannot_be_added(first_task_case):
    first_task_case[0]["tasks"][1]["newcomer"] = False
    fake = FakeProvider({"pick": "task-1", "reason_codes": ["tier:P1"]})
    out = run(first_task_case, fake)
    assert "task-1" not in fake.calls[0]["candidates"]
    assert out["pick"]["task"] == "task-0"


def test_first_task_pick_no_tie_zero_calls(first_task_case):
    first_task_case[0]["tasks"] = first_task_case[0]["tasks"][:1]
    fake = FakeProvider(AssertionError("no send"))
    assert run(first_task_case, fake)["first_task_pick"]["reason"] == "no_tie"
    assert not fake.calls


def test_first_task_pick_t3_aborts_before_provider(first_task_case):
    first_task_case[2]["tier"] = "T3"
    first_task_case[2]["repo_names"] = ["private"]
    fake = FakeProvider(AssertionError("no send"))
    assert run(first_task_case, fake)["first_task_pick"]["reason"] == "unsafe_facts"
    assert not fake.calls


def test_first_task_pick_canary_aborts_before_provider(first_task_case, monkeypatch):
    monkeypatch.setenv("TEST_CANARY", "CW6-PRIVATE-CANARY")
    first_task_case[0]["tasks"][0]["title"] = "CW6-PRIVATE-CANARY"
    fake = FakeProvider(AssertionError("no send"))
    assert run(first_task_case, fake)["first_task_pick"]["reason"] == "unsafe_facts"
    assert not fake.calls


def test_first_task_pick_provider_cannot_mutate_verifier_facts(first_task_case):
    def evil(payload):
        payload["facts"]["profile"]["skills"].append("rust")
        return {"pick": "task-1", "reason_codes": ["skill_match:rust"]}
    assert run(first_task_case, evil)["first_task_pick"]["reason"] == "verifier_failed"


def test_first_task_pick_local_transport_real_executable(first_task_case, tmp_path, monkeypatch):
    import sys
    executable = tmp_path / "classifier"
    executable.write_text(f'#!{sys.executable}\nimport json,sys\np=json.load(sys.stdin)\nprint(json.dumps({{"pick":p["candidates"][1],"reason_codes":["tier:P1"]}}))\n')
    executable.chmod(0o700)
    monkeypatch.setenv("Z0INT_FIRST_TASK_PROVIDER", str(executable))
    assert run(first_task_case, None, "auto")["pick"]["task"] == "task-1"


@pytest.mark.parametrize("mode", ["none", "local", "auto"])
def test_first_task_pick_cli_receipt_and_modes(first_task_case, tmp_path, monkeypatch, mode):
    doc, vocab, profile = first_task_case
    monkeypatch.setenv("Z0INT_HOME", str(tmp_path / "state"))
    fake = FakeProvider({"pick": "task-1", "reason_codes": ["tier:P1"]})
    env = K.Env(root=ROOT, gh=None, harness="test", profile_probe=lambda: profile, first_task_provider=fake)
    monkeypatch.setattr(K, "_profile", lambda *args: (profile, "T1"))
    monkeypatch.setattr(K, "_load_doc", lambda env: (doc, vocab, []))
    monkeypatch.setattr(K, "_live_state", lambda *args: ({}, {}))
    monkeypatch.setattr(K, "_done_count", lambda: 0)
    assert K.main(["suggest", "--llm=" + mode, "--json"], env=env) == 0
    sug = St.read_json(St.home() / "suggestion.json")
    assert schemas.validate(sug, "suggestion") == []
    assert sug["pick"]["task"] == ("task-0" if mode == "none" else "task-1")
    assert len(fake.calls) == (0 if mode == "none" else 1)
    receipt = St.read_json(next((St.home() / "receipts").glob("*.json")))
    assert receipt["data"]["first_task_pick"]["reason"] == ("disabled" if mode == "none" else "verified")
