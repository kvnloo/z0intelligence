"""Sensitivity probe (scratch, not committed): run the latency test with the opportunity build on the reply
path (in-process instead of the detached child). Expected: FAIL."""
import importlib.util
import sys

spec = importlib.util.spec_from_file_location(
    "c3_capture_tests", "/mnt/zer0models/z0-wt/wiring/wt/C3-omp-omo-capture/tests/test_omp_capture.py")
t = importlib.util.module_from_spec(spec)
sys.modules["c3_capture_tests"] = t
spec.loader.exec_module(t)
home = t.home


def test_probe_build_on_reply_path(home, tmp_path, monkeypatch):
    monkeypatch.setattr(t.hc, "spawn_detached",
                        lambda argv, job: t.hc.record_opportunity("omp", job["payload"], job["ctx"]))
    t.test_bridge_reply_latency_stays_within_its_bound_over_200_turns(home, tmp_path)
