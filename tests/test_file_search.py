from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from z0int import file_search


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
        with tempfile.TemporaryDirectory() as tmp, mock.patch(
            "z0int.file_search._load_fff", return_value=_FFF
        ):
            one = file_search.search_repository(tmp, "context")
            two = file_search.search_repository(tmp, "context")
            self.assertEqual(_Finder.created, 1)
            self.assertFalse(one["reused"])
            self.assertTrue(two["reused"])
            self.assertTrue(_Finder.instances[0].kwargs["watch"])
            self.assertTrue(_Finder.instances[0].kwargs["ai_mode"])
            self.assertTrue(_Finder.instances[0].kwargs["enable_content_indexing"])

    def test_normalizes_path_and_content_hits(self):
        with tempfile.TemporaryDirectory() as tmp, mock.patch(
            "z0int.file_search._load_fff", return_value=_FFF
        ):
            out = file_search.search_repository(tmp, "find_context", kind="exact_symbol")
            self.assertEqual(out["status"], "ready")
            self.assertEqual(out["path_hits"][0]["path"], "src/main.py")
            self.assertEqual(out["content_hits"][0]["line"], 7)
            self.assertTrue(out["content_hits"][0]["is_definition"])
            self.assertEqual(out["grep_mode"], "plain")

    def test_watcher_bumps_source_epoch(self):
        with tempfile.TemporaryDirectory() as tmp, mock.patch(
            "z0int.file_search._load_fff", return_value=_FFF
        ):
            first = file_search.search_repository(tmp, "context")
            finder = _Finder.instances[0]
            assert finder.callback is not None
            finder.callback([object()])
            second = file_search.search_repository(tmp, "context")
            self.assertEqual(second["index_epoch"], first["index_epoch"] + 1)

    def test_missing_binding_fails_open(self):
        with tempfile.TemporaryDirectory() as tmp, mock.patch(
            "z0int.file_search._load_fff", return_value=None
        ):
            out = file_search.search_repository(tmp, "context")
            self.assertEqual(out["status"], "absent")
            self.assertEqual(out["content_hits"], [])


if __name__ == "__main__":
    unittest.main()
