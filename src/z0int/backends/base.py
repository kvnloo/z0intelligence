"""Canonical bounded-decision backend contract.

This module is intentionally stdlib-only. Importing backend metadata must not
load torch, transformers, model weights, or network clients.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, Protocol, runtime_checkable
import json
import math


DecisionType = Literal["boolean", "choice", "score"]


def _finite_json(value: Any) -> None:
    try:
        json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True)
    except (TypeError, ValueError) as exc:
        raise ValueError("state must be finite JSON-compatible data") from exc


@dataclass(frozen=True)
class DecisionOption:
    id: str
    description: str

    def __post_init__(self) -> None:
        if not self.id.strip():
            raise ValueError("option id must be nonempty")
        if not self.description.strip():
            raise ValueError("option description must be nonempty")


@dataclass(frozen=True)
class DecisionQuestion:
    id: str
    type: DecisionType
    instructions: str
    options: tuple[DecisionOption, ...] = ()
    levels: tuple[str, ...] = ()
    false_criterion: str | None = None
    true_criterion: str | None = None

    def __post_init__(self) -> None:
        if not self.id.strip():
            raise ValueError("question id must be nonempty")
        if self.type not in ("boolean", "choice", "score"):
            raise ValueError(f"unsupported decision type: {self.type}")
        if not self.instructions.strip():
            raise ValueError("instructions must be nonempty")
        if self.type == "boolean":
            if self.options or self.levels:
                raise ValueError("boolean questions do not use options/levels")
            for x in (self.false_criterion, self.true_criterion):
                if x is not None and not x.strip():
                    raise ValueError("boolean criteria must be nonempty when present")
        elif self.type == "choice":
            if not 2 <= len(self.options) <= 255:
                raise ValueError("choice requires 2..255 options")
            if self.levels or self.false_criterion is not None or self.true_criterion is not None:
                raise ValueError("choice cannot use score/boolean criteria")
            ids = [x.id for x in self.options]
            if len(ids) != len(set(ids)):
                raise ValueError("choice option ids must be unique")
        else:
            if not 2 <= len(self.levels) <= 10:
                raise ValueError("score requires 2..10 ordered levels")
            if self.options or self.false_criterion is not None or self.true_criterion is not None:
                raise ValueError("score cannot use choice/boolean criteria")
            if any(not x.strip() for x in self.levels):
                raise ValueError("score levels must be nonempty")


@dataclass(frozen=True)
class DecisionRequest:
    state: str | dict[str, Any] | list[Any]
    questions: tuple[DecisionQuestion, ...]
    request_id: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.state, (str, dict, list)) or not self.state:
            raise ValueError("state must be a nonempty string/object/array")
        _finite_json(self.state)
        if not self.questions:
            raise ValueError("at least one question is required")
        ids = [q.id for q in self.questions]
        if len(ids) != len(set(ids)):
            raise ValueError("question ids must be unique")


@dataclass(frozen=True)
class DecisionAnswer:
    question_id: str
    type: DecisionType
    probabilities: dict[str, float]
    value: bool | str | float
    confidence: float | None = None

    def __post_init__(self) -> None:
        if not self.probabilities:
            raise ValueError("probabilities cannot be empty")
        vals = list(self.probabilities.values())
        if any((not math.isfinite(v)) or v < 0 or v > 1 for v in vals):
            raise ValueError("probabilities must be finite and in [0,1]")
        if abs(math.fsum(vals) - 1.0) > 1e-5:
            raise ValueError("probabilities must sum to 1")


@dataclass(frozen=True)
class BackendCapabilities:
    """What a decision backend can answer.

    Two shapes were in flight: the original (`trainable`, `returns_distribution`)
    used by `nanojev`, and a newer descriptive shape (`kind`, `description`,
    `supports_batch_questions`) used by `decider_2b`, `laya` and
    `openjev_direct`. Constructing the newer shape raised

        TypeError: BackendCapabilities.__init__() got an unexpected keyword
        argument 'kind'

    which took out `registry.backend_status()` entirely — so no backend could be
    inventoried at all. Every field that the newer shape needs is therefore
    optional here, and `trainable` gained a default so the newer adapters can
    omit it. Both shapes now construct.
    """

    id: str
    local: bool
    supports_boolean: bool
    supports_choice: bool
    supports_score: bool
    max_choice_options: int
    max_score_levels: int
    # Descriptive metadata (newer shape) — also what the runtime inventory
    # projects into its `capabilities`/`kind` columns.
    kind: str = "decision"
    description: str = ""
    trainable: bool = False
    returns_distribution: bool = True
    autoregressive_decode: bool = False
    shared_prefix: bool = False
    supports_batch_questions: bool = False
    supports_observed_outcome_training: bool = False
    supports_soft_distribution_training: bool = False


@dataclass(frozen=True)
class BackendHealth:
    id: str
    configured: bool
    ready: bool
    loaded: bool = False
    detail: str = ""
    model: str | None = None
    checkpoint: str | None = None
    diagnostics: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class DecisionResult:
    backend: str
    model: str | None
    revision: str | None
    answers: tuple[DecisionAnswer, ...]
    latency_ms: float
    diagnostics: dict[str, Any] = field(default_factory=dict)


@runtime_checkable
class DecisionBackend(Protocol):
    @property
    def capabilities(self) -> BackendCapabilities:
        ...

    def health(self, *, load: bool = False) -> BackendHealth:
        ...

    def evaluate(self, request: DecisionRequest) -> DecisionResult:
        ...


def request_from_mapping(raw: dict[str, Any]) -> DecisionRequest:
    """Parse the canonical z0int backends CLI / fixture JSON into DecisionRequest.

    Choice/score questions accept either internal field names (``options`` /
    ``levels``) or the transport alias ``criteria`` used in the master prompt.
    """
    if not isinstance(raw, dict):
        raise ValueError("request must be a JSON object")
    state = raw.get("state")
    qs_raw = raw.get("questions")
    if not isinstance(qs_raw, list) or not qs_raw:
        raise ValueError("questions must be a nonempty array")
    questions: list[DecisionQuestion] = []
    for item in qs_raw:
        if not isinstance(item, dict):
            raise ValueError("each question must be an object")
        qid = str(item.get("id") or "")
        qtype = str(item.get("type") or "")
        instructions = str(item.get("instructions") or "")
        criteria = item.get("criteria")
        if qtype == "boolean":
            false_c = true_c = None
            if isinstance(criteria, dict):
                false_c = criteria.get("false")
                true_c = criteria.get("true")
                if false_c is not None:
                    false_c = str(false_c)
                if true_c is not None:
                    true_c = str(true_c)
            questions.append(
                DecisionQuestion(
                    id=qid,
                    type="boolean",
                    instructions=instructions,
                    false_criterion=false_c,
                    true_criterion=true_c,
                )
            )
        elif qtype == "choice":
            opts_src = item.get("options")
            if opts_src is None:
                opts_src = criteria
            if not isinstance(opts_src, list):
                raise ValueError(f"choice {qid!r} requires options/criteria array")
            options: list[DecisionOption] = []
            for o in opts_src:
                if not isinstance(o, dict):
                    raise ValueError("choice option must be an object")
                options.append(
                    DecisionOption(
                        id=str(o.get("id") or ""),
                        description=str(o.get("description") or ""),
                    )
                )
            questions.append(
                DecisionQuestion(
                    id=qid,
                    type="choice",
                    instructions=instructions,
                    options=tuple(options),
                )
            )
        elif qtype == "score":
            levels_src = item.get("levels")
            if levels_src is None:
                levels_src = criteria
            if not isinstance(levels_src, (list, tuple)):
                raise ValueError(f"score {qid!r} requires levels/criteria array")
            questions.append(
                DecisionQuestion(
                    id=qid,
                    type="score",
                    instructions=instructions,
                    levels=tuple(str(x) for x in levels_src),
                )
            )
        else:
            raise ValueError(f"unsupported decision type: {qtype!r}")
    return DecisionRequest(
        state=state,  # type: ignore[arg-type]
        questions=tuple(questions),
        request_id=(str(raw["request_id"]) if raw.get("request_id") is not None else None),
    )


def result_to_dict(result: DecisionResult) -> dict[str, Any]:
    return {
        "schema": "z0int.decision_result.v1",
        "backend": result.backend,
        "model": result.model,
        "revision": result.revision,
        "latency_ms": result.latency_ms,
        "answers": [
            {
                "question_id": a.question_id,
                "type": a.type,
                "probabilities": a.probabilities,
                "value": a.value,
                "confidence": a.confidence,
            }
            for a in result.answers
        ],
        "diagnostics": result.diagnostics,
    }

