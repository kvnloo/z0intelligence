"""AgentWeb-only shadow decision experiment lanes.

These are deliberately not production capability routes. They collect
receipt-backed evidence before a bounded decision can become eligible.
"""
from __future__ import annotations

import hashlib
import json
import os
import time
from typing import Any, Callable

from .functions.contract import EXPECTED_JEV_MODEL
from .functions.jev import decide_choice, decide_noul
from .provider_saturation import LocalAdmission


COMMON_ALLOWED = {
    "harness", "trace_id", "parent_agent", "function", "decision_id",
    "state", "allow_remote", "experimental",
}


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


def _validate_common(args: dict[str, Any], *, function: str, allowed: set[str]) -> None:
    if not isinstance(args, dict) or set(args) - allowed:
        raise ValueError("Unknown experimental decision fields")
    for key in ("harness", "trace_id", "parent_agent", "function", "decision_id"):
        value = args.get(key)
        if not isinstance(value, str) or not value.strip() or len(value) > 200:
            raise ValueError("Invalid " + key)
    if args["harness"] != "agentweb":
        raise ValueError("Experimental decision lane is AgentWeb-only")
    if args["function"] != function:
        raise ValueError("Invalid experimental function")
    if type(args.get("allow_remote")) is not bool or type(args.get("experimental")) is not bool:
        raise ValueError("Explicit boolean authorization flags are required")

    state = args.get("state")
    if not isinstance(state, (str, dict, list)) or not state:
        raise ValueError("state must be nonempty string/object/array")
    encoded = json.dumps(state, sort_keys=True, ensure_ascii=False, allow_nan=False)
    if len(encoded.encode("utf-8")) > 24000:
        raise ValueError("state exceeds 24KB experiment bound")


def validate_choice_experiment(args: dict[str, Any]) -> None:
    _validate_common(
        args,
        function="experimental_choice",
        allowed=COMMON_ALLOWED | {"instructions", "options"},
    )
    instructions = args.get("instructions")
    if not isinstance(instructions, str) or not instructions.strip() or len(instructions) > 4000:
        raise ValueError("Invalid instructions")
    options = args.get("options")
    if not isinstance(options, dict) or not 2 <= len(options) <= 16:
        raise ValueError("options must contain 2..16 labels")
    for label, description in options.items():
        if not isinstance(label, str) or not label.strip() or len(label) > 80:
            raise ValueError("Invalid option label")
        if not isinstance(description, str) or not description.strip() or len(description) > 1000:
            raise ValueError("Invalid option description")


def validate_noul_experiment(args: dict[str, Any]) -> None:
    _validate_common(
        args,
        function="experimental_noul",
        allowed=COMMON_ALLOWED | {"proposition"},
    )
    proposition = args.get("proposition")
    if not isinstance(proposition, str) or not proposition.strip() or len(proposition) > 4000:
        raise ValueError("Invalid proposition")


def _authorized(args: dict[str, Any]) -> dict[str, Any] | None:
    if args.get("experimental") is not True:
        return _skip(args, "experimental_opt_in_required")
    if args.get("allow_remote") is not True:
        return _skip(args, "remote_context_not_authorized")
    if os.environ.get("Z0INT_EXPERIMENTAL_JEV_SHADOW") != "1":
        return _skip(args, "experimental_paid_jev_shadow_disabled")
    return None


def _run_experiment(
    args: dict[str, Any],
    *,
    decision_kind: str,
    invoke: Callable[[], dict[str, Any]],
    receipt_metadata: dict[str, Any],
    admission_factory: Callable[[], Any],
) -> dict[str, Any]:
    from .dispatch_authority import run

    def work(receipt_sink):
        call_id = hashlib.sha256(
            (
                args["harness"]
                + "\0"
                + args["trace_id"]
                + "\0experimental-"
                + decision_kind
            ).encode()
        ).hexdigest()
        state_blob = json.dumps(
            args["state"],
            sort_keys=True,
            ensure_ascii=False,
            allow_nan=False,
        )
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
                "decision_kind": decision_kind,
                "status": "started",
                "physical_call_attempted": False,
                "applied": False,
                "quality_authoritative": False,
                "cost_policy": "experimental_paid_jev_shadow",
                "paid_execution_authorized": True,
                "free_tier_validated": False,
                "state_sha256": hashlib.sha256(state_blob.encode()).hexdigest(),
                **receipt_metadata,
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
            result = invoke()
            status = 200
            latency_ms = float(
                result.get("latency_ms") or ((time.monotonic() - started) * 1000)
            )
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
                    "reason": (
                        "explicit downstream shadow experiment; "
                        "result is observational only"
                    ),
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
                    "reason": (
                        "experimental decision failed; "
                        "incumbent remains authoritative"
                    ),
                    "executed": False,
                },
            }
        finally:
            admission.release(
                permit,
                status,
                (time.monotonic() - started) * 1000,
            )

    return run(args, work)


def run_choice_experiment(
    args: dict[str, Any],
    *,
    decision_fn: Callable[..., dict[str, Any]] = decide_choice,
    admission_factory: Callable[[], Any] = LocalAdmission,
) -> dict[str, Any]:
    validate_choice_experiment(args)
    if skipped := _authorized(args):
        return skipped
    return _run_experiment(
        args,
        decision_kind="choice",
        invoke=lambda: decision_fn(
            args["state"],
            instructions=args["instructions"],
            options=args["options"],
        ),
        receipt_metadata={"option_labels": sorted(args["options"])},
        admission_factory=admission_factory,
    )


def run_noul_experiment(
    args: dict[str, Any],
    *,
    decision_fn: Callable[..., dict[str, Any]] = decide_noul,
    admission_factory: Callable[[], Any] = LocalAdmission,
) -> dict[str, Any]:
    validate_noul_experiment(args)
    if skipped := _authorized(args):
        return skipped
    return _run_experiment(
        args,
        decision_kind="noul",
        invoke=lambda: decision_fn(
            args["state"],
            proposition=args["proposition"],
        ),
        receipt_metadata={
            "proposition_sha256": hashlib.sha256(
                args["proposition"].encode()
            ).hexdigest(),
        },
        admission_factory=admission_factory,
    )
