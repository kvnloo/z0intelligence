"""AgentWeb-only shadow decision experiment lane.

This is deliberately not a production capability route. It exists to gather
receipt-backed evidence for bounded decisions before they can become eligible.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import time
from typing import Any, Callable

from .functions.contract import EXPECTED_JEV_MODEL
from .functions.jev import decide_choice
from .provider_saturation import LocalAdmission


def _skip(args: dict[str, Any], reason: str) -> dict[str, Any]:
    return {
        "ok": False,
        "executed": False,
        "applied": False,
        "mode": "shadow",
        "reason": reason,
        "trace_id": args.get("trace_id"),
        "requires_parent": True,
    }


def validate_choice_experiment(args: dict[str, Any]) -> None:
    allowed = {
        "harness", "trace_id", "parent_agent", "function", "decision_id",
        "state", "instructions", "options", "allow_remote", "experimental",
    }
    if not isinstance(args, dict) or set(args) - allowed:
        raise ValueError("Unknown experimental choice fields")
    for key in ("harness", "trace_id", "parent_agent", "function", "decision_id", "instructions"):
        value = args.get(key)
        if not isinstance(value, str) or not value.strip() or len(value) > (4000 if key == "instructions" else 200):
            raise ValueError("Invalid " + key)
    if args["harness"] != "agentweb":
        raise ValueError("Experimental choice lane is AgentWeb-only")
    if args["function"] != "experimental_choice":
        raise ValueError("Invalid experimental function")
    if type(args.get("allow_remote")) is not bool or type(args.get("experimental")) is not bool:
        raise ValueError("Explicit boolean authorization flags are required")

    options = args.get("options")
    if not isinstance(options, dict) or not 2 <= len(options) <= 16:
        raise ValueError("options must contain 2..16 labels")
    for label, description in options.items():
        if not isinstance(label, str) or not label.strip() or len(label) > 80:
            raise ValueError("Invalid option label")
        if not isinstance(description, str) or not description.strip() or len(description) > 1000:
            raise ValueError("Invalid option description")

    state = args.get("state")
    if not isinstance(state, (str, dict, list)) or not state:
        raise ValueError("state must be nonempty string/object/array")
    encoded = json.dumps(state, sort_keys=True, ensure_ascii=False, allow_nan=False)
    if len(encoded.encode("utf-8")) > 24000:
        raise ValueError("state exceeds 24KB experiment bound")


def run_choice_experiment(
    args: dict[str, Any],
    *,
    decision_fn: Callable[..., dict[str, Any]] = decide_choice,
    admission_factory: Callable[[], Any] = LocalAdmission,
) -> dict[str, Any]:
    validate_choice_experiment(args)
    if args.get("experimental") is not True:
        return _skip(args, "experimental_opt_in_required")
    if args.get("allow_remote") is not True:
        return _skip(args, "remote_context_not_authorized")
    if os.environ.get("Z0INT_EXPERIMENTAL_JEV_SHADOW") != "1":
        return _skip(args, "experimental_paid_jev_shadow_disabled")

    from .dispatch_authority import run

    def work(receipt_sink):
        call_id = hashlib.sha256(
            (args["harness"] + "\0" + args["trace_id"] + "\0experimental-choice").encode()
        ).hexdigest()
        state_blob = json.dumps(args["state"], sort_keys=True, ensure_ascii=False, allow_nan=False)
        row: dict[str, Any] = {
            "trace_id": call_id,
            "session_id": args["parent_agent"],
            "capability_id": "experiment." + args["decision_id"],
            "provider": "typesafe",
            "model": EXPECTED_JEV_MODEL,
            "route": "JEV_EXPERIMENTAL_SHADOW",
            "execution": "shadow",
            "extra": {
                "harness": args["harness"],
                "parent_agent": args["parent_agent"],
                "caller_trace_id": args["trace_id"],
                "decision_id": args["decision_id"],
                "status": "started",
                "physical_call_attempted": False,
                "applied": False,
                "quality_authoritative": False,
                "cost_policy": "experimental_paid_jev_shadow",
                "paid_execution_authorized": True,
                "free_tier_validated": False,
                "state_sha256": hashlib.sha256(state_blob.encode()).hexdigest(),
                "option_labels": sorted(args["options"]),
            },
        }
        admission = admission_factory()
        permit = admission.acquire(
            "jev",
            call_id,
            {"harness": args["harness"], "caller_trace_id": args["trace_id"]},
        )
        row["extra"].update(
            admission_token=permit["token"],
            inflight_at_admission=permit["inflight_at_admission"],
            cap=permit["cap"],
            capped=permit["capped"],
        )
        if not permit["admitted"]:
            row["extra"].update(
                status="capped" if permit["capped"] else "rejected",
                admission_reason=permit["reason"],
            )
            receipt_sink(row)
            return {
                "ok": False,
                "executed": False,
                "applied": False,
                "mode": "shadow",
                "requires_parent": True,
                "reason": permit["reason"],
                "route": {
                    "kind": "JEV_EXPERIMENTAL_SHADOW",
                    "reason": "provider admission refused",
                    "executed": False,
                },
            }

        try:
            receipt_sink(row)
        except BaseException:
            admission.release(permit, 0, 0, attempted=False)
            raise

        started = time.monotonic()
        status = 0
        try:
            row["extra"]["physical_call_attempted"] = True
            result = decision_fn(
                args["state"],
                instructions=args["instructions"],
                options=args["options"],
            )
            status = 200
            latency_ms = float(result.get("latency_ms") or ((time.monotonic() - started) * 1000))
            row["latency_ms"] = latency_ms
            usage = result.get("usage") or {}
            if isinstance(usage.get("input_tokens"), int):
                row["input_tokens"] = usage["input_tokens"]
            if isinstance(usage.get("output_tokens"), int):
                row["output_tokens"] = usage["output_tokens"]
            row["extra"].update(
                status="completed",
                physical_call_attempted=True,
                response_model=result.get("model"),
                output_sha256=hashlib.sha256(
                    json.dumps(result, sort_keys=True, default=str).encode()
                ).hexdigest(),
            )
            receipt_sink(row)
            return {
                "ok": True,
                "executed": True,
                "applied": False,
                "mode": "shadow",
                "output": result,
                "requires_parent": True,
                "route": {
                    "kind": "JEV_EXPERIMENTAL_SHADOW",
                    "reason": "explicit downstream shadow experiment; result is observational only",
                    "executed": True,
                },
            }
        except Exception as exc:
            row["latency_ms"] = (time.monotonic() - started) * 1000
            row["extra"].update(
                status="failed",
                error_type=type(exc).__name__,
                physical_call_attempted=True,
            )
            receipt_sink(row)
            return {
                "ok": False,
                "executed": False,
                "applied": False,
                "mode": "shadow",
                "requires_parent": True,
                "reason": type(exc).__name__,
                "route": {
                    "kind": "JEV_EXPERIMENTAL_SHADOW",
                    "reason": "experimental decision failed; incumbent remains authoritative",
                    "executed": False,
                },
            }
        finally:
            admission.release(permit, status, (time.monotonic() - started) * 1000)

    return run(args, work)
