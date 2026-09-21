"""Multi-turn model-as-tool orchestration evaluation.

The bounded-choice suite cannot judge an orchestrator: it asks for one pick from
a small list. ``Nemotron-Orchestrator-8B`` is trained to alternate reasoning and
tool calling across *turns*, dispatching to basic tools, specialist models and
generalist models while trading quality against cost and latency. That is a
different task, and it needs its own harness.

This module implements it as a deterministic, replayable simulation:

* a scenario declares a task, a set of heterogeneous callables (tools,
  specialists, generalists) with real costs, and the specialties the task needs;
* each turn the orchestrator picks one callable;
* the world answers deterministically, resolving the specialties that callable
  actually covers, and says so;
* the orchestrator stops by answering without a tool call, or runs out of turns
  or budget.

Nothing here executes anything real. That is deliberate: the point is to measure
dispatch quality, cost and stopping behaviour under a fixed, honest world.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Protocol, Sequence

from .adapters.local_slm import ToolDecisionBackend
from .adapters.transport import ChatOutcome, OpenAICompatTransport, ServerConfig, TransportError

SCHEMA = "z0int.orchestration_eval.v1"

KIND_TOOL = "tool"
KIND_SPECIALIST = "specialist"
KIND_GENERALIST = "generalist"


@dataclass(frozen=True)
class OrchestrationTool:
    """One callable the orchestrator may dispatch to."""

    name: str
    kind: str
    description: str
    cost_units: int
    resolves: tuple[str, ...] = ()
    latency_class: str = "fast"
    parameters: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.kind not in (KIND_TOOL, KIND_SPECIALIST, KIND_GENERALIST):
            raise ValueError(f"unknown callable kind {self.kind!r}")
        if self.cost_units < 0:
            raise ValueError("cost_units must be >= 0")

    def to_openai_tool(self) -> dict[str, Any]:
        schema = dict(self.parameters) or {
            "type": "object",
            "properties": {"input": {"type": "string"}},
            "additionalProperties": False,
        }
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": f"[{self.kind}] {self.description}",
                "parameters": schema,
            },
        }


@dataclass(frozen=True)
class Scenario:
    """A multi-turn orchestration task with a deterministic world."""

    scenario_id: str
    family: str
    task: str
    callables: tuple[OrchestrationTool, ...]
    requires: tuple[str, ...]
    max_turns: int = 6
    budget_units: int = 10
    # False when no offered callable can satisfy the task. The correct
    # behaviour is then to stop early -- a "declined" scenario, not a failure.
    solvable: bool = True
    # ordered pairs: first must be dispatched before second
    order_constraints: tuple[tuple[str, str], ...] = ()

    def __post_init__(self) -> None:
        if not self.task.strip():
            raise ValueError("task must be nonempty")
        names = [c.name for c in self.callables]
        if len(names) != len(set(names)):
            raise ValueError("callable names must be unique")
        for a, b in self.order_constraints:
            if a not in names or b not in names:
                raise ValueError(f"order constraint names unknown callable: {a!r} -> {b!r}")

    def by_name(self, name: str) -> OrchestrationTool | None:
        for c in self.callables:
            if c.name == name:
                return c
        return None

    def to_dict(self) -> dict[str, Any]:
        return {
            "scenario_id": self.scenario_id,
            "family": self.family,
            "task": self.task,
            "requires": list(self.requires),
            "max_turns": self.max_turns,
            "budget_units": self.budget_units,
            "solvable": self.solvable,
            "callables": [
                {"name": c.name, "kind": c.kind, "cost_units": c.cost_units,
                 "resolves": list(c.resolves), "latency_class": c.latency_class}
                for c in self.callables
            ],
        }


@dataclass
class Turn:
    index: int
    chosen: str | None
    resolved: tuple[str, ...]
    observation: str
    cost_units: int
    model_text: str = ""
    parse_error: str | None = None
    invalid_call: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "index": self.index,
            "chosen": self.chosen,
            "resolved": list(self.resolved),
            "cost_units": self.cost_units,
            "model_text": self.model_text[:400],
            "parse_error": self.parse_error,
            "invalid_call": self.invalid_call,
        }


@dataclass
class OrchestrationResult:
    scenario_id: str
    backend: str
    model: str | None
    solved: bool
    solvable: bool
    correct_stop: bool
    stopped: bool
    premature_stop: bool
    budget_exhausted: bool
    turns: int
    cost_units: int
    unresolved: tuple[str, ...]
    order_violations: int
    wasted_calls: int
    specialist_precision: float | None
    latency_ms: float
    turns_detail: tuple[Turn, ...]
    error: str | None = None
    invalid_calls: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "scenario_id": self.scenario_id,
            "backend": self.backend,
            "model": self.model,
            "solved": self.solved,
            "solvable": self.solvable,
            "correct_stop": self.correct_stop,
            "stopped": self.stopped,
            "premature_stop": self.premature_stop,
            "budget_exhausted": self.budget_exhausted,
            "turns": self.turns,
            "cost_units": self.cost_units,
            "unresolved": list(self.unresolved),
            "order_violations": self.order_violations,
            "wasted_calls": self.wasted_calls,
            "specialist_precision": self.specialist_precision,
            "latency_ms": self.latency_ms,
            "invalid_calls": self.invalid_calls,
            "error": self.error,
            "turns_detail": [t.to_dict() for t in self.turns_detail],
        }


_SYSTEM = (
    "You are an orchestrator. You dispatch to the available tools, specialists "
    "and generalist models, one call per turn, until the task is complete. "
    "Cheap and fast is better when it suffices. Call `finish` when done."
)

_FINISH_TOOL = OrchestrationTool(
    name="finish",
    kind=KIND_TOOL,
    description="Stop: the task is complete (or cannot be completed with what is available)",
    cost_units=0,
    parameters={"type": "object", "properties": {}, "additionalProperties": False},
)


class OrchestratorProtocol(Protocol):
    backend_id: str

    def step(self, *, messages: list[dict[str, Any]], tools: list[dict[str, Any]]) -> ChatOutcome:
        ...


class LocalOrchestrator:
    """Drives a served model through a multi-turn tool loop."""

    def __init__(self, *, backend_id: str, config: ServerConfig) -> None:
        self._id = backend_id
        self._transport = OpenAICompatTransport(config)

    @property
    def backend_id(self) -> str:
        return self._id

    def step(self, *, messages: list[dict[str, Any]], tools: list[dict[str, Any]]) -> ChatOutcome:
        return self._transport.chat(
            messages,
            tools=tools,
            tool_choice="auto",
            max_tokens=1024,
            temperature=0.0,
            stop=self._stop_sequences(),
        )

    @staticmethod
    def _stop_sequences() -> tuple[str, ...]:
        return ("<start_function_response>",)


def run_scenario(
    scenario: Scenario,
    orchestrator: OrchestratorProtocol,
    *,
    max_tokens_note: str | None = None,
) -> OrchestrationResult:
    """Play one scenario against one orchestrator, deterministically."""
    tools = [c.to_openai_tool() for c in scenario.callables] + [_FINISH_TOOL.to_openai_tool()]
    finish_names = {"finish", "stop", "done"}

    messages: list[dict[str, Any]] = [
        {"role": "system", "content": _SYSTEM},
        {
            "role": "user",
            "content": (
                f"Task: {scenario.task}\n"
                f"Budget: {scenario.budget_units} cost units over at most "
                f"{scenario.max_turns} turns."
            ),
        },
    ]

    resolved: set[str] = set()
    dispatches: list[str] = []
    turns: list[Turn] = []
    cost = 0
    order_violations = 0
    wasted = 0
    invalid_calls = 0
    stopped = False
    budget_exhausted = False
    error: str | None = None
    total_latency = 0.0

    for index in range(scenario.max_turns):
        try:
            outcome = orchestrator.step(messages=messages, tools=tools)
        except TransportError as exc:
            error = f"transport_error: {exc}"
            break
        total_latency += outcome.latency_ms

        call = outcome.tool_call
        name = ((call or {}).get("function") or {}).get("name") if call else None
        args_raw = ((call or {}).get("function") or {}).get("arguments") if call else None

        if not name:
            # No tool call: treat as the orchestrator answering/stalling.
            stopped = True
            turns.append(
                Turn(index, None, (), "", 0, model_text=outcome.content or "")
            )
            break

        if str(name) in finish_names:
            stopped = True
            turns.append(Turn(index, "finish", (), "", 0, model_text=outcome.content or ""))
            break

        tool = scenario.by_name(str(name))
        if tool is None:
            invalid_calls += 1
            turns.append(
                Turn(index, str(name), (), f"unknown callable {name!r}", 0, invalid_call=True)
            )
            messages.append({"role": "assistant", "content": outcome.content or "",
                            "tool_calls": [call]})
            messages.append({"role": "tool", "tool_call_id": (call or {}).get("id", "x"),
                             "content": f"error: unknown callable {name!r}"})
            continue

        if cost + tool.cost_units > scenario.budget_units:
            budget_exhausted = True
            turns.append(
                Turn(index, tool.name, (), "budget exceeded", 0,
                     model_text=outcome.content or "")
            )
            break

        cost += tool.cost_units
        dispatches.append(tool.name)
        newly = tuple(s for s in tool.resolves if s in scenario.requires and s not in resolved)
        # Order constraints are checked against what has already been dispatched.
        for before, after in scenario.order_constraints:
            if tool.name == after and before not in dispatches[:-1]:
                order_violations += 1
        resolved.update(newly)
        if not newly:
            wasted += 1
        observation = (
            f"{tool.name} returned: resolved {', '.join(newly)}"
            if newly
            else f"{tool.name} returned: no relevant result for the remaining work"
        )
        turns.append(Turn(index, tool.name, newly, observation, tool.cost_units,
                          model_text=outcome.content or ""))
        messages.append(
            {"role": "assistant", "content": outcome.content or "", "tool_calls": [call]}
        )
        messages.append(
            {"role": "tool", "tool_call_id": (call or {}).get("id", "x"), "content": observation}
        )

    unresolved = tuple(r for r in scenario.requires if r not in resolved)
    solved = not unresolved
    engaging = [t for t in turns if t.chosen and t.chosen != "finish"]
    precision = (
        sum(1 for t in engaging if t.resolved) / len(engaging) if engaging else None
    )
    # For an unsolvable scenario the win condition is declining cheaply.
    correct_stop = (
        (solved and stopped) if scenario.solvable else (stopped and wasted == 0 and invalid_calls == 0)
    )
    return OrchestrationResult(
        scenario_id=scenario.scenario_id,
        backend=orchestrator.backend_id,
        model=getattr(orchestrator, "_config", None) and None,
        solved=solved,
        solvable=scenario.solvable,
        correct_stop=correct_stop,
        stopped=stopped,
        premature_stop=stopped and not solved,
        budget_exhausted=budget_exhausted,
        turns=len(turns),
        cost_units=cost,
        unresolved=unresolved,
        order_violations=order_violations,
        wasted_calls=wasted,
        specialist_precision=precision,
        latency_ms=total_latency,
        turns_detail=tuple(turns),
        error=error,
        invalid_calls=invalid_calls,
    )


def scenario_from_dict(raw: Mapping[str, Any]) -> Scenario:
    callables = tuple(
        OrchestrationTool(
            name=str(c["name"]),
            kind=str(c.get("kind") or KIND_TOOL),
            description=str(c.get("description") or c["name"]),
            cost_units=int(c.get("cost_units") or 0),
            resolves=tuple(c.get("resolves") or ()),
            latency_class=str(c.get("latency_class") or "fast"),
            parameters=c.get("parameters") or {},
        )
        for c in raw.get("callables") or []
    )
    return Scenario(
        scenario_id=str(raw["scenario_id"]),
        family=str(raw.get("family") or "uncategorised"),
        task=str(raw["task"]),
        callables=callables,
        requires=tuple(raw.get("requires") or ()),
        max_turns=int(raw.get("max_turns") or 6),
        budget_units=int(raw.get("budget_units") or 10),
        solvable=bool(raw.get("solvable", True)),
        order_constraints=tuple(
            (str(a), str(b)) for a, b in (raw.get("order_constraints") or [])
        ),
    )


__all__ = [
    "KIND_GENERALIST",
    "KIND_SPECIALIST",
    "KIND_TOOL",
    "LocalOrchestrator",
    "OrchestrationResult",
    "OrchestrationTool",
    "Scenario",
    "Turn",
    "run_scenario",
    "scenario_from_dict",
]
