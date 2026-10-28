"""Contract tests for conservative, pure route_worker eligibility."""

import copy
import json
import math

import pytest

from z0int.delegation_eligibility import classify
from z0int.intelligence import REGISTRY
from z0int.intelligence_mcp import TOOLS


@pytest.fixture
def low_overhead_policy():
    # Explicit lab fixture: the live registry's observed overhead is much higher.
    return {
        "tiny_parent_tokens_max": 256,
        "default_overhead_ms": 1000,
        "observed_workflow_overhead_ms": 500,
    }


def candidate(task, **updates):
    return {
        "task": task,
        "parent_agent": "test-agent",
        "trace_id": "test-trace",
        "expected_parent_ms": 20000,
        **updates,
    }


def padded(prefix, length):
    assert len(prefix) <= length
    return prefix + "x" * (length - len(prefix))


def test_4000_character_summary_routes_with_explicit_policy(low_overhead_policy):
    request = candidate(padded("summarize this log: ", 4000))
    result = classify(request, low_overhead_policy)
    assert set(result) == {"decision", "reasons", "function", "route_worker_args"}
    assert result["decision"] == "ROUTE_WORKER"
    assert result["reasons"] == []
    assert result["function"] == "summarization"
    args = result["route_worker_args"]
    assert args == {
        "task": request["task"],
        "parent_agent": "test-agent",
        "trace_id": "test-trace",
        "function": "summarization",
        "expected_parent_tokens": 1000,
        "expected_parent_ms": 20000,
        "allow_remote": False,
    }


@pytest.mark.parametrize(
    ("flag", "reason"),
    [
        ("needs_tools", "NEEDS_TOOLS"),
        ("needs_session_files", "NEEDS_FILES"),
        ("needs_judgement", "NEEDS_JUDGEMENT"),
    ],
)
def test_parent_resources_block_same_large_summary(low_overhead_policy, flag, reason):
    result = classify(
        candidate(padded("summarize this log: ", 4000), **{flag: True}),
        low_overhead_policy,
    )
    assert result["decision"] == "PARENT_ONLY"
    assert result["reasons"] == [reason]
    assert result["function"] == "summarization"
    assert result["route_worker_args"] is None


def test_multiple_rejections_keep_rule_order(low_overhead_policy):
    result = classify(
        candidate(
            "implement feature X",
            needs_tools=True,
            needs_session_files=True,
            needs_judgement=True,
            expected_parent_ms=0,
        ),
        low_overhead_policy,
    )
    assert result == {
        "decision": "PARENT_ONLY",
        "reasons": [
            "NEEDS_TOOLS",
            "NEEDS_FILES",
            "NEEDS_JUDGEMENT",
            "TINY",
            "BELOW_OVERHEAD",
            "NO_FUNCTION",
        ],
        "function": None,
        "route_worker_args": None,
    }


def test_100_character_task_is_tiny(low_overhead_policy):
    result = classify(candidate(padded("summarize ", 100), expected_parent_ms=500), low_overhead_policy)
    assert result["decision"] == "PARENT_ONLY"
    assert result["reasons"] == ["TINY", "BELOW_OVERHEAD"]


def test_just_above_tiny_but_below_overhead(low_overhead_policy):
    policy = {**low_overhead_policy, "observed_workflow_overhead_ms": 10000}
    task = padded("summarize: ", 1030)  # ceil(1030 / 4) = 258
    result = classify(candidate(task, expected_parent_ms=5160), policy)
    assert result["decision"] == "PARENT_ONLY"
    assert result["reasons"] == ["BELOW_OVERHEAD"]


def test_inclusive_tiny_and_overhead_thresholds(low_overhead_policy):
    policy = {**low_overhead_policy, "default_overhead_ms": 0, "observed_workflow_overhead_ms": 0}
    assert classify(candidate(padded("summarize: ", 1024)), policy)["reasons"] == ["TINY"]
    assert classify(candidate(padded("summarize: ", 1025)), policy)["decision"] == "ROUTE_WORKER"

    policy["observed_workflow_overhead_ms"] = 5160
    assert classify(candidate(padded("summarize: ", 1030), expected_parent_ms=5160), policy)["reasons"] == ["BELOW_OVERHEAD"]
    policy["observed_workflow_overhead_ms"] = 5159
    assert classify(candidate(padded("summarize: ", 1030), expected_parent_ms=5160), policy)["decision"] == "ROUTE_WORKER"


