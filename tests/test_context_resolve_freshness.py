"""Production-path freshness and scope admission regressions for #66."""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from z0int.context_resolve import (
    InformationNeed,
    _file_version,
    _git_worktree_revision,
    project_to_aodl_fields,
    resolve_context,
)
from z0int.memory_contract import MemoryScope


REQUEST = MemoryScope(user="u1", project="p1", repo="r1", task="t1")
ANCESTOR = MemoryScope(user="u1", project="p1")
SIBLING = MemoryScope(user="u1", project="p1", repo="r2", task="t1")
CLOCK_A = 1_790_000_000_100_000_000
CLOCK_B = 1_790_000_000_900_000_000


def need(
    name: str = "source.py",
    *,
    scope: MemoryScope | None = REQUEST,
    required: bool = True,
) -> InformationNeed:
    return InformationNeed(
        id="source",
        description="source-backed code",
        kind="exact_path",
        path=name,
        scope=scope,
        required=required,
    )


def resolve(root: Path, item: InformationNeed | None = None, **revisions):
    params = {
        "needs": [item or need()],
        "project_root": root,
        "request_scope": REQUEST,
        "workstream_revision": "work-1",
        "compiler_revision": "compiler-1",
        "policy_revision": "policy-1",
        "allow_fff": False,
        "allow_qmd": False,
        "use_cache": False,
    }
    params.update(revisions)
    return resolve_context(**params)


def git(root: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-C", str(root), *args],
        capture_output=True, check=True, timeout=10,
    )


