"""Comparison invariants for DecisionBackend evaluations.

Why this module exists
----------------------
Two historical failures, both of which published a model-accuracy number for a
comparison that was not a comparison.

1. **NanoJev vs Hammer3B (September 2026).** NanoJev was scored against the full
   agent action space while Hammer3B was scored against the compiler's
   legal-action set. The result was reported as "NanoJev 43.6%, probably
   useless". It was neither: with candidate sets forced to match, the same
   checkpoint eliminated 60.7% of Hammer3B calls at 94.1%
   success-given-covered, and the cascade improved end-to-end accuracy from
   0.893 to 0.929. `candidate_set_equal` was false on all 40 paired decisions
   before the fix and true on all 40 after.

2. **`recovery_action` (September 2026).** The same frozen 640-parameter bundle
   was reported as 1.000 (per episode) and 0.839 (per step) on the same split,
   with the promotion gate reading the per-episode number while the "can it
   replace the rule?" question was asked in the per-step unit. See
   `~/.z0int/benchmarks/recovery_action_l2.json` and
   `results/recovery-compare.json`.

Neither was a modelling error. Both compared two things that were not the same
thing. This module makes that unrepresentable rather than merely discouraged:
every comparable receipt carries the semantic inputs it was produced from, and
two receipts are comparable only when the fields that must be held equal
actually match. A mismatch yields ``INVALID_COMPARISON`` — never a score.

Stdlib only. Importing this must not load torch, transformers, weights or
network clients.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, Sequence

import hashlib
import json

from .base import DecisionQuestion

COMPARABILITY_SCHEMA = "z0int.decision_comparability.v1"

#: Fields that must be equal for two receipts to describe the same problem.
#: A difference in any of these makes a comparison meaningless regardless of how
#: close the two numbers look.
SEMANTIC_FIELDS: tuple[str, ...] = (
    "state_schema_id",
    "state_hash",
    "question_schema_id",
    "question_hash",
    "candidate_set_hash",
    "candidate_descriptions_hash",
    "decision_semantics",
)

#: Fields that must be equal for two *measurements* to be comparable.
MEASUREMENT_FIELDS: tuple[str, ...] = (
    "metric_name",
    "metric_unit",
    "aggregation_unit",
    "split_id",
)

#: Generation-contract fields. Two generative rows measured under different
#: effective caps are not a capability comparison — Phase 1B compared arms whose
#: receipts all said 1024 while four of six actually ran at 256 or 128. These are
#: compared only when both receipts carry them, since non-generative backends
#: (Laya, NanoJev, the mushroom readouts) legitimately have neither. A caller may
#: declare one of them an intentional variable via ``compare_receipts(varying=...)``.
EXECUTION_CONTRACT_FIELDS: tuple[str, ...] = (
    "requested_max_tokens",
    "effective_max_tokens",
)

#: Fields that record which artifact produced the number. These may legitimately
#: differ — that is usually the point of the comparison — but a receipt without
#: them is not reproducible, so they are required rather than equal-checked.
EXECUTION_FIELDS: tuple[str, ...] = (
    "backend",
    "runtime",
    "model",
    "revision",
    "checkpoint_digest",
)

REQUIRED_FIELDS: tuple[str, ...] = SEMANTIC_FIELDS + MEASUREMENT_FIELDS + EXECUTION_FIELDS + (
    "ordered_candidate_ids",
    "trace_id",
    "value",
)

#: The all-caps verdict used wherever a score would otherwise be reported.
INVALID_COMPARISON = "INVALID_COMPARISON"
VALID = "VALID"


class InvalidComparison(ValueError):
    """Raised when a caller tries to consume a comparison that is not one."""

    def __init__(self, verdict: "ComparisonVerdict") -> None:
        self.verdict = verdict
        super().__init__(
            "refusing to compare receipts that do not describe the same problem: "
            + ", ".join(verdict.mismatches)
        )


def canonical_json(value: Any) -> str:
    """Deterministic serialization. `sort_keys` makes dict order irrelevant."""
    return json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":"))


def sha256_hex(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def digest(value: Any) -> str:
    return sha256_hex(canonical_json(value))


def hash_state(state: Any, *, schema_id: str) -> str:
    """Identity of the compiled state. `schema_id` versions the projection."""
    return digest({"schema": schema_id, "state": state})


def hash_question(question: DecisionQuestion, *, schema_id: str) -> str:
    """Identity of the question including its typed options and criteria."""
    return digest(
        {
            "schema": schema_id,
            "id": question.id,
            "type": question.type,
            "instructions": question.instructions,
            "options": [[o.id, o.description] for o in question.options],
            "levels": list(question.levels),
            "false_criterion": question.false_criterion,
            "true_criterion": question.true_criterion,
        }
    )


def hash_candidates(
    ordered_candidate_ids: Sequence[str],
    candidate_descriptions: dict[str, str] | Sequence[str] | None = None,
) -> tuple[str, str]:
    """Return ``(candidate_set_hash, candidate_descriptions_hash)``.

    Order is part of the identity: the same menu in a different order is a
    different question to a bounded-choice model, and the eval suite sweeps
    order (original / reversed / shuffled) deliberately. Descriptions are hashed
    separately so a description rewrite is distinguishable from a menu change.
    """
    ids = [str(x) for x in ordered_candidate_ids]
    if len(ids) != len(set(ids)):
        raise ValueError("candidate ids must be unique within a question")
    set_hash = digest({"ordered_candidate_ids": ids})
    if candidate_descriptions is None:
        desc_hash = digest({"ordered_candidate_ids": ids, "descriptions": None})
    elif isinstance(candidate_descriptions, dict):
        desc_hash = digest(
            {"ordered_candidate_ids": ids, "descriptions": [candidate_descriptions.get(i) for i in ids]}
        )
    else:
        descs = [str(x) for x in candidate_descriptions]
        if len(descs) != len(ids):
            raise ValueError("candidate_descriptions length must match ordered_candidate_ids")
        desc_hash = digest({"ordered_candidate_ids": ids, "descriptions": descs})
    return set_hash, desc_hash


def contract_hash(
    *,
    state_schema_id: str,
    state_hash: str,
    question_schema_id: str,
    question_hash: str,
    candidate_set_hash: str,
    candidate_descriptions_hash: str,
    decision_semantics: str,
) -> str:
    """Hash of the semantic inputs only.

    Two receipts with equal ``contract_hash`` describe the same bounded decision
    and may be compared. Executions with different backends routinely share a
    contract hash — that is what makes them comparable at all.
    """
    return digest(
        {
            "schema": COMPARABILITY_SCHEMA,
            "state_schema_id": state_schema_id,
            "state_hash": state_hash,
            "question_schema_id": question_schema_id,
            "question_hash": question_hash,
            "candidate_set_hash": candidate_set_hash,
            "candidate_descriptions_hash": candidate_descriptions_hash,
            "decision_semantics": decision_semantics,
        }
    )


@dataclass(frozen=True)
class DecisionReceipt:
    """A single measured decision outcome, with the problem it measured pinned.

    Build these with :meth:`build` rather than by hand so the hashes cannot drift
    from the inputs they claim to describe.
    """

    # --- identity of the problem -------------------------------------------
    state_schema_id: str
    state_hash: str
    question_schema_id: str
    question_hash: str
    ordered_candidate_ids: tuple[str, ...]
    candidate_set_hash: str
    candidate_descriptions_hash: str
    decision_semantics: str
    # --- the measurement ---------------------------------------------------
    metric_name: str
    metric_unit: str
    aggregation_unit: str
    value: float
    # --- scope -------------------------------------------------------------
    split_id: str
    trace_id: str
    # --- which artifact produced it ----------------------------------------
    backend: str
    runtime: str
    model: str | None = None
    revision: str | None = None
    checkpoint_digest: str | None = None
    #: Generation contract. Optional because non-generative backends (Laya,
    #: NanoJev, the mushroom readouts) have neither. When both receipts carry
    #: them they must agree, or the pair is not a capability comparison.
    requested_max_tokens: int | None = None
    effective_max_tokens: int | None = None
    # --- optional extras ---------------------------------------------------
    latency_ms: float | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        missing = [
            f
            for f in REQUIRED_FIELDS
            if getattr(self, f, None) in (None, "")
            # `revision` and `checkpoint_digest` are not individually required:
            # locally generated artifacts (the mushroom readouts, a fitted
            # ridge) have no upstream revision. The rule below requires at least
            # one of the two, so an artifact is always identifiable.
            and f not in ("ordered_candidate_ids", "value", "revision", "checkpoint_digest")
        ]
        if missing:
            raise ValueError(f"receipt is missing required fields: {', '.join(sorted(missing))}")
        if not self.revision and not self.checkpoint_digest:
            raise ValueError(
                "receipt must identify its artifact by `revision` (upstream) "
                "or `checkpoint_digest` (local); neither was given"
            )
        # A hash that does not describe the inputs actually carried is worse than
        # no hash: it makes two different problems look comparable. `build`
        # cannot get this wrong, but direct construction can. Reviewer B raised
        # the inconsistency; this closes the checkable half of it.
        if self.ordered_candidate_ids:
            expected_set, _ = hash_candidates(self.ordered_candidate_ids, None)
            if self.candidate_set_hash != expected_set:
                raise ValueError(
                    "candidate_set_hash does not describe ordered_candidate_ids; "
                    "the receipt was not built with DecisionReceipt.build()"
                )

    @property
    def contract_hash(self) -> str:
        return contract_hash(
            state_schema_id=self.state_schema_id,
            state_hash=self.state_hash,
            question_schema_id=self.question_schema_id,
            question_hash=self.question_hash,
            candidate_set_hash=self.candidate_set_hash,
            candidate_descriptions_hash=self.candidate_descriptions_hash,
            decision_semantics=self.decision_semantics,
        )

    def to_dict(self) -> dict[str, Any]:
        out = {
            "schema": COMPARABILITY_SCHEMA,
            "state_schema_id": self.state_schema_id,
            "state_hash": self.state_hash,
            "question_schema_id": self.question_schema_id,
            "question_hash": self.question_hash,
            "ordered_candidate_ids": list(self.ordered_candidate_ids),
            "candidate_set_hash": self.candidate_set_hash,
            "candidate_descriptions_hash": self.candidate_descriptions_hash,
            "decision_semantics": self.decision_semantics,
            "contract_hash": self.contract_hash,
            "metric_name": self.metric_name,
            "metric_unit": self.metric_unit,
            "aggregation_unit": self.aggregation_unit,
            "value": self.value,
            "split_id": self.split_id,
            "trace_id": self.trace_id,
            "backend": self.backend,
            "runtime": self.runtime,
            "model": self.model,
            "revision": self.revision,
            "checkpoint_digest": self.checkpoint_digest,
            "requested_max_tokens": self.requested_max_tokens,
            "effective_max_tokens": self.effective_max_tokens,
            "latency_ms": self.latency_ms,
        }
        if self.extra:
            out["extra"] = dict(self.extra)
        return out

    @classmethod
    def build(
        cls,
        *,
        state: Any,
        question: DecisionQuestion,
        ordered_candidate_ids: Sequence[str],
        candidate_descriptions: dict[str, str] | Sequence[str] | None,
        decision_semantics: str,
        metric_name: str,
        metric_unit: str,
        aggregation_unit: str,
        value: float,
        split_id: str,
        trace_id: str,
        backend: str,
        runtime: str,
        model: str | None = None,
        revision: str | None = None,
        checkpoint_digest: str | None = None,
        state_schema_id: str = "state.v1",
        question_schema_id: str = "question.v1",
        latency_ms: float | None = None,
        requested_max_tokens: int | None = None,
        effective_max_tokens: int | None = None,
        extra: dict[str, Any] | None = None,
    ) -> "DecisionReceipt":
        set_hash, desc_hash = hash_candidates(ordered_candidate_ids, candidate_descriptions)
        return cls(
            state_schema_id=state_schema_id,
            state_hash=hash_state(state, schema_id=state_schema_id),
            question_schema_id=question_schema_id,
            question_hash=hash_question(question, schema_id=question_schema_id),
            ordered_candidate_ids=tuple(str(x) for x in ordered_candidate_ids),
            candidate_set_hash=set_hash,
            candidate_descriptions_hash=desc_hash,
            decision_semantics=decision_semantics,
            metric_name=metric_name,
            metric_unit=metric_unit,
            aggregation_unit=aggregation_unit,
            value=float(value),
            split_id=split_id,
            trace_id=trace_id,
            backend=backend,
            runtime=runtime,
            model=model,
            revision=revision,
            checkpoint_digest=checkpoint_digest,
            latency_ms=latency_ms,
            requested_max_tokens=requested_max_tokens,
            effective_max_tokens=effective_max_tokens,
            extra=dict(extra or {}),
        )


@dataclass(frozen=True)
class ComparisonVerdict:
    """The outcome of asking whether two receipts may be compared."""

    status: str
    mismatches: tuple[str, ...]
    detail: tuple[dict[str, Any], ...]
    contract_hash_a: str
    contract_hash_b: str
    #: Fields the caller explicitly declared as the variable under test. A
    #: difference here is the experiment, not a defect — but it must be *named*.
    varying: tuple[str, ...] = ()

    @property
    def valid(self) -> bool:
        return self.status == VALID

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "valid": self.valid,
            "mismatches": list(self.mismatches),
            "varying": list(self.varying),
            "detail": [dict(d) for d in self.detail],
            "contract_hash_a": self.contract_hash_a,
            "contract_hash_b": self.contract_hash_b,
        }

    def __str__(self) -> str:  # pragma: no cover - presentation
        if self.valid:
            return f"{VALID} (contract {self.contract_hash_a[:12]})"
        return f"{INVALID_COMPARISON}: {', '.join(self.mismatches)}"


def compare_receipts(a: DecisionReceipt, b: DecisionReceipt, *, varying: Iterable[str] = ()) -> ComparisonVerdict:
    """Decide whether two receipts may be compared.

    Semantic and measurement mismatches are hard failures. Execution-identity
    differences are *expected* and are not mismatches — comparing two backends is
    the point — provided both receipts name the artifact that produced them.
    """
    held_equal = SEMANTIC_FIELDS + MEASUREMENT_FIELDS
    mismatches: list[str] = []
    detail: list[dict[str, Any]] = []
    varying = tuple(varying)
    for f in held_equal:
        va, vb = getattr(a, f), getattr(b, f)
        if va != vb:
            mismatches.append(f)
            detail.append({"field": f, "a": va, "b": vb})
    # Generation contract, only when both receipts declare one. Phase 1B compared
    # arms whose receipts all said 1024 while four of six actually ran at 256 or
    # 128; that pair must not read as a capability comparison.
    for f in EXECUTION_CONTRACT_FIELDS:
        va, vb = getattr(a, f, None), getattr(b, f, None)
        if va is None or vb is None or va == vb:
            continue
        if f in varying:
            detail.append({"field": f, "a": va, "b": vb, "declared_variable": True})
            continue
        mismatches.append(f)
        detail.append({"field": f, "a": va, "b": vb})
    status = VALID if not mismatches else INVALID_COMPARISON
    return ComparisonVerdict(
        status=status,
        mismatches=tuple(mismatches),
        detail=tuple(detail),
        contract_hash_a=a.contract_hash,
        contract_hash_b=b.contract_hash,
        varying=varying,
    )


def require_comparable(a: DecisionReceipt, b: DecisionReceipt, *, varying: Iterable[str] = ()) -> ComparisonVerdict:
    """Return the verdict, raising :class:`InvalidComparison` when invalid.

    This is the function an evaluation should call *before* it computes a delta.
    There is deliberately no helper that returns a score for an invalid pair.
    """
    verdict = compare_receipts(a, b, varying=varying)
    if not verdict.valid:
        raise InvalidComparison(verdict)
    return verdict


def comparable_value(a: DecisionReceipt, b: DecisionReceipt) -> float:
    """``a.value - b.value``, or raise. The only supported way to take a delta."""
    require_comparable(a, b)
    return a.value - b.value


def contract_hash_of(receipts: Iterable[DecisionReceipt]) -> str | None:
    """The shared contract hash of a group, or ``None`` if they do not agree."""
    hashes = {r.contract_hash for r in receipts}
    if len(hashes) == 1:
        return hashes.pop()
    return None
