"""Promotion replay simulator v0: labels, policies, scoring and the pre-registration guard."""
import json
from pathlib import Path

from z0int import promotion as pa
from z0int import promotion_sim as ps


def ev(**kw):
    base = {"id": "r#1", "repo": "r", "t": 0.0, "tier": "default", "agent": "agent", "size": 10, "docs_only": False,
            "touches_src": True, "touches_tests": True, "ci_all": "green", "ci_code": "green", "wired": "observed",
            "tested_static": "observed", "receipt": "n/a", "reverted": False, "ci_broke": False,
            "fix_3d": [], "observed_days": 30.0}
    base.update(kw)
    base["pa_v0"] = ps.pa_verdict(base)
    return base


def test_ci_state_latest_rerun_wins_and_receipt_is_not_code():
    checks = [
        {"workflowName": "Evidence receipt", "name": "receipt", "conclusion": "FAILURE", "completedAt": "2026-09-01T00:00:00Z"},
        {"workflowName": "Evidence receipt", "name": "receipt", "conclusion": "SUCCESS", "completedAt": "2026-09-01T01:00:00Z"},
        {"workflowName": "CI", "name": "test", "conclusion": "SUCCESS", "completedAt": "2026-09-01T00:30:00Z"},
    ]
    assert ps.ci_state(checks)["all"] == "green"
    checks.append({"workflowName": "Evidence receipt", "name": "receipt", "conclusion": "FAILURE",
                   "completedAt": "2026-09-01T02:00:00Z"})
    st = ps.ci_state(checks)
    assert st["all"] == "red" and st["code"] == "green" and st["n_code_checks"] == 1
    assert ps.ci_state([])["all"] == "none"


def test_pa_verdict_mapping():
    assert ev()["pa_v0"] == "ASK"
    assert ev(wired="no_match")["pa_v0"] == "ABSTAIN"
    assert ev(ci_code="red")["pa_v0"] == "ABSTAIN"          # CI red: PA `tested` is negative
    assert ev(tested_static="unknown")["pa_v0"] == "OBSERVE"
    assert ev(receipt="source_unavailable")["pa_v0"] == "ASK"  # a claim without a receipt is a question


def test_policies_route_and_score():
    evs = [ev(), ev(ci_all="red", reverted=True), ev(wired="no_match", fix_3d=["x"]), ev(size=5000)]
    bad = ps._bad_fn(3, False)
    assert [ps.pa_v0(e) for e in evs] == ["HUMAN", "HUMAN", "BLOCK", "HUMAN"]
    m = ps.score(evs, ps.auto_on_green, bad)
    assert (m["auto"], m["human_reviews"], m["bad_auto"], m["bad_through"]) == (3, 1, 1, 2)
    m = ps.score(evs, ps.threshold(1000, "all", need_wired=True), bad)
    assert (m["auto"], m["bad_auto"], m["good_auto"]) == (1, 0, 1)
    m = ps.score(evs, ps.human_all, bad)
    assert m["human_reviews"] == 4 and m["bad_through"] == 2 and m["base_bad_rate"] == 0.5
    assert ps.is_bad(ev(fix_3d=["x"]), strict=True) is False


def test_decision7_scope():
    assert ps.decision7(ev(tier="integration")) == "AUTO"
    assert ps.decision7(ev(tier="integration", agent="human")) == "HUMAN"
    assert ps.decision7(ev(tier="default")) == "HUMAN"


def test_select_respects_bad_rate_bar():
    good = [ev(t=float(i)) for i in range(8)]
    bad_big = [ev(t=10.0 + i, size=900, reverted=True) for i in range(4)]
    pols = {"human_all": ps.human_all, "all_green": ps.auto_on_green, "small": ps.threshold(100, "all")}
    sel = ps.select(good + bad_big, pols, ps._bad_fn(3, False))
    assert sel["pick"] == "small" and "all_green" not in sel["eligible"]


def test_split_by_time_holds_out_latest():
    evs = [ev(t=float(i)) for i in range(10)]
    train, hold, t = ps.split_by_time(list(reversed(evs)), 0.3)
    assert [e["t"] for e in hold] == [7.0, 8.0, 9.0] and t == 7.0 and len(train) == 7


def test_revert_and_fix_labels():
    e = {"title": "feat: add widget thing", "number": 12}
    landed = [ps.Landed("a" * 40, 50.0, 'Revert "feat: add widget thing"', "", frozenset({"src/w.py"})),
              ps.Landed("b" * 40, 200.0, "fix: widget off-by-one", "", frozenset({"src/w.py"})),
              ps.Landed("c" * 40, 300.0, "fix: typo", "", frozenset({"README.md"})),
              ps.Landed("d" * 40, 9e6, "fix: late", "", frozenset({"src/w.py"}))]
    assert ps.reverted_by(e, {"f" * 40}, landed, 10.0) == "a" * 40
    assert ps.reverted_by(e, {"f" * 40}, landed, 60.0) is None
    assert ps.fix_followup({"src/w.py"}, set(), landed, 10.0, 3) == ["b" * 40]
    assert ps.fix_followup({"README.md"} - {"README.md"}, set(), landed, 10.0, 3) == []


