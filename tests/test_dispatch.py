from __future__ import annotations

import json

import pytest

from z0int import dispatch as d


def capacity(**over):
    lanes = {
        "opus": {"cost_class": "subscription", "data_policy": "subscription", "slots_now": 4, "reason": "ok",
                 "pacing": {"band": "under-burn", "headroom_pct_per_h": 0.25}, "limits": {}},
        "sol": {"cost_class": "subscription", "data_policy": "subscription", "slots_now": 0,
                "reason": "burn-pace band hard-throttle", "pacing": {"band": "hard-throttle",
                                                                     "headroom_pct_per_h": 0}, "limits": {}},
        "haiku": {"cost_class": "capped", "data_policy": "subscription", "slots_now": 3, "reason": "ok",
                  "spend": {"remaining_usd": 67.0, "cap_usd": 220.0, "usd_per_job_prior": 0.04}, "limits": {}},
        "luna": {"cost_class": "capped", "data_policy": "subscription", "slots_now": 3, "reason": "ok",
                 "spend": {"remaining_usd": 67.0, "cap_usd": 220.0, "usd_per_job_prior": 0.06}, "limits": {}},
        "ds": {"cost_class": "free", "data_policy": "zero-retention", "slots_now": 2, "reason": "ok",
               "window_jobs_left": 30, "limits": {"window_jobs": {"value": 30}}},
        "groq": {"cost_class": "free", "data_policy": "unstated", "slots_now": 2, "reason": "ok",
                 "window_jobs_left": 80, "limits": {"window_jobs": {"value": 80}, "tpm": {"value": 8000}}},
    }
    for lane, patch in over.items():
        lanes[lane] = {**lanes[lane], **patch}
    return {"schema": "kerdoios.capacity.v1", "at": 1.0, "lanes": lanes}


@pytest.fixture()
def con(tmp_path, monkeypatch):
    monkeypatch.setenv("Z0INT_DISPATCH_LEDGER", str(tmp_path / "ledger.sqlite"))
    monkeypatch.setattr(d, "observe_capacity", lambda *a, **k: None)
    monkeypatch.setattr(d, "PCT_PER_JOB_PRIOR", {"opus": 0.05, "sol": 0.05})  # tests pin the cost prior
    return d.connect()


def jobs(n, **kw):
    return [d.Job.from_dict({"id": f"j{i}", "kind": "scan", **kw}) for i in range(n)]


def lanes_of(out):
    return [a["lane"] for a in out["assign"]]


def test_under_burn_subscription_fills_first_then_free_lanes_add_speed(con):
    out = d.assign(jobs(8), capacity(), con=con, now=1000.0)
    # 0.25%/h headroom * 0.8 share / 0.05% per job = 4 opus jobs, then the 2 free slots.
    assert lanes_of(out).count("opus") == 4
    assert lanes_of(out).count("ds") == 2
    # The capped Nous lane is not used for bulk scans; they wait for a free slot.
    assert [w["next_best"] for w in out["wait"]] == ["ds", "ds"]


def test_hourly_headroom_is_shared_across_calls(con):
    d.assign(jobs(4), capacity(), con=con, now=1000.0)
    out = d.assign(jobs(2), capacity(), con=con, now=1100.0)
    assert "opus" not in lanes_of(out)
    later = d.assign(jobs(1), capacity(), con=con, now=1000.0 + 3700)
    assert lanes_of(later) == ["opus"]


def test_code_never_reaches_free_lanes_and_avoided_pool(con):
    cap = capacity(opus={"slots_now": 0})
    out = d.assign(jobs(1, kind="code", quality=0.5), cap, con=con)
    assert out["assign"] == []
    rejected = out["wait"][0]["rejected"]
    assert "code" in rejected["ds"]
    assert "hard-throttle" in rejected["sol"]


