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




class ReceiptJoin(unittest.TestCase):
    def test_replayed_receipts_count_one_task_and_one_token_measurement(self):
        from z0int.receipt import (
            Outcome, append_receipt, build_receipt, close_turn, join_outcome, summarize_tokenomics,
        )

        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            row = append_receipt(build_receipt(
                capability_id="coding.edit", route="local", baseline_input_tokens=1000,
                baseline_output_tokens=200, estimated_frontier_tokens_avoided=1200,
            ), root=home)
            trace = row["trace_id"]
            close_turn(trace, measured_frontier_tokens=500, measurement_state="complete",
                       outcome=Outcome(test_pass=True, source="ci"), root=home)
            join_outcome(trace, Outcome(test_pass=True, source="ci"), root=home)
            summary = summarize_tokenomics(root=home)
            self.assertEqual(summary["verified_tasks"], 1)
            self.assertEqual(summary["rows"], 1)
            self.assertEqual(summary["baseline_tokens_sum"], 1200)
            self.assertEqual(summary["measured_frontier_tokens_sum"], 500)
            self.assertEqual(summary["actual_tokens_saved_authoritative"], 700)
            self.assertEqual(summary["frontier_tokens_avoided_est"], 1200)
            self.assertEqual(len((home / "receipts" / "decisions.jsonl").read_text().splitlines()), 4)
            other = append_receipt(build_receipt(capability_id="coding.edit", route="local"), root=home)
            join_outcome(other["trace_id"], Outcome(test_pass=True, source="ci"), root=home)
            self.assertEqual(summarize_tokenomics(root=home)["verified_tasks"], 2)

    def test_latest_negative_canonical_outcome_beats_positive_history_and_bridge_mirror(self):
        from z0int.receipt import Outcome, append_receipt, build_receipt, find_receipt, join_outcome, summarize_tokenomics

        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            row = append_receipt(build_receipt(capability_id="coding.edit", route="local"), root=home)
            trace = row["trace_id"]
            join_outcome(trace, Outcome(test_pass=True, source="ci"), root=home)
            positive = find_receipt(trace, root=home)
            stream = home / "stream" / "bridge.jsonl"
            stream.parent.mkdir(parents=True, exist_ok=True)
            stream.write_text(json.dumps({"trace_id": trace, "receipt": positive}) + "\n")
            join_outcome(trace, Outcome(test_pass=False, source="ci"), root=home)
            summary = summarize_tokenomics(root=home)
            self.assertEqual(summary["verified_tasks"], 0)
            self.assertEqual(summary["by_tier"], {"negative": 1})
            self.assertEqual(summary["rows"], 1)
            self.assertEqual(len((home / "receipts" / "outcomes.jsonl").read_text().splitlines()), 2)
            self.assertEqual(json.loads(stream.read_text())["receipt"]["outcome_tier"], "gold")

    def test_legacy_nested_receipt_uses_outer_trace_and_keeps_unknown_identities_separate(self):
        from z0int.receipt import summarize_tokenomics

        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            stream = home / "stream" / "bridge.jsonl"
            stream.parent.mkdir(parents=True)
            rows = [
                {"trace_id": "legacy-trace", "receipt": {"outcome": {"test_pass": verdict, "source": "ci"}}}
                for verdict in (True, False)
            ] + [{"receipt": {"outcome": {"success": True}}} for _ in range(2)]
            stream.write_text("".join(json.dumps(row) + "\n" for row in rows))
            summary = summarize_tokenomics(root=home)
            self.assertEqual(summary["verified_tasks"], 0)
            self.assertEqual(summary["rows"], 3)
            self.assertEqual(summary["by_tier"], {"negative": 1, "soft": 2})

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




