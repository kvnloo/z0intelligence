"""Triage: where does the Bend AODL gate's time go, per run mode?

Builds the corpus the frozen parity harness builds (fixtures + targeted mutations + fuzz),
keeps the kernel-routed requests, and times the SAME request lines through:
  py      canonical CPython validate() in-process (the baseline Bend is compared against)
  enc     host shape-check + Core IR encode (Python; paid before Bend sees anything)
  rt      adapter round-trip, as shipped (BendGate.raw: thread spawn per call, one line per call)
  pipe    one line per call over the same pipe, plain blocking readline (no per-call thread)
  batch   all lines written at once to one native process, replies read back (amortized/decision)
  js      `bend main.bend` (the default run mode: checks, then runs IO main as JS in Bun), same batch
Usage: python3 bench_modes.py <gate-worktree> <native-binary> <bend> <n_fuzz>
"""
from __future__ import annotations

import json
import statistics
import subprocess
import sys
import threading
import time
from pathlib import Path

wt, binary, bend, n_fuzz = Path(sys.argv[1]), sys.argv[2], sys.argv[3], int(sys.argv[4])
sys.path[:0] = [str(wt / "src"), str(wt / "benchmarks")]
import bend_gate_parity as par  # noqa: E402
import z0int.bend_gate as bg  # noqa: E402
import copy, random  # noqa: E402

base = par.fixtures()
bases02 = [(n, d) for n, d in base if isinstance(d, dict) and d.get("specVersion") == "0.2"]
docs = [d for _, d in base]
for _, doc in bases02:
    for fn in par.targeted().values():
        d = copy.deepcopy(doc)
        try:
            fn(d)
        except (KeyError, IndexError, StopIteration, TypeError, AttributeError):
            continue
        docs.append(d)
rng = random.Random(7)
for i in range(n_fuzz):
    _, doc = rng.choice(bases02)
    docs.append(par.fuzz_mutate(doc, rng, rng.randint(1, 3)))

py_lat, enc_lat, lines, ntok = [], [], [], []
for doc in docs:
    t0 = time.perf_counter()
    par.python_verdict(doc)
    t1 = time.perf_counter()
    enc = bg.encode_document(doc, par.CATALOG)
    t2 = time.perf_counter()
    py_lat.append((t1 - t0) * 1e6)
    enc_lat.append((t2 - t1) * 1e6)
    if enc.representable:
        toks = enc.tokens or []
        lines.append((" ".join(str(t) for t in toks) + "\n").encode())
        ntok.append(len(toks))


def stats(xs):
    xs = sorted(xs)
    return {"n": len(xs), "p50": round(xs[len(xs) // 2], 1), "p95": round(xs[int(0.95 * len(xs))], 1),
            "mean": round(statistics.fmean(xs), 1)}


out = {"docs": len(docs), "kernel_routed": len(lines), "tokens": stats(ntok),
       "py_validate_us": stats(py_lat), "host_encode_us": stats(enc_lat)}

# rt: adapter as shipped
with bg.BendGate(binary, timeout_s=10.0, threads=1) as g:
    rt = []
    g.raw([0])  # warm the process
    for ln in lines:
        toks = ln.split()
        t0 = time.perf_counter(); g.raw(toks); rt.append((time.perf_counter() - t0) * 1e6)
out["adapter_roundtrip_us"] = stats(rt)

# pipe: same protocol, blocking readline, no per-call thread
p = subprocess.Popen([binary, "--threads", "1"], stdin=subprocess.PIPE, stdout=subprocess.PIPE, bufsize=0)
p.stdin.write(b"0\n"); p.stdout.readline()
pl, replies_pipe = [], []
for ln in lines:
    t0 = time.perf_counter(); p.stdin.write(ln); r = p.stdout.readline(); pl.append((time.perf_counter() - t0) * 1e6)
    replies_pipe.append(r)
p.stdin.close(); p.wait()
out["pipe_roundtrip_us"] = stats(pl)

blob = b"".join(lines)


def batch(cmd, label):
    t0 = time.perf_counter()
    r = subprocess.run(cmd, input=blob, capture_output=True, cwd=str(wt / "bend" / "aodl_gate"))
    dt = time.perf_counter() - t0
    got = r.stdout.splitlines()
    out[label] = {"rc": r.returncode, "wall_s": round(dt, 3), "per_decision_us": round(dt * 1e6 / len(lines), 1),
                  "replies": len(got), "agree_with_pipe": got == [x.rstrip(b"\n") for x in replies_pipe],
                  "user_cpu_s": None}


batch([binary, "--threads", "1"], "batch_native_t1")
batch([binary], "batch_native_default_threads")
batch([bend, "main.bend"], "batch_js_default_run")
# scan-only cost: same byte lengths, every line poisoned at its first byte, so decide() never runs
real_blob = blob
blob = b"".join(b"x" + ln[1:] for ln in lines)
batch([binary, "--threads", "1"], "batch_native_t1_scan_only")
blob = real_blob
out["bytes_per_line"] = stats([len(l) for l in lines])
# process start floor (empty stdin)
t = []
for _ in range(20):
    t0 = time.perf_counter(); subprocess.run([binary, "--threads", "1"], input=b"", capture_output=True)
    t.append((time.perf_counter() - t0) * 1e6)
out["native_process_start_us"] = stats(t)
print(json.dumps(out, indent=1))
