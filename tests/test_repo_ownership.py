from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from z0int.context_resolve import resolve_owning_repository


REGISTRY = """components:
  records:
    name: Records
    repo: example/known-library
    summary: Row normalization and deduplication for tabular records.
    provides: [records.normalized_rows.v1]
    boundaries:
      owns: [row normalization, record deduplication]
      not_here: [provider routing]
  router:
    name: Router
    repo: example/router
    summary: Provider routing and quota placement.
    provides: [router.route.v1]
    boundaries:
      owns: [provider routing, quota placement]
      not_here: [row normalization]
  ledger:
    name: Ledger
    repo: example/ledger
    summary: Cost and token measurement for records of provider usage.
    boundaries:
      owns: [cost measurement, token accounting]
"""


class RepoOwnershipTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.registry = Path(self.tmp.name) / "components.yaml"
        self.registry.write_text(REGISTRY)

    def resolve(self, task):
        return resolve_owning_repository(task, self.registry)

    def test_task_naming_an_owned_capability_resolves_to_its_single_owner(self):
        out = self.resolve("Add a small CLI that prints rows after normalization, dropping duplicates.")
        self.assertEqual(out["status"], "resolved", out)
        self.assertEqual(out["component_id"], "records")
        self.assertEqual(out["canonical_repo"], "example/known-library")
        self.assertEqual(out["registry_source_version"][:7], "sha256:")
        self.assertIn("normal", out["matched_terms"])  # six-letter stem of "normalization"

    def test_task_spanning_two_owners_is_ambiguous(self):
        out = self.resolve("Normalize rows and then pick the provider routing for them.")
        self.assertEqual(out["status"], "ambiguous", out)
        self.assertIsNone(out["canonical_repo"])
        self.assertEqual({c["component_id"] for c in out["candidates"][:2]}, {"records", "router"})
        self.assertTrue(out["gaps"])

    def test_task_with_no_ownership_vocabulary_is_unresolved(self):
        out = self.resolve("Make the button blue and tidy the stylesheet.")
        self.assertEqual(out["status"], "unresolved", out)
        self.assertIsNone(out["canonical_repo"])

    def test_capability_another_component_disclaims_does_not_count_for_it(self):
        # "row normalization" appears in router's not_here; it must not earn router a match.
        out = self.resolve("Fix row normalization for empty input.")
        self.assertEqual(out["component_id"], "records", out)
        self.assertNotIn("router", [c["component_id"] for c in out["candidates"] if c["score"] > 0])

    def test_resolution_is_deterministic_and_order_independent(self):
        task = "Report token accounting and cost measurement per session."
        first = self.resolve(task)
        head, *blocks = REGISTRY.split("\n  ")
        components = ["  " + block.rstrip("\n") + "\n" for block in blocks]
        starts = [i for i, block in enumerate(components) if not block.startswith("    ")]
        grouped = ["".join(components[a:b]) for a, b in zip(starts, [*starts[1:], len(components)])]
        self.registry.write_text(head + "\n" + "".join(reversed(grouped)))
        second = self.resolve(task)
        self.assertEqual(first["component_id"], "ledger")
        self.assertEqual(
            {key: first[key] for key in ("status", "component_id", "canonical_repo", "matched_terms", "candidates")},
            {key: second[key] for key in ("status", "component_id", "canonical_repo", "matched_terms", "candidates")},
        )

    def test_unreadable_registry_is_unavailable_not_a_guess(self):
        out = resolve_owning_repository("normalize rows", Path(self.tmp.name) / "missing.yaml")
        self.assertEqual(out["status"], "unavailable")
        self.assertIsNone(out["canonical_repo"])
        self.assertTrue(out["gaps"])


if __name__ == "__main__":
    unittest.main()
