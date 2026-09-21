"""The compiler runs before any model. These tests are the authority contract."""

from __future__ import annotations

import pytest

from z0int.cognition.actions import (
    ActionCandidate,
    ActionGraph,
    Rule,
    compile_actions,
)


def _tool(action_id, **kw):
    kw.setdefault("tool", action_id)
    if kw.get("kind") is None:
        kw["kind"] = "tool"
    return ActionCandidate(action_id=action_id, **kw)


def _read(description="Read a file"):
    return _tool("fs.read", description=description, family="fs", risk_class="read")


def _graph(*actions, rules=()):
    return ActionGraph(actions=tuple(actions), rules=tuple(rules))


# --- stage 1: capability ------------------------------------------------


def test_missing_capability_eliminates_action_with_a_reason():
    graph = _graph(
        _tool("fs.read", description="read", required_capabilities=("fs.read",), family="fs"),
        _tool("net.fetch", description="fetch", required_capabilities=("net",), family="net"),
    )
    legal = compile_actions(
        graph=graph, granted_capabilities=("fs.read",), authority=("read",), budget_units=5
    )
    assert legal.ids() == ("fs.read",)
    (elim,) = legal.eliminated
    assert elim.action_id == "net.fetch"
    assert elim.stage == "capability"
    assert "net" in elim.reason


# --- stage 2: dependency ------------------------------------------------


def test_hard_dependency_keeps_action_blocked_not_ready():
    graph = _graph(
        _tool("a", description="write", family="fs", risk_class="write", provides=("x",)),
        _tool("b", description="verify", family="fs", requires=("a",)),
    )
    legal = compile_actions(
        graph=graph, granted_capabilities=(), authority=("read", "write"), budget_units=5
    )
    assert legal.ids() == ("a",)
    assert legal.blocked == ("b",)


def test_dependency_already_satisfied_admits_the_dependent():
    graph = _graph(
        _tool("a", description="write", family="fs", risk_class="write"),
        _tool("b", description="verify", family="fs", requires=("a",)),
    )
    legal = compile_actions(
        graph=graph,
        granted_capabilities=(),
        authority=("read", "write"),
        budget_units=5,
        satisfied=("a",),
    )
    # `a` is done, so it leaves the ready set rather than being re-offered.
    assert legal.ids() == ("b",)
    assert legal.spent_units == 1
    assert any(
        e.action_id == "a" and e.stage == "dependency" for e in legal.eliminated
    )


def test_unknown_dependency_is_rejected_at_graph_construction():
    with pytest.raises(ValueError, match="requires unknown action"):
        _graph(_tool("b", description="verify", requires=("nope",)))


def test_dependency_cycle_is_rejected():
    with pytest.raises(ValueError, match="cycle"):
        _graph(
            _tool("a", description="a", requires=("b",)),
            _tool("b", description="b", requires=("a",)),
        )


def test_diamond_dependency_orders_the_join_last():
    graph = _graph(
        _tool("root", description="root", family="fs"),
        _tool("left", description="left", requires=("root",), family="fs"),
        _tool("right", description="right", requires=("root",), family="fs"),
        _tool("join", description="join", requires=("left", "right"), family="fs"),
    )
    first_wave = compile_actions(
        graph=graph, granted_capabilities=(), authority=("read",), budget_units=10,
        satisfied=("root",),
    )
    assert set(first_wave.ids()) == {"left", "right"}
    assert "join" in first_wave.blocked

    second_wave = compile_actions(
        graph=graph, granted_capabilities=(), authority=("read",), budget_units=10,
        satisfied=("root", "left", "right"),
    )
    # left/right are done, so the join is the only remaining candidate.
    assert second_wave.ids() == ("join",)
    assert second_wave.deterministic_solution == "join"


# --- stage 3: permission (the security contract) ------------------------


def test_risk_class_outside_authority_is_never_legal():
    graph = _graph(
        _read(),
        _tool("mail.send", description="send mail", family="mail", risk_class="publish"),
        _tool("cred.read", description="read key", family="cred", risk_class="credential"),
        _tool("pay.buy", description="buy", family="pay", risk_class="payment"),
        _tool("fs.rm", description="delete", family="fs", risk_class="destructive"),
    )
    legal = compile_actions(
        graph=graph,
        granted_capabilities=(),
        authority=("read", "write"),
        budget_units=20,
    )
    assert legal.ids() == ("fs.read",)
    blocked = {e.action_id for e in legal.eliminated if e.stage == "permission"}
    assert blocked == {"mail.send", "cred.read", "pay.buy", "fs.rm"}


