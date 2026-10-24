# z0 compact governor

Opt-in OMP 18.12.6 extension for boundary-aware compaction experiments. It never
writes OMP settings. Without an `enabled` file containing `on`, its hooks do no
work; its registered `compact_boundary` tool returns a disabled result.

## Enable and disable

Link this directory into `~/.omp/agent/extensions/z0-compact-governor` and start a
new session (or use OMP's extension reload). Then, **after operator approval**:

```sh
mkdir -p ~/.z0int/compaction
printf 'on\n' > ~/.z0int/compaction/enabled
```

Remove the flag or write `off` to disable. `Z0INT_HOME` relocates all governor
state for isolated experiments. No flag is created by the extension or install.
Existing native compaction remains enabled and is never cancelled by this plugin.

Optional `~/.z0int/compaction/policy.json` (defaults shown):

```json
{
  "mode": "measure",
  "arm": "adaptive",
  "floorPercent": 10,
  "ceilingPercent": 70,
  "cooldownMs": 300000,
  "packetTimeoutMs": 10000,
  "packetTtlMs": 120000
}
```

`measure` records `would_compact` decisions but does not request compaction. It
still enriches and measures native/manual compactions. Only explicitly selecting
`active` enables requests through `ctx.compact`; it does not modify native settings.

## Arms and adaptive decisions

- `25`: request at a natural boundary above 25% usage.
- `50`: request at a natural boundary above 50% usage.
- `native`: no threshold-based boundary requests; native settings are the control.
- `adaptive`: balanced exploration until each arm has five scored compactions,
  then select by mean exact retention minus compressed-token and latency costs.
  The latest recall below 0.75 excludes that arm from the next exploitation choice;
  every fifth choice explores again to detect drift. The thresholds are experiment
  arms, not a claim that 25% is optimal. Receipts persist evidence across sessions;
  model/provider/context-window cohorts are not pooled. Each arm uses its latest
  twenty scored outcomes from a bounded 1 MiB receipt tail.
  Only active threshold/ceiling-triggered receipts have `policy_evidence=true`.
  Manual compactions, measurement-only observations and model-request overrides
  are measured but cannot promote a threshold arm.

All arms share the low floor, cooldown, and hard ceiling. The ceiling is checked
between turns, but compaction executes only when the agent is idle and has no queued
messages. A tool loop is never aborted merely to hit a number. OMP's native
reserve/overflow protection is still the in-flight backstop. `agent_end` is a
natural boundary. A completed todo phase is detected from successful native todo
results, not arbitrary output text. `compact_boundary({reason, durableFacts})`
lets the model mark a boundary and nominate durable facts; its request is serviced
after idle, subject to the floor/cooldown. Pending jobs, switches and disabled
flags are checked before dispatch. Native modes/order are preserved.

## Compressed context

The extension prefetches a State Packet for the current objective, caches it for
120 seconds, and injects it in `session.compacting` alongside a structured handoff:
objective, boundary reason, nominated durable facts, exact literals/decisions and
open blockers extracted from the summarized messages. Packet retrieval uses
`python3 /mnt/zer0models/workspace/zer0/oss/tools/statepack.py orient QUERY --json`.
Override the script with `Z0INT_STATEPACK_PATH`, Python with `Z0INT_PYTHON`, or
its source checkout with the wrapper's `Z0INT_SRC`. It is asynchronous,
single-flight per objective, bounded to 4,000 packet characters, cached and
fail-open on timeout/malformed output/unavailability. These are provenance
packets, never AODL authority or instructions. Nothing is saved as a native handoff
file, so `compaction.handoffSaveToDisk=false` remains respected.

## Memory measurement

Private receipts: `~/.z0int/compaction/receipts.jsonl` (new directory 0700, file
0600). They contain counts and metadata, **not probe literals or packet content**.
A small balanced probe set covers paths, IDs, decisions and open blockers; obvious
secret-bearing lines and hidden thinking are excluded. Scoring uses exact literal
matching against the latest summary plus retained messages only, excluding old
branch history and `preserveData`. `summary_recall_score` separately excludes even
the retained tail. Empty probe sets score `null`, never 100%. When a hook is bypassed
by another custom compactor, probes fall back to pre-compaction visible context;
`packet_status=not_requested` exposes that no packet was injected.

Fields include arm, cohort, selection reason, mode, actual method, usage percent,
`tokens_before`, `tokens_after`, host-projected token metric, compaction latency,
packet status, probe count, retained count, recall score and per-kind counts.
`would_compact`, failures and cancellations are not treated as successful evidence.
The bounded literal metric is memory retention, **not semantic correctness, task
success or downstream performance**. Probe facts are deliberately nominated for
preservation; this is not a held-out reasoning benchmark. Opaque provider remote
compactions cannot be decoded through the public extension API: their visible
summary/tail score is conservative, not a claim about hidden provider state.

## Verification and rollout recommendation

Unit command (run off Groot):

```sh
fleet-build --node mbp --mem 3G --cpus 2 --wait 600 --timeout 3600 \
  --src "$PWD" -- bun test omp-extensions/z0-compact-governor/index.test.ts
```

The installed 18.12.6 CLI was exercised through RPC with a separate
`PI_CODING_AGENT_DIR` and `Z0INT_HOME`, no discovered extensions/rules/skills,
no production credentials or configuration writes, and the existing GPU router's
`qwen3-0.6b-q8` via OpenAI-compatible HTTP. Smoke compactions force `soft` only
inside that isolated profile; they do not alter the operator's method order.

Final source smoke: two fresh isolated sessions received identical fixture prompts
and automatically compacted on `agent_end` at 60.9375% usage:

| Arm | Retained literals | Projected tokens | Latency | Packet |
| --- | --- | --- | --- | --- |
| 25, active | 3/6 (0.50) | 2,517 → 2,421 | 33.618 s | timeout |
| 50, active | 0/6 (0.00) | 2,517 → 2,461 | 19.057 s | ok |

Earlier runs also exercised the model's `compact_boundary` tool and measurement-only
manual compaction. No extension runtime errors were emitted. The weak 0.6B
summarizer invented completion claims in an earlier run. These are integration
measurements, not proof of semantic quality or optimal thresholds: both final
fixtures crossed both thresholds at one boundary, and one packet timed out.
The production summarizer and real-task outcome quality remain unqualified.

MBP unit job `fleet-mbp-085cd7a333184f9e`: 13 passed, 0 failed, 32 assertions.

**Recommended exact OMP settings change: none.** Keep thresholdPercent=-1,
methodOrder=[remote,snapcompact,handoff,shake,soft], asyncEnabled=true,
handoffSaveToDisk=false and idleEnabled=false. Approve only an opt-in measurement
flag first. Do not enable active mode or promote an arm from these smoke results;
collect comparable real-task receipts with the actual compaction model and inspect
semantic losses before approving a policy change.
