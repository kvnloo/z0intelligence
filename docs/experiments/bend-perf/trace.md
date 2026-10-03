# Trace: "Bend gate 4-25x slower" and "bend_verify warm p50 140 ms"

Date: 2026-10-03. Read-only trace. No new timings were taken, so nothing here ran under quiet-timed.
Sources were read from git objects, committed files, run artifacts, gh (read), and the agentsview archive (opened read-only).

## TL;DR

1. **"4-25x slower" was not Bend interpreted vs Python.** The Bend AODL gate was **compiled to a native binary**: `bend main.bend -o build/gate`, which emits C and builds it with clang. It ran as a **persistent process** that took one request per stdin line, with `--threads 1`. The comparison side was CPython `validate()` called **in-process**, with no IPC.
2. The source is the README in z0intelligence `exp/bend-aodl-gate` @ `a0e95785` (`bend/aodl_gate/README.md`, "Latency" table), produced by `benchmarks/bend_gate_parity.py`. The raw `parity.json` was **never committed** and is not on this host. The measuring session is **not in the agentsview archive**. It ran on a different machine: the README says the CPU was an i7-4870HQ, but this host has an i9-10900KF. It also used a sandbox with `~/workspace/aodl`, a path that does not exist here.
3. The README table supports **~6x** (docs, kernel round-trip vs in-process validator, at both p50 and p95) and **~23-25x** (spawn transitions). The "4" lower bound matches no row in the frozen table. It is probably left over from an earlier noisy run (the README notes pytest was running concurrently), so treat it as unverified.
4. **"warm p50 140 ms" does not measure execution at all.** It is `hermes bend verify`, which runs `bend ./PROOF.bend --verdict`. That is **proof checking**: Bend's front-end check, then a recheck by the Lean-built BendTT kernel. It ran on a 3-law toy project (`double`), not the AODL gate. The 22.6 s "bootstrap" is the first call building the BendTT Lean kernel into a **per-session temporary HOME**.
5. **Triage verdict: not a Bend misconfiguration in the interpreted-vs-compiled sense.** The build used the fastest CPU mode the guide offers. The slowdown is structural:
   - per-call IPC (~70 us floor);
   - a host-side Python encode that costs about as much as the whole validator;
   - a kernel written as serial, linked-list, per-byte/per-token code, with no parallel calls and no GPU;
   - a tiny per-decision workload that leaves Bend's parallel runtime nothing to parallelize.

   Nothing live is slowed by Bend today: the gate is not wired into the controller, and `bend_verify` is a shadow-mode proof check with no Python counterpart. There is one real configurable cost: the **22.6 s cold BendTT bootstrap is paid once per Hermes session**, because bend-native deliberately points HOME at a temp dir.

## 1. Origin of "4-25x"

Chain of restatement:

- `cua-lanes/artifacts/bend-stack/SYNTHESIS.md` §7 ("Restated (SOURCE) ... 4-25x slower").
- Workflow agents `agent-aebb2b009fb0b4b49` and `agent-a72daa15924214ea8` (2026-10-03, parent session `5bc3424d`) restate "4–25x slower than in-process CPython validation" by grepping
  `/mnt/zer0models/cua-lane-tmp/aodl-verify-r1/z0src/bend/aodl_gate/README.md`.
- That README is identical to z0intelligence `a0e95785:bend/aodl_gate/README.md`, line 5:
  "it is 4–25x slower than the in-process CPython validator, so it gives no hot-path benefit."

Commits on `exp/bend-aodl-gate` (all authored 2026-09-30 01:14-01:52 -0500 as lesseradmin, Co-Authored-By Claude Opus 5.5):

| commit | content |
|---|---|
| 7841aff | kernel in Bend 2.0.34 plus a fail-closed host adapter (`src/z0int/bend_gate.py`) |
| eabab67 | byte-scanned U32 wire format; `benchmarks/bend_gate_parity.py` (records latency + RSS) |
| 0ff58cd | LAWS.bend / PROOF.bend, law-mutation suite |
| 06e1435 | transition fuzz, Nat cap 2^47, tests |
| a0e9578 | README with the frozen parity and latency results (kernel rev `fc2e56d486f6d546`, seed 48, `--fuzz 20000 --transitions 20000`) |

Where the measuring session is:

- z0int#48 has **0 comments**. Its body defines the promotion rule: "material latency/parallelism/verification benefit".
- The agentsview archive has **no** message or tool call in 2026-09-30 03:00-08:00Z mentioning bend, and no match for `fc2e56d486f6d546`, `BendGate`, `Bend process per decision` or `clang -O3 -march=native`. Raw logs under `~/.claude-home/projects`, `~/.codex/sessions` and `~/.hermes/sessions` hold only the later downstream readers.
- The local worktree was checked out detached at a0e9578 on 2026-10-02 21:59 -0500 (reflog), after the branch had been pushed from elsewhere.
- Conclusion: the run happened in a remote sandbox on an i7-4870HQ host, and its transcript and `parity.json` are not retrievable here.