def test_granting_authority_is_the_only_way_a_destructive_action_becomes_legal():
    graph = _graph(_tool("fs.rm", description="delete", family="fs", risk_class="destructive"))
    denied = compile_actions(
        graph=graph, granted_capabilities=(), authority=("read",), budget_units=5
    )
    assert denied.is_empty
    granted = compile_actions(
        graph=graph,
        granted_capabilities=(),
        authority=("read", "destructive"),
        budget_units=5,
    )
    assert granted.ids() == ("fs.rm",)


# --- stage 4: budget ----------------------------------------------------


def test_budget_admits_cheapest_first_and_reports_the_rest():
    graph = _graph(
        _tool("cheap", description="cheap", family="fs", cost_units=1),
        _tool("mid", description="mid", family="fs", cost_units=3),
        _tool("pricey", description="pricey", family="fs", cost_units=9),
    )
    legal = compile_actions(
        graph=graph, granted_capabilities=(), authority=("read",), budget_units=4
    )
    assert set(legal.ids()) == {"cheap", "mid"}
    rejected = {e.action_id for e in legal.eliminated if e.stage == "budget"}
    assert rejected == {"pricey"}
    assert legal.spent_units == 0


# --- stage 5: deterministic shortcuts -----------------------------------


def test_singleton_legal_set_is_solved_deterministically():
    graph = _graph(_read())
    legal = compile_actions(
        graph=graph, granted_capabilities=(), authority=("read",), budget_units=1
    )
    assert legal.deterministic_solution == "fs.read"
    assert legal.deterministic_reason == "singleton:exactly one legal action"


def test_declared_rule_wins_without_any_model():
    graph = _graph(
        _read(),
        _tool("fs.write", description="write", family="fs", risk_class="write"),
        rules=(
            Rule(
                id="always-answer-from-state",
                when={"intent": "inspect"},
                choose="fs.read",
                rationale="inspection never needs a write",
            ),
        ),
    )
    legal = compile_actions(
        graph=graph,
        granted_capabilities=(),
        authority=("read", "write"),
        budget_units=5,
        facts={"intent": "inspect"},
    )
    assert legal.deterministic_solution == "fs.read"
    assert legal.deterministic_reason.startswith("rule:always-answer-from-state")


def test_rule_choosing_a_filtered_action_cannot_resurrect_it():
    graph = _graph(
        _read(),
        _tool("fs.rm", description="delete", family="fs", risk_class="destructive"),
        rules=(Rule(id="bad", when={"intent": "cleanup"}, choose="fs.rm"),),
    )
    legal = compile_actions(
        graph=graph,
        granted_capabilities=(),
        authority=("read",),
        budget_units=5,
        facts={"intent": "cleanup"},
    )
    # fs.rm was removed by the permission stage; the rule must not re-admit it.
    assert legal.deterministic_solution == "fs.read"
    assert "fs.rm" not in legal.ids()


# --- replayability ------------------------------------------------------


def test_compilation_is_deterministic_and_digest_stable():
    graph = _graph(
        _tool("a", description="a", family="fs"),
        _tool("b", description="b", family="fs"),
    )
    kwargs = dict(
        graph=graph, granted_capabilities=(), authority=("read",), budget_units=5
    )
    first = compile_actions(**kwargs)
    second = compile_actions(**kwargs)
    assert first.to_dict() == second.to_dict()
    assert first.graph_digest == second.graph_digest


def test_to_dict_is_json_serializable():
    import json

    graph = _graph(_read(), _tool("fs.write", description="w", family="fs", risk_class="write"))
    legal = compile_actions(
        graph=graph, granted_capabilities=(), authority=("read", "write"), budget_units=5
    )
    json.dumps(legal.to_dict())


def test_empty_legal_set_is_not_a_crash():
    graph = _graph(_tool("fs.rm", description="rm", family="fs", risk_class="destructive"))
    legal = compile_actions(
        graph=graph, granted_capabilities=(), authority=("read",), budget_units=5
    )
    assert legal.is_empty
    assert legal.deterministic_solution is None
    assert legal.candidate_count == 0
