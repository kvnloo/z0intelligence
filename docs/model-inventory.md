# Model inventory — exact checkpoints, revisions, runtimes, and what is actually wired

Status: **measured 2026-09-21/22 from the filesystem and the live supervisor.** Every
row below was read from disk, not from a manifest's intention. Where a manifest
disagrees with the disk, both values are shown.

Purpose: stop assigning roles from family names. `H3`, "laya", "openjev",
"system-one", "nemotron-orchestrator" are family labels. The rows below are the
exact artifacts.

---

## 0. The headline

There are **three different answers to "what is resident"** in three files, and
the model that is actually resident is in none of the plans:

| file | claims resident |
|---|---|
| `~/.z0int/config/serving.json` (what the supervisor actually serves) | `hammer2.1_3b` |
| `~/.z0int/config/z0int.json` → `models_plan` | `openjev_06b`, `local_mb` |
| `manifests/models.z0int.json` → `policies.twelve_gb` | `nanojev_06b`, `local_mb` |

And the portfolio is **not organised by parameter count**. It is organised by
*what the llama.cpp supervisor can hold as a single resident GGUF on a 12 GB
3080 Ti*. That is why the tier reads 270m → 3b → 4b → 7b → 8b → 9b: those are the
checkpoints that have GGUFs in `/mnt/zer0models/zer0-models/gguf/`. Every
decision-native model we own — Laya 421M, Decider-2B, NanoJev 0.6B, OpenJev 4B,
the 151M Verdict — is a **non-GGUF, non-generative readout** and is therefore
*outside the runtime*, not outside the budget. `serving.json` says so in its own
description: "Do not add a second runtime here."

So the diagnosis is one step sharper than "organised by size": **the ladder is
ordered by runtime compatibility, and the runtime is a generative-GGUF server.**
A model that does not generate tokens cannot climb it. The "small models look
useless" result is downstream of a serving constraint that has been read as a
capability result.

---

## 1. Served tier — llama.cpp supervisor, `127.0.0.1:11500`

Live now: `{"status":"ok","runtime":"llama.cpp-0.4.1-dev","resident":"hammer2.1_3b"}`.
All six are `Q4_K_M` except FunctionGemma. Source: `/home/kvn/.z0int/config/serving.json`.

| id | gguf | bytes | quant | licence | commercial |
|---|---|---|---|---|---|
| `functiongemma_270m` | `gguf/functiongemma-270m/functiongemma-270m-it-q8_0.gguf` | 291,557,792 | Q8_0 | Gemma Terms, **gated** | restricted |
| `hammer2.1_3b` | `gguf/hammer2.1-3b/Hammer2.1-3b.Q4_K_M.gguf` | 1,929,442,272 | Q4_K_M | `qwen-research` | **no** |
| `hammer2.1_7b` | `gguf/hammer2.1-7b/Hammer2.1-7b-Q4_K_M.gguf` | 4,178,136,704 | Q4_K_M | `cc-by-nc-4.0` | **no** |
| `nemotron_orchestrator_8b` | `gguf/nvidia-orchestrator-8b/nvidia_Orchestrator-8B-Q4_K_M.gguf` | 5,027,783,840 | Q4_K_M | NVIDIA License | **no** |
| `qwen3.5_4b` | `gguf/qwen3.5-4b/Qwen_Qwen3.5-4B-Q4_K_M.gguf` | 3,013,027,808 | Q4_K_M | apache-2.0 | **yes** |
| `qwen3.5_9b` | `gguf/qwen3.5-9b/Qwen_Qwen3.5-9B-Q4_K_M.gguf` | 6,169,341,984 | Q4_K_M | apache-2.0 | **yes** |

Upstream GGUF repo revisions (HF cache refs):

