import tempfile
import unittest
from pathlib import Path

from z0int import paths
from z0int.ao_bridge import OUTCOME_SCHEMA, join_ao_outcome, spawn_decision
from z0int.ao_experiment import register_pair
from z0int.ao_report import build_ao_promotion_report
from z0int.counterfactual import SNAPSHOTS_NAME
from z0int.receipt import append_receipt, attach_experiment, find_receipt, join_outcome


def spawn_request(trace_id: str, session_id: str):
    return {
        "schema": "ao.z0int.spawn.v1",
        "trace_id": trace_id,
        "session_id": session_id,
        "project_id": "proj",
        "kind": "worker",
        "task": "fix the retry race",
        "current": {
            "harness": "codex",
            "model": "gpt-5",
            "mode": "chat",
            "permission": "default",
        },
        "constraints": {
            "explicit_harness": False,
            "explicit_model": False,
            "explicit_mode": False,
        },
    }


def evidence(disposition: str, *, terminated: bool, merged: bool):
    return {
        "project_id": "proj",
        "kind": "worker",
        "harness": "codex",
        "mode": "chat",
        "model": "gpt-5",
        "activity": "idle",
        "disposition": disposition,
        "terminated": terminated,
        "scm_complete": disposition != "seed_deleted",
        "prs": [] if disposition == "seed_deleted" else [{
            "url": "https://github.com/example/repo/pull/7",
            "number": 7,
            "draft": False,
            "merged": merged,
            "closed": False,
            "ci": "passing",
            "review": "approved",
            "mergeability": "mergeable",
            "review_comments": False,
            "external_approved": True,
            "external_changes_requested": False,
            "external_comments": False,
            "head_sha": "abc123",
        }],
    }


class AOPromotionReportTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_report_preserves_missing_measurements_and_is_deterministic(self):
        spawn_decision(spawn_request("ao-spawn-s1", "s1"), root=self.root)
        spawn_decision(spawn_request("ao-spawn-s2", "s2"), root=self.root)

        # Keep one explicit measurement and strip the other's measurement
        # fields to prove missing telemetry never becomes an implicit zero.
        row = dict(find_receipt("ao-spawn-s1", root=self.root))
        row["latency_ms"] = 120.0
        row["measured_frontier_tokens"] = 90
        row["estimated_frontier_tokens_avoided"] = 30
        row["extra"] = dict(row["extra"])
        row["extra"]["decision_measurement"] = dict(row["extra"]["decision_measurement"])
        row["extra"]["decision_measurement"]["cost_usd"] = 0.25
        append_receipt(row, root=self.root)

        missing = dict(find_receipt("ao-spawn-s2", root=self.root))
        for key in (
            "latency_ms", "input_tokens", "output_tokens", "cached_input_tokens",
            "measured_frontier_tokens",
        ):
            missing.pop(key, None)
        missing["extra"] = dict(missing["extra"])
        missing["extra"].pop("decision_measurement", None)
        append_receipt(missing, root=self.root)

        join_ao_outcome({
            "schema": OUTCOME_SCHEMA,
            "trace_id": "ao-spawn-s1",
            "outcome_id": "ao-outcome-s1-merged",
            "session_id": "s1",
            "outcome": {
                "pr_merged": True,
                "source": "agent-orchestrator",
                "verification_source": "ao-pr-merge",
            },
            "evidence": evidence("terminated", terminated=True, merged=True),
        }, root=self.root)

        join_ao_outcome({
            "schema": OUTCOME_SCHEMA,
            "trace_id": "ao-spawn-s2",
            "outcome_id": "ao-outcome-s2-seed-deleted",
            "session_id": "s2",
            "outcome": {"source": "agent-orchestrator"},
            "evidence": evidence("seed_deleted", terminated=False, merged=False),
        }, root=self.root)

        first = build_ao_promotion_report(root=self.root)
        second = build_ao_promotion_report(root=self.root)

        self.assertEqual(first["snapshot_sha256"], second["snapshot_sha256"])
        self.assertEqual(first["decisions"]["count"], 2)
        self.assertEqual(first["decisions"]["abstain"], 2)
        self.assertEqual(first["outcomes"]["join_coverage"], 1.0)
        self.assertEqual(first["outcomes"]["terminal_join_coverage"], 0.5)
        self.assertEqual(first["outcomes"]["final_disposition_join_coverage"], 1.0)
        self.assertEqual(first["outcomes"]["verified_positive_decisions"], 1)

        latency = first["measurements"]["decision_latency_ms"]
        self.assertEqual(latency["count"], 1)
        self.assertEqual(latency["coverage"], 0.5)
        self.assertEqual(latency["p50"], 120.0)
        self.assertEqual(latency["p95"], 120.0)

        tokens = first["measurements"]["frontier_tokens"]
        self.assertEqual(tokens["count"], 1)
        self.assertEqual(tokens["coverage"], 0.5)
        self.assertEqual(tokens["sum"], 90)

        self.assertEqual(first["measurements"]["cost"]["count"], 1)
        self.assertEqual(first["measurements"]["cost"]["coverage"], 0.5)
        self.assertEqual(first["measurements"]["cost"]["sum_usd"], 0.25)
        self.assertIsNone(first["measurements"]["cost"]["delta"])
        self.assertIsNone(first["decisions"]["safe_coverage"])
        self.assertFalse(first["comparison"]["available"])
        self.assertEqual(first["comparison"]["matched_pairs"], 0)
        self.assertIsNone(first["comparison"]["verified_positive"]["delta"])
        self.assertEqual(first["promotion"]["decision"], "not_computed")
        self.assertIn(
            "no_non_abstain_policy_decisions",
            first["promotion"]["evidence_gaps"],
        )
        family = first["families"]["worker:codex:chat"]
        self.assertEqual(family["decisions"], 2)
        self.assertEqual(family["outcome_join_coverage"], 1.0)
        self.assertFalse(family["calibration_ready"])

    def test_report_computes_only_explicit_matched_pair_deltas(self):
        spawn_decision(spawn_request("ao-spawn-candidate", "candidate"), root=self.root)
        spawn_decision(spawn_request("ao-spawn-reference", "reference"), root=self.root)

        candidate = dict(find_receipt("ao-spawn-candidate", root=self.root))
        candidate["latency_ms"] = 10.0
        candidate["measured_frontier_tokens"] = 20
        candidate["extra"] = dict(candidate["extra"])
        candidate["extra"]["decision_measurement"] = dict(
            candidate["extra"]["decision_measurement"]
        )
        candidate["extra"]["decision_measurement"]["cost_usd"] = 0.02
        candidate = attach_experiment(
            candidate,
            experiment_id="exp-1",
            pair_id="pair-1",
            task_snapshot_id="task-1",
            arm_id="candidate",
        )
        append_receipt(candidate, root=self.root)

        reference = dict(find_receipt("ao-spawn-reference", root=self.root))
        reference["latency_ms"] = 15.0
        reference["measured_frontier_tokens"] = 50
        reference["extra"] = dict(reference["extra"])
        reference["extra"]["decision_measurement"] = dict(
            reference["extra"]["decision_measurement"]
        )
        reference["extra"]["decision_measurement"]["cost_usd"] = 0.05
        reference = attach_experiment(
            reference,
            experiment_id="exp-1",
            pair_id="pair-1",
            task_snapshot_id="task-1",
            arm_id="reference",
        )
        append_receipt(reference, root=self.root)

        join_ao_outcome({
            "schema": OUTCOME_SCHEMA,
            "trace_id": "ao-spawn-candidate",
            "outcome_id": "ao-outcome-candidate-merged",
            "session_id": "candidate",
            "outcome": {
                "pr_merged": True,
                "source": "agent-orchestrator",
                "verification_source": "ao-pr-merge",
            },
            "evidence": evidence("terminated", terminated=True, merged=True),
        }, root=self.root)

        negative_evidence = evidence("terminated", terminated=True, merged=False)
        negative_evidence["prs"][0]["ci"] = "failing"
        join_ao_outcome({
            "schema": OUTCOME_SCHEMA,
            "trace_id": "ao-spawn-reference",
            "outcome_id": "ao-outcome-reference-ci-failed",
            "session_id": "reference",
            "outcome": {
                "ci_failed": True,
                "source": "agent-orchestrator",
                "verification_source": "ao-ci",
            },
            "evidence": negative_evidence,
        }, root=self.root)

        report = build_ao_promotion_report(root=self.root)
        comparison = report["comparison"]
        self.assertTrue(comparison["available"])
        self.assertEqual(comparison["matched_pairs"], 1)
        self.assertEqual(comparison["candidate_reference_outcome_pairs"], 1)
        self.assertEqual(comparison["verified_positive"]["candidate_rate"], 1.0)
        self.assertEqual(comparison["verified_positive"]["reference_rate"], 0.0)
        self.assertEqual(comparison["verified_positive"]["delta"], 1.0)
        self.assertEqual(comparison["decision_latency_ms"]["delta_mean"], -5.0)
        self.assertEqual(comparison["frontier_tokens"]["delta_mean"], -30.0)
        self.assertAlmostEqual(
            comparison["decision_cost_usd"]["delta_mean"],
            -0.03,
        )
        self.assertEqual(comparison["promotion_decision"], "not_computed")
        self.assertNotIn(
            "counterfactual_or_control_delta_not_measured",
            report["promotion"]["evidence_gaps"],
        )

    def test_report_consumes_registry_pair_with_materialized_reference(self):
        spawn_decision(
            spawn_request("ao-spawn-registry-candidate", "candidate"),
            root=self.root,
        )
        replay_dir = paths.ensure_layout(self.root)["replay"]
        snapshot = {
            "schema": "z0int.task_snapshot.v1",
            "task_snapshot_id": "registry-task",
            "arm_id": "reference",
            "selection_policy": "historical_replay",
            "replay_grade": "B",
            "session_id": "historical",
            "provider": "xai",
            "model": "grok-reference",
            "treatment_hash": "reference-treatment",
            "prompt_hash": "prompt-hash",
            "output_hash": "output-hash",
            "usage": {
                "input": 30,
                "output": 20,
                "cacheRead": 0,
                "totalTokens": 50,
                "cost_total": 0.05,
            },
        }
        (replay_dir / SNAPSHOTS_NAME).write_text(
            __import__("json").dumps(snapshot) + "\n",
            encoding="utf-8",
        )

        pair = register_pair(
            experiment_id="registry-exp",
            pair_id="registry-pair",
            task_snapshot_id="registry-task",
            candidate_trace_id="ao-spawn-registry-candidate",
            root=self.root,
        )
        reference_trace = pair["reference_trace_id"]

        join_ao_outcome({
            "schema": OUTCOME_SCHEMA,
            "trace_id": "ao-spawn-registry-candidate",
            "outcome_id": "ao-outcome-registry-candidate-merged",
            "session_id": "candidate",
            "outcome": {
                "pr_merged": True,
                "source": "agent-orchestrator",
                "verification_source": "ao-pr-merge",
            },
            "evidence": evidence("terminated", terminated=True, merged=True),
        }, root=self.root)
        join_outcome(
            reference_trace,
            {
                "ci_failed": True,
                "source": "historical_verifier",
                "verification_source": "frozen_reference",
            },
            root=self.root,
        )

        first = build_ao_promotion_report(root=self.root)
        second = build_ao_promotion_report(root=self.root)
        comparison = first["comparison"]

        self.assertEqual(first["snapshot_sha256"], second["snapshot_sha256"])
        self.assertEqual(comparison["source"], "pair_registry")
        self.assertTrue(comparison["available"])
        self.assertEqual(comparison["matched_pairs"], 1)
        self.assertEqual(comparison["candidate_reference_outcome_pairs"], 1)
        self.assertEqual(comparison["verified_positive"]["delta"], 1.0)
        self.assertEqual(comparison["frontier_tokens"]["pairs"], 1)
        self.assertEqual(comparison["frontier_tokens"]["delta_mean"], -50.0)
        self.assertEqual(comparison["decision_cost_usd"]["pairs"], 1)
        self.assertAlmostEqual(
            comparison["decision_cost_usd"]["delta_mean"],
            -0.05,
        )

    def test_report_rejects_pair_with_mismatched_task_snapshot(self):
        spawn_decision(spawn_request("ao-spawn-c", "c"), root=self.root)
        spawn_decision(spawn_request("ao-spawn-r", "r"), root=self.root)

        candidate = attach_experiment(
            dict(find_receipt("ao-spawn-c", root=self.root)),
            experiment_id="exp-2",
            pair_id="pair-2",
            task_snapshot_id="task-a",
            arm_id="candidate",
        )
        reference = attach_experiment(
            dict(find_receipt("ao-spawn-r", root=self.root)),
            experiment_id="exp-2",
            pair_id="pair-2",
            task_snapshot_id="task-b",
            arm_id="reference",
        )
        append_receipt(candidate, root=self.root)
        append_receipt(reference, root=self.root)

        comparison = build_ao_promotion_report(root=self.root)["comparison"]
        self.assertFalse(comparison["available"])
        self.assertEqual(comparison["matched_pairs"], 0)
        self.assertEqual(comparison["excluded"]["task_snapshot_mismatch"], 1)

    def test_empty_report_uses_none_for_undefined_rates(self):
        report = build_ao_promotion_report(root=self.root)
        self.assertEqual(report["decisions"]["count"], 0)
        self.assertIsNone(report["decisions"]["abstention_rate"])
        self.assertIsNone(report["outcomes"]["join_coverage"])
        self.assertIsNone(
            report["measurements"]["decision_latency_ms"]["coverage"]
        )
        self.assertIsNone(report["measurements"]["frontier_tokens"]["sum"])


if __name__ == "__main__":
    unittest.main()
