"""Adapters that present the z0int backend contract through the cognition surface.

``JevBoundedToolBackend`` lets the existing JEV/OpenJev/NanoJev
:class:`~z0int.backends.base.DecisionBackend` implementations participate in the
same cascade as the generative SLMs: they receive the identical already-legal
action set and return the identical :class:`ToolDecision`.

``DecisionBackendToolAdapter`` runs the other direction — any
:class:`ToolDecisionBackend` (a local SLM) can be evaluated by the existing
``z0int backends`` CLI and ``backends/bench`` runner as a normal DecisionBackend.
Both directions exist so the harness never grows a second brain.
"""

from __future__ import annotations

from typing import Any, Mapping, Sequence

from ...backends.base import (
    BackendCapabilities,
    BackendHealth,
    DecisionAnswer,
    DecisionBackend,
    DecisionOption,
    DecisionQuestion,
    DecisionRequest,
    DecisionResult,
)
from ..actions import LegalActionSet
from .local_slm import ToolDecision, ToolDecisionBackend, ToolDecisionRequest


def _one_hot(ids: Sequence[str], chosen: str) -> dict[str, float]:
    dist = {i: 0.0 for i in ids}
    dist[chosen] = 1.0
    return dist


class JevBoundedToolBackend:
    """Expose a bounded JEV/OpenJev/NanoJev scorer as a ToolDecisionBackend."""

    def __init__(self, delegate: DecisionBackend, *, backend_id: str = "jev") -> None:
        self._delegate = delegate
        self._backend_id = backend_id

    @property
    def backend_id(self) -> str:
        return self._backend_id

    @property
    def delegate(self) -> DecisionBackend:
        return self._delegate

    def health(self, *, load: bool = False) -> dict[str, Any]:
        h = self._delegate.health(load=load)
        return {
            "ready": h.ready,
            "backend": self._backend_id,
            "model": h.model,
            "checkpoint": h.checkpoint,
            "detail": h.detail,
            "capabilities": {
                "supports_boolean": self._delegate.capabilities.supports_boolean,
                "supports_choice": self._delegate.capabilities.supports_choice,
                "returns_distribution": self._delegate.capabilities.returns_distribution,
                "max_choice_options": self._delegate.capabilities.max_choice_options,
            },
        }

    def decide(self, request: ToolDecisionRequest) -> ToolDecision:
        legal = request.legal
        ids = legal.ids()
        caps = self._delegate.capabilities
        if not ids:
            return ToolDecision(
                backend=self._backend_id,
                model=None,
                revision=None,
                selected_action=None,
                arguments={},
                confidence=None,
                distribution=None,
                latency_ms=0.0,
                abstained=True,
                candidate_action_count=0,
                parse_error="no legal actions",
            )
        if not caps.supports_choice:
            return ToolDecision(
                backend=self._backend_id,
                model=caps.id,
                revision=None,
                selected_action=None,
                arguments={},
                confidence=None,
                distribution=None,
                latency_ms=0.0,
                abstained=True,
                candidate_action_count=len(ids),
                parse_error="backend does not support choice questions",
            )
        if len(ids) > caps.max_choice_options:
            # Too many options for a bounded scorer: hand back to the caller,
            # which is expected to cluster/partition rather than truncate.
            return ToolDecision(
                backend=self._backend_id,
                model=caps.id,
                revision=None,
                selected_action=None,
                arguments={},
                confidence=None,
                distribution=None,
                latency_ms=0.0,
                abstained=True,
                candidate_action_count=len(ids),
                parse_error=(
                    f"{len(ids)} options exceed backend max {caps.max_choice_options}"
                ),
            )

        question = DecisionQuestion(
            id="select_action",
            type="choice",
            instructions=(
                "Choose the single best next action id. Never invent an action."
            ),
            options=tuple(
                DecisionOption(id=a.action_id, description=a.description) for a in legal.legal
            ),
        )
        state = request.state
        if request.objective:
            state = f"Objective: {request.objective}\n\n{state}"
        decision_request = DecisionRequest(state=state or " ", questions=(question,))
        result = self._delegate.evaluate(decision_request)
        answer = result.answers[0] if result.answers else None
        if answer is None:
            return ToolDecision(
                backend=self._backend_id,
                model=result.model,
                revision=result.revision,
                selected_action=None,
                arguments={},
                confidence=None,
                distribution=None,
                latency_ms=result.latency_ms,
                abstained=True,
                candidate_action_count=len(ids),
                diagnostics=dict(result.diagnostics),
            )
        selected = str(answer.value)
        if selected not in set(ids):
            return ToolDecision(
                backend=self._backend_id,
                model=result.model,
                revision=result.revision,
                selected_action=None,
                arguments={},
                confidence=answer.confidence,
                distribution=None,
                latency_ms=result.latency_ms,
                abstained=True,
                invalid_call=True,
                candidate_action_count=len(ids),
                parse_error=f"invalid_call:{selected}",
                diagnostics=dict(result.diagnostics),
            )
        return ToolDecision(
            backend=self._backend_id,
            model=result.model,
            revision=result.revision,
            selected_action=selected,
            arguments={},
            confidence=answer.confidence,
            distribution=dict(answer.probabilities),
            latency_ms=result.latency_ms,
            abstained=False,
            candidate_action_count=len(ids),
            diagnostics=dict(result.diagnostics),
        )