class ContextSourceFreshnessTests(unittest.TestCase):
    def test_same_length_same_second_change_invalidates_evidence_and_snapshot(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "source.py"
            source.write_bytes(b"value = 1\n")
            os.utime(source, ns=(CLOCK_A, CLOCK_A))
            first = resolve(root)
            version_before = _file_version(source)

            source.write_bytes(b"value = 2\n")
            os.utime(source, ns=(CLOCK_B, CLOCK_B))
            second = resolve(root)

            self.assertEqual(len(b"value = 1\n"), len(b"value = 2\n"))
            self.assertEqual(CLOCK_A // 1_000_000_000, CLOCK_B // 1_000_000_000)
            self.assertTrue(first.evidence[0].source_version.startswith("sha256:"))
            self.assertNotEqual(version_before, _file_version(source))
            self.assertNotEqual(first.evidence[0].source_version, second.evidence[0].source_version)
            self.assertNotEqual(first.memory_snapshot.snapshot_id, second.memory_snapshot.snapshot_id)
            self.assertIn("value = 1", first.evidence[0].excerpt)
            self.assertIn("value = 2", second.evidence[0].excerpt)

    def test_equal_bytes_with_changed_mtime_keep_identity(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "source.py"
            source.write_bytes(b"value = 1\n")
            os.utime(source, ns=(CLOCK_A, CLOCK_A))
            before = resolve(root)
            os.utime(source, ns=(CLOCK_B, CLOCK_B))
            after = resolve(root)
            self.assertEqual(before.evidence[0].source_version, after.evidence[0].source_version)
            self.assertEqual(before.memory_snapshot.snapshot_id, after.memory_snapshot.snapshot_id)

    def test_atomic_replacement_with_preserved_mtime_and_size(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "source.py"
            source.write_bytes(b"value = 1\n")
            os.utime(source, ns=(CLOCK_A, CLOCK_A))
            before = resolve(root)

            replacement = root / "replacement.tmp"
            replacement.write_bytes(b"value = 2\n")
            os.utime(replacement, ns=(CLOCK_A, CLOCK_A))
            os.replace(replacement, source)
            os.utime(source, ns=(CLOCK_A, CLOCK_A))
            after = resolve(root)
            self.assertNotEqual(before.evidence[0].source_version, after.evidence[0].source_version)
            self.assertEqual(source.stat().st_size, len(b"value = 1\n"))
            self.assertEqual(source.stat().st_mtime_ns, CLOCK_A)

    def test_required_missing_source_is_explicit_gap(self):
        with tempfile.TemporaryDirectory() as tmp:
            packet = resolve(Path(tmp))
            self.assertEqual(packet.evidence, [])
            self.assertTrue(any("missing path" in gap for gap in packet.unresolved_gaps))

    def test_read_change_race_does_not_lend_new_hash_to_old_excerpt(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "source.py"
            source.write_bytes(b"value = 1\n")
            opened = Path.open
            changed = False

            class RacingFile:
                def __init__(self, stream):
                    self.stream = stream

                def __enter__(self):
                    return self

                def __exit__(self, *args):
                    return self.stream.__exit__(*args)

                def fileno(self):
                    return self.stream.fileno()

                def read(self, *args):
                    nonlocal changed
                    original = self.stream.read(*args)
                    if not changed:
                        changed = True
                        fd = os.open(source, os.O_WRONLY | os.O_TRUNC)
                        try:
                            os.write(fd, b"value = 2\n")
                        finally:
                            os.close(fd)
                    return original

            def opening(path: Path, *args, **kwargs):
                stream = opened(path, *args, **kwargs)
                return RacingFile(stream) if path == source else stream

            with mock.patch.object(Path, "open", new=opening):
                packet = resolve(root)
            self.assertTrue(changed)
            self.assertEqual(packet.evidence, [])
            self.assertTrue(packet.unresolved_gaps)
            self.assertNotIn("value = 1", json.dumps(packet.to_dict()))

    def test_stale_index_snippet_never_inherits_new_file_hash(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "source.py"
            source.write_text("def trusted():\n    return 1\n", encoding="utf-8")
            indexed = {
                "status": "ready",
                "package_version": "v0",
                "index_epoch": 1,
                "content_hits": [{
                    "path": "source.py", "line": 1, "text": "def trusted():",
                    "context_before": [], "context_after": ["    return 1"],
                }],
                "path_hits": [],
            }
            item = InformationNeed(
                id="symbol", description="trusted", kind="exact_symbol",
                symbol="trusted", scope=REQUEST,
            )
            with mock.patch("z0int.context_resolve._fff_search_repository", return_value=indexed):
                before = resolve(root, item, allow_fff=True)
                source.write_text("def replaced():\n    return 2\n", encoding="utf-8")
                after = resolve(root, item, allow_fff=True)
            self.assertEqual(len(before.evidence), 1)
            self.assertEqual(after.evidence, [])
            self.assertTrue(after.unresolved_gaps)
            self.assertNotIn("trusted():", json.dumps(after.to_dict()))
            self.assertTrue(before.evidence[0].source_version.startswith("sha256:"))

    def test_unscoped_and_sibling_sources_reveal_no_payload_or_metadata(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            secret = root / "source.py"
            secret.write_text("TOP_SECRET_SIBLING_PAYLOAD\n", encoding="utf-8")
            for item, request_scope in (
                (need(scope=None), REQUEST),
                (need(scope=SIBLING), REQUEST),
                (need(scope=REQUEST), None),
                (need(scope=MemoryScope()), REQUEST),
            ):
                packet = resolve(root, item, request_scope=request_scope)
                serialized = json.dumps(packet.to_dict())
                self.assertEqual(packet.evidence, [])
                self.assertIsNone(packet.recipe)
                self.assertIsNone(packet.memory_snapshot)
                self.assertEqual(packet.needs, [])
                self.assertEqual(packet.unresolved_gaps, ["scope: unavailable or excluded"])
                self.assertNotIn("source.py", serialized)
                self.assertNotIn(str(root), serialized)
                self.assertNotIn("TOP_SECRET_SIBLING_PAYLOAD", serialized)

    def test_authorized_ancestor_is_visible_but_not_action_authority(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "source.py").write_text("answer = 1\n", encoding="utf-8")
            packet = resolve(root, need(scope=ANCESTOR))
            self.assertEqual(len(packet.evidence), 1)
            self.assertFalse(packet.unresolved_gaps)
            self.assertEqual(packet.memory_snapshot.scope, REQUEST)
            projection = project_to_aodl_fields(packet)
            self.assertIn("memory_snapshot", projection["provenance"])
            self.assertNotIn("authorizes_action", packet.to_dict())

    def test_scope_check_prevents_outside_root_hydration(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "repo"
            root.mkdir()
            outside = Path(tmp) / "outside.py"
            outside.write_text("OFF_LIMITS\n", encoding="utf-8")
            packet = resolve(root, need("../outside.py"))
            self.assertEqual(packet.evidence, [])
            self.assertNotIn("OFF_LIMITS", json.dumps(packet.to_dict()))

    def test_porcelain_z_rename_and_copy_hash_second_raw_pathname(self):
        """Rename/copy's second NUL field is not another XY-prefixed record."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for name in ("renamed.py", "old.py", "copied.py", "original.py"):
                (root / name).write_text(f"{name}: first\n", encoding="utf-8")

            def revision():
                # Synthetic status isolates the -z parser. In a real rename,
                # the old source is typically absent; a copy retains its source.
                head = subprocess.CompletedProcess([], 0, stdout=b"abc123\n")
                status = subprocess.CompletedProcess(
                    [], 0,
                    stdout=b"R  renamed.py\0old.py\0C  copied.py\0original.py\0",
                )
                with mock.patch(
                    "z0int.context_resolve.subprocess.run",
                    side_effect=[head, status],
                ):
                    return _git_worktree_revision(root)

            prior = revision()
            self.assertIsNotNone(prior)
            (root / "old.py").write_text("rename source changed\n", encoding="utf-8")
            rename_changed = revision()
            self.assertNotEqual(prior, rename_changed)
            (root / "original.py").write_text("copy source changed\n", encoding="utf-8")
            self.assertNotEqual(rename_changed, revision())
            (root / "renamed.py").write_text("rename target changed\n", encoding="utf-8")
            changed_target = revision()
            (root / "renamed.py").write_text("another target change\n", encoding="utf-8")
            self.assertNotEqual(changed_target, revision())
            copied = revision()
            (root / "copied.py").write_text("copied target changed\n", encoding="utf-8")
            self.assertNotEqual(copied, revision())

    def test_porcelain_z_malformed_second_pathname_fails_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            head = subprocess.CompletedProcess([], 0, stdout=b"abc123\n")
            status = subprocess.CompletedProcess([], 0, stdout=b"R  renamed.py\0")
            with mock.patch(
                "z0int.context_resolve.subprocess.run",
                side_effect=[head, status],
            ):
                self.assertIsNone(_git_worktree_revision(root))

    def test_git_symlink_target_changes_are_content_bound(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            link = root / "source-link"
            try:
                link.symlink_to("one")
            except (OSError, NotImplementedError):
                self.skipTest("symlinks unavailable on this host")

            def revision():
                head = subprocess.CompletedProcess([], 0, stdout=b"abc123\n")
                status = subprocess.CompletedProcess(
                    [], 0, stdout=b" M source-link\0",
                )
                with mock.patch(
                    "z0int.context_resolve.subprocess.run",
                    side_effect=[head, status],
                ):
                    return _git_worktree_revision(root)

            before = revision()
            link.unlink()
            link.symlink_to("two")
            self.assertNotEqual(before, revision())

    def test_git_dirty_and_untracked_state_change_epoch_and_snapshot(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            git(root, "init", "-q")
            git(root, "config", "user.email", "fixture@example.invalid")
            git(root, "config", "user.name", "Fixture")
            source = root / "source.py"
            source.write_text("value = 1\n", encoding="utf-8")
            git(root, "add", "source.py")
            git(root, "commit", "-qm", "fixture")
            base = resolve(root, require_git_revision=True)
            self.assertFalse(base.unresolved_gaps)
            epoch = base.recipe.source_epochs["git"]

            untracked = root / "untracked.txt"
            untracked.write_text("first\n", encoding="utf-8")
            new = resolve(root, require_git_revision=True)
            self.assertNotEqual(epoch, new.recipe.source_epochs["git"])
            self.assertNotEqual(base.memory_snapshot.snapshot_id, new.memory_snapshot.snapshot_id)

            untracked.write_text("second\n", encoding="utf-8")
            changed = resolve(root, require_git_revision=True)
            self.assertNotEqual(new.recipe.source_epochs["git"], changed.recipe.source_epochs["git"])
            source.write_text("value = 2\n", encoding="utf-8")
            dirty = resolve(root, require_git_revision=True)
            self.assertNotEqual(changed.recipe.source_epochs["git"], dirty.recipe.source_epochs["git"])
            self.assertNotEqual(changed.evidence[0].source_version, dirty.evidence[0].source_version)


if __name__ == "__main__":
    unittest.main()