| repo | revision |
|---|---|
| `ggml-org/functiongemma-270m-it-GGUF` | `2566ce14aedfc14fdd0de955ba67346425e67126` |
| `eaddario/Hammer2.1-7b-GGUF` | `d90bfcf86fe677cab5722eca4f77ff6142c02a55` |
| `bartowski/nvidia_Orchestrator-8B-GGUF` | `b4bbbc08d2b475fe529428e6f358c932881aa0b5` |
| `bartowski/Qwen_Qwen3.5-9B-GGUF` | `182be2fd6c7bc44887d88a91cb03ff009cc9f549` |

**Four of the six production models are non-commercial.** Only `qwen3.5_4b` and
`qwen3.5_9b` are apache-2.0. This is recorded in
`docs/local-cognition-portfolio.md` but is absent from the architecture
proposal, which routes production traffic through Hammer and FunctionGemma.

**Second runtime, still up.** A standalone `llama-server` for the same Hammer 3B
checkpoint is running on `127.0.0.1:18100` (`.work/llama.cpp/build/bin/llama-server`,
started 22:45). The older bench run's receipts hit `:18100`/`:18101`; the newer run
hit `:11500`. The two receipt sets are therefore not runtime-comparable — the
portfolio doc flags this correctly, but the raw receipts still sit side by side in
`~/.z0int/benchmarks/local_cognition/`.

---

## 1b. Exact revisions and per-arm outcomes as recorded in the frozen corpus

`results/phase1b/p1b-20260921T1430Z/observations.jsonl` records a
`model_revision` per observation. These are the revisions the numbers came from —
quote these, not family names.

| `model_id` | recorded `model_revision` | quant |
|---|---|---|
| `functiongemma_270m` | `39eccb091651513a5dfb56892d3714c1b5b8276c` | Q8_0 |
| `hammer2.1_3b` | `702ce4215e1391cb000a9c037b86b997660750f3` | Q4_K_M |
| `hammer2.1_7b` | `c5692ee193b806c1aadeaf68c4aec3de503fac30` | Q4_K_M |
| `nemotron_orchestrator_8b` | `26df4b9aad5abdc5b7871ee4c71063ce888feb26` | Q4_K_M |
| `qwen3.5_4b` | `851bf6e806efd8d0a36b00ddf55e13ccb7b8cd0a` | Q4_K_M |
| `qwen3.5_9b` | `c202236235762e1c871ad0ccb60c8ee5ba337b9a` | Q4_K_M |
| `nanojev_06b` (arm `compiler+jev`) | `4a19595eada0857133c0d2be024f879a4077054b` | **bfloat16** |

Note these differ from the GGUF *repo* revisions in §1 — the corpus records the
upstream base-model revision, not the GGUF conversion repo. Both are needed to
reproduce a number; only one of them is currently recorded anywhere.

Per-arm outcome, all 19 arms, 28 states:

| arm | correct | alias |
|---|---|---|
| `compiler+qwen3.5_9b` | 78/84 | |
| `unfiltered+qwen3.5_9b` | 78/84 | `A_qwen9b_alone` |
| `compiler+hammer2.1_3b` | 75/84 | `SUB_compiler_hammer3b` |
| `unfiltered+qwen3.5_4b` | 75/84 | `SUB_qwen4b_alone` |
| `compiler+qwen3.5_4b` | 75/84 | `SUB_compiler_qwen4b` |
| `compiler+hammer2.1_7b` | 72/84 | `SUB_compiler_hammer7b` |
| `unfiltered+hammer2.1_3b` | 69/84 | |
| `compiler+jev+qwen3.5_4b` | 63/76 | |
| `unfiltered+nemotron_orchestrator_8b` | 61/84 | `B_nemotron_alone` |
| `compiler+nemotron_orchestrator_8b` | 52/84 | `C_compiler_nemotron` |
| `compiler+functiongemma_270m` | 51/84 | `SUB_compiler_functiongemma` |
| `compiler+jev` | 45/84 | |
| `deterministic.compiler_only` | 15/84 | `rules_baseline` |
| `coldprobe+{qwen3.5_4b, qwen3.5_9b, hammer2.1_3b, functiongemma_270m}` | 3/3 each | |
| `coldprobe+nemotron_orchestrator_8b` | 0/3 | |
| `coldprobe+hammer2.1_7b` | 0/3 | |

