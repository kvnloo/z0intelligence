from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path


class ReceiptBuild(unittest.TestCase):
    def test_schema_and_trace(self):
        from z0int.receipt import SCHEMA, build_receipt, validate_receipt

        r = build_receipt(
            capability_id="coding.next_action",
            route="local",
            confidence=0.9,
            baseline_input_tokens=4000,
            baseline_output_tokens=800,
            estimated_frontier_tokens_avoided=4800,
        )
        d = r.to_dict()
        self.assertEqual(d["schema"], SCHEMA)
        self.assertTrue(d["trace_id"])
        self.assertEqual(d["capability_id"], "coding.next_action")
        self.assertEqual(validate_receipt(d), [])
        self.assertEqual(r.tokens_saved_est(), 4800)

    def test_partial_receipt_does_not_infer_saved_tokens_from_incomplete_actual(self):
        from z0int.receipt import build_receipt

        r = build_receipt(
            capability_id="coding.next_action",
            route="model",
            baseline_input_tokens=1000,
            baseline_output_tokens=200,
            input_tokens=400,
            output_tokens=100,
            measurement_state="partial",
            state_reason="char_count_proxy",
        )
        self.assertIsNone(r.tokens_saved_est())

    def test_validate_missing_trace(self):
        from z0int.receipt import validate_receipt

        self.assertIn("missing_trace_id", validate_receipt({"schema": "z0int.decision_receipt.v1"}))


class OutcomeTiers(unittest.TestCase):
    def test_execution_completed_is_not_gold(self):
        from z0int.receipt import Outcome

        self.assertEqual(Outcome(execution_completed=True, source="bridge_turn_end").tier(), "execution")

    def test_tool_ok_and_success_not_gold(self):
        from z0int.receipt import Outcome

        self.assertEqual(Outcome(success=True, tool_ok=True, source="legacy").tier(), "soft")
        self.assertFalse(Outcome(success=True, tool_ok=True).is_verified())

    def test_test_pass_and_verified_success_are_gold(self):
        from z0int.receipt import Outcome

        self.assertEqual(Outcome(test_pass=True, source="ci").tier(), "gold")
        self.assertEqual(Outcome(verified_success=True, source="join").tier(), "gold")
        self.assertEqual(Outcome(verified=True, source="join").tier(), "gold")
        self.assertTrue(Outcome(test_pass=True).is_verified())

    def test_negative_beats_gold_signals(self):
        from z0int.receipt import Outcome

        self.assertEqual(
            Outcome(test_pass=True, user_correction=True, source="user").tier(),
            "negative",
        )

    def test_stale_ambient_success_is_execution_not_gold(self):
        from z0int.receipt import Outcome

        # Old bridge closeFromMessages: success+toolOk on turn_end
        self.assertEqual(
            Outcome(success=True, tool_ok=True, source="bridge_turn_end").tier(),
            "execution",
        )
        self.assertFalse(
            Outcome(success=True, tool_ok=True, source="bridge_turn_end").is_verified()
        )

    def test_ambient_test_pass_without_verification_source_demoted(self):
        from z0int.receipt import Outcome

        # Stale /z0int-close defaulted testPass=success on ambient-like paths;
        # automatic bridge closes must not mint gold without verification_source.
        self.assertEqual(
            Outcome(test_pass=True, success=True, tool_ok=True, source="bridge_turn_end").tier(),
            "execution",
        )

    def test_ambient_gold_allowed_with_verification_source(self):
        from z0int.receipt import Outcome

        self.assertEqual(
            Outcome(
                test_pass=True,
                source="bridge_turn_end",
                verification_source="ci",
            ).tier(),
            "gold",
        )

    def test_malformed_verification_source_cannot_credit_an_ambient_close(self):
        from z0int.receipt import Outcome

        for source in (" \t\n", True, 1, {"run": "ci"}):
            with self.subTest(source=source):
                self.assertEqual(
                    Outcome(test_pass=True, source="bridge_turn_end", verification_source=source).tier(),
                    "execution",
                )

    def test_placeholder_verification_source_cannot_credit_an_ambient_close(self):
        from z0int.receipt import Outcome

        placeholders = (
            "null", "NULL", "None", "none", "false", "False", "0", "n/a", "N/A", "na", "nil",
            "~", "-", "\u200b", "\u200b\u200c\u200d\ufeff", " null ", "\u00a0n/a\u00a0", "\u2060",
            "\u200bnull", "None\u200b", False, 0, 0.0, None,
        )
        for source in placeholders:
            with self.subTest(source=source):
                self.assertEqual(
                    Outcome(test_pass=True, source="bridge_turn_end", verification_source=source).tier(),
                    "execution",
                )

    def test_named_verification_source_still_credits_an_ambient_close(self):
        from z0int.receipt import Outcome

        for source in ("ci", "pinned-d2-oracle-and-owning-tests", "sha256:3a396dc5", "operator", "0x1", "nullable-check"):
            with self.subTest(source=source):
                self.assertEqual(
                    Outcome(test_pass=True, source="bridge_turn_end", verification_source=source).tier(),
                    "gold",
                )




