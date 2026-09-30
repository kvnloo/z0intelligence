import pytest

from tokenomics.models import TokenUsage, TokenomicsEvent
from tokenomics.report import build_savings_report, parse_range

NOW = 1_700_000_000.0
DAY = 86400.0


def test_weeks_basic():
    assert parse_range("2w", now=NOW) == (NOW - 14 * DAY, NOW)
    assert parse_range("1w", now=NOW) == (NOW - 7 * DAY, NOW)


def test_weeks_case_and_whitespace():
    assert parse_range("3W", now=NOW) == (NOW - 21 * DAY, NOW)
    assert parse_range("  4w ", now=NOW) == (NOW - 28 * DAY, NOW)


def test_weeks_zero():
    assert parse_range("0w", now=NOW) == (NOW, NOW)


@pytest.mark.parametrize("bad", ["w", "1.5w", "-1w", "xw", "2 w", "2wk"])
def test_invalid_week_specs_raise(bad):
    with pytest.raises(ValueError):
        parse_range(bad, now=NOW)


def test_existing_units_unchanged():
    assert parse_range("7d", now=NOW) == (NOW - 7 * DAY, NOW)
    assert parse_range("24h", now=NOW) == (NOW - 24 * 3600.0, NOW)
    assert parse_range("30m", now=NOW) == (NOW - 30 * 60.0, NOW)
    assert parse_range("all", now=NOW) == (0.0, NOW)


def _ev(age_days: float) -> TokenomicsEvent:
    return TokenomicsEvent(
        kind="llm",
        name="root",
        ts=NOW - age_days * DAY,
        usage=TokenUsage(input_tokens=10, output_tokens=1, source="provider"),
    )


def test_savings_report_accepts_week_range():
    events = [_ev(1), _ev(6.5), _ev(8), _ev(13), _ev(20)]
    assert build_savings_report(events, range_spec="1w", now=NOW)["n_events"] == 2
    assert build_savings_report(events, range_spec="2w", now=NOW)["n_events"] == 4
