"""claimcheck: the F2 report's 18 cases. No network: git/gh/curl/systemctl go through a fake RUN."""
import hashlib
import importlib.util
import os
import time
from pathlib import Path

import pytest

P = Path(__file__).resolve().parents[1] / "plugins" / "owner-canon-gate" / "claimcheck.py"
spec = importlib.util.spec_from_file_location("owner_canon_claimcheck_t", P)
C = importlib.util.module_from_spec(spec)
spec.loader.exec_module(C)

JUNIT_OK = '<testsuites><testsuite errors="0" failures="0" skipped="10" tests="75"/></testsuites>'
JUNIT_ERR = '<testsuite errors="140" failures="0" skipped="0" tests="157"/>'


@pytest.fixture(autouse=True)
def fake(monkeypatch):
    """calls records every command; answers maps a command substring to (rc, stdout, stderr)."""
    C._cache.clear()
    calls, answers = [], {}

    def run(cmd, env=None):
        calls.append(" ".join(cmd))
        for key, val in answers.items():
            if key in calls[-1]:
                return val
        return 1, b"", "unexpected command"
    monkeypatch.setattr(C, "RUN", run)
    monkeypatch.setattr(C, "NOW_MONO", lambda: 1000.0)
    return calls, answers


def one(text, kind):
    got = [c for c in C.check(text) if c["type"] == kind]
    assert len(got) == 1, got
    return got[0]


def types(text):
    return sorted(c["type"] for c in C.check(text))


def test_01_bare_done_has_no_typed_claim():
    assert C.check("Done, all fixed.") == []


def test_02_missing_path_fails_and_real_path_passes(tmp_path):
    f = tmp_path / "receipt.json"
    assert one(f"Done: commit 6a7b5a8, receipt {f}", "path")["ok"] is False
    f.write_text("{}")
    C._cache.clear()
    assert one(f"Done: commit 6a7b5a8, receipt {f}", "path")["ok"] is True
    # a path whose root does not exist on this host (a route, another machine) is not a local path claim
    assert C.check("Done: see /a/b/c.json and the /v1/chat/completions route") == []
    # a path named without a done-word, or one the line says was removed, is not a claim
    assert C.check(f"Report will go to {f}x") == []
    assert C.check(f"Done. Deleted {tmp_path}/old-stage") == []


@pytest.mark.parametrize("text", [
    "local branch ayo/Z11, not pushed",                      # 3
    "PRs NOT created (branches exist)",                      # 4
    "fix 8b96606 local (push = K07)",                        # 5
    "unverified: gh unreachable, pushed 8b96606",            # 16
    "Did the suite pass, and is it pushed?",
    "Report back when tests pass and the branch is pushed.",
    "Your own log ends 1 failed, 2 passed.",
    "2/3 passed",
])
def test_negated_future_unverified_and_questions_are_not_claims(text):
    assert C.check(text) == []


def test_a_failure_in_an_earlier_clause_does_not_hide_a_pass_claim():
    assert one("Tests: RED 6 failed → GREEN 6 passed", "tests")["ok"] is None
    assert types("staged, NOT pushed; pushed 8b96606 instead") == ["pushed"]


def test_06_pushed_without_repo_has_no_locator():
    c = one("pushed 8b96606", "pushed")
    assert c["ok"] is None and "owner/repo@sha" in c["detail"] and not C.infra(c)


def test_07_08_pushed_is_dereferenced(fake):
    calls, answers = fake
    answers["commits/8b96606"] = (1, b"", "gh: No commit found for SHA: 8b96606 (HTTP 422)")
    assert one("pushed kvnloo/x@8b96606 to main", "pushed")["ok"] is False
    C._cache.clear()
    answers["commits/8b96606"] = (0, b"8b96606aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa\n", "")
    assert one("pushed kvnloo/x@8b96606 to main", "pushed")["ok"] is True
    # local repo: a remote-tracking branch must contain the commit
    answers["branch -r --contains"] = (0, b"", "")
    assert one("pushed /srv/repo@8b96606", "pushed")["ok"] is False
    C._cache.clear()
    answers["branch -r --contains"] = (0, b"  origin/fix/x\n", "")
    assert one("pushed /srv/repo@8b96606", "pushed")["ok"] is True
    assert one("merged /srv/repo@8b96606", "merged")["ok"] is False  # on a branch, not on main
    answers["branch -r --contains"] = (129, b"", "error: malformed object name")
    C._cache.clear()
    assert one("pushed /srv/repo@8b96606", "pushed")["ok"] is False


def test_09_pr_state_and_head(fake):
    _, answers = fake
    text = "PR opened https://github.com/kvnloo/noop/pull/117 at 2a1de7609"
    answers["pulls/117"] = (0, b"open false 2a1de7609aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa\n", "")
    assert one(text, "pr")["ok"] is True
    C._cache.clear()
    answers["pulls/117"] = (0, b"closed false 2a1de7609aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa\n", "")
    assert one(text, "pr")["ok"] is False
    assert one("merged https://github.com/kvnloo/noop/pull/117", "merged")["ok"] is False
    C._cache.clear()
    answers["pulls/117"] = (0, b"open false ffffffffffffffffffffffffffffffffffffffff\n", "")
    assert "head is" in one(text, "pr")["detail"]
    assert one("PR opened for the fix", "pr")["ok"] is None


