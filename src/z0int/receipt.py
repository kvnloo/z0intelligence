"""Decision receipt: one object for cognition → action → world → economics.

Schema ``z0int.decision_receipt.v1`` is the contract. Harnesses and Evolution Lab
emit compatible rows; Kerdoios consumes ``capability_id`` + token fields only.
Personal artifacts land under ``~/.z0int/receipts/`` (never the git tree).
"""

from __future__ import annotations

import json
import fcntl
import os
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from . import paths

SCHEMA = "z0int.decision_receipt.v1"
RECEIPTS_NAME = "decisions.jsonl"
OUTCOMES_NAME = "outcomes.jsonl"

# Ambient harness closes (OMP turn_end / agent_end). Old in-memory bridges still
# pass success+tool_ok; those must never mint gold or inflate verified_tasks.
AMBIENT_CLOSE_SOURCES = frozenset(
    {
        "bridge_turn_end",
        "bridge_agent_end",
        "bridge_agent_start",
        "omp_turn_end",
        "omp_agent_end",
        "close_turn",
    }
)

GOLD_SIGNALS = (
    "verified_success",
    "verified",
    "test_pass",
    "verifier_ok",
    "pr_merged",
    "task_done",
)

NEGATIVE_TRUE_SIGNALS = ("user_correction", "reverted", "ci_failed")

MEASUREMENT_STATES = frozenset({"complete", "partial", "unsupported", "failed", "unknown"})
EXPLICIT_INCOMPLETE_MEASUREMENT_STATES = frozenset({"partial", "unsupported", "failed"})


def normalize_measurement_state(value: Any) -> str | None:
    if value is None:
        return None
    state = str(value).strip().lower()
    return state if state in MEASUREMENT_STATES else None


