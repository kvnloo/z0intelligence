"""Promotion Authority v0: the one promotion rule as a DecisionOpportunity (fixture git repo)."""
import json
import os
import subprocess
from pathlib import Path

import pytest

from z0int import decision_opportunity as do
from z0int import promotion as pa

pytest.importorskip("coverage")

ENV = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.invalid",
       "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.invalid",
       "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_NOSYSTEM": "1"}
TEST_CMD = "PYTHONPATH=src python -m unittest tests.test_core"


def git(repo, *args):
    return subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, text=True, env=ENV).stdout.strip()


def write(repo, rel, text):
    p = repo / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text)


def commit(repo, msg):
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", msg)
    return git(repo, "rev-parse", "HEAD")


CLI = "from pkg import core\n{imports}\n\ndef main():\n    return core.value(){calls}\n"
TEST = ("import unittest\nfrom pkg import core\n{imports}\n\nclass T(unittest.TestCase):\n"
        "    def test_core(self):\n        self.assertEqual(core.value(), 1)\n{tests}")


def branch(repo, name, files, msg, base="master"):
    git(repo, "checkout", "-q", "-b", name, base)
    for rel, text in files.items():
        write(repo, rel, text)
    sha = commit(repo, msg)
    git(repo, "checkout", "-q", "master")
    return sha


@pytest.fixture()
def fx(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "-q", "-b", "master")
    write(repo, "pyproject.toml", '[project]\nname = "pkg"\nversion = "0"\n[project.scripts]\npkg = "pkg.cli:main"\n')
    write(repo, "src/pkg/__init__.py", "")
    write(repo, "src/pkg/core.py", "def value():\n    return 1\n")
    write(repo, "src/pkg/cli.py", CLI.format(imports="", calls=""))
    write(repo, "tests/__init__.py", "")
    write(repo, "tests/test_core.py", TEST.format(imports="", tests=""))
    write(repo, "shared.txt", "base\n")
    commit(repo, "base")
    shas = {}
    wired_tested = {
        "src/pkg/feature.py": "def feature():\n    return 2\n",
        "src/pkg/cli.py": CLI.format(imports="from pkg import feature", calls=" + feature.feature()"),
        "tests/test_core.py": TEST.format(imports="from pkg import cli, feature",
                                          tests="    def test_feature(self):\n        self.assertEqual(feature.feature(), 2)\n"
                                                "    def test_cli(self):\n        self.assertEqual(cli.main(), 3)\n"),
    }
    shas["feat/good"] = branch(repo, "feat/good", wired_tested, "feat: add feature")
    shas["feat/dead"] = branch(repo, "feat/dead", {"src/pkg/orphan.py": "def orphan():\n    return 3\n"}, "feat: orphan")
    shas["feat/untested"] = branch(repo, "feat/untested", {
        "src/pkg/extra.py": "def extra():\n    return 4\n",
        "src/pkg/cli.py": CLI.format(imports="from pkg import extra", calls=" + extra.extra()")}, "feat: extra")
    shas["feat/claim"] = branch(repo, "feat/claim", wired_tested, "perf: feature path is 2x faster than before")
    shas["feat/claim-receipt"] = branch(repo, "feat/claim-receipt", {
        **wired_tested, "benchmarks/feature/results_pinned.json": '{"speedup": 2.0}\n'},
        "perf: feature path is 2x faster (benchmarks/feature/results_pinned.json)")
    shas["feat/claim-bad-ref"] = branch(repo, "feat/claim-bad-ref", wired_tested,
                                        "perf: 2x faster, see benchmarks/feature/results_missing.json")
    shas["feat/conflict"] = branch(repo, "feat/conflict", {"shared.txt": "branch\n"}, "chore: edit shared")
    shas["feat/docs"] = branch(repo, "feat/docs", {"docs/note.md": "hi\n"}, "docs: note")
    git(repo, "checkout", "-q", "-b", "feat/old", "master")
    write(repo, "docs/old.md", "old\n")
    shas["feat/old"] = commit(repo, "docs: old")
    shas["feat/squashed"] = branch(repo, "feat/squashed", {"docs/sq.md": "sq\n"}, "docs: sq")
    git(repo, "checkout", "-q", "master")
    write(repo, "docs/sq.md", "sq\n")
    commit(repo, "docs: sq (squash-merged)")
    write(repo, "shared.txt", "master moved\n")
    commit(repo, "master: edit shared")
    git(repo, "merge", "-q", "--no-edit", "feat/old")
    return repo, shas, tmp_path


def manifest(tmp, names):
    p = tmp / "branches"
    p.write_text("# repo (default: master)\n" + "".join(f"{n}  required  kevin\n" for n in names))
    return p