def test_implement_feature_never_infers_coding_implementation(low_overhead_policy):
    result = classify(candidate(padded("implement feature X: ", 4000)), low_overhead_policy)
    assert result["decision"] == "PARENT_ONLY"
    assert result["reasons"] == ["NO_FUNCTION"]
    assert result["function"] is None


@pytest.mark.parametrize(
    ("task", "expected"),
    [
        ("summarize this text", "summarization"),
        ("TL;DR the provided text", "summarization"),
        ("extract JSON fields", "cheap_bounded_worker"),
        ("classify this feedback", "cheap_bounded_worker"),
        ("label the rows", "cheap_bounded_worker"),
        ("rewrite the supplied passage", "cheap_bounded_worker"),
        ("convert the supplied text", "cheap_bounded_worker"),
        ("format the output", "cheap_bounded_worker"),
        ("review this diff", "code_review"),
        ("review the patch", "code_review"),
        ("answer a question over supplied text", "research"),
        ("answer the question using the following passage", "research"),
    ],
)
def test_only_supported_bounded_functions_are_inferred(task, expected):
    policy = {
        "tiny_parent_tokens_max": 0,
        "default_overhead_ms": 0,
        "observed_workflow_overhead_ms": 0,
    }
    result = classify(candidate(task), policy)
    assert result["decision"] == "ROUTE_WORKER"
    assert result["function"] == expected
    assert result["route_worker_args"]["function"] == expected
    registered = {entry["function"] for entry in json.loads(REGISTRY.read_text())["entries"]}
    assert expected in registered
    assert expected != "coding_implementation"


def test_inference_uses_task_not_context():
    policy = {"tiny_parent_tokens_max": 0, "default_overhead_ms": 0, "observed_workflow_overhead_ms": 0}
    result = classify(candidate("implement feature X", context="summarize a file"), policy)
    assert result["decision"] == "PARENT_ONLY"
    assert result["reasons"] == ["NO_FUNCTION"]


@pytest.mark.parametrize("private", [False, True])
def test_remote_requires_explicit_authorization_and_respects_privacy(low_overhead_policy, private):
    request = candidate(padded("summarize this log: ", 4000), contains_private=private)
    assert classify(request, low_overhead_policy)["route_worker_args"]["allow_remote"] is False
    result = classify(request, low_overhead_policy, allow_remote=True)
    assert result["decision"] == "ROUTE_WORKER"
    assert result["route_worker_args"]["allow_remote"] is (not private)


def test_remote_denied_when_privacy_is_unknown(low_overhead_policy):
    request = candidate(padded("summarize: ", 4000))
    result = classify(request, low_overhead_policy, allow_remote=True)
    assert result["route_worker_args"]["allow_remote"] is False


def test_unknown_latency_cannot_produce_worker_args(low_overhead_policy):
    request = candidate(padded("summarize: ", 4000))
    del request["expected_parent_ms"]
    result = classify(request, low_overhead_policy)
    assert result["reasons"] == ["UNKNOWN_PARENT_LATENCY"]
    assert result["route_worker_args"] is None


def test_json_encoding_limit_counts_unicode(low_overhead_policy):
    request = candidate("summarize: " + "é" * 7000)
    assert classify(request, low_overhead_policy)["reasons"] == ["TOO_LARGE"]


def test_context_is_counted_and_only_included_when_present(low_overhead_policy):
    request = candidate("extract fields", context="x" * 4000)
    result = classify(request, low_overhead_policy)
    assert result["decision"] == "ROUTE_WORKER"
    args = result["route_worker_args"]
    assert args["context"] == request["context"]
    assert args["expected_parent_tokens"] == math.ceil((len(request["task"]) + 4000) / 4)
    assert "context" not in classify(
        candidate(padded("extract fields: ", 4000)), low_overhead_policy
    )["route_worker_args"]


def test_route_worker_args_use_live_mcp_schema(low_overhead_policy):
    tool = next(tool for tool in TOOLS if tool["name"] == "route_worker")
    schema = tool["inputSchema"]
    result = classify(
        candidate("extract the entries", context="x" * 4000), low_overhead_policy
    )
    args = result["route_worker_args"]
    assert set(args) <= set(schema["properties"])
    assert set(schema["required"]) <= set(args)
    assert set(args) == {
        "task",
        "context",
        "parent_agent",
        "trace_id",
        "function",
        "expected_parent_tokens",
        "expected_parent_ms",
        "allow_remote",
    }


