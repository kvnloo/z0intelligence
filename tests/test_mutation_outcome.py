from __future__ import annotations

import pytest

from z0int.cognition.mutation_outcome import MutationOutcome, mutation_outcome_from_dict
from z0int.cognition.receipts import CognitionReceipt, receipt_from_dict


def test_unknown_effect_observes_without_becoming_execution_failure_retry() -> None:
    mutation = MutationOutcome(
        mutation_key="step:2:submit",
        authority_scope="session:a",
        attempted=True,
        effect="unknown",
        verification="unverified",
        retry_disposition="observe",
    )
    receipt = CognitionReceipt(
        trace_id="trace-1",
        tier="fast",
        prediction="submit",
        retries=2,  # model/provider retries are a separate fact
    ).mark_mutation_outcome(mutation)

    raw = receipt.to_dict()
    assert raw["retries"] == 2
    assert raw["mutation_outcome"]["retryDisposition"] == "observe"
    rebuilt = receipt_from_dict(raw)
    assert rebuilt.mutation_outcome == mutation


def test_durable_same_identity_resend_is_explicit() -> None:
    mutation = MutationOutcome(
        mutation_key="op-018f",
        authority_scope="device:a",
        attempted=True,
        effect="unknown",
        verification="unverified",
        retry_disposition="resend",
        idempotency="receiver-durable",
        mutation_hash="e" * 64,
        evidence_ref="receiver:operation-id-unique",
    )

    assert mutation.to_dict() == {
        "receiptKind": "mutation-outcome/v0",
        "mutationKey": "op-018f",
        "authorityScope": "device:a",
        "attempted": True,
        "effect": "unknown",
        "verification": "unverified",
        "retryDisposition": "resend",
        "idempotency": "receiver-durable",
        "mutationHash": "e" * 64,
        "evidenceRef": "receiver:operation-id-unique",
    }


@pytest.mark.parametrize(
    ("kwargs", "needle"),
    [
        (
            dict(
                mutation_key="m",
                authority_scope="s",
                attempted=True,
                effect="unknown",
                verification="unverified",
                retry_disposition="retry",
            ),
            "ambiguous mutation",
        ),
        (
            dict(
                mutation_key="m",
                authority_scope="s",
                attempted=True,
                effect="unknown",
                verification="unverified",
                retry_disposition="resend",
                mutation_hash="e" * 64,
                evidence_ref="heuristic-window",
            ),
            "receiver-durable",
        ),
        (
            dict(
                mutation_key="m",
                authority_scope="s",
                attempted=True,
                effect="unknown",
                verification="unverified",
                retry_disposition="stop",
            ),
            "ambiguous mutation",
        ),
        (
            dict(
                mutation_key="m",
                authority_scope="s",
                attempted=True,
                effect="unknown",
                verification="unverified",
                retry_disposition="escalate",
            ),
            "requires unresolved-boundary evidence",
        ),
    ],
)
def test_unsafe_ambiguous_transitions_are_rejected(kwargs: dict, needle: str) -> None:
    with pytest.raises(ValueError, match=needle):
        MutationOutcome(**kwargs)


def test_observed_verified_effect_stops_retry() -> None:
    mutation = MutationOutcome(
        mutation_key="m",
        authority_scope="session:a",
        attempted=True,
        effect="observed",
        verification="verified",
        retry_disposition="stop",
        evidence_ref="oracle:postcondition",
        effect_hash="f" * 64,
    )
    assert mutation.to_dict()["retryDisposition"] == "stop"


def test_attempted_no_effect_can_retry_only_with_evidence() -> None:
    with pytest.raises(ValueError, match="no-effect evidence"):
        MutationOutcome(
            mutation_key="m",
            authority_scope="session:a",
            attempted=True,
            effect="none",
            verification="unverified",
            retry_disposition="retry",
        )

    safe = MutationOutcome(
        mutation_key="m",
        authority_scope="session:a",
        attempted=True,
        effect="none",
        verification="unverified",
        retry_disposition="retry",
        evidence_ref="transport:failed-before-send",
    )
    assert mutation_outcome_from_dict(safe.to_dict()) == safe