class ReceiptRevisionTruth(unittest.TestCase):
    def test_outcomeless_replay_preserves_verified_outcome_and_accounting(self):
        from z0int.receipt import Outcome, append_receipt, build_receipt, close_turn, summarize_tokenomics

        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            original = append_receipt(build_receipt(baseline_input_tokens=1000), root=home)
            close_turn(original["trace_id"], measured_frontier_tokens=400,
                       measurement_state="complete", outcome=Outcome(test_pass=True, source="ci"), root=home)
            before = (home / "receipts" / "decisions.jsonl").read_bytes()
            append_receipt(original, root=home)
            summary = summarize_tokenomics(root=home)
            self.assertEqual(summary["verified_tasks"], 1)
            self.assertEqual(summary["rows"], 1)
            self.assertEqual(summary["measured_frontier_tokens_sum"], 400)
            self.assertEqual(summary["actual_tokens_saved_authoritative"], 600)
            self.assertTrue((home / "receipts" / "decisions.jsonl").read_bytes().startswith(before))

    def test_bridge_only_negative_overrides_canonical_gold(self):
        from z0int.receipt import Outcome, append_receipt, build_receipt, find_receipt, join_outcome, summarize_tokenomics

        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            original = append_receipt(build_receipt(capability_id="canonical"), root=home)
            trace = original["trace_id"]
            joined = join_outcome(trace, Outcome(test_pass=True, source="ci"), root=home)
            stream = home / "stream" / "bridge.jsonl"
            stream.write_text(json.dumps({"trace_id": trace, "ts": joined["ts"] + 1,
                                          "receipt": {"capability_id": "mirror"},
                                          "outcome": {"ci_failed": True, "source": "ci"}}) + "\n")
            summary = summarize_tokenomics(root=home)
            self.assertEqual(summary["verified_tasks"], 0)
            self.assertEqual(summary["by_tier"], {"negative": 1})
            self.assertEqual(summary["by_capability"], {"canonical": 1})
            self.assertTrue(find_receipt(trace, root=home)["outcome"]["ci_failed"])

    def test_bridge_only_outcome_is_joined_to_outcomeless_canonical_receipt(self):
        from z0int.receipt import append_receipt, build_receipt, summarize_tokenomics

        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            original = append_receipt(build_receipt(), root=home)
            stream = home / "stream" / "bridge.jsonl"
            stream.write_text(json.dumps({"trace_id": original["trace_id"],
                                          "receipt": {"outcome": {"test_pass": True, "source": "ci"}}}) + "\n")
            summary = summarize_tokenomics(root=home)
            self.assertEqual(summary["rows"], 1)
            self.assertEqual(summary["verified_tasks"], 1)
            self.assertEqual(summary["by_tier"], {"gold": 1})

    def test_scrub_preserves_later_measured_close_and_original_evidence(self):
        from z0int.receipt import append_receipt, build_receipt, close_turn, find_receipt, scrub_contaminated_outcomes, summarize_tokenomics

        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            original = append_receipt(build_receipt(baseline_input_tokens=1000), root=home)
            trace = original["trace_id"]
            op = home / "receipts" / "outcomes.jsonl"
            op.write_text(json.dumps({"trace_id": trace, "ts": original["ts"],
                                      "outcome": {"success": True, "source": "bridge_turn_end"},
                                      "outcome_tier": "gold", "receipt": original}) + "\n")
            close_turn(trace, measured_frontier_tokens=400, input_tokens=300, output_tokens=100,
                       cached_input_tokens=200, measurement_state="complete", root=home)
            before = (home / "receipts" / "decisions.jsonl").read_bytes()
            self.assertEqual(scrub_contaminated_outcomes(root=home)["rewritten"], 1)
            summary = summarize_tokenomics(root=home)
            self.assertEqual(summary["measured_frontier_tokens_sum"], 400)
            self.assertEqual(summary["actual_tokens_saved_authoritative"], 600)
            self.assertEqual(summary["verified_tasks"], 0)
            self.assertEqual(find_receipt(trace, root=home)["cached_input_tokens"], 200)
            physical = json.loads((home / "receipts" / "decisions.jsonl").read_text().splitlines()[-1])
            self.assertEqual(physical.get("measured_frontier_tokens"), 400)
            self.assertEqual(physical.get("cached_input_tokens"), 200)
            self.assertTrue((home / "receipts" / "decisions.jsonl").read_bytes().startswith(before))

    def test_stale_positive_replay_cannot_overwrite_later_negative(self):
        from z0int.receipt import Outcome, append_receipt, build_receipt, find_receipt, join_outcome, summarize_tokenomics

        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            trace = append_receipt(build_receipt(), root=home)["trace_id"]
            join_outcome(trace, Outcome(test_pass=True, source="ci"), root=home)
            positive = find_receipt(trace, root=home)
            join_outcome(trace, Outcome(test_pass=False, source="ci"), root=home)
            append_receipt(positive, root=home)
            self.assertEqual(summarize_tokenomics(root=home)["by_tier"], {"negative": 1})

    def test_outcome_revision_controls(self):
        from z0int.receipt import append_receipt, build_receipt, find_receipt, summarize_tokenomics

        cases = [
            # Explicit later verification can reopen a previously negative assessment.
            ({"test_pass": False, "source": "ci"}, 1, {"test_pass": True, "source": "ci"}, 2, "gold"),
            # Equal-time and unclocked contradictory evidence cannot mint success.
            ({"test_pass": True, "source": "ci"}, 1, {"test_pass": False, "source": "ci"}, 1, "negative"),
            ({"test_pass": True, "source": "ci"}, 1, {"ci_failed": True, "source": "ci"}, None, "negative"),
            # A later ambient close is execution evidence, not a replacement verdict.
            ({"test_pass": True, "source": "ci"}, 1, {"test_pass": True, "source": "bridge_turn_end"}, 2, "gold"),
            ({"test_pass": False, "source": "ci"}, 1, {"execution_completed": True}, 2, "negative"),
        ]
        for first, first_ts, second, second_ts, tier in cases:
            with self.subTest(tier=tier, second=second), tempfile.TemporaryDirectory() as tmp:
                home = Path(tmp)
                row = append_receipt(build_receipt(), root=home)
                append_receipt({**row, "outcome": first, "outcome_ts": first_ts}, root=home)
                append_receipt({**row, "outcome": second, "outcome_ts": second_ts}, root=home)
                summary = summarize_tokenomics(root=home)
                self.assertEqual(summary["by_tier"], {tier: 1})
                self.assertEqual(summary["verified_tasks"], int(tier == "gold"))
                self.assertEqual(summary["rows"], 1)
                self.assertIsNotNone(find_receipt(row["trace_id"], root=home))

    def test_new_partial_close_defeats_stale_complete_snapshot(self):
        from z0int.receipt import append_receipt, build_receipt, close_turn, find_receipt, summarize_tokenomics

        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            trace = append_receipt(build_receipt(baseline_input_tokens=1000), root=home)["trace_id"]
            close_turn(trace, measured_frontier_tokens=400, measurement_state="complete", root=home)
            complete = find_receipt(trace, root=home)
            close_turn(trace, measured_frontier_tokens=450, measurement_state="partial", root=home)
            append_receipt(complete, root=home)
            summary = summarize_tokenomics(root=home)
            self.assertEqual(summary["measured_frontier_tokens_sum"], 450)
            self.assertEqual(summary["actual_tokens_saved"], 0)
            self.assertEqual(summary["measurement_state_counts"], {"partial": 1})

    def test_malformed_legacy_rows_and_nested_identity_do_not_hide_evidence(self):
        from z0int.receipt import summarize_tokenomics

        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            stream = home / "stream" / "bridge.jsonl"
            stream.parent.mkdir(parents=True)
            stream.write_text('[]\nnull\n' + json.dumps({"trace_id": None, "receipt": {
                "trace_id": "nested", "outcome": {"test_pass": True, "source": "ci"},
            }}) + "\n" + json.dumps({"trace_id": "malformed", "outcome": "invalid"}) + "\n")
            summary = summarize_tokenomics(root=home)
            self.assertEqual(summary["rows"], 2)
            self.assertEqual(summary["verified_tasks"], 1)

    def test_bridge_envelope_cannot_enter_a_canonical_outcome_join(self):
        from z0int.receipt import Outcome, append_receipt, build_receipt, join_outcome

        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            original = append_receipt(build_receipt(), root=home)
            stream = home / "stream" / "bridge.jsonl"
            stream.write_text(json.dumps({"trace_id": original["trace_id"], "receipt": original,
                                          "prompt": "synthetic-private-prompt", "preflight": {"private": True}}) + "\n")
            join_outcome(original["trace_id"], Outcome(test_pass=True, source="ci"), root=home)
            for filename in ("decisions.jsonl", "outcomes.jsonl"):
                self.assertNotIn("synthetic-private-prompt", (home / "receipts" / filename).read_text())

    def test_metadata_only_legacy_revision_cannot_retag_usage_time(self):
        from z0int.receipt import summarize_tokenomics

        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            stream = home / "stream" / "bridge.jsonl"
            stream.parent.mkdir(parents=True)
            stream.write_text("".join(json.dumps(row) + "\n" for row in [
                {"trace_id": "legacy", "ts": 10, "baseline_input_tokens": 1000,
                 "measured_frontier_tokens": 400, "measurement_state": "complete"},
                {"trace_id": "legacy", "ts": 100, "capability_id": "metadata"},
                {"trace_id": "legacy", "ts": 20, "baseline_input_tokens": 1000,
                 "measured_frontier_tokens": 450, "measurement_state": "partial"},
            ]))
            summary = summarize_tokenomics(root=home)
            self.assertEqual(summary["measured_frontier_tokens_sum"], 450)
            self.assertEqual(summary["actual_tokens_saved_authoritative"], 0)

    def test_new_bridge_envelope_cannot_retag_a_stale_positive_outcome(self):
        from z0int.receipt import append_receipt, build_receipt, summarize_tokenomics

        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            original = append_receipt(build_receipt(), root=home)
            append_receipt({**original, "outcome": {"ci_failed": True}, "outcome_ts": 20}, root=home)
            stream = home / "stream" / "bridge.jsonl"
            stream.write_text(json.dumps({"trace_id": original["trace_id"], "ts": 30, "receipt": {
                "trace_id": original["trace_id"], "ts": 10, "outcome": {"test_pass": True, "source": "ci"},
            }}) + "\n")
            self.assertEqual(summarize_tokenomics(root=home)["by_tier"], {"negative": 1})


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


if __name__ == "__main__":
    unittest.main()