### What was timed (`benchmarks/bend_gate_parity.py` @ a0e9578)

All timers are `time.perf_counter()` in the Python harness, one process.

| README row | code | what is inside the timer |
|---|---|---|
| CPython `validate()` in-process (p50 90 us / p95 598 us) | `t1-t0` around `python_verdict(doc)` | a direct call to canonical `aodl_contract` validate; no IPC; warm interpreter |
| host shape + encode alone (97 / 671 us) | `t2-t1` around `bg.encode_document(doc, CATALOG)` | the Python walk of the JSON: shape checks plus Core IR interning to U32 tokens |
| Bend kernel round-trip, docs (551 / 3,653 us) | `t3-t2` around `gate.raw(tokens)`, kernel-routed cases only | Python writes a token line to the pipe, then the **native compiled** Bend binary (persistent, `--threads 1`, warm) byte-scans, parses and decides, then prints a verdict, which Python reads |
| Bend process per decision, argv (2,867 / 10,249 us) | `subprocess.run([binary, "--threads","1", *tokens])` over the first `--spawn-sample` (default 200) cases | a fresh process per decision: exec + runtime init + argv `U32.read` + decide |
| Python spawn reference in-process (6.6 / 34 us) | `run_transitions`: `bg.reference_transition(p)` | a plain-Python transition check |
| Bend spawn round-trip (150 / 834 us) | `gate.raw(tokens)` on transition tokens | the same persistent-pipe path, on a much smaller request |

Other details:

- Binary: built by `build_kernel()`, which runs `$BEND_BIN or ~/.bend/bin/bend main.bend -o bend/aodl_gate/build/gate`, from the official installer (Bend 2.0.34).
- Per the guide, `-o <file>` with no extension produces a **native binary via clang**. That is the compiled path, not the interpreter (`bend file.bend` without `-o` checks and then runs).
- The README says rebuilding the emitted C with `clang -O3 -march=native` "gained nothing".
- The kernel's `main` returns `IO`, so even `bend main.bend` would "run compiled".
- Noise: "the full pytest suite ran concurrently for part of it". This was not quiet-lane.

### Ratios implied by the frozen table