def normalize_state_reason(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text[:500] if text else None


def receipts_path(root: Path | None = None) -> Path:
    layout = paths.ensure_layout(root)
    return layout["receipts"] / RECEIPTS_NAME


def outcomes_path(root: Path | None = None) -> Path:
    layout = paths.ensure_layout(root)
    return layout["receipts"] / OUTCOMES_NAME


def new_trace_id() -> str:
    return uuid.uuid4().hex


@dataclass
class Outcome:
    """World consequences joined later via ``trace_id``.

    Evidence semantics (do not collapse these):

    - ``execution_completed`` — harness turn finished (agent returned). Not quality.
    - ``tool_ok`` — a tool call returned without transport error. Not quality.
    - ``success`` — legacy soft flag; does **not** mint gold by itself.
    - ``verified_success`` / ``verified`` — explicit quality verdict (async join OK).
    - ``test_pass`` / ``verifier_ok`` / ``pr_merged`` / ``task_done`` — gold signals.

    Ambient OMP ``turn_end`` should set ``execution_completed=true`` and leave
    ``verified_success=null`` until CI/tests/user-correction/join.
    """

    execution_completed: bool | None = None
    verified_success: bool | None = None
    verified: bool | None = None  # alias; prefer verified_success
    success: bool | None = None  # soft / legacy — never alone → gold
    tool_ok: bool | None = None  # tool transport OK — never alone → gold
    test_pass: bool | None = None
    task_done: bool | None = None
    user_correction: bool | None = None
    reverted: bool | None = None
    verifier_ok: bool | None = None
    ci_failed: bool | None = None
    pr_merged: bool | None = None
    retries: int | None = None
    note: str | None = None
    source: str | None = None  # hermes|omp|ci|manual|bridge_turn_end|...
    verification_source: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {k: v for k, v in asdict(self).items() if v is not None}

    def tier(self) -> str:
        """Classify outcome strength for training / tokenomics.

        Returns:
          gold | negative | execution | soft

        Gold requires a *verification* signal, not mere turn completion.
        Ambient harness closes without verification_source never mint gold
        even if a stale bridge stamped test_pass/success.
        """
        return normalize_outcome(self).raw_tier()

    def raw_tier(self) -> str:
        """Tier from fields as-is (no ambient sanitize). Prefer ``tier()``."""
        d = self.to_dict()
        negative = any(d.get(k) is True for k in NEGATIVE_TRUE_SIGNALS) or any(
            d.get(k) is False for k in GOLD_SIGNALS
        )
        if negative:
            return "negative"
        gold = any(d.get(k) is True for k in GOLD_SIGNALS)
        if gold:
            return "gold"
        if d.get("execution_completed") is True:
            return "execution"
        if d.get("success") is True or d.get("tool_ok") is True:
            return "soft"
        return "soft"

    def is_verified(self) -> bool:
        """True only when a verification signal supports quality learning."""
        return self.tier() == "gold"


def _outcome_from_mapping(outcome: Outcome | dict[str, Any]) -> Outcome:
    if isinstance(outcome, Outcome):
        return outcome
    fields = Outcome.__dataclass_fields__
    return Outcome(**{k: v for k, v in outcome.items() if k in fields})


def is_ambient_close_source(source: str | None) -> bool:
    if not source:
        return False
    s = str(source)
    if s in AMBIENT_CLOSE_SOURCES:
        return True
    # automatic bridge_* closes except explicit operator markers
    if s.startswith("bridge_") and s not in {"bridge_manual", "bridge_verified"}:
        return True
    return False


def normalize_outcome(outcome: Outcome | dict[str, Any]) -> Outcome:
    """Demote stale ambient closes so they cannot contaminate gold.

    Old OMP processes bind an older z0int-bridge that still closes with
    ``success=true tool_ok=true`` (and sometimes ``test_pass=true``).
    Python is the shared write path those processes still call — sanitize
    here so tip and stale hosts agree.

    Rules:
    - verification_source set → trust gold signals (async CI/join/operator).
    - ambient source without verification_source → strip gold claims;
      force execution_completed so bare success/tool_ok become ``execution``.
    - non-ambient success/tool_ok alone stay ``soft`` (probes, legacy).
    """
    oc = _outcome_from_mapping(outcome)
    d = oc.to_dict()
    src = d.get("source")
    vsrc = d.get("verification_source")
    has_vsrc = isinstance(vsrc, str) and bool(vsrc.strip())
    if is_ambient_close_source(src if isinstance(src, str) else None) and not has_vsrc:
        for k in GOLD_SIGNALS:
            if getattr(oc, k, None) is True:
                setattr(oc, k, None)
        if oc.execution_completed is not False:
            oc.execution_completed = True
        if oc.note is None:
            oc.note = "ambient_close_sanitized"
        else:
            note = str(oc.note)
            if "ambient_close_sanitized" not in note:
                oc.note = note + "|ambient_close_sanitized"
    return oc


def effective_tier(
    outcome: Outcome | dict[str, Any] | None,
    *,
    stored_tier: str | None = None,
) -> str | None:
    """Recompute tier from outcome fields; never trust a stale stored gold label."""
    if outcome is None and not stored_tier:
        return None
    if outcome is None:
        if stored_tier == "gold":
            return "soft"
        return stored_tier
    return normalize_outcome(outcome).raw_tier()


    def is_verified(self) -> bool:
        """True only when a verification signal supports quality learning."""
        return self.tier() == "gold"


@dataclass
class DecisionReceipt:
    """One specialist/model decision and its (optional) measured economics."""

    trace_id: str
    session_id: str | None = None
    capability_id: str | None = None
    provider: str | None = None  # local_mb | openjev | kerdoios_plan | frontier | ...
    model: str | None = None
    prediction: str | None = None
    confidence: float | None = None
    action_taken: str | None = None  # route/label/action actually applied
    route: str | None = None  # local | model | escalate | shadow
    execution: str = "log_only"  # log_only | shadow | canary | live
    outcome: dict[str, Any] | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    cached_input_tokens: int | None = None
    baseline_input_tokens: int | None = None
    baseline_output_tokens: int | None = None
    estimated_frontier_tokens_avoided: int | None = None
    measured_frontier_tokens: int | None = None  # actual post-turn when known
    measurement_state: str | None = None
    state_reason: str | None = None
    latency_ms: float | None = None
    fallbacks: int = 0
    ts: float = field(default_factory=time.time)
    schema: str = SCHEMA
    extra: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        extra = d.pop("extra") or {}
        # drop Nones for compact jsonl
        out = {k: v for k, v in d.items() if v is not None}
        if extra:
            out["extra"] = extra
        out["schema"] = SCHEMA
        return out

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> DecisionReceipt:
        known = {f.name for f in cls.__dataclass_fields__.values()}  # type: ignore[attr-defined]
        base = {k: v for k, v in raw.items() if k in known and k != "extra"}
        extra = {k: v for k, v in raw.items() if k not in known and k != "schema"}
        if "extra" in raw and isinstance(raw["extra"], dict):
            extra.update(raw["extra"])
        base.setdefault("trace_id", new_trace_id())
        base["extra"] = extra
        return cls(**base)  # type: ignore[arg-type]

    def tokens_saved_est(self) -> int | None:
        if self.estimated_frontier_tokens_avoided is not None:
            return int(self.estimated_frontier_tokens_avoided)
        if normalize_measurement_state(self.measurement_state) in EXPLICIT_INCOMPLETE_MEASUREMENT_STATES:
            return None
        if self.baseline_input_tokens is None and self.baseline_output_tokens is None:
            return None
        base = int(self.baseline_input_tokens or 0) + int(self.baseline_output_tokens or 0)
        used = int(self.input_tokens or 0) + int(self.output_tokens or 0)
        if self.measured_frontier_tokens is not None:
            used = int(self.measured_frontier_tokens)
        return max(0, base - used)


def build_receipt(
    *,
    trace_id: str | None = None,
    session_id: str | None = None,
    capability_id: str | None = None,
    provider: str | None = None,
    model: str | None = None,
    prediction: str | None = None,
    confidence: float | None = None,
    action_taken: str | None = None,
    route: str | None = None,
    execution: str = "log_only",
    latency_ms: float | None = None,
    input_tokens: int | None = None,
    output_tokens: int | None = None,
    baseline_input_tokens: int | None = None,
    baseline_output_tokens: int | None = None,
    estimated_frontier_tokens_avoided: int | None = None,
    measurement_state: str | None = None,
    state_reason: str | None = None,
    fallbacks: int = 0,
    extra: dict[str, Any] | None = None,
) -> DecisionReceipt:
    return DecisionReceipt(
        trace_id=trace_id or new_trace_id(),
        session_id=session_id,
        capability_id=capability_id,
        provider=provider,
        model=model,
        prediction=prediction,
        confidence=confidence,
        action_taken=action_taken,
        route=route,
        execution=execution,
        latency_ms=latency_ms,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        baseline_input_tokens=baseline_input_tokens,
        baseline_output_tokens=baseline_output_tokens,
        estimated_frontier_tokens_avoided=estimated_frontier_tokens_avoided,
        measurement_state=normalize_measurement_state(measurement_state),
        state_reason=normalize_state_reason(state_reason),
        fallbacks=fallbacks,
        extra=dict(extra or {}),
    )


# Optional experiment / counterfactual keys stored on receipt.extra (or top-level).
EXPERIMENT_KEYS = (
    "experiment_id",
    "pair_id",
    "task_snapshot_id",
    "arm_id",  # candidate | reference | audit
    "treatment_hash",
    "selection_policy",  # active | audit | historical_replay | production
    "assignment_probability",
    "reference_requested",
    "reason_for_reference",
    "replay_grade",  # A|B|C|D
    "verifier_class",
)


def attach_experiment(receipt: dict[str, Any] | DecisionReceipt, **fields: Any) -> dict[str, Any]:
    """Merge counterfactual experiment identity onto a receipt dict."""
    row = receipt.to_dict() if isinstance(receipt, DecisionReceipt) else dict(receipt)
    extra = dict(row.get("extra") or {})
    for k in EXPERIMENT_KEYS:
        if k in fields and fields[k] is not None:
            extra[k] = fields[k]
            row[k] = fields[k]
    if extra:
        row["extra"] = extra
    return row


def treatment_hash(
    *,
    model_version: str | None = None,
    reasoning_effort: str | None = None,
    system_prompt_hash: str | None = None,
    skills_hash: str | None = None,
    context_policy: str | None = None,
    tool_schema_hash: str | None = None,
    temperature: float | None = None,
) -> str:
    """Stable fingerprint of a model/prompt/context treatment arm."""
    import hashlib

    payload = {
        "model_version": model_version,
        "reasoning_effort": reasoning_effort,
        "system_prompt_hash": system_prompt_hash,
        "skills_hash": skills_hash,
        "context_policy": context_policy,
        "tool_schema_hash": tool_schema_hash,
        "temperature": temperature,
    }
    blob = json.dumps(payload, sort_keys=True, default=str).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()[:16]



def validate_receipt(raw: dict[str, Any]) -> list[str]:
    """Return list of problems; empty = ok enough to store."""
    errs: list[str] = []
    if not isinstance(raw, dict):
        return ["not_an_object"]
    if not raw.get("trace_id"):
        errs.append("missing_trace_id")
    schema = raw.get("schema")
    if schema and schema != SCHEMA and not str(schema).startswith("z0int.decision_receipt"):
        errs.append(f"unexpected_schema:{schema}")
    conf = raw.get("confidence")
    if conf is not None:
        try:
            c = float(conf)
            if c < 0 or c > 1.5:  # allow slight overshoot from logits
                errs.append("confidence_out_of_range")
        except (TypeError, ValueError):
            errs.append("confidence_not_numeric")
    return errs


def append_receipt(receipt: DecisionReceipt | dict[str, Any], *, root: Path | None = None) -> dict[str, Any]:
    row = receipt.to_dict() if isinstance(receipt, DecisionReceipt) else dict(receipt)
    row.setdefault("schema", SCHEMA)
    row.setdefault("ts", time.time())
    row.setdefault("trace_id", new_trace_id())
    errs = validate_receipt(row)
    if errs and "missing_trace_id" in errs:
        raise ValueError(f"invalid receipt: {errs}")
    path = receipts_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fcntl.flock(fh, fcntl.LOCK_EX)
        fh.write(json.dumps(row, default=str) + "\n")
        fh.flush()
        os.fsync(fh.fileno())
        fcntl.flock(fh, fcntl.LOCK_UN)
    return row


def _iter_jsonl(path: Path):
    if not path.is_file():
        return
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError:
                continue


def find_receipt(trace_id: str, *, root: Path | None = None) -> dict[str, Any] | None:
    """Latest canonical receipt, falling back to legacy streams in the same root."""
    hits: list[dict[str, Any]] = []
    primary = receipts_path(root)
    state_root = paths.home() if root is None else root
    for path in (
        primary,
        state_root / "stream" / "bridge.jsonl",
        state_root / "stream" / "raw.jsonl",
    ):
        for row in _iter_jsonl(path) or []:
            if not isinstance(row, dict):
                continue
            if row.get("trace_id") == trace_id:
                # bridge nests receipt
                if "receipt" in row and isinstance(row["receipt"], dict):
                    merged = dict(row)
                    nested = dict(row["receipt"])
                    for k, v in nested.items():
                        merged.setdefault(k, v)
                    hits.append(merged)
                else:
                    hits.append(row)
        if path == primary and hits:
            return hits[-1]
    return hits[-1] if hits else None


def find_receipt_by_extra(
    key: str,
    value: Any,
    *,
    root: Path | None = None,
) -> dict[str, Any] | None:
    """Latest canonical correlation match, with same-root legacy fallback."""
    if not isinstance(key, str) or not key or value is None:
        return None
    hits: list[dict[str, Any]] = []
    paths_home = paths.home() if root is None else root
    primary = receipts_path(root)
    for path in (
        primary,
        paths_home / "stream" / "bridge.jsonl",
        paths_home / "stream" / "raw.jsonl",
    ):
        for row in _iter_jsonl(path) or []:
            if not isinstance(row, dict):
                continue
            rec = row.get("receipt") if isinstance(row.get("receipt"), dict) else row
            if not isinstance(rec, dict):
                continue
            extra = rec.get("extra")
            if isinstance(extra, dict) and extra.get(key) == value:
                hits.append(rec)
        if path == primary and hits:
            return hits[-1]
    return hits[-1] if hits else None


def join_outcome(
    trace_id: str,
    outcome: Outcome | dict[str, Any],
    *,
    root: Path | None = None,
) -> dict[str, Any] | None:
    """Attach world outcome to a prior decision; append gold/negative to outcomes.jsonl."""
    base = find_receipt(trace_id, root=root)
    oc = normalize_outcome(outcome)
    oc_dict = oc.to_dict()
    tier = oc.raw_tier()
    joined = {
        "schema": "z0int.outcome_join.v1",
        "ts": time.time(),
        "trace_id": trace_id,
        "outcome": oc_dict,
        "outcome_tier": tier,
        "receipt": base,
    }
    # always record the join event
    op = outcomes_path(root)
    op.parent.mkdir(parents=True, exist_ok=True)
    with op.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(joined, default=str) + "\n")
    # update latest receipt row copy into receipts with outcome (append-only)
    if base is not None:
        updated = dict(base)
        updated["outcome"] = oc_dict
        updated["outcome_tier"] = tier
        updated["outcome_ts"] = joined["ts"]
        updated["schema"] = SCHEMA
        append_receipt(updated, root=root)
    return joined





