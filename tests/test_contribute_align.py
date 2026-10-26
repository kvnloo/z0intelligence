"""CW3: deterministic alignment engine (spec §3.3, §3.6) — E1 twin, oracles, fallbacks.

The twin (``_ref_reject``) is an independent re-statement of the hard filters. The
enumeration covers hardware class x tier x difficulty x done-count x filter flags and
proves: no ``newcomer:false``, owner-approval or hardware-unfit task is ever returned,
the survivor set equals the twin's, and the order is stable under input permutation.
"""
from __future__ import annotations

import copy
import itertools
import json
import random
from pathlib import Path

import pytest

from z0int.contribute import align as A
from z0int.contribute import priorities as P
from z0int.contribute import schemas as S

ROOT = Path(__file__).resolve().parents[1]
PROFILES = ROOT / "tests" / "fixtures" / "contrib_profiles"
FRONTEND_ONLY = {"typescript", "javascript", "frontend", "ui", "plugin-hooks"}


@pytest.fixture(scope="module")
def doc():
    return P.load_priorities(ROOT)


@pytest.fixture(scope="module")
def vocab():
    return P.load_vocab(ROOT)


def _profile(gpu_class="none", vram=0, ram=16, toolchains=("python",), os_="linux", arch="x86_64", network=True):
    return {
        "schema": "z0.contribute.profile.v1", "tier": "T0",
        "hw": {"os": os_, "arch": arch, "ram_gib": ram, "disk_free_gib": 500,
               "gpu": {"class": gpu_class, "vram_gib": vram}},
        "network": network, "toolchains": list(toolchains), "harnesses": [],
    }


# ---------------------------------------------------------------- oracles (§4.2)

def _load_case(name):
    return json.loads((PROFILES / f"{name}.json").read_text(encoding="utf-8"))


def _forbidden(task, oracle):
    f = oracle.get("forbidden", {})
    if f.get("newcomer_false") and not task["newcomer"]:
        return True
    if "gpu_vram_gib_gt" in f and task["hardware"]["gpu_vram_gib"] > f["gpu_vram_gib_gt"]:
        return True
    if f.get("frontend_only") and set(task["skills"]) <= FRONTEND_ONLY:
        return True
    return task["issue"] in set(oracle.get("forbidden_issues", []))


@pytest.mark.contrib_smoke
@pytest.mark.parametrize("name", ["frontend", "ml-research", "systems-rust"])
def test_oracle_profiles(doc, vocab, name):
    case = _load_case(name)
    out = A.align(doc, vocab, case["profile"], case["prefs"], live_claims={}, issue_states={})
    by_id = {t["id"]: t for t in doc["tasks"]}
    oracle = case["oracle"]
    pick = by_id[out["pick"]["task"]]
    if "allowed_picks" in oracle:
        assert pick["id"] in oracle["allowed_picks"], out["pick"]
    else:
        assert set(pick["skills"]) & set(oracle["allowed_pick_skills_any"]), out["pick"]
    for card in [out["pick"], *out["alternatives"]]:
        assert not _forbidden(by_id[card["task"]], oracle), card
    for code in oracle["reasons"]:
        assert code in out["pick"]["reasons"], (code, out["pick"])
    assert len(out["alternatives"]) == 2


@pytest.mark.contrib_smoke
@pytest.mark.parametrize("name", ["frontend", "ml-research", "systems-rust"])
def test_oracle_profiles_at_t1_with_github_unreadable(doc, vocab, name):
    """The clean-container agent run: `--profile minimal` gives T1 (no language
    histogram, no recency) and gh is logged out (claims and issue states unknown).
    The oracle must still hold. The ml-research cell picked t-139: 15.5 GiB failed
    t-231's ram_gib 16, and a CUDA host did not map to ml-eval/benchmarking."""
    case = _load_case(name)
    prof = {k: v for k, v in case["profile"].items() if k not in ("languages", "recency", "interests")}
    prof["tier"] = "T1"
    prof["hw"] = {**prof["hw"], "ram_gib": 15.5}   # what the probe reads on a "16 GB" machine
    out = A.align(doc, vocab, prof, case["prefs"], live_claims=None, issue_states=None,
                  onboard_mode="repo_only")
    by_id = {t["id"]: t for t in doc["tasks"]}
    oracle = case["oracle"]
    pick = by_id[out["pick"]["task"]]
    if "allowed_picks" in oracle:
        assert pick["id"] in oracle["allowed_picks"], out["pick"]
    else:
        assert set(pick["skills"]) & set(oracle["allowed_pick_skills_any"]), out["pick"]
    for card in [out["pick"], *out["alternatives"]]:
        assert not _forbidden(by_id[card["task"]], oracle), card
    for code in oracle["reasons"]:
        assert code in out["pick"]["reasons"], (code, out["pick"])


