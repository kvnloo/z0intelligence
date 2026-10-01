from __future__ import annotations

import pytest

from z0int.cognition.mutation_policy import MutationCapabilities, decide_mutation_outcome


def test_cua_like_ambiguous_click_observes() -> None:
    out = decide_mutation_outcome(
        mutation_key="step:2:submit",
        authority_scope="session:a",
        attempted=True,
        effect="unknown",
        capabilities=MutationCapabilities(can_observe=True),
    )
    assert out.retry_disposition == "observe"


def test_supersync_like_durable_identity_resends_same_mutation() -> None:
    out = decide_mutation_outcome(
        mutation_key="op-018f",
        authority_scope="device:a",
        attempted=True,
        effect="unknown",
        capabilities=MutationCapabilities(
            can_observe=True,
            stable_mutation_key=True,
            receiver_durable_idempotency=True,
        ),
        durable_idempotency_evidence="server:operation-id-unique",
        mutation_hash="e" * 64,
    )
    assert out.retry_disposition == "resend"
    assert out.mutation_key == "op-018f"
    assert out.mutation_hash == "e" * 64


def test_relay_sealed_frame_can_resend_under_receiver_tombstone() -> None:
    out = decide_mutation_outcome(
        mutation_key="draft:chat:turn:42",
        authority_scope="relay:slack",
        attempted=True,
        effect="unknown",
        capabilities=MutationCapabilities(
            can_observe=False,
            stable_mutation_key=True,
            receiver_durable_idempotency=True,
        ),
        durable_idempotency_evidence="connector:sealed-key-tombstone",
        mutation_hash="a" * 64,
    )
    assert out.retry_disposition == "resend"
    assert out.idempotency == "receiver-durable"


def test_wger_window_guard_does_not_promote_to_durable_resend() -> None:
    out = decide_mutation_outcome(
        mutation_key="heuristic:set",
        authority_scope="mcp:log_set",
        attempted=True,
        effect="unknown",
        capabilities=MutationCapabilities(
            can_observe=True,
            stable_mutation_key=False,
            receiver_durable_idempotency=False,
        ),
        durable_idempotency_evidence="recent-row-window",
        mutation_hash="e" * 64,
    )
    assert out.retry_disposition == "observe"
    assert out.idempotency == "none"


def test_hermes_shell_like_opaque_mutation_escalates() -> None:
    out = decide_mutation_outcome(
        mutation_key="terminal:run",
        authority_scope="session:a",
        attempted=True,
        effect="unknown",
        capabilities=MutationCapabilities(),
        unresolved_evidence="process-spawned:completion-lost",
    )
    assert out.retry_disposition == "escalate"
    assert out.evidence_ref == "process-spawned:completion-lost"


def test_provider_or_pre_effect_failure_can_retry_with_evidence() -> None:
    out = decide_mutation_outcome(
        mutation_key="provider:request",
        authority_scope="decision:rung",
        attempted=True,
        effect="none",
        capabilities=MutationCapabilities(),
        no_effect_evidence="transport:failed-before-world-dispatch",
    )
    assert out.retry_disposition == "retry"


def test_observed_verified_effect_stops() -> None:
    out = decide_mutation_outcome(
        mutation_key="op-1",
        authority_scope="device:a",
        attempted=True,
        effect="observed",
        capabilities=MutationCapabilities(),
        observed_effect_hash="f" * 64,
        verification_evidence="oracle:postcondition",
    )
    assert out.retry_disposition == "stop"
    assert out.verification == "verified"


def test_unknown_requires_an_attempt() -> None:
    with pytest.raises(ValueError):
        decide_mutation_outcome(
            mutation_key="m",
            authority_scope="s",
            attempted=False,
            effect="unknown",
            capabilities=MutationCapabilities(),
        )