### Two corrections this table forces

**1. The "jev" arm is NanoJev, not Jev.** `compiler+jev` has
`model_id = nanojev_06b`, revision `4a19595eada0857133c0d2be024f879a4077054b`,
quant `bfloat16`. So the **45/84 result is our local 0.6B, not the hosted TypeSafe
Jev teacher.** Any reading of 45/84 as evidence about Jev is misattributed. Read
correctly, 45/84 is exactly the zero-shot-OOD number the NanoJev caveat predicts:
the upstream checkpoint was trained on maze/snake/ViZDoom, and it is being asked
Hermes routing. It supports the caveat; it says nothing about Jev.

**2. The compiler's measured value is asymmetric and model-specific.**

| model | unfiltered | compiler-first | delta |
|---|---|---|---|
| `hammer2.1_3b` | 69/84 | 75/84 | **+6** |
| `qwen3.5_4b` | 75/84 | 75/84 | 0 |
| `qwen3.5_9b` | 78/84 | 78/84 | 0 |
| `nemotron_orchestrator_8b` | 61/84 | 52/84 | **−9** |

The compiler is worth +6 to Hammer-3B, worth nothing to either Qwen, and costs
Nemotron **nine** states. The doc-level claim "ordinary computer science goes in
the compiler, not the model" is currently supported by one model out of four, and
the safety claim (0 dangerous vs 6/84) is likewise a Hammer-3B result. The
compiler's *safety* property may be general — elimination is elimination — but its
*accuracy* effect is not, and `compiler+nemotron` is strictly worse than
`unfiltered+nemotron`, which no current doc mentions.

Also: `hammer2.1_7b` (72/84) is **worse than `hammer2.1_3b` (75/84)** on this
bounded action choice, and both 7B and Nemotron returned 0/3 on the cold probe
(consistent with their 13.3 s and 15.3 s supervisor cold loads).

---

## 2. Decision-native tier — torch/transformers, on disk, mostly unwired

This is the tier the research pass is about, and it is where the wiring failures are.

| id | upstream | revision | on disk | contract | licence | registered? |
|---|---|---|---|---|---|---|
| `laya_421m` | `convaiinnovations/laya` | `7c76b622dfc5cac71b2dc1c29873efe2ce509a05` | 842,609,210 B `model.safetensors` | ModernBERT-large 395M + 2-layer decision head = **421M**; `choice`/`score`/`noul`; **512 tokens per question**; many questions per forward pass (~33–38 ms GPU); non-autoregressive | apache-2.0 | **NO** |
| `decider_2b` | `Mapika/decider-2b` | `1d96be0093133e194fe18105a521b3e69be931d2` | 3,763,692,048 B | base **`Qwen/Qwen3.5-2B-Base`**, `Qwen3_5ForCausalLM`, 24 layers, linear+full attention; `max_options 255`, `max_state_tokens 32768`; option-letter logits, no decode | apache-2.0 | yes |
| `nanojev_06b` | `C-Tianyu/NanoJev` | `4a19595eada0857133c0d2be024f879a4077054b` | `~/.z0int/models/nanojev_06b/best.safetensors` 2,385,039,280 B **fp32** | backbone `Qwen/Qwen3-0.6B`; 2–255 candidates; bool/score; **zero output-token decoding** | **null (unset)** | yes |
| `openjev_06b` | `Qwen/Qwen3-0.6B` | `c1899de289a04d12100db370d81485cdf75e47ca` | 1.5 G dir | plain Qwen3-0.6B | null | no |
| `openjev_4b` | `Qwen/Qwen3.5-4B` | `851bf6e806efd8d0a36b00ddf55e13ccb7b8cd0a` | 8.8 G dir | plain Qwen3.5-4B | null | no |
| `reranker_4b` | `Qwen/Qwen3-Reranker-4B` | `22e683669bc0f0bd69640a1354a6d0aebcfeede5` | **not downloaded** | cross-encoder rerank | — | no |
| `system_one_4b` | `pngwn/system-one-qwen3.5-4b-scorer` | `e6464dce15f013c2ef641593a85cc6afcdaea928` | **not downloaded** | LoRA r=16, **seq 384**, **option cap 16** | `cc-by-nc-4.0` | no |
| `reflex` | — | — | — | `browser_app`, no headless adapter | — | no |
| `local_mb` | generated | — | `recovery_student.npz` | see §3 | — | yes (as `mushroom`) |

