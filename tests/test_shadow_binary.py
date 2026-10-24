"""No-network contracts for hard-capped, goal-aware yes/no logit scoring."""
from dataclasses import replace
import json
import math

import pytest

from z0int.cognition.actions import ActionCandidate, LegalActionSet
from z0int.cognition.adapters.transport import ChatOutcome, TransportError
from z0int.cognition.shadow_binary import (
    YES_NO_GRAMMAR, candidate_cap, run_goal_binary, score_choices, yes_no_logprobs,
)


def legal_tools(*names):
    return LegalActionSet(
        legal=tuple(ActionCandidate(action_id=name, kind="tool", tool=name,
                    family="omp.tool", description=f"Inspect {name} data") for name in names),
        blocked=("SECRET_BLOCKED",), eliminated=(), deterministic_solution="SECRET_GOLD",
        deterministic_reason="SECRET_REASON", budget_units=200, spent_units=0,
        graph_digest="graph", facts={"tool_name": "SECRET_CURRENT_TARGET"})


def goal_payload():
    return {"goal_context": {
        "latest_user_message": "Find the code that parses the config.",
        "last_assistant_sentence": "I will inspect the parser.",
        "previous_tools": [{"tool": "glob", "intent": "Locate parser files"}],
        "sources": {"latest_user_message": "sessionManager.getBranch",
                    "last_assistant_sentence": "message_update"},
    }, "facts": {"tool_name": "SECRET_CURRENT_TARGET", "input": {"i": "SECRET_INTENT"}},
       "state": "SECRET_CURRENT_STATE", "objective": "SECRET_OBJECTIVE"}


def raw_probs(yes=-0.2, no=-2.0):
    token, value = ("yes", yes) if yes > no else ("no", no)
    return {"choices": [{"message": {"content": token}, "logprobs": {"content": [{
        "token": token, "logprob": value, "top_logprobs": [
            {"token": "yes", "logprob": yes}, {"token": "no", "logprob": no},
        ],
    }]}}]}


def outcome(yes=-0.2, no=-2.0, raw=None, content=None):
    return ChatOutcome(content=content if content is not None else ("yes" if yes > no else "no"),
        tool_call=None, usage={"prompt_tokens": 123, "completion_tokens": 1},
        timings={}, raw=raw if raw is not None else raw_probs(yes, no), latency_ms=2)


def fake_chat(scores=None):
    calls = []

    def chat(messages, **kwargs):
        calls.append((messages, kwargs))
        candidate = json.loads(messages[1]["content"])["candidate"]["id"]
        return outcome(*(scores or {}).get(candidate, (-2.0, -0.2)))

    return chat, calls


def test_goal_binary_uses_real_context_and_history_shortlist_without_current_gold():
    legal = legal_tools("read", "grep", "glob", "edit", "bash")
    chat, calls = fake_chat({"grep": (-0.2, -2.0), "glob": (-0.6, -1.0)})
    row = run_goal_binary(goal_payload(), legal, chat=chat, shortlist=["grep", "glob", "read"])
    assert row["selected_action"] == "grep"
    assert row["arm"] == row["label"] == "goal-binary"
    assert row["family"] == "omp.tool"
    assert row["model_candidate_ids"] == ["grep", "glob", "read"]
    assert len(calls) == 3
    assert row["confidence"] == pytest.approx(1 / (1 + math.exp(-1.8)))
    assert row["diagnostics"]["top_margin"] == pytest.approx(1.4)
    assert row["diagnostics"]["context_sources"]["last_assistant_sentence"] == "message_update"
    assert row["error"] is None and row["parse_error"] is None
    for messages, kwargs in calls:
        assert "Find the code" in messages[1]["content"]
        assert "Locate parser files" in messages[1]["content"]
        assert "SECRET" not in json.dumps(messages)
        assert kwargs["logprobs"] is True
        assert kwargs["top_logprobs"] == 20
        assert kwargs["max_tokens"] == 1
        assert kwargs["extra_body"]["grammar"] == YES_NO_GRAMMAR
        assert kwargs["extra_body"]["chat_template_kwargs"] == {"enable_thinking": False}
    assert len(legal.ids()) == 5  # No change to full scoring denominator.
    assert legal.facts["tool_name"] == "SECRET_CURRENT_TARGET"


