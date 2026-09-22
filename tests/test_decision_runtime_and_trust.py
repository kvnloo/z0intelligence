"""Tests for the decision-runtime plane and per-capability trust."""

from __future__ import annotations

import json

import pytest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

from z0int.backends import trust as trust_mod
from z0int.backends.lifecycle import (
    InvocationReceipt,
    structural_checkpoint_digest,
    runtime_status,
)
from z0int.backends.trust import (
    PRODUCTION_STATUSES,
    CapabilityTrust,
    assert_no_global_trust,
    may_serve,
    status_for,
    trust_for,
    trust_records,
    trust_table,
)


# --------------------------------------------------------------------------
# Per-capability trust (item 8)
# --------------------------------------------------------------------------


def test_repository_trust_manifest_is_valid_and_cited():
    records = trust_records()
    assert records, "capability_trust.json produced no records"
    for r in records:
        assert r.status in trust_mod.TRUST_STATUSES
        if r.status in ("tested_experimental", "trusted_shadow", "trusted_bounded"):
            assert r.evidence, f"{r.backend_id}/{r.capability} claims {r.status} with no evidence"


def test_same_model_is_trusted_for_one_capability_and_rejected_for_another():
    """The whole point: trust is per capability, not per model."""
    bounded = status_for("nanojev", "bounded_legal_action")
    generic = status_for("nanojev", "generic_next_action_real_traffic")

    assert bounded == "tested_experimental"
    assert generic == "rejected_for_current_checkpoint"
    assert bounded != generic


def test_unknown_capability_defaults_to_untested_never_trusted():
    assert status_for("laya_421m", "no_such_capability") == "untested"
    assert may_serve("laya_421m", "no_such_capability") is False


def test_only_explicitly_trusted_capabilities_may_serve():
    assert may_serve("hammer2.1_3b", "bounded_legal_action") is True
    assert may_serve("nanojev", "bounded_legal_action") is False  # tested_experimental
    assert "tested_experimental" not in PRODUCTION_STATUSES


def test_nemotron_is_not_quarantined_for_its_actual_contract():
    """It failed a bounded-choice test; its contract is orchestration.

    Corrected 2026-09-22: the bounded-choice failure was a decode-budget
    artefact (effective 256-token cap while the receipt recorded 1024; 37.4% of
    draws truncated before emitting a tool call). Re-measured at a truthful
    contract it is 0.857, equal to its reasoning-implied ceiling, so it is no
    longer quarantined — but 28 states is still not production-grade, and its
    native contract remains orchestration, which is still untested.
    """
    assert status_for("nemotron_orchestrator_8b", "bounded_legal_action") == "experimental"
    assert status_for("nemotron_orchestrator_8b", "orchestration") == "untested"
    # The quarantined flag must not silently come back.
    assert status_for("nemotron_orchestrator_8b", "bounded_legal_action") != "quarantined"
    # And no capability number may be published from a preliminary slice: the only
    # figure available is rep 0 at n=28, which is not a capability record.
    rec = trust_for("nemotron_orchestrator_8b", "bounded_legal_action")
    assert rec is not None
    assert rec.success_given_covered is None, (
        "a rep-0 n=28 slice was promoted into a capability number; wait for n=84"
    )
    # The manifest records *why* the number is withheld; CapabilityTrust does not
    # project that field, so read the file rather than the resolved record.
    raw = json.loads((REPO_ROOT / "manifests" / "capability_trust.json").read_text(encoding="utf-8"))
    entry = raw["models"]["nemotron_orchestrator_8b"]["capabilities"]["bounded_legal_action"]
    assert "awaiting" in entry, "the manifest must say what the number is waiting on"
    assert "success_given_covered" not in entry


@pytest.mark.parametrize(
    "payload",
    [
        {"models": {"decider_2b": {"trusted": True}}},
        {"models": {"decider_2b": {"is_trusted": False}}},
        {"decider_2b": {"trust": True}},
        {"models": {"decider_2b": {"capabilities": ["bounded_legal_action"]}}},
    ],
)
def test_global_model_trust_is_rejected(payload):
    with pytest.raises(ValueError):
        assert_no_global_trust(payload)


