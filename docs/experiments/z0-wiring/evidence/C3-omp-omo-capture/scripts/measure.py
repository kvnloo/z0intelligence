import importlib.util, statistics, sys, time, os, json
from pathlib import Path
from unittest import mock
spec = importlib.util.spec_from_file_location("t", "/mnt/zer0models/z0-wt/wiring/wt/C3-omp-omo-capture/tests/test_omp_capture.py")
t = importlib.util.module_from_spec(spec); spec.loader.exec_module(t)
tmp = Path(os.environ["TMPDIR"]) / f"measure-{time.time()}"; tmp.mkdir()
mode = sys.argv[1]
with mock.patch.object(t.rtmod, "preflight", lambda p: dict(t.PREFLIGHT)), mock.patch.object(t.rtmod, "kerdoios_plan", lambda *a, **k: None), mock.patch.object(t.rtmod, "kerdoios_record", lambda **k: None):
    repo = t.git_repo(tmp / "repo")
    def spawn(argv, job):
        if mode == "inline":
            t.hc.record_opportunity("omp", job["payload"], job["ctx"])
        else:
            t.hc.spawn_detached(["cat"], job)
    sides = {"off": (tmp / "off", "0", t.BridgeRuntime(generation=1, instance_id="off", build_id="b", capture_spawn=spawn)),
             "on": (tmp / "on", None, t.BridgeRuntime(generation=1, instance_id="on", build_id="b", capture_spawn=spawn))}
    lat = {"off": [], "on": []}
    for i in range(200):
        for tag, (h, cap, rt) in sides.items():
            with t.z0home(h, Z0INT_CAPTURE=cap):
                t0 = time.perf_counter(); t.open_close(rt, f"{tag}-{i}", f"{tag}-s", cwd=str(repo)); lat[tag].append((time.perf_counter() - t0) * 1000)
    d = [a - b for a, b in zip(lat["on"], lat["off"])]
    q = lambda xs: [round(v, 1) for v in (statistics.median(xs), statistics.quantiles(xs, n=20)[18], max(xs))]
    print(mode, "off p50/p95/max", q(lat["off"]), "on", q(lat["on"]), "paired diff median", round(statistics.median(d), 2))
