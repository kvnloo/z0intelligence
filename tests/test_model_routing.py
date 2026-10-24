"""Synthetic O14 integration with the real Kerdoios allocator, no provider calls."""
from copy import deepcopy
from datetime import datetime, timezone
import json
from pathlib import Path

import pytest

from z0int.policy.compile import CompilerConfig, compile_policy, validate_policy
from z0int.policy.model_routing import (FORMULA_ID, RoutingDenied, allocation, enforce,
                                       audit_stamp, quota_allows, reserve_admission)

NOW = "2026-10-24T12:00:00Z"
DT = datetime(2026, 10, 24, 12, tzinfo=timezone.utc)
SCENARIOS = json.loads((Path(__file__).parent / "fixtures/model_routing_scenarios.json").read_text())


def entry(provider, ttft=.1, tps=200):
    def metric(value):
        return {"value": value, "source": "synthetic/test", "timestamp": NOW, "task_class": "small"}
    return {"observed_at": NOW, "evidence": ["synthetic/test"],
            "opencode_access": {"program": "zen", "free": True, "source": "synthetic/free-catalog", "timestamp": NOW},
            "offer": {"id": provider, "provider": provider, "transport": provider,
                      "resource_type": "llm", "model": "synthetic", "local": False,
                      "capabilities": {"coding": .9, "reasoning": .9, "tool_use": .9, "vision": 0},
                      "capacity": {"concurrency": 1},
                      "economics": {"input_token_price": 0, "output_token_price": 0},
                      "telemetry": {"latency_p50_ms": 100, "latency_p95_ms": 200,
                                    "failure_rate": 0, "availability": 1}},
            "metrics": {key: metric(value) for key, value in {
                "ttft_seconds": ttft, "tokens_per_second": tps, "burn": .01,
                "remaining": 100, "time_to_reset_hours": 1, "p_accept": .9}.items()}}


def policy(entries=None):
    bundle = {"formula_id": FORMULA_ID, "expires_at": "2026-10-24T13:00:00Z",
              "offers": entries or [entry("cerebras"), entry("groq", .2, 180), entry("openrouter", 2, 30)],
              "task_classes": {"small": {"coding": .5, "estimated_input_tokens": 20,
                                         "estimated_output_tokens": 10}}}
    return compile_policy([], None, NOW, CompilerConfig(compiler_revision="synthetic", model_routing=bundle))


def test_fast_small_task_prefers_cerebras_when_floor_met():
    selected = allocation(policy(), "small", [], NOW)
    assert selected["provider"] == SCENARIOS["scenarios"][0]["expected"]
    stamp = enforce(policy(), "small", "cerebras", "synthetic", now=NOW)
    assert stamp["o14_credit"] and not audit_stamp(stamp)
    assert stamp["formula_inputs"]["selected"]["lambda_evidence"]["status"] == "unmeasured"


@pytest.mark.parametrize("status", [403, 429])
def test_failed_free_provider_next_eligible_and_never_relabelled(status):
    p = policy([entry("opencode"), entry("groq", .2)])
    failures = [{"provider": "opencode", "model": "synthetic", "status": "failed",
                 "reason": f"HTTP {status}", "http_status": status}]
    stamp = enforce(p, "small", "groq", "synthetic", failed=failures, now=NOW)
    assert stamp["provider"] == "groq" and stamp["failed_attempts"] == failures
    assert stamp["failed_attempts"][0]["provider"] == "opencode"


def test_nonoptimal_without_reason_denied_and_guard_flags_dominance():
    with pytest.raises(RoutingDenied, match="non-argmin"):
        enforce(policy(), "small", "openrouter", "synthetic", now=NOW)
    stamp = enforce(policy(), "small", "openrouter", "synthetic", now=NOW, reason="operator fixed-model comparison")
    assert stamp["dominated_by"] and not stamp["o14_credit"]
    stamp["reason"] = None
    assert "O14 dominated offer without recorded reason" in audit_stamp(stamp)