class ReceiptJoin(unittest.TestCase):
    def test_invalid_ambient_verifier_join_preserves_negative_history_without_credit(self):
        from z0int.receipt import (
            append_receipt, build_receipt, find_receipt, join_outcome, summarize_tokenomics,
        )

        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            receipt = append_receipt(build_receipt(capability_id="coding.edit", route="local"), root=home)
            trace = receipt["trace_id"]
            for verdict, tier in ((True, "execution"), (False, "negative")):
                joined = join_outcome(trace, {
                    "source": "bridge_turn_end", "verified_success": verdict, "verification_source": " \t\n",
                }, root=home)
                self.assertEqual(joined["outcome_tier"], tier)
            found = find_receipt(trace, root=home)
            self.assertIs(found["outcome"]["verified_success"], False)
            self.assertEqual(found["outcome"]["verification_source"], " \t\n")
            rows = [json.loads(line) for line in (home / "receipts" / "outcomes.jsonl").read_text().splitlines()]
            self.assertEqual([row["outcome_tier"] for row in rows], ["execution", "negative"])
            self.assertEqual(summarize_tokenomics(root=home)["verified_tasks"], 0)

    def test_emit_join_summary_gold_from_test_pass(self):
        from z0int.receipt import (
            Outcome,
            append_receipt,
            build_receipt,
            find_receipt,
            join_outcome,
            summarize_tokenomics,
        )

        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            r = build_receipt(
                capability_id="recovery_action",
                route="local",
                estimated_frontier_tokens_avoided=1200,
                baseline_input_tokens=2000,
                baseline_output_tokens=400,
            )
            row = append_receipt(r, root=home)
            tid = row["trace_id"]
            joined = join_outcome(
                tid,
                Outcome(test_pass=True, source="test"),
                root=home,
            )
            self.assertEqual(joined["outcome_tier"], "gold")
            found = find_receipt(tid, root=home)
            self.assertIsNotNone(found)
            self.assertEqual(found.get("outcome", {}).get("test_pass"), True)
            summary = summarize_tokenomics(root=home)
            self.assertGreaterEqual(summary["rows"], 1)
            self.assertGreaterEqual(summary["frontier_tokens_avoided_est"], 1200)
            self.assertGreaterEqual(summary["rows_with_outcome"], 1)
            self.assertIn("actual_tokens_saved", summary)
            self.assertIn("tokens_per_verified_task", summary)
            self.assertIn("baseline_tokens_sum", summary)
            self.assertGreaterEqual(summary["verified_tasks"], 1)
            self.assertTrue((home / "receipts" / "decisions.jsonl").is_file())
            self.assertTrue((home / "receipts" / "outcomes.jsonl").is_file())

    def test_execution_close_does_not_inflate_verified(self):
        from z0int.receipt import Outcome, append_receipt, build_receipt, close_turn, summarize_tokenomics

        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            r = append_receipt(
                build_receipt(
                    capability_id="coding.edit",
                    route="model",
                    baseline_input_tokens=2500,
                    baseline_output_tokens=600,
                ),
                root=home,
            )
            tid = r["trace_id"]
            closed = close_turn(
                tid,
                measured_frontier_tokens=1240,
                input_tokens=900,
                output_tokens=340,
                outcome=Outcome(execution_completed=True, source="bridge_turn_end"),
                root=home,
            )
            self.assertEqual(closed["outcome_join"]["outcome_tier"], "execution")
            s = summarize_tokenomics(root=home)
            self.assertEqual(s["verified_tasks"], 0)
            self.assertGreaterEqual(s["rows_with_outcome"], 1)

    def test_tool_ok_close_not_verified(self):
        from z0int.receipt import Outcome, append_receipt, build_receipt, close_turn, summarize_tokenomics

        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            r = append_receipt(
                build_receipt(capability_id="coding.edit", route="model"),
                root=home,
            )
            close_turn(
                r["trace_id"],
                measured_frontier_tokens=100,
                outcome=Outcome(success=True, tool_ok=True, source="legacy"),
                root=home,
            )
            s = summarize_tokenomics(root=home)
            self.assertEqual(s["verified_tasks"], 0)

    def test_false_stored_gold_ignored_in_summary(self):
        from z0int.receipt import append_receipt, build_receipt, summarize_tokenomics

        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            r = append_receipt(
                build_receipt(
                    capability_id="coding.edit",
                    route="model",
                    baseline_input_tokens=1000,
                    baseline_output_tokens=100,
                ),
                root=home,
            )
            # Contaminated historical shape: tier=gold, only success/tool_ok
            bad = dict(r)
            bad["outcome"] = {
                "success": True,
                "tool_ok": True,
                "source": "bridge_turn_end",
            }
            bad["outcome_tier"] = "gold"
            bad["measured_frontier_tokens"] = 50
            append_receipt(bad, root=home)
            s = summarize_tokenomics(root=home)
            self.assertEqual(s["verified_tasks"], 0)
            self.assertGreaterEqual(s["false_gold_ignored"], 1)

    def test_scrub_rewrites_false_gold(self):
        from z0int.receipt import (
            append_receipt,
            build_receipt,
            join_outcome,
            scrub_contaminated_outcomes,
            summarize_tokenomics,
            Outcome,
        )
        import json

        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            r = append_receipt(
                build_receipt(capability_id="coding.edit", route="model"),
                root=home,
            )
            tid = r["trace_id"]
            # Bypass normalize by writing raw contaminated join (historical)
            op = home / "receipts" / "outcomes.jsonl"
            op.parent.mkdir(parents=True, exist_ok=True)
            with op.open("a", encoding="utf-8") as fh:
                fh.write(
                    json.dumps(
                        {
                            "schema": "z0int.outcome_join.v1",
                            "ts": 1.0,
                            "trace_id": tid,
                            "outcome": {
                                "success": True,
                                "tool_ok": True,
                                "source": "bridge_turn_end",
                            },
                            "outcome_tier": "gold",
                            "receipt": r,
                        }
                    )
                    + "\n"
                )
            dry = scrub_contaminated_outcomes(root=home, dry_run=True)
            self.assertEqual(dry["contaminated_latest"], 1)
            self.assertEqual(dry["rewritten"], 0)
            live = scrub_contaminated_outcomes(root=home, dry_run=False)
            self.assertEqual(live["rewritten"], 1)
            self.assertEqual(live["corrections"][0]["to"], "execution")
            # second scrub is no-op on latest
            again = scrub_contaminated_outcomes(root=home, dry_run=True)
            self.assertEqual(again["contaminated_latest"], 0)
            # real gold still joins
            join_outcome(tid, Outcome(test_pass=True, source="ci"), root=home)
            s = summarize_tokenomics(root=home)
            self.assertGreaterEqual(s["verified_tasks"], 1)




