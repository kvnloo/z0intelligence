"""AODL/HOTL 0.2 binding for z0int specialist cascades.

AODL owns intent, topology, policies, budgets and authority.  z0int owns the
implementation that satisfies that contract.  This module intentionally does
*not* invent ``fly``, ``mushroomBody``, ``routine`` or ``cascade`` node kinds.
It compiles z0int into the existing HOTL 0.2 vocabulary without turning
implementation strategy into user intent.  Following the intent-contract
profile, the stable ``intentGraph`` declares the capability, participating
executor, verifier, evidence store, budgets and acceptance criteria.  The
actual routine -> specialist -> local-SLM -> frontier strategy lives in the
compiled ``plan``.

Implementation-specific model/checkpoint/provider bindings therefore live in
``plan``; runtime route/outcome receipts live in ``eventLog`` and concrete
runtime topology may be projected into optional ``observedGraph``.  This keeps
intent, compiled strategy and observed O_t separate.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from .cascade import CascadePolicy
from .routines import RoutineCandidate

SPEC_VERSION = "0.2"
# AODL nightly/intent-contract catalog: executor ids only. o8 is a control-room;
# firstmate is a distro. Unknown ids fail closed, matching the upstream validator.
AODL_EXECUTOR_HARNESS_IDS = frozenset({"hermes", "omp", "grok", "codex", "claude", "pi", "fx"})
AODL_NODE_KINDS = frozenset(
    {
        "task",
        "executor",
        "model",
        "tool",
        "service",
        "memory",
        "stateStore",
        "humanGate",
        "environment",
        "artifact",
        "verifier",
    }
)
AODL_EDGE_RELATIONS = frozenset(
    {
        "dependency",
        "data",
        "message",
        "delegation",
        "critique",
        "verification",
        "allocation",
        "control",
        "observation",
        "artifact",
    }
)
_ID_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_.:-]{0,127}$")


@dataclass(frozen=True)
class AodlBudgets:
    """Declared Gamma budgets, never observed spend.

    ``premium_tokens`` is z0int/Kerdoios' scarce-frontier budget.  ``tokens``
    remains the portable HOTL token budget.  Extra dimensions are legal in
    HOTL 0.2's open ``constraints.budgets`` object.
    """

    tokens: int | None = None
    premium_tokens: int | None = None
    latency_ms: float | None = None
    usd: float | None = None
    joules: float | None = None
    attention: float | None = None

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for key, value in asdict(self).items():
            if value is not None:
                out[key] = value
        return out


@dataclass(frozen=True)
class AodlBindingConfig:
    harness_id: str = "omp"
    executor_id: str = "z0int-runtime"
    verifier_id: str = "outcome-verifier"
    receipt_store_id: str = "decision-receipts"
    routine_service_id: str = "routine-service"
    routine_artifact_id: str = "routine-registry"
    revision: int = 0
    precision_floor: float = 0.95
    budgets: AodlBudgets = field(default_factory=AodlBudgets)
    stage_bindings: Mapping[str, Mapping[str, Any]] = field(default_factory=dict)
    stage_roles: Mapping[str, str] = field(default_factory=dict)
    include_routine_slot: bool = True
    allow_uncredited_shadow: bool = False
    include_candidate_routines_in_shadow: bool = False
    source: str = "z0int"
    memory_id: str | None = None
    memory_schema: str = "MemoryContext"
    memory_binding: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        for value in (
            self.executor_id,
            self.verifier_id,
            self.receipt_store_id,
            self.routine_service_id,
            self.routine_artifact_id,
        ):
            _require_id(value)
        if self.harness_id not in AODL_EXECUTOR_HARNESS_IDS:
            raise ValueError(
                f"unknown/non-executor AODL harness_id {self.harness_id!r}; "
                f"expected one of {sorted(AODL_EXECUTOR_HARNESS_IDS)}"
            )
        if self.memory_id is not None:
            _require_id(self.memory_id)
            if not self.memory_schema.strip():
                raise ValueError("memory_schema must be non-empty when memory_id is set")
        if self.revision < 0:
            raise ValueError("revision must be >= 0")
        if not 0.0 < self.precision_floor <= 1.0:
            raise ValueError("precision_floor must be in (0, 1]")


def _require_id(value: str) -> str:
    if not _ID_RE.match(value):
        raise ValueError(f"invalid AODL id {value!r}")
    return value


def _safe_id(prefix: str, value: str) -> str:
    raw = re.sub(r"[^A-Za-z0-9_.:-]+", "-", value).strip("-._:") or "node"
    if not raw[0].isalpha():
        raw = f"n-{raw}"
    return _require_id(f"{prefix}{raw}"[:128])


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def source_hash(value: Any) -> str:
    """Canonical sha256 used for AODL provenance and runtime event linkage."""

    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _port(pid: str, direction: str, schema: str, *, classification: str = "sanitized") -> dict[str, Any]:
    return {
        "id": pid,
        "direction": direction,
        "schema": schema,
        "classification": classification,
    }


def _node(
    node_id: str,
    kind: str,
    ports: Sequence[dict[str, Any]],
    capabilities: Sequence[str],
    *,
    authority: Sequence[str] | None = None,
    harness: str | None = None,
    lifecycle: str = "declared",
) -> dict[str, Any]:
    _require_id(node_id)
    if kind not in AODL_NODE_KINDS:
        raise ValueError(f"unknown AODL node kind {kind!r}")
    if harness is not None:
        if kind != "executor":
            raise ValueError("AODL harness is only allowed on executor nodes")
        if harness not in AODL_EXECUTOR_HARNESS_IDS:
            raise ValueError(f"unknown/non-executor AODL harness {harness!r}")
    out = {
        "id": node_id,
        "kind": kind,
        "ports": list(ports),
        "capabilities": list(capabilities),
        "authorityCeiling": list(authority if authority is not None else capabilities),
        "lifecycle": lifecycle,
    }
    if harness is not None:
        out["harness"] = harness
    return out


def _edge(
    eid: str,
    relation: str,
    frm: str,
    to: str,
    from_port: str,
    to_port: str,
    schema_hash: str,
    *,
    grant: Sequence[str] = (),
) -> dict[str, Any]:
    if relation not in AODL_EDGE_RELATIONS:
        raise ValueError(f"unknown AODL edge relation {relation!r}")
    return {
        "id": eid,
        "relation": relation,
        "from": frm,
        "to": to,
        "fromPort": from_port,
        "toPort": to_port,
        "delivery": {
            "order": "ordered",
            "idempotent": True,
            "timeoutMs": 60_000,
            "maxRetries": 0,
        },
        "authority": {"grant": list(grant), "delegationDepth": 0},
        "provenance": {"sourceHash": schema_hash},
    }


def _promoted_routines(routines: Iterable[RoutineCandidate]) -> list[RoutineCandidate]:
    return [r for r in routines if r.status == "promoted"]


def _stage_node_id(stage: str) -> str:
    return _safe_id("stage-", stage)


def _stage_binding(stage: str, config: AodlBindingConfig) -> dict[str, Any]:
    explicit = config.stage_bindings.get(stage)
    if explicit is not None:
        return dict(explicit)
    # Provider/model choice is intentionally left unbound here.  Kerdoios or a
    # harness compiler may resolve this residual model stage later.
    return {
        "binding": "unbound",
        "selector": {"stage": stage},
    }


def _logical_stage(stage: str, config: AodlBindingConfig) -> str:
    role = str(config.stage_roles.get(stage, stage))
    return role


@dataclass(frozen=True)
class AodlSpend:
    tokens: int = 0
    premium_tokens: int = 0
    latency_ms: float = 0.0
    usd: float = 0.0
    joules: float = 0.0
    attention: float = 0.0

    def plus(self, other: "AodlSpend") -> "AodlSpend":
        return AodlSpend(
            tokens=max(0, self.tokens + other.tokens),
            premium_tokens=max(0, self.premium_tokens + other.premium_tokens),
            latency_ms=max(0.0, self.latency_ms + other.latency_ms),
            usd=max(0.0, self.usd + other.usd),
            joules=max(0.0, self.joules + other.joules),
            attention=max(0.0, self.attention + other.attention),
        )


@dataclass(frozen=True)
class BudgetCheck:
    allowed: bool
    exceeded: tuple[str, ...] = ()
    projected: AodlSpend = AodlSpend()


@dataclass(frozen=True)
class AodlRuntimeContract:
    """Runtime-facing view of a compiled AODL document.

    This is the inverse edge of the bridge: harness/z0int code can consume the
    AODL contract without learning the rest of the AODL schema.
    """

    graph_id: str
    revision: int
    capability_id: str
    harness_id: str
    route_order: tuple[str, ...]
    bindings: dict[str, Any]
    budgets: dict[str, Any]
    acceptance: dict[str, Any]
    source_hash: str


def compile_aodl(
    *,
    capability_id: str,
    cascade: CascadePolicy,
    routines: Sequence[RoutineCandidate] = (),
    config: AodlBindingConfig | None = None,
) -> dict[str, Any]:
    """Compile a z0int capability into the AODL intent-contract profile.

    This intentionally keeps three objects separate:

    * intent contract: ``intentGraph`` + policies + Gamma constraints;
    * compiled strategy: routine/specialist/SLM/frontier bindings in ``plan``;
    * observed runtime: append-only ``eventLog`` and optional ``observedGraph``.

    Evolution Lab may change checkpoints, thresholds, routines or providers
    without changing the intent contract or its provenance hash.
    """

    cfg = config or AodlBindingConfig()
    if cascade.capability_id != capability_id:
        raise ValueError("cascade capability_id does not match")
    shadow = cascade.status != "promoted"
    if shadow and not cfg.allow_uncredited_shadow:
        raise ValueError(
            "production AODL compile requires a promoted cascade; "
            "set allow_uncredited_shadow=True for explicit shadow evaluation"
        )

    if cfg.include_candidate_routines_in_shadow and not shadow:
        raise ValueError("candidate routine bindings are shadow-only")
    promoted = (
        [r for r in routines if r.status in {"candidate", "credited", "promoted"}]
        if cfg.include_candidate_routines_in_shadow else _promoted_routines(routines)
    )
    if any(r.capability_id != capability_id for r in promoted):
        raise ValueError("routine capability_id does not match the compiled capability")
    # Intent provenance excludes implementation details. A new champion, routine
    # or threshold changes planHash, not sourceHash.
    intent_material = {
        "profile": "intent-contract",
        "capability_id": capability_id,
        "harness_id": cfg.harness_id,
        "revision": cfg.revision,
        "budgets": cfg.budgets.to_dict(),
        "precision_floor": cfg.precision_floor,
        "max_success_regression": cascade.max_success_regression,
        "objective": cascade.objective,
        "privacy": {"personalArtifacts": "local_only", "receipts": "confidential"},
    }
    if cfg.memory_id is not None:
        intent_material["memory"] = {"id": cfg.memory_id, "schema": cfg.memory_schema}
    sh = source_hash(intent_material)

    task_id = _safe_id("capability-", capability_id)
    # Stable intent/participation graph. Do not put routine/model cascade stages
    # here: they are strategy and belong to the compiled plan.
    executor_ports = [
        _port("in", "in", "DecisionRequest"),
        _port("decision", "out", "DecisionResult"),
    ]
    if cfg.memory_id is not None:
        executor_ports.append(_port("memory", "in", cfg.memory_schema, classification="sanitized"))

    nodes: list[dict[str, Any]] = [
        _node(
            task_id,
            "task",
            [_port("out", "out", "DecisionRequest")],
            ["request"],
            authority=["request"],
        ),
        _node(
            cfg.executor_id,
            "executor",
            executor_ports,
            ["execute"],
            authority=["execute"],
            harness=cfg.harness_id,
        ),
        _node(
            cfg.verifier_id,
            "verifier",
            [
                _port("in", "in", "DecisionResult"),
                _port("out", "out", "VerifiedOutcome"),
            ],
            ["verify"],
            authority=["verify"],
        ),
        _node(
            cfg.receipt_store_id,
            "stateStore",
            [_port("in", "in", "VerifiedOutcome", classification="confidential")],
            ["store"],
            authority=["store"],
        ),
    ]
    if cfg.memory_id is not None:
        nodes.append(
            _node(
                cfg.memory_id,
                "memory",
                [_port("out", "out", cfg.memory_schema, classification="sanitized")],
                ["recall"],
                authority=["recall"],
            )
        )

    edges: list[dict[str, Any]] = [
        _edge(
            "e-task-runtime",
            "dependency",
            task_id,
            cfg.executor_id,
            "out",
            "in",
            sh,
            grant=["execute"],
        ),
        _edge(
            "e-runtime-verify",
            "verification",
            cfg.executor_id,
            cfg.verifier_id,
            "decision",
            "in",
            sh,
            grant=["verify"],
        ),
        _edge(
            "e-verify-receipts",
            "observation",
            cfg.verifier_id,
            cfg.receipt_store_id,
            "out",
            "in",
            sh,
            grant=["store"],
        ),
    ]
    if cfg.memory_id is not None:
        edges.append(
            _edge(
                "e-memory-runtime",
                "data",
                cfg.memory_id,
                cfg.executor_id,
                "out",
                "memory",
                sh,
                grant=[],
            )
        )

    # Everything below is strategy/implementation and therefore compiled plan.
    bindings: dict[str, Any] = {
        cfg.executor_id: {
            "runtime": "z0int",
            "entrypoint": "preflight",
            "harnessId": cfg.harness_id,
        },
        cfg.verifier_id: {"runtime": "z0int", "entrypoint": "outcome"},
        cfg.receipt_store_id: {"runtime": "z0int", "uri": "z0int://receipts"},
    }
    if cfg.memory_id is not None:
        bindings[cfg.memory_id] = {
            "kind": "memory",
            "schema": cfg.memory_schema,
            **dict(cfg.memory_binding),
        }
    execution_nodes: list[dict[str, Any]] = []
    route_order: list[str] = []

    if cfg.include_routine_slot:
        bindings[cfg.routine_artifact_id] = {
            "kind": "artifact",
            "artifactType": "z0int.routine_registry.v1",
            "uri": "z0int://routines",
            "routineIds": sorted(r.routine_id for r in promoted),
            "routineArtifacts": {
                r.routine_id: {"sha256": source_hash(r.to_dict()), "status": r.status}
                for r in sorted(promoted, key=lambda r: r.routine_id)
            },
            "enabled": bool(promoted),
        }
        bindings[cfg.routine_service_id] = {
            "kind": "service",
            "runtime": "z0int",
            "entrypoint": (
                "routine_registry.decide_shadow" if cfg.include_candidate_routines_in_shadow
                else "routine_registry.decide"
            ),
            "enabled": bool(promoted),
            "failOpen": "next_route_stage",
            "artifact": cfg.routine_artifact_id,
        }
        execution_nodes.append({
            "id": cfg.routine_service_id,
            "kind": "service",
            "binding": cfg.routine_service_id,
        })
        route_order.append(cfg.routine_service_id)

    seen_logical: set[str] = set()
    for stage in cascade.stages:
        logical = _logical_stage(stage.name, cfg)
        sid = _stage_node_id(logical)
        if sid in seen_logical:
            raise ValueError(f"multiple implementation stages map to logical AODL slot {logical!r}")
        seen_logical.add(sid)
        bindings[sid] = {
            "kind": "model",
            **_stage_binding(stage.name, cfg),
            "implementationStage": stage.name,
            "logicalRole": logical,
            "enabled": True,
            "minConfidence": stage.min_confidence,
            "terminal": stage.terminal,
        }
        execution_nodes.append({"id": sid, "kind": "model", "binding": sid})
        route_order.append(sid)

    final_node = _stage_node_id(_logical_stage(cascade.final_stage, cfg))
    policies = {
        "kinds": ["sequence", "retry"],
        "fanIn": "all",
        "dynamic": {"allowed": False, "maxChildren": 0, "maxDepth": 0},
        "protocol": "intent-contract",
    }
    constraints = {
        "budgets": cfg.budgets.to_dict(),
        "termination": {"on": f"{cfg.verifier_id}.succeeded"},
        "acceptance": {
            "capabilityId": capability_id,
            "precisionFloor": cfg.precision_floor,
            "maxSuccessRegression": cascade.max_success_regression,
            "objective": cascade.objective,
        },
        "privacy": {
            "personalArtifacts": "local_only",
            "receipts": "confidential",
        },
    }
    deployment = {
        "mode": "shadow" if shadow else "production",
        "creditStatus": cascade.status,
        "trafficEligible": not shadow,
    }
    plan_material = {
        "deployment": deployment,
        "bindings": bindings,
        "route_order": route_order,
        "final": final_node,
        "sourceSchemas": {
            "cascade": cascade.schema,
            "routine": "z0int.routine_candidate.v1",
        },
    }
    plan_hash = source_hash(plan_material)
    plan = {
        "compiler": "z0int.aodl.v2",
        "profile": "intent-contract",
        "deployment": deployment,
        "harnessId": cfg.harness_id,
        "sourceHash": sh,
        "planHash": plan_hash,
        "bindings": bindings,
        "executionGraph": {
            "nodes": execution_nodes,
            "routeOrder": route_order,
        },
        "route": {
            "order": route_order,
            "thresholds": {
                _stage_node_id(_logical_stage(s.name, cfg)): s.min_confidence
                for s in cascade.stages
                if not s.terminal
            },
            "final": final_node,
            "strategy": "first_eligible",
            "abstain": "next",
        },
        "sourceSchemas": plan_material["sourceSchemas"],
        "note": (
            "Intent contract is stable; z0int/Evolution Lab may change this compiled "
            "strategy. Provider placement for residual model stages may be resolved by Kerdoios."
        ),
    }
    doc = {
        "specVersion": SPEC_VERSION,
        "graphId": _safe_id("z0int-", capability_id),
        "revision": cfg.revision,
        "intentGraph": {"nodes": nodes, "edges": edges},
        "policies": policies,
        "constraints": constraints,
        "provenance": {"source": cfg.source, "sourceHash": sh},
        "plan": plan,
        "eventLog": [],
    }
    assert_basic_aodl_invariants(doc)
    return doc


def project_observed_graph(
    doc: Mapping[str, Any],
    *,
    stage: str,
    lifecycle: str = "running",
) -> dict[str, Any]:
    """Project one concrete z0int runtime choice into AODL ``observedGraph``.

    The observed graph may contain implementation participants that are absent
    from the stable intent graph. Policies remain on the intent document.
    """

    contract = runtime_contract(doc)
    if lifecycle not in {"declared", "ready", "running", "succeeded", "failed", "cancelled"}:
        raise ValueError(f"invalid AODL lifecycle {lifecycle!r}")
    if stage not in contract.bindings:
        raise ValueError(f"unknown compiled stage {stage!r}")
    binding = contract.bindings[stage]
    kind = str(binding.get("kind") or ("service" if stage == "routine-service" else "model"))
    if kind not in {"service", "model"}:
        raise ValueError(f"observed route stage kind {kind!r} is not executable")

    executor = _node(
        "observed-z0int",
        "executor",
        [_port("out", "out", "StageRequest")],
        ["execute"],
        authority=["execute"],
        harness=contract.harness_id,
        lifecycle="running",
    )
    selected = _node(
        _safe_id("observed-", stage),
        kind,
        [
            _port("in", "in", "StageRequest"),
            _port("out", "out", "DecisionResult"),
        ],
        ["evaluate" if kind == "service" else "infer"],
        lifecycle=lifecycle,
    )
    verifier = _node(
        "observed-verifier",
        "verifier",
        [_port("in", "in", "DecisionResult")],
        ["verify"],
        authority=["verify"],
        lifecycle="ready",
    )
    sh = contract.source_hash
    graph = {
        "nodes": [executor, selected, verifier],
        "edges": [
            _edge(
                "e-observed-route",
                "allocation",
                executor["id"],
                selected["id"],
                "out",
                "in",
                sh,
                grant=["evaluate" if kind == "service" else "infer"],
            ),
            _edge(
                "e-observed-verify",
                "verification",
                selected["id"],
                verifier["id"],
                "out",
                "in",
                sh,
                grant=["verify"],
            ),
        ],
    }
    return graph


def with_observed_graph(
    doc: Mapping[str, Any],
    *,
    stage: str,
    lifecycle: str = "running",
) -> dict[str, Any]:
    out = dict(doc)
    out["observedGraph"] = project_observed_graph(doc, stage=stage, lifecycle=lifecycle)
    assert_basic_aodl_invariants(out)
    return out

def assert_basic_aodl_invariants(doc: Mapping[str, Any]) -> None:
    """Small local guard; AODL's own validator remains authoritative.

    This deliberately checks only the invariants z0int itself could violate and
    does not attempt to fork AODL's validator.
    """

    required = {"specVersion", "graphId", "revision", "intentGraph", "policies", "constraints", "provenance"}
    missing = required - set(doc)
    if missing:
        raise ValueError(f"AODL document missing {sorted(missing)}")
    if doc["specVersion"] != SPEC_VERSION:
        raise ValueError("z0int only emits HOTL/AODL 0.2")
    _require_id(str(doc["graphId"]))
    def _check_graph(graph: Mapping[str, Any], *, label: str) -> None:
        nodes = list(graph["nodes"])
        edges = list(graph["edges"])
        index = {n["id"]: n for n in nodes}
        if len(index) != len(nodes):
            raise ValueError(f"duplicate AODL node id in {label}")
        for node in nodes:
            if node["kind"] not in AODL_NODE_KINDS:
                raise ValueError(f"non-AODL node kind {node['kind']!r}")
            if "harness" in node:
                if node["kind"] != "executor":
                    raise ValueError("AODL harness is only allowed on executor nodes")
                if node["harness"] not in AODL_EXECUTOR_HARNESS_IDS:
                    raise ValueError(f"unknown/non-executor AODL harness {node['harness']!r}")
            if node["kind"] == "verifier":
                held = {str(x).lower() for x in node.get("capabilities", [])} | {
                    str(x).lower() for x in node.get("authorityCeiling", [])
                }
                if held & {"merge", "deploy", "approve"}:
                    raise ValueError("AODL verifier cannot hold irreversible human-gate authority")
        for edge in edges:
            if edge["relation"] not in AODL_EDGE_RELATIONS:
                raise ValueError(f"non-AODL edge relation {edge['relation']!r}")
            if edge["from"] not in index or edge["to"] not in index:
                raise ValueError("AODL edge endpoint missing")
            fp = {p["id"]: p for p in index[edge["from"]]["ports"]}[edge["fromPort"]]
            tp = {p["id"]: p for p in index[edge["to"]]["ports"]}[edge["toPort"]]
            if fp["direction"] != "out" or tp["direction"] != "in":
                raise ValueError("AODL edge port direction invalid")
            if fp["schema"] != tp["schema"]:
                raise ValueError("AODL edge port schemas do not match")
            grant = {str(x).lower() for x in edge.get("authority", {}).get("grant", [])}
            if index[edge["to"]]["kind"] == "verifier" and grant & {"merge", "deploy", "approve"}:
                raise ValueError("AODL verifier cannot receive irreversible human-gate authority")
            if (
                edge["relation"] == "delegation"
                and index[edge["from"]]["kind"] == "humanGate"
                and index[edge["to"]]["kind"] == "executor"
            ):
                raise ValueError("AODL humanGate identity cannot be delegated to an executor")

    _check_graph(doc["intentGraph"], label="intentGraph")
    if "observedGraph" in doc:
        observed = doc["observedGraph"]
        if not isinstance(observed, Mapping):
            raise ValueError("observedGraph must be an object")
        _check_graph(observed, label="observedGraph")
    allowed_events = {
        "spawn", "bind", "route", "retry", "cancel", "addNode", "removeNode",
        "addEdge", "removeEdge", "stateUpdate", "snapshot",
    }
    for event in doc.get("eventLog", []):
        if event.get("type") not in allowed_events:
            raise ValueError(f"unknown AODL event type {event.get('type')!r}")
    budgets = doc["constraints"].get("budgets")
    termination = doc["constraints"].get("termination")
    if not isinstance(budgets, dict) or not isinstance(termination, dict):
        raise ValueError("AODL Gamma requires budgets and termination")


def runtime_contract(doc: Mapping[str, Any]) -> AodlRuntimeContract:
    """Extract the z0int runtime contract from a compiled AODL document."""

    assert_basic_aodl_invariants(doc)
    plan = doc.get("plan")
    if not isinstance(plan, Mapping) or plan.get("compiler") not in {"z0int.aodl.v1", "z0int.aodl.v2"}:
        raise ValueError("AODL document has no z0int compiled plan")
    route = plan.get("route")
    bindings = plan.get("bindings")
    if not isinstance(route, Mapping) or not isinstance(bindings, Mapping):
        raise ValueError("z0int plan requires route and bindings")
    acceptance = doc.get("constraints", {}).get("acceptance", {})
    if not isinstance(acceptance, Mapping) or not acceptance.get("capabilityId"):
        raise ValueError("AODL constraints.acceptance.capabilityId is required")
    return AodlRuntimeContract(
        graph_id=str(doc["graphId"]),
        revision=int(doc["revision"]),
        capability_id=str(acceptance["capabilityId"]),
        harness_id=str(plan.get("harnessId") or ""),
        route_order=tuple(str(x) for x in route.get("order") or ()),
        bindings={str(k): dict(v) for k, v in bindings.items() if isinstance(v, Mapping)},
        budgets=dict(doc.get("constraints", {}).get("budgets") or {}),
        acceptance=dict(acceptance),
        source_hash=str(doc.get("provenance", {}).get("sourceHash") or ""),
    )


def spend_from_decision(decision: Any) -> AodlSpend:
    """Project a z0int cascade decision into Gamma accounting dimensions."""

    return AodlSpend(
        tokens=max(0, int(getattr(decision, "total_tokens", 0) or 0)),
        premium_tokens=max(0, int(getattr(decision, "premium_tokens", 0) or 0)),
        latency_ms=max(0.0, float(getattr(decision, "latency_ms", 0.0) or 0.0)),
    )


def check_budget(
    contract: AodlRuntimeContract,
    *,
    observed: AodlSpend | None = None,
    proposed: AodlSpend | None = None,
) -> BudgetCheck:
    """Fail-closed Gamma guard for cumulative z0int spend.

    A missing dimension is unbounded. Declared budgets are never rewritten as
    observed spend; callers keep the latter separately and pass it back here.
    """

    current = observed or AodlSpend()
    delta = proposed or AodlSpend()
    projected = current.plus(delta)
    exceeded: list[str] = []
    fields = {
        "tokens": projected.tokens,
        "premium_tokens": projected.premium_tokens,
        "latency_ms": projected.latency_ms,
        "usd": projected.usd,
        "joules": projected.joules,
        "attention": projected.attention,
    }
    for key, actual in fields.items():
        limit = contract.budgets.get(key)
        if limit is None:
            continue
        try:
            lim = float(limit)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"AODL budget {key!r} must be numeric") from exc
        if actual > lim + 1e-12:
            exceeded.append(key)
    return BudgetCheck(allowed=not exceeded, exceeded=tuple(exceeded), projected=projected)


def binding_for_implementation_stage(contract: AodlRuntimeContract, stage: str) -> tuple[str, dict[str, Any]] | None:
    """Resolve an implementation stage through the AODL compiled plan."""

    for node_id in contract.route_order:
        binding = contract.bindings.get(node_id)
        if isinstance(binding, dict) and binding.get("implementationStage") == stage:
            return node_id, binding
    return None


def _event_id(kind: str, trace_id: str, revision: int, payload: Mapping[str, Any]) -> str:
    digest = hashlib.blake2b(
        _canonical({"kind": kind, "trace": trace_id, "revision": revision, "payload": payload}).encode("utf-8"),
        digest_size=10,
    ).hexdigest()
    return f"{kind}-{digest}"


def route_event(
    *,
    trace_id: str,
    source_hash_hex: str,
    revision: int,
    stage: str,
    capability_id: str,
    confidence: float | None = None,
    routine_id: str | None = None,
    causal_parents: Sequence[str] = (),
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "traceId": trace_id,
        "capabilityId": capability_id,
        "stage": stage,
    }
    if confidence is not None:
        payload["confidence"] = float(confidence)
    if routine_id is not None:
        payload["routineId"] = routine_id
    return {
        "eventId": _event_id("route", trace_id, revision, payload),
        "type": "route",
        "sourceHash": source_hash_hex,
        "revision": revision,
        "causalParents": list(causal_parents),
        "payload": payload,
    }


def outcome_event(
    *,
    trace_id: str,
    source_hash_hex: str,
    revision: int,
    success: bool,
    verifier: str,
    premium_tokens: int = 0,
    total_tokens: int = 0,
    latency_ms: float | None = None,
    causal_parents: Sequence[str] = (),
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "traceId": trace_id,
        "verified": True,
        "success": bool(success),
        "verifier": verifier,
        "spend": {
            "premiumTokens": max(0, int(premium_tokens)),
            "tokens": max(0, int(total_tokens)),
        },
    }
    if latency_ms is not None:
        payload["latencyMs"] = max(0.0, float(latency_ms))
    return {
        "eventId": _event_id("stateUpdate", trace_id, revision, payload),
        "type": "stateUpdate",
        "sourceHash": source_hash_hex,
        "revision": revision,
        "causalParents": list(causal_parents),
        "payload": payload,
    }


def append_event(doc: dict[str, Any], event: Mapping[str, Any]) -> dict[str, Any]:
    if event.get("sourceHash") != doc.get("provenance", {}).get("sourceHash"):
        raise ValueError("event sourceHash must match compiled AODL document")
    out = json.loads(json.dumps(doc))
    out.setdefault("eventLog", []).append(dict(event))
    return out


def load_routines_jsonl(path: Path) -> list[RoutineCandidate]:
    rows: list[RoutineCandidate] = []
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                rows.append(RoutineCandidate.from_dict(json.loads(line)))
    return rows


def _main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m z0int.aodl")
    sub = parser.add_subparsers(dest="cmd", required=True)

    comp = sub.add_parser("compile")
    comp.add_argument("--capability", required=True)
    comp.add_argument("--cascade", type=Path, required=True)
    comp.add_argument("--routines", type=Path)
    comp.add_argument("--output", type=Path, required=True)
    comp.add_argument("--harness", default="omp")
    comp.add_argument("--tokens", type=int)
    comp.add_argument("--premium-tokens", type=int)
    comp.add_argument("--latency-ms", type=float)
    comp.add_argument("--precision-floor", type=float, default=0.95)

    args = parser.parse_args(argv)
    if args.cmd == "compile":
        cascade = CascadePolicy.from_dict(json.loads(args.cascade.read_text(encoding="utf-8")))
        routines = load_routines_jsonl(args.routines) if args.routines else []
        cfg = AodlBindingConfig(
            harness_id=args.harness,
            precision_floor=args.precision_floor,
            budgets=AodlBudgets(
                tokens=args.tokens,
                premium_tokens=args.premium_tokens,
                latency_ms=args.latency_ms,
            ),
        )
        doc = compile_aodl(capability_id=args.capability, cascade=cascade, routines=routines, config=cfg)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(doc, indent=2) + "\n", encoding="utf-8")
        print(json.dumps({"ok": True, "output": str(args.output), "sourceHash": doc["provenance"]["sourceHash"]}, indent=2))
        return 0
    raise AssertionError(args.cmd)


if __name__ == "__main__":
    raise SystemExit(_main())
