"""Hermetic unit tests for decision benchmark analytics reducers."""

import pytest

from z0int.backends.bench import analytics as an


def row(**overrides):
    data = {
        "status": "ok",
        "capability": "choice",
        "candidate_id": "A",
        "fixture_id": "f1",
        "verified_correct": False,
        "dangerous_false": False,
        "brier": None,
        "latency_ms": None,
        "startup_ms": None,
    }
    data.update(overrides)
    return data


R = [
    row(fixture_id="f1", verified_correct=True, brier=0.1, latency_ms=10, startup_ms=100),
    row(fixture_id="f2", dangerous_false=True, brier=0.3, latency_ms=30),
    row(candidate_id="B", fixture_id="f1", verified_correct=True, latency_ms=20, startup_ms=50),
    row(candidate_id="B", capability="bool", fixture_id="f2",
        verified_correct=True, brier=0.2, latency_ms=40),
    row(fixture_id="f3", status="unavailable"),
    row(candidate_id="B", fixture_id="f3", status="error"),
]


@pytest.fixture(autouse=True)
def _use_plain_bench_rows(monkeypatch):
    """Exercise only aggregation, not event materialization or external services."""
    monkeypatch.setattr(an, "materialize_rows", lambda events: list(events))


def test_percentile():
    assert an._percentile([], 50) is None
    assert an._percentile([5.0], 50) == pytest.approx(5.0)
    assert an._percentile([4, 1, 3, 2], 50) == pytest.approx(2.5)
    assert an._percentile([4, 1, 3, 2], 0) == pytest.approx(1.0)
    assert an._percentile([4, 1, 3, 2], 100) == pytest.approx(4.0)
    assert an._percentile([4, 1, 3, 2], 95) == pytest.approx(3.85)


def test_quality_by_capability():
    result = an.build_decision_analytics(R)
    assert result["schema"] == "z0int.backends_bench.analytics.v1"
    capabilities = result["quality"]["by_capability"]
    assert list(capabilities) == ["bool", "choice"]

    choice = capabilities["choice"]
    assert choice["n"] == 3
    assert choice["correct"] == 2
    assert choice["accuracy"] == pytest.approx(2 / 3)
    assert choice["dangerous_false_count"] == 1
    assert choice["dangerous_false_rate"] == pytest.approx(1 / 3)
    assert choice["mean_brier"] == pytest.approx(0.2)
    assert choice["latency_ms_p50"] == pytest.approx(20)
    assert choice["latency_ms_p95"] == pytest.approx(29)
    assert choice["startup_ms_p50"] == pytest.approx(75)

    boolean = capabilities["bool"]
    assert boolean["n"] == 1
    assert boolean["correct"] == 1
    assert boolean["accuracy"] == pytest.approx(1.0)
    assert boolean["dangerous_false_count"] == 0
    assert boolean["mean_brier"] == pytest.approx(0.2)
    assert boolean["latency_ms_p50"] == pytest.approx(40)
    assert boolean["latency_ms_p95"] == pytest.approx(40)
    assert boolean["startup_ms_p50"] is None


def test_safety_section():
    safety = an.build_decision_analytics(R)["safety"]["by_capability"]
    assert list(safety) == ["bool", "choice"]
    assert safety["choice"]["dangerous_false_count"] == 1
    assert safety["choice"]["dangerous_false_rate"] == pytest.approx(1 / 3)
    assert safety["bool"]["dangerous_false_count"] == 0
    assert safety["bool"]["dangerous_false_rate"] == pytest.approx(0.0)


def test_latency_by_backend():
    latency = an.build_decision_analytics(R)["latency"]
    assert list(latency) == ["A", "B"]

    a = latency["A"]
    assert list(a["by_capability"]) == ["choice"]
    assert a["by_capability"]["choice"] == a["overall"]
    assert a["overall"]["n"] == 2
    assert a["overall"]["correct"] == 1
    assert a["overall"]["accuracy"] == pytest.approx(0.5)
    assert a["overall"]["dangerous_false_rate"] == pytest.approx(0.5)
    assert a["overall"]["latency_ms_p50"] == pytest.approx(20)
    assert a["overall"]["startup_ms_p50"] == pytest.approx(100)

    b = latency["B"]
    assert list(b["by_capability"]) == ["bool", "choice"]
    assert b["overall"]["n"] == 2
    assert b["overall"]["correct"] == 2
    assert b["overall"]["accuracy"] == pytest.approx(1.0)
    assert b["overall"]["latency_ms_p50"] == pytest.approx(30)


