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
import fcntl
import threading
import re
import time
from pathlib import Path
from typing import Any, Mapping, Sequence

from .actions import ActionCandidate, ActionGraph, Rule
from .cascade import CascadeContext, CognitionCascade
from .registry import LocalModelRegistry
from ..receipt import new_trace_id

SCHEMA = "z0int.cognition.shadow.v1"
RECEIPT_SCHEMA = "z0int.cognition.shadow.receipt.v1"
RECEIPT_NAME = "cognition-shadow.jsonl"
_SOURCE_STAMP = Path(__file__).resolve().parents[3] / "DEPLOYED_SHA"
SOURCE_REVISION = _SOURCE_STAMP.read_text().strip() if _SOURCE_STAMP.exists() else None

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


# No shadow call is worth waiting longer than this; it also keeps the wait inside
# what the platform's timers accept.
MAX_SERVER_TIMEOUT_S = 3600.0


def _capped_s(seconds: float) -> float:
    """The wait actually used: at least 0.1 s (also for NaN), at most the cap."""
    return min(MAX_SERVER_TIMEOUT_S, max(0.1, seconds))


def _timeout_s(payload: Mapping[str, Any], timeout_s: float | None) -> float:
    # Each channel resolves exactly as before the cap existed; only a value too
    # large for the platform's timers now resolves to the cap instead of raising.
    if timeout_s is not None:
        try:
            return _capped_s(float(timeout_s))
        except OverflowError:
            return MAX_SERVER_TIMEOUT_S if timeout_s > 0 else 0.1
    raw = payload.get("timeout_ms")
    if isinstance(raw, (int, float)) and not isinstance(raw, bool) and raw > 0:
        try:
            return _capped_s(float(raw) / 1000.0)
        except OverflowError:
            return MAX_SERVER_TIMEOUT_S
    env = os.environ.get("Z0INT_COGNITION_SHADOW_SERVER_TIMEOUT_MS")
    if env:
        try:
            return _capped_s(float(env) / 1000.0)
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
    legal: Any, payload: Mapping[str, Any], *, timeout_s: float,
    shortlist: Sequence[str] = (), n_ctx: int = 4096,
    policy_identity: dict | None = None, policy_home: Path | None = None,
    decision_config: Mapping[str, Any] | None = None, requested_tool: str | None = None,
) -> list[dict[str, Any]]:
    """Run scoped counterfactual arms under one shared caller deadline."""
    from .shadow_binary import run_goal_binary
    results: dict[int, dict] = {}
    lock = threading.Lock()
    deadline = time.monotonic() + max(.1, timeout_s)

    def run(index: int, backend: Any) -> None:
        if decision_config and decision_config.get('decision_method') != 'per_candidate_binary_logit':
            from .slm_scoped import run_scoped
            row = run_scoped(payload, legal, backend, dict(decision_config),
                requested_tool=requested_tool, shortlist=shortlist, n_ctx=n_ctx,
                policy_identity=policy_identity, policy_home=policy_home)
        else:
            row = run_goal_binary(payload, legal, backend, shortlist=shortlist, n_ctx=n_ctx,
                                  policy_identity=policy_identity, policy_home=policy_home)
        row['failure_class'] = failure_class(row.get('error') or row.get('parse_error'))
        with lock:
            results[index] = row

    threads = []
    for index, (_, backend, _) in enumerate(resolved):
        if backend is not None:
            thread = threading.Thread(target=run, args=(index, backend), daemon=True,
                                      name=f'z0int-shadow-{index}')
            threads.append(thread)
            thread.start()
    for thread in threads:
        thread.join(max(0, deadline-time.monotonic()))
    rows = []
    for index, (spec, backend, resolve_error) in enumerate(resolved):
        with lock:
            row = results.get(index)
        if row is None:
            error = resolve_error or 'shadow_timeout'
            row = {'label': 'goal-binary', 'arm': 'goal-binary', 'requested_model': spec.model_id,
                'backend': None, 'model': spec.model_id, 'selected_action': None,
                'abstained': True, 'invalid_call': False, 'error': error,
                'failure_class': failure_class(error), 'latency_ms': timeout_s*1000 if backend else 0}
            if decision_config and decision_config.get('decision_method') != 'per_candidate_binary_logit':
                from .slm_scoped import scope_digest
                digest = scope_digest(dict(decision_config))
                row.update(label='slm-method', arm='slm-method:'+digest, arm_scope=digest,
                           method_config=dict(decision_config))
            if backend is not None:
                row['timed_out'] = True
                row['waited_ms'] = timeout_s*1000
        row['requested_model'] = spec.model_id
        rows.append(row)
    return rows


