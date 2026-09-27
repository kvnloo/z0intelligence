#!/usr/bin/env python3
"""Build the orchestration-v2 expansion corpus (Phase 1B section F).

The 12 scenarios in ``orchestration-v1`` stay exactly as they are and are now the
**frozen regression cohort**: Phase 1's numbers are quoted against that file, so
it must not move.  This builder adds a separate 28-scenario expansion covering
the task families Phase 1 could not measure, as distinct files rather than
copies -- the v1 rows are never duplicated here, which would fork the fixture.

Families, chosen because the 12-scenario cohort is thin on all of them:

``premature_stop_trap``      stopping after one dispatch loses the task
``unnecessary_tool_trap``    a cheap, plausible, useless call wastes budget
``dependency_chain``         ordered multi-step work with real constraints
``recovery_after_bad_result``the world returns an error or an empty result
``done_is_optimal``          no offered callable can help; declining is correct
``expensive_optional_evidence``  legal but never necessary spend
``escalation_required``      the cheap tool tier is provably insufficient

    python benchmarks/build_orchestration_v2.py
"""

from __future__ import annotations

import json
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
OUT = REPO / "benchmarks" / "fixtures" / "orchestration-v2" / "scenarios.jsonl"

TOOL, SPECIALIST, GENERALIST = "tool", "specialist", "generalist"


def cal(name, kind, cost, resolves=(), description=None, latency="fast"):
    return {
        "name": name,
        "kind": kind,
        "description": description or name.replace("_", " "),
        "cost_units": cost,
        "resolves": list(resolves),
        "latency_class": latency,
    }


def scenario(sid, family, task, requires, callables, *, budget=10, max_turns=6,
             solvable=True, order=(), faulty=(), optional=(), escalation=False, note=""):
    return {
        "scenario_id": sid,
        "family": family,
        "task": task,
        "requires": list(requires),
        "budget_units": budget,
        "max_turns": max_turns,
        "solvable": solvable,
        "order_constraints": [list(p) for p in order],
        "faulty_callables": [list(p) for p in faulty],
        "optional_evidence": list(optional),
        "requires_escalation": escalation,
        "note": note,
        "callables": callables,
    }


