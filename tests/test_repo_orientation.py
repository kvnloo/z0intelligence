from __future__ import annotations

import os
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path

from z0int.context_resolve import InformationNeed, resolve_context


def git(root: Path, *args: str) -> str:
    proc = subprocess.run(
        ["git", "-C", str(root), *args],
        check=True,
        capture_output=True,
        text=True,
    )
    return proc.stdout.strip()


def make_repo(root: Path, slug: str, *, filename: str = "marker.txt") -> Path:
    root.mkdir(parents=True)
    subprocess.run(["git", "init", "--initial-branch=main", str(root)], check=True, capture_output=True)
    git(root, "config", "user.email", "test@example.invalid")
    git(root, "config", "user.name", "Repo Orientation Test")
    (root / filename).write_text("original content\n", encoding="utf-8")
    git(root, "add", filename)
    git(root, "commit", "-m", "initial")
    git(root, "remote", "add", "origin", f"https://github.com/{slug}.git")
    return root


def write_registry(path: Path, *, repo: str = "acme/widget", owner: str = "widget") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "components:\n"
        "  widget:\n"
        f"    repo: {repo}\n"
        "    boundaries:\n"
        f"      owns: [{owner}]\n"
        "      not_here: [other responsibility]\n",
        encoding="utf-8",
    )
    return path