TRANSPORT_FAILURES = frozenset({
    "connection_refused", "timeout", "http_5xx", "queue_full", "context_overflow",
    "model_not_served", "transport_error",
})


def failure_class(error: Any) -> str | None:
    """Classify legacy and current errors identically without rewriting receipts."""
    text = str(error or "").lower()
    if not text:
        return None
    if "context_overflow" in text or ("exceed" in text and "context" in text):
        return "context_overflow"
    if "connection refused" in text or "errno 111" in text:
        return "connection_refused"
    if "timeout" in text or "timed out" in text:
        return "timeout"
    if "queue" in text and ("full" in text or "exceed" in text):
        return "queue_full"
    if re.search(r"http\s+5\d\d", text):
        return "http_5xx"
    if "is not served" in text or "no_model_requested" in text:
        return "model_not_served"
    if "transport_error" in text or "badstatusline" in text:
        return "transport_error"
    return "parse_error" if ("unparseable" in text or "invalid_call" in text) else "backend_error"




# --- receipt ------------------------------------------------------------


def shadow_receipt_path() -> Path:
    """The only path this lane ever writes to (under ``Z0INT_HOME``)."""
    from .. import paths

    return paths.home() / "shadow" / RECEIPT_NAME


def append_shadow_receipt(row: Mapping[str, Any]) -> Path:
    path = shadow_receipt_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    models = list(row.get("shadow") or [])
    failed = not models or any(m.get("parse_error") or m.get("error") or not m.get("backend") for m in models)
    identity_missing = row.get("identity_contract") == "session-tool-call.v1" and not (
        row.get("session_id") and row.get("tool_call_id"))
    failed = bool(failed or identity_missing)
    # Keep the legacy 72k journal untouched as backfill input. New failed
    # observations have bounded retention and cannot masquerade as training wins.
    with path.with_name("cognition-shadow-write.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        counter = path.with_name("cognition-shadow-health.json")
        try:
            health = json.loads(counter.read_text())
        except (OSError, ValueError):
            health = {"days": {}, "failed_calls": 0, "successful_calls": 0}
        day = time.strftime("%Y-%m-%d", time.gmtime())
        bucket = health["days"].setdefault(day, {"attempts": 0, "failures": 0})
        bucket["attempts"] += 1
        bucket["failures"] += int(failed)
        bucket["identity_failures"] = bucket.get("identity_failures", 0) + int(identity_missing)
        classes = bucket.setdefault("failure_classes", {})
        for model in models:
            kind = model.get("failure_class") or failure_class(model.get("parse_error") or model.get("error"))
            if kind:
                classes[kind] = classes.get(kind, 0) + 1
        health["failed_calls" if failed else "successful_calls"] += 1
        health["days"] = dict(sorted(health["days"].items())[-32:])
        from .learning import atomic_json
        atomic_json(counter, health)
        if failed:
            path = path.with_name("cognition-shadow-failures.jsonl")
            if path.exists() and path.stat().st_size >= 1_048_576:
                for index in (3, 2, 1):
                    source = path.with_name(path.name + (f".{index-1}" if index > 1 else ""))
                    if source.exists():
                        source.replace(path.with_name(path.name + f".{index}"))
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(dict(row), ensure_ascii=False, default=str) + "\n")
        os.chmod(path, 0o600)
    return path


# --- the bridge op body -------------------------------------------------