def test_probed_ram_meets_the_nominal_size(doc, vocab):
    t = next(t for t in doc["tasks"] if t["id"] == "t-231-local-inference-profile")
    assert t["hardware"]["ram_gib"] == 16
    ok = _profile(gpu_class="nvidia", vram=12, ram=15.5, toolchains=("python", "cuda"))
    low = _profile(gpu_class="nvidia", vram=12, ram=12, toolchains=("python", "cuda"))
    base = dict(live_claims={}, issue_states={})
    assert t["id"] in {r["task"] for r in A.align({**doc, "tasks": [t]}, vocab, ok, **base)["ranked"]}
    assert A.align({**doc, "tasks": [t]}, vocab, low, **base)["excluded_counts"] == {"hardware": 1}


@pytest.mark.parametrize("name", ["frontend", "ml-research", "systems-rust"])
def test_oracle_profiles_have_an_allowed_newcomer_task(doc, vocab, name):  # P2
    case = _load_case(name)
    ranked = A.align(doc, vocab, case["profile"], case["prefs"], live_claims={}, issue_states={})["ranked"]
    assert any(not _forbidden({**t, **next(x for x in doc["tasks"] if x["id"] == t["task"])}, case["oracle"])
               for t in ranked)


# ---------------------------------------------------------------- E1 twin enumeration

def _ref_reject(task, prof, prefs, done, onboard_mode, ids_done):
    """Independent re-statement of the hard filters (twin)."""
    hw = task["hardware"]
    if not task["newcomer"] or task["needs_owner_approval"] or task["status"] != "open":
        return True
    gate = task["gate"] or {}
    if "task" in gate and gate["task"] not in ids_done:
        return True
    if "issue" in gate or "condition" in gate:
        return True  # issue_states={} -> gate state unknown -> not satisfied
    if hw["gpu_vram_gib"] > 0 and (prof["hw"]["gpu"]["vram_gib"] < hw["gpu_vram_gib"] or prefs.get("gpu_use") == "never"):
        return True
    if prof["hw"]["ram_gib"] < hw["ram_gib"] or prof["hw"]["os"] not in hw["os"] or prof["hw"]["arch"] not in hw["arch"]:
        return True
    if task["runtime"] == "z0" and onboard_mode == "repo_only":
        return True
    if task["network"] == "hf_download" and (prof["network"] is False or prefs.get("hf_download") == "never"):
        return True
    if done == 0 and (task["difficulty"] == "L" or task["expected_hours"] > prefs.get("max_first_task_hours", 4)):
        return True
    return False


def _synthetic_doc():
    tasks = []
    ranks = {"P0": 0, "P1": 0, "P2": 0, "P3": 0}
    combos = itertools.product(
        ["P0", "P1", "P2", "P3"], ["S", "M", "L"], [0, 8, 12], [True, False], [False, True],
        ["open", "parked"], ["repo_only", "z0"], ["none", "hf_download"],
    )
    for i, (tier, diff, vram, newcomer, approval, status, runtime, network) in enumerate(combos):
        ranks[tier] += 1
        tasks.append({
            "id": f"t-{i}", "issue": 1000 + i, "title": "t", "kind": ["issue", "experiment", "docs"][i % 3],
            "tier": tier, "rank": ranks[tier], "status": status, "gate": None,
            "newcomer": newcomer and not approval, "needs_owner_approval": approval,
            "claim": "parallel(2)" if vram else "exclusive", "difficulty": diff,
            "expected_hours": {"S": 2, "M": 4, "L": 12}[diff], "skills": ["python", "ml-eval"] if vram else ["python"],
            "touches": [], "hardware": {"gpu_vram_gib": vram, "ram_gib": 8, "disk_gib": 1,
                                        "os": ["linux"], "arch": ["x86_64"]},
            "runtime": runtime, "network": network, "risk_class": "low", "smoke": None,
            "verifier": "true", "exit_criterion": "x", "impact_note": "x",
        })
    tasks.append({**tasks[0], "id": P.FEEDBACK_TASK, "issue": None, "tier": "P3", "rank": ranks["P3"] + 1,
                  "status": "open", "newcomer": True, "needs_owner_approval": False, "claim": "none",
                  "difficulty": "S", "expected_hours": 1, "runtime": "repo_only", "network": "none",
                  "skills": ["docs"], "hardware": {"gpu_vram_gib": 0, "ram_gib": 0, "disk_gib": 0,
                                                   "os": ["linux", "macos"], "arch": ["x86_64", "arm64"]}})
    return {"schema": "z0.priorities.v1", "updated": "2026-10-26", "north_star": "n",
            "alignment": {"weights": {"impact": .3, "fit": .3, "interest": .15, "ramp": .15, "uniq_hw": .1},
                          "setup_penalty_per_missing": .08, "tie_epsilon": .05},
            "tasks": tasks}


