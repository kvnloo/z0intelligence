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


def test_every_candidate_cites_a_known_source():
    for role, cands in ROLES["roles"].items():
        for c in cands:
            assert c.get("source") and set(c["source"]) <= set(ROLES["sources"]), (role, c)
    assert ROLES["privacy_source"] in ROLES["sources"]


def test_task_on_fx_picks_terra_medium_on_codex():
    d = decide({"role": "task", "privacy": "public", "executors": ["fx"]}, signals())
    assert d["kind"] == "ROUTE"
    assert (d["harness"], d["provider"], d["model"], d["effort"], d["account"]) == ("fx", "openai-codex", "gpt-6.1-terra", "medium", "codex")
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
    assert d["kind"] == "ROUTE" and d["model"] == "qwen3.8-27b"
    sat = {"groq": {"available": False, "reason": "429 cooldown"}}
    d = decide({"role": "free", "privacy": "public", "executors": ["vllm-sr"]}, signals(saturation=sat))
    assert d["kind"] == "HOLD" and all("429" in r for r in d["rejected"] if r.startswith("groq/"))
    d = decide({"role": "free", "privacy": "public", "executors": ["vllm-sr", "vllm-sr-cerebras"]}, signals(saturation=sat))
    assert (d["provider"], d["model"]) == ("cerebras", "gpt-oss-120b")


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


def test_null_burn_fields_do_not_crash():
    s = signals()
    s["burn"]["lanes"]["claude"]["pace_ratio"] = None
    s["burn"]["lanes"]["claude2"]["max_workflow_agents"] = None
    d = decide({"role": "plan", "privacy": "public", "executors": ["hermes"]}, s)
    assert d["kind"] == "ROUTE" and d["provider"] == "anthropic"


def test_exhausted_lane_with_null_pace_is_never_picked():
    s = signals()
    for n in ("claude", "claude2"):
        s["burn"]["lanes"][n]["pace_ratio"] = 0.9
    s["burn"]["lanes"]["claude3"] = {"band": "hard-stop-exhausted", "pace_ratio": None, "max_workflow_agents": None}
    d = decide({"role": "plan", "privacy": "public", "executors": ["hermes"]}, s)
    assert d["account"] != "claude3"


# Every band burn-pace knobs() emits (burn-pace L136-151). Null pace_ratio means ratio 0 (under-burn) or undefined (unknown).
BANDS = {"under-burn": "pick", "on-pace": "pick", "throttle": "pick", "unknown": "pick", "hard-throttle": "block", "hard-stop-exhausted": "block"}


def test_band_domain_is_fully_classified():
    from z0int import decide as D
    for band, want in BANDS.items():
        assert (band in D.BLOCKED_BANDS) == (want == "block"), band


def test_unknown_lane_ranks_behind_a_measured_lane_and_is_capped():
    s = signals()
    s["burn"]["lanes"] = {"claude": {"band": "unknown", "pace_ratio": None, "max_workflow_agents": 4, "effort": "medium"},
                          "claude2": {"band": "on-pace", "pace_ratio": 0.9, "max_workflow_agents": 6, "effort": "medium"},
                          "claude3": {"band": "hard-throttle", "pace_ratio": 2.0, "max_workflow_agents": 0, "effort": "low"},
                          "codex": {"band": "on-pace", "pace_ratio": 0.9, "max_workflow_agents": 6, "effort": "medium"}}
    d = decide({"role": "plan", "privacy": "public", "executors": ["hermes"]}, s)
    assert d["account"] == "claude2"
    s["burn"]["lanes"]["claude2"]["band"] = "hard-throttle"
    d = decide({"role": "plan", "privacy": "public", "executors": ["hermes"]}, s)
    assert d["account"] == "claude" and d["effort"] == "medium"     # unknown is usable, capped at burn-pace's effort


def test_null_pace_under_burn_still_ranks_first():
    s = signals()
    s["burn"]["lanes"]["claude"].update(band="under-burn", pace_ratio=None)
    s["burn"]["lanes"]["claude2"].update(band="on-pace", pace_ratio=0.5)
    d = decide({"role": "plan", "privacy": "public", "executors": ["hermes"]}, s)
    assert d["account"] == "claude"
