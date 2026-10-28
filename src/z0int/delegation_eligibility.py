"""Pure, conservative eligibility hints for the route_worker MCP interface.

This module estimates whether a self-contained text subtask is worth delegating.
It does not select a provider, authorize execution, or replace the host router.
"""

import json
import math
import re

from .intelligence import REGISTRY


_ALLOWED_INPUTS = frozenset({
    "task", "context", "needs_tools", "needs_session_files", "needs_judgement",
    "contains_private", "parent_agent", "trace_id",
})
_BOOLEAN_INPUTS = (
    "needs_tools", "needs_session_files", "needs_judgement", "contains_private",
)

_SUMMARIZATION = re.compile(
    r"\b(?:summari[sz](?:e|es|ed|ing|ation)|summary|summaries)\b"
    r"|\btl\s*;\s*dr\b",
    re.IGNORECASE,
)
_BOUNDED = re.compile(
    r"\b(?:extract(?:s|ed|ing|ion)?|classif(?:y|ies|ied|ying|ication)"
    r"|label(?:s|ed|ing)?|rewrit(?:e|es|ing|ten)"
    r"|convert(?:s|ed|ing)?|format(?:s|ted|ting)?)\b",
    re.IGNORECASE,
)
_REVIEW = re.compile(r"\breview(?:ing)?\b.{0,96}\b(?:diff|patch)(?:es)?\b", re.IGNORECASE | re.DOTALL)
_ANSWER = re.compile(r"\b(?:answer|respond(?:ing)?\s+to)\b", re.IGNORECASE)
_QUESTION = re.compile(r"\b(?:question|query)\b", re.IGNORECASE)
_SUPPLIED_TEXT = re.compile(
    r"\b(?:supplied|provided|following)\b.{0,32}\b(?:text|passage|excerpt|content)\b",
    re.IGNORECASE | re.DOTALL,
)


def _infer_function(task: str) -> str | None:
    """Infer only bounded text functions, in the specified precedence order."""
    if _SUMMARIZATION.search(task):
        return "summarization"
    if _BOUNDED.search(task):
        return "cheap_bounded_worker"
    if _REVIEW.search(task):
        return "code_review"
    if _ANSWER.search(task) and _QUESTION.search(task) and _SUPPLIED_TEXT.search(task):
        return "research"
    return None


def _nonnegative_number(value: object, name: str) -> int | float:
    if type(value) not in (int, float) or not math.isfinite(value) or value < 0:
        raise ValueError(f"{name} must be a finite nonnegative number")
    return value


def _policy_values(policy: dict | None) -> tuple[int, int | float, int | float]:
    if policy is None:
        policy = json.loads(REGISTRY.read_text(encoding="utf-8"))["delegation_policy"]
    if not isinstance(policy, dict):
        raise ValueError("policy must be a dictionary")

    tiny = policy.get("tiny_parent_tokens_max", 256)
    if type(tiny) is not int or tiny < 0:
        raise ValueError("tiny_parent_tokens_max must be a nonnegative integer")
    default_overhead = _nonnegative_number(
        policy.get("default_overhead_ms", 1000), "default_overhead_ms"
    )
    observed_overhead = _nonnegative_number(
        policy.get("observed_workflow_overhead_ms", 0), "observed_workflow_overhead_ms"
    )
    parent_ms_per_token = _nonnegative_number(
        policy.get("parent_ms_per_token", 20), "parent_ms_per_token"
    )
    return tiny, max(default_overhead, observed_overhead), parent_ms_per_token


def classify(
    subtask: dict, policy: dict | None = None, *, allow_remote: bool = False
) -> dict:
    """Classify a candidate without executing it or modifying its inputs.

    Rules a-e are evaluated in order, recording every applicable rejecting
    reason. The first rejecting guard wins; a worker is eligible only when
    no guard matches. Private content never receives remote authorization.

    A caller must supply nonempty parent_agent and trace_id before a task
    eligible for route_worker can yield a dispatchable argument object.
    """
    if not isinstance(subtask, dict):
        raise ValueError("subtask must be a dictionary")
    unknown = set(subtask) - _ALLOWED_INPUTS
    if unknown:
        raise ValueError(f"Unknown subtask keys: {sorted(map(str, unknown))}")
    if type(allow_remote) is not bool:
        raise ValueError("allow_remote must be boolean")

    task = subtask.get("task")
    if not isinstance(task, str) or not task.strip():
        raise ValueError("task must be nonempty text")
    context = subtask.get("context", "")
    if not isinstance(context, str):
        raise ValueError("context must be text")
    for key in _BOOLEAN_INPUTS:
        if key in subtask and type(subtask[key]) is not bool:
            raise ValueError(f"{key} must be boolean")
    for key in ("parent_agent", "trace_id"):
        if key in subtask:
            value = subtask[key]
            if not isinstance(value, str) or not value.strip() or len(value) > 200:
                raise ValueError(f"{key} must be nonempty text of at most 200 characters")

    tiny_max, overhead_ms, ms_per_token = _policy_values(policy)
    estimated_tokens = math.ceil((len(task) + len(context)) / 4)
    estimated_ms = estimated_tokens * ms_per_token

    reasons = []
    # a. A worker cannot access the parent's tools, session files or judgement.
    for key, reason in (
        ("needs_tools", "NEEDS_TOOLS"),
        ("needs_session_files", "NEEDS_FILES"),
        ("needs_judgement", "NEEDS_JUDGEMENT"),
    ):
        if subtask.get(key, False):
            reasons.append(reason)
    # b-c. Avoid tiny jobs and work cheaper than the observed offload overhead.
    if estimated_tokens <= tiny_max:
        reasons.append("TINY")
    if estimated_ms <= overhead_ms:
        reasons.append("BELOW_OVERHEAD")
    # d. Keep bounds aligned with worker_routing.validate_request.
    if len(task) > 24000 or len(context) > 24000:
        reasons.append("TOO_LARGE")
    # e. No automatic inference of coding_implementation.
    function = _infer_function(task)
    if function is None:
        reasons.append("NO_FUNCTION")

    if reasons:
        return {
            "decision": "PARENT_ONLY",
            "reasons": reasons,
            "function": function,
            "route_worker_args": None,
        }

    # Only eligible work needs dispatch identity. Do not invent an agent or
    # trace ID: the caller must bind its real session and stable request ID.
    if "parent_agent" not in subtask or "trace_id" not in subtask:
        raise ValueError("Eligible subtasks require parent_agent and trace_id")

    args = {
        "task": task,
        "parent_agent": subtask["parent_agent"],
        "trace_id": subtask["trace_id"],
        "function": function,
        "expected_parent_tokens": estimated_tokens,
        "expected_parent_ms": estimated_ms,
        "allow_remote": bool(allow_remote and not subtask.get("contains_private", False)),
    }
    if context:
        args["context"] = context
    return {
        "decision": "ROUTE_WORKER",
        "reasons": [],
        "function": function,
        "route_worker_args": args,
    }