class DecisionBackendToolAdapter:
    """Run a ToolDecisionBackend through the legacy DecisionBackend protocol.

    This is what lets a local SLM appear in ``z0int backends list`` and in the
    existing ``decision-capability-v1`` bench without either side knowing about
    the other.
    """

    def __init__(self, delegate: ToolDecisionBackend, *, backend_id: str | None = None) -> None:
        self._delegate = delegate
        self._backend_id = backend_id or delegate.backend_id

    @property
    def delegate(self) -> ToolDecisionBackend:
        return self._delegate

    @property
    def capabilities(self) -> BackendCapabilities:
        return BackendCapabilities(
            id=self._backend_id,
            local=True,
            trainable=False,
            supports_boolean=True,
            supports_choice=True,
            supports_score=False,
            max_choice_options=255,
            max_score_levels=0,
            returns_distribution=True,
            autoregressive_decode=True,
        )

    def health(self, *, load: bool = False) -> BackendHealth:
        info = self._delegate.health(load=load)
        return BackendHealth(
            id=self._backend_id,
            configured=True,
            ready=bool(info.get("ready")),
            loaded=bool(info.get("ready")),
            detail=str(info.get("detail") or ""),
            model=info.get("model"),
            diagnostics=dict(info),
        )

    def evaluate(self, request: DecisionRequest) -> DecisionResult:
        from ..actions import ActionCandidate, ActionGraph, compile_actions

        actions = []
        for question in request.questions:
            if question.type == "choice":
                for opt in question.options:
                    actions.append(
                        ActionCandidate(
                            action_id=opt.id,
                            kind="tool",
                            description=opt.description,
                            tool=opt.id,
                            arguments_schema={"type": "object", "properties": {},
                                              "additionalProperties": False},
                        )
                    )
            elif question.type == "boolean":
                for label in ("true", "false"):
                    actions.append(
                        ActionCandidate(
                            action_id=label, kind="control", description=f"answer {label}"
                        )
                    )
        if not actions:
            return DecisionResult(
                backend=self._backend_id,
                model=None,
                revision=None,
                answers=(),
                latency_ms=0.0,
                diagnostics={"reason": "no choosable question"},
            )
        graph = ActionGraph(actions=tuple(actions))
        # This adapter is a *transport shim*: the caller already filtered. Grant
        # every capability and every risk class only because the options were
        # constructed here from the question the caller supplied.
        legal = compile_actions(
            graph=graph,
            granted_capabilities=tuple(
                sorted({c for a in actions for c in a.required_capabilities})
            ),
            authority=("read", "write", "destructive", "publish", "credential", "payment"),
            budget_units=10_000,
        )
        state = request.state if isinstance(request.state, str) else _stringify(request.state)
        decision = self._delegate.decide(
            ToolDecisionRequest(state=state, legal=legal, max_tokens=256)
        )
        answers: list[DecisionAnswer] = []
        if decision.selected_action is not None:
            dist = (
                dict(decision.distribution)
                if decision.distribution
                else _one_hot(legal.ids(), decision.selected_action)
            )
            answers.append(
                DecisionAnswer(
                    question_id=request.questions[0].id,
                    type=request.questions[0].type,
                    probabilities=dist,
                    value=(
                        decision.selected_action == "true"
                        if request.questions[0].type == "boolean"
                        else decision.selected_action
                    ),
                    confidence=decision.confidence,
                )
            )
        return DecisionResult(
            backend=self._backend_id,
            model=decision.model,
            revision=decision.revision,
            answers=tuple(answers),
            latency_ms=decision.latency_ms,
            diagnostics={
                **dict(decision.diagnostics),
                "abstained": decision.abstained,
                "invalid_call": decision.invalid_call,
                "candidate_action_count": decision.candidate_action_count,
                "ttft_ms": decision.ttft_ms,
                "decode_tok_s": decision.decode_tok_s,
                "prompt_tokens": decision.prompt_tokens,
                "completion_tokens": decision.completion_tokens,
            },
        )


def _stringify(state: Any) -> str:
    import json

    return json.dumps(state, sort_keys=True, ensure_ascii=False)


__all__ = ["JevBoundedToolBackend", "DecisionBackendToolAdapter"]
