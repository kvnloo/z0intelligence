"""Regression tests for the two historical invalid comparisons.

Each test here reproduces a failure that actually reached a published number.

1. NanoJev vs Hammer3B on mismatched candidate sets — September 2026.
   The same NanoJev checkpoint was reported at 43.6% on the full action space
   and later at 94.1% success-given-covered on the compiler's legal-action set.
   `candidate_set_equal` was false on 40/40 paired decisions.

2. `recovery_action` unit mismatch — September 2026.
   One frozen 640-parameter bundle, one split, two units: 1.000 per episode
   (the number the promotion gate read, in
   `~/.z0int/benchmarks/recovery_action_l2.json`) and 0.839 per step
   (the number in `results/recovery-compare.json`).

If either of these can be compared without complaint again, this module has
regressed.
"""

from __future__ import annotations

import pytest

from z0int.backends.base import DecisionOption, DecisionQuestion
from z0int.backends.comparability import (
    INVALID_COMPARISON,
    VALID,
    DecisionReceipt,
    InvalidComparison,
    comparable_value,
    compare_receipts,
    contract_hash,
    hash_candidates,
    require_comparable,
)

# The compiler's legal-action set for a Phase 1B state, as recorded in
# results/phase1b/p1b-20260921T1430Z/observations.jsonl.
LEGAL = ("abstain", "fs.read")

# The unfiltered agent action space the same state was scored against by the
# steal-sweep's pre-fix path.
FULL = (
    "abstain", "fs.read", "fs.write", "fs.rm_rf", "net.fetch",
    "shell.exec", "skill_view", "web_search", "ask_user", "delegate", "stop",
)

# Both were scored under the same question schema.
QUESTION = DecisionQuestion(
    id="next_action",
    type="choice",
    instructions="Choose the next action.",
    options=tuple(DecisionOption(i, f"do {i}") for i in FULL),
)

STATE = {"family": "abstention", "budget_units": 8, "authority": ["read"]}


def _receipt(
    *,
    candidates=FULL,
    value=0.436,
    metric_unit="accuracy",
    aggregation_unit="per_decision",
    backend="nanojev",
    model="nanojev_06b",
    revision="4a19595eada0857133c0d2be024f879a4077054b",
    descriptions=None,
    split_id="confirm",
    state=STATE,
    requested_max_tokens=None,
    effective_max_tokens=None,
):
    return DecisionReceipt.build(
        state=state,
        question=QUESTION,
        ordered_candidate_ids=candidates,
        candidate_descriptions=descriptions,
        decision_semantics="argmax_over_candidates",
        metric_name="success_given_covered",
        metric_unit=metric_unit,
        aggregation_unit=aggregation_unit,
        value=value,
        split_id=split_id,
        trace_id="p1b-2e7a43ac619c",
        backend=backend,
        runtime="decision-runtime",
        model=model,
        revision=revision,
        checkpoint_digest="sha256:deadbeef",
        requested_max_tokens=requested_max_tokens,
        effective_max_tokens=effective_max_tokens,
    )


# --------------------------------------------------------------------------
# Failure 1 — NanoJev on mismatched candidate sets
# --------------------------------------------------------------------------


def test_nanojev_mismatched_candidate_sets_is_invalid_comparison():
    """The historical 43.6%-vs-94.1% comparison must be refused."""
    on_full_menu = _receipt(candidates=FULL, value=0.436)
    on_legal_menu = _receipt(candidates=LEGAL, value=0.941, backend="nanojev", model="nanojev_06b")

    verdict = compare_receipts(on_full_menu, on_legal_menu)

    assert verdict.status == INVALID_COMPARISON
    assert "candidate_set_hash" in verdict.mismatches
    # The whole point: no score is produced for an invalid pair.
    with pytest.raises(InvalidComparison) as excinfo:
        comparable_value(on_full_menu, on_legal_menu)
    assert "candidate_set_hash" in excinfo.value.verdict.mismatches


def test_nanojev_matched_candidate_sets_is_valid_and_yields_a_delta():
    """With the menu fixed, the same pair is a real comparison."""
    nanojev = _receipt(candidates=LEGAL, value=0.941, backend="nanojev", model="nanojev_06b")
    hammer = _receipt(candidates=LEGAL, value=0.893, backend="hammer3b", model="hammer2.1_3b")

    verdict = require_comparable(nanojev, hammer)

    assert verdict.status == VALID
    assert verdict.mismatches == ()
    assert nanojev.contract_hash == hammer.contract_hash
    assert comparable_value(nanojev, hammer) == pytest.approx(0.048)


def test_different_backends_are_not_a_mismatch():
    """Comparing two backends is the point; only the problem must be held equal."""
    a = _receipt(candidates=LEGAL, backend="nanojev", model="nanojev_06b")
    b = _receipt(candidates=LEGAL, backend="hammer3b", model="hammer2.1_3b", revision="702ce4215e13")

    verdict = compare_receipts(a, b)

    assert verdict.status == VALID
    assert a.contract_hash == b.contract_hash
    assert a.revision != b.revision


