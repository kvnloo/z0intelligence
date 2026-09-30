"""Hidden acceptance test for ker-feature-exclude (run with PYTHONPATH=<work root>)."""
from __future__ import annotations

import dataclasses

from kerdoios import _req_from_args
from kerdoios.optimize import hard_filter, plan
from kerdoios.providers.fixture import fixture_offers
from kerdoios.types import Mode, WorkRequirement


def test_field_default_is_empty_tuple():
    fields = {f.name: f for f in dataclasses.fields(WorkRequirement)}
    assert "excluded_providers" in fields
    assert WorkRequirement().excluded_providers == ()


def test_hard_filter_rejects_excluded_provider_with_exact_reason():
    req = WorkRequirement(context=128_000, tool_use=True, tools=("github",), coding=0.6,
                          excluded_providers=("paid-api", "groq"))
    kept, rejected = hard_filter(fixture_offers(), req)
    ids = {o.id for o in kept}
    reasons = {r.offer_id: r.reason for r in rejected}
    assert "frontier/paid" not in ids and "groq/free-70b" not in ids
    assert reasons["frontier/paid"] == "provider paid-api excluded"
    assert reasons["groq/free-70b"] == "provider groq excluded"
    assert {"cerebras/free-coder", "openrouter/free", "local/qwen-32b", "nous/hermes-70b"} <= ids


def test_exclusion_checked_before_other_rules():
    # tiny/8k-no-tools would fail the context rule, but exclusion must win
    req = WorkRequirement(context=128_000, privacy="local_only", excluded_providers=("openrouter",))
    _, rejected = hard_filter(fixture_offers(), req)
    reasons = {r.offer_id: r.reason for r in rejected}
    assert reasons["tiny/8k-no-tools"] == "provider openrouter excluded"
    assert reasons["openrouter/free"] == "provider openrouter excluded"
    assert reasons["groq/free-70b"] == "privacy local_only excludes remote providers"


def test_plan_never_places_excluded():
    req = WorkRequirement(coding=0.6, reasoning=0.6, tool_use=True, tools=("github",), context=128_000,
                          parallelism=100, maximum_cost=0.50, mode=Mode.CHEAP, excluded_providers=("paid-api",))
    built = plan(fixture_offers(), req)
    assert built.placements
    assert all(p.provider != "paid-api" for p in built.placements)
    assert "frontier/paid" not in built.fallbacks


def test_default_behaviour_unchanged():
    req = WorkRequirement(context=128_000, tool_use=True, tools=("github",), coding=0.6)
    kept, _ = hard_filter(fixture_offers(), req)
    assert "frontier/paid" in {o.id for o in kept}


def test_plugin_args_accept_list_or_comma_string():
    assert _req_from_args({"exclude": "paid-api, groq"}).excluded_providers == ("paid-api", "groq")
    assert _req_from_args({"exclude": ["local"]}).excluded_providers == ("local",)
    assert _req_from_args({}).excluded_providers == ()
