"""Host-owned governed remote worker canary.

The harness supplies task intent and a stable trace id. The host owns the AODL
contract, observed controller state, provider/model selection, and the protocol-v3
envelope sent to the remote executor.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import urllib.request
from typing import Any, Mapping

from .receipt import append_receipt, receipts_path
from .worker_routing import (
    configuration,
    estimate,
    free_route,
    messages_for,
    validate_request,
)

DEFAULT_EXECUTOR_URL = "http://z0intelligence-executor.hermes-lab.svc.cluster.local:11503"
PREPARE_CAPABILITY = "aodl.governed_request"


def _contract_api():
    import aodl_contract  # type: ignore

    if getattr(aodl_contract, "CANON_VERSION", None) != "aodl-canon-1":
        raise ValueError("aodl-canon-1 is required")
    return aodl_contract


def load_contract() -> dict[str, Any]:
    raw = os.environ.get("Z0INT_AODL_CONTRACT_PATH")
    if not raw:
        raise ValueError("Z0INT_AODL_CONTRACT_PATH is required for governed remote execution")
    path = Path(raw).expanduser()
    doc = json.loads(path.read_text(encoding="utf-8"))
    api = _contract_api()
    issues = api.validate(doc)
    if issues:
        raise ValueError("invalid governed AODL contract: " + str(issues[0]))
    if not isinstance(doc, dict):
        raise ValueError("AODL contract must be an object")
    return doc


def parent_node_id(document: Mapping[str, Any]) -> str:
    configured = os.environ.get("Z0INT_AODL_PARENT_NODE_ID", "parent")
    graph = document.get("intentGraph")
    nodes = graph.get("nodes") if isinstance(graph, Mapping) else None
    if not isinstance(nodes, list):
        raise ValueError("AODL intentGraph.nodes must be an array")
    if not any(isinstance(node, Mapping) and node.get("id") == configured for node in nodes):
        raise ValueError("configured AODL parent node is not declared")
    return configured


def _latest_receipts() -> list[dict[str, Any]]:
    path = receipts_path()
    if not path.exists():
        return []
    latest: dict[str, dict[str, Any]] = {}
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            row = json.loads(line)
            trace = row.get("trace_id")
            if isinstance(trace, str):
                latest[trace] = row
    return list(latest.values())


def observed_spawn_state(document: Mapping[str, Any], parent: str) -> dict[str, Any]:
    """Controller-observed state for the next root-parent spawn."""

    provenance = document.get("provenance")
    source_hash = provenance.get("sourceHash") if isinstance(provenance, Mapping) else None
    if not isinstance(source_hash, str):
        raise ValueError("AODL provenance.sourceHash is required")

    rows = _latest_receipts()
    admission_ids: set[str] = set()
    for row in rows:
        if row.get("capability_id") != "aodl.structural_admission":
            continue
        extra = row.get("extra")
        payload = extra.get("aodl_admission") if isinstance(extra, Mapping) else None
        if (
            isinstance(payload, Mapping)
            and payload.get("allowed") is True
            and payload.get("aodl_intent_source_hash") == source_hash
            and payload.get("parent_node_id") == parent
        ):
            trace = row.get("trace_id")
            if isinstance(trace, str):
                admission_ids.add(trace)

    dispatch_ids: set[str] = set()
    live_children = 0
    for row in rows:
        if row.get("capability_id") != "intelligence.dispatch":
            continue
        extra = row.get("extra")
        if not isinstance(extra, Mapping) or extra.get("aodl_admission_receipt_id") not in admission_ids:
            continue
        trace = row.get("trace_id")
        if isinstance(trace, str):
            dispatch_ids.add(trace)
        if extra.get("status") == "started":
            live_children += 1

    observed_tokens = 0
    unknown_token_usage_attempts = 0
    for row in rows:
        extra = row.get("extra")
        if not isinstance(extra, Mapping) or extra.get("authority_dispatch_id") not in dispatch_ids:
            continue
        status = extra.get("status")
        if status not in {"completed", "failed", "incomplete", "completed_unmetered_or_unidentified"}:
            continue
        known = True
        values: list[int] = []
        for key in ("input_tokens", "output_tokens"):
            value = row.get(key)
            if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
                values.append(value)
            else:
                known = False
        if known:
            observed_tokens += sum(values)
        elif extra.get("physical_call_attempted") is True:
            unknown_token_usage_attempts += 1

    try:
        depth = int(os.environ.get("Z0INT_AODL_PARENT_DEPTH", "0"))
    except ValueError as exc:
        raise ValueError("Z0INT_AODL_PARENT_DEPTH must be an integer") from exc
    if depth < 0:
        raise ValueError("Z0INT_AODL_PARENT_DEPTH must be >= 0")

    return {
        "live_children": live_children,
        "parent_depth": depth,
        "observed": {"tokens": observed_tokens},
        "unknown_token_usage_attempts": unknown_token_usage_attempts,
    }


def _provider_model(policy: Mapping[str, Any], providers: Mapping[str, Any]) -> tuple[str, str]:
    from .aodl_canary_study import bounds
    study = bounds(policy, providers)
    if study is not None:
        if os.environ.get("Z0INT_GOVERNED_PROVIDER", "nous") != "nous":
            raise ValueError("governed study provider override is forbidden")
        return "nous", str(study["model"])
    provider = os.environ.get("Z0INT_GOVERNED_PROVIDER")
    if not provider:
        order = policy.get("free_provider_order")
        if not isinstance(order, list) or not order:
            raise ValueError("no governed provider configured")
        provider = str(order[0])
    if provider not in providers:
        raise ValueError("governed provider is unknown")
    defaults = policy.get("defaults")
    if not isinstance(defaults, Mapping) or not isinstance(defaults.get(provider), str):
        raise ValueError("governed provider has no default model")
    model = str(defaults[provider])
    if free_route(policy, provider, model) is None:
        raise ValueError("governed provider/model lacks validated zero-cost evidence")
    return provider, model


def _caller_fingerprint(args: Mapping[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(args, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode("utf-8")
    ).hexdigest()


def _prepare_trace_id(harness: str, trace_id: str) -> str:
    key = hashlib.sha256((harness + "\0" + trace_id).encode("utf-8")).hexdigest()
    return "governed-request-" + key


def _prepared(trace: str) -> dict[str, Any] | None:
    for row in _latest_receipts():
        if row.get("trace_id") == trace and row.get("capability_id") == PREPARE_CAPABILITY:
            return row
    return None


def build_remote_request(args: object) -> dict[str, Any]:
    if not isinstance(args, dict):
        raise ValueError("governed worker request must be an object")
    allowed = {"harness", "trace_id", "parent_agent", "task", "context", "max_tokens", "free_only", "allow_remote"}
    if set(args) - allowed:
        raise ValueError("unknown governed worker fields")
    for key in ("harness", "trace_id"):
        if not isinstance(args.get(key), str) or not args[key]:
            raise ValueError("invalid " + key)
    expected_harness = os.environ.get("Z0INT_GOVERNED_HARNESS", "omp")
    if args["harness"] != expected_harness:
        raise ValueError("governed remote canary is not enabled for this harness")
    if args.get("allow_remote") is not True:
        raise ValueError("governed remote execution requires explicit allow_remote=true")

    from .aodl_canary_study import bounds, validate_worker
    policy, providers = configuration()
    study = bounds(policy, providers)
    if study is not None and args.get("free_only") is True:
        raise ValueError("paid study cannot satisfy free_only=true")
    worker = {key: args[key] for key in ("task", "context", "parent_agent", "max_tokens", "free_only") if key in args}
    worker["free_only"] = study is None
    validate_request(worker)
    if study is not None:
        validate_worker(worker, study, messages_for(worker))

    document = load_contract()
    parent = parent_node_id(document)
    observed = observed_spawn_state(document, parent)
    budgets = document.get("constraints", {}).get("budgets", {})
    if (
        isinstance(budgets, Mapping)
        and "tokens" in budgets
        and observed["unknown_token_usage_attempts"]
    ):
        raise ValueError("prior governed token spend is unknown; reconcile usage before another spawn")

    provider, model = _provider_model(policy, providers)
    prompt_tokens = estimate(json.dumps(messages_for(worker), ensure_ascii=False, separators=(",", ":")))
    max_tokens = int(worker.get("max_tokens", 512))
    proposed_tokens = prompt_tokens + max_tokens

    revision = document.get("revision")
    if isinstance(revision, bool) or not isinstance(revision, int) or revision < 0:
        raise ValueError("AODL revision must be an integer >= 0")

    return {
        "harness": args["harness"],
        "trace_id": args["trace_id"],
        "parent_agent": worker["parent_agent"],
        "function": "cheap_bounded_worker",
        "task": worker["task"],
        "context": worker.get("context", ""),
        "provider": provider,
        "model": model,
        "reason": "host-governed AODL remote worker canary",
        "max_tokens": max_tokens,
        "free_only": worker["free_only"],
        "aodl": {
            "document": document,
            "spawn": {
                "request_revision": revision,
                "parent_node_id": parent,
                "live_children": observed["live_children"],
                "parent_depth": observed["parent_depth"],
                "observed": observed["observed"],
                "proposed": {"tokens": proposed_tokens},
                "requested": ["execute"],
            },
        },
    }


def enabled() -> bool:
    return os.environ.get("Z0INT_GOVERNED_REMOTE") == "1"


def prepare_remote_request(args: object) -> dict[str, Any]:
    if not isinstance(args, dict):
        raise ValueError("governed worker request must be an object")
    harness = args.get("harness")
    trace = args.get("trace_id")
    if not isinstance(harness, str) or not harness or not isinstance(trace, str) or not trace:
        raise ValueError("governed worker requires harness and trace_id")
    prepare_trace = _prepare_trace_id(harness, trace)
    caller_sha = _caller_fingerprint(args)

    from .dispatch_authority import locked

    key = hashlib.sha256((harness + "\0" + trace).encode("utf-8")).hexdigest()
    with locked(key):
        prior = _prepared(prepare_trace)
        if prior is not None:
            extra = prior.get("extra")
            if not isinstance(extra, Mapping) or extra.get("caller_request_sha256") != caller_sha:
                raise ValueError("trace_id reused for different governed worker request")
            frozen = extra.get("frozen_governance")
            if not isinstance(frozen, dict):
                raise ValueError("prepared governed request is corrupt")
            return {
                "harness": harness,
                "trace_id": trace,
                "parent_agent": args["parent_agent"],
                "function": "cheap_bounded_worker",
                "task": args["task"],
                "context": args.get("context", ""),
                "provider": frozen["provider"],
                "model": frozen["model"],
                "reason": frozen["reason"],
                "max_tokens": int(args.get("max_tokens", 512)),
                "free_only": frozen.get("free_only", True),
                "aodl": json.loads(json.dumps(frozen["aodl"])),
            }

        remote = build_remote_request(args)
        frozen = {
            "provider": remote["provider"],
            "model": remote["model"],
            "reason": remote["reason"],
            "free_only": remote["free_only"],
            "aodl": remote["aodl"],
        }
        append_receipt({
            "trace_id": prepare_trace,
            "session_id": args.get("parent_agent"),
            "capability_id": PREPARE_CAPABILITY,
            "prediction": "PREPARED",
            "action_taken": "freeze_governed_request",
            "route": "governed_remote",
            "execution": "log_only",
            "extra": {
                "status": "prepared",
                "harness": harness,
                "caller_trace_id": trace,
                "caller_request_sha256": caller_sha,
                "frozen_governance": frozen,
                "aodl_intent_source_hash": remote["aodl"]["document"]["provenance"]["sourceHash"],
            },
        })
        return json.loads(json.dumps(remote))


def execute(args: object) -> dict[str, Any]:
    if not enabled():
        raise ValueError("governed remote execution is disabled")
    request = prepare_remote_request(args)
    base = os.environ.get("Z0INT_REMOTE_EXECUTOR_URL", DEFAULT_EXECUTOR_URL).rstrip("/")
    data = json.dumps(request, allow_nan=False).encode("utf-8")
    req = urllib.request.Request(
        base + "/v1/execute",
        data=data,
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=120) as response:
        result = json.load(response)
    if not isinstance(result, dict):
        raise ValueError("remote executor returned a non-object")
    result.setdefault("trace_id", request["trace_id"])
    result.setdefault("governed_remote", True)
    return result
