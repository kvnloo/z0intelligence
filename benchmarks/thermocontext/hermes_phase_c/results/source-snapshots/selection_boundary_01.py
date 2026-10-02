"""Offline bridge discriminator, not a runtime plugin or selector.

Reuses z0 ContextPacket. Host policy supplies pinned/legal IDs and token accounting;
the candidate supplies optional IDs only. No verifier labels reach a selector.
"""
from __future__ import annotations

import copy
import hashlib
import json
from collections.abc import Callable, Sequence

from z0int.context_resolve import ContextPacket, project_to_aodl_fields


class SelectionRejected(ValueError):
    """Reject a candidate without increasing its authority or silently repairing it."""


def _digest(value: object) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                     ensure_ascii=False).encode()).hexdigest()


def freeze_digest(packet: ContextPacket) -> str:
    return _digest(packet.to_dict())


def materialize(packet: ContextPacket, optional_ids: Sequence[str], *,
                pinned_ids: Sequence[str], legal_optional_ids: Sequence[str],
                expected_pool_digest: str, budget: int,
                count_tokens: Callable[[str], int], tokenizer_identity: str) -> dict:
    """Apply a bounded proposal in pool order; count the complete rendered context.

    The caller owns required/allowed IDs. This performs membership/provenance checks,
    not semantic intent validation, content authorization or downstream verification.
    Token counts are caller-supplied: fixture counters establish conformance only.
    The budget applies to the added context; full request limits remain host-owned.
    """
    if freeze_digest(packet) != expected_pool_digest:
        raise SelectionRejected("candidate pool changed")
    if type(budget) is not int or budget < 0 or not tokenizer_identity:
        raise SelectionRejected("a valid budget and tokenizer identity are required")
    evidence = {ref.source_id: ref for ref in packet.evidence}
    source_keys = {(ref.source_version, ref.locator) for ref in packet.evidence}
    if len(evidence) != len(packet.evidence) or len(source_keys) != len(evidence):
        raise SelectionRejected("duplicate source identity or alias")
    for ids in (optional_ids, pinned_ids, legal_optional_ids):
        if isinstance(ids, str) or any(not isinstance(item, str) for item in ids) or len(set(ids)) != len(ids):
            raise SelectionRejected("IDs must be a sequence without duplicates")
    pinned, legal, chosen = set(pinned_ids), set(legal_optional_ids), set(optional_ids)
    if not pinned <= evidence.keys() or not legal <= evidence.keys() or pinned & legal:
        raise SelectionRejected("invalid host policy or missing pinned evidence")
    if not chosen <= legal:
        raise SelectionRejected("proposal contains non-optional or ineligible evidence")
    selected = [ref for ref in packet.evidence if ref.source_id in pinned | chosen]
    if any(not ref.excerpt or not ref.source_version or not ref.locator for ref in selected):
        raise SelectionRejected("selected evidence lacks source-backed text")
    projected = copy.deepcopy(packet)
    projected.evidence = selected
    # Derived recipe and projection described the original pool, so regenerate the
    # AODL projection and explicitly remove the now-inapplicable cached recipe.
    projected.recipe = None
    projected.measurements = {}
    projected.aodl_projection = {}
    projected.aodl_projection = project_to_aodl_fields(projected)
    context = json.dumps({"evidence": [ref.to_dict() for ref in selected],
                          "contradictions": projected.contradictions,
                          "unresolved_gaps": projected.unresolved_gaps},
                         ensure_ascii=False, sort_keys=True)
    units = count_tokens(context)
    if type(units) is not int or units < 0:
        raise SelectionRejected("unknown or invalid token count")
    if units > budget:
        raise SelectionRejected("rendered context exceeds budget; retain baseline")
    return {"pool_sha256": expected_pool_digest, "selected_ids": [ref.source_id for ref in selected],
            "packet": projected.to_dict(), "context": context,
            "context_sha256": hashlib.sha256(context.encode()).hexdigest(),
            "budget_units": units, "budget": budget, "tokenizer_identity": tokenizer_identity,
            "runtime_authority": False}


def witness(selection: dict, request: dict) -> dict:
    """Record exact request presence separately from real consumption or usefulness.

    Pass an observed transport request for real evidence. Locally constructed
    requests only exercise serialization; callers must preserve this distinction.
    """
    texts = []
    for message in request.get("messages", []):
        content = message.get("content")
        if message.get("role") == "user" and isinstance(content, str):
            texts.append(content)
    occurrences = sum(text.count(selection["context"]) for text in texts)
    return {"pool_sha256": selection["pool_sha256"],
            "context_sha256": selection["context_sha256"],
            "request_sha256": _digest(request),
            "request_contains_exact_context": occurrences == 1,
            "occurrences": occurrences, "model_consumed_context": None,
            "verified_success": None, "actual_provider_tokens": None}