def test_current_target_changes_cannot_change_any_binary_prompt():
    chat, calls = fake_chat({"read": (-0.2, -2.0)})
    payload, legal = goal_payload(), legal_tools("read", "grep", "edit")
    run_goal_binary(payload, legal, chat=chat)
    first = [messages for messages, _ in calls]
    calls.clear()
    payload.update(facts={"tool_name": "edit", "input": {"i": "LEAK"}}, state="LEAK", objective="LEAK")
    run_goal_binary(payload, replace(legal, facts={"tool_name": "edit"}), chat=chat)
    assert first == [messages for messages, _ in calls]
    assert "LEAK" not in json.dumps(first)


@pytest.mark.parametrize("size", [0.1, 0.6, 1.0])
def test_sub_billion_hard_cap_is_three_even_for_119_tools(size):
    chat, calls = fake_chat()
    names = [f"tool_{index}" for index in range(119)]
    row = run_goal_binary(goal_payload(), legal_tools(*names), chat=chat,
        shortlist=list(reversed(names)), model_parameters_b=size)
    assert row["model_candidate_ids"] == names[-1:-4:-1]
    assert len(calls) == 3
    assert row["diagnostics"]["option_cap"] == 3
    assert len(legal_tools(*names).ids()) == 119


def test_explicit_17b_lane_has_five_option_cap_and_unknown_sizes_fail_closed():
    assert candidate_cap(1.7) == 5
    with pytest.raises(ValueError):
        candidate_cap(1.5)
    choices = [{"id": str(index)} for index in range(5)]
    chat, calls = fake_chat()
    row = score_choices({}, choices, chat, model_parameters_b=1.7)
    assert len(calls) == 5
    assert row["diagnostics"]["option_cap"] == 5


def test_generic_atomic_scorer_rejects_four_choices_for_06b_without_rpc():
    chat, calls = fake_chat()
    row = score_choices({}, [{"id": str(index)} for index in range(4)], chat)
    assert row["error"] == "option_cap_exceeded"
    assert row["parse_error"] is None
    assert calls == []


def test_atomic_json_context_removes_choices_and_advertises_one_candidate_per_rpc():
    choices = [{"id": "choice-A", "description": "Read the file"},
               {"id": "choice-B", "description": "Edit the file"}]
    chat, calls = fake_chat({"choice-A": (-0.2, -2.0)})
    row = score_choices(json.dumps({"state": "Inspect config", "choices": choices}), choices, chat)
    assert row["selected_action"] == "choice-A"
    for messages, _ in calls:
        body = json.loads(messages[1]["content"])
        assert body["context"] == {"state": "Inspect config"}
        assert set(body) == {"context", "candidate"}
        assert "choice-A" not in messages[1]["content"] or "choice-B" not in messages[1]["content"]
    assert row["candidate_scores"][0]["log_odds"] == pytest.approx(1.8)


def test_yes_no_odds_require_both_real_logprobs():
    assert yes_no_logprobs(raw_probs()) == (-0.2, -2.0)
    # The sampled token need not appear in top_logprobs, but its own logprob is real evidence.
    raw = raw_probs()
    raw["choices"][0]["logprobs"]["content"][0]["top_logprobs"] = [{"token": "no", "logprob": -2.0}]
    assert yes_no_logprobs(raw) == (-0.2, -2.0)
    raw["choices"][0]["logprobs"]["content"][0]["top_logprobs"] = []
    assert yes_no_logprobs(raw) is None
    assert yes_no_logprobs({"choices": [{"message": {"content": "yes"}}]}) is None


@pytest.mark.parametrize("raw", [{}, {"choices": [{"logprobs": None}]},
    {"choices": [{"logprobs": {"content": [{"token": "yes", "logprob": -0.1,
                                           "top_logprobs": []}]}}]}])
