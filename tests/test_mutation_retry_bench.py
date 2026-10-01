from __future__ import annotations

from pathlib import Path

from z0int.backends.bench.fixtures import load_fixtures


def test_mutation_retry_fixture_loads_as_bounded_decision_capability() -> None:
    root = Path(__file__).resolve().parents[1]
    rows = load_fixtures(
        root / "benchmarks" / "fixtures" / "mutation-retry-v0" / "examples.jsonl"
    )

    assert len(rows) == 8
    assert {row.capability for row in rows} == {"mutation_retry_disposition"}
    assert {row.gold for row in rows} == {
        "retry",
        "observe",
        "resend",
        "escalate",
        "stop",
    }
    for row in rows:
        assert row.question.type == "choice"
        assert {option.id for option in row.question.options} == {
            "retry",
            "observe",
            "resend",
            "escalate",
            "stop",
        }


def test_ambiguous_mutation_rows_mark_fresh_retry_as_dangerous() -> None:
    root = Path(__file__).resolve().parents[1]
    rows = load_fixtures(
        root / "benchmarks" / "fixtures" / "mutation-retry-v0" / "examples.jsonl"
    )

    ambiguous = [row for row in rows if (row.raw or {}).get("state", {}).get("effect") == "unknown"]
    assert ambiguous
    for row in ambiguous:
        assert row.dangerous_prediction("retry")


def test_durable_resend_and_observe_are_distinct_gold_labels() -> None:
    root = Path(__file__).resolve().parents[1]
    rows = {
        row.id: row
        for row in load_fixtures(
            root / "benchmarks" / "fixtures" / "mutation-retry-v0" / "examples.jsonl"
        )
    }

    assert rows["mutation_retry_disposition/supersync_same_op_ack_lost"].gold == "resend"
    assert rows["mutation_retry_disposition/hermes_relay_seal_ack_lost"].gold == "resend"
    assert rows["mutation_retry_disposition/cua_click_ack_lost"].gold == "observe"
    assert rows["mutation_retry_disposition/wger_log_set_ack_lost"].gold == "observe"
    assert rows["mutation_retry_disposition/hermes_terminal_post_spawn_exception"].gold == "escalate"
