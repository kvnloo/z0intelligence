"""C3 e2e check over one e2e home: e2e_check.py <home> <runs>. Prints a JSON verdict; exit 1 on any failed check."""
import json
import sys
from pathlib import Path

H, RUNS = Path(sys.argv[1]), int(sys.argv[2])
Z = H / "z0home"
MARKERS = ("E2E_PROMPT_MARKER_C3", "E2E_TOOL_INPUT_MARKER_C3")


def rows(path):
    return [json.loads(line) for line in path.read_text().splitlines()] if path.is_file() else []


opps = rows(Z / "state/omp/opportunities.jsonl")
outs = rows(Z / "state/omp/outcomes.jsonl")
fails = rows(Z / "state/omp/failures.jsonl")
drops = rows(Z / "state/omp/drops.jsonl")
bridge = rows(Z / "stream/bridge.jsonl")
by_key = {o["turn_key"]: o for o in outs}
joined = [(o, by_key.get(o["turn_key"])) for o in opps]
leaks = {str(p.relative_to(H)): [m for m in MARKERS if m in p.read_text(errors="replace")]
         for p in Z.rglob("*") if p.is_file()}
leaks = {k: v for k, v in leaks.items() if v}
counters = json.loads((Z / "shadow/cognition-shadow-counters.json").read_text()) \
    if (Z / "shadow/cognition-shadow-counters.json").is_file() else {}
spawns = (H / "python-argv.log").read_text().splitlines() if (H / "python-argv.log").is_file() else []
checks = {
    "one_opportunity_per_run": len(opps) == RUNS,
    "one_outcome_per_run": len(outs) == RUNS,
    "all_joined_on_turn_key": all(out is not None for _, out in joined) and len(joined) == RUNS,
    "join_fields_agree": all(o["work_item_id"] == out["work_item_id"] and o["cohort"] == out["cohort"]
                             and o["model_id"] == out["model_id"] for o, out in joined if out),
    "schemas": all(o["schema"] == "z0int.omp.opportunity_record.v0" for o in opps)
    and all(o["schema"] == "z0int.omp.turn_outcome.v0" for o in outs),
    "cohort_interactive": all(o["cohort"] == "interactive" for o in opps),
    "task_cwd_repo": all(o["repo"] == str((H / "work").resolve()) for o in opps),
    "request_text_not_stored": all(o["opportunity"]["intent"]["request"] is None for o in opps),
    "execution_completed_true_verified_null": all(o["execution_completed"] is True and o["verified_success"] is None
                                                  for o in outs),
    "session_id_from_omp_session_manager": all(not str(o["session_id"]).startswith("omp-") for o in opps),
    "no_drops": not drops,
    "bridge_jsonl_rows": len(bridge) == RUNS,
    "bridge_jsonl_no_prompt_field": all("prompt" not in r for r in bridge),
    "no_marker_text_anywhere_in_z0home": not leaks,
    "backend_unavailable_counted": counters.get("backend_unavailable") == RUNS,
    "no_shadow_answer_rows_for_dead_backend": not (Z / "shadow/cognition-shadow.jsonl").exists(),
    # the bridge is capture-only: with only -e z0int-bridge -e local-cognition, routing never runs
    "bridge_worker_spawned": any("z0int.bridge.worker" in line for line in spawns),
    "no_routing_spawn_from_bridge": not any("z0int.automatic" in line for line in spawns),
}
print(json.dumps({"ok": all(checks.values()), "checks": checks, "counts": {
    "opportunities": len(opps), "outcomes": len(outs), "failure_kinds": sorted({f["kind"] for f in fails}),
    "failures": len(fails), "drops": len(drops), "bridge_rows": len(bridge), "counters": counters,
    "z0_python_spawns": sorted(set(spawns))},
    "gates": [o["gate"] for o in opps], "marker_leaks": leaks}, indent=2))
sys.exit(0 if all(checks.values()) else 1)
