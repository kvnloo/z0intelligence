"""Read-only ctx coding-history evidence capability.

ctx remains a specialized external retrieval system. This adapter converts its
already-normalized local history into z0 EvidenceRef/EventIdentity objects; it
never imports, refreshes, mutates memory, or grants action authority.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import time
from dataclasses import dataclass
from typing import Any, Callable

from z0int.context_resolve import EvidenceRef
from z0int.memory_contract import EventIdentity

CAPABILITY_ID = "ctx.history.v1"
SEARCH_SCHEMA_VERSION = 2
_ALLOWED_BACKENDS = {"lexical", "semantic", "hybrid"}


class CtxHistoryError(RuntimeError):
    """Base error for the ctx evidence adapter."""


class CtxUnavailable(CtxHistoryError):
    """The ctx CLI is not installed or cannot be executed."""


class CtxCommandError(CtxHistoryError):
    """A read-only ctx command failed."""


class CtxProtocolError(CtxHistoryError):
    """ctx returned an unexpected machine-readable contract."""


@dataclass(frozen=True)
class CtxSearchEvidence:
    query: str
    evidence: tuple[EvidenceRef, ...]
    generation_id: str
    requested_mode: str
    effective_mode: str
    returned: int
    more_available: bool
    latency_ms: float
    capability_id: str = CAPABILITY_ID

    def to_dict(self) -> dict[str, Any]:
        return {
            "capability_id": self.capability_id,
            "query": self.query,
            "generation_id": self.generation_id,
            "requested_mode": self.requested_mode,
            "effective_mode": self.effective_mode,
            "returned": self.returned,
            "more_available": self.more_available,
            "latency_ms": self.latency_ms,
            "evidence": [e.to_dict() for e in self.evidence],
        }


@dataclass(frozen=True)
class CtxEventHydration:
    event: dict[str, Any]
    identity: EventIdentity
    window_events: tuple[dict[str, Any], ...]
    latency_ms: float
    capability_id: str = CAPABILITY_ID


def _canonical_hash(value: Any) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return "sha256:" + hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _bounded_error(text: str | None, limit: int = 400) -> str:
    return (text or "").strip().replace("\n", " ")[:limit]


class CtxHistoryCapability:
    """Read-only adapter over the local ctx CLI.

    Search always uses ``--refresh off`` so a z0 evidence request cannot wake or
    mutate ctx maintenance state. Exact event hydration uses ``ctx show event``,
    which ctx documents as a read of the active verified Core generation.
    """

    def __init__(
        self,
        *,
        binary: str | None = None,
        runner: Callable[..., Any] | None = None,
    ) -> None:
        self.binary = binary if binary is not None else shutil.which("ctx")
        self._runner = runner or subprocess.run

    @property
    def available(self) -> bool:
        return bool(self.binary)

    def _run_json(self, args: list[str], *, timeout_s: float) -> tuple[dict[str, Any], float]:
        if not self.binary:
            raise CtxUnavailable("ctx CLI not found on PATH")
        started = time.perf_counter()
        try:
            proc = self._runner(
                [self.binary, *args],
                capture_output=True,
                text=True,
                timeout=timeout_s,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise CtxCommandError(f"ctx command timed out after {timeout_s:g}s") from exc
        except OSError as exc:
            raise CtxUnavailable(f"ctx CLI could not be executed: {exc}") from exc
        latency_ms = (time.perf_counter() - started) * 1000.0
        if int(proc.returncode) != 0:
            detail = _bounded_error(getattr(proc, "stderr", None)) or "no stderr"
            raise CtxCommandError(f"ctx exited {proc.returncode}: {detail}")
        stdout = str(getattr(proc, "stdout", "") or "")
        try:
            payload = json.loads(stdout)
        except json.JSONDecodeError as exc:
            raise CtxProtocolError("ctx returned invalid JSON") from exc
        if not isinstance(payload, dict):
            raise CtxProtocolError("ctx JSON response must be an object")
        return payload, latency_ms

    def search(
        self,
        query: str,
        *,
        limit: int = 5,
        backend: str = "lexical",
        provider: str | None = None,
        workspace: str | None = None,
        file: str | None = None,
        allow_semantic: bool = False,
        timeout_s: float = 8.0,
    ) -> CtxSearchEvidence:
        query = str(query).strip()
        if not query:
            raise ValueError("query must be non-empty")
        if backend not in _ALLOWED_BACKENDS:
            raise ValueError(f"unsupported ctx backend: {backend}")
        if backend != "lexical" and not allow_semantic:
            raise ValueError(
                "ctx semantic/hybrid retrieval requires explicit allow_semantic=True"
            )
        if not 1 <= int(limit) <= 100:
            raise ValueError("limit must be between 1 and 100")

        args = [
            "search",
            "--format=json",
            "--refresh",
            "off",
            "--limit",
            str(int(limit)),
            "--backend",
            backend,
        ]
        if provider:
            args.extend(["--provider", str(provider)])
        if workspace:
            args.extend(["--workspace", str(workspace)])
        if file:
            args.extend(["--file", str(file)])
        args.extend(["--", query])

        payload, latency_ms = self._run_json(args, timeout_s=timeout_s)
        if payload.get("schema_version") != SEARCH_SCHEMA_VERSION:
            raise CtxProtocolError(
                f"unsupported ctx search schema: {payload.get('schema_version')!r}"
            )
        if payload.get("payload_type") != "search_results":
            raise CtxProtocolError(
                f"unexpected ctx search payload_type: {payload.get('payload_type')!r}"
            )
        retrieval = payload.get("retrieval")
        if not isinstance(retrieval, dict):
            raise CtxProtocolError("ctx search response missing retrieval metadata")
        generation_id = str(retrieval.get("generation_id") or "").strip()
        if not generation_id:
            raise CtxProtocolError("ctx search response missing Core generation_id")
        requested_mode = str(retrieval.get("requested_mode") or backend)
        effective_mode = str(retrieval.get("effective_mode") or requested_mode)
        freshness = payload.get("freshness")
        if not isinstance(freshness, dict) or freshness.get("mode") != "off":
            raise CtxProtocolError("ctx search did not confirm refresh=off")
        generated_at = str(payload.get("generated_at") or "").strip()
        if not generated_at:
            raise CtxProtocolError("ctx search response missing generated_at")
        raw_results = payload.get("results")
        if not isinstance(raw_results, list):
            raise CtxProtocolError("ctx search response missing results[]")

        evidence: list[EvidenceRef] = []
        for hit in raw_results:
            if not isinstance(hit, dict):
                raise CtxProtocolError("ctx search result must be an object")
            event_id = str(hit.get("ctx_event_id") or "").strip()
            session_id = str(hit.get("ctx_session_id") or "").strip()
            if not event_id and not session_id:
                raise CtxProtocolError("ctx search hit has no stable event/session identity")
            locator = f"ctx:event:{event_id}" if event_id else f"ctx:session:{session_id}"
            source_id = f"ctx:event:{event_id}" if event_id else f"ctx:session:{session_id}"
            provider_name = str(hit.get("provider") or "unknown")
            result_scope = str(hit.get("result_scope") or "unknown")
            rank = hit.get("rank")
            note_parts = [
                f"provider={provider_name}",
                f"scope={result_scope}",
                f"retrieval={effective_mode}",
            ]
            if session_id:
                note_parts.append(f"session={session_id}")
            if rank is not None:
                note_parts.append(f"rank={rank}")
            evidence.append(
                EvidenceRef(
                    source_id=source_id,
                    source_version=f"ctx-core:{generation_id}",
                    locator=locator,
                    trust_class="conversation",
                    observed_at=generated_at,
                    excerpt=str(hit.get("snippet") or "")[:400] or None,
                    note="; ".join(note_parts),
                )
            )

        window = payload.get("result_window")
        if window is None:
            window = {}
        if not isinstance(window, dict):
            raise CtxProtocolError("ctx result_window must be an object")
        returned = int(window.get("returned", len(evidence)))
        if returned != len(evidence):
            raise CtxProtocolError("ctx returned count does not match results[]")
        more_available = bool(window.get("more_available", False))
        return CtxSearchEvidence(
            query=str(payload.get("query") or query),
            evidence=tuple(evidence),
            generation_id=generation_id,
            requested_mode=requested_mode,
            effective_mode=effective_mode,
            returned=returned,
            more_available=more_available,
            latency_ms=latency_ms,
        )

    def show_event(
        self,
        event_id: str,
        *,
        window: int = 0,
        timeout_s: float = 8.0,
    ) -> CtxEventHydration:
        event_id = str(event_id).strip()
        if not event_id:
            raise ValueError("event_id must be non-empty")
        if not 0 <= int(window) <= 100:
            raise ValueError("window must be between 0 and 100")
        args = ["show", "event", event_id, "--format", "json"]
        if window:
            args.extend(["--window", str(int(window))])
        payload, latency_ms = self._run_json(args, timeout_s=timeout_s)
        if payload.get("target") not in (None, "event"):
            raise CtxProtocolError(f"unexpected ctx show target: {payload.get('target')!r}")
        event = payload.get("event")
        if not isinstance(event, dict):
            raise CtxProtocolError("ctx show event response missing event object")
        actual_id = str(event.get("ctx_event_id") or "").strip()
        session_id = str(event.get("ctx_session_id") or "").strip()
        if actual_id != event_id:
            raise CtxProtocolError("ctx show event returned a different event identity")
        if not session_id:
            raise CtxProtocolError("ctx show event missing ctx_session_id")
        provider = str(event.get("provider") or "unknown")
        identity = EventIdentity.from_source(
            source_system=f"ctx:{provider}",
            source_session=session_id,
            source_event_id=actual_id,
            payload_hash=_canonical_hash(event),
        )
        raw_window = payload.get("events") or []
        if not isinstance(raw_window, list) or any(not isinstance(x, dict) for x in raw_window):
            raise CtxProtocolError("ctx show event window must be a list of event objects")
        return CtxEventHydration(
            event=dict(event),
            identity=identity,
            window_events=tuple(dict(x) for x in raw_window),
            latency_ms=latency_ms,
        )
