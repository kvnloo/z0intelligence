"""Adapter tests: model-specific parsing is isolated, and failures fail open."""

from __future__ import annotations

import json

import pytest

from z0int.cognition.actions import ActionCandidate, ActionGraph, compile_actions
from z0int.cognition.adapters.bounded import (
    DecisionBackendToolAdapter,
    JevBoundedToolBackend,
)
from z0int.cognition.adapters.dialects import (
    DIALECTS,
    HAMMER_DIALECT,
    PLAIN_JSON_DIALECT,
    ToolDialect,
    dialect_for,
    extract_json_object,
    render_action_tools,
)
from z0int.cognition.adapters.local_slm import (
    LocalSLMBackend,
    ToolDecision,
    ToolDecisionRequest,
)
from z0int.cognition.adapters.transport import ChatOutcome, ServerConfig, TransportError


# --- dialects -----------------------------------------------------------


def test_extract_json_object_handles_fences_and_prose():
    assert extract_json_object('```json\n{"action_id": "a"}\n```') == {"action_id": "a"}
    assert extract_json_object('I choose {"action_id": "b"} now.') == {"action_id": "b"}
    assert extract_json_object("no json here") is None
    assert extract_json_object("") is None


def test_dialect_for_resolves_known_parsers():
    assert dialect_for("hermes").name == "hermes"
    assert dialect_for(None, "qwen3_coder").name == "qwen"
    assert dialect_for("functiongemma").name == "functiongemma"
    assert dialect_for("nonsense").name == "hermes"  # safe default
    assert set(DIALECTS) == {
        "hermes",
        "qwen",
        "nemotron",
        "hammer",
        "functiongemma",
        "plain_json",
    }


def test_render_action_tools_emits_a_schema_per_legal_action():
    legal = _legal()
    tools = render_action_tools(legal.legal)
    assert [t["function"]["name"] for t in tools] == list(legal.ids())
    for tool in tools:
        assert tool["type"] == "function"
        assert "parameters" in tool["function"]


def test_hermes_dialect_parses_a_native_tool_call():
    parsed = HAMMER_DIALECT.parse(
        content="",
        tool_call={"function": {"name": "fs.read", "arguments": '{"path": "/tmp/x"}'}},
        allowed_ids=["fs.read", "fs.write"],
    )
    assert parsed is not None
    assert parsed.tool == "fs.read"
    assert parsed.arguments == {"path": "/tmp/x"}


def test_dialect_parses_json_content_and_rejects_out_of_set_ids():
    parsed = HAMMER_DIALECT.parse(
        content='{"action_id": "fs.read", "confidence": 0.8}',
        tool_call=None,
        allowed_ids=["fs.read"],
    )
    assert parsed is not None and parsed.confidence == 0.8
    assert (
        HAMMER_DIALECT.parse(
            content='{"action_id": "fs.rm"}', tool_call=None, allowed_ids=["fs.read"]
        )
        is None
    )


def test_plain_json_dialect_sends_no_tools():
    legal = _legal()
    assert PLAIN_JSON_DIALECT.tools_payload(legal.legal) is None
    rf = PLAIN_JSON_DIALECT.response_format({"type": "object"})
    assert rf is not None and rf["type"] == "json_schema"


def test_functiongemma_text_syntax_is_parsed_and_first_call_wins():
    """Regression: a real FunctionGemma run emitted three calls; picking the
    longest id silently chose the last/wrong one."""
    from z0int.cognition.adapters.dialects import FUNCTIONGEMMA_DIALECT

    content = (
        "<start_function_call>call:fs.read{path:<escape>/etc/hostname<escape>}"
        "<end_function_call>"
        "<start_function_call>call:fs.write{}<end_function_call>"
        "<start_function_call>call:shell.run{}<end_function_call>"
    )
    parsed = FUNCTIONGEMMA_DIALECT.parse(
        content=content, tool_call=None, allowed_ids=["fs.read", "fs.write", "shell.run"]
    )
    assert parsed is not None
    assert parsed.tool == "fs.read"
    assert parsed.arguments == {"path": "/etc/hostname"}
    assert parsed.source == "text:functiongemma"


def test_hermes_and_qwen_xml_text_syntax_round_trip():
    from z0int.cognition.adapters.dialects import HERMES_DIALECT, QWEN_DIALECT

    hermes = HERMES_DIALECT.parse(
        content='<tool_call>\n{"name": "fs.write", "arguments": {"path": "/tmp/a"}}\n</tool_call>',
        tool_call=None,
        allowed_ids=["fs.read", "fs.write"],
    )
    assert hermes is not None and hermes.tool == "fs.write"

    qwen = QWEN_DIALECT.parse(
        content="<tool_call>\n<function=fs.read>\n<parameter=path>\n/tmp/a\n</parameter>\n"
        "</function>\n</tool_call>",
        tool_call=None,
        allowed_ids=["fs.read", "fs.write"],
    )
    assert qwen is not None and qwen.tool == "fs.read"


