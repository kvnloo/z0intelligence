"""Consumer-level regression: resolve_context -> ArchitectureReusePacket -> check."""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from z0int.context_resolve import EvidenceRef, InformationNeed, _read_verified_bytes, resolve_context
from z0int.memory_contract import MemoryScope
from z0int.reuse_packet import (
    ReuseCandidate,
    build_reuse_packet,
    check_reuse_packet,
    observe_reuse_revisions,
)


SCOPE = MemoryScope(user="u1", project="p1", repo="r1", task="t1")
SIBLING = MemoryScope(user="u1", project="p1", repo="r2", task="t1")
CLOCK = 1_790_000_000_100_000_000


def context(root: Path, **overrides):
    opts = {
        "needs": [InformationNeed(
            id="implementation", description="read exact implementation",
            kind="exact_path", path="existing.py", scope=SCOPE,
        )],
        "project_root": root,
        "request_scope": SCOPE,
        "workstream_revision": "work-1",
        "compiler_revision": "compiler-1",
        "policy_revision": "policy-1",
        "allow_fff": False,
        "allow_qmd": False,
        "use_cache": False,
    }
    opts.update(overrides)
    return resolve_context(**opts)


def packet_for(root: Path):
    resolved = context(root)
    assert not resolved.unresolved_gaps
    candidate = ReuseCandidate(
        candidate_id="existing",
        summary="reuse existing implementation",
        strategy="reuse",
        evidence=(resolved.evidence[0],),
        owner="verified-owner",
        symbol="existing",
    )
    packet = build_reuse_packet(resolved, candidates=(candidate,))
    assert packet.decision["mode"] == "REUSE"
    return packet


def current_check(packet, root: Path, **changes):
    fresh = context(root, **changes)
    # Deliberately independently observed: never dict(packet.source_revisions).
    current = observe_reuse_revisions(packet, fresh)
    return check_reuse_packet(packet, current)


def assert_observe(case, check):
    case.assertFalse(check["valid"])
    case.assertEqual(check["decision"]["mode"], "OBSERVE")
    case.assertFalse(check["decision"]["implementation_allowed"])
    case.assertFalse(check["decision"]["authorizes_action"])


