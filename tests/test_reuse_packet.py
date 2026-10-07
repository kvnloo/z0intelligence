from __future__ import annotations

import unittest
from dataclasses import replace

from z0int.context_resolve import ContextPacket, EvidenceRef, ResolutionRecipe
from z0int.reuse_packet import (
    NoveltyReceipt,
    RejectedCandidate,
    ReuseCandidate,
    build_reuse_packet,
    check_reuse_packet,
)


def _ref(
    name: str,
    version: str = "sha:abc",
    trust_class: str = "code",
) -> EvidenceRef:
    return EvidenceRef(
        source_id=f"file:{name}",
        source_version=version,
        locator=name,
        trust_class=trust_class,
        observed_at="2026-10-07T00:00:00Z",
    )


def _context(
    *,
    gaps: list[str] | None = None,
    coverage: str = "complete",
    operation_coverage: str = "complete",
    status: str = "complete",
    scanning: bool = False,
    error: str | None = None,
) -> ContextPacket:
    evidence = [_ref("src/existing.py")]
    return ContextPacket(
        task_id="task-138",
        evidence=evidence,
        unresolved_gaps=list(gaps or []),
        measurements={"coverage": coverage},
        recipe=ResolutionRecipe(
            capability_id="context_resolve",
            request_signature="sig",
            scope_fingerprint="scope",
            policy_revision="v0",
            source_epochs={"policy": "v0", "git": "head:123"},
            operations=(
                {
                    "op": "fff_symbol",
                    "need": "existing",
                    "required": True,
                    "hits": 1,
                    "coverage": operation_coverage,
                    "status": status,
                    "root": "/repo",
                    "generation": 4,
                    "index_status": status,
                    "scanning": scanning,
                    "error": error,
                },
            ),
            required_evidence_fields=("existing",),
            verifier_revision="none",
            hardware_profile="test",
        ),
    )


def _candidate(
    *,
    strategy: str = "reuse",
    owner: str | None = "z0intelligence/context",
    implementation: str = "src/z0int/context_resolve.py",
    test: str | None = "tests/test_context_resolve.py",
) -> ReuseCandidate:
    return ReuseCandidate(
        candidate_id="context-resolver",
        summary="reuse the existing context resolver",
        strategy=strategy,
        owner=owner,
        symbol="resolve_context",
        evidence=(_ref(implementation, "blob:impl"),),
        related_tests=(_ref(test, "blob:test"),) if test else (),
        invariants=("retrieval is evidence, not execution authority",),
    )