def test_hammer_json_array_syntax_is_parsed():
    parsed = HAMMER_DIALECT.parse(
        content='```\n[{"name": "fs.write", "arguments": {"path": "/tmp/b"}}]\n```',
        tool_call=None,
        allowed_ids=["fs.read", "fs.write"],
    )
    assert parsed is not None and parsed.tool == "fs.write"
    assert parsed.arguments == {"path": "/tmp/b"}


def test_bare_text_fallback_prefers_the_earliest_mention():
    parsed = HAMMER_DIALECT.parse(
        content="First fs.read the file, then shell.run the formatter.",
        tool_call=None,
        allowed_ids=["fs.read", "shell.run"],
    )
    assert parsed is not None and parsed.tool == "fs.read"


# --- local SLM backend --------------------------------------------------


class _FakeTransport:
    def __init__(self, outcome=None, error=None):
        self._outcome = outcome
        self._error = error
        self.calls = []

    def health(self):
        return {"ready": self._error is None, "detail": "fake"}

    def chat(self, messages, **kwargs):
        self.calls.append({"messages": messages, **kwargs})
        if self._error:
            raise self._error
        return self._outcome


def _outcome(content="", tool_call=None, latency_ms=12.0, usage=None, timings=None):
    return ChatOutcome(
        content=content,
        tool_call=tool_call,
        usage=usage or {"prompt_tokens": 100, "completion_tokens": 8},
        timings=timings or {"prompt_ms": 30.0, "predicted_n": 8, "predicted_ms": 40.0},
        raw={},
        latency_ms=latency_ms,
        ttft_ms=30.0,
    )


def _legal():
    graph = ActionGraph(
        actions=(
            ActionCandidate(action_id="fs.read", kind="tool", description="read", tool="fs.read"),
            ActionCandidate(action_id="fs.write", kind="tool", description="write", tool="fs.write",
                            risk_class="write"),
        )
    )
    return compile_actions(
        graph=graph, granted_capabilities=(), authority=("read", "write"), budget_units=5
    )


def _backend(transport, dialect=None):
    return LocalSLMBackend(
        backend_id="test_slm",
        config=ServerConfig(base_url="http://127.0.0.1:1", model="m", revision="r1"),
        dialect=dialect,
        transport=transport,
    )


def test_backend_selects_a_legal_action_and_reports_telemetry():
    transport = _FakeTransport(
        _outcome(tool_call={"function": {"name": "fs.read", "arguments": "{}"}})
    )
    decision = _backend(transport).decide(ToolDecisionRequest(state="s", legal=_legal()))
    assert decision.selected_action == "fs.read"
    assert decision.abstained is False
    assert decision.invalid_call is False
    assert decision.candidate_action_count == 2
    assert decision.model == "m" and decision.revision == "r1"
    assert decision.prompt_tokens == 100 and decision.completion_tokens == 8
    assert decision.decode_tok_s == pytest.approx(200.0)


def test_backend_never_readmits_a_filtered_action():
    transport = _FakeTransport(
        _outcome(tool_call={"function": {"name": "fs.rm", "arguments": "{}"}})
    )
    decision = _backend(transport).decide(ToolDecisionRequest(state="s", legal=_legal()))
    assert decision.selected_action is None
    assert decision.abstained is True
    assert decision.invalid_call is True
    assert "fs.rm" in (decision.parse_error or "")


def test_backend_fails_open_on_transport_error():
    transport = _FakeTransport(error=TransportError("connection refused"))
    decision = _backend(transport).decide(ToolDecisionRequest(state="s", legal=_legal()))
    assert decision.abstained is True
    assert decision.selected_action is None
    assert decision.invalid_call is False
    assert "transport_error" in (decision.parse_error or "")


def test_backend_fails_open_on_unparseable_output():
    transport = _FakeTransport(_outcome(content="I am not sure, let me think."))
    decision = _backend(transport).decide(ToolDecisionRequest(state="s", legal=_legal()))
    assert decision.abstained is True
    assert decision.selected_action is None
    assert decision.parse_error == "unparseable_response"


def test_backend_abstains_when_nothing_is_legal_and_makes_no_call():
    graph = ActionGraph(
        actions=(
            ActionCandidate(action_id="fs.rm", kind="tool", description="rm", tool="fs.rm",
                            risk_class="destructive"),
        )
    )
    legal = compile_actions(
        graph=graph, granted_capabilities=(), authority=("read",), budget_units=5
    )
    transport = _FakeTransport(_outcome())
    decision = _backend(transport).decide(ToolDecisionRequest(state="s", legal=legal))
    assert decision.abstained is True
    assert decision.parse_error == "no legal actions"
    assert transport.calls == []