def test_missing_raw_logprobs_abstain_and_retain_raw_census_evidence(raw):
    row = score_choices({}, [{"id": "read"}], lambda *args, **kwargs: outcome(raw=raw))
    assert row["selected_action"] is None
    assert row["confidence"] is None
    assert row["abstained"] is True
    assert row["error"] == "missing_yes_no_logprobs"
    assert row["parse_error"] is None
    assert row["candidate_scores"] == []
    assert row["diagnostics"]["calls"][0]["raw_response"] is raw


def test_malformed_probability_evidence_is_not_repaired_with_floors():
    for bad in [float("nan"), float("inf"), True, 0.1, None]:
        raw = raw_probs()
        raw["choices"][0]["logprobs"]["content"][0]["top_logprobs"][1]["logprob"] = bad
        assert yes_no_logprobs(raw) is None


def test_small_top_margin_abstains_instead_of_selecting_a_wrong_answer():
    chat, _ = fake_chat({"a": (-0.2, -2.0), "b": (-0.3, -2.0)})
    row = score_choices({}, [{"id": "a"}, {"id": "b"}], chat, min_margin=0.25)
    assert row["selected_action"] is None
    assert row["abstained"] is True
    assert row["error"] is None and row["parse_error"] is None
    assert row["diagnostics"]["abstention_reason"] == "small_top_margin"
    assert row["diagnostics"]["top_margin"] == pytest.approx(0.1)


def test_no_positive_candidate_abstains_and_single_positive_candidate_selects():
    chat, _ = fake_chat()
    row = score_choices({}, [{"id": "a"}], chat)
    assert row["diagnostics"]["abstention_reason"] == "no_positive_candidate"
    chat, _ = fake_chat({"a": (-0.2, -2.0)})
    row = score_choices({}, [{"id": "a"}], chat)
    assert row["selected_action"] == "a"
    assert row["diagnostics"]["top_margin"] is None


def test_transport_failure_is_not_parse_error_and_has_per_rpc_error():
    def broken(*args, **kwargs):
        raise TransportError("HTTP 503 unavailable")

    row = score_choices({}, [{"id": "a"}], broken)
    assert row["error"] == "TransportError: HTTP 503 unavailable"
    assert row["parse_error"] is None
    assert row["diagnostics"]["calls"][0]["error"] == row["error"]


def test_non_binary_response_is_a_parse_error_even_with_probability_metadata():
    row = score_choices({}, [{"id": "a"}], lambda *args, **kwargs: outcome(content="perhaps"))
    assert row["parse_error"] == "non_binary_response"
    assert row["error"] is None
    assert row["selected_action"] is None


def test_utf8_prompt_bound_includes_control_and_completion_reserves():
    payload = goal_payload()
    payload["goal_context"]["latest_user_message"] = "界" * 1000
    payload["goal_context"]["last_assistant_sentence"] = "界" * 1000
    payload["goal_context"]["previous_tools"] = [
        {"tool": "read", "intent": "界" * 1000} for _ in range(8)]
    chat, calls = fake_chat({"read": (-0.2, -2.0)})
    row = run_goal_binary(payload, legal_tools("read"), chat=chat)
    assert row["error"] is None
    context = json.loads(calls[0][0][1]["content"])["context"]
    assert len(context["latest_user_message"].encode()) <= 600
    assert len(context["last_assistant_sentence"].encode()) <= 300
    assert len(context["previous_tools"]) == 5
    for call in row["diagnostics"]["calls"]:
        assert call["serialized_prompt_bytes"] + call["control_reserve"] + call["completion_reserve"] <= 4096
        assert call["prompt_tokens"] == 123 and call["completion_tokens"] == 1
    chat, calls = fake_chat()
    row = run_goal_binary(payload, legal_tools("read"), chat=chat, n_ctx=600)
    assert row["error"] == "context_overflow:prompt_budget_exhausted"
    assert row["parse_error"] is None
    assert calls == []


