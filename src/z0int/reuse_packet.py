"""Revision-bound architecture reuse packet (#137; memory parent #22).

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
        # This schema records search labels and evidence about rejected
        # candidates, but no revision-bound proof that the search scope was
        # complete. Those fields alone cannot qualify NOVEL.
        return False

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
    packet: ArchitectureReusePacket,
) -> str:
    # Hash the full derived packet state. The fingerprint is an integrity check,
    # not authentication: source observations still belong to the caller.
    blob = packet.to_dict()
    blob.pop("input_fingerprint", None)
    return hashlib.sha256(
        json.dumps(
            blob,
            sort_keys=True,
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def _has_revision(ref: EvidenceRef) -> bool:
    return bool(
        ref.source_id.strip()
        and ref.locator.strip()
        and ref.source_version.strip()
        and ref.source_version.strip().lower() not in {"unknown", "missing", "none", "null"}
    )


def _is_implementation_ref(ref: EvidenceRef) -> bool:
    return ref.trust_class == "code" and _has_revision(ref)


def _is_test_or_verifier_ref(ref: EvidenceRef) -> bool:
    if not _is_implementation_ref(ref):
        return False
    identity = f"{ref.source_id}/{ref.locator}".lower().replace("\\", "/")
    parts = {part for part in identity.replace(".", "/").replace("-", "/").split("/") if part}
    return any(
        part in {"test", "tests", "verify", "verifier", "verification"}
        or part.startswith(("test_", "verify_"))
        for part in parts
    )


def _candidate_is_adequate(candidate: ReuseCandidate) -> bool:
    owner = (candidate.owner or "").strip()
    implementations = [ref for ref in candidate.evidence if _is_implementation_ref(ref)]
    tests = [ref for ref in candidate.related_tests if _is_test_or_verifier_ref(ref)]
    return bool(
        owner
        and implementations
        and any(
            (test.source_id, test.locator) != (implementation.source_id, implementation.locator)
            for test in tests
            for implementation in implementations
        )
    )


_INCOMPLETE_OPERATION_STATES = {
    "building",
    "error",
    "failed",
    "incomplete",
    "missing",
    "not_ready",
    "partial",
    "pending",
    "scanning",
    "stale",
    "timeout",
    "timed_out",
    "unreliable",
    "unknown",
    "unavailable",
    "warming",
}

_COMPLETED_OPERATION_STATES = {"complete", "ready", "ready_empty"}


def _flag_is_set(value: Any) -> bool:
    if isinstance(value, str):
        return value.strip().lower() not in {"", "0", "false", "no", "off", "none"}
    return bool(value)


def _required_coverage_is_complete(context: ContextPacket) -> bool:
    if context.measurements.get("coverage") != "complete":
        return False

    recipe = context.recipe
    if recipe is None:
        return False
    required_needs = set(recipe.required_evidence_fields)
    completed_needs: set[str] = set()
    for operation in recipe.operations:
        need = operation.get("need")
        explicitly_optional = operation.get("required") is False
        required = operation.get("required") is True or (
            not explicitly_optional and need in required_needs
        )
        if not required:
            continue

        if operation.get("coverage") != "complete":
            return False
        state = operation.get("status")
        if not isinstance(state, str) or state.strip().lower() not in _COMPLETED_OPERATION_STATES:
            return False
        if _flag_is_set(operation.get("error")):
            return False
        if _flag_is_set(operation.get("scanning")):
            return False
        for field_name in ("status", "index_status", "generation_status"):
            state = operation.get(field_name)
            if isinstance(state, str) and state.strip().lower() in _INCOMPLETE_OPERATION_STATES:
                return False
        if need in required_needs:
            completed_needs.add(need)
    if not required_needs.issubset(completed_needs):
        return False
    return True


def _decision(
    *,
    context: ContextPacket,
    candidates: Sequence[ReuseCandidate],
    unresolved_gaps: Sequence[str],
) -> dict[str, Any]:
    if any(str(item).strip() for item in context.contradictions):
        return {
            "mode": "OBSERVE",
            "implementation_allowed": False,
            "authorizes_action": False,
            "verified_success": None,
            "reason": "context contains unresolved contradictions",
        }

    if unresolved_gaps or context.unresolved_gaps:
        return {
            "mode": "OBSERVE",
            "implementation_allowed": False,
            "authorizes_action": False,
            "verified_success": None,
            "reason": "required architecture archaeology is incomplete",
        }

    if not _required_coverage_is_complete(context):
        return {
            "mode": "OBSERVE",
            "implementation_allowed": False,
            "authorizes_action": False,
            "verified_success": None,
            "reason": "required context coverage is incomplete or unavailable",
        }

    adequate_candidates = [candidate for candidate in candidates if _candidate_is_adequate(candidate)]
    if adequate_candidates:
        mode: ReuseMode = (
            "REUSE"
            if any(candidate.strategy == "reuse" for candidate in adequate_candidates)
            else "EXTEND"
        )
        return {
            "mode": mode,
            "implementation_allowed": True,
            "authorizes_action": False,
            "verified_success": None,
            "reason": "revision-bound existing implementation evidence is available",
        }

    return {
        "mode": "OBSERVE",
        "implementation_allowed": False,
        "authorizes_action": False,
        "verified_success": None,
        "reason": (
            "no adequately sourced reusable candidate; NOVEL requires structured complete-search evidence"
        ),
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

    packet = ArchitectureReusePacket(
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
        decision=_decision(
            context=context,
            candidates=candidate_list,
            unresolved_gaps=gaps,
        ),
        measurements=dict(measurements or {}),
    )
    packet.input_fingerprint = _input_fingerprint(packet)
    return packet


def check_reuse_packet(
    packet: ArchitectureReusePacket,
    current_source_revisions: Mapping[str, str],
) -> dict[str, Any]:
    """Check a packet against freshly observed dependency revisions.

    The caller owns observation of current revisions. Missing or changed
    dependencies fail closed: the packet becomes stale/OBSERVE rather than
    silently being treated as current.
    """

    integrity_errors: list[str] = []
    try:
        recomputed_revisions = _source_revisions(
            packet.context,
            packet.reuse_candidates,
            packet.related_tests,
            packet.novelty_receipt,
        )
        recomputed_fingerprint = _input_fingerprint(packet)
        recomputed_decision = _decision(
            context=packet.context,
            candidates=packet.reuse_candidates,
            unresolved_gaps=packet.unresolved_gaps,
        )
    except (AttributeError, TypeError, ValueError) as exc:
        recomputed_revisions = {}
        recomputed_fingerprint = ""
        recomputed_decision = {
            "mode": "OBSERVE",
            "implementation_allowed": False,
            "authorizes_action": False,
            "verified_success": None,
            "reason": "reuse packet contents are malformed",
        }
        integrity_errors.append(f"malformed_packet:{type(exc).__name__}")

    if packet.source_revisions != recomputed_revisions:
        integrity_errors.append("source_revisions_mismatch")
    if packet.input_fingerprint != recomputed_fingerprint:
        integrity_errors.append("input_fingerprint_mismatch")
    if packet.decision != recomputed_decision:
        integrity_errors.append("decision_mismatch")

    dependencies = set(recomputed_revisions) | set(current_source_revisions)
    changed = sorted(
        key
        for key in dependencies
        if recomputed_revisions.get(key) != current_source_revisions.get(key)
    )
    changed.extend(
        key
        for key in set(packet.source_revisions) | set(recomputed_revisions)
        if packet.source_revisions.get(key) != recomputed_revisions.get(key) and key not in changed
    )
    changed.sort()
    valid = not changed and not integrity_errors
    if valid:
        decision = recomputed_decision
        status = "current"
        reason = None
    else:
        decision = {
            "mode": "OBSERVE",
            "implementation_allowed": False,
            "authorizes_action": False,
            "verified_success": None,
            "reason": (
                "reuse packet contents or derived decision changed"
                if integrity_errors
                else "reuse packet is stale; source revisions changed or are unavailable"
            ),
        }
        status = "tampered" if integrity_errors else "stale"
        reason = decision["reason"]
    return {
        "valid": valid,
        "status": status,
        "input_fingerprint": packet.input_fingerprint,
        "changed_sources": changed,
        "integrity_errors": integrity_errors,
        "reason": reason,
        "decision": decision,
    }
