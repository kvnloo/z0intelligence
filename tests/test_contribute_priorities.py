"""CW1: .z0/priorities.yaml, vocab, credits and the priorities-check laws (spec §2)."""
from __future__ import annotations

import copy
from pathlib import Path

import pytest

from z0int.contribute import priorities as P

ROOT = Path(__file__).resolve().parents[1]
EXCLUDED_ISSUES = {234, 94, 95, 131, 187, 181, 22, 208, 195, 203}


@pytest.fixture(scope="module")
def repo_doc():
    return P.load_priorities(ROOT)


@pytest.fixture(scope="module")
def vocab():
    return P.load_vocab(ROOT)


def _errs(doc, vocab, root=ROOT):
    return P.check_priorities(doc, vocab, repo_root=root)


@pytest.mark.contrib_smoke
def test_repo_priorities_pass_the_check(repo_doc, vocab):  # P1
    assert _errs(repo_doc, vocab) == []
    assert P.check_vocab(vocab) == []
    assert P.check_credits(P.load_yaml(ROOT / ".z0" / "credits.yaml")) == []


def test_priorities_are_marked_draft_for_owner_review(repo_doc):
    first = (ROOT / ".z0" / "priorities.yaml").read_text(encoding="utf-8").splitlines()[0]
    assert first.startswith("# DRAFT")
    assert repo_doc["draft"] is True


def test_exclusion_list_is_never_newcomer(repo_doc):
    by_issue = {t["issue"]: t for t in repo_doc["tasks"] if t["issue"]}
    assert EXCLUDED_ISSUES <= set(by_issue)
    for n in EXCLUDED_ISSUES:
        assert by_issue[n]["newcomer"] is False, n


def test_each_acceptance_profile_has_a_newcomer_task(repo_doc):  # P2 (content side)
    newcomer = [t for t in repo_doc["tasks"] if t["newcomer"] and t["status"] == "open"]
    tags = lambda t: set(t["skills"])  # noqa: E731
    assert any(tags(t) & {"typescript", "javascript", "frontend"} for t in newcomer)
    assert any(t["hardware"]["gpu_vram_gib"] > 0 or "ml-eval" in tags(t) for t in newcomer)
    # O-2: systems maps to Python/C++ performance and latency tasks.
    assert any(tags(t) & {"systems", "latency"} and t["hardware"]["gpu_vram_gib"] == 0 for t in newcomer)


def test_z0evals_kind_has_a_smoke_path(repo_doc):  # O-7
    z = [t for t in repo_doc["tasks"] if t["kind"] == "z0evals"]
    assert z and all(t["smoke"] and t["newcomer"] for t in z)


def test_onboarding_feedback_task_is_always_on(repo_doc):
    fb = [t for t in repo_doc["tasks"] if t["id"] == P.FEEDBACK_TASK]
    assert len(fb) == 1
    t = fb[0]
    assert t["newcomer"] and t["status"] == "open" and t["claim"] == "none"
    assert t["hardware"]["gpu_vram_gib"] == 0 and t["runtime"] == "repo_only" and t["network"] == "none"


# --- law-like rules: each has a red (broken) case ------------------------------------

def _mutate(doc, fn):
    d = copy.deepcopy(doc)
    fn(d)
    return d


def _task(d, tid):
    return next(t for t in d["tasks"] if t["id"] == tid)