def test_10_11_12_13_tests_need_a_fresh_result_file_and_a_count(tmp_path):
    ok, err = tmp_path / "junit.xml", tmp_path / "collect140-junit.xml"
    ok.write_text(JUNIT_OK)
    err.write_text(JUNIT_ERR)
    c = one(f"suite passed {ok}", "tests")                               # 10
    assert c["ok"] is False and "no count claimed" in c["detail"]
    assert one(f"65 passed, junit {ok}", "tests")["ok"] is True          # 11, fresh
    assert one(f"75 passed, junit {ok}", "tests")["ok"] is False         # the 10 skipped are not passes
    old = time.time() - 25 * 3600
    os.utime(ok, (old, old))
    C._cache.clear()
    assert "older than 24 h" in one(f"65 passed, junit {ok}", "tests")["detail"]   # 11, stale
    assert "140 failures/errors" in one(f"tests green {err}", "tests")["detail"]   # 12
    relay = one("45 tests pass (reported)", "tests")                     # 13
    assert relay["ok"] is None and not C.infra(relay)
    log = tmp_path / "e2e.log"
    log.write_text("....\n=== 1 failed, 2 passed in 3.1s ===\n")
    assert one(f"3/3 passed, log {log}", "tests")["ok"] is False
    log.write_text("....\n=== 3 passed in 3.1s ===\n")
    C._cache.clear()
    assert one(f"3/3 passed, log {log}", "tests")["ok"] is True


def test_tests_claim_on_ci(fake):
    _, answers = fake
    answers["check-runs"] = (0, b"success success failure\n", "")
    assert one("tests pass on kvnloo/x@8b96606", "tests")["ok"] is False
    C._cache.clear()
    answers["check-runs"] = (0, b"success skipped\n", "")
    assert one("tests pass on kvnloo/x@8b96606", "tests")["ok"] is True


def test_14_deployed_hash_must_match(fake):
    _, answers = fake
    body = b"console.log(1)"
    answers["curl"] = (0, body, "")
    assert one("deployed, live at https://host:18799/acp.js sha256 de582a41", "deployed")["ok"] is False
    good = hashlib.sha256(body).hexdigest()[:12]
    assert one(f"deployed, live at https://host:18799/acp.js sha256 {good}", "deployed")["ok"] is True
    answers["curl"] = (22, b"", "404")
    C._cache.clear()
    assert one("deployed to https://host:18799/acp.js", "deployed")["ok"] is False
    assert one("deployed", "deployed")["ok"] is None
    answers["curl"] = (28, b"", "timed out")
    C._cache.clear()
    assert C.infra(one("deployed to https://host:18799/acp.js", "deployed"))  # a timeout flags, it does not block


def show(**kv):
    base = {"LoadState": "loaded", "ActiveState": "active", "SubState": "running", "Result": "success",
            "ExecMainStatus": "0", "Type": "simple", "ActiveEnterTimestampMonotonic": "100000000"}
    return 0, "\n".join(f"{k}={v}" for k, v in (base | kv).items()).encode(), ""


def test_15_service_must_be_up_and_stay_up(fake):
    _, answers = fake
    answers["show agentsview.service"] = show(ActiveEnterTimestampMonotonic=str(995 * 10**6))   # up 5 s: crash loop
    c = one("agentsview restarted and healthy", "service")
    assert c["ok"] is False and "active only 5 s" in c["detail"]
    C._cache.clear()
    answers["show agentsview.service"] = show()                                                   # up 900 s
    assert one("agentsview restarted and healthy", "service")["ok"] is True
    answers["show x.service"] = show(ActiveState="failed", SubState="failed", Result="exit-code")
    assert one("x.service is active", "service")["ok"] is False
    answers["show job.service"] = show(Type="oneshot", ActiveState="inactive", SubState="dead")
    assert one("job.service is healthy", "service")["ok"] is True
    # a word that is not a unit is not a service claim; a named unit that does not exist is a false one
    assert C.check("Kueue is healthy again") == []
    assert one("nope.service is healthy", "service")["ok"] is False


def test_18_infra_failure_is_unverified_not_false(fake):
    _, answers = fake
    answers["gh api"] = (None, b"", "TimeoutExpired")
    c = one("pushed kvnloo/x@8b96606", "pushed")
    assert c["ok"] is None and C.infra(c)


def test_cache_and_dedupe(fake):
    calls, answers = fake
    answers["commits/8b96606"] = (0, b"8b96606a\n", "")
    text = "pushed kvnloo/x@8b96606\nagain: pushed kvnloo/x@8b96606"
    assert types(text) == ["pushed"]
    C.check(text)
    assert len(calls) == 1