def test_reliability_counts():
    assert an.build_decision_analytics(R)["reliability"] == {
        "rows_total": 6,
        "rows_ok": 4,
        "rows_unavailable": 1,
        "rows_error": 1,
    }


def test_empty_events():
    result = an.build_decision_analytics([])
    assert result["quality"]["by_capability"] == {}
    assert result["safety"]["by_capability"] == {}
    assert result["latency"] == {}
    assert result["reliability"] == {
        "rows_total": 0,
        "rows_ok": 0,
        "rows_unavailable": 0,
        "rows_error": 0,
    }


def test_accepts_one_shot_iterator():
    reliability = an.build_decision_analytics(iter(R))["reliability"]
    assert reliability == {
        "rows_total": 6,
        "rows_ok": 4,
        "rows_unavailable": 1,
        "rows_error": 1,
    }


def test_paired_comparisons_counts():
    events = [
        row(candidate_id="A", fixture_id="f1", verified_correct=True, latency_ms=10),
        row(candidate_id="B", fixture_id="f1", verified_correct=False, latency_ms=20),
        row(candidate_id="A", fixture_id="f2", verified_correct=False, latency_ms=30),
        row(candidate_id="B", fixture_id="f2", verified_correct=True, latency_ms=10),
        row(candidate_id="A", fixture_id="f3", verified_correct=False, latency_ms=20),
        row(candidate_id="B", fixture_id="f3", verified_correct=False, latency_ms=20),
        row(candidate_id="A", fixture_id="f4", verified_correct=True, latency_ms=None),
        row(candidate_id="B", fixture_id="f4", verified_correct=True, latency_ms=5),
        row(candidate_id="A", fixture_id="f5", verified_correct=True, latency_ms=7),
    ]
    report = an.build_paired_comparisons(events)
    assert report["schema"] == "z0int.backends_bench.paired.v1"
    assert len(report["comparisons"]) == 1
    comparison = report["comparisons"][0]
    assert comparison["a"] == "A"
    assert comparison["b"] == "B"
    assert comparison["paired_fixtures"] == 4
    assert comparison["both_correct"] == 1
    assert comparison["a_only_correct"] == 1
    assert comparison["b_only_correct"] == 1
    assert comparison["both_wrong"] == 1
    assert comparison["median_latency_ratio_a_over_b"] == pytest.approx(1.0)


def test_paired_edge_cases():
    only_a = an.build_paired_comparisons([row(candidate_id="A")])
    assert only_a["comparisons"] == []

    events = [
        row(candidate_id="C", fixture_id="f1", verified_correct=True, latency_ms=40),
        row(candidate_id="B", fixture_id="f1", verified_correct=True, latency_ms=0),
        row(candidate_id="A", fixture_id="f1", verified_correct=True, latency_ms=10),
        row(candidate_id="A", fixture_id="f2", status="error", latency_ms=10),
        row(candidate_id="B", fixture_id="f2", verified_correct=False, latency_ms=10),
        row(candidate_id="C", fixture_id="f2", status="unavailable", latency_ms=10),
    ]
    comparisons = an.build_paired_comparisons(events)["comparisons"]
    assert [(c["a"], c["b"]) for c in comparisons] == [
        ("A", "B"),
        ("A", "C"),
        ("B", "C"),
    ]
    assert all(c["paired_fixtures"] == 1 for c in comparisons)
    assert all(c["both_correct"] == 1 for c in comparisons)
    assert comparisons[0]["median_latency_ratio_a_over_b"] is None
    assert comparisons[1]["median_latency_ratio_a_over_b"] == pytest.approx(0.25)
    assert comparisons[2]["median_latency_ratio_a_over_b"] is None


def test_render_analytics_md():
    analytics = an.build_decision_analytics(R)
    rendered = an.render_analytics_md(analytics)
    assert rendered.startswith("# Decision bench analytics\n\n## Reliability\n")
    assert "- rows total: 6" in rendered
    assert "### choice" in rendered
    assert "- n=3 accuracy=" in rendered
    assert rendered.endswith("\n")
    assert "## Measurement coverage" not in rendered

    with_coverage = an.render_analytics_md(analytics, coverage_text="line1\n\n")
    fence = chr(96) * 3
    assert with_coverage.endswith(
        f"## Measurement coverage\n\n{fence}\nline1\n{fence}\n"
    )
    assert "- rows total: None" in an.render_analytics_md({})
