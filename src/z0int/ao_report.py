"""Frozen promotion evidence for the Agent Orchestrator shadow bridge.

This report is deliberately descriptive. It never promotes a policy, chooses a
global confidence threshold, or treats missing measurements as zero.
"""
from __future__ import annotations

import hashlib
import json
import math
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from .ao_bridge import CAPABILITY, ao_events_path
from .receipt import receipts_path


def _iter_jsonl(path: Path):
    if not path.is_file():
        return
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(row, dict):
                yield row


def _latest_ao_receipts(root: Path | None = None) -> dict[str, dict[str, Any]]:
    latest: dict[str, dict[str, Any]] = {}
    for row in _iter_jsonl(receipts_path(root)) or []:
        trace_id = row.get("trace_id")
        if not isinstance(trace_id, str) or not trace_id:
            continue
        if row.get("capability_id") != CAPABILITY:
            continue
        latest[trace_id] = row
    return latest


def _ao_events(root: Path | None = None) -> list[dict[str, Any]]:
    return list(_iter_jsonl(ao_events_path(root)) or [])


def _rate(num: int, den: int) -> float | None:
    return (num / den) if den else None


def _percentile(values: list[float], q: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    idx = max(0, min(len(ordered) - 1, math.ceil(q * len(ordered)) - 1))
    return ordered[idx]


def _family(row: dict[str, Any]) -> str:
    extra = row.get("extra") if isinstance(row.get("extra"), dict) else {}
    current = extra.get("current") if isinstance(extra.get("current"), dict) else {}
    kind = str(extra.get("kind") or "unknown")
    harness = str(current.get("harness") or "unknown")
    mode = str(current.get("mode") or "unknown")
    return f"{kind}:{harness}:{mode}"


def _snapshot_digest(
    receipts: dict[str, dict[str, Any]],
    events: list[dict[str, Any]],
) -> str:
    payload = {
        "receipts": [receipts[key] for key in sorted(receipts)],
        "events": sorted(
            events,
            key=lambda row: (
                str(row.get("trace_id") or ""),
                str(row.get("outcome_id") or ""),
                str(row.get("event_sha256") or ""),
            ),
        ),
    }
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode()
    return hashlib.sha256(raw).hexdigest()


def build_ao_promotion_report(*, root: Path | None = None) -> dict[str, Any]:
    receipts = _latest_ao_receipts(root)
    events = _ao_events(root)
    traces = set(receipts)

    action_counts = Counter(str(row.get("prediction") or "unknown") for row in receipts.values())
    abstain = action_counts.get("abstain", 0)
    non_abstain = len(receipts) - abstain

    comparable = agreement = disagreement = 0
    for row in receipts.values():
        action = str(row.get("prediction") or "")
        if action == "abstain":
            continue
        extra = row.get("extra") if isinstance(row.get("extra"), dict) else {}
        current = extra.get("current")
        recommendation = extra.get("recommendation")
        if not isinstance(current, dict) or not isinstance(recommendation, dict):
            continue
        comparable += 1
        if current == recommendation:
            agreement += 1
        else:
            disagreement += 1

    latency = []
    measured_tokens = []
    actual_saved = []
    estimated_saved = []
    decision_costs = []
    decision_usage_states: Counter[str] = Counter()
    decision_measurement_scopes: Counter[str] = Counter()
    for row in receipts.values():
        value = row.get("latency_ms")
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            latency.append(float(value))
        value = row.get("measured_frontier_tokens")
        if isinstance(value, int) and not isinstance(value, bool):
            measured_tokens.append(value)
        value = row.get("actual_tokens_saved")
        if isinstance(value, int) and not isinstance(value, bool):
            actual_saved.append(value)
        value = row.get("estimated_frontier_tokens_avoided")
        if isinstance(value, int) and not isinstance(value, bool):
            estimated_saved.append(value)
        extra = row.get("extra") if isinstance(row.get("extra"), dict) else {}
        decision_measurement = (
            extra.get("decision_measurement")
            if isinstance(extra.get("decision_measurement"), dict)
            else None
        )
        if decision_measurement is not None:
            state = decision_measurement.get("usage_state")
            if isinstance(state, str) and state:
                decision_usage_states[state] += 1
            scope = decision_measurement.get("scope")
            if isinstance(scope, str) and scope:
                decision_measurement_scopes[scope] += 1
            value = decision_measurement.get("cost_usd")
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                decision_costs.append(float(value))

    events_by_trace: dict[str, list[dict[str, Any]]] = defaultdict(list)
    disposition_counts: Counter[str] = Counter()
    event_tier_counts: Counter[str] = Counter()
    retry_measurements = 0
    retry_sum = 0
    correction_measurements = 0
    corrections = 0
    reverted_measurements = 0
    reverts = 0
    for event in events:
        trace_id = event.get("trace_id")
        if isinstance(trace_id, str) and trace_id:
            events_by_trace[trace_id].append(event)
        evidence = event.get("evidence") if isinstance(event.get("evidence"), dict) else {}
        disposition_counts[str(evidence.get("disposition") or "unknown")] += 1
        tier = event.get("outcome_tier")
        event_tier_counts[str(tier) if tier is not None else "unscored"] += 1
        outcome = event.get("outcome") if isinstance(event.get("outcome"), dict) else {}
        retries = outcome.get("retries")
        if isinstance(retries, int) and not isinstance(retries, bool):
            retry_measurements += 1
            retry_sum += retries
        if isinstance(outcome.get("user_correction"), bool):
            correction_measurements += 1
            corrections += int(outcome["user_correction"])
        if isinstance(outcome.get("reverted"), bool):
            reverted_measurements += 1
            reverts += int(outcome["reverted"])

    joined_traces = traces & set(events_by_trace)
    terminal_joined = set()
    final_disposition_joined = set()
    gold_traces = set()
    negative_traces = set()
    for trace_id, trace_events in events_by_trace.items():
        for event in trace_events:
            evidence = event.get("evidence") if isinstance(event.get("evidence"), dict) else {}
            disposition = evidence.get("disposition")
            if disposition == "terminated":
                terminal_joined.add(trace_id)
            if disposition in ("terminated", "seed_deleted"):
                final_disposition_joined.add(trace_id)
            if event.get("outcome_tier") == "gold":
                gold_traces.add(trace_id)
            if event.get("outcome_tier") == "negative":
                negative_traces.add(trace_id)

    family_rows: dict[str, dict[str, Any]] = {}
    for trace_id, receipt in receipts.items():
        key = _family(receipt)
        row = family_rows.setdefault(
            key,
            {
                "decisions": 0,
                "abstain": 0,
                "non_abstain": 0,
                "outcome_joined": 0,
                "gold": 0,
                "negative": 0,
                "confidence_measured": 0,
                "calibration_ready": False,
            },
        )
        row["decisions"] += 1
        if receipt.get("prediction") == "abstain":
            row["abstain"] += 1
        else:
            row["non_abstain"] += 1
        if trace_id in joined_traces:
            row["outcome_joined"] += 1
        if trace_id in gold_traces:
            row["gold"] += 1
        if trace_id in negative_traces:
            row["negative"] += 1
        if isinstance(receipt.get("confidence"), (int, float)) and not isinstance(receipt.get("confidence"), bool):
            row["confidence_measured"] += 1

    for row in family_rows.values():
        row["abstention_rate"] = _rate(row["abstain"], row["decisions"])
        row["outcome_join_coverage"] = _rate(row["outcome_joined"], row["decisions"])
        # Confidence here is decision confidence, not a calibrated success
        # probability. Do not compute Brier/ECE from it without a defined target.
        row["calibration_ready"] = False

    evidence_gaps = []
    if non_abstain == 0:
        evidence_gaps.append("no_non_abstain_policy_decisions")
    if not latency:
        evidence_gaps.append("decision_latency_unmeasured")
    if len(joined_traces) < len(receipts):
        evidence_gaps.append("incomplete_outcome_join_coverage")
    if not measured_tokens:
        evidence_gaps.append("frontier_token_usage_unmeasured")
    if not decision_costs:
        evidence_gaps.append("decision_cost_unmeasured")
    if retry_measurements == 0:
        evidence_gaps.append("retry_measurement_unavailable")
    if correction_measurements == 0:
        evidence_gaps.append("correction_measurement_unavailable")
    evidence_gaps.append("safe_coverage_gate_not_defined")
    evidence_gaps.append("per_family_calibration_target_not_defined")
    evidence_gaps.append("counterfactual_or_control_delta_not_measured")

    return {
        "schema": "ao.z0int.promotion_report.v1",
        "snapshot_sha256": _snapshot_digest(receipts, events),
        "decisions": {
            "count": len(receipts),
            "actions": dict(sorted(action_counts.items())),
            "abstain": abstain,
            "abstention_rate": _rate(abstain, len(receipts)),
            "non_abstain": non_abstain,
            "non_abstain_coverage": _rate(non_abstain, len(receipts)),
            "safe_coverage": None,
            "safe_coverage_reason": "no family-specific promotion/calibration gate has been declared",
            "recommendation_comparison": {
                "comparable": comparable,
                "agreement": agreement,
                "disagreement": disagreement,
                "agreement_rate": _rate(agreement, comparable),
            },
        },
        "outcomes": {
            "event_count": len(events),
            "joined_decisions": len(joined_traces),
            "join_coverage": _rate(len(joined_traces), len(receipts)),
            "terminal_joined_decisions": len(terminal_joined & traces),
            "terminal_join_coverage": _rate(len(terminal_joined & traces), len(receipts)),
            "final_disposition_joined_decisions": len(final_disposition_joined & traces),
            "final_disposition_join_coverage": _rate(len(final_disposition_joined & traces), len(receipts)),
            "event_tiers": dict(sorted(event_tier_counts.items())),
            "dispositions": dict(sorted(disposition_counts.items())),
            "verified_positive_decisions": len(gold_traces & traces),
            "verified_negative_decisions": len(negative_traces & traces),
            "positive_negative_overlap": len((gold_traces & negative_traces) & traces),
        },
        "measurements": {
            "decision_latency_ms": {
                "count": len(latency),
                "coverage": _rate(len(latency), len(receipts)),
                "p50": _percentile(latency, 0.50),
                "p95": _percentile(latency, 0.95),
            },
            "frontier_tokens": {
                "scope": "policy_plane_decision",
                "count": len(measured_tokens),
                "coverage": _rate(len(measured_tokens), len(receipts)),
                "sum": sum(measured_tokens) if measured_tokens else None,
                "actual_saved_count": len(actual_saved),
                "actual_saved_sum": sum(actual_saved) if actual_saved else None,
                "estimated_saved_count": len(estimated_saved),
                "estimated_saved_sum": sum(estimated_saved) if estimated_saved else None,
            },
            "cost": {
                "scope": "policy_plane_decision",
                "count": len(decision_costs),
                "coverage": _rate(len(decision_costs), len(receipts)),
                "sum_usd": sum(decision_costs) if decision_costs else None,
                "usage_states": dict(sorted(decision_usage_states.items())),
                "measurement_scopes": dict(sorted(decision_measurement_scopes.items())),
                "delta": None,
            },
            "retries": {
                "count": retry_measurements,
                "coverage": _rate(retry_measurements, len(events)),
                "sum": retry_sum if retry_measurements else None,
                "delta": None,
            },
            "corrections": {
                "count": correction_measurements,
                "coverage": _rate(correction_measurements, len(events)),
                "true": corrections if correction_measurements else None,
                "delta": None,
            },
            "reverts": {
                "count": reverted_measurements,
                "coverage": _rate(reverted_measurements, len(events)),
                "true": reverts if reverted_measurements else None,
                "delta": None,
            },
        },
        "families": dict(sorted(family_rows.items())),
        "comparison": {
            "ready": False,
            "task_success_delta": None,
            "verifier_delta": None,
            "retry_delta": None,
            "correction_delta": None,
            "token_delta": None,
            "cost_delta": None,
            "reason": "no matched control/counterfactual cohort is recorded for AO traffic yet",
        },
        "promotion": {
            "decision": "not_computed",
            "reason": "report is descriptive; policy-specific promotion thresholds must be declared separately",
            "evidence_gaps": evidence_gaps,
            "global_confidence_threshold": None,
        },
    }


def format_ao_promotion_report(report: dict[str, Any]) -> str:
    decisions = report["decisions"]
    outcomes = report["outcomes"]
    latency = report["measurements"]["decision_latency_ms"]
    return (
        "AO shadow promotion evidence\n"
        f"  decisions={decisions['count']} abstention={decisions['abstention_rate']} "
        f"non_abstain={decisions['non_abstain']}\n"
        f"  outcome_join={outcomes['join_coverage']} terminal_join={outcomes['terminal_join_coverage']} "
        f"final_disposition_join={outcomes['final_disposition_join_coverage']} "
        f"gold={outcomes['verified_positive_decisions']} negative={outcomes['verified_negative_decisions']}\n"
        f"  decision_latency_count={latency['count']} p50={latency['p50']} p95={latency['p95']}\n"
        f"  snapshot={report['snapshot_sha256']}\n"
        f"  promotion={report['promotion']['decision']} gaps={len(report['promotion']['evidence_gaps'])}"
    )
