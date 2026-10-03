# PREREG: fair benchmark of the AODL gate decision, CPython vs Bend (bend-perf triage)

Date: 2026-10-03. Committed before any timed run. Owner question: "Bend should be much faster than
Python for how we use it; is it misconfigured?" Inputs: `trace.md`, `docs-audit.md` (this repo).

## Subject and fixed inputs

- Gate: z0intelligence `exp/bend-aodl-gate` @ a0e95785 (my worktree `wt/gate`, read-only use);
  kernel revision `fc2e56d486f6d546`; AODL reference kvnloo/aodl@a848270 (vendored).
- Toolchain: official Bend 2.0.34 (`/mnt/zer0models/bend-stack/official/bend/bin/bend`, byte-identical
  to `~/.bend/bin/bend` that the gate README used), clang 22.1.8, CPython 3.14.7.
- Host: i9-10900KF (10 cores / 20 threads), 23 GB RAM. The original measurement was on an i7-4870HQ,
  so absolute numbers are not comparable; ratios are.
- Corpus: the frozen parity corpus rebuilt exactly as `benchmarks/bend_gate_parity.py run()` builds it
  (seed 48, fuzz 20000): 22,079 cases, corpus_sha256
  `0e415e9ee987f96ef52b6e778e1c7aa689f7127581aefb04d4f55b387493fe0d`; 10,019 cases reach the kernel,
  12,059 are refused by the host shape check, 1 unsupported (hotl-0.1).

## Parity gate (done before timing; `harness/parity.py`, `results/parity.json`)

Reference B reproduces the README parity: 0 false allows, 1 false deny (hotl-0.1-fanin, explained),
0 code-multiset disagreements on 9,519 kernel-routed cases without a validator exception. Every other
Bend configuration must return byte-identical reply lines on all 10,019 kernel-routed requests, or it
is excluded from timing. All configurations below passed.

## Configurations

- **A** CPython canonical `validate()` in-process (`python_verdict`, exception = reject).
- **A-mp10** A over a fork `multiprocessing.Pool(10)` (throughput only; the fair counterpart to Bend's
  multi-threaded runtime).
- **B** as originally measured: native binary from `bend main.bend -o gate` (clang -std=c11 -O3),
  `--threads 1`, persistent process, one request line per call through the shipped `BendGate` adapter
  (thread per call), host shape check + encode in Python before each call. Also B-argv: one process per
  decision with tokens in argv (README row "process per decision").
- **C1** B's binary, plain blocking pipe (no per-call thread).
- **C2** B's binary with the documented default thread count (no `--threads`, = 20 online CPUs).
- **C3** B's C rebuilt with `clang -O3 -march=native` (README says no gain; re-checked).
- **C4** batched input: all 10,019 kernel-routed request lines in one stdin file to one process
  (kernel-only throughput), for the serial binary (t1, default threads, march=native).
- **C5** parallel runtime: `kernels/par/main_par.bend`, the same pure `gate.bend`/`core`/`rules`, but
  each stdin chunk's lines are decided with Bend's fork-join parallel call over a balanced split
  (2^7 leaves). Threads 1, 2, 4, 10, 20 (default). Batched from file, and single-request over the pipe.
- **D** (slow-mode reference, not a candidate) `bend main.bend` = check + run IO main as JS in Bun.
- **GPU**: not runnable here (`/usr/local/cuda/include/nvrtc.h` absent, so `bend` builds CPU-only; the
  gate has no `!` calls). Recorded as not measured.
- **Bend 2.0.35**: not installed; not measured.

Excluded as not semantics-preserving configuration: rewriting the wire format or data structures
(arrays, binary tokens), moving JSON parsing into Bend (2.0.34 has no JSON reader).

## Metrics

Per-decision latency (warm, single request): wall µs per document, p50/p95/p99/mean, measured on (i)
all 22,079 cases and (ii) the 10,019 kernel-routed subset (primary). Bend single-request rows are
end-to-end (host shape+encode + kernel round trip); the kernel round trip alone is also reported.
Throughput (batched): decisions/s and wall µs/decision over the kernel-routed set; CPU µs/decision
(user+sys of the deciding process(es)); peak RSS. Cold: fresh-process first decision (Python: interpreter
start + import + first validate; Bend: spawn + first reply). One-time: `bend -o` build, `--check-only`,
`PROOF.bend` check, `--verdict` with a cold and a warm BendTT kernel cache.

## Procedure

Every timed block runs under `/mnt/zer0models/github/cua-lanes/bin/quiet-timed <label>` (exclusive
quiet-lane lock), ≤10 min each: `single` (3 repeats, config order rotated each repeat, 500-doc warm-up per
config), `batch` (3 repeats), `cold`, `onetime`. Load average and top CPU processes are recorded at the
start of each block. A block whose 1-min load average at start exceeds 3.0 is flagged noisy and rerun
once if time allows; both are reported. Reported value = median across repeats of each repeat's
statistic; per-repeat values are kept in the raw JSON. Every timed Bend run re-checks its reply lines
against the parity reference; a mismatch voids that row.

## Predictions (written before timing)

- P1 (no misconfiguration): no configuration in C1-C5 brings Bend's single-request end-to-end p50 on the
  kernel-routed subset to or below A's p50. Expected ratio B/A on this host: 3-10x at p50.
- P2: host shape+encode alone costs ≥ 0.7x of A's validate on the kernel-routed subset, so the
  end-to-end Bend path cannot beat A even with a zero-cost kernel.
- P3: C1 (no per-call thread) saves ≤ 40 µs p50 vs B.
- P4: C2 (default 20 threads on the serial kernel) is no faster than t1 (within ±10%, or slower).
- P5: C3 (-march=native) is within ±5% of B.
- P6: C4 batching lowers kernel µs/decision vs the single-request pipe (no per-request IPC) but the
  serial kernel's CPU µs/decision stays above A's.
- P7: C5 scales with threads: 10 threads give ≥ 3x the t1 batch throughput. Kernel-only C5 at 10-20
  threads may exceed single-core A throughput; A-mp10 also scales, so A-mp10 vs C5-t10 is the fair
  throughput comparison, and the Bend end-to-end throughput stays bounded by the single-core Python
  encode unless the encode is also parallelised.
- P8: D (JS run mode) is ≥ 3x slower than C4-t1.
- P9: one-time: `bend -o` build ≈ 4 s; cold `--verdict` (BendTT kernel build) ≥ 15 s; warm ≤ 3 s.

## Decision rule

"Misconfigured" is declared only if a configuration-only change (C1-C4: flags, threads, compiler
options, batching, process reuse; no change to the Bend program) makes Bend's per-decision cost at the
same boundary ≤ A's, or improves B by ≥ 2x at the same boundary. C5 is a program change (parallel call
added; verdict code unchanged) and is reported separately as "what Bend's parallel runtime can do".