### 2.1 Laya is wired but not registered — a one-entry omission

`src/z0int/backends/laya.py` exists. The `laya` package **is installed** in the
venv (`/home/kvn/tmp/openjev/.venv/lib/python3.11/site-packages/laya/`). Weights are
present. `LayaBackend.for_manifest_id("laya_421m").health(load=False)` returns
**`ready: True, detail: "weights present"`**.

`register_builtin_backends()` (`src/z0int/backends/registry.py:89–136`) registers
exactly three ids — `mushroom`, `nanojev`, `decider_2b`:

```
$ python -c "from z0int.backends import registry; print([b['id'] for b in registry.backend_status()])"
['decider_2b', 'mushroom', 'nanojev']
```

`laya` and `openjev_direct` are not in that list, so they cannot appear in any
roster, bench run, Pareto plot, or shadow lane. **The best-evidenced cheap decision
model we own is code-complete, weight-complete, package-complete, and invisible
because no one added it to the registry.**

### 2.2 Revision drift in the config the runtime reads

`openjev_06b` is pinned differently in the two files:

| source | revision |
|---|---|
| `manifests/models.z0int.json` | `c1899de289a04d12100db370d81485cdf75e47ca` ← **matches disk** |
| `~/.z0int/config/z0int.json` | `c1899deebe5c9af36d47b04d0214f765e99987a8` ← not on disk |

The manifest projection in `~/.z0int/config/z0int.json` is also stale on residency
(it says `openjev_06b` resident; the manifest policy says `nanojev_06b`). It was
written 2026-09-18 00:39; the manifest was last touched 2026-09-21 16:58. The
generated config is upstream of nothing and reconciled with nothing.

### 2.3 Naming collisions, ours and theirs

* `openjev_06b` = **plain `Qwen/Qwen3-0.6B`**. "OpenJev" is a *method/project*, not a
  checkpoint, and we have pinned it as though it were a model.
* `thesysdev/OUI-1` (49 GB, in the HF cache) is a **generative-UI diffusion model**
  (`google/diffusiongemma-26B-A4B-it` finetune, 4B active) and has nothing to do
  with Jev/OpenJev. Same org, unrelated product. It is the largest single item in
  the HF cache and is not part of this portfolio.
* `heman10x/rlcd-modernbert-151m` self-describes as **"OpenJev (Verdict)"** — so
  "OpenJev" externally denotes a 151M ModernBERT decision engine, while our
  `openjev_06b` denotes plain Qwen3-0.6B. Two different things, one name.
* `bonsai/openjev` (the 4B direct-logits project) uses `master`, not `main`.

---

## 3. Biological tier — what we actually trained

All under `/home/kvn/tmp/evolution-lab`. These are not "models" in the same sense
as the rows above; the parameter counts make the category error obvious.

| artifact | path | contents | trainable numbers |
|---|---|---|---|
| next-action mushroom | `data/next_action/champion.npz` | `W_pn_kc (64,16)`, `W_kc_mbon (16,8)`, `k_winners=4`, `n_kc=16` | **1,152** |
| recovery mushroom | `data/p0/recovery_student.npz` | `W_pn_kc (151,128)`, `W_kc_mbon (128,5)`, `k_winners=13` | 19,328 + **640** |
| fly Jev-student | `~/.z0int/shadow/jev-fly.jsonl`, `source: fly_jev_student` | runtime-reported | **768** |
| `next_action_gpu_champion` | same stream | runtime-reported | **6,912** |

