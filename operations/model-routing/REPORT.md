# Model routing — O14

## Approved objective and provenance

Verbatim proposal recovered from OMP session `01a16527-824c-7714-8205-9a2d0d5eff38`, ordinal 4409, 2026-10-24 03:32:22 UTC (Kevin's requested inputs at ordinal 4408; response at 4410):

```text
latency_u = TTFT_u + out_tokens_t / tok/s_u
pace_u = burn_u * tokens_t / (remaining_u / time_to_reset_u)
score_u = latency_u * (1 + λ * pace_u) / p_accept[u, task_class(t)]
```

Minimize this score among eligible offers. Kevin confirmed this is the agreed optimization formula on 2026-10-24 through the parent assignment. Source: [recorded session](http://127.0.0.1:8080/sessions/omp/01a16527-824c-7714-8205-9a2d0d5eff38), ordinals 4408–4410, plus current Kevin confirmation. The earlier Kerdoios weighted cost/capability/p50/concurrency score remains an existing objective, not this formula.

Lambda: `λ=1`, **unmeasured**, explicit configuration default authorized in the 2026-10-24 parent assignment. A non-default value requires measured source and timestamp; no claimed calibration exists. TTFT is seconds, decode throughput is output tokens/second, reset horizon is hours, and the burn/balance quota unit must be retained together. The equation is implemented verbatim, not silently dimensionally reinterpreted. A benchmark's aggregate score is not automatically task acceptance probability.

## Sources and refresh

The refreshable evidence catalog is `operations/model-routing/catalog.json`; generator: `python3 operations/model-routing/generate_catalog.py --refresh-public`. Public refresh performs unauthenticated metadata GETs, not inference or account changes. SQLite inputs use read-only connections. Every number carries its source ID, observation timestamp, unit and measurement/derivation basis; unresolved numbers are JSON null.

Canonical provider knowledge is already documented in Hermes, not rediscovered by this project:

- `/workspace/hermes-home/profiles/clean/skills/research/offline-rate-limit-catalog/SKILL.md:20-48`: catalog semantics, shared windows, provenance, no invented limits, canonical extractor and Kerdoios authority.
- `/workspace/hermes-home/profiles/clean/skills/research/offline-rate-limit-catalog/references/free-tier-providers.md:10-38`: route/checkpoint identity, data restrictions, free/credit distinctions, Groq per-model dimensions, OpenRouter daily cap and Vercel eligible subset.
- `/workspace/hermes-home/profiles/chiefstaff/skills/autonomous-ai-agents/hermes-agent/references/providers-and-models.md`: provider IDs, transports and credential ENV names. The managed skill points here rather than copying the provider inventory.

The catalog joins OMP `model_perf`/`usage_history`, the offline rate ledger, Kerdoios observations, agent-outcomes, cached provider inventories, documented provider terms and public metadata/evals. TTFT quantiles use actual retained TTFT samples, not total request duration. OMP aggregate TTFT is labelled mean, never p50/p95. Historical assistant-message samples retain their timestamp/granularity caveat. Task-outcome proxies and HTTP success do not become verified product acceptance. Shared subscription balances are not attributed to a model merely because its provider matches.

## Catalog table

The machine-readable table is `catalog.json:models[]`, keyed by `(provider, model)`, with `pricing`, `performance`, `burn`, `task_classes`, `benchmarks`, `capability_floor` and quota-pool references. `quota_pools[]` is explicitly shared-account evidence, not model membership proof. The final documentation-seeded metadata refresh is **2026-10-24T23:53:24.587356+00:00**, 2,109 models and 10 shared quota pools.

| Provider | Catalog models | Known TTFT p50 | Known TTFT p95 | Known tok/s | Rows with public benchmark |
|---|---:|---:|---:|---:|---:|
| Anthropic subscriptions | 30 | 4 | 4 | 4 | 2 |
| OpenAI Codex subscriptions | 45 | 6 | 6 | 6 | 0 |
| OpenCode Zen free only | 40 | 0 | 0 | 0 | 0 |
| OpenCode Go free only | 0 | 0 | 0 | 0 | 0 |
| Nous Portal | 905 | 0 | 0 | 0 | 1 |
| OpenRouter | 472 | 2 | 2 | 2 | 1 |
| Vercel AI Gateway | 581 | 0 | 0 | 0 | 1 |
| Cerebras | 9 | 0 | 0 | 0 | 0 |
| Groq | 27 | 0 | 0 | 0 | 0 |

Zero in this table means **zero rows with that measurement**, not a zero latency or throughput. The public benchmark is the sourced Haiku 4.5 vendor SWE-bench Verified claim (73.3%), replicated only as an exact-family public claim, not a served-alias result. All task-class `p_accept` values and model-specific quota attribution remain unknown. Therefore the current evidence does not authorize scored O14 credit for any real offer. The runtime and tests support scored offers once genuine compatible evidence exists, without fake priors.

The referenced Hermes docs contain no numeric OpenCode Go DeepSeek daily cap or exact free Go model proof. `provider_constraints["opencode-go"].documented_model_constraints` preserves the daily-window constraint with null limit/remaining/reset and its file:line citation. The roughly 35-job Zen historical burst is not a published request/day cap. No Go row or cap was invented.

Read-only exhaustion audit: the canonical offline `rate-limits/catalog.sqlite` has no `opencode-go` records. It retains five `opencode-zen`/`deepseek-v4.1-flash-free` CLI diagnostic observations with `kind=rate_limit_429` but **null HTTP status**, account hash, request ID, retry-after, reset and window. Example: event `42eef4a4cce968e944c6837d`, source `32fdb9bc5c82af84dc8247c4`, offset 3 in kitchen `scan-wave2/raw/coordination-bus.txt`. These are not five deduplicated physical HTTP calls. `coverage-gaps.json` reports 45 diagnostic files, not a same-account request/token count. Hermes clean session `20261019_203027_e67d6d`, tool message 168693, 2026-10-20T01:40:21.638Z describes a successful Zen inventory call; its six RPM is client admission policy, not a provider cap. No first observed **HTTP 429** or reset/count is recoverable for Go. All requested Go measurements therefore remain unknown rather than borrowed from Zen or fabricated. No inference requests were issued to exhaust an account.

OpenCode rule: **Only sourced OpenCode Go/Zen free models are allowed.** A paid or unproven OpenCode model is an O14 violation. A `-free` suffix alone is not proof. Catalog availability is not entitlement, current quota or successful inference. DeepSeek Go/Zen route daily exhaustion must remain a quota window with its documented limit or unknown bound; it is not the direct DeepSeek provider's allowance. Cerebras is a speed provider, not a permanent free tier ($5/30-day trial/card terms must remain visible).

## Law and integration

`src/z0int/policy/model_routing.py` compiles reviewed routing bundles into the existing content-addressed `decision-policy.json` through `CompilerConfig.model_routing`. Runtime reads that compiled artifact; it does not rescan catalogs, benchmarks or histories. Explicit task-class capability floors use Kerdoios `hard_filter`. Privacy, tool, context and reliability constraints remain Kerdoios constraints.

`kerdoios/routing_objective.py` in the separate Kerdoios feature branch owns the sole new optimizer, `plan_routing(offers, req, metrics, lambda_value=1, lambda_evidence=...)`. O14 consumes and enforces its minimum; it does not reimplement scoring. Task-specific verified acceptance can update the benchmark prior only through the existing blend weight and `MIN_OBSERVATIONS=5`. Unknown TTFT/throughput/burn/acceptance is unscored, usable only without O14 credit; it is never guessed or labelled optimal.

The worker transport gate stamps `rule_id`, provider/model, policy/snapshot IDs, exact formula inputs, result, lambda provenance, selected offer and fallback failures. A nonminimum scored offer needs a recorded reason; capability/quota/OpenCode restrictions cannot be waived by a reason. A 403/429 failure excludes that actual provider/model before replanning and is retained honestly. OpenRouter fallback cannot be relabelled Groq/Cerebras.

Subscription admission checks every supplied shared quota window, observation age, burn until reset and a bounded next request. The existing canonical admission receipts hold conservative upper-bound charges under one shared-group lock, so concurrent routed calls cannot double-book those windows. An elapsed reset requires fresh evidence; no refill is assumed. Unknown subscription bounds or unsupported consumption units refuse admission rather than promise safety. This guarantee covers participating routed calls and documented upper bounds; unrelated external account usage is not observable or controllable here.

`z0int policy audit-routing --receipts PATH --json` audits physical model-call receipts against archived content-addressed policies, recomputes allocations and flags missing/changed inputs, false unknown-metric credit, dishonest identities, nonminimum/dominated selections without reason, and OpenCode violations. An empty audit is unavailable, not success.

`bend/model_routing/{routing,LAWS,PROOF}.bend` proves the abstract admission projection: capability and quota are necessary; a scored unreasoned choice is minimum; unknowns never earn credit. These are not floating-point correctness or real-provider claims. `mutations.py` breaks each gate independently. Python fixtures exercise the actual Kerdoios objective and transport regressions.

## Verification and publication evidence

No local tests, builds, linters or inference requests were used. Phone job **`20261024-185511-aafb`** completed with **160 z0intelligence tests passed and 26 Kerdoios tests passed** (exit 0). This includes the new formula, unknowns, exact task-class acceptance findings, first-real-429 importer, OpenCode restriction, capability/burn/shared-reservation gates, honest 403/429 fallback and existing transport/authority regressions. Log: `~/.local/state/fleet/runs/20261024-185511-aafb-omarchy-phone.log`.

The remote staging includes real pinned test dependencies: canonical AODL `68231658f0ec0338464c0916a2329b9587444312` and the existing Kerdoios quota subpackage from `68fc2a9ac88ab8623a31ec23696904315a56b495` (introduced by `46b8707`). The latter is still on the allocator's separate capacity feature branch and is needed by pre-existing Groq admission tests. It was staged as a test dependency, not silently vendored into this PR or merged into the live plugin. The test source roots are `z0/`, `allocator/`, `aodl/`; the outer allocator checkout deliberately does not shadow the Python `kerdoios` package.

Retained failed attempts: `20261024-184040-4d1f` and `20261024-184323-9041` failed at pytest collection, not suite completion. `20261024-184741-663a` reached the suites (155 passed, 9 failed) and exposed a fixed catalog test epoch argument plus absent canonical dependencies/import-mode isolation in the initial test harness. No failing test was removed or weakened. Initial MBP job `fleet-mbp-8b71e773067a4edc` failed before proof execution because its compiler was not transported. A new equipped MBP gate was submitted with the actual installed Bend toolchain and `--wait -1`; its final receipt is recorded below. Live Hermes/Kerdoios state is unchanged.

Equipped MBP job **`fleet-mbp-0b039525619e4b5c`** completed with exit 0: reference **ALL PROOFS CHECK**, and **4/4** capability/quota/argmin/unknown-credit mutants rejected with **SOME PROOFS FAIL**. Log: `~/.local/state/fleet/runs/fleet-mbp-0b039525619e4b5c-mbp.log`. Proof execution used the transported installed Bend compiler, not a nonexistent container HOME binary. No local compiler run or provider inference was used.

## Automatic evidence updates

`--findings PATH` defaults to `~/.z0int/findings/findings.jsonl`. The generator reuses #211's canonical hash-chain reader, without repairing or writing the source ledger. Eligible findings have family `model.routing.acceptance`, job `ShadowEvals`, an exact task class and actual provider/model, metric `acceptance_rate`, accepted/n counts, a consistent Wilson interval, nonfuture/unexpired timestamps and nonsynthetic measured evidence. Scoped `rubric_draft_quality` findings become benchmark priors only, with `promotion_eligible=false` and explicit **not product success** provenance. Generic proxy outcomes do not qualify. Verified product priors must additionally pass the existing canonical receipt verifier. The selected prior appears automatically at `models[].task_classes[task].p_accept`, including finding ID, scope, count, interval, timestamp and expiry. This does not change the unknown state before a real eligible finding exists.

The first genuine Go/DeepSeek HTTP 429 is now a **pending automatic measurement**, not a task to exhaust an account. The worker records every actual physical attempt through `kerdoios.observed.record_routing_attempt` in the existing observation ledger. These `routing_quota` records retain real status, nullable measured tokens, known quota group and safe numeric/ISO quota headers. They are excluded from capability, cost and acceptance aggregates. Catalog refresh imports the first exact `opencode-go` DeepSeek 429 into `provider_constraints["opencode-go"].observed_first_429`; until then `pending_measurement` remains explicit. Only an ISO `x-ratelimit-reset-requests-day` header establishes the observed daily reset. Generic resets, retry-after and relative durations do not. Distinct traced request/token counts require the same known quota group and are labelled as a recorded interval, **not complete daily account coverage or a measured cap**. No Zen-to-Go attribution occurs.

The managed skill is installed at `~/.omp/agent/managed-skills/model-routing/SKILL.md`; its reviewable source lives under `operations/model-routing/managed-skill/SKILL.md`. StartupPacket integrated the six source-linked formula/catalog/OpenCode lines in Draft PR `kvnloo/z0intelligence#218`, commit `436d573`. That separate startup generator owns its own gate and does not imply a live policy rollout.