class ArchitectureReusePacketTests(unittest.TestCase):
    def test_required_archaeology_gap_blocks_implementation(self):
        packet = build_reuse_packet(
            _context(gaps=["owners: unresolved subsystem owner"]),
            candidates=[
                ReuseCandidate(
                    candidate_id="existing",
                    summary="existing implementation",
                    strategy="reuse",
                    evidence=(_ref("src/existing.py"),),
                )
            ],
        )
        self.assertEqual(packet.decision["mode"], "OBSERVE")
        self.assertFalse(packet.decision["implementation_allowed"])
        self.assertFalse(packet.decision["authorizes_action"])
        self.assertIsNone(packet.decision["verified_success"])

    def test_reuse_candidate_preserves_revision_bound_evidence(self):
        candidate = _candidate()
        packet = build_reuse_packet(_context(), candidates=[candidate])
        data = packet.to_dict()

        self.assertEqual(packet.decision["mode"], "REUSE")
        self.assertTrue(packet.decision["implementation_allowed"])
        self.assertFalse(packet.decision["authorizes_action"])
        self.assertIsNone(packet.decision["verified_success"])
        self.assertEqual(data["reuse_candidates"][0]["evidence"][0]["source_version"], "blob:impl")
        self.assertIn("input_fingerprint", data)

    def test_candidate_without_evidence_is_rejected(self):
        with self.assertRaises(ValueError):
            ReuseCandidate(
                candidate_id="handwave",
                summary="looks reusable",
                strategy="reuse",
                evidence=(),
            )

    def test_novel_mode_requires_evidence_backed_receipt(self):
        blocked = build_reuse_packet(_context(), candidates=[])
        self.assertEqual(blocked.decision["mode"], "OBSERVE")
        self.assertFalse(blocked.decision["implementation_allowed"])

        receipt = NoveltyReceipt(
            searched=("existing parser", "existing index"),
            candidates_rejected=(
                RejectedCandidate(
                    candidate_id="existing-parser",
                    reason="cannot preserve the required exact-ref identity",
                    evidence=(_ref("src/parser.py", "blob:old"),),
                ),
            ),
            new_abstraction_necessary=True,
        )
        self.assertFalse(receipt.evidence_backed)
        allowed = build_reuse_packet(_context(), candidates=[], novelty_receipt=receipt)
        self.assertEqual(allowed.decision["mode"], "OBSERVE")
        self.assertFalse(allowed.decision["implementation_allowed"])
        self.assertFalse(allowed.decision["authorizes_action"])

    def test_changed_source_revision_invalidates_packet(self):
        packet = build_reuse_packet(
            _context(),
            candidates=[
                _candidate(strategy="extend")
            ],
        )
        current = dict(packet.source_revisions)
        self.assertTrue(check_reuse_packet(packet, current)["valid"])

        current["epoch:git"] = "head:999"
        check = check_reuse_packet(packet, current)
        self.assertFalse(check["valid"])
        self.assertIn("epoch:git", check["changed_sources"])
        self.assertEqual(check["decision"]["mode"], "OBSERVE")
        self.assertFalse(check["decision"]["implementation_allowed"])

    def test_packet_never_claims_execution_or_verified_success(self):
        packet = build_reuse_packet(
            _context(),
            candidates=[
                _candidate(strategy="extend")
            ],
        )
        data = packet.to_dict()
        self.assertFalse(data["execution_completed"])
        self.assertIsNone(data["verified_success"])
        self.assertFalse(data["authorizes_action"])

    def test_packet_mutations_invalidate_fingerprint_and_readiness(self):
        mutations = (
            (
                "summary",
                lambda packet: packet.reuse_candidates.__setitem__(
                    0, replace(packet.reuse_candidates[0], summary="changed")
                ),
            ),
            ("gap", lambda packet: packet.unresolved_gaps.append("new unresolved owner")),
            ("ownership", lambda packet: setattr(packet, "ownership", ("different owner",))),
            ("rejected path", lambda packet: setattr(packet, "superseded_or_rejected_paths", ("old path",))),
            ("measurement", lambda packet: packet.measurements.update(search_hits=0)),
            ("decision", lambda packet: packet.decision.update(mode="OBSERVE")),
        )
        for label, mutate in mutations:
            with self.subTest(label=label):
                packet = build_reuse_packet(
                    _context(), candidates=[_candidate()], ownership=("z0intelligence/context",)
                )
                mutate(packet)
                result = check_reuse_packet(packet, dict(packet.source_revisions))
                self.assertFalse(result["valid"])
                self.assertEqual(result["decision"]["mode"], "OBSERVE")

    def test_newly_required_dependency_invalidates_packet(self):
        packet = build_reuse_packet(_context(), candidates=[_candidate()])
        current = dict(packet.source_revisions)
        current["epoch:new-required-source"] = "generation:2"

        result = check_reuse_packet(packet, current)

        self.assertFalse(result["valid"])
        self.assertIn("epoch:new-required-source", result["changed_sources"])
        self.assertEqual(result["decision"]["mode"], "OBSERVE")

    def test_missing_dependency_invalidates_packet(self):
        packet = build_reuse_packet(_context(), candidates=[_candidate()])
        current = dict(packet.source_revisions)
        missing = next(iter(current))
        del current[missing]

        result = check_reuse_packet(packet, current)

        self.assertFalse(result["valid"])
        self.assertIn(missing, result["changed_sources"])
        self.assertEqual(result["decision"]["mode"], "OBSERVE")

    def test_context_contradictions_block_positive_readiness(self):
        context = _context()
        context.contradictions.append("repository identity conflicts with task scope")

        packet = build_reuse_packet(context, candidates=[_candidate()])

        self.assertEqual(packet.decision["mode"], "OBSERVE")
        self.assertFalse(packet.decision["implementation_allowed"])

    def test_optional_provider_error_does_not_block_complete_required_coverage(self):
        context = _context()
        context.recipe = replace(
            context.recipe,
            operations=(
                *context.recipe.operations,
                {
                    "op": "qmd_search",
                    "need": "optional-docs",
                    "required": False,
                    "coverage": "unavailable",
                    "status": "error",
                    "error": "optional provider timed out",
                },
            ),
        )

        packet = build_reuse_packet(context, candidates=[_candidate()])

        self.assertEqual(packet.decision["mode"], "REUSE")
        self.assertTrue(packet.decision["implementation_allowed"])

    def test_missing_candidate_adequacy_evidence_blocks_positive_readiness(self):
        candidates = (
            _candidate(owner=None),
            _candidate(test=None),
            replace(_candidate(), evidence=(_ref("memory/suggestion.txt", trust_class="unknown"),)),
        )
        for candidate in candidates:
            with self.subTest(owner=candidate.owner, tests=candidate.related_tests):
                packet = build_reuse_packet(_context(), candidates=[candidate])
                self.assertEqual(packet.decision["mode"], "OBSERVE")
                self.assertFalse(packet.decision["implementation_allowed"])

    def test_incomplete_or_unhealthy_required_coverage_blocks_positive_readiness(self):
        missing = _context()
        missing.measurements.pop("coverage")
        contexts = (
            missing,
            _context(coverage="partial"),
            _context(operation_coverage="unavailable"),
            _context(status="warming"),
            _context(scanning=True),
            _context(error="index provider failed"),
        )
        for context in contexts:
            with self.subTest(measurements=context.measurements, operations=context.recipe.operations):
                packet = build_reuse_packet(context, candidates=[_candidate()])
                self.assertEqual(packet.decision["mode"], "OBSERVE")
                self.assertFalse(packet.decision["implementation_allowed"])

    def test_each_required_need_needs_a_completed_required_operation(self):
        base = _context()
        recipe = base.recipe
        assert recipe is not None
        operation = recipe.operations[0]
        contexts = (
            replace(base, recipe=replace(recipe, operations=())),
            replace(base, recipe=replace(recipe, operations=({**operation, "need": "other"},))),
            replace(base, recipe=replace(recipe, operations=({key: value for key, value in operation.items() if key != "coverage"},))),
            replace(base, recipe=replace(recipe, operations=({**operation, "required": False},))),
            replace(base, recipe=replace(recipe, operations=({**operation, "status": "timeout"},))),
        )
        for context in contexts:
            with self.subTest(operations=context.recipe.operations):
                packet = build_reuse_packet(context, candidates=[_candidate()])
                self.assertEqual(packet.decision["mode"], "OBSERVE")
                self.assertFalse(packet.decision["implementation_allowed"])

    def test_complete_empty_required_query_is_a_completed_operation(self):
        context = _context()
        recipe = context.recipe
        assert recipe is not None
        operation = {**recipe.operations[0], "status": "ready_empty", "hits": 0}
        context.recipe = replace(recipe, operations=(operation,))

        packet = build_reuse_packet(context, candidates=[_candidate()])

        self.assertEqual(packet.decision["mode"], "REUSE")
        self.assertTrue(packet.decision["implementation_allowed"])

    def test_unreliable_required_source_generation_blocks_readiness(self):
        context = _context()
        recipe = context.recipe
        assert recipe is not None
        operation = {**recipe.operations[0], "generation_status": "unreliable"}
        context.recipe = replace(recipe, operations=(operation,))

        packet = build_reuse_packet(context, candidates=[_candidate()])

        self.assertEqual(packet.decision["mode"], "OBSERVE")
        self.assertFalse(packet.decision["implementation_allowed"])


if __name__ == "__main__":
    unittest.main()