| comparison | p50 | p95 |
|---|---|---|
| kernel round-trip / CPython validate | 6.1x | 6.1x |
| (encode + kernel) / CPython validate (estimated: the sum of p50s; the harness's own path_lat is not reported) | ~7.2x | — |
| Bend spawn round-trip / Python reference | 22.7x | 24.5x |
| argv process per decision / CPython validate | 32x | 17x |

The "25" matches the spawn p95. No row gives 4x, so the low end is unsupported by the published numbers.

### Why it is slow (the author's own profiling, from the README "Findings")

- The host must walk the JSON to shape-check and intern it, which costs about the same as the canonical validator. Bend can therefore never beat the validator at this boundary while the encoder stays in Python.
- Byte scanning plus parsing in the kernel alone took 0.9-2.4 ms per fixture, which is more than the whole CPython validator. The runtime spends about 1 us per token on linked-list and closure-heavy code.
- IPC floor: about 70 us.
- Code shape, which I verified in `bend/aodl_gate/*.bend`:
  - every collection is `List<...>`: 72 uses in rules.bend, 41 in core.bend;
  - stdin bytes become a `List<U32>` scanned one byte at a time;
  - there are **no parallel calls** (`a b = f(x) g(y)`) and no `!` GPU calls.

  With `--threads 1` and serial code, Bend's parallel runtime is unused. More threads would not help a single small decision.

## 2. What "warm p50 140 ms" measures

- Source: `cua-lanes/artifacts/bend-stack/e2e/bin/measure_kernel.py`, run by `bin/kernel_block.sh` as K-01..K-10 under quiet-timed. Results are in `e2e/runs/K-*/kernel.json`.
- Each K run is one Hermes Python process that loads the installed bend-native plugin (`plugin_load_ms` about 2 s). It then makes 1 cold + 10 warm calls of `registry.dispatch("bend_verify", {"project_dir": runs/K-nn/cwd})`, each timed with `perf_counter`.
- Project under test: `fixtures/bend-fix-solved`. That is impl.bend + LAWS.bend + PROOF.bend for `double(n)` with 3 laws. It is **not** the AODL gate. The AODL proof project was only run 3/3 for pass/fail receipts (§7), not timed.
- What one call does (bend-native `session_kernel.py`, `verify_core.py`):
  1. captures and hashes the import closure;
  2. materializes it into a temp dir;
  3. runs `bend ./PROOF.bend --verdict` as a **new subprocess** with `BEND_LIB` set and the hub disabled;
  4. builds a receipt.

  `--verdict` means: check the file, then recheck it with the proven BendTT kernel, which is a Lean 4.34.0-built checker. This is proof checking, not program execution.
- Cold vs warm:
  - **Cold (session-bootstrap):** the plugin sets `HOME` to a fresh `TemporaryDirectory(prefix="hermes-bend-kernel-")`, so Bend must build the BendTT kernel into `$HOME/.bend/bendtt/<sha16>/bendtt` through Lean. Median **22.6 s**.
  - **Warm (session-pinned):** the plugin reuses that kernel path and checks its SHA.
- Recomputed from the 10 kernel.json files:

  | measure | value |
  |---|---|
  | cold median | 22,627 ms |
  | warm, n=100, p50 | 140.0 ms |
  | receipt `duration_ms`, p50 | 111 ms |

  The difference of about 29 ms is plugin capture, hashing and receipt overhead. The 111 ms is the whole `bend --verdict` subprocess: process spawn, front-end check, translation, and the Lean kernel recheck.
- Python has no equivalent operation, so "Bend slower than Python" does not apply to this number.

## 3. Triage: is Bend misconfigured?

| hypothesis | status | evidence |
|---|---|---|
| The gate ran in Bend's interpreter instead of compiled | **No** | `bend main.bend -o build/gate` gives a native clang binary; `main` returns IO, so it runs compiled; `-O3 -march=native` rebuild gained nothing |
| Process spawned per decision | **No** for the headline numbers (persistent pipe). Yes only for the separate argv row (32x) | `BendGate._start`, `Popen` once |
| Wrong Bend generation (Bend 1 / HVM docs vs Bend 2) | Possible doc mismatch | The kernel targets Bend 2.0.34 (BendTT, a C/clang backend with a unified CPU/GPU heap). Bend 1 / HVM2 docs (`gen-c`, `run-c`, `run-cu`, interaction-net speedups) describe a different toolchain. 2.0.35 is available; this is untested |
| Bend's parallelism left on the table | **Yes, but by design of the workload** | `--threads 1`; no parallel calls in the kernel; a single small serial decision per request. README Gaps: "Concurrency/parallel batch throughput was not explored." This is the only regime where Bend could plausibly beat Python, and it was never measured |
| Data representation in the kernel | **Yes, likely a large factor** | per-byte `List<U32>` scanning and list-based rule scans (about 1 us/token). Bend 2 has in-place arrays (guide: `a[5] <- 42`), which the kernel does not use |
| Boundary design (Python encoder in front) | **Yes, a hard floor** | encode p50 97 us ≥ validator p50 90 us. No kernel speedup can win while the host re-walks the JSON in Python |
| Measurement noise | Yes | concurrent pytest, a different machine (i7-4870HQ), not quiet-lane |
| bend_verify cold cost | **A configurable cost** | the per-session temp HOME throws away the cached BendTT kernel, so every Hermes session pays about 22.6 s. Sharing a SHA-pinned kernel across sessions would remove it (it is a bend-native design choice made for isolation) |
| Bend causing a live slowdown anywhere | **No evidence** | the AODL gate is not in the controller path (README Gaps); bend_verify runs in shadow mode |

## 4. Suggested next measurements (not run; each needs the quiet lane)

1. Rebuild the gate at a0e95785 with the official 2.0.34 and the patched bend under `flock -s`. Rerun `bend_gate_parity.py --fuzz 2000` under `quiet-timed` on this host to get clean numbers, and save the `parity.json` this time.
2. Split the round-trip with a no-op Bend echo binary. This isolates the IPC floor from the scan+parse+decide cost.
3. Batch throughput: N requests per line or per pipe write, `--threads 1/4/8`. This is only meaningful after the kernel uses parallel calls over independent rules or documents.
4. bend_verify: time `bend PROOF.bend --verdict` directly with a pre-warmed shared HOME, to separate Lean recheck time from plugin overhead. Also time the AODL gate PROOF project, not only the `double` toy.

## Key paths

- `/mnt/zer0models/z0-wt/bend-aodl-gate/bend/aodl_gate/README.md` (source of the claim)
- `/mnt/zer0models/z0-wt/bend-aodl-gate/benchmarks/bend_gate_parity.py` (timers at lines ~537-583, ~713-725)
- `/mnt/zer0models/z0-wt/bend-aodl-gate/src/z0int/bend_gate.py` (`build_kernel`, `BendGate`, lines ~554-601)
- `/mnt/zer0models/github/cua-lanes/artifacts/bend-stack/e2e/bin/measure_kernel.py` and `e2e/runs/K-*/kernel.json`
- `/mnt/zer0models/z0-wt/ro/bend-native/session_kernel.py`, `verify_core.py` (`--verdict` command at ~line 520, `kernel_cache_identity` at line 252)
- `/mnt/zer0models/bend-stack/official/bend/guide/GUIDE.md` (lines 128-168 parallelism; 547-563 run modes; 639-650 runtime)
