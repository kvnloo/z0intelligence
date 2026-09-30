"""Canonical z0 memory primitives.

Raw events are immutable source truth. Trees, search indexes, semantic memories,
and StatePackets are derived projections.
"""

from .event_log import EventLog, EventLogCorruption, MemoryEvent

__all__ = ["EventLog", "EventLogCorruption", "MemoryEvent"]
