"""Explicit native Codex preferences admitted into the existing scoped EventLog.

This adapter is opt-in and intentionally recognizes only a tiny delegated-agent
configuration grammar. It stores source references and normalized values, never
the native message body. Callers must provide an active workstream binding from
the trusted host; model-facing input must not construct or invoke this adapter.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..memory_contract import EventIdentity, MemoryScope
from .claims import NativeUserClaimAttestation, admit_user_claim, project_claims
from .event_log import EventLog, MemoryEvent

DEFAULT_AGENTSVIEW_CLI = Path("/home/kvn/.local/bin/agentsview")
DEFAULT_AGENTSVIEW_DATA_DIR = Path("/mnt/zer0models/sft-svlm/data/agentsview")
_SUBJECT = "delegated_agent"
_PREDICATE = "configuration"
_ALLOWED_FIELDS = {"model", "effort", "subagents"}
_MAX_MESSAGE_CHARS = 64 * 1024
_DIRECT_CONFIG = re.compile(
    r"model\s*=\s*luna(?:\s*,\s*effort\s*=\s*max)?",
    re.IGNORECASE,
)
_SUBAGENT_CONFIG = re.compile(r"use\s+10\s+luna\s+subagents", re.IGNORECASE)
_AFFIRMATIVE_SUBAGENT_REQUEST = re.compile(
    r"(?:(?:can|could)\s+you|please)\s+"
    r"(?:audit|compare|inspect|investigate|iterate|review|analy[sz]e|search|summarize)\s+"
    r"[a-z0-9][a-z0-9 ,;:&/-]{0,180}"
    r"\(\s*use\s+10\s+luna\s+subagents\s*\)\s*[.!]?",
    re.IGNORECASE,
)
_NEGATED_OR_QUOTED_CONFIG = re.compile(
    r"\b(?:not|never|without|avoid|cannot|can't|don't|do\s+not)\b|[\"'“”‘’]",
    re.IGNORECASE,
)


class NativePreferenceAdmissionError(ValueError):
    """Native source, role, identity, or trusted-scope binding failed validation."""


class UnsupportedNativePreference(NativePreferenceAdmissionError):
    """Native user text is outside the deliberately narrow config grammar."""


@dataclass(frozen=True)
class NativeWorkstreamBinding:
    """Scope/session pair supplied by the trusted host for the active workstream."""

    session_id: str
    scope: MemoryScope

    def __post_init__(self) -> None:
        if (
            not isinstance(self.session_id, str)
            or not self.session_id.startswith("codex:")
            or len(self.session_id) > 512
            or self.session_id != self.session_id.strip()
            or any(character.isspace() for character in self.session_id)
        ):
            raise ValueError("binding requires the exact native Codex session ID")
        if not isinstance(self.scope, MemoryScope):
            raise TypeError("binding scope must be a MemoryScope")
        if self.scope.user is None or self.scope.project is None or self.scope.task is None:
            raise ValueError("binding requires a user/project/task-scoped workstream")


NativeMessageReader = Callable[[str, int], Mapping[str, Any]]


def agentsview_native_reader(session_id: str, ordinal: int) -> Mapping[str, Any]:
    """Read one exact user message from the configured local AgentsView archive."""
    env = os.environ.copy()
    if not env.get("AGENTSVIEW_DATA_DIR"):
        env["AGENTSVIEW_DATA_DIR"] = str(DEFAULT_AGENTSVIEW_DATA_DIR)
    command = [
        str(DEFAULT_AGENTSVIEW_CLI),
        "session",
        "messages",
        session_id,
        "--from",
        str(ordinal),
        "--limit",
        "1",
        "--role",
        "user",
        "--json",
    ]
    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
            env=env,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise NativePreferenceAdmissionError(
            f"AgentsView native message read failed: {type(exc).__name__}"
        ) from exc
    if result.returncode != 0:
        raise NativePreferenceAdmissionError(
            f"AgentsView session messages exited {result.returncode}"
        )
    try:
        data = json.loads(result.stdout)
    except (TypeError, json.JSONDecodeError) as exc:
        raise NativePreferenceAdmissionError("AgentsView returned invalid JSON") from exc
    messages = data.get("messages") if isinstance(data, dict) else None
    if not isinstance(messages, list):
        raise NativePreferenceAdmissionError("AgentsView response has no messages list")
    exact = [
        row
        for row in messages
        if isinstance(row, dict)
        and row.get("session_id") == session_id
        and row.get("ordinal") == ordinal
        and row.get("role") == "user"
    ]
    if len(exact) != 1:
        raise NativePreferenceAdmissionError(
            "AgentsView did not return exactly one matching native user message"
        )
    return exact[0]


def _parse_config_delta(content: str) -> dict[str, Any]:
    text = content.strip()
    folded = text.casefold()
    if folded == "luna max**" or folded == "luna max":
        return {"model": "luna", "effort": "max"}
    match = _DIRECT_CONFIG.fullmatch(text)
    if match:
        normalized = re.sub(r"\s+", "", folded)
        if ",effort=max" in normalized:
            return {"model": "luna", "effort": "max"}
        return {"model": "luna"}
    if _SUBAGENT_CONFIG.fullmatch(text):
        return {"model": "luna", "subagents": 10}
    if (
        _NEGATED_OR_QUOTED_CONFIG.search(text) is None
        and _AFFIRMATIVE_SUBAGENT_REQUEST.fullmatch(text)
    ):
        return {"model": "luna", "subagents": 10}
    raise UnsupportedNativePreference("native message is outside the delegated-agent config grammar")


def _scope_from_native(value: Any) -> MemoryScope | None:
    if isinstance(value, MemoryScope):
        return value
    if not isinstance(value, Mapping) or set(value) - {"level", "user", "project", "repo", "task"}:
        return None
    try:
        scope = MemoryScope(**{key: value.get(key) for key in ("user", "project", "repo", "task")})
    except (TypeError, ValueError):
        return None
    if "level" in value and value.get("level") != scope.level:
        return None
    return scope


def _validated_message(
    raw: Mapping[str, Any],
    *,
    session_id: str,
    ordinal: int,
    binding: NativeWorkstreamBinding,
) -> tuple[str, str, dict[str, Any], str]:
    if not isinstance(raw, Mapping):
        raise NativePreferenceAdmissionError("native reader did not return a message mapping")
    raw_ordinal = raw.get("ordinal")
    if (
        raw.get("session_id") != session_id
        or isinstance(raw_ordinal, bool)
        or not isinstance(raw_ordinal, int)
        or raw_ordinal != ordinal
    ):
        raise NativePreferenceAdmissionError("native message does not match the requested session ordinal")
    if raw.get("role") != "user":
        raise NativePreferenceAdmissionError("only an exact native user message may support a preference")
    content = raw.get("content")
    if not isinstance(content, str) or not content or len(content) > _MAX_MESSAGE_CHARS:
        raise NativePreferenceAdmissionError("native user message content is missing or exceeds the size bound")
    if "identity" in raw or "event_uid" in raw:
        raise NativePreferenceAdmissionError("event identity is derived internally from native source fields")
    source_scope = raw.get("scope")
    if source_scope is not None and _scope_from_native(source_scope) != binding.scope:
        raise NativePreferenceAdmissionError("native workstream scope does not match the trusted host binding")
    observed_at = raw.get("timestamp")
    if not isinstance(observed_at, str) or not observed_at.strip() or len(observed_at) > 64:
        raise NativePreferenceAdmissionError("native message timestamp is missing or invalid")
    payload_hash = "sha256:" + hashlib.sha256(content.encode("utf-8")).hexdigest()
    supplied_hash = raw.get("payload_hash")
    if supplied_hash is not None and supplied_hash != payload_hash:
        raise NativePreferenceAdmissionError("native reader payload hash does not match the exact message")
    return content, observed_at, _parse_config_delta(content), payload_hash


def _validate_config(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict) or not value or set(value) - _ALLOWED_FIELDS:
        raise NativePreferenceAdmissionError("prior config is outside the supported normalized schema")
    if value.get("model") != "luna":
        raise NativePreferenceAdmissionError("prior config model is not supported by this adapter")
    if "effort" in value and value["effort"] != "max":
        raise NativePreferenceAdmissionError("prior config effort is not supported by this adapter")
    if "subagents" in value and (
        isinstance(value["subagents"], bool)
        or not isinstance(value["subagents"], int)
        or value["subagents"] != 10
    ):
        raise NativePreferenceAdmissionError("prior config subagent count is not supported by this adapter")
    return dict(value)


def _correction_base(
    correction_of: str,
    *,
    binding: NativeWorkstreamBinding,
    ledger_root: str | Path | None,
) -> tuple[dict[str, Any], str, int, str]:
    projection = project_claims(
        binding.scope,
        subject=_SUBJECT,
        predicate=_PREDICATE,
        ledger_root=ledger_root,
    )
    log = EventLog(ledger_root, read_only=True)
    events = list(log.iter_events()) if log.events_path.is_file() else []
    event_by_id = {event.event_id: event for event in events}
    for row in projection.measurements.get("claim_history", []):
        if row.get("claim_id") != correction_of:
            continue
        if row.get("scope") != binding.scope.to_dict() or row.get("origin_trust") != "explicit_user":
            raise NativePreferenceAdmissionError(
                "correction target is not an explicit user claim in the bound workstream"
            )
        if row.get("current") is not True:
            raise NativePreferenceAdmissionError("correction target has already been superseded")
        claim_event_id = row.get("event_id")
        claim_event = event_by_id.get(claim_event_id) if type(claim_event_id) is int else None
        if (
            claim_event is None
            or claim_event.event_type != "memory.claim"
            or not isinstance(claim_event.payload, dict)
            or claim_event.payload.get("claim_id") != correction_of
        ):
            raise NativePreferenceAdmissionError("correction target lacks a validated source event")
        evidence_uids = set(str(uid) for uid in row.get("evidence_event_uids") or ())
        source_events: list[tuple[str, int]] = []
        for parent_id in claim_event.parent_event_ids:
            reference = event_by_id.get(parent_id)
            if (
                reference is None
                or reference.event_type != "source.reference"
                or not isinstance(reference.payload, dict)
                or reference.payload.get("native_source_validated") is not True
                or reference.payload.get("role") != "user"
                or reference.payload.get("scope") != binding.scope.to_dict()
            ):
                continue
            identity = reference.event_identity()
            if (
                identity is None
                or identity.source_system != "codex"
                or identity.event_uid not in evidence_uids
                or identity.source_seq is None
                or reference.payload.get("locator")
                != f"agentsview:{identity.source_session}#{identity.source_seq}"
            ):
                continue
            source_events.append((identity.source_session, identity.source_seq))
        if len(source_events) != 1:
            raise NativePreferenceAdmissionError("correction target lacks one exact native source reference")
        source_session, source_seq = source_events[0]
        observed_at = row.get("observed_at")
        if not isinstance(observed_at, str) or not observed_at.strip() or len(observed_at) > 64:
            raise NativePreferenceAdmissionError("correction target has no validated source timestamp")
        return _validate_config(row.get("value")), source_session, source_seq, observed_at
    raise ValueError("correction_of must name an existing explicit claim in the bound workstream")


def admit_delegated_agent_config(
    session_id: str,
    ordinal: int,
    binding: NativeWorkstreamBinding,
    *,
    native_reader: NativeMessageReader | None = None,
    correction_of: str | None = None,
    ledger_root: str | Path | None = None,
) -> MemoryEvent:
    """Admit one exact native user config message into the scoped EventLog.

    The trusted host supplies the active ``binding``. Normalized config, role,
    source hash, and event identity are derived here from the hydrated message;
    callers cannot assert any of those values. A correction carries unchanged
    fields forward only from a visible, validated explicit-user claim.
    """
    if (
        not isinstance(session_id, str)
        or not session_id.startswith("codex:")
        or len(session_id) > 512
        or session_id != session_id.strip()
        or any(character.isspace() for character in session_id)
    ):
        raise NativePreferenceAdmissionError("session_id must be the exact native Codex session ID")
    if isinstance(ordinal, bool) or not isinstance(ordinal, int) or ordinal < 0:
        raise NativePreferenceAdmissionError("ordinal must be a nonnegative native message ordinal")
    if not isinstance(binding, NativeWorkstreamBinding):
        raise NativePreferenceAdmissionError("a trusted NativeWorkstreamBinding is required")
    if binding.session_id != session_id:
        raise NativePreferenceAdmissionError("native session does not match the trusted workstream binding")
    reader = agentsview_native_reader if native_reader is None else native_reader
    if not callable(reader):
        raise NativePreferenceAdmissionError("native_reader must be a trusted in-process callable")
    try:
        raw = reader(session_id, ordinal)
    except NativePreferenceAdmissionError:
        raise
    except Exception as exc:  # noqa: BLE001 - native source errors are admission failures
        raise NativePreferenceAdmissionError("native source hydration failed") from exc
    content, observed_at, config_delta, payload_hash = _validated_message(
        raw,
        session_id=session_id,
        ordinal=ordinal,
        binding=binding,
    )
    config = config_delta
    if correction_of is not None:
        if not isinstance(correction_of, str) or not correction_of.strip():
            raise NativePreferenceAdmissionError("correction_of must be a claim ID")
        prior_config, prior_session, prior_ordinal, _prior_observed_at = _correction_base(
            correction_of,
            binding=binding,
            ledger_root=ledger_root,
        )
        if prior_session != session_id:
            raise NativePreferenceAdmissionError(
                "corrections must remain in the same native session"
            )
        if ordinal <= prior_ordinal:
            raise NativePreferenceAdmissionError(
                "a correction must use a later source ordinal than its prior claim"
            )
        config = {**prior_config, **config_delta}
    config = _validate_config(config)
    if len(json.dumps(config, sort_keys=True, separators=(",", ":")).encode("utf-8")) > 256:
        raise NativePreferenceAdmissionError("normalized delegated-agent config exceeds the size bound")

    identity = EventIdentity.from_source(
        source_system="codex",
        source_session=session_id,
        source_seq=ordinal,
        payload_hash=payload_hash,
    )
    locator = f"agentsview:{session_id}#{ordinal}"
    attestation = NativeUserClaimAttestation(
        identity=identity,
        scope=binding.scope,
        role="user",
        subject=_SUBJECT,
        predicate=_PREDICATE,
        value=config,
        observed_at=observed_at,
    )

    def source_reader(requested_locator: str) -> Mapping[str, Any]:
        if requested_locator != locator:
            raise NativePreferenceAdmissionError("claim admission requested a different native locator")
        return {
            "role": "user",
            "content": content,
            "identity": identity.to_dict(),
            "scope": binding.scope.to_dict(),
            "admitted_claims": (attestation,),
        }

    return admit_user_claim(
        identity=identity,
        locator=locator,
        scope=binding.scope,
        subject=_SUBJECT,
        predicate=_PREDICATE,
        value=config,
        observed_at=observed_at,
        source_reader=source_reader,
        correction_of=correction_of,
        ledger_root=ledger_root,
    )
