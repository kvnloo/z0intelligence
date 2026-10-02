"""Independent, offline audit of named-provider factual baseline receipts.

Reads saved evidence only; never imports the driver or contacts a provider. A
catalog estimate is not a billing cap, and unknown response cost stays unknown.
The original task/checker digests below deliberately cannot be replaced by a
self-consistent edited freeze. This audit makes no comparative performance claim.
"""
from __future__ import annotations

import argparse
from datetime import datetime
from decimal import Decimal, InvalidOperation
import hashlib
import json
import math
from pathlib import Path
import types

HERE = Path(__file__).resolve().parent
PROMPT_SHA256 = "d6ac8bcb93d8157f5396f81b9c54a038fc38952382b7c92fb1db6bd6eadc72f6"
CONTEXT_SHA256 = "e3a286f8d6ea0c3a2091f8495c9ca3b6b2a56da9c3bc5de122782aa91d271472"
CHECKER_SHA256 = "8a1a37449c1f33a0028866b49316f6815aaa74881b35b08768748faf1698343d"
ENDPOINTS = {
    "nous": "https://inference-api.nousresearch.com/v1/chat/completions",
    "openrouter": "https://openrouter.ai/api/v1/chat/completions",
}
CATALOG_URLS = {provider: url.removesuffix("chat/completions") + "models"
                for provider, url in ENDPOINTS.items()}
FREE_CONSTRAINTS = {"allow_fallbacks": False, "max_price": {"prompt": 0, "completion": 0}}


