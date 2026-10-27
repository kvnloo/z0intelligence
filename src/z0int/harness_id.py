"""Cross-harness identity fields (avoid generic omp_* naming), the canonical turn key and hook attribution."""

from __future__ import annotations

import hashlib
import os
from dataclasses import asdict, dataclass
from typing import Any, Mapping


@dataclass(frozen=True)
class HarnessIdentity:
    harness_id: str
    session_id: str | None = None
    process_id: int | None = None
    trace_id: str | None = None
    turn_id: str | None = None
    bridge_generation: int | None = None
    build_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {k: v for k, v in asdict(self).items() if v is not None}


def detect_harness_id(explicit: str | None = None) -> str:
    if explicit:
        return explicit
    env = os.environ.get("Z0INT_HARNESS_ID") or os.environ.get("HARNESS_ID")
    if env:
        return env
    # soft detect
    if os.environ.get("OMP_SESSION_ID") or os.environ.get("PI_SESSION_ID"):
        return "omp"
    if os.environ.get("HERMES_HOME") or os.environ.get("HERMES_PROFILE"):
        return "hermes"
    if os.environ.get("CLAUDECODE") == "1":
        return "claude-code"
    return "unknown"


def identity_from_bridge(
    *,
    session_id: str | None,
    process_id: int | None,
    trace_id: str | None,
    turn_id: str | None = None,
    bridge_generation: int | None = None,
    build_id: str | None = None,
    harness_id: str | None = None,
) -> HarnessIdentity:
    return HarnessIdentity(
        harness_id=detect_harness_id(harness_id),
        session_id=session_id,
        process_id=process_id,
        trace_id=trace_id,
        turn_id=turn_id,
        bridge_generation=bridge_generation,
        build_id=build_id,
    )


TURN_KEY_SCHEMA = "z0int.turn_key.v0"


def _sha(*parts: str) -> str:
    return hashlib.sha256("\0".join(parts).encode()).hexdigest()


def _hermes_turn(session_id: str, turn_id: str) -> str:
    # Hermes turn ids usually embed the session already ("<session>:<task>:<uuid>"); both spellings are one turn.
    return turn_id if turn_id.startswith(f"{session_id}:") else f"{session_id}:{turn_id}"


def turn_key(harness: str, session_id: Any, turn_id: Any) -> str:
    """Canonical cross-harness turn key (z0int#62 R1/F1): a pure function of (harness, session, turn).

    A replay, another process or another capture vehicle derives the same key; ledger position, capture time
    and the vehicle never enter it, and two harnesses never share a key.
    """
    session, turn = str(session_id or ""), str(turn_id or "")
    if harness == "hermes":
        turn = _hermes_turn(session, turn)
    return _sha(TURN_KEY_SCHEMA, harness, session, turn)[:32]


# The trace-id conventions in use (R1 section 5.3) -> how each recovers (harness, session, turn). A hashed
# convention cannot be inverted: the record's own ids are hashed again and must reproduce the legacy trace id,
# otherwise the alias is refused (None), never guessed.
TRACE_ALIASES: dict[str, dict[str, Any]] = {
    "claude-code.prompt_id": {"harness": "claude-code"},           # prompt_id is the turn id
    "hermes.session_turn": {"harness": "hermes"},                   # z0int-decisions raw "<session>:<turn>"
    "hermes.bend_sha256": {"harness": "hermes", "hashed": True},    # bend-native sha256(session \0 turn)
    "automatic.sha256": {"harness": None, "hashed": True},          # automatic.normalize sha256(parent \0 turn)
    "dsh.lineage_turn_key": {"harness": "dsh"},                     # hermes-jev-dsh lineage turn_key
    "omp.bridge_trace": {"harness": "omp"},                         # z0int-bridge per-turn trace
}


def turn_key_from_alias(convention: str, trace_id: str, *, session_id: str, turn_id: str | None = None,
                        harness: str | None = None) -> str | None:
    rule = TRACE_ALIASES.get(convention)
    harness = harness or (rule or {}).get("harness")
    if rule is None or not harness or not trace_id:
        return None
    if not rule.get("hashed"):
        return turn_key(harness, session_id, trace_id)
    if not turn_id or _sha(str(session_id), str(turn_id)) != trace_id:
        return None
    return turn_key(harness, session_id, turn_id)


def detect_hook_harness(payload: Mapping[str, Any], env: Mapping[str, str] | None = None,
                        explicit: str | None = None) -> tuple[str, dict[str, str] | None]:
    """Harness of one Claude-compatible hook event, from its payload and runner env, never the hook file location.

    Grok runs ~/.claude/settings.json hooks too (compat scan) and marks its own runs with GROK_HOOK_EVENT or
    camelCase payload keys; Codex payloads carry ``turn_id``; Claude Code payloads carry ``prompt_id`` /
    ``hook_event_name``. An explicit harness wins; disagreeing evidence comes back as the conflict.
    """
    env = os.environ if env is None else env
    if env.get("GROK_HOOK_EVENT") or "hookEventName" in payload:
        detected = "grok"
    elif "hook_event_name" in payload and "turn_id" in payload:
        detected = "codex"
    elif "hook_event_name" in payload or "prompt_id" in payload:
        detected = "claude-code"
    else:
        detected = None
    if explicit:
        return explicit, ({"explicit": explicit, "detected": detected} if detected and detected != explicit else None)
    return detected or "unknown", None
