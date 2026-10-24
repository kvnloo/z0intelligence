"""Deterministic compiler and policy publication regressions (synthetic only)."""
from copy import deepcopy
from datetime import datetime, timezone
import json

import pytest

from z0int.policy.changelog import markdown_diff, policy_diff
from z0int.policy.compile import (
    CompilerConfig, compile_policy, resolve_family, rule_key, scope_of, validate_policy, write_policy,
)
from z0int.policy.findings import Finding, canonical, digest

NOW = "2026-10-24T12:00:00Z"
CONFIG = CompilerConfig(compiler_revision="a" * 40)


def finding(kind="measured", *, low=.98, n=200, ts="2026-10-24T10:00:00Z", arm=None,
            metric=None, expires_at="2026-11-07T10:00:00Z", source=None):
    values = {"name": "acc_at_conf", "value": max(low, .99), "n": n, "ci_low": low,
              "ci_high": 1., "ci_method": "wilson95", "higher_is_better": True}
    values.update(metric or {})
    return Finding(ts=ts, source=source or {"kind": "manual", "path": "/synthetic/results.json",
        "sha256": "b" * 64, "run_id": "test"}, subject={"family": "omp.tool", "job": "next-action"},
        arm=arm or {"kind": "slm", "model": "synthetic@rev"}, metric=values,
        evidence_class=kind, scope_note="Only synthetic next-action tasks and this exact arm.",
        expires_at=expires_at).to_dict()


def stage(policy):
    return next(iter(policy["families"].values()))["stage"]


def compile_rows(rows, previous=None, now=NOW, config=CONFIG):
    return compile_policy(rows, previous, now, config)


def all_evidence():
    return [finding(metric={"live_shadow": True}),
            finding("replayed_L1", ts="2026-10-24T10:01:00Z", metric={"divergence": .01, "aa_noise_floor": .01}),
            finding("canary", ts="2026-10-24T10:02:00Z", metric={"name": "task_success", "divergence": 0.,
                "aa_noise_floor": .01, "outcome_ledger_verified": True})]


def test_byte_identical_inputs_and_content_address():
    rows = all_evidence()
    snapshot = deepcopy(rows)
    first, second = compile_rows(rows), compile_rows(rows)
    assert canonical(first) == canonical(second)
    assert rows == snapshot
    assert first["version"] == digest({k: v for k, v in first.items() if k != "version"})
    validate_policy(first)


def test_exactly_one_promotion_step_per_compile():
    rows = all_evidence()
    shadow = compile_rows(rows)
    canary = compile_rows(rows, shadow)
    live = compile_rows(rows, canary)
    assert [stage(p) for p in (shadow, canary, live)] == ["shadow", "canary", "live"]
    assert stage(compile_rows(rows, live)) == "live"


@pytest.mark.parametrize("kind", ["inference", "published", "replayed_L1", "replayed_L2", "canary"])
def test_initial_shadow_requires_measured(kind):
    assert stage(compile_rows([finding(kind)])) == "none"


@pytest.mark.parametrize("low,n,expected", [(.8, 30, "shadow"), (.799, 30, "none"), (.9, 29, "none")])
def test_shadow_boundaries(low, n, expected):
    assert stage(compile_rows([finding(low=low, n=n)])) == expected


@pytest.mark.parametrize("shadow_metric,replay_metric,expected", [
    ({"live_shadow": True}, {"divergence": .01, "aa_noise_floor": .01}, "canary"),
    ({}, {"divergence": 0., "aa_noise_floor": .01}, "shadow"),
    ({"live_shadow": True}, {"divergence": .02, "aa_noise_floor": .01}, "shadow"),
    ({"live_shadow": True}, {}, "shadow"),
])
def test_shadow_canary_requires_live_labels_and_aa_replay(shadow_metric, replay_metric, expected):
    measured = finding(metric=shadow_metric)
    shadow = compile_rows([measured])
    replay = finding("replayed_L1", metric=replay_metric)
    assert stage(compile_rows([measured, replay], shadow)) == expected


