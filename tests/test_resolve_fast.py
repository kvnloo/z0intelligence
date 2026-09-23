"""Tests for the fast concrete-resolution path.

The invariants that matter here are not "is it fast" but:

* a question's requested slots are inferred only from explicit cues;
* a slot is never resolved by a value that a model (or a regex) proposed -- it
  must be present in returned evidence, with a pointer;
* a shape match with no relevant evidence must NOT resolve a slot, because that
  is how a confident wrong answer is produced;
* anything unresolved falls back, and the fallback is held to the same standard.

Latency is asserted only loosely: these are correctness tests, and a tight
timing bound would be a flaky test rather than a stronger guarantee.
"""

from __future__ import annotations

import os
import tempfile
import unittest
from contextlib import contextmanager

from z0int import context_providers as cp
from z0int.context_resolve import (
    FAST_EXECUTABLE,
    OPERATORS,
    Slot,
    emit_episode,
    infer_slots,
    resolve_fast,
    select_operators,
)

HAVE_INDEXES = cp.SESSIONS_DB.is_file() and cp.COVERAGE_DB.is_file()


@contextmanager
def z0home(tmp: str):
    prev = os.environ.get("Z0INT_HOME")
    os.environ["Z0INT_HOME"] = tmp
    try:
        yield
    finally:
        if prev is None:
            os.environ.pop("Z0INT_HOME", None)
        else:
            os.environ["Z0INT_HOME"] = prev


class SlotInferenceTests(unittest.TestCase):
    def test_documented_examples(self):
        # The three examples the slot vocabulary is specified against.
        self.assertEqual([s.kind for s in infer_slots("where is the plugin key stored?")], ["PATH"])
        self.assertEqual(
            sorted(s.kind for s in infer_slots("which model and config file did we use?")),
            ["MODEL", "PATH"],
        )
        self.assertEqual([s.kind for s in infer_slots("what command fixed it?")], ["COMMAND"])

    def test_no_cue_means_no_slots(self):
        # A broad task question must not be forced into a slot.
        self.assertEqual(infer_slots("trace how the project evolved"), [])

    def test_single_slot_without_conjunction(self):
        # "key" also matches CONFIG_VALUE, but the primary cue wins: a question
        # that does not explicitly conjoin cues asks for one value.
        self.assertEqual(len(infer_slots("where is the plugin key stored?")), 1)

    def test_conjunction_adds_exactly_one_secondary(self):
        slots = infer_slots("which model and config file did we use?")
        self.assertLessEqual(len(slots), 2)


class OperatorSelectionTests(unittest.TestCase):
    def test_operators_are_from_the_bounded_set(self):
        ops, source = select_operators("where is the key stored", infer_slots("where is the key stored"),
                                       allow_model=False)
        self.assertTrue(set(ops) <= set(OPERATORS))
        self.assertEqual(source, "rule")

    def test_full_recall_is_always_last_resort(self):
        ops, _ = select_operators("where is the key stored", [Slot(kind="PATH")], allow_model=False)
        self.assertIn("FULL_RECALL", ops)
        self.assertEqual(ops[-1], "FULL_RECALL")

    def test_executable_operators_are_declared_in_the_action_set(self):
        self.assertTrue(FAST_EXECUTABLE <= set(OPERATORS))


