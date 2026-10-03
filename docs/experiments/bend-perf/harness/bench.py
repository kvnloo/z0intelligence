"""AODL gate decision benchmark: CPython validate() vs Bend configurations (see ../PREREG.md).

Usage: python3 bench.py <phase> [--smoke]
  phase: single | batch | cold | onetime
Each phase writes results/<phase>.json. Run timed phases only under quiet-timed (exclusive).
--smoke runs a tiny functional pass and prints NO timing (correctness only).
"""
from __future__ import annotations

import gc
import json
import multiprocessing as mp
import os
import resource
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from common import BIN, GATE_WT, ROOT, WORK, bg, load, par, stats

PHASE = sys.argv[1]
SMOKE = "--smoke" in sys.argv
BEND = "/mnt/zer0models/bend-stack/official/bend/bin/bend"
LEAN_BIN = "/mnt/zer0models/bend-stack/lean-4.34.0-linux/bin"
KDIR = GATE_WT / "bend" / "aodl_gate"
ENV = dict(os.environ, BEND_NO_TELEMETRY="1")
C = load()
CASES, ROWS, LINES = C["cases"], C["rows"], C["lines"]
REF = (WORK / "ref_replies.txt").read_bytes().splitlines()
LINES_FILE = WORK / "kernel_lines.txt"
KROUTED = {r["i"] for r in ROWS if r["route"] == "kernel"}
if SMOKE:
    CASES, ROWS = CASES[:300], ROWS[:300]
    KROUTED = {r["i"] for r in ROWS if r["route"] == "kernel"}


