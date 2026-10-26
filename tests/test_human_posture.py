"""Human posture twin: golden fixtures, the circadian laws (red/green), purity, Bend parity.

Every input here is SYNTHETIC (law C8). Run:
  uv run --no-project --with pytest --with hypothesis python -m pytest -q tests/test_human_posture.py tests/test_circadian_service.py
"""
from __future__ import annotations

import ast
import itertools
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from z0int import human_posture as hp  # noqa: E402

FIXTURES = json.loads((ROOT / "tests/fixtures/human_posture/cases.json").read_text())["cases"]
WAKE = "2026-10-27T11:30:00Z"  # SYNTHETIC default wake 07:30 EDT


def at(minutes: int, base: str = WAKE) -> str:
    from datetime import timedelta
    return hp.fmt(hp.parse_ts(base) + timedelta(minutes=minutes))


def decide(now: str, facts: dict | None = None, **cfg) -> dict:
    return hp.policy(hp.evaluate_human_state(facts or {}, now, {**hp.DEFAULT_CONFIG, **cfg}), now)


# ---------------------------------------------------------------- golden fixtures (C4)

@pytest.mark.parametrize("case", FIXTURES, ids=[c["name"] for c in FIXTURES])
def test_golden_fixture(case):
    i = case["input"]
    if case["kind"] in ("clock", "evaluate"):
        got = hp.evaluate_human_state(i["facts"], i["now"], i["config"])
    elif case["kind"] == "policy":
        got = hp.policy(i["state"], i["now"])
    else:
        got = hp.route(i["decision"], i["notice"], i["now"])
    assert hp.canonical(got) == hp.canonical(case["expected"])


def test_fixtures_are_current():
    """The checked-in fixtures equal a fresh generation (no hand edits, no drift)."""
    out = subprocess.run([sys.executable, "-c",
                          "import runpy,json,sys; sys.argv=['x']; g=runpy.run_path('scripts/gen_human_posture_fixtures.py');"
                          "print(json.dumps(g['cases'], sort_keys=True))"],
                         cwd=ROOT, capture_output=True, text=True, check=True)
    assert json.loads(out.stdout) == FIXTURES


def test_fixtures_are_synthetic_and_have_no_biometrics():
    raw = (ROOT / "tests/fixtures/human_posture/cases.json").read_text()
    for key in ("rmssd", "hrv\"", "restingHr", "spo2", "heartRate", "skinTemp", "stages"):
        assert key not in raw
    assert "SYNTHETIC" in raw


# ---------------------------------------------------------------- laws, red then green

def test_c2_no_decisions_after_freeze():
    """Red: a decision after the freeze delivered. Green: it waits for the digest and the next morning."""
    d = decide(at(870))
    assert d["phase"] == "WIND_DOWN" and d["allowDecisions"] == "none"
    r = hp.route(d, {"cls": "decision"}, at(870))
    assert r["action"] == "digest" and r["releaseAt"] == at(1440 + 30)


def test_c2_decisions_by_day_only_in_peak_or_tonight_plan():
    assert hp.route(decide(at(120)), {"cls": "decision"}, at(120))["action"] == "deliver"
    assert hp.route(decide(at(400)), {"cls": "decision"}, at(400))["action"] == "defer"
    assert hp.route(decide(at(600)), {"cls": "decision"}, at(600))["action"] == "defer"
    assert hp.route(decide(at(600)), {"cls": "decision", "tonightPlan": True}, at(600))["action"] == "deliver"


def test_c3_interrupt_cap_five_decisions_in_peak():
    """Five decision notices in PEAK block 1: three delivered, two deferred to the next hour."""
    now = at(120)
    counters = {"hourKey": now[:13], "delivered": 0, "blockStart": at(45), "interrupts": 0}
    actions = []
    for _ in range(5):
        d = decide(now, {"counters": dict(counters)})
        r = hp.route(d, {"cls": "decision"}, now)
        actions.append(r["action"])
        if r["action"] == "deliver":
            counters["delivered"] += 1
            counters["interrupts"] += 1
    assert actions == ["deliver"] * 3 + ["defer"] * 2


def test_c5_agent_critical_is_downgraded():
    d = decide(at(1100))
    r = hp.route(d, {"cls": "critical", "producer": "dash-notify", "irreversible": True}, at(1100))
    assert r["cls"] == "report" and r["action"] == "digest"


