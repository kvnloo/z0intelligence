"""Synthetic controls only: no captured identifiers, arguments or source bodies."""
import importlib.util
import json
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location("neutral_aggregate", Path(__file__).with_name("aggregate.py"))
assert spec is not None and spec.loader is not None
aggregate = importlib.util.module_from_spec(spec)
spec.loader.exec_module(aggregate)


def row(identity="synthetic-1", **changes):
    base = {"schema": "z0.latency_call.v1", "call_id": identity, "harness": "omp", "run": "synthetic-r12", "native_response_id": "a" * 32, "latency_ms": 1000, "ttft_ms": 100, "after_first_token_ms": 900, "output_tokens": 100, "reasoning_tokens": 70, "reasoning_is_subset_of_output": True, "input_uncached_tokens": 20, "cache_read_tokens": 80, "cache_write_tokens": 0}
    base.update(changes)
    return base


def test_mixed_strata_and_missing_denominators():
    result = aggregate.project([row(), row("synthetic-2", native_response_id="00000000-0000-0000-0000-000000000001", reasoning_tokens=None, ttft_ms=None, after_first_token_ms=None)])
    assert len(result["groups"]) == 2
    groups = {g["response_shape"]: g for g in result["groups"]}
    assert groups["32hex"]["metrics"]["reasoning_tokens"]["known"] == 1
    assert groups["UUID"]["metrics"]["reasoning_tokens"]["known"] == 0
    assert groups["UUID"]["metrics"]["reasoning_tokens"]["sum_observed"] is None
    assert result["mixed_stratum_runs"] == ["r12"]


@pytest.mark.parametrize("value", [-1, True, float("nan"), float("inf"), "12"])
@pytest.mark.parametrize("field", ["latency_ms", "ttft_ms", "reasoning_tokens", "cache_read_tokens"])
def test_malformed_metrics_refused(field, value):
    with pytest.raises(ValueError, match="invalid metric"):
        aggregate.project([row(**{field: value})])


def test_fractional_tokens_refused():
    with pytest.raises(ValueError, match="invalid metric"):
        aggregate.project([row(output_tokens=1.5)])


def test_duration_relations_refused():
    with pytest.raises(ValueError, match="timing relation"):
        aggregate.project([row(ttft_ms=1100)])
    with pytest.raises(ValueError, match="timing relation"):
        aggregate.project([row(after_first_token_ms=50)])


def test_subset_not_double_counted():
    group = aggregate.project([row()])["groups"][0]
    assert group["metrics"]["output_tokens"]["sum_observed"] == 100
    assert group["metrics"]["nonreasoning_output_tokens"]["sum_observed"] == 30
    with pytest.raises(ValueError, match="reasoning subset"):
        aggregate.project([row(reasoning_tokens=101)])
    group = aggregate.project([row(reasoning_is_subset_of_output=None)])["groups"][0]
    assert group["metrics"]["nonreasoning_output_tokens"]["known"] == 0


def test_copied_identity_refused_without_disclosure():
    with pytest.raises(ValueError) as e:
        aggregate.project([row("PRIVATE-ID"), row("PRIVATE-ID")])
    assert str(e.value) == "duplicate call identity"
    assert "PRIVATE-ID" not in str(e.value)


def test_unknown_labels_and_content_never_exported():
    result = aggregate.project([row(harness="PRIVATE-HARNESS", run="PRIVATE-RUN", native_response_id="PRIVATE-ID", model="PRIVATE-MODEL", source_refs=["PRIVATE-PATH"], next_actions=["PRIVATE-ACTION"])])
    text = json.dumps(result)
    assert "PRIVATE" not in text and "synthetic-1" not in text
    assert result["groups"][0]["harness"] == "unknown"
    assert result["groups"][0]["run"] == "unknown"
    assert result["groups"][0]["response_shape"] == "unknown"


@pytest.mark.parametrize("response", ["a" * 31, " " + "a" * 32, "000000000000-0000-0000-000000000001", None, True])
def test_malformed_shape_not_repaired(response):
    assert aggregate.response_shape(response) == "unknown"


def test_cache_missing_not_zero_or_complete():
    group = aggregate.project([row(cache_read_tokens=None)])["groups"][0]
    assert group["cache_read_share_observed"] is None
    assert group["cache_paired_calls"] == 0
    assert group["metrics"]["cache_read_tokens"]["missing"] == 1


def test_nearest_rank_percentiles_and_empty():
    result = aggregate.project([row(str(i), latency_ms=i * 1000, ttft_ms=0, after_first_token_ms=i * 1000) for i in range(1, 5)])
    assert result["groups"][0]["metrics"]["latency_ms"]["p50"] == 2000
    assert result["groups"][0]["metrics"]["latency_ms"]["p95"] == 4000
    assert aggregate.project([])["observations"] == 0


@pytest.mark.parametrize("change", [{"schema": "future.v2"}, {"call_id": None}, {"call_id": True}])
def test_unsupported_source_or_identity_refused(change):
    with pytest.raises(ValueError):
        aggregate.project([row(**change)])


def test_rates_are_not_additive_measurements():
    group = aggregate.project([row(), row("second")])["groups"][0]
    rate = group["metrics"]["output_tokens_per_second_observed"]
    assert rate["sum_observed"] is None
    assert rate["additive"] is False
    assert group["metrics"]["latency_ms"]["additive"] is True


def test_unknown_runs_do_not_become_one_mixed_run():
    result = aggregate.project([row(run="PRIVATE-ONE"), row("second", run="PRIVATE-TWO", native_response_id="00000000-0000-0000-0000-000000000001")])
    assert result["unknown_omp_runs"] == 2
    assert result["unknown_omp_mixed_stratum_runs"] == 0
    assert all(g["run_attribution"] == "pooled-unknown" for g in result["groups"])
    result = aggregate.project([row(run="PRIVATE-ONE"), row("second", run="PRIVATE-ONE", native_response_id="00000000-0000-0000-0000-000000000001")])
    assert result["unknown_omp_mixed_stratum_runs"] == 1
    assert "PRIVATE" not in json.dumps(result)


def test_native_accounting_requires_union_not_sum_for_residual():
    result = aggregate.account_native_summary(wall_ms=100, model_sum_ms=130, tool_union_ms=20)
    assert result["model_sum_exceeds_wall"] is True
    assert result["uncovered_wall_ms"] is None
    assert result["tool_compute_ms"] is None
    assert result["approval_wait_ms"] is None
    assert result["verified_success"] is None
    result = aggregate.account_native_summary(wall_ms=100, model_sum_ms=130, tool_union_ms=20, combined_union_ms=90)
    assert result["uncovered_wall_ms"] == 10


@pytest.mark.parametrize("value", [-1, True, float("nan"), float("inf"), "100"])
def test_bad_native_summary_measurements_refused(value):
    with pytest.raises(ValueError):
        aggregate.account_native_summary(wall_ms=value, model_sum_ms=10)


def test_union_exceeds_wall_refused_not_clipped():
    with pytest.raises(ValueError, match="union exceeds wall"):
        aggregate.account_native_summary(wall_ms=100, model_sum_ms=50, combined_union_ms=101)
    with pytest.raises(ValueError, match="union exceeds wall"):
        aggregate.account_native_summary(wall_ms=100, model_sum_ms=50, tool_union_ms=101)


def test_overflow_rate_refused_without_serializing_nonfinite():
    with pytest.raises(ValueError, match="invalid metric"):
        aggregate.project([row(output_tokens=10**308, reasoning_tokens=None, latency_ms=1e-308, ttft_ms=0, after_first_token_ms=1e-308)])