@pytest.mark.parametrize("text", [
    # shapes that blocked real lane messages in a 7-day replay and are not claims of this type
    "Tier 1: 69 refs merged elsewhere, not checked out.",
    "Another session was editing it, and I merged into its version (backup kept).",
    "Chrome task #585 landed on top of the S22 screen.",
    "My claim that they were pushed as PRs was false.",
    "10 comments, 0 PRs opened, 4 closed.",
    "Done. Other owners' MBP data: /home/nobody-here/.cache/export 20 GiB.",
    "Done with the audit.\nDeploy plan: copy /tmp/does-not-exist-l3 to /mnt/does-not-exist-l3/x",
    "shadow is running in parallel",
])
def test_replayed_false_positives_are_not_claims(text, fake):
    fake[1]["systemctl --user show shadow.service"] = (0, b"LoadState=not-found\n", "")
    assert C.check(text) == []


def test_short_pr_reference_is_a_locator(fake):
    calls, answers = fake
    answers["pulls/229"] = (0, b"closed true 67701f5aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa\n", "")
    c = one("The change is kvnloo/glide#229 (67701f5), merged into preview", "merged")
    assert c["ok"] is True and c["locator"] == "https://github.com/kvnloo/glide/pull/229"
    answers["pulls/155"] = (0, b"open false 496a8ca8aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa\n", "")
    assert one("kvnloo/.files #155 merged as 496a8ca8", "merged")["detail"] == "PR #155 is open, not merged"


# F24: false-positive classes seen in the first 2 h of live traffic (2026-11-11), reduced to their shape.
def test_f24_hex_elsewhere_in_the_paragraph_is_not_the_pr_head(fake):
    fake[1]["pulls/233"] = (0, b"open false d6a4aaf06aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa\n", "")
    c = one("Ran it in session local-3116e038… and it returned 3 hits. kvnloo/glide#233 has the fix and is "
            "still open; its branch went into the union branch as commit eb09c59.", "pr")
    assert c["ok"] is True, c
    C._cache.clear()  # a sha written right after the PR reference is still its head
    assert "head is" in one("The fix is kvnloo/glide#233 at head 0123abc.", "pr")["detail"]


def test_f24_merged_into_a_side_branch_is_not_pr_merged(fake):
    fake[1]["pulls/233"] = (0, b"open false d6a4aaf06aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa\n", "")
    c = one("PR kvnloo/glide#233 has the screenshot and is merged into preview (eb09c59).", "merged")
    assert c["ok"] is True, c
    assert C.check("The fixes are merged to `kvnloo/.files` dev and active in the release.") == []
    C._cache.clear()
    assert one("kvnloo/glide#233 is merged into main", "merged")["detail"] == "PR #233 is open, not merged"


def test_f24_merged_pr_cited_by_its_squash_sha(fake):
    fake[1]["pulls/232"] = (0, b"closed true fbd8d6dceaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa\n", "")
    assert one("kvnloo/glide#232 (0a1b2c3) is merged", "merged")["ok"] is True


@pytest.mark.parametrize("text", [
    "The cause: the PR never found that deployed reader, so recall fell back.",   # adjective, not a deploy claim
    "The deployed stdlib reader is at the usual place.",
    "The bug was already fixed on main by upstream #4643.",                       # a location, not a merge claim
    "It fails on main with the source reverted (#88).",
])
def test_f24_adjectives_and_locations_are_not_claims(text):
    assert C.check(text) == []


def test_f24_deploy_and_on_main_claims_still_fire():
    assert one("I fixed and deployed four bugs", "deployed")["ok"] is None
    assert one("the fix is on main (#12)", "merged")["ok"] is None


def test_f24_pushed_header_takes_the_locators_listed_below(fake):
    calls, answers = fake
    answers["commits/b321765"] = (0, b"b321765aaa\n", "")
    answers["commits/82931c5"] = (1, b"", "gh: No commit found (HTTP 422)")
    got = C.check("**New this run**, both tested and pushed to the fork:\n"
                  "- one thing. Commit: `kvnloo/hermes-agent@b321765`.\n"
                  "- another. Commit: `kvnloo/hermes-agent@82931c5`.\n\nNot sent yet: 3742f51.")
    assert [(c["type"], c["ok"]) for c in got] == [("pushed", True), ("pushed", False)], got


def test_f24_a_named_commit_is_checked_whatever_the_verb(fake):
    calls, answers = fake
    answers["commits/b321765"] = (1, b"", "gh: No commit found (HTTP 422)")
    assert one("Two slices are on the fork: kvnloo/hermes-agent@b321765 (13F→70P)", "pushed")["ok"] is False
    assert C.check("kvnloo/hermes-agent@b321765 is committed locally, not pushed") == []
    assert C.check("unverified: kvnloo/hermes-agent@b321765 is on the fork") == []
    assert [c["type"] for c in C.check("the worktree commit /srv/repo@b321765 stays local")] == []
