"""Read-only GBrain MEMORY_VERBS v1 bridge.

This is the first z0intelligence × GBrain × AODL vertical slice:

GBrain -> provenance-preserving ContextPacket -> AODL projection -> shadow candidate.

The bridge is deliberately read-only. It never calls remember/forget/synthesize,
never widens GBrain visibility with include_private, and never turns a memory
hit into execution authority.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from .context_resolve import ContextPacket, EvidenceRef, InformationNeed, project_to_aodl_fields

SCHEMA = "z0int.gbrain_shadow.v1"
GBRAIN_PROTOCOL_VERSION = 1
READ_VERBS = frozenset({"context_pack", "delta"})


class GBrainProtocolError(RuntimeError):
    """The GBrain command completed but did not return a usable protocol result."""


def _now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _hash(value: Any, n: int = 16) -> str:
    raw = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:n]


def resolve_gbrain_binary() -> str | None:
    """Mirror GBrain's documented resolution preference."""
    explicit = os.environ.get("GBRAIN_BIN")
    if explicit:
        return explicit
    preferred = Path.home() / ".bun" / "bin" / "gbrain"
    if preferred.is_file():
        return str(preferred)
    return shutil.which("gbrain")


class GBrainClient:
    """Small CLI transport for the world-only ambient MEMORY_VERBS v1 surface.\n\n    The trusted-local CLI can return unredacted data from ``recall``. Until a\n    remote/scoped transport is wired, this adapter therefore exposes only the\n    two verbs whose protocol contract is world-only by default: ``context_pack``\n    and ``delta``.\n    """

    def __init__(
        self,
        *,
        binary: str | None = None,
        source: str = "default",
        timeout_s: float = 2.0,
        runner: Callable[..., Any] | None = None,
    ) -> None:
        self.binary = binary or resolve_gbrain_binary()
        self.source = source
        self.timeout_s = timeout_s
        self._runner = runner or subprocess.run

    def call(self, verb: str, params: Mapping[str, Any]) -> dict[str, Any]:
        if verb not in READ_VERBS:
            raise ValueError(f"gbrain bridge is read-only; unsupported verb {verb!r}")
        if params.get("include_private"):
            raise ValueError("gbrain bridge never widens visibility with include_private")
        if not self.binary:
            raise GBrainProtocolError("gbrain executable not found")

        payload = json.dumps(dict(params), separators=(",", ":"), ensure_ascii=False)
        try:
            proc = self._runner(
                [self.binary, "call", "--source", self.source, verb, payload],
                capture_output=True,
                text=True,
                timeout=self.timeout_s,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise GBrainProtocolError(f"gbrain {verb} failed: {type(exc).__name__}") from exc

        if proc.returncode != 0:
            detail = (proc.stderr or "").strip()[-400:]
            raise GBrainProtocolError(f"gbrain {verb} exited {proc.returncode}: {detail}")
        try:
            out = json.loads((proc.stdout or "").strip())
        except json.JSONDecodeError as exc:
            raise GBrainProtocolError(f"gbrain {verb} returned non-JSON output") from exc
        if not isinstance(out, dict):
            raise GBrainProtocolError(f"gbrain {verb} returned a non-object response")
        if out.get("error"):
            raise GBrainProtocolError(f"gbrain {verb} error: {out.get('error')}: {out.get('message', '')}")
        version = out.get("protocol_version")
        if version is not None and version != GBRAIN_PROTOCOL_VERSION:
            raise GBrainProtocolError(
                f"unsupported GBrain MEMORY_VERBS protocol_version {version!r}; expected 1"
            )
        return out

    def context_pack(
        self,
        entities: Sequence[str] | str,
        *,
        budget_tokens: int = 1200,
        session_id: str | None = None,
    ) -> dict[str, Any]:
        names = entities if isinstance(entities, str) else ",".join(x for x in entities if x)
        params: dict[str, Any] = {"entities": names, "budget_tokens": int(budget_tokens)}
        if session_id:
            params["session_id"] = session_id
        return self.call("context_pack", params)

    def delta(
        self,
        *,
        session_id: str | None = None,
        since: str | None = None,
        since_slug: str | None = None,
        entities: Sequence[str] | str | None = None,
        budget_tokens: int = 1200,
    ) -> dict[str, Any]:
        if not session_id and not since:
            raise ValueError("delta requires session_id or since")
        if since_slug and not since:
            raise ValueError("since_slug requires since")
        params: dict[str, Any] = {"budget_tokens": int(budget_tokens)}
        if session_id:
            params["session_id"] = session_id
        if since:
            params["since"] = since
        if since_slug:
            params["since_slug"] = since_slug
        if entities:
            params["entities"] = entities if isinstance(entities, str) else ",".join(entities)
        return self.call("delta", params)


def _excerpt(value: Any, limit: int = 320) -> str:
    if isinstance(value, str):
        text = value
    else:
        text = json.dumps(value, sort_keys=True, ensure_ascii=False, default=str)
    text = " ".join(text.split())
    return text[:limit]


def _item_locator(source: str, kind: str, item: Mapping[str, Any]) -> str:
    entity = item.get("entity")
    entity_slug = entity.get("slug") if isinstance(entity, Mapping) else None
    ident = item.get("fact_id") or item.get("loop_id") or item.get("slug") or item.get("id") or entity_slug
    if not ident:
        ident = _hash(item)
    return f"gbrain://{source}/{kind}/{ident}"


def _observed_at(item: Mapping[str, Any]) -> str:
    for key in ("updated_at", "created_at", "last_timeline_date", "due"):
        value = item.get(key)
        if isinstance(value, str) and value:
            return value
    return _now_iso()


def response_to_context_packet(
    response: Mapping[str, Any],
    *,
    verb: str,
    source: str = "default",
) -> ContextPacket:
    """Project a GBrain read receipt into z0int's existing evidence surface."""
    if verb not in READ_VERBS:
        raise ValueError(f"unsupported GBrain read verb {verb!r}")

    refs: list[EvidenceRef] = []
    groups = (
        ("cards", "entity"),
        ("open_threads", "open-thread"),
        ("threads", "open-thread"),
        ("facts", "fact"),
        ("pages", "page"),
    )
    seen: set[str] = set()
    for key, kind in groups:
        rows = response.get(key) or []
        if not isinstance(rows, list):
            continue
        for raw in rows:
            if not isinstance(raw, Mapping):
                continue
            locator = _item_locator(source, kind, raw)
            if locator in seen:
                continue
            seen.add(locator)
            refs.append(
                EvidenceRef(
                    source_id=f"gbrain:{source}",
                    source_version=f"memory-verbs-v{response.get('protocol_version', GBRAIN_PROTOCOL_VERSION)}:{_hash(raw)}",
                    locator=locator,
                    trust_class="derived_memory",
                    observed_at=_observed_at(raw),
                    excerpt=_excerpt(raw),
                    note=f"GBrain {verb}; read-only memory evidence, not authority",
                )
            )

    if not refs and isinstance(response.get("text"), str) and response.get("text"):
        refs.append(
            EvidenceRef(
                source_id=f"gbrain:{source}",
                source_version=f"memory-verbs-v{response.get('protocol_version', GBRAIN_PROTOCOL_VERSION)}:{_hash(response.get('text'))}",
                locator=f"gbrain://{source}/{verb}/envelope",
                trust_class="derived_memory",
                observed_at=_now_iso(),
                excerpt=_excerpt(response["text"]),
                note=f"GBrain {verb} rendered envelope; read-only memory evidence, not authority",
            )
        )

    measurements = {
        "source": "gbrain",
        "verb": verb,
        "protocol_version": response.get("protocol_version", GBRAIN_PROTOCOL_VERSION),
        "budget_tokens": response.get("budget_tokens"),
        "budget_used": response.get("budget_used"),
        "dropped_count": response.get("dropped_count"),
        "has_more": response.get("has_more"),
        "since": response.get("since"),
        "next_cursor": response.get("next_cursor"),
        "degraded_reason": response.get("degraded_reason"),
    }
    packet = ContextPacket(
        task_id=f"gbrain-{verb}-{_hash(response, 20)}",
        needs=[
            InformationNeed(
                id=f"gbrain.{verb}",
                description=f"GBrain {verb} memory context",
                kind="memory",
            )
        ],
        evidence=refs,
        measurements=measurements,
    )
    packet.aodl_projection = project_to_aodl_fields(packet)
    return packet


def _aodl_ref(aodl_doc: Mapping[str, Any] | None) -> dict[str, Any] | None:
    if not aodl_doc:
        return None
    out = {
        "graph_id": aodl_doc.get("graphId"),
        "revision": aodl_doc.get("revision"),
        "source_hash": (aodl_doc.get("provenance") or {}).get("sourceHash"),
    }
    try:
        from aodl_contract import semantic_fingerprint  # type: ignore

        out["semantic_fingerprint"] = semantic_fingerprint(dict(aodl_doc))
    except Exception:
        out["semantic_fingerprint"] = None
    return out


def build_shadow_candidate(
    response: Mapping[str, Any],
    *,
    verb: str,
    source: str = "default",
    aodl_doc: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Create a non-authoritative candidate for z0int shadow evaluation.

    This baseline intentionally never returns ACT or SURFACE. Open threads are
    worth preparing around; other deltas are observations; empty deltas are ignored.
    Learned policy can later compete against this baseline without changing AODL.
    """
    packet = response_to_context_packet(response, verb=verb, source=source)
    thread_count = sum(
        len(response.get(k) or []) for k in ("open_threads", "threads")
        if isinstance(response.get(k) or [], list)
    )
    change_count = sum(
        len(response.get(k) or []) for k in ("pages", "facts", "cards")
        if isinstance(response.get(k) or [], list)
    )
    incomplete = bool(
        response.get("degraded_reason")
        or response.get("has_more")
        or int(response.get("dropped_count") or 0) > 0
    )
    if thread_count:
        attention_action = "PREPARE"
        reason_codes = ["open_thread"]
    elif change_count or packet.evidence:
        attention_action = "OBSERVE"
        reason_codes = ["memory_change"]
    elif incomplete:
        # Partial delivery is not evidence of "nothing changed". Preserve the
        # uncertainty in shadow state so a learned policy cannot train on a
        # false negative created by budget/degradation.
        attention_action = "OBSERVE"
        reason_codes = ["incomplete_memory"]
    else:
        attention_action = "IGNORE"
        reason_codes = ["no_change"]

    aodl_ref = _aodl_ref(aodl_doc)
    candidate_id = "gb:" + _hash(
        {
            "source": source,
            "verb": verb,
            "context_packet_id": packet.task_id,
            "aodl": aodl_ref,
            "attention_action": attention_action,
        },
        20,
    )
    return {
        "schema": SCHEMA,
        "candidate_id": candidate_id,
        "traffic_eligible": False,
        "shadow_only": True,
        "source": {"kind": "gbrain", "source_id": source, "verb": verb},
        "context_packet": packet.to_dict(),
        "aodl": aodl_ref,
        "attention": {
            "action": attention_action,
            "reason_codes": reason_codes,
            "thread_count": thread_count,
            "change_count": change_count,
        },
        "authority": {
            "granted": [],
            "note": "memory evidence never grants execution or interruption authority",
        },
        "receipt_extra": {
            "gbrain_candidate_id": candidate_id,
            "gbrain_shadow_action": attention_action,
            "gbrain_context_packet_id": packet.task_id,
            "gbrain_verb": verb,
        },
    }


def attach_shadow_candidate(
    receipt: Mapping[str, Any],
    candidate: Mapping[str, Any],
) -> dict[str, Any]:
    """Join a GBrain shadow candidate to an existing z0int receipt.

    The candidate remains counterfactual evidence. This helper only adds opaque
    correlation fields under extra; it never changes the receipt's chosen
    action, authority, success, or verifier result.
    """
    if candidate.get("schema") != SCHEMA or not candidate.get("shadow_only"):
        raise ValueError("expected a z0int GBrain shadow candidate")
    fields = candidate.get("receipt_extra")
    if not isinstance(fields, Mapping) or not fields.get("gbrain_candidate_id"):
        raise ValueError("shadow candidate has no receipt correlation fields")
    out = dict(receipt)
    extra = dict(out.get("extra") or {})
    extra.update({str(k): v for k, v in fields.items()})
    out["extra"] = extra
    return out
