from __future__ import annotations

import os
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest import mock

from z0int.context_resolve import (
    InformationNeed,
    append_context_packet_event,
    project_to_aodl_fields,
    resolve_context,
)


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


class ContextResolveTests(unittest.TestCase):
    def test_exact_path_hit(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            f = root / "req.md"
            f.write_text("# requirement\nmust verify independently\n", encoding="utf-8")
            packet = resolve_context(
                needs=[InformationNeed(id="r1", description="req", kind="exact_path", path="req.md")],
                project_root=root,
                allow_qmd=False,
                use_cache=False,
            )
            self.assertEqual(len(packet.evidence), 1)
            self.assertEqual(packet.evidence[0].trust_class, "project_constraint")
            self.assertFalse(packet.unresolved_gaps)
            self.assertFalse(packet.measurements["gpu_loaded"])
            self.assertEqual(packet.measurements["network_model_calls"], 0)
            proj = project_to_aodl_fields(packet)
            self.assertIn("provenance", proj)
            self.assertIn("evidence", proj)
            self.assertNotIn("intentContract", proj)

    def test_missing_path_is_gap_not_invention(self):
        with tempfile.TemporaryDirectory() as tmp:
            packet = resolve_context(
                needs=[
                    InformationNeed(
                        id="r1",
                        description="missing",
                        kind="exact_path",
                        path="nope.md",
                        required=True,
                    )
                ],
                project_root=tmp,
                allow_qmd=False,
                use_cache=False,
            )
            self.assertEqual(packet.evidence, [])
            self.assertTrue(any("missing path" in g for g in packet.unresolved_gaps))

    def test_empty_query_rejected(self):
        with self.assertRaises(ValueError):
            resolve_context(needs=[], allow_qmd=False, use_cache=False)

    def test_memory_off_by_default(self):
        packet = resolve_context(
            needs=[InformationNeed(id="m1", description="prefs", kind="memory")],
            allow_qmd=False,
            allow_memory=False,
            use_cache=False,
        )
        self.assertTrue(any("memory recall disabled" in g for g in packet.unresolved_gaps))

    def test_exact_symbol_prefers_fff_and_emits_index_evidence(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "src").mkdir()
            target = root / "src" / "main.py"
            target.write_text("def resolve_context():\n    pass\n", encoding="utf-8")
            found = {
                "status": "ready",
                "package_version": "0.11.0",
                "index_epoch": 4,
                "reused": True,
                "wall_ms": 1.25,
                "path_hits": [],
                "content_hits": [
                    {
                        "path": "src/main.py",
                        "line": 1,
                        "col": 1,
                        "text": "def resolve_context():",
                        "context_before": [],
                        "context_after": ["    pass"],
                        "is_definition": True,
                    }
                ],
            }
            with mock.patch(
                "z0int.context_resolve._fff_search_repository",
                return_value=found,
            ) as search:
                packet = resolve_context(
                    needs=[
                        InformationNeed(
                            id="s1",
                            description="resolve_context",
                            kind="exact_symbol",
                            symbol="resolve_context",
                        )
                    ],
                    project_root=root,
                    allow_fff=True,
                    allow_qmd=False,
                    use_cache=False,
                )
            search.assert_called_once()
            self.assertEqual(len(packet.evidence), 1)
            self.assertEqual(packet.evidence[0].trust_class, "index_hit")
            self.assertIn("src/main.py:1", packet.evidence[0].locator)
            self.assertEqual(packet.measurements["fff_status"], "ready")
            self.assertEqual(packet.measurements["fff_hits"], 1)
            self.assertFalse(packet.unresolved_gaps)
            self.assertIn("fff:", next(k for k in packet.recipe.source_epochs if k.startswith("fff:")))

    def test_context_packet_can_be_recorded_in_event_log_without_granting_success(self):
        with tempfile.TemporaryDirectory() as tmp, z0home(tmp):
            root = Path(tmp) / "proj"
            root.mkdir()
            (root / "a.py").write_text("x=1\n", encoding="utf-8")
            packet = resolve_context(
                needs=[InformationNeed(id="e", description="a", kind="exact_path", path="a.py")],
                project_root=root,
                allow_fff=False,
                allow_qmd=False,
                use_cache=False,
            )
            event = append_context_packet_event(
                packet,
                source="test:harness",
                project=str(root),
                session_id="s1",
            )
            self.assertEqual(event.event_type, "context.resolve")
            self.assertFalse(event.payload["execution_completed"])
            self.assertIsNone(event.payload["verified_success"])
            self.assertEqual(event.session_id, "s1")

    def test_recipe_cache_roundtrip(self):
        with tempfile.TemporaryDirectory() as tmp, z0home(tmp):
            from z0int.context_resolve import load_recipe_cache

            root = Path(tmp) / "proj"
            root.mkdir()
            (root / "a.py").write_text("x=1\n", encoding="utf-8")
            p1 = resolve_context(
                needs=[InformationNeed(id="e", description="a", kind="exact_path", path="a.py")],
                project_root=root,
                allow_qmd=False,
                use_cache=True,
            )
            cached = load_recipe_cache(p1.recipe.request_signature)
            self.assertIsNotNone(cached)
            self.assertEqual(cached["recipe"]["capability_id"], "context_resolve")


if __name__ == "__main__":
    unittest.main()
