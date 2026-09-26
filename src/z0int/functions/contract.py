"""Function contract for z0intelligence function calls.

One function id, one input shape, one result shape, one receipt. The router
(owned elsewhere) chooses *which* implementation runs; this package only defines
the contract and the two verified implementations of it.

    contract.py                    <- this file: ids, proposition, policy, shapes
    jev.py                         <- implementation: TypeSafe Jev (reference)
    laya.py                        <- implementation: local Laya 421M (fast path)
    verify_evidence_sufficiency.py  <- the function itself + Laya->Jev escalation

Deliberately free of any router, registry, or routing-policy import: this layer
decides nothing about which function should run.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

FUNCTION_ID = "verify.evidence_sufficiency"

#: The proposition under test. Fixed wording, never interpolated with the query:
#: the query is supplied once, in the state, as context.
PROPOSITION = (
    "Does the supplied evidence contain enough information to answer the user's "
    "query correctly without additional retrieval or assumptions?"
)

#: Semantically equivalent phrasings. Acceptance requires the ordering to survive
#: at least two of them, so an implementation is only trustworthy if it is stable
#: across this list.
PHRASINGS: tuple[str, ...] = (
    PROPOSITION,
    "Is the evidence provided sufficient to answer the user's question correctly, "
    "with no further retrieval and no assumptions?",
    "Could the user's query be answered correctly using only the evidence shown, "
    "as it stands?",
)

#: PROVISIONAL policy thresholds. Uncalibrated; a receipt records them verbatim.
DEFAULT_ALLOW_AT = 0.65
DEFAULT_ESCALATE_BELOW = 0.35

ACTIONS = ("ALLOW", "ABSTAIN", "ESCALATE")

#: Set when a backend returns a different served revision than the one validated.
EXPECTED_JEV_MODEL = "jev-1.13.0"


def action_of(
    p_true: float,
    *,
    allow_at: float = DEFAULT_ALLOW_AT,
    escalate_below: float = DEFAULT_ESCALATE_BELOW,
) -> str:
    """Two-threshold band. Explicit ambiguity, never a bare 0.5 cut."""
    if allow_at <= escalate_below:
        raise ValueError("allow_at must be greater than escalate_below")
    if p_true >= allow_at:
        return "ALLOW"
    if p_true <= escalate_below:
        return "ESCALATE"
    return "ABSTAIN"


def is_uncertain(
    p_true: float,
    *,
    allow_at: float = DEFAULT_ALLOW_AT,
    escalate_below: float = DEFAULT_ESCALATE_BELOW,
) -> bool:
    """True when the fast path should hand the same question to the reference.

    Uncertainty is the abstain band, not a failure: the fast path answered, it
    just did not answer decisively enough to act on alone.
    """
    return action_of(p_true, allow_at=allow_at, escalate_below=escalate_below) == "ABSTAIN"


#: State budget, in characters. Identical to the validated harness on purpose:
#: a verifier is only validated on the state it was measured with, so this layer
#: must not render a near-copy. ``render_state`` below is the measured rendering.
_STATE_CHAR_BUDGET = 1300
#: Uniform entry cap so no section can consume the whole budget.
_MAX_ENTRIES_PER_SECTION = 4
#: Shortest clipped line worth emitting; below this it is a stub.
_MIN_LINE_CHARS = 40
#: Cap on the unbilled query context.
_QUERY_MAX_CHARS = 300


def _clip(value: Any, limit: int) -> str:
    if isinstance(value, str):
        text = value
    else:
        text = json.dumps(value, ensure_ascii=False, sort_keys=True)
    text = " ".join(text.split())
    if limit <= 1:
        return ""
    return text if len(text) <= limit else text[: limit - 1] + "\u2026"


class _Budget:
    """Accumulate lines in priority order, clipped to fit the remaining budget.

    Trimming by one budget beats tuning a cap per section: caps fitted to the
    three pilot packets would not generalize, and every section here is optional
    except the header. Highest-priority sections are emitted first and later ones
    are progressively squeezed, so the highest-value content always survives.
    """

    def __init__(self, budget: int):
        self.budget = budget
        self.lines: list[str] = []
        #: Context emitted ahead of the budgeted lines and NOT charged to them.
        self.lead: list[str] = []
        self.dropped: list[str] = []

    def _room(self) -> int:
        used = sum(len(x) + 1 for x in self.lines)
        return self.budget - used

    def add_lead(self, line: str) -> None:
        """Emit context outside the evidence budget.

        The query is context for the proposition, not evidence. Charging it to
        the budget would re-trim the trailing sections and change the evidence
        payload as a side effect, which is exactly what must not happen when only
        the proposition is meant to change. It is capped by the caller, so it
        cannot grow without bound.
        """
        if line:
            self.lead.append(line)

    def add(self, line: str, *, priority: str = "low") -> None:
        room = self._room()
        # A line clipped to a few characters ("Evide...") costs tokens and tells
        # the verifier nothing, so drop it rather than emit a stub.
        if room < _MIN_LINE_CHARS:
            self.dropped.append(priority)
            return
        if len(line) > room:
            line = _clip(line, room - 1)
        if line:
            self.lines.append(line)
        else:
            self.dropped.append(priority)

    def text(self) -> str:
        return "\n".join(self.lead + self.lines)


def render_state(packet: dict[str, Any]) -> str:
    """Compact deterministic rendering of the packet -- what the verifier sees.

    Deterministic so the same packet always yields the same judgement, and
    budget-bounded so NanoJev's truncation should not fire.

    Note this renders only what the packet actually carries. Provenance stays on
    the packet -- tied to this text by ``state_sha256`` -- rather than being
    repeated here, where it would spend budget the prose needs.
    """
    b = _Budget(_STATE_CHAR_BUDGET)

    # The user's query, supplied exactly once, as context for the proposition.
    # It moved here from the proposition itself: the proposition now asks whether
    # this evidence answers "the user's query", so the query has to be visible to
    # the verifier, and it must not also appear in the question or it would be
    # duplicated. Highest priority so it cannot be squeezed out, and it displaces
    # only the low-priority index sections below, never the evidence.
    query = str(packet.get("query") or "").strip()
    if query:
        b.add_lead("Query: " + _clip(query, _QUERY_MAX_CHARS))

    counts = packet.get("evidence_counts") or {}
    if counts:
        b.add("Evidence counts: " + ", ".join(f"{k}={v}" for k, v in sorted(counts.items())),
              priority="counts")

    # Order is generic decision value, not per-section tuning: contradictions,
    # unknowns and supersession signals are the decisive content and are usually
    # absent (so they cost nothing). Evidence prose comes next, because judging
    # support is the whole job. Retrieval plumbing (which harnesses answered, the
    # time window, the plan) and the index-shaped sections are demoted below it:
    # they are auditable from the packet, and they used to crowd out the evidence
    # entirely.
    for label, key in (
        ("Contradiction candidate", "contradiction_candidates"),
        ("Unknown", "unknowns"),
        ("Supersession signal", "supersession_signals"),
    ):
        for item in (packet.get(key) or [])[:_MAX_ENTRIES_PER_SECTION]:
            if isinstance(item, dict):
                body = item.get("excerpt") or item.get("summary") or item.get("reason") or item
                src = item.get("session_id") or item.get("pointer") or item.get("agent") or ""
                b.add(f"{label} [{_clip(src, 50)}]: {_clip(body, 160)}", priority=key)
            else:
                b.add(f"{label}: {_clip(item, 160)}", priority=key)

    # The evidence itself. Before the packet carried an `evidence` field this
    # renderer could only show pointers and literals -- file paths -- so the
    # verifier was judging an index and could not tell a supported claim from an
    # unsupported one.
    for item in (packet.get("evidence") or [])[:_MAX_ENTRIES_PER_SECTION * 2]:
        if isinstance(item, dict):
            excerpt = item.get("excerpt") or item.get("text") or ""
            harness = str(item.get("source_id") or item.get("source") or "?")
            harness = harness.split(":", 1)[0]
            b.add(f"Evidence {item.get('rank')} [{_clip(harness, 24)}]: {_clip(excerpt, 320)}",
                  priority="evidence")
        else:
            b.add(f"Evidence: {_clip(item, 320)}", priority="evidence")

    cov = packet.get("coverage") or {}
    if cov:
        b.add("Sources: " + _clip(cov.get("harnesses_with_hits") or [], 160), priority="sources")
        if cov.get("earliest") or cov.get("latest"):
            b.add(f"Evidence window: {cov.get('earliest')} .. {cov.get('latest')}",
                  priority="window")
    plan = packet.get("plan") or {}
    if plan:
        b.add("Question type: " + _clip(plan.get("question_type"), 40)
              + "; entities: " + _clip(plan.get("entities") or [], 100), priority="plan")

    literals = packet.get("literals") or []
    if literals:
        b.add("Literals: " + _clip(", ".join(str(x) for x in literals), 200), priority="literals")

    for item in (packet.get("decision_like") or [])[:_MAX_ENTRIES_PER_SECTION]:
        if isinstance(item, dict):
            body = item.get("excerpt") or item.get("summary") or item.get("reason") or item
            src = item.get("session_id") or item.get("pointer") or item.get("agent") or ""
            b.add(f"Decision-like prior [{_clip(src, 50)}]: {_clip(body, 160)}",
                  priority="decision_like")
        else:
            b.add(f"Decision-like prior: {_clip(item, 160)}", priority="decision_like")

    ranked = packet.get("evidence_pointers_ranked") or packet.get("evidence_pointers") or []
    for item in ranked[:_MAX_ENTRIES_PER_SECTION]:
        if isinstance(item, dict):
            b.add(f"Pointer (hits={item.get('hits')}): {_clip(item.get('pointer'), 90)}",
                  priority="pointers")
        else:
            b.add(f"Pointer: {_clip(item, 90)}", priority="pointers")

    omitted = packet.get("pointers_omitted")
    if omitted:
        b.add(f"Pointers omitted: {omitted}", priority="omitted")
    return b.text()


def state_digest(state: str) -> str:
    return hashlib.sha256(state.encode("utf-8")).hexdigest()[:16]


def _git_sha() -> str | None:
    try:
        root = Path(__file__).resolve().parents[2]
        out = subprocess.run(["git", "-C", str(root), "rev-parse", "HEAD"],
                             capture_output=True, text=True, timeout=10)
        if out.returncode != 0:
            return None
        return out.stdout.strip() or None
    except (OSError, subprocess.SubprocessError):
        return None


@dataclass(frozen=True)
class PhysicalCall:
    """One real backend call. Both escalations are recorded, not just the winner."""

    backend: str
    model: str | None
    latency_ms: float
    ok: bool
    detail: str = ""
    p_true: float | None = None
    usage: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        out = {"backend": self.backend, "model": self.model,
               "latency_ms": round(self.latency_ms, 2), "ok": self.ok}
        if self.detail:
            out["detail"] = self.detail
        if self.p_true is not None:
            out["p_true"] = self.p_true
        if self.usage:
            out["usage"] = self.usage
        return out


@dataclass(frozen=True)
class BackendCapabilities:
    """What the router needs to choose. No routing decision is made here."""

    function_id: str
    backend: str
    local: bool
    role: str                      # fast_path | reference_escalation | fallback
    model: str
    revision: str | None
    available: bool
    credential_source: str | None = None
    validated_on: tuple[str, ...] = ()
    validation: dict[str, Any] = field(default_factory=dict)
    typical_latency_ms: float | None = None
    cost_per_call_usd: float | None = None
    detail: str = ""

    def to_dict(self) -> dict[str, Any]:
        out = {
            "function_id": self.function_id, "backend": self.backend, "local": self.local,
            "role": self.role, "model": self.model, "revision": self.revision,
            "available": self.available, "validated_on": list(self.validated_on),
            "validation": self.validation,
        }
        if self.credential_source:
            out["credential_source"] = self.credential_source
        if self.typical_latency_ms is not None:
            out["typical_latency_ms"] = self.typical_latency_ms
        if self.cost_per_call_usd is not None:
            out["cost_per_call_usd"] = self.cost_per_call_usd
        if self.detail:
            out["detail"] = self.detail
        return out


class VerifierUnavailable(RuntimeError):
    """The backend cannot answer: missing credential, missing checkpoint, transport error."""

    def __init__(self, backend: str, detail: str) -> None:
        super().__init__(f"{backend}: {detail}")
        self.backend = backend
        self.detail = detail


@dataclass(frozen=True)
class VerificationResult:
    p_true: float
    p_false: float
    action: str
    backend: str
    model: str | None
    revision: str | None
    latency_ms: float
    trace_id: str
    state_sha256_16: str
    receipt: dict[str, Any]
    capabilities: BackendCapabilities
    calls: tuple[PhysicalCall, ...] = ()
    escalated: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "function_id": FUNCTION_ID,
            "p_true": self.p_true, "p_false": self.p_false, "action": self.action,
            "backend": self.backend, "model": self.model, "revision": self.revision,
            "latency_ms": round(self.latency_ms, 2), "trace_id": self.trace_id,
            "escalated": self.escalated,
            "calls": [c.to_dict() for c in self.calls],
        }