def test_privacy_and_label_routing(con):
    label_public = d.Job.from_dict({"id": "p", "kind": "label", "privacy": "public", "quality": 0.5})
    label_private = d.Job.from_dict({"id": "q", "kind": "label", "privacy": "private", "quality": 0.5})
    out = d.assign([label_public, label_private], capacity(), con=con)
    by_job = {a["job_id"]: a["lane"] for a in out["assign"]}
    assert by_job["p"] == "groq"  # one-shot lane, not an agent harness
    assert by_job["q"] in ("opus", "haiku", "luna")
    assert "privacy" in out["assign"][[a["job_id"] for a in out["assign"]].index("q")]["rejected"]["ds"]


def test_qa_avoids_the_family_it_checks_and_needs_quality(con):
    qa = d.Job.from_dict({"id": "qa1", "kind": "qa", "quality": 0.8, "avoid_family": "anthropic"})
    out = d.assign([qa], capacity(), con=con)
    assert lanes_of(out) == ["luna"]
    assert "quality" in out["assign"][0]["rejected"]["ds"]


def test_outcomes_teach_quality_and_move_work_off_a_bad_lane(con):
    cap = capacity(opus={"slots_now": 0})
    first = d.assign(jobs(1, quality=0.6), cap, con=con, batch="b")
    assert lanes_of(first) == ["ds"]
    for i in range(6):
        d.record_outcome(con, {"decision_id": None, "job_id": f"x{i}", "lane": "ds", "kind": "scan",
                               "status": "ok", "schema_valid": False, "seconds": 100})
    assert d.quality_posterior(con)[("ds", "scan")][0] < 0.6
    after = d.assign(jobs(1, quality=0.6), cap, con=con, batch="b")
    assert after["assign"] == []  # ds no longer clears the bar; capped haiku is too dear for bulk
    assert after["wait"][0]["next_best"] == "opus"
    assert "quality" in after["wait"][0]["rejected"]["ds"]


def test_qa_agreement_overrides_completion_signal(con):
    out = d.assign(jobs(1), capacity(opus={"slots_now": 0}), con=con, batch="b")
    did = out["assign"][0]["decision_id"]
    d.record_outcome(con, {"decision_id": did, "status": "ok", "schema_valid": True, "seconds": 50})
    assert d.record_qa(con, "j0", 0.2, qa_lane="opus") == 1
    rep = d.report(con, "b")
    assert rep["per_lane"]["ds/scan"]["qa_agreement"] == 0.2
    assert rep["quality_posterior"]["ds/scan"]["n"] == 1


def test_nous_spend_respects_batch_budget(con, monkeypatch):
    monkeypatch.setattr(d, "NOUS_BATCH_USD", 0.1)
    qa = [d.Job.from_dict({"id": f"q{i}", "kind": "qa", "quality": 0.8, "avoid_family": "anthropic"})
          for i in range(3)]
    out = d.assign(qa, capacity(), con=con, batch="b")
    assert lanes_of(out) == ["luna"]  # $0.06 fits, the second would pass $0.10
    assert "budget" in out["wait"][0]["rejected"]["luna"]


