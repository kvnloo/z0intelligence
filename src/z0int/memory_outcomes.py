"""Offline projection of the existing private MCP audit; no store or source reads.

A native output citation proves evidenced output use, not provider-input inclusion,
answer quality, or user benefit. Missing joins remain unknown, never ignored.
"""
from __future__ import annotations

import re
from typing import Any, Iterable


_DIGEST = re.compile(r"[0-9a-f]{64}\Z")


def _digest(value: Any) -> bool:
    return isinstance(value, str) and _DIGEST.fullmatch(value) is not None


def _native(value: Any) -> bool:
    return (isinstance(value, dict) and
            set(value) == {"identity_hmac", "revision_hmac", "session_hmac", "ordinal", "role", "complete"} and
            all(_digest(value[k]) for k in ("identity_hmac", "revision_hmac", "session_hmac")) and
            type(value["ordinal"]) is int and value["ordinal"] >= 0 and
            value["role"] in ("user", "assistant") and value["complete"] is True)


def replay_outcomes(rows: Iterable[dict], *, scope_hmac: str, principal: str) -> list[dict]:
    """Replay saved scoped receipts without a live server, key, or native history.

    The caller retains the opaque scope token from its reviewed receipt. Hashes
    are equality joins, not authentication of an externally edited audit file.
    """
    if not _digest(scope_hmac) or principal not in ("operator", "chatgpt"):
        raise ValueError("explicit replay scope and principal required")
    queries: dict[str, dict] = {}
    joins: dict[str, dict] = {}
    reads: dict[str, dict] = {}
    positions: dict[str, int] = {}

    def prove_read(join: dict, key: str, query: dict, role: str) -> None:
        native = join.get(key)
        fetched = reads.get(join.get(key + "_fetch_receipt_id"))
        if (not _native(native) or native["role"] != role or fetched is None or
                fetched.get("outcome") != "hit" or
                native not in fetched.get("native_records", []) or
                fetched.get("scope_hmac") != scope_hmac or
                fetched.get("principal") != principal or
                native["session_hmac"] != query.get("native_session_hmac") or
                type(query.get("after_ordinal")) is not int or
                native["ordinal"] <= query["after_ordinal"] or
                positions.get(join.get(key + "_fetch_receipt_id"), -1) <= positions.get(query["receipt_id"], -1)):
            raise ValueError("native join lacks a matching authorized original read")
    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            raise ValueError("invalid audit row")
        schema = row.get("schema")
        if schema == "z0int.memory_query_outcome.v1":
            if row.get("tool") == "fetch":
                if row.get("scope_hmac") == scope_hmac and row.get("principal") == principal:
                    key = row.get("receipt_id")
                    if not _digest(key) or key in reads:
                        raise ValueError("invalid native fetch receipt")
                    reads[key] = row
                    positions[key] = index
                continue
            if row.get("tool") != "search":
                continue
            if row.get("scope_hmac") != scope_hmac or row.get("principal") != principal:
                continue
            receipt_id = row.get("receipt_id")
            evidence = row.get("evidence_hmacs")
            if (not _digest(receipt_id) or receipt_id in queries or
                    not isinstance(evidence, list) or not all(_digest(v) for v in evidence) or
                    len(set(evidence)) != len(evidence)):
                raise ValueError("invalid scoped query provenance")
            if ((row.get("outcome") == "hit" and not evidence) or
                    (row.get("outcome") == "miss" and evidence)):
                raise ValueError("query outcome contradicts retrieval evidence")
            queries[receipt_id] = row
            positions[receipt_id] = index
        elif schema == "z0int.memory_use_outcome.v1":
            receipt_id = row.get("receipt_id")
            query = queries.get(receipt_id)
            if query is None:
                # Other workstreams may share this audit, but a join targeting
                # one of our queries must never borrow a foreign scope.
                if row.get("scope_hmac") == scope_hmac and row.get("principal") == principal:
                    raise ValueError("native join has no earlier scoped query")
                continue
            if row.get("scope_hmac") != scope_hmac or row.get("principal") != principal:
                raise ValueError("native join scope mismatch")
            cited = row.get("cited_evidence_hmacs")
            if (query.get("outcome") not in ("hit", "miss") or
                    not _native(row.get("decision")) or
                    not isinstance(cited, list) or not all(_digest(v) for v in cited) or
                    cited != sorted(set(cited)) or
                    not set(cited) <= set(query["evidence_hmacs"])):
                raise ValueError("native join provenance mismatch")
            prove_read(row, "decision", query, "assistant")
            expected = ("miss" if query["outcome"] == "miss" else
                        "hit_used" if cited else "hit_ignored")
            if row.get("outcome") == "user_restated":
                restated = row.get("restatement")
                decision = row["decision"]
                if (row.get("prior_outcome") != expected or not _native(restated) or
                        restated["session_hmac"] != decision["session_hmac"] or
                        restated["identity_hmac"] == decision["identity_hmac"] or
                        restated["ordinal"] <= decision["ordinal"]):
                    raise ValueError("native user restatement provenance mismatch")
                prove_read(row, "restatement", query, "user")
            elif row.get("outcome") != expected or "restatement" in row:
                raise ValueError("native outcome does not follow citation evidence")
            previous = joins.get(receipt_id)
            if previous is not None and previous != row:
                if (previous.get("outcome") == "user_restated" or
                        row.get("outcome") != "user_restated" or
                        previous["decision"] != row["decision"] or
                        previous["cited_evidence_hmacs"] != cited):
                    raise ValueError("conflicting native decision join")
            joins[receipt_id] = row
        else:
            raise ValueError("unsupported outcome audit schema")
    return [{"receipt_id": key, "scope_hmac": scope_hmac, "principal": principal,
             "retrieval": query["outcome"],
             "outcome": joins[key]["outcome"] if key in joins else None,
             **({"native_join": joins[key]} if key in joins else {})}
            for key, query in queries.items()]
