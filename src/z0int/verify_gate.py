"""Smallest end-to-end instance of the paper primitive.

    State Packet
      -> one boolean verification question
      -> NanoJev P(true) / P(false)
      -> two-threshold policy
      -> ALLOW | ABSTAIN | ESCALATE
      -> receipt

The state packet is the real one: ``tools/statepack.py orient "<query>" --json``
from the zer0 workspace. Nothing here re-implements it, and nothing here trains.

How NanoJev scores a boolean, verified by probe rather than assumed
(``nanojev_runtime.py`` DecisionModel.forward):

    out.append(F.pad(torch.stack([z[i, 0] * 0, z[i, 0]]), (0, kmax - 2)))

so the boolean head emits logits ``[0.0, a]`` for candidate ids
``("false", "true")``. Hence ``P("true") = softmax([0, a])[1] = sigmoid(a)``,
where ``a`` is the logit of the fixed candidate text "The proposition is true."
Orientation is therefore correct: ``P(true)`` rises with the model's belief that
the proposition holds. The cost is that ``P(true)`` hugs 0.5 whenever ``|a|`` is
small, which is what the real corpus shows.

Thresholds are PROVISIONAL. On the real RLCDAlignBench corpus every NanoJev
score falls in 0.323-0.543, so 0.5 is not a decision boundary and this module
refuses to pretend otherwise. It exposes the ambiguity as a band:

    p_true >= allow_at          -> ALLOW     evidence answers the query
    p_true <= escalate_below    -> ESCALATE  evidence does not; get more or a human
    otherwise                   -> ABSTAIN   unresolved; do not act on this alone

The proposition is fixed wording and does not interpolate the query:

    "Does the supplied evidence contain enough information to answer the user's
     query correctly without additional retrieval or assumptions?"

It previously read "does the evidence support acting on <query>", which asks
whether the material is relevant to acting. The experiment's labels mean
something else -- whether the query can be answered from this evidence alone --
so the two questions were conflated and the cases did not separate. The query is
supplied exactly once, in the state, as context for the proposition.

``ALLOW``/``ABSTAIN``/``ESCALATE`` are this gate's vocabulary. ``families.py``
owns the action-family vocabulary and already has ``ABSTAIN``; the other two are
gate outcomes, not families, so they are not added there.

Usage:

    python -m z0int.verify_gate --packet packet.json
    python -m z0int.verify_gate --packet - < packet.json      # stdin
    python -m z0int.verify_gate --packet packet.json --show-state
    python -m z0int.verify_gate --packet packet.json --receipt-out /tmp/receipts.jsonl
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Sequence

from .backends.base import DecisionOption, DecisionQuestion, DecisionRequest
from .backends.nanojev_runtime import canonical_state_text
from .backends.registry import create_backend
from .receipt import build_receipt, validate_receipt

SCHEMA = "z0int.verify_gate.v1"

ACTIONS = ("ALLOW", "ABSTAIN", "ESCALATE")

#: PROVISIONAL. Not calibrated on a balanced dataset; see the module docstring.
DEFAULT_ALLOW_AT = 0.65
DEFAULT_ESCALATE_BELOW = 0.35

#: Character budget for the rendered state. NanoJev's budget is 512 *tokens*
#: (its checkpoint config) and its runtime drops the head of an over-budget
#: state. This text mixes ids and paths (~2.1-2.7 chars/token) with prose, so an
#: assumed ~4 chars/token overruns by more than 2x. Measured against the real
#: tokenizer on the three proof packets: 1300 chars encoded to 423-462 of 512
#: tokens including the question block, while 1500 chars reached 578 and
#: overflowed. 1300 is the largest budget measured to fit with margin.
#:
#: This is a heuristic; it is not a guarantee. The safety net is the contract,
#: not this number: the runtime reports ``truncated_questions`` and the receipt
#: records ``state_truncated``, so a run can never claim the verifier saw more
#: than it did.
_STATE_CHAR_BUDGET = 1300

#: Uniform entry cap so no one section can consume the whole budget. Applied
#: identically to every list section -- this is not a per-section knob.
_MAX_ENTRIES_PER_SECTION = 4

#: Shortest clipped line worth emitting. Below this the line is a stub that
#: spends tokens without carrying content.
_MIN_LINE_CHARS = 40

#: Cap on the unbilled query context. The whole state is therefore bounded by
#: ``_STATE_CHAR_BUDGET + len("Query: ") + _QUERY_MAX_CHARS``.
_QUERY_MAX_CHARS = 300


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


#: The proposition under test. It is deliberately *not* interpolated with the
#: query: the previous wording ("does the evidence support acting on <query>")
#: asked whether the material was relevant to acting, while the experiment's
#: labels mean "can this query be answered correctly from this evidence alone".
#: Those are different questions, and mixing them made the three cases
#: unseparable. The query is supplied once, in the state, as context.
_PROPOSITION = (
    "Does the supplied evidence contain enough information to answer the user's "
    "query correctly without additional retrieval or assumptions?"
)


def verification_question(packet: dict[str, Any]) -> str:
    """Return the fixed proposition; the query travels in the state instead.

    The query is still required -- the proposition refers to "the user's query"
    and cannot be judged without it -- so a packet lacking one is rejected here
    rather than producing an unanswerable prompt.
    """
    query = str(packet.get("query") or "").strip()
    if not query:
        raise ValueError("state packet has no 'query'; cannot pose a verification question")
    return _PROPOSITION


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


def packet_state_text(packet: dict[str, Any]) -> str:
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


def state_digest(state_text: str) -> str:
    return hashlib.sha256(state_text.encode("utf-8")).hexdigest()[:16]


def _git_sha() -> str | None:
    try:
        root = Path(__file__).resolve().parents[2]
        out = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "HEAD"],
            capture_output=True, text=True, timeout=10,
        )
        if out.returncode != 0:
            return None
        return out.stdout.strip() or None
    except (OSError, subprocess.SubprocessError):
        return None


def packet_provenance(packet: dict[str, Any]) -> dict[str, Any]:
    """Identity of the packet, so a receipt can be traced back to its state."""
    return {
        "source": packet.get("source"),
        "recipe_signature": packet.get("recipe_signature"),
        "query": packet.get("query"),
        "evidence_counts": packet.get("evidence_counts"),
        "compiled_by": (packet.get("plan") or {}).get("compiled_by"),
        "question_type": (packet.get("plan") or {}).get("question_type"),
        "pointers_omitted": packet.get("pointers_omitted"),
        "budgets_ms": packet.get("budgets_ms"),
    }


def gate(
    packet: dict[str, Any],
    *,
    backend: Any = None,
    backend_name: str = "nanojev",
    allow_at: float = DEFAULT_ALLOW_AT,
    escalate_below: float = DEFAULT_ESCALATE_BELOW,
) -> dict[str, Any]:
    """Run one packet through the primitive and return the receipt."""
    question = verification_question(packet)
    state_text = packet_state_text(packet)
    verifier = backend if backend is not None else create_backend(backend_name)

    # The proposition is asked as a two-candidate `choice`, not a `boolean`.
    #
    # This is not a stylistic preference, it is what the checkpoint can actually
    # do. NanoJev is trained with "complete-question categorical cross entropy"
    # over an offered candidate set (attention-based Choice head), on four
    # embodied tasks; boolean and score are interface affordances it was never
    # trained on. The two paths differ structurally:
    #
    #   choice  -> one encoded candidate text per option, the trained set head is
    #              applied (`if self.set_head == "attention" and len(choice)`),
    #              and softmax normalises ACROSS candidates.
    #   boolean -> a single candidate text, slot 0 hard-pinned to `z * 0`, and
    #              boolean is excluded from the set-head list entirely. So
    #              P(true) = sigmoid(z) with no second candidate to contrast
    #              against, and nothing to cancel a shared shift in how the
    #              prefix reads.
    #
    # With one candidate text and a pinned zero there is no explicit FALSE
    # alternative anywhere -- not in the encoding, not in the loss. Naming both
    # candidates makes the contrast real and uses the path the head was trained
    # on. The candidate texts are the minimal symmetric pair: the fixed
    # continuation the boolean path already used, plus its negation.
    q = DecisionQuestion(
        id="evidence_answers_query",
        type="choice",
        instructions=question,
        options=(
            DecisionOption(id="true", description="The proposition is true."),
            DecisionOption(id="false", description="The proposition is false."),
        ),
    )
    request = DecisionRequest(
        state=canonical_state_text(state_text),
        questions=(q,),
        request_id=str(packet.get("recipe_signature") or packet.get("query") or "packet"),
    )
    t0 = time.perf_counter()
    result = verifier.evaluate(request)
    wall_ms = (time.perf_counter() - t0) * 1000.0

    answer = result.answers[0]
    if "true" not in answer.probabilities:
        raise ValueError(f"backend returned no 'true' probability: {sorted(answer.probabilities)}")
    p_true = float(answer.probabilities["true"])
    p_false = float(answer.probabilities.get("false", 1.0 - p_true))
    action = action_of(p_true, allow_at=allow_at, escalate_below=escalate_below)

    # A truncated state means the model judged a state this gate did not build.
    # The decision is still recorded, but never as if the whole state was seen.
    diagnostics = getattr(result, "diagnostics", None) or {}
    truncated = [qid for qid in diagnostics.get("truncated_questions", []) if qid]

    receipt = build_receipt(
        capability_id="verify_gate",
        provider=getattr(result, "backend", None) or backend_name,
        model=getattr(result, "model", None),
        prediction=action,
        confidence=p_true,
        action_taken=action,
        route={"ALLOW": "local", "ABSTAIN": "shadow", "ESCALATE": "escalate"}[action],
        execution="log_only",
        latency_ms=getattr(result, "latency_ms", None) or wall_ms,
        extra={
            "gate_schema": SCHEMA,
            "state_packet": packet_provenance(packet),
            "verification_question": question,
            "p_true": p_true,
            "p_false": p_false,
            "thresholds": {
                "allow_at": allow_at,
                "escalate_below": escalate_below,
                "provisional": True,
                "note": "uncalibrated; both thresholds must be refit on a balanced set",
            },
            "verifier": {
                "backend": getattr(result, "backend", None) or backend_name,
                "model": getattr(result, "model", None),
                "revision": getattr(result, "revision", None),
                "state_chars": len(state_text),
                "state_sha256_16": state_digest(state_text),
                "state_truncated": bool(truncated),
                "truncated_questions": truncated,
            },
            # Two different clocks, deliberately not conflated: latency_ms is
            # the backend's own forward-only measurement, while this one wraps
            # the whole evaluate() call and therefore includes the lazy model
            # load on a cold process (~15s here).
            "evaluate_wall_ms": wall_ms,
            "evaluate_wall_ms_includes_lazy_load": True,

            "git_sha": _git_sha(),
        },
    ).to_dict()
    receipt["_state_text"] = state_text  # inspectability; not part of the schema
    return receipt


def _load_packet(path: str) -> dict[str, Any]:
    raw = sys.stdin.read() if path == "-" else Path(path).read_text(encoding="utf-8")
    packet = json.loads(raw)
    if not isinstance(packet, dict):
        raise ValueError("state packet must be a JSON object")
    return packet


def _append_row(path: Path, row: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, default=str) + "\n")


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="State Packet -> NanoJev -> action -> receipt")
    ap.add_argument("--packet", required=True, help="state packet JSON path, or - for stdin")
    ap.add_argument("--backend", default="nanojev")
    ap.add_argument("--allow-at", type=float, default=DEFAULT_ALLOW_AT)
    ap.add_argument("--escalate-below", type=float, default=DEFAULT_ESCALATE_BELOW)
    ap.add_argument("--show-state", action="store_true", help="print the rendered state to stderr")
    ap.add_argument("--receipt-out", default=None, help="append the receipt to exactly this JSONL file")
    args = ap.parse_args(argv)

    receipt = gate(
        _load_packet(args.packet),
        backend_name=args.backend,
        allow_at=args.allow_at,
        escalate_below=args.escalate_below,
    )
    state_text = receipt.pop("_state_text", "")
    if args.show_state:
        print("--- state fed to verifier ---", file=sys.stderr)
        print(state_text, file=sys.stderr)
        print("--- end state ---", file=sys.stderr)

    problems = validate_receipt(receipt)
    if problems:
        print(json.dumps({"error": "receipt failed validation", "problems": problems}), file=sys.stderr)
        return 2
    if args.receipt_out:
        _append_row(Path(args.receipt_out), receipt)
    print(json.dumps(receipt, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