def test_c5_r2_allowlisted_critical_needs_opt_in_at_night():
    n = {"cls": "critical", "producer": "backup-guard", "irreversible": True}
    assert hp.route(decide(at(1100)), n, at(1100))["action"] == "digest"
    assert hp.route(decide(at(1100), criticalOptIn=True), n, at(1100))["action"] == "deliver"
    assert hp.route(decide(at(1100), criticalOptIn=True), {**n, "irreversible": False}, at(1100))["action"] == "digest"


def test_c9_stale_wearable_is_not_measured():
    wear = {"available": True, "lastSessionStart": "2026-10-25T08:00:00Z", "lastSessionEnd": "2026-10-25T15:00:00Z"}
    s = hp.evaluate_human_state({"wearable": wear}, at(120), hp.DEFAULT_CONFIG)
    assert s["quality"] == "default" and s["readiness"] == "unknown"


def test_c10_done_for_today_silences_reports_until_wake():
    facts = {"doneAt": at(500)}
    for m in (510, 700, 900, 1100, 1435):
        d = decide(at(m), facts)
        assert d["phase"] in hp.QUIET, m
        assert hp.route(d, {"cls": "report"}, at(m))["action"] == "digest"
    assert decide(at(1440), facts)["phase"] == "INERTIA"


def test_c14_only_peak_uses_a_sounding_channel():
    for m in range(-30, 1440, 15):
        d = decide(at(m))
        assert (d["channel"] in hp.SOUND_CHANNELS) == (d["phase"] == "PEAK")


def test_c15_default_wake_does_not_score_or_release():
    s = hp.evaluate_human_state({}, at(120), hp.DEFAULT_CONFIG)
    assert s["quality"] == "default" and s["sleepOnsetLast"] is None


def test_c17_dst_night_is_continuous():
    """2026-11-01 01:00 happens twice. Phases follow elapsed time, not wall clock."""
    seq = [decide(t)["phase"] for t in ("2026-11-01T04:59:00Z", "2026-11-01T05:00:00Z",
                                         "2026-11-01T06:00:00Z", "2026-11-01T07:00:00Z")]
    assert seq == ["SLEEP"] * 4
    late = {"bedTarget": "01:30"}   # an anchor inside the repeated hour: first occurrence (fold=0)
    s = hp.evaluate_human_state({}, "2026-11-01T05:00:00Z", {**hp.DEFAULT_CONFIG, **late})
    assert s["anchors"]["bedTarget"] == "2026-11-01T05:30:00Z"   # 01:30 EDT
    assert s["anchors"]["freezeAt"] == "2026-11-01T02:00:00Z"    # night mode 22:00 EDT
    assert [decide(t, **late)["phase"] for t in ("2026-11-01T05:29:00Z", "2026-11-01T05:30:00Z", "2026-11-01T06:30:00Z")] \
        == ["PRE_SLEEP", "SLEEP", "SLEEP"]


def test_r2_real_time_sleep_rule():
    late = {"activity": {"available": True, "intervals": [
        {"start": "2026-10-27T11:00:00Z", "end": "2026-10-28T04:40:00Z", "source": "desk"}]}}
    assert decide("2026-10-28T05:05:00Z", late)["phase"] == "PRE_SLEEP"
    assert decide("2026-10-28T05:10:00Z", late)["phase"] == "SLEEP"


def test_r3_no_sources_gives_clock_only_default():
    s = hp.evaluate_human_state({"activity": {"available": False}, "wearable": {"available": False}}, at(60), hp.DEFAULT_CONFIG)
    assert s["quality"] == "default" and s["wakeSource"] == "clock"


def test_r4_public_view_is_an_allowlist():
    assert set(hp.public_view(decide(at(120)))) == {"allowDecisions", "nextPeakAt"}


def test_r5_readiness_never_selects_a_demanding_row():
    for m in range(0, 1440, 30):
        d = decide(at(m))
        assert d["interruptCapPerBlock"] <= 3 and d["notifyBudgetPerHour"] <= 6


def test_capture_is_shadow_only():
    d = decide(at(900))
    assert d["phase"] == "WIND_DOWN" and d["captureMode"] is True and d["captureEnforced"] is False


