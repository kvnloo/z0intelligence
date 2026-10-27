"""Downstream Hermes native-source adapter; not a model-facing memory tool.

Requirements/invariants: Kevin Rajan. Reuses the existing EventLog/claims
implementation and Hermes authors' persisted messages; this adapter only binds,
reads and normalizes explicit native user decisions. Downstream adapter delta:
Hermes Agent (Nous Research).
"""

from __future__ import annotations

from contextlib import closing
from dataclasses import dataclass, field
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import re
import sqlite3
import stat
from typing import Any

from z0int.memory.claims import (
    MAX_CLAIM_LABEL_CHARS, MAX_CLAIM_VALUE_BYTES,
    NativeUserClaimAttestation, admit_user_claim,
)
from z0int.memory.event_log import MemoryEvent
from z0int.memory_contract import EventIdentity, MemoryScope

MAX_NATIVE_CONTENT_BYTES = 16_384
DECISION_SCHEMA = "z0int.hermes.native_decision.v1"


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate decision field")
        result[key] = value
    return result


def _decision(content: str) -> dict[str, Any]:
    """Accept one explicit declaration, never prose, blocks or inferred intent."""
    try:
        decision = json.loads(content, object_pairs_hook=_unique_object)
        required = {"schema", "subject", "predicate", "value"}
        if (
            not isinstance(decision, dict) or not required <= decision.keys()
            or decision.keys() - required - {"correction_of"}
            or decision["schema"] != DECISION_SCHEMA
        ):
            raise ValueError("unsupported decision schema or fields")
        for key in ("subject", "predicate"):
            label = decision[key]
            if (
                not isinstance(label, str) or label != label.strip()
                or not 0 < len(label) <= MAX_CLAIM_LABEL_CHARS or not label.isprintable()
            ):
                raise ValueError("decision labels must be bounded normalized text")
        correction = decision.get("correction_of")
        if correction is not None and (
            not isinstance(correction, str) or not re.fullmatch(r"claim_[0-9a-f]{32}", correction)
        ):
            raise ValueError("invalid decision correction_of")
        pending = [(decision, 0)]
        while pending:
            value, depth = pending.pop()
            if depth > 16:
                raise ValueError("decision nesting exceeds bounded grammar")
            children = value.values() if isinstance(value, dict) else value if isinstance(value, list) else ()
            pending.extend((child, depth + 1) for child in children)
        encoded = json.dumps(decision["value"], ensure_ascii=False, sort_keys=True,
                             separators=(",", ":"), allow_nan=False).encode("utf-8")
        if len(encoded) > MAX_CLAIM_VALUE_BYTES:
            raise ValueError("decision value exceeds bounded grammar")
    except (ValueError, RecursionError) as exc:
        raise ValueError("unsupported or unbounded native decision grammar") from exc
    return decision


def _native_id(value: Any) -> bool:
    return isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9_.:-]{1,256}", value) is not None


@dataclass(frozen=True)
class HermesSessionBinding:
    """Trusted host binding, never populated from model arguments or message text."""

    hermes_home: Path
    session_id: str
    scope: MemoryScope
    synthetic: bool = False
    attribution: str | None = None
    session_source: str = "cli"
    _database_identity: tuple[int, int] = field(init=False, repr=False)

    def __post_init__(self) -> None:
        if not isinstance(self.scope, MemoryScope) or not all(
            isinstance(value, str) and value.strip() == value and 0 < len(value) <= 160
            for value in (self.scope.user, self.scope.project, self.scope.repo, self.scope.task)
        ):
            raise ValueError("host binding requires a full typed user/project/repo/task scope")
        if not _native_id(self.session_id) or not _native_id(self.session_source):
            raise ValueError("invalid native session identity")
        if type(self.synthetic) is not bool or (self.synthetic and (
            not isinstance(self.attribution, str)
            or not 0 < len(self.attribution) <= MAX_CLAIM_LABEL_CHARS
            or self.attribution != self.attribution.strip() or not self.attribution.isprintable()
        )):
            raise ValueError("synthetic host attribution must be explicit bounded text")
        if not Path(self.hermes_home).is_absolute():
            raise ValueError("host binding requires an explicit absolute HERMES_HOME")
        try:
            home = Path(self.hermes_home).resolve(strict=True)
            database = home / "state.db"
            if database.resolve(strict=True) != database:
                raise ValueError("native database must not redirect outside the host binding")
            info = database.stat()
            if not stat.S_ISREG(info.st_mode):
                raise ValueError("native database must be a regular file")
        except OSError as exc:
            raise ValueError("native source unavailable") from exc
        object.__setattr__(self, "hermes_home", home)
        object.__setattr__(self, "_database_identity", (info.st_dev, info.st_ino))

    def _check_database(self) -> Path:
        database = self.hermes_home / "state.db"
        info = database.stat()
        if database.resolve(strict=True) != database or (info.st_dev, info.st_ino) != self._database_identity:
            raise ValueError("native database no longer matches host binding")
        return database


