"""Multi-turn orchestration tests — deterministic world, scripted orchestrators."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from z0int.cognition.adapters.transport import ChatOutcome
from z0int.cognition.orchestration import (
    KIND_GENERALIST,
    KIND_SPECIALIST,
    KIND_TOOL,
    OrchestrationTool,
    Scenario,
    run_scenario,
    scenario_from_dict,
)

SCENARIOS = (
    Path(__file__).resolve().parents[1]
    / "benchmarks"
    / "fixtures"
    / "orchestration-v1"
    / "scenarios.jsonl"
)


class Scripted:
    """Replays a fixed dispatch plan, then finishes."""

    def __init__(self, backend_id: str, plan: list[str]) -> None:
        self.backend_id = backend_id
        self._plan = list(plan)
        self.seen: list[list[dict]] = []

    def step(self, *, messages, tools) -> ChatOutcome:
        self.seen.append([t["function"]["name"] for t in tools])
        name = self._plan.pop(0) if self._plan else "finish"
        if name == "__answer__":
            return ChatOutcome("", None, {}, {}, {}, 1.0)
        call = {"id": f"c{len(self.seen)}", "function": {"name": name, "arguments": "{}"}}
        return ChatOutcome("", call, {}, {}, {}, 1.0)


class Exploding:
    backend_id = "exploding"

    def step(self, *, messages, tools) -> ChatOutcome:
        from z0int.cognition.adapters.transport import TransportError

        raise TransportError("server gone")


def _scenario(**kw) -> Scenario:
    callables = kw.pop(
        "callables",
        (
            OrchestrationTool("cheap_tool", KIND_TOOL, "deterministic", 1, ("alpha",)),
            OrchestrationTool("specialist", KIND_SPECIALIST, "specialist model", 3, ("beta",)),
            OrchestrationTool("generalist", KIND_GENERALIST, "expensive generalist", 8, ("alpha", "beta")),
        ),
    )
    return Scenario(
        scenario_id=kw.pop("scenario_id", "s1"),
        family=kw.pop("family", "f"),
        task=kw.pop("task", "do the thing"),
        callables=callables,
        requires=kw.pop("requires", ("alpha", "beta")),
        **kw,
    )


# --- fixtures -----------------------------------------------------------


def test_shipped_scenarios_all_parse_and_are_self_consistent():
    rows = [json.loads(line) for line in SCENARIOS.read_text().splitlines() if line.strip()]
    assert len(rows) >= 12
    ids = set()
    for raw in rows:
        scenario = scenario_from_dict(raw)
        ids.add(scenario.scenario_id)
        # every required specialty must be resolvable unless declared unsolvable
        resolvable: set[str] = set()
        for c in scenario.callables:
            resolvable.update(c.resolves)
        if scenario.solvable:
            assert set(scenario.requires) <= resolvable, scenario.scenario_id
        else:
            assert not set(scenario.requires) <= resolvable, scenario.scenario_id
        for a, b in scenario.order_constraints:
            assert scenario.by_name(a) and scenario.by_name(b)
    assert len(ids) == len(rows)


def test_scenario_rejects_duplicate_callable_names():
    with pytest.raises(ValueError, match="unique"):
        _scenario(
            callables=(
                OrchestrationTool("x", KIND_TOOL, "a", 1, ("alpha",)),
                OrchestrationTool("x", KIND_TOOL, "b", 1, ("beta",)),
            )
        )


def test_scenario_rejects_order_constraints_naming_unknown_callables():
    with pytest.raises(ValueError, match="unknown callable"):
        _scenario(order_constraints=(("nope", "cheap_tool"),))


def test_unknown_callable_kind_is_rejected():
    with pytest.raises(ValueError, match="unknown callable kind"):
        OrchestrationTool("x", "wizard", "d", 1)


# --- the loop -----------------------------------------------------------


def test_cheap_covering_path_solves_within_budget():
    result = run_scenario(_scenario(), Scripted("s", ["cheap_tool", "specialist"]))
    assert result.solved is True
    assert result.cost_units == 4
    # two dispatches plus the terminating stop turn
    assert [t.chosen for t in result.turns_detail] == ["cheap_tool", "specialist", "finish"]
    assert result.wasted_calls == 0
    assert result.correct_stop is True


def test_expensive_shortcut_solves_but_costs_more():
    result = run_scenario(_scenario(), Scripted("s", ["generalist"]))
    assert result.solved is True
    assert result.cost_units == 8
    assert len(result.turns_detail) == 2  # one dispatch + the stop


def test_stopping_early_is_a_premature_stop():
    result = run_scenario(_scenario(), Scripted("s", ["cheap_tool", "finish"]))
    assert result.solved is False
    assert result.premature_stop is True
    assert result.unresolved == ("beta",)
    assert result.correct_stop is False


def test_answering_without_a_tool_call_stops_the_loop():
    result = run_scenario(_scenario(), Scripted("s", ["cheap_tool", "__answer__"]))
    assert result.stopped is True
    assert result.solved is False


def test_unknown_callable_is_counted_and_does_not_crash():
    result = run_scenario(_scenario(), Scripted("s", ["does_not_exist", "cheap_tool", "specialist"]))
    assert result.invalid_calls == 1
    assert result.solved is True
    assert result.cost_units == 4


def test_budget_exhaustion_stops_before_overspending():
    scenario = _scenario(budget_units=3)
    result = run_scenario(scenario, Scripted("s", ["specialist", "specialist"]))
    assert result.budget_exhausted is True
    assert result.cost_units <= 3


def test_wasted_calls_are_counted():
    result = run_scenario(_scenario(), Scripted("s", ["specialist", "specialist", "cheap_tool"]))
    assert result.wasted_calls == 1
    assert result.solved is True


def test_order_violation_is_detected():
    scenario = _scenario(
        requires=("alpha", "beta"),
        order_constraints=(("cheap_tool", "specialist"),),
    )
    bad = run_scenario(scenario, Scripted("s", ["specialist", "cheap_tool"]))
    assert bad.order_violations == 1
    good = run_scenario(scenario, Scripted("s", ["cheap_tool", "specialist"]))
    assert good.order_violations == 0


def test_max_turns_is_respected():
    scenario = _scenario(max_turns=2)
    result = run_scenario(scenario, Scripted("s", ["cheap_tool", "specialist", "generalist"]))
    assert result.turns == 2
    assert result.solved is True  # cheap_tool + specialist cover both requirements


def test_unsolvable_scenario_is_won_by_declining_cheaply():
    scenario = _scenario(requires=("gamma",), solvable=False)
    declined = run_scenario(scenario, Scripted("s", ["finish"]))
    assert declined.solved is False
    assert declined.correct_stop is True
    assert declined.wasted_calls == 0

    churned = run_scenario(scenario, Scripted("s", ["cheap_tool", "specialist", "finish"]))
    assert churned.correct_stop is False


def test_transport_error_is_recorded_not_raised():
    result = run_scenario(_scenario(), Exploding())
    assert result.error is not None and "transport_error" in result.error
    assert result.solved is False


def test_the_orchestrator_sees_a_finish_tool_and_every_callable():
    scripted = Scripted("s", ["finish"])
    run_scenario(_scenario(), scripted)
    offered = scripted.seen[0]
    assert "finish" in offered
    assert {"cheap_tool", "specialist", "generalist"} <= set(offered)


def test_result_is_json_serializable_and_replayable():
    result = run_scenario(_scenario(), Scripted("s", ["cheap_tool", "specialist"]))
    payload = result.to_dict()
    text = json.dumps(payload, sort_keys=True)
    assert "turns_detail" in text
    assert payload["schema" if "schema" in payload else "scenario_id"] == "s1"


def test_offered_tool_schemas_describe_kind_and_cost_is_not_leaked_as_a_hint():
    scenario = _scenario()
    schema = scenario.callables[2].to_openai_tool()
    assert schema["function"]["name"] == "generalist"
    # the kind is advertised; the cost is not (the orchestrator must infer value)
    assert "[generalist]" in schema["function"]["description"]
    assert "8" not in schema["function"]["description"]