def report(tmp, shas, statuses=None, final_gate="green"):
    statuses = statuses or {}
    entries = [{"branch": b, "sha": s, "status": statuses.get(b, "MERGED"), "reason": ""} for b, s in shas.items()]
    p = tmp / "report.json"
    p.write_text(json.dumps({"schema": pa.REPORT_SCHEMA, "base": {"branch": "master"}, "mode": "dry-run",
                             "finished_at": "2026-09-30T00:00:00Z", "test_cmd": TEST_CMD,
                             "result": {"final_gate": final_gate}, "entries": entries}))
    return p


def run(fx, names, **kw):
    repo, shas, tmp = fx
    rep = kw.pop("report_path", "default")
    if rep == "default":
        rep = report(tmp, {n: shas[n] for n in names if n in shas}, kw.pop("statuses", None))
    res = pa.check(repo, manifest(tmp, names), report=rep, test_cmd=TEST_CMD, use_gh=False, **kw)
    return {r["branch"]: r for r in res["branches"]}


def test_ready_branch_is_ask_never_act(fx):
    r = run(fx, ["feat/good"])["feat/good"]
    assert r["verdict"] == "ASK"
    assert r["failing"] == []
    assert all(r["criteria"][c]["status"] in ("observed", "n/a") for c in pa.CRITERIA)
    assert r["ask_about"] == ["authority:privileged"]
    act = next(a for a in r["opportunity"]["action_space"] if a["kind"] == "ACT")
    assert not act["legal"] and act["blocked_by"] == ["authority:privileged"]
    assert "pkg.cli [console_script `pkg`]" in r["criteria"]["wired"]["evidence"][1]


def test_act_only_after_identified_user_grant(fx):
    opp = run(fx, ["feat/good"])["feat/good"]["opportunity"]
    with pytest.raises(ValueError):
        pa.grant(opp, granted_by_user="")
    granted = pa.grant(opp, granted_by_user="kevin")
    assert pa.promotion_verdict(granted) == "ACT"
    assert granted["authority"]["granted"] == [{"effect": "privileged", "by": "kevin"}]
    assert granted["semantic_id"] != opp["semantic_id"]


def test_grant_does_not_make_an_unready_branch_act(fx):
    opp = run(fx, ["feat/dead"])["feat/dead"]["opportunity"]
    assert pa.promotion_verdict(pa.grant(opp, granted_by_user="kevin")) == "ABSTAIN"


def test_unreachable_module_is_not_wired(fx):
    r = run(fx, ["feat/dead"])["feat/dead"]
    assert r["verdict"] == "ABSTAIN"
    assert r["criteria"]["wired"]["status"] == "no_match"
    assert r["criteria"]["wired"]["value"]["unreached"] == ["src/pkg/orphan.py"]


def test_wired_but_untested_abstains(fx):
    r = run(fx, ["feat/untested"])["feat/untested"]
    assert r["criteria"]["wired"]["status"] == "observed"
    assert r["criteria"]["tested"]["status"] == "no_match"
    assert set(r["criteria"]["tested"]["value"]["uncovered"]) == {"src/pkg/cli.py", "src/pkg/extra.py"}
    assert r["verdict"] == "ABSTAIN"


def test_claim_without_receipt_asks(fx):
    r = run(fx, ["feat/claim"])["feat/claim"]
    assert r["criteria"]["receipt"]["status"] == "source_unavailable"
    assert r["verdict"] == "ASK"
    assert "unknown:promote.receipt" in r["ask_about"] and "authority:privileged" in r["ask_about"]


def test_claim_with_receipt_is_observed(fx):
    r = run(fx, ["feat/claim-receipt"])["feat/claim-receipt"]
    assert r["criteria"]["receipt"]["status"] == "observed"
    assert r["ask_about"] == ["authority:privileged"]


def test_cited_receipt_missing_from_tree_escalates(fx):
    r = run(fx, ["feat/claim-bad-ref"])["feat/claim-bad-ref"]
    assert r["verdict"] == "ESCALATE"
    assert "not in the branch tree" in r["criteria"]["receipt"]["contradiction"]


def test_kept_in_nightly_but_conflicts_with_default_escalates(fx):
    r = run(fx, ["feat/conflict"])["feat/conflict"]
    assert {k: r["criteria"]["merge"]["value"][k] for k in ("clean", "kept")} == {"clean": False, "kept": True}
    assert r["verdict"] == "ESCALATE"


def test_dropped_and_conflicting_abstains(fx):
    r = run(fx, ["feat/conflict"], statuses={"feat/conflict": "DROPPED"})["feat/conflict"]
    assert r["criteria"]["merge"]["status"] == "no_match"
    assert r["verdict"] == "ABSTAIN"


def test_missing_report_is_unknown_not_false(fx):
    r = run(fx, ["feat/good"], report_path=None)["feat/good"]
    assert r["criteria"]["merge"]["status"] == "unknown"
    assert r["verdict"] == "OBSERVE"
    assert r["observe"] and "nightly" in r["observe"][0]


def test_stale_report_sha_observes(fx):
    repo, shas, tmp = fx
    rep = report(tmp, {"feat/good": shas["feat/dead"]})
    r = run(fx, ["feat/good"], report_path=rep)["feat/good"]
    assert "stale" in r["criteria"]["merge"]["evidence"][1]
    assert r["verdict"] == "OBSERVE"