Two provenance defects:

1. **`next_action_candidate.npz` is not a candidate.** It is byte-identical to
   `next_action_champion.npz` and to `data/next_action/champion.npz`
   (`md5 a237b5dc72cbb71e3d44ad49ca42c838`, all three). Three names, one artifact.
2. **The shadow stream and the bundle disagree on the champion's size** — 6,912
   reported vs 1,152 on disk. Unresolved; either a different `n_kc` or a different
   artifact under the same name.

`W_pn_kc` in both bundles is a sparse fixed expansion with whole rows at zero; the
*learned* object is `W_kc_mbon` (640 numbers for recovery, 128 for next-action).
So "mushroom policy" = a 640-number linear readout on a frozen sparse projection.
It is not a small neural network; it is a fitted linear model with a random
feature map. Comparing it to Hammer-3B on next-action accuracy measures the
feature map, not the architecture.

P0 frames: `train (192,8,15)`, `val (48,8,15)`, `confirm (48,8,15)`, `ood (32,8,15)`,
seed 20260912, `env` and `secret` flags present. **`ood` labels are 100 %
`escalate` (class 2) on all 32 episodes** — see §5.

---

## 4. What does not exist anywhere on this machine

Checked against both HF caches (they are the same directory —
`~/.cache/huggingface` is a symlink to `/mnt/zer0models/zer0-models/huggingface`),
the GGUF store, the ollama store, and the venv:

* `Qwen/Qwen3-Reranker-0.6B` — **absent.** This is the proposed p0 priority.
* `Qwen/Qwen3-Reranker-4B` — absent, though pinned in the manifest.
* `pngwn/system-one-qwen3.5-4b-scorer` — absent (manifest pins `-scorer`;
  the research pass cites `-scorer-v2b`; neither is downloaded).
* `heman10x/rlcd-modernbert-151m` (Verdict 151M) — absent.
* `kshetrajna12/reflex` — absent, and declared `browser_app` with no headless adapter.
* `Mapika/decider-0.8b` — absent.
* `MadeAgents/Hammer2.1-0.5b`, `-1.5b` — absent (both exist upstream; we have only 3b and 7b).
* `bespokelabs/Bespoke-Nimble-9B` — absent.
* North Mini Code, Laguna XS 2.1 — absent.

Present but outside the z0int roster: `ollama` holds `qwen2.5:3b`,
`qwen2.5vl:3b`, `qwen2.5-coder:7b` (4,683,074,048 B blob). `qwen2.5-coder:7b` is
symlinked into the GGUF store but is not in `serving.json`.

---

## 5. The promotion gate is currently passing on a saturated split

`~/.z0int/benchmarks/recovery_action_l2.json` → promoted to
`~/.z0int/specialists/recovery_action.canary.json` with
`execution_policy: {"current": "canary", "promote_allowed": true}`.

```
gates: confirm_ge 0.95, val_ge 0.95, ood_ge 0.85, closed_loop_ge 0.999999999
       confirm_ok true, val_ok true, ood_ok true, closed_loop_ok true  ->  pass: true

majority      confirm 0.4792 (n=48)   ood 1.0000 (n=32)
rule_teacher  confirm 0.8125 (n=48)   ood 1.0000 (n=32)
student       confirm 1.0000 (n=48)   val 1.0000 (n=48)   ood 1.0000 (n=32)
```

Three defects in one artifact:

1. **`ood_ok` cannot discriminate.** Every OOD label is `escalate`, so the trivial
   majority baseline scores 1.0000 and clears the 0.85 gate. A gate that a
   constant predictor passes is not a gate.
2. **`confirm_ok` is n=48 and the student beats its own teacher** (1.0000 vs
   0.8125). On 48 episodes that is 0 vs 9 errors; at that sample size the 0.95
   threshold has no resolution worth the word "confirm".
