#!/usr/bin/env python3
"""Independent, offline acceptance of one bounded AODL public canary.

Uses only Python's standard library. Never imports the runtime, contacts a
provider, reads credentials, or promotes an old study. A PASS is consistency of
recorded evidence plus the fixed literal predicate, not provider authentication.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path
from typing import Any

STUDY = "aodl-nous-solar-completion-v1"
ENDPOINT = "https://inference-api.nousresearch.com/v1/chat/completions"
MODEL = "upstage/solar-mini4"
ROOT_TRACE = "remote-public-proof"
EXPECTED_OUTPUT = "CANONICAL_OK"
VERIFIER = "exact_string_CANONICAL_OK"
TASK = "Public synthetic diagnostic. Return exactly CANONICAL_OK and nothing else."
CONTEXT = "Public synthetic diagnostic only."
SYSTEM = "Complete the bounded task using supplied context. Return your answer to the Codex parent. You have no filesystem, shell, or external tools."
MESSAGES = [
    {"role": "system", "content": SYSTEM},
    {"role": "user", "content": TASK + "\n\nContext supplied by parent:\n" + CONTEXT},
]
MAX_TOKENS = 128
MAX_COST_USD = 0.001
# contracts/aodl/governed-worker-v1.json at z0int 28db1eecbe9fa9921c52945e15d4894b6dddae3d.
# Fingerprint independently recomputed with pinned AODL 68231658f0ec0338464c0916a2329b9587444312.
DOCUMENT_SHA256 = "d572cee8a2573ed1c12f8e7a19b807578c9fe9dc676c807c37c47d45251df6ff"
INTENT_SOURCE_HASH = "fb7c01ddf8b82e81d7003a259f508067abe96577c77302d1ce8b29c82ef0bd24"
SEMANTIC_FINGERPRINT = "aodl-canon-1:0275717f01a651a63942cab5826f69e5aefad4532a550b4552b591b3408b1177"
SUMMARY_TRUE = (
    "parent_only", "unavailable_native", "host_governed_endpoint",
    "remote_executor_http", "omp_registered_tool_execution",
    "one_remote_physical_call", "restart_replay", "conflict_rejected",
    "uncertain_refused", "cap_refused", "authority_owned",
    "governance_snapshot_prompt_free", "synthetic_verified_outcome",
)
JSON_FILES = {
    "request": "public-request.json", "response": "public-response.json",
    "summary": "summary.json", "omp": "omp-governed.json",
    "replay": "replay.json", "capped": "capped.json",
}


class CheckError(ValueError):
    """Evidence is missing, malformed, inconsistent or outside this protocol."""


def require(condition: bool, reason: str) -> None:
    if not condition:
        raise CheckError(reason)


def obj(value: Any, label: str) -> dict:
    require(isinstance(value, dict), label + ": object required")
    return value


def nonnegative_int(value: Any, label: str) -> int:
    require(type(value) is int and value >= 0, label + ": exact nonnegative integer required")
    return value


def finite_number(value: Any, label: str) -> int | float:
    require(type(value) in (int, float), label + ": finite nonnegative number required")
    try:
        valid = math.isfinite(value) and value >= 0
    except OverflowError:
        valid = False
    require(valid, label + ": finite nonnegative number required")
    return value


def cost(value: Any, label: str) -> int | float:
    number = finite_number(value, label)
    require(number <= MAX_COST_USD, label + ": exceeds study cost bound")
    return number


def exact_output(value: Any) -> bool:
    # Preserve the original canary predicate; hash the unstripped bytes separately.
    return isinstance(value, str) and value.strip() == EXPECTED_OUTPUT


def canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def immutable_receipt(row: dict) -> str:
    # join_outcome appends a copy carrying only these additional outcome fields.
    return canonical({k: v for k, v in row.items() if k not in {"outcome", "outcome_tier", "outcome_ts"}})


def positive_outcome(row: dict, physical_id: str) -> None:
    require(row.get("trace_id") == physical_id, "gold outcome must join the physical receipt")
    require(row.get("outcome_tier") == "gold", "gold outcome tier required")
    outcome = obj(row.get("outcome"), "gold outcome")
    require(outcome.get("verified_success") is True, "gold verified_success must be true")
    require(outcome.get("verified") is True, "gold verified must be true")
    require(outcome.get("verification_source") == VERIFIER, "exact verifier identity required")
    for field in ("success", "test_pass", "task_done", "verifier_ok"):
        require(outcome.get(field) is not False, "contradictory negative outcome: " + field)
    for field in ("user_correction", "reverted", "ci_failed"):
        require(outcome.get(field) is not True, "contradictory negative outcome: " + field)


def extra(row: dict) -> dict:
    return obj(row.get("extra"), "receipt.extra")


def exactly_one(rows: list, label: str) -> dict:
    require(len(rows) == 1, label + ": exactly one required")
    return rows[0]


def validate_artifacts(data: dict, *, require_live: bool = False) -> dict:
    """Check already loaded artifacts; raise CheckError without changing them."""
    request = obj(data.get("request"), "public request")
    require(set(request) == {"endpoint", "payload"}, "public request keys changed")
    require(request["endpoint"] == ENDPOINT, "endpoint differs from frozen study")
    payload = obj(request["payload"], "request payload")
    require(set(payload) == {"model", "messages", "max_tokens", "temperature"}, "request payload keys changed")
    require(payload["model"] == MODEL, "requested model differs")
    require(nonnegative_int(payload["max_tokens"], "max_tokens") == MAX_TOKENS, "completion cap differs")
    require(finite_number(payload["temperature"], "temperature") == 0, "temperature differs")
    require(payload["messages"] == MESSAGES, "task/context/system messages differ")
    prompt_bytes = len(json.dumps(MESSAGES, ensure_ascii=False, separators=(",", ":")).encode())
    require(prompt_bytes <= 2048, "prompt byte bound exceeded")

    response = obj(data.get("response"), "public response")
    require(response.get("model") == MODEL, "response model differs")
    response_id = response.get("id")
    require(isinstance(response_id, str) and bool(response_id), "response id missing")
    choices = response.get("choices")
    require(isinstance(choices, list) and len(choices) == 1, "one response choice required")
    choice = obj(choices[0], "response choice")
    require(choice.get("finish_reason") == "stop", "response did not stop normally")
    if "index" in choice:
        require(nonnegative_int(choice["index"], "choice index") == 0, "response choice index differs")
    message = obj(choice.get("message"), "response message")
    require(message.get("role") == "assistant", "response role must be assistant")
    require(message.get("tool_calls") is None or message.get("tool_calls") == [], "tool calls forbidden")
    require(message.get("function_call") is None, "legacy function call forbidden")
    require(message.get("refusal") in (None, ""), "response refusal forbidden")
    output = message.get("content")
    require(exact_output(output), "exact output predicate failed")
    output_hash = hashlib.sha256(output.encode("utf-8")).hexdigest()
    usage = obj(response.get("usage"), "response usage")
    prompt = nonnegative_int(usage.get("prompt_tokens"), "response prompt_tokens")
    completion = nonnegative_int(usage.get("completion_tokens"), "response completion_tokens")
    require(completion <= MAX_TOKENS, "reported completion exceeds cap")
    if "total_tokens" in usage:
        require(nonnegative_int(usage["total_tokens"], "response total_tokens") == prompt + completion, "total token accounting differs")
    reported_cost = cost(usage.get("cost"), "response cost")

    rows = data.get("receipts")
    require(isinstance(rows, list) and bool(rows), "decision receipts missing")
    grouped: dict[str, list[dict]] = {}
    positions: dict[int, int] = {}
    for index, row in enumerate(rows):
        obj(row, "decision receipt")
        require(row.get("schema") == "z0int.decision_receipt.v1", "noncanonical decision receipt schema")
        trace = row.get("trace_id")
        require(isinstance(trace, str) and bool(trace), "receipt trace id missing")
        extra(row)
        grouped.setdefault(trace, []).append(row)
        positions[id(row)] = index
    latest = [group[-1] for group in grouped.values()]

    def stage(capability: str, trace: str = ROOT_TRACE) -> dict:
        return exactly_one([r for r in latest if r.get("capability_id") == capability and extra(r).get("caller_trace_id") == trace], capability + "/" + trace)

    preparation = stage("aodl.governed_request")
    admission = stage("aodl.structural_admission")
    dispatch = stage("intelligence.dispatch")
    frozen = obj(extra(preparation).get("frozen_governance"), "frozen governance")
    require(frozen.get("provider") == "nous" and frozen.get("model") == MODEL and frozen.get("free_only") is False, "frozen provider/model/cost authority differs")
    require(frozen.get("reason") == "host-governed AODL remote worker canary", "frozen governance reason differs")
    envelope = obj(frozen.get("aodl"), "frozen AODL envelope")
    document = obj(envelope.get("document"), "frozen AODL document")
    require(hashlib.sha256(canonical(document).encode()).hexdigest() == DOCUMENT_SHA256, "frozen intent document differs from pinned contract")
    expected_spawn = {"request_revision": 1, "parent_node_id": "parent", "live_children": 0,
                      "parent_depth": 0, "observed": {"tokens": 0},
                      "proposed": {"tokens": math.ceil(prompt_bytes / 4) + MAX_TOKENS}, "requested": ["execute"]}
    require(canonical(envelope.get("spawn")) == canonical(expected_spawn), "frozen spawn authority or bounds differ")
    require(extra(preparation).get("aodl_intent_source_hash") == INTENT_SOURCE_HASH, "preparation intent lineage differs")
    admission_id, dispatch_id = admission["trace_id"], dispatch["trace_id"]
    decision = obj(extra(admission).get("aodl_admission"), "AODL admission")
    require(decision.get("allowed") is True and decision.get("decision") == "ALLOW", "AODL admission not positive")
    require(decision.get("codes") == [] and decision.get("numeric_codes") == [], "AODL admission has denial codes")
    require(decision.get("aodl_canon_version") == "aodl-canon-1", "AODL canonical version differs")
    fingerprint = decision.get("aodl_semantic_fingerprint")
    lineage = decision.get("aodl_intent_source_hash")
    require(fingerprint == SEMANTIC_FINGERPRINT, "AODL semantic fingerprint differs from pinned contract")
    require(lineage == INTENT_SOURCE_HASH, "AODL intent lineage differs from pinned contract")
    require(len(grouped[admission_id]) == 1, "duplicate structural admission")
    require(extra(dispatch).get("aodl_admission_receipt_id") == admission_id, "dispatch/admission link differs")
    dispatch_history = grouped[dispatch_id]
    require([extra(r).get("status") for r in dispatch_history] == ["started", "completed"], "dispatch lifecycle differs")
    for row in dispatch_history:
        for field in ("authority_owner_sha256", "request_sha256", "aodl_admission_receipt_id", "caller_trace_id", "harness"):
            require(extra(row).get(field) == extra(dispatch).get(field), "dispatch identity changed: " + field)
        require(row.get("session_id") == dispatch.get("session_id"), "dispatch session changed")
    require(extra(admission).get("request_sha256") == extra(dispatch).get("request_sha256"), "admission/dispatch request fingerprint differs")

    live_rows = [r for r in rows if r.get("execution") == "live"]
    for row in live_rows:
        require(type(extra(row).get("physical_call_attempted")) is bool, "physical attempt flag must be boolean")
    attempted_ids = {r["trace_id"] for r in live_rows if extra(r)["physical_call_attempted"] is True}
    require(len(attempted_ids) == 1, "exactly one physical call required")
    physical_id = next(iter(attempted_ids))
    history = grouped[physical_id]
    require(all(r.get("execution") == "live" for r in history), "physical receipt kind changed")
    physical = history[-1]
    terminal = history[1] if len(history) >= 2 else {}
    require(len(history) in (2, 3), "unexpected physical receipt history")
    require(extra(history[0]).get("status") == "started", "physical start missing")
    require(extra(history[0]).get("physical_call_attempted") is False, "physical start attempt marker differs")
    for index, row in enumerate(history):
        e = extra(row)
        require(type(e.get("authority_event_index")) is int and e["authority_event_index"] == (0 if index == 0 else 1), "authority event index differs")
        require(row.get("provider") == "nous" and row.get("model") == MODEL, "physical provider/model differs")
        require(e.get("authority_dispatch_id") == dispatch_id and e.get("caller_trace_id") == ROOT_TRACE and e.get("harness") == "omp", "physical dispatch/root/harness link differs")
        if index:
            require(e.get("status") == "completed" and e.get("physical_call_attempted") is True, "physical terminal status differs")
            require(immutable_receipt(row) == immutable_receipt(terminal), "physical receipt mutated after completion")
    e = extra(physical)
    require(obj(terminal.get("outcome"), "terminal outcome").get("execution_completed") is True, "physical execution was not completed")
    require(e.get("response_id") == response_id and e.get("response_model") == MODEL, "receipt/response identity differs")
    require(e.get("output_sha256") == output_hash and e.get("finish_reason") == "stop", "receipt/output hash or finish differs")
    require(nonnegative_int(physical.get("input_tokens"), "receipt input_tokens") == prompt, "receipt/input usage differs")
    require(nonnegative_int(physical.get("output_tokens"), "receipt output_tokens") == completion, "receipt/output usage differs")
    require(cost(e.get("provider_reported_cost_usd"), "receipt cost") == reported_cost, "receipt/response cost differs")
    require(e.get("usage_source") == "provider_response", "receipt usage source differs")
    require(e.get("task_sha256") == hashlib.sha256(TASK.encode()).hexdigest(), "receipt task hash differs")
    require(e.get("context_sha256") == hashlib.sha256(CONTEXT.encode()).hexdigest(), "receipt context hash differs")
    require(e.get("study_id") == STUDY, "receipt study identity differs")
    require(e.get("free_only") is False and e.get("free_tier_validated") is False, "paid bounded study mislabeled free")
    for flag in ("study_cost_known", "study_cost_within_bound", "study_model_identity_matches"):
        require(e.get(flag) is True, "receipt study constraint failed: " + flag)

    permit_id = e.get("admission_token")
    require(all(extra(row).get("admission_token") == permit_id for row in history), "physical permit changed")
    require(isinstance(permit_id, str) and permit_id in grouped, "physical provider admission missing")
    permits = grouped[permit_id]
    require([extra(r).get("status") for r in permits] == ["acquired", "released"], "provider permit lifecycle differs")
    for row in permits:
        p = extra(row)
        require(row.get("capability_id") == "intelligence.provider_admission" and row.get("provider") == "nous", "provider permit identity differs")
        require(p.get("call_id") == physical_id and p.get("permit_dispatch_id") == dispatch_id, "provider permit links differ")
        owner = extra(dispatch).get("authority_owner_sha256")
        require(isinstance(owner, str) and len(owner) == 64 and p.get("permit_owner_sha256") == owner, "provider permit owner differs")
    released = extra(permits[-1])
    require(released.get("physical_call_attempted") is True and type(released.get("http_status")) is int and released["http_status"] == 200, "provider permit release not successful")
    ordered = [preparation, admission, dispatch_history[0], permits[0], history[0], permits[-1], terminal, dispatch]
    require([positions[id(r)] for r in ordered] == sorted(positions[id(r)] for r in ordered), "admission/execution evidence order differs")

    result = obj(extra(dispatch).get("result"), "dispatch result")
    require(result.get("ok") is True and result.get("trace_id") == ROOT_TRACE, "dispatch result/root not successful")
    require(result.get("output") == output, "dispatch output differs from raw response")
    require(result.get("aodl_admission_receipt_id") == admission_id, "result admission link differs")
    attempts = result.get("attempts")
    require(isinstance(attempts, list) and len(attempts) == 1 and canonical(obj(attempts[0], "result attempt")) == canonical(terminal), "dispatch result receipt mismatch")

    outcomes = data.get("outcomes")
    require(isinstance(outcomes, list), "outcomes missing")
    matching = [obj(r, "outcome row") for r in outcomes if isinstance(r, dict) and r.get("trace_id") == physical_id]
    joined = exactly_one(matching, "physical gold outcome")
    positive_outcome(joined, physical_id)
    require(canonical(obj(joined.get("receipt"), "joined physical receipt")) == canonical(terminal), "gold outcome receipt mismatch")
    positive_outcome(physical, physical_id)

    events = data.get("tokenomics")
    require(isinstance(events, list), "Tokenomics events missing")
    gate = exactly_one([r for r in events if isinstance(r, dict) and r.get("schema") == "z0int.aodl_gate_latency.v1" and r.get("trace_id") == admission_id], "root gate latency")
    require(gate.get("allowed") is True and gate.get("caller_trace_id") == ROOT_TRACE, "gate latency/admission mismatch")
    finite_number(gate.get("latency_ms"), "gate latency")
    require(gate.get("task_success") is None and gate.get("verified_success") is None, "structural gate minted success")
    require(gate.get("aodl_semantic_fingerprint") == fingerprint and gate.get("aodl_intent_source_hash") == lineage, "gate fingerprint/lineage mismatch")

    summary = obj(data.get("summary"), "summary")
    require(summary.get("study_id") == STUDY, "summary study identity differs")
    require(type(summary.get("authority_protocol_version")) is int and summary["authority_protocol_version"] == 3, "authority protocol differs")
    require(summary.get("aodl_canon_version") == "aodl-canon-1", "summary canonical version differs")
    for flag in SUMMARY_TRUE:
        require(summary.get(flag) is True, "summary control failed: " + flag)
    require(summary.get("parent_model_called") is False, "parent model was called")
    require(summary.get("task_b_import_allowed") is False and summary.get("frozen_task_a_status") == "FAIL_FROZEN_COMPLETION_CAP", "new study must not promote frozen study")
    require(summary.get("synthetic_verifier") == VERIFIER, "summary verifier differs")
    require(summary.get("same_root_trace") == ROOT_TRACE and summary.get("physical_receipt") == physical_id and summary.get("aodl_admission_receipt") == admission_id, "summary trace links differ")

    omp = obj(data.get("omp"), "OMP result")
    require(omp.get("runtime") == "OMP ExtensionRunner.getRegisteredTool().definition.execute" and omp.get("tool") == "z0int_route_worker", "OMP tool identity differs")
    require(omp.get("root_trace_id") == ROOT_TRACE and omp.get("aodl_admission_receipt_id") == admission_id, "OMP trace links differ")
    require(omp.get("parent_model_called") is False and omp.get("governed_remote") is True and omp.get("output_verified_exact") is True and exact_output(omp.get("output")), "OMP exact result failed")
    session = omp.get("session_id")
    require(isinstance(session, str) and bool(session), "OMP session missing")
    require(all(r.get("session_id") == session for r in (preparation, admission, dispatch, physical)), "session linkage differs")
    caller = {"harness": "omp", "trace_id": ROOT_TRACE, "parent_agent": session,
              "task": TASK, "context": CONTEXT, "max_tokens": MAX_TOKENS, "allow_remote": True}
    require(extra(preparation).get("caller_request_sha256") == hashlib.sha256(canonical(caller).encode()).hexdigest(), "prepared caller fingerprint differs")
    remote = {k: v for k, v in caller.items() if k != "allow_remote"}
    remote.update(function="cheap_bounded_worker", **frozen)
    remote_digest = hashlib.sha256(json.dumps(remote, sort_keys=True, allow_nan=False).encode()).hexdigest()
    require(extra(dispatch).get("request_sha256") == remote_digest, "frozen remote request fingerprint differs")

    replay = obj(data.get("replay"), "replay")
    require(replay.get("ok") is True and replay.get("replayed") is True and replay.get("governed_remote") is True, "replay not successful")
    require(replay.get("trace_id") == ROOT_TRACE and replay.get("dispatch_receipt_id") == dispatch_id and replay.get("aodl_admission_receipt_id") == admission_id, "replay links differ")
    require(replay.get("output") == output, "replay output differs")
    require(replay.get("provider") == "nous" and replay.get("model") == MODEL, "replay provider/model differs")
    require(nonnegative_int(replay.get("input_tokens"), "replay input_tokens") == prompt and nonnegative_int(replay.get("output_tokens"), "replay output_tokens") == completion, "replay usage differs")
    require(canonical(replay.get("attempts")) == canonical(attempts), "replay physical receipt mismatch")

    capped = obj(data.get("capped"), "capped control")
    require(capped.get("ok") is False and capped.get("requires_parent") is True and capped.get("trace_id") == "capped", "cap refusal failed")
    cap_dispatch = stage("intelligence.dispatch", "capped")
    require(capped.get("dispatch_receipt_id") == cap_dispatch["trace_id"] and extra(cap_dispatch).get("status") == "completed", "cap dispatch link differs")
    cap_physical = exactly_one([r for r in latest if r.get("execution") == "live" and extra(r).get("authority_dispatch_id") == cap_dispatch["trace_id"]], "capped physical control")
    require(extra(cap_physical).get("status") == "capped" and extra(cap_physical).get("physical_call_attempted") is False, "capped control attempted execution")
    uncertain_dispatch = stage("intelligence.dispatch", "uncertain")
    require(extra(uncertain_dispatch).get("status") == "started" and len(grouped[uncertain_dispatch["trace_id"]]) == 1, "uncertain dispatch was taken over")
    require(not any(extra(r).get("authority_dispatch_id") == uncertain_dispatch["trace_id"] for r in rows), "uncertain dispatch executed")
    if data.get("uncertain") is not None:
        uncertain = obj(data["uncertain"], "uncertain control")
        require(uncertain.get("ok") is False and uncertain.get("executed") is False and uncertain.get("requires_parent") is True and uncertain.get("execution_status") == "uncertain" and uncertain.get("trace_id") == "uncertain", "uncertain response changed")

    offline = data.get("offline_marker") is not None or response_id.lower().startswith("offline")
    require(not require_live or not offline, "offline fixture cannot be promoted to live evidence")
    stages = {
        "governed_preparation": preparation["trace_id"], "aodl_admission": admission_id,
        "dispatch": dispatch_id, "provider_admission": permit_id,
        "physical_execution": physical_id, "verified_outcome": physical_id,
    }
    if data.get("golden") is not None:
        golden = obj(data["golden"], "existing collector")
        require(golden.get("root_trace_id") == ROOT_TRACE and golden.get("structural_execution_complete") is True and golden.get("verified_outcome_complete") is True, "existing collector completion differs")
        gs = obj(golden.get("stages"), "existing collector stages")
        for name, identity in stages.items():
            field = "trace_id" if name == "verified_outcome" else "receipt_id"
            require(obj(gs.get(name), "collector " + name).get(field) == identity, "existing collector stage link differs: " + name)
        positive_outcome(obj(gs.get("verified_outcome"), "collector outcome"), physical_id)
    return {
        "schema": "z0eval.independent_study_check.v1", "study_id": STUDY,
        "passed": True, "errors": [], "live_evidence": not offline,
        "evidence_mode": "offline_fixture" if offline else "captured_study_artifacts",
        "root_trace_id": ROOT_TRACE, "stages": stages, "output_sha256": output_hash,
        "input_tokens": prompt, "output_tokens": completion, "provider_reported_cost_usd": reported_cost,
        "verification_source": VERIFIER, "physical_call_count": 1,
        "task_a_pass": False, "task_b_import_allowed": False,
        "replay_restart_scope": summary.get("replay_restart_scope", "unspecified_by_producer"),
        "limitations": [
            "Checks local artifact consistency and exact public literal output; does not authenticate provider origin.",
            "Conflict rejection and OMP invocation claims include producer summary evidence; this checker makes no provider calls.",
            "Service restart evidence is limited to the producer's stated scope.",
        ],
    }


def _unique_object(pairs: list[tuple[str, Any]]) -> dict:
    value: dict = {}
    for key, item in pairs:
        require(key not in value, "duplicate JSON key: " + key)
        value[key] = item
    return value


def _invalid_number(text: str) -> Any:
    raise CheckError("non-finite JSON number: " + text)


def decode_json(text: str) -> Any:
    return json.loads(text, object_pairs_hook=_unique_object, parse_constant=_invalid_number)


def read_object(path: Path) -> dict:
    return obj(decode_json(path.read_text(encoding="utf-8")), path.name)


def read_jsonl(path: Path) -> list[dict]:
    raw = path.read_text(encoding="utf-8")
    require(not raw or raw.endswith("\n"), path.name + ": truncated JSONL")
    return [obj(decode_json(line), path.name) for line in raw.splitlines() if line.strip()]


def load_artifacts(directory: Path, state_dir: Path | None = None) -> dict:
    state = state_dir if state_dir is not None else directory / "state"
    data = {name: read_object(directory / path) for name, path in JSON_FILES.items()}
    data.update(
        receipts=read_jsonl(state / "receipts" / "decisions.jsonl"),
        outcomes=read_jsonl(state / "receipts" / "outcomes.jsonl"),
        tokenomics=read_jsonl(state / "tokenomics" / "events.jsonl"),
    )
    for name, filename in (("uncertain", "uncertain.json"), ("golden", "golden-trace.json"), ("offline_marker", "OFFLINE_ONLY.json")):
        path = directory / filename
        data[name] = read_object(path) if path.exists() else None
    return data


def check_directory(directory: Path, state_dir: Path | None = None, *, require_live: bool = False) -> dict:
    try:
        return validate_artifacts(load_artifacts(directory, state_dir), require_live=require_live)
    except (CheckError, OSError, ValueError, TypeError, KeyError) as exc:
        return {
            "schema": "z0eval.independent_study_check.v1", "study_id": STUDY,
            "passed": False, "live_evidence": False, "errors": [str(exc)],
            "task_a_pass": False, "task_b_import_allowed": False,
        }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--state-dir", type=Path)
    parser.add_argument("--require-live", action="store_true")
    parser.add_argument("--out", type=Path, help="Optional create-only JSON report path.")
    args = parser.parse_args()
    report = check_directory(args.output_dir, args.state_dir, require_live=args.require_live)
    rendered = json.dumps(report, indent=2, allow_nan=False) + "\n"
    if args.out is not None:
        with args.out.open("x", encoding="utf-8") as stream:
            stream.write(rendered)
    print(rendered, end="")
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