def host_info() -> dict:
    top = subprocess.run(["top", "-b", "-n", "1", "-o", "%CPU", "-w", "200"], capture_output=True, text=True).stdout
    procs = [ln.split() for ln in top.splitlines() if ln.strip()[:1].isdigit()]
    return {"loadavg": open("/proc/loadavg").read().split()[:3],
            "top_cpu_now": [f"{p[8]}% {p[11]}" for p in procs[:5] if len(p) > 11],
            "time_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}


def proc_cpu_s(pid: int) -> float:
    f = open(f"/proc/{pid}/stat").read().rsplit(")", 1)[1].split()
    return (int(f[11]) + int(f[12])) / os.sysconf("SC_CLK_TCK")


def proc_hwm_kb(pid: int) -> int | None:
    for ln in open(f"/proc/{pid}/status"):
        if ln.startswith("VmHWM:"):
            return int(ln.split()[1])
    return None


def reply_bytes(ok: bool, codes) -> bytes:
    return b"ALLOW" if ok else ("DENY " + " ".join(map(str, codes))).encode()


# --------------------------------------------------------------------------- single
class Pipe:
    """Plain blocking pipe to a persistent kernel process (no per-call thread)."""

    def __init__(self, binary, threads):
        args = [str(binary)] + (["--threads", str(threads)] if threads else [])
        self.p = subprocess.Popen(args, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                  bufsize=0)
        self.r = self.p.stdout

    def raw(self, tokens):
        self.p.stdin.write((" ".join(str(t) for t in tokens) + "\n").encode("ascii"))
        return bg.parse_reply(self.r.readline().decode("ascii"))

    @property
    def pid(self):
        return self.p.pid

    def close(self):
        self.p.stdin.close()
        self.p.wait()


class Adapter:
    def __init__(self, binary, threads):
        self.g = bg.BendGate(binary, timeout_s=30.0, threads=threads)
        self.g.raw([0])  # start the process

    def raw(self, tokens):
        return self.g.raw(tokens)

    @property
    def pid(self):
        return self.g._proc.pid  # noqa: SLF001

    def close(self):
        self.g.close()


def run_python(idx):
    lat = []
    c0 = time.process_time()
    for i in idx:
        doc = CASES[i][1]
        t0 = time.perf_counter()
        par.python_verdict(doc)
        lat.append((time.perf_counter() - t0) * 1e6)
    return lat, time.process_time() - c0


def run_bend(idx, gate):
    """End-to-end Bend path per document: host shape+encode, then the kernel if representable."""
    e2e, enc_only, kern = [], [], []
    bad = 0
    c0 = time.process_time()
    k0 = proc_cpu_s(gate.pid)
    for i in idx:
        doc = CASES[i][1]
        t0 = time.perf_counter()
        enc = bg.encode_document(doc, par.CATALOG)
        t1 = time.perf_counter()
        if enc.representable:
            ok, codes = gate.raw(enc.tokens or [])
            t2 = time.perf_counter()
            kern.append((t2 - t1) * 1e6)
            if reply_bytes(ok, codes) != REF[ROWS[i]["line_index"]]:
                bad += 1
        else:
            t2 = t1
        e2e.append((t2 - t0) * 1e6)
        enc_only.append((t1 - t0) * 1e6)
    return {"e2e": e2e, "enc": enc_only, "kern": kern, "parent_cpu_s": time.process_time() - c0,
            "kernel_cpu_s": proc_cpu_s(gate.pid) - k0, "mismatches": bad}


SINGLE_BEND = {
    "B_adapter_ref_t1": (Adapter, "gate_ref", 1),
    "C1_pipe_ref_t1": (Pipe, "gate_ref", 1),
    "C2_pipe_ref_default_threads": (Pipe, "gate_ref", None),
    "C3_pipe_ref_native_t1": (Pipe, "gate_ref_native", 1),
    "C5_pipe_par_t1": (Pipe, "gate_par", 1),
}


def phase_single():
    idx_all = [r["i"] for r in ROWS]
    warm = idx_all[:500] if not SMOKE else idx_all[:20]
    names = ["A_python"] + list(SINGLE_BEND)
    reps = 1 if SMOKE else 3
    out = {"host": host_info(), "repeats": []}
    for rep in range(reps):
        order = names[rep % len(names):] + names[: rep % len(names)]
        res = {}
        for name in order:
            gc.collect()
            if name == "A_python":
                run_python(warm)
                lat, cpu = run_python(idx_all)
                res[name] = {"all": stats(lat), "kernel_routed": stats([x for i, x in zip(idx_all, lat) if i in KROUTED]),
                             "cpu_us_per_decision": round(cpu * 1e6 / len(idx_all), 2)}
                continue
            cls, binname, thr = SINGLE_BEND[name]
            gate = cls(BIN / binname, thr)
            run_bend(warm, gate)
            r = run_bend(idx_all, gate)
            hwm = proc_hwm_kb(gate.pid)
            gate.close()
            ke2e = [x for i, x in zip(idx_all, r["e2e"]) if i in KROUTED]
            kenc = [x for i, x in zip(idx_all, r["enc"]) if i in KROUTED]
            res[name] = {"all_e2e": stats(r["e2e"]), "kernel_routed_e2e": stats(ke2e),
                         "kernel_routed_encode": stats(kenc), "kernel_roundtrip": stats(r["kern"]),
                         "host_cpu_us_per_decision": round(r["parent_cpu_s"] * 1e6 / len(idx_all), 2),
                         "kernel_cpu_us_per_kernel_decision": round(r["kernel_cpu_s"] * 1e6 / max(1, len(r["kern"])), 2),
                         "kernel_peak_rss_kb": hwm, "mismatches_vs_reference": r["mismatches"]}
        out["repeats"].append({"order": order, "results": res})
    out["host_end"] = host_info()
    out["python_harness_maxrss_kb"] = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return out


# --------------------------------------------------------------------------- batch
def run_batch_file(cmd, cwd=None, pipe_feed=False):
    blob = LINES_FILE.read_bytes() if pipe_feed else None
    t0 = time.perf_counter()
    if pipe_feed:
        p = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, cwd=cwd,
                             env=ENV)
        import threading
        w = threading.Thread(target=lambda: (p.stdin.write(blob), p.stdin.close()))
        w.start()
        data = p.stdout.read()
        w.join()
    else:
        with open(LINES_FILE, "rb") as f:
            p = subprocess.Popen(cmd, stdin=f, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, cwd=cwd, env=ENV)
            data = p.stdout.read()
    _, status, ru = os.wait4(p.pid, 0)
    p.returncode = os.waitstatus_to_exitcode(status)
    wall = time.perf_counter() - t0
    got = data.splitlines()
    n = len(LINES)
    return {"rc": p.returncode, "wall_s": round(wall, 4), "wall_us_per_decision": round(wall * 1e6 / n, 2),
            "decisions_per_s": round(n / wall, 1), "cpu_us_per_decision": round((ru.ru_utime + ru.ru_stime) * 1e6 / n, 2),
            "user_s": round(ru.ru_utime, 3), "sys_s": round(ru.ru_stime, 3), "peak_rss_kb": ru.ru_maxrss,
            "identical_to_reference": got == REF}