HW = {
    "cpu": _profile(),
    "nvidia-8g": _profile("nvidia", 8, 32, ("python", "cuda")),
    "nvidia-12g": _profile("nvidia", 12, 32, ("python", "cuda")),
    "nvidia-24g-offline": _profile("nvidia", 24, 64, ("python", "cuda"), network=False),
}


def test_e1_twin_enumeration(vocab):
    doc = _synthetic_doc()
    assert S.validate(doc, "priorities") == []
    by_id = {t["id"]: t for t in doc["tasks"]}
    n = 0
    for (hw_name, prof), done, mode, gpu_use, hf in itertools.product(
        HW.items(), [0, 1, 3], ["full", "repo_only"], ["ask", "never"], ["ask", "never"],
    ):
        prefs = {"gpu_use": gpu_use, "hf_download": hf}
        out = A.align(doc, vocab, prof, prefs, done_count=done, onboard_mode=mode,
                      live_claims={}, issue_states={})
        survivors = {r["task"] for r in out["ranked"]}
        expected = {t["id"] for t in doc["tasks"] if not _ref_reject(t, prof, prefs, done, mode, set())}
        assert survivors == expected, (hw_name, done, mode, gpu_use, hf)
        for card in [out["pick"], *out["alternatives"]]:
            t = by_id[card["task"]]
            assert t["newcomer"] and not t["needs_owner_approval"], card
            assert t["hardware"]["gpu_vram_gib"] <= prof["hw"]["gpu"]["vram_gib"], card
            if gpu_use == "never":
                assert t["hardware"]["gpu_vram_gib"] == 0, card
        assert sum(out["excluded_counts"].values()) == len(doc["tasks"]) - len(survivors)
        n += 1
    assert n == 4 * 3 * 2 * 2 * 2


def test_e1_order_is_stable_under_permutation_and_repetition(vocab):
    doc = _synthetic_doc()
    prof = HW["nvidia-12g"]
    base = A.align(doc, vocab, prof, {}, done_count=1, live_claims={}, issue_states={})
    rng = random.Random(7)
    for _ in range(5):
        shuffled = copy.deepcopy(doc)
        rng.shuffle(shuffled["tasks"])
        again = A.align(shuffled, vocab, prof, {}, done_count=1, live_claims={}, issue_states={})
        assert again == base
    scores = [(-r["score"], r["tier"], r["rank"], r["task"]) for r in base["ranked"]]
    assert scores == sorted(scores)


# ---------------------------------------------------------------- score terms (§3.3)

def _one_task_doc(doc, **over):
    d = copy.deepcopy(doc)
    t = next(x for x in d["tasks"] if x["id"] == "t-24-rlcd-alignbench")
    t.update(over)
    return d, t


def test_impact_term():
    assert A.impact("P0", 1, 5) == 1.0
    assert A.impact("P1", 1, 7) == 0.75
    assert A.impact("P2", 3, 6) == pytest.approx(0.5 * (1 - 2 / 6))
    assert A.impact("P3", 1, 4) == 0.3


@pytest.mark.parametrize("done,diff,want", [
    (0, "S", 1.0), (0, "M", 0.6), (1, "S", 0.6), (1, "M", 1.0), (1, "L", 0.4),
    (3, "M", 0.8), (3, "L", 1.0), (5, "L", 1.0),
])
def test_ramp_table(done, diff, want):
    assert A.ramp(diff, done) == want