def _actual_tool(payload: Mapping[str, Any]) -> dict[str, str] | None:
    """Copy label-side identity only; never retain observed arguments or text."""
    actual = payload.get("actual_tool")
    actual = actual if isinstance(actual, Mapping) else {}
    facts = payload.get("facts")
    facts = facts if isinstance(facts, Mapping) else {}
    name = actual.get("name") or facts.get("tool_name")
    if not isinstance(name, str) or not name:
        return None
    result = {"name": name}
    if actual.get("risk_class") in _ALLOWED_RISKS:
        result["risk_class"] = actual["risk_class"]
    return result


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
    facts = payload.get("facts") if isinstance(payload.get("facts"), Mapping) else {}
    tool_call_id = facts.get("tool_call_id") if isinstance(facts.get("tool_call_id"), str) else None
    tool_name = facts.get("tool_name") if isinstance(facts.get("tool_name"), str) else None

    try:
        # Credit is loaded only inside this observe-only lane. Live cascades
        # retain their explicit policy and cannot inherit shadow credit.
        from .. import paths
        from .escalation import EscalationPolicy, EscalationThresholds
        status_path = paths.home() / "loop" / "shadow-learning-status.json"
        try:
            learning_state = json.loads(status_path.read_text())
        except (OSError, ValueError):
            learning_state = {}
        credited = tuple(learning_state.get("credited_families") or ())
        # Compile first, and only then let any backend see the result.
        outcome = CognitionCascade(policy=EscalationPolicy(
            EscalationThresholds(credited_families=credited))).run(context, trace_id=trace_id)
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
    active = learning_state.get('family_search', {}).get('families', {}).get('omp.tool', {})
    projection_strategy = active.get('current_strategy', 'history-1')
    projection_config = {'candidate_k': 3, 'max_options': 3,
                         'decision_method': 'per_candidate_binary_logit',
                         **(active.get('strategy') or {}).get('config', {})}
    local = None
    recent = payload.get("recent_tools")
    if isinstance(recent, list) and all(isinstance(x, str) for x in recent):
        try:
            from .learning import shadow_portfolio
            local = shadow_portfolio(paths.home(), recent, "omp.tool", legal_ids)
        except Exception:
            pass  # missing/locked learner must not block the operator
    if specs and not legal.is_empty:
        from .learning import projection_examples
        try:
            extra = projection_examples(paths.home(), dict(payload), projection_config)
        except (OSError, ValueError):
            extra = {}
        shortlist = extra.get('shortlist') or ([local['selected_action']] if local and local.get('selected_action') else [])
        binary_payload = dict(payload)
        if extra.get('examples'):
            binary_payload['goal_context'] = {**(payload.get('goal_context') or {}), 'examples': extra['examples']}
        resolved = _resolve_shadows(registry, specs)
        if resolved:
            n_ctx = learning_state.get('experiments', {}).get('serving_scope', {}).get('n_ctx', 4096)
            shadow_rows = _decision_rows(resolved, legal, binary_payload,
                timeout_s=_timeout_s(payload, timeout_s), shortlist=shortlist, n_ctx=n_ctx,
                policy_identity=learning_state.get('experiments', {}).get('identity'),
                policy_home=paths.home(), decision_config=projection_config,
                requested_tool=tool_name)
    if local is not None:
        shadow_rows.append(local)

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
        from .shadow_binary import _context
        receipt_goal_context, receipt_context_diagnostics = _context(payload)
        receipt = {
            "schema": RECEIPT_SCHEMA,
            "ts": time.time(),
            "trace_id": trace_id,
            "session_id": session_id,
            # The harness's own identity for the observed call. A random trace
            # id cannot be joined back to a native session on its own.
            "tool_call_id": tool_call_id or None,
            "identity_contract": "session-tool-call.v1",
            "source_revision": SOURCE_REVISION,
            "tool_name": tool_name or None,
            "actual_tool": _actual_tool(payload),
            "graph_digest": legal.graph_digest,
            "candidate_action_count": legal.candidate_count,
            "legal_ids": list(legal.ids()),
            "goal_context": receipt_goal_context,
            "goal_context_diagnostics": receipt_context_diagnostics,
            "projection_config": projection_config,
            "projection_strategy": projection_strategy,
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


# --- scorecard (read-only) ----------------------------------------------

_OLD_STATE_PREFIX = "OMP tool_call: "


def _observed_tool(receipt: Mapping[str, Any]) -> str | None:
    """The tool the harness actually called, or ``None`` if the row does not say."""
    name = receipt.get("tool_name")
    if isinstance(name, str) and name:
        return name
    # Rows written before ``tool_name`` existed carry it on the first state line.
    state = receipt.get("state")
    if isinstance(state, str) and state.startswith(_OLD_STATE_PREFIX):
        return state[len(_OLD_STATE_PREFIX) :].split("\n", 1)[0].strip() or None
    return None


def summarize_shadow_receipts(
    path: str | os.PathLike[str], since_ts: float | None = None
) -> dict[str, Any]:
    """Count what the shadow receipts at ``path`` hold. Reads only; returns counts only.

    A model row is ``unreachable`` or ``timed_out`` when no backend answered, and
    otherwise ``answered``: one of ``selections``, ``invalid`` or ``abstained``.
    """
    totals = {
        "receipts": 0,
        "unreadable_lines": 0,
        "observed_tool_known": 0,
        "observed_in_legal": 0,
        "selections": 0,
        "agreements": 0,
    }
    models: dict[str, dict[str, int]] = {}
    with open(path, encoding="utf-8", errors="replace") as fh:
        for line in fh:
            if not line.strip():
                continue
            try:
                receipt = json.loads(line)
            except ValueError:
                receipt = None
            if not isinstance(receipt, Mapping):
                totals["unreadable_lines"] += 1
                continue
            ts = receipt.get("ts")
            if since_ts is not None and not (
                isinstance(ts, (int, float)) and ts >= since_ts
            ):
                continue
            totals["receipts"] += 1
            observed = _observed_tool(receipt)
            legal = receipt.get("legal_ids")
            if observed is not None:
                totals["observed_tool_known"] += 1
                if isinstance(legal, list) and observed in legal:
                    totals["observed_in_legal"] += 1
            rows = receipt.get("shadow")
            for row in rows if isinstance(rows, list) else ():
                if not isinstance(row, Mapping):
                    continue
                counts = models.setdefault(
                    str(row.get("label") or row.get("model") or "?"),
                    dict.fromkeys(
                        (
                            "rows",
                            "answered",
                            "unreachable",
                            "timed_out",
                            "abstained",
                            "invalid",
                            "selections",
                            "agreements",
                        ),
                        0,
                    ),
                )
                counts["rows"] += 1
                # A backend that could not reach its server still returns a row
                # of its own, with the failure in ``parse_error``.
                failure = row.get("error") or row.get("parse_error")
                failure = failure if isinstance(failure, str) else ""
                if not row.get("backend") or row.get("error") or failure.startswith(
                    "transport_error"
                ):
                    timed_out = failure == "shadow_timeout" or "timed out" in failure
                    counts["timed_out" if timed_out else "unreachable"] += 1
                    continue
                counts["answered"] += 1
                selected = row.get("selected_action")
                if row.get("invalid_call") is True:
                    counts["invalid"] += 1
                elif isinstance(selected, str) and selected:
                    counts["selections"] += 1
                    agrees = row.get("agrees_with_observed")
                    if not isinstance(agrees, bool):
                        agrees = observed is not None and selected == observed
                    counts["agreements"] += int(agrees)
                else:
                    counts["abstained"] += 1
    totals["selections"] = sum(m["selections"] for m in models.values())
    totals["agreements"] = sum(m["agreements"] for m in models.values())
    known = totals["observed_tool_known"]
    return {
        **totals,
        "observed_in_legal_share": round(totals["observed_in_legal"] / known, 4)
        if known
        else None,
        "models": dict(sorted(models.items())),
    }


def _main(argv: Sequence[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(
        prog="python -m z0int.cognition.shadow",
        description="Print counts over the shadow receipts. Reads only.",
    )
    parser.add_argument("path", nargs="?", default=None, help="default: the live receipt file")
    parser.add_argument("--since", type=float, default=None, help="only receipts with ts >= this")
    args = parser.parse_args(argv)
    path = args.path or shadow_receipt_path()
    print(json.dumps(summarize_shadow_receipts(path, args.since), indent=2))
    return 0


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
    "summarize_shadow_receipts",
]


if __name__ == "__main__":
    raise SystemExit(_main())
