"""Carly navigation inlet: hot-path decisions, learning from Carly, budget tuning."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from z0int.carly_nav import decide as hot
from z0int.carly_nav import learn as learner
from z0int.carly_nav import store, tune

DAY = 86400.0


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("Z0INT_HOME", str(tmp_path / "z0int"))
    monkeypatch.setenv("Z0INT_CARLY_SESSIONS", str(tmp_path / "carly"))
    (tmp_path / "carly").mkdir()
    return tmp_path


def jev_answer(action: str, prob: float, target: str | None = None):
    def transport(body, timeout):
        answers = {"action": {"choice": action, "probabilities": {action: prob}}}
        if target is not None:
            answers["target"] = {"choice": target}
        return {"answers": answers}

    return transport


def write_carly_turn(root: Path, ts: float, text: str, calls: list[dict]) -> None:
    from datetime import datetime, timezone

    iso = datetime.fromtimestamp(ts, timezone.utc).isoformat().replace("+00:00", "Z")
    rows = [
        {"type": "message", "timestamp": iso, "message": {"role": "user", "content": [{"type": "text", "text": text}]}},
        {"type": "message", "timestamp": iso, "message": {"role": "assistant", "content": [
            {"type": "toolCall", "name": c["name"], "arguments": c["arguments"]} for c in calls]}},
    ]
    with open(root / "carly" / f"s{ts}.jsonl", "a", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row) + "\n")


def fallback(episode: str, utterance: str, ts: float, offered=("next_tab", "previous_tab", "focus")) -> None:
    store.append_jsonl("episodes.jsonl", {"id": episode, "ts": ts, "backend": "jev", "utterance": utterance,
                                          "offered": list(offered), "decision": {"action": "none"}})
    store.append_jsonl("outcomes.jsonl", {"episode": episode, "outcome": "fallback", "ts": ts})


def universe(*ids: str) -> None:
    store.write_json("universe.json", {"actions": [{"id": i, "label": i.replace("_", " ").title()} for i in ids]})


def test_decide_runs_learned_action_and_logs_episode():
    catalog = store.default_catalog()
    catalog["actions"]["zoom"] = {"description": "Zoom the block.", "exec": {"type": "tern_action", "name": "zoom"},
                                  "status": "active", "source": "learned", "exemplars": ["make this big"], "support": 4}
    seen = {}

    def transport(body, timeout):
        seen["criteria"] = body["questions"]["action"]["criteria"]
        return jev_answer("zoom", 0.9)(body, timeout)

    result = hot.decide("blow this pane up", [], transport=transport, catalog=catalog)
    assert result["action"] == "zoom" and result["exec"] == {"type": "tern_action", "name": "zoom"}
    assert '"make this big"' in seen["criteria"]["zoom"] and "none" in seen["criteria"]
    [episode] = list(store.read_jsonl("episodes.jsonl"))
    assert episode["id"] == result["episode"] and "zoom" in episode["offered"]


def test_decide_hands_off_below_threshold_or_without_target():
    low = hot.decide("hmm", [], transport=jev_answer("next_tab", 0.2))
    assert low["action"] == "none" and low["reason"] == "low_confidence"
    candidates = [{"id": "t1", "label": "tab 1: logs"}]
    no_target = hot.decide("go there", candidates, transport=jev_answer("focus", 0.9, target="none"))
    assert no_target["action"] == "none" and no_target["reason"] == "no_target"
    hit = hot.decide("logs", candidates, transport=jev_answer("focus", 0.9, target="t1"))
    assert hit["action"] == "focus" and hit["target"] == "t1"
    assert hot.decide("x", [], transport=lambda b, t: None)["action"] == "none"


def test_learn_promotes_action_carly_keeps_running(isolated):
    now = 1_800_000_000.0
    universe("zoom", "close_pane")
    for i, phrase in enumerate(["zoom in on this", "make the pane big", "maximize this block"]):
        ts = now - 100 * (i + 1)
        fallback(f"e{i}", phrase, ts)
        write_carly_turn(isolated, ts + 2, f"[window outline]\n{phrase}", [{"name": "run_command", "arguments": {"command": "zoom"}}])
    report = learner.learn(now=now)
    catalog = store.load_catalog()
    assert catalog["actions"]["zoom"]["status"] == "active"
    assert catalog["actions"]["zoom"]["exec"] == {"type": "tern_action", "name": "zoom"}
    assert {"action": "zoom", "change": "promoted", "support": 3} in report["changes"]
    assert "zoom" in hot.offered_actions(catalog)


def test_learn_needs_support_and_never_learns_destructive_actions(isolated):
    now = 1_800_000_000.0
    universe("zoom", "close_pane")
    fallback("a", "zoom please", now - 10)
    write_carly_turn(isolated, now - 8, "zoom please", [{"name": "lua", "arguments": {"code": "cx:command('zoom')"}}])
    for i in range(4):
        fallback(f"c{i}", f"close it {i}", now - 50 - i)
        write_carly_turn(isolated, now - 48 - i, f"close it {i}", [{"name": "actions", "arguments": {"action": "close_pane"}}])
    report = learner.learn(now=now)
    actions = store.load_catalog()["actions"]
    assert actions["zoom"]["status"] == "candidate"
    assert "close_pane" not in actions and report["blocked"]["close_pane"] == 4


def test_learn_skips_ambiguous_turns_and_counts_unsupported(isolated):
    now = 1_800_000_000.0
    universe("zoom", "next_tab")
    fallback("m", "do things", now - 10)
    write_carly_turn(isolated, now - 9, "do things", [{"name": "lua", "arguments": {"code": "cx:command('zoom') cx:command('next_tab')"}}])
    fallback("s", "switch to work session", now - 20)
    write_carly_turn(isolated, now - 19, "switch to work session", [{"name": "session", "arguments": {"operation": "switch"}}])
    fallback("q", "what time is it", now - 30)
    write_carly_turn(isolated, now - 29, "what time is it", [])
    report = learner.learn(now=now)
    assert report["unsupported"] == {"unsupported:session_switch": 1}
    labels = {row["episode"]: row["label"] for row in store.read_jsonl("labels.jsonl")}
    assert "m" not in labels and labels["q"] == "none"


def test_missed_phrasing_becomes_exemplar_and_stale_learned_action_retires(isolated):
    now = 1_800_000_000.0
    catalog = store.default_catalog()
    catalog["actions"]["zoom"] = {"description": "Zoom.", "exec": {"type": "tern_action", "name": "zoom"},
                                  "status": "active", "source": "learned", "exemplars": [], "support": 5, "last_used": now - 90 * DAY}
    store.save_catalog(catalog)
    fallback("x", "flip to the tab on the right", now - 5)
    write_carly_turn(isolated, now - 4, "flip to the tab on the right", [{"name": "run_command", "arguments": {"command": "next_tab"}}])
    learner.learn(now=now)
    actions = store.load_catalog()["actions"]
    assert actions["next_tab"]["exemplars"][0] == "flip to the tab on the right"
    assert actions["zoom"]["status"] == "retired" and actions["next_tab"]["status"] == "active"


def test_undone_actions_lower_precision_and_raise_threshold():
    labels = [{"backend": "jev", "confidence": 0.45, "correct": False}] * 10
    labels += [{"backend": "jev", "confidence": 0.8, "correct": True}] * 20
    assert learner.calibrate_threshold(labels, 0.4) == 0.5
    assert learner.calibrate_threshold(labels[:5], 0.4) == 0.4


def test_tune_picks_smallest_budget_within_tolerance():
    catalog = store.default_catalog()
    learned = [f"act{i:02d}" for i in range(20)]
    for rank, aid in enumerate(learned):
        catalog["actions"][aid] = {"description": aid, "exec": {"type": "tern_action", "name": aid},
                                   "status": "active", "source": "learned", "exemplars": [], "support": 100 - rank}
    store.save_catalog(catalog)
    with open(store.path("labels.jsonl"), "w") as handle:
        for i in range(24):
            handle.write(json.dumps({"utterance": f"request {i}", "label": "act00" if i % 2 else "next_tab", "ts": i}) + "\n")

    def transport(body, timeout):
        # Bigger option lists confuse this fake judge: it misses act00 with more than 8 learned options.
        options = body["questions"]["action"]["criteria"]
        wants_act = "request 1" in body["state"]["request"] or body["state"]["request"][-1] in "13579"
        if wants_act:
            return {"answers": {"action": {"choice": "act00" if len(options) <= 12 else "none"}}}
        return {"answers": {"action": {"choice": "next_tab"}}}

    result = tune.tune(transport=transport)
    assert result["status"] == "ok" and result["chosen"] == 8
    assert store.load_catalog()["policy"]["max_actions"] == 8
    assert tune.tune(transport=transport, min_rows=100)["status"] == "insufficient_labels"
