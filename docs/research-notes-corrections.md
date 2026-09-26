# Research-note corrections

Every external claim used to justify a plan in this repo was re-fetched on
2026-09-22 and checked against the live page. Most held. These are the ones that
did not, recorded here so they are not re-imported.

The rule this enforces: **exact provider / model / revision first.** A family name
is not an artifact, and a number without a source is not evidence. Two of the
errors below were load-bearing — one invented a model, one dismissed a model on
a claim that does not exist.

## Remove or do not use

| claim | status | what the source actually says |
|---|---|---|
| "Cerebras documents **GLM-4.7** at ~1000 tok/s" | **fabricated** | The Cerebras model catalog lists exactly two models: `gpt-oss-120b` (~3000 tok/s) and `qwen-3.8-27b` (~1850 tok/s). GLM-4.7 is not there. Do not claim availability unless authenticated live discovery returns it. |
| Cerebras "free-tier capacity reduced under demand" | **unfindable** | Not on the models page or the rate-limits page. Only "subject to rate limits". |
| Verdict/OpenJev 151M: "hard-tier / general external performance drops sharply" | **fabricated** | The card reports 95.00% top-1, 3.35% ECE, 97.50% out-of-scope abstention recall, and contains no mention of general, hard, tier, external or degradation. The claim that discounted the model has no source. It did dismiss a model on a fabrication. |
| "AgentWeave's small study **demonstrates**" that fewer tools → fewer tokens and lower latency | **overstated** | Real direction, wrong strength. The source self-labels a 12-task result a "resource-constrained pilot" that is "**not statistically significant**" (McNemar p = 0.5, 95% CI [0.0, +41.67] pp, 2/12 solved) and reports its own preregistered ≥85% recall target **missed** at 77.08%. Cite it as an interesting small custom routing-pressure study, not as conclusive evidence. |
| "AgentWeave's **Hammer1.5B** experiment" | **misnamed** | The model is `MadeAgents/Hammer2.1-1.5b` — Hammer **2.1**. |
| "FunctionGemma is not a generic **router** model" | **not in the card** | The card says "intended to be fine-tuned for your specific function-calling task… **not intended for use as a direct dialogue model**". The word "router" appears nowhere. Describe it as a task-specific function-calling foundation. |
| RouteLMT as direct agent-routing validation | **scoped wrong** | Real paper (*RouteLMT: Learned Sample Routing for Hybrid LLM Translation Deployment*, Luo et al., 24 Apr 2026) and the marginal-gain argument is real and verbatim — but it is **machine-translation** routing. It may motivate a marginal-gain target; it is not agent-routing evidence. |
| "RouteLLM, FrugalGPT and AutoMix all show versions of cost-aware cascading" | **partly unsourced** | RouteLLM is confirmed (Ong et al., ICLR 2025, "reduce costs by over 2 times"). FrugalGPT and AutoMix appear nowhere on that page and were cited without verification. |

## Provenance caveats to carry, not errors

* `google/functiongemma-270m-it` is **gated** (`gated: manual`); its raw README
  returns HTTP 401 unauthenticated. Quotes attributed to its card were read from
  republished mirrors plus Google's blog, not byte-exact from the canonical card.
  The 58% → 85% Mobile Actions figure and the "fine-tuned for a specific
  function-calling task" wording are confirmed from those sources.
* "Keep `choice` questions under ~20 options" is on the
  `convaiinnovations/laya-typed-decisions` card. The base `laya` card warns about
  ">20 options" and "50+ options". Same substance, different page.
* `github.com/bonsai/openjev` uses `master`, not `main`; a `main` URL 404s.
* `https://ithub.global.ssl.fastly.net/sauravsingla/agentweave/...` is **dead**
  (TLS failure). Use the GitHub URL.
* The Nature mushroom-body citation (Bennett, Philippides, Nowotny, *Nat Commun*
  2021) is a **modelling** study, and "sparse representation in ~5–10% of the KCs"
  is in the body, not the abstract.

## Publisher-reported, so they may be used as claims

Everything below is **the model publisher's own reported number about their own
model**. It was verified to exist and to say what we say it says; it was *not*
independently reproduced on this hardware. An independent reviewer flagged that
this distinction was blurred. These are source claims, and this repo already
treats source claims as claims, not measurements — `selectable()` reads only
local measurements for exactly this reason.

Laya 421M / multilingual 322M and its 0.362 vs 0.766 typed-workflow contrast ·
Decider-2B's 0.98@10 → 0.88@151 and 2–255 runtime options and its "split
multi-step judgments into several questions" advice · decider-0.8b within 1–4
points of the 2B · system-one scorer 0.915/0.943/1.000/0.634 with CC-BY-NC, seq
384, option cap 16 · Verdict 151M as a 151,378,177-parameter selective classifier
with a dedicated `__insufficient_evidence__` slot · Hammer2.1 0.5b/1.5b/3b/7b all
existing and being trained for multi-step, multi-turn and irrelevance rejection ·
NanoJev's 2–255 candidates, zero decoding and Qwen3-0.6B backbone · Nimble's 9B,
2,048-token limit, 1–26 enum, explicit not-calibrated caveat and one-fact
contrastive pairs · the central-complex connectome (Hulse et al., *eLife* 2021) ·
ToolOrchestra / Nemotron-Orchestrator-8B including its verbatim code/web coverage
limitation · Groq `gpt-oss-20b` having **no** parallel tool use while
`qwen/qwen3.8-27b` (~450+ tps, ctx 131,042) has it · North Mini Code (30B total /
3B active, Apache-2.0, 256K ctx, SWE-Agent and OpenCode) · Laguna XS 2.1 (33B
total / 3B active).

## Why this file exists

Recorded as an **observation, not a finding** (an independent reviewer objected,
correctly, that the causal claim below is not supported by anything in this repo
— there are no review logs, timestamps or reviewer comments here to test it
against):

Both errors ran in the **permissive direction** — one invented a model, the other
supplied a reason to dismiss one. The re-check that produced this file was
prompted by an eval result that embarrassed a model, and the claim that would
have excused it turned out to be the fabricated one.

That sequence is suggestive. It is not evidence of a systematic bias, and it
should not be cited as one. What *is* actionable and testable is narrower: a
citation that removes an inconvenient result deserves the same verification as
one that threatens a plan. Treat the rest as a hypothesis with n=2.
