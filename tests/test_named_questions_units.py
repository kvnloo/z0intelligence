"""Pure edge and error-path tests for the shared named-question transport."""

import pytest

from z0int.backends.base import DecisionOption, DecisionQuestion
from z0int.backends.named_questions import (
    DEFAULT_FALSE_CRITERION,
    DEFAULT_TRUE_CRITERION,
    answer_from_named,
    normalize_probs,
    question_to_named,
)


def _choice_question() -> DecisionQuestion:
    return DecisionQuestion(
        id="q",
        type="choice",
        instructions="p",
        options=(DecisionOption("a", "A"), DecisionOption("b", "B")),
    )


def _boolean_question() -> DecisionQuestion:
    return DecisionQuestion(id="q", type="boolean", instructions="p")


def _score_question() -> DecisionQuestion:
    return DecisionQuestion(
        id="q", type="score", instructions="p", levels=("x", "y", "z")
    )


def test_normalize_renormalizes_and_stringifies_keys():
    assert normalize_probs({"a": 0.2, "b": 0.2}, source="x") == {
        "a": 0.5,
        "b": 0.5,
    }
    assert normalize_probs({1: 0.5}, source="x") == {"1": 1.0}


def test_normalize_rejects_out_of_range():
    for probs in ({"a": 1.2}, {"a": -0.1, "b": 0.5}):
        with pytest.raises(ValueError, match="invalid probability"):
            normalize_probs(probs, source="x")


def test_choice_without_choice_key_uses_argmax():
    answer = answer_from_named(
        _choice_question(),
        {"type": "choice", "probabilities": {"a": 0.2, "b": 0.6}},
        source="t",
    )
    assert answer.question_id == "q"
    assert answer.type == "choice"
    assert answer.value == "b"
    assert answer.probabilities == pytest.approx({"a": 0.25, "b": 0.75})


def test_choice_explicit_choice_wins_and_confidence_parsed():
    raw = {
        "type": "choice",
        "choice": "b",
        "probabilities": {"a": 0.2, "b": 0.2},
    }
    answer = answer_from_named(
        _choice_question(), {**raw, "confidence": "0.75"}, source="t"
    )
    assert answer.value == "b"
    assert answer.probabilities == pytest.approx({"a": 0.5, "b": 0.5})
    assert answer.confidence == 0.75

    missing = answer_from_named(_choice_question(), raw, source="t")
    assert missing.confidence is None

    zero = answer_from_named(
        _choice_question(), {**raw, "confidence": 0}, source="t"
    )
    assert zero.confidence == 0.0


def test_boolean_missing_noul_defaults_to_half_true():
    question = _boolean_question()
    default = answer_from_named(question, {"type": "noul"}, source="t")
    assert default.type == "boolean"
    assert default.value is True
    assert default.probabilities == {"false": 0.5, "true": 0.5}

    half = answer_from_named(
        question, {"type": "noul", "noul": 0.5}, source="t"
    )
    assert half.value is True
    assert half.probabilities == {"false": 0.5, "true": 0.5}

    below = answer_from_named(
        question, {"type": "noul", "noul": 0.4999}, source="t"
    )
    assert below.value is False
    assert below.probabilities == pytest.approx(
        {"false": 0.5001, "true": 0.4999}
    )


def test_noul_out_of_range_raises():
    with pytest.raises(ValueError, match="invalid probability for 'false'"):
        answer_from_named(
            _boolean_question(), {"type": "noul", "noul": 1.5}, source="t"
        )


def test_raw_noul_type_overrides_choice_question():
    boolean_answer = answer_from_named(
        _choice_question(), {"type": "noul", "noul": 0.9}, source="t"
    )
    assert boolean_answer.type == "boolean"
    assert boolean_answer.value is True
    assert boolean_answer.probabilities == pytest.approx(
        {"false": 0.1, "true": 0.9}
    )

    choice_answer = answer_from_named(
        _score_question(),
        {"type": "choice", "choice": "1", "probabilities": {"1": 1}},
        source="t",
    )
    assert choice_answer.type == "choice"
    assert choice_answer.value == "1"
    assert choice_answer.probabilities == {"1": 1.0}


def test_boolean_question_ignores_raw_type():
    answer = answer_from_named(
        _boolean_question(), {"type": "score", "noul": 0.2}, source="t"
    )
    assert answer.type == "boolean"
    assert answer.value is False
    assert answer.probabilities == pytest.approx(
        {"false": 0.8, "true": 0.2}
    )


def test_score_expected_value_when_score_missing():
    expected = answer_from_named(
        _score_question(),
        {"type": "score", "probabilities": {"0": 0.5, "2": 0.5}},
        source="t",
    )
    assert expected.type == "score"
    assert expected.value == 1.0
    assert expected.probabilities == {"0": 0.5, "2": 0.5}

    zero = answer_from_named(
        _score_question(),
        {"type": "score", "score": 0, "probabilities": {"1": 1}},
        source="t",
    )
    assert zero.value == 0.0
    assert zero.probabilities == {"1": 1.0}


def test_empty_probabilities_rejected():
    for question, raw in (
        (_score_question(), {"type": "score"}),
        (_score_question(), {"type": "score", "probabilities": {}}),
        (_choice_question(), {"type": "choice"}),
    ):
        with pytest.raises(ValueError, match="empty probability mass from t"):
            answer_from_named(question, raw, source="t")

    with pytest.raises(ValueError):
        answer_from_named(
            _score_question(),
            {"type": "score", "score": "x", "probabilities": {"1": 1}},
            source="t",
        )


def test_boolean_question_default_and_partial_criteria():
    assert DEFAULT_FALSE_CRITERION == "no, the statement does not hold"
    assert DEFAULT_TRUE_CRITERION == "yes, the statement holds"

    question = DecisionQuestion(
        id="q", type="boolean", instructions="p", true_criterion="T"
    )
    rendered = question_to_named(question)
    assert rendered["type"] == "noul"
    assert rendered["criteria"] == {
        "false": DEFAULT_FALSE_CRITERION,
        "true": "T",
    }