def test_per_capability_shape_is_accepted():
    assert_no_global_trust(
        {"models": {"decider_2b": {"capabilities": {"bounded_legal_action": {"status": "untested"}}}}}
    )


def test_a_trust_claim_without_evidence_is_rejected():
    with pytest.raises(ValueError) as excinfo:
        CapabilityTrust(backend_id="x", capability="y", status="trusted_bounded")
    assert "evidence" in str(excinfo.value)


def test_unknown_trust_status_is_rejected():
    with pytest.raises(ValueError):
        CapabilityTrust(backend_id="x", capability="y", status="pretty_good", evidence=("a",))


def test_trust_table_is_nested_by_model_then_capability():
    table = trust_table()
    assert "laya_421m" in table
    assert "verification_needed" in table["laya_421m"]
    # Laya was registered only on 2026-09-22; it must not be claiming anything.
    assert table["laya_421m"]["verification_needed"]["status"] == "untested"


# --------------------------------------------------------------------------
# Runtime plane (item 2)
# --------------------------------------------------------------------------


def test_runtime_status_reports_both_planes():
    st = runtime_status()
    assert st["schema"] == "z0int.runtime_status.v1"
    planes = st["planes"]
    assert set(planes) == {"generative", "decision"}
    assert planes["generative"]["kind"] == "generative"
    assert planes["generative"]["implementation"] == "llama.cpp / GGUF"
    assert planes["decision"]["kind"] == "decision"
    assert planes["decision"]["implementation"] == "in-process DecisionBackend workers"


def test_generative_resident_comes_from_the_live_runtime_not_config():
    """`resident` must mean a live runtime reports it loaded now."""
    gen = runtime_status()["planes"]["generative"]
    assert "resident" in gen
    if gen.get("reachable"):
        # A live supervisor answers with what it holds, not with an intention.
        # It is legitimately None while a model swap is in flight, which a
        # concurrent experiment can cause at any moment.
        assert gen["resident"] is None or isinstance(gen["resident"], str)
        assert gen["runtime_version"]


def test_registered_backends_are_visible_in_the_decision_plane():
    ids = {b["backend_id"] for b in runtime_status()["planes"]["decision"]["backends"]}
    # The four that existed before this block, plus the two that were wired but
    # unreachable, plus openjev_4b.
    assert {"mushroom", "nanojev", "decider_2b"} <= ids
    assert "laya_421m" in ids, "Laya must be enumerable; it was the one-entry omission"
    assert {"openjev_06b", "openjev_4b"} <= ids


def test_invocation_receipt_carries_the_required_runtime_fields():
    r = InvocationReceipt(
        runtime_id="decision-runtime",
        backend_id="laya_421m",
        model_id="laya_421m",
        revision="7c76b622dfc5cac71b2dc1c29873efe2ce509a05",
        checkpoint_digest="sha256:abc",
        checkpoint_digest_kind="structural",
        device="cpu",
        resident_before=None,
        resident_after="laya_421m",
        load_ms=9079.4,
        forward_ms=275.4,
        serialization_ms=0.01,
        total_ms=9355.0,
        network_model_calls=0,
        autoregressive_decode_steps=0,
    )
    d = r.to_dict()
    for key in (
        "runtime_id", "backend_id", "model_id", "revision", "checkpoint_digest",
        "device", "resident_before", "load_ms", "forward_ms", "serialization_ms",
        "total_ms", "network_model_calls", "autoregressive_decode_steps",
    ):
        assert key in d, f"runtime receipt is missing {key}"
    # A non-generative backend decodes zero tokens; that is a fact, not a null.
    assert d["autoregressive_decode_steps"] == 0
    assert d["network_model_calls"] == 0


def test_structural_digest_detects_a_changed_checkpoint_layout(tmp_path):
    (tmp_path / "config.json").write_text("{}")
    (tmp_path / "best.safetensors").write_bytes(b"x" * 32)
    d1 = structural_checkpoint_digest(str(tmp_path))
    assert d1 and d1.startswith("sha256:")

    (tmp_path / "extra.json").write_text("{}")
    d2 = structural_checkpoint_digest(str(tmp_path))
    assert d2 != d1, "adding a file must change the structural digest"

    assert structural_checkpoint_digest(str(tmp_path / "missing")) is None
    assert structural_checkpoint_digest(None) is None