3. **Two accuracy units exist for the same frozen bundle and they disagree.**
   This artifact records `student confirm 1.0000` per episode. The recovery
   comparison recorded the same 640-number bundle at **0.8385 per step** on the
   same confirm split. The promotion gate used the per-episode unit; the
   question "can this replace the rule?" is asked in the per-step unit. I
   reported 0.839 last block and did not flag that 1.000 was already the
   promoted number. Neither number is wrong; having both without a declared unit
   is the defect.

This is the general form of the nanojev candidate-set incident: **not just
"same candidates?" but "same unit, same split, same runtime, same revision?"**

---

## 6. Where the proposed eval suite already exists

The framework is largely built. `src/z0int/backends/bench/` contains
`analytics, bootstrap, eligibility, fixtures, materialize, metrics, pareto,
roster, run_manifest, runner, tokenomics_bridge`. There is a named contract
(`decision-capability-v1`), a five-capability list
(`rlm.worker_needed, tool_family_select, retry_or_escalate,
context_compress_needed, verification_needed`), an eight-candidate roster, a
bootstrapped Pareto layer, and an eligibility layer.

`docs/decision-capability-v2-plan.md` already specifies the dataset upgrade:
50–100 independently-labeled replayable rows per capability, verifier-derived
gold never taken from the backend under test, `task_snapshot_id` dedupe, 20 %
holdout by `trace_id` hash, promotion gate at
`validated_min_examples = 50`. It is marked **"design only — not implemented in
bench runner yet"** and ends with a "next engineering slice (when authorized)".

What the bench does **not** have — the actual gap:

| missing gate | evidence |
|---|---|
| candidate-set identity hashed per row | `grep -i parity\|candidate_set\|state_hash` across `bench/*.py` returns one docstring and one unrelated sha256 |
| question/state hash + revision + runtime pinned into the receipt | not present |
| declared metric unit per row | not present (see §5.3) |
| per-capability trust status instead of per-model trust | `BackendCapabilities` has boolean feature flags; there is no `capability -> {status, coverage, max_risk}` record |

And the bench has barely been run: **11 serving receipts total** across two runs
(`20260921T172755Z`: 5, `20260921T182719Z`: 6), on two different runtimes.

The `contract.py` roster also still optimistically lists `reflex` and
`system_one_4b` as candidates; `reflex` is hard-coded `unavailable` with the
reason "browser_app; no headless DecisionBackend adapter for bench".

---

## 7. Corrections to the research pass, from checking it

Every URL in the research pass was fetched live. The model-card citations largely
hold up. Four do not, and two of those are load-bearing.

### 7.1 Model cards and repos

| claim | verdict |
|---|---|
| FunctionGemma is meant to be fine-tuned for a specific function-calling task | **confirmed** — "intended to be fine-tuned for your specific function-calling task… not intended for use as a direct dialogue model" |
| FunctionGemma is "not a generic **router** model" | **not in the card.** The word "router" appears nowhere. The card's actual exclusion is "direct dialogue". The stronger phrasing is ours |
| FunctionGemma Mobile Actions 58 % → 85 % | **confirmed** (blog + mirrored card) |
| Laya base 0.362 / typed-decisions 0.766 | **confirmed**, both cards; typed-decisions says "This is a specialist… four specific synthetic workflows" |
| Laya warns choice degrades past ~20 options | **confirmed**, page misattributed: "keep `choice` questions under ~20 options" is on `laya-typed-decisions`; the base `laya` card warns about ">20" and "50+" |
| Decider 0.98@10 → 0.88@151; 2–255 options; abstention; split multi-step into several questions; decider-0.8b within 1–4 points and 1.5× faster; AgentGym/Mind2Web training | **all confirmed verbatim** |
| system-one scorer 0.915 / 0.943 / 1.0 / 0.634; CC-BY-NC; seq 384; option cap 16 | **all confirmed** |
| Verdict/OpenJev 151M: selective classifier, explicit abstention, ONNX/WebGPU/CPU | **confirmed** — `__insufficient_evidence__` candidate slot, 151,378,177 params, backbone `gliclass-modern-base-v2.0` |
| "its hard-tier/general external performance drops sharply" | **NOT FOUND — fabricated.** The card has no mention of general/hard/tier/external/degradation. It reports 95.00 % top-1, 3.35 % ECE, 97.50 % abstention recall. The claim that dismisses it has no source |
| Hammer2.1 0.5B/1.5B/3B/7B exist; trained for multi-step, multi-turn, irrelevance rejection | **confirmed**; 0.5b/1.5b are live upstream and we do not have them |
| NanoJev: 2–255 candidates, zero decoding, backbone Qwen3-0.6B, maze/snake/ViZDoom | **confirmed** |
| Nimble: 9B, ≤2048 input tokens, 1–26 enum, explicitly not calibrated, one-fact contrastive pairs | **confirmed** |
| `bonsai/openjev` = 4B direct option logits | **confirmed**; default branch is `master`, not `main` |

