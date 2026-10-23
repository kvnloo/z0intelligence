"""Hermes native decision capture only; no host activation or observations."""
from .native_capture import HermesSessionBinding, capture_native_decision

__all__ = ["HermesSessionBinding", "capture_native_decision"]
