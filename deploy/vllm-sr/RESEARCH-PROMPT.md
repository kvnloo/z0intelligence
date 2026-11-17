# Research + trial prompt: Turbo orchestration (OMH × vLLM Semantic Router × Kerdoios × z0intelligence)

Run at max effort in Hermes profile `clean`. Kevin approved running this end to end without per-step OKs. Report once at the end, with receipts.
Ask him only before you change one of his explicit constraints, spend money beyond his subscriptions, or touch another session's live work.

## 0. Ground first (don't redo work that exists)
Read these before designing anything:
- /mnt/zer0models/workspace/zer0/services/vllm-sr-turbo/PLAN.md (v3) and skill `vllm-sr-turbo-router`.
- Turbo v1, already built: z0intelligence RFC #175, PR #176 (draft, head 5e8b6d6), Kerdoios PR #64 (draft). Artifacts in /mnt/zer0models/turbo-mode-20261019/.
  The PR #176 body has the A/B result. Key fact: **peak overlap was 1 in every cell**, so turbo never actually ran in parallel.
- Live router: k3s ns `vllm-sr`, svc 10.43.216.55:8899, config in services/vllm-sr-turbo/config-k8s.yaml.
- z0intelligence live capacity: `curl 127.0.0.1:11501/v1/providers` (caps and health for 10 providers). Also `~/.z0int/policy/decision-policy.json` (z0int-policy.timer runs every 15 min)
  and manifests/worker_routing.v1.json (free_only, validated_free_routes, fallback_order).
- Burn rate: `burn-pace --json`. It currently sees only codex, and its Claude probe fails with "OAuth token missing". Fix or flag that before trusting its posture.
- Kerdoios: README, ROADMAP, UPSTREAM, kerdoios/optimize.py (Pareto + N-worker portfolio, perishable quota) and open issues #38 #41 #45 #50 #51 #55 #58.
- vLLM SR features: selection algorithms (latency-aware, multi-factor, knn/mlp/automix), looper algorithms (confidence = small_to_large escalation, fusion = parallel panel + judge, ratings, remom, workflows),
  cascade, the context-compression plugin, remote-classifier contract. The source is cloned at $TMPDIR/sr (otherwise reclone with --depth 1).
- oh-my-hermes = rlaope/oh-my-hermes (5k★). Learn its `/dag-ulw` graph runs, per-category model chains with fall-through on rejection, the verifier node and the `omh model` picker.
  Read its source (src/routing/task_cards.py, MODEL_OPTI.md, benchmarks/live-model-tools) before running it.

## 1. Ultimate goal: mine it, don't assume it
Build an evidence-backed statement of what Kevin wants from this whole system. Every claim needs a dated quote from him.
- Sources:
  - every profile's state.db, user rows only (FTS)
  - `~/.hermes/profiles/clean/pastes/`
  - ops/unified-memory/OWNER-CANON.md
  - ctx (`CTX_DATA_ROOT=/mnt/zer0models/host-offload/home_kvn_.ctx ctx search ... --since 60d`)
  - omp session jsonl
  - the ChatGPT export at /mnt/zer0models/sft-svlm/data/raw/openai-extracted/
  - z0int ROADMAP north star, Kerdoios README, AODL
- Quotes already found, to start from:
  - 10-19: turbo is "part of z0intelligence… emulate GPT ultra fast… Groq + Cerebras + DeepSeek (OpenCode) + Opus 5.5 as meta agent… maximizing free tier models & speed".
  - 10-17: use Nous, Vercel, OpenRouter and OpenCode free tiers, with rigorous verification.
  - 10-20: burn-rate aware; paid subs hit ~100% exactly at reset, and free models offload work to add speed.
  - 10-20: free models get only easy, well-defined tasks and never write code.
  - 10-10: "smallest changes that instantly compound… GPT overengineers and never pushes for results".
  - 11-01: turbo is for parallel small calls; main turns stay on Opus.
- Output: a one-paragraph goal, a ranked objective list, hard constraints vs. preferences, and the things he repeats because agents keep missing them.
  Label anything you infer as inference.

