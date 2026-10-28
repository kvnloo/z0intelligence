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
            metric=None, expires_at="2026-11-07T10:00:00Z", source=None, synthetic=False):
    values = {"name": "acc_at_conf", "value": max(low, .99), "n": n, "ci_low": low,
              "ci_high": 1., "ci_method": "wilson95", "higher_is_better": True}
    values.update(metric or {})
    return Finding(ts=ts, source=source or {"kind": "manual", "path": "/synthetic/results.json",
        "sha256": "b" * 64, "run_id": "test"}, subject={"family": "omp.tool", "job": "next-action"},
        arm=arm or {"kind": "slm", "model": "synthetic@rev"}, metric=values,
        evidence_class=kind, scope_note="Only synthetic next-action tasks and this exact arm.",
        expires_at=expires_at, synthetic=synthetic).to_dict()


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


def test_synthetic_provenance_and_measured_identity_survive_compilation():
    arm = {"kind": "slm", "model": "synthetic@rev", "quant": "Q8_0",
           "engine": "synthetic-build", "backend": "cpu", "device": "fixture"}
    metadata = {"expected_model_id": "synthetic-alias", "engine_build": "synthetic-build",
                "model_path": "/synthetic/alias.gguf", "model_resolved_path": "/synthetic/model.gguf",
                "model_sha256": "f" * 64}
    source = {"kind": "manual", "path": "/synthetic/results.json", "sha256": "e" * 64,
              "url": "http://127.0.0.1:9000/v1", **metadata}
    measured = finding(arm=arm, source=source, synthetic=True, metric={"live_shadow": True})
    replay = finding("replayed_L1", arm=arm, metric={"divergence": 0., "aa_noise_floor": .01})
    rows = [measured, replay]
    shadow = compile_rows(rows)
    assert stage(shadow) == "shadow"  # Synthetic never skips a promotion gate.
    policy = compile_rows(rows, shadow)
    assert stage(policy) == "canary"
    assert policy["synthetic"] is True
    assert next(iter(policy["families"].values()))["synthetic"] is True
    assert policy["serving"][0]["synthetic"] is True
    assert {key: policy["serving"][0][key] for key in metadata} == metadata
    assert policy_diff(None, policy, rows)["synthetic"] is True
    assert "**Synthetic policy. This is not a real experiment promotion.**" in markdown_diff(None, policy, rows)


def test_unused_synthetic_input_still_marks_whole_policy():
    row = finding("inference", synthetic=True)
    policy = compile_rows([row])
    assert stage(policy) == "none"
    assert policy["synthetic"] is True


def choice_finding(task, k, *, value=1., n=100, low=1., arm=None,
                   ts="2026-10-24T10:00:00Z", expires_at="2026-11-07T10:00:00Z",
                   evidence_class="measured", metric=None):
    row = finding(evidence_class, synthetic=True, n=n, low=low, ts=ts, expires_at=expires_at,
        arm=arm or {"kind": "slm", "model": "sha256:" + "a" * 64, "quant": "Q8_0",
                    "engine": "choice-engine@rev", "backend": "cpu", "device": "fixture"},
        metric={"name": "choice_accuracy", "option_count": k, "value": value, **(metric or {})})
    row["subject"] = {"family": "choice.option_capacity", "job": "ChoiceLadder", "task": task}
    row["id"] = digest({key: value for key, value in row.items() if key not in {"id", "prev"}})
    return row


def capacity(policy, row, risk="read"):
    from z0int.policy.compile import option_capacity_key
    return policy["option_capacity"][option_capacity_key(scope_of(row))][risk]


def compile_capacity(rows):
    """Supply explicit synthetic measured economics for capacity-only fixtures."""
    costs = []
    seen = set()
    for choice in rows:
        identity = canonical(scope_of(choice))
        if identity in seen:
            continue
        seen.add(identity)
        for field, value in {"cost_call": .1, "cost_frontier": 1.,
                             "cost_error": 20., "abstain_rate": 0.}.items():
            row = finding(synthetic=True, arm=choice["arm"],
                metric={"name": field, "value": value, "ci_low": value,
                        "ci_high": value, "ci_method": "none",
                        "higher_is_better": False, "cost_unit": "latency_ms_lambda0"})
            row["subject"] = {"family": "choice.error_cost", "risk_class": "read",
                              "job": "observed_frontier_recovery"}
            row["id"] = digest({k: v for k, v in row.items() if k not in {"id", "prev"}})
            costs.append(row)
    return compile_rows(rows + costs)


