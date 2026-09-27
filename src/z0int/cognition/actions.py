"""Deterministic legal-action compiler.

This module is the *compiler before the model*. It is pure stdlib, side-effect
free and fully replayable: given the same graph, facts, authority and budget it
returns the same :class:`LegalActionSet`, with a reason recorded for every
elimination.

Hard rule (z0intelligence#20): a learned controller may only choose among the
actions this module already declared legal. Rejecting an action here is
authoritative; no backend can re-admit it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, Literal, Mapping, Sequence
import hashlib
import json

RiskClass = Literal["read", "write", "destructive", "publish", "credential", "payment"]
ActionKind = Literal["tool", "model", "control"]
Stage = Literal["capability", "dependency", "permission", "budget", "shortcut"]

# Risk classes that carry real-world side effects. Authority is granted
# explicitly per risk class and is never inferred from a model decision.
IRREVERSIBLE_RISKS: frozenset[str] = frozenset(
    {"destructive", "publish", "credential", "payment"}
)

#: Risk classes ordered least- to most-consequential. Public so the candidate
#: capability model can gate on the *same* ordering the compiler uses instead of
#: re-declaring it.
RISK_CLASSES: tuple[str, ...] = (
    "read",
    "write",
    "destructive",
    "credential",
    "payment",
    "publish",
)

_RISK_RANK = {name: rank for rank, name in enumerate(RISK_CLASSES)}


def risk_rank(risk_class: str) -> int:
    """Position of ``risk_class`` in :data:`RISK_CLASSES`; raises when unknown."""
    try:
        return _RISK_RANK[risk_class]
    except KeyError as exc:
        raise ValueError(f"unsupported risk class: {risk_class!r}") from exc


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


@dataclass(frozen=True)
class ActionCandidate:
    """One thing the runtime could do, before any learned selection."""

    action_id: str
    kind: ActionKind
    description: str
    tool: str | None = None
    family: str | None = None
    requires: tuple[str, ...] = ()
    provides: tuple[str, ...] = ()
    required_capabilities: tuple[str, ...] = ()
    risk_class: RiskClass = "read"
    cost_units: int = 1
    parallel_safe: bool = True
    arguments_schema: Mapping[str, Any] | None = None
    escalates_to: str | None = None

    def __post_init__(self) -> None:
        if not self.action_id.strip():
            raise ValueError("action_id must be nonempty")
        if self.kind not in ("tool", "model", "control"):
            raise ValueError(f"unsupported action kind: {self.kind!r}")
        if self.risk_class not in _RISK_RANK:
            raise ValueError(f"unsupported risk class: {self.risk_class!r}")
        if self.cost_units < 0:
            raise ValueError("cost_units must be >= 0")
        if self.kind == "tool" and not (self.tool or "").strip():
            raise ValueError(f"tool action {self.action_id!r} requires a tool name")

    @property
    def irreversible(self) -> bool:
        return self.risk_class in IRREVERSIBLE_RISKS

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "action_id": self.action_id,
            "kind": self.kind,
            "description": self.description,
            "risk_class": self.risk_class,
            "cost_units": self.cost_units,
            "parallel_safe": self.parallel_safe,
        }
        if self.tool:
            out["tool"] = self.tool
        if self.family:
            out["family"] = self.family
        if self.requires:
            out["requires"] = list(self.requires)
        if self.provides:
            out["provides"] = list(self.provides)
        if self.required_capabilities:
            out["required_capabilities"] = list(self.required_capabilities)
        if self.arguments_schema is not None:
            out["arguments_schema"] = dict(self.arguments_schema)
        if self.escalates_to:
            out["escalates_to"] = self.escalates_to
        return out


@dataclass(frozen=True)
class Rule:
    """A declarative deterministic shortcut.

    ``when`` is a conjunction of exact fact matches against the observed state.
    A matching rule selects ``choose`` without consulting any learned backend.
    """

    id: str
    when: Mapping[str, Any]
    choose: str
    rationale: str = ""

    def matches(self, facts: Mapping[str, Any]) -> bool:
        return all(facts.get(k) == v for k, v in self.when.items())

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "when": dict(self.when),
            "choose": self.choose,
            "rationale": self.rationale,
        }


@dataclass(frozen=True)
class ActionGraph:
    """Declared candidate actions plus their hard dependencies."""

    actions: tuple[ActionCandidate, ...]
    rules: tuple[Rule, ...] = ()

    def __post_init__(self) -> None:
        ids = [a.action_id for a in self.actions]
        if len(ids) != len(set(ids)):
            raise ValueError("action ids must be unique")
        known = set(ids)
        for a in self.actions:
            missing = [d for d in a.requires if d not in known]
            if missing:
                raise ValueError(
                    f"action {a.action_id!r} requires unknown action(s): {missing}"
                )
            if a.action_id in a.requires:
                raise ValueError(f"action {a.action_id!r} cannot depend on itself")
        rule_ids = [r.id for r in self.rules]
        if len(rule_ids) != len(set(rule_ids)):
            raise ValueError("rule ids must be unique")
        for r in self.rules:
            if r.choose not in known:
                raise ValueError(f"rule {r.id!r} chooses unknown action {r.choose!r}")
        self._assert_acyclic()

    def _assert_acyclic(self) -> None:
        by_id = {a.action_id: a for a in self.actions}
        state: dict[str, int] = {}

        def visit(node: str) -> None:
            colour = state.get(node, 0)
            if colour == 1:
                raise ValueError(f"dependency cycle detected at {node!r}")
            if colour == 2:
                return
            state[node] = 1
            for dep in by_id[node].requires:
                visit(dep)
            state[node] = 2

        for a in self.actions:
            visit(a.action_id)

    def by_id(self, action_id: str) -> ActionCandidate:
        for a in self.actions:
            if a.action_id == action_id:
                return a
        raise KeyError(action_id)

    def digest(self) -> str:
        payload = [a.to_dict() for a in self.actions]
        rules = [r.to_dict() for r in self.rules]
        return hashlib.sha256(_canonical({"actions": payload, "rules": rules}).encode()).hexdigest()[:16]


@dataclass(frozen=True)
class Eliminated:
    action_id: str
    stage: Stage
    reason: str

    def to_dict(self) -> dict[str, Any]:
        return {"action_id": self.action_id, "stage": self.stage, "reason": self.reason}


@dataclass(frozen=True)
class LegalActionSet:
    """The only thing a learned backend is ever allowed to see."""

    legal: tuple[ActionCandidate, ...]
    blocked: tuple[str, ...]
    eliminated: tuple[Eliminated, ...]
    deterministic_solution: str | None
    deterministic_reason: str | None
    budget_units: int
    spent_units: int
    graph_digest: str
    authority: tuple[str, ...] = ()
    facts: Mapping[str, Any] = field(default_factory=dict)

    @property
    def candidate_count(self) -> int:
        return len(self.legal)

    @property
    def is_empty(self) -> bool:
        return not self.legal

    def ids(self) -> tuple[str, ...]:
        return tuple(a.action_id for a in self.legal)

    def actions_for_kind(self, kind: ActionKind) -> tuple[ActionCandidate, ...]:
        return tuple(a for a in self.legal if a.kind == kind)

    def family_options(self) -> tuple[str, ...]:
        seen: list[str] = []
        for a in self.legal:
            if a.family and a.family not in seen:
                seen.append(a.family)
        return tuple(seen)

    def to_dict(self) -> dict[str, Any]:
        return {
            "graph_digest": self.graph_digest,
            "legal": [a.to_dict() for a in self.legal],
            "blocked": list(self.blocked),
            "eliminated": [e.to_dict() for e in self.eliminated],
            "deterministic_solution": self.deterministic_solution,
            "deterministic_reason": self.deterministic_reason,
            "budget_units": self.budget_units,
            "spent_units": self.spent_units,
            "authority": list(self.authority),
        }


def _dependency_blocked(graph: ActionGraph, action_id: str) -> str | None:
    """Return a reason string when a hard dependency is unsatisfiable."""
    by_id = {a.action_id: a for a in graph.actions}
    seen: set[str] = set()

    def walk(node: str) -> str | None:
        if node in seen:
            return None
        seen.add(node)
        action = by_id.get(node)
        if action is None:
            return f"depends on unknown action {node!r}"
        return None

    for dep in by_id[action_id].requires:
        reason = walk(dep)
        if reason:
            return reason
    return None


def _topo_rank(graph: ActionGraph, candidates: Sequence[ActionCandidate]) -> dict[str, int]:
    """Longest dependency path length; used as the primary deterministic priority."""
    allowed = {a.action_id for a in candidates}
    by_id = {a.action_id: a for a in graph.actions}
    memo: dict[str, int] = {}

    def depth(node: str) -> int:
        if node in memo:
            return memo[node]
        action = by_id[node]
        deps = [d for d in action.requires if d in allowed]
        memo[node] = 0 if not deps else 1 + max(depth(d) for d in deps)
        return memo[node]

    return {a.action_id: depth(a.action_id) for a in candidates}


def compile_actions(
    *,
    graph: ActionGraph,
    granted_capabilities: Iterable[str] = (),
    authority: Iterable[str] = (),
    budget_units: int = 0,
    facts: Mapping[str, Any] | None = None,
    satisfied: Iterable[str] = (),
) -> LegalActionSet:
    """Filter a declared graph down to the legal, ordered ready set.

    Stages, in order (each elimination is recorded, never silently dropped):

    1. ``capability`` — required runtime capabilities must be granted.
    2. ``dependency`` — hard dependencies must be satisfiable and already met,
       or already satisfied by the observed state.
    3. ``permission`` — risk class must be inside the granted authority.
    4. ``budget``    — cumulative cost must fit ``budget_units``.
    5. ``shortcut``  — deterministic rules / singleton may resolve the choice.
    """
    observed = dict(facts or {})
    granted = frozenset(granted_capabilities)
    auth = frozenset(authority)
    done = frozenset(satisfied)
    spent = sum(a.cost_units for a in graph.actions if a.action_id in done)

    eliminated: list[Eliminated] = []
    survivors: list[ActionCandidate] = []

    # Stage 1: capability.
    for a in graph.actions:
        missing = [c for c in a.required_capabilities if c not in granted]
        if missing:
            eliminated.append(
                Eliminated(a.action_id, "capability", f"missing capability: {', '.join(missing)}")
            )
            continue
        survivors.append(a)

    # Stage 2: dependency — already-done actions leave the ready set; actions
    # with unsatisfied dependencies are blocked (not eliminated).
    ready: list[ActionCandidate] = []
    blocked: list[str] = []
    satisfied_ids = {a.action_id for a in graph.actions if a.action_id in done}
    for a in survivors:
        broken = _dependency_blocked(graph, a.action_id)
        if broken:
            eliminated.append(Eliminated(a.action_id, "dependency", broken))
            continue
        if a.action_id in satisfied_ids:
            # Re-issuing a completed action is not a decision, it is a bug.
            eliminated.append(
                Eliminated(a.action_id, "dependency", "already satisfied by observed state")
            )
            continue
        pending = [d for d in a.requires if d not in satisfied_ids]
        if pending:
            blocked.append(a.action_id)
            continue
        ready.append(a)

    # Stage 3: permission.
    permitted: list[ActionCandidate] = []
    for a in ready:
        if a.risk_class not in auth:
            eliminated.append(
                Eliminated(
                    a.action_id,
                    "permission",
                    f"risk_class {a.risk_class!r} not in granted authority",
                )
            )
            continue
        permitted.append(a)

    # Stage 4: budget (cheapest-first admission keeps the outcome deterministic).
    ordered_for_budget = sorted(
        permitted, key=lambda a: (a.cost_units, _RISK_RANK[a.risk_class], a.action_id)
    )
    admitted: list[ActionCandidate] = []
    running = 0
    for a in ordered_for_budget:
        if running + a.cost_units > budget_units:
            eliminated.append(
                Eliminated(
                    a.action_id,
                    "budget",
                    f"cost {a.cost_units} exceeds remaining budget "
                    f"{max(0, budget_units - running)}",
                )
            )
            continue
        running += a.cost_units
        admitted.append(a)

    # Stage 5: deterministic priority ordering + shortcut resolution.
    ranks = _topo_rank(graph, admitted)
    legal = tuple(
        sorted(
            admitted,
            key=lambda a: (ranks[a.action_id], a.cost_units, _RISK_RANK[a.risk_class], a.action_id),
        )
    )

    solution: str | None = None
    reason: str | None = None
    for rule in graph.rules:
        if not rule.matches(observed):
            continue
        if rule.choose in {a.action_id for a in legal}:
            solution = rule.choose
            reason = f"rule:{rule.id}:{rule.rationale or 'matched'}"
            break
    if solution is None and len(legal) == 1:
        solution = legal[0].action_id
        reason = "singleton:exactly one legal action"

    return LegalActionSet(
        legal=legal,
        blocked=tuple(sorted(blocked)),
        eliminated=tuple(eliminated),
        deterministic_solution=solution,
        deterministic_reason=reason,
        budget_units=budget_units,
        spent_units=spent,
        graph_digest=graph.digest(),
        authority=tuple(sorted(auth)),
        facts=observed,
    )
