"""CW4: state machine, receipts, prefs and local lease laws (spec §1.2, §1.6, §2.4 step 1)."""
from __future__ import annotations

import datetime as _dt
import stat
import threading

import pytest

from z0int.contribute import cli as K
from z0int.contribute import state as St

pytestmark = pytest.mark.contrib_smoke

A, B, W = "a" * 40, "b" * 40, "c" * 40
T0 = _dt.datetime(2026, 10, 26, 12, 0, 0, tzinfo=_dt.timezone.utc)


@pytest.fixture(autouse=True)
def _home(tmp_path, monkeypatch):
    monkeypatch.setenv("Z0INT_HOME", str(tmp_path / "z0int"))


def _r(state, sha, task=None, result="pass"):
    return St.make_receipt(state=state, sha=sha, command="x", exit_code=0 if result == "pass" else 1, output="o",
                           host_class="cpu", harness="claude-code", result=result, task=task, now=T0)


CHAIN = [_r("S0_bootstrap", A), _r("S1_onboarded", A), _r("S2_smoke_passed", A), _r("S3_suggested", A)]


def test_state_needs_its_receipt_on_the_exact_sha():
    assert St.derive_state(CHAIN, repo_sha=A, task=None, lease_active=False, worktree_sha=None) == "S3_suggested"
    # a receipt on another sha never counts: the checkout moved -> back to the start
    assert St.derive_state(CHAIN, repo_sha=B, task=None, lease_active=False, worktree_sha=None) == "none"


def test_chain_must_be_contiguous_and_passing():
    gap = [CHAIN[0], CHAIN[2], CHAIN[3]]
    assert St.derive_state(gap, repo_sha=A, task=None, lease_active=False, worktree_sha=None) == "S0_bootstrap"
    failed = CHAIN[:2] + [_r("S2_smoke_passed", A, result="fail"), CHAIN[3]]
    assert St.derive_state(failed, repo_sha=A, task=None, lease_active=False, worktree_sha=None) == "S1_onboarded"


def test_work_states_bind_to_worktree_head():
    rs = CHAIN + [_r("S6_evidenced", W, task="t-1"), _r("S7_pr_ready", W, task="t-1")]
    d = lambda **kw: St.derive_state(rs, repo_sha=A, task="t-1", **kw)  # noqa: E731
    assert d(lease_active=False, worktree_sha=W) == "S3_suggested"   # no lease -> no S4
    assert d(lease_active=True, worktree_sha=None) == "S4_claimed"
    assert d(lease_active=True, worktree_sha=W) == "S7_pr_ready"
    assert d(lease_active=True, worktree_sha=B) == "S5_working"      # new commit/rebase drops S6+S7
    other = CHAIN + [_r("S6_evidenced", W, task="t-2")]
    assert St.derive_state(other, repo_sha=A, task="t-1", lease_active=True, worktree_sha=W) == "S5_working"


def test_s7_requires_s6_on_same_head():
    rs = CHAIN + [_r("S7_pr_ready", W, task="t-1")]
    assert St.derive_state(rs, repo_sha=A, task="t-1", lease_active=True, worktree_sha=W) == "S5_working"


def test_invalid_receipt_is_refused():
    bad = _r("S2_smoke_passed", A)
    bad["sha"] = "not-a-sha"
    with pytest.raises(ValueError):
        St.write_receipt(bad)
    bad = _r("S2_smoke_passed", A)
    bad["host_class"] = "my-laptop.lan"
    with pytest.raises(ValueError):
        St.write_receipt(bad)


def test_private_files_are_0600_and_dirs_0700():
    p = St.write_receipt(_r("S0_bootstrap", A))
    assert stat.S_IMODE(p.stat().st_mode) == 0o600
    assert stat.S_IMODE(St.home().stat().st_mode) == 0o700
    St.load_prefs()
    assert stat.S_IMODE((St.home() / "prefs.yaml").stat().st_mode) == 0o600


def test_prefs_defaults_have_no_personal_values():
    prefs, warnings = St.load_prefs()
    assert prefs["login"] is None and prefs["interests"] == [] and warnings == []
    text = (St.home() / "prefs.yaml").read_text()
    assert "github_post: ask" in text


@pytest.mark.parametrize("key,val", [
    ("github_post", "ok"), ("gpu_use", "always"), ("llm", "grok"), ("profile_tier", "T9"),
    ("login", "someone@example.com"), ("interests", ["Not A Tag"]), ("max_first_task_hours", 0),
])
def test_prefs_reject_disallowed_values(key, val):
    prefs, warnings = St.validate_prefs({key: val})
    assert prefs[key] == St.DEFAULT_PREFS[key]
    assert warnings


def test_lease_is_exclusive_on_one_host():
    ok1, _ = St.take_lease("t-1", harness="claude-code", now=T0)
    ok2, held = St.take_lease("t-1", harness="codex", now=T0)
    assert ok1 and not ok2 and held["harness"] == "claude-code"
    # expired after 72 h -> can be taken again
    ok3, _ = St.take_lease("t-1", harness="codex", now=T0 + _dt.timedelta(hours=72))
    assert ok3


def test_lease_race_two_agents_one_winner():
    results = []
    barrier = threading.Barrier(8)

    def go(i):
        barrier.wait()
        results.append(St.take_lease("t-race", harness=f"agent-{i}", now=T0)[0])

    threads = [threading.Thread(target=go, args=(i,)) for i in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert results.count(True) == 1


def test_release_lease():
    St.take_lease("t-1", harness="x", now=T0)
    assert St.release_lease("t-1") and not St.release_lease("t-1")
    assert St.read_lease("t-1", T0) is None


@pytest.mark.parametrize("bad", ["dev", "preview", "nightly", ""])
def test_contributor_base_never_dev_preview_nightly(bad):
    with pytest.raises(ValueError):
        K.contributor_base(bad)


def test_contributor_base_is_default_branch():
    assert K.contributor_base("master") == "master"