def test_nightly_test_failure_vs_green_ci_escalates(fx):
    r = run(fx, ["feat/good"], statuses={"feat/good": "WARN"})["feat/good"]
    assert r["criteria"]["tested"]["contradiction"]
    assert r["verdict"] == "ESCALATE"


def test_graduated_and_gone(fx):
    rows = run(fx, ["feat/old", "feat/nope"])
    assert rows["feat/old"]["verdict"] == "ABSTAIN" and "pending" in rows["feat/old"]["criteria"]
    assert rows["feat/nope"]["verdict"] == "ABSTAIN" and rows["feat/nope"]["sha"] is None


def test_docs_only_branch_is_vacuously_wired_and_tested(fx):
    r = run(fx, ["feat/docs"])["feat/docs"]
    assert r["verdict"] == "ASK"
    assert r["criteria"]["wired"]["value"] == {"added_modules": 0}


def test_no_tests_run_is_observe(fx):
    r = run(fx, ["feat/good"], run_tests=False)["feat/good"]
    assert r["criteria"]["tested"]["status"] == "unknown"
    assert r["verdict"] == "OBSERVE"


def test_check_is_read_only(fx):
    repo = fx[0]

    def snap():
        objs = sorted(str(p.relative_to(repo / ".git")) for p in (repo / ".git/objects").rglob("*") if p.is_file())
        return git(repo, "for-each-ref"), git(repo, "status", "--porcelain"), git(repo, "rev-parse", "HEAD"), objs

    before = snap()
    run(fx, ["feat/good", "feat/conflict", "feat/dead"])
    assert snap() == before


def test_semantic_id_is_deterministic(fx):
    a = run(fx, ["feat/good"])["feat/good"]["opportunity"]["semantic_id"]
    b = run(fx, ["feat/good"])["feat/good"]["opportunity"]["semantic_id"]
    assert a == b


def test_parse_manifest_and_instrument():
    m = pa.parse_manifest("# x  (default: main)\n# NIGHTLY_TEST_CMD (from ci):\n#   python -m pytest -q\n"
                          "feat/a  advisory  bot  # note\nfeat/b\n")
    assert m["default"] == "main" and m["test_cmd"] == "python -m pytest -q"
    assert m["entries"][0] == {"branch": "feat/a", "mode": "advisory", "owner": "bot", "note": "note"}
    assert m["entries"][1]["mode"] == "required"
    argv, env, _ = pa.instrument("PYTHONPATH=src FOO=1 python3 -m unittest t", Path("/tmp/d"), "/x/*")
    assert env == {"PYTHONPATH": "src", "FOO": "1"} and argv[1:4] == ["-m", "coverage", "run"] and argv[-3:] == ["-m", "unittest", "t"]
    assert pa.instrument("make test && echo", Path("/tmp/d"), "/x/*")[0] is None


def test_verdict_contract_matches_decision_opportunity():
    assert set(pa.CRITERIA) == {"merge", "wired", "tested", "receipt"}
    assert "privileged" in pa.EFFECTS and "privileged" not in do.DEFAULT_AUTHORITY


def test_claim_detector_needs_a_quantity_and_a_claim_word():
    assert pa.is_claim("perf: feature path is 2x faster than before")
    assert pa.is_claim("v0 hides 41% of description tokens, 2/30 recall failures")
    assert not pa.is_claim("docs: mark NanoJev third-party port as legacy benchmark only")
    assert not pa.is_claim("Architectural deletion, not byte deletion: no benchmark or data touched")
    assert not pa.is_claim("- AUROC, F1, balanced accuracy, Brier, ECE;")


def test_import_graph_roots_and_parent_packages(tmp_path):
    files = {"src/pkg/__init__.py": "", "src/pkg/sub/__init__.py": "", "src/pkg/sub/m.py": "X = 1\n",
             "src/pkg/hook.py": "def main():\n    return 0\n", "src/pkg/lonely.py": "",
             "plug/hooks/hooks.json": '{"command": "python -m pkg.hook"}',
             ".github/workflows/ci.yml": "run: python -m unittest pkg.lonely\n"}
    for rel, text in files.items():
        write(tmp_path, rel, text)
    g = pa.ImportGraph(tmp_path, sorted(files), {"pkg.sub.m": "systemd"})
    assert g.reach("src/pkg/sub/__init__.py") and g.reach("src/pkg/__init__.py")
    assert "Claude Code plugin manifest" in g.reach("src/pkg/hook.py")
    assert g.reach("src/pkg/lonely.py") is None  # a CI test line is test evidence, not production reach


def test_squash_merged_branch_has_nothing_to_promote(fx):
    r = run(fx, ["feat/squashed"])["feat/squashed"]
    assert r["verdict"] == "ABSTAIN"
    assert r["criteria"]["pending"]["status"] == "no_match" and "no-op" in r["criteria"]["pending"]["reason"]
