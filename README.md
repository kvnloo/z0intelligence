# z0int

> **Name:** product identity is **z0intelligence** — a play on stochastic parrots and useful abundance under finite frontier budgets (with Kerdoios). See `docs/brand.md`. Package/CLI stay `z0int` until an explicit rename cutover.


**Personal intelligence that learns how you work, so your frontier models do less.**

z0int is evolving from an open Jev-style decision runtime into a private, personalized intelligence layer for agentic systems.

The long-term goal is not to train one giant model on a person's life. It is to turn a user's own history into a hierarchy of tiny, verifiable specialists that understand recurring intent, retrieve the right identity/context, make bounded decisions, and offload repetitive work from expensive LLMs.

Today, this repository contains the OpenJev runtime and benchmark substrate: typed probabilistic decisions, direct logit readout, trainable scorers, shared-state reuse, and integration with [Evolution Lab](https://github.com/kvnloo/evolution-lab). The personalization and onboarding system described below is the roadmap, not a claim that all of it is implemented today.

See [ROADMAP.md](ROADMAP.md) for the staged build.


## Decision backends

```bash
z0int onboard --auto --sync-models
z0int backends list
z0int backends eval --backend nanojev --input tests/fixtures/nanojev_request.json --json
```

See `docs/backends.md`. NanoJev is a local DecisionBackend; existing OpenJev/vLLM/MB lanes are unchanged.

## Why

Your LLM should not have to rediscover who you are, what you care about, how you work, and which routine action to take on every turn.

Most of that information already exists across years of conversations, projects, messages, tool traces, and decisions. z0int aims to compile that history into two complementary layers:

- **retrieval-backed personal context** for mutable facts, current projects, relationships, documents, and provenance;
- **small learned policies** for stable preferences, judgment, intent resolution, routing, tool selection, recovery, verification, and other repeated bounded decisions.

The result should be a system where the frontier LLM spends its compute on genuinely novel reasoning instead of repeatedly reconstructing the same context.

```text
raw user request
      |
      v
private context + identity retrieval
      |
      v
z0int specialists
  |       |       |
  |       |       +--> temporal / recovery fly
  |       +----------> mushroom-body decision head
  +------------------> local semantic scorer
      |
      +--> confident: act / route / compress context
      |
      +--> uncertain: Jev / stronger local model
      |
      +--> hard tail: frontier LLM
```

## Onboarding: bring your history

**Start here for install/setup:** [docs/ONBOARDING.md](docs/ONBOARDING.md).

```bash
./scripts/bootstrap.sh     # venv + editable install + doctor
z0int onboard --auto       # resumable; safe to re-run
z0int status
z0int doctor --json        # agents consume JSON, not prose
```

Agents: prefer the `z0int` CLI over reproducing setup from memory
(`skills/z0int-onboard/SKILL.md`, `AGENTS.md`, `CLAUDE.md`).

z0int is designed around **data-complete onboarding** rather than a short preference questionnaire. The goal is to import as much of your user-owned digital history as you can safely and legally export, preserve provenance, and let the training/retrieval pipeline discover what is actually useful.

Recommended sources include:

- **GitHub**: repositories, commits, issues, pull requests, reviews, discussions, and other development history;
- **Google**: Google Takeout data such as Search/Chrome history, Drive documents, Gmail, Calendar, and other relevant exports;
- **messages**: personal chat archives from the services you use, with third-party/private content handled conservatively;
- **LLM conversation history**: ChatGPT data export, Claude conversation export, and conversations from other assistants;
- **agent harness history**: Hermes, OMP, Pi, Codex, Claude Code, Cursor, OpenCode, and other harness traces where available;
- **Hermes state**: especially `state.db`, session/tool history, memories, outcomes, recovery events, and other local state that can be safely parsed;
- **future computer-use history**: screen/action traces from tools such as [Memento](https://github.com/kvnloo/Memento), collected prospectively with privacy filtering.

The onboarding target is comprehensive context, but **not every byte belongs in model weights**. Personalized state lives under **`~/.z0int/`** only.

### Privacy boundary

Raw personal data should stay private and local by default.

- Secrets, credentials, raw environment variables, payment data, and other sensitive values must be filtered or excluded.
- Mutable personal facts and current project state should stay retrieval-backed instead of being memorized into weights.
- Personalized corpora and personalized checkpoints should not be committed to this public repository.
- External training services should receive only explicitly sanitized, non-PII training material.
- Every derived training example should retain source/provenance so it can be excluded, rebuilt, or invalidated later.

This carries forward the core idea from the earlier private `sft-svlm` project: **learn how the user thinks; retrieve what is currently true.**


## From `sft-svlm` to z0int

The earlier `sft-svlm` idea was a personalized SLM/SVLM that could learn stable user behavior from personal history and help compile messy requests into better instructions for powerful workers.

z0int keeps that thesis but decomposes it further.

Instead of assuming one personal model should absorb everything:

```text
personal history
      |
      +--> retrieval memory --------> current facts / projects / provenance
      |
      +--> z0int training ----------> stable intent / preference / routing policies
      |
      +--> Evolution Lab -----------> specialist population
```

The model layer becomes an **army of specialists**, each earning production traffic through measurement.

## From history to training signal

z0int should not require a giant manual labeling project.

Existing histories already contain weak or strong supervision:

| Source | Useful signal |
| --- | --- |
| Harness traces | tool/action chosen, retry, model route, success/error |
| GitHub | edit -> test -> review -> merge/reject outcomes |
| Jev / typed judges | soft probability distributions over bounded choices |
| Conversations | corrections, follow-ups, accepted/rejected directions |
| Hermes state | recurring workflows, recovery actions, session outcomes |
| Memento going forward | screen state -> human/agent action -> next state |

The training pipeline should compile these into versioned episodes rather than dumping raw chat logs directly into SFT.

```text
context / observation
      |
      v
bounded decision or action
      |
      v
result / verifier / downstream outcome
      |
      v
training episode
```

Human labeling should be reserved for high-information cases such as disagreements, ambiguous outcomes, or novel workflows.

## Mushroom bodies + an army of flies

[Evolution Lab](https://github.com/kvnloo/evolution-lab) is the experiment engine for turning those episodes into small specialists.

The first production target is the **mushroom-body-style learner** already explored in FlyForge: sparse expansion, k-winner coding, and a tiny plastic readout for bounded decisions.

Over time, z0int should train an **army of flies**, each optimized for a narrow repeated process rather than one universal student:

- intent and skill routing;
- tool-family selection;
- context retrieval and compression;
- retry / recover / escalate decisions;
- verification and completion checks;
- model / effort routing;
- temporal computer-use state and action prediction;
- personalized preference and workflow decisions.

Richer fly / MaleCNS-derived circuits can be used as temporal research substrates, but production promotion is results-driven: if a simpler mushroom-body, ridge, MLP, or deterministic rule is faster and equally correct, the simpler system wins.

## Identity and context offload

The long-term product is a personal intelligence layer that can answer questions such as:

- What is the user actually trying to accomplish?
- Which prior project, conversation, or preference matters here?
- What information is stable identity versus a mutable current fact?
- Which context should the frontier model see, and which context can be compressed away?
- Which routine decision can be handled locally without invoking the frontier model?
- When should the system abstain and escalate?

This is where z0int can reduce context pressure on large models. Instead of shipping a lifetime of history into every prompt, local specialists can select, summarize, route, or act on the small slice that matters.

## Intent and AODL

[AODL](https://github.com/kvnloo/aodl) is a complementary typed language/IR for describing agent orchestration graphs.

z0int does **not** depend on AODL to train or run. Results come first. But AODL can become useful as a shared representation for intent once the behavior is working:

```text
natural-language request
        |
        v
z0int: infer intent / constraints / relevant identity
        |
        v
AODL: typed intent + topology + budgets + authority
        |
        v
Hermes / OMP / other runtime
```

The useful connection is not adding a `fly` node kind. It is using personalized learned policies to help **decode messy human language into a more explicit intent representation**, while AODL provides a portable vocabulary for the resulting orchestration.

## JEV rollout: dogfood before formal eval

The first live JEV surface is the existing [hermes-jev-skills](https://github.com/kvnloo/hermes-jev-skills) plugin, using public Hermes plugin seams. z0intelligence does **not** need to block that activation on a finished replay platform.

Rollout order:

1. enable one reversible, bounded decision lane such as model routing in live shadow/low-risk dogfood traffic;
2. emit compact replayable decision receipts while preserving Hermes' existing path as fail-open fallback;
3. after real traces exist, run the grouped JEV/NanoJev/OpenJev/control evaluations tracked in [#14](https://github.com/kvnloo/z0intelligence/issues/14);
4. promote cheaper specialists only when independent outcomes credit that question family.

JEV is the seed semantic observer/reference backend, not verified truth or a security authority. Historical traces remain useful controls, but synthetic or frozen replay is not a prerequisite to the first low-risk dogfood slice.

## Evolution Lab

z0int supplies runtime scorers and training surfaces. [Evolution Lab](https://github.com/kvnloo/evolution-lab) owns the empirical search loop:

```text
personal episodes
     |
     v
candidate dataset recipe + model genome
     |
     v
train / distill / DAgger
     |
     v
frozen replay + verifier
     |
     v
keep / discard
     |
     v
shadow traffic
     |
     v
promote verified winner
```

The ABAB meta-loop can evolve both **what data to train on** and **which architecture to use**. The benchmark, privacy boundary, and sealed evaluation set stay outside the evolvable surface.

## Local candidate foundation: OpenJev runtime

The code in this repository currently reproduces the useful *interface pattern* of TypeSafe Jev with open components. It does not reproduce Jev's undisclosed model or training.

Current capabilities include:

- runtime-defined typed options;
- generation-free direct-logit scoring;
- shared-state prefix reuse;
- trainable one-pass option scorers;
- frozen benchmark/evaluation bundles;
- integration with Evolution Lab and FlyForge.

This is **setup for the runtime that exists today**. Personal-history onboarding (P1 in [ROADMAP.md](ROADMAP.md)) is not implemented yet.

### Quick start

Python 3.10+, CUDA, and a GPU that can hold a 4B BF16 model:

```bash
python -m venv .venv
. .venv/bin/activate
export HF_HOME=/path/to/large-drive/huggingface
pip install -e '.[test]'
```

Owned example (typed option scores, timing, model revision, prompt hash):

```bash
CUDA_VISIBLE_DEVICES=0 openjev-score \
  --mode direct \
  --model Qwen/Qwen3.5-4B \
  --revision 851bf6e806efd8d0a36b00ddf55e13ccb7b8cd0a \
  --input examples/decisions.jsonl \
  --output results.jsonl
```

If every row has the same exact state, use `--mode shared` to prefill once and score criteria in parallel.

On the committed RTX 3090 benchmark, direct typed logits returned 21 probability pairs in a median **1.023 s** versus **5.332 s** for the compact autoregressive JSON-array baseline. Exact scope and limitations: [docs/RESULTS.md](docs/RESULTS.md), [docs/METHOD.md](docs/METHOD.md), [docs/REPRODUCE.md](docs/REPRODUCE.md).

### FlyForge stack (this repo + Evolution Lab)

Clone **this repo first**. It is the runtime. [Evolution Lab](https://github.com/kvnloo/evolution-lab) is the evolve sidecar (genomes, locked splits, promotion, DAgger, ABAB).

```bash
bash scripts/setup-flyforge.sh
# installs this package, clones evolution-lab @ nightly, locks splits, prints smoke commands
```

Contract: [docs/evolution-lab.md](docs/evolution-lab.md) (Track A recovery fly, Track B JEV heads, export paths).

### Route A: trainable scorers

When zero-shot direct readout is not enough, train a one-pass option head on labelled JSONL:

```bash
pip install -e '.[test]'
openjev-data synthetic --output data/synthetic
openjev-train data/synthetic/train.jsonl --validation data/synthetic/validation.jsonl \
  --output runs/synthetic.pt --device cuda
openjev-eval runs/synthetic.pt data/synthetic/test.jsonl
```

Details: [docs/jevlike-trainable-route.md](docs/jevlike-trainable-route.md). Optional `pip install -e '.[games]'` for Wikispeedia / Doom / Chess examples.

### Route C: vLLM DiffusionGemma

When a local vLLM with structured diffusion reads is up:

```bash
bash scripts/setup-vllm-diffusion.sh

openjev-score --mode vllm \
  --upstream http://127.0.0.1:8000 \
  --model dgemma \
  --tokenizer nvidia/diffusiongemma-26B-A4B-it-NVFP4 \
  --input examples/decisions.jsonl \
  --output results-vllm.jsonl
```

Details: [docs/vllm-diffusion-route.md](docs/vllm-diffusion-route.md).

## Repository map

- [ROADMAP.md](ROADMAP.md) - long-term z0int build order
- [docs/RESEARCH.md](docs/RESEARCH.md) - durable research context and critical path
- [docs/evolution-lab.md](docs/evolution-lab.md) - FlyForge / Evolution Lab contract
- [docs/RESULTS.md](docs/RESULTS.md) - measured OpenJev results
- [docs/METHOD.md](docs/METHOD.md) - frozen evaluation methodology
- [docs/REPRODUCE.md](docs/REPRODUCE.md) - pinned environment and verification
- [docs/jevlike-trainable-route.md](docs/jevlike-trainable-route.md) - Route A
- [docs/vllm-diffusion-route.md](docs/vllm-diffusion-route.md) - Route C
- [benchmarks/](benchmarks/) - reproducible fixtures
- [demo/index.html](demo/index.html) - interactive replay
- [webgpu-demo/index.html](webgpu-demo/index.html) - browser-only demo
- [src/openjev_phase1/](src/openjev_phase1/) - current scoring/runtime implementation

## Principles

1. **Results first.** New theory or architecture must earn its place through measured downstream improvement.
2. **Private by default.** Raw personal history is a local asset, not public training data.
3. **Retrieve facts, learn behavior.** Stable preferences/policies may be learned; mutable truth stays provenance-backed.
4. **Specialize aggressively.** Tiny verified specialists should replace repeated expensive reasoning where they can.
5. **Abstain instead of bluffing.** Low-confidence cases escalate to stronger systems.
6. **Keep the controls.** Ridge, MLP, deterministic rules, and other simple baselines remain mandatory.
7. **Evolution changes implementations, not the judge.** Frozen evaluation and privacy constraints are not optimization variables.

## Related projects

- [Evolution Lab](https://github.com/kvnloo/evolution-lab) - genomes, DAgger, Pareto/MAP-Elites, autoresearch and ABAB
- [AODL](https://github.com/kvnloo/aodl) - typed intent/orchestration IR
- [frontier-kb](https://github.com/kvnloo/frontier-kb) - research memory and claims
- [Hermes Agent](https://github.com/NousResearch/hermes-agent) - runtime, state, tools, messaging and learning surfaces
- [Oh My Pi](https://github.com/can1357/oh-my-pi) - fast coding harness and judgment/decision surfaces
- [Memento](https://github.com/kvnloo/Memento) - prospective private computer-use history source

This repository is an independent research project. Upstream components retain their licenses. Project code is released under the [MIT License](LICENSE).

## Future compiler stack (integrated)

Implemented in-tree (library + `z0int` CLI delegates). **Not product-validated on live Astra/Grok traffic.**

| Command | Module | Role |
|---|---|---|
| `z0int routine …` | `z0int.routines` | Compile stable specialist regions into routines |
| `z0int cascade …` | `z0int.cascade` | Premium-token cascades over specialists |
| `z0int aodl …` | `z0int.aodl` | Bind routine/cascade into AODL strategy (`plan`, not intent) |
| `z0int repair …` | `z0int.refinement` | Counterexample-driven routine repair |
| `z0int abab …` | `z0int.abab` | Experiment archive / next / stop helpers |

Docs: `docs/routine-compiler.md`, `docs/cascade-compiler.md`, `docs/aodl-integration.md`, `docs/abab-loop.md`, `docs/routine-repair.md`, `AODL_COMPAT.md`.

### Measured (synthetic / unit only)

- Future-feature unit suite: 73 tests.
- Synthetic repair demo: 384/384 toy labels correct; fallback callbacks 384 → 192 after repaired-child activation; rollback returns 384. **No real LLM token savings.**

### Not yet measured

- Real frontier token savings from routines/cascades
- Real Blender / harness L3 replacement
- Energy / joule savings

Token accounting stays on the existing **receipt** spine (`z0int receipt …`). Kerdoios still owns *where* residual model work runs; z0int owns *whether* cognition can stop earlier.