class ReceiptCli(unittest.TestCase):
    def test_cli_emit_join_summary(self):
        from z0int.cli import main

        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            old = os.environ.get("Z0INT_HOME")
            os.environ["Z0INT_HOME"] = str(home)
            try:
                rc = main(
                    [
                        "receipt",
                        "emit",
                        "--capability-id",
                        "needs_verification",
                        "--route",
                        "local",
                        "--avoided",
                        "900",
                        "--prediction",
                        "VERIFY",
                        "--confidence",
                        "0.88",
                    ]
                )
                self.assertEqual(rc, 0)
                lines = (home / "receipts" / "decisions.jsonl").read_text().strip().splitlines()
                self.assertEqual(len(lines), 1)
                tid = json.loads(lines[0])["trace_id"]
                rc = main(
                    [
                        "receipt",
                        "join",
                        tid,
                        "--test-pass",
                        "true",
                        "--verified-success",
                        "true",
                    ]
                )
                self.assertEqual(rc, 0)
                rc = main(["receipt", "summary", "--json"])
                self.assertEqual(rc, 0)
            finally:
                if old is None:
                    os.environ.pop("Z0INT_HOME", None)
                else:
                    os.environ["Z0INT_HOME"] = old


class ReceiptClose(unittest.TestCase):
    def test_close_turn_measured_savings(self):
        from z0int.receipt import Outcome, append_receipt, build_receipt, close_turn, summarize_tokenomics

        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            r = append_receipt(
                build_receipt(
                    capability_id="coding.edit",
                    route="model",
                    baseline_input_tokens=2500,
                    baseline_output_tokens=600,
                    estimated_frontier_tokens_avoided=0,
                ),
                root=home,
            )
            tid = r["trace_id"]
            closed = close_turn(
                tid,
                measured_frontier_tokens=1240,
                input_tokens=900,
                output_tokens=340,
                outcome=Outcome(test_pass=True, source="test"),
                root=home,
            )
            self.assertEqual(closed["schema"], "z0int.turn_close.v1")
            self.assertEqual(closed["actual_tokens_saved"], 1860)
            self.assertEqual(closed["outcome_join"]["outcome_tier"], "gold")
            s = summarize_tokenomics(root=home)
            self.assertGreaterEqual(s["rows_with_baseline_and_measured"], 1)
            self.assertGreaterEqual(s["actual_tokens_saved"], 1860)
            self.assertGreaterEqual(s["measured_frontier_tokens_sum"], 1240)
            self.assertGreaterEqual(s["verified_tasks"], 1)


    def test_partial_close_preserves_observed_tokens_without_measured_savings(self):
        from z0int.receipt import append_receipt, build_receipt, close_turn, summarize_tokenomics

        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            row = append_receipt(
                build_receipt(
                    capability_id="coding.edit",
                    route="model",
                    baseline_input_tokens=2500,
                    baseline_output_tokens=600,
                ),
                root=home,
            )
            closed = close_turn(
                row["trace_id"],
                measured_frontier_tokens=1240,
                input_tokens=900,
                output_tokens=340,
                measurement_state="partial",
                state_reason="char_count_proxy",
                root=home,
            )
            self.assertEqual(closed["measurement_state"], "partial")
            self.assertIsNone(closed["actual_tokens_saved"])
            self.assertEqual(closed["measured_frontier_tokens"], 1240)
            summary = summarize_tokenomics(root=home)
            self.assertEqual(summary["actual_tokens_saved"], 0)
            self.assertEqual(summary["measurement_state_counts"].get("partial"), 1)

    def test_complete_close_mints_authoritative_savings(self):
        from z0int.receipt import append_receipt, build_receipt, close_turn, summarize_tokenomics

        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            row = append_receipt(
                build_receipt(
                    capability_id="coding.edit",
                    route="model",
                    baseline_input_tokens=2500,
                    baseline_output_tokens=600,
                ),
                root=home,
            )
            closed = close_turn(
                row["trace_id"],
                measured_frontier_tokens=1240,
                measurement_state="complete",
                state_reason="provider_turn_usage",
                root=home,
            )
            self.assertEqual(closed["actual_tokens_saved"], 1860)
            summary = summarize_tokenomics(root=home)
            self.assertGreaterEqual(summary["actual_tokens_saved_authoritative"], 1860)
            self.assertEqual(summary["actual_tokens_saved_provisional"], 0)