def test_option_capacity_minimum_of_task_maxima_and_recomputed_wilson():
    rows = [choice_finding("T1", 8), choice_finding("T1", 16, value=.9),
            choice_finding("T2", 5), choice_finding("T3", 16), choice_finding("T4", 10)]
    policy = compile_capacity(rows)
    entry = capacity(policy, rows[0])
    assert entry["allowed_k"] == 5
    assert entry["task"] == ["T1", "T2", "T3", "T4"]
    assert len(entry["finding_ids"]) == 8
    assert .96 < entry["wilson_lower"] < .97  # Stated 1.0 cannot inflate the Wilson bound.
    assert entry["bar_derived"] == pytest.approx(.955)
    assert stage(policy) == "none"  # Choice capacity does not create routing credit.
    assert canonical(policy) == canonical(compile_capacity(rows))


def test_unmeasured_default_economics_never_authorize_routing():
    rows = [choice_finding(task, 8) for task in ("T1", "T2", "T3", "T4")]
    policy = compile_rows(rows)
    for risk, bar in (("read", .955), ("mutating_reversible", .991)):
        entry = capacity(policy, rows[0], risk)
        assert entry["allowed_k"] == 0
        assert entry["bar_derived"] == pytest.approx(bar)
        assert entry["inputs"]["status"] == "unmeasured"
        assert entry["wilson_lower"] is None
        assert entry["finding_ids"] == []


def test_option_capacity_missing_failed_expired_tasks_and_unknown_scope_disable_slm():
    rows = [choice_finding(task, 8) for task in ("T1", "T2", "T3")]
    missing = capacity(compile_capacity(rows), rows[0])
    assert missing["allowed_k"] == 0
    assert len(missing["finding_ids"]) == 4 and missing["wilson_lower"] is None
    rows.append(choice_finding("T4", 10, value=.8))
    assert capacity(compile_capacity(rows), rows[0])["allowed_k"] == 0
    rows[-1] = choice_finding("T4", 10, expires_at=NOW)
    assert capacity(compile_capacity(rows), rows[0])["allowed_k"] == 0
    assert compile_rows([])["option_capacity"] == {}


def test_option_capacity_never_crosses_any_physical_scope_dimension():
    rows = [choice_finding(task, 8) for task in ("T1", "T2", "T3", "T4")]
    for dimension in ("model", "quant", "engine", "backend", "device"):
        different = {**rows[0]["arm"], dimension: ("sha256:" + "b" * 64 if dimension == "model"
                                                else "different-" + dimension)}
        other = choice_finding("T1", 32, arm=different)
        policy = compile_capacity(rows + [other])
        assert capacity(policy, rows[0])["allowed_k"] == 8
        assert capacity(policy, other)["allowed_k"] == 0


def test_option_capacity_latest_failure_supersedes_old_success():
    rows = [choice_finding(task, 8) for task in ("T1", "T2", "T3", "T4")]
    rows.append(choice_finding("T2", 8, value=.5, ts="2026-10-24T11:00:00Z"))
    assert capacity(compile_capacity(rows), rows[0])["allowed_k"] == 0


@pytest.mark.parametrize("override", [
    {"evidence_class": "inference"},
    {"ts": "2026-10-25T00:00:00Z"},
    {"metric": {"ci_method": "none"}},
    {"metric": {"promotion_eligible": False}},
    {"metric": {"correct": 50}},
])
def test_option_capacity_rejects_ineligible_or_inconsistent_evidence(override):
    rows = [choice_finding(task, 8) for task in ("T1", "T2", "T3")]
    rows.append(choice_finding("T4", 8, **override))
    assert capacity(compile_capacity(rows), rows[0])["allowed_k"] == 0


