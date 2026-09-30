"""Hidden acceptance test for evo-feature-capacity-max-calls (max_calls budget)."""

from __future__ import annotations

import io
import json
from contextlib import redirect_stdout

from evolution_lab import cli
from evolution_lab.capacity_queue import EstimatedUse, WorkItem, plan_capacity


def item(item_id, work_class="router_eval", **est):
    return WorkItem(
        item_id=item_id,
        work_class=work_class,
        summary="s",
        provenance="data/tool_tournament/v1/fixtures.jsonl",
        estimate=EstimatedUse(**est),
    )


def ids(rows):
    return [r["item_id"] for r in rows]


def test_default_is_unlimited():
    plan = plan_capacity([item("a", calls=1000), item("b", calls=5)], provider="groq")
    assert ids(plan["selected"]) == ["a", "b"]
    assert plan["totals"]["calls"] == 1005


def test_calls_budget_skips_and_continues():
    items = [
        item("big", "dsh_hermes", calls=10),
        item("mid", "router_eval", calls=5),
        item("tiny", "queued_research", calls=1),
    ]
    plan = plan_capacity(items, provider="groq", max_calls=6)
    assert ids(plan["selected"]) == ["mid", "tiny"]
    assert plan["totals"]["calls"] == 6
    reasons = {r["item_id"]: r["reason"] for r in plan["skipped"]}
    assert reasons == {"big": "budget_exhausted_calls"}


def test_budget_is_inclusive():
    plan = plan_capacity([item("x", calls=4), item("y", calls=3)], provider="groq", max_calls=7)
    assert ids(plan["selected"]) == ["x", "y"]
    plan = plan_capacity([item("x", calls=4), item("y", calls=4)], provider="groq", max_calls=7)
    assert ids(plan["selected"]) == ["x"]


def test_zero_budget_allows_zero_call_items():
    plan = plan_capacity([item("free", calls=0), item("paid", calls=1)], provider="groq", max_calls=0)
    assert ids(plan["selected"]) == ["free"]
    assert plan["skipped"][0]["reason"] == "budget_exhausted_calls"


def test_token_reason_takes_precedence():
    plan = plan_capacity([item("both", calls=10, prompt_tokens=100)], provider="groq", max_tokens=5, max_calls=1)
    assert plan["skipped"][0]["reason"] == "budget_exhausted_tokens"
    plan = plan_capacity([item("both", calls=10, cost_usd=1.0)], provider="groq", max_cost_usd=0.5, max_calls=1)
    assert plan["skipped"][0]["reason"] == "budget_exhausted_cost"


def test_combined_with_token_budget():
    items = [
        item("a", "dsh_hermes", calls=1, prompt_tokens=100),
        item("b", "router_eval", calls=5, prompt_tokens=1),
        item("c", "queued_research", calls=1, prompt_tokens=1),
    ]
    plan = plan_capacity(items, provider="groq", max_tokens=50, max_calls=3)
    assert ids(plan["selected"]) == ["c"]
    reasons = {r["item_id"]: r["reason"] for r in plan["skipped"]}
    assert reasons == {"a": "budget_exhausted_tokens", "b": "budget_exhausted_calls"}


def test_cli_max_calls_flag():
    buf = io.StringIO()
    with redirect_stdout(buf):
        code = cli.main(["capacity-queue", "plan", "--provider", "groq", "--max-calls", "150"])
    assert code == 0
    payload = json.loads(buf.getvalue())
    assert payload["totals"]["calls"] == 142
    assert ids(payload["selected"]) == [
        "qroute-counterfactual-densified-cells",
        "tool-tournament-candidate-substitutions",
    ]
    assert any(s["reason"] == "budget_exhausted_calls" for s in payload["skipped"])
