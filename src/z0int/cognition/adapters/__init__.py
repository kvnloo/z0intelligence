"""Model-specific adapters. Everything model-flavoured lives under this package."""

from __future__ import annotations

from .bounded import DecisionBackendToolAdapter, JevBoundedToolBackend
from .dialects import (
    DIALECTS,
    FUNCTIONGEMMA_DIALECT,
    HAMMER_DIALECT,
    HERMES_DIALECT,
    NEMOTRON_DIALECT,
    PLAIN_JSON_DIALECT,
    QWEN_DIALECT,
    ToolDialect,
    dialect_for,
    extract_json_object,
)
from .local_slm import (
    DECISION_SCHEMA,
    LocalSLMBackend,
    ToolDecision,
    ToolDecisionBackend,
    ToolDecisionRequest,
)
from .transport import ChatOutcome, OpenAICompatTransport, ServerConfig, TransportError

__all__ = [
    "DIALECTS",
    "DECISION_SCHEMA",
    "ChatOutcome",
    "DecisionBackendToolAdapter",
    "FUNCTIONGEMMA_DIALECT",
    "HAMMER_DIALECT",
    "HERMES_DIALECT",
    "JevBoundedToolBackend",
    "LocalSLMBackend",
    "NEMOTRON_DIALECT",
    "OpenAICompatTransport",
    "PLAIN_JSON_DIALECT",
    "QWEN_DIALECT",
    "ServerConfig",
    "ToolDecision",
    "ToolDecisionBackend",
    "ToolDecisionRequest",
    "ToolDialect",
    "TransportError",
    "dialect_for",
    "extract_json_object",
]
