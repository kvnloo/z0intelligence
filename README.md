# z0intelligence

**A provenance-backed cognition and control plane for agent systems.**

z0intelligence turns evidence, runtime state, and observed outcomes into bounded decisions, auditable execution choices, and eventually cheaper verified mechanisms. The goal is not “replace the frontier model with a tiny model.” The goal is to make expensive generic inference do less work **without confusing confidence, completion, or memory with truth or authority**.

The Python import/CLI namespace remains `z0int`; the distribution metadata still carries the historical `openjev-phase1` name for compatibility. Historical OpenJev code remains in-tree, but it is now one substrate inside the larger z0intelligence system.

> Core invariant: **evidence ≠ claim/state ≠ authority ≠ action ≠ outcome ≠ learned procedure.**

## What exists on master

The current repository is much more than the original OpenJev prototype.

| Surface | Current role |
| --- | --- |
| Provenance-backed context and state | ContextPacket resolver with resident FFF codebase search + QMD docs fallback, source-backed State Packets, question-scoped DecisionOpportunities and explicit invalidation; retrieval is not authority |
| Capability router | `z0int.intelligence.route(request, snapshot)` chooses only from evidence-backed eligible capabilities; missing evidence returns `PARENT_ONLY` |
| Dispatch authority | Owns execution identity, replay/conflict protection, canonical receipts, and “do not execute twice when prior execution is uncertain” semantics |
| Local cognition | Deterministic legal-action compiler + provider-neutral candidate model + risk/quality gates + escalation + shadow cascade |
| Decision backends | Jev, Laya, OpenJev/direct logits, Decider, legacy NanoJev, Julia-1, plus a separate image-decision protocol |
| Harness bridges | OMP, Hermes, Claude Code, DSH, AgentWeb and Agent Orchestrator seams; experimental routes remain explicitly gated |
| Compiler stack | Routines, cascades, counterexample repair, ABAB experiment helpers, AODL plan bindings |
| Evaluation | Backend Pareto harness, RLCDAlignBench-style detector lane, Image JevBench entry, observer/outcome receipts, Decision Dataset v2 |
| Economics | Measurement-complete Tokenomics emission and provider-usage accounting; incomplete measurements stay explicitly partial |
| Deployment | Host-owned z0int authority with a hardened Kubernetes `hermes-lab` executor lane |
| Memory foundations | Append-only EventLog, rebuildable OptMem structural projection, scoped/bitemporal memory contracts; no migration of production stores |
| AODL admission | Pinned canonical Python structural gate, authority protocol v3, immutable drift observations and opt-in host-governed remote worker |
| Architecture export | `zer0.repo.yaml` exposes exact-ref subsystem boundaries to z0archy |

The source of truth for the current architectural boundary is [zer0.repo.yaml](zer0.repo.yaml).

## Candidate artifact imports

Use `z0int artifacts inspect MANIFEST --json`, then
`z0int artifacts import MANIFEST --json`. Import always creates an unpromoted
candidate. Evolution Lab remains the producer and the independent evaluation
owner remains responsible for promotion.

The v1 identity matches Evolution Lab's exporter: SHA-256 over the sorted file
digests joined with `|`. Import checks both the declared digests and the artifact
ID. Bundle members must be regular relative files, with no traversal, symlink,
duplicate destination basename, or reserved `manifest.json` destination.

Import copies into private staging, verifies the copied bytes, then publishes
one identity directory atomically. An interrupted copy publishes nothing.
Exact reimport preserves the existing artifact and any independent local
promotion. Different metadata, a missing installed identity, or damaged
installed bytes cause a conflict. Reimport does not silently repair them.
The former `--force` overwrite option is removed.

Artifact discovery reads published identity directories, not staging or the
old index file. `inspect` and `import` verify bytes. `list` and `get_artifact`
read metadata only, without hashing model weights on a preflight call.
An artifact hash does not authenticate its producer, prove training quality,
or attest the weights loaded by a runtime.

## Evidence-bound decision policy

`z0int policy ingest` converts supported JSON measurements into an append-only,
locked hash chain at `$Z0INT_HOME/findings/findings.jsonl`. It records skipped
sources and their reasons in `findings/ingest-status.json`. Unknown layouts,
prose reports, missing observation timestamps, missing source hashes and missing
sample counts never become evidence. Repeated source hashes do not add findings.
Configure discovery with `--operations-root` / `Z0INT_OPERATIONS_ROOT` and
`--z0evals-root` / `Z0INT_Z0EVALS_ROOT`. Use `--sources sources.json` for a JSON
array of `{kind,path,arm?,ts?,scope_note?,url?}` when a source omits runtime identity.
No device, engine, backend, quantization, timestamp or sample count is guessed.

`z0int policy compile [--now ISO8601] [--config config.json]` writes
`policy/decision-policy.json` atomically and retains content-addressed versions
under `policy/history/`. A corrupt findings chain stops compilation without
overwriting the previous policy. Compilation is pure for fixed findings,
previous policy, timestamp and compiler configuration. The CLI supplies the
compiler Git revision. Library callers supply `CompilerConfig.compiler_revision`.
Bars, minimum counts, expiry, endpoint outage duration and canary share are
configuration fields. Promotion advances one stage per compile.

Contract interpretations:
- `prev` hashes the complete canonical previous line, excluding its newline.
  Finding IDs exclude `id` and `prev`. Policy versions exclude only `version`.
  Canonical JSON uses sorted keys, compact separators, UTF-8 and finite numbers.
- Shadow uses the 0.80 retention bar. Canary/live use 0.95. A latest lower bound
  below 0.80 removes credit. A bound below 0.95 demotes canary/live to shadow.
  No fresh evidence or an outage longer than 24 hours removes all credit.
  Expired promotion prerequisites demote immediately to the highest stage
  whose prerequisites remain fresh. An expired outcome can reduce live to canary.
- Observation expiry is the earlier of `expires_at` and `ts + expiry_days`.
  Equality with the current timestamp means expired. Future observations cannot
  promote an arm. Runtime outage evidence uses metric `endpoint_health` and
  an explicit timezone-aware `unhealthy_since`.