class CounterfactualMine(unittest.TestCase):
    def test_grade_and_mine_smoke(self):
        from z0int.counterfactual import grade_snapshot, mine_omp_sessions, summarize_replay

        self.assertEqual(
            grade_snapshot(
                has_prompt=True,
                has_output=True,
                has_model=True,
                has_usage=True,
                has_env=True,
                has_verifier=False,
            ),
            "B",
        )
        self.assertEqual(
            grade_snapshot(
                has_prompt=True,
                has_output=True,
                has_model=False,
                has_usage=False,
                has_env=False,
                has_verifier=False,
            ),
            "C",
        )
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            sess = home / "sessions" / "bucket"
            sess.mkdir(parents=True)
            # minimal synthetic OMP session with Grok assistant turn
            rows = [
                {
                    "type": "session",
                    "id": "sess-test",
                    "cwd": "/tmp/proj",
                    "timestamp": "2026-09-18T00:00:00Z",
                },
                {
                    "type": "message",
                    "message": {
                        "role": "user",
                        "content": "fix the recovery path",
                        "timestamp": "2026-09-18T00:00:01Z",
                    },
                },
                {
                    "type": "message",
                    "message": {
                        "role": "assistant",
                        "provider": "xai-oauth",
                        "model": "grok-composer-2.5-fast",
                        "content": [{"type": "text", "text": "done"}],
                        "usage": {"input": 100, "output": 20, "totalTokens": 120, "cost": {"total": 0.01}},
                        "timestamp": "2026-09-18T00:00:02Z",
                    },
                },
            ]
            (sess / "s.jsonl").write_text("\n".join(json.dumps(r) for r in rows) + "\n")
            rep = mine_omp_sessions(sessions_root=home / "sessions", root=home, limit=100)
            self.assertTrue(rep["ok"])
            self.assertGreaterEqual(rep["n_written"], 1)
            self.assertGreaterEqual(rep["grades"].get("B", 0), 1)
            s = summarize_replay(root=home)
            self.assertGreaterEqual(s["n_snapshots"], 1)


