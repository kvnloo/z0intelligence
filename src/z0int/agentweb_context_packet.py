"""Compile pre-selected AgentWeb knowledge evidence into a bounded ContextPacket.

AgentWeb remains the private retrieval/data authority. This module never reaches
into AgentWeb stores and never persists private excerpts.
"""
from __future__ import annotations

import hashlib
import json
import re
import time
from typing import Any

from .context_resolve import (
    ContextPacket,
    EvidenceRef,
    InformationNeed,
    ResolutionRecipe,
    project_to_aodl_fields,
)

SESSION_RE = re.compile(r"^agentweb:[0-9a-f]{24}$")
SOURCE_RE = re.compile(r"^agentweb-kb:[0-9a-f]{64}$")
VERSION_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
LOCATOR_RE = re.compile(r"^agentweb-kb://[0-9a-f]{64}$")
MAX_EVIDENCE = 20
MAX_CONTENT_CHARS = 5000
DEFAULT_PACKET_BYTES = 6000
MIN_PACKET_BYTES = 1024
MAX_PACKET_BYTES = 12000


def _now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _sha16(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:16]


def _json_bytes(value: Any) -> int:
    return len(
        json.dumps(
            value,
            sort_keys=True,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
        ).encode("utf-8")
    )


def _parse_need(raw: Any, index: int) -> InformationNeed:
    if not isinstance(raw, dict):
        raise ValueError("need must be an object")
    allowed = {"id", "description", "required"}
    if set(raw) - allowed:
        raise ValueError("unknown need fields")
    ident = raw.get("id")
    description = raw.get("description")
    required = raw.get("required", True)
    if not isinstance(ident, str) or not ident.strip() or len(ident) > 120:
        raise ValueError("invalid need id")
    if not isinstance(description, str) or not description.strip() or len(description) > 1000:
        raise ValueError("invalid need description")
    if type(required) is not bool:
        raise ValueError("need.required must be boolean")
    return InformationNeed(
        id=ident,
        description=description,
        kind="natural_language",
        required=required,
    )


def _validate_source(raw: Any, need_ids: set[str]) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise ValueError("evidence must be an object")
    allowed = {
        "source_id", "source_version", "locator", "content",
        "need_ids", "source_kind",
    }
    if set(raw) - allowed:
        raise ValueError("unknown evidence fields")
    source_id = raw.get("source_id")
    source_version = raw.get("source_version")
    locator = raw.get("locator")
    content = raw.get("content")
    refs = raw.get("need_ids")
    source_kind = raw.get("source_kind", "unknown")
    if not isinstance(source_id, str) or not SOURCE_RE.fullmatch(source_id):
        raise ValueError("invalid pseudonymous source_id")
    if not isinstance(source_version, str) or not VERSION_RE.fullmatch(source_version):
        raise ValueError("invalid source_version")
    if not isinstance(locator, str) or not LOCATOR_RE.fullmatch(locator):
        raise ValueError("invalid pseudonymous locator")
    if not isinstance(content, str) or not content.strip() or len(content) > MAX_CONTENT_CHARS:
        raise ValueError("evidence content must be 1..5000 chars")
    if not isinstance(refs, list) or not refs or any(x not in need_ids for x in refs):
        raise ValueError("evidence need_ids must reference declared needs")
    if source_kind not in {
        "public-knowledge-base",
        "private-knowledge-base",
        "google-drive",
        "clickup",
        "clickup-tasks",
        "unknown",
    }:
        raise ValueError("invalid source_kind")
    return {
        "source_id": source_id,
        "source_version": source_version,
        "locator": locator,
        "content": content,
        "need_ids": tuple(dict.fromkeys(refs)),
        "source_kind": source_kind,
    }


def validate_agentweb_context_pack(args: dict[str, Any]) -> tuple[
    list[InformationNeed], list[dict[str, Any]], int
]:
    allowed = {
        "harness", "trace_id", "parent_agent", "task_id", "needs", "evidence",
        "scan_incomplete", "max_packet_bytes",
    }
    if not isinstance(args, dict) or set(args) - allowed:
        raise ValueError("unknown AgentWeb context-pack fields")
    if args.get("harness") != "agentweb":
        raise ValueError("context pack is AgentWeb-only")
    for key in ("trace_id", "task_id"):
        value = args.get(key)
        if not isinstance(value, str) or not value.strip() or len(value) > 200:
            raise ValueError("invalid " + key)
    parent = args.get("parent_agent")
    if not isinstance(parent, str) or not SESSION_RE.fullmatch(parent):
        raise ValueError("parent_agent must already be pseudonymous")
    if type(args.get("scan_incomplete", False)) is not bool:
        raise ValueError("scan_incomplete must be boolean")

    raw_needs = args.get("needs")
    if not isinstance(raw_needs, list) or not 1 <= len(raw_needs) <= 16:
        raise ValueError("needs must contain 1..16 items")
    needs = [_parse_need(raw, i) for i, raw in enumerate(raw_needs)]
    ids = [need.id for need in needs]
    if len(ids) != len(set(ids)):
        raise ValueError("need ids must be unique")

    raw_evidence = args.get("evidence")
    if not isinstance(raw_evidence, list) or len(raw_evidence) > MAX_EVIDENCE:
        raise ValueError("evidence must contain at most 20 items")
    evidence = [_validate_source(raw, set(ids)) for raw in raw_evidence]

    packet_bytes = args.get("max_packet_bytes", DEFAULT_PACKET_BYTES)
    if type(packet_bytes) is not int or not MIN_PACKET_BYTES <= packet_bytes <= MAX_PACKET_BYTES:
        raise ValueError("max_packet_bytes out of bounds")
    return needs, evidence, packet_bytes