- Live shadow labels require `metric.live_shadow=true`. Replay non-inferiority
  requires explicit `divergence <= aa_noise_floor`. Product-outcome promotion
  requires complete paired cohorts of independently bound product checks,
  not a caller-asserted aggregate `n` or `outcome_ledger_verified` flag.
  z0eval receipts need `verdict=KEEP` and all four architecture gates `pass`
  for promotion. Negative receipts remain evidence, not promotion authority.
- Scope is structural, never inferred from prose. Each explicit arm dimension
  constrains the rule. `subject.scope` can add constraints but cannot erase arm
  boundaries. Scoped rules use `family@<scope hash prefix>` keys, with base
  `family` and `scope` fields. The narrowest matching rule wins, even at `none`.
  Missing scope dimensions do not match narrower rules. No cross-device,
  quantization or engine relaxation is inferred from `scope_note`.
- Bench JSON uses `results` or `models` arrays, explicit arm identity and counts.
  Offload JSON uses `opportunities` rows with subject/arm, evidence class,
  recall confidence bounds and `retained_fraction=0.2`. z0eval receipts supply
  an explicit `findings` array. Unsupported layouts are skipped with reasons.

`z0int policy show`, `explain <family>` and
`changelog [--from VERSION --to VERSION]` expose policy and evidence provenance.
All commands support `--json`. The changelog defaults to the latest policy and
the latest older compiled version. Markdown emits one entry per stage change,
including finding IDs and source paths. Publication must replace local evidence
links with authorized immutable links. Measured findings can supply serving
URLs through source metadata. Prose URLs and inference never create endpoints.
Serving entries preserve the complete measured model, quant, engine, backend
and device scope, plus arm kind. Missing quantization metadata never becomes
an endpoint with guessed identity. Canary/live rules without an exact measured
endpoint retain their evidence-derived stage and carry `serving_missing=true`.
JSON and Markdown changelogs expose these missing endpoint annotations.

Optional finding `synthetic=true` is part of the content hash and hash chain.
The compiler propagates synthetic provenance to affected rules and endpoints.
Any synthetic input marks the whole policy and its changelog synthetic.
Synthetic fixtures use the same promotion bars and stages as measured evidence.
They do not prove a real experiment promotion. Runtime and guard owners must
reject synthetic authorization outside an explicitly owned temporary smoke context.

Measured source metadata can include `expected_model_id`, `engine_build`,
`model_path`, `model_resolved_path` and `model_sha256`. Source specifications,
receipt `source` metadata and individual result finding `source` metadata
preserve these fields and explicit URLs. Finding provenance always uses the
actual receipt file path and hash. The compiler preserves endpoint metadata.
The pure compiler never reads or hashes external model files. Identity metadata
must come from the measurement owner, not from a guessed engine-path revision.

Product checks observe one run each. Ingest verifies the referenced receipt's
hash, run identity, boolean result and observed product target. A successful
single run is not a low-confidence aggregate that automatically removes prior
credit; a latest verified product failure still removes credit.

For outcome promotion, each receipt also binds its role, pair, task snapshot,
experiment, subject and arm to a canonical JSON
`z0.policy.paired_outcome_protocol.v1` document. The protocol declares candidate
and reference arms, both required roles, every expected pair and distinct task
snapshot, observation window and non-inferiority margin. The compiler rechecks
its hash and requires the complete membership: unknown, missing, conflicting or
duplicated members cannot become a selected successful subset. It recomputes
the candidate-success Wilson lower bound and candidate-loss Wilson upper bound
against the reference, retaining all contributing finding IDs. Imported
aggregate counts and noise floors do not supply this outcome gate.

The hash chain and protocol hash prove content consistency, not trusted
preregistration or verifier authorship. `frozen_at` and receipt observation times
are assertions from the measurement owner. Keep ledger writes under that
owner's control; independent provenance and native product verification remain
required before treating a real cohort as promotion authority.

### ChoiceLadder option capacity

The pure compiler emits an evidence-bound `option_capacity` map. Each key is
the canonical JSON string of the exact model SHA-256, quant, engine, backend
and device scope. `option_capacity_key(scope)` rejects model aliases. No
capacity credit transfers between different physical scopes.

Each scope contains `read`, `mutating_reversible` and `destructive` entries.
Each entry records `allowed_k`, `finding_ids`, `wilson_lower`, `task`,
`bar_derived`, cost `inputs` and `expires_at`. The default required tasks are
T1, T2, T3 and T4; configure them with `option_capacity_tasks`. Observed
measured task names also remain required, including failed or expired tasks.
The accuracy bar is derived from fresh, measured, exact-scope and risk-bound
call, frontier, error and abstention costs in one supported unit:
`1 - (cost_frontier * (1 - abstain_rate) - cost_call) / cost_error`.
Unmeasured default costs are diagnostic only. Destructive capacity remains
blocked; routing promotion thresholds are unchanged.

Capacity evidence requires `subject.family=choice.option_capacity`,
`subject.job=ChoiceLadder`, `subject.task` (or `metric.task`), metric
`choice_accuracy`, integer `metric.option_count >= 2`, `ci_method=wilson95`,
positive `n`, and measured, unexpired observations. The compiler compares the
smaller of the stated lower bound and a recomputed Wilson lower bound against
the derived bar; the lower bound must strictly exceed it. An explicit correct
count must agree with accuracy and sample count. The latest eligible measured
observation for a task and k supersedes older success.

The compiler selects the largest passing k per task. The allowed capacity is
the minimum across every required task. Missing, pending, expired, inference,
ineligible or inconsistent evidence never counts as success: incomplete
coverage yields zero, not a default capacity credit. Proof IDs and the earliest
selected proof or cost expiry accompany positive credit.

The exploratory scorer has no trusted action-risk classification. Its reader
therefore requires a fresh, digest-verified policy with measured entries for
all three risks at the exact physical scope, and uses their minimum capacity.
A valid zero capacity abstains before an RPC. Missing or invalid evidence
retains the existing exploratory candidate cap; this fallback is not routing
credit or action authorization. The current serving endpoint supplies a model
alias, not a verified model digest, and the compiler blocks destructive credit.
Thus current live callers cannot receive policy-backed capacity uplift.
The reader neither hashes model files on the hot path nor substitutes aliases.

