"""Observe-only shadow lane for the OMP bridge (oh-my-pi#83).

The bridge op ``cognition_shadow`` is a *safety boundary*, not a controller:

* it compiles the deterministic legal action set **first** and hands every
  shadow backend that set and nothing else;
* it never returns an executable decision, never calls a tool and never writes
  outside ``~/.z0int/``;
* every shadow answer is recorded next to the compiler's verdict, so a
  disagreement between "what the compiler allows" and "what a local model
  wanted" is visible without ever being acted on;
* it fails open. A bad payload, a missing model or a dead server produces a
  result dict — never an exception into the host.

The pure part is :func:`build_shadow_plan`; :func:`run_shadow` is the small
effectful shell the bridge worker calls. Both are unit-testable with fake
backends and no model server.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
import os
import threading
import time
from pathlib import Path
from typing import Any, Mapping, Sequence

from .actions import ActionCandidate, ActionGraph, Rule
from .cascade import CascadeContext, CognitionCascade
from .adapters.local_slm import ToolDecision, ToolDecisionRequest
from .registry import LocalModelRegistry
from ..receipt import new_trace_id

SCHEMA = "z0int.cognition.shadow.v1"
RECEIPT_SCHEMA = "z0int.cognition.shadow.receipt.v1"
RECEIPT_NAME = "cognition-shadow.jsonl"

_ALLOWED_KINDS = ("tool", "model", "control")
_ALLOWED_RISKS = ("read", "write", "destructive", "publish", "credential", "payment")
DEFAULT_SERVER_TIMEOUT_MS = 30_000


class ShadowPayloadError(ValueError):
    """A payload the observe-only lane refuses to compile. Carries a reason."""


@dataclass(frozen=True)
class ShadowSpec:
    """One backend to run in shadow over the compiled legal set."""

    label: str
    model_id: str | None = None


# --- payload parsing (pure) --------------------------------------------


def _str_list(raw: Mapping[str, Any], field: str) -> tuple[str, ...]:
    value = raw.get(field)
    if value is None:
        return ()
    if isinstance(value, str) or not isinstance(value, (list, tuple)):
        raise ShadowPayloadError(f"{field} must be a list of strings")
    out: list[str] = []
    for index, item in enumerate(value):
        if not isinstance(item, str) or not item.strip():
            raise ShadowPayloadError(f"{field}[{index}] must be a nonempty string")
        out.append(item)
    return tuple(out)


def _mapping(raw: Mapping[str, Any], field: str) -> Mapping[str, Any]:
    value = raw.get(field)
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise ShadowPayloadError(f"{field} must be an object")
    return value


def _nonnegative_int(raw: Mapping[str, Any], field: str, default: int) -> int:
    value = raw.get(field)
    if value is None:
        return default
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ShadowPayloadError(f"{field} must be a number")
    if float(value) < 0:
        raise ShadowPayloadError(f"{field} must be >= 0")
    return int(value)


def _action_from_row(raw: Any, index: int) -> ActionCandidate:
    where = f"actions[{index}]"
    if not isinstance(raw, Mapping):
        raise ShadowPayloadError(f"{where} must be an object")

    action_id = raw.get("action_id")
    if not isinstance(action_id, str) or not action_id.strip():
        raise ShadowPayloadError(f"{where}.action_id must be a nonempty string")

    kind = raw.get("kind", "tool")
    if not isinstance(kind, str) or kind not in _ALLOWED_KINDS:
        raise ShadowPayloadError(f"{where}.kind must be one of {list(_ALLOWED_KINDS)}")

    tool = raw.get("tool")
    if tool is not None and not isinstance(tool, str):
        raise ShadowPayloadError(f"{where}.tool must be a string")

    risk_class = raw.get("risk_class", "read")
    if not isinstance(risk_class, str) or risk_class not in _ALLOWED_RISKS:
        raise ShadowPayloadError(
            f"{where}.risk_class must be one of {list(_ALLOWED_RISKS)}"
        )

    description = raw.get("description", action_id)
    if not isinstance(description, str):
        raise ShadowPayloadError(f"{where}.description must be a string")

    family = raw.get("family")
    if family is not None and not isinstance(family, str):
        raise ShadowPayloadError(f"{where}.family must be a string")

    schema = raw.get("arguments_schema")
    if schema is not None and not isinstance(schema, Mapping):
        raise ShadowPayloadError(f"{where}.arguments_schema must be an object")

    try:
        return ActionCandidate(
            action_id=action_id,
            kind=kind,  # type: ignore[arg-type]
            description=description,
            tool=(tool or action_id) if kind == "tool" else tool,
            family=family,
            requires=_str_list(raw, "requires"),
            provides=_str_list(raw, "provides"),
            required_capabilities=_str_list(raw, "required_capabilities"),
            risk_class=risk_class,  # type: ignore[arg-type]
            cost_units=_nonnegative_int(raw, "cost_units", 1),
            parallel_safe=bool(raw.get("parallel_safe", True)),
            arguments_schema=schema,
            escalates_to=raw.get("escalates_to")
            if isinstance(raw.get("escalates_to"), str)
            else None,
        )
    except ShadowPayloadError:
        raise
    except ValueError as exc:
        raise ShadowPayloadError(f"{where}: {exc}") from exc


def _rules_from_payload(raw: Any) -> tuple[Rule, ...]:
    if raw is None:
        return ()
    if not isinstance(raw, (list, tuple)):
        raise ShadowPayloadError("rules must be a list")
    rules: list[Rule] = []
    for index, item in enumerate(raw):
        where = f"rules[{index}]"
        if not isinstance(item, Mapping):
            raise ShadowPayloadError(f"{where} must be an object")
        rule_id = item.get("id")
        choose = item.get("choose")
        if not isinstance(rule_id, str) or not rule_id.strip():
            raise ShadowPayloadError(f"{where}.id must be a nonempty string")
        if not isinstance(choose, str) or not choose.strip():
            raise ShadowPayloadError(f"{where}.choose must be a nonempty string")
        when = item.get("when") or {}
        if not isinstance(when, Mapping):
            raise ShadowPayloadError(f"{where}.when must be an object")
        rationale = item.get("rationale") or ""
        if not isinstance(rationale, str):
            raise ShadowPayloadError(f"{where}.rationale must be a string")
        rules.append(Rule(id=rule_id, when=dict(when), choose=choose, rationale=rationale))
    return tuple(rules)


def _shadow_specs(raw: Any) -> tuple[ShadowSpec, ...]:
    if raw is None:
        return ()
    if isinstance(raw, str) or not isinstance(raw, (list, tuple)):
        raise ShadowPayloadError("shadows must be a list")
    specs: list[ShadowSpec] = []
    seen: set[str] = set()
    for index, item in enumerate(raw):
        where = f"shadows[{index}]"
        if isinstance(item, str):
            if not item.strip():
                raise ShadowPayloadError(f"{where} must be a nonempty string")
            spec = ShadowSpec(label=item, model_id=item)
        elif isinstance(item, Mapping):
            model_id = item.get("model_id") or item.get("model")
            label = item.get("label") or model_id
            if not isinstance(model_id, str) or not model_id.strip():
                raise ShadowPayloadError(f"{where}.model_id must be a nonempty string")
            if not isinstance(label, str) or not label.strip():
                raise ShadowPayloadError(f"{where}.label must be a nonempty string")
            spec = ShadowSpec(label=label, model_id=model_id)
        else:
            raise ShadowPayloadError(f"{where} must be a string or object")
        if spec.label in seen:
            continue
        seen.add(spec.label)
        specs.append(spec)
    return tuple(specs)


def build_shadow_plan(
    payload: Mapping[str, Any],
) -> tuple[CascadeContext, tuple[ShadowSpec, ...]]:
    """Turn a raw bridge payload into a compile context plus shadow backends.

    Pure and strict: unknown/extra keys are ignored, but a malformed *known*
    field (including a malformed action row) raises :class:`ShadowPayloadError`
    with a precise reason. The caller is expected to turn that into
    ``{"ok": false, "reason": ...}`` rather than letting it escape.
    """
    if not isinstance(payload, Mapping):
        raise ShadowPayloadError("payload must be an object")

    actions_raw = payload.get("actions")
    if actions_raw is None:
        actions_raw = []
    if isinstance(actions_raw, str) or not isinstance(actions_raw, (list, tuple)):
        raise ShadowPayloadError("actions must be a list")

    actions = tuple(_action_from_row(raw, index) for index, raw in enumerate(actions_raw))
    rules = _rules_from_payload(payload.get("rules"))
    try:
        graph = ActionGraph(actions=actions, rules=rules)
    except ValueError as exc:
        raise ShadowPayloadError(f"invalid action graph: {exc}") from exc

    context = CascadeContext(
        state=str(payload.get("state") or ""),
        graph=graph,
        granted_capabilities=_str_list(payload, "granted_capabilities"),
        authority=_str_list(payload, "authority") or ("read",),
        budget_units=_nonnegative_int(payload, "budget_units", 8),
        facts=_mapping(payload, "facts"),
        satisfied=_str_list(payload, "satisfied"),
        objective=payload.get("objective")
        if isinstance(payload.get("objective"), str)
        else None,
        risk_class=payload.get("risk_class")
        if isinstance(payload.get("risk_class"), str)
        else "read",
        constraints=_mapping(payload, "constraints"),
        latency_budget_ms=payload.get("latency_budget_ms")
        if isinstance(payload.get("latency_budget_ms"), (int, float))
        and not isinstance(payload.get("latency_budget_ms"), bool)
        else None,
    )
    return context, _shadow_specs(payload.get("shadows"))


# --- backend resolution + execution (never raises) ----------------------


def _timeout_s(payload: Mapping[str, Any], timeout_s: float | None) -> float:
    if timeout_s is not None:
        return max(0.1, float(timeout_s))
    raw = payload.get("timeout_ms")
    if isinstance(raw, (int, float)) and not isinstance(raw, bool) and raw > 0:
        return max(0.1, float(raw) / 1000.0)
    env = os.environ.get("Z0INT_COGNITION_SHADOW_SERVER_TIMEOUT_MS")
    if env:
        try:
            return max(0.1, float(env) / 1000.0)
        except ValueError:
            pass
    return DEFAULT_SERVER_TIMEOUT_MS / 1000.0


def _resolve_shadows(
    registry: Any, specs: Sequence[ShadowSpec]
) -> list[tuple[ShadowSpec, Any, str | None]]:
    """Resolve each spec to a backend. Failures are recorded, never raised."""
    if registry is None:
        try:
            registry = LocalModelRegistry.from_environment()
        except Exception:  # noqa: BLE001 - no manifest / no serving map is fine
            return []
    if not specs:
        return []

    served_attr = getattr(registry, "served", None)
    if callable(served_attr):
        try:
            served = tuple(served_attr())
        except Exception:  # noqa: BLE001
            served = ()
        if not served:
            # Nothing is served: still return the compiled legal set.
            return []

    resolved: list[tuple[ShadowSpec, Any, str | None]] = []
    for spec in specs:
        backend = None
        error: str | None = None
        model_id = spec.model_id
        if not model_id:
            error = "no_model_requested"
        else:
            try:
                backend = registry.backend_for(model_id)
            except Exception as exc:  # noqa: BLE001 - missing/unreachable model
                backend = None
                error = f"{type(exc).__name__}: {exc}"
        resolved.append((spec, backend, error))
    return resolved


def _decision_rows(
    resolved: Sequence[tuple[ShadowSpec, Any, str | None]],
    legal_ids: frozenset[str],
    request: ToolDecisionRequest,
    *,
    timeout_s: float,
) -> list[dict[str, Any]]:
    results: list[tuple[ToolDecision | None, str | None]] = [
        (None, error if backend is None else "shadow_timeout")
        for _, backend, error in resolved
    ]
    lock = threading.Lock()

    def run(index: int, backend: Any) -> None:
        try:
            decision = backend.decide(request)
        except BaseException as exc:  # noqa: BLE001 - shadow must never raise
            with lock:
                results[index] = (None, f"{type(exc).__name__}: {exc}")
            return
        with lock:
            results[index] = (decision, None)

    deadline = time.monotonic() + max(0.1, float(timeout_s))
    threads: list[tuple[int, threading.Thread]] = []
    for index, (_, backend, _) in enumerate(resolved):
        if backend is None:
            continue
        thread = threading.Thread(
            target=run, args=(index, backend), daemon=True, name=f"z0int-shadow-{index}"
        )
        threads.append((index, thread))
        thread.start()
    for index, thread in threads:
        thread.join(max(0.0, deadline - time.monotonic()))
        if thread.is_alive():
            with lock:
                if results[index][0] is None and results[index][1] is None:
                    results[index] = (None, "shadow_timeout")

    rows: list[dict[str, Any]] = []
    for index, (spec, backend, error) in enumerate(resolved):
        decision, failure = results[index]
        rows.append(_row(spec, backend, decision, failure, error, legal_ids))
    return rows


def _row(
    spec: ShadowSpec,
    backend: Any,
    decision: ToolDecision | None,
    failure: str | None,
    resolve_error: str | None,
    legal_ids: frozenset[str],
) -> dict[str, Any]:
    if decision is None:
        return {
            "label": spec.label,
            "backend": None,
            "model": spec.model_id,
            "selected_action": None,
            "abstained": True,
            "invalid_call": False,
            "latency_ms": 0.0,
            "parse_error": failure or resolve_error or "unavailable",
            "error": failure or resolve_error or "unavailable",
        }

    selected = decision.selected_action
    invalid_call = bool(decision.invalid_call)
    attempted: str | None = None
    if selected is not None and selected not in legal_ids:
        # A backend is supposed to pick from the legal set; if it names anything
        # else the answer is recorded as an illegal call, never as a selection.
        attempted = selected
        selected = None
        invalid_call = True

    row: dict[str, Any] = {
        "label": spec.label,
        "backend": decision.backend,
        "model": decision.model or spec.model_id,
        "selected_action": selected,
        "abstained": bool(decision.abstained or selected is None),
        "invalid_call": invalid_call,
        "latency_ms": decision.latency_ms,
        "candidate_action_count": decision.candidate_action_count,
        "parse_error": decision.parse_error if selected is None else None,
    }
    if decision.confidence is not None:
        row["confidence"] = decision.confidence
    if attempted is not None:
        row["attempted_action"] = attempted
    return row


# --- receipt ------------------------------------------------------------


def shadow_receipt_path() -> Path:
    """The only path this lane ever writes to (under ``Z0INT_HOME``)."""
    from .. import paths

    return paths.home() / "shadow" / RECEIPT_NAME


def append_shadow_receipt(row: Mapping[str, Any]) -> Path:
    path = shadow_receipt_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(dict(row), ensure_ascii=False, default=str) + "\n")
    return path


# --- the bridge op body -------------------------------------------------


def run_shadow(
    payload: Mapping[str, Any],
    *,
    registry: Any = None,
    write: bool = True,
    timeout_s: float | None = None,
) -> dict[str, Any]:
    """Execute the observe-only shadow op. Guaranteed not to raise.

    The returned dict is always JSON-serializable and always has ``selected_action``
    and ``executed_action`` fixed to ``None`` — this lane observes, it never acts.
    """
    if not isinstance(payload, Mapping):
        return {"ok": False, "schema": SCHEMA, "reason": "payload must be an object"}

    try:
        context, specs = build_shadow_plan(payload)
    except ShadowPayloadError as exc:
        return {"ok": False, "schema": SCHEMA, "reason": str(exc)}
    except Exception as exc:  # noqa: BLE001 - fail open on anything else
        return {"ok": False, "schema": SCHEMA, "reason": f"{type(exc).__name__}: {exc}"}

    trace_id = payload.get("trace_id")
    if not isinstance(trace_id, str) or not trace_id.strip():
        trace_id = new_trace_id()
    session_id = payload.get("session_id")
    if not isinstance(session_id, str):
        session_id = None

    try:
        # Compile first, and only then let any backend see the result.
        outcome = CognitionCascade().run(context, trace_id=trace_id)
        legal = outcome.legal
        escalation = outcome.escalation.to_dict()
    except Exception as exc:  # noqa: BLE001
        return {
            "ok": False,
            "schema": SCHEMA,
            "trace_id": trace_id,
            "reason": f"compile_failed: {type(exc).__name__}: {exc}",
        }

    legal_ids = frozenset(legal.ids())
    shadow_rows: list[dict[str, Any]] = []
    if specs and not legal.is_empty:
        request = ToolDecisionRequest(
            state=context.state,
            legal=legal,
            objective=context.objective,
            risk_class=context.risk_class,
            constraints=context.constraints,
            max_tokens=context.max_tokens,
        )
        resolved = _resolve_shadows(registry, specs)
        if resolved:
            shadow_rows = _decision_rows(
                resolved, legal_ids, request, timeout_s=_timeout_s(payload, timeout_s)
            )

    result: dict[str, Any] = {
        "ok": True,
        "schema": SCHEMA,
        "trace_id": trace_id,
        "graph_digest": legal.graph_digest,
        "candidate_action_count": legal.candidate_count,
        "legal_ids": list(legal.ids()),
        "eliminated": [e.to_dict() for e in legal.eliminated],
        "deterministic_solution": legal.deterministic_solution,
        "escalation": escalation,
        "shadow": shadow_rows,
        "receipts_path": None,
        "selected_action": None,
        "executed_action": None,
    }

    if write:
        receipt = {
            "schema": RECEIPT_SCHEMA,
            "ts": time.time(),
            "trace_id": trace_id,
            "session_id": session_id,
            "state": context.state[:400],
            "graph_digest": legal.graph_digest,
            "candidate_action_count": legal.candidate_count,
            "legal_ids": list(legal.ids()),
            "eliminated": [e.to_dict() for e in legal.eliminated],
            "deterministic_solution": legal.deterministic_solution,
            "escalation": escalation,
            "authority": list(legal.authority),
            "budget_units": legal.budget_units,
            "spent_units": legal.spent_units,
            "shadow": shadow_rows,
            # Nothing in this lane is ever executed; keep both facts explicit.
            "selected_action": None,
            "executed_action": None,
        }
        try:
            result["receipts_path"] = str(append_shadow_receipt(receipt))
        except Exception:  # noqa: BLE001 - a write failure must not fail the op
            result["receipts_path"] = None

    return result


__all__ = [
    "DEFAULT_SERVER_TIMEOUT_MS",
    "RECEIPT_NAME",
    "RECEIPT_SCHEMA",
    "SCHEMA",
    "ShadowPayloadError",
    "ShadowSpec",
    "append_shadow_receipt",
    "build_shadow_plan",
    "run_shadow",
    "shadow_receipt_path",
]