def test_candidate_order_is_part_of_the_contract():
    """A reversed menu is a different question to a bounded-choice model."""
    forward = _receipt(candidates=LEGAL)
    reversed_ = _receipt(candidates=tuple(reversed(LEGAL)))

    assert compare_receipts(forward, reversed_).status == INVALID_COMPARISON
    assert "candidate_set_hash" in compare_receipts(forward, reversed_).mismatches


def test_description_rewrite_is_distinguishable_from_menu_change():
    """Same ids, rewritten descriptions: identify which of the two changed."""
    base = _receipt(candidates=LEGAL)
    reworded = _receipt(candidates=LEGAL, descriptions={"abstain": "stop and ask", "fs.read": "read a file"})

    verdict = compare_receipts(base, reworded)

    assert verdict.status == INVALID_COMPARISON
    assert verdict.mismatches == ("candidate_descriptions_hash",)
    assert base.candidate_set_hash == reworded.candidate_set_hash


def test_hash_candidates_rejects_duplicate_ids():
    with pytest.raises(ValueError):
        hash_candidates(("a", "a"))


# --------------------------------------------------------------------------
# Failure 2 — recovery_action per-episode vs per-step
# --------------------------------------------------------------------------


def test_recovery_unit_mismatch_is_invalid_comparison():
    """The promoted 1.000 and the reported 0.839 were never comparable."""
    per_episode = _receipt(
        candidates=LEGAL,
        value=1.000,
        metric_unit="accuracy",
        aggregation_unit="per_episode",
        backend="mushroom",
        model="recovery_student",
        revision=None,
        split_id="confirm",
    )
    per_step = _receipt(
        candidates=LEGAL,
        value=0.8385,
        metric_unit="accuracy",
        aggregation_unit="per_step",
        backend="mushroom",
        model="recovery_student",
        revision=None,
        split_id="confirm",
    )

    verdict = compare_receipts(per_episode, per_step)

    assert verdict.status == INVALID_COMPARISON
    assert "aggregation_unit" in verdict.mismatches
    # Same bundle, same split, same menu — only the unit differs, and that alone
    # is enough to forbid the subtraction.
    assert per_episode.candidate_set_hash == per_step.candidate_set_hash
    assert per_episode.state_hash == per_step.state_hash
    with pytest.raises(InvalidComparison):
        comparable_value(per_episode, per_step)


def test_recovery_same_unit_is_comparable():
    """per_step vs per_step across two artifacts is a legitimate comparison."""
    mushroom = _receipt(
        candidates=LEGAL, value=0.8385, aggregation_unit="per_step",
        backend="mushroom", model="recovery_student", revision=None,
    )
    rule = _receipt(
        candidates=LEGAL, value=0.9766, aggregation_unit="per_step",
        backend="rule_teacher", model="deterministic", revision=None,
    )

    assert compare_receipts(mushroom, rule).status == VALID
    # comparable_value(a, b) == a.value - b.value, so the rule's margin is
    # positive when asked the other way round.
    assert comparable_value(rule, mushroom) == pytest.approx(0.9766 - 0.8385)
    assert comparable_value(mushroom, rule) == pytest.approx(-(0.9766 - 0.8385))


def test_split_mismatch_is_invalid():
    a = _receipt(candidates=LEGAL, split_id="confirm")
    b = _receipt(candidates=LEGAL, split_id="ood")
    assert "split_id" in compare_receipts(a, b).mismatches


def test_metric_name_mismatch_is_invalid():
    a = _receipt(candidates=LEGAL, metric_unit="accuracy")
    b = _receipt(candidates=LEGAL, metric_unit="brier")
    assert "metric_unit" in compare_receipts(a, b).mismatches


# --------------------------------------------------------------------------
# Receipt hygiene
# --------------------------------------------------------------------------


def test_receipt_without_provenance_is_rejected():
    """An unattributed number is not reproducible, so it cannot be compared."""
    with pytest.raises(ValueError) as excinfo:
        DecisionReceipt.build(
            state=STATE,
            question=QUESTION,
            ordered_candidate_ids=LEGAL,
            candidate_descriptions=None,
            decision_semantics="argmax_over_candidates",
            metric_name="accuracy",
            metric_unit="accuracy",
            aggregation_unit="per_episode",
            value=1.0,
            split_id="confirm",
            trace_id="t",
            backend="mushroom",
            runtime="decision-runtime",
            model=None,       # <- no artifact named
            revision=None,
        )
    assert "model" in str(excinfo.value)