@pytest.mark.parametrize("kind,verified,n,expected", [
    ("canary", True, 50, "live"), ("replayed_L2", True, 50, "live"),
    ("canary", False, 50, "canary"), ("canary", True, 49, "canary"),
    ("measured", True, 50, "canary"),
])
def test_canary_live_requires_verified_outcome(kind, verified, n, expected):
    rows = all_evidence()[:2]
    canary = compile_rows(rows, compile_rows(rows))
    outcome = finding(kind, n=n, ts="2026-10-24T11:00:00Z", metric={"name": "task_success",
        "outcome_ledger_verified": verified, "divergence": 0., "aa_noise_floor": .01})
    assert stage(compile_rows(rows + [outcome], canary)) == expected


@pytest.mark.parametrize("before,low,expected", [
    ("shadow", .7, "none"), ("canary", .7, "none"), ("live", .7, "none"),
    ("canary", .9, "shadow"), ("live", .9, "shadow"),
])
def test_immediate_ci_demotions(before, low, expected):
    rows = all_evidence()
    previous = compile_rows(rows)
    while stage(previous) != before:
        previous = compile_rows(rows, previous)
    rows.append(finding(low=low, ts="2026-10-24T11:00:00Z"))
    result = compile_rows(rows, previous)
    assert stage(result) == expected
    assert next(iter(result["families"].values()))["demoted_reason"]


@pytest.mark.parametrize("before", ["shadow", "canary", "live"])
def test_expiry_demotes_every_stage(before):
    rows = all_evidence()
    previous = compile_rows(rows)
    while stage(previous) != before:
        previous = compile_rows(rows, previous)
    assert stage(compile_rows(rows, previous, now="2026-11-08T00:00:00Z")) == "none"


def test_default_fourteen_days_and_explicit_earlier_expiry():
    row = finding(expires_at="2027-01-01T00:00:00Z")
    previous = compile_rows([row])
    assert stage(compile_rows([row], previous, now="2026-11-07T10:00:00Z")) == "none"
    early = finding(expires_at=NOW)
    assert stage(compile_rows([early])) == "none"


def test_endpoint_unhealthy_more_than_24h_demotes_immediately():
    rows = all_evidence()
    previous = compile_rows(rows)
    for _ in range(2):
        previous = compile_rows(rows, previous)
    health = finding(ts="2026-10-24T11:00:00Z", metric={"name": "endpoint_health", "value": 0.,
        "ci_low": 0., "ci_high": 0., "unhealthy_since": "2026-10-23T11:59:59Z"})
    assert stage(compile_rows(rows + [health], previous)) == "none"
    boundary = finding(ts="2026-10-24T11:00:00Z", metric={"name": "endpoint_health", "value": 0.,
        "ci_low": 0., "ci_high": 0., "unhealthy_since": "2026-10-23T12:00:00Z"})
    assert stage(compile_rows(rows + [boundary], previous)) == "live"


def test_narrow_scope_overrides_even_with_none_and_does_not_generalize():
    broad = {"kind": "rules"}
    narrow = {"kind": "rules", "device": "s25", "quant": "Q4_0", "engine": "test@rev"}
    policy = compile_rows([finding(arm=broad), finding(arm=narrow, low=.1)])
    assert resolve_family(policy, "omp.tool")["stage"] == "shadow"
    assert resolve_family(policy, "omp.tool", {"device": "s25", "quant": "Q4_0", "engine": "test@rev"})["stage"] == "none"
    assert resolve_family(policy, "omp.tool", {"device": "s22"})["stage"] == "shadow"
    scoped_only = compile_rows([finding(arm=narrow)])
    assert resolve_family(scoped_only, "omp.tool", {"device": "s22"}) is None
    assert rule_key("omp.tool", scope_of(finding(arm=narrow))) in scoped_only["families"]


def test_serving_requires_measured_url_and_no_credentials():
    arm = {"kind": "engine", "device": "s25", "model": "test@rev", "engine": "test@rev",
           "backend": "cpu", "quant": "Q4_0"}
    source = {"kind": "manual", "path": "/synthetic/result.json", "sha256": "a" * 64,
              "url": "http://127.0.0.1:9000/v1"}
    row = finding(arm=arm, source=source)
    assert compile_rows([row])["serving"][0]["url"] == source["url"]
    assert not compile_rows([finding("inference", arm=arm, source=source)])["serving"]
    source["url"] = "http://private:secret@127.0.0.1/v1"
    assert not compile_rows([finding(arm=arm, source=source)])["serving"]


