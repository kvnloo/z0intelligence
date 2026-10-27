# Bend AODL structural gate (experiment)

z0intelligence#48, kvnloo/aodl#19, z0intelligence#13. **Status: experimental.
Keep it that way.** Parity with the canonical validator is exact on everything
the kernel models. Eight laws are machine-checked. But it is 4–25x slower than
the in-process CPython validator, so it gives no hot-path benefit.

```
AODL JSON ──► z0int.bend_gate (host, Python)            ──► Bend kernel (pure)     ──► ALLOW | DENY <codes>
              shape checks + Core IR interning                 rules.bend / transition.bend
              (refuses what it cannot represent)               laws proven in PROOF.bend
                         │ any error / timeout / garbage / kernel abort ─► DENY
                         └ optional: canonical validate() disagrees     ─► DENY
```

Built against Bend **2.0.34** (BendTT, an affine dependent type theory with
laws and proofs). This is not Bend 1 / HVM. The AODL reference is frozen at
`kvnloo/aodl@a848270` in `third_party/aodl_a848270/`.

## Files

| file | role |
| --- | --- |
| `core.bend` | Core IR types and the token parser (a parser monad over U32 tokens) |
| `rules.bend` | the structural rules of `validate_02`, codes 1–18 |
| `transition.bend` | the spawn transition gate (#13): revision pin, dynamic spawn, maxChildren/maxDepth, exact-Nat budgets, authority ceiling |
| `gate.bend` | pure `decide(tokens) -> Verdict` |
| `main.bend` | effect shell: one request per stdin line, or one request as argv |
| `LAWS.bend` / `PROOF.bend` | the claims and their proofs |
| `../../src/z0int/bend_gate.py` | host adapter: shape checks, Core IR encoder, fail-closed client |
| `../../benchmarks/bend_gate_parity.py` | differential parity + latency |
| `../../benchmarks/bend_gate_law_mutations.py` | are the laws load-bearing? |

## Run

```bash
curl -fsSL https://bend-lang.com/install.sh | sh             # Bend -> ~/.bend
cd bend/aodl_gate && bend PROOF.bend                          # ALL PROOFS CHECK (~1s)
PATH=~/.elan/bin:$PATH bend PROOF.bend --verdict              # needs Lean 4.34.0 (elan); ~3s
cd ../.. && PYTHONPATH=src python -c 'from z0int.bend_gate import build_kernel; build_kernel()'  # ~40s
.venv/bin/python -m pytest tests/test_bend_gate.py
.venv/bin/python benchmarks/bend_gate_parity.py --fuzz 20000 --transitions 20000 --out parity.json
python benchmarks/bend_gate_law_mutations.py [--verdict]
```

## What is proven and what is only checked

A proven law is a theorem about the **Bend kernel's own functions over the
Core IR**. It says nothing about the host encoder, about agreement with the
Python validator, or about whether a task succeeds in the world. None of
this is surfaced as task success.

| property | status | evidence |
| --- | --- | --- |
| admitted spawn used exactly the frozen contract revision (`{cr == rr}`) | **proven** | `spawn_revision_pinned` |
| admitted spawn requires `dynamic.allowed` | **proven** | `spawn_needs_dynamic` |
| admitted spawn: `kids+1 ≤ maxChildren`, `depth+1 ≤ maxDepth` | **proven** | `spawn_within_bounds` |
| admitted spawn: every declared budget has `seen + prop ≤ limit` in exact Nat (no wrap, no float epsilon) | **proven** (model) | `spawn_within_budget`; runtime caveat below |
| no invented authority: every requested cap is `Elem` of the parent ceiling | **proven** | `spawn_no_invented_authority` |
| if a bigger spend fits then a smaller one fits | **proven** | `spend_antitone` |
| an admitted document grants no privileged capability (payment/wallet/sudo/…, case-insensitive) on any intent edge | **proven** | `doc_no_privileged_grant` |
| fail closed: the gate never admits a request that did not parse in full (unknown mode, truncated or padded stream) | **proven** | `allow_needs_parse` |
| determinism | holds by construction (pure, total, terminating); not a separate law | Bend checks termination |
| the laws are load-bearing | **checked** | 9/9 well-typed weakenings of covered rules rejected by `PROOF.bend` |
| cycles, self-edges, verifier merge grants, recursion bounds, ports/schemas, duplicates, reachability, fan-in, auction, harness, events, privilege-vs-ceiling | **runtime-checked only** | parity corpus; the law-mutation suite shows 4 of these can be weakened and still re-proven |
| kernel ≡ canonical `validate_02` | **measured, not proven** | parity below |
| host shape checks + encoder ≡ validator | **measured, not proven** | parity below; lives in Python |
| transition gate ≡ `reference_transition` / `aodl.check_budget` | **measured** | 16,356 fuzz cases, 0 disagreements |

## Results (seed 48, 20k doc fuzz, 20k spawn fuzz; kernel `fc2e56d486f6d546`)

**Parity.** Documents: 22,079 cases, made up of 32 fixtures, 1 z0int-emitted
doc, 2,046 targeted mutations (62 per 0.2 fixture, covering every rule family
and the validator's quirks) and 20,000 seeded structural fuzz cases. There
were **0 unexplained verdict disagreements and 0 false allows**. There was 1
false deny: the `hotl-0.1-fanin` fixture, which is not modeled and so is
refused. On all 9,519 kernel-routed cases without a validator exception, the
**issue-code multisets agreed exactly**, and every rule code 1–18 fired at
least 33 times. Spawn transitions: 16,356 compared (3,887 allow), 0
disagreements with the reference and 0 with `check_budget`. Another 3,644
unrepresentable proposals were denied.

**Latency** (Intel i7-4870HQ, 8 threads, CPU only, `--threads 1`, persistent pipe; the full pytest
suite ran concurrently for part of it, so absolute numbers are noisy):

| path | p50 | p95 |
| --- | --- | --- |
| CPython `validate()` in-process | 90 µs | 598 µs |
| host shape + encode alone | 97 µs | 671 µs |
| Bend kernel round-trip (docs) | 551 µs | 3,653 µs |
| Bend process per decision (argv, no daemon) | 2,867 µs | 10,249 µs |
| Python spawn reference in-process | 6.6 µs | 34 µs |
| Bend spawn round-trip | 150 µs | 834 µs |

Kernel peak RSS was 8.8 MB. Profiling showed that byte scanning plus parsing
alone takes 0.9–2.4 ms per fixture, which is more than the whole CPython
validator. Rebuilding the emitted C with `clang -O3 -march=native` gained
nothing.

## Findings

1. **The CPU-only latency paragraph is not a verdict.** Those numbers are Intel i7-4870HQ, `--threads 1`, persistent pipe. This file's own limit says the GPU was unused (below). Do not cite them as "Bend is slower" or as promotion condition 2 failed. A GPU-path measurement of this gate has not been published. On that CPU path, the host JSON walk already costs about as much as the canonical validator, and the Bend runtime then adds ~1 µs per token plus an IPC floor of ~70 µs. That does not decide the GPU path.
2. **The runtime Nat is capped at 2^48−1 and aborts past it**, while the
   proofs model an unbounded Nat. The fuzzer found it:
   `bend: a Nat past the largest immediate 2^48-1`. The adapter denies on the
   abort. The host now refuses values ≥ 2^47 so `seen+prop` cannot reach the
   cap. This is a concrete proven-vs-executed gap.
3. **`--verdict` needs Lean 4.34.0** and stack-overflows on a closed unary Nat
   like `Nat.mul(65536n, 65536n)`, because the translation normalizes closed
   terms. The workaround is to build 2^32 by doubling a variable.
4. **The canonical validator raises `TypeError` on unhashable values**, e.g.
   `"kind": ["task"]` or an event `"type": [...]`. This happened in 1,158 of
   22,079 cases. It is fail-closed only if every caller treats an exception
   as a rejection. This is worth an AODL fix: return an Issue instead.
5. The validator's quirks are reproduced exactly and pinned by tests:
   - `1 == 1.0 == True` schema equality
   - `payment: 0 / 0.0 / False` is accepted
   - an uppercase privileged cap is flagged even when the ceiling declares it lowercase
   - an edge `kind: "dependency"` counts for cycles but not for fan-in
   - delivery/provenance shape is checked only on edges whose endpoints resolve
   - `quorum: true` is valid
   - the last duplicate port wins the port index
6. **Proof brittleness.** The document law's proof restates the kernel's
   term shape. Edits to unrelated rules therefore break `PROOF.bend` until a
   mechanical re-proof is done. That is a maintenance cost, not coverage.
7. Bend limits that shaped the design:
   - no JSON and no stdin effect (the kernel reads `/dev/stdin` via `File.read_bytes`)
   - no mutual recursion (continuations instead)
   - no computed `match`
   - no argument inference in proofs (every `-b` is spelled out)
   - F32 is axiomatic, so fractional budgets are refused, not modeled
   - all evaluation is on the CPU; the GPU is unused

## Gaps

- The Core IR normalizer and shape checks live in z0int. Per the owner's
  constraint on aodl#19 they must move into `aodl_contract` (`to_core()`)
  before Bend could be more than experimental.
- No aodl-side branch was created: the agent sandbox refused `git worktree`
  on `~/workspace/aodl`.
- hotl-0.1, the Hermes compile-stops (message/mesh/auction) and the
  observed-graph laws are not modeled.
- The gate is not wired into the live controller path (#13 P0 items 3–6).
- Concurrency/parallel batch throughput was not explored.

## Recommendation

Do not promote Bend to the hot path. If the thread continues, pick the
highest-value next slice:

1. Upstream the Core IR as `aodl_contract.to_core()` with its own tests, plus
   the `TypeError` fix.
2. Wire #13's spawn gate in z0int using the plain-Python
   `reference_transition`, which runs in µs. Keep the Bend kernel as a CI
   verifier that proves the spec and runs differential fuzz against the
   Python gate.
3. Add rule-local lemmas for cycles, self-edges and verifier grants, so that
   proofs track rules instead of the whole issue chain.
