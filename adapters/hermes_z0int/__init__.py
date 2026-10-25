"""Thin Hermes adapter: envelope normalize, close observation, outcome join, native decision capture.

Importing this package activates no host wiring.
"""

from .adapter import close_observation, join_outcome, normalize_envelope, resolve_repository_context
from .native_capture import HermesSessionBinding, capture_native_decision

__all__ = [
    "normalize_envelope",
    "close_observation",
    "join_outcome",
    "resolve_repository_context",
    "HermesSessionBinding",
    "capture_native_decision",
]
