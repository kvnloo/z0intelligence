"""Fail-closed adapter for the experimental Bend AODL structural gate.

Experiment for z0intelligence#48 / kvnloo/aodl#19 / z0intelligence#13.

The canonical AODL validator (``aodl_contract.validator.validate``) stays
authoritative.  This module is the host half of a candidate *fast structural
filter* written in Bend 2 (``bend/aodl_gate``):

* the host owns JSON: it runs the validator's **shape** checks (types,
  required/closed fields, id and sha256 syntax, delivery/provenance objects,
  harness-catalog lookup) and, when they pass, emits a flat token stream --
  the *Core IR* -- in which every string/scalar the rules compare is interned
  with Python equality;
* the Bend kernel owns the **structural rules** (vocabularies, topology,
  ports/schemas, duplicates, reachability, dependency cycles, fan-in,
  bounds, auction, authority/privilege/gate rules, event types) and answers
  ``ALLOW`` or ``DENY <codes>``;
* anything the adapter cannot represent, any kernel error, timeout,
  malformed reply or missing binary becomes a DENY.  Nothing here can turn
  an error into an ALLOW.

The Core IR normalizer lives here, in z0int, only for this experiment.  Per
the owner's constraint on aodl#19 it must move into AODL (``aodl_contract``)
before Bend could be anything more than experimental.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Sequence

AODL_REFERENCE = "kvnloo/aodl@a848270356225699fc828e80a0cc93d2754f7413"
KERNEL_DIR = Path(__file__).resolve().parents[2] / "bend" / "aodl_gate"
KERNEL_SOURCES = ("core.bend", "rules.bend", "transition.bend", "gate.bend", "main.bend")

ID_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_.:-]{0,127}$")
HASH_RE = re.compile(r"^[a-f0-9]{64}$")
CLOSED_02 = frozenset(
    {"specVersion", "graphId", "revision", "intentGraph", "policies", "constraints",
     "provenance", "plan", "eventLog", "observedGraph"}
)
REQUIRED_02 = ("specVersion", "graphId", "revision", "intentGraph", "policies", "constraints", "provenance")
EDGE_REQUIRED = ("id", "relation", "from", "to", "fromPort", "toPort", "delivery", "authority", "provenance")
EVENT_REQUIRED = ("eventId", "type", "sourceHash", "revision", "causalParents", "payload")

# Bend rule code -> the canonical validator's Issue.code it mirrors.
RULE_CODES: dict[int, str] = {
    1: "kind", 2: "harness", 3: "port", 4: "duplicate", 5: "relation", 6: "self-edge",
    7: "endpoint", 8: "schema", 9: "authority", 10: "payment", 11: "gate",
    12: "reachability", 13: "cycle", 14: "fan-in", 15: "bounds", 16: "auction",
    17: "privilege", 18: "event",
}
TRANSITION_CODES: dict[int, str] = {
    101: "revision-mismatch", 102: "dynamic-spawn-disallowed", 103: "max-children",
    104: "max-depth", 105: "budget", 106: "authority",
}
UNPARSEABLE = 0
MODE_DOCUMENT = 1
MODE_TRANSITION = 2
U32_MAX = (1 << 32) - 1
# The compiled Bend runtime stores a Nat as an immediate of at most 2^48 - 1
# and ABORTS the process past it (the proofs model Nat as unbounded).  The
# kernel adds two values (seen + prop), so the host refuses any value of
# 2^47 or more: every sum then stays below the runtime cap.
NAT_MAX = (1 << 47) - 1
# Doc-level numeric fields are only compared against 1, so clamping keeps
# every comparison exact while fitting a U32 token.
_CLAMP = 1 << 30


def kernel_revision(kernel_dir: Path = KERNEL_DIR) -> str:
    """sha256 over the Bend sources: the exact kernel a decision came from."""

    h = hashlib.sha256()
    for name in KERNEL_SOURCES:
        path = kernel_dir / name
        h.update(name.encode())
        h.update(b"\0")
        h.update(path.read_bytes() if path.exists() else b"<missing>")
        h.update(b"\0")
    return h.hexdigest()[:16]


# ---------------------------------------------------------------------------
# Value interning (Python equality, exactly as the validator compares)
# ---------------------------------------------------------------------------


def _key(value: Any) -> Any:
    if isinstance(value, str):
        return ("s", value)
    if value is None:
        return ("null",)
    if isinstance(value, (bool, int, float)):
        # ("n", True) == ("n", 1) == ("n", 1.0), and they hash alike, so a dict
        # keyed by this unifies them precisely when the validator's == would.
        return ("n", value)
    if isinstance(value, list):
        return ("l", tuple(_key(v) for v in value))
    if isinstance(value, dict):
        return ("d", frozenset((k, _key(v)) for k, v in value.items()))
    return ("o", repr(value))


class _Table:
    def __init__(self) -> None:
        self._index: dict[Any, int] = {}
        self._entries: list[list[int]] = []

    def ref(self, value: Any) -> int:
        k = _key(value)
        idx = self._index.get(k)
        if idx is not None:
            return idx
        idx = len(self._entries)
        self._index[k] = idx
        if isinstance(value, str):
            chars = [ord(c) for c in value]
            self._entries.append([0, len(chars), *chars])
        elif value is None:
            self._entries.append([1])
        elif not isinstance(value, (list, dict)) and value == False:  # noqa: E712 - Python equality is the point
            self._entries.append([2])
        else:
            self._entries.append([3])
        return idx

    def text(self, value: Any) -> int:
        """Intern ``str(value)``, as the validator does before lower()."""

        return self.ref(str(value))

    def tokens(self) -> list[int]:
        out = [len(self._entries)]
        for entry in self._entries:
            out.extend(entry)
        return out


# ---------------------------------------------------------------------------
# Shape checks + Core IR emission
# ---------------------------------------------------------------------------


@dataclass
class Encoded:
    tokens: list[int] | None
    shape: list[tuple[str, str]] = field(default_factory=list)
    unsupported: str | None = None

    @property
    def representable(self) -> bool:
        return self.tokens is not None


def _int_ok(value: Any) -> bool:
    # isinstance(True, int) holds, exactly as in the validator.
    return isinstance(value, int)


def _nat_enc(value: Any) -> int:
    """0 when not an int >= 0, else min(value, 2^30) + 1."""

    if not _int_ok(value) or int(value) < 0:
        return 0
    return min(int(value), _CLAMP) + 1


class _Encoder:
    def __init__(self, catalog: Mapping[str, Any] | None) -> None:
        self.catalog = catalog
        self.shape: list[tuple[str, str]] = []
        self.tab = _Table()

    def issue(self, code: str, msg: str) -> None:
        self.shape.append((code, msg))

    # -- harness catalog ---------------------------------------------------
    def harness_tag(self, harness_id: Any) -> int:
        cat = self.catalog
        if cat is None:
            self.issue("harness", "missing harnesses/catalog.json")
            return 4
        if not isinstance(cat, dict):
            self.issue("harness", "harnesses/catalog.json must be an object")
            return 4
        supported = cat.get("supported")
        harnesses = cat.get("harnesses")
        if not isinstance(supported, list) or harness_id not in supported:
            return 4
        if not isinstance(harnesses, dict) or not isinstance(harnesses.get(harness_id), dict):
            return 5
        kind = harnesses[harness_id].get("kind")
        if kind == "control-room":
            return 2
        if kind != "executor":
            return 3
        return 1

    # -- graphs ------------------------------------------------------------
    def graph(self, graph: Any, label: str) -> list[int] | None:
        if not isinstance(graph, dict):
            self.issue("type", f"{label} must be an object")
            return None
        if set(graph) - {"nodes", "edges"}:
            self.issue("closed", f"{label} unknown fields")
        raw_nodes = graph.get("nodes")
        raw_edges = graph.get("edges")
        if not isinstance(raw_nodes, list):
            self.issue("type", f"{label}.nodes must be an array")
        if not isinstance(raw_edges, list):
            self.issue("type", f"{label}.edges must be an array")
        if not isinstance(raw_nodes, list) or not isinstance(raw_edges, list):
            return None
        if len(raw_nodes) < 1:
            self.issue("required", f"{label}.nodes must be non-empty")

        node_toks: list[int] = []
        nodes: list[dict[str, Any]] = []
        for i, node in enumerate(raw_nodes):
            if not isinstance(node, dict):
                self.issue("type", f"nodes[{i}] must be an object")
                continue
            for key in ("id", "kind", "ports", "capabilities"):
                if key not in node:
                    self.issue("required", f"nodes[{i}] missing {key}")
            harness = self.harness_tag(node.get("harness")) if "harness" in node else 0
            ports = node.get("ports")
            if not isinstance(ports, list):
                self.issue("type", f"nodes[{i}].ports must be an array")
                ports = []
            port_toks: list[int] = []
            nports = 0
            for j, port in enumerate(ports):
                if not isinstance(port, dict):
                    self.issue("type", f"nodes[{i}].ports[{j}] must be an object")
                    continue
                for key in ("id", "direction", "schema"):
                    if key not in port:
                        self.issue("required", f"nodes[{i}].ports[{j}] missing {key}")
                pid = port.get("id")
                port_toks += [self.tab.ref(pid), self.tab.ref(port.get("direction")), self.tab.ref(port.get("schema"))]
                nports += 1
            caps = node.get("capabilities")
            caps = [self.tab.text(v) for v in caps] if isinstance(caps, list) else []
            ceil = node.get("authorityCeiling")
            ceil = [self.tab.text(v) for v in ceil] if isinstance(ceil, list) else []
            ident = node.get("id")
            if not isinstance(ident, str) or not ID_RE.match(ident):
                self.issue("id", f"nodes[{i}].id is invalid")
            node_toks += [self.tab.ref(ident), self.tab.ref(node.get("kind")), harness, nports, *port_toks,
                          len(caps), *caps, len(ceil), *ceil]
            nodes.append(node)

        # validator._ids: the node dictionary keeps the first valid id
        index: dict[str, dict[str, Any]] = {}
        for node in nodes:
            ident = node.get("id")
            if isinstance(ident, str) and ID_RE.match(ident) and ident not in index:
                index[ident] = node

        edge_toks: list[int] = []
        nedges = 0
        edges: list[dict[str, Any]] = []
        for i, edge in enumerate(raw_edges):
            if not isinstance(edge, dict):
                self.issue("type", f"edges[{i}] must be an object")
                continue
            for key in EDGE_REQUIRED:
                if key not in edge:
                    self.issue("required", f"edges[{i}] missing {key}")
            edges.append(edge)
        for i, edge in enumerate(edges):
            ident = edge.get("id")
            if not isinstance(ident, str) or not ID_RE.match(ident):
                self.issue("id", f"edges[{i}].id is invalid")
            frm, to = edge.get("from"), edge.get("to")
            authority = edge.get("authority")
            grant = authority.get("grant") if isinstance(authority, dict) else None
            try:
                ends = frm in index and to in index
            except TypeError:  # unhashable endpoint: the validator raises here
                ends = False
            if ends:
                # these shape checks run only past the endpoint `continue`
                delivery = edge.get("delivery")
                if not isinstance(delivery, dict):
                    self.issue("type", f"edge {ident}.delivery must be an object")
                    delivery = {}
                if "order" not in delivery or "idempotent" not in delivery:
                    self.issue("required", f"edge {ident} delivery needs order and idempotent")
                if not isinstance(authority, dict):
                    self.issue("type", f"edge {ident}.authority must be an object")
                    authority = {}
                if not isinstance(authority.get("grant"), list):
                    self.issue("authority", f"edge {ident} grant must be an array")
                depth = authority.get("delegationDepth")
                if not isinstance(depth, int) or depth < 0:
                    self.issue("authority", f"edge {ident} delegationDepth must be a finite integer >= 0")
                eprov = edge.get("provenance")
                if not isinstance(eprov, dict):
                    self.issue("type", f"edge {ident}.provenance must be an object")
                    eprov = {}
                if not isinstance(eprov.get("sourceHash"), str) or not HASH_RE.match(str(eprov["sourceHash"])):
                    self.issue("provenance", f"edge {ident} provenance.sourceHash must be sha256 hex")
            grant_ids = [self.tab.text(v) for v in grant] if isinstance(grant, list) else []
            edge_toks += [
                self.tab.ref(ident), self.tab.ref(edge.get("relation")), self.tab.ref(frm), self.tab.ref(to),
                self.tab.ref(str(edge.get("fromPort"))), self.tab.ref(str(edge.get("toPort"))),
                self.tab.ref(edge.get("kind")), len(grant_ids), *grant_ids,
            ]
            nedges += 1
        return [len(nodes), *node_toks, nedges, *edge_toks]

    # -- policies ----------------------------------------------------------
    def policies(self, policies: Any, budgets_nonempty: bool) -> list[int]:
        if not isinstance(policies, dict):
            self.issue("type", "policies must be an object")
            policies = {}
        fan = self.tab.ref(policies.get("fanIn"))
        quorum = _nat_enc(policies.get("quorum"))
        reducer = 1 if isinstance(policies.get("reducer"), str) else 0
        kinds = policies.get("kinds")
        kinds = [self.tab.text(k) for k in kinds] if isinstance(kinds, list) else []
        dynamic = policies.get("dynamic")
        if not isinstance(dynamic, dict):
            dynamic = {}
        dyn = 1 if bool(dynamic.get("allowed")) else 0
        maxc = _nat_enc(dynamic.get("maxChildren"))
        maxd = _nat_enc(dynamic.get("maxDepth"))
        akey = 1 if "auction" in policies else 0
        auction = policies.get("auction")
        atag = 2 if isinstance(auction, dict) else (1 if akey else 0)
        phases_toks = [0]
        pay = self.tab.ref("unsupported")
        if isinstance(auction, dict):
            phases = auction.get("phases")
            if isinstance(phases, list):
                ids = [self.tab.ref(p) for p in phases]
                phases_toks = [1, len(ids), *ids]
            pay = self.tab.ref(auction.get("payment", "unsupported"))
        return [fan, quorum, reducer, len(kinds), *kinds, dyn, maxc, maxd, akey, atag, *phases_toks, pay]

    # -- document ----------------------------------------------------------
    def document(self, doc: Any) -> list[int] | None:
        if not isinstance(doc, dict):
            self.issue("type", "document must be an object")
            return None
        for key in REQUIRED_02:
            if key not in doc:
                self.issue("required", f"document missing {key}")
        if set(doc) - CLOSED_02:
            self.issue("closed", f"unknown fields {sorted(set(doc) - CLOSED_02)}")
        if doc.get("specVersion") != "0.2":
            self.issue("version", f"unknown specVersion {doc.get('specVersion')!r}")
            return None
        if not isinstance(doc.get("graphId"), str) or not ID_RE.match(str(doc["graphId"])):
            self.issue("id", "graphId is invalid")
        if not isinstance(doc.get("revision"), int) or int(doc["revision"]) < 0:
            self.issue("type", "revision must be an integer >= 0")
        provenance = doc.get("provenance")
        if not isinstance(provenance, dict):
            self.issue("type", "provenance must be an object")
            provenance = {}
        if not isinstance(provenance.get("sourceHash"), str) or not HASH_RE.match(provenance["sourceHash"]):
            self.issue("provenance", "provenance.sourceHash must be sha256 hex")
        constraints = doc.get("constraints")
        if not isinstance(constraints, dict):
            self.issue("type", "constraints must be an object")
            constraints = {}
        if "budgets" not in constraints or "termination" not in constraints:
            self.issue("required", "constraints must include budgets and termination")
        budgets = constraints.get("budgets")
        budgets_nonempty = isinstance(budgets, dict) and bool(budgets)

        intent = self.graph(doc.get("intentGraph"), "intentGraph")
        if intent is None:
            return None
        pol = self.policies(doc.get("policies"), budgets_nonempty)

        events: list[int] = []
        if "eventLog" in doc:
            raw = doc.get("eventLog")
            if not isinstance(raw, list):
                self.issue("type", "eventLog must be an array")
            else:
                for i, event in enumerate(raw):
                    if not isinstance(event, dict):
                        self.issue("type", f"eventLog[{i}] must be an object")
                        continue
                    for key in EVENT_REQUIRED:
                        if key not in event:
                            self.issue("required", f"eventLog[{i}] missing {key}")
                    if not isinstance(event.get("causalParents"), list):
                        self.issue("event", f"eventLog[{i}].causalParents must be an array")
                    if not isinstance(event.get("payload"), dict):
                        self.issue("event", f"eventLog[{i}].payload must be an object")
                    events.append(self.tab.ref(event.get("type")))

        obs: list[int] = [0]
        if "observedGraph" in doc:
            g = self.graph(doc.get("observedGraph"), "observedGraph")
            if g is not None:
                obs = [1, *g]
        return [*intent, *pol, 1 if budgets_nonempty else 0, len(events), *events, *obs]


def encode_document(doc: Any, catalog: Mapping[str, Any] | None) -> Encoded:
    """Shape-check ``doc`` and, when it is shape-clean, emit its Core IR tokens."""

    if isinstance(doc, dict) and doc.get("specVersion") == "0.1":
        return Encoded(None, [], unsupported="hotl-0.1 is not modeled by the Bend kernel")
    if isinstance(doc, dict) and doc.get("specVersion") != "0.2":
        # validate() dispatches on the version before any other check
        return Encoded(None, [("version", f"unknown specVersion {doc.get('specVersion')!r}")])
    enc = _Encoder(catalog)
    body = enc.document(doc)
    if enc.shape or body is None:
        return Encoded(None, enc.shape or [("type", "unrepresentable")])
    return Encoded([MODE_DOCUMENT, *enc.tab.tokens(), *body], [])


# ---------------------------------------------------------------------------
# Transition gate encoding (z0intelligence#13 spawn check)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SpawnProposal:
    """A proposed spawn under a frozen contract, with the observed state."""

    contract_revision: int
    request_revision: int
    dynamic_allowed: bool
    max_children: Any
    max_depth: Any
    live_children: int
    parent_depth: int
    budgets: Mapping[str, Any]  # declared Gamma limits (missing = unbounded)
    observed: Mapping[str, Any]  # observed spend so far
    proposed: Mapping[str, Any]  # spend this spawn proposes
    ceiling: Sequence[str]  # parent's authorityCeiling
    requested: Sequence[str]  # capabilities the spawn asks for


BUDGET_DIMS = ("tokens", "premium_tokens", "latency_ms", "usd", "joules", "attention")


def _nat(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0 or value > NAT_MAX:
        raise ValueError(f"not representable as a Bend Nat: {value!r}")
    return int(value)


def _nat_or_zero(value: Any) -> int:
    # an invalid declared bound becomes 0, which denies every spawn
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return 0
    return min(int(value), NAT_MAX)


def _hl(n: int) -> list[int]:
    """A Nat as the kernel reads it: two U32 tokens, hi and lo."""

    return [n >> 32, n & U32_MAX]


def encode_transition(p: SpawnProposal) -> list[int]:
    """Tokens for the transition gate; raises ValueError when unrepresentable."""

    dims: list[int] = []
    n = 0
    for dim in BUDGET_DIMS:
        limit = p.budgets.get(dim)
        seen = _nat(p.observed.get(dim, 0) or 0)
        prop = _nat(p.proposed.get(dim, 0) or 0)
        if limit is None:
            dims += [0, *_hl(0), *_hl(seen), *_hl(prop)]
        else:
            dims += [1, *_hl(_nat(limit)), *_hl(seen), *_hl(prop)]
        n += 1
    names: dict[str, int] = {}

    def cap(s: str) -> int:
        return names.setdefault(str(s), len(names))

    ceil = [cap(c) for c in p.ceiling]
    req = [cap(c) for c in p.requested]
    return [
        MODE_TRANSITION, *_hl(_nat(p.contract_revision)), *_hl(_nat(p.request_revision)),
        1 if p.dynamic_allowed else 0,
        *_hl(_nat_or_zero(p.max_children)), *_hl(_nat_or_zero(p.max_depth)),
        *_hl(_nat(p.live_children)), *_hl(_nat(p.parent_depth)),
        n, *dims, len(ceil), *[t for c in ceil for t in _hl(c)], len(req), *[t for c in req for t in _hl(c)],
    ]


def reference_transition(p: SpawnProposal) -> list[int]:
    """Plain-Python reference for the transition gate: its deny codes."""

    codes: list[int] = []
    if p.contract_revision != p.request_revision:
        codes.append(101)
    if not p.dynamic_allowed:
        codes.append(102)
    if p.live_children + 1 > _nat_or_zero(p.max_children):
        codes.append(103)
    if p.parent_depth + 1 > _nat_or_zero(p.max_depth):
        codes.append(104)
    for dim in BUDGET_DIMS:
        limit = p.budgets.get(dim)
        if limit is None:
            continue
        if (p.observed.get(dim, 0) or 0) + (p.proposed.get(dim, 0) or 0) > limit:
            codes.append(105)
            break
    ceiling = {str(c) for c in p.ceiling}
    if any(str(r) not in ceiling for r in p.requested):
        codes.append(106)
    return codes


# ---------------------------------------------------------------------------
# The kernel process
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class GateDecision:
    allowed: bool
    codes: tuple[str, ...]
    source: str  # "bend" | "host-shape" | "unsupported" | "fail-closed" | "canonical-mismatch"
    kernel_revision: str
    aodl_reference: str = AODL_REFERENCE
    latency_us: float = 0.0
    detail: str = ""


def default_binary() -> Path:
    env = os.environ.get("Z0INT_BEND_GATE_BIN")
    if env:
        return Path(env)
    return KERNEL_DIR / "build" / "gate"


def build_kernel(bend: str | None = None, out: Path | None = None) -> Path:
    """Compile ``bend/aodl_gate/main.bend`` to a native binary."""

    bend = bend or os.environ.get("BEND_BIN") or str(Path.home() / ".bend" / "bin" / "bend")
    out = out or default_binary()
    out.parent.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ, BEND_NO_TELEMETRY="1")
    subprocess.run([bend, str(KERNEL_DIR / "main.bend"), "-o", str(out)], check=True, env=env,
                   capture_output=True, timeout=600)
    return out


def parse_reply(line: str) -> tuple[bool, tuple[int, ...]]:
    """Parse ``ALLOW`` / ``DENY <codes>``; anything else raises ValueError."""

    parts = line.strip().split(" ")
    if parts == ["ALLOW"]:
        return True, ()
    if parts and parts[0] == "DENY" and len(parts) >= 2:
        return False, tuple(int(p) for p in parts[1:])
    raise ValueError(f"malformed kernel reply {line!r}")


class BendGate:
    """A persistent Bend kernel process: one request line, one verdict line.

    Every failure path denies.  ``timeout_s`` bounds a single decision; on a
    timeout the process is killed and restarted on the next call.
    """

    def __init__(self, binary: Path | str | None = None, *, timeout_s: float = 2.0,
                 threads: int | None = 1) -> None:
        self.binary = Path(binary) if binary is not None else default_binary()
        self.timeout_s = timeout_s
        self.threads = threads
        self.revision = kernel_revision()
        self._proc: subprocess.Popen[bytes] | None = None
        self._lock = threading.Lock()

    # -- process management ------------------------------------------------
    def _start(self) -> subprocess.Popen[bytes]:
        if self._proc is not None and self._proc.poll() is None:
            return self._proc
        args = [str(self.binary)]
        if self.threads:
            args += ["--threads", str(self.threads)]
        self._proc = subprocess.Popen(args, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                      stderr=subprocess.DEVNULL, bufsize=0)
        return self._proc

    def close(self) -> None:
        proc, self._proc = self._proc, None
        if proc is not None:
            try:
                proc.stdin.close()  # type: ignore[union-attr]
                proc.wait(timeout=1)
            except Exception:
                proc.kill()

    def __enter__(self) -> "BendGate":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def _read_line(self, proc: subprocess.Popen[bytes]) -> bytes:
        out: list[bytes] = []

        def reader() -> None:
            out.append(proc.stdout.readline())  # type: ignore[union-attr]

        t = threading.Thread(target=reader, daemon=True)
        t.start()
        t.join(self.timeout_s)
        if t.is_alive() or not out:
            proc.kill()
            self._proc = None
            raise TimeoutError("bend gate timed out")
        return out[0]

    def raw(self, tokens: Iterable[Any]) -> tuple[bool, tuple[int, ...]]:
        """Send one request; raise on any transport or protocol failure."""

        line = (" ".join(str(t) for t in tokens) + "\n").encode("ascii")
        with self._lock:
            proc = self._start()
            try:
                proc.stdin.write(line)  # type: ignore[union-attr]
            except (BrokenPipeError, OSError):
                self._proc = None
                raise
            reply = self._read_line(proc)
        if not reply:
            self._proc = None
            raise RuntimeError("bend gate exited")
        return parse_reply(reply.decode("ascii", "replace"))

    # -- decisions -----------------------------------------------------------
    def _decide(self, tokens: Iterable[Any], names: Mapping[int, str]) -> GateDecision:
        t0 = time.perf_counter()
        try:
            allowed, codes = self.raw(tokens)
        except Exception as exc:  # every failure denies
            return GateDecision(False, ("bend.unavailable",), "fail-closed", self.revision,
                                latency_us=(time.perf_counter() - t0) * 1e6, detail=repr(exc))
        dt = (time.perf_counter() - t0) * 1e6
        if allowed:
            return GateDecision(True, (), "bend", self.revision, latency_us=dt)
        labels = tuple("unparseable" if c == UNPARSEABLE else names.get(c, f"unknown:{c}") for c in codes)
        return GateDecision(False, labels, "bend", self.revision, latency_us=dt)

    def check_document(self, doc: Any, catalog: Mapping[str, Any] | None,
                       canonical: Callable[[Any], Sequence[Any]] | None = None) -> GateDecision:
        """Structural gate for a document; with ``canonical``, a disagreement denies."""

        enc = encode_document(doc, catalog)
        if enc.unsupported:
            return GateDecision(False, ("core.unsupported",), "unsupported", self.revision, detail=enc.unsupported)
        if not enc.representable:
            return GateDecision(False, tuple(sorted({c for c, _ in enc.shape})), "host-shape", self.revision)
        decision = self._decide(enc.tokens or [], RULE_CODES)
        if canonical is not None and decision.allowed:
            try:
                issues = list(canonical(doc))
            except Exception as exc:
                return GateDecision(False, ("canonical.error",), "canonical-mismatch", self.revision, detail=repr(exc))
            if issues:
                return GateDecision(False, tuple(sorted({getattr(i, "code", str(i)) for i in issues})),
                                    "canonical-mismatch", self.revision,
                                    detail="bend allowed, canonical validator rejected")
        return decision

    def check_spawn(self, proposal: SpawnProposal) -> GateDecision:
        try:
            tokens = encode_transition(proposal)
        except ValueError as exc:
            return GateDecision(False, ("core.unrepresentable",), "host-shape", self.revision, detail=str(exc))
        return self._decide(tokens, TRANSITION_CODES)


def load_catalog(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None
