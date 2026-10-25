---
name: model-routing
description: Uses Kevin's approved Kerdoios optimization formula, compiled O14 policy and sourced quota catalog when selecting LLM providers or models, checking burn rate, TTFT, throughput, capability, free tiers or subscription capacity.
---
# Model routing

## Read the existing authority first

- Provider limits, credits, privacy and route identity: `/workspace/hermes-home/profiles/clean/skills/research/offline-rate-limit-catalog/SKILL.md` and `references/free-tier-providers.md` (2026-10-22).
- Exact provider IDs, transports and ENV names: `/workspace/hermes-home/profiles/chiefstaff/skills/autonomous-ai-agents/hermes-agent/references/providers-and-models.md`. Do not rediscover or duplicate this inventory. Never print credentials.
- Evidence: `operations/model-routing/catalog.json` in the z0intelligence checkout; report and provenance: adjacent `REPORT.md`. Refresh with `python3 operations/model-routing/generate_catalog.py --refresh-public` (metadata only). Every number has source/time/unit; missing is unknown.

## Approved objective

Verbatim OMP `01a16527-824c-7714-8205-9a2d0d5eff38`, ordinal 4409, 2026-10-24 03:32:22Z; Kevin confirmed the agreement on 2026-10-24:

```text
latency_u = TTFT_u + out_tokens_t / tok/s_u
pace_u = burn_u * tokens_t / (remaining_u / time_to_reset_u)
score_u = latency_u * (1 + λ * pace_u) / p_accept[u, task_class(t)]
```

Minimize among eligible offers. `λ=1` is configured and **unmeasured**, not a calibrated result. Keep hard capability/task, privacy, context, tool and reliability floors. Unknown TTFT/tok/s/burn/acceptance means unscored: an offer can run, but cannot claim O14 optimization credit. Public benchmark scores and HTTP completion are not automatically task acceptance probabilities. Verified task-class acceptance updates use Kerdoios's existing blend after at least five observations.

## Provider and quota discipline

Free-provider candidates: OpenCode Go/Zen, Nous Portal, OpenRouter and Vercel AI Gateway. These names do not establish that every model is free. **OpenCode permits only sourced Go/Zen free models; any other OpenCode route violates O14.** OpenCode DeepSeek daily limits are route limits, not direct DeepSeek allowances. Follow documented reset windows; do not invent a numeric cap.

Speed candidates: Cerebras and Groq. Cerebras trial/card/expiration terms are not a permanent free tier. Subscription candidates: luna/terra/haiku/sol through their OMP login pools. Names are aliases, not universal provider model IDs; resolve exact identity from the referenced provider inventory and catalog. Respect the actual task's allowed model set (a Luna/Terra/Haiku canary does not authorize Sol).

Do not exhaust a shared subscription window before reset. Missing balance/reset/burn/bounded-consumption evidence is not safe capacity. Never assume an elapsed reset refilled an account. Preserve each quota dimension and pool membership; provider-level balance is not per-model attribution. On 403/429, record the actual failed provider/model, then replan. Never relabel an OpenRouter attempt as Groq/Cerebras.

## Query the one allocator

In the Kerdoios checkout/environment, `python3 -m kerdoios --help` lists the installed CLI; use its existing `plan` and `capacity` commands according to that version's help. The established feedback loop is plan → execute → `python3 -m kerdoios record` → replan. Do not write a second optimizer or trust planner metadata as execution proof.

Approved-objective Python API: `kerdoios.routing_objective.plan_routing(offers, requirement, metrics, lambda_value=1, lambda_evidence=...)`. Metrics retain source/timestamp in the compiled O14 bundle. z0intelligence compiles it through `CompilerConfig.model_routing`, then enforces and receipts each physical worker call. Audit with `z0int policy audit-routing --receipts PATH --json`; it recomputes from the archived content-addressed policy, not a live benchmark scan. A reason can explain a nonminimum scored choice, but never waive capability, quota, privacy or OpenCode restrictions.

Current feature source: `/mnt/zer0models/z0-wt/model-routing-o14`; Kerdoios objective branch: `/mnt/zer0models/z0-wt/kerdoios-routing-o14`. These are review branches, not evidence of live rollout. Canonical provider facts remain the Hermes files above.

Scoped benchmark acceptance enters the catalog automatically from the canonical
#211 findings chain (`--findings PATH`). `model.routing.acceptance` / `ShadowEvals`
rubric findings are draft-quality priors, never product-success promotion proof.
The first real Go/DeepSeek HTTP 429 is captured by the Kerdoios routed-attempt
recorder into its existing observation log. Catalog refresh retains actual status,
explicit daily reset and measured counts when available. It labels incomplete
account/window coverage. Do not send requests merely to exhaust a limit.