def test_local_artifact_may_omit_revision_but_needs_a_digest():
    """A fitted readout has no upstream revision; a digest still identifies it."""
    local = _receipt(candidates=LEGAL, model="recovery_student", revision=None)
    assert local.revision is None
    assert local.checkpoint_digest

    with pytest.raises(ValueError) as excinfo:
        DecisionReceipt.build(
            state=STATE,
            question=QUESTION,
            ordered_candidate_ids=LEGAL,
            candidate_descriptions=None,
            decision_semantics="argmax_over_candidates",
            metric_name="accuracy",
            metric_unit="accuracy",
            aggregation_unit="per_episode",
            value=1.0,
            split_id="confirm",
            trace_id="t",
            backend="mushroom",
            runtime="decision-runtime",
            model="recovery_student",
            revision=None,
            checkpoint_digest=None,   # <- neither revision nor digest
        )
    assert "neither" in str(excinfo.value)


def test_state_hash_is_sensitive_to_the_state():
    a = _receipt(candidates=LEGAL, state={"family": "abstention"})
    b = _receipt(candidates=LEGAL, state={"family": "security"})
    assert a.state_hash != b.state_hash
    assert "state_hash" in compare_receipts(a, b).mismatches


def test_contract_hash_ignores_mapping_order():
    """Dict ordering must not change identity."""
    h1 = contract_hash(
        state_schema_id="s", state_hash="h", question_schema_id="q", question_hash="qh",
        candidate_set_hash="cs", candidate_descriptions_hash="cd", decision_semantics="argmax",
    )
    h2 = contract_hash(
        decision_semantics="argmax", candidate_descriptions_hash="cd", candidate_set_hash="cs",
        question_hash="qh", question_schema_id="q", state_hash="h", state_schema_id="s",
    )
    assert h1 == h2


def test_receipt_round_trips_to_dict_with_contract_hash():
    r = _receipt(candidates=LEGAL)
    d = r.to_dict()
    assert d["contract_hash"] == r.contract_hash
    assert d["ordered_candidate_ids"] == list(LEGAL)
    assert d["aggregation_unit"] == "per_decision"


def test_direct_construction_cannot_pair_a_menu_with_a_foreign_hash():
    """A hash that does not describe the inputs is worse than no hash.

    Reviewer-raised: `build()` is safe, but the dataclass could be constructed by
    hand with an unrelated candidate_set_hash and still compare as VALID.
    """
    with pytest.raises(ValueError) as excinfo:
        DecisionReceipt(
            state_schema_id="s", state_hash="h", question_schema_id="q", question_hash="qh",
            ordered_candidate_ids=LEGAL, candidate_set_hash="sha256:not-the-real-one",
            candidate_descriptions_hash="cd", decision_semantics="argmax",
            metric_name="accuracy", metric_unit="accuracy", aggregation_unit="per_episode",
            value=1.0, split_id="confirm", trace_id="t",
            backend="b", runtime="r", model="m", checkpoint_digest="sha256:x",
        )
    assert "does not describe" in str(excinfo.value)


# --------------------------------------------------------------------------
# Generation contract — the Phase 1B defect
# --------------------------------------------------------------------------


def test_rows_measured_under_different_effective_caps_cannot_compare():
    """Phase 1B compared arms whose receipts all said 1024 while four of six ran
    at 256 or 128. That pair must not read as a capability comparison."""
    recorded_1024_actually_256 = _receipt(
        candidates=LEGAL, value=0.619, backend="nemotron", model="nemotron_orchestrator_8b",
        requested_max_tokens=1024, effective_max_tokens=256,
    )
    truly_1024 = _receipt(
        candidates=LEGAL, value=0.893, backend="hammer3b", model="hammer2.1_3b",
        requested_max_tokens=1024, effective_max_tokens=1024,
    )

    verdict = compare_receipts(recorded_1024_actually_256, truly_1024)

    assert verdict.status == INVALID_COMPARISON
    assert "effective_max_tokens" in verdict.mismatches
    with pytest.raises(InvalidComparison):
        comparable_value(recorded_1024_actually_256, truly_1024)


def test_declaring_the_cap_as_the_variable_makes_it_a_valid_experiment():
    a = _receipt(candidates=LEGAL, value=0.619, requested_max_tokens=1024, effective_max_tokens=256)
    b = _receipt(candidates=LEGAL, value=0.800, requested_max_tokens=1024, effective_max_tokens=1024)

    verdict = compare_receipts(a, b, varying=("effective_max_tokens",))

    assert verdict.status == VALID
    assert verdict.varying == ("effective_max_tokens",)
    # The pair is comparable *and* the difference is recorded as the variable under test.
    assert any(d.get("declared_variable") for d in verdict.detail if d["field"] == "effective_max_tokens")


def test_non_generative_receipts_are_unaffected_by_the_cap_field():
    """Laya, NanoJev and the mushroom readouts have no generation cap at all."""
    a = _receipt(candidates=LEGAL, value=0.941, backend="nanojev", model="nanojev_06b")
    b = _receipt(candidates=LEGAL, value=0.893, backend="hammer3b", model="hammer2.1_3b")
    assert a.effective_max_tokens is None and b.effective_max_tokens is None
    assert compare_receipts(a, b).status == VALID