_POOL_IDX: list[int] = []


def _mp_chunk(chunk):
    for i in chunk:
        par.python_verdict(CASES[i][1])
    return len(chunk)


def run_py_batch(idx):
    gc.collect()
    c0 = time.process_time()
    t0 = time.perf_counter()
    for i in idx:
        par.python_verdict(CASES[i][1])
    wall = time.perf_counter() - t0
    cpu = time.process_time() - c0
    return {"n": len(idx), "wall_s": round(wall, 4), "wall_us_per_decision": round(wall * 1e6 / len(idx), 2),
            "decisions_per_s": round(len(idx) / wall, 1), "cpu_us_per_decision": round(cpu * 1e6 / len(idx), 2)}


def run_encode_batch(idx):
    t0 = time.perf_counter()
    for i in idx:
        bg.encode_document(CASES[i][1], par.CATALOG)
    wall = time.perf_counter() - t0
    return {"n": len(idx), "wall_us_per_decision": round(wall * 1e6 / len(idx), 2), "decisions_per_s": round(len(idx) / wall, 1)}


def run_py_mp(pool, idx, nproc):
    chunks = [idx[k:k + 100] for k in range(0, len(idx), 100)]
    t0 = time.perf_counter()
    done = sum(pool.imap_unordered(_mp_chunk, chunks))
    wall = time.perf_counter() - t0
    assert done == len(idx)
    return {"n": len(idx), "procs": nproc, "wall_s": round(wall, 4), "wall_us_per_decision": round(wall * 1e6 / len(idx), 2),
            "decisions_per_s": round(len(idx) / wall, 1)}


BATCH_BEND = {
    "C4_batch_ref_t1": ([str(BIN / "gate_ref"), "--threads", "1"], None, False),
    "C4_batch_ref_default_threads": ([str(BIN / "gate_ref")], None, False),
    "C4_batch_ref_native_t1": ([str(BIN / "gate_ref_native"), "--threads", "1"], None, False),
    "C4_batch_ref_t1_pipefed": ([str(BIN / "gate_ref"), "--threads", "1"], None, True),
    "C5_batch_par_t1": ([str(BIN / "gate_par"), "--threads", "1"], None, False),
    "C5_batch_par_t2": ([str(BIN / "gate_par"), "--threads", "2"], None, False),
    "C5_batch_par_t4": ([str(BIN / "gate_par"), "--threads", "4"], None, False),
    "C5_batch_par_t10": ([str(BIN / "gate_par"), "--threads", "10"], None, False),
    "C5_batch_par_default_threads": ([str(BIN / "gate_par")], None, False),
    "C5_batch_par_default_threads_pipefed": ([str(BIN / "gate_par")], None, True),
    "D_js_run_mode": ([BEND, "main.bend"], str(KDIR), False),
}