## 2. Audit the existing algorithm (O14)
"The algo" is O14, agreed 2026-10-24: score = (TTFT + out_tokens/tps) × (1 + λ·pace) / p_accept, with λ=1 (unmeasured).
- Optimizer: Kerdoios `routing_objective.plan_routing` (#69).
- Compiled policy and audit: z0intelligence#222 `operations/model-routing/REPORT.md`.
- Lane capacity: `kerdoios capacity` (#66).
- Lanes and limits are already recorded in skills `token-thrifty-agent-routing` and `offline-rate-limit-catalog`. Read them first.

The rest of this section predates finding O14. Use it to test O14, not to replace it:
the pace term already acts as the burn-rate shadow price. The open questions are its unscored inputs and how the router executes its ranking under fanout (ABAB state: services/vllm-sr-turbo/.hermes/abab/turbo-router-context.json, H1 vs H2).

Find out exactly which algorithm "the algo we made" refers to. Likely candidates are Kerdoios `plan()` (cost-first Pareto portfolio), turbo `choose_turbo_offer` (Mode.FAST, 1 call per provider, 6 RPM),
burn-pace (posture and offload bands) and z0int free_only admission. Confirm from history rather than assuming.
For each: what does it optimize, what inputs does it actually use live, and which inputs are fixtures or unknowns?
Check it against the objectives from §1: max total tok/s across providers, min cost, burn-rate respect, TTFT, verified quality, privacy (no private source to free cloud models).
Show with real numbers where it fails. Example: concurrency 1 per provider bounds throughput at the sum of single-stream decode rates. Use Little's law with the measured latencies.

If it doesn't hold up, make the case for a new one. Hypothesis to test, not adopt:
- one scheduler over lanes, each lane with a token bucket for each limit (RPM, TPM, RPD, TPD, concurrency) taken from z0int caps, provider headers and the Kerdoios #51 ledger
- a lane's cost = $ + λ_sub·quota%, where λ comes from burn-pace (≈0 when under pace and the quota expires at reset, high when over pace), + a latency/TTFT penalty
- hedged requests ("tail at scale") for TTFT-critical calls, i.e. race
- fanout packing that respects TPM, with 429/413 spilling to the next lane
- quality gates: draft→check, with a frontier model or Jev/Laya as checker

Compare it against alternatives you find (RouteLLM-style cascades, vLLM SR automix/latency-aware, OMH chains, LiteLLM routing).
Keep z0int → Kerdoios → executor separation (#55): z0int picks capability, Kerdoios picks placement and capacity, and the router or OMH executes.

## 3. Trials. They must measure speed, not HTTP 200.
Install OMH into a **disposable** profile (`hermes profile create omh-trial`, cloned config with no memories), never `clean`.
Pin an exact commit, read install.sh before running it, and don't use `curl | sh` blind.
Run CPU-heavy work on MBP through fleet-build (owner rule). delegate_task bills Kevin's Codex sub, so stay within burn-pace bands.

Task set: reuse the 10-19 A/B tasks (research context packet; garden_room `tests/turbo_dark_mode.mjs`), plus one **50-item fanout** job (e.g. summarize/classify 50 public repo files or issues) and one TTFT-sensitive short-answer batch.

Arms:
- A: Hermes native, serial.
- B: OMH `/dag-ulw`, default chains.
- C: turbo #176 as it is.
- D: OMH chains pointed at vllm-sr (`vllm-sr/auto`, one decision per category).
- E: D + Kerdoios/burn-pace placement.
- F: the new scheduler from §2, if you build one.

Metrics for each cell:
- wall clock to *verified* completion
- peak and mean in-flight calls across providers
- aggregate output tok/s
- TTFT p50/p95
- frontier tokens
- sub-quota % consumed vs. the burn-pace budget
- $ spent
- 429/413/5xx counts
- quality verdict from an independent verifier

Run ≥3 repeats where cheap, and report variance. One trial is exploratory, and you must say so.

Don't send private repo source to free cloud models. Free models get no code-writing tasks.

## 4. Decide the first Semantic Router slice
Recommend the smallest slice that makes work measurably faster within a week. Default candidate: one `turbo/fanout` model name for tool-free, bounded text jobs.
It would have generated backends + per-lane caps from z0int and Kerdoios, a size guard (>6K tokens never goes to Groq), 429 spill, and a `confidence` draft→check decision.
Explain how OMH lanes call it, and how z0int turbo #176 calls it in place of its per-provider adapters.
Then lay out the later path: SR's classifier/remote-classifier signal backed by z0int Laya/Jev (shadow-only first, with promotion gates per z0int ROADMAP),
and the SLM shadow network logging SR decisions as DecisionOpportunity episodes.

## 5. Deliverables
Write everything to /mnt/zer0models/workspace/zer0/services/vllm-sr-turbo/research-<date>/:
- GOAL.md (§1, with quotes)
- ALGO-AUDIT.md (§2)
- TRIALS.md + raw jsonl receipts (§3)
- SLICE.md (§4, with exact config and code changes)
- REPORT.md: ≤1 page, for Kevin
If the chosen slice passed its gates, implement it as a PR on z0intelligence (rebase #176 first) and link it.
Update skill `vllm-sr-turbo-router` with the lessons.

Done = measured numbers per arm plus a recommendation that follows from them. A design document alone is not done.
If something blocks the real measurement, say so and take the next unblocked arm. Never fill a gap with estimates presented as results.