@pytest.mark.parametrize(
    "updates",
    [{"task": padded("summarize: ", 24001)}, {"context": "x" * 24001}],
)
def test_worker_request_size_boundaries_block_oversize(low_overhead_policy, updates):
    request = candidate(padded("summarize: ", 4000))
    request.update(updates)
    result = classify(request, low_overhead_policy)
    assert result["decision"] == "PARENT_ONLY"
    assert "TOO_LARGE" in result["reasons"]
    assert result["route_worker_args"] is None


def test_combined_json_limit_blocks_individually_valid_fields(low_overhead_policy):
    request = candidate(padded("summarize: ", 24000), context="x" * 24000)
    assert classify(request, low_overhead_policy)["reasons"] == ["TOO_LARGE"]


def test_caller_latency_controls_overhead_guard(low_overhead_policy):
    task = padded("summarize: ", 4000)
    result = classify(candidate(task, expected_parent_ms=1000), low_overhead_policy)
    assert result["decision"] == "PARENT_ONLY"
    assert result["reasons"] == ["BELOW_OVERHEAD"]


def test_default_policy_comes_from_canonical_registry(low_overhead_policy):
    request = candidate(padded("summarize this log: ", 4000))
    manifest_policy = json.loads(REGISTRY.read_text())["delegation_policy"]
    assert classify(request) == classify(request, manifest_policy)
    observed = max(
        manifest_policy["default_overhead_ms"],
        manifest_policy["observed_workflow_overhead_ms"],
    )
    if 20000 <= observed:
        assert "BELOW_OVERHEAD" in classify(request)["reasons"]


@pytest.mark.parametrize(
    "invalid",
    [
        {"task": ""},
        {"task": 123},
        {"task": "summarize", "unexpected": True},
        {"task": "summarize", "allow_remote": True},
        {"task": "summarize", "context": None},
        {"task": "summarize", "needs_tools": 1},
        {"task": "summarize", "needs_session_files": None},
        {"task": "summarize", "needs_judgement": "false"},
        {"task": "summarize", "contains_private": "no"},
        {"task": "summarize", "expected_parent_ms": float("inf")},
        {"task": "summarize", "trace_id": None},
        {"task": "summarize", "parent_agent": ""},
    ],
)
def test_invalid_subtask_fields_raise_value_error(invalid, low_overhead_policy):
    with pytest.raises(ValueError):
        classify(invalid, low_overhead_policy)


@pytest.mark.parametrize(
    "policy",
    [
        [],
        {"tiny_parent_tokens_max": -1},
        {"tiny_parent_tokens_max": True},
        {"default_overhead_ms": float("nan")},
        {"observed_workflow_overhead_ms": -2},
    ],
)
def test_invalid_policy_raises_value_error(policy):
    with pytest.raises(ValueError):
        classify(candidate("summarize"), policy)


def test_remote_keyword_must_be_boolean(low_overhead_policy):
    with pytest.raises(ValueError):
        classify(candidate("summarize"), low_overhead_policy, allow_remote="yes")


def test_only_task_is_required_to_reject_a_tiny_candidate(low_overhead_policy):
    result = classify({"task": "summarize"}, low_overhead_policy)
    assert result["decision"] == "PARENT_ONLY"
    assert result["route_worker_args"] is None
    assert "UNKNOWN_PARENT_LATENCY" in result["reasons"]


def test_eligible_task_requires_real_dispatch_identity(low_overhead_policy):
    request = {"task": padded("summarize: ", 4000), "expected_parent_ms": 20000}
    with pytest.raises(ValueError, match="parent_agent and trace_id"):
        classify(request, low_overhead_policy)


def test_classifier_is_deterministic_and_does_not_mutate_inputs(low_overhead_policy):
    task = candidate(padded("extract fields: ", 4000), context="input data")
    original_task, original_policy = copy.deepcopy(task), copy.deepcopy(low_overhead_policy)
    first = classify(task, low_overhead_policy)
    assert first == classify(task, low_overhead_policy)
    assert task == original_task
    assert low_overhead_policy == original_policy