def test_unknown_metrics_unscored_no_credit():
    unknown = entry("opencode")
    unknown["metrics"]["tokens_per_second"]["value"] = None
    stamp = enforce(policy([unknown]), "small", "opencode", "synthetic", now=NOW)
    assert stamp["score_status"] == "unscored" and stamp["score"] is None
    assert stamp["o14_credit"] is False and not audit_stamp(stamp)


def test_capability_floor_never_waived_by_reason():
    weak = entry("weak")
    weak["offer"]["capabilities"]["coding"] = .1
    with pytest.raises(RoutingDenied, match="no eligible"):
        enforce(policy([weak]), "small", "weak", "synthetic", now=NOW, reason="fast")


def test_burn_guard_never_assumes_refill_or_exhausts_reset_window():
    sub = entry("luna")
    sub["quota_windows"] = [{"group": "claude-shared", "unit": "tokens", "source": "synthetic",
        "remaining": 100, "burn_per_hour": 100, "reserved": 0, "request_upper_bound": 1,
        "reset_at": "2026-10-24T13:00:00Z"}]
    sub["subscription"] = True
    assert not quota_allows(sub, DT)
    sub["quota_windows"][0]["remaining"] = 102
    assert quota_allows(sub, DT)
    assert not quota_allows(sub, datetime(2026, 10, 24, 13, tzinfo=timezone.utc))


def test_compiled_evidence_content_addressed_no_live_scan(tmp_path):
    p = policy()
    validate_policy(p)
    tampered = deepcopy(p)
    tampered["model_routing"]["offers"][0]["metrics"]["burn"]["value"] = 0
    with pytest.raises(ValueError, match="hash"):
        allocation(tampered, "small", [], NOW)
    assert "model_routing" in compile_policy([], None, NOW, CompilerConfig(compiler_revision="synthetic"))


def test_shared_window_reservations_are_serialized_from_existing_receipts(monkeypatch, tmp_path):
    from z0int import provider_saturation, dispatch_authority
    from contextlib import nullcontext
    monkeypatch.setattr(dispatch_authority, "locked", lambda name: nullcontext())
    selected = entry("luna")
    selected["quota_windows"] = [{"group": "claude-shared", "unit": "tokens", "source": "synthetic",
        "remaining": 100, "burn_per_hour": 0, "reserved": 0, "request_upper_bound": 60,
        "reset_at": "2026-10-24T13:00:00Z"}]
    stamp = {"formula_inputs": {"offers": [selected]}, "offer_id": "luna"}
    monkeypatch.setattr(provider_saturation, "_rows", lambda: [{"trace_id": "other", "ts": DT.timestamp(),
        "extra": {"status": "acquired", "o14_reservations": [{"group": "claude-shared",
        "reset_at": "2026-10-24T13:00:00Z", "amount": 60}]}}])
    class Admission:
        def acquire(self, *args):
            pytest.fail("must reject before admission")
    with pytest.raises(RoutingDenied, match="exhaust"):
        reserve_admission(Admission(), "luna", "call", {"quota_reserved_tokens": 60}, stamp, DT)


def test_opencode_non_go_zen_free_is_always_a_violation():
    paid = entry("opencode")
    paid["opencode_access"]["free"] = False
    with pytest.raises(ValueError, match="Go/Zen free"):
        policy([paid])
    stamp = {"rule_id": "O14.model_routing", "provider": "opencode", "model": "paid",
             "offer_id": "opencode", "formula_inputs": {"offers": [paid]},
             "score_status": "unscored", "o14_credit": False}
    assert "O14 OpenCode non-Go/Zen-free model" in audit_stamp(stamp)


def test_equal_minimum_offers_are_valid_without_fabricated_reason():
    p = policy([entry("groq"), entry("cerebras")])
    stamp = enforce(p, "small", "groq", "synthetic", now=NOW)
    assert stamp["o14_credit"] and stamp["score"] == stamp["minimum_score"]
    assert not audit_stamp(stamp)
