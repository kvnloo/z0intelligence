"""Callable z0intelligence functions.

Each function is one id, one input/output contract, one canonical receipt, and
one or more implementations the caller may choose between. This package owns no
router and imports no routing policy.
"""

from .contract import (
    ACTIONS,
    DEFAULT_ALLOW_AT,
    DEFAULT_ESCALATE_BELOW,
    FUNCTION_ID,
    PHRASINGS,
    PROPOSITION,
    BackendCapabilities,
    PhysicalCall,
    VerificationResult,
    VerifierUnavailable,
    action_of,
    is_uncertain,
    render_state,
    state_digest,
)
from .verify_evidence_sufficiency import (
    capabilities,
    implementations,
    verify,
    verify_with_escalation,
)

__all__ = [
    "ACTIONS", "DEFAULT_ALLOW_AT", "DEFAULT_ESCALATE_BELOW", "FUNCTION_ID",
    "PHRASINGS", "PROPOSITION", "BackendCapabilities", "PhysicalCall",
    "VerificationResult", "VerifierUnavailable", "action_of", "is_uncertain",
    "render_state", "state_digest", "capabilities", "implementations", "verify",
    "verify_with_escalation",
]
