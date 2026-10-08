"""Revision-bound architecture reuse packet (#138).

This module deliberately does not perform repository search. It compiles already
resolved, provenance-carrying evidence into a pre-implementation gate so coding
agents must prefer reuse/extension or justify novelty.

Retrieval evidence is not execution authority and never implies verified success.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any, Literal, Mapping, Sequence

from .context_resolve import ContextPacket, EvidenceRef

SCHEMA = "z0int.architecture_reuse_packet.v0"
NOVELTY_SCHEMA = "z0int.novelty_receipt.v0"

ReuseStrategy = Literal["reuse", "extend"]
ReuseMode = Literal["REUSE", "EXTEND", "NOVEL", "OBSERVE"]


def _sha16(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:16]


@dataclass(frozen=True)
class ReuseCandidate:
    """One existing implementation that may satisfy or extend the task."""

    candidate_id: str
    summary: str
    strategy: ReuseStrategy
    evidence: tuple[EvidenceRef, ...]
    owner: str | None = None
    symbol: str | None = None
    related_tests: tuple[EvidenceRef, ...] = ()
    invariants: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.candidate_id.strip():
            raise ValueError("reuse candidate requires candidate_id")
        if not self.summary.strip():
            raise ValueError("reuse candidate requires summary")
        if self.strategy not in {"reuse", "extend"}:
            raise ValueError(f"unsupported reuse strategy: {self.strategy}")
        if not self.evidence:
            raise ValueError("reuse candidate requires revision-bound evidence")

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "candidate_id": self.candidate_id,
            "summary": self.summary,
            "strategy": self.strategy,
            "evidence": [ref.to_dict() for ref in self.evidence],
            "related_tests": [ref.to_dict() for ref in self.related_tests],
            "invariants": list(self.invariants),
        }
        if self.owner is not None:
            out["owner"] = self.owner
        if self.symbol is not None:
            out["symbol"] = self.symbol
        return out


@dataclass(frozen=True)
class RejectedCandidate:
    """Existing candidate ruled out when novelty is genuinely required."""

    candidate_id: str
    reason: str
    evidence: tuple[EvidenceRef, ...]

    def __post_init__(self) -> None:
        if not self.candidate_id.strip():
            raise ValueError("rejected candidate requires candidate_id")
        if not self.reason.strip():
            raise ValueError("rejected candidate requires reason")
        if not self.evidence:
            raise ValueError("rejected candidate requires evidence")

    def to_dict(self) -> dict[str, Any]:
        return {
            "candidate_id": self.candidate_id,
            "reason": self.reason,
            "evidence": [ref.to_dict() for ref in self.evidence],
        }


@dataclass(frozen=True)
class NoveltyReceipt:
    """Evidence that known candidates were searched and ruled out."""

    searched: tuple[str, ...]
    candidates_rejected: tuple[RejectedCandidate, ...]
    new_abstraction_necessary: bool
    schema: str = NOVELTY_SCHEMA

    @property
    def evidence_backed(self) -> bool:
        return bool(
            self.new_abstraction_necessary
            and self.searched
            and self.candidates_rejected
            and all(rejected.evidence for rejected in self.candidates_rejected)
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "searched": list(self.searched),
            "candidates_rejected": [item.to_dict() for item in self.candidates_rejected],
            "new_abstraction_necessary": self.new_abstraction_necessary,
            "evidence_backed": self.evidence_backed,
        }


@dataclass
class ArchitectureReusePacket:
    """Bounded derived view used to gate entry into implementation mode."""

    task_id: str | None
    context: ContextPacket
    reuse_candidates: list[ReuseCandidate] = field(default_factory=list)
    novelty_receipt: NoveltyReceipt | None = None
    ownership: tuple[str, ...] = ()
    relevant_invariants: tuple[str, ...] = ()
    related_tests: tuple[EvidenceRef, ...] = ()
    superseded_or_rejected_paths: tuple[str, ...] = ()
    unresolved_gaps: list[str] = field(default_factory=list)
    source_revisions: dict[str, str] = field(default_factory=dict)
    input_fingerprint: str = ""
    decision: dict[str, Any] = field(default_factory=dict)
    measurements: dict[str, Any] = field(default_factory=dict)
    schema: str = SCHEMA

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "task_id": self.task_id,
            "context": self.context.to_dict(),
            "ownership": list(self.ownership),
            "reuse_candidates": [candidate.to_dict() for candidate in self.reuse_candidates],
            "relevant_invariants": list(self.relevant_invariants),
            "related_tests": [ref.to_dict() for ref in self.related_tests],
            "superseded_or_rejected_paths": list(self.superseded_or_rejected_paths),
            "unresolved_gaps": list(self.unresolved_gaps),
            "novelty_receipt": self.novelty_receipt.to_dict() if self.novelty_receipt else None,
            "source_revisions": dict(self.source_revisions),
            "input_fingerprint": self.input_fingerprint,
            "decision": dict(self.decision),
            "measurements": dict(self.measurements),
            # Explicit authority boundary.
            "authorizes_action": False,
            "execution_completed": False,
            "verified_success": None,
        }


def _all_refs(
    context: ContextPacket,
    candidates: Sequence[ReuseCandidate],
    related_tests: Sequence[EvidenceRef],
    novelty_receipt: NoveltyReceipt | None,
) -> list[EvidenceRef]:
    refs = list(context.evidence)
    refs.extend(related_tests)
    for candidate in candidates:
        refs.extend(candidate.evidence)
        refs.extend(candidate.related_tests)
    if novelty_receipt is not None:
        for rejected in novelty_receipt.candidates_rejected:
            refs.extend(rejected.evidence)
    return refs


def _source_revisions(
    context: ContextPacket,
    candidates: Sequence[ReuseCandidate],
    related_tests: Sequence[EvidenceRef],
    novelty_receipt: NoveltyReceipt | None,
) -> dict[str, str]:
    revisions: dict[str, str] = {}
    if context.recipe is not None:
        for key, value in sorted(context.recipe.source_epochs.items()):
            revisions[f"epoch:{key}"] = value

    for ref in _all_refs(context, candidates, related_tests, novelty_receipt):
        key = "evidence:" + _sha16(f"{ref.source_id}|{ref.locator}")
        existing = revisions.get(key)
        if existing is not None and existing != ref.source_version:
            # A packet with two versions of the same evidence locator is not
            # silently collapsed. Encoding both versions makes the conflict
            # visible in the fingerprint.
            key += ":" + _sha16(ref.source_version)
        revisions[key] = ref.source_version
    return dict(sorted(revisions.items()))


def _input_fingerprint(
    *,
    task_id: str | None,
    source_revisions: Mapping[str, str],
    candidates: Sequence[ReuseCandidate],
    novelty_receipt: NoveltyReceipt | None,
) -> str:
    blob = {
        "schema": SCHEMA,
        "task_id": task_id,
        "source_revisions": dict(sorted(source_revisions.items())),
        "candidates": [
            {
                "candidate_id": candidate.candidate_id,
                "strategy": candidate.strategy,
                "owner": candidate.owner,
                "symbol": candidate.symbol,
            }
            for candidate in candidates
        ],
        "novelty_receipt": novelty_receipt.to_dict() if novelty_receipt else None,
    }
    return hashlib.sha256(
        json.dumps(blob, sort_keys=True, ensure_ascii=False).encode("utf-8")
    ).hexdigest()


def _decision(
    *,
    candidates: Sequence[ReuseCandidate],
    novelty_receipt: NoveltyReceipt | None,
    unresolved_gaps: Sequence[str],
) -> dict[str, Any]:
    if unresolved_gaps:
        return {
            "mode": "OBSERVE",
            "implementation_allowed": False,
            "authorizes_action": False,
            "verified_success": None,
            "reason": "required architecture archaeology is incomplete",
        }

    if candidates:
        mode: ReuseMode = (
            "REUSE" if any(candidate.strategy == "reuse" for candidate in candidates) else "EXTEND"
        )
        return {
            "mode": mode,
            "implementation_allowed": True,
            "authorizes_action": False,
            "verified_success": None,
            "reason": "revision-bound existing implementation evidence is available",
        }

    if novelty_receipt is not None and novelty_receipt.evidence_backed:
        return {
            "mode": "NOVEL",
            "implementation_allowed": True,
            "authorizes_action": False,
            "verified_success": None,
            "reason": "existing candidates were searched and rejected with evidence",
        }

    return {
        "mode": "OBSERVE",
        "implementation_allowed": False,
        "authorizes_action": False,
        "verified_success": None,
        "reason": "no reusable candidate and no evidence-backed NoveltyReceipt",
    }


def build_reuse_packet(
    context: ContextPacket,
    *,
    candidates: Sequence[ReuseCandidate] = (),
    novelty_receipt: NoveltyReceipt | None = None,
    ownership: Sequence[str] = (),
    relevant_invariants: Sequence[str] = (),
    related_tests: Sequence[EvidenceRef] = (),
    superseded_or_rejected_paths: Sequence[str] = (),
    unresolved_gaps: Sequence[str] = (),
    measurements: Mapping[str, Any] | None = None,
) -> ArchitectureReusePacket:
    """Compile resolved evidence into a revision-bound pre-implementation gate.

    This function performs no new search. Callers should resolve repository,
    architecture, test and history evidence through existing providers first.
    """

    candidate_list = list(candidates)
    test_refs = tuple(related_tests)
    gaps = list(dict.fromkeys([*context.unresolved_gaps, *unresolved_gaps]))
    revisions = _source_revisions(context, candidate_list, test_refs, novelty_receipt)
    fingerprint = _input_fingerprint(
        task_id=context.task_id,
        source_revisions=revisions,
        candidates=candidate_list,
        novelty_receipt=novelty_receipt,
    )

    return ArchitectureReusePacket(
        task_id=context.task_id,
        context=context,
        reuse_candidates=candidate_list,
        novelty_receipt=novelty_receipt,
        ownership=tuple(ownership),
        relevant_invariants=tuple(relevant_invariants),
        related_tests=test_refs,
        superseded_or_rejected_paths=tuple(superseded_or_rejected_paths),
        unresolved_gaps=gaps,
        source_revisions=revisions,
        input_fingerprint=fingerprint,
        decision=_decision(
            candidates=candidate_list,
            novelty_receipt=novelty_receipt,
            unresolved_gaps=gaps,
        ),
        measurements=dict(measurements or {}),
    )


def check_reuse_packet(
    packet: ArchitectureReusePacket,
    current_source_revisions: Mapping[str, str],
) -> dict[str, Any]:
    """Check a packet against freshly observed dependency revisions.

    The caller owns observation of current revisions. Missing or changed
    dependencies fail closed: the packet becomes stale/OBSERVE rather than
    silently being treated as current.
    """

    changed = [
        key
        for key, expected in packet.source_revisions.items()
        if current_source_revisions.get(key) != expected
    ]
    valid = not changed
    if valid:
        decision = dict(packet.decision)
    else:
        decision = {
            "mode": "OBSERVE",
            "implementation_allowed": False,
            "authorizes_action": False,
            "verified_success": None,
            "reason": "reuse packet is stale; source revisions changed or are unavailable",
        }
    return {
        "valid": valid,
        "status": "current" if valid else "stale",
        "input_fingerprint": packet.input_fingerprint,
        "changed_sources": changed,
        "decision": decision,
    }