## Startup orientation

`scripts/state-packet/generate_packet.py` publishes an atomic, source-linked
orientation packet at `~/.local/state/z0int/STATE-PACKET.md`. Its header records
`generated_at`, the generator SHA-256, and the observed repository commit.
The complete packet has at most 223 lines and 64 KiB. Every line names its source.
The operations inputs remain under `/mnt/zer0models/workspace/zer0/data/operations`.
No gold questions or experiment answers enter the generator.
The packet retains the O13 default of two options and the preserved, explicitly
nonfinal pooled pilot. Model-routing formula, lambda provenance, catalog coverage,
unknown metrics and shared-quota limits come from the O14 report/catalog directory.
Set `Z0INT_POLICY_ROOT`, `Z0INT_ROUTING_ROOT` or `Z0INT_LIVE_REPO` to select other
observed source roots. Runtime identity prefers a validated `DEPLOYED_SHA` in an
exact-SHA deployment directory and otherwise reads a real Git checkout.

Install a committed feature with `python3 scripts/install-state-packet.py`.
The installer archives exact Git content into `~/.local/lib/z0-state-packet/<SHA>/`,
backs up replaced files, then links the OMP extension and Hermes clean-profile plugin.
Enable Hermes with `hermes -p clean plugins enable hermes-state-packet`.
The Claude Code user settings retain unrelated hooks.
The `.files` `install-state-packet-timer.sh --activate` command installs the
15-minute user timer and generates the first packet.

OMP uses its real `before_agent_start` system-prompt-array contract.
Hermes uses independent `pre_llm_call` context callbacks, not the routing adapter.
Claude Code uses the documented `UserPromptSubmit` / `hookSpecificOutput` /
`additionalContext` envelope. Hermes and Claude context chunks stay below 9,000
characters to avoid hook-output spill limits. All chunks concatenate to the same
packet. A packet older than two hours produces only `packet stale since …`.
Unreadable or malformed packets fail open without facts. Hooks restore the current
packet on later user turns as well as the first turn, including after compaction.
Evidence is never an authority grant.

Behavior checks: `PYTHONPATH=src python3 -m unittest discover -s tests -p test_orientation.py`
and `bun test omp-extensions/z0-state-packet/index.test.ts`.
Run these checks on the fleet, not on Groot.

## Architecture

~~~text
authoritative sources / traces
  git · OMP · Hermes · DSH · files · memory stores · external evidence
                         |
                         v
              provenance-backed resolution
              ContextPacket / state projection
                         |
                         v
             capability / decision request
                         |
          +--------------+---------------+
          |                              |
          v                              v
 deterministic compiler          typed decision surfaces
 legal actions / budgets         Jev · Laya · OpenJev
 permissions / constraints       local SLMs · specialists
          |                              |
          +--------------+---------------+
                         |
                         v
                 intelligence.route()
                         |
                         v
                  dispatch authority
          replay · conflict · idempotency
                         |
                         v
                  harness / executor
              OMP · Hermes · DSH · ...
                         |
                         v
               result + verifier/outcome
                         |
                         v
        DecisionReceipt + Tokenomics + activity
                         |
             +-----------+-----------+
             |                       |
             v                       v
          z0evals               Evolution Lab
      frozen studies       search / promotion / credit
             |                       |
             +-----------+-----------+
                         |
                         v
       routine / specialist / policy candidate
             only after evidence-backed promotion
~~~

AODL remains the portable intent / authority / structural contract. Kerdoios owns residual resource placement and quota economics. Harnesses own execution. Tokenomics owns measurement semantics. Evolution Lab owns experiment search/promotion. z0intelligence owns the evidence-to-state-to-decision runtime and its receipts.

## Decision stack: current truth

There are two different questions that older README text blurred together:

1. **Can a backend answer a typed question?**
2. **Is that backend eligible to influence an automatic production route for this specific capability?**

Those are not the same.

The canonical capability registry is [manifests/capabilities.v1.json](manifests/capabilities.v1.json). On current master, the narrow `evidence_sufficiency` route through **TypeSafe Jev 1.13.0** is the clear eligible verification capability. Most other backend/provider entries are observed, provisional, experimental, or ineligible until independent evidence improves.

| Backend | Role today |
| --- | --- |
| TypeSafe Jev 1.13.0 | Reference verifier / typed semantic backend for the registered evidence-sufficiency contract |
| Laya 421M | Resident local typed-decision fast path; function-level Laya→Jev escalation exists, but broader automatic verification eligibility remains experimental |
| OpenJev 0.6B / 4B | Local direct-logit / training / benchmark substrate |
| Decider-2B | Calibrated typed-decision candidate in the common benchmark surface |
| NanoJev | **Legacy benchmark/reproducibility lane only**; not the verification default |
| Julia-1 | Installed and measured, **default-off / rejected for the tested decision domains** |
| Qwen2.5-VL-3B image adapter | Separate multimodal categorical decision protocol for Image JevBench-style experiments |

Generic remote workers remain free-only by default. The Jev paid path is a narrow, named verification exception; backend registration itself never grants tool, filesystem, credential, or execution authority.

See [docs/intelligence-layer.md](docs/intelligence-layer.md), [docs/backends.md](docs/backends.md), and [docs/observer-evaluation.md](docs/observer-evaluation.md).

## What the experiments actually taught us

The architecture is deliberately shaped by negative results, not just demos.

### Local typed models are not automatically a cheap Jev replacement

On the existing authored144 comparison:

| model | accuracy | p50 |
| --- | ---: | ---: |
| Jev 1.13.0 | 95.1% | 267.6 ms |
| Laya 421M | 61.1% | 226.5 ms |
| Julia-1 | 41.7% | 28.6 ms |

Laya is only modestly faster on that corpus and far less accurate. Julia is much faster, but its confidence is not a usable cascade gate and its performance collapses on the tested z0 decision domains. Julia therefore remains default-off rather than being promoted because it is small or fast.

