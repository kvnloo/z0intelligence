from __future__ import annotations

import unittest

from z0int.context_resolve import ContextPacket, EvidenceRef, ResolutionRecipe
from z0int.reuse_packet import (
    NoveltyReceipt,
    RejectedCandidate,
    ReuseCandidate,
    build_reuse_packet,
    check_reuse_packet,
)


def _ref(name: str, version: str = "sha:abc") -> EvidenceRef:
    return EvidenceRef(
        source_id=f"file:{name}",
        source_version=version,
        locator=name,
        trust_class="code",
        observed_at="2026-10-07T00:00:00Z",
    )


def _context(*, gaps: list[str] | None = None) -> ContextPacket:
    evidence = [_ref("src/existing.py")]
    return ContextPacket(
        task_id="task-138",
        evidence=evidence,
        unresolved_gaps=list(gaps or []),
        recipe=ResolutionRecipe(
            capability_id="context_resolve",
            request_signature="sig",
            scope_fingerprint="scope",
            policy_revision="v0",
            source_epochs={"policy": "v0", "git": "head:123"},
            operations=({"op": "fff_symbol", "hits": 1},),
            required_evidence_fields=("existing",),
            verifier_revision="none",
            hardware_profile="test",
        ),
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
        candidate = ReuseCandidate(
            candidate_id="context-resolver",
            summary="reuse the existing context resolver",
            strategy="reuse",
            owner="z0intelligence/context",
            symbol="resolve_context",
            evidence=(_ref("src/z0int/context_resolve.py", "blob:123"),),
            related_tests=(_ref("tests/test_context_resolve.py", "blob:456"),),
            invariants=("retrieval is evidence, not execution authority",),
        )
        packet = build_reuse_packet(_context(), candidates=[candidate])
        data = packet.to_dict()

        self.assertEqual(packet.decision["mode"], "REUSE")
        self.assertTrue(packet.decision["implementation_allowed"])
        self.assertFalse(packet.decision["authorizes_action"])
        self.assertIsNone(packet.decision["verified_success"])
        self.assertEqual(data["reuse_candidates"][0]["evidence"][0]["source_version"], "blob:123")
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
        allowed = build_reuse_packet(_context(), candidates=[], novelty_receipt=receipt)
        self.assertEqual(allowed.decision["mode"], "NOVEL")
        self.assertTrue(allowed.decision["implementation_allowed"])
        self.assertFalse(allowed.decision["authorizes_action"])

    def test_changed_source_revision_invalidates_packet(self):
        packet = build_reuse_packet(
            _context(),
            candidates=[
                ReuseCandidate(
                    candidate_id="existing",
                    summary="existing implementation",
                    strategy="extend",
                    evidence=(_ref("src/existing.py"),),
                )
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
                ReuseCandidate(
                    candidate_id="existing",
                    summary="existing implementation",
                    strategy="extend",
                    evidence=(_ref("src/existing.py"),),
                )
            ],
        )
        data = packet.to_dict()
        self.assertFalse(data["execution_completed"])
        self.assertIsNone(data["verified_success"])
        self.assertFalse(data["authorizes_action"])


if __name__ == "__main__":
    unittest.main()
