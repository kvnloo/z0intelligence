import importlib.util
import json
from pathlib import Path

RUN = Path(__file__).resolve().parents[1] / "experiments" / "hz-compaction-carryover" / "run.py"
spec = importlib.util.spec_from_file_location("hz_compaction_carryover", RUN)
cc = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cc)

MANIFEST = "0a070915d095e3739600321f51ce3cf361048e641fb22b53db5d1d9e2c4891da"
HEAD_TAIL, PINNED = 0.762744, 0.13753


def test_fixture_is_deterministic():
    assert cc.fixture(5) == cc.fixture(5)


def test_fixture_truth_matches_transcript():
    for s in cc.fixture():
        text = " ".join(s["turns"])
        assert cc.score(s, " ".join(s["live"].values())) == 0.0
        for k, v in s["live"].items():
            assert text.count(f"{k} ") + text.count(f"{k}:") == 1 + len(s["stale"].get(k, []))
            assert text.count(v) == 1  # each value written exactly once
        for k, olds in s["stale"].items():
            assert all(text.count(o) == 1 for o in olds)


def test_score_counts_lost_and_stale_items():
    s = {"live": {"sk-a-1": "sk-a-1", "opt-b-2": "v-new"}, "stale": {"opt-b-2": ["v-old"]}}
    assert cc.score(s, "loaded skill sk-a-1: x decision update: opt-b-2 = v-new") == 0.0
    assert cc.score(s, "loaded skill sk-a-1: x") == 0.5  # decision lost
    assert cc.score(s, "sk-a-1 v-new v-old") == 0.5  # stale value shadows the current one


def test_budget_is_enforced_not_trusted(monkeypatch):
    s = {"turns": ["a"], "live": {"sk-z-9": "sk-z-9"}, "stale": {}}
    monkeypatch.setitem(cc.ARMS, "cheat", lambda turns, budget: "pad " * budget + "sk-z-9")
    assert cc.run_arm("cheat", [s]) == [1.0]
    monkeypatch.setitem(cc.ARMS, "commas", lambda turns, budget: ",".join(["pad"] * budget + ["sk-z-9"]))
    assert cc.run_arm("commas", [s]) == [1.0]  # one whitespace "word", budget + 1 tokens


def test_fixture_and_baselines_are_pinned():
    """Same numbers on every Python version: a changed value here needs an epoch bump."""
    data = cc.fixture()
    assert cc.result("head-tail", data)["data_manifest_sha256"] == MANIFEST
    assert round(cc.mean(cc.run_arm("head-tail", data)), 6) == HEAD_TAIL
    assert round(cc.mean(cc.run_arm("pinned-manifest", data)), 6) == PINNED


def test_result_shape_and_verdicts(tmp_path):
    data = cc.fixture(20)
    ctrl = cc.result("head-tail", data)
    cand = cc.result("pinned-manifest", data)
    required = ("schema", "exp_id", "epoch", "arm", "data_tier", "data_manifest_sha256",
                "repo_sha", "n", "metric", "verdict", "command")
    assert all(k in ctrl for k in required)
    assert ctrl["verdict"] == "NULL" and "controls" not in ctrl
    assert cand["verdict"] in {"KEEP", "DISCARD"} and set(cand["controls"]) == {"head-tail"}
    assert cand["metric"]["value"] < ctrl["metric"]["value"]
    assert "--out" not in cand["command"]  # posted publicly: no local paths


def test_cli_writes_result(tmp_path):
    out = tmp_path / "r.json"
    assert cc.main(["--arm", "head-tail", "--out", str(out)]) == 0
    assert json.loads(out.read_text())["exp_id"] == "hz-compaction-carryover"