BREAKERS = {
    "duplicate id": lambda d: d["tasks"].append(copy.deepcopy(_task(d, "t-24-rlcd-alignbench"))),
    "duplicate tier rank": lambda d: _task(d, "t-24-rlcd-alignbench").update(rank=1),
    "skill outside vocab": lambda d: _task(d, "t-24-rlcd-alignbench")["skills"].append("haskell"),
    "toolchain outside vocab": lambda d: _task(d, "t-24-rlcd-alignbench").update(toolchains=["make"]),
    "issue-less exclusive claim": lambda d: _task(d, "t-z0evals-memory-cohort").update(claim="exclusive"),
    "verifier path missing": lambda d: _task(d, "t-24-rlcd-alignbench").update(
        verifier="python -m pytest -q tests/test_does_not_exist.py"),
    "smoke path missing": lambda d: _task(d, "t-231-local-inference-profile").update(
        smoke="python -m pytest -q tests/nope/test_x.py"),
    "gate on unknown task": lambda d: _task(d, "t-24-rlcd-alignbench").update(gate={"task": "t-nope"}),
    "self gate": lambda d: _task(d, "t-24-rlcd-alignbench").update(gate={"task": "t-24-rlcd-alignbench"}),
    "weights do not sum to 1": lambda d: d["alignment"]["weights"].update(impact=0.9),
    "feedback task missing": lambda d: d.update(tasks=[t for t in d["tasks"] if t["id"] != P.FEEDBACK_TASK]),
    "newcomer verifier uses env/secret": lambda d: _task(d, "t-24-rlcd-alignbench").update(
        verifier="python -m pytest -q tests/test_alignbench.py --token $GH_TOKEN"),
    "newcomer needs owner approval (schema)": lambda d: _task(d, "t-24-rlcd-alignbench").update(
        needs_owner_approval=True),
}


@pytest.mark.parametrize("name", sorted(BREAKERS))
def test_check_rejects_broken_priorities(repo_doc, vocab, name):
    assert _errs(_mutate(repo_doc, BREAKERS[name]), vocab), name


def test_vocab_laws(vocab):
    assert P.check_vocab(_mutate(vocab, lambda v: v["affinity"].update(rust=["gardening"])))
    assert P.check_vocab(_mutate(vocab, lambda v: v["toolchain_languages"].update(cargo=["klingon"])))
    assert P.check_vocab(_mutate(vocab, lambda v: v.update(installable_toolchains=["apt"])))


def test_credits_laws():
    ok = {"schema": "z0.credits.v1", "contributors": [
        {"login": "octo-cat", "tasks": ["t-24-rlcd-alignbench"], "prs": [12], "since": "2026-10-26"}]}
    assert P.check_credits(ok) == []
    bad_email = copy.deepcopy(ok)
    bad_email["contributors"][0]["login"] = "someone@example.com"
    assert P.check_credits(bad_email)
    dup = copy.deepcopy(ok)
    dup["contributors"].append(copy.deepcopy(ok["contributors"][0]))
    assert P.check_credits(dup)


# --- live (L1 labels vs L2 file) conflict rule ----------------------------------------

def _live(doc, **issues):
    base = {t["issue"]: {"state": "OPEN", "labels": []} for t in doc["tasks"] if t["issue"]}
    for k, v in issues.items():
        base[int(k[1:])] = v
    return base


def test_live_check_clean(repo_doc):
    assert P.check_live(repo_doc, _live(repo_doc), claimable_issues=[24]) == []


@pytest.mark.parametrize("issue_state", [
    {"state": "CLOSED", "labels": []},
    {"state": "OPEN", "labels": ["blocked"]},
    {"state": "OPEN", "labels": ["parked"]},
    {"state": "OPEN", "labels": ["hcl:backlog"]},
])
def test_live_check_flags_open_newcomer_task_on_closed_or_blocked_issue(repo_doc, issue_state):
    assert P.check_live(repo_doc, _live(repo_doc, i24=issue_state), claimable_issues=[])


def test_live_check_ignores_excluded_tasks_with_blocking_labels(repo_doc):
    assert P.check_live(repo_doc, _live(repo_doc, i181={"state": "OPEN", "labels": ["hcl:backlog"]}),
                        claimable_issues=[]) == []


def test_live_check_flags_claimable_label_without_file_entry(repo_doc):
    assert P.check_live(repo_doc, _live(repo_doc), claimable_issues=[99999])


def test_verifier_paths_extraction():
    assert P.verifier_paths("python -m pytest -q tests/a.py::T::t -k x tests/b.py") == ["tests/a.py", "tests/b.py"]
    assert P.verifier_paths("bun --cwd omp-extensions/z0-obspack test") == ["omp-extensions/z0-obspack"]
    assert P.verifier_paths("python -m z0int contribute feedback --check") == []