def test_missing_context_does_not_fall_back_to_state_or_current_objective():
    chat, calls = fake_chat()
    row = run_goal_binary({"objective": "SECRET", "state": "SECRET"}, legal_tools("read"), chat=chat)
    assert row["diagnostics"]["missing_fields"] == ["latest_user_message", "last_assistant_sentence"]
    assert row["diagnostics"]["context_sources"] == {
        "latest_user_message": None, "last_assistant_sentence": None}
    assert "SECRET" not in json.dumps(calls)


def test_existing_backend_transport_is_reused_without_a_second_router():
    chat, calls = fake_chat({"read": (-0.2, -2.0)})

    class Backend:
        backend_id = "existing"
        config = type("Config", (), {"model": "qwen3-0.6b-q8"})()
        _transport = type("Transport", (), {"chat": staticmethod(chat)})()

    row = run_goal_binary(goal_payload(), legal_tools("read"), Backend())
    assert row["selected_action"] == "read"
    assert row["backend"] == "existing"
    assert row["model"] == "qwen3-0.6b-q8"
    assert len(calls) == 1


def test_retrieved_examples_are_allowlisted_and_bounded_to_three_prior_transitions():
    payload = goal_payload()
    payload["goal_context"]["examples"] = [
        {"previous_tools": ["read", "grep"], "next_tool": "edit",
         "facts": "SECRET_CURRENT_FACT", "arguments": {"i": "SECRET_ARGS"}}
        for _ in range(9)
    ]
    chat, calls = fake_chat({"read": (-0.2, -2.0)})
    row = run_goal_binary(payload, legal_tools("read"), chat=chat)
    assert row["error"] is None
    body = json.loads(calls[0][0][1]["content"])
    examples = body["context"]["examples"]
    assert examples == [{"previous_tools": ["read", "grep"], "next_tool": "edit"}] * 3
    assert "SECRET" not in json.dumps(body)
    assert len(payload["goal_context"]["examples"]) == 9  # Read-only caller data.


def test_preflight_overflow_is_not_counted_as_a_sent_rpc():
    chat, calls = fake_chat()
    row = score_choices({"state": "x" * 9000}, [{"id": "read"}], chat)
    assert row["error"] == "context_overflow:prompt_budget_exhausted"
    assert row["diagnostics"]["calls"][0]["rpc_sent"] is False
    assert calls == []


def test_absent_capacity_policy_cannot_raise_default_via_identity_or_prompt(tmp_path):
    chat, calls = fake_chat()
    identity = {"model": "qwen3-0.6b-q8", "quant": "q8", "engine": "llama.cpp",
                "backend": "fixture", "device": "private-fixture"}
    row = score_choices({"max_options": 99, "policy": {"allowed_k": 99}},
        [{"id": str(index)} for index in range(4)], chat,
        policy_identity=identity, policy_home=tmp_path)
    assert row["diagnostics"]["option_cap"] == 3
    assert row["error"] == "option_cap_exceeded"
    assert calls == []


def test_verified_capacity_applies_to_shortlist_and_each_rpc(tmp_path, monkeypatch):
    from z0int.cognition import choice_capacity

    identity = {"model": "qwen3-0.6b-q8", "quant": "q8", "engine": "llama.cpp",
                "backend": "fixture", "device": "private-fixture"}
    evidence_calls = []

    def verified_cap(size, scope, home):
        evidence_calls.append((size, scope, home))
        return 2

    monkeypatch.setattr(choice_capacity, "effective_cap", verified_cap)
    chat, calls = fake_chat({"read": (-0.2, -2.0)})
    row = run_goal_binary(goal_payload(), legal_tools("read", "grep", "edit", "bash"),
        chat=chat, shortlist=["read", "grep", "edit"], policy_identity=identity, policy_home=tmp_path)
    assert row["model_candidate_ids"] == ["read", "grep"]
    assert row["diagnostics"]["option_cap"] == 2
    assert len(calls) == 2
    assert evidence_calls == [(0.6, identity, tmp_path)] * 2