See [docs/julia-decision-model.md](docs/julia-decision-model.md).

### Real decision data matters more than synthetic routing stories

The merged Decision Dataset v2 extractor reconstructs per-turn OMP decisions from real session JSONL rather than treating a repeated session prompt as per-turn state.

A 300-file sample produced:

- 101 sessions;
- **35,248** outcome-backed action rows;
- 169 compaction rows;
- 264 model-usage rows;
- roughly **291k** projected actions over the full 2,477-session corpus.

That immediately exposed the real bottleneck: several useful decision families are now data-rich, while others still lack the counterfactual or verifier labels required for trustworthy learning.

See PR #43 and [docs/decision-capability-v2-plan.md](docs/decision-capability-v2-plan.md).

### Measurement completeness is part of correctness

The OMP bridge now accumulates provider usage across the full multi-turn/tool loop and closes the trace only at terminal `agent_end`. A trace is marked complete only when every observed provider turn carries usage. Partial/failed/unsupported measurement cannot silently mint “measured savings.”

That distinction flows into Tokenomics and Kerdoios instead of being repaired later with guesses.

## State, memory, and personal context

The old README described “bring your history” as if the main missing step were training on exports. The newer architecture is stricter:

**raw history is evidence, not current truth; retrieval is not belief; memory is not instruction authority.**

Master contains the provenance-backed resolver, source-revision-keyed State Packet reducer and question-scoped DecisionOpportunity builder. The current memory program is being built additively without replacing the existing stores.

The startup packet reads exact deployment stamps before Git metadata. It only
asks Git about paths with a `.git` ancestor; bounded command timeouts are
reported on stderr and leave that observation unavailable, not a fabricated
repository identity or a partially published packet.

### Memory foundations and remaining design work

| Work | Status | Purpose |
| --- | --- | --- |
| #67 canonical event ledger | integrated | Append-only canonical event truth, checksums, blobs, replayable index |
| #69 OptMem temporal projection | integrated | Age-decay TREE cover, raw recent tail, exact `zoom()`, hard token budget |
| #68 lifelong-memory contract v1 | integrated | Stable source identity, hierarchical scopes, bitemporal claims, snapshots, memory-use receipts |
| #63 memory control-plane RFC | open | Defines EventLog → OptMem / FTS5 / AgentsView / TencentDB → StatePacket split |
| #66 belief/scope/credit RFC | open | Defines the evidence→claim→state→outcome→procedure lifecycle |

The intended separation is:

~~~text
EPISODIC     immutable events + OptMem temporal projection
RETRIEVAL    FTS5 + AgentsView
SEMANTIC     TencentDB L1/L2/L3
WORKING      StatePacket / query-time state compiler
PROCEDURAL   existing routine compiler after verified repeated outcomes
~~~

These are different projections over evidence. No second “universal memory database” is being introduced.

`EventLog.get(id)` returns the canonical envelope unchanged. Use
`resolve_payload(event)` for inline data or verified blob contents separately.
OptMem cover/zoom v2 entries carry `event` plus `resolved_payload`; a hydrated
payload is never inserted into a blob-backed canonical envelope. The v2 TREE
manifest points to one complete generation. Shared reader/exclusive publisher
locks protect publication and cleanup, and loaded summaries are checked against
the canonical ledger. Failed publication preserves the prior cover. Old v1 TREE
caches require explicit `nap()` or `rebuild()`; canonical event history is not
migrated or deleted. Structural tests and a cross-process publication smoke
qualify these boundaries, not semantic retrieval quality or live harness use.

## Deterministic compiler before learned policy
### Private read-only memory MCP (#22 / #194)

`python -m z0int.memory_mcp` exposes only `search` and `fetch` via a private **stdio**
process. Memory is source evidence, never permission or a command. No HTTP
listener, tool execution, source mutation, import, setup, refresh, arbitrary
path/URL, or automatic context injection is added.

