"""Local cognition plane: model capability inventory + compiled decision cascade.

Ownership (z0intelligence#20): this package owns *semantic policy* — which
already-legal action a learned controller should pick. It never owns authority:
permissions, dependency legality, budgets, retries, leases and destructive-action
gating are deterministic and live in :mod:`z0int.cognition.actions`.

Layering:

    observe -> compile (deterministic) -> tiny specialist -> JEV -> orchestrator SLM
            -> general SLM -> remote fallback

Every layer receives the *same* already-filtered :class:`LegalActionSet`, and any
tier may abstain; abstention escalates rather than guessing.
"""

from __future__ import annotations

from .actions import (
    IRREVERSIBLE_RISKS,
    ActionCandidate,
    ActionGraph,
    Eliminated,
    LegalActionSet,
    Rule,
    compile_actions,
)
from .cascade import (
    CascadeContext,
    CascadeOutcome,
    CognitionCascade,
    mark_executed,
)
from .escalation import (
    TIERS,
    EscalationDecision,
    EscalationPolicy,
    EscalationSignals,
    EscalationThresholds,
    entropy_of,
    margin_of,
)
from .manifest import (
    ROLES,
    LocalCognitionManifest,
    Measurement,
    ModelCapability,
    SourceClaim,
    assert_selection_is_evidence_based,
    load_local_cognition,
)
from .registry import LocalModelRegistry, ServingEndpoint, load_serving
from .shadow import (
    ShadowPayloadError,
    ShadowSpec,
    append_shadow_receipt,
    build_shadow_plan,
    run_shadow,
    shadow_receipt_path,
)

SCHEMA = "z0int.cognition.v1"

__all__ = [
    "SCHEMA",
    "IRREVERSIBLE_RISKS",
    "ROLES",
    "TIERS",
    "ActionCandidate",
    "ActionGraph",
    "CascadeContext",
    "CascadeOutcome",
    "CognitionCascade",
    "Eliminated",
    "EscalationDecision",
    "EscalationPolicy",
    "EscalationSignals",
    "EscalationThresholds",
    "LegalActionSet",
    "LocalCognitionManifest",
    "LocalModelRegistry",
    "Measurement",
    "ModelCapability",
    "Rule",
    "ServingEndpoint",
    "ShadowPayloadError",
    "ShadowSpec",
    "SourceClaim",
    "append_shadow_receipt",
    "assert_selection_is_evidence_based",
    "build_shadow_plan",
    "compile_actions",
    "entropy_of",
    "load_local_cognition",
    "load_serving",
    "margin_of",
    "mark_executed",
    "run_shadow",
    "shadow_receipt_path",
]
