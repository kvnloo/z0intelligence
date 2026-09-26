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
from .candidates import (
    QUALITY_CLASSES,
    QUALITY_TIER_FLOOR,
    CapabilityProfile,
    CapabilityRequirement,
    CandidateInventory,
    CandidateModel,
    CandidateRungBackend,
    build_inventory,
    candidates_from_local_manifest,
    candidates_from_pi_ai_catalog,
    filter_candidates,
    inventory_from_environment,
    load_pi_ai_catalog,
    rank_candidates,
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
from .receipts import (
    CognitionReceipt,
    CostState,
    ExecutionOutcome,
    LatencyState,
    QuotaState,
    TokenState,
    receipt_from_dict,
)
from .surface import (
    DecisionSurface,
    SurfaceDecision,
    SurfaceRequest,
    SurfaceThresholds,
    uncertainty_of,
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
    "QUALITY_CLASSES",
    "QUALITY_TIER_FLOOR",
    "ROLES",
    "TIERS",
    "ActionCandidate",
    "ActionGraph",
    "CapabilityProfile",
    "CapabilityRequirement",
    "CandidateInventory",
    "CandidateModel",
    "CandidateRungBackend",
    "CascadeContext",
    "CascadeOutcome",
    "CognitionCascade",
    "CognitionReceipt",
    "CostState",
    "DecisionSurface",
    "Eliminated",
    "EscalationDecision",
    "EscalationPolicy",
    "EscalationSignals",
    "EscalationThresholds",
    "ExecutionOutcome",
    "LatencyState",
    "LegalActionSet",
    "LocalCognitionManifest",
    "LocalModelRegistry",
    "Measurement",
    "ModelCapability",
    "QuotaState",
    "Rule",
    "ServingEndpoint",
    "ShadowPayloadError",
    "ShadowSpec",
    "SourceClaim",
    "SurfaceDecision",
    "SurfaceRequest",
    "SurfaceThresholds",
    "TokenState",
    "append_shadow_receipt",
    "assert_selection_is_evidence_based",
    "build_inventory",
    "build_shadow_plan",
    "candidates_from_local_manifest",
    "candidates_from_pi_ai_catalog",
    "compile_actions",
    "entropy_of",
    "filter_candidates",
    "inventory_from_environment",
    "load_local_cognition",
    "load_pi_ai_catalog",
    "load_serving",
    "margin_of",
    "mark_executed",
    "rank_candidates",
    "receipt_from_dict",
    "run_shadow",
    "shadow_receipt_path",
    "uncertainty_of",
]
