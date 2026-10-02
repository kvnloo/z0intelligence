"""Offline source-declaration contract, not a producer or truth verifier.

No oracle, provider client, natural-language inference, or answer repair lives here.
The existing ContextPacket renderer owns membership and provenance preservation.
"""
from __future__ import annotations

import hashlib
import json
import re

SCOPE_SCHEMA = "z0int.source_scope_declaration.v1"


class ContractRejected(ValueError):
    pass


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False, allow_nan=False)


def _object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ContractRejected("duplicate JSON key")
        result[key] = value
    return result


def _constant(value):
    raise ContractRejected("non-finite JSON constant")


def _excerpt(text):
    try:
        return json.loads(text, object_pairs_hook=_object, parse_constant=_constant)
    except json.JSONDecodeError:
        # Prose is retained by the renderer, never converted to a typed claim.
        return None


def _part(declarations):
    values = {canonical(row["value"]) for row in declarations}
    status = "unknown" if not values else "declared" if len(values) == 1 else "conflict"
    return {"status": status,
            "value": declarations[0]["value"] if status == "declared" else None,
            "citations": [row["source_id"] for row in declarations]}


def report(packet: dict, target: dict) -> dict:
    """Inventory exact top-level declarations; never infer semantic authority.

    JSON schema keys are strict for the known scope schema. Source revisions are
    preserved literally, including hex casing. Unknown schemas, nested fields,
    and unstructured prose do not supply a typed scope declaration.
    """
    if (set(target) != {"subject", "predicate"}
            or any(type(value) is not str or not value for value in target.values())):
        raise ContractRejected("target requires exact subject and predicate strings")
    sources, scopes, inventory, seen = [], [], [], set()
    for ref in packet["evidence"]:
        if ref["source_id"] in seen:
            raise ContractRejected("duplicate source ID")
        seen.add(ref["source_id"])
        if any(type(ref.get(key)) is not str or not ref[key]
               for key in ("source_id", "source_version", "locator", "excerpt")):
            raise ContractRejected("source-backed excerpt required")
        parsed = _excerpt(ref["excerpt"])
        if not isinstance(parsed, dict):
            continue
        provenance = {key: ref[key] for key in ("source_id", "source_version", "locator")}
        provenance["excerpt_sha256"] = hashlib.sha256(ref["excerpt"].encode()).hexdigest()
        if "tested_source" in parsed:
            value = parsed["tested_source"]
            if type(value) is not str or re.fullmatch(r"[0-9a-fA-F]{40}", value) is None:
                raise ContractRejected("malformed tested_source declaration")
            row = dict(provenance, json_pointer="/tested_source", value=value)
            sources.append(row)
            inventory.append(dict(row, kind="tested_source"))
        if parsed.get("schema") == SCOPE_SCHEMA:
            if (set(parsed) != {"schema", "subject", "predicate", "declared_value"}
                    or any(type(parsed[key]) is not str or not parsed[key]
                           for key in ("subject", "predicate"))
                    or type(parsed["declared_value"]) is not bool):
                raise ContractRejected("malformed known scope declaration")
            matches = all(parsed[key] == target[key] for key in ("subject", "predicate"))
            row = dict(provenance, json_pointer="/declared_value",
                       subject=parsed["subject"], predicate=parsed["predicate"],
                       value=parsed["declared_value"], matches_target=matches)
            inventory.append(dict(row, kind="scope"))
            if matches:
                scopes.append(row)
    return {"answer": {"tested_source": _part(sources), "scope": _part(scopes)},
            "inventory": inventory, "runtime_authority": False,
            "independent_outcome_verified": False}


def validate_report(candidate: dict, packet: dict, target: dict) -> None:
    """Reject unsupported declarations without changing or repairing an answer."""
    if canonical(candidate) != canonical(report(packet, target)["answer"]):
        raise ContractRejected("answer exceeds or differs from exact source declarations")