# ---------------------------------------------------------------- C4 purity

def test_c4_no_clock_disk_env_or_random_inside():
    tree = ast.parse((ROOT / "src/z0int/human_posture.py").read_text())
    banned_attrs = {"now", "utcnow", "today", "time", "random", "environ", "getenv", "urandom"}
    banned_calls = {"open", "input", "print"}
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute):
            assert node.attr not in banned_attrs, f"impure access .{node.attr} at line {node.lineno}"
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            assert node.func.id not in banned_calls, f"impure call {node.func.id} at line {node.lineno}"
        mods = [a.name for a in node.names] if isinstance(node, ast.Import) else \
            [node.module or ""] if isinstance(node, ast.ImportFrom) else []
        assert not {"random", "os", "time", "socket", "urllib", "subprocess"} & {m.split(".")[0] for m in mods}


def test_c4_canonical_has_no_floats():
    with pytest.raises(TypeError):
        hp.canonical({"a": 1.5})


hypothesis = pytest.importorskip("hypothesis")
from hypothesis import given, settings, strategies as st  # noqa: E402


@settings(max_examples=300, deadline=None)
@given(minute=st.integers(min_value=-2000, max_value=60 * 24 * 10),
       done=st.one_of(st.none(), st.integers(min_value=-600, max_value=3000)),
       delivered=st.integers(min_value=0, max_value=9))
def test_c4_property_repeatable_and_lawful(minute, done, delivered):
    now = at(minute)
    facts = {"counters": {"hourKey": now[:13], "delivered": delivered}}
    if done is not None:
        facts["doneAt"] = at(done)
    s1 = hp.evaluate_human_state(facts, now, hp.DEFAULT_CONFIG)
    s2 = hp.evaluate_human_state(json.loads(json.dumps(facts)), now, hp.DEFAULT_CONFIG)
    assert hp.canonical(s1) == hp.canonical(s2)
    d = hp.policy(s1, now)
    assert hp.canonical(d) == hp.canonical(hp.policy(json.loads(hp.canonical(s1)), now))
    assert hp.parse_ts(d["nextTransitionAt"]) >= hp.parse_ts(now) or d["phase"] == "SLEEP"
    for cls in hp.CLASSES:
        r = hp.route(d, {"cls": cls}, now)
        if d["phase"] in hp.QUIET:
            assert r["action"] != "deliver"            # C2, C10
        if r["action"] != "deliver":
            assert r["releaseAt"] is not None


# ---------------------------------------------------------------- Bend twin

BEND = shutil.which("bend") or str(Path.home() / ".bend/bin/bend")
needs_bend = pytest.mark.skipif(not Path(BEND).exists(), reason="Bend 2 not installed")
KERNEL = ROOT / "bend" / "circadian"


@needs_bend
def test_bend_laws_all_proven():
    out = subprocess.run([BEND, "PROOF.bend"], cwd=KERNEL, capture_output=True, text=True, timeout=300)
    assert "ALL PROOFS CHECK" in out.stdout + out.stderr


def test_bend_proof_is_regenerated():
    before = (KERNEL / "PROOF.bend").read_text()
    subprocess.run([sys.executable, "scripts/gen_circadian_proof.py"], cwd=ROOT, check=True)
    assert (KERNEL / "PROOF.bend").read_text() == before


@needs_bend
def test_bend_python_parity_all_1024_inputs(tmp_path):
    binary = tmp_path / "circadian-gate"
    subprocess.run([BEND, "main.bend", "-o", str(binary)], cwd=KERNEL, check=True, capture_output=True, timeout=600)
    rows = subprocess.run([str(binary)], capture_output=True, text=True, check=True, timeout=60).stdout.split("\n")
    rows = [r for r in rows if r.strip()]
    assert len(rows) == 1024
    seen = set()
    for line in rows:
        p, c, a, i, o, t, k, act = (int(x) for x in line.split())
        want = hp.gate(hp.PHASES[p], hp.CLASSES[c], bool(a), bool(i), bool(o), bool(t), bool(k))
        assert hp.ACTIONS[act] == want, line
        seen.add((p, c, a, i, o, t, k))
    assert seen == set(itertools.product(range(8), range(4), *[(0, 1)] * 5))
