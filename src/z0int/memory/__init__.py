"""Existing admitted EventLog and scoped claim capture, without new storage.

OptMem temporal projection and read-only source retrieval remain separate.
No host adapters or live configuration are activated by importing this package.
"""
from .event_log import (
    EventIdentityConflict, EventLog, EventLogCorruption, MemoryEvent,
    SourceIngestDisabled, source_ingest_enabled,
)

__all__ = [
    "EventIdentityConflict", "EventLog", "EventLogCorruption", "MemoryEvent",
    "SourceIngestDisabled", "source_ingest_enabled",
]
