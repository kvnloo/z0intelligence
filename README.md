# z0intelligence

**A provenance-backed cognition and control plane for agent systems.**

z0intelligence turns evidence, runtime state, and observed outcomes into bounded decisions, auditable execution choices, and eventually cheaper verified mechanisms. The goal is not “replace the frontier model with a tiny model.” The goal is to make expensive generic inference do less work **without confusing confidence, completion, or memory with truth or authority**.

The Python import/CLI namespace remains `z0int`; the distribution metadata still carries the historical `openjev-phase1` name for compatibility. Historical OpenJev code remains in-tree, but it is now one substrate inside the larger z0intelligence system.

> Core invariant: **evidence ≠ claim/state ≠ authority ≠ action ≠ outcome ≠ learned procedure.**

## What exists on master

The current repository is much more than the original OpenJev prototype.

| Surface | Current role |
| --- | --- |
| Provenance-backed context and state | ContextPacket resolver plus source-backed State Packets, question-scoped DecisionOpportunities and explicit invalidation; retrieval is not authority |
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
  requires explicit `divergence <= aa_noise_floor`. Outcome promotion additionally
  requires metric `task_success` and `outcome_ledger_verified=true`.
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

### Agent outcome evidence

`policy ingest --sources specs.json` accepts source specs with
`kind=agent_outcomes` for structured JSON/JSONL exports
and frozen SQLite `runs` tables. Duplicate runs (`dup_of`) are excluded.
SQLite inputs with a nonempty WAL are rejected; export a consistent snapshot.
Discovery includes `operations/agent-outcomes/ledger.sqlite`.

Pushes, PRs, passing tests and ledger success labels are proxy evidence.
The adapter stamps `evidence_kind=proxy`, clears outcome verification and
disables promotion. The compiler also excludes proxies independently.
An exported z0eval receipt cannot restore promotion credit to these proxies.

Product evidence requires `verifier_receipt` with an absolute `path` and the
file's SHA-256. The file must contain either a verified `z0.product_check.v1`
receipt or an AODL `stateUpdate` selected by `event_id`. Its run/trace ID must
match the outcome row. AODL events also require a source hash consistent with
document provenance when present. Both forms require a verifier, boolean
success, receipt ID and structured `observed_target`: a product/UI/API/runtime/
filesystem/database target ID, `observed=true`, and nonempty observed `state`.
Tests and publication signals alone are not observed product targets.

A valid receipt emits `evidence_kind=product_outcome` and one `task_success`
observation (`n=1`), using the receipt's success rather than the ledger claim
or any asserted aggregate metric. Missing, mismatched or malformed receipts
remain proxies with an explicit reason. Receipt hashes establish provenance,
not independent trust in the verifier's implementation.

### ChoiceLadder option capacity

The pure compiler emits an evidence-bound `option_capacity` map. Each key is
the canonical JSON string of the exact model, quant, engine, backend and device
scope. `option_capacity_key(scope)` creates this lossless key. No capacity
credit transfers between different physical scopes.

Each scope contains risk entries `read`, `mutating_reversible`, and `destructive`.
Each entry contains `allowed_k`, `finding_ids`, `wilson_lower`, `task`,
`bar_derived`, `inputs` and `expires_at`. The required tasks default to T1–T4
and are configured with `option_capacity_tasks`; observed tasks remain required.
Routing promotion thresholds remain unchanged.

Configure `option_capacity_costs[risk]` with nonnegative finite `cost_call`,
`cost_frontier`, positive `cost_error`, and `abstain_rate` in [0,1], all using
the same cost unit (for example O14 pace-adjusted latency, with token burn
already included). A cell passes only when
`cost_call + (1 - p_lower) * cost_error + abstain_rate * cost_frontier < cost_frontier`.
Thus `bar_derived = 1 - (cost_frontier * (1 - abstain_rate) - cost_call) / cost_error`;
equality does not route. `p_lower` describes the answered subset, not abstentions.
Destructive decisions never route to an SLM, regardless of cost or accuracy.

Absent measurements use explicitly `unmeasured` conservative normalized defaults:
call 0.1, frontier 1, error 20 for reads and 100 for reversible mutations,
abstention 0. These are assumptions, not observed timings. Measured inputs must
include `measured=true` and canonical `finding_ids` for fresh measured
`choice.error_cost` findings with matching `subject.risk_class` and metric
names/values for all four inputs. Their earliest expiry also bounds the grant.
Observed frontier recovery is a proxy for SLM error cost, not an executed shadow
intervention; record that limitation in its source evidence. Shadow agreement
alone cannot measure recovery cost.

With no explicit cost override, compilation automatically adopts the latest fresh
measured finding for each of the four cost fields at the exact physical scope and
risk class. All four must declare the same `metric.cost_unit`, either
`latency_ms_lambda0` (the approved formula with zero pace weight) or `o14_score_ms`.
Partial, expired or mismatched units do not replace the conservative defaults.
Finding `subject.job` and source adapter identify `observed_frontier_recovery`,
`observed_frontier_turn_timings`, or the measured scorer-call protocol. Proxy
limitations belong in `scope_note`/`baseline`; they never become accuracy credit.
No runtime switch is required when complete matching evidence arrives.

Capacity evidence requires `subject.family=choice.option_capacity`,
`subject.job=ChoiceLadder`, `subject.task` (or `metric.task`), metric
`choice_accuracy`, integer `metric.option_count >= 2`, `ci_method=wilson95`,
positive `n`, and measured, unexpired observations. The compiler compares the
smaller of the stated lower bound and a recomputed Wilson lower bound against
the bar. An explicit correct count must agree with accuracy and sample count.
The latest measured observation for a task and k supersedes older success.

The compiler selects the largest passing k per task. The allowed capacity is
the minimum across every required task. Missing, pending, expired, inference,
ineligible or inconsistent evidence never counts as success. Any unsupported
task limits capacity to 0 (no SLM request); unknown scopes also default to 0.
Proof IDs and the earliest selected proof expiry accompany positive credit.
O13 readers consume this content-addressed projection, not a live findings scan.

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

## Deterministic compiler before learned policy

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