def test_backend_only_advertises_the_legal_action_ids():
    transport = _FakeTransport(_outcome(content='{"action_id": "fs.read"}'))
    _backend(transport).decide(ToolDecisionRequest(state="s", legal=_legal()))
    sent = transport.calls[0]
    names = [t["function"]["name"] for t in sent["tools"]]
    assert names == ["fs.read", "fs.write"]
    assert "fs.rm" not in json.dumps(sent["tools"])


# --- JEV -> tool surface ------------------------------------------------


class _StubDecisionBackend:
    """Stands in for a JEV/OpenJev/NanoJev DecisionBackend."""

    def __init__(self, value, probabilities=None, supports_choice=True, max_options=255):
        from z0int.backends.base import BackendCapabilities

        self._value = value
        self._probabilities = probabilities
        self._caps = BackendCapabilities(
            id="stub_jev",
            local=True,
            trainable=False,
            supports_boolean=True,
            supports_choice=supports_choice,
            supports_score=False,
            max_choice_options=max_options,
            max_score_levels=0,
        )
        self.requests = []

    @property
    def capabilities(self):
        return self._caps

    def health(self, *, load=False):
        from z0int.backends.base import BackendHealth

        return BackendHealth(id="stub_jev", configured=True, ready=True, detail="stub")

    def evaluate(self, request):
        from z0int.backends.base import DecisionAnswer, DecisionResult

        self.requests.append(request)
        if self._value is None:
            return DecisionResult(
                backend="stub_jev", model="stub", revision="r", answers=(), latency_ms=1.0
            )
        q = request.questions[0]
        ids = [o.id for o in q.options]
        if self._probabilities is not None:
            probs = dict(self._probabilities)
        elif self._value in ids:
            probs = {i: (1.0 if i == self._value else 0.0) for i in ids}
        else:
            # Backend answered something outside the offered set: keep the
            # distribution valid so the *adapter* is what rejects it.
            probs = {i: 1.0 / len(ids) for i in ids}
        answer = DecisionAnswer(
            question_id=q.id,
            type=q.type,
            probabilities=probs,
            value=self._value,
            confidence=0.9,
        )
        return DecisionResult(
            backend="stub_jev", model="stub", revision="r", answers=(answer,), latency_ms=1.0
        )


def test_jev_adapter_presents_the_same_legal_set():
    stub = _StubDecisionBackend("fs.read")
    decision = JevBoundedToolBackend(stub).decide(
        ToolDecisionRequest(state="s", legal=_legal())
    )
    assert decision.selected_action == "fs.read"
    assert decision.abstained is False
    assert [o.id for o in stub.requests[0].questions[0].options] == ["fs.read", "fs.write"]


def test_jev_adapter_rejects_an_out_of_set_answer():
    stub = _StubDecisionBackend("fs.rm")
    decision = JevBoundedToolBackend(stub).decide(
        ToolDecisionRequest(state="s", legal=_legal())
    )
    assert decision.selected_action is None
    assert decision.invalid_call is True


def test_jev_adapter_abstains_when_the_scorer_has_too_many_options():
    stub = _StubDecisionBackend("fs.read", max_options=1)
    decision = JevBoundedToolBackend(stub).decide(
        ToolDecisionRequest(state="s", legal=_legal())
    )
    assert decision.abstained is True
    assert "exceed" in (decision.parse_error or "")
    assert stub.requests == []


def test_jev_adapter_passes_through_an_empty_answer_as_abstention():
    stub = _StubDecisionBackend(None)
    decision = JevBoundedToolBackend(stub).decide(
        ToolDecisionRequest(state="s", legal=_legal())
    )
    assert decision.abstained is True


# --- ToolDecisionBackend -> legacy DecisionBackend ----------------------


def test_a_local_slm_can_be_evaluated_by_the_existing_backend_contract():
    transport = _FakeTransport(_outcome(content='{"action_id": "true"}'))
    adapter = DecisionBackendToolAdapter(_backend(transport))
    assert adapter.health().ready is True
    assert adapter.capabilities.supports_choice is True
    assert adapter.capabilities.autoregressive_decode is True

    from z0int.backends.base import DecisionQuestion, DecisionRequest

    request = DecisionRequest(
        state="should we continue?",
        questions=(
            DecisionQuestion(id="q", type="boolean", instructions="continue?",
                             false_criterion="stop", true_criterion="continue"),
        ),
    )
    result = adapter.evaluate(request)
    assert result.backend == "test_slm"
    assert result.answers[0].value is True
    assert result.diagnostics["candidate_action_count"] == 2