class RepositoryOrientationTests(unittest.TestCase):
    def test_non_github_host_cannot_impersonate_the_declared_repository(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            registry = write_registry(base / "components.yaml")
            repo = make_repo(base / "checkout", "acme/widget")
            git(repo, "remote", "set-url", "origin", "https://notgithub.com/acme/widget.git")
            packet = resolve_context(
                needs=[InformationNeed(id="path", description="marker", kind="exact_path", path="marker.txt")],
                canonical_repo="widget", registry_path=registry, project_root=repo,
                candidate_roots=[repo], allow_fff=False, allow_qmd=False, use_cache=False,
            )
            self.assertNotEqual(packet.measurements["repo_orientation"]["status"], "ready")
            self.assertTrue(packet.unresolved_gaps)

    def test_pinned_z0_schema_less_registry_component_shape(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            registry = base / "z0" / "registry" / "components.yaml"
            registry.parent.mkdir(parents=True)
            registry.write_text(
                "components:\n"
                "  z0intelligence:\n"
                "    name: z0intelligence\n"
                "    repo: kvnloo/z0intelligence\n"
                "    kind: intelligence\n"
                "    status: experimental\n"
                "    boundaries:\n"
                "      owns: [personal policy, provenance-backed context resolution]\n"
                "      not_here: [source memory databases, harness execution]\n",
                encoding="utf-8",
            )
            repo = make_repo(base / "checkout", "kvnloo/z0intelligence")
            packet = resolve_context(
                needs=[InformationNeed(id="path", description="marker", kind="exact_path", path="marker.txt")],
                canonical_repo="kvnloo/z0intelligence",
                registry_path=registry,
                candidate_roots=[repo],
                allow_fff=False,
                allow_qmd=False,
                use_cache=False,
            )
            orientation = packet.measurements["repo_orientation"]
            self.assertEqual(orientation["status"], "ready")
            self.assertEqual(orientation["component_id"], "z0intelligence")
            self.assertEqual(orientation["repo_slug"], "kvnloo/z0intelligence")
            self.assertEqual(orientation["boundaries"]["owns"], ["personal policy", "provenance-backed context resolution"])
            self.assertEqual(orientation["boundaries"]["not_here"], ["source memory databases", "harness execution"])

    def test_canonical_component_selects_matching_checkout_and_records_epochs(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            registry = write_registry(base / "z0" / "registry" / "components.yaml")
            expected = make_repo(base / "checkouts" / "widget", "acme/widget")
            distractor = make_repo(base / "checkouts" / "other", "acme/other")

            packet = resolve_context(
                needs=[InformationNeed(id="path", description="marker", kind="exact_path", path="marker.txt")],
                canonical_repo="widget",
                registry_path=registry,
                candidate_roots=[expected, distractor],
                allow_fff=False,
                allow_qmd=False,
                use_cache=False,
            )

            orientation = packet.measurements["repo_orientation"]
            self.assertEqual(orientation["status"], "ready")
            self.assertEqual(orientation["component_id"], "widget")
            self.assertEqual(orientation["repo_slug"], "acme/widget")
            self.assertEqual(Path(orientation["root"]), expected.resolve())
            self.assertFalse(orientation["worktree"])
            self.assertEqual(orientation["origin_slug"], "acme/widget")
            self.assertTrue(orientation["git_head"])
            self.assertEqual(orientation["branch"], "main")
            self.assertTrue(orientation["worktree_fingerprint"])
            self.assertEqual(orientation["boundaries"]["owns"], ["widget"])
            self.assertEqual(orientation["boundaries"]["not_here"], ["other responsibility"])
            self.assertEqual(orientation["registry_representation"], "canonical_components_mapping_without_schema")
            self.assertEqual(orientation["registry_schema"], "components_mapping_without_schema")
            self.assertTrue(orientation["registry_source_version"].startswith("sha256:"))
            self.assertEqual(len(packet.evidence), 1)
            self.assertIn(f"repo:{expected.resolve()}", packet.recipe.source_epochs)
            self.assertIn(f"registry:{registry.resolve()}", packet.recipe.source_epochs)
            self.assertTrue(any(op["op"] == "repo_orientation" for op in packet.recipe.operations))
            self.assertEqual(packet.measurements["coverage"], "complete")

    def test_explicit_matching_project_root_wins_when_multiple_worktrees_exist(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            registry = write_registry(base / "registry" / "components.yaml")
            main = make_repo(base / "repo", "acme/widget")
            linked = base / "worktrees" / "topic"
            linked.parent.mkdir()
            git(main, "worktree", "add", "-b", "topic", str(linked))

            packet = resolve_context(
                needs=[InformationNeed(id="path", description="marker", kind="exact_path", path="marker.txt")],
                project_root=linked,
                canonical_repo="acme/widget",
                registry_path=registry,
                candidate_roots=[main],
                allow_fff=False,
                allow_qmd=False,
                use_cache=False,
            )

            orientation = packet.measurements["repo_orientation"]
            self.assertEqual(orientation["status"], "ready")
            self.assertEqual(Path(orientation["root"]), linked.resolve())
            self.assertEqual(orientation["branch"], "topic")
            self.assertTrue(orientation["worktree"])
            self.assertFalse(packet.unresolved_gaps)

    def test_explicit_project_subdirectory_orients_to_its_git_root(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            registry = write_registry(base / "registry" / "components.yaml")
            root = make_repo(base / "repo", "acme/widget")
            nested = root / "src" / "package"
            nested.mkdir(parents=True)

            packet = resolve_context(
                needs=[InformationNeed(id="path", description="marker", kind="exact_path", path="marker.txt")],
                project_root=nested,
                canonical_repo="widget",
                registry_path=registry,
                candidate_roots=[],
                allow_fff=False,
                allow_qmd=False,
                use_cache=False,
            )

            orientation = packet.measurements["repo_orientation"]
            self.assertEqual(orientation["status"], "ready")
            self.assertEqual(Path(orientation["root"]), root.resolve())
            self.assertTrue(packet.evidence)

    def test_ambiguous_worktrees_are_a_gap_without_a_guessed_root(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            registry = write_registry(base / "registry" / "components.yaml")
            main = make_repo(base / "repo", "acme/widget")
            linked = base / "worktrees" / "topic"
            linked.parent.mkdir()
            git(main, "worktree", "add", "-b", "topic", str(linked))

            packet = resolve_context(
                query="orientation",
                canonical_repo="widget",
                registry_path=registry,
                candidate_roots=[main],
                allow_fff=False,
                allow_qmd=False,
                use_cache=False,
            )

            orientation = packet.measurements["repo_orientation"]
            self.assertEqual(orientation["status"], "ambiguous")
            self.assertIsNone(orientation["root"])
            self.assertTrue(any("ambiguous" in gap for gap in packet.unresolved_gaps))

    def test_wrong_explicit_root_blocks_otherwise_valid_candidates(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            registry = write_registry(base / "registry" / "components.yaml")
            expected = make_repo(base / "expected", "acme/widget")
            wrong = make_repo(base / "wrong", "acme/other")

            packet = resolve_context(
                needs=[InformationNeed(id="path", description="marker", kind="exact_path", path="marker.txt")],
                project_root=wrong,
                canonical_repo="widget",
                registry_path=registry,
                candidate_roots=[expected, wrong],
                allow_fff=False,
                allow_qmd=False,
                use_cache=False,
            )

            self.assertEqual(packet.measurements["repo_orientation"]["status"], "wrong_root")
            self.assertIsNone(packet.measurements["repo_orientation"]["root"])
            self.assertFalse(packet.evidence)
            self.assertTrue(any("wrong" in gap for gap in packet.unresolved_gaps))

    def test_unresolvable_explicit_root_does_not_fall_back_to_other_candidate(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            registry = write_registry(base / "registry" / "components.yaml")
            expected = make_repo(base / "expected", "acme/widget")
            first = base / "bad-root-a"
            second = base / "bad-root-b"
            first.symlink_to(second)
            second.symlink_to(first)

            packet = resolve_context(
                query="orientation",
                project_root=first,
                canonical_repo="widget",
                registry_path=registry,
                candidate_roots=[expected],
                allow_fff=False,
                allow_qmd=False,
                use_cache=False,
            )

            orientation = packet.measurements["repo_orientation"]
            self.assertEqual(orientation["status"], "wrong_root")
            self.assertIsNone(orientation["root"])
            self.assertFalse(packet.evidence)
            self.assertTrue(any("bad-root-a" in error for error in orientation["candidate_root_errors"]))

    def test_missing_registry_or_checkout_is_unavailable_and_never_uses_cwd(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            root = make_repo(base / "repo", "acme/widget")
            packet = resolve_context(
                query="orientation",
                canonical_repo="widget",
                registry_path=base / "missing.yaml",
                candidate_roots=[root],
                allow_fff=False,
                allow_qmd=False,
                use_cache=False,
            )
            self.assertEqual(packet.measurements["repo_orientation"]["status"], "unavailable")
            self.assertIsNone(packet.measurements["repo_orientation"]["root"])
            self.assertTrue(any("registry" in gap for gap in packet.unresolved_gaps))

            registry = write_registry(base / "registry" / "components.yaml")
            distractor = make_repo(base / "distractor", "acme/other")
            missing = resolve_context(
                query="orientation",
                canonical_repo="widget",
                registry_path=registry,
                candidate_roots=[distractor],
                allow_fff=False,
                allow_qmd=False,
                use_cache=False,
            )
            self.assertEqual(missing.measurements["repo_orientation"]["status"], "missing")
            self.assertIsNone(missing.measurements["repo_orientation"]["root"])
            self.assertTrue(any("no candidate checkout" in gap for gap in missing.unresolved_gaps))

    def test_unresolvable_registry_path_is_a_gap_not_an_exception(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            first = base / "registry-a.yaml"
            second = base / "registry-b.yaml"
            first.symlink_to(second)
            second.symlink_to(first)

            packet = resolve_context(
                query="orientation",
                canonical_repo="widget",
                registry_path=first,
                candidate_roots=[],
                allow_fff=False,
                allow_qmd=False,
                use_cache=False,
            )

            orientation = packet.measurements["repo_orientation"]
            self.assertEqual(orientation["status"], "unavailable")
            self.assertIsNone(orientation["root"])
            self.assertTrue(any("registry" in gap.lower() for gap in packet.unresolved_gaps))

    def test_generated_registry_requires_explicit_format_and_keeps_provenance(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            repo = make_repo(base / "repo", "acme/widget")
            generated = base / "zer0.registry.yaml"
            generated.write_text(
                "version: 2\necosystem: zer0\ncanonical_repo: acme/z0\n"
                "generated_from: [registry/*.yaml]\ncomponents:\n"
                "  widget:\n    repo: acme/widget\n    owns: [widget]\n",
                encoding="utf-8",
            )

            unsupported = resolve_context(
                query="orientation",
                canonical_repo="widget",
                registry_path=generated,
                candidate_roots=[repo],
                allow_fff=False,
                allow_qmd=False,
                use_cache=False,
            )
            self.assertEqual(unsupported.measurements["repo_orientation"]["status"], "unavailable")

            packet = resolve_context(
                query="orientation",
                canonical_repo="widget",
                registry_path=generated,
                registry_format="generated",
                candidate_roots=[repo],
                allow_fff=False,
                allow_qmd=False,
                use_cache=False,
            )
            orientation = packet.measurements["repo_orientation"]
            self.assertEqual(orientation["status"], "ready")
            self.assertEqual(orientation["registry_format"], "generated")
            self.assertEqual(orientation["registry_representation"], "generated_flattened_v2")
            self.assertEqual(orientation["registry_canonical_repo"], "acme/z0")
            self.assertEqual(orientation["registry_generated_from"], ["registry/*.yaml"])
            self.assertTrue(orientation["registry_source_version"].startswith("sha256:"))

    def test_worktree_fingerprint_tracks_same_size_same_mtime_dirty_and_untracked_edits(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            registry = write_registry(base / "registry" / "components.yaml")
            root = make_repo(base / "repo", "acme/widget")

            def orient():
                return resolve_context(
                    query="orientation",
                    canonical_repo="widget",
                    registry_path=registry,
                    candidate_roots=[root],
                    allow_fff=False,
                    allow_qmd=False,
                    use_cache=False,
                )

            first_packet = orient()
            first = first_packet.measurements["repo_orientation"]
            head = first["git_head"]
            marker = root / "marker.txt"
            old_stat = marker.stat()
            marker.write_text("modified content\n", encoding="utf-8")
            os.utime(marker, ns=(old_stat.st_atime_ns, old_stat.st_mtime_ns))
            dirty_packet = orient()
            dirty = dirty_packet.measurements["repo_orientation"]
            self.assertEqual(dirty["git_head"], head)
            self.assertNotEqual(dirty["worktree_fingerprint"], first["worktree_fingerprint"])
            self.assertNotEqual(
                first_packet.recipe.source_epochs[f"worktree:{root.resolve()}"],
                dirty_packet.recipe.source_epochs[f"worktree:{root.resolve()}"],
            )

            untracked = root / "new.txt"
            untracked.write_text("untracked A\n", encoding="utf-8")
            untracked_stat = untracked.stat()
            added_packet = orient()
            added = added_packet.measurements["repo_orientation"]
            untracked.write_text("untracked B\n", encoding="utf-8")
            os.utime(untracked, ns=(untracked_stat.st_atime_ns, untracked_stat.st_mtime_ns))
            edited_packet = orient()
            edited = edited_packet.measurements["repo_orientation"]
            self.assertEqual(edited["git_head"], head)
            self.assertNotEqual(edited["worktree_fingerprint"], added["worktree_fingerprint"])
            self.assertNotEqual(edited["worktree_fingerprint"], first["worktree_fingerprint"])

    def test_uninspectable_candidate_blocks_unpinned_sole_match_but_is_recorded_when_pinned(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            registry = write_registry(base / "registry" / "components.yaml")
            root = make_repo(base / "repo", "acme/widget")
            missing = base / "missing-checkout"

            unpinned = resolve_context(
                query="orientation",
                canonical_repo="widget",
                registry_path=registry,
                candidate_roots=[root, missing],
                allow_fff=False,
                allow_qmd=False,
                use_cache=False,
            ).measurements["repo_orientation"]

            self.assertEqual(unpinned["status"], "unavailable")
            self.assertIsNone(unpinned["root"])
            self.assertEqual(unpinned["selection_basis"], None)
            self.assertTrue(any("missing-checkout" in error for error in unpinned["candidate_root_errors"]))

            pinned = resolve_context(
                query="orientation",
                project_root=root,
                canonical_repo="widget",
                registry_path=registry,
                candidate_roots=[missing],
                allow_fff=False,
                allow_qmd=False,
                use_cache=False,
            ).measurements["repo_orientation"]

            self.assertEqual(pinned["status"], "ready")
            self.assertEqual(pinned["selection_basis"], "explicit_project_root")
            self.assertEqual(Path(pinned["root"]), root.resolve())
            self.assertTrue(any("missing-checkout" in error for error in pinned["candidate_root_errors"]))

    def test_broken_git_metadata_is_an_uninspectable_candidate(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            registry = write_registry(base / "registry" / "components.yaml")
            matching = make_repo(base / "repo", "acme/widget")
            stale = base / "stale-checkout"
            stale.mkdir()
            (stale / ".git").symlink_to("missing-git-directory")

            orientation = resolve_context(
                query="orientation",
                canonical_repo="widget",
                registry_path=registry,
                candidate_roots=[matching, stale],
                allow_fff=False,
                allow_qmd=False,
                use_cache=False,
            ).measurements["repo_orientation"]

            self.assertEqual(orientation["status"], "unavailable")
            self.assertIsNone(orientation["root"])
            self.assertTrue(any("stale-checkout" in error for error in orientation["candidate_root_errors"]))

    def test_worktree_fingerprint_tracks_mode_changes_with_same_git_status_and_bytes(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            registry = write_registry(base / "registry" / "components.yaml")
            root = make_repo(base / "repo", "acme/widget")
            marker = root / "marker.txt"
            marker.write_text("same dirty bytes\n", encoding="utf-8")

            def orient():
                return resolve_context(
                    query="orientation",
                    canonical_repo="widget",
                    registry_path=registry,
                    candidate_roots=[root],
                    allow_fff=False,
                    allow_qmd=False,
                    use_cache=False,
                ).measurements["repo_orientation"]

            os.chmod(marker, 0o744)
            first = orient()
            os.chmod(marker, 0o755)
            second = orient()

            self.assertTrue(first["dirty"] and second["dirty"])
            self.assertNotEqual(first["worktree_fingerprint"], second["worktree_fingerprint"])

    def test_worktree_fingerprint_qualifies_deleted_tracked_content(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            registry = write_registry(base / "registry" / "components.yaml")
            root = make_repo(base / "repo", "acme/widget")

            def orient():
                return resolve_context(
                    query="orientation",
                    canonical_repo="widget",
                    registry_path=registry,
                    candidate_roots=[root],
                    allow_fff=False,
                    allow_qmd=False,
                    use_cache=False,
                ).measurements["repo_orientation"]

            before = orient()
            (root / "marker.txt").unlink()
            deleted = orient()

            self.assertEqual(deleted["status"], "ready")
            self.assertTrue(deleted["dirty"])
            self.assertTrue(deleted["worktree_fingerprint"])
            self.assertNotEqual(before["worktree_fingerprint"], deleted["worktree_fingerprint"])

    def test_worktree_fingerprint_tracks_dirty_submodule_content(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            registry = write_registry(base / "registry" / "components.yaml")
            child = make_repo(base / "child", "acme/child", filename="nested.txt")
            root = make_repo(base / "repo", "acme/widget", filename="README.md")
            subprocess.run(
                [
                    "git", "-C", str(root), "-c", "protocol.file.allow=always",
                    "submodule", "add", str(child), "vendor/child",
                ],
                check=True,
                capture_output=True,
                text=True,
            )
            git(root, "add", ".gitmodules", "vendor/child")
            git(root, "commit", "-m", "add local submodule")
            nested = root / "vendor" / "child" / "nested.txt"

            def orient():
                return resolve_context(
                    query="orientation",
                    canonical_repo="widget",
                    registry_path=registry,
                    candidate_roots=[root],
                    allow_fff=False,
                    allow_qmd=False,
                    use_cache=False,
                ).measurements["repo_orientation"]

            nested.write_text("first dirty content\n", encoding="utf-8")
            first_stat = nested.stat()
            first = orient()
            nested.write_text("other dirty content\n", encoding="utf-8")
            os.utime(nested, ns=(first_stat.st_atime_ns, first_stat.st_mtime_ns))
            second = orient()

            self.assertTrue(first["dirty"] and second["dirty"])
            self.assertNotEqual(first["worktree_fingerprint"], second["worktree_fingerprint"])

            (nested.parent / ".git").unlink()
            unavailable = orient()
            self.assertEqual(unavailable["status"], "partial")
            self.assertIsNone(unavailable["worktree_fingerprint"])
            self.assertTrue(any("submodule" in error.lower() for error in unavailable["gaps"]))

    def test_registry_content_change_advances_registry_epoch(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            registry = write_registry(base / "registry" / "components.yaml")
            root = make_repo(base / "repo", "acme/widget")

            def orient():
                return resolve_context(
                    query="orientation",
                    canonical_repo="widget",
                    registry_path=registry,
                    candidate_roots=[root],
                    allow_fff=False,
                    allow_qmd=False,
                    use_cache=False,
                )

            before_packet = orient()
            before = before_packet.measurements["repo_orientation"]
            write_registry(registry, owner="changed boundary")
            after_packet = orient()
            after = after_packet.measurements["repo_orientation"]
            self.assertNotEqual(before["registry_source_version"], after["registry_source_version"])
            registry_epoch = f"registry:{registry.resolve()}"
            self.assertNotEqual(
                before_packet.recipe.source_epochs[registry_epoch],
                after_packet.recipe.source_epochs[registry_epoch],
            )


if __name__ == "__main__":
    unittest.main()