def test_fit_uses_toolchain_languages_and_o2_affinity(vocab):
    have = A.build_have(_profile(toolchains=("python", "cargo")), vocab, {})
    assert {"python", "rust", "cargo", "systems", "latency"} <= have
    assert "frontend" not in have
    t2 = A.build_have({**_profile(), "languages": {"python": 0.95, "go": 0.05}}, vocab, {})
    assert "python" in t2 and "go" not in t2  # T2 share < 0.10 does not count


def test_missing_installable_toolchain_is_penalized_not_removed(doc, vocab):
    d, _ = _one_task_doc(doc, toolchains=["python", "cargo"])
    out = A.align(d, vocab, _profile(), {}, live_claims={}, issue_states={})
    row = next(r for r in out["ranked"] if r["task"] == "t-24-rlcd-alignbench")
    assert "missing_toolchain:cargo" in row["reasons"]
    assert row["terms"]["penalty"] == pytest.approx(0.08)


def test_missing_non_installable_toolchain_removes_task(doc, vocab):
    d, _ = _one_task_doc(doc, toolchains=["python", "cuda"])
    out = A.align(d, vocab, _profile(), {}, live_claims={}, issue_states={})
    assert "t-24-rlcd-alignbench" not in {r["task"] for r in out["ranked"]}
    assert out["excluded_counts"].get("toolchain", 0) >= 1


def test_uniq_hw_only_without_a_receipt_for_this_class(doc, vocab):
    prof = _load_case("ml-research")["profile"]
    fresh = A.align(doc, vocab, prof, {}, live_claims={}, issue_states={})
    seen = A.align(doc, vocab, prof, {}, live_claims={}, issue_states={},
                   receipts_index=[{"task": "t-231-local-inference-profile", "host_class": "nvidia-12g"}])
    row = lambda o: next(r for r in o["ranked"] if r["task"] == "t-231-local-inference-profile")  # noqa: E731
    assert row(fresh)["terms"]["uniq_hw"] == 1 and "hw_unique:nvidia-12g" in row(fresh)["reasons"]
    assert row(seen)["terms"]["uniq_hw"] == 0


def test_claimed_to_capacity_is_removed(doc, vocab):
    prof = _load_case("frontend")["profile"]
    out = A.align(doc, vocab, prof, {}, live_claims={"t-139-e2e-trace": 1}, issue_states={})
    assert "t-139-e2e-trace" not in {r["task"] for r in out["ranked"]}
    assert out["excluded_counts"]["claimed"] == 1
    par = A.align(doc, vocab, _load_case("ml-research")["profile"], {},
                  live_claims={"t-231-local-inference-profile": 5}, issue_states={})
    assert "t-231-local-inference-profile" in {r["task"] for r in par["ranked"]}  # parallel(6): 5 < 6


def test_gate_on_issue_opens_when_issue_closes(doc, vocab):
    prof = _load_case("ml-research")["profile"]
    closed = A.align(doc, vocab, prof, {}, live_claims={}, issue_states={24: "CLOSED"})
    opened = A.align(doc, vocab, prof, {}, live_claims={}, issue_states={24: "OPEN"})
    assert "t-25-alignbench-bakeoff" in {r["task"] for r in closed["ranked"]}
    assert "t-25-alignbench-bakeoff" not in {r["task"] for r in opened["ranked"]}


def test_avoid_and_interest_prefs(doc, vocab):
    prof = _load_case("frontend")["profile"]
    out = A.align(doc, vocab, prof, {"avoid": ["t-139-e2e-trace", "ml-eval"], "interests": ["retrieval"]},
                  live_claims={}, issue_states={})
    ids = {r["task"] for r in out["ranked"]}
    assert "t-139-e2e-trace" not in ids and "t-24-rlcd-alignbench" not in ids
    row = next(r for r in out["ranked"] if r["task"] == "t-137-reuse-gate")
    assert "interest:retrieval" in row["reasons"] and row["terms"]["interest"] == 1


# ---------------------------------------------------------------- picking + reasons

def test_alt1_prefers_a_different_kind_within_quarter(doc, vocab):
    out = A.align(doc, vocab, _load_case("ml-research")["profile"], {}, live_claims={}, issue_states={})
    ranked = out["ranked"]
    top = ranked[0]
    diff = [r for r in ranked[1:] if r["kind"] != top["kind"] and top["score"] - r["score"] <= 0.25]
    want = diff[0]["task"] if diff else ranked[1]["task"]
    assert out["alternatives"][0]["task"] == want