class ReusePacketFreshnessTests(unittest.TestCase):
    def test_unchanged_authorized_evidence_is_usable_not_authorization(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "existing.py").write_text("value = 1\n", encoding="utf-8")
            packet = packet_for(root)
            check = current_check(packet, root)
            self.assertTrue(check["valid"])
            self.assertEqual(check["decision"]["mode"], "REUSE")
            self.assertTrue(check["decision"]["implementation_allowed"])
            self.assertFalse(check["decision"]["authorizes_action"])
            self.assertIsNone(check["decision"]["verified_success"])
            self.assertTrue(packet.context.memory_snapshot.snapshot_id.startswith("mem_"))
            self.assertIn("snapshot", packet.source_revisions)
            self.assertTrue(any(key.startswith("evidence:") for key in packet.source_revisions))

    def test_required_evidence_or_snapshot_revision_missing_is_observe(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "existing.py").write_text("value = 1\n", encoding="utf-8")
            packet = packet_for(root)
            missing = InformationNeed(
                id="missing", description="required file", kind="exact_path",
                path="missing.py", scope=SCOPE, required=True,
            )
            for changes in (
                {"needs": [missing]},
                {"workstream_revision": None},
                {"compiler_revision": None},
                {"policy_revision": ""},
            ):
                with self.subTest(changes=changes):
                    fresh = context(root, **changes)
                    self.assertTrue(fresh.unresolved_gaps)
                    observed = observe_reuse_revisions(packet, fresh)
                    assert_observe(self, check_reuse_packet(packet, observed))

    def test_same_second_same_size_replacement_invalidates_real_consumer(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "existing.py"
            source.write_bytes(b"value = 1\n")
            os.utime(source, ns=(CLOCK, CLOCK))
            packet = packet_for(root)

            source.write_bytes(b"value = 2\n")
            os.utime(source, ns=(CLOCK + 800_000_000, CLOCK + 800_000_000))
            check = current_check(packet, root)
            assert_observe(self, check)
            self.assertIn("snapshot", check["changed_sources"])
            self.assertTrue(any(x.startswith("evidence:") for x in check["changed_sources"]))

    def test_same_metadata_atomic_replacement_rejects_packet(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "existing.py"
            source.write_bytes(b"value = 1\n")
            os.utime(source, ns=(CLOCK, CLOCK))
            packet = packet_for(root)

            incoming = root / "incoming.tmp"
            incoming.write_bytes(b"value = 2\n")
            os.utime(incoming, ns=(CLOCK, CLOCK))
            os.replace(incoming, source)
            os.utime(source, ns=(CLOCK, CLOCK))
            assert_observe(self, current_check(packet, root))

    def test_missing_required_source_rejects_dependent_write(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "existing.py"
            source.write_text("value = 1\n", encoding="utf-8")
            packet = packet_for(root)
            source.unlink()
            self.assertTrue(context(root).unresolved_gaps)
            assert_observe(self, current_check(packet, root))

    def test_change_between_fresh_resolve_and_consumer_read_rejects(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "existing.py"
            source.write_text("value = 1\n", encoding="utf-8")
            packet = packet_for(root)
            fresh = context(root)
            # The resolver observed A; a separate independent source read must
            # now observe B rather than echo the former snapshot's file map.
            source.write_text("value = 2\n", encoding="utf-8")
            revisions = observe_reuse_revisions(packet, fresh)
            self.assertNotEqual(revisions.get(next(
                key for key in packet.source_revisions if key.startswith("evidence:")
            )), next(value for key, value in packet.source_revisions.items()
                     if key.startswith("evidence:")))
            assert_observe(self, check_reuse_packet(packet, revisions))

    def test_scoped_reobservation_after_symlink_swap_is_observe(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve() / "repo"
            outside = Path(tmp).resolve() / "private-sibling"
            root.mkdir()
            outside.mkdir()
            source = root / "existing.py"
            source.write_text("public = 1\n", encoding="utf-8")
            sibling = outside / "sibling.py"
            sibling.write_text("PRIVATE_REOBSERVATION_PAYLOAD\n", encoding="utf-8")
            original = packet_for(root)
            swapped = False

            def swap_after_admission(admitted: Path):
                nonlocal swapped
                self.assertEqual(admitted, source)
                if not swapped:
                    source.unlink()
                    source.symlink_to(sibling)
                    swapped = True
                return _read_verified_bytes(admitted)

            with mock.patch(
                "z0int.context_resolve._read_verified_bytes", side_effect=swap_after_admission,
            ):
                fresh = context(root)
            self.assertTrue(swapped)
            self.assertEqual(fresh.evidence, [])
            self.assertTrue(fresh.unresolved_gaps)
            check = check_reuse_packet(original, observe_reuse_revisions(original, fresh))
            assert_observe(self, check)
            serialized = json.dumps({"fresh": fresh.to_dict(), "check": check})
            self.assertNotIn("PRIVATE_REOBSERVATION_PAYLOAD", serialized)
            self.assertNotIn(str(outside), serialized)
            self.assertNotIn(str(sibling), serialized)

    def test_policy_workstream_compiler_and_scope_each_invalidate(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "existing.py").write_text("value = 1\n", encoding="utf-8")
            packet = packet_for(root)
            for changed in (
                {"policy_revision": "policy-2"},
                {"workstream_revision": "work-2"},
                {"compiler_revision": "compiler-2"},
                {"request_scope": SIBLING},
                {"request_scope": None},
            ):
                with self.subTest(changed=changed):
                    result = current_check(packet, root, **changed)
                    assert_observe(self, result)

    def test_dirty_and_untracked_bytes_invalidate_source_snapshot(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for args in (
                ("init", "-q"),
                ("config", "user.email", "fixture@example.invalid"),
                ("config", "user.name", "Fixture"),
            ):
                subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True)
            source = root / "existing.py"
            source.write_text("value = 1\n", encoding="utf-8")
            subprocess.run(["git", "-C", str(root), "add", "existing.py"], check=True, capture_output=True)
            subprocess.run(["git", "-C", str(root), "commit", "-qm", "fixture"], check=True, capture_output=True)
            packet = packet_for(root)
            self.assertTrue(current_check(packet, root)["valid"])

            untracked = root / "scratch.txt"
            untracked.write_text("first\n", encoding="utf-8")
            assert_observe(self, current_check(packet, root))
            untracked.unlink()
            self.assertTrue(current_check(packet, root)["valid"])
            source.write_text("value = 2\n", encoding="utf-8")
            assert_observe(self, current_check(packet, root))

    def test_unverifiable_candidate_source_must_not_be_admitted_or_disclosed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "existing.py").write_text("value = 1\n", encoding="utf-8")
            resolved = context(root)
            external = EvidenceRef(
                source_id="file:/outside/sibling-private.py",
                source_version="sha256:unverified",
                locator="/outside/sibling-private.py",
                trust_class="code",
                observed_at="2026-10-07T00:00:00Z",
                excerpt="SIBLING_SECRET",
            )
            candidate = ReuseCandidate(
                candidate_id="sibling-private",
                summary="SIBLING_SECRET",
                strategy="reuse",
                evidence=(external,),
            )
            packet = build_reuse_packet(resolved, candidates=(candidate,))
            serialized = json.dumps(packet.to_dict())
            self.assertNotIn("sibling-private", serialized)
            self.assertNotIn("SIBLING_SECRET", serialized)
            self.assertNotIn("/outside/", serialized)
            self.assertEqual(packet.reuse_candidates, [])
            self.assertEqual(packet.decision["mode"], "OBSERVE")
            self.assertFalse(packet.decision["implementation_allowed"])

    def test_unknown_scope_has_no_payload_or_packet_metadata(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "existing.py").write_text("private sibling payload", encoding="utf-8")
            missing_scope = context(root, request_scope=None)
            packet = build_reuse_packet(missing_scope)
            data = json.dumps(packet.to_dict())
            self.assertEqual(packet.context.evidence, [])
            self.assertEqual(packet.reuse_candidates, [])
            self.assertNotIn("existing.py", data)
            self.assertNotIn("private sibling payload", data)
            self.assertEqual(packet.decision["mode"], "OBSERVE")
            self.assertFalse(packet.decision["implementation_allowed"])

    def test_snapshot_required_for_candidate_admission(self):
        from z0int.context_resolve import ContextPacket
        fake_ref = EvidenceRef(
            source_id="file:/untrusted.py",
            source_version="sha256:fake",
            locator="/untrusted.py",
            trust_class="code",
            observed_at="2026-10-07T00:00:00Z",
            excerpt="not admitted",
        )
        candidate = ReuseCandidate(
            candidate_id="fake", summary="do not expose", strategy="reuse",
            evidence=(fake_ref,),
        )
        packet = build_reuse_packet(
            ContextPacket(task_id="unknown", evidence=[fake_ref]),
            candidates=(candidate,),
        )
        self.assertEqual(packet.context.evidence, [])
        self.assertEqual(packet.reuse_candidates, [])
        self.assertEqual(packet.decision["mode"], "OBSERVE")


if __name__ == "__main__":
    unittest.main()
