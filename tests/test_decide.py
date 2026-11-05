import copy
import json
from pathlib import Path

import pytest

from z0int.decide import decide, load_roles

ROLES = load_roles()


def lane(band="on-pace", pace=0.9, agents=6, effort="medium"):
    return {"band": band, "pace_ratio": pace, "max_workflow_agents": agents, "effort": effort}


def signals(**over):
    base = {
        "roles": ROLES,
        "burn": {"lanes": {"claude": lane("under-burn", 0.44, 12, "high"), "claude2": lane("on-pace", 0.86),
                           "claude3": lane("hard-throttle", 58.9, 0, "low"), "codex": lane("on-pace", 0.93)}},
        "saturation": {},
        "free_inventory": {"disabled": [], "paid": []},
    }
    base.update(over)
    return base


def test_role_table_obeys_harness_law():
    law = ROLES["harness_law"]
    for role, cands in ROLES["roles"].items():
        for c in cands:
            if c["provider"] in law:
                assert c["harness"] in law[c["provider"]], (role, c)


def test_task_on_fx_picks_sol_low_on_codex():
    d = decide({"role": "task", "privacy": "public", "executors": ["fx"]}, signals())
    assert d["kind"] == "ROUTE"
    assert (d["harness"], d["provider"], d["model"], d["effort"], d["account"]) == ("fx", "openai-codex", "gpt-6.1-sol", "low", "codex")
    assert d["receipt_id"] and "codex on-pace" in d["reason"]


def test_anthropic_account_is_least_paced_admitted_pool():
    d = decide({"role": "default", "privacy": "personal"}, signals())
    assert d["provider"] == "anthropic" and d["account"] == "claude"


def test_hard_throttled_pool_is_never_chosen():
    burn = {"lanes": {"claude": lane("hard-throttle", 20, 0, "low"), "claude2": lane("hard-throttle", 9, 0, "low"),
                      "claude3": lane("hard-throttle", 58, 0, "low"), "codex": lane()}}
    d = decide({"role": "default", "privacy": "personal", "executors": ["hermes"]}, signals(burn=burn))
    assert d["kind"] == "ROUTE" and d["provider"] == "local", d
    assert any("hard-throttle" in r for r in d["rejected"])


def test_throttle_band_caps_effort():
    burn = {"lanes": {"claude": lane("throttle", 1.2, 2, "low"), "claude2": lane("throttle", 1.3, 2, "low"),
                      "claude3": lane("throttle", 1.4, 2, "low"), "codex": lane()}}
    d = decide({"role": "plan", "privacy": "public"}, signals(burn=burn))
    assert d["provider"] == "anthropic" and d["effort"] == "low"


def test_personal_privacy_excludes_gpt():
    d = decide({"role": "task", "privacy": "personal"}, signals())
    assert d["provider"] in ("anthropic", "local")
    assert any("privacy" in r for r in d["rejected"])


@pytest.mark.parametrize("privacy", ["location", "credentials"])
def test_location_and_credentials_are_local_only(privacy):
    for role in ROLES["roles"]:
        d = decide({"role": role, "privacy": privacy}, signals())
        assert d["kind"] == "HOLD" or d["provider"] == "local", (role, d)


def test_saturated_model_is_skipped_then_hold_when_all_saturated():
    sat = {"groq/gpt-oss-20b": {"available": False, "reason": "429 cooldown"}}
    d = decide({"role": "free", "privacy": "public", "executors": ["vllm-sr"]}, signals(saturation=sat))
    assert d["kind"] == "ROUTE" and d["model"] != "gpt-oss-20b"
    sat = {"groq": {"available": False, "reason": "429 cooldown"}}
    d = decide({"role": "free", "privacy": "public", "executors": ["vllm-sr"]}, signals(saturation=sat))
    assert d["kind"] == "HOLD" and all("429" in r for r in d["rejected"] if r.startswith("groq/"))


def test_free_inventory_disables_a_free_candidate():
    first = next(c for c in ROLES["roles"]["free"] if c.get("inventory_id"))
    inv = {"disabled": [first["inventory_id"]], "paid": []}
    d = decide({"role": "free", "privacy": "public"}, signals(free_inventory=inv))
    assert d["kind"] == "ROUTE" and d["model"] != first["model"]
    assert any("inventory" in r for r in d["rejected"])


def test_unknown_role_or_privacy_raises():
    with pytest.raises(ValueError):
        decide({"role": "wizard", "privacy": "public"}, signals())
    with pytest.raises(ValueError):
        decide({"role": "task", "privacy": "secretish"}, signals())


def test_decide_is_pure():
    s = signals()
    before = json.dumps(s, sort_keys=True)
    a = decide({"role": "task", "privacy": "public"}, s)
    b = decide({"role": "task", "privacy": "public"}, s)
    assert a == b and json.dumps(s, sort_keys=True) == before


def test_missing_burn_lane_rejects_metered_provider():
    burn = {"lanes": {"claude": lane()}}
    d = decide({"role": "task", "privacy": "public", "executors": ["fx"]}, signals(burn=burn))
    assert d["kind"] == "HOLD" and any("no burn-pace row" in r for r in d["rejected"])