def test_reasons_are_closed_and_agree_with_facts(doc, vocab):
    for name in ("frontend", "ml-research", "systems-rust"):
        case = _load_case(name)
        out = A.align(doc, vocab, case["profile"], case["prefs"], live_claims={}, issue_states={})
        have = A.build_have(case["profile"], vocab, case["prefs"])
        for card in [out["pick"], *out["alternatives"]]:
            assert A.reasons_consistent(card["reasons"], have=have, host_class=A.host_class(case["profile"]),
                                        vocab=vocab), card
    assert not A.reasons_consistent(["skill_match:rust"], have={"python"}, host_class="cpu", vocab=vocab)
    assert not A.reasons_consistent(["vibes:great"], have={"python"}, host_class="cpu", vocab=vocab)
    assert not A.reasons_consistent(["hw_unique:nvidia-24g"], have=set(), host_class="cpu", vocab=vocab)


def test_suggestion_document_validates(doc, vocab):
    out = A.align(doc, vocab, _load_case("frontend")["profile"], {}, live_claims={}, issue_states={})
    sug = A.to_suggestion(out, sha="a" * 40, priorities_sha256="b" * 64, profile_tier="T1")
    assert S.validate(sug, "suggestion") == []
    assert sug["engine"]["llm"] == "none"


# ---------------------------------------------------------------- fallbacks (§3.6)

def test_fallback_no_survivor_gives_feedback_task(doc, vocab):
    d = copy.deepcopy(doc)
    for t in d["tasks"]:
        if t["id"] != P.FEEDBACK_TASK:
            t["status"] = "parked"
    out = A.align(d, vocab, _profile(), {}, live_claims={}, issue_states={})
    assert out["pick"]["task"] == P.FEEDBACK_TASK
    assert out["alternatives"] == []


def test_fallback_everything_filtered_including_feedback(doc, vocab):
    out = A.align(doc, vocab, _profile(), {"avoid": [P.FEEDBACK_TASK, "python", "typescript"]},
                  live_claims={}, issue_states={})
    assert out["pick"]["task"] == P.FEEDBACK_TASK
    assert out["pick"]["reasons"] == ["fallback:feedback"]


def test_fewer_than_three_fill_with_feedback(doc, vocab):
    d = copy.deepcopy(doc)
    keep = {"t-24-rlcd-alignbench", P.FEEDBACK_TASK}
    for t in d["tasks"]:
        if t["id"] not in keep:
            t["status"] = "parked"
    out = A.align(d, vocab, _profile(), {}, live_claims={}, issue_states={})
    ids = [out["pick"]["task"], *[a["task"] for a in out["alternatives"]]]
    assert ids == ["t-24-rlcd-alignbench", P.FEEDBACK_TASK]


def test_fallback_labels():
    issues = [
        {"number": 7, "title": "b", "labels": ["claimable"]},
        {"number": 5, "title": "a", "labels": ["good first issue"]},
        {"number": 9, "title": "c", "labels": ["good first issue", "blocked"]},
    ]
    out = A.fallback_from_labels(issues)
    assert out["source"] == "fallback:labels"
    tasks = [out["pick"]["task"], *[a["task"] for a in out["alternatives"]]]
    assert tasks[:2] == ["gh-5", "gh-7"] and "gh-9" not in tasks
    assert all(c["reasons"] == ["fallback:labels"] for c in [out["pick"], *out["alternatives"]] if c["task"] != P.FEEDBACK_TASK)
    sug = A.to_suggestion(out, sha=None, priorities_sha256=None, profile_tier="T0")
    assert S.validate(sug, "suggestion") == []


def test_claim_state_unknown_warning(doc, vocab):
    out = A.align(doc, vocab, _profile(), {}, live_claims=None, issue_states=None)
    assert "claim state unknown" in out["warnings"]


def test_engine_has_no_llm_and_refuses_grok_and_cursor():
    for bad in ("grok", "xai:grok-4", "cursor", "Cursor-small"):
        with pytest.raises(ValueError):
            A.check_llm_choice(bad)
    assert A.check_llm_choice("none") == "none"