def phase_batch():
    idx_all = [r["i"] for r in ROWS]
    idx_k = [r["i"] for r in ROWS if r["route"] == "kernel"]
    out = {"host": host_info(), "n_kernel_lines": len(LINES), "repeats": []}
    reps = 1 if SMOKE else 3
    ctx = mp.get_context("fork")
    with ctx.Pool(10) as pool:
        run_py_mp(pool, idx_k[:2000], 10)  # warm the workers
        for rep in range(reps):
            res = {}
            run_py_batch(idx_k[:500])
            res["A_python_batch_kernel_routed"] = run_py_batch(idx_k)
            res["A_python_batch_all"] = run_py_batch(idx_all)
            res["A_mp10_batch_kernel_routed"] = run_py_mp(pool, idx_k, 10)
            res["encode_batch_kernel_routed"] = run_encode_batch(idx_k)
            for name, (cmd, cwd, pf) in BATCH_BEND.items():
                if SMOKE and name == "D_js_run_mode":
                    continue
                res[name] = run_batch_file(cmd, cwd, pf)
            out["repeats"].append(res)
    # process start floor
    t = []
    for _ in range(20):
        t0 = time.perf_counter()
        subprocess.run([str(BIN / "gate_ref"), "--threads", "1"], input=b"", capture_output=True)
        t.append((time.perf_counter() - t0) * 1e6)
    out["native_process_start_empty_stdin_us"] = stats(t)
    out["host_end"] = host_info()
    return out


# --------------------------------------------------------------------------- cold
PY_COLD = r"""
import json, sys, time, resource
t0 = time.perf_counter()
sys.path.insert(0, %(ref)r)
from aodl_contract import validate
t1 = time.perf_counter()
doc = json.loads(open(%(doc)r).read())
t2 = time.perf_counter()
try: validate(doc)
except Exception: pass
t3 = time.perf_counter()
try: validate(doc)
except Exception: pass
t4 = time.perf_counter()
print(json.dumps({"import_us": (t1-t0)*1e6, "first_validate_us": (t3-t2)*1e6, "second_validate_us": (t4-t3)*1e6,
                  "maxrss_kb": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss}))
"""


def phase_cold():
    out = {"host": host_info()}
    reps = 2 if SMOKE else 10
    # pick the first kernel-routed fixture document
    fx = next(r for r in ROWS if r["kind"] == "fixture" and r["route"] == "kernel")
    docfile = WORK / "cold_doc.json"
    docfile.write_text(json.dumps(CASES[fx["i"]][1]))
    tokens = bg.encode_document(CASES[fx["i"]][1], par.CATALOG).tokens
    out["doc"] = fx["case"]
    code = PY_COLD % {"ref": str(GATE_WT / "third_party" / "aodl_a848270"), "doc": str(docfile)}
    py = []
    for _ in range(reps):
        t0 = time.perf_counter()
        r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True)
        d = json.loads(r.stdout)
        d["process_total_us"] = (time.perf_counter() - t0) * 1e6
        py.append(d)
    out["A_python_cold"] = {k: stats([x[k] for x in py]) for k in py[0]}
    # Bend: spawn + first request + second request on a persistent process
    bc = []
    line = (" ".join(map(str, tokens)) + "\n").encode()
    for _ in range(reps):
        t0 = time.perf_counter()
        p = subprocess.Popen([str(BIN / "gate_ref"), "--threads", "1"], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                             bufsize=0)
        p.stdin.write(line)
        r1 = p.stdout.readline()
        t1 = time.perf_counter()
        p.stdin.write(line)
        r2 = p.stdout.readline()
        t2 = time.perf_counter()
        hwm = proc_hwm_kb(p.pid)
        p.stdin.close()
        p.wait()
        assert r1 == r2 and r1.rstrip(b"\n") == REF[fx["line_index"]]
        bc.append({"spawn_plus_first_reply_us": (t1 - t0) * 1e6, "second_reply_us": (t2 - t1) * 1e6, "peak_rss_kb": hwm})
    out["B_bend_cold_persistent"] = {k: stats([x[k] for x in bc]) for k in bc[0]}
    # B-argv: one process per decision (README row), first 200 kernel-routed docs
    lat = []
    sample = [r for r in ROWS if r["route"] == "kernel"][: (10 if SMOKE else 200)]
    bad = 0
    for r in sample:
        toks = bg.encode_document(CASES[r["i"]][1], par.CATALOG).tokens or []
        t0 = time.perf_counter()
        pr = subprocess.run([str(BIN / "gate_ref"), "--threads", "1", *map(str, toks)], capture_output=True)
        lat.append((time.perf_counter() - t0) * 1e6)
        bad += pr.stdout.strip() != REF[r["line_index"]]
    out["B_argv_process_per_decision_us"] = stats(lat)
    out["B_argv_mismatches"] = bad
    out["host_end"] = host_info()
    return out


