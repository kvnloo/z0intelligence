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
        self.total_files_searched = 4
        self.total_files = 4
        self.filtered_file_count = 0
        self.has_more = False
        self.regex_fallback_error = None


class _Progress:
    is_scanning = False
    is_watcher_ready = True
    is_warmup_complete = True


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
            self.assertEqual(out["coverage"], "complete")
            self.assertFalse(out["scanning"])
            self.assertTrue(out["warmup_complete"])
            self.assertRegex(out["generation"], r"^0\.test:resident=\d+:epoch=0$")
            self.assertEqual(out["content_scan"]["files_searched"], 4)

    def test_filtered_file_count_is_eligible_scope_not_an_omission_count(self):
        class EligibleFinder(_Finder):
            def grep(self, query, **kwargs):
                result = _Result([_Match()])
                result.total_files = 3
                result.filtered_file_count = 2
                result.total_files_searched = 2
                return result

        class EligibleFFF:
            __version__ = "0.11.0"
            FileFinder = EligibleFinder

        with tempfile.TemporaryDirectory() as tmp, mock.patch(
            "z0int.file_search._load_fff", return_value=EligibleFFF
        ):
            complete = file_search.search_repository(tmp, "find_context", kind="exact_symbol")
            self.assertEqual(complete["coverage"], "complete")
            self.assertEqual(complete["content_scan"]["eligible_files"], 2)
            self.assertEqual(complete["content_scan"]["total_files"], 3)

        class IncompleteFinder(EligibleFinder):
            def grep(self, query, **kwargs):
                result = super().grep(query, **kwargs)
                result.total_files_searched = 1
                return result

        class IncompleteFFF:
            __version__ = "0.11.0"
            FileFinder = IncompleteFinder

        with tempfile.TemporaryDirectory() as tmp, mock.patch(
            "z0int.file_search._load_fff", return_value=IncompleteFFF
        ):
            incomplete = file_search.search_repository(tmp, "find_context", kind="exact_symbol")
            self.assertEqual(incomplete["coverage"], "partial")
            self.assertIn("content_scan_incomplete", incomplete["content_scan"]["partial_reasons"])

    def test_missing_scan_scope_counts_cannot_qualify_complete_coverage(self):
        class UncountedFinder(_Finder):
            def grep(self, query, **kwargs):
                result = _Result([_Match()])
                del result.total_files_searched
                del result.total_files
                del result.filtered_file_count
                return result

        class UncountedFFF:
            __version__ = "unknown"
            FileFinder = UncountedFinder

        with tempfile.TemporaryDirectory() as tmp, mock.patch(
            "z0int.file_search._load_fff", return_value=UncountedFFF
        ):
            out = file_search.search_repository(tmp, "find_context", kind="exact_symbol")

            self.assertEqual(out["coverage"], "partial")
            self.assertIn("content_scan_counts_unavailable", out["content_scan"]["partial_reasons"])

    def test_partial_warming_search_exposes_scan_state_and_keeps_hits(self):
        class WarmingFinder(_Finder):
            def __init__(self, root, **kwargs):
                super().__init__(root, **kwargs)
                self.scan_progress.is_scanning = True
                self.scan_progress.is_watcher_ready = False
                self.scan_progress.is_warmup_complete = False

            def wait_for_scan_blocking(self, timeout_ms=5000):
                return False

        class WarmingFFF:
            __version__ = "0.11.0"
            FileFinder = WarmingFinder

        with tempfile.TemporaryDirectory() as tmp, mock.patch(
            "z0int.file_search._load_fff", return_value=WarmingFFF
        ):
            out = file_search.search_repository(tmp, "find_context", kind="exact_symbol")
            self.assertEqual(out["status"], "warming")
            self.assertEqual(out["coverage"], "partial")
            self.assertTrue(out["scanning"])
            self.assertFalse(out["warmup_complete"])
            self.assertEqual(len(out["content_hits"]), 1)

    def test_query_error_retains_generation_and_is_unavailable(self):
        class BrokenFinder(_Finder):
            def grep(self, query, **kwargs):
                raise RuntimeError("grep transport failed")

        class BrokenFFF:
            __version__ = "0.11.0"
            FileFinder = BrokenFinder

        with tempfile.TemporaryDirectory() as tmp, mock.patch(
            "z0int.file_search._load_fff", return_value=BrokenFFF
        ):
            out = file_search.search_repository(tmp, "find_context", kind="exact_symbol")
            self.assertEqual(out["status"], "error")
            self.assertEqual(out["coverage"], "unavailable")
            self.assertIn("grep transport failed", out["error"])
            self.assertRegex(out["generation"], r"^0\.11\.0:resident=\d+:epoch=0$")

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

    def test_failed_epoch_subscription_makes_search_coverage_partial(self):
        class UnsubscribedFinder(_Finder):
            def watch(self, pattern, callback):
                raise RuntimeError("watch callback unavailable")

        class UnsubscribedFFF:
            __version__ = "0.11.0"
            FileFinder = UnsubscribedFinder

        with tempfile.TemporaryDirectory() as tmp, mock.patch(
            "z0int.file_search._load_fff", return_value=UnsubscribedFFF
        ):
            out = file_search.search_repository(tmp, "find_context", kind="exact_symbol")
            self.assertEqual(out["coverage"], "partial")
            self.assertFalse(out["generation_reliable"])
            self.assertEqual(out["generation_status"], "unreliable")
            self.assertIn("watch callback unavailable", out["error"])
            self.assertIn("generation_unreliable", out["content_scan"]["partial_reasons"])
            self.assertIn("watch callback unavailable", out["content_scan"]["generation_error"])
            self.assertIsNone(out["content_scan"]["regex_fallback_error"])

    def test_missing_epoch_subscription_handle_is_unreliable(self):
        class NoHandleFinder(_Finder):
            def watch(self, pattern, callback):
                self.callback = callback
                return None

        class NoHandleFFF:
            __version__ = "0.11.0"
            FileFinder = NoHandleFinder

        with tempfile.TemporaryDirectory() as tmp, mock.patch(
            "z0int.file_search._load_fff", return_value=NoHandleFFF
        ):
            out = file_search.search_repository(tmp, "find_context", kind="exact_symbol")

            self.assertEqual(out["generation_status"], "unreliable")
            self.assertFalse(out["generation_reliable"])
            self.assertEqual(out["coverage"], "partial")
            self.assertIn("subscription handle", out["error"])

    def test_epoch_subscription_recovers_after_watcher_startup(self):
        class StartingFinder(_Finder):
            attempts = 0

            def watch(self, pattern, callback):
                self.attempts += 1
                if self.attempts == 1:
                    raise RuntimeError("File system watcher is not ready")
                return super().watch(pattern, callback)

        class StartingFFF:
            __version__ = "0.11.0"
            FileFinder = StartingFinder

        with tempfile.TemporaryDirectory() as tmp, mock.patch(
            "z0int.file_search._load_fff", return_value=StartingFFF
        ):
            first = file_search.search_repository(tmp, "find_context", kind="exact_symbol")
            self.assertFalse(first["generation_reliable"])
            second = file_search.search_repository(tmp, "find_context", kind="exact_symbol")
            self.assertTrue(second["generation_reliable"])
            self.assertEqual(second["coverage"], "complete")

    def test_missing_binding_fails_open(self):
        with tempfile.TemporaryDirectory() as tmp, mock.patch(
            "z0int.file_search._load_fff", return_value=None
        ):
            out = file_search.search_repository(tmp, "context")
            self.assertEqual(out["status"], "absent")
            self.assertEqual(out["content_hits"], [])


if __name__ == "__main__":
    unittest.main()
