# Turbo (experimental)

Turbo optimizes **time to independently verified result**, not tokens generated.
Tracked in [RFC #175](https://github.com/kvnloo/z0intelligence/issues/175).
It is not installed/enabled in a running voice service by opening a draft PR.

```sh
pip install -e '.[turbo]'
z0int turbo inventory --json                 # catalogs only, no inference
z0int turbo inventory --probe --json         # real bounded inference, usage may bill
z0int turbo run --task 'Compare these sources' --context-file sources.md --deadline 10m --jsonl
z0int turbo run --task 'Add a regression test' --repo /absolute/repo --deadline 15m --jsonl
z0int turbo status                          # all persisted runs
z0int turbo status turbo-0123456789abcdef --json
z0int turbo stop turbo-0123456789abcdef --json
z0int mode turbo on                         # one next z0int run task
z0int run --task 'Use Turbo for this' --deadline 10m --jsonl
z0int mode turbo on --duration 10m           # time-box subsequent z0int run tasks
z0int mode turbo status --json
z0int mode turbo off                        # disable admission flag + request stop of active runs
z0int run --task 'Normal single-agent run'   # Sol only when mode flag off
```

An explicit `turbo run` is per-task consent even with the flag off. The flag
controls `z0int run`, not arbitrary Hermes conversations or existing governed
`z0int task run` checkpoints. No background work starts from toggling it.

## Ownership and execution

* Native Hermes Opus `anthropic/claude-opus-5-5` plans bounded jobs and acceptance.
* Canonical `aodl_contract` validates the compiled HOTL 0.2 graph, fail-closed.
* Kerdoios discovers live Groq/Cerebras free-eligible catalogs and the exact
  `opencode/deepseek-v4.1-flash-free` model; FAST placement uses fresh measured
  request output tok/s and latency. Discovery never executes inference.
* Ready jobs run concurrently on different providers. A single provider is
  serialized across this host's runs using flock; default client policy is
  concurrency 1/provider, 6 requests/minute. These are **not provider limits**.
* Fast workers receive supplied text only, no browsing/repo tools. Supply the
  relevant source packet with `--context-file`. Code workers run OpenCode in
  detached git worktrees; independent native Sol `openai-codex/gpt-6.1-sol`
  inspects candidates and integrates into another detached worktree. Never merge,
  push or deploy. These are trusted local agent processes, **not a sandbox**.
* Native invocations use the installation-bound Hermes runtime and canonical
  gateway with `safe_mode=True`, explicit provider/model, and check the gateway's
  **observed** session model. `hermes --print-runtime-command` must be supported;
  older installations fail closed. The adapter preserves that emitted bootstrap
  and structurally replaces only its terminal `run_module` entry with `run_path`
  for the owned adapter file (the runpy CLI does not execute file paths).
  An advertised per-session `run_budget` RPC field is used when available;
  no shared profile settings are changed.
  Repository rules must be read explicitly by code roles.
* `--normal` performs one nondelegating Sol reference plus the same independent
  Sol verification phase, for matched A/B measurements.

The CLI does not switch to paid/premium fallback lanes after an error. Catalog
free eligibility is not proof that the account is billed $0: API tier and billed
cost may be unknown. Explicitly inspect receipts/account billing. The Opus/Sol
native account may use subscription entitlements; no dollars are inferred from
missing prices. No dollar cap is implemented. Do not use for unattended spend.

## Bounds, stopping and evidence

Default deadline 10m, max 1h; default worker token reservation budget 12000,
max 100000; default request budget 10, max 20; concurrency 1–3; graph 1–6 jobs.
Fast calls cap output at 1024 tokens (including reasoning where applicable).
Text reservations use a conservative UTF-8 byte bound plus output cap.
**Native planner/verifier and multi-step OpenCode usage are observed, not
hard token-capped.** A worktree or completed subprocess is not verified success.
`--checks-file checks.json` accepts owner-supplied nonempty argv arrays (no shell),
executed on the integration worktree; failing checks override a model verdict.

Stop is cooperative for HTTP with a bounded socket timeout (up to 20 seconds),
and process-group scoped for OpenCode/check subprocesses. Native Hermes work is
owned by the shared gateway, not its CLI viewer: the adapter sends a fenced
`session.interrupt` to its exact owned session and reads back the admission's
terminal receipt. Merely killing/detaching a viewer is never cancellation.
Native terminal reconciliation can add up to 40 seconds beyond the task deadline;
unconfirmed cancellation is a failure/unknown, not confirmed stopped work. A stop request is not terminal
confirmation; read status until `cancelled`/`deadline`/`failed`/`completed`. A 429
persists a provider cooldown of at least 60 seconds and honors a longer numeric
Retry-After (including HTTP-date) and longer reset headers; no immediate retry or silent fallback.
Groq unsuffixed request headers are RPD, tokens are TPM; Turbo adapts the pinned
allocator parser at the runtime boundary and retains the original response headers. Observed exhausted quota waits
for a known reset; unknown reset fails closed. Unknown quota is never zero/free.
Independent jobs may continue after a required-node failure, but the run cannot
be verified successful. Quota state is shared only by processes with the same
`Z0INT_HOME`; it is not a distributed cross-machine ledger.

Private request/graph/JSONL events/results/receipt live at
`$Z0INT_HOME/state/turbo/<run_id>/` (default `~/.z0int`). Files are private;
source snippets/outputs can be sensitive and must not be published wholesale.
Candidates and integration worktrees are intentionally retained for review.
`receipt.json` records wall time, peak actual inference concurrency, model IDs,
provided tokens, request output tok/s, raw rate-limit headers, failures/429s,
accounting gaps and the independent quality verdict. Inventory output tok/s is
**output tokens/full request seconds**, not steady-state decode; first-output
latency includes reasoning tokens. Missing CLI usage/latency remains null.
One sample is not sustained capacity. No Jev approval is inferred; Sol is required.

## Private source boundary

Fast cloud workers receive task/context text and dependency outputs. Supply only
scrubbed public-safe text; the runtime is not a content classifier or sandbox.
For private repositories use `--no-code-workers`: it rejects any planner-authored
code job before dispatch, uses source-free fast design/review helpers, then a
native Sol implementer in its own detached worktree and a separate native Sol
verifier/integrator. No private repository files are sent to OpenCode/DeepSeek in
this mode. Native agents retain their normal approval boundary. Without this flag,
code workers can read repository source; use only with approved public source.
The matched Garden Room proof uses this private-source mode, **not** a DeepSeek
private-code throughput benchmark. Native usage remains subscription/unknown.

## Voice follow-up

The stacked z0live follow-up to PR #27 registers `turbo_run`, `turbo_status`,
`turbo_stop` and calls this CLI via argv, never a shell. Say **“turbo: <task>”**,
**“go turbo on this”** (when the current task is clear), or **“stop turbo”**.
z0live does not choose models. Progress belongs only to the active human-started
session, deduplicated through the existing speech journal; stale sessions are
silent and barge-in cancels speech. Physical microphone/TTS/device behavior
requires separate live WebRTC/audio qualification; fake-CLI tests do not prove it.
