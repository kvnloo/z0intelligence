"""Suite-wide isolation: no test may reach the developer's real AgentsView database (~/.agentsview) or extra
Claude transcript trees ($Z0INT_CLAUDE_PROJECTS_DIRS)."""
from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _isolated_agentsview(tmp_path_factory, monkeypatch):
    monkeypatch.setenv('AGENTSVIEW_DATA_DIR', str(tmp_path_factory.mktemp('agentsview')))
    monkeypatch.delenv('Z0INT_CLAUDE_PROJECTS_DIRS', raising=False)  # nor extra Claude transcript trees


@pytest.fixture
def routing_policy_snapshot(tmp_path, monkeypatch):
    """Write real compiled synthetic routing evidence for transport-only tests."""
    from datetime import datetime, timedelta, timezone
    from z0int import paths
    from z0int.policy.compile import CompilerConfig, compile_policy, write_policy
    from z0int.policy.model_routing import FORMULA_ID
    monkeypatch.setenv("Z0INT_HOME", str(tmp_path / "routing-home"))
    monkeypatch.setenv("KERDOIOS_OBSERVED_LOG", str(tmp_path / "kerdoios-observed.jsonl"))

    def create(plan):
        now = datetime.now(timezone.utc)
        iso = now.isoformat()
        entries = []
        for index, candidate in enumerate(plan["candidates"]):
            metric = lambda value: {"value": value, "source": "synthetic/transport-regression",
                                    "timestamp": iso, "task_class": plan["category"]}
            provider, model = candidate["provider"], candidate["model"]
            entries.append({"observed_at": iso, "evidence": ["synthetic/transport-regression"],
                "offer": {"id": provider + "/" + model, "provider": provider,
                    "resource_type": "llm", "model": model, "local": False,
                    "capabilities": {"coding": .9, "reasoning": .9, "tool_use": .9, "vision": 0},
                    "capacity": {"concurrency": 1},
                    "economics": {"input_token_price": 0, "output_token_price": 0},
                    "telemetry": {"latency_p50_ms": 1, "latency_p95_ms": 2, "failure_rate": 0, "availability": 1}},
                "metrics": {k: metric(v) for k, v in {"ttft_seconds": index + 1,
                    "tokens_per_second": 100, "burn": 0, "remaining": 100,
                    "time_to_reset_hours": 1, "p_accept": .9}.items()}})
        bundle = {"formula_id": FORMULA_ID, "offers": entries,
                  "expires_at": (now + timedelta(hours=1)).isoformat(),
                  "task_classes": {plan["category"]: {"coding": .5}}}
        policy = compile_policy([], None, now, CompilerConfig(compiler_revision="synthetic", model_routing=bundle))
        write_policy(policy, paths.home())
        return policy
    return create


@pytest.fixture(autouse=True)
def _unittest_routing_snapshot(request):
    if request.node.name == "test_unmetered_failed_attempt_does_not_break_successful_fallback":
        request.instance.routing_snapshot = request.getfixturevalue("routing_policy_snapshot")