class ReceiptVersionAttribution(unittest.TestCase):
    @staticmethod
    def stream(root, name, row):
        path = root / "stream" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(row) + "\n")

    def test_explicit_root_never_uses_ambient_harness_stream(self):
        from unittest.mock import patch
        from z0int.receipt import find_receipt

        with tempfile.TemporaryDirectory() as tmp:
            selected, ambient = Path(tmp) / "selected", Path(tmp) / "ambient"
            for name in ("bridge.jsonl", "raw.jsonl"):
                with self.subTest(stream=name):
                    self.stream(selected, name, {"trace_id": "same-trace", "capability_id": "selected"})
                    self.stream(ambient, name, {"trace_id": "same-trace", "capability_id": "ambient"})
                    with patch.dict(os.environ, {"Z0INT_HOME": str(ambient)}):
                        row = find_receipt("same-trace", root=selected)
                    self.assertEqual(row["capability_id"], "selected")

    def test_canonical_close_and_join_override_old_bridge_snapshot(self):
        from unittest.mock import patch
        from z0int.receipt import append_receipt, close_turn, find_receipt, find_receipt_by_extra, join_outcome

        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {"Z0INT_HOME": tmp}):
            root = Path(tmp)
            row = append_receipt({"trace_id": "turn", "capability_id": "fixture",
                                  "extra": {"observation_id": "observation"}}, root=root)
            self.stream(root, "bridge.jsonl", {"trace_id": "turn", "receipt": row})
            close_turn("turn", measured_frontier_tokens=123, measurement_state="complete", root=root)
            join_outcome("turn", {"test_pass": True, "source": "fixture"}, root=root)
            for found in (find_receipt("turn", root=root),
                          find_receipt_by_extra("observation_id", "observation", root=root)):
                self.assertEqual(found["measured_frontier_tokens"], 123)
                self.assertTrue(found["outcome"]["test_pass"])

    def test_summary_counts_each_receipt_identity_once_after_repeated_joins(self):
        from unittest.mock import patch
        from z0int.receipt import append_receipt, close_turn, join_outcome, summarize_tokenomics

        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {"Z0INT_HOME": tmp}):
            root = Path(tmp)
            row = append_receipt({"trace_id": "turn", "capability_id": "fixture",
                                  "baseline_input_tokens": 900, "baseline_output_tokens": 100,
                                  "estimated_frontier_tokens_avoided": 1000}, root=root)
            self.stream(root, "bridge.jsonl", {"trace_id": "turn", "receipt": row})
            close_turn("turn", measured_frontier_tokens=100, measurement_state="complete", root=root)
            for _ in range(2):
                join_outcome("turn", {"test_pass": True, "source": "fixture"}, root=root)
            summary = summarize_tokenomics(root=root)
            self.assertEqual(summary["rows"], 1)
            self.assertEqual(summary["verified_tasks"], 1)
            self.assertEqual(summary["baseline_tokens_sum"], 1000)
            self.assertEqual(summary["measured_frontier_tokens_sum"], 100)
            self.assertEqual(summary["frontier_tokens_avoided_est"], 1000)
            self.assertEqual(summary["actual_tokens_saved_authoritative"], 900)
            self.assertEqual(summary["tokens_per_verified_task_authoritative"], 100)

    def test_later_failure_revokes_prior_success_credit(self):
        from unittest.mock import patch
        from z0int.receipt import append_receipt, join_outcome, summarize_tokenomics

        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {"Z0INT_HOME": tmp}):
            root = Path(tmp)
            append_receipt({"trace_id": "turn", "capability_id": "fixture"}, root=root)
            join_outcome("turn", {"test_pass": True, "source": "fixture"}, root=root)
            join_outcome("turn", {"test_pass": False, "source": "fixture"}, root=root)
            summary = summarize_tokenomics(root=root)
            self.assertEqual(summary["verified_tasks"], 0)
            self.assertEqual(summary["by_tier"], {"negative": 1})

    def test_estimated_baseline_is_not_verified_token_measurement(self):
        from z0int.receipt import append_receipt, summarize_tokenomics

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            append_receipt({"trace_id": "turn", "baseline_input_tokens": 900,
                            "measurement_state": "complete", "outcome": {"test_pass": True}}, root=root)
            summary = summarize_tokenomics(root=root)
            self.assertEqual(summary["verified_tasks"], 1)
            self.assertFalse(summary["verified_token_measurement_authoritative"])
            self.assertIsNone(summary["tokens_per_verified_task_authoritative"])
            self.assertIsNone(summary["tokens_per_verified_task"])

    def test_limit_counts_current_identities_not_append_versions(self):
        from z0int.receipt import append_receipt, summarize_tokenomics

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for trace_id in ("old", "attempt-one", "attempt-two", "attempt-one"):
                append_receipt({"trace_id": trace_id, "capability_id": trace_id,
                                "measured_frontier_tokens": 10}, root=root)
            summary = summarize_tokenomics(root=root, limit=2)
            self.assertEqual(summary["rows"], 2)
            self.assertEqual(summary["by_capability"], {"attempt-two": 1, "attempt-one": 1})
            self.assertEqual(summary["measured_frontier_tokens_sum"], 20)
            self.assertEqual(summarize_tokenomics(root=root, limit=0)["rows"], 0)

    def test_removed_correlation_does_not_match_stale_snapshot(self):
        from z0int.receipt import append_receipt, find_receipt_by_extra

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            row = append_receipt({"trace_id": "turn", "extra": {"observation_id": "old"}}, root=root)
            self.stream(root, "bridge.jsonl", {"receipt": row})
            append_receipt({"trace_id": "turn", "extra": {"observation_id": "new"}}, root=root)
            self.assertIsNone(find_receipt_by_extra("observation_id", "old", root=root))
            self.assertEqual(find_receipt_by_extra("observation_id", "new", root=root)["trace_id"], "turn")

    def test_unidentified_stream_rows_cannot_create_decisions(self):
        from z0int.receipt import find_receipt, summarize_tokenomics

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.stream(root, "bridge.jsonl", {"outcome": {"test_pass": True}})
            with (root / "stream" / "bridge.jsonl").open("a") as stream:
                stream.write('null\n[]\n{bad json\n{"receipt":{"trace_id":"real","measured_frontier_tokens":7}}\n')
            self.assertEqual(find_receipt("real", root=root)["measured_frontier_tokens"], 7)
            summary = summarize_tokenomics(root=root)
            self.assertEqual(summary["rows"], 1)
            self.assertEqual(summary["verified_tasks"], 0)
            self.assertEqual(summary["measured_frontier_tokens_sum"], 7)


