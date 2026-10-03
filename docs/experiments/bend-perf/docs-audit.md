# Bend configuration and usage vs the Bend docs (triage)

Date: 2026-10-03. Scope: the owner's question "is Bend misconfigured, since it should be much faster than Python?"
Toolchain audited: official Bend 2.0.34 (`/mnt/zer0models/bend-stack/official/bend/bin/bend`, a Bun-compiled
JS bundle; a 2.0.35 is advertised, not installed). Sources: `bend --help`, `bend guide` (the 2.0.34 docs,
saved as `guide.txt` here), the bundle's own JS (`bend-strings.txt` here), upstream README
github.com/bendlang/bend, the AODL gate at z0intelligence `exp/bend-aodl-gate` @ a0e95785 (my own
worktree `wt/gate`), and bend-native e85e65e5 (read-only).

## Bottom line

- **The "4-25x slower than Python" result is not caused by misconfiguration.** The AODL gate already
  uses Bend's documented fast path: a native binary built with `bend main.bend -o gate`
  (clang `-O3`), kept alive as one process, fed over a pipe. It does **not** use the slow modes: the
  checker normalising a value `main`, or `bend file.bend`, which runs IO through JavaScript in Bun.
- **The slowdown comes from the workload, not from Bend's setup.** Each decision is about 300 small
  tokens of branchy, linked-list work. Python's validator finishes it in tens of µs. Before Bend
  sees a request, Python has to walk the same JSON to encode it, which costs about as much as
  validating it outright. Bend's speed claims ("as fast as C", parallel fork-join, GPU) only pay off
  on large, balanced, parallel workloads. For a per-request gate at this size, Bend can't beat an
  in-process Python validator.
- **`bend_verify` does a different job.** It checks proofs (`bend PROOF.bend --verdict`); it does not
  run a decision function. Python has no equivalent, so "faster than Python" doesn't apply. Its one
  real self-inflicted cost is the 22.6 s cold kernel bootstrap (M1 below), which can be avoided.

## 1. What each consumer asks Bend to do

| consumer | Bend command | what Bend does | run mode |
| --- | --- | --- | --- |
| AODL gate, build | `bend main.bend -o <bin>` (`src/z0int/bend_gate.py:554-562`) | checks, emits C, `clang -std=c11 -O3 file.c -lpthread -lm` | native C (CPU; `#define BANGS 0`, so no GPU program) |
| AODL gate, decisions | `<bin> --threads 1`, one line of decimal U32 tokens per request, persistent pipe (`bend_gate.py:594-648`) | runs `G.decide` per line; byte scanner in `main.bend` | native C, long-lived process |
| AODL gate, laws | `bend PROOF.bend` (+ `--verdict`) in CI/benchmarks only | proof checking of `LAWS.bend` | checker (Bun) + Lean kernel |
| bend-native `bend_verify` | `bend ./PROOF.bend --verdict` per call (`verify_core.py:520`) | bend2 check, then a recheck by the Lean-built BendTT kernel | checker (Bun JS) + native kernel; no user code runs |

## 2. Run modes in 2.0.34 (from `bend --help`, `bend guide` § Tooling, and the bundle source)

| invocation | what actually runs | speed per the docs and source |
| --- | --- | --- |
| `bend f.bend`, `main` returns a value | checker normalisation (`term_snf`) | docs: "normalized by the checker (slow for big work)" |
| `bend f.bend`, `main` returns IO | `io_run` builds JS (`js_lib(book)`) and runs it in Bun (`new Function`) | upstream: "The JavaScript target runs on one core"; it also re-checks on every invocation |
| `bend f.bend -o f` | emits C, then clang `-O3` (+ CUDA via NVRTC if the program uses `!` and `/usr/local/cuda/include/nvrtc.h` exists) | the documented fast path; `--threads N` defaults to `sysconf(_SC_NPROCESSORS_ONLN)`; `--gpu on/off/<size>` |
| `bend f.bend -o f.c / .js / .mjs / .bendtt` | emit source only | n/a |
| `--check-only` / `--verdict` | check only / check, then run the Lean-proven BendTT kernel | the kernel is built once per `$HOME` (below) |

Parallelism in Bend is explicit. Only parallel calls (`a b = f(x) g(y)`) are spread across threads,
and `f!(x)` sends work to the GPU. Plain sequential code gains nothing from `--threads`.

## 3. Findings: concrete issues, with evidence

**M1. `bend_verify` rebuilds the BendTT kernel in every new Hermes session (real, avoidable).**
Bend caches the kernel at `$HOME/.bend/bendtt/<sha256(bendtt.lean)[:16]>/bendtt`. A miss runs
`lean -c` and then `leanc -O3` (bundle `kernel_bin()`). `session_kernel.py` sets `HOME` to a fresh
`tempfile.TemporaryDirectory("hermes-bend-kernel-")` for each profile and session, so the first verify
in every session pays the Lean build. That build is the 22.6 s median "bootstrap" in SYNTHESIS §6.
The plugin can already pin a kernel by path and sha256 (`BENDTT`, `kernel_override` in
`verify_core.py:469-486`). Fix: build the kernel once per Bend and Lean identity, at install or doctor
time, into a read-only, hash-pinned location, and start each session on the `session-pinned` path.
This keeps the isolation and removes the 22.6 s. The 120 s cold timeout (`_KERNEL_BOOTSTRAP_TIMEOUT_SECONDS`)
is there only because of this rebuild.