def test_policy_history_and_changelog(tmp_path):
    rows = all_evidence()
    before = compile_rows(rows)
    after = compile_rows(rows, before)
    write_policy(before, tmp_path)
    target = write_policy(after, tmp_path)
    assert json.loads(target.read_text()) == after
    assert json.loads((target.parent / "history" / (before["version"] + ".json")).read_text()) == before
    diff = policy_diff(before, after, rows)
    assert len(diff["changes"]) == 1
    text = markdown_diff(before, after, rows)
    assert "shadow → canary" in text and "/synthetic/results.json" in text
    assert rows[0]["id"] in text


def test_configurable_bars_counts_and_expiry():
    config = CompilerConfig(compiler_revision="c" * 40, bar_shadow=.6, n_shadow=5, expiry_days=1)
    row = finding(low=.7, n=5)
    previous = compile_rows([row], config=config)
    assert stage(previous) == "shadow"
    assert stage(compile_rows([row], previous, now="2026-10-25T10:00:00Z", config=config)) == "none"


def test_latest_failure_cannot_repromote_from_old_success():
    rows = [finding(), finding(low=.2, ts="2026-10-24T11:00:00Z")]
    assert stage(compile_rows(rows)) == "none"


def test_expired_outcome_demotes_live_to_canary_with_fresh_lower_stage_support():
    rows = all_evidence()
    old_outcome = finding("canary", ts="2026-10-24T09:00:00Z",
        expires_at="2026-10-24T12:30:00Z",
        metric={"name": "task_success", "divergence": 0., "aa_noise_floor": .01,
                "outcome_ledger_verified": True})
    rows = [old_outcome] + rows[:2]
    previous = compile_rows(rows)
    previous = compile_rows(rows, previous)
    previous = compile_rows(rows, previous)
    assert stage(previous) == "live"
    assert stage(compile_rows(rows, previous, now="2026-10-24T13:00:00Z")) == "canary"


def test_expired_replay_demotes_canary_to_shadow():
    measured = finding(metric={"live_shadow": True})
    replay = finding("replayed_L1", ts="2026-10-24T09:00:00Z",
                     expires_at="2026-10-24T12:30:00Z",
                     metric={"divergence": 0., "aa_noise_floor": .01})
    rows = [replay, measured]
    previous = compile_rows(rows, compile_rows(rows))
    assert stage(previous) == "canary"
    assert stage(compile_rows(rows, previous, now="2026-10-24T13:00:00Z")) == "shadow"


def test_scope_cannot_replace_an_arm_device():
    row = finding(arm={"kind": "slm", "device": "s25"})
    row["subject"]["scope"] = {"device": "s22"}
    row["id"] = digest({k: v for k, v in row.items() if k not in {"id", "prev"}})
    with pytest.raises(ValueError, match="conflicts"):
        compile_rows([row])


def test_future_findings_and_rejected_results_never_promote():
    assert stage(compile_rows([finding(ts="2026-10-25T00:00:00Z")])) == "none"
    assert stage(compile_rows([finding(metric={"promotion_eligible": False})])) == "none"


def test_serving_preserves_quant_scope_and_missing_endpoint_annotation():
    arm = {"kind": "slm", "model": "synthetic@rev", "quant": "Q4_0",
           "device": "mbp", "engine": "synthetic@rev", "backend": "metal"}
    source = {"kind": "manual", "path": "/synthetic/results.json",
              "sha256": "d" * 64, "url": "http://127.0.0.1:9000/v1"}
    rows = [finding(arm=arm, source=source, metric={"live_shadow": True}),
            finding("replayed_L1", arm=arm, metric={"divergence": 0., "aa_noise_floor": .01})]
    policy = compile_rows(rows, compile_rows(rows))
    endpoint = policy["serving"][0]
    assert endpoint["quant"] == "Q4_0"
    assert {key: endpoint[key] for key in arm if key != "kind"} == next(iter(policy["families"].values()))["scope"]
    assert next(iter(policy["families"].values()))["serving_missing"] is False
    missing_rows = [finding(metric={"live_shadow": True}),
                    finding("replayed_L1", metric={"divergence": 0., "aa_noise_floor": .01})]
    missing = compile_rows(missing_rows, compile_rows(missing_rows))
    assert next(iter(missing["families"].values()))["serving_missing"] is True
    assert "Missing serving endpoints" in markdown_diff(None, missing, missing_rows)