class ReceiptMeasurementCompleteness(unittest.TestCase):
    def test_one_component_does_not_become_a_complete_total(self):
        from z0int.receipt import append_receipt, close_turn, summarize_tokenomics

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for field in ("input_tokens", "output_tokens"):
                with self.subTest(field=field):
                    append_receipt({"trace_id": field, "baseline_input_tokens": 1000}, root=root)
                    result = close_turn(field, **{field: 100}, measurement_state="complete", root=root)
                    self.assertIsNone(result["measured_frontier_tokens"])
                    self.assertEqual(result["receipt"][field], 100)
                    self.assertEqual(result["measurement_state"], "partial")
                    self.assertIsNone(result["actual_tokens_saved"])
            self.assertEqual(summarize_tokenomics(root=root)["actual_tokens_saved_authoritative"], 0)

    def test_partial_correction_clears_old_total_and_savings(self):
        from z0int.receipt import append_receipt, close_turn

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            append_receipt({"trace_id": "turn", "baseline_input_tokens": 1000}, root=root)
            close_turn("turn", input_tokens=90, output_tokens=10, measurement_state="complete", root=root)
            corrected = close_turn("turn", input_tokens=150, root=root)
            self.assertIsNone(corrected["measured_frontier_tokens"])
            self.assertIsNone(corrected["actual_tokens_saved"])
            self.assertEqual(corrected["measurement_state"], "partial")
            self.assertNotIn("output_tokens", corrected["receipt"])

    def test_explicit_total_does_not_require_component_breakdown(self):
        from z0int.receipt import close_turn

        with tempfile.TemporaryDirectory() as tmp:
            result = close_turn("turn", measured_frontier_tokens=123, input_tokens=100,
                                measurement_state="complete", root=Path(tmp))
            self.assertEqual(result["measured_frontier_tokens"], 123)
            self.assertEqual(result["measurement_state"], "complete")

    def test_token_regression_remains_signed_in_close_and_summary(self):
        from z0int.receipt import append_receipt, close_turn, summarize_tokenomics

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for trace, measured in (("saved", 80), ("regressed", 160)):
                append_receipt({"trace_id": trace, "baseline_input_tokens": 100}, root=root)
                closed = close_turn(trace, measured_frontier_tokens=measured,
                                    measurement_state="complete", root=root)
                self.assertEqual(closed["actual_tokens_saved"], 100 - measured)
            summary = summarize_tokenomics(root=root)
            self.assertEqual(summary["actual_tokens_saved"], -40)
            self.assertEqual(summary["actual_tokens_saved_authoritative"], -40)

    def test_no_usage_cannot_infer_full_baseline_savings(self):
        from z0int.receipt import build_receipt

        for usage in ({}, {"input_tokens": 20}, {"output_tokens": 20}):
            with self.subTest(usage=usage):
                receipt = build_receipt(baseline_input_tokens=1000, **usage)
                self.assertIsNone(receipt.tokens_saved_est())


if __name__ == "__main__":
    unittest.main()
