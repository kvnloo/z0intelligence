from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from z0int.bridge.runtime import BridgeRuntime
from z0int.bridge.reuse import _is_test, context_block
from z0int.receipt import append_receipt, find_receipt


class BridgeRepoReuseTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.env = patch.dict(os.environ, {"Z0INT_HOME": str(self.base / "state")})
        self.env.start()
        self.addCleanup(self.env.stop)
        self.repo = self.base / "unexpected-checkout"
        self.repo.mkdir()
        self.git("init", "-q")
        self.git("config", "user.name", "Fixture")
        self.git("config", "user.email", "fixture@example.invalid")
        self.git("remote", "add", "origin", "https://github.com/example/known-library.git")
        (self.repo / "upstream").mkdir()
        (self.repo / "tests").mkdir()
        self.code = self.repo / "upstream" / "records.py"
        self.code.write_text(
            "def normalize_rows(rows):\n"
            "    '''Normalize rows by preserving first occurrence.'''\n"
            "    return list(dict.fromkeys(rows))\n"
        )
        (self.repo / "tests" / "test_records.py").write_text(
            "from upstream.records import normalize_rows\n"
            "def test_normalize_rows():\n"
            "    assert normalize_rows([1, 1, 2]) == [1, 2]\n"
        )
        (self.repo / "zer0.repo.yaml").write_text(
            "version: 1\nrepo: example/known-library\narchitecture:\n"
            "  subsystems:\n  - id: records\n    paths: [upstream/records.py, tests/test_records.py]\n"
        )
        self.git("add", ".")
        self.git("commit", "-qm", "source fixture")
        self.registry = self.base / "components.yaml"
        self.registry.write_text(
            "components:\n  records:\n    repo: example/known-library\n"
            "    owner: Library\n    boundaries:\n      owns: [row normalization]\n"
        )
        self.rt = BridgeRuntime(generation=1, instance_id="test", build_id="test-build")

    def git(self, *args):
        return subprocess.run(["git", "-C", str(self.repo), *args], check=True, capture_output=True, text=True).stdout

    def payload(self):
        return {
            "canonical_repo": "records",
            "registry_path": str(self.registry),
            "candidate_roots": [str(self.repo)],
            "project_root": str(self.repo),
            "query": "normalize_rows",
            "symbol": "normalize_rows",
            "trace_id": "trace-one",
            "session_id": "session-one",
            "task_id": "work-one",
        }

    def fff_hits(self, *relative_paths):
        return {
            "status": "ready",
            "coverage": "complete",
            "generation": "fff-fixture:epoch=1",
            "generation_status": "tracked",
            "generation_reliable": True,
            "watcher_ready": True,
            "warmup_complete": True,
            "package_version": "fixture",
            "index_epoch": 1,
            "path_hits": [],
            "content_hits": [{"path": path, "line": 1, "text": (self.repo / path).read_text().splitlines()[0]} for path in relative_paths],
        }

    def prepare(self):
        return self.rt.reuse_resolve(self.payload())

    def injected(self, prepared):
        text = context_block(prepared["context_text"])
        out = self.rt.reuse_injected({
            "packet_id": prepared["packet_id"],
            "trace_id": "trace-one",
            "session_id": "session-one",
            "context_sha256": hashlib.sha256(text.encode()).hexdigest(),
            "bytes": len(text.encode()),
            "stage": "context",
        })
        prepared["injection_id"] = out.get("event_id")
        return out

    def check(self, prepared, **extra):
        out = self.rt.reuse_check({
            "packet_id": prepared["packet_id"],
            "trace_id": "trace-one",
            "session_id": "session-one",
            "target_cwd": str(self.repo),
            "tool_name": "write",
            "target_paths": ["consumer.py"],
            **extra,
        })
        if out.get("mutation_event_id") is not None:
            prepared["mutation_event_id"] = out["mutation_event_id"]
        return out

    def model_input(self, prepared):
        block = context_block(prepared["context_text"])
        out = self.rt.reuse_model_input({
            "packet_id": prepared["packet_id"], "trace_id": "trace-one", "session_id": "session-one",
            "context_sha256": hashlib.sha256(block.encode()).hexdigest(),
            "request_sha256": hashlib.sha256(block.encode()).hexdigest(),
            "stage": "provider_payload", "boundary": "sdk_final_payload",
            "injection_id": prepared.get("injection_id"),
        })
        prepared["model_input_event_id"] = out.get("event_id")
        return out

    def test_file_search_without_task_root_never_searches_package_checkout(self):
        with patch("z0int.context_resolve.resolve_context") as resolve:
            out = self.rt.file_search({"query": "normalize_rows", "record": False})
        self.assertFalse(out["ok"])
        resolve.assert_not_called()

    def test_real_repository_evidence_builds_positive_packet(self):
        out = self.prepare()
        self.assertTrue(out["ok"], out)
        self.assertEqual(out["decision"]["mode"], "REUSE", out["packet"]["unresolved_gaps"])
        self.assertFalse(out["decision"]["authorizes_action"])
        candidates = out["packet"]["reuse_candidates"]
        self.assertTrue(any("records.py" in ref["locator"] for c in candidates for ref in c["evidence"]))
        self.assertTrue(any("test_records.py" in ref["locator"] for c in candidates for ref in c["related_tests"]))

    def test_prepared_metadata_joins_packet_and_receipt_without_evidence_bodies(self):
        from z0int.memory.event_log import EventLog

        marker = "PRIVATE_SOURCE_BODY_SENTINEL"
        query = "normalize_rows café 🚲"
        self.code.write_text(
            "def normalize_rows(rows):\n"
            "    '''" + marker + "'''\n"
            "    return list(dict.fromkeys(rows))\n"
        )
        with patch("z0int.context_resolve._fff_search_repository", return_value=self.fff_hits(
            "upstream/records.py", "tests/test_records.py",
        )):
            request = self.payload()
            request["query"] = query
            out = self.rt.reuse_resolve(request)
        self.assertTrue(out["ok"], out)
        self.assertIn(marker, out["context_text"])

        event = EventLog().get(out["event_id"])
        metadata = event.payload["packet_metadata"]
        packet = out["packet"]
        recipe = packet["context"]["recipe"]
        self.assertEqual(event.payload["trace_id"], "trace-one")
        self.assertEqual(event.payload["input_fingerprint"], packet["input_fingerprint"])
        self.assertEqual(metadata["context_recipe"]["request_signature"], recipe["request_signature"])
        self.assertEqual(metadata["context_recipe"]["source_epochs"], recipe["source_epochs"])
        query_sha256 = hashlib.sha256(query.encode("utf-8")).hexdigest()
        self.assertEqual(metadata["context_input_sha256"], query_sha256)
        self.assertNotEqual(query_sha256, hashlib.sha256(b"a different query").hexdigest())
        self.assertEqual(metadata["context_unresolved_gaps"], [])
        self.assertEqual(metadata["context_unresolved_gap_count"], len(packet["context"]["unresolved_gaps"]))
        self.assertEqual(metadata["reuse_unresolved_gaps"], [])
        self.assertEqual(metadata["reuse_unresolved_gap_count"], len(packet["unresolved_gaps"]))
        self.assertEqual(metadata["context_measurements"]["coverage"], packet["context"]["measurements"]["coverage"])
        self.assertEqual(metadata["context_measurements"]["fff_coverage"], packet["context"]["measurements"]["fff_coverage"])
        self.assertEqual(metadata["metadata_gaps"], [])

        expected_refs = []
        for ref in packet["context"]["evidence"]:
            expected_refs.append({key: ref[key] for key in (
                "source_id", "source_version", "locator", "trust_class",
            )} | {"role": "context"})
        for candidate in packet["reuse_candidates"]:
            for ref in candidate["evidence"]:
                expected_refs.append({key: ref[key] for key in (
                    "source_id", "source_version", "locator", "trust_class",
                )} | {"role": "reuse_candidate", "candidate_id": candidate["candidate_id"]})
            for ref in candidate["related_tests"]:
                expected_refs.append({key: ref[key] for key in (
                    "source_id", "source_version", "locator", "trust_class",
                )} | {"role": "reuse_candidate_test", "candidate_id": candidate["candidate_id"]})
        for ref in packet["related_tests"]:
            expected_refs.append({key: ref[key] for key in (
                "source_id", "source_version", "locator", "trust_class",
            )} | {"role": "related_test"})
        self.assertEqual(metadata["context_evidence"], expected_refs)
        for actual, original in zip(metadata["context_measurements"]["coverage_operations"], recipe["operations"]):
            for key in ("op", "need", "status", "coverage", "required"):
                if key in original:
                    self.assertEqual(actual[key], original.get(key))
            self.assertEqual(actual["error_present"], bool(original.get("error")))
            if original.get("error"):
                self.assertEqual(actual["error_sha256"], hashlib.sha256(original["error"].encode()).hexdigest())

        digest = hashlib.sha256(json.dumps(
            metadata, sort_keys=True, ensure_ascii=False, separators=(",", ":"),
        ).encode("utf-8")).hexdigest()
        self.assertEqual(event.payload["packet_metadata_sha256"], digest)
        receipt = find_receipt("trace-one")
        receipt_reuse = receipt["extra"]["reuse"]
        self.assertEqual(receipt_reuse["packet_metadata"], metadata)
        self.assertEqual(receipt_reuse["packet_metadata_sha256"], digest)
        self.assertEqual(receipt_reuse["preparation_event_id"], out["event_id"])
        self.assertNotIn(marker, json.dumps(event.payload, sort_keys=True))
        self.assertNotIn(marker, json.dumps(receipt_reuse, sort_keys=True))

        other_request = self.payload()
        other_request.update(
            trace_id="trace-other-query",
            session_id="session-other-query",
            query="normalize_rows différente",
        )
        with patch("z0int.context_resolve._fff_search_repository", return_value=self.fff_hits(
            "upstream/records.py", "tests/test_records.py",
        )):
            other = self.rt.reuse_resolve(other_request)
        self.assertTrue(other["ok"], other)
        other_metadata = EventLog().get(other["event_id"]).payload["packet_metadata"]
        self.assertEqual(
            other_metadata["context_input_sha256"],
            hashlib.sha256(other_request["query"].encode("utf-8")).hexdigest(),
        )
        self.assertNotEqual(metadata["context_input_sha256"], other_metadata["context_input_sha256"])

    def test_untrusted_fff_generation_is_hashed_in_all_persisted_revisions(self):
        from z0int.memory.event_log import EventLog

        marker = "UNRECOGNIZED_PROVIDER_BODY_MARKER"
        raw_generation = marker + " https://provider.invalid/private?token=hidden"
        result = self.fff_hits()
        result.update(generation=raw_generation, generation_reliable=True)
        request = self.payload()
        request["trace_id"] = "trace-bad-generation"
        request["session_id"] = "session-bad-generation"
        with patch("z0int.context_resolve._fff_search_repository", return_value=result):
            out = self.rt.reuse_resolve(request)
        self.assertTrue(out["ok"], out)
        self.assertEqual(out["decision"]["mode"], "OBSERVE")

        event = EventLog().get(out["event_id"])
        metadata = event.payload["packet_metadata"]
        epoch_rows = metadata["context_recipe"]["source_epochs"]
        fff_key = next(key for key in epoch_rows if key.startswith("fff:"))
        self.assertTrue(epoch_rows[fff_key].startswith("sha256:"))
        self.assertIn("source_epoch_value_hashed", metadata["metadata_gaps"])
        revision_key = next(key for key in event.payload["source_revisions"] if key.startswith("epoch:fff:"))
        self.assertTrue(event.payload["source_revisions"][revision_key].startswith("sha256:"))
        self.assertIn("source_revision_value_hashed", metadata["metadata_gaps"])
        event_blob = json.dumps(event.payload, sort_keys=True)
        for raw_marker in (marker, "https://provider.invalid", "token=hidden"):
            self.assertNotIn(raw_marker, event_blob)

        receipt = find_receipt("trace-bad-generation")
        reuse_extra = receipt["extra"]["reuse"]
        self.assertEqual(reuse_extra["packet_metadata"], metadata)
        self.assertEqual(
            reuse_extra["packet_metadata_sha256"],
            hashlib.sha256(json.dumps(metadata, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()).hexdigest(),
        )
        receipt_blob = json.dumps(reuse_extra, sort_keys=True)
        for raw_marker in (marker, "https://provider.invalid", "token=hidden"):
            self.assertNotIn(raw_marker, receipt_blob)

    def test_unrecognized_source_epoch_and_reference_values_are_hashed(self):
        from types import SimpleNamespace
        from z0int.bridge.reuse import _metadata_reference, _metadata_source_epochs

        marker = "UNRECOGNIZED_SECRET_AND_BODY_MARKER"
        epoch_gaps = []
        epochs = _metadata_source_epochs(
            {"provider:unrecognized": marker + " https://provider.invalid/private"},
            epoch_gaps,
        )
        self.assertIn("unclassified_source_epoch_hashed", epoch_gaps)
        self.assertNotIn(marker, json.dumps(epochs, sort_keys=True))
        self.assertNotIn("provider:unrecognized", json.dumps(epochs, sort_keys=True))

        reference_gaps = []
        reference = _metadata_reference(
            SimpleNamespace(
                source_id="qmd:https://provider.invalid/" + marker,
                locator="https://provider.invalid/" + marker,
                source_version=marker,
                trust_class="index_hit",
            ),
            "context",
            reference_gaps,
        )
        self.assertTrue(reference_gaps)
        self.assertNotIn(marker, json.dumps(reference, sort_keys=True))
        self.assertIn("source_id_sha256", reference)
        self.assertIn("locator_sha256", reference)
        self.assertIn("source_version_sha256", reference)

        agentsview_id = "agentsview:session-1#7"
        prior_reference_gap_count = len(reference_gaps)
        canonical_reference = _metadata_reference(
            SimpleNamespace(
                source_id=agentsview_id,
                locator=agentsview_id,
                source_version="a" * 64,
                trust_class="conversation",
            ),
            "context",
            reference_gaps,
        )
        self.assertEqual(canonical_reference["source_id"], agentsview_id)
        self.assertEqual(canonical_reference["locator"], agentsview_id)
        self.assertEqual(canonical_reference["source_version"], "a" * 64)
        self.assertEqual(canonical_reference["role"], "context")
        self.assertEqual(len(reference_gaps), prior_reference_gap_count)

    def test_partial_and_outage_provider_metadata_remain_distinct_and_private(self):
        from z0int.memory.event_log import EventLog

        secret_marker = "UNRECOGNIZED_SECRET_MARKER"
        body_marker = "UNRECOGNIZED_PROVIDER_BODY_MARKER"
        url_marker = "https://provider.invalid/private-response?token=" + secret_marker
        partial_error = body_marker + " " + url_marker
        partial_result = self.fff_hits("upstream/records.py", "tests/test_records.py")
        partial_result.update(
            status="partial",
            coverage="partial",
            error=partial_error,
            content_hits=[],
            path_hits=[],
        )
        partial_request = self.payload()
        partial_request["trace_id"] = "trace-partial"
        partial_request["session_id"] = "session-partial"
        partial_request["symbol"] = "PRIVATE_QUERY_MARKER " + url_marker
        with patch("z0int.context_resolve._fff_search_repository", return_value=partial_result):
            partial = self.rt.reuse_resolve(partial_request)
        self.assertTrue(partial["ok"], partial)
        self.assertEqual(partial["decision"]["mode"], "OBSERVE")
        partial_event = EventLog().get(partial["event_id"])
        partial_metadata = partial_event.payload["packet_metadata"]
        partial_errors = [item for item in partial_metadata["provider_errors"] if item["op"] == "fff_symbol"]
        self.assertTrue(partial_errors)
        self.assertEqual(partial_errors[0]["status"], "partial")
        self.assertEqual(partial_errors[0]["coverage"], "partial")
        self.assertTrue(partial_errors[0]["error_present"])
        self.assertEqual(partial_errors[0]["error_sha256"], hashlib.sha256(partial_error.encode()).hexdigest())
        self.assertEqual(partial_metadata["context_unresolved_gap_count"], len(partial["packet"]["context"]["unresolved_gaps"]))
        persisted_partial = json.dumps(partial_event.payload, sort_keys=True)
        for raw_marker in (secret_marker, body_marker, url_marker, "PRIVATE_QUERY_MARKER"):
            self.assertNotIn(raw_marker, persisted_partial)
        partial_receipt = find_receipt("trace-partial")
        self.assertEqual(
            partial_receipt["extra"]["reuse"]["packet_metadata"], partial_metadata,
        )
        for raw_marker in (secret_marker, body_marker, url_marker, "PRIVATE_QUERY_MARKER"):
            self.assertNotIn(raw_marker, json.dumps(partial_receipt["extra"]["reuse"], sort_keys=True))

        outage_request = self.payload()
        outage_request["trace_id"] = "trace-outage"
        outage_request["session_id"] = "session-outage"
        with patch("z0int.context_resolve._fff_search_repository", return_value={
            "status": "error", "coverage": "unavailable", "error": "provider down",
            "content_hits": [], "path_hits": [],
        }):
            outage = self.rt.reuse_resolve(outage_request)
        self.assertTrue(outage["ok"], outage)
        self.assertEqual(outage["decision"]["mode"], "OBSERVE")
        outage_event = EventLog().get(outage["event_id"])
        outage_metadata = outage_event.payload["packet_metadata"]
        outage_errors = [item for item in outage_metadata["provider_errors"] if item["op"] == "fff_symbol"]
        self.assertTrue(outage_errors)
        self.assertEqual(outage_errors[0]["status"], "error")
        self.assertEqual(outage_errors[0]["coverage"], "unavailable")
        self.assertTrue(outage_errors[0]["error_present"])
        self.assertEqual(outage_errors[0]["error_sha256"], hashlib.sha256(b"provider down").hexdigest())
        self.assertNotEqual(
            (partial_errors[0]["status"], partial_errors[0]["coverage"]),
            (outage_errors[0]["status"], outage_errors[0]["coverage"]),
        )

    def test_unrelated_test_query_cannot_support_candidate(self):
        unrelated = self.repo / "tests" / "test_payments.py"
        unrelated.write_text("def test_payment_flow(): pass\n")
        (self.repo / "zer0.repo.yaml").write_text(
            "version: 1\nrepo: example/known-library\narchitecture:\n"
            "  subsystems:\n"
            "  - id: records\n    paths: [upstream/records.py, tests/test_records.py]\n"
            "  - id: payments\n    paths: [tests/test_payments.py]\n"
        )
        request = self.payload()
        request["test_query"] = "payment flow test"
        with patch("z0int.context_resolve._fff_search_repository", return_value=self.fff_hits(
            "upstream/records.py", "tests/test_payments.py",
        )):
            out = self.rt.reuse_resolve(request)
        self.assertEqual(out["decision"]["mode"], "OBSERVE")
        self.assertFalse(out["packet"]["related_tests"])
        self.assertTrue(all(not candidate["related_tests"] for candidate in out["packet"]["reuse_candidates"]))

    def test_manifest_linked_test_supports_only_its_subsystem_candidate(self):
        with patch("z0int.context_resolve._fff_search_repository", return_value=self.fff_hits(
            "upstream/records.py", "tests/test_records.py",
        )):
            out = self.prepare()
        self.assertEqual(out["decision"]["mode"], "REUSE", out["packet"]["unresolved_gaps"])
        candidates = out["packet"]["reuse_candidates"]
        self.assertEqual(len(candidates), 1)
        self.assertEqual([Path(ref["locator"]).name for ref in candidates[0]["related_tests"]], ["test_records.py"])
        manifest = str((self.repo / "zer0.repo.yaml").resolve())
        self.assertIn(f"file:{manifest}", out["packet"]["context"]["recipe"]["source_epochs"])

    def test_manifest_identity_mismatch_fails_closed(self):
        (self.repo / "zer0.repo.yaml").write_text(
            "version: 1\nrepo: example/other-library\narchitecture:\n"
            "  subsystems:\n  - id: records\n    paths: [upstream/records.py, tests/test_records.py]\n"
        )
        with patch("z0int.context_resolve._fff_search_repository", return_value=self.fff_hits(
            "upstream/records.py", "tests/test_records.py",
        )):
            out = self.prepare()
        self.assertEqual(out["decision"]["mode"], "OBSERVE")
        self.assertTrue(any("identity does not match" in gap for gap in out["packet"]["unresolved_gaps"]))

    def test_manifest_owned_test_membership_change_invalidates_prepared_packet(self):
        with patch("z0int.context_resolve._fff_search_repository", return_value=self.fff_hits(
            "upstream/records.py", "tests/test_records.py",
        )):
            out = self.prepare()
            self.injected(out)
            self.model_input(out)
            (self.repo / "zer0.repo.yaml").write_text(
                "version: 1\nrepo: example/known-library\narchitecture:\n"
                "  subsystems:\n"
                "  - id: records\n    paths: [upstream/records.py]\n"
                "  - id: records-tests\n    paths: [tests/test_records.py]\n"
            )
            checked = self.check(out)
        self.assertFalse(checked["valid"])
        self.assertEqual(checked["decision"]["mode"], "OBSERVE")

    def test_manifest_declared_symlink_escape_fails_closed(self):
        outside = self.base / "outside.py"
        outside.write_text("def normalize_rows(rows): return rows\n")
        test_path = self.repo / "tests" / "test_records.py"
        test_path.unlink()
        test_path.symlink_to(outside)
        with patch("z0int.context_resolve._fff_search_repository", return_value=self.fff_hits(
            "upstream/records.py", "tests/test_records.py",
        )):
            out = self.prepare()
        self.assertEqual(out["decision"]["mode"], "OBSERVE")
        self.assertTrue(any("invalid or escaping path" in gap for gap in out["packet"]["unresolved_gaps"]))

    def test_manifest_declared_candidate_symlink_escape_fails_closed(self):
        outside = self.base / "outside.py"
        outside.write_text("def normalize_rows(rows): return rows\n")
        self.code.unlink()
        self.code.symlink_to(outside)
        with patch("z0int.context_resolve._fff_search_repository", return_value=self.fff_hits(
            "upstream/records.py", "tests/test_records.py",
        )):
            out = self.prepare()
        self.assertEqual(out["decision"]["mode"], "OBSERVE")
        self.assertTrue(any("invalid or escaping path" in gap for gap in out["packet"]["unresolved_gaps"]))

    def test_test_classification_ignores_checkout_ancestor_names(self):
        root = self.base / "tests" / "nested-checkout"
        implementation = root / "module" / "existing.py"
        implementation.parent.mkdir(parents=True)
        implementation.write_text("def existing(): pass\n")
        owning_test = root / "tests" / "test_existing.py"
        owning_test.parent.mkdir()
        owning_test.write_text("def test_existing(): pass\n")
        self.assertFalse(_is_test(implementation, root))
        self.assertTrue(_is_test(owning_test, root))

    def test_mutation_requires_matching_injected_context_and_scope(self):
        out = self.prepare()
        self.assertFalse(self.check(out)["valid"])
        self.assertTrue(self.injected(out)["ok"])
        self.assertFalse(self.check(out)["valid"])
        self.assertTrue(self.model_input(out)["ok"])
        self.assertTrue(self.check(out)["valid"])
        self.assertFalse(self.check(out, session_id="another-session")["valid"])
        self.assertFalse(self.check(out, target_cwd=str(self.base))["valid"])

    def test_verifier_binding_is_prepared_before_mutation_and_cannot_be_swapped(self):
        request = self.payload()
        request["verifier_binding"] = {
            "verifier_id": "pytest:records-owning-test",
            "candidate_id": "file:upstream/records.py",
            "argv": [sys.executable, "-m", "pytest", "-q", "tests/test_records.py"],
            "test_paths": ["tests/test_records.py"],
        }
        with patch("z0int.context_resolve._fff_search_repository", return_value=self.fff_hits(
            "upstream/records.py", "tests/test_records.py",
        )):
            out = self.rt.reuse_resolve(request)
        self.assertTrue(out["ok"], out)
        self.injected(out)
        self.assertTrue(self.model_input(out)["ok"])

        changed_binding = {**request["verifier_binding"], "verifier_id": "pytest:other"}
        with patch("z0int.context_resolve._fff_search_repository", return_value=self.fff_hits(
            "upstream/records.py", "tests/test_records.py",
        )):
            rejected = self.check(out, verifier_binding=changed_binding)
            self.assertFalse(rejected["valid"])
            self.assertTrue(self.check(out)["valid"])

        from z0int.memory.event_log import EventLog

        prepared_event = EventLog().get(out["event_id"])
        self.assertEqual(prepared_event.payload["trace_id"], "trace-one")
        self.assertEqual(prepared_event.payload["verifier_binding"]["verifier_id"], "pytest:records-owning-test")

    def test_independent_owning_test_joins_mutation_trace_after_real_file_change(self):
        from z0int.memory.event_log import EventLog
        from z0int.receipt import Outcome, close_turn

        with patch("z0int.bridge.runtime.preflight", return_value={
            "route": "local", "capability_id": "coding.reuse", "label": "REUSE", "p": 0.9,
            "baseline_input_tokens": 100, "baseline_output_tokens": 50,
        }):
            opened = self.rt.turn_open(trace_id="trace-one", session_id="session-one", prompt="reuse normalize_rows")
        self.assertTrue(opened["ok"], opened)

        request = self.payload()
        request["verifier_binding"] = {
            "verifier_id": "pytest:records-owning-test",
            "candidate_id": "file:upstream/records.py",
            "argv": [sys.executable, "-m", "pytest", "-q", "tests/test_records.py"],
            "test_paths": ["tests/test_records.py"],
        }
        with patch("z0int.context_resolve._fff_search_repository", return_value=self.fff_hits(
            "upstream/records.py", "tests/test_records.py",
        )):
            prepared = self.rt.reuse_resolve(request)
        self.assertTrue(prepared["ok"], prepared)
        self.injected(prepared)
        self.assertTrue(self.model_input(prepared)["ok"])

        with patch("z0int.context_resolve._fff_search_repository", return_value=self.fff_hits(
            "upstream/records.py", "tests/test_records.py",
        )):
            mutation = self.check(prepared, target_paths=["upstream/records.py"])
        self.assertTrue(mutation["valid"], mutation)
        first_injection_event_id = prepared["injection_id"]
        first_model_input_event_id = prepared["model_input_event_id"]
        first_mutation_event_id = mutation["mutation_event_id"]

        self.code.write_text(
            "def normalize_rows(rows):\n"
            "    seen = set()\n"
            "    normalized = []\n"
            "    for row in rows:\n"
            "        if row not in seen:\n"
            "            seen.add(row)\n"
            "            normalized.append(row)\n"
            "    return normalized\n"
        )
        verifier = subprocess.run(
            [sys.executable, "-m", "pytest", "-q", "tests/test_records.py"],
            cwd=self.repo, capture_output=True, text=True, check=False,
        )
        evidence_file = self.base / "owning-test-result.json"
        evidence_file.write_text(json.dumps({
            "verifier_id": request["verifier_binding"]["verifier_id"],
            "argv": request["verifier_binding"]["argv"],
            "test_paths": request["verifier_binding"]["test_paths"],
            "returncode": verifier.returncode,
            "stdout": verifier.stdout,
            "stderr": verifier.stderr,
        }, sort_keys=True))
        self.assertEqual(verifier.returncode, 0, verifier.stdout + verifier.stderr)

        closed = close_turn(
            "trace-one",
            input_tokens=18,
            output_tokens=7,
            measurement_state="complete",
            provider="pytest-provider",
            model="fixture-verifier",
            outcome=Outcome(
                execution_completed=True,
                verified_success=True,
                test_pass=True,
                source="independent_verifier",
                verification_source=evidence_file.resolve().as_uri(),
            ),
        )
        self.assertEqual(closed["outcome_join"]["trace_id"], "trace-one")
        self.assertEqual(closed["outcome_join"]["outcome_tier"], "gold")
        outcome = closed["outcome_join"]["outcome"]
        self.assertEqual(outcome["verification_source"], evidence_file.resolve().as_uri())

        # A separate same-trace writer may add receipt metadata after close.
        # The next reuse stage must merge from that canonical row, preserving
        # both the trusted outcome/measurements and unrelated extra fields.
        latest_at_close = find_receipt("trace-one")
        unrelated_extra = dict(latest_at_close.get("extra") or {})
        unrelated_extra["post_close_observer"] = {"source": "test-fixture", "marker": "retain-me"}
        append_receipt({**latest_at_close, "extra": unrelated_extra})

        # A later provider round revokes current readiness. Its stage receipt
        # must retain the prior verified close and the pre-mutation check.
        self.injected(prepared)
        self.assertTrue(self.model_input(prepared)["ok"])

        latest = find_receipt("trace-one")
        self.assertEqual(latest["outcome"]["verification_source"], evidence_file.resolve().as_uri())
        self.assertTrue(latest["outcome"]["verified_success"])
        self.assertEqual(latest["outcome_tier"], "gold")
        self.assertEqual(latest["input_tokens"], 18)
        self.assertEqual(latest["output_tokens"], 7)
        self.assertEqual(latest["measured_frontier_tokens"], 25)
        self.assertEqual(latest["actual_tokens_saved"], 125)
        self.assertEqual(latest["provider"], "pytest-provider")
        self.assertEqual(latest["model"], "fixture-verifier")
        self.assertEqual(latest["measurement_state"], "complete")
        self.assertEqual(latest["extra"]["post_close_observer"]["marker"], "retain-me")

        receipt_extra = latest["extra"]
        self.assertEqual(receipt_extra["memory"]["snapshot_id"], prepared["snapshot_id"])
        reuse = receipt_extra["reuse"]
        self.assertEqual(reuse["preparation_event_id"], prepared["event_id"])
        self.assertEqual(reuse["model_input_event_id"], prepared["model_input_event_id"])
        self.assertIsNone(reuse["mutation_event_id"])
        self.assertEqual(reuse["mutation_check_event_ids"], [first_mutation_event_id])
        self.assertEqual(reuse["injection_event_id"], prepared["injection_id"])
        self.assertEqual(reuse["verifier_binding"]["verifier_id"], "pytest:records-owning-test")

        for event_id in (prepared["event_id"], first_model_input_event_id, first_mutation_event_id):
            event = EventLog().get(event_id)
            self.assertEqual(event.payload["trace_id"], "trace-one")
        model_event = EventLog().get(first_model_input_event_id)
        mutation_event = EventLog().get(first_mutation_event_id)
        self.assertEqual(model_event.payload["preparation_event_id"], prepared["event_id"])
        self.assertIn(first_injection_event_id, model_event.parent_event_ids)
        self.assertEqual(mutation_event.payload["model_input_event_id"], first_model_input_event_id)
        self.assertIn(first_model_input_event_id, mutation_event.parent_event_ids)
        self.assertEqual(mutation_event.payload["verifier_binding"]["verifier_id"], "pytest:records-owning-test")

    def test_execution_close_without_independent_verifier_stays_unverified(self):
        from z0int.receipt import Outcome, close_turn

        with patch("z0int.bridge.runtime.preflight", return_value={
            "route": "local", "capability_id": "coding.reuse", "label": "REUSE", "p": 0.9,
        }):
            self.rt.turn_open(trace_id="trace-one", session_id="session-one", prompt="reuse normalize_rows")
        closed = close_turn("trace-one", outcome=Outcome(execution_completed=True, source="bridge_turn_end"))
        self.assertEqual(closed["outcome_join"]["outcome_tier"], "execution")
        self.assertTrue(closed["outcome_join"]["outcome"]["execution_completed"])
        self.assertNotIn("verified_success", closed["outcome_join"]["outcome"])

    def test_same_size_same_mtime_edit_invalidates_before_mutation(self):
        out = self.prepare()
        self.injected(out)
        self.model_input(out)
        st = self.code.stat()
        content = self.code.read_text()
        content = content.replace("first", "final")
        self.assertEqual(len(content.encode()), st.st_size)
        self.code.write_text(content)
        os.utime(self.code, ns=(st.st_atime_ns, st.st_mtime_ns))
        check = self.check(out)
        self.assertFalse(check["valid"], check)
        self.assertEqual(check["decision"]["mode"], "OBSERVE")

    def test_wrong_registry_owner_is_not_a_search_fallback(self):
        payload = self.payload()
        payload["canonical_repo"] = "unregistered"
        out = self.rt.reuse_resolve(payload)
        self.assertFalse(out["decision"]["implementation_allowed"])
        self.assertEqual(out["decision"]["mode"], "OBSERVE")

    def test_mutation_paths_and_context_receipt_replay_fail_closed(self):
        out = self.prepare()
        self.injected(out)
        old_injection = out["injection_id"]
        self.model_input(out)
        self.assertFalse(self.check(out, target_paths=["../outside.py"])["valid"])
        (self.repo / "escape").symlink_to(self.base, target_is_directory=True)
        self.assertFalse(self.check(out, target_paths=["escape/outside.py"])["valid"])
        self.assertFalse(self.check(out, target_paths=[".git/config"])["valid"])
        self.assertFalse(self.check(out, target_paths=[])["valid"])
        self.assertFalse(self.check(out, tool_name="bash")["valid"])
        self.injected(out)
        self.assertFalse(self.check(out)["valid"])
        new_injection = out["injection_id"]
        out["injection_id"] = old_injection
        self.assertFalse(self.model_input(out)["ok"])
        out["injection_id"] = new_injection

    def test_new_worker_and_provider_outage_do_not_restore_readiness(self):
        out = self.prepare()
        self.injected(out)
        self.model_input(out)
        new_worker = BridgeRuntime(generation=2, instance_id="new", build_id="test-build")
        self.assertFalse(new_worker.reuse_check({"packet_id": out["packet_id"], "trace_id": "trace-one", "session_id": "session-one"})["valid"])
        with patch("z0int.context_resolve._fff_search_repository", return_value={
            "status": "error", "coverage": "unavailable", "error": "provider down", "content_hits": [], "path_hits": [],
        }):
            checked = self.check(out)
        self.assertFalse(checked["valid"])
        self.assertEqual(checked["decision"]["mode"], "OBSERVE")

    def test_registry_or_dependency_membership_change_invalidates(self):
        out = self.prepare()
        self.injected(out)
        self.model_input(out)
        self.registry.write_text(self.registry.read_text().replace("row normalization", "record normalization"))
        self.assertFalse(self.check(out)["valid"])

    def test_failed_injection_receipt_revokes_previous_witness(self):
        out = self.prepare()
        self.injected(out)
        self.model_input(out)
        checked = self.rt.reuse_injected({"packet_id": out["packet_id"], "trace_id": "trace-one", "session_id": "session-one", "context_sha256": "wrong", "bytes": 1, "stage": "context"})
        self.assertFalse(checked["ok"])
        self.assertFalse(self.check(out)["valid"])

    def test_required_admitted_memory_is_not_satisfied_by_model_assertion(self):
        from z0int.memory.claims import record_claim
        from z0int.memory_contract import BitemporalClaim, MemoryScope

        scope = MemoryScope(user="u", project="p", repo="example/known-library", task="work-one")
        record_claim(BitemporalClaim(
            claim_id="model-only", scope=scope, subject="delegated_agents", predicate="configuration",
            value={"model": "luna"}, status="observed", observed_at="2026-10-07T00:00:00Z", recorded_at="2026-10-07T00:00:00Z",
        ))
        request = self.payload()
        request["memory_scope"] = {key: value for key, value in scope.to_dict().items() if key != "level"}
        out = self.rt.reuse_resolve(request)
        self.assertEqual(out["decision"]["mode"], "OBSERVE")
        self.assertTrue(any("admitted" in gap for gap in out["packet"]["unresolved_gaps"]))


if __name__ == "__main__":
    unittest.main()