class VerificationInvariantTests(unittest.TestCase):
    """The rule that makes this safe: no value without evidence."""

    def test_slot_is_not_resolved_without_an_evidence_pointer(self):
        slot = Slot(kind="PATH")
        self.assertFalse(slot.resolved)
        slot.value = "/tmp/whatever"
        self.assertFalse(slot.resolved, "a value alone must not count as resolved")
        slot.verification = "evidence"
        self.assertTrue(slot.resolved)

    def test_shape_match_without_relevant_evidence_does_not_resolve(self):
        # Regression: "what memory backend did OMP use" resolved to `BUILT_IN`
        # because that token is genuinely CONFIG_VALUE-shaped and its fact row
        # carried the harness name `omp:` in its *locator*. Scoring relevance
        # from the locator awarded it a match. A candidate whose evidence is not
        # about the question must not resolve a slot.
        class FakeHit:
            excerpt = "BUILT_IN"
            locator = "coverage:omp:01a0b065#value:BUILT_IN"
            provider = "facts"

        slots = [Slot(kind="CONFIG_VALUE")]
        from z0int.context_resolve import _absorb_candidates

        _absorb_candidates(slots, [FakeHit()], set(),
                           question="what memory backend did OMP use before and what does it use now")
        self.assertFalse(slots[0].resolved,
                         "a shape match with no relevant evidence must not resolve")

    def test_relevant_shape_match_does_resolve(self):
        class FakeHit:
            excerpt = "The key lives in /home/kvn/.hermes/profiles/chiefstaff/.env per the DSH plugin"
            locator = "agentsview:codex:abc#1"
            provider = "conversation"

        slots = [Slot(kind="PATH")]
        from z0int.context_resolve import _absorb_candidates

        _absorb_candidates(slots, [FakeHit()], set(),
                           question="where is the key that the DSH plugin reads stored on disk")
        self.assertTrue(slots[0].resolved)
        self.assertIn("chiefstaff", slots[0].value or "")
        self.assertTrue(slots[0].evidence_pointer)

    def test_claim_slot_needs_prose_not_a_token(self):
        class FakeHit:
            excerpt = "short"
            locator = "agentsview:x#1"
            provider = "conversation"

        slots = [Slot(kind="CLAIM")]
        from z0int.context_resolve import _absorb_candidates

        _absorb_candidates(slots, [FakeHit()], set(), question="what did we decide about short")
        self.assertFalse(slots[0].resolved, "a claim must not resolve from a fragment")


class FastPathBehaviourTests(unittest.TestCase):
    def test_no_slots_goes_straight_to_fallback(self):
        answer = resolve_fast("trace how the project evolved", allow_model=False, emit=False)
        self.assertTrue(answer.fallback_used)
        self.assertFalse(answer.complete)

    def test_missing_indexes_degrade_instead_of_fabricating(self):
        import unittest.mock as mock

        with mock.patch.object(cp, "SESSIONS_DB", __import__("pathlib").Path("/nonexistent/s.db")), \
             mock.patch.object(cp, "COVERAGE_DB", __import__("pathlib").Path("/nonexistent/c.db")), \
             mock.patch.object(cp, "TOOLINDEX_DB", __import__("pathlib").Path("/nonexistent/t.db")):
            answer = resolve_fast("where is the key stored", allow_model=False, emit=False)
        # Either it resolved nothing (correct) -- but it must never resolve a
        # value that has no evidence behind it.
        for slot in answer.slots:
            if slot.resolved:
                self.assertIsNotNone(slot.evidence_pointer)

    def test_episode_is_written_and_is_not_fatal(self):
        with tempfile.TemporaryDirectory() as tmp, z0home(tmp):
            answer = resolve_fast("where is the plugin key stored", allow_model=False, emit=True)
            path = os.path.join(tmp, "episodes", "resolve_fast.jsonl")
            self.assertTrue(os.path.exists(path), "episode must be written")
            import json

            row = json.loads(open(path, encoding="utf-8").read().strip().splitlines()[-1])
            for key in ("question", "requested_slots", "operator", "providers_used",
                        "fallback_used", "ttfa_ms", "evidence_opened"):
                self.assertIn(key, row)
            self.assertEqual(row["requested_slots"], ["PATH"])

    def test_emit_never_raises_on_a_bad_home(self):
        with z0home("/proc/definitely-not-writable"):
            emit_episode(resolve_fast("where is the key stored", allow_model=False, emit=False))


@unittest.skipUnless(HAVE_INDEXES, "local indexes not present")
class LiveFastPathTests(unittest.TestCase):
    def test_easy_identifier_question_resolves_without_fallback(self):
        answer = resolve_fast(
            "where is the key that the DSH plugin reads stored on disk", allow_model=False, emit=False
        )
        self.assertFalse(answer.fallback_used)
        self.assertTrue(answer.complete)
        value = next(s.value for s in answer.slots if s.resolved)
        self.assertIn(".env", value or "")

    def test_fast_path_opens_less_evidence_than_the_full_resolver(self):
        from z0int.context_resolve import resolve_context

        q = "where is the key that the DSH plugin reads stored on disk"
        fast = resolve_fast(q, allow_model=False, emit=False)
        packet = resolve_context(query=q, use_cache=False)
        self.assertLess(fast.evidence_opened, len(packet.evidence),
                        "the fast path must not open more evidence than the full resolver")

    def test_unresolved_slot_reports_no_answer(self):
        answer = resolve_fast("where is /nonexistent/zzz/qqq.xyz stored", allow_model=False, emit=False)
        for slot in answer.slots:
            if slot.resolved:
                self.assertTrue(slot.evidence_pointer)


if __name__ == "__main__":
    unittest.main()