def test_ci_broke_needs_green_before():
    runs = [{"name": "CI", "event": "push", "head_branch": "main", "status": "completed", "head_sha": "p",
             "conclusion": "success", "created_at": "2026-09-01T00:00:00Z"},
            {"name": "CI", "event": "push", "head_branch": "main", "status": "completed", "head_sha": "m",
             "conclusion": "failure", "created_at": "2026-09-02T00:00:00Z"},
            {"name": "Evidence receipt", "event": "push", "head_branch": "main", "status": "completed", "head_sha": "m",
             "conclusion": "failure", "created_at": "2026-09-02T00:00:00Z"}]
    t = ps.ts("2026-09-02T00:00:00Z")
    r = ps.ci_broke(runs, "main", "m", t)
    assert r["broke"] and r["workflows"] == ["CI"]
    runs[0]["conclusion"] = "failure"
    assert not ps.ci_broke(runs, "main", "m", t)["broke"]          # already red: not this merge's fault
    assert not ps.ci_broke(runs, "main", "zz", t)["observed"]


def test_cached_graph_matches_promotion_graph_and_static_tested(tmp_path):
    files = {"pyproject.toml": '[project]\nname="p"\n[project.scripts]\np = "pkg.cli:main"\n',
             "pkg/__init__.py": "", "pkg/cli.py": "from pkg import core\nimport json\n",
             "pkg/core.py": "def value():\n    return 1\n", "pkg/dead.py": "def f():\n    return 2\n",
             "tests/test_core.py": "from pkg import core\n"}
    for rel, txt in files.items():
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_text(txt)
    rels = sorted(files)
    g, h = pa.ImportGraph(tmp_path, rels, {}), ps.CachedImportGraph(tmp_path, rels, {})
    assert dict(g.edges) == dict(h.edges) and g.parent == h.parent
    assert h.reach("pkg/core.py") and h.reach("pkg/dead.py") is None
    assert ps.static_tested(h, ["pkg/core.py"]) == ("observed", [])
    assert ps.static_tested(h, ["pkg/core.py", "pkg/dead.py"]) == ("no_match", ["pkg/dead.py"])
    assert ps.static_tested(None, ["pkg/core.py"])[0] == "unknown"


def test_holdout_refuses_changed_events(tmp_path):
    evs = [ev(t=float(i), id=f"r#{i}") for i in range(20)]
    p = tmp_path / "events.json"
    p.write_text(json.dumps(evs))
    out = tmp_path / "out"
    assert ps.main(["prereg", "--events", str(p), "--out", str(out)]) == 0
    pre = json.loads((out / "prereg.json").read_text())
    assert pre["split"]["n_holdout"] == 6
    evs[0]["reverted"] = True
    p.write_text(json.dumps(evs))
    assert ps.main(["holdout", "--events", str(p), "--out", str(out)]) == 2


def test_szz_blames_the_introducing_commit(tmp_path):
    import os
    import subprocess
    env = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.invalid", "GIT_COMMITTER_NAME": "t",
           "GIT_COMMITTER_EMAIL": "t@example.invalid", "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_NOSYSTEM": "1"}

    def g(*a):
        return subprocess.run(["git", "-C", str(tmp_path), *a], check=True, capture_output=True, text=True, env=env).stdout.strip()

    g("init", "-q")
    (tmp_path / "m.py").write_text("a = 1\n")
    g("add", "-A"); g("commit", "-qm", "base")
    (tmp_path / "m.py").write_text("a = 1\nb = 2\nc = 3\n")
    g("add", "-A"); g("commit", "-qm", "feat: add b and c")
    intro = g("rev-parse", "HEAD")
    (tmp_path / "m.py").write_text("a = 1\nb = 20\nc = 3\nd = 4\n")
    g("add", "-A"); g("commit", "-qm", "fix: b off by ten")
    fix = ps.Landed(g("rev-parse", "HEAD"), 1.0, "fix: b off by ten", "", frozenset({"m.py"}))
    assert ps.szz_origins(tmp_path, fix) == {intro}
    (tmp_path / "m.py").write_text("a = 1\nb = 20\nc = 3\nd = 4\ne = 5\n")
    g("add", "-A"); g("commit", "-qm", "fix: add e")          # pure addition: blames nothing
    assert ps.szz_origins(tmp_path, ps.Landed(g("rev-parse", "HEAD"), 2.0, "", "", frozenset({"m.py"}))) == set()
