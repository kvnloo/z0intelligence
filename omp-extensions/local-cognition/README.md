# local-cognition (OMP extension, shadow-only)

Dogfood lane for [oh-my-pi#83](https://github.com/kvnloo/oh-my-pi/issues/83) and
z0intelligence#20. On every OMP `tool_call`, it asks the resident z0int bridge
worker to compile the deterministic legal action set and then records what the
configured local models *would* have chosen from that set.

Nothing is ever executed. The lane exists to measure agreement between the
compiler's verdict and the local cognition portfolio.

## Safety boundary

- The `tool_call` handler is **synchronous and always returns `undefined`**. It
  never blocks a tool, never revises input, and never approves or denies
  anything.
- Sending is fire-and-forget with a hard timeout. A timeout or error is
  swallowed and recorded — never surfaced as a tool failure.
- No tool, permission, approval, retry, cancellation, provider or model is ever
  changed by this extension.
- The Python op behind it (`cognition_shadow`) compiles first, only shows the
  legal set to models, records an out-of-set answer as `invalid_call` with
  `selected_action: null`, and always writes `selected_action: null` /
  `executed_action: null` in its receipt.

## Install

From the repository root, symlink the extension into OMP the same way the other
z0int extensions are installed:

```bash
ln -s "$PWD/omp-extensions/local-cognition" ~/.omp/agent/extensions/local-cognition
```

One OMP restart is required after adding an `index.ts` extension. The Python side
(`src/z0int/cognition/shadow.py` and `src/z0int/bridge/`) is hot-swappable via
`/reload-plugins` once the `z0int-bridge` shim owns the worker.

The lane prefers the transport published by `z0int-bridge`
(`globalThis.__omp_z0int_bridge_transport__`). If it is missing it tries to
import the sibling `z0int-bridge` extension's `ensureWorker`/`request`, and
otherwise no-ops.

## Configuration

The decision policy at `$Z0INT_HOME/policy/decision-policy.json` supplies shadow
models from each unexpired family's `arm.model` at stage shadow, canary or live.
Both consumers verify `z0.decision_policy.v1` and its canonical-body SHA-256.
Missing, malformed or tampered policies cannot interrupt a tool call: they
stamp stage `none` and a `policy_reason`, and the observe-only lane falls back
to the settings below. Valid policy models take priority over fallback settings.

Every observation and Python shadow receipt records `policy_version`,
`policy_sha256`, `policy_family`, `rule_id`, `stage`, `family` and `serving_healthy`. Health is
`null` when the endpoint has not passed a current check. A shared cache at
`$Z0INT_HOME/policy/serving-health.json` stores one entry per URL, with the exact
five-field scope SHA, status, `checked_at`, returned model and revision.
Python writers merge updates under a file lock and publish by atomic rename.
Readers trust only the same scope and an observation no more than 60 s old.
The standalone CLI refreshes a miss with one `/v1/models` GET, with a 1.5 s
timeout. An unknown result produces `serving_health_unknown`, never an offload.
The synchronous OMP tool handler does not wait for HTTP. Its asynchronous probe
uses the same shared cache and a 1.5 s timeout. Python receipt-only paths can
refresh in a daemon with a 1 s timeout.
Session entries use the snapshot loaded when their request was constructed,
even when the active policy changes before the response arrives.

`z0int cognition decide` combines policy credit with explicit payload credit.
Only payload additions appear in `payload_override` on every decision row.
Canary membership is the first 64 bits of SHA-256(trace_id), compared against
`canary_share * 2**64`. Reuse a trace id for deterministic replay. Threshold
table revisions remain separately named `escalation_version`.

The policy picks its own scoped arm. The CLI binds that exact
model/quant/engine/backend/device to a matching policy serving entry. It never
uses a role-registry fallback for an authorized decision. Policy credit enables
this binding automatically. An absent or unhealthy endpoint produces
stage `none` and `policy_reason: no_serving_for_scope`. Unknown health after the
bounded probe produces stage `none` and `policy_reason: serving_health_unknown`.

The endpoint returns its model id. For physical model calls, serving entries must
also contain `expected_model_id`, `engine_build`, `model_path` and the GGUF's
`model_sha256` from measured provenance. Each decision reads
`/props?model=<URL-encoded expected_model_id>`, never the bare router `/props`.
The receipt records `observed_identity` from the response model id plus the
props `build_info` and `model_path`. `props_model_id` records `model_alias`.
`expected_identity` carries the serving expectations and provenance hash.
The consumer compares model id, build and path before it accepts the answer.
It does not hash a model file on each turn. A revision is not required and
`response_revision` remains null.

An identity mismatch fails back to frontier/Jev. A below-threshold answer records
its actual confidence, the policy threshold and its abstain reason. The same arm
is called at most once per decision, even across several semantic tiers.

Every receipt has a `synthetic` boolean. Synthetic policy/rule evidence or an
incoming `synthetic: true` payload taints nested receipt, attempt, shadow and
escalation rows. Payload false cannot clear policy taint. Unknown-family fallback
rows retain policy-wide synthetic provenance. Compiled stage-none rules retain
their `demoted_reason` as the receipt's `policy_reason`.

`policy_scope` remains an optional narrowing filter, not a prerequisite for
policy selection. The CLI records that filter when supplied. The OMP extension
reads the same optional filter from cognition settings for receipt attribution.
Neither consumer generalizes a measured arm to a different physical scope.

O13 bounds each SLM request before provider IO. `option_capacity` is a map keyed
by the sorted compact canonical JSON of `{model,quant,engine,backend,device}`.
Each entry contains `allowed_k`, `finding_ids`, `wilson_lower`, `task` (a list),
`bar` and `expires_at`. Only an unexpired exact-scope grant whose Wilson lower
bound meets its bar can raise the default binary cap. Missing, invalid or expired
capacity data means a cap of 2, not permission for a broad choice set.

An over-cap proposal never reaches the model. It returns to frontier/Jev with
`option_capacity_exceeded`, effective stage `none`, `slm_requested: false`,
`option_count: 0` and its original `attempted_option_count`. No candidate is
silently discarded to satisfy the law. Existing shadow projection widths remain
explicit proposals: an unqualified wide projection is blocked, not shrunk.

Each actual request records its rendered `option_count`, canonical `model_id`,
exact `quant`, complete five-field `scope` and `allowed_k`. Provider wire metadata
uses OpenAI metadata strings. Receipts and in-process request metadata retain
typed counts. These are actual candidate counts, never the grant's maximum.
Per-model metadata remains available on timeout and is preserved in OMP's nested
model observations. Aggregate envelopes and pure Python portfolio strategies
record `slm_requested: false` and do not masquerade as provider calls.


Fallback environment (wins over the fallback settings file):

| Variable | Default | Meaning |
| --- | --- | --- |
| `OMP_Z0INT_COGNITION_SHADOW` | on | `0`/`false`/`off` disables the lane |
| `OMP_Z0INT_COGNITION_TIMEOUT_MS` | `4000` | Hard client-side timeout for one shadow send |
| `OMP_Z0INT_COGNITION_MODELS` | `qwen3_06b` | Fallback manifest model ids when the policy has no eligible shadow models |
| `OMP_Z0INT_COGNITION_TOOLS` | discovered, else builtin | Comma-separated tool allow-list for the action set |
| `OMP_Z0INT_COGNITION_AUTHORITY` | `read` | Risk classes the compiler may declare legal, in addition to those of the available tools (shadow-only: nothing is executed) |
| `OMP_Z0INT_COGNITION_BRIDGE_FALLBACK` | on | `0` disables the sibling-extension import fallback |
| `Z0INT_HOME` | `~/.z0int` | Policy, receipt and settings root |

Optional settings file `~/.z0int/config/cognition.json` (env still wins):

```json
{
  "shadow": true,
  "timeout_ms": 4000,
  "models": ["qwen3_06b"],
  "authority": ["read"]
}
```

## Command

`/z0int-cognition-status [N]` prints the last `N` (default 10) rows from the
receipt file `~/.z0int/shadow/cognition-shadow.jsonl`, falling back to the
in-memory status ring when no receipt exists.

## Test

```bash
cd omp-extensions/local-cognition
bun test
```

The tests use a fake `ExtensionAPI` and a fake bridge transport; they need no
OMP runtime, no Python and no model server.

## What an observation records

Every observed tool call settles into exactly one outcome:

| Outcome | Meaning |
| --- | --- |
| `slm_executed` | at least one requested model answered |
| `compiler_only` | the worker compiled the legal action set; no model was served |
| `unavailable` | models were requested and none could be reached |
| `timeout` | every requested model, or the worker itself, ran out of time |
| `error` | the worker answered with a failure, or the send failed |
| `no_transport` | no bridge worker was reachable |

Per model the status is `selected`, `abstained`, `invalid_call`, `unavailable` or
`timeout`. A model has abstained only if its backend answered; the worker's
receipt marks unreached models `abstained` as well, and this lane does not
repeat that.

Each observation is also appended to the OMP session as a
`z0int-cognition-shadow` entry (`pi.appendEntry`), carrying the live session id,
the tool-call id, the trace id sent to the worker, the outcome, the per-model
statuses and the worker generation. Session entries are records, not messages:
nothing is added to what the model reads. Tool input is not copied into the
entry. `/z0int-cognition-status` reports coverage (tool calls seen against
observations settled, by outcome) alongside the recent rows.

## One time budget

The extension gives up on an observation after `OMP_Z0INT_COGNITION_TIMEOUT_MS`
(default 4000). It sends the worker 80% of that as `timeout_ms`, so the worker
stops waiting on a slow or still-loading model inside the same budget and
returns it as a `timeout` row. Without it the worker waits up to its own 30 s
default and later writes a receipt saying the model answered, for a tool call
whose session entry already says it timed out. A model's first call after it
loads can exceed the budget (about 11 s was measured for a 3 B model); those
observations are recorded as timeouts, by both records.

## Private online learning

`z0int loop tick` now starts with cognition learning, before the legacy
verified-turn lane. It incrementally indexes native OMP session JSONL and joins
receipts by **both** session id and tool-call id. It never substitutes the
receipt's observed tool name for a missing join. The original receipts remain
immutable and non-executable; `~/.z0int/loop/labelled-shadow.jsonl` supplies
`executed_action`, tool-error/exit-code outcomes and preceding-tool features.
Unknown later corrections remain null, not successful outcomes.

`~/.z0int/loop/shadow-learning.sqlite` stores watermarks, exact joins and
prequential arm scores. Rules and bounded history kNN predict before consuming
the current label; SLM scores use only their original recorded predictions,
and a missing confidence abstains at the confidence gate. Per arm the status
reports gated accuracy, coverage and a Wilson 95% lower bound. At least five
covered observations and a lower bound of 0.8 are required for **shadow credit**.
Credit is loaded only by the shadow cascade, never by a live registry cascade.

Every tick reports labels added and the best-arm lower-bound change against a
24-hour baseline. No new labels, excessive transport failures, partial
measurement or a plateau are DEGRADED, with a nonzero tick exit. The persistent
`strategy-ledger.json` records each hypothesis, baseline and prospective result;
a failed configuration is never selected again. The active shadow strategy
switches between short/long history kNN, transition rules and ordered bigrams.
Exhaustion is explicit, not an unimplemented embedding/model fallback.

`z0int loop status` prints one line with credit, labels/day, best arms,
current strategy and last evolution; add `--json` for the complete curve.
`z0int loop shadow --budget-s 600` runs just this lane. Set
`Z0INT_OMP_SESSIONS` to override the session root; the default is
`/workspace/kvn-home/.omp/agent/sessions`. Thresholds are configurable under
`shadow_learning` in `~/.z0int/config/loop.json`.

Serving uses `~/.z0int/config/serving.json`; its base URL must exclude `/v1`
because the adapter appends `/v1/chat/completions`. Each endpoint has a real
HTTP `timeout_s` (configured as 3 seconds for shadow candidates), not merely a thread-join timeout.
The bridge runs one shadow request on a separate bounded worker thread,
rejecting overlapping shadow work instead of queueing or delaying ordinary
operator requests. Client handlers remain synchronous/fire-and-forget.

Failed-only shadow observations go to
`~/.z0int/shadow/cognition-shadow-failures.jsonl`, not the normal receipt
journal. The failure journal rotates at 1 MiB with three retained generations;
`cognition-shadow-health.json` keeps cumulative counts and 32 daily buckets.
Ticks ingest the bounded failure journals and flag the new-writer failure rate
independently of the old backfill transport-error rate. The original legacy
journal is never deleted or truncated.

On Groot this farm endpoint is also the Quackles arbiter's canonical router:
`hermes-k8s-lab/quackles/arbiter/qk-arbiter` defaults to
`http://100.113.138.100:11530`. Quackles schedules jobs and manages pressure on
that router; the k3s `hermes-lab/z0intelligence-executor` service on 11503 is a
stateless dispatcher, not an OpenAI model server. Do not point cognition
completion requests at the executor's HTTP port.

New writer receipts declare `identity_contract: session-tool-call.v1`.
Missing session/tool-call identities quarantine even a valid prediction in the
bounded failure journal; daily identity failures force
`labels_identity_invariant` degradation. Exact labels never use timestamp
matching as gold. A legacy timestamp recovery census must remain a separate
non-gold tier and be discarded when collisions are high.


## Context-safe candidate projection

The farm's `-c 8192 --parallel 2` provides 4096 tokens per slot, not 8192.
Real OMP sessions expose about 120 tools; sending every description can exceed
that slot. Shadow model inputs now use a deterministic projection: the kNN
recommendation, preceding tools, tools previously used in the session, common
tools, then the remaining names in lexical order. The current observed tool is
never used to build or rank that projection. Descriptions/schemas are compacted.

The compiler's full `legal_ids` remain unchanged for scoring, including the
executed tool even when it misses the projection. Each model records its actual
`model_candidate_ids` and server-reported `prompt_tokens`. The model-only request
uses a conservative 3000-byte serialized prompt/schema budget, including a
512-byte template/control reserve, and at most 128 completion tokens. UTF-8 bytes
bound byte-tokenizer input tokens; non-shadow requests are not pruned.

`projection_recall` measures how often an exact executed-action label is in the
model's candidate set, per family and projection configuration. It is separate
from prediction accuracy and exposes its denominator. With at least five
observations and recall below 0.8, the evolver tries wider candidate caps (48,
then 64) and raises the conservative input budget to 3500 before attributing the
failure to model quality. Old configurations and their outcomes remain in the
ledger. Real prompt overflow is a distinct `context_overflow` failure class and
daily counter; an abstaining local arm cannot mask a failed SLM observation into
the normal journal. No shared farm context/parallel settings are changed.

