from __future__ import annotations

import contextlib
import subprocess
import threading
from concurrent.futures import ThreadPoolExecutor
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from z0int import file_search


@contextlib.contextmanager
def repository():
    with tempfile.TemporaryDirectory() as tmp:
        subprocess.run(["git", "init", "-q", tmp], check=True)
        yield tmp


class _Score:
    total = 99
    exact_match = True
    match_type = "exact"


class _File:
    relative_path = "src/main.py"
    file_name = "main.py"
    git_status = "modified"
    size = 20
    modified = 1


class _Match:
    relative_path = "src/main.py"
    file_name = "main.py"
    git_status = "modified"
    line_content = "def find_context():"
    context_before = ["# context"]
    context_after = ["    pass"]
    line_number = 7
    col = 1
    is_definition = True
    fuzzy_score = None


class _Result:
    def __init__(self, items, scores=None):
        self.items = items
        self.scores = scores or []
        self.total_matched = len(items)


class _Progress:
    is_scanning = False


class _Sub:
    def __init__(self):
        self.active = True

    def unsubscribe(self):
        self.active = False
        return True


class _Finder:
    created = 0
    instances = []

    def __init__(self, root, **kwargs):
        type(self).created += 1
        type(self).instances.append(self)
        self.root = Path(root)
        self.kwargs = kwargs
        self.scan_progress = _Progress()
        self.callback = None
        self.closed = False

    def wait_for_scan_blocking(self, timeout_ms=5000):
        return True

    def watch(self, pattern, callback):
        self.callback = callback
        return _Sub()

    def search(self, query, **kwargs):
        return _Result([_File()], [_Score()])

    def grep(self, query, **kwargs):
        return _Result([_Match()])

    def close(self):
        self.closed = True


class _FFF:
    __version__ = "0.test"
    FileFinder = _Finder


