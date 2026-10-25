"""Canonical z0 memory primitives.

Raw events are immutable source truth. Trees, search indexes, semantic memories,
and StatePackets are derived projections. No host adapters or live configuration
are activated by importing this package.
"""

from .event_log import (
    EventIdentityConflict,
    EventLog,
    EventLogCorruption,
    MemoryEvent,
    SourceIngestDisabled,
    source_ingest_enabled,
)
from .optmem_tree import CoverBudgetExceeded, OptMemTree, TreeNode

__all__ = [
    "CoverBudgetExceeded",
    "EventIdentityConflict",
    "EventLog",
    "EventLogCorruption",
    "MemoryEvent",
    "OptMemTree",
    "SourceIngestDisabled",
    "TreeNode",
    "source_ingest_enabled",
]