def test_structural_digest_is_labelled_as_structural_not_content():
    """It must not be passable as a content hash."""
    r = InvocationReceipt(
        runtime_id="decision-runtime", backend_id="b", model_id="m",
        revision=None, checkpoint_digest="sha256:deadbeef",
        checkpoint_digest_kind="structural", device="cpu",
        resident_before=None, resident_after="b",
        load_ms=0.0, forward_ms=0.0, serialization_ms=0.0, total_ms=0.0,
        network_model_calls=0, autoregressive_decode_steps=0,
    )
    assert r.to_dict()["checkpoint_digest_kind"] == "structural"


# --------------------------------------------------------------------------
# The vocabulary must be the registry's, not this repo's
# --------------------------------------------------------------------------

# Mirror of kvnloo/z0 `registry/maturity.yaml` (`trust:`), lowercased exactly as
# that registry serializes it. Hardcoded because this repo has no dependency on
# z0's lib/; the comment is the contract and the test is the tripwire.
CANONICAL_TRUST_STATUSES = (
    "untested", "experimental", "tested_experimental", "trusted_shadow",
    "trusted_bounded", "trusted_general", "quarantined", "deprecated",
    "not_applicable",
)

# Declared aliases: names the registry accepts for a value it also defines.
DECLARED_TRUST_ALIASES = {
    "rejected_for_current_checkpoint": "deprecated",
}

# Mirror of the registry's `evidence:` keys, lowercased.
CANONICAL_EVIDENCE_CLASSES = (
    "smoke", "exploratory_beta", "shadow", "paired_replay",
    "confirm", "ood", "promotion",
)


def test_trust_statuses_are_canonical():
    """Every status here is a value the z0 registry declares, or an alias of one."""
    unknown = [
        s for s in trust_mod.TRUST_STATUSES
        if s not in CANONICAL_TRUST_STATUSES and s not in DECLARED_TRUST_ALIASES
    ]
    assert not unknown, (
        f"trust statuses not in the canonical vocabulary: {unknown}. "
        "The taxonomy is defined in kvnloo/z0 registry/maturity.yaml (trust:)."
    )


def test_no_trust_status_collides_with_an_evidence_class():
    """The defect this test exists for.

    `exploratory_beta` was BOTH a trust status in `capability_trust.json` and an
    evidence class in every artifact beside it, so a reader could not tell from
    the string which one a record was reporting, and a normalizer had to guess.
    The status is now the canonical `tested_experimental`; the registry rejects
    the collision from its side and this checks it from ours.
    """
    overlap = sorted(set(trust_mod.TRUST_STATUSES) & set(CANONICAL_EVIDENCE_CLASSES))
    assert not overlap, (
        f"trust statuses that are also evidence classes: {overlap}. "
        "One token cannot mean two things; see registry/maturity.yaml."
    )


def test_only_tested_experimental_and_above_require_a_citation():
    """The EXPERIMENTAL / TESTED_EXPERIMENTAL distinction is load-bearing.

    `experimental` means somebody ran it informally and no artifact records how;
    `tested_experimental` means there is a measurement to cite. Requiring a
    citation for `experimental` would make an unrecorded run unrepresentable;
    not requiring one for `tested_experimental` would let a tested claim stand
    without evidence.
    """
    with pytest.raises(ValueError):
        CapabilityTrust(backend_id="b", capability="c", status="tested_experimental")
    CapabilityTrust(backend_id="b", capability="c", status="experimental")

    # Only the two production tiers may gate a production path: bounded (inside
    # the measured distribution) and general (beyond it, which only OOD
    # evidence establishes). Nothing below them may.
    assert PRODUCTION_STATUSES == frozenset({"trusted_bounded", "trusted_general"})
    for weak in ("untested", "experimental", "tested_experimental", "trusted_shadow",
                 "quarantined", "rejected_for_current_checkpoint", "not_applicable"):
        assert weak not in PRODUCTION_STATUSES
