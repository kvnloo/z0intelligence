"""Importable AODL/HOTL contract validation."""

from .validator import (
    Issue,
    WIRE_SPEC,
    validate,
    validate_document,
    validate_document_messages,
    validate_or_raise,
    spec_revision,
)

__all__ = [
    "Issue",
    "WIRE_SPEC",
    "validate",
    "validate_document",
    "validate_document_messages",
    "validate_or_raise",
    "spec_revision",
]
