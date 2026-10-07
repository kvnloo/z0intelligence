from __future__ import annotations

import hashlib
import os
import subprocess
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
            self.assertIn("sha256=", packet.evidence[0].source_version)
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

    def test_file_evidence_version_changes_for_same_size_same_mtime_edit(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            target = root / "evidence.md"
            target.write_text("alpha\n", encoding="utf-8")
            first_stat = target.stat()

            def resolve() -> str:
                packet = resolve_context(
                    needs=[InformationNeed(id="file", description="file", kind="exact_path", path="evidence.md")],
                    project_root=root,
                    allow_fff=False,
                    allow_qmd=False,
                    use_cache=False,
                )
                return packet.evidence[0].source_version

            before = resolve()
            target.write_text("bravo\n", encoding="utf-8")
            self.assertEqual(target.stat().st_size, first_stat.st_size)
            os.utime(target, ns=(first_stat.st_atime_ns, first_stat.st_mtime_ns))
            after = resolve()
            self.assertNotEqual(before, after)

    def test_exact_path_excerpt_and_version_share_one_read_snapshot(self):
        from z0int import context_resolve as resolver

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            target = root / "evidence.md"
            target.write_text("alpha\n", encoding="utf-8")
            original_version = resolver._file_version
            changed = False

            def replace_before_version(path, **kwargs):
                nonlocal changed
                if not changed:
                    path.write_text("bravo\n", encoding="utf-8")
                    changed = True
                return original_version(path, **kwargs)

            with mock.patch("z0int.context_resolve._file_version", side_effect=replace_before_version):
                ref = resolver._fetch_exact_path("evidence.md", root)

            assert ref is not None
            expected_hash = hashlib.sha256(b"alpha\n").hexdigest()
            assert "alpha" in (ref.excerpt or "")
            assert f"sha256={expected_hash}" in ref.source_version
            assert target.read_text(encoding="utf-8") == "bravo\n"

    def test_stale_fff_content_hit_is_dropped_and_reported_partial(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "main.py").write_text("def current_symbol(): pass\n", encoding="utf-8")
            found = {
                "status": "ready",
                "coverage": "complete",
                "scanning": False,
                "warmup_complete": True,
                "generation": "fff-0.11.0:epoch=3",
                "generation_status": "tracked",
                "generation_reliable": True,
                "package_version": "0.11.0",
                "index_epoch": 3,
                "path_hits": [],
                "content_hits": [
                    {
                        "path": "main.py",
                        "line": 1,
                        "text": "def stale_symbol(): pass",
                        "context_before": [],
                        "context_after": [],
                    }
                ],
            }
            with mock.patch("z0int.context_resolve._fff_search_repository", return_value=found):
                packet = resolve_context(
                    needs=[
                        InformationNeed(
                            id="s",
                            description="stale_symbol",
                            kind="exact_symbol",
                            symbol="stale_symbol",
                        )
                    ],
                    project_root=root,
                    allow_fff=True,
                    allow_qmd=False,
                    use_cache=False,
                )

            self.assertFalse(packet.evidence)
            self.assertEqual(packet.measurements["coverage"], "partial")
            self.assertTrue(any("incomplete" in gap for gap in packet.unresolved_gaps))
            operation = next(op for op in packet.recipe.operations if op["op"] == "fff_symbol")
            self.assertEqual(operation["coverage"], "partial")
            self.assertIn("stale", operation["error"].lower())

    def test_fff_excerpt_context_comes_from_current_file_snapshot(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = "current before\ndef target_symbol(): pass\ncurrent after\n"
            (root / "main.py").write_text(source, encoding="utf-8")
            found = {
                "status": "ready",
                "coverage": "complete",
                "scanning": False,
                "warmup_complete": True,
                "generation": "fff-0.11.0:epoch=6",
                "generation_status": "tracked",
                "generation_reliable": True,
                "package_version": "0.11.0",
                "index_epoch": 6,
                "path_hits": [],
                "content_hits": [
                    {
                        "path": "main.py",
                        "line": 2,
                        "text": "def target_symbol(): pass",
                        "context_before": ["stale before"],
                        "context_after": ["stale after"],
                    }
                ],
            }
            with mock.patch("z0int.context_resolve._fff_search_repository", return_value=found):
                packet = resolve_context(
                    needs=[
                        InformationNeed(
                            id="s",
                            description="target_symbol",
                            kind="exact_symbol",
                            symbol="target_symbol",
                        )
                    ],
                    project_root=root,
                    allow_fff=True,
                    allow_qmd=False,
                    use_cache=False,
                )

            self.assertEqual(len(packet.evidence), 1)
            self.assertIn("current before", packet.evidence[0].excerpt)
            self.assertIn("current after", packet.evidence[0].excerpt)
            self.assertNotIn("stale", packet.evidence[0].excerpt)
            expected_hash = hashlib.sha256(source.encode()).hexdigest()
            self.assertIn(f"sha256={expected_hash}", packet.evidence[0].source_version)
            self.assertFalse(packet.unresolved_gaps)

    def test_exact_symbol_filename_hit_does_not_satisfy_symbol_need(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "useful_helper.py").write_text("pass\n", encoding="utf-8")
            found = {
                "status": "ready",
                "coverage": "complete",
                "scanning": False,
                "warmup_complete": True,
                "generation": "fff-0.11.0:epoch=4",
                "generation_status": "tracked",
                "generation_reliable": True,
                "package_version": "0.11.0",
                "index_epoch": 4,
                "path_hits": [{"path": "useful_helper.py"}],
                "content_hits": [],
            }
            with mock.patch("z0int.context_resolve._fff_search_repository", return_value=found):
                packet = resolve_context(
                    needs=[
                        InformationNeed(
                            id="s",
                            description="useful_helper",
                            kind="exact_symbol",
                            symbol="useful_helper",
                        )
                    ],
                    project_root=root,
                    allow_fff=True,
                    allow_qmd=False,
                    use_cache=False,
                )

            self.assertEqual(len(packet.evidence), 1)
            self.assertIn("filename hypothesis", packet.evidence[0].note)
            self.assertTrue(any("no repository symbol hits" in gap for gap in packet.unresolved_gaps))

    def test_natural_language_filename_hit_remains_a_hypothesis(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "project_overview.md").write_text("overview\n", encoding="utf-8")
            found = {
                "status": "ready",
                "coverage": "complete",
                "scanning": False,
                "warmup_complete": True,
                "generation": "fff-0.11.0:epoch=5",
                "generation_status": "tracked",
                "generation_reliable": True,
                "package_version": "0.11.0",
                "index_epoch": 5,
                "path_hits": [{"path": "project_overview.md"}],
                "content_hits": [],
            }
            with mock.patch("z0int.context_resolve._fff_search_repository", return_value=found):
                packet = resolve_context(
                    needs=[InformationNeed(id="n", description="project overview", kind="natural_language")],
                    project_root=root,
                    allow_fff=True,
                    allow_qmd=False,
                    use_cache=False,
                )

            self.assertEqual(len(packet.evidence), 1)
            self.assertIn("filename hypothesis", packet.evidence[0].note)
            self.assertFalse(packet.unresolved_gaps)

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
                "generation": "fff-0.11.0:resident=1:epoch=4",
                "generation_status": "tracked",
                "generation_reliable": True,
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
            operation = next(op for op in packet.recipe.operations if op["op"] == "fff_symbol")
            self.assertEqual(operation["coverage"], "complete")
            self.assertTrue(operation["required"])
            self.assertEqual(packet.measurements["coverage"], "complete")
            self.assertIn("fff:", next(k for k in packet.recipe.source_epochs if k.startswith("fff:")))

    def test_partial_fff_hits_remain_evidence_but_required_completeness_is_a_gap(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            target = root / "main.py"
            target.write_text("def useful_helper(): pass\n", encoding="utf-8")
            found = {
                "status": "warming",
                "scanning": True,
                "warmup_complete": False,
                "coverage": "partial",
                "generation": "fff-0.11.0:epoch=2",
                "generation_status": "tracked",
                "generation_reliable": True,
                "package_version": "0.11.0",
                "index_epoch": 2,
                "reused": True,
                "path_hits": [],
                "content_hits": [{
                    "path": "main.py", "line": 1, "text": "def useful_helper(): pass",
                    "context_before": [], "context_after": [],
                }],
            }
            with mock.patch("z0int.context_resolve._fff_search_repository", return_value=found):
                packet = resolve_context(
                    needs=[InformationNeed(id="s", description="helper", kind="exact_symbol", symbol="useful_helper")],
                    project_root=root,
                    allow_fff=True,
                    allow_qmd=False,
                    use_cache=False,
                )
            self.assertEqual(len(packet.evidence), 1)
            self.assertTrue(any("incomplete" in gap for gap in packet.unresolved_gaps))
            self.assertEqual(packet.measurements["coverage"], "partial")
            operation = next(op for op in packet.recipe.operations if op["op"] == "fff_symbol")
            self.assertEqual(operation["coverage"], "partial")
            self.assertTrue(operation["scanning"])
            self.assertEqual(operation["generation"], "fff-0.11.0:epoch=2")

    def test_unreliable_fff_generation_is_preserved_on_required_operation(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "main.py").write_text("def useful_helper(): pass\n", encoding="utf-8")
            found = {
                "status": "ready",
                "coverage": "partial",
                "scanning": False,
                "watcher_ready": True,
                "warmup_complete": True,
                "generation": "fff-0.11.0:epoch=2",
                "generation_status": "unreliable",
                "generation_reliable": False,
                "error": "watch callback unavailable",
                "package_version": "0.11.0",
                "index_epoch": 2,
                "path_hits": [],
                "content_hits": [{
                    "path": "main.py", "line": 1, "text": "def useful_helper(): pass",
                }],
                "content_scan": {"files_searched": 1, "eligible_files": 1},
            }
            with mock.patch("z0int.context_resolve._fff_search_repository", return_value=found):
                packet = resolve_context(
                    needs=[InformationNeed(id="s", description="helper", kind="exact_symbol", symbol="useful_helper")],
                    project_root=root,
                    allow_fff=True,
                    allow_qmd=False,
                    use_cache=False,
                )

            operation = next(op for op in packet.recipe.operations if op["op"] == "fff_symbol")
            self.assertEqual(operation["coverage"], "partial")
            self.assertEqual(operation["generation_status"], "unreliable")
            self.assertFalse(operation["generation_reliable"])
            self.assertTrue(operation["watcher_ready"])
            self.assertTrue(operation["warmup_complete"])
            self.assertEqual(operation["content_scan"]["eligible_files"], 1)
            self.assertIn("watch callback unavailable", operation["error"])
            self.assertFalse(any(key.startswith("fff:") for key in packet.recipe.source_epochs))
            self.assertEqual(packet.measurements["coverage"], "partial")

    def test_ready_empty_fff_query_is_complete_empty(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            found = {
                "status": "ready",
                "coverage": "complete",
                "scanning": False,
                "warmup_complete": True,
                "generation": "fff-0.11.0:epoch=0",
                "generation_status": "tracked",
                "generation_reliable": True,
                "package_version": "0.11.0",
                "index_epoch": 0,
                "path_hits": [],
                "content_hits": [],
            }
            with mock.patch("z0int.context_resolve._fff_search_repository", return_value=found):
                packet = resolve_context(
                    needs=[InformationNeed(id="s", description="missing", kind="exact_symbol", symbol="missing")],
                    project_root=root,
                    allow_fff=True,
                    allow_qmd=False,
                    use_cache=False,
                )
            operation = next(op for op in packet.recipe.operations if op["op"] == "fff_symbol")
            self.assertEqual(operation["status"], "ready")
            self.assertEqual(operation["coverage"], "complete")
            self.assertEqual(operation["hits"], 0)
            self.assertEqual(packet.measurements["coverage"], "complete")
            self.assertFalse(packet.evidence)

    def test_optional_partial_provider_stays_visible_without_blocking_required_source(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "required.md").write_text("required source\n", encoding="utf-8")
            (root / "helper.py").write_text("def existing_helper(): pass\n", encoding="utf-8")
            found = {
                "status": "warming",
                "coverage": "partial",
                "scanning": True,
                "generation": "fff-0.11.0:epoch=3",
                "generation_status": "tracked",
                "generation_reliable": True,
                "package_version": "0.11.0",
                "index_epoch": 3,
                "path_hits": [],
                "content_hits": [{
                    "path": "helper.py", "line": 1, "text": "def existing_helper(): pass",
                }],
            }
            with mock.patch("z0int.context_resolve._fff_search_repository", return_value=found):
                packet = resolve_context(
                    needs=[
                        InformationNeed(id="required", description="required", kind="exact_path", path="required.md"),
                        InformationNeed(id="optional", description="helper", kind="exact_symbol", symbol="existing_helper", required=False),
                    ],
                    project_root=root,
                    allow_fff=True,
                    allow_qmd=False,
                    use_cache=False,
                )
            operation = next(op for op in packet.recipe.operations if op["op"] == "fff_symbol")
            self.assertEqual(operation["coverage"], "partial")
            self.assertFalse(operation["required"])
            self.assertEqual(packet.measurements["coverage"], "complete")
            self.assertFalse(packet.unresolved_gaps)

    def test_qmd_ready_then_query_error_is_not_complete_empty(self):
        with tempfile.TemporaryDirectory() as tmp:
            status = mock.Mock(returncode=0, stdout="ready", stderr="")
            with (
                mock.patch("z0int.context_resolve._qmd_bin", return_value="/usr/bin/qmd"),
                mock.patch("z0int.context_resolve.subprocess.run", side_effect=[status, subprocess.TimeoutExpired("qmd", 8)]),
            ):
                packet = resolve_context(
                    query="missing term",
                    allow_fff=False,
                    allow_qmd=True,
                    use_cache=False,
                )
            self.assertEqual(packet.measurements["qmd_status"], "error")
            self.assertEqual(packet.measurements["coverage"], "unavailable")
            self.assertTrue(any("qmd" in gap.lower() and "error" in gap.lower() for gap in packet.unresolved_gaps))
            operation = next(op for op in packet.recipe.operations if op["op"] == "qmd_search")
            self.assertEqual(operation["coverage"], "unavailable")
            self.assertIn("TimeoutExpired", operation["error"])

    def test_partial_qmd_does_not_mask_unavailable_required_orientation(self):
        with tempfile.TemporaryDirectory() as tmp:
            status = mock.Mock(returncode=0, stdout="ready", stderr="")
            with (
                mock.patch("z0int.context_resolve._qmd_bin", return_value="/usr/bin/qmd"),
                mock.patch("z0int.context_resolve.subprocess.run", return_value=status),
                mock.patch(
                    "z0int.context_resolve._qmd_search",
                    return_value={
                        "status": "ready",
                        "coverage": "partial",
                        "hits": [],
                        "error": "unstructured results",
                    },
                ),
            ):
                packet = resolve_context(
                    query="repository question",
                    canonical_repo="widget",
                    registry_path=Path(tmp) / "missing-components.yaml",
                    candidate_roots=[],
                    allow_fff=True,
                    allow_qmd=True,
                    use_cache=False,
                )

            self.assertEqual(packet.measurements["coverage"], "unavailable")
            orientation = next(op for op in packet.recipe.operations if op["op"] == "repo_orientation")
            qmd = next(op for op in packet.recipe.operations if op["op"] == "qmd_search")
            self.assertEqual(orientation["coverage"], "unavailable")
            self.assertEqual(qmd["coverage"], "partial")

    def test_qmd_ready_empty_query_is_complete_empty(self):
        with tempfile.TemporaryDirectory() as tmp:
            status = mock.Mock(returncode=0, stdout="ready", stderr="")
            query = mock.Mock(returncode=0, stdout="[]", stderr="")
            with (
                mock.patch("z0int.context_resolve._qmd_bin", return_value="/usr/bin/qmd"),
                mock.patch("z0int.context_resolve.subprocess.run", side_effect=[status, query]),
            ):
                packet = resolve_context(
                    query="no matching term",
                    allow_fff=False,
                    allow_qmd=True,
                    use_cache=False,
                )
            operation = next(op for op in packet.recipe.operations if op["op"] == "qmd_search")
            self.assertEqual(operation["status"], "ready")
            self.assertEqual(operation["coverage"], "complete")
            self.assertEqual(operation["hits"], 0)
            self.assertEqual(packet.measurements["coverage"], "complete")
            self.assertFalse(packet.evidence)

    def test_fff_hit_does_not_spawn_qmd_status(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            target = root / "README.md"
            target.write_text("resident index evidence\n", encoding="utf-8")
            found = {
                "status": "ready",
                "package_version": "0.11.0",
                "index_epoch": 1,
                "generation": "fff-0.11.0:resident=1:epoch=1",
                "generation_status": "tracked",
                "generation_reliable": True,
                "reused": True,
                "wall_ms": 0.5,
                "path_hits": [{"path": "README.md"}],
                "content_hits": [],
            }
            with (
                mock.patch(
                    "z0int.context_resolve._fff_search_repository",
                    return_value=found,
                ),
                mock.patch(
                    "z0int.context_resolve._qmd_bin",
                    return_value="/usr/bin/qmd",
                ),
                mock.patch("z0int.context_resolve.subprocess.run") as run,
            ):
                packet = resolve_context(
                    query="README",
                    project_root=root,
                    allow_fff=True,
                    allow_qmd=True,
                    use_cache=False,
                )
            run.assert_not_called()
            self.assertEqual(packet.measurements["qmd_status"], "idle")
            self.assertEqual(packet.measurements["fff_hits"], 1)

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
