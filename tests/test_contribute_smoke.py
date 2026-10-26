"""CW4 smoke eval (spec §1.4): golden fixture eval, subset budget, pass/fail law."""
from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

from z0int.contribute import smoke

ROOT = Path(__file__).resolve().parents[1]


def test_golden_matches_the_live_fixture_eval(tmp_path):
    env = smoke.offline_env(ROOT)
    env["Z0INT_HOME"] = str(tmp_path / "z0int")
    p = subprocess.run([sys.executable, *smoke.EVAL_ARGV], cwd=ROOT, env=env, capture_output=True, text=True,
                       timeout=120)
    assert p.returncode == 0, p.stderr
    assert smoke.canonical_eval_sha256(p.stdout) == smoke.golden_sha256(ROOT)


def test_canonical_hash_ignores_only_volatile_keys():
    a = smoke.canonical_eval_sha256('{"x": 1, "evaluated_at": 1}')
    assert a == smoke.canonical_eval_sha256('{"evaluated_at": 2, "x": 1}')
    assert a != smoke.canonical_eval_sha256('{"x": 2, "evaluated_at": 1}')
    assert smoke.canonical_eval_sha256("not json") is None


def test_smoke_pass_law():
    ok = [{"name": "doctor", "exit": 0}, {"name": "pytest_subset", "exit": 0},
          {"name": "eval_fixture", "exit": 0, "golden_match": True}]
    assert smoke.smoke_passed(ok)
    assert not smoke.smoke_passed([])
    assert not smoke.smoke_passed([*ok[:2], {**ok[2], "golden_match": False}])
    assert not smoke.smoke_passed([{**ok[0], "exit": 1}, *ok[1:]])


def test_run_smoke_parts_and_offline_env(tmp_path):
    seen = []

    def runner(argv, cwd, env):
        seen.append((argv, env))
        return 0, '{"x": 1}'

    ok, parts = smoke.run_smoke(ROOT, runner)
    assert [p["name"] for p in parts] == ["doctor", "pytest_subset", "eval_fixture"]
    assert not ok and parts[2]["golden_match"] is False        # wrong eval output -> fail
    pytest_argv = seen[2][0]
    assert "contrib_smoke and not live" in pytest_argv
    assert all(env["HF_HUB_OFFLINE"] == "1" and env["CUDA_VISIBLE_DEVICES"] == "" for _, env in seen)


def test_smoke_subset_stays_within_budget(tmp_path):
    env = smoke.offline_env(ROOT)
    env["Z0INT_HOME"] = str(tmp_path / "z0int")
    t = time.monotonic()
    p = subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "-m",
                        "contrib_smoke and not live", *smoke.SMOKE_TESTS], cwd=ROOT, env=env,
                       capture_output=True, text=True, timeout=300)
    assert p.returncode == 0, p.stdout[-2000:]
    assert time.monotonic() - t < 120  # spec target 60 s; 2x headroom for loaded CI runners