def test_cli_assign_reads_jobs_and_capacity_files(con, tmp_path, capsys):
    (tmp_path / "cap.json").write_text(json.dumps(capacity()))
    (tmp_path / "jobs.jsonl").write_text("\n".join(json.dumps({"id": f"c{i}", "kind": "scan"}) for i in range(3)))
    assert d.main(["assign", "--jobs", str(tmp_path / "jobs.jsonl"), "--capacity", str(tmp_path / "cap.json"),
                   "--batch", "cli"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert len(out["assign"]) == 3
    assert con.execute("SELECT count(*) FROM decisions WHERE batch='cli'").fetchone()[0] == 3


def test_runner_ceiling_limits_slots_and_lanes(con):
    out = d.assign(jobs(5), capacity(), ceiling={"opus": 2, "ds": 2}, in_flight={"opus": 1}, con=con)
    assert lanes_of(out) == ["opus", "ds", "ds"]
    assert all(set(w["rejected"]) <= {"opus", "ds"} for w in out["wait"])


def test_unrun_and_throttled_decisions_do_not_spend_headroom(con):
    first = d.assign(jobs(4), capacity(), con=con, now=1000.0, ceiling={"opus": 4})
    for a in first["assign"]:
        d.record_outcome(con, {"decision_id": a["decision_id"], "status": "not_run"})
    again = d.assign(jobs(4), capacity(), con=con, now=1001.0, ceiling={"opus": 4})
    assert lanes_of(again) == ["opus"] * 4


def test_capped_cost_is_learned_from_spend_cap_snapshots(con):
    con.execute("INSERT INTO batches(batch, started, ended, nous_start, nous_end) VALUES ('q', 1, 2, ?, ?)",
                (json.dumps({"used_usd": 100.0}), json.dumps({"used_usd": 100.6})))
    for i in range(6):
        d.record_outcome(con, {"decision_id": None, "batch": "q", "job_id": f"q{i}", "lane": "luna", "kind": "qa",
                               "status": "ok", "schema_valid": True})
    assert d.usd_per_job(con) == 0.1
    qa = d.Job.from_dict({"id": "z", "kind": "qa", "quality": 0.8, "avoid_family": "anthropic"})
    out = d.assign([qa], capacity(), con=con, batch="b2")
    assert out["assign"][0]["lane"] == "luna"
    assert con.execute("SELECT usd_est FROM decisions WHERE job_id='z'").fetchone()[0] == 0.1


def test_subscription_cost_subtracts_the_pools_background_burn(con):
    start = {"lanes": {"claude": {"weekly_used_pct": 52.0}}, "recent_pct_per_h": {"claude": 1.9}}
    end = {"lanes": {"claude": {"weekly_used_pct": 57.0}}}
    con.execute("INSERT INTO batches(batch, started, ended, pace_start, pace_end) VALUES ('w', 0, 7560, ?, ?)",
                (json.dumps(start), json.dumps(end)))
    for i in range(36):
        d.record_outcome(con, {"decision_id": None, "batch": "w", "job_id": f"s{i}", "lane": "opus", "kind": "scan",
                               "status": "ok", "schema_valid": True})
    pct, how = d.pct_per_job(con, "opus")
    # 5% total minus 1.9 %/h x 2.1 h of background leaves 1.01% for 36 jobs, blended with the 0.05 prior.
    assert 0.03 < pct < 0.04
    assert "background" in how


def nim_capacity(**over):
    cap = capacity(**over)
    cap["lanes"]["nim"] = {"cost_class": "free", "data_policy": "trial-no-confidential", "slots_now": 8, "reason": "ok",
                           "window_jobs_left": 9000, "limits": {"window_jobs": {"value": 10000},
                                                                "jobs_per_min": {"value": 36}}}
    return cap


def test_one_shot_lane_takes_scans_only_with_inline_evidence(con):
    ceiling = {"nim": 8, "ds": 2}
    bare = d.assign(jobs(2, privacy="public"), nim_capacity(), con=con, now=1000.0, ceiling=ceiling)
    assert "nim" not in lanes_of(bare)
    out = d.assign(jobs(4, privacy="public", context="inline"), nim_capacity(), con=con, now=1000.0, ceiling=ceiling)
    assert lanes_of(out).count("nim") >= 2
    assert all(a["pace_s"] == round(60 / 36, 1) for a in out["assign"] if a["lane"] == "nim")


def test_inline_scans_still_respect_privacy(con):
    out = d.assign(jobs(2, privacy="internal", context="inline"), nim_capacity(), con=con, now=1000.0,
                   ceiling={"nim": 8})
    assert out["assign"] == []
    assert "privacy" in out["wait"][0]["rejected"]["nim"]


def test_quota_outcome_rests_lane_and_teaches_no_quality(con, monkeypatch):
    seen = []
    monkeypatch.setattr(d, "observe_capacity", lambda lane, status, **k: seen.append((lane, status)))
    out = d.assign(jobs(1, privacy="public", context="inline"), nim_capacity(), con=con, now=1000.0,
                   ceiling={"nim": 8})
    did = out["assign"][0]["decision_id"]
    d.record_outcome(con, {"decision_id": did, "status": "quota", "schema_valid": False})
    assert seen == [("nim", "quota")]
    assert ("nim", "scan") not in d.quality_posterior(con)
