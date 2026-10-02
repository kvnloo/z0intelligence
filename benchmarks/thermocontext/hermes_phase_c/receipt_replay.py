"""Independent, read-only replay of saved native baseline receipts. No networking.

Checks captured request objects, host output, frozen checker and source bytes.
The original driver did not save raw incoming HTTP bytes: their hash cannot be
independently rechecked here. This is receipt reconstruction, not provider replay.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import math
from pathlib import Path

HERE = Path(__file__).resolve().parent


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def occurrences(context: str, body: dict) -> int:
    """Independent implementation; do not trust the producer's witness boolean."""
    count = 0
    for message in body.get("messages", []):
        if message.get("role") != "user":
            continue
        content = message.get("content")
        if isinstance(content, str):
            count += content.count(context)
        elif isinstance(content, list):
            count += sum(part.get("text", "").count(context) for part in content
                         if isinstance(part, dict) and part.get("type") in {"text", "input_text"}
                         and isinstance(part.get("text"), str))
    return count


def replay_run(run: Path, snapshots: Path) -> dict:
    names = ["freeze.json", "receipt.json", "physical-calls.json", "worker-result.json",
             "answer.json", "prompt.txt", "prepared/selection.json"]
    docs = {name: json.loads((run / name).read_text()) for name in names if name.endswith(".json")}
    frozen, original, calls = docs["freeze.json"], docs["receipt.json"], docs["physical-calls.json"]
    selection, worker = docs["prepared/selection.json"], docs["worker-result.json"]
    context = selection["context"]
    bindings = {
        "freeze_digest": sha(run / "freeze.json") == original["freeze_sha256"],
        "prompt_digest": sha(run / "prompt.txt") == frozen["prompt_sha256"],
        "context_digest": hashlib.sha256(context.encode()).hexdigest() == selection["context_sha256"] == frozen["context_sha256"],
        "checker_source": sha(snapshots / "baseline_check.py") == frozen["checker_sha256"],
    }
    sources = [path for path in snapshots.glob("live_baseline_*.py") if sha(path) == frozen["driver_sha256"]]
    bindings["exact_driver_bytes"] = len(sources) == 1
    if not bindings["checker_source"]:
        raise ValueError("frozen checker source mismatch; refusing to execute it")
    spec = importlib.util.spec_from_file_location("frozen_independent_baseline_checker", snapshots / "baseline_check.py")
    checker = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(checker)
    try:
        answer = json.loads(worker["result"]["final_response"])
        parsed = True
    except (KeyError, TypeError, ValueError):
        answer, parsed = None, False
    verdict = checker.grade(answer)
    attempts = [row for row in calls if row.get("kind") == "inference"]
    if len(attempts) != 1:
        raise ValueError("this frozen study requires exactly one attempted upstream inference")
    call = attempts[0]
    native, forwarded = call["native_request"], call["forwarded_request"]
    expected_forwarded = {**native, "provider": {"allow_fallbacks": False, "max_price": {"prompt": 0, "completion": 0}},
                          "usage": {"include": True}}
    response = call.get("response") or {}
    usage = response.get("usage")
    cost = usage.get("cost") if isinstance(usage, dict) else None
    input_tokens = usage.get("prompt_tokens") if isinstance(usage, dict) else None
    output_tokens = usage.get("completion_tokens") if isinstance(usage, dict) else None
    total_tokens = usage.get("total_tokens") if isinstance(usage, dict) else None
    numeric_cost = type(cost) in (int, float) and math.isfinite(cost)
    zero_cost = cost == 0 if numeric_cost else None
    exact = occurrences(context, forwarded) == 1
    accounting_eligible = (type(input_tokens) is int and 0 <= input_tokens <= 20000
                           and type(output_tokens) is int and 0 <= output_tokens <= frozen["max_output_tokens"]
                           and numeric_cost and cost == 0)
    comparisons = {
        "answer_parse": parsed == original["answer_parsed"],
        "saved_answer": answer == docs["answer.json"],
        "independent_outcome": verdict == original["outcome"],
        "exact_context_witness": exact == original["exact_selected_context_in_forwarded_request"],
        "provider_attempt_count": len(attempts) == original["provider_calls"] == frozen["max_physical_inference_attempts"],
        "local_metadata_count": sum(row.get("kind") == "local_metadata_non_inference" for row in calls) == original["local_metadata_calls"],
        "route_and_constraints": forwarded == expected_forwarded and forwarded.get("model") == frozen["model"] == "openrouter/free",
        "no_tools": not native.get("tools") and not forwarded.get("tools"),
        "declared_output_cap": type(forwarded.get("max_tokens")) is int and 1 <= forwarded["max_tokens"] <= frozen["max_output_tokens"],
        "recorded_request_byte_cap": call["native_wire_bytes"] <= frozen["max_serialized_request_bytes"],
        "served_identity": response.get("model") == original["served_model"] and response.get("provider") == original["served_provider"],
        "usage": usage == call.get("usage") == original["usage"],
        "cost": cost == original["reported_cost"] and zero_cost == original["reported_zero_cost"],
        "usage_sum": total_tokens == input_tokens + output_tokens if type(input_tokens) is int and type(output_tokens) is int else total_tokens is None,
    }
    issues = []
    if not exact:
        issues.append("INVALID_CONTEXT_ADMISSION")
    if call.get("status_code") == 429:
        issues.append("RATE_LIMITED_RESOURCE_ADEQUACY_UNRESOLVED")
    choices = response.get("choices") or [{}]
    finish = choices[0].get("finish_reason")
    if finish == "length":
        issues.append("OUTPUT_BUDGET_EXHAUSTED")
    if usage is None:
        issues.append("USAGE_AND_COST_UNKNOWN")
    if not verdict["verified_success"]:
        issues.append("NO_VERIFIED_ANSWER")
    semantic_eligible = exact and accounting_eligible
    historical_discrepancy = original["resource_comparison_eligible"] != semantic_eligible
    return {"run": run.name, "input_sha256": {name: sha(run / name) for name in names},
            "source_bindings": bindings, "comparisons": comparisons,
            "replay_matches_saved_evidence": all(bindings.values()) and all(comparisons.values()),
            "exact_driver_snapshot": sources[0].name if sources else None,
            "native_wire_bytes_binding": "UNKNOWN_RAW_BYTES_NOT_SAVED; captured parsed request object checked",
            "verified_success": verdict["verified_success"], "exact_context": exact,
            "served_model": response.get("model"), "served_provider": response.get("provider"),
            "http_status": call.get("status_code"), "finish_reason": finish,
            "reported_usage": usage, "reported_zero_cost": zero_cost,
            "resource_comparison_eligible_recomputed": semantic_eligible,
            "historical_eligibility_discrepancy_preserved": historical_discrepancy,
            "disposition": issues, "provider_calls_during_replay": 0,
            "new_speedup_or_cap_causality_claim": False}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", type=Path, action="append", required=True)
    parser.add_argument("--snapshots", type=Path, default=HERE / "results/source-snapshots")
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    report = {"runs": [replay_run(path, args.snapshots) for path in args.run],
              "provider_calls": 0, "note": "Original receipts unchanged. Free-router model/provider changes prohibit attributing differences to output cap."}
    with args.out.open("x") as stream:
        json.dump(report, stream, indent=2, sort_keys=True)
        stream.write("\n")
    print(json.dumps({"all_replays_match": all(r["replay_matches_saved_evidence"] for r in report["runs"]),
                      "runs": [{k: r[k] for k in ("run", "verified_success", "exact_context", "disposition")} for r in report["runs"]]}, sort_keys=True))