def test_option_capacity_risk_expected_cost_and_expiry():
    config = CompilerConfig(compiler_revision="a" * 40,
        option_capacity_tasks=("custom-task",),
        option_capacity_costs={
            "read": {"cost_call": 1., "cost_frontier": 10., "cost_error": 2.,
                     "abstain_rate": 0., "measured": False, "finding_ids": []},
            "mutating_reversible": {"cost_call": 1., "cost_frontier": 10.,
                     "cost_error": 100., "abstain_rate": 0., "measured": False, "finding_ids": []}})
    row = choice_finding("custom-task", 8, value=.85, n=1000,
                         expires_at="2026-10-24T13:00:00Z")
    policy = compile_rows([row], config=config)
    assert capacity(policy, row)["allowed_k"] == 0
    assert capacity(policy, row, "mutating_reversible")["allowed_k"] == 0
    assert capacity(policy, row, "destructive")["allowed_k"] == 0
    assert capacity(policy, row)["inputs"]["status"] == "unmeasured"
    assert capacity(policy, row)["bar_derived"] == pytest.approx(-3.5)
    assert capacity(policy, row)["wilson_lower"] is None
    assert capacity(compile_rows([row], now="2026-10-24T13:00:00Z", config=config), row)["allowed_k"] == 0


def test_option_capacity_missing_evidence_known_scope_disables_slm():
    row = finding(arm={"kind": "slm", "model": "sha256:" + "c" * 64, "quant": "Q4_0",
                       "engine": "engine", "backend": "cpu", "device": "fixture"})
    entry = capacity(compile_rows([row]), row)
    assert entry["allowed_k"] == 0
    assert not entry["finding_ids"] and entry["wilson_lower"] is None


def test_measured_capacity_costs_require_real_risk_bound_findings():
    config = CompilerConfig(compiler_revision="a" * 40, option_capacity_costs={
        "read": {"cost_call": 1., "cost_frontier": 10., "cost_error": 2.,
                 "abstain_rate": 0., "measured": True, "finding_ids": ["missing"]}})
    with pytest.raises(ValueError, match="fresh exact-scope"):
        compile_rows([choice_finding("T1", 8)], config=config)


def test_capacity_cost_measurements_are_bound_to_finding_values_and_expiry():
    costs = {"cost_call": 1., "cost_frontier": 10., "cost_error": 2., "abstain_rate": 0.}
    choice = choice_finding("T1", 8, value=.85, n=1000)
    sources = []
    for field, value in costs.items():
        row = finding(synthetic=True, arm=choice["arm"], expires_at="2026-10-24T13:00:00Z",
            metric={"name": field, "value": value, "ci_low": value,
                    "ci_high": value, "ci_method": "none", "higher_is_better": False,
                    "cost_unit": "latency_ms_lambda0"})
        row["subject"] = {"family": "choice.error_cost", "risk_class": "read",
                          "job": "observed_frontier_recovery"}
        row["id"] = digest({key: value for key, value in row.items() if key not in {"id", "prev"}})
        sources.append(row)
    config = CompilerConfig(compiler_revision="a" * 40, option_capacity_tasks=("T1",),
        option_capacity_costs={"read": {**costs, "measured": True,
                                      "cost_unit": "latency_ms_lambda0",
                                      "finding_ids": [row["id"] for row in sources]}})
    entry = capacity(compile_rows(sources + [choice], config=config), choice)
    assert entry["allowed_k"] == 8
    assert entry["expires_at"] == "2026-10-24T13:00:00Z"
    assert entry["inputs"]["status"] == "measured"
    config.option_capacity_costs["read"]["cost_error"] = 3.
    with pytest.raises(ValueError, match="matching finding"):
        compile_rows(sources + [choice], config=config)