# --------------------------------------------------------------------------- one-time
def timed(cmd, cwd=None, env=None, timeout=420):
    t0 = time.perf_counter()
    r = subprocess.run(cmd, cwd=cwd, env=env or ENV, capture_output=True, text=True, timeout=timeout)
    return {"cmd": " ".join(map(str, cmd))[-160:], "rc": r.returncode, "wall_s": round(time.perf_counter() - t0, 3),
            "tail": (r.stdout + r.stderr).strip().splitlines()[-2:]}


def phase_onetime():
    out = {"host": host_info()}
    od = ROOT / "bin" / "onetime"
    od.mkdir(exist_ok=True)
    reps = 1 if SMOKE else 3
    out["bend_build_native"] = [timed([BEND, "main.bend", "-o", str(od / f"gate{k}")], cwd=str(KDIR)) for k in range(reps)]
    out["bend_emit_c"] = timed([BEND, "main.bend", "-o", str(od / "gate.c")], cwd=str(KDIR))
    out["clang_O3_only"] = timed(["clang", "-std=c11", "-O3", str(od / "gate.c"), "-lpthread", "-lm", "-o", str(od / "gate_cc")])
    out["check_only_main"] = [timed([BEND, "main.bend", "--check-only"], cwd=str(KDIR)) for _ in range(reps)]
    out["proof_check"] = [timed([BEND, "PROOF.bend"], cwd=str(KDIR)) for _ in range(reps)]
    if not SMOKE:
        home = ROOT / "home" / "cold"
        shutil.rmtree(home, ignore_errors=True)
        home.mkdir(parents=True)
        env = dict(ENV, HOME=str(home), PATH=LEAN_BIN + ":" + ENV["PATH"])
        out["proof_verdict_cold_kernel"] = timed([BEND, "PROOF.bend", "--verdict"], cwd=str(KDIR), env=env)
        out["proof_verdict_warm_kernel"] = [timed([BEND, "PROOF.bend", "--verdict"], cwd=str(KDIR), env=env)
                                            for _ in range(reps)]
    out["host_end"] = host_info()
    return out


if __name__ == "__main__":
    fn = {"single": phase_single, "batch": phase_batch, "cold": phase_cold, "onetime": phase_onetime}[PHASE]
    res = fn()
    res.update({"phase": PHASE, "smoke": SMOKE, "corpus_sha256": C["sha"], "python": sys.version.split()[0]})
    if SMOKE:
        def scrub(o):
            if isinstance(o, dict):
                return {k: scrub(v) for k, v in o.items() if k in ("identical_to_reference", "mismatches_vs_reference",
                                                                   "rc", "B_argv_mismatches", "repeats", "results",
                                                                   "order", "tail") or isinstance(v, (dict, list))}
            if isinstance(o, list):
                return [scrub(x) for x in o]
            return o
        print(json.dumps(scrub(res), indent=None)[:4000])
    else:
        (ROOT / "results" / f"{PHASE}.json").write_text(json.dumps(res, indent=1) + "\n")
        print(f"wrote results/{PHASE}.json")