**Exact recovery qualified for one source only.** The [AgentsView public
Session API](https://www.agentsview.io/docs/session-api/) documents
`GET /api/v1/search/content`, `GET /api/v1/sessions/{id}` and
`GET /api/v1/sessions/{id}/messages?around=N&before=0&after=0`.
Search is FTS on dialogue messages with `reveal=false`, constrained to
an exactly granted project. A message match's native `session_id` and `ordinal` are used only
to locate an original. Before reading any message content or metadata, the
adapter verifies that the returned message's own `session_id` equals the
requested session and that `db.Message.id` is a valid positive int64.
It retains that stable native message ID separately from the ordinal and
includes both in the full, untruncated source locator. Fetch requires the same
native ID; sibling/missing identities and identical-text ordinal replacements
are denied even when their SHA-256 content hashes match. The backend also
checks the session's native project **before and after** the original read.
Thinking, tool-use, system and unknown-class messages fail closed.

When a compatible local AgentsView SQLite archive is available, search ranks
matches across all granted projects together. It returns at most two eligible
messages per session and applies the result limit after that selection, so a
long transcript cannot exhaust a fixed candidate window. Candidate identities
are streamed under the same request deadline; excluded message classes do not
consume the result limit. The HTTP fallback retains its per-project search path.

`source_version` is SHA-256 of the **complete normalized AgentsView
message `content` UTF-8 bytes**, read from the message endpoint (not
a display date and **not** a digest of the underlying provider's raw JSONL).
Search exposes a small, labeled `preview` from that original plus an
opaque HMAC handle. `fetch` checks the current principal grant, re-reads
the original under a bounded deadline, rechecks its scope, compares
`source_version`, and returns a labeled `original_read` with the same
native locator. A changed same-date/same-size message is **stale**; source
outage is **unavailable**; revoked or wrong scope is **denied**; missing results
are **empty**. The fetch output remains bounded and may be marked
`text_truncated=true`; it is never a cached search snippet. Source
identity/revision, not date, enters the provenance-only ContextPacket.
The handle retains a detached snapshot of the native identity, so later backend
record mutation cannot rewrite the identity covered by its signature.

**Explicit principal grants (policy schema v2).** The reviewed operator and
least-privileged ChatGPT profiles must be separately granted; no scope or
credential from one is inherited by the other. The checked-in
`manifests/memory-mcp-readonly.json` grants the operator only the
AgentsView `history` collection at exact project `z0intelligence`,
with `private` classification; ChatGPT's allowlist is **empty**.
Review the archive's suitability and copy that file into private operator-owned
configuration before any connection. Missing/invalid/v1 policy, wildcard,
unknown scope and unreviewed secrets never grant access. AgentsView's
`project` is an archive metadata label, not an independent source
ACL. Its `auth_token` bearer is **broad access to the local AgentsView API**,
not scoped to a provider, session, project or MCP principal. The operator and
ChatGPT grants are separate **facade-side** allowlists enforced only by this
MCP process, through exact project and native message checks. They do not
reduce the privileges of the underlying AgentsView bearer or isolate a
different process with direct API access. Keep that token private, use an
operator-controlled archive/corpus classified for the granted scope, and
validate tunnel identity and consent before exposing stdio. Redaction of
credentials occurs **before** UTF-8 clipping, not in lieu of curation
or provider consent.

`Z0INT_MEMORY_MCP_PRINCIPAL` is required **at process startup** and
must be set by the operator's stdio/tunnel configuration, never by the model or
tool arguments:

```bash
export Z0INT_MEMORY_MCP_POLICY="$PWD/manifests/memory-mcp-readonly.json"
export Z0INT_MEMORY_MCP_PRINCIPAL=operator
PYTHONPATH=src python -m z0int.memory_mcp
```

For a ChatGPT tunnel, use a **separate, explicitly reviewed `chatgpt` grant**
and start a dedicated process with `Z0INT_MEMORY_MCP_PRINCIPAL=chatgpt`.
The server has no OAuth or ChatGPT workspace authentication of its own:
the operator controls stdio exposure and validates the tunnel's identity
and consent independently. Never assume the example grants ChatGPT access.

The backend reads AgentsView's `port` and `auth_token` by reference
from `~/.agentsview/config.toml` and connects only to its loopback port.
It never follows a redirect or arbitrary URL. **ctx, QMD and TencentDB
return `unavailable`** from this facade until their native exact-source
rehydration and authorization interfaces are qualified. ctx remains a
separate, explicit pull via the existing integration path; this server never
runs ctx refresh, import or the latency-sensitive automatic 300ms path.
Do not mistake a lexical match or zero-hit smoke test for model-visible use.

`z0int memory eval` runs the common seam against a cohort, not a native
harness or model. It emits `z0int.memory_seam_diagnostic.v1`, with retrieval,
context support and replay results. It never emits a native-study receipt or
claims provider-input visibility or answer quality. The former `--observed`
boolean override and schema-selection option are removed: caller assertions
are not transport evidence. `--revision` identifies z0int only. Native memory
acceptance still requires a captured provider request, the actual answer and
independent verification against the frozen sources.

Output is capped by policy (default 4096 bytes per record, 32768 bytes per
payload), with a 10-minute, process-bound, 256-handle metadata-only cache
and 30 calls/minute shared across tools. The private 0700 state directory
and 0600 audit log contain only fixed decisions, counts, sizes and keyed
query/handle hashes, never raw prompts or originals. Audit failure blocks
disclosure. Output text is untrusted evidence even when successfully read.

Audit appends take a nonblocking exclusive file lock. A busy writer, unsafe
file, or malformed/unterminated final JSON-object frame returns
`audit_unavailable` without disclosure. Rows are capped at 8192 bytes; only
the final frame is checked on the request path, not the entire historical
ledger. A detected short write is rolled back to its pre-write byte offset
under that same lock, preserving earlier receipts. This is not a claim of
power-loss durability or authenticated offline replay.

A pre-existing damaged tail is preserved, not silently discarded or
acknowledged. Recovery is an explicit offline operator action: stop all
writers, retain a private byte-for-byte copy, validate the complete
newline-terminated prefix, and remove only the verified incomplete suffix
under the file lock. A complete but malformed row or corruption inside the
prefix needs investigation, not automatic truncation. Never count an
unterminated JSON object as a completed receipt.

Each successfully appended row in that **same** private audit.jsonl has
schema `z0int.memory_query_outcome.v1`, tool (`search`, `fetch`, or `invalid`),
and outcome (`hit`, `miss`, `denied`, `unavailable`, `stale`,
`rate_limited`, `invalid`, or `error`). A nonempty successful search or
original fetch is a hit; an empty authorized search is a miss, not an outage.
An entirely policy-filtered search is a denial, not a miss. Unknown error
contents map to `error` in both `decision` and `outcome` without changing the
tool response; arbitrary exception strings or objects are never persisted.
Existing `timestamp`, keyed `query_hmac`, `count`, `bytes`, `denied_count`,
`unavailable_count`, and `decision` remain in the row. Neither raw
queries/handles nor source bodies, paths, or credentials are logged. These
are retrieval dispositions only: they do **not** measure answer quality,
injection, downstream usefulness, or independent verification. No outcome
mutation tool, secondary ledger, or permission is created.

Stdio request validation uses Pydantic's `TypeAdapter` for the MCP message
type, which can be either a root model or a union. The repository dependency
remains pinned to MCP 1.26.0; the same entry point was also exercised with
the separately installed Hermes MCP 2.3.0. Neither smoke activates a client.

Run the focused suites on the MBP. Revision regressions replace the original
and its content digest together, then independently exercise stale handles and
fresh searches; synthetic tests do not claim live-source validation:

```bash
PYTHONPATH=src python -m pytest -q tests/test_memory_mcp.py
PYTHONPATH=src python -m pytest -q tests/test_memory_mcp_backends.py
PYTHONPATH=src python -m pytest -q tests/test_memory_mcp.py tests/test_memory_mcp_backends.py
```

## Current foundation: OpenJev runtime

The local-cognition stack follows a simple rule:

> **ordinary computer science goes in the compiler, not the model.**

Dependencies, legal actions, permissions, budgets, deterministic shortcuts, retries, and hard policy are compiled first. Learned systems only choose among already-legal candidates.

The in-tree cognition stack includes:

- legal-action compilation;
- one provider-neutral candidate schema;
- evidence-based model selectability;
- quality/risk requirements;
- abstention/escalation;
- local/remote SLM adapters;
- shadow cascade execution;
- replayable cognition receipts;
- a serving/probe layer for what is actually resident on the machine.

See [docs/local-cognition-portfolio.md](docs/local-cognition-portfolio.md).

### Private continuous shadow experiments

The hourly cognition tick exact-joins OMP session and native tool-call IDs.
Alongside `omp.tool`, it extracts literal skill choice, path kind, and the
wrapper's error flag. The wrapper flag is **not** task success. Explicit model
arguments form a separate family only when actually observed. Labels/day use
native event timestamps, not the date old traces were ingested.

Small-model decisions use deterministic narrowing followed by one yes/no
logit query per candidate. Models up to 1B have a hard three-candidate cap;
the explicit 1.7B lane has a five-candidate cap. Wide 24/64-option projections
are retired. Tool prompts include the latest visible user goal, the last
visible assistant sentence, and five preceding tools with their intent strings.
Current tool names and arguments never enter the model projection. Missing
yes/no probabilities or a small top margin produce an abstention. Accuracy
and Wilson bounds use covered predictions; coverage reports abstentions.
Explicit binary abstentions are uncovered rows, not predictor errors.

Each family's arms must all plateau over three ticks before search advances
to an untried bounded history, recent-session, or few-shot strategy. Exhausted
below-bar families propose the next exact-label census family. Selection is
not execution: a trial starts only after an observation with that strategy.
Every positive-covered arm emits a scoped, `synthetic:false` finding into the
canonical findings ledger each tick. Historical inference remains
`replayed_L1`; only a prediction stored before its exact label may be live
shadow. Zero-covered arms record a publication blocker, never invented evidence.

Status retains rolling-day errors and separately exposes current-hour,
post-deployment, per-tick, and atomic-RPC counts. Transport errors live in
`error`, while `parse_error` means a model response could not be parsed.
Legacy receipts are classified without rewriting their source bytes.
Shadow receipts retain structured tool identity, never the raw tool-input state.
`operations/shadow-learner/deploy-proposed.sh` backs up private state and
installs a committed content-stamped copy. Extension links must never point
into a task worktree. Existing OMP processes cache extension modules: a fresh
process is required; `/reload-plugins` is not an extension-code reload.
After this shim is loaded once, each worker respawn (including
`/z0int-bridge-reload`) resolves `~/.local/lib/z0int-shadow/current` again.
`Z0INT_INSTALL_ROOT` selects another stable install link; explicit checkout
roots are fallbacks when no installed link exists. The child runs from the
resolved release, whose `DEPLOYED_SHA` becomes each shadow receipt's
`source_revision`. Changing TypeScript extension code still requires a restart.
A broken installed link fails the spawn; it never falls back to an old checkout.


## Compilation is mechanism-neutral

An earlier project story hard-coded a ladder like:

~~~text
deterministic -> mushroom body / fly -> Jev -> SLM -> frontier
~~~

That is now treated as an **experiment hypothesis**, not the product architecture.

The stronger invariant is:

> repeated cognition may move to the cheapest **verified** mechanism that preserves the registered outcome contract, and it must deoptimize when the evidence/state region that justified promotion stops holding.

A promoted mechanism might be a rule, cache, retrieval recipe, linear model, MB/fly specialist, SLM, typed decision backend, or something else. Evolution Lab should compare them; z0intelligence should not prejudge the winner.

The existing mushroom-body/fly work remains valuable research. It just no longer defines the mandatory architecture.

See #56 and #59.

## AODL and Bend

[AODL](https://github.com/kvnloo/aodl) owns portable intent, topology, authority, and budgets. z0intelligence binds concrete routines/models/policies to that contract and writes observed state/outcome events back without extending the AODL ontology.

Current compiler support lives in `src/z0int/aodl.py` and [docs/aodl-integration.md](docs/aodl-integration.md).

The canonical Python structural gate now lives in [aodl_admission.py](src/z0int/aodl_admission.py).
Authority protocol v3 persists admission before remote dispatch/provider ownership;
protocol v2 remains accepted for historical compatibility. Runtime drift is recorded
without rewriting authored intent or minting task success. The host-governed remote
worker is an explicitly enabled OMP canary, not a default all-model fabric.
See [admission](docs/aodl-admission.md), [drift](docs/aodl-observation.md), and
[governed worker](docs/aodl-governed-worker.md).

The #48 Bend experiment established differential parity for its stated laws but
**did not establish a hot-path latency advantage**. Bend remains an independent
specification/CI verifier. The native [Bend/Hermes plugin](https://github.com/kvnloo/bend-native)
provides captured proof inputs, private kernels, receipt replay and optional z0
shadow evidence composition. Its emitted-book PASS is neither source equivalence,
whole-task success nor an AODL admission grant.

## Harness integrations

### Merged / in-tree

- **OMP**: hot-reloadable bridge v2, trace ownership, dispatch/receipt integration, complete-vs-partial provider usage, worker activity projection.
- **Hermes**: adapter surfaces for z0int decisions/context and the broader recovery/evolution research path.
- **DSH / other harnesses**: harness-neutral identity/activity/context seams are present; cross-harness DecisionOpportunity/credit standardization is tracked in #62.
- **Kubernetes hermes-lab**: host z0int authority stays outside the cluster while a hardened executor runs behind the service boundary.

### Integrated experimental surfaces — explicit gates remain

- **AgentWeb / Emma**: bounded context packets, pure planning, opt-in Choice/Noul experiments,
  bridge validation/capabilities, provenance, backpressure and separate observational outcome ledgers.
- **Official TypeSafe SDK**: pinned `typesafe-sdk==0.7.2`, zero SDK retries, served-model
  validation and usage capture. Missing credentials are a blocker, not live model evidence.
- **Agent Orchestrator**: shadow decision/outcome bridge, frozen coverage/economics reports,
  matched comparisons and sidecar experiment-pair registry; no automatic promotion.
- **Hermes / Claude Code**: source-backed State Packet/DecisionOpportunity shadow hooks and
  observed-behavior records. Observed behavior is not an optimal-action label.
- **Memory**: EventLog/OptMem and the scoped memory contract are structural foundations.
  Existing personal stores are not automatically migrated or granted instruction authority.
- **Mutation receipts**: a pure disposition kernel distinguishes provider retries from
  ambiguous world effects; it does not itself retry tools or authorize execution.

Opportunity capture reserves one of four process-shared slots before spawning a
child. The child inherits the locked descriptor and holds it until capture
finishes. When all slots are occupied, the hook records a `fanout_cap` drop
without starting a waiting process. Claude Code, Codex, Grok, and OMP share this
admission path; memory-seam capture uses the same slot pool.
Hook-time identity helpers stay in `harness_id`; structured bridge records live
in `harness_identity` so cold hooks do not load dataclasses and inspection code.

For exact source PRs, integrated/retained status, verification and reproducible test setup,
see [the current reconciliation audit](docs/pr-reconciliation-2026-10-02.md).

## Experience → outcome → learning

The current research direction is not “train on everything the agent did.”

Observed behavior is not automatically optimal, and a successful task does not tell us which component caused success.

The next shared contract is:

~~~text
evidence
  -> state / DecisionOpportunity
  -> legal candidate set
  -> action actually taken
  -> physical execution
  -> independent outcome/verifier
  -> episode + credit evidence
  -> experiment
  -> promotion / rejection / deoptimization
~~~

Key open contracts:

- #53 — DecisionOpportunity + evidence-derived state;
- #54 — experience → outcome → counterfactual credit;
- #55 — selective autonomy: ACT / OBSERVE_MORE / ASK / ABSTAIN / ESCALATE;
- #56 — promotion validity, drift, and deoptimization;
- #57 — determine the natural unit of cognition/control;
- #58 — causal attribution across retrieval → policy → tool → verifier.

Teacher agreement, model confidence, user history, and execution completion are all useful evidence. None is automatically gold.

## Evaluation and promotion

Three repositories deliberately have different jobs:

| Repo | Owns |
| --- | --- |
| [z0evals](https://github.com/kvnloo/z0evals) | frozen studies, exact-ref cohorts, comparative evaluation, public result surfaces |
| [Evolution Lab](https://github.com/kvnloo/evolution-lab) | search, candidate populations, grouped splits, promotion experiments, repair/ABAB loops |
| [Tokenomics](https://github.com/kvnloo/tokenomics) | token/cost/latency/measurement completeness and outcome economics |

z0intelligence supplies runtime contracts, candidate implementations, receipts, replayable state, and promotion/deoptimization hooks. It does not get to grade itself by changing the judge.

Current evaluation surfaces include:

- DecisionBackend Pareto runner;
- observer-first stability/calibration receipts;
- RLCDAlignBench-style detector scoring;
- Image JevBench adapter;
- Julia reproduction and rejection studies;
- contrastive evidence experiments;
- OMP Decision Dataset v2;
- routine/cascade/repair synthetic/unit harnesses.

## Quick start

Python 3.10–3.13.

~~~bash
git clone https://github.com/kvnloo/z0intelligence.git
cd z0intelligence

python -m venv .venv
. .venv/bin/activate
pip install -e '.[test]'

z0int onboard --auto
z0int doctor --json
z0int status
z0int models plan
z0int backends list --json
~~~

Onboarding leaves OMP plugins unchanged by default. To install a specific
plugin, use `z0int onboard --omp-extension NAME` (repeat for each selection).

### Resolve context

~~~bash
z0int context resolve --query "what evidence matters for this task?" --json
~~~

### Inspect the decision backends

~~~bash
z0int backends doctor --json
z0int backends capabilities laya_421m --json
z0int backends eval   --backend laya_421m   --input tests/fixtures/decision_request.json   --json
~~~

### Local cognition

~~~bash
z0int cognition manifest
z0int cognition candidates
z0int cognition serving
z0int cognition probe
~~~

### OptChat fresh-view turns

The OMP extension follows [Victor Taelin's OptChat specification](https://gist.github.com/VictorTaelin/91837951a5ce5b38f341ec1ba1df6449/f51fe5c910427fd6f384d22823140b1693c76207).
Each enabled turn sends the summarized view and queued input in a fresh request.
It does not send the OMP session history. **All OMP sessions share one permanent
OptChat log** under `$Z0INT_HOME/optchat`, or `~/.z0int/optchat`. A new OMP session
does not reset that log.

The main turn uses the active OMP model and selected thinking effort, including
`max` when supported. OpenAI-family requests keep a stable prompt-cache routing
key for the shared log, without retaining a server conversation. A stable key
does not prove a cache hit; verify the real provider's token-usage report.

The related [OptMem repository](https://github.com/VictorTaelin/OptMem) is the tree
memory predecessor. Its `wake` tiling is not OptChat's append-and-merge view.
The creator's [AI-scripts Anthropic adapter](https://github.com/VictorTaelin/AI-scripts/blob/906c4feb8e3345dfab8675c3961182ab8b647637/askai/Vendors/Anthropic.ts)
also documents prefix caching and signed assistant-block replay.

The default compactor is native `anthropic/claude-sonnet-4-6` at medium effort,
through the installed OMP SDK and its normal credential store. It requires a
model context of at least 200,000 tokens. It sends the full prefix and source,
with no input truncation and no provider fallback. This route incurs Anthropic
API cost. Invalid model configuration fails before credential lookup or requests.

~~~bash
omp login anthropic
bun src/z0int/optchat/sonnet.mjs --check
# In OMP:
/optchat on
/optchat status
# Escape cancels the current turn or readiness wait.
/optchat off
~~~

Use `Z0INT_OPTCHAT_AGENT_DIR` for a separate compactor credential/config directory.
Use `Z0INT_OMP_ROOT` for a nonstandard SDK install and `Z0INT_PYTHON` for the Python
runtime. An explicit `Z0INT_OPTCHAT_HOST` selects an OpenAI-compatible compactor
instead. Set its model with `Z0INT_OPTCHAT_MODEL` and provide enough context.
An undersized endpoint fails visibly rather than discard the middle of the input.

The resident wakes on new work, runs at most eight independent compactor jobs,
and performs short joins without a model call. Failed nodes retry after ten
seconds. A missing credential or provider error appears during the readiness
wait. Escape and `/optchat off` preserve unanswered input exactly once.
Native SDK stream errors and empty completions fail the node. Hidden SDK
resampling does not run inside a compactor attempt.

Reproduce the behavioral and emitted-wire checks:

~~~bash
PYTHONPATH=src python -m pytest -q tests/test_optchat.py
bun scripts/prove-optchat-conformance.mjs all /tmp/optchat-omp-proof
bun scripts/prove-optchat-sonnet.mjs /tmp/optchat-sonnet-proof
~~~

The native Sonnet probe requires Bun and Node. Node hosts the loopback HTTP
fixture so that an aborted pre-header request emits a real socket-close event.

These probes use the real resident and installed provider SDK, but scripted
loopback responses. They prove contracts, cancellation and serialized cache
boundaries, **not** real summary quality or provider cache hits.
`Z0INT_OPTCHAT_DUMP` captures final provider payloads for a private trace. Those
payloads contain chat content. Keep them outside the repository.

### Image decision experiments

~~~bash
pip install -e '.[image]'
z0int-image-decide run --input benchmarks/image-jev-bench/red-square.json
~~~

### Historical OpenJev compatibility

The older direct-logit/trainable paths still exist:

~~~bash
openjev-score --help
openjev-train --help
openjev-eval --help
~~~

They are compatibility/research surfaces, not the product identity.

## Repository map

| Path | Purpose |
| --- | --- |
| `src/z0int/intelligence.py` | pure capability selection |
| `src/z0int/dispatch_authority.py` | canonical execution/replay authority |
| `src/z0int/context_resolve.py` | provenance-backed context resolution |
| `src/z0int/file_search.py` | resident FFF path/content index + watcher; bounded retrieval only |
| `src/z0int/cognition/` | deterministic legal-action compiler, candidate model, cascade, serving, shadow path |
| `src/z0int/backends/` | typed decision backends + benchmark harness |
| `src/z0int/functions/` | named function-level verification implementations |
| `src/z0int/bridge/` | resident hot-reload OMP bridge runtime |
| `src/z0int/routines.py` | compiled routine surface |
| `src/z0int/cascade.py` | promoted cascade surface |
| `src/z0int/refinement.py` | counterexample-driven repair |
| `src/z0int/abab.py` | ABAB experiment/archive helpers |
| `src/z0int/aodl.py` | AODL plan/runtime binding |
| `src/z0int/worker_routing.py` | bounded text-worker execution policy |
| `src/z0int/worker_activity.py` | harness-independent activity projection over receipts |
| `src/z0int/tokenomics_emit.py` | measurement-state-aware Tokenomics emission |
| `src/openjev_phase1/` | historical OpenJev runtime/training compatibility |
| `omp-extensions/` | OMP integration surfaces |
| `adapters/`, `harness-adapters/` | external harness bridges |
| `deploy/k8s/` | host-authority / cluster-executor deployment lane |
| `benchmarks/` | replayable benchmark/data-extraction surfaces |
| `manifests/` | models, capabilities, routing, and evidence contracts |
| `zer0.repo.yaml` | z0archy exact-ref architecture metadata |

## Boundaries

z0intelligence intentionally does **not** own:

- the canonical AODL ontology or user-authored authority;
- harness scheduling/execution semantics;
- source memory database authority;
- Tokenomics measurement semantics;
- Evolution Lab promotion authority;
- Kerdoios provider/quota placement;
- “success” merely because a model or worker finished.

Unknown stays unknown. Partial measurement stays partial. Execution completion stays distinct from verified success.

## Related repositories

- [z0](https://github.com/kvnloo/z0) — broader control-plane integration and architecture
- [z0evals](https://github.com/kvnloo/z0evals) — frozen evaluations and public results
- [Evolution Lab](https://github.com/kvnloo/evolution-lab) — search, evolution, promotion, repair
- [AODL](https://github.com/kvnloo/aodl) — portable intent / authority / orchestration IR
- [Tokenomics](https://github.com/kvnloo/tokenomics) — measurement/economics
- [Kerdoios](https://github.com/kvnloo/kerdoios) — residual compute and quota placement
- [frontier-kb](https://github.com/kvnloo/frontier-kb) — research evidence / claims / kill criteria
- [z0archy](https://github.com/kvnloo/z0archy) — whole-system architecture graph
- [Hermes Agent](https://github.com/NousResearch/hermes-agent) — execution/state/tool harness
- [Oh My Pi](https://github.com/can1357/oh-my-pi) — coding harness and local judgment surfaces
- [Agent Orchestrator](https://github.com/kvnloo/agent-orchestrator) — durable session/execution orchestration lab
- [AgentWeb](https://github.com/kvnloo/agentweb) — downstream Emma/AgentWeb integration lab

## Principles

1. **Evidence before authority.** A model probability cannot mint permission or truth.
2. **Independent outcomes before promotion.** Execution, teacher agreement, and historical behavior are not gold by themselves.
3. **Compiler before model.** Deterministic legality, permissions, dependencies, and budgets stay deterministic.
4. **Mechanism-neutral optimization.** Promote the cheapest verified mechanism; deoptimize when its validity assumptions break.
5. **Private by default.** Personal evidence and checkpoints stay local unless explicitly sanitized.
6. **One semantic receipt spine.** Extend existing receipts rather than inventing parallel telemetry/outcome systems.
7. **Fail explicit.** Unknown, partial, stale, contradicted, unavailable, and ineligible are first-class states.
8. **Exact revisions matter.** Experiments, adapters, and cross-repo claims must bind to the exact code/data revision that produced them.

Project code is released under the [MIT License](LICENSE). Third-party models, runtimes, datasets, and trademarks retain their upstream terms; see [THIRD_PARTY.md](THIRD_PARTY.md).