def close_turn(
    trace_id: str,
    *,
    measured_frontier_tokens: int | None = None,
    input_tokens: int | None = None,
    output_tokens: int | None = None,
    cached_input_tokens: int | None = None,
    latency_ms: float | None = None,
    provider: str | None = None,
    model: str | None = None,
    outcome: Outcome | dict[str, Any] | None = None,
    measurement_state: str | None = None,
    state_reason: str | None = None,
    root: Path | None = None,
    source: str = "close_turn",
) -> dict[str, Any]:
    """Post-turn close: write measured tokens, optional outcome join, economics fields.

    Append-only: emits an updated receipt row. When baseline+measured both exist,
    ``actual_tokens_saved`` is computed on the closed row for immediate proof.
    """
    base = find_receipt(trace_id, root=root) or {"trace_id": trace_id, "schema": SCHEMA}
    closed = dict(base)
    # unwrap nested bridge receipt fields already merged by find_receipt
    if measured_frontier_tokens is not None:
        closed["measured_frontier_tokens"] = int(measured_frontier_tokens)
    elif input_tokens is not None or output_tokens is not None:
        closed["measured_frontier_tokens"] = int(input_tokens or 0) + int(output_tokens or 0)
    if input_tokens is not None:
        closed["input_tokens"] = int(input_tokens)
    if output_tokens is not None:
        closed["output_tokens"] = int(output_tokens)
    if cached_input_tokens is not None:
        closed["cached_input_tokens"] = int(cached_input_tokens)
    if latency_ms is not None:
        closed["latency_ms"] = float(latency_ms)
    if provider is not None:
        closed["provider"] = provider
    if model is not None:
        closed["model"] = model
    normalized_state = normalize_measurement_state(measurement_state)
    if normalized_state is not None:
        closed["measurement_state"] = normalized_state
    normalized_reason = normalize_state_reason(state_reason)
    if normalized_reason is not None:
        closed["state_reason"] = normalized_reason
    closed["schema"] = SCHEMA
    closed["close_ts"] = time.time()
    closed["close_source"] = source
    b_in = closed.get("baseline_input_tokens")
    b_out = closed.get("baseline_output_tokens")
    base_tot = None
    try:
        if b_in is not None or b_out is not None:
            base_tot = int(b_in or 0) + int(b_out or 0)
    except (TypeError, ValueError):
        base_tot = None
    meas = closed.get("measured_frontier_tokens")
    state = normalize_measurement_state(closed.get("measurement_state"))
    if state in EXPLICIT_INCOMPLETE_MEASUREMENT_STATES:
        closed.pop("actual_tokens_saved", None)
    elif base_tot is not None and meas is not None:
        try:
            closed["actual_tokens_saved"] = max(0, base_tot - int(meas))
        except (TypeError, ValueError):
            pass
    # strip non-receipt noise from bridge merge
    for k in ("preflight", "kerdoios_plan", "prompt", "receipt"):
        closed.pop(k, None)
    row = append_receipt(closed, root=root)
    joined = None
    if outcome is not None:
        joined = join_outcome(trace_id, outcome, root=root)
    return {
        "schema": "z0int.turn_close.v1",
        "trace_id": trace_id,
        "receipt": row,
        "outcome_join": joined,
        "actual_tokens_saved": row.get("actual_tokens_saved"),
        "measured_frontier_tokens": row.get("measured_frontier_tokens"),
        "measurement_state": row.get("measurement_state"),
        "state_reason": row.get("state_reason"),
        "baseline_tokens": base_tot,
    }