def capture_native_decision(
    binding: HermesSessionBinding,
    *,
    session_id: str,
    message_id: int,
    message_uid: str,
    expected_content_hash: str,
    scope: MemoryScope,
    ledger_root: str | Path,
) -> MemoryEvent:
    """Admit one exact, already persisted user input via canonical claim admission.

    The host supplies the native row identity and SHA-256 of the exact submitted
    text after persistence. No content, normalized value or role is accepted from
    a model. ``ledger_root`` is the existing EventLog root, not HERMES_HOME.
    """
    if not isinstance(binding, HermesSessionBinding):
        raise ValueError("a trusted HermesSessionBinding is required")
    if session_id != binding.session_id:
        raise ValueError("native session does not match host binding")
    if scope != binding.scope:
        raise ValueError("requested scope does not match host binding")
    if type(message_id) is not int or not 0 < message_id < 2**63 or not _native_id(message_uid):
        raise ValueError("invalid native message identity")
    if not isinstance(expected_content_hash, str) or not re.fullmatch(r"sha256:[0-9a-f]{64}", expected_content_hash):
        raise ValueError("invalid expected content hash")
    locator = f"agentsview:{session_id}#{message_id}"

    def source_reader(requested_locator: str) -> dict[str, Any]:
        if requested_locator != locator:
            raise ValueError("native locator does not match bound identity")
        try:
            database = binding._check_database()
            with closing(sqlite3.connect(database.as_uri() + "?mode=ro", uri=True, timeout=1)) as conn:
                conn.row_factory = sqlite3.Row
                conn.execute("BEGIN")  # Identity and content belong to one read snapshot.
                row = conn.execute(
                    "SELECT m.id, m.session_id, m.message_uid, m.role, m.timestamp, "
                    "m._compressed_summary, m.active, m.compacted, "
                    "m.display_kind IS NOT NULL AS has_display_kind, "
                    "m.display_metadata IS NOT NULL AS has_display_metadata, "
                    "m.tool_name IS NOT NULL AS has_tool_name, "
                    "m.tool_call_id IS NOT NULL AS has_tool_call_id, "
                    "m.tool_calls IS NOT NULL AS has_tool_calls, "
                    "CASE WHEN length(CAST(m.content AS BLOB)) <= ? THEN m.content END AS content, "
                    "s.source AS session_source, s.parent_session_id "
                    "FROM messages m JOIN sessions s ON s.id = m.session_id "
                    "WHERE m.session_id = ? AND m.id = ?",
                    (MAX_NATIVE_CONTENT_BYTES, session_id, message_id),
                ).fetchone()
                duplicates = conn.execute(
                    "SELECT id FROM messages WHERE session_id = ? AND message_uid = ? LIMIT 2",
                    (session_id, message_uid),
                ).fetchall()
                if len(duplicates) > 1:
                    raise ValueError("ambiguous native message identity")
                binding._check_database()
        except (OSError, sqlite3.Error) as exc:
            raise ValueError("native source unavailable") from exc
        if row is None:
            raise ValueError("native source not found")
        if row["session_source"] != binding.session_source or row["parent_session_id"] is not None:
            raise ValueError("native session origin does not match top-level host binding")
        if (
            row["role"] != "user" or row["_compressed_summary"] != 0
            or row["active"] != 1 or row["compacted"] != 0
            or any(row[key] for key in (
                "has_display_kind", "has_display_metadata", "has_tool_name", "has_tool_call_id", "has_tool_calls",
            ))
        ):
            raise ValueError("native source must be an active, unmarked user row")
        if row["message_uid"] != message_uid:
            raise ValueError("native message identity does not match")
        content = row["content"]
        if not isinstance(content, str):
            raise ValueError("native decision must be bounded UTF-8 text")
        content_hash = "sha256:" + hashlib.sha256(content.encode("utf-8")).hexdigest()
        if content_hash != expected_content_hash:
            raise ValueError("native content hash does not match submitted input")
        decision = _decision(content)
        value = decision["value"]
        if binding.synthetic:
            if (
                not isinstance(value, dict) or value.get("synthetic") is not True
                or value.get("attribution") != binding.attribution
            ):
                raise ValueError("synthetic decision attribution must match the host binding")
        elif isinstance(value, dict) and "synthetic" in value and value["synthetic"] is not False:
            raise ValueError("synthetic decision attribution requires a matching host binding")
        identity = EventIdentity.from_source(
            source_system="hermes", source_session=row["session_id"],
            source_event_id=row["message_uid"], source_seq=row["id"],
            payload_hash=content_hash,
        )
        timestamp = row["timestamp"]
        if type(timestamp) not in (int, float) or not math.isfinite(timestamp) or timestamp < 0:
            raise ValueError("invalid native timestamp")
        try:
            observed_at = datetime.fromtimestamp(timestamp, timezone.utc).isoformat().replace("+00:00", "Z")
        except (ValueError, OverflowError, OSError) as exc:
            raise ValueError("invalid native timestamp") from exc
        fact = NativeUserClaimAttestation(
            identity=identity, scope=binding.scope, role=row["role"],
            subject=decision["subject"], predicate=decision["predicate"],
            value=decision["value"],
            observed_at=observed_at,
        )
        return {
            "identity": identity, "scope": binding.scope, "role": row["role"],
            "content": content, "admitted_claims": (fact,),
            "correction_of": decision.get("correction_of"),
        }

    source = source_reader(locator)
    fact = source["admitted_claims"][0]
    return admit_user_claim(
        identity=fact.identity, locator=locator, scope=scope,
        subject=fact.subject, predicate=fact.predicate, value=fact.value,
        observed_at=fact.observed_at, source_reader=source_reader,
        correction_of=source["correction_of"], ledger_root=ledger_root,
    )
