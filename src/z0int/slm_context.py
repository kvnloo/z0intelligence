"""Bounded, opt-in context for labelled decision replay (never routing authority).

Memory is supplied as the existing resolver/MCP ContextPacket. This module does
not search a second memory store, persist private text, or invoke a model.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import re
from typing import Any, Callable

from .context_resolve import ContextPacket


def canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def digest(value: Any) -> str:
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def decision_input(row: dict[str, Any]) -> dict[str, Any]:
    """Explicit allowlist: labels, outcomes, IDs and provenance never reach inference."""
    return {key: row[key] for key in ("state", "question", "options")}


@dataclass(frozen=True)
class Variant:
    name: str
    components: tuple[str, ...]
    budget: int


VARIANTS = (
    Variant("none", (), 0),
    Variant("memory", ("memory",), 512),
    Variant("few_shot", ("few_shot",), 2048),
    Variant("summary", ("summary",), 1024),
    Variant("skill_relevance", ("skill_relevance",), 512),
    Variant("combination", ("summary", "skill_relevance", "memory", "few_shot"), 4096),
)


def packet_evidence(packet: ContextPacket | dict[str, Any] | None,
                    allowed_sources: set[str]) -> list[dict[str, str]]:
    """Consume resolver evidence, fail closed on unregistered memory sources.

    Callers search/fetch using the existing granted memory MCP, freeze that
    packet and the source allowlist before replay, and retain its receipt outside
    the public repository. Search hits are evidence, not instructions.
    """
    if packet is None:
        return []
    raw = packet.to_dict() if isinstance(packet, ContextPacket) else packet
    if raw.get("schema") != "z0int.context_resolve.v1":
        raise ValueError("expected a context_resolve ContextPacket")
    result = []
    for ref in raw.get("evidence", []):
        if ref.get("source_id") not in allowed_sources:
            continue
        if not all(ref.get(k) for k in ("source_version", "locator", "trust_class")):
            raise ValueError("memory evidence lacks provenance")
        if ref.get("excerpt"):
            result.append({k: str(ref[k]) for k in ("source_id", "source_version", "locator", "excerpt")})
    return result


def _terms(value: Any) -> set[str]:
    return set(re.findall(r"[a-z0-9_]{2,}", canonical(value).lower()))


def examples_for(row: dict[str, Any], pool: list[dict[str, Any]], limit: int = 2) -> list[dict[str, Any]]:
    terms = _terms(row["state"])
    options = {v["id"] for v in row["options"]}
    candidates = []
    for item in pool:
        if item["family"] != row["family"] or item["group"] == row["group"]:
            continue
        if item["teacher_label"] not in options:
            continue
        other = _terms(item["state"])
        score = len(terms & other) / max(1, len(terms | other))
        candidates.append((-score, str(item["id"]), item))
    return [{"input": {"state": item.get("summary") or item["state"],
                      "option_ids": [option["id"] for option in item["options"]]},
             "choice": item["teacher_label"]}
            for _, _, item in sorted(candidates, key=lambda x: (x[0], x[1]))[:limit]]


def inject(row: dict[str, Any], variant: Variant, pool: list[dict[str, Any]], *,
           memory_packet: ContextPacket | dict[str, Any] | None = None,
           allowed_memory_sources: set[str] | None = None,
           count_tokens: Callable[[str], int] | None = None) -> dict[str, Any]:
    """Add whole JSON fields only; preserve the original decision input verbatim.

    With a tokenizer, budget is exact rendered injection tokens. Otherwise UTF-8
    bytes form a conservative token ceiling for the byte-level Qwen tokenizers;
    this is explicitly NOT a measured prompt-token count. No mid-JSON slicing.
    """
    count = count_tokens or (lambda value: len(value.encode("utf-8")))
    values: dict[str, Any] = {}
    for name in variant.components:
        if name == "memory":
            values[name] = packet_evidence(memory_packet, allowed_memory_sources or set())
        elif name == "few_shot":
            values[name] = examples_for(row, pool)
        elif name in ("summary", "skill_relevance"):
            values[name] = row.get(name, {})
        else:
            raise ValueError(f"unknown context component: {name}")
    included: dict[str, Any] = {}
    omitted = []
    for name in variant.components:
        value = values[name]
        if not value:
            omitted.append(name)
            continue
        if isinstance(value, list):
            accepted = []
            for item in value:
                proposal = {**included, name: [*accepted, item]}
                if count(canonical(proposal)) <= variant.budget:
                    accepted.append(item)
            if accepted:
                included[name] = accepted
            else:
                omitted.append(name)
        elif count(canonical({**included, name: value})) <= variant.budget:
            included[name] = value
        else:
            omitted.append(name)
    request = decision_input(row)
    if included:
        request["state"] = {"decision_state": row["state"],
                            "untrusted_context_evidence": included}
    text = canonical(included) if included else ""
    return {**request, "variant": variant.name, "context_budget": variant.budget,
            "context_units": count(text), "budget_unit": "tokens" if count_tokens else "utf8_bytes_upper_bound",
            "context_bytes": len(text.encode()), "context_components": list(included),
            "context_omitted": omitted}


def split_rows(rows: list[dict[str, Any]], seed: int = 1729) -> tuple[dict[str, list[dict[str, Any]]], dict[str, Any]]:
    """Group split, co-locating groups connected by identical decision inputs."""
    ids: set[str] = set()
    fingerprints: dict[str, str] = {}
    families: dict[str, set[str]] = {}
    parents = {row["group"]: row["group"] for row in rows}

    def root(group: str) -> str:
        while parents[group] != group:
            parents[group] = parents[parents[group]]
            group = parents[group]
        return group

    duplicate_groups = 0
    for row in rows:
        if row["id"] in ids:
            raise ValueError("duplicate row id")
        ids.add(row["id"])
        options = [x["id"] for x in row["options"]]
        if len(set(options)) != len(options) or row["teacher_label"] not in options:
            raise ValueError("invalid choice vocabulary/teacher label")
        if row.get("human_label") is not None and row["human_label"] not in options:
            raise ValueError("invalid human label")
        fp = digest(decision_input(row))
        if fp in fingerprints and root(fingerprints[fp]) != root(row["group"]):
            first, second = sorted((root(fingerprints[fp]), root(row["group"])))
            parents[second] = first
            duplicate_groups += 1
        fingerprints[fp] = row["group"]
        families.setdefault(row["family"], set()).add(row["group"])
    assignments: dict[str, str] = {}
    # Global groups prevent cross-family leakage from the same task/session.
    groups = sorted({root(r["group"]) for r in rows}, key=lambda g: digest([seed, g]))
    if len(groups) < 5:
        raise ValueError("at least five independent groups required")
    pool_end = max(1, len(groups) // 2)
    dev_end = max(pool_end + 1, (len(groups) * 7) // 10)
    for index, group in enumerate(groups):
        assignments[group] = "pool" if index < pool_end else "dev" if index < dev_end else "test"
    assignments = {group: assignments[root(group)] for group in parents}
    split = {name: [r for r in rows if assignments[r["group"]] == name] for name in ("pool", "dev", "test")}
    for family in families:
        if any(not any(r["family"] == family for r in split[name]) for name in split):
            raise ValueError(f"family {family} lacks pool/dev/test coverage; increase independent groups")
    input_partitions: dict[str, set[str]] = {}
    group_partitions: dict[str, set[str]] = {}
    for partition, items in split.items():
        for item in items:
            input_partitions.setdefault(digest(decision_input(item)), set()).add(partition)
            group_partitions.setdefault(root(item["group"]), set()).add(partition)
    input_overlap = sum(len(value) > 1 for value in input_partitions.values())
    group_overlap = sum(len(value) > 1 for value in group_partitions.values())
    if input_overlap or group_overlap:
        raise ValueError("cross-split input/group leakage")
    manifest = {"seed": seed, "dataset_sha256": digest(rows), "assignments": assignments,
                "row_ids": {k: [r["id"] for r in v] for k, v in split.items()},
                "input_hashes": {r["id"]: digest(decision_input(r)) for r in rows},
                "co_located_duplicate_group_edges": duplicate_groups,
                "group_clusters": {group: root(group) for group in parents},
                "duplicate_input_cross_split": input_overlap, "cross_split_groups": group_overlap,
                "human_label_count": sum(r.get("human_label") is not None for r in rows)}
    return split, manifest