Provenance caveat: `google/functiongemma-270m-it` is **gated** (`gated: manual`);
the raw README returns HTTP 401 unauthenticated. FunctionGemma quotes come from
republished mirrors and Google's blog, not a byte-exact read of the canonical card.

### 7.2 Papers, benchmarks, providers

| claim | verdict |
|---|---|
| RouteLMT: "marginal gain from upgrading" beats absolute difficulty/quality as a budget signal | **confirmed** — Luo et al., *RouteLMT: Learned Sample Routing for Hybrid LLM Translation Deployment*, 24 Apr 2026. "hard inputs may be hard for both models, yielding zero gain". **Caveat: it is machine-translation routing, not agent routing.** The import into Q-Route is ours |
| RouteLLM / FrugalGPT / AutoMix as cost-aware cascading | **RouteLLM confirmed** (Ong et al., ICLR 2025, "reduce costs by over 2 times"). **FrugalGPT and AutoMix appear nowhere on that page** — cited but unverified |
| Mushroom body is associative learning, sparse cues, reward/punishment, outcome prediction | **confirmed** — Bennett, Philippides, Nowotny, *Nat Commun* 2021, DOI 10.1038/s41467-021-22592-4. "sparse representation … ~5–10 % of the KCs" is body-only, not abstract; it is a modelling study |
| Fly central complex = recurrent dynamics, maintained state, context-dependent action selection | **confirmed** — Hulse et al., *eLife* 2021;10:e66039, "recurrent circuit dynamics", "attractor dynamics", "state-dependent action selection" |
| Nemotron/ToolOrchestra: heterogeneous tools **and other LLMs**, outcome/efficiency/preference rewards, multi-turn, code/web limitation | **confirmed** — *ToolOrchestra* (arXiv 2511.21689), model **Nemotron-Orchestrator-8B**, HLE 37.1 % vs GPT-5 35.1 %, ~30 % cost; limitation verbatim: "We have not evaluated Orchestrator across broader domains (e.g., code generation, web interaction)". Retiring it was premature — we hold exactly this 8B as a Q4_K_M GGUF |
| "AgentWeave's small study demonstrates" that fewer tools → fewer tokens and lower latency, with a Hammer1.5B experiment | **misstated.** The model is `MadeAgents/Hammer2.1-1.5b` (Hammer **2.1**), and the file self-labels the result a "resource-constrained pilot … **not statistically significant**" (McNemar p = 0.5, 95 % CI [0.0, +41.67] pp, 12 tasks, 2/12 solved, preregistered ≥85 % recall target **missed** at 77.08 %). The direction is real; "demonstrates" is not supported. The cited URL (`ithub.global.ssl.fastly.net/...`) is **dead** (TLS failure); GitHub serves it |
| Groq: GPT-OSS-20B supports tool use, browser search, code execution, JSON; no parallel tool use | **confirmed** (`gpt-oss-20b`: Parallel **No**, built-ins = browser search + code execution) |
| Groq "Qwen3.8-27B" ~450+ tok/s, multimodal, parallel tool use | **confirmed** — id `qwen/qwen3.8-27b`, ~450+ tps, text+images, ctx 131,042 |
| Cerebras: GPT-OSS-120B ~3000 tok/s **and GLM-4.7 ~1000 tok/s** | **half fabricated.** GPT-OSS-120B ~3000 tok/s confirmed. **GLM-4.7 does not exist in the Cerebras catalog** — the only two rows are `gpt-oss-120b` (~3000) and `qwen-3.8-27b` (~1850 tok/s) |
| Cerebras "free-tier capacity reduced under demand" | **not found** on the models or rate-limits page; only "subject to rate limits" |
| North Mini Code: 30B total / 3B active MoE, repo-level agentic SWE and terminal agents across harnesses | **confirmed** — Apache 2.0, 256K context, 64K max output, SWE-Agent and OpenCode |
| Laguna XS 2.1: 33B total / 3B active, local long-horizon coding | **confirmed** — BF16 total 33,442,617,088 |

