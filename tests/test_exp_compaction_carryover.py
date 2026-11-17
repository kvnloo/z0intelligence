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
    assert cand["command"].startswith("python3 ")  # stock Debian/macOS have no `python`


def test_cli_writes_result(tmp_path):
    out = tmp_path / "r.json"
    assert cc.main(["--arm", "head-tail", "--out", str(out)]) == 0
    assert json.loads(out.read_text())["exp_id"] == "hz-compaction-carryover"


def test_repo_sha_marks_uncommitted_experiment_code(tmp_path, monkeypatch):
    """#372: a run from a dirty tree must not pass as a run of HEAD."""
    import subprocess
    for k in ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", "/dev/null")
    git = lambda *a: subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", "-c", "commit.gpgsign=false",
                                     *a], cwd=tmp_path, check=True, capture_output=True, text=True).stdout.strip()
    exp = tmp_path / "experiments" / "x"
    exp.mkdir(parents=True)
    (exp / "run.py").write_text("A = 1\n")
    git("init", "-q")
    git("add", "-A")
    git("commit", "-qm", "base")
    head = git("rev-parse", "HEAD")
    assert cc.repo_sha(exp) == head
    (tmp_path / "result.json").write_text("{}")  # the docstring's --out lands in the repo root
    assert cc.repo_sha(exp) == head
    (exp / "run.py").write_text("A = 2\n")  # tracked edit that changes the score
    assert cc.repo_sha(exp) == head + "+dirty"
    (tmp_path / "src").mkdir()
    git("checkout", "-q", "--", ".")
    git("config", "status.showUntrackedFiles", "no")  # a user setting must not hide new files
    (tmp_path / "src" / "arm.py").write_text("")  # new, uncommitted arm code
    assert cc.repo_sha(exp) == head + "+dirty"
    git("add", "-A")
    git("commit", "-qm", "arm")
    assert cc.repo_sha(exp) == git("rev-parse", "HEAD")
    (tmp_path / "outside").mkdir()
    monkeypatch.setenv("GIT_CEILING_DIRECTORIES", str(tmp_path))
    assert cc.repo_sha(tmp_path / "outside") == "unknown"  # not in a repo: as before
