#!/usr/bin/env python3
"""Differential parity + latency: Bend AODL structural gate vs the canonical validator.

z0intelligence#48 experiment.  Frozen reference: third_party/aodl_a848270
(kvnloo/aodl@a848270).  Corpus:

* every AODL fixture (valid / invalid / compile-stop) and z0int's own emitted
  AODL example(s);
* targeted mutations: for every 0.2 fixture, one mutation per structural
  rule family plus the validator's equality/typing quirks;
* seeded random structural fuzz over the same bases.

For every case the canonical ``validate()`` verdict (an exception counts as a
rejection) is compared with the Bend path (host shape checks + Core IR +
kernel).  On shape-clean cases the multiset of issue codes is compared too.

    PYTHONPATH=src python benchmarks/bend_gate_parity.py --fuzz 20000 \
        --out ~/.z0int/research/claude-code-overnight/bend-parity.json
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import platform
import random
import resource
import statistics
import subprocess
import sys
import time
from collections import Counter
from pathlib import Path
from typing import Any, Callable, Iterator

ROOT = Path(__file__).resolve().parents[1]
REF = ROOT / "third_party" / "aodl_a848270"
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(REF))

from aodl_contract import validate  # noqa: E402
from z0int import bend_gate as bg  # noqa: E402

CATALOG = bg.load_catalog(REF / "harnesses" / "catalog.json")
H = "0" * 64


# ---------------------------------------------------------------------------
# Corpus
# ---------------------------------------------------------------------------


def fixtures() -> list[tuple[str, Any]]:
    out = []
    for p in sorted((REF / "examples").rglob("*.json")):
        out.append((f"fixture:{p.parent.name}/{p.stem}", json.loads(p.read_text())))
    for p in sorted((ROOT / "examples" / "aodl").glob("*.json")):
        out.append((f"z0int:{p.stem}", json.loads(p.read_text())))
    return out


def _edge(eid: str, rel: str, a: str, b: str, ap: str = "out", bp: str = "in", grant: list | None = None) -> dict:
    return {
        "id": eid, "relation": rel, "from": a, "to": b, "fromPort": ap, "toPort": bp,
        "delivery": {"order": "ordered", "idempotent": True},
        "authority": {"grant": list(grant or []), "delegationDepth": 0},
        "provenance": {"sourceHash": H},
    }


def _node(nid: str, kind: str = "task", caps: list | None = None, ceil: list | None = None, schema: str = "Task") -> dict:
    return {
        "id": nid, "kind": kind,
        "ports": [{"id": "in", "direction": "in", "schema": schema}, {"id": "out", "direction": "out", "schema": schema}],
        "capabilities": list(caps or ["execute"]), "authorityCeiling": list(ceil if ceil is not None else (caps or ["execute"])),
    }


def _g(d: dict) -> dict:
    return d["intentGraph"]


def _first_edge(d: dict) -> dict | None:
    es = _g(d).get("edges") or []
    return es[0] if es else None


def _n(d: dict, nid: str) -> dict:
    return next(n for n in _g(d)["nodes"] if n.get("id") == nid)


def _attach(d: dict, kind: str = "task", rel: str = "data", grant: list | None = None, caps=None, ceil=None, to_new=True):
    """Add a node wired to the first node through its ports."""

    first = _g(d)["nodes"][0]
    out_port = next((p for p in first.get("ports", []) if p.get("direction") == "out"), None)
    nid = "mut-node"
    schema = out_port.get("schema") if out_port else "Task"
    node = _node(nid, kind, caps, ceil, schema=schema)
    _g(d)["nodes"].append(node)
    if out_port is not None:
        if to_new:
            _g(d)["edges"].append(_edge("mut-edge", rel, first["id"], nid, out_port["id"], "in", grant))
        else:
            in_port = next((p for p in first.get("ports", []) if p.get("direction") == "in"), None)
            if in_port is not None:
                node["ports"][1]["schema"] = in_port.get("schema")
                _g(d)["edges"].append(_edge("mut-edge", rel, nid, first["id"], "out", in_port["id"], grant))
    return node


Mut = Callable[[dict], None]


def targeted() -> dict[str, Mut]:
    m: dict[str, Mut] = {}

    def kind_unknown(d):
        _g(d)["nodes"][0]["kind"] = "fly"
    m["kind.unknown"] = kind_unknown
    m["kind.unhashable"] = lambda d: _g(d)["nodes"][0].__setitem__("kind", ["task"])
    m["kind.case"] = lambda d: _g(d)["nodes"][0].__setitem__("kind", "Task")

    def harness_on_task(d):
        _attach(d, "task")["harness"] = "omp"
    m["harness.on_task"] = harness_on_task

    def harness_unknown(d):
        _attach(d, "executor")["harness"] = "langchain"
    m["harness.unknown"] = harness_unknown

    def harness_control_room(d):
        _attach(d, "executor")["harness"] = "o8"
    m["harness.control_room"] = harness_control_room

    def harness_null(d):
        _attach(d, "executor")["harness"] = None
    m["harness.null"] = harness_null

    def port_dir(d):
        _g(d)["nodes"][0]["ports"][0]["direction"] = "both"
    m["port.direction"] = port_dir

    def port_dup(d):
        ps = _g(d)["nodes"][0]["ports"]
        ps.append(copy.deepcopy(ps[0]))
    m["port.duplicate"] = port_dup

    def port_int_id(d):
        n = _attach(d, "task")
        n["ports"].append({"id": 5, "direction": "in", "schema": "Task"})
        e = _g(d)["edges"][-1]
        e["toPort"] = 5  # str(5) == "5" is not the int port 5
    m["port.int_id"] = port_int_id

    def node_dup(d):
        _g(d)["nodes"].append(copy.deepcopy(_g(d)["nodes"][0]))
    m["node.duplicate"] = node_dup

    def rel_unknown(d):
        e = _first_edge(d)
        if e:
            e["relation"] = "review"
    m["relation.unknown"] = rel_unknown

    def edge_dup(d):
        e = _first_edge(d)
        if e:
            _g(d)["edges"].append(copy.deepcopy(e))
    m["edge.duplicate"] = edge_dup

    def self_edge(d):
        e = _first_edge(d)
        if e:
            e["to"] = e["from"]
    m["edge.self"] = self_edge

    def endpoint(d):
        e = _first_edge(d)
        if e:
            e["to"] = "ghost"
    m["edge.endpoint"] = endpoint

    def endpoint_null(d):
        e = _first_edge(d)
        if e:
            e["from"] = None
            e["to"] = None
    m["edge.endpoint_null_self"] = endpoint_null

    def port_unknown(d):
        e = _first_edge(d)
        if e:
            e["fromPort"] = "nope"
    m["edge.port_unknown"] = port_unknown

    def port_reversed(d):
        e = _first_edge(d)
        if e:
            e["fromPort"], e["toPort"] = e["toPort"], e["fromPort"]
    m["edge.port_reversed"] = port_reversed

    def schema_mismatch(d):
        n = _attach(d, "task")
        n["ports"][0]["schema"] = "Other"
    m["schema.mismatch"] = schema_mismatch

    def schema_numeric_equal(d):
        first = _g(d)["nodes"][0]
        for p in first["ports"]:
            if p.get("direction") == "out":
                p["schema"] = 1
        n = _attach(d, "task")
        n["ports"][0]["schema"] = 1.0  # 1 == 1.0 in Python
    m["schema.int_float_equal"] = schema_numeric_equal

    def schema_numeric_str(d):
        first = _g(d)["nodes"][0]
        for p in first["ports"]:
            if p.get("direction") == "out":
                p["schema"] = 1
        n = _attach(d, "task")
        n["ports"][0]["schema"] = "1"
    m["schema.int_vs_str"] = schema_numeric_str

    def deleg_no_grant(d):
        _attach(d, "task", rel="delegation", grant=[])
    m["authority.delegation_without_grant"] = deleg_no_grant

    def pay_grant(d):
        _attach(d, "task", grant=["execute", "PayMent"])
    m["payment.grant_mixed_case"] = pay_grant

    def verifier_merge(d):
        _attach(d, "verifier", rel="verification", grant=["merge"])
    m["gate.verifier_merge_grant"] = verifier_merge

    def gate_delegated(d):
        first = _g(d)["nodes"][0]
        first_kind = first["kind"]
        first["kind"] = "humanGate"
        _attach(d, "executor", rel="delegation", grant=["approve"])["harness"] = "omp"
        _ = first_kind
    m["gate.humangate_delegated"] = gate_delegated

    def isolated(d):
        _g(d)["nodes"].append(_node("lonely"))
    m["reachability.isolated"] = isolated

    def cycle(d):
        _attach(d, "task", rel="dependency")
        _g(d)["edges"].append(_edge("mut-back", "dependency", "mut-node", _g(d)["nodes"][0]["id"], "out",
                                    next((p["id"] for p in _g(d)["nodes"][0]["ports"] if p.get("direction") == "in"), "in")))
    m["cycle.dependency"] = cycle

    def cycle_edge_kind(d):
        _attach(d, "task", rel="dependency")
        e = _edge("mut-back", "data", "mut-node", _g(d)["nodes"][0]["id"], "out",
                  next((p["id"] for p in _g(d)["nodes"][0]["ports"] if p.get("direction") == "in"), "in"))
        e["kind"] = "dependency"  # a 0.2 edge with a 0.1-style kind still counts
        _g(d)["edges"].append(e)
    m["cycle.edge_kind_quirk"] = cycle_edge_kind

    def fan_in(d):
        _g(d)["nodes"] += [_node("fa"), _node("fb"), _node("fc")]
        _g(d)["edges"] += [_edge("efa", "dependency", "fa", "fc"), _edge("efb", "dependency", "fb", "fc"),
                           _edge("efx", "data", _g(d)["nodes"][0]["id"], "fa",
                                 next((p["id"] for p in _g(d)["nodes"][0]["ports"] if p.get("direction") == "out"), "out"))]
        _g(d)["nodes"][-3]["ports"][0]["schema"] = next(
            (p.get("schema") for p in _g(d)["nodes"][0]["ports"] if p.get("direction") == "out"), "Task")
        d["policies"].pop("fanIn", None)
    m["fan_in.implicit"] = fan_in

    def fan_in_list(d):
        fan_in(d)
        d["policies"]["fanIn"] = ["all"]  # unhashable: the validator raises
    m["fan_in.unhashable"] = fan_in_list

    m["fan_in.quorum_missing"] = lambda d: d["policies"].update({"fanIn": "quorum"})
    m["fan_in.quorum_true"] = lambda d: d["policies"].update({"fanIn": "quorum", "quorum": True})
    m["fan_in.quorum_zero"] = lambda d: d["policies"].update({"fanIn": "quorum", "quorum": 0})
    m["fan_in.reducer_missing"] = lambda d: d["policies"].update({"fanIn": "reducer"})
    m["fan_in.reducer_ok"] = lambda d: d["policies"].update({"fanIn": "reducer", "reducer": "concat"})

    def unbounded(d):
        d["policies"]["kinds"] = list(d["policies"].get("kinds") or []) + ["recursion"]
        d["policies"]["dynamic"] = {"allowed": True, "maxChildren": 0}
    m["bounds.unbounded"] = unbounded

    def bounded_no_budget(d):
        d["policies"]["dynamic"] = {"allowed": True, "maxChildren": 3, "maxDepth": 2}
        d["constraints"]["budgets"] = {}
    m["bounds.no_budget"] = bounded_no_budget

    def bounded_float(d):
        d["policies"]["dynamic"] = {"allowed": "yes", "maxChildren": 3.0, "maxDepth": 2}
    m["bounds.float_max"] = bounded_float

    def bounded_bool(d):
        d["policies"]["dynamic"] = {"allowed": 1, "maxChildren": True, "maxDepth": True}
    m["bounds.bool_max"] = bounded_bool

    def auction_bad(d):
        d["policies"]["kinds"] = ["auction"]
        d["policies"]["auction"] = {"phases": ["announce", "bid", "award"]}
    m["auction.phases"] = auction_bad

    def auction_not_obj(d):
        d["policies"]["kinds"] = ["auction"]
        d["policies"].pop("auction", None)
    m["auction.missing"] = auction_not_obj

    def auction_pay(val):
        def f(d):
            d["policies"]["auction"] = {"phases": list(("announce", "bid", "award", "execute", "verify", "settle")),
                                        "payment": val}
        return f
    m["auction.payment_true"] = auction_pay(True)
    m["auction.payment_zero"] = auction_pay(0)
    m["auction.payment_zero_float"] = auction_pay(0.0)
    m["auction.payment_false"] = auction_pay(False)
    m["auction.payment_null"] = auction_pay(None)
    m["auction.payment_str"] = auction_pay("escrow")

    def priv_cap(d):
        _g(d)["nodes"][0]["capabilities"] = list(_g(d)["nodes"][0].get("capabilities") or []) + ["wallet"]
    m["privilege.undeclared"] = priv_cap

    def priv_cap_declared(d):
        n = _g(d)["nodes"][0]
        n["capabilities"] = list(n.get("capabilities") or []) + ["wallet"]
        n["authorityCeiling"] = list(n.get("authorityCeiling") or []) + ["WALLET"]
    m["privilege.declared_upper_ceiling"] = priv_cap_declared

    def priv_cap_upper(d):
        n = _g(d)["nodes"][0]
        n["capabilities"] = list(n.get("capabilities") or []) + ["Wallet"]
        n["authorityCeiling"] = list(n.get("authorityCeiling") or []) + ["wallet"]
    m["privilege.upper_cap_quirk"] = priv_cap_upper

    def priv_kelvin(d):
        n = _g(d)["nodes"][0]
        n["capabilities"] = list(n.get("capabilities") or []) + ["K", "SECRET"]
    m["privilege.unicode"] = priv_kelvin

    def verifier_holds(d):
        _attach(d, "verifier", rel="verification", caps=["verify", "Deploy"], ceil=["verify"])
    m["gate.verifier_holds"] = verifier_holds

    def ev_unknown(d):
        d["eventLog"] = [{"eventId": "e1", "type": "fail", "sourceHash": H, "revision": 0, "causalParents": [], "payload": {}}]
    m["event.unknown"] = ev_unknown

    def ev_ok(d):
        d["eventLog"] = [{"eventId": "e1", "type": "route", "sourceHash": H, "revision": 0, "causalParents": [], "payload": {}}]
    m["event.ok"] = ev_ok

    def obs_bad(d):
        g = copy.deepcopy(_g(d))
        if g["edges"]:
            g["edges"][0]["to"] = g["edges"][0]["from"]
        d["observedGraph"] = g
    m["observed.self_edge"] = obs_bad

    def obs_ok(d):
        d["observedGraph"] = copy.deepcopy(_g(d))
    m["observed.copy"] = obs_ok

    def obs_fanin(d):
        g = copy.deepcopy(_g(d))
        g["nodes"] += [_node("fa"), _node("fb"), _node("fc")]
        g["edges"] += [_edge("efa", "dependency", "fa", "fc"), _edge("efb", "dependency", "fb", "fc"),
                       _edge("efc", "data", "fc", "fa")]
        d["observedGraph"] = g
        d["policies"].pop("fanIn", None)
    m["observed.fan_in_uses_shadow_policy"] = obs_fanin

    # shape
    m["shape.missing_constraints"] = lambda d: d.pop("constraints")
    m["shape.bad_graph_id"] = lambda d: d.__setitem__("graphId", "1bad")
    m["shape.bool_revision"] = lambda d: d.__setitem__("revision", True)
    m["shape.bad_hash"] = lambda d: d["provenance"].__setitem__("sourceHash", "abc")
    m["shape.extra_field"] = lambda d: d.__setitem__("openQuestions", [])

    def delivery_missing(d):
        e = _first_edge(d)
        if e:
            e["delivery"] = {"order": "ordered"}
    m["shape.delivery"] = delivery_missing

    def delivery_missing_dead_edge(d):
        e = _first_edge(d)
        if e:
            e["delivery"] = {"order": "ordered"}
            e["to"] = "ghost"  # past the endpoint `continue`: delivery is never checked
    m["shape.delivery_on_dead_edge"] = delivery_missing_dead_edge

    def grant_string(d):
        e = _first_edge(d)
        if e:
            e["authority"]["grant"] = "execute"
    m["shape.grant_not_list"] = grant_string

    def caps_string(d):
        _g(d)["nodes"][0]["capabilities"] = "payment"  # not a list: never inspected
    m["quirk.caps_not_list"] = caps_string
    return m


# ---------------------------------------------------------------------------
# Random structural fuzz
# ---------------------------------------------------------------------------

VOCAB = [
    "task", "executor", "model", "tool", "service", "memory", "stateStore", "humanGate", "environment",
    "artifact", "verifier", "dependency", "data", "message", "delegation", "critique", "verification",
    "allocation", "control", "observation", "in", "out", "both", "all", "any", "quorum", "reducer",
    "recursion", "auction", "sequence", "payment", "Payment", "wallet", "SUDO", "secret", "merge", "Deploy",
    "approve", "execute", "verify", "route", "spawn", "fail", "omp", "o8", "hermes", "langchain", "Task",
    "unsupported", "announce", "bid", "award", "settle", "ghost", "",
]


def _paths(obj: Any, path: tuple = ()) -> Iterator[tuple]:
    yield path
    if isinstance(obj, dict):
        for k, v in obj.items():
            yield from _paths(v, path + (k,))
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            yield from _paths(v, path + (i,))


def _get(obj: Any, path: tuple) -> Any:
    for p in path:
        obj = obj[p]
    return obj


def _rand_value(rng: random.Random, doc: Any) -> Any:
    r = rng.random()
    if r < 0.45:
        return rng.choice(VOCAB)
    if r < 0.6:
        ids = [n.get("id") for n in (doc.get("intentGraph", {}).get("nodes") or []) if isinstance(n, dict)]
        return rng.choice(ids) if ids else "x"
    if r < 0.7:
        return rng.choice([0, 1, 2, -1, 1.0, 0.0, True, False, None, 3, 10**20])
    if r < 0.8:
        return [rng.choice(VOCAB) for _ in range(rng.randint(0, 3))]
    if r < 0.85:
        return {}
    # a subtree from elsewhere in the document
    paths = list(_paths(doc))
    return copy.deepcopy(_get(doc, rng.choice(paths)))


def fuzz_mutate(doc: Any, rng: random.Random, steps: int) -> Any:
    d = copy.deepcopy(doc)
    for _ in range(steps):
        paths = [p for p in _paths(d) if p]
        if not paths:
            break
        path = rng.choice(paths)
        parent = _get(d, path[:-1])
        key = path[-1]
        op = rng.random()
        if op < 0.5:
            parent[key] = _rand_value(rng, d)
        elif op < 0.65:
            if isinstance(parent, dict):
                parent.pop(key, None)
            else:
                del parent[key]
        elif op < 0.8 and isinstance(parent, list):
            parent.insert(key, copy.deepcopy(parent[key]))
        elif op < 0.9 and isinstance(parent[key], str):
            s = parent[key]
            parent[key] = rng.choice([s.upper(), s.lower(), s.capitalize(), s + "x", s[:-1]])
        else:
            other = rng.choice(paths)
            try:
                o_parent, o_key = _get(d, other[:-1]), other[-1]
                parent[key], o_parent[o_key] = copy.deepcopy(o_parent[o_key]), copy.deepcopy(parent[key])
            except (KeyError, IndexError, TypeError):
                pass
    return d


# ---------------------------------------------------------------------------
# Evaluation
# ---------------------------------------------------------------------------


def python_verdict(doc: Any) -> tuple[bool, Counter, str | None]:
    try:
        issues = validate(doc)
    except Exception as exc:  # the canonical validator can raise; callers must treat that as reject
        return False, Counter(), type(exc).__name__
    return (not issues), Counter(i.code for i in issues), None


def pct(xs: list[float], q: float) -> float:
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(q * len(xs)))]


def run(n_fuzz: int, seed: int, out: Path | None, spawn_sample: int) -> dict:
    gate = bg.BendGate(timeout_s=10.0)
    base = fixtures()
    cases: list[tuple[str, Any]] = list(base)
    muts = targeted()
    bases02 = [(n, d) for n, d in base if isinstance(d, dict) and d.get("specVersion") == "0.2"]
    for name, doc in bases02:
        for mname, fn in muts.items():
            d = copy.deepcopy(doc)
            try:
                fn(d)
            except (KeyError, IndexError, StopIteration, TypeError, AttributeError):
                continue
            cases.append((f"mut:{mname}@{name}", d))
    rng = random.Random(seed)
    for i in range(n_fuzz):
        name, doc = rng.choice(bases02)
        cases.append((f"fuzz:{i}@{name}", fuzz_mutate(doc, rng, rng.randint(1, 3))))

    corpus_hash = hashlib.sha256()
    rows = []
    py_lat, enc_lat, kern_lat, path_lat = [], [], [], []
    for name, doc in cases:
        blob = json.dumps(doc, sort_keys=True, default=str).encode()
        corpus_hash.update(blob)
        t0 = time.perf_counter()
        p_ok, p_codes, p_exc = python_verdict(doc)
        t1 = time.perf_counter()
        enc = bg.encode_document(doc, CATALOG)
        t2 = time.perf_counter()
        if enc.representable:
            try:
                b_ok, codes = gate.raw(enc.tokens or [])
                b_codes = Counter(bg.RULE_CODES.get(c, f"?{c}") for c in codes)
                route = "kernel"
            except Exception as exc:
                b_ok, b_codes, route = False, Counter({"fail-closed": 1}), f"error:{exc!r}"
        else:
            b_ok = False
            b_codes = Counter(c for c, _ in enc.shape)
            route = "unsupported" if enc.unsupported else "host-shape"
        t3 = time.perf_counter()
        py_lat.append((t1 - t0) * 1e6)
        enc_lat.append((t2 - t1) * 1e6)
        path_lat.append((t3 - t1) * 1e6)
        if route == "kernel":
            kern_lat.append((t3 - t2) * 1e6)
        verdict_agree = p_ok == b_ok
        explained = None
        if not verdict_agree:
            if route == "unsupported":
                explained = "hotl-0.1 not modeled (fails closed)"
        codes_agree = None
        if route == "kernel" and p_exc is None:
            codes_agree = p_codes == b_codes
        rows.append({
            "case": name, "kind": name.split(":")[0], "python_ok": p_ok, "python_exception": p_exc,
            "python_codes": dict(p_codes), "bend_ok": b_ok, "bend_codes": dict(b_codes), "route": route,
            "verdict_agree": verdict_agree, "explained": explained, "codes_agree": codes_agree,
        })

    # one-shot process-per-decision latency (what a naive adapter would pay)
    spawn_lat = []
    binary = str(gate.binary)
    for name, doc in cases[:spawn_sample]:
        enc = bg.encode_document(doc, CATALOG)
        if not enc.representable:
            continue
        args = [binary, "--threads", "1", *[str(t) for t in enc.tokens or []]]
        t0 = time.perf_counter()
        subprocess.run(args, capture_output=True, timeout=30)
        spawn_lat.append((time.perf_counter() - t0) * 1e6)

    kernel_rss_kb = None
    try:
        status = Path(f"/proc/{gate._proc.pid}/status").read_text() if gate._proc else ""  # noqa: SLF001
        for line in status.splitlines():
            if line.startswith("VmHWM:"):
                kernel_rss_kb = int(line.split()[1])
    except OSError:
        pass
    gate.close()

    def summarize(kind: str | None) -> dict:
        rs = [r for r in rows if kind is None or r["kind"] == kind]
        dis = [r for r in rs if not r["verdict_agree"]]
        return {
            "cases": len(rs),
            "python_accept": sum(r["python_ok"] for r in rs),
            "python_reject": sum(not r["python_ok"] for r in rs),
            "python_exceptions": sum(r["python_exception"] is not None for r in rs),
            "via_kernel": sum(r["route"] == "kernel" for r in rs),
            "via_host_shape": sum(r["route"] == "host-shape" for r in rs),
            "verdict_disagree": len(dis),
            "verdict_disagree_unexplained": sum(r["explained"] is None for r in dis),
            "false_allow": sum(r["bend_ok"] and not r["python_ok"] for r in rs),
            "false_deny": sum(r["python_ok"] and not r["bend_ok"] for r in rs),
            "codes_compared": sum(r["codes_agree"] is not None for r in rs),
            "codes_disagree": sum(r["codes_agree"] is False for r in rs),
        }

    def lat(xs: list[float]) -> dict:
        if not xs:
            return {}
        return {"n": len(xs), "p50_us": round(pct(xs, 0.5), 1), "p95_us": round(pct(xs, 0.95), 1),
                "mean_us": round(statistics.fmean(xs), 1)}

    rule_hits = Counter()
    for r in rows:
        if r["route"] == "kernel":
            rule_hits.update(r["bend_codes"].keys())
    report = {
        "experiment": "z0intelligence#48 Bend AODL structural gate parity",
        "aodl_reference": bg.AODL_REFERENCE,
        "kernel_revision": bg.kernel_revision(),
        "bend_version": subprocess.run([str(Path.home() / ".bend/bin/bend"), "version"], capture_output=True,
                                       text=True, env={"BEND_NO_TELEMETRY": "1", "PATH": "/usr/bin"}).stdout.strip(),
        "seed": seed, "fuzz": n_fuzz, "corpus_sha256": corpus_hash.hexdigest(),
        "host": {"python": platform.python_version(), "machine": platform.machine(), "cpu": platform.processor()},
        "summary": {k or "all": summarize(k) for k in (None, "fixture", "z0int", "mut", "fuzz")},
        "kernel_rule_hits": dict(sorted(rule_hits.items())),
        "latency": {
            "python_validate_inprocess": lat(py_lat),
            "host_encode": lat(enc_lat),
            "bend_kernel_roundtrip_persistent": lat(kern_lat),
            "bend_path_total_encode_plus_kernel": lat(path_lat),
            "bend_process_per_decision": lat(spawn_lat),
        },
        "kernel_peak_rss_kb": kernel_rss_kb,
        "harness_peak_rss_kb": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
        "disagreements": [r for r in rows if not r["verdict_agree"]][:50],
        "code_disagreements": [r for r in rows if r["codes_agree"] is False][:50],
        "python_exceptions": [r for r in rows if r["python_exception"]][:20],
    }
    if out:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(report, indent=2) + "\n")
    return report


# ---------------------------------------------------------------------------
# Transition gate differential fuzz
# ---------------------------------------------------------------------------


def _boundary(rng: random.Random, around: int) -> Any:
    """Mostly values at the boundary (around-1 / around / around+1)."""

    r = rng.random()
    if r < 0.85:
        return max(0, around + rng.choice([-2, -1, -1, 0, 0, 1]))
    if r < 0.95:
        return rng.choice([0, 1, 2**32 - 1, 2**32, 2**32 + 1, 2**47 - 1])
    return rng.choice([2**47, 2**60, -1, 1.5, None, True])  # unrepresentable: must deny


def gen_spawn(rng: random.Random) -> bg.SpawnProposal:
    caps = ["execute", "verify", "store", "route", "merge", "deploy", "payment"]
    ceiling = rng.sample(caps, rng.randint(1, 5))
    if rng.random() < 0.8:
        requested = rng.sample(ceiling, rng.randint(0, len(ceiling)))
    else:
        requested = rng.sample(caps, rng.randint(1, 3))
    maxc = rng.choice([1, 2, 4, 8, 2**33]) if rng.random() < 0.93 else rng.choice([0, "4", None, 3.0, True])
    maxd = rng.choice([1, 2, 3, 2**33]) if rng.random() < 0.95 else rng.choice([0, None])
    kids = _boundary(rng, (maxc if isinstance(maxc, int) and not isinstance(maxc, bool) else 2) - 1)
    depth = _boundary(rng, (maxd if isinstance(maxd, int) else 1) - 1)
    budgets, observed, proposed = {}, {}, {}
    for dim in bg.BUDGET_DIMS:
        if rng.random() < 0.4:
            limit = rng.choice([0, 100, 4000, 2**32 + 7, 2**46])
            budgets[dim] = limit
            seen = rng.randint(0, limit)
            observed[dim] = seen
            proposed[dim] = _boundary(rng, limit - seen)
        elif rng.random() < 0.3:
            observed[dim] = rng.randint(0, 50)
    rev = rng.randint(0, 3)
    return bg.SpawnProposal(
        contract_revision=rev, request_revision=rev if rng.random() < 0.9 else rev + 1,
        dynamic_allowed=rng.random() < 0.95, max_children=maxc, max_depth=maxd,
        live_children=kids, parent_depth=depth, budgets=budgets, observed=observed, proposed=proposed,
        ceiling=ceiling, requested=requested,
    )


def run_transitions(n: int, seed: int) -> dict:
    from z0int.aodl import AodlRuntimeContract, AodlSpend, check_budget

    gate = bg.BendGate(timeout_s=10.0)
    rng = random.Random(seed)
    stats = Counter()
    disagreements = []
    bend_lat, ref_lat = [], []
    for i in range(n):
        p = gen_spawn(rng)
        try:
            tokens = bg.encode_transition(p)
        except ValueError:
            stats["unrepresentable_denied"] += 1
            continue
        t0 = time.perf_counter()
        try:
            ref = set(bg.reference_transition(p))
        except TypeError:
            stats["reference_error"] += 1
            continue
        t1 = time.perf_counter()
        try:
            ok, codes = gate.raw(tokens)
        except Exception:  # kernel abort: the adapter would deny
            stats["kernel_abort_denied"] += 1
            continue
        t2 = time.perf_counter()
        ref_lat.append((t1 - t0) * 1e6)
        bend_lat.append((t2 - t1) * 1e6)
        stats["compared"] += 1
        stats["allow" if ok else "deny"] += 1
        if set(codes) != ref or ok != (not ref):
            stats["disagree"] += 1
            disagreements.append({"case": i, "bend": codes, "reference": sorted(ref), "proposal": repr(p)})
        # budget dimension against z0int's pre-existing float-based Gamma guard
        contract = AodlRuntimeContract("g", 0, "c", "omp", (), {}, dict(p.budgets), {}, "")
        spend = lambda m: AodlSpend(**{k: m.get(k, 0) or 0 for k in bg.BUDGET_DIMS})  # noqa: E731
        cb = check_budget(contract, observed=spend(p.observed), proposed=spend(p.proposed))
        stats["check_budget_compared"] += 1
        if cb.allowed != (105 not in codes):
            stats["check_budget_disagree"] += 1
            disagreements.append({"case": i, "vs": "check_budget", "bend": codes, "exceeded": cb.exceeded,
                                  "proposal": repr(p)})
    gate.close()

    def lat(xs: list[float]) -> dict:
        return {"n": len(xs), "p50_us": round(pct(xs, 0.5), 1), "p95_us": round(pct(xs, 0.95), 1)} if xs else {}

    return {"stats": dict(stats), "latency": {"bend_roundtrip": lat(bend_lat), "python_reference": lat(ref_lat)},
            "disagreements": disagreements[:20]}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--fuzz", type=int, default=2000)
    ap.add_argument("--seed", type=int, default=48)
    ap.add_argument("--spawn-sample", type=int, default=200)
    ap.add_argument("--transitions", type=int, default=5000)
    ap.add_argument("--out", type=Path)
    args = ap.parse_args(argv)
    rep = run(args.fuzz, args.seed, None, args.spawn_sample)
    rep["transitions"] = run_transitions(args.transitions, args.seed)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(rep, indent=2) + "\n")
    print(json.dumps(rep["transitions"], indent=2)[:3000])
    view = {k: rep[k] for k in ("aodl_reference", "kernel_revision", "bend_version", "corpus_sha256", "summary",
                                "latency", "kernel_peak_rss_kb")}
    print(json.dumps(view, indent=2))
    for r in rep["disagreements"][:10]:
        print("DISAGREE", r["case"], r["python_codes"], r["python_exception"], r["bend_codes"], r["route"], r["explained"])
    for r in rep["code_disagreements"][:10]:
        print("CODES", r["case"], r["python_codes"], r["bend_codes"])
    s = rep["summary"]["all"]
    t = rep["transitions"]["stats"]
    ok = s["verdict_disagree_unexplained"] == 0 and s["codes_disagree"] == 0 and not t.get("disagree")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