def build():
    s = []

    # ------------------------------------------------------------------
    # premature_stop_trap -- one dispatch is not the task
    # ------------------------------------------------------------------
    s.append(scenario(
        "trap_two_required_before_stop", "premature_stop_trap",
        "Check whether the release notes mention the security fix and whether the fix is in the built binary.",
        ["note_mentions_fix", "binary_contains_fix"],
        [cal("grep_release_notes", TOOL, 1, ["note_mentions_fix"], "Search the release notes"),
         cal("inspect_binary_symbols", TOOL, 2, ["binary_contains_fix"], "List symbols in the binary"),
         cal("release_generalist_llm", GENERALIST, 5, ["note_mentions_fix", "binary_contains_fix"],
             "Answer release questions end to end")],
        budget=12, note="answering after one of two checks is a partial answer, not a stop"))

    s.append(scenario(
        "trap_partial_cover_looks_complete", "premature_stop_trap",
        "Summarise the incident and state the mitigation that was applied.",
        ["timeline", "mitigation"],
        [cal("fetch_timeline", TOOL, 1, ["timeline"]),
         cal("fetch_change_log", TOOL, 2, ["mitigation"]),
         cal("incident_generalist_llm", GENERALIST, 6, ["timeline", "mitigation"])],
        budget=10, note="one tool resolves 'timeline' and a weak orchestrator stops there"))

    s.append(scenario(
        "trap_three_checks_one_tool", "premature_stop_trap",
        "Verify the config is valid, the migration ran, and the service is healthy.",
        ["config_ok", "migration_ran", "health_ok"],
        [cal("validate_config", TOOL, 1, ["config_ok"]),
         cal("check_migration_table", TOOL, 2, ["migration_ran"]),
         cal("query_health_endpoint", TOOL, 2, ["health_ok"]),
         cal("ops_generalist_llm", GENERALIST, 7, ["config_ok", "migration_ran", "health_ok"])],
        budget=14, max_turns=7,
        note="three independent checks; stopping after two is the classic failure"))

    s.append(scenario(
        "trap_dependency_then_stop", "premature_stop_trap",
        "Take a database backup and then confirm the backup file exists.",
        ["backup_taken", "backup_verified"],
        [cal("db_backup", TOOL, 2, ["backup_taken"]),
         cal("db_verify_backup", TOOL, 1, ["backup_verified"]),
         cal("db_generalist_llm", GENERALIST, 5, ["backup_taken", "backup_verified"])],
        budget=10, order=[("db_backup", "db_verify_backup")],
        note="the second step only makes sense after the first; both are required"))

    # ------------------------------------------------------------------
    # unnecessary_tool_trap -- plausible, cheap, useless
    # ------------------------------------------------------------------
    s.append(scenario(
        "trap_disk_check_for_code_question", "unnecessary_tool_trap",
        "Explain why the retry loop in worker.py can spin forever.",
        ["explanation"],
        [cal("df_command", TOOL, 1, [], "Report free disk space"),
         cal("read_source_file", TOOL, 1, [], "Print a source file"),
         cal("code_generalist_llm", GENERALIST, 5, ["explanation"], "Explain code")],
        budget=8,
        note="df is cheap and relevant-sounding; taking it is pure waste here"))

    s.append(scenario(
        "trap_regenerate_derived_artifact", "unnecessary_tool_trap",
        "Report the checksum already recorded in the lockfile.",
        ["checksum"],
        [cal("read_lockfile", TOOL, 1, ["checksum"]),
         cal("recompute_checksum", TOOL, 3, [], "Re-download and hash the artifact"),
         cal("hash_tool", TOOL, 1, [], "Hash a local file")],
        budget=6, note="the value is already on disk; recomputation is a legal but pointless spend"))

    s.append(scenario(
        "trap_image_tool_for_text_task", "unnecessary_tool_trap",
        "Classify the sentiment of the attached review text.",
        ["sentiment"],
        [cal("sentiment_llm", SPECIALIST, 3, ["sentiment"]),
         cal("image_gen", TOOL, 4, [], "Generate an illustration"),
         cal("fetch_timeline", TOOL, 1, [], "Fetch a timeline")],
        budget=8, note="two decoys; only one callable resolves anything"))

    s.append(scenario(
        "trap_already_answered_by_cache", "unnecessary_tool_trap",
        "Answer the pricing question for the EU region.",
        ["price"],
        [cal("cache_lookup", TOOL, 1, ["price"]),
         cal("frontier_llm", GENERALIST, 9, ["price"], "Ask the frontier model"),
         cal("web_search", TOOL, 3, ["price"], "Search the web")],
        budget=12,
        note="cheapest correct path exists; escalating past it is needless escalation"))

    # ------------------------------------------------------------------
    # dependency_chain -- ordered multi-step work
    # ------------------------------------------------------------------
    s.append(scenario(
        "chain_four_step_release", "dependency_chain",
        "Cut a release: bump the version, build, sign, publish.",
        ["bumped", "built", "signed", "published"],
        [cal("bump_version", TOOL, 1, ["bumped"]),
         cal("run_build", TOOL, 3, ["built"]),
         cal("sign_artifact", TOOL, 2, ["signed"]),
         cal("publish_artifact", TOOL, 2, ["published"]),
         cal("release_generalist_llm", GENERALIST, 8, ["bumped", "built", "signed", "published"])],
        budget=16, max_turns=8,
        order=[("bump_version", "run_build"), ("run_build", "sign_artifact"),
               ("sign_artifact", "publish_artifact")],
        note="a genuine four-stage dependency chain with strict ordering"))

    s.append(scenario(
        "chain_fan_in_then_transform", "dependency_chain",
        "Merge the two coverage reports and compute the delta.",
        ["cov_a", "cov_b", "delta"],
        [cal("fetch_coverage_a", TOOL, 2, ["cov_a"]),
         cal("fetch_coverage_b", TOOL, 2, ["cov_b"]),
         cal("merge_reports", TOOL, 2, ["delta"], description="Merge reports and diff them"),
         cal("coverage_specialist_llm", SPECIALIST, 6, ["cov_a", "cov_b", "delta"])],
        budget=12, order=[("fetch_coverage_a", "merge_reports"),
                          ("fetch_coverage_b", "merge_reports")],
        note="fan-in: two prerequisites before the join"))

    s.append(scenario(
        "chain_retry_after_transient", "dependency_chain",
        "Fetch the upstream manifest and confirm its signature.",
        ["manifest", "signature_ok"],
        [cal("fetch_manifest", TOOL, 2, ["manifest"]),
         cal("verify_signature", TOOL, 2, ["signature_ok"], description="Verify the manifest signature"),
         cal("supply_chain_specialist_llm", SPECIALIST, 5, ["manifest", "signature_ok"])],
        budget=10, order=[("fetch_manifest", "verify_signature")],
        note="verification is meaningless before the artifact is present"))

    s.append(scenario(
        "chain_long_pipeline_with_cheap_tools", "dependency_chain",
        "Collect metrics, detect the anomaly, then attribute it to a commit.",
        ["metrics", "anomaly", "attribution"],
        [cal("fetch_metrics", TOOL, 2, ["metrics"]),
         cal("detect_anomaly", TOOL, 2, ["anomaly"]),
         cal("attribute_commit", TOOL, 3, ["attribution"]),
         cal("perf_generalist_llm", GENERALIST, 7, ["metrics", "anomaly", "attribution"])],
        budget=9, max_turns=6,
        order=[("fetch_metrics", "detect_anomaly"), ("detect_anomaly", "attribute_commit")],
        note="three cheap steps fit the budget only if nothing is wasted"))

    # ------------------------------------------------------------------
    # recovery_after_bad_result
    # ------------------------------------------------------------------
    s.append(scenario(
        "recover_build_repro_errors_then_specialist", "recovery_after_bad_result",
        "Diagnose the intermittent build failure.",
        ["root_cause"],
        [cal("static_analyse", TOOL, 1, [], "Read the build scripts"),
         cal("run_build_repro", TOOL, 2, ["root_cause"], "Reproduce the failure"),
         cal("infra_specialist_llm", SPECIALIST, 4, ["root_cause"], "Infra expert")],
        budget=10, faulty=[("run_build_repro", "error")],
        note="the repro tool errors on first use; the correct move is to keep going, not to stop"))

    s.append(scenario(
        "recover_empty_search_then_second_source", "recovery_after_bad_result",
        "Find the owner of the payments service.",
        ["owner"],
        [cal("search_internal_wiki", TOOL, 1, ["owner"], "Search the internal wiki"),
         cal("read_service_manifest", TOOL, 1, ["owner"], "Read the service manifest"),
         cal("org_generalist_llm", GENERALIST, 4, ["owner"])],
        budget=8, faulty=[("search_internal_wiki", "empty")],
        note="an empty result set is not an answer; a second source or the generalist is"))

    s.append(scenario(
        "recover_error_then_generalist", "recovery_after_bad_result",
        "Summarise the failing test's assertion and suggest a cause.",
        ["summary", "cause"],
        [cal("read_test_log", TOOL, 1, [], "Read the test log"),
         cal("parse_assertion", TOOL, 2, ["summary"], "Parse the assertion"),
         cal("root_cause_llm", SPECIALIST, 4, ["summary", "cause"], "Root-cause analysis")],
        budget=10, faulty=[("parse_assertion", "error")],
        note="after the parse fails, the specialist is the recovery path"))

    s.append(scenario(
        "recover_two_faulty_tools", "recovery_after_bad_result",
        "Establish whether the queue backlog is real and how deep it is.",
        ["backlog_confirmed", "depth"],
        [cal("query_queue_api", TOOL, 2, ["backlog_confirmed", "depth"], "Query the queue API"),
         cal("read_queue_metrics", TOOL, 2, ["backlog_confirmed", "depth"], "Read queue metrics"),
         cal("ops_generalist_llm", GENERALIST, 6, ["backlog_confirmed", "depth"])],
        budget=12, faulty=[("query_queue_api", "error")],
        note="two independent sources exist; a single failure must not end the run"))

    s.append(scenario(
        "recover_budget_after_wasted_retry", "recovery_after_bad_result",
        "Confirm the certificate chain is complete.",
        ["chain_ok"],
        [cal("inspect_chain", TOOL, 2, ["chain_ok"], "Inspect the certificate chain"),
         cal("verify_chain", TOOL, 2, ["chain_ok"], "Verify the chain"),
         cal("security_specialist_llm", SPECIALIST, 4, ["chain_ok"])],
        budget=6, faulty=[("inspect_chain", "empty")],
        note="tight budget: recovery requires not wasting the remaining units"))

    # ------------------------------------------------------------------
    # done_is_optimal -- declining is the win condition
    # ------------------------------------------------------------------
    s.append(scenario(
        "done_nothing_can_forecast", "done_is_optimal",
        "Forecast next quarter's revenue from the local repository.",
        ["forecast"],
        [cal("grep_logs", TOOL, 1, [], "Search local logs"),
         cal("count_lines", TOOL, 1, [], "Count lines of code"),
         cal("local_generalist_llm", GENERALIST, 4, [], "Local generalist")],
        budget=6, solvable=False,
        note="no callable resolves 'forecast'; spending anything is a loss"))

    s.append(scenario(
        "done_permission_is_missing", "done_is_optimal",
        "Deploy the staging build to production.",
        ["deployed"],
        [cal("build_status", TOOL, 1, [], "Read build status"),
         cal("staging_health", TOOL, 1, [], "Check staging health")],
        budget=8, solvable=False,
        note="the deploy capability is absent from the graph; the orchestrator must decline"))

    s.append(scenario(
        "done_user_already_has_answer", "done_is_optimal",
        "The user already pasted the answer; confirm nothing further is needed.",
        ["confirmed"],
        [cal("recompute_everything", TOOL, 5, [], "Recompute all derived values"),
         cal("frontier_llm", GENERALIST, 9, [], "Ask the frontier model")],
        budget=10, solvable=False,
        note="both callables are expensive and resolve nothing required"))

    s.append(scenario(
        "done_wrong_repo", "done_is_optimal",
        "Find the Terraform module in this repository.",
        ["module_found"],
        [cal("list_python_files", TOOL, 1, [], "List Python files"),
         cal("count_commits", TOOL, 1, [], "Count commits")],
        budget=4, solvable=False,
        note="there is no Terraform here; two plausible decoys and an empty required set match"))

    # ------------------------------------------------------------------
    # expensive_optional_evidence
    # ------------------------------------------------------------------
    s.append(scenario(
        "optional_full_benchmark_not_needed", "expensive_optional_evidence",
        "Report the current p50 latency of the local model endpoint.",
        ["p50"],
        [cal("read_metrics_endpoint", TOOL, 1, ["p50"]),
         cal("run_full_benchmark", TOOL, 8, [], "Run the entire benchmark suite"),
         cal("perf_generalist_llm", GENERALIST, 5, ["p50"])],
        budget=12, optional=["run_full_benchmark"],
        note="the metric already exists; running the suite is legal and useless"))

    s.append(scenario(
        "optional_second_opinion", "expensive_optional_evidence",
        "State the licence of the dependency pinned in the lockfile.",
        ["licence"],
        [cal("read_lockfile_entry", TOOL, 1, ["licence"]),
         cal("frontier_llm", GENERALIST, 9, ["licence"], "Frontier second opinion")],
        budget=12, optional=["frontier_llm"],
        note="a second opinion is not evidence; it is spend"))

    s.append(scenario(
        "optional_historical_correlation", "expensive_optional_evidence",
        "Report this build's duration.",
        ["duration"],
        [cal("read_build_report", TOOL, 1, ["duration"]),
         cal("scan_all_historical_builds", TOOL, 6, [], "Scan two years of builds")],
        budget=10, optional=["scan_all_historical_builds"],
        note="historical context does not resolve the required specialty"))

    s.append(scenario(
        "optional_translate_then_answer", "expensive_optional_evidence",
        "Answer whether the config key 'retry_limit' is set.",
        ["key_present"],
        [cal("grep_config", TOOL, 1, ["key_present"]),
         cal("translate_docs", SPECIALIST, 3, [], "Translate the docs"),
         cal("general_llm", GENERALIST, 5, ["key_present"])],
        budget=8, optional=["translate_docs"],
        note="translation is a real capability and entirely irrelevant here"))

    # ------------------------------------------------------------------
    # escalation_required -- the cheap tier provably cannot do it
    # ------------------------------------------------------------------
    s.append(scenario(
        "escalate_legal_review", "escalation_required",
        "Decide whether the proposed change violates the data-retention policy.",
        ["legal_review"],
        [cal("grep_policy", TOOL, 1, [], "Grep the policy text"),
         cal("legal_specialist_llm", SPECIALIST, 5, ["legal_review"], "Legal specialist"),
         cal("general_llm", GENERALIST, 6, ["legal_review"])],
        budget=12, escalation=True,
        note="no tool resolves the requirement; a model tier is mandatory"))

    s.append(scenario(
        "escalate_novel_schema_design", "escalation_required",
        "Design a schema for the new telemetry events.",
        ["schema_design"],
        [cal("list_existing_tables", TOOL, 1, [], "List existing tables"),
         cal("read_style_guide", TOOL, 1, [], "Read the naming style guide"),
         cal("design_specialist_llm", SPECIALIST, 5, ["schema_design"]),
         cal("frontier_llm", GENERALIST, 9, ["schema_design"])],
        budget=14, escalation=True,
        note="two cheap tools give context but never the deliverable"))

    s.append(scenario(
        "escalate_ambiguous_requirement", "escalation_required",
        "Resolve the contradictory acceptance criteria and pick one interpretation.",
        ["resolution"],
        [cal("read_ticket", TOOL, 1, [], "Read the ticket"),
         cal("read_spec", TOOL, 1, [], "Read the spec"),
         cal("reasoning_specialist_llm", SPECIALIST, 6, ["resolution"]),
         cal("frontier_llm", GENERALIST, 9, ["resolution"])],
        budget=13, max_turns=6, escalation=True,
        note="the cheap tier can gather both sides but cannot adjudicate"))

    return s


def main() -> int:
    scenarios = build()
    OUT.parent.mkdir(parents=True, exist_ok=True)
    with OUT.open("w", encoding="utf-8") as fh:
        for row in scenarios:
            fh.write(json.dumps(row, sort_keys=True) + "\n")
    families: dict[str, int] = {}
    for row in scenarios:
        families[row["family"]] = families.get(row["family"], 0) + 1
    print(f"wrote {len(scenarios)} scenarios to {OUT}")
    print("by family:", json.dumps(families, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