**M2. Python has to pre-parse the JSON, so Bend can never beat Python at this boundary (design, not a flag).**
Bend 2.0.34 has no JSON reader and no stdin effect (gate README, Findings 7). So `bend_gate.py` walks
the document, shape-checks it, interns strings, and emits decimal U32 tokens. The gate README
measured that encoding step alone at p50 97 µs, more than the whole CPython `validate()` at 90 µs.
Even if Bend took zero time, the Bend path would still be slower than Python. No Bend setting can
change that.

**M3. Text wire format scanned one byte at a time over linked lists (the kernel's own cost).**
`main.bend` reads `/dev/stdin` with `File.read_bytes(f, 1048576)`, which returns a `List<U32>` of
bytes, one cons cell per byte. It then classifies each byte through a chain of matches, builds a token
list, reverses it, and parses it with a closure-based parser monad (`core.bend`). The README measured
"~1 µs per token" and "byte scanning plus parsing alone takes 0.9-2.4 ms per fixture". This is the
access pattern that Bend's runtime notes (affine heap, no GC, fork-join scheduler) are not built to
make fast. The README found that rebuilding with `-O3 -march=native` gained nothing, which fits: Bend
already passes `-O3`. A binary or array wire format would cut this cost, but it can't fix M2.

**M4. The parallel runtime is unused, and the CUDA path is unreachable here (expected, not a fix).**
The adapter starts the binary with `--threads 1`, and the kernel has no parallel calls, so extra
threads have no work to share. The gate README lists "all evaluation is on the CPU; the GPU is unused"
and "Concurrency/parallel batch throughput was not explored". On this host (RTX 3080 Ti) the CUDA
toolkit's `/usr/local/cuda/include/nvrtc.h` is absent, so Bend would build CPU-only even with `!`.
The docs say divergent work such as validation "stays faster on the CPU". The workload that would use
the parallel runtime is a batch of documents decided in one parallel fold: many requests per write,
with a `!`/fork-join split over the batch. That serves an offline throughput use (CI differential
fuzz, corpus replay), not per-request latency.

**M5. The headline ratio was measured on a different machine and is noisy (evidence quality).**
The gate README's latency table says "Intel i7-4870HQ, 8 threads … the full pytest suite ran
concurrently for part of it, so absolute numbers are noisy". This host has 10 cores. "4-25x" is
roughly p50 551/90 = 6x up to the p95 tail, round trip vs in-process. It mixes IPC, host encoding and
kernel time. A same-host breakdown is prepared (`bench_modes.py`, section 5) but has not run yet.

**M6. Minor adapter overhead.** `BendGate._read_line` starts a new `threading.Thread` for every
decision to enforce the timeout (`bend_gate.py:619-632`). A `select`/`poll` deadline on the stdout fd
would remove one thread per call. This is µs-scale and does not change the conclusion.

**Not misconfigured (checked):** builds use clang `-O3` (bundle `cli_build`). Nothing in our stack calls
`bend file.bend` on the hot path: that would be the checker or the single-core JS mode, roughly 4-5x
slower than the native binary on the same batch in an unofficial smoke run. The gate keeps its process
alive instead of spawning per decision; the README measured one-shot spawn at p50 2.9 ms. Bend 2.0.34
is not Bend 1/HVM, so HVM docs and flags (`run-c`, `run-cu`, `gen-c`, `-s`) do not apply to this
toolchain.

## 4. Does our usage match "how we are trying to use it"?

Bend is used in two ways:
1. **As a proof checker** (LAWS/PROOF, `--verdict`). This is Bend 2's headline feature, and it fits.
   Speed is not measured against Python here, and the only real cost is M1.
2. **As a per-request policy decision function** that replaces or shadows a Python validator. The docs
   do not claim this case gets faster. A small, branchy, sequential function behind a pipe, with Python
   pre-parsing the input, will not be faster than in-process Python, whatever the flags. The gate
   README's recommendation stands: keep the Python validator or `reference_transition` on the hot path,
   and use the Bend kernel for proven-spec checks plus CI differential fuzz. If speed matters there,
   make it a batch or throughput job (M4).

## 5. Same-host measurement (prepared, NOT yet run)

`bench_modes.py` times the same kernel-routed request lines through: CPython `validate()`, host encode,
the adapter round trip, a plain pipe round trip, a batched native run (`--threads 1` and default), the
`bend main.bend` JS mode, a scan-only control (poisoned lines, so `decide` never runs), and native
process start. Run it under the exclusive lock:

```
cd /mnt/zer0models/z0-wt/wiring/bend-perf && D=$PWD
/mnt/zer0models/github/cua-lanes/bin/quiet-timed bend-perf-modes-r1 timeout 540 \
  python3 bench_modes.py $D/wt/gate $D/build/gate /mnt/zer0models/bend-stack/official/bend/bin/bend 3000 \
  > results/modes-r1.json
```

Status: queued 11:53 to about 13:00 local and never got the exclusive lock, because a steady stream of
shared holders kept it busy. Another exclusive waiter, r207g-T1, was also starved for over 90 min.
I cancelled it, so this audit has **no valid timing numbers of its own**. One earlier functional smoke
of this script ran under the shared lock only. Its timings are not evidence and are not quoted as such
above, except for the "roughly 4-5x" JS-vs-native ordering, which is labelled unofficial.

## Recommended actions

1. M1: pre-build and hash-pin the BendTT kernel once per toolchain identity; start sessions pinned
   (bend-native fix, issue on the fork).
2. Keep Bend off the AODL hot path (no change). Record that the cause is the workload shape (M2/M3), not
   configuration.
3. If Bend gate speed still matters: run section 5 under the exclusive lock, then try a batched
   parallel-fold kernel for offline corpus or CI throughput (M4) before any binary wire format (M3).