def digest(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def _unique_object(pairs):
    obj = {}
    for key, value in pairs:
        if key in obj:
            raise ValueError("duplicate JSON key: " + key)
        obj[key] = value
    return obj


def read_json(path: Path):
    return json.loads(path.read_bytes(), object_pairs_hook=_unique_object)


def number(value) -> bool:
    return type(value) in (int, float) and math.isfinite(value) and value >= 0


def _price(value) -> Decimal:
    if type(value) not in (str, int, float):
        raise ValueError("catalog price must be an explicit number")
    try:
        result = Decimal(str(value))
    except InvalidOperation as error:
        raise ValueError("invalid catalog price") from error
    if not result.is_finite() or result < 0:
        raise ValueError("catalog price must be finite and nonnegative")
    return result


def close(left, right) -> bool:
    # Accommodate only JSON float representation noise, not material price edits.
    return number(left) and number(right) and math.isclose(left, right, rel_tol=1e-12, abs_tol=1e-15)


def audit_route(route: dict, catalogs: list[Path] | tuple[Path, ...], *,
                input_token_cap: int = 20000, max_output_tokens: int = 4096) -> dict:
    """Validate a route and independently reconstruct its raw-catalog estimate."""
    if not isinstance(route, dict) or set(route) != {
            "schema_version", "provider", "model", "endpoint", "pricing", "max_estimated_cost_usd"}:
        raise ValueError("route schema fields mismatch")
    provider, model = route["provider"], route["model"]
    if type(route["schema_version"]) is not int or route["schema_version"] != 1:
        raise ValueError("route schema version must be 1")
    if provider not in ENDPOINTS or route["endpoint"] != ENDPOINTS[provider]:
        raise ValueError("route endpoint is not allowlisted for the provider")
    if (not isinstance(model, str) or not model.strip() or model != model.strip()
            or model.lower() in {"auto", "free", "openrouter/free", "openrouter/auto"}
            or any(ch.isspace() for ch in model)):
        raise ValueError("a named fixed model is required")
    pricing = route["pricing"]
    if not isinstance(pricing, dict) or set(pricing) != {
            "prompt_usd_per_million", "completion_usd_per_million", "source_url",
            "retrieved_at", "catalog_sha256"}:
        raise ValueError("pricing schema fields mismatch")
    if pricing["source_url"] != CATALOG_URLS[provider]:
        raise ValueError("catalog source URL is not allowlisted")
    try:
        retrieved = datetime.fromisoformat(pricing["retrieved_at"].replace("Z", "+00:00"))
        if retrieved.tzinfo is None:
            raise ValueError("catalog retrieval time needs timezone")
    except (AttributeError, TypeError, ValueError) as error:
        raise ValueError("invalid catalog retrieval timestamp") from error
    prompt_rate, completion_rate = (pricing[key] for key in
                                    ("prompt_usd_per_million", "completion_usd_per_million"))
    if not number(prompt_rate) or not number(completion_rate):
        raise ValueError("route prices must be finite and nonnegative")
    budget = route["max_estimated_cost_usd"]
    if not number(budget) or budget > .02:
        raise ValueError("maximum estimated cost must be at most USD 0.02")
    if provider == "openrouter" and (prompt_rate != 0 or completion_rate != 0 or budget != 0):
        raise ValueError("OpenRouter requalification permits only catalog-zero routes")
    if provider == "nous" and (prompt_rate > .20 or completion_rate > .50):
        raise ValueError("Nous route exceeds the bounded price policy")
    if type(input_token_cap) is not int or input_token_cap != 20000:
        raise ValueError("input token cap must remain 20000")
    if type(max_output_tokens) is not int or not 1 <= max_output_tokens <= 4096:
        raise ValueError("output token cap must be bounded by 4096")
    matches = [Path(path) for path in dict.fromkeys(catalogs)
               if digest(Path(path).read_bytes()) == pricing["catalog_sha256"]]
    if not matches:
        raise ValueError("no raw catalog matches the frozen catalog digest")
    catalog = read_json(matches[0])
    rows = [row for row in catalog.get("data", []) if isinstance(row, dict) and row.get("id") == model]
    if len(rows) != 1:
        raise ValueError("catalog must contain exactly one row for the fixed model")
    row = rows[0]
    raw_pricing = row.get("pricing")
    if not isinstance(raw_pricing, dict) or not {"prompt", "completion"} <= raw_pricing.keys():
        raise ValueError("catalog row lacks explicit prompt/completion prices")
    raw_prompt, raw_completion = (_price(raw_pricing[key]) for key in ("prompt", "completion"))
    quoted_prompt, quoted_completion = float(raw_prompt * 1000000), float(raw_completion * 1000000)
    if not close(prompt_rate, quoted_prompt) or not close(completion_rate, quoted_completion):
        raise ValueError("route prices disagree with the raw catalog model row")
    estimate = float(raw_prompt * input_token_cap + raw_completion * max_output_tokens)
    if estimate > budget and not close(estimate, budget):
        raise ValueError("catalog estimate exceeds the route's estimate policy")
    return {"provider": provider, "model": model, "endpoint": route["endpoint"],
            "route_sha256": digest(canonical(route)), "catalog_sha256": pricing["catalog_sha256"],
            "catalog_row_sha256": digest(canonical(row)), "catalog_model": row["id"],
            "catalog_prompt_usd_per_million": quoted_prompt,
            "catalog_completion_usd_per_million": quoted_completion,
            "estimated_cost_at_token_caps_usd": estimate,
            "cost_control": "openrouter_max_price_zero" if provider == "openrouter"
                else "fixed_model_catalog_estimate_only",
            "estimate_is_billing_cap": False}


def user_texts(body: dict) -> list[str]:
    result = []
    for message in body.get("messages", []):
        if not isinstance(message, dict) or message.get("role") != "user":
            continue
        content = message.get("content")
        if isinstance(content, str):
            result.append(content)
        elif isinstance(content, list):
            parts = [part.get("text", "") for part in content if isinstance(part, dict)
                     and part.get("type") in {"text", "input_text"} and isinstance(part.get("text"), str)]
            result.append("".join(parts))
    return result


def parse_answer(text):
    try:
        return json.loads(text), True
    except (ValueError, TypeError):
        return None, False


def replay_run(run: Path, snapshots: Path, catalogs: list[Path] | tuple[Path, ...] = ()) -> dict:
    names = ["freeze.json", "receipt.json", "physical-calls.json", "answer.json",
             "prompt.txt", "prepared/selection.json", "route.json",
             "native-request.bin", "forwarded-request.bin"]
    frozen, receipt, calls = (read_json(run / name) for name in names[:3])
    selection = read_json(run / "prepared/selection.json")
    prompt, context = (run / "prompt.txt").read_text(), selection["context"]
    route = frozen["route_spec"]
    route_audit = audit_route(route, catalogs, input_token_cap=frozen["input_token_cap"]["value"],
                              max_output_tokens=frozen["max_output_tokens"])
    checker_path = snapshots / "baseline_check.py"
    checker_bytes = checker_path.read_bytes()
    sources = [path for path in snapshots.glob("live_baseline*.py")
               if digest(path.read_bytes()) == frozen["driver_sha256"]]
    bindings = {
        "freeze_digest": digest((run / "freeze.json").read_bytes()) == receipt["freeze_sha256"],
        "frozen_original_prompt": digest((run / "prompt.txt").read_bytes()) == frozen["prompt_sha256"] == PROMPT_SHA256,
        "frozen_original_context": digest(context.encode()) == selection["context_sha256"] == frozen["context_sha256"] == CONTEXT_SHA256,
        "frozen_original_checker": digest(checker_bytes) == frozen["checker_sha256"] == CHECKER_SHA256,
        "exact_driver_bytes": bool(sources),
        "route_snapshot": read_json(run / "route.json") == route,
        "route_digest": route_audit["route_sha256"] == frozen["route_sha256"] == receipt["route_sha256"],
    }
    if not bindings["frozen_original_checker"]:
        raise ValueError("original checker source mismatch; refusing to execute it")
    checker = types.ModuleType("independent_provider_baseline_checker")
    exec(compile(checker_bytes, str(checker_path), "exec"), checker.__dict__)
    worker_path = run / "worker-result.json"
    worker = read_json(worker_path) if worker_path.exists() else {}
    if worker_path.exists():
        names.append("worker-result.json")
    answer, parsed = parse_answer(worker.get("result", {}).get("final_response"))
    verdict = checker.grade(answer)
    attempts = [row for row in calls if row.get("kind") == "inference"]
    if len(attempts) != 1 or sum(row.get("upstream_sent") is True for row in calls) != 1:
        raise ValueError("exactly one physical upstream inference is required")
    call = attempts[0]
    native, forwarded = call["native_request"], call["forwarded_request"]
    raw_native, raw_forwarded = ((run / name).read_bytes() for name in ("native-request.bin", "forwarded-request.bin"))
    bindings.update({
        "native_wire_bytes": digest(raw_native) == call["native_wire_sha256"] and len(raw_native) == call["native_wire_bytes"]
            and json.loads(raw_native, object_pairs_hook=_unique_object) == native,
        "forwarded_wire_bytes": digest(raw_forwarded) == call["forwarded_wire_sha256"] and len(raw_forwarded) == call["forwarded_wire_bytes"]
            and json.loads(raw_forwarded, object_pairs_hook=_unique_object) == forwarded,
    })
    provider = route["provider"]
    expected_forwarded = dict(native)
    if provider == "openrouter":
        expected_forwarded.update(provider=FREE_CONSTRAINTS, usage={"include": True})
    response = call.get("response") or {}
    choices = response.get("choices") or [{}]
    choice = choices[0]
    response_answer, response_parsed = parse_answer((choice.get("message") or {}).get("content"))
    response_verdict = checker.grade(response_answer)
    usage = response.get("usage")
    cost = usage.get("cost") if isinstance(usage, dict) else None
    input_tokens = usage.get("prompt_tokens") if isinstance(usage, dict) else None
    output_tokens = usage.get("completion_tokens") if isinstance(usage, dict) else None
    total_tokens = usage.get("total_tokens") if isinstance(usage, dict) else None
    zero_cost = cost == 0 if number(cost) else None
    cost_within_policy = number(cost) and cost <= route["max_estimated_cost_usd"]
    exact_context = sum(text.count(context) for text in user_texts(forwarded)) == 1
    tokens_in_bounds = (type(input_tokens) is int and 0 <= input_tokens <= 20000
                       and type(output_tokens) is int and 0 <= output_tokens <= frozen["max_output_tokens"])
    eligible = exact_context and tokens_in_bounds and cost_within_policy
    expected_identity_source = "response" if response.get("provider") else "configured_endpoint_only"
    response_format = frozen.get("response_format_requested")
    checks = {
        "original_task_scope": frozen.get("cohort") == "one_inspected_development_claim" and frozen.get("case_metadata") is None,
        "protected_checker": frozen.get("protected_checker_visible_to_worker_model") is False,
        "fixed_requalification_output_cap": frozen["max_output_tokens"] == receipt["configured_max_output_tokens"] == 4096,
        "fixed_request_byte_cap": frozen["max_serialized_request_bytes"] == 20000 and len(raw_native) <= 20000 and len(raw_forwarded) <= 20000,
        "route_and_endpoint": call.get("method") == "POST" and call.get("endpoint") == frozen["endpoint"] == route["endpoint"]
            and native.get("model") == forwarded.get("model") == frozen["model"] == route["model"],
        "provider_specific_forwarding": forwarded == expected_forwarded
            and (provider != "nous" or not any(key in native for key in ("provider", "usage"))),
        "explicit_response_format": response_format in (None, {"type": "json_object"})
            and (native.get("response_format") == response_format if response_format is not None
                 else "response_format" not in native),
        "no_fallbacks_tools_or_streaming": not any(body.get(key) for body in (native, forwarded)
            for key in ("models", "route", "tools", "stream", "fallback_model", "fallback_models")),
        "exact_original_prompt_and_context_on_wire": user_texts(native) == user_texts(forwarded) == [prompt + "\n\n" + context],
        "exact_context_witness": exact_context and receipt["exact_selected_context_in_forwarded_request"] is True,
        "declared_output_cap": type(forwarded.get("max_tokens")) is int and 1 <= forwarded["max_tokens"] <= frozen["max_output_tokens"],
        "one_physical_post": call.get("physical_attempt") == 1 and call.get("upstream_sent") is True
            and receipt["provider_calls"] == frozen["max_physical_inference_attempts"] == 1,
        "no_repeated_local_inference": not any(row.get("kind") == "blocked_inference" for row in calls),
        "local_metadata_count": sum(row.get("kind") == "local_metadata_non_inference" for row in calls) == receipt["local_metadata_calls"],
        "no_sampler_or_comparison_claim": frozen.get("sampler_enabled") is False and receipt.get("sampler_enabled") is False
            and receipt.get("speedup_or_noninferiority_claim") is False,
        "catalog_estimate": close(frozen["estimated_cost_at_token_caps_usd"], route_audit["estimated_cost_at_token_caps_usd"])
            and close(receipt["estimated_cost_at_token_caps_usd"], route_audit["estimated_cost_at_token_caps_usd"]),
        "estimate_not_billing_cap": frozen.get("cost_control") == route_audit["cost_control"]
            and "max_paid_cost_usd" in frozen
            and frozen["max_paid_cost_usd"] == (0 if provider == "openrouter" else None)
            and frozen.get("catalog_estimate_is_billing_cap") is False,
        "answer_parse": parsed == receipt["answer_parsed"],
        "saved_answer": answer == read_json(run / "answer.json"),
        "independent_outcome": verdict == receipt["outcome"],
        "provider_output_binding": response_parsed == parsed and response_answer == answer and response_verdict == verdict,
        "served_identity": response.get("model") == receipt.get("served_model") == call.get("served_model")
            and response.get("provider") == receipt.get("served_provider") == call.get("served_provider"),
        "provider_identity_provenance": receipt.get("requested_provider") == provider
            and receipt.get("requested_model") == route["model"]
            and receipt.get("provider_identity_source") == expected_identity_source,
        "usage": usage == call.get("usage") == receipt.get("usage"),
        "cost": cost == receipt.get("reported_cost") and zero_cost == receipt.get("reported_zero_cost"),
        "usage_sum_when_known": total_tokens is None or (type(total_tokens) is int and type(input_tokens) is int
            and type(output_tokens) is int and total_tokens == input_tokens + output_tokens),
        "resource_eligibility": receipt.get("resource_comparison_eligible") is eligible,
    }
    issues = []
    if not exact_context:
        issues.append("INVALID_CONTEXT_ADMISSION")
    if call.get("status_code") == 429:
        issues.append("RATE_LIMITED_RESOURCE_ADEQUACY_UNRESOLVED")
    if choice.get("finish_reason") == "length":
        issues.append("OUTPUT_BUDGET_EXHAUSTED")
    if usage is None:
        issues.append("USAGE_AND_COST_UNKNOWN")
    elif not number(cost):
        issues.append("REPORTED_COST_UNKNOWN_OR_INVALID")
    elif not cost_within_policy:
        issues.append("REPORTED_COST_OUTSIDE_POLICY")
    if not verdict["verified_success"]:
        issues.append("NO_VERIFIED_ANSWER")
    matches = all(bindings.values()) and all(checks.values())
    if not matches:
        issues.append("EVIDENCE_AUDIT_FAILED")
    return {"run": run.name, "input_sha256": {name: digest((run / name).read_bytes()) for name in names},
            "source_bindings": bindings, "comparisons": checks, "route_audit": route_audit,
            "replay_matches_saved_evidence": matches,
            "exact_driver_snapshot": sources[0].name if sources else None,
            "verified_success": verdict["verified_success"], "independent_outcome": verdict,
            "exact_context": exact_context, "requested_provider": provider,
            "served_model": response.get("model"), "served_provider": response.get("provider"),
            "provider_identity_source": expected_identity_source,
            "response_format_requested": response_format,
            "upstream_reached_evidence": "observed_http_response" if type(call.get("status_code")) is int
                and 100 <= call["status_code"] <= 599 else "recorded_attempt_without_http_response",
            "http_status": call.get("status_code"), "finish_reason": choice.get("finish_reason"),
            "reported_usage": usage, "reported_cost": cost, "reported_zero_cost": zero_cost,
            "reported_cost_known_within_policy": cost_within_policy,
            "resource_comparison_eligible_recomputed": eligible,
            "disposition": issues, "provider_calls_during_replay": 0,
            "new_speedup_or_cap_causality_claim": False}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", type=Path, action="append", required=True)
    parser.add_argument("--snapshots", type=Path, required=True)
    parser.add_argument("--catalog", type=Path, action="append", required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    runs = [replay_run(path, args.snapshots, args.catalog) for path in args.run]
    report = {"runs": runs, "provider_calls": 0,
              "note": "Offline evidence audit. Catalog estimates are not billing caps. No speedup, noninferiority, context-selection or output-cap causal claim."}
    with args.out.open("x") as stream:
        json.dump(report, stream, indent=2, sort_keys=True, allow_nan=False)
        stream.write("\n")
    matches = all(run["replay_matches_saved_evidence"] for run in runs)
    print(json.dumps({"all_replays_match": matches, "provider_calls": 0,
                      "runs": [{key: run[key] for key in ("run", "verified_success", "reported_cost", "disposition")} for run in runs]}))
    return 0 if matches else 1


if __name__ == "__main__":
    raise SystemExit(main())
