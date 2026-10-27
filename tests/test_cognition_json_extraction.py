"""Extraction correctness and downstream legality, with synthetic text only."""

import json
import pytest
from z0int.cognition.adapters.dialects import extract_json_object, PLAIN_JSON_DIALECT


@pytest.mark.parametrize(
    "value", ["{", "}", "{{", "}}", '"{', '"}', "\\{", "λ}", "\n{", "{a}b{", '\\"{']
)
@pytest.mark.parametrize(
    "prefix,suffix",
    [("I choose ", " now."), ("<tool_call>", "</tool_call>"), ("Answer:\n", "\nDone")],
)
def test_quoted_braces_are_not_object_boundaries(value, prefix, suffix):
    expected = {
        "action_id": "inspect",
        "arguments": {"pattern": value, "nested": [{"value": value}]},
    }
    assert extract_json_object(prefix + json.dumps(expected) + suffix) == expected


@pytest.mark.parametrize(
    "text",
    [
        "not json",
        '{"action_id": "inspect"',
        '{"action_id": "inspect",}',
        'prefix {"action_id": "inspect", "arguments": {"pattern": "unterminated }}} suffix',
    ],
)
def test_malformed_json_remains_unparseable(text):
    assert extract_json_object(text) is None


@pytest.mark.parametrize("pattern", ["{", "}", '\\"{', "\\\\{", "λ}"])
def test_dialect_preserves_arguments_instead_of_bare_id_fallback(pattern):
    expected = {"action_id": "inspect", "arguments": {"pattern": pattern}}
    parsed = PLAIN_JSON_DIALECT.parse(
        content="I choose " + json.dumps(expected) + " now.",
        tool_call=None,
        allowed_ids=["inspect"],
    )
    assert parsed is not None
    assert parsed.source == "json"
    assert parsed.arguments == expected["arguments"]


def test_json_extraction_does_not_expand_allowed_actions():
    text = 'I choose {"action_id": "forbidden", "arguments": {"pattern": "{"}}'
    assert (
        PLAIN_JSON_DIALECT.parse(content=text, tool_call=None, allowed_ids=["inspect"])
        is None
    )


def test_first_object_and_fence_priority_are_preserved():
    first = {"action_id": "first", "arguments": {"pattern": "{"}}
    second = {"action_id": "second"}
    assert (
        extract_json_object("prefix " + json.dumps(first) + json.dumps(second)) == first
    )
    fenced = "```json\n" + json.dumps(first) + "\n```"
    assert extract_json_object(json.dumps(second) + fenced) == first


def test_nested_object_in_malformed_outer_is_not_salvaged():
    assert (
        extract_json_object('{"broken": nope, "nested": {"action_id": "inspect"}}')
        is None
    )
