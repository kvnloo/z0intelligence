"""Backend-neutral local-SLM adapter.

One adapter surface covers Nemotron-Orchestrator/Qwen3.5/Hammer/FunctionGemma and
any other OpenAI-compatible local server. Model-specific prompt and parser
behaviour is injected as a :class:`~z0int.cognition.adapters.dialects.ToolDialect`,
so the controller never branches on model identity.

Safety invariants (z0intelligence#20, oh-my-pi#83):

* The backend only ever *chooses from* the already-legal action set.
* A tool call for an action outside that set is reported as ``invalid_call`` and
  resolves to ``abstained`` — it can never re-admit a filtered action.
* Any transport or parse failure fails open (``abstained=True``) rather than
  guessing.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Protocol, runtime_checkable
import time

from ..actions import LegalActionSet
from ..manifest import ModelCapability
from .dialects import DIALECTS, ToolDialect, dialect_for
from .transport import ChatOutcome, OpenAICompatTransport, ServerConfig, TransportError

# JSON Schema used when a server supports constrained decoding.
DECISION_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "action_id": {"type": "string"},
        "confidence": {"type": "number"},
    },
    "required": ["action_id"],
    "additionalProperties": False,
}


@dataclass(frozen=True)
class ToolDecisionRequest:
    """What a learned backend is allowed to see."""

    state: str
    legal: LegalActionSet
    objective: str | None = None
    risk_class: str | None = None
    constraints: Mapping[str, Any] = field(default_factory=dict)
    budget: Mapping[str, Any] = field(default_factory=dict)
    max_tokens: int = 256


@dataclass(frozen=True)
class ToolDecision:
    backend: str
    model: str | None
    revision: str | None
    selected_action: str | None
    arguments: Mapping[str, Any]
    confidence: float | None
    distribution: Mapping[str, float] | None
    latency_ms: float
    abstained: bool
    ttft_ms: float | None = None
    decode_tok_s: float | None = None
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    cached_tokens: int | None = None
    candidate_action_count: int = 0
    invalid_call: bool = False
    irrelevant_call: bool = False
    unnecessary_call: bool = False
    parse_error: str | None = None
    reasoning_metadata: Mapping[str, Any] = field(default_factory=dict)
    diagnostics: Mapping[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "backend": self.backend,
            "model": self.model,
            "revision": self.revision,
            "selected_action": self.selected_action,
            "arguments": dict(self.arguments),
            "confidence": self.confidence,
            "distribution": dict(self.distribution) if self.distribution else None,
            "latency_ms": self.latency_ms,
            "abstained": self.abstained,
            "candidate_action_count": self.candidate_action_count,
            "invalid_call": self.invalid_call,
            "irrelevant_call": self.irrelevant_call,
            "unnecessary_call": self.unnecessary_call,
        }
        for key in (
            "ttft_ms",
            "decode_tok_s",
            "prompt_tokens",
            "completion_tokens",
            "cached_tokens",
            "parse_error",
        ):
            value = getattr(self, key)
            if value is not None:
                out[key] = value
        if self.reasoning_metadata:
            out["reasoning_metadata"] = dict(self.reasoning_metadata)
        if self.diagnostics:
            out["diagnostics"] = dict(self.diagnostics)
        return out


@runtime_checkable
class ToolDecisionBackend(Protocol):
    """Shared surface for every semantic decision layer in the cascade."""

    @property
    def backend_id(self) -> str:
        ...

    def health(self, *, load: bool = False) -> dict[str, Any]:
        ...

    def decide(self, request: ToolDecisionRequest) -> ToolDecision:
        ...


def _abstain(
    *,
    backend: str,
    model: str | None,
    revision: str | None,
    candidate_count: int,
    latency_ms: float,
    reason: str,
    invalid_call: bool = False,
    extra: Mapping[str, Any] | None = None,
) -> ToolDecision:
    diagnostics: dict[str, Any] = {"reason": reason}
    if extra:
        diagnostics.update(dict(extra))
    return ToolDecision(
        backend=backend,
        model=model,
        revision=revision,
        selected_action=None,
        arguments={},
        confidence=None,
        distribution=None,
        latency_ms=latency_ms,
        abstained=True,
        candidate_action_count=candidate_count,
        invalid_call=invalid_call,
        parse_error=reason,
        diagnostics=diagnostics,
    )


def _supervisor_facts(raw: Mapping[str, Any] | None) -> dict[str, Any] | None:
    """Residency facts the production supervisor attaches to every response.

    A router must be able to see the *load* cost of choosing a model, not only
    the decode cost: on a 12 GB card a cold 9B pays a real multi-second load that
    a warm resident invocation does not. ``z0int cognition serve`` records that
    per response; this keeps it on the decision so receipts can carry it.
    """
    if not isinstance(raw, Mapping):
        return None
    facts = raw.get("z0int_supervisor")
    if not isinstance(facts, Mapping):
        return None
    return dict(facts)


class LocalSLMBackend:
    """A local generative model used as a *bounded* chooser."""

    def __init__(
        self,
        *,
        backend_id: str,
        config: ServerConfig,
        dialect: ToolDialect | None = None,
        capability: ModelCapability | None = None,
        transport: OpenAICompatTransport | None = None,
    ) -> None:
        self._backend_id = backend_id
        self._config = config
        self._capability = capability
        self._dialect = dialect or dialect_for(
            capability.tool_parser if capability else None,
            capability.tool_call_template if capability else None,
        )
        self._transport = transport or OpenAICompatTransport(config)

    @classmethod
    def for_capability(cls, capability: ModelCapability, *, base_url: str, model: str | None = None,
                       api_key: str | None = None, runtime: str | None = None,
                       quantization: str | None = None, backend_id: str | None = None,
                       dialect: ToolDialect | None = None) -> LocalSLMBackend:
        return cls(
            backend_id=backend_id or capability.model_id,
            config=ServerConfig(
                base_url=base_url,
                model=model or capability.model_id,
                api_key=api_key,
                runtime=runtime or (capability.supported_runtimes[0] if capability.supported_runtimes else "unknown"),
                quantization=quantization or capability.tested_quantization,
                revision=capability.revision,
            ),
            dialect=dialect,
            capability=capability,
        )

    # ---- protocol ---------------------------------------------------
    @property
    def backend_id(self) -> str:
        return self._backend_id

    @property
    def dialect(self) -> ToolDialect:
        return self._dialect

    @property
    def config(self) -> ServerConfig:
        return self._config

    def health(self, *, load: bool = False) -> dict[str, Any]:
        info = self._transport.health()
        info.update(
            {
                "backend": self._backend_id,
                "runtime": self._config.runtime,
                "quantization": self._config.quantization,
                "dialect": self._dialect.name,
            }
        )
        return info

    def decide(self, request: ToolDecisionRequest) -> ToolDecision:
        legal = request.legal
        ids = legal.ids()
        if not ids:
            return _abstain(
                backend=self._backend_id,
                model=self._config.model,
                revision=self._config.revision,
                candidate_count=0,
                latency_ms=0.0,
                reason="no legal actions",
            )

        prompt = self._dialect.render(
            state=request.state,
            actions=legal.legal,
            objective=request.objective,
            risk_class=request.risk_class,
            constraints=request.constraints,
        )
        tools = self._dialect.tools_payload(legal.legal)
        response_format = self._dialect.response_format(DECISION_SCHEMA)
        messages = [
            {
                "role": "system",
                "content": (
                    "You are a bounded decision component. Choose exactly one action "
                    "from the provided list. Never invent an action."
                ),
            },
            {"role": "user", "content": prompt},
        ]

        started = time.perf_counter()
        try:
            outcome: ChatOutcome = self._transport.chat(
                messages,
                tools=tools,
                tool_choice=self._dialect.tool_choice,
                response_format=response_format,
                max_tokens=min(request.max_tokens, self._dialect.max_tokens)
                if self._dialect.max_tokens
                else request.max_tokens,
                temperature=self._dialect.temperature,
                stop=self._dialect.stop or None,
            )
        except TransportError as exc:
            return _abstain(
                backend=self._backend_id,
                model=self._config.model,
                revision=self._config.revision,
                candidate_count=len(ids),
                latency_ms=(time.perf_counter() - started) * 1000.0,
                reason=f"transport_error: {exc}",
            )

        invocation = self._dialect.parse(
            content=outcome.content, tool_call=outcome.tool_call, allowed_ids=ids
        )
        usage = outcome.usage or {}
        timings = outcome.timings or {}
        decode_tok_s = None
        if timings.get("predicted_ms") and timings.get("predicted_n"):
            decode_tok_s = float(timings["predicted_n"]) / (float(timings["predicted_ms"]) / 1000.0)
        elif usage.get("completion_tokens") and outcome.latency_ms > 0:
            decode_tok_s = float(usage["completion_tokens"]) / (outcome.latency_ms / 1000.0)

        common = dict(
            backend=self._backend_id,
            model=self._config.model,
            revision=self._config.revision,
            candidate_count=len(ids),
            ttft_ms=outcome.ttft_ms,
            decode_tok_s=decode_tok_s,
            prompt_tokens=usage.get("prompt_tokens"),
            completion_tokens=usage.get("completion_tokens"),
            cached_tokens=(usage.get("prompt_tokens_details") or {}).get("cached_tokens")
            if isinstance(usage.get("prompt_tokens_details"), dict)
            else usage.get("cached_tokens"),
            supervisor=_supervisor_facts(outcome.raw),
        )

        if invocation is None:
            return self._build(
                selected=None,
                arguments={},
                confidence=None,
                distribution=None,
                latency_ms=outcome.latency_ms,
                abstained=True,
                parse_error="unparseable_response",
                diagnostics={"content_head": (outcome.content or "")[:400]},
                **common,
            )

        if invocation.tool not in set(ids):
            # The model proposed something the compiler already rejected.
            return self._build(
                selected=None,
                arguments=invocation.arguments,
                confidence=invocation.confidence,
                distribution=None,
                latency_ms=outcome.latency_ms,
                abstained=True,
                parse_error=f"invalid_call:{invocation.tool}",
                invalid_call=True,
                diagnostics={
                    "attempted_action": invocation.tool,
                    "legal_ids": list(ids),
                },
                **common,
            )

        distribution = {i: 0.0 for i in ids}
        distribution[invocation.tool] = 1.0
        confidence = invocation.confidence
        diagnostics: dict[str, Any] = {
            "parse_source": invocation.source,
            "content_head": (outcome.content or "")[:400],
        }
        if invocation.extra_calls:
            # A bounded single-choice decision should produce one call. Extra
            # distinct calls are recorded (never executed) so calibration can see
            # that the model was not actually decisive.
            diagnostics["parallel_calls_detected"] = [
                invocation.tool,
                *invocation.extra_calls,
            ]
        return self._build(
            selected=invocation.tool,
            arguments=invocation.arguments,
            confidence=confidence,
            distribution=distribution,
            latency_ms=outcome.latency_ms,
            abstained=False,
            parse_error=None,
            diagnostics=diagnostics,
            **common,
        )

    def _build(
        self,
        *,
        backend: str,
        model: str | None,
        revision: str | None,
        selected: str | None,
        arguments: Mapping[str, Any],
        confidence: float | None,
        distribution: Mapping[str, float] | None,
        latency_ms: float,
        abstained: bool,
        parse_error: str | None,
        candidate_count: int,
        invalid_call: bool = False,
        ttft_ms: float | None = None,
        decode_tok_s: float | None = None,
        prompt_tokens: int | None = None,
        completion_tokens: int | None = None,
        cached_tokens: int | None = None,
        diagnostics: Mapping[str, Any] | None = None,
        supervisor: Mapping[str, Any] | None = None,
    ) -> ToolDecision:
        merged: dict[str, Any] = dict(diagnostics or {})
        if supervisor:
            merged["supervisor"] = dict(supervisor)
        return ToolDecision(
            backend=backend,
            model=model,
            revision=revision,
            selected_action=selected,
            arguments=dict(arguments),
            confidence=confidence,
            distribution=dict(distribution) if distribution else None,
            latency_ms=latency_ms,
            abstained=abstained,
            ttft_ms=ttft_ms,
            decode_tok_s=decode_tok_s,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            cached_tokens=cached_tokens,
            candidate_action_count=candidate_count,
            invalid_call=invalid_call,
            parse_error=parse_error,
            diagnostics=merged,
        )


# Keep the dialect registry importable from here for convenience.
__all__ = [
    "DIALECTS",
    "DECISION_SCHEMA",
    "LocalSLMBackend",
    "ToolDecision",
    "ToolDecisionBackend",
    "ToolDecisionRequest",
]