def summarize_tokenomics(*, root: Path | None = None, limit: int = 5000) -> dict[str, Any]:
    """Aggregate estimated + measured frontier-token economics.

    Count the latest receipt per trace, preferring canonical receipts over
    legacy bridge mirrors. Retain unidentified rows as separate observations.
    Verified counts always re-derive from outcome *fields* via
    ``normalize_outcome``; a stored ``outcome_tier=gold`` without
    verification signals does not count.
    """
    rows = 0
    avoided = 0
    measured = 0
    baseline = 0
    actual_saved = 0
    actual_saved_authoritative = 0
    actual_saved_provisional = 0
    rows_with_both = 0
    measurement_state_counts: dict[str, int] = {}
    with_outcome = 0
    verified = 0
    verified_tokens = 0
    verified_complete_tokens = 0
    verified_with_token_measurement = 0
    verified_complete_token_measurements = 0
    false_gold_ignored = 0
    by_cap: dict[str, int] = {}
    by_tier: dict[str, int] = {}
    paths_home = paths.home() if root is None else root
    candidates = [
        receipts_path(root),
        paths_home / "stream" / "bridge.jsonl",
    ]
    seen_traces: set[str] = set()
    for path in candidates:
        for row in reversed(list(_iter_jsonl(path) or [])[-limit:]):
            rec = row.get("receipt") if isinstance(row.get("receipt"), dict) else row
            if not isinstance(rec, dict):
                continue
            trace_id = row.get("trace_id") or rec.get("trace_id")
            if isinstance(trace_id, str) and trace_id:
                if trace_id in seen_traces:
                    continue
                seen_traces.add(trace_id)
            rows += 1
            state = normalize_measurement_state(rec.get("measurement_state")) or "unknown"
            measurement_state_counts[state] = measurement_state_counts.get(state, 0) + 1
            av = rec.get("estimated_frontier_tokens_avoided")
            if av is not None:
                try:
                    avoided += int(av)
                except (TypeError, ValueError):
                    pass
            b_in = rec.get("baseline_input_tokens")
            b_out = rec.get("baseline_output_tokens")
            base_tot = None
            try:
                if b_in is not None or b_out is not None:
                    base_tot = int(b_in or 0) + int(b_out or 0)
                    baseline += base_tot
            except (TypeError, ValueError):
                base_tot = None
            meas = rec.get("measured_frontier_tokens")
            meas_i = None
            if meas is not None:
                try:
                    meas_i = int(meas)
                    measured += meas_i
                except (TypeError, ValueError):
                    meas_i = None
            if base_tot is not None and meas_i is not None:
                rows_with_both += 1
                if state not in EXPLICIT_INCOMPLETE_MEASUREMENT_STATES:
                    saved = max(0, base_tot - meas_i)
                    actual_saved += saved
                    if state == "complete":
                        actual_saved_authoritative += saved
                    else:
                        actual_saved_provisional += saved
            outcome = rec.get("outcome") or row.get("outcome")
            stored_tier = rec.get("outcome_tier") or row.get("outcome_tier")
            if outcome or stored_tier:
                with_outcome += 1
                tier = effective_tier(
                    outcome if isinstance(outcome, dict) else None,
                    stored_tier=stored_tier if isinstance(stored_tier, str) else None,
                )
                if stored_tier == "gold" and tier != "gold":
                    false_gold_ignored += 1
                if tier:
                    by_tier[tier] = by_tier.get(tier, 0) + 1
                if tier == "gold":
                    verified += 1
                    tok = meas_i if meas_i is not None else (
                        base_tot if base_tot is not None else None
                    )
                    if tok is None and av is not None:
                        try:
                            tok = int(av)
                        except (TypeError, ValueError):
                            tok = None
                    if tok is not None:
                        verified_tokens += int(tok)
                        verified_with_token_measurement += 1
                        if state == "complete":
                            verified_complete_tokens += int(tok)
                            verified_complete_token_measurements += 1
            cap = rec.get("capability_id") or row.get("capability_id") or "unknown"
            by_cap[str(cap)] = by_cap.get(str(cap), 0) + 1
    t_per_v = (verified_tokens / verified) if verified else None
    verified_tokens_authoritative = (
        verified > 0
        and verified_with_token_measurement == verified
        and verified_complete_token_measurements == verified
    )
    authoritative_t_per_v = (
        verified_complete_tokens / verified
        if verified_tokens_authoritative
        else None
    )
    return {
        "schema": "z0int.tokenomics_summary.v1",
        "rows": rows,
        "frontier_tokens_avoided_est": avoided,
        "baseline_tokens_sum": baseline,
        "measured_frontier_tokens_sum": measured,
        "actual_tokens_saved": actual_saved,
        "actual_tokens_saved_authoritative": actual_saved_authoritative,
        "actual_tokens_saved_provisional": actual_saved_provisional,
        "rows_with_baseline_and_measured": rows_with_both,
        "measurement_state_counts": measurement_state_counts,
        "rows_with_outcome": with_outcome,
        "verified_tasks": verified,
        "tokens_per_verified_task": t_per_v,
        "tokens_per_verified_task_authoritative": authoritative_t_per_v,
        "verified_token_measurement_authoritative": verified_tokens_authoritative,
        "false_gold_ignored": false_gold_ignored,
        "by_tier": by_tier,
        "by_capability": by_cap,
    }


