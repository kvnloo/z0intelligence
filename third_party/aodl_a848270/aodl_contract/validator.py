"""HOTL/AODL validator core — importable, zero dependencies, fail-closed."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

WIRE_SPEC = "hotl-0.2"
_ROOT = Path(__file__).resolve().parent.parent


def spec_revision() -> str:
    return hashlib.sha256(Path(__file__).read_bytes()).hexdigest()[:16]


ID_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_.:-]{0,127}$")
HASH_RE = re.compile(r"^[a-f0-9]{64}$")

NODE_KINDS_02 = {
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
EDGE_REL_02 = {
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
EVENT_TYPES_02 = {
    "spawn",
    "bind",
    "route",
    "retry",
    "cancel",
    "addNode",
    "removeNode",
    "addEdge",
    "removeEdge",
    "stateUpdate",
    "snapshot",
}
NODE_ROLES_01 = {
    "orchestrator",
    "worker",
    "supervisor",
    "reviewer",
    "critic",
    "judge",
    "router",
    "humanGate",
    "tool",
    "service",
}
EDGE_KINDS_01 = {
    "dependency",
    "data",
    "message",
    "delegation",
    "review",
    "control",
    "observation",
    "artifact",
}
FAN_IN = {"all", "any", "quorum", "reducer"}
AUCTION_PHASES = ("announce", "bid", "award", "execute", "verify", "settle")
PRIVILEGED = frozenset(
    {
        "payment",
        "finance",
        "pay",
        "transfer",
        "wallet",
        "sudo",
        "install",
        "secret",
        "credential",
    }
)
# Irreversible gate actions. Review is a verifier; these stay on humanGate.
IRREVERSIBLE = frozenset({"merge", "deploy", "approve"})
CLOSED_02 = {
    "specVersion",
    "graphId",
    "revision",
    "intentGraph",
    "policies",
    "constraints",
    "provenance",
    "plan",
    "eventLog",
    "observedGraph",
}
REQUIRED_02 = (
    "specVersion",
    "graphId",
    "revision",
    "intentGraph",
    "policies",
    "constraints",
    "provenance",
)
REQUIRED_01 = (
    "specVersion",
    "graphId",
    "nodes",
    "edges",
    "policies",
    "provenance",
)

# filename stem -> substring that must appear in the failure
INVALID_EXPECT = {
    "unknown-version": "unknown specVersion",
    "unbounded-recursion": "unbounded",
    "implicit-fan-in": "implicit fan-in",
    "missing-port": "unknown port",
    "self-edge": "self-edge",
    "dependency-cycle": "dependency cycle",
    "craid-feedback-cycle": "dependency cycle",
    "payment-execution": "payment execution",
    "hidden-privilege": "privileged capability",
    "missing-endpoint": "unknown node",
    "isolated-node": "isolated node",
    "control-room-as-executor": "control-room",
    "unknown-harness": "unknown harness",
    "verifier-merge-grant": "merge grant",
    "gate-identity-delegated": "humanGate identity",
    "langchain-as-harness": "unknown harness",
    "fail-event": "unknown event",
    "open-questions-field": "unknown fields",
}

_CATALOG_UNSET = object()
_harness_catalog: object = _CATALOG_UNSET


class Issue:
    def __init__(self, code: str, msg: str) -> None:
        self.code = code
        self.msg = msg

    def __str__(self) -> str:
        return f"{self.code}: {self.msg}"


def _as_dict(value: object, path: str, issues: list[Issue]) -> dict[str, object] | None:
    if not isinstance(value, dict):
        issues.append(Issue("type", f"{path} must be an object"))
        return None
    return value


def _as_list(value: object, path: str, issues: list[Issue]) -> list[object] | None:
    if not isinstance(value, list):
        issues.append(Issue("type", f"{path} must be an array"))
        return None
    return value


def _require(obj: dict[str, object], keys: tuple[str, ...], path: str, issues: list[Issue]) -> None:
    for key in keys:
        if key not in obj:
            issues.append(Issue("required", f"{path} missing {key}"))


def _ids(items: list[dict[str, object]], path: str, issues: list[Issue]) -> dict[str, dict[str, object]]:
    out: dict[str, dict[str, object]] = {}
    for i, item in enumerate(items):
        ident = item.get("id")
        if not isinstance(ident, str) or not ID_RE.match(ident):
            issues.append(Issue("id", f"{path}[{i}].id is invalid"))
            continue
        if ident in out:
            issues.append(Issue("duplicate", f"duplicate id {ident}"))
            continue
        out[ident] = item
    return out


def _port_index(node: dict[str, object]) -> dict[str, dict[str, object]]:
    ports = node.get("ports")
    if not isinstance(ports, list):
        return {}
    index: dict[str, dict[str, object]] = {}
    for port in ports:
        if isinstance(port, dict) and isinstance(port.get("id"), str):
            index[str(port["id"])] = port
    return index


def _dep_cycle(nodes: dict[str, dict[str, object]], edges: list[dict[str, object]]) -> str | None:
    adj: dict[str, list[str]] = {ident: [] for ident in nodes}
    for edge in edges:
        if edge.get("relation") != "dependency" and edge.get("kind") != "dependency":
            continue
        frm = edge.get("from")
        to = edge.get("to")
        if isinstance(frm, str) and isinstance(to, str) and frm in adj and to in adj:
            adj[frm].append(to)
    visiting: set[str] = set()
    seen: set[str] = set()

    def dfs(node: str) -> str | None:
        if node in visiting:
            return node
        if node in seen:
            return None
        visiting.add(node)
        for nxt in adj[node]:
            hit = dfs(nxt)
            if hit is not None:
                return hit
        visiting.remove(node)
        seen.add(node)
        return None

    for ident in nodes:
        hit = dfs(ident)
        if hit is not None:
            return hit
    return None


def _privileged(values: object) -> list[str]:
    if not isinstance(values, list):
        return []
    return [str(v) for v in values if str(v).lower() in PRIVILEGED]


def _irreversible(values: object) -> list[str]:
    if not isinstance(values, list):
        return []
    return [str(v) for v in values if str(v).lower() in IRREVERSIBLE]


def _validate_observed_graph(graph: dict[str, object], issues: list[Issue]) -> None:
    """Same node/edge rules as intent. Policies stay on the intent document."""
    shadow: dict[str, object] = {
        "specVersion": "0.2",
        "graphId": "observed",
        "revision": 0,
        "intentGraph": graph,
        "policies": {
            "kinds": [],
            "fanIn": "all",
            "dynamic": {"allowed": False, "maxChildren": 0, "maxDepth": 0},
        },
        "constraints": {"budgets": {"tokens": 0}, "termination": {"on": "observed"}},
        "provenance": {"sourceHash": "0" * 64},
    }
    issues.extend(validate_02(shadow))


def validate_02(doc: dict[str, object]) -> list[Issue]:
    issues: list[Issue] = []
    _require(doc, REQUIRED_02, "document", issues)
    extra = set(doc) - CLOSED_02
    if extra:
        issues.append(Issue("closed", f"unknown fields {sorted(extra)}"))

    if doc.get("specVersion") != "0.2":
        issues.append(Issue("version", f"unknown specVersion {doc.get('specVersion')!r}"))
        return issues

    if not isinstance(doc.get("graphId"), str) or not ID_RE.match(str(doc["graphId"])):
        issues.append(Issue("id", "graphId is invalid"))
    if not isinstance(doc.get("revision"), int) or int(doc["revision"]) < 0:
        issues.append(Issue("type", "revision must be an integer >= 0"))

    provenance = _as_dict(doc.get("provenance"), "provenance", issues) or {}
    source_hash = provenance.get("sourceHash")
    if not isinstance(source_hash, str) or not HASH_RE.match(source_hash):
        issues.append(Issue("provenance", "provenance.sourceHash must be sha256 hex"))

    constraints = _as_dict(doc.get("constraints"), "constraints", issues) or {}
    if "budgets" not in constraints or "termination" not in constraints:
        issues.append(Issue("required", "constraints must include budgets and termination"))

    graph = _as_dict(doc.get("intentGraph"), "intentGraph", issues)
    if graph is None:
        return issues
    extra_g = set(graph) - {"nodes", "edges"}
    if extra_g:
        issues.append(Issue("closed", f"intentGraph unknown fields {sorted(extra_g)}"))

    raw_nodes = _as_list(graph.get("nodes"), "intentGraph.nodes", issues)
    raw_edges = _as_list(graph.get("edges"), "intentGraph.edges", issues)
    if raw_nodes is None or raw_edges is None:
        return issues
    if len(raw_nodes) < 1:
        issues.append(Issue("required", "intentGraph.nodes must be non-empty"))

    nodes_l: list[dict[str, object]] = []
    harness_catalog: object = _CATALOG_UNSET
    for i, item in enumerate(raw_nodes):
        node = _as_dict(item, f"nodes[{i}]", issues)
        if node is None:
            continue
        _require(node, ("id", "kind", "ports", "capabilities"), f"nodes[{i}]", issues)
        if node.get("kind") not in NODE_KINDS_02:
            issues.append(Issue("kind", f"nodes[{i}].kind {node.get('kind')!r} is unknown"))
        if "harness" in node:
            harness_id = node.get("harness")
            if node.get("kind") != "executor":
                issues.append(Issue("harness", f"node {node.get('id')} harness is only allowed on executor nodes"))
            if harness_catalog is _CATALOG_UNSET:
                harness_catalog = _load_harness_catalog()
                if harness_catalog is None:
                    issues.append(Issue("harness", "missing harnesses/catalog.json"))
                elif not isinstance(harness_catalog, dict):
                    issues.append(Issue("harness", "harnesses/catalog.json must be an object"))
            if isinstance(harness_catalog, dict):
                supported = harness_catalog.get("supported")
                harnesses = harness_catalog.get("harnesses")
                if not isinstance(supported, list) or harness_id not in supported:
                    issues.append(Issue("harness", f"node {node.get('id')} unknown harness id {harness_id!r}"))
                elif not isinstance(harnesses, dict) or not isinstance(harnesses.get(harness_id), dict):
                    issues.append(Issue("harness", f"node {node.get('id')} harness {harness_id!r} has no catalog row"))
                else:
                    harness_kind = harnesses[harness_id].get("kind")
                    if harness_kind == "control-room":
                        issues.append(
                            Issue(
                                "harness",
                                f"node {node.get('id')} harness {harness_id!r} is a control-room, not an executor; "
                                "control rooms compile graphs, they are not nodes in them",
                            )
                        )
                    elif harness_kind != "executor":
                        issues.append(
                            Issue(
                                "harness",
                                f"node {node.get('id')} harness {harness_id!r} kind {harness_kind!r} is not an executor",
                            )
                        )
        ports = _as_list(node.get("ports"), f"nodes[{i}].ports", issues) or []
        seen_ports: set[str] = set()
        for j, port in enumerate(ports):
            p = _as_dict(port, f"nodes[{i}].ports[{j}]", issues)
            if p is None:
                continue
            _require(p, ("id", "direction", "schema"), f"nodes[{i}].ports[{j}]", issues)
            if p.get("direction") not in {"in", "out"}:
                issues.append(Issue("port", f"nodes[{i}].ports[{j}] direction invalid"))
            pid = p.get("id")
            if isinstance(pid, str):
                if pid in seen_ports:
                    issues.append(Issue("duplicate", f"duplicate port {pid} on {node.get('id')}"))
                seen_ports.add(pid)
        nodes_l.append(node)

    nodes = _ids(nodes_l, "nodes", issues)

    edges_l: list[dict[str, object]] = []
    for i, item in enumerate(raw_edges):
        edge = _as_dict(item, f"edges[{i}]", issues)
        if edge is None:
            continue
        _require(
            edge,
            ("id", "relation", "from", "to", "fromPort", "toPort", "delivery", "authority", "provenance"),
            f"edges[{i}]",
            issues,
        )
        if edge.get("relation") not in EDGE_REL_02:
            issues.append(Issue("relation", f"edges[{i}].relation {edge.get('relation')!r} is unknown"))
        edges_l.append(edge)

    _ids(edges_l, "edges", issues)

    incoming_dep: dict[str, int] = {ident: 0 for ident in nodes}
    degree: dict[str, int] = {ident: 0 for ident in nodes}

    for edge in edges_l:
        frm = edge.get("from")
        to = edge.get("to")
        if frm == to:
            issues.append(Issue("self-edge", f"self-edge {edge.get('id')}"))
        if frm not in nodes:
            issues.append(Issue("endpoint", f"edge {edge.get('id')} from unknown node {frm!r}"))
            continue
        if to not in nodes:
            issues.append(Issue("endpoint", f"edge {edge.get('id')} to unknown node {to!r}"))
            continue
        degree[str(frm)] += 1
        degree[str(to)] += 1
        if edge.get("relation") == "dependency":
            incoming_dep[str(to)] += 1

        from_port = _port_index(nodes[str(frm)]).get(str(edge.get("fromPort")))
        to_port = _port_index(nodes[str(to)]).get(str(edge.get("toPort")))
        if from_port is None:
            issues.append(Issue("port", f"edge {edge.get('id')} unknown port {edge.get('fromPort')} on {frm}"))
        elif from_port.get("direction") != "out":
            issues.append(Issue("port", f"edge {edge.get('id')} fromPort must be an out port"))
        if to_port is None:
            issues.append(Issue("port", f"edge {edge.get('id')} unknown port {edge.get('toPort')} on {to}"))
        elif to_port.get("direction") != "in":
            issues.append(Issue("port", f"edge {edge.get('id')} toPort must be an in port"))
        if from_port and to_port and from_port.get("schema") != to_port.get("schema"):
            issues.append(
                Issue(
                    "schema",
                    f"edge {edge.get('id')} schema {from_port.get('schema')} incompatible with {to_port.get('schema')}",
                )
            )

        delivery = _as_dict(edge.get("delivery"), f"edge {edge.get('id')}.delivery", issues) or {}
        if "order" not in delivery or "idempotent" not in delivery:
            issues.append(Issue("required", f"edge {edge.get('id')} delivery needs order and idempotent"))
        authority = _as_dict(edge.get("authority"), f"edge {edge.get('id')}.authority", issues) or {}
        grant = authority.get("grant")
        depth = authority.get("delegationDepth")
        if not isinstance(grant, list):
            issues.append(Issue("authority", f"edge {edge.get('id')} grant must be an array"))
            grant = []
        if not isinstance(depth, int) or depth < 0:
            issues.append(Issue("authority", f"edge {edge.get('id')} delegationDepth must be a finite integer >= 0"))
        if edge.get("relation") == "delegation" and not grant:
            issues.append(Issue("authority", f"delegation edge {edge.get('id')} must declare a grant"))
        if _privileged(grant):
            issues.append(Issue("payment", f"edge {edge.get('id')} payment execution is unsupported"))
        to_node = nodes.get(str(to))
        from_node = nodes.get(str(frm))
        if to_node is not None and to_node.get("kind") == "verifier" and _irreversible(grant):
            issues.append(
                Issue(
                    "gate",
                    f"edge {edge.get('id')} merge grant to verifier {to_node.get('id')}; review is not the gate",
                )
            )
        if (
            edge.get("relation") == "delegation"
            and from_node is not None
            and to_node is not None
            and from_node.get("kind") == "humanGate"
            and to_node.get("kind") == "executor"
        ):
            issues.append(
                Issue(
                    "gate",
                    f"edge {edge.get('id')} humanGate identity cannot be delegated to an executor",
                )
            )

        eprov = _as_dict(edge.get("provenance"), f"edge {edge.get('id')}.provenance", issues) or {}
        if not isinstance(eprov.get("sourceHash"), str) or not HASH_RE.match(str(eprov["sourceHash"])):
            issues.append(Issue("provenance", f"edge {edge.get('id')} provenance.sourceHash must be sha256 hex"))

    if len(nodes) > 1:
        for ident, n in degree.items():
            if n == 0:
                issues.append(Issue("reachability", f"isolated node {ident}"))

    cycle = _dep_cycle(nodes, edges_l)
    if cycle is not None:
        issues.append(Issue("cycle", f"dependency cycle involving {cycle}"))

    policies = _as_dict(doc.get("policies"), "policies", issues) or {}
    fan_in = policies.get("fanIn")
    if any(count >= 2 for count in incoming_dep.values()) and fan_in not in FAN_IN:
        issues.append(Issue("fan-in", "implicit fan-in: declare policies.fanIn as all|any|quorum|reducer"))
    if fan_in == "quorum":
        quorum = policies.get("quorum")
        if not isinstance(quorum, int) or quorum < 1:
            issues.append(Issue("fan-in", "quorum fan-in requires policies.quorum >= 1"))
    if fan_in == "reducer" and not isinstance(policies.get("reducer"), str):
        issues.append(Issue("fan-in", "reducer fan-in requires policies.reducer"))

    kinds = policies.get("kinds")
    kind_set = {str(k) for k in kinds} if isinstance(kinds, list) else set()
    dynamic = policies.get("dynamic")
    if not isinstance(dynamic, dict):
        dynamic = {}
    allowed = bool(dynamic.get("allowed"))
    max_children = dynamic.get("maxChildren")
    max_depth = dynamic.get("maxDepth")
    recursive = "recursion" in kind_set or allowed
    if recursive:
        finite = (
            isinstance(max_children, int)
            and max_children >= 1
            and isinstance(max_depth, int)
            and max_depth >= 1
        )
        if not finite:
            issues.append(Issue("bounds", "unbounded recursion/spawn: set finite dynamic.maxChildren and maxDepth"))
        budgets = constraints.get("budgets") if isinstance(constraints.get("budgets"), dict) else {}
        if not budgets:
            issues.append(Issue("bounds", "dynamic spawn requires a reserved budget"))

    if "auction" in kind_set or "auction" in policies:
        auction = policies.get("auction")
        if not isinstance(auction, dict):
            issues.append(Issue("auction", "auction policy must be an object"))
        else:
            phases = auction.get("phases")
            if not isinstance(phases, list) or tuple(phases) != AUCTION_PHASES:
                issues.append(
                    Issue("auction", "auction requires phases announce,bid,award,execute,verify,settle")
                )
            payment = auction.get("payment", "unsupported")
            if payment not in {None, "unsupported", False}:
                issues.append(Issue("payment", "payment execution is unsupported"))

    for ident, node in nodes.items():
        extra_priv = _privileged(node.get("capabilities"))
        ceiling = node.get("authorityCeiling")
        ceiling_set = {str(x).lower() for x in ceiling} if isinstance(ceiling, list) else set()
        leaked = [p for p in extra_priv if p not in ceiling_set]
        if leaked:
            issues.append(
                Issue("privilege", f"node {ident} privileged capability {leaked} is undeclared on authorityCeiling")
            )
        if node.get("kind") == "verifier":
            held = _irreversible(node.get("capabilities")) + _irreversible(node.get("authorityCeiling"))
            if held:
                issues.append(
                    Issue("gate", f"verifier {ident} cannot hold merge grant {held}; review is not the gate")
                )

    if "observedGraph" in doc:
        observed = _as_dict(doc.get("observedGraph"), "observedGraph", issues)
        if observed is not None:
            _validate_observed_graph(observed, issues)

    if "eventLog" in doc:
        raw_events = _as_list(doc.get("eventLog"), "eventLog", issues)
        if raw_events is not None:
            for i, item in enumerate(raw_events):
                event = _as_dict(item, f"eventLog[{i}]", issues)
                if event is None:
                    continue
                _require(
                    event,
                    ("eventId", "type", "sourceHash", "revision", "causalParents", "payload"),
                    f"eventLog[{i}]",
                    issues,
                )
                etype = event.get("type")
                if etype not in EVENT_TYPES_02:
                    issues.append(Issue("event", f"unknown event type {etype!r}"))
                if not isinstance(event.get("causalParents"), list):
                    issues.append(Issue("event", f"eventLog[{i}].causalParents must be an array"))
                if not isinstance(event.get("payload"), dict):
                    issues.append(Issue("event", f"eventLog[{i}].payload must be an object"))

    return issues


def validate_01(doc: dict[str, object]) -> list[Issue]:
    issues: list[Issue] = []
    _require(doc, REQUIRED_01, "document", issues)
    if doc.get("specVersion") != "0.1":
        issues.append(Issue("version", f"unknown specVersion {doc.get('specVersion')!r}"))
        return issues

    provenance = _as_dict(doc.get("provenance"), "provenance", issues) or {}
    if not isinstance(provenance.get("sourceHash"), str) or not HASH_RE.match(str(provenance["sourceHash"])):
        issues.append(Issue("provenance", "provenance.sourceHash must be sha256 hex"))

    raw_nodes = _as_list(doc.get("nodes"), "nodes", issues)
    raw_edges = _as_list(doc.get("edges"), "edges", issues)
    if raw_nodes is None or raw_edges is None:
        return issues

    nodes_l: list[dict[str, object]] = []
    for i, item in enumerate(raw_nodes):
        node = _as_dict(item, f"nodes[{i}]", issues)
        if node is None:
            continue
        _require(node, ("id", "role"), f"nodes[{i}]", issues)
        if node.get("role") not in NODE_ROLES_01:
            issues.append(Issue("role", f"nodes[{i}].role {node.get('role')!r} is unknown"))
        nodes_l.append(node)
    nodes = _ids(nodes_l, "nodes", issues)

    edges_l: list[dict[str, object]] = []
    for i, item in enumerate(raw_edges):
        edge = _as_dict(item, f"edges[{i}]", issues)
        if edge is None:
            continue
        _require(edge, ("id", "from", "to", "kind"), f"edges[{i}]", issues)
        if edge.get("kind") not in EDGE_KINDS_01:
            issues.append(Issue("kind", f"edges[{i}].kind {edge.get('kind')!r} is unknown"))
        if edge.get("from") == edge.get("to"):
            issues.append(Issue("self-edge", f"self-edge {edge.get('id')}"))
        edges_l.append(edge)
    _ids(edges_l, "edges", issues)

    incoming_dep: dict[str, int] = {ident: 0 for ident in nodes}
    for edge in edges_l:
        frm, to = edge.get("from"), edge.get("to")
        if frm not in nodes:
            issues.append(Issue("endpoint", f"edge {edge.get('id')} from unknown node {frm!r}"))
        if to not in nodes:
            issues.append(Issue("endpoint", f"edge {edge.get('id')} to unknown node {to!r}"))
        if edge.get("kind") == "dependency" and isinstance(to, str) and to in incoming_dep:
            incoming_dep[to] += 1

    cycle = _dep_cycle(nodes, [{"relation": e.get("kind"), "from": e.get("from"), "to": e.get("to")} for e in edges_l])
    if cycle is not None:
        issues.append(Issue("cycle", f"dependency cycle involving {cycle}"))

    policies = _as_dict(doc.get("policies"), "policies", issues) or {}
    _require(policies, ("fanIn", "coordination", "dynamicFanOut"), "policies", issues)
    fan_in = policies.get("fanIn")
    if fan_in not in FAN_IN:
        issues.append(Issue("fan-in", "policies.fanIn must be all|any|quorum|reducer"))
    elif any(count >= 2 for count in incoming_dep.values()) and fan_in not in FAN_IN:
        issues.append(Issue("fan-in", "implicit fan-in"))
    dyn = _as_dict(policies.get("dynamicFanOut"), "policies.dynamicFanOut", issues) or {}
    if dyn.get("allowed") is True:
        max_children = dyn.get("maxChildren")
        if not isinstance(max_children, int) or max_children < 1:
            issues.append(Issue("bounds", "unbounded spawn: dynamicFanOut.maxChildren must be finite >= 1"))
    return issues


def validate(doc: object) -> list[Issue]:
    if not isinstance(doc, dict):
        return [Issue("type", "document must be an object")]
    version = doc.get("specVersion")
    if version == "0.2":
        return validate_02(doc)
    if version == "0.1":
        return validate_01(doc)
    return [Issue("version", f"unknown specVersion {version!r}")]



def validate_encodings() -> list[Issue]:
    issues: list[Issue] = []
    visual_path = _ROOT / "encodings" / "visual.json"
    ir_path = _ROOT / "encodings" / "ir-map.json"
    graphs_path = _ROOT / "encodings" / "topology-graphs.json"
    for path in (visual_path, ir_path, graphs_path):
        if not path.exists():
            issues.append(Issue("encodings", f"missing {path.relative_to(_ROOT)}"))
            return issues
    visual = load_json(visual_path)
    ir_map = load_json(ir_path)
    graphs = load_json(graphs_path)
    if not isinstance(visual, dict) or not isinstance(ir_map, dict) or not isinstance(graphs, dict):
        issues.append(Issue("type", "encodings documents must be objects"))
        return issues
    vis_ids = set((visual.get("topologies") or {}).keys())
    ir_ids = set((ir_map.get("topologies") or {}).keys())
    graph_ids = set(graphs.keys())
    if vis_ids != ir_ids:
        issues.append(Issue("encodings", f"visual/ir topology id mismatch extra={sorted(ir_ids-vis_ids)} missing={sorted(vis_ids-ir_ids)}"))
    if vis_ids != graph_ids:
        issues.append(Issue("encodings", f"visual/graph id mismatch extra={sorted(graph_ids-vis_ids)} missing={sorted(vis_ids-graph_ids)}"))
    swarm = (ir_map.get("topologies") or {}).get("swarm") or {}
    if swarm.get("status") != "not-inferred":
        issues.append(Issue("encodings", "swarm must be not-inferred"))
    market = (ir_map.get("topologies") or {}).get("marketplace") or {}
    kinds = (market.get("policies") or {}).get("kinds") or []
    if "auction" not in kinds:
        issues.append(Issue("encodings", "marketplace must map to auction policy"))
    payment = ((market.get("policies") or {}).get("auction") or {}).get("payment")
    if payment not in {None, "unsupported", False}:
        issues.append(Issue("payment", "marketplace payment execution is unsupported"))
    for tid, rec in (ir_map.get("topologies") or {}).items():
        if rec.get("status") not in {"expressible", "not-inferred", "unspecified"}:
            issues.append(Issue("encodings", f"{tid} has unknown ir status {rec.get('status')!r}"))
        example = rec.get("example")
        if example and not (_ROOT / example).exists():
            issues.append(Issue("encodings", f"{tid} example missing {example}"))
    for kind, tid in (ir_map.get("fromHotl") or {}).items():
        if tid is None:
            continue
        if tid not in ir_ids:
            issues.append(Issue("encodings", f"fromHotl {kind} -> unknown topology {tid}"))
    translation = ir_map.get("translation") or {}
    if translation.get("symbol") != "tau" or translation.get("failClosed") != "bot" or translation.get("partial") is not True:
        issues.append(Issue("encodings", "translation must be partial tau with failClosed bot"))
    required_channels = ("provider", "model", "effort", "topology", "operatingMode", "state", "economics")
    channels = ir_map.get("channels") or {}
    for name in required_channels:
        rec = channels.get(name) or {}
        compile_mode = rec.get("compile")
        if compile_mode not in {"none", "declared-only", "ir-map"}:
            issues.append(Issue("encodings", f"channel {name} missing compile mode"))
    if (channels.get("topology") or {}).get("compile") != "ir-map":
        issues.append(Issue("encodings", "topology channel must compile through ir-map"))
    hybrid = (ir_map.get("topologies") or {}).get("hybrid") or {}
    if hybrid.get("status") != "not-inferred":
        issues.append(Issue("encodings", "unlabeled hybrid must be not-inferred"))
    return issues

REQUIRED_HARNESS_IDS = ("hermes", "omp", "o8", "grok", "codex", "claude", "pi", "fx")
HARNESS_KINDS = {"executor", "control-room"}
DASH_STATUS = {"wired", "none"}
FIRSTMATE_STATUS = {"primary", "crew", "none"}
REQUIRED_NETWORK = ("aodl", "dash", "frontier-kb", "hermes-keel", "hermes-agent")


def _load_harness_catalog() -> object:
    global _harness_catalog
    if _harness_catalog is _CATALOG_UNSET:
        path = _ROOT / "harnesses" / "catalog.json"
        _harness_catalog = load_json(path) if path.exists() else None
    return _harness_catalog


def validate_catalog() -> list[Issue]:
    issues: list[Issue] = []
    doc = _load_harness_catalog()
    if doc is None:
        return [Issue("catalog", "missing harnesses/catalog.json")]
    if not isinstance(doc, dict):
        return [Issue("type", "harnesses/catalog.json must be an object")]
    supported = doc.get("supported")
    harnesses = doc.get("harnesses")
    distros = doc.get("distros")
    network = doc.get("network")
    if not isinstance(supported, list):
        issues.append(Issue("catalog", "supported must be an array"))
        return issues
    if list(supported) != list(REQUIRED_HARNESS_IDS):
        issues.append(
            Issue(
                "catalog",
                f"supported must be {list(REQUIRED_HARNESS_IDS)}, got {supported}",
            )
        )
    if not isinstance(harnesses, dict):
        issues.append(Issue("catalog", "harnesses must be an object"))
        return issues
    missing = [hid for hid in REQUIRED_HARNESS_IDS if hid not in harnesses]
    extra = [hid for hid in harnesses if hid not in REQUIRED_HARNESS_IDS]
    if missing:
        issues.append(Issue("catalog", f"missing harness ids {missing}"))
    if extra:
        issues.append(Issue("catalog", f"unknown harness ids {sorted(extra)}"))
    for hid in REQUIRED_HARNESS_IDS:
        row = harnesses.get(hid)
        if not isinstance(row, dict):
            issues.append(Issue("catalog", f"{hid} must be an object"))
            continue
        for key in ("name", "kind", "bin", "repo", "dash", "role"):
            if key not in row:
                issues.append(Issue("catalog", f"{hid} missing {key}"))
        if row.get("kind") not in HARNESS_KINDS:
            issues.append(Issue("catalog", f"{hid} kind {row.get('kind')!r} is unknown"))
        if row.get("dash") not in DASH_STATUS:
            issues.append(Issue("catalog", f"{hid} dash status {row.get('dash')!r} is unknown"))
        if row.get("firstmate") not in FIRSTMATE_STATUS:
            issues.append(Issue("catalog", f"{hid} firstmate status {row.get('firstmate')!r} is unknown"))
        repo = row.get("repo")
        if not isinstance(repo, str) or not repo.startswith("https://github.com/"):
            issues.append(Issue("catalog", f"{hid} repo must be a github URL"))
    if hid := "o8":
        if isinstance(harnesses.get("o8"), dict) and harnesses["o8"].get("kind") != "control-room":
            issues.append(Issue("catalog", "o8 must be kind control-room"))
    if not isinstance(distros, dict) or "firstmate" not in distros:
        issues.append(Issue("catalog", "distros.firstmate is required"))
    else:
        fm = distros["firstmate"]
        if not isinstance(fm, dict) or fm.get("kind") != "distro":
            issues.append(Issue("catalog", "firstmate must be kind distro"))
        if "firstmate" in (supported or []):
            issues.append(Issue("catalog", "firstmate is a distro, not a supported harness id"))
    if not isinstance(network, dict):
        issues.append(Issue("catalog", "network must be an object"))
    else:
        for nid in REQUIRED_NETWORK:
            node = network.get(nid)
            if not isinstance(node, dict) or not str(node.get("repo", "")).startswith("https://github.com/"):
                issues.append(Issue("catalog", f"network.{nid} needs a github repo"))
    return issues

def load_json(path: Path) -> object:
    return json.loads(path.read_text())


def _run_corpus() -> int:
    failed = 0
    valid_files = sorted(VALID_DIR.glob("*.json"))
    invalid_files = sorted(INVALID_DIR.glob("*.json"))
    if not valid_files:
        print("no valid fixtures", file=sys.stderr)
        return 1
    if not invalid_files:
        print("no invalid fixtures", file=sys.stderr)
        return 1

    for path in valid_files:
        issues = validate(load_json(path))
        if issues:
            failed += 1
            print(f"FAIL {path.relative_to(_ROOT)} (expected pass)")
            for issue in issues:
                print(f"  {issue}")
        else:
            print(f"ok   {path.relative_to(_ROOT)}")

    for path in invalid_files:
        issues = validate(load_json(path))
        expect = INVALID_EXPECT.get(path.stem)
        blob = " ".join(str(i) for i in issues).lower()
        if not issues:
            failed += 1
            print(f"FAIL {path.relative_to(_ROOT)} (expected fail)")
        elif expect and expect.lower() not in blob:
            failed += 1
            print(f"FAIL {path.relative_to(_ROOT)} (wanted {expect!r})")
            for issue in issues:
                print(f"  {issue}")
        else:
            print(f"ok   {path.relative_to(_ROOT)} -> {issues[0]}")

    missing = set(INVALID_EXPECT) - {p.stem for p in invalid_files}
    extra = {p.stem for p in invalid_files} - set(INVALID_EXPECT)
    if missing:
        print(f"FAIL missing invalid fixtures: {sorted(missing)}")
        failed += 1
    if extra:
        print(f"FAIL unexpected invalid fixtures: {sorted(extra)}")
        failed += 1

    encoding_issues = validate_encodings()
    if encoding_issues:
        failed += 1
        print("FAIL encodings join table")
        for issue in encoding_issues:
            print(f"  {issue}")
    else:
        print("ok   encodings/visual.json ↔ encodings/ir-map.json")


    catalog_issues = validate_catalog()
    if catalog_issues:
        failed += 1
        print("FAIL harnesses/catalog.json")
        for issue in catalog_issues:
            print(f"  {issue}")
    else:
        print("ok   harnesses/catalog.json")
    if failed:
        print(f"{failed} failure(s)")
        return 1
    print(f"{len(valid_files)} valid, {len(invalid_files)} invalid — all matched")
    return 0



def validate_document(mapping: object) -> list:
    return validate(mapping)


def validate_document_messages(mapping: object) -> list[str]:
    return [f"{i.code}: {i.msg}" if hasattr(i, "code") else str(i) for i in validate(mapping)]


def validate_or_raise(mapping: object) -> None:
    issues = validate_document(mapping)
    if not issues:
        return
    raise ValueError("AODL validation failed: " + "; ".join(validate_document_messages(mapping)))
