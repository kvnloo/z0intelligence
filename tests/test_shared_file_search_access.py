"""HTTP repository access uses operator configuration, not caller-chosen roots."""
import json
import os
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
import urllib.error
import urllib.request

from z0int.intelligence_service import Handler, Service
from z0int.memory.event_log import EventLog


class SharedFileSearchAccess(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name).resolve()
        self.repository = self.root / "repository"
        self.repository.mkdir()
        (self.repository / "engine.py").write_text("REPOSITORY_EVIDENCE\n")
        self.private = self.root / "repository-private"
        self.private.mkdir()
        (self.private / "key").write_text("PRIVATE_MARKER\n")
        self.state = self.root / "state"
        self.state.mkdir()
        (self.state / "private.txt").write_text("STATE_MARKER\n")
        self.environment = patch.dict(os.environ, {"Z0INT_HOME": str(self.state)})
        self.environment.start()
        os.environ.pop("Z0INT_FILE_SEARCH_ROOTS", None)
        self.server = Service(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}/v1/file-search"

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)
        self.environment.stop()
        self.temporary.cleanup()

    def post(self, root=None, query="engine.py", **fields):
        payload = {
            "repository_root": str(root or self.repository), "query": query,
            "kind": "exact_path", "harness": "codex", **fields,
        }
        request = urllib.request.Request(
            self.url, data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(request, timeout=5) as response:
                return response.status, json.load(response)
        except urllib.error.HTTPError as response:
            with response:
                return response.code, json.load(response)

    def assert_denied(self, root=None, query="engine.py"):
        status, body = self.post(root, query)
        self.assertEqual(status, 403, body)
        self.assertEqual(body, {"error": "file_search_root_denied"})
        self.assertFalse(EventLog().events_path.exists())

    def test_unconfigured_http_search_cannot_read_or_record_host_files(self):
        for root, query in ((self.repository, "engine.py"), (self.private, "key")):
            with self.subTest(root=root):
                self.assert_denied(root, query)

    def test_only_exact_configured_repository_roots_are_accepted(self):
        os.environ["Z0INT_FILE_SEARCH_ROOTS"] = json.dumps([str(self.repository)])
        self.assert_denied(self.private, "key")
        nested = self.repository / "nested"
        nested.mkdir()
        (nested / "engine.py").write_text("NESTED_EVIDENCE\n")
        self.assert_denied(nested)
        for harness in ("omp", "hermes", "codex", "claude-code", "dsh"):
            with self.subTest(harness=harness):
                status, body = self.post(harness=harness)
                self.assertEqual(status, 200, body)
                self.assertIn("REPOSITORY_EVIDENCE", body["packet"]["evidence"][0]["excerpt"])
                self.assertIsNotNone(body["event_id"])
                self.assertEqual(body["harness_id"], harness)

    def test_configured_roots_and_request_roots_resolve_symlinks(self):
        alias = self.root / "alias"
        alias.symlink_to(self.repository, target_is_directory=True)
        os.environ["Z0INT_FILE_SEARCH_ROOTS"] = json.dumps([str(alias)])
        for root in (self.repository, alias):
            with self.subTest(root=root):
                status, body = self.post(root)
                self.assertEqual(status, 200, body)
                self.assertIn("REPOSITORY_EVIDENCE", body["packet"]["evidence"][0]["excerpt"])
        (self.repository / "escape").symlink_to(self.private / "key")
        status, body = self.post(query="escape")
        self.assertEqual(status, 400, body)
        self.assertNotIn("PRIVATE_MARKER", json.dumps(body))

    def test_private_z0_state_is_excluded_even_when_configured(self):
        nested = self.state / "nested"
        nested.mkdir()
        (nested / "engine.py").write_text("STATE_MARKER\n")
        for root, query in ((self.state, "private.txt"), (self.root, "state/private.txt"),
                            (nested, "engine.py")):
            with self.subTest(root=root):
                os.environ["Z0INT_FILE_SEARCH_ROOTS"] = json.dumps([str(root)])
                self.assert_denied(root, query)

    def test_invalid_operator_root_configuration_fails_closed(self):
        for configured in ("not json", "null", '"/tmp"', "{}", "[null]", "[1]", '["relative"]'):
            with self.subTest(configured=configured):
                os.environ["Z0INT_FILE_SEARCH_ROOTS"] = configured
                self.assert_denied()

    def test_request_cannot_supply_its_own_allowlist(self):
        status, body = self.post(allowed_roots=[str(self.repository)])
        self.assertEqual(status, 400, body)
        self.assertFalse(EventLog().events_path.exists())


if __name__ == "__main__":
    unittest.main()