def scrub_contaminated_outcomes(
    *,
    root: Path | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Append corrected joins for rows whose stored gold lacks verification.

    Append-only: original contaminated lines stay for audit; latest join wins
    for consumers that take the last row per trace_id.
    """
    op = outcomes_path(root)
    scanned = 0
    contaminated = 0
    rewritten = 0
    corrections: list[dict[str, Any]] = []
    latest: dict[str, dict[str, Any]] = {}
    for row in _iter_jsonl(op) or []:
        scanned += 1
        tid = row.get("trace_id")
        if isinstance(tid, str) and tid:
            latest[tid] = row
    for tid, row in latest.items():
        stored = row.get("outcome_tier")
        oc = row.get("outcome") if isinstance(row.get("outcome"), dict) else {}
        tier = effective_tier(
            oc, stored_tier=stored if isinstance(stored, str) else None
        )
        if stored == "gold" and tier != "gold":
            contaminated += 1
            fixed_oc = normalize_outcome(oc).to_dict()
            if tier == "soft" and is_ambient_close_source(fixed_oc.get("source")):
                fixed_oc["execution_completed"] = True
                tier = "execution"
            correction = {
                "schema": "z0int.outcome_join.v1",
                "ts": time.time(),
                "trace_id": tid,
                "outcome": fixed_oc,
                "outcome_tier": tier,
                "receipt": row.get("receipt"),
                "scrubbed_from_tier": stored,
                "scrub_reason": "false_gold_no_verification_signal",
                "scrubbed_from_ts": row.get("ts"),
            }
            corrections.append(correction)
    if not dry_run and corrections:
        op.parent.mkdir(parents=True, exist_ok=True)
        with op.open("a", encoding="utf-8") as fh:
            for c in corrections:
                fh.write(json.dumps(c, default=str) + "\n")
                rewritten += 1
                base = (
                    c.get("receipt")
                    if isinstance(c.get("receipt"), dict)
                    else find_receipt(c["trace_id"], root=root)
                )
                if isinstance(base, dict):
                    updated = dict(base)
                    updated["outcome"] = c["outcome"]
                    updated["outcome_tier"] = c["outcome_tier"]
                    updated["outcome_ts"] = c["ts"]
                    updated["scrubbed_from_tier"] = c["scrubbed_from_tier"]
                    updated["schema"] = SCHEMA
                    append_receipt(updated, root=root)
    return {
        "schema": "z0int.outcome_scrub.v1",
        "ok": True,
        "dry_run": dry_run,
        "scanned_joins": scanned,
        "unique_traces": len(latest),
        "contaminated_latest": contaminated,
        "rewritten": rewritten if not dry_run else 0,
        "would_rewrite": contaminated if dry_run else rewritten,
        "corrections": [
            {
                "trace_id": c["trace_id"],
                "from": c.get("scrubbed_from_tier"),
                "to": c.get("outcome_tier"),
            }
            for c in corrections
        ],
    }