@pytest.mark.parametrize("dimension", ["model", "quant", "engine", "backend", "device"])
def test_option_capacity_configured_costs_do_not_cross_scope(dimension):
    choice = choice_finding("T1", 8, n=1000)
    other = {**choice["arm"], dimension: ("sha256:" + "b" * 64 if dimension == "model" else "other")}
    costs = {"cost_call": .1, "cost_frontier": 1., "cost_error": 20., "abstain_rate": 0.}
    sources = []
    for name, value in costs.items():
        row = finding(arm=other, metric={"name": name, "value": value, "cost_unit": "latency_ms_lambda0"})
        row["subject"] = {"family": "choice.error_cost", "risk_class": "read"}
        row["id"] = digest({k: v for k, v in row.items() if k not in {"id", "prev"}})
        sources.append(row)
    other_choice = choice_finding("T1", 8, n=1000, arm=other)
    config = CompilerConfig(compiler_revision="a" * 40, option_capacity_tasks=("T1",), option_capacity_costs={"read": {
        **costs, "measured": True, "cost_unit": "latency_ms_lambda0",
        "finding_ids": [row["id"] for row in sources]}})
    policy = compile_rows([choice, other_choice] + sources, config=config)
    assert capacity(policy, choice)["allowed_k"] == 0
    assert capacity(policy, choice)["inputs"]["status"] == "unmeasured"
    assert capacity(policy, other_choice)["allowed_k"] == 8
    mixed = deepcopy(sources[0])
    mixed["arm"] = choice["arm"]
    mixed["id"] = digest({k: v for k, v in mixed.items() if k not in {"id", "prev"}})
    config.option_capacity_costs["read"]["finding_ids"][0] = mixed["id"]
    with pytest.raises(ValueError, match="one exact-scope"):
        compile_rows([choice, other_choice, mixed] + sources[1:], config=config)


def test_capacity_abstention_cost_can_remove_otherwise_profitable_route():
    costs = {"cost_call": 1., "cost_frontier": 10., "cost_error": 2.,
             "abstain_rate": 1., "measured": False, "finding_ids": []}
    config = CompilerConfig(compiler_revision="a" * 40, option_capacity_tasks=("T1",),
                            option_capacity_costs={"read": costs})
    row = choice_finding("T1", 8, n=1000)
    assert capacity(compile_rows([row], config=config), row)["allowed_k"] == 0


def test_fresh_scoped_cost_findings_are_adopted_without_configuration_flip():
    choice = choice_finding("T1", 8, value=.85, n=1000)
    rows = [choice]
    for field, value in {"cost_call": 1., "cost_frontier": 10.,
                         "cost_error": 2., "abstain_rate": 0.}.items():
        row = finding(synthetic=True, arm=choice["arm"],
            metric={"name": field, "value": value, "ci_low": value,
                    "ci_high": value, "ci_method": "none", "higher_is_better": False,
                    "cost_unit": "latency_ms_lambda0"})
        row["subject"] = {"family": "choice.error_cost", "risk_class": "read",
                          "job": "observed_frontier_recovery"}
        row["id"] = digest({key: value for key, value in row.items() if key not in {"id", "prev"}})
        rows.append(row)
    config = CompilerConfig(compiler_revision="a" * 40, option_capacity_tasks=("T1",))
    entry = capacity(compile_rows(rows, config=config), choice)
    assert entry["allowed_k"] == 8
    assert entry["inputs"]["status"] == "measured"
    other = choice_finding("T1", 8, value=.85, n=1000,
                           arm={**choice["arm"], "device": "unmeasured-device"})
    assert capacity(compile_rows(rows + [other], config=config), other)["allowed_k"] == 0
    proxy = deepcopy(rows[1])
    proxy["ts"] = "2026-10-24T11:00:00Z"
    proxy["evidence_kind"] = "proxy"
    proxy["id"] = digest({k: v for k, v in proxy.items() if k not in {"id", "prev"}})
    with_proxy = capacity(compile_rows(rows + [proxy], config=config), choice)
    assert with_proxy["allowed_k"] == 8
    assert proxy["id"] not in with_proxy["inputs"]["finding_ids"]


def test_capacity_identity_rejects_aliases_and_uses_content_addressed_scope():
    from z0int.policy.compile import option_capacity_key
    row = choice_finding("T1", 8)
    scope = scope_of(row)
    assert option_capacity_key(scope) == option_capacity_key(dict(reversed(list(scope.items()))))
    with pytest.raises(ValueError, match="GGUF bytes"):
        option_capacity_key({**scope, "model": "friendly-alias"})
    assert option_capacity_key({**scope, "model": "sha256:" + "b" * 64}) != option_capacity_key(scope)
