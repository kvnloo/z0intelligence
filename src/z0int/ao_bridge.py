"""Agent Orchestrator integration: shadow decisions and outcome joins.

AO owns execution/session truth. This module only records a bounded decision
opportunity, returns non-authoritative advice, and joins later verified world
outcomes onto the same canonical z0intelligence receipt.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any

from .dispatch_authority import locked
from .receipt import DecisionReceipt, Outcome, append_receipt, find_receipt, join_outcome

SPAWN_SCHEMA = "ao.z0int.spawn.v1"
OUTCOME_SCHEMA = "ao.z0int.outcome.v1"
CAPABILITY = "ao.spawn_decision.v1"
POLICY_REVISION = "ao-shadow-baseline-v1"

_SPAWN_FIELDS = {
    "schema", "trace_id", "session_id", "project_id", "kind", "task",
    "current", "constraints",
}
_CURRENT_FIELDS = {"harness", "model", "mode", "permission"}
_CONSTRAINT_FIELDS = {"explicit_harness", "explicit_model", "explicit_mode"}
_OUTCOME_FIELDS = {"schema", "trace_id", "session_id", "outcome_id", "outcome", "evidence"}
_EVIDENCE_FIELDS = {
    "project_id", "kind", "harness", "mode", "model", "activity",
    "terminated", "scm_complete", "prs",
}
_EVIDENCE_PR_FIELDS = {
    "url", "number", "draft", "merged", "closed", "ci", "review",
    "mergeability", "review_comments", "external_approved",
    "external_changes_requested", "external_comments", "head_sha",
}


def _digest(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, allow_nan=False, separators=(",", ":")).encode()
    ).hexdigest()


def _text(value: Any, name: str, limit: int, *, allow_empty: bool = False) -> str:
    if not isinstance(value, str) or len(value) > limit or (not allow_empty and not value.strip()):
        raise ValueError("Invalid " + name)
    return value


def validate_spawn(args: dict[str, Any]) -> None:
    if not isinstance(args, dict) or set(args) - _SPAWN_FIELDS:
        raise ValueError("Invalid AO spawn decision fields")
    if args.get("schema") != SPAWN_SCHEMA:
        raise ValueError("Unsupported AO spawn schema")
    _text(args.get("trace_id"), "trace_id", 200)
    _text(args.get("session_id"), "session_id", 200)
    _text(args.get("project_id"), "project_id", 500)
    if args.get("kind") not in ("worker", "orchestrator"):
        raise ValueError("Invalid kind")
    _text(args.get("task"), "task", 24000, allow_empty=True)

    current = args.get("current")
    if not isinstance(current, dict) or set(current) - _CURRENT_FIELDS:
        raise ValueError("Invalid current selection")
    for key in _CURRENT_FIELDS:
        if key in current:
            _text(current[key], "current." + key, 500, allow_empty=True)

    constraints = args.get("constraints")
    if not isinstance(constraints, dict) or set(constraints) - _CONSTRAINT_FIELDS:
        raise ValueError("Invalid constraints")
    if any(type(value) is not bool for value in constraints.values()):
        raise ValueError("Constraints must be boolean")


def _response(row: dict[str, Any], *, replayed: bool) -> dict[str, Any]:
    extra = row.get("extra") or {}
    recommendation = extra.get("recommendation")
    if not isinstance(recommendation, dict):
        recommendation = {}
    verification = extra.get("requested_verification")
    if not isinstance(verification, list):
        verification = []
    return {
        "schema": SPAWN_SCHEMA,
        "decision_id": row["trace_id"],
        "action": row.get("prediction") or "abstain",
        "recommendation": recommendation,
        "confidence": row.get("confidence"),
        "reason": extra.get("reason", "shadow_baseline_no_learned_policy"),
        "policy_revision": extra.get("policy_revision", POLICY_REVISION),
        "requested_verification": verification,
        "receipt_id": row["trace_id"],
        "replayed": replayed,
    }


def spawn_decision(args: dict[str, Any], *, root=None) -> dict[str, Any]:
    """Record one AO spawn opportunity.

    P0 is deliberately an abstaining baseline. It proves the durable bridge and
    outcome join before a learned policy is allowed to affect AO behavior.
    """
    validate_spawn(args)
    trace_id = args["trace_id"]
    request_sha = _digest(args)
    lock_key = "ao-spawn-" + hashlib.sha256(trace_id.encode()).hexdigest()

    with locked(lock_key):
        previous = find_receipt(trace_id, root=root)
        if previous is not None:
            extra = previous.get("extra") or {}
            if previous.get("capability_id") != CAPABILITY:
                raise ValueError("trace_id already belongs to another capability")
            if extra.get("ao_request_sha256") != request_sha:
                raise ValueError("trace_id reused for different AO spawn request")
            return _response(previous, replayed=True)

        current = dict(args["current"])
        row = DecisionReceipt(
            trace_id=trace_id,
            session_id=args["session_id"],
            capability_id=CAPABILITY,
            provider="deterministic_baseline",
            prediction="abstain",
            action_taken="shadow_only",
            route="shadow",
            execution="shadow",
            measurement_state="unknown",
            state_reason="No AO routing policy has passed a production promotion gate",
            extra={
                "source": "agent-orchestrator",
                "schema": SPAWN_SCHEMA,
                "project_id": args["project_id"],
                "kind": args["kind"],
                "current": current,
                "constraints": dict(args["constraints"]),
                "task_sha256": hashlib.sha256(args["task"].encode()).hexdigest(),
                "ao_request_sha256": request_sha,
                "policy_revision": POLICY_REVISION,
                "reason": "shadow_baseline_no_learned_policy",
                "recommendation": current,
                "requested_verification": [],
            },
        )
        stored = append_receipt(row, root=root)
        return _response(stored, replayed=False)


def _bool(value: Any, name: str) -> None:
    if type(value) is not bool:
        raise ValueError(name + " must be boolean")


def validate_outcome(args: dict[str, Any]) -> Outcome:
    if not isinstance(args, dict) or set(args) - _OUTCOME_FIELDS:
        raise ValueError("Invalid AO outcome fields")
    if args.get("schema") != OUTCOME_SCHEMA:
        raise ValueError("Unsupported AO outcome schema")
    _text(args.get("trace_id"), "trace_id", 200)
    _text(args.get("session_id"), "session_id", 200)
    _text(args.get("outcome_id"), "outcome_id", 240)

    raw = args.get("outcome")
    if not isinstance(raw, dict):
        raise ValueError("outcome must be an object")
    allowed = set(Outcome.__dataclass_fields__)
    if set(raw) - allowed:
        raise ValueError("Unknown outcome fields")

    evidence = args.get("evidence")
    if not isinstance(evidence, dict) or set(evidence) - _EVIDENCE_FIELDS:
        raise ValueError("Invalid AO outcome evidence fields")
    _text(evidence.get("project_id"), "evidence.project_id", 500)
    if evidence.get("kind") not in ("worker", "orchestrator"):
        raise ValueError("Invalid evidence.kind")
    _text(evidence.get("harness"), "evidence.harness", 500, allow_empty=True)
    _text(evidence.get("mode"), "evidence.mode", 100, allow_empty=True)
    _text(evidence.get("model", ""), "evidence.model", 500, allow_empty=True)
    _text(evidence.get("activity"), "evidence.activity", 100, allow_empty=True)
    _bool(evidence.get("terminated"), "evidence.terminated")
    _bool(evidence.get("scm_complete"), "evidence.scm_complete")

    prs = evidence.get("prs")
    if not isinstance(prs, list) or len(prs) > 64:
        raise ValueError("evidence.prs must be a bounded list")
    for index, pr in enumerate(prs):
        prefix = f"evidence.prs[{index}]"
        if not isinstance(pr, dict) or set(pr) - _EVIDENCE_PR_FIELDS:
            raise ValueError("Invalid " + prefix + " fields")
        _text(pr.get("url"), prefix + ".url", 4000)
        number = pr.get("number")
        if type(number) is not int or number < 0:
            raise ValueError(prefix + ".number must be a non-negative integer")
        for key in (
            "draft", "merged", "closed", "review_comments", "external_approved",
            "external_changes_requested", "external_comments",
        ):
            _bool(pr.get(key), prefix + "." + key)
        _text(pr.get("ci"), prefix + ".ci", 100, allow_empty=True)
        _text(pr.get("review"), prefix + ".review", 100, allow_empty=True)
        _text(pr.get("mergeability"), prefix + ".mergeability", 100, allow_empty=True)
        _text(pr.get("head_sha", ""), prefix + ".head_sha", 200, allow_empty=True)

    return Outcome(**raw)


def join_ao_outcome(args: dict[str, Any], *, root=None) -> dict[str, Any]:
    """Join one idempotent AO lifecycle/SCM outcome onto a spawn decision."""
    outcome = validate_outcome(args)
    trace_id = args["trace_id"]
    event_sha = _digest(args)
    lock_key = "ao-outcome-" + hashlib.sha256(trace_id.encode()).hexdigest()

    with locked(lock_key):
        previous = find_receipt(trace_id, root=root)
        if previous is None or previous.get("capability_id") != CAPABILITY:
            raise ValueError("Unknown AO decision")
        if previous.get("session_id") != args["session_id"]:
            raise ValueError("AO outcome session does not match decision")
        extra = previous.get("extra") or {}
        prior_sha = extra.get("ao_outcome_sha256")
        if prior_sha is not None:
            if prior_sha != event_sha:
                raise ValueError("AO outcome already joined with different payload")
            return {
                "schema": OUTCOME_SCHEMA,
                "trace_id": trace_id,
                "outcome_id": args["outcome_id"],
                "outcome_tier": previous.get("outcome_tier"),
                "replayed": True,
            }

        raw = outcome.to_dict()
        raw["source"] = "agent-orchestrator"
        joined = join_outcome(trace_id, raw, root=root)
        updated = find_receipt(trace_id, root=root)
        if updated is None:
            raise RuntimeError("AO outcome join lost its decision receipt")
        marked = dict(updated)
        marked_extra = dict(marked.get("extra") or {})
        marked_extra["ao_outcome_sha256"] = event_sha
        marked_extra["ao_outcome_id"] = args["outcome_id"]
        marked_extra["ao_outcome_evidence"] = args["evidence"]
        marked["extra"] = marked_extra
        append_receipt(marked, root=root)
        return {
            "schema": OUTCOME_SCHEMA,
            "trace_id": trace_id,
            "outcome_id": args["outcome_id"],
            "outcome_tier": joined["outcome_tier"] if joined else None,
            "replayed": False,
        }