class FileSearchTests(unittest.TestCase):
    def setUp(self):
        _Finder.created = 0
        _Finder.instances = []
        file_search._reset_for_tests()

    def tearDown(self):
        file_search._reset_for_tests()

    def test_reuses_one_resident_finder_per_root(self):
        with repository() as tmp, mock.patch(
            "z0int.file_search._load_fff", return_value=_FFF
        ):
            one = file_search.search_repository(tmp, "context")
            two = file_search.search_repository(tmp, "context")
            self.assertEqual(_Finder.created, 1)
            self.assertFalse(one["reused"])
            self.assertTrue(two["reused"])
            self.assertFalse(_Finder.instances[0].kwargs["watch"])
            self.assertIsNone(_Finder.instances[0].callback)
            self.assertTrue(_Finder.instances[0].kwargs["ai_mode"])
            self.assertTrue(_Finder.instances[0].kwargs["enable_content_indexing"])

    def test_normalizes_path_and_content_hits(self):
        with repository() as tmp, mock.patch(
            "z0int.file_search._load_fff", return_value=_FFF
        ):
            out = file_search.search_repository(tmp, "find_context", kind="exact_symbol")
            self.assertEqual(out["status"], "ready")
            self.assertEqual(out["path_hits"][0]["path"], "src/main.py")
            self.assertEqual(out["content_hits"][0]["line"], 7)
            self.assertTrue(out["content_hits"][0]["is_definition"])
            self.assertEqual(out["grep_mode"], "plain")

    def test_lazy_ttl_rebuild_changes_epoch_without_watcher(self):
        with repository() as tmp, mock.patch(
            "z0int.file_search._load_fff", return_value=_FFF
        ):
            first = file_search.search_repository(tmp, "context")
            finder = _Finder.instances[0]
            file_search._POOL[str(Path(tmp).resolve())].created_at -= 601
            second = file_search.search_repository(tmp, "context")
            self.assertEqual(_Finder.created, 2)
            self.assertTrue(finder.closed)
            self.assertFalse(second["reused"])
            self.assertNotEqual(second["index_epoch"], first["index_epoch"])

    def test_missing_binding_fails_open(self):
        with repository() as tmp, mock.patch(
            "z0int.file_search._load_fff", return_value=None
        ):
            out = file_search.search_repository(tmp, "context")
            self.assertEqual(out["status"], "absent")
            self.assertEqual(out["content_hits"], [])

    def test_non_repository_is_rejected_before_fff_load(self):
        with tempfile.TemporaryDirectory() as tmp, mock.patch(
            "z0int.file_search._load_fff"
        ) as load:
            out = file_search.search_repository(tmp, "context")
            self.assertEqual(out["status"], "rejected_root")
            load.assert_not_called()

    def test_repository_subdirectory_is_rejected(self):
        with repository() as tmp:
            child = Path(tmp) / "src"
            child.mkdir()
            self.assertEqual(
                file_search.search_repository(child, "context")["status"],
                "rejected_root",
            )

    def test_workspace_roots_are_rejected_even_if_git_reports_toplevel(self):
        for root in ("/mnt/zer0models", "/mnt/zer0models/workspace/zer0"):
            with mock.patch("z0int.file_search.subprocess.run") as git:
                with self.assertRaisesRegex(ValueError, "forbidden workspace"):
                    file_search._admit_root(Path(root))
                git.assert_not_called()

    def test_file_count_ceiling_fails_closed(self):
        with repository() as tmp, mock.patch.dict(
            "os.environ", {"Z0INT_FFF_MAX_FILES": "1"}
        ), mock.patch("z0int.file_search._load_fff") as load:
            (Path(tmp) / "one").write_text("one")
            (Path(tmp) / "two").write_text("two")
            with self.assertLogs("z0int.file_search", level="WARNING") as logs:
                out = file_search.search_repository(tmp, "context")
            self.assertEqual(out["status"], "rejected_root")
            self.assertIn("Z0INT_FFF_MAX_FILES=1", out["error"])
            self.assertIn("FFF root rejected", logs.output[0])
            load.assert_not_called()

    def test_ceiling_conservatively_counts_caches_and_nested_repositories(self):
        with repository() as tmp:
            root = Path(tmp)
            (root / "source.py").write_text("pass")
            for name in ("target", "node_modules", ".venv", ".cache", "other"):
                child = root / name
                child.mkdir()
                (child / "generated").write_text("ignored")
            subprocess.run(["git", "init", "-q", str(root / "other")], check=True)
            self.assertEqual(file_search._admit_root(root), 6)

    def test_scan_timeout_closes_before_next_rebuild(self):
        with repository() as tmp, mock.patch(
            "z0int.file_search._load_fff", return_value=_FFF
        ), mock.patch.object(_Finder, "wait_for_scan_blocking", return_value=False):
            result = file_search.search_repository(tmp, "context")
            self.assertEqual(result["status"], "scan_timeout")
            self.assertTrue(_Finder.instances[0].closed)
            self.assertFalse(file_search._POOL)

    def test_concurrent_misses_share_one_serialized_rebuild(self):
        entered = threading.Event()
        release = threading.Event()
        original = file_search._build_resident

        def build(root):
            entered.set()
            self.assertTrue(release.wait(5))
            return original(root)

        with repository() as tmp, mock.patch(
            "z0int.file_search._load_fff", return_value=_FFF
        ), mock.patch("z0int.file_search._build_resident", side_effect=build):
            with ThreadPoolExecutor(max_workers=4) as pool:
                first = pool.submit(file_search.search_repository, tmp, "context")
                try:
                    self.assertTrue(entered.wait(5))
                    others = [
                        pool.submit(file_search.search_repository, tmp, "context")
                        for _ in range(3)
                    ]
                finally:
                    release.set()
                results = [first.result(5)] + [future.result(5) for future in others]
            self.assertEqual(_Finder.created, 1)
            self.assertEqual(sum(not result["reused"] for result in results), 1)

    def test_walk_error_fails_closed(self):
        with repository() as tmp, mock.patch(
            "z0int.file_search.os.walk", side_effect=PermissionError("denied")
        ), mock.patch("z0int.file_search._load_fff") as load:
            result = file_search.search_repository(tmp, "context")
            self.assertEqual(result["status"], "rejected_root")
            load.assert_not_called()


if __name__ == "__main__":
    unittest.main()