def compile_agentweb_context_packet(args: dict[str, Any]) -> dict[str, Any]:
    """Return a bounded packet. Private excerpts are never written to z0 storage."""
    started = time.perf_counter()
    needs, evidence, max_packet_bytes = validate_agentweb_context_pack(args)

    # De-dupe exact source/version pairs while preserving caller ranking.
    deduped: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for item in evidence:
        key = (item["source_id"], item["source_version"])
        if key in seen:
            continue
        seen.add(key)
        deduped.append(item)

    selected: list[dict[str, Any]] = []
    selected_keys: set[tuple[str, str]] = set()
    truncated = 0

    # Must-keep pass: one source for each required information need when available.
    for need in needs:
        if not need.required:
            continue
        candidate = next(
            (
                item
                for item in deduped
                if need.id in item["need_ids"]
                and (item["source_id"], item["source_version"]) not in selected_keys
            ),
            None,
        )
        if candidate is not None:
            selected.append(candidate)
            selected_keys.add((candidate["source_id"], candidate["source_version"]))

    # Fill with remaining ranked evidence.
    for item in deduped:
        key = (item["source_id"], item["source_version"])
        if key not in selected_keys:
            selected.append(item)
            selected_keys.add(key)

    refs: list[EvidenceRef] = []
    retained_need_ids: set[str] = set()
    for item in selected:
        # Start bounded even before total-packet accounting.
        content = item["content"]
        excerpt = content[:1200]
        if len(excerpt) < len(content):
            truncated += 1
        candidate = EvidenceRef(
            source_id=item["source_id"],
            source_version=item["source_version"],
            locator=item["locator"],
            trust_class="index_hit",
            observed_at=_now_iso(),
            excerpt=excerpt,
            note=f"agentweb_source_kind={item['source_kind']}",
        )
        tentative = refs + [candidate]
        packet_probe = {
            "needs": [
                {"id": n.id, "description": n.description, "required": n.required}
                for n in needs
            ],
            "evidence": [ref.to_dict() for ref in tentative],
        }
        if _json_bytes(packet_probe) > max_packet_bytes:
            # Try a smaller excerpt before dropping the source entirely.
            remaining = max(120, min(600, max_packet_bytes // max(2, len(selected))))
            shorter = excerpt[:remaining]
            if len(shorter) < len(excerpt):
                truncated += 1
            candidate = EvidenceRef(
                source_id=item["source_id"],
                source_version=item["source_version"],
                locator=item["locator"],
                trust_class="index_hit",
                observed_at=_now_iso(),
                excerpt=shorter,
                note=f"agentweb_source_kind={item['source_kind']}",
            )
            packet_probe["evidence"] = [ref.to_dict() for ref in refs + [candidate]]
            if _json_bytes(packet_probe) > max_packet_bytes:
                continue
        refs.append(candidate)
        retained_need_ids.update(item["need_ids"])

    required_ids = {need.id for need in needs if need.required}
    gaps = [
        f"{need.id}: no retained AgentWeb knowledge evidence"
        for need in needs
        if need.required and need.id not in retained_need_ids
    ]
    if args.get("scan_incomplete") is True:
        gaps.append(
            "agentweb-kb: source scan incomplete; absence is not confirmed exhaustive"
        )

    source_epochs = {
        "agentweb_evidence_set": _sha16(
            "|".join(
                item["source_id"] + ":" + item["source_version"]
                for item in deduped
            )
        )
    }
    recipe = ResolutionRecipe(
        capability_id="context_pack.agentweb",
        request_signature=_sha16(
            json.dumps(
                {
                    "task_id": args["task_id"],
                    "needs": [
                        {"id": n.id, "description": n.description, "required": n.required}
                        for n in needs
                    ],
                    "sources": [
                        [item["source_id"], item["source_version"]]
                        for item in deduped
                    ],
                },
                sort_keys=True,
                ensure_ascii=False,
            )
        ),
        scope_fingerprint=_sha16(args["parent_agent"]),
        policy_revision="agentweb-context-pack-v0",
        source_epochs=source_epochs,
        operations=(
            {
                "op": "agentweb_preselected_evidence",
                "input_count": len(evidence),
                "deduped_count": len(deduped),
                "retained_count": len(refs),
                "scan_incomplete": args.get("scan_incomplete") is True,
            },
        ),
        required_evidence_fields=tuple(sorted(required_ids)),
        verifier_revision="none",
        hardware_profile="metadata-only",
    )
    packet = ContextPacket(
        task_id=args["task_id"],
        needs=needs,
        evidence=refs,
        contradictions=[],
        unresolved_gaps=gaps,
        recipe=recipe,
        measurements={
            "wall_ms": (time.perf_counter() - started) * 1000,
            "input_evidence_count": len(evidence),
            "deduped_evidence_count": len(deduped),
            "retained_evidence_count": len(refs),
            "input_content_bytes": sum(len(item["content"].encode("utf-8")) for item in evidence),
            "max_packet_bytes": max_packet_bytes,
            "truncated_excerpts": truncated,
            "scan_incomplete": args.get("scan_incomplete") is True,
            "gpu_loaded": False,
            "network_model_calls": 0,
            "private_text_persisted": False,
        },
    )
    packet.aodl_projection = project_to_aodl_fields(packet)
    result = packet.to_dict()
    result["measurements"]["packet_bytes"] = _json_bytes(result)
    result["measurements"]["compression_ratio"] = (
        result["measurements"]["packet_bytes"]
        / max(1, result["measurements"]["input_content_bytes"])
    )
    return {
        "ok": True,
        "mode": "shadow",
        "applied": False,
        "packet": result,
    }