The pattern worth naming: the two incorrect rows are both rows that **dismissed a
model** (Verdict 151M) or **invented a capability** (Cerebras GLM-4.7). The
permissive direction is the one that goes unchecked.

---

## 7b. Is the marginal-gain target even learnable from our data?

The proposed Q-Route objective is
`gain(b ← s | x, c) = verified_utility(b, x, c) − verified_utility(s, x, c)`,
escalating when expected marginal gain exceeds upgrade cost. That quantity
requires **both arms measured on the same state**. Our two data sources differ
decisively:

**`results/phase1b/p1b-20260921T1430Z/observations.jsonl` — 1,102 rows, properly paired.**
Fields include `state_id` (28 distinct), `arm` (19 distinct), `arm_kind`,
`legal_actions`, `selected_action`, `correct`, `utility_components`, `cost_usd`,
`decision_ms`, `tokens_in/out`, `dangerous_exposed`, `dangerous_selected`,
`distribution`, `confidence`, `margin`, `entropy`, `state_features`,
`state_family`, `model_revision`, `compiler_revision`, `quant`,
`resident_before/after`, `cold_or_warm`, `repetition`.
27 of 28 states carry 13 distinct arms; one carries 19. This corpus **can**
support a marginal-gain target.

**`~/.z0int/episodes/next_action.jsonl` — 76,970 rows, single-arm by construction.**
The whole schema is:

```
ts, session, user, prev, tool, family, tier
```

No arm, no utility, no counterfactual, no cost. This corpus **cannot** support a
marginal-gain target at all — not approximately, not as a proxy.

**And the shadow lane can never produce paired data.** The live lane is
observe-only: `executed_action` is `None` on every one of the 160 receipts across
all four batches. An observe-only lane observes one arm per state. So:

> The proposal to change Q-Route's target to expected marginal gain (#24) and the
> proposal to feed it from the observe-only shadow lane (#6) are mutually
> exclusive. Paired targets require paired execution.

This is the same defect as the nanojev candidate-set incident, one layer down:
comparing two things that were never run against the same menu. Candidate-set
parity is necessary but not sufficient — **arm parity at the data layer is the
harder version**, and the 28-state corpus is currently the only place we have it.

---

## 8. What I could not determine

* Whether `next_action_gpu_champion` (reported 6,912 params) is the same artifact
  as `next_action_champion.npz` (1,152 numbers).
* Which unit `docs/` and the gate consider canonical for `recovery_action`
  accuracy. Both are in active use; no artifact declares one.
* Which revision of `Qwen/Qwen3-0.6B` the running code would resolve if it loaded
  `openjev_06b` — the manifest and the generated config disagree, and nothing has
  loaded it, so nothing has raised.
* Whether Laya's 512-token-per-question budget is sufficient for the Hermes
  decision families we would want to move onto it. Not measured.
* Whether `Qwen3-Reranker-0.6B` can be served by llama.cpp's rerank mode or needs
  a second runtime. Not measured; it is not in the GGUF store.
