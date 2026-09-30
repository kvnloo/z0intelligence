# z0int Research Context

**Snapshot:** 2026-09-17  
**Purpose:** durable context for future agents working on z0int, Evolution Lab, FlyForge, OpenJev/Jev-style decision systems, personal intelligence, and intent/context offload.

This document summarizes the relevant research and architectural decisions from the working conversation that led to the current z0int direction.

It is intentionally split into:

- **Current / measured**: things already present in repositories or measured in existing experiments.
- **Proposed / long-term**: research directions, product goals, and architectural hypotheses that still need validation.

The main rule is:

> **Results first. Keep the useful concepts, but prefer the shortest path to measurable reduction in frontier-model work.**

---

# 1. Executive thesis

The original direction was to fine-tune a small language/vision-language model (`sft-svlm`) on a user's data so it could understand the user better and offload work from large frontier models.

That idea remains valuable, but the current direction is more compositional:

```text
user-owned history
      |
      v
private retrieval + provenance
      |
      v
automatic episode compiler
      |
      v
Evolution Lab + ABAB
      |
      +--> deterministic routines
      +--> mushroom-body specialists
      +--> temporal / fly specialists
      +--> local semantic scorers
      +--> small SLM/SVLM where justified
      |
      v
shadow evaluation + frozen verifier
      |
      v
production hierarchy
      |
      +--> local specialist
      +--> Jev / stronger local model
      +--> frontier LLM for hard tail
```

The desired outcome is **not** one giant personalized model.

The desired outcome is a **self-compiling personal intelligence layer** that progressively learns repeated parts of the user's cognition and workflow, while retrieving mutable facts and escalating genuinely novel reasoning to stronger models.

---

# 2. Core design principles

## 2.1 Results first

Architecture, biology, and orchestration theory only matter if they improve measurable downstream results.

Every candidate must compete against simple controls:

- deterministic rules;
- direct input;
- ridge / linear;
- MLP;
- GRU / RNN;
- random reservoir;
- rewired topology;
- local semantic scorer;
- small SLM/SVLM when appropriate.

A biologically inspired system is not promoted because it is interesting. It must expand the measured capability / latency / cost frontier.

## 2.2 Retrieve mutable truth, learn stable behavior

This is the core idea carried forward from the earlier `sft-svlm` concept.

**Retrieve:**

- current projects;
- current files;
- current relationships/context;
- current facts;
- current schedules;
- conversation history;
- fresh external information;
- provenance.

**Learn:**

- stable preferences;
- recurring judgments;
- routing tendencies;
- risk / verification preferences;
- common workflow patterns;
- intent transformations;
- bounded action policies.

Short form:

> **Learn how the user tends to think and act. Retrieve what is currently true.**

## 2.3 Specialize aggressively

Most repeated agentic decisions are much smaller than full language modeling.

Examples:

- which skill is relevant?
- which tool family should run next?
- should context be retrieved?
- is this result done?
- retry or escalate?
- should we verify?
- which worker/model tier should handle this?
- did the GUI state change enough to require new reasoning?

These should be candidates for tiny specialists rather than repeated frontier-model calls.

## 2.4 Abstention is a feature

The specialist does not need to solve every case.

A strong production policy is:

```text
easy / familiar / high confidence
    -> local specialist

ambiguous
    -> Jev / stronger local semantic model

hard / novel
    -> frontier LLM
```

Therefore one of the most important metrics is **safe coverage**, not just raw accuracy.

## 2.5 The judge must stay frozen

Evolution may change:

- dataset recipe;
- architecture;
- thresholds;
- feature projection;
- training method;
- circuit subset;
- hyperparameters.

Evolution must not change:

- sealed evaluation;
- privacy boundary;
- verifier;
- safety gates;
- scoring code;
- acceptance criteria.

---

# 3. Jev: what matters

## 3.1 Public behavioral model

TypeSafe Jev is useful primarily because it frames many agent decisions as **typed probabilistic questions**, not text generation.

The important public primitives are roughly:

```text
state
  |
  +--> binary / Noul question
  |
  +--> finite Choice question
  |
  +--> ordered Score question
  |
  v
probability distributions
```

The useful system-level properties are:

- bounded output domains;
- runtime-defined criteria/options;
- probability-preserving outputs;
- no need to generate explanatory text;
- cheap parallel questions over shared state;
- natural abstention / confidence policy;
- better fit for routing, judgment, filtering, and bounded control.

## 3.2 What is not public

Jev's actual underlying architecture, parameter count, sampler internals, RLCD training details, and full training corpus are not publicly known.

Therefore:

> z0intelligence should reproduce and improve useful **decision interfaces and systems behavior**, not pretend to reproduce Jev's hidden implementation. OpenJev is one historical/local substrate inside that broader system.

---

# 4. z0intelligence current foundation

`kvnloo/z0intelligence` began from the OpenJev codebase but is now a broader provenance-backed cognition/control plane.

Current repository identity:

- repo: `kvnloo/z0intelligence`
- default branch: `master`
- historical substrate: `TheoLeeCJ/openjev`

Current implemented substrate includes:

- typed runtime-defined decisions;
- direct option-logit readout;
- trainable one-pass scorers;
- shared-state reuse;
- reranker controls;
- reproducible benchmark fixtures;
- Evolution Lab integration;
- Jev-like browser/WebGPU demo;
- train/eval tooling.

## 4.1 Direct typed logits

The current direct path takes:

```text
state + question + runtime options
          |
          v
frozen causal model
          |
          v
last-position logits
          |
          v
restrict to allowed answer slots
          |
          v
softmax
```

No answer sentence needs to be generated.

## 4.2 Shared-state reuse

When multiple questions share the same state:

```text
                     +--> question A
state -> one prefill -+--> question B
                     +--> question C
```

This is a key pattern for future z0int specialists:

> **Encode shared state once, then ask many cheap bounded questions.**

## 4.3 Existing measured OpenJev results

On the committed RTX 3090 benchmark:

- 21 direct typed decisions: ~1.023 s median;
- compact autoregressive JSON-array baseline: ~5.332 s median;
- 777-decision shared-state parallel path: ~20.03 decisions/s;
- direct Qwen3.5-4B is the strongest tested general-decision OpenJev baseline in the current repo.

These numbers are systems measurements of the specific committed benchmark. They are not direct measurements of TypeSafe's Jev service.

---

# 5. Evolution Lab current state

Repo:

- `kvnloo/evolution-lab`
- active research branch: `nightly`

The current research branch is materially ahead of `main`.

Important recent work includes:

- `local_plasticity` mushroom-body-style student;
- P1 control table;
- DAgger / per-step closed-loop training;
- OpenJev route integration;
- recovery controller bundle;
- autoresearch;
- parallel sweeps;
- ABAB meta-loop;
- Jev skill-routing distillation.

Useful recent commits included:

- `7ed11ee...` add P1 control table + mushroom-body specialist;
- `20555e7...` tune cheapest perfect L1 student;
- `3273f86...` DAgger scaffold;
- `ef4acba...` close the recovery closed-loop gap with per-step training;
- `e230e769...` autoresearch loop;
- `0dcdb6e...` Jev skill-routing distillation;
- `a4e5b724...` parallel sweep + `k_winners`;
- `8d504e2...` ABAB A-timescale;
- `9dc5b57...` first ABAB Jev+sweep ideas wave.

---

# 6. Mushroom-body specialist

## 6.1 Current production-oriented interpretation

The mushroom-body-inspired student is the strongest current path for tiny bounded decisions.

Conceptually:

```text
structured / semantic features
        |
        v
PN feature bank
        |
        v
sparse PN -> KC projection
        |
        v
k-winners
        |
        v
plastic KC -> MBON readout
        |
        v
bounded action / probability distribution
```

The important properties are:

- very small plastic state;
- sparse representation;
- cheap inference;
- local plasticity;
- naturally suited to bounded associative decisions;
- easy to benchmark against linear/MLP controls.

## 6.2 Current Hermes recovery result

The recovery task maps typed Hermes operational state to:

```text
retry
restart_sandbox
escalate
noop
page_human
```

The current research branch reports a 640-plastic-weight `local_plasticity` configuration that reaches perfect confirm performance on the locked L1 recovery set and, after per-step prefix training, reaches closed-loop reward 1.0 in the current synthetic recovery environment.

Important caveat:

> "640 weights" is the plastic KC→MBON parameter count, not the total computation or storage footprint of the entire feature pipeline.

---

# 7. MaleCNS / full fly brain: the revised role

Earlier brainstorming considered using the full MaleCNS fly brain directly as a production controller.

The better current interpretation is:

> **MaleCNS is primarily a biological search substrate / temporal feature laboratory. The mushroom body or a distilled motif is the preferred production target.**

## 7.1 Why not put full MaleCNS on the hot path by default?

The full fly CNS contains rich evolution-designed recurrent structure, but using a large simulation for tiny repeated decisions is likely inefficient.

A better loop is:

```text
MaleCNS
   |
   | lesions / taps / rewires / pinout experiments
   v
candidate useful circuit
   |
   | verified causal contribution
   v
distill
   |
   +--> tiny recurrent motif
   +--> MB input feature
   +--> deterministic routine
```

## 7.2 Synergy with mushroom body

The proposed hybrid is:

```text
semantic / structured features --------+
                                       |
temporal stream -> MaleCNS-derived ----+--> unified PN bank
                circuit taps           |
                                       v
                                  sparse KCs
                                       |
                                       v
                               plastic MBONs
```

MaleCNS can remain largely frozen.

Evolution searches:

- input pinout;
- circuit subset;
- state taps;
- temporal horizon;
- cell-type gains;
- local plastic regions;
- readout coupling.

Then any useful circuit is challenged by:

- direct-input control;
- GRU/RNN;
- random reservoir;
- rewired topology;
- distilled small motif.

If the distilled motif preserves the gain, deploy the motif rather than the full simulator.

---

# 8. FlyForge interpretation

The useful long-term system is an **army of specialist flies**, not one universal "fly model."

Potential specialists:

- skill router;
- tool-family router;
- context relevance filter;
- memory retrieval selector;
- recovery controller;
- verification controller;
- model/effort router;
- subagent selector;
- computer-use transition detector;
- action family selector;
- routine compiler detector.

Each specialist should have:

- one bounded contract;
- one verifier;
- one calibration curve;
- one fallback path;
- one promotion history.

---

# 9. AODL mapping

AODL remains useful, but it is no longer on the critical path for z0int training.

Repo:

- `kvnloo/aodl`
- wire format: `hotl-0.2`

AODL's conceptual object is:

\[
\mathcal{O}_t = (V_t, E_t, S_t, \Pi_t, \Gamma_t)
\]

with:

- \(V_t\): nodes;
- \(E_t\): typed edges;
- \(S_t\): runtime state;
- \(\Pi_t\): routing/allocation policy;
- \(\Gamma_t\): objectives, budgets, confidence, privacy, authority, human gates.

## 9.1 Do not add fly-specific node kinds

Do not add:

```text
mushroomBody
MaleCNS
Jev
fly
```

as new AODL kinds.

They are implementations beneath existing node roles.

Example mapping:

- local learned decision system -> `model`;
- decision runtime / provider lane -> `executor`;
- sanitizer / state projector -> `service`;
- actual side-effecting action -> `tool`;
- outcome checker -> `verifier`;
- episode archive -> `stateStore`;
- trained checkpoint -> `artifact`;
- full MaleCNS research simulation -> `environment`;
- Evolution Lab -> `executor`.

## 9.2 Where the fallback hierarchy belongs

This:

```text
deterministic
-> mushroom body
-> bio temporal specialist
-> OpenJev
-> Jev
-> frontier LLM
```

is primarily a routing policy \(\Pi_t\), constrained by \(\Gamma_t\).

## 9.3 Why AODL still matters

The useful eventual connection is:

```text
natural-language request
        |
        v
z0int personalized intent inference
        |
        v
explicit intent / constraints
        |
        v
AODL
        |
        v
Hermes / OMP / other harness compiler
```

So z0int can help decode messy human language into explicit intent, while AODL provides a portable typed language for the resulting orchestration.

But:

> **Do not block z0int learning or deployment on AODL.**

---

# 10. The `sft-svlm` idea and the z0int pivot

The earlier `sft-svlm` project aimed to use a small language/vision-language model as a personalized helper trained on the user's data.

The current z0int thesis reuses that idea while decomposing it:

```text
all personal history
      |
      +--> retrieval plane
      |
      +--> episode compiler
      |
      +--> Evolution Lab
             |
             +--> mushroom-body specialists
             +--> temporal / fly specialists
             +--> local semantic heads
             +--> small SLM/SVLM if it wins
```

The small SVLM is no longer presumed to be the final architecture.

It becomes a competitor and possibly a representation supplier.

---

# 11. z0int onboarding vision

The future onboarding process should ingest as much user-owned digital history as is safe, legal, and useful.

Recommended sources:

## Development

- GitHub repositories;
- commits;
- issues;
- pull requests;
- code reviews;
- discussions;
- CI/test history.

## Google

Prefer user-owned exports such as Google Takeout.

Potentially useful sources:

- Chrome/Search history;
- Drive documents;
- Gmail;
- Calendar;
- other relevant activity/history exports.

## Messages

- personal message archives;
- team/group conversations where appropriate;
- preserve third-party privacy boundaries.

## LLM history

- ChatGPT data export;
- Claude data export;
- other assistant conversation exports;
- local harness conversation/session logs.

## Harnesses

- Hermes;
- OMP;
- Pi;
- Codex;
- Claude Code;
- Cursor;
- OpenCode;
- other agent runtimes.

## Hermes

Especially valuable:

- `state.db`;
- session history;
- tool actions;
- recovery events;
- local memories;
- outcome signals;
- model routing;
- retries/errors.

## Future screen/computer-use history

- Memento;
- event-synchronous screenshots;
- action logs;
- app/window state;
- OCR / accessibility state;
- before/after state transitions.

---

# 12. Privacy boundary

Raw personal data should remain local by default.

## Never treat these as ordinary training text

- passwords;
- API keys;
- recovery codes;
- raw environment variables;
- payment details;
- secret-store payloads;
- high-risk private documents;
- sensitive third-party content without a clear need.

## External training rule

Any external/Tinker-like training service should receive only explicitly sanitized, non-PII data.

## Provenance requirement

Every derived training example should retain enough provenance to:

- remove it;
- rebuild it;
- invalidate it;
- trace why a model learned from it;
- distinguish first-party authored data from received content.

---

# 13. The most important data insight: avoid manual labeling

The system does **not** need a giant human labeling project.

It does need supervision.

The strategy is to extract supervision from what happened.

## 13.1 Automatic strong labels

Harness traces already contain:

- chosen tool;
- action;
- retry;
- model route;
- tool result;
- test result;
- next action;
- completion/failure.

GitHub contains:

- edit;
- test;
- review;
- merge/reject;
- follow-up fix.

These can produce high-quality:

```text
state -> action -> outcome
```

episodes.

## 13.2 Soft labels

Jev and other typed decision systems can produce full probability distributions.

Those should be preserved as soft labels rather than collapsed to just the winning class.

## 13.3 Weak labels

Conversation history can provide signals such as:

- user correction;
- follow-up;
- rephrasing;
- repeated request;
- acceptance;
- abandonment.

These are noisier but useful.

## 13.4 Hindsight labels

A strong teacher can inspect completed trajectories and infer:

- what actually worked;
- which action was unnecessary;
- which goal was achieved;
- whether a failure produced useful information.

These should be lower-trust pseudo-labels than deterministic verifier outcomes.

---

# 14. Canonical training episode

Do not dump raw chat logs directly into SFT.

Compile data into episodes.

Minimal conceptual format:

```text
Episode

context
observation_before
action / bounded decision
observation_after
outcome

teacher_distribution
source
timestamp
privacy_class
label_source
label_confidence
trace_reference
```

One episode may have multiple training views.

## Mushroom-body view

Aggressively compressed:

```text
intent features
recent actions
recent outcomes
repo/app context
candidate set
budget/retry state
```

## Temporal fly view

Sequence:

```text
state t-4
action
state t-3
action
state t-2
...
state t
```

## SVLM view

Where vision is actually available:

```text
instruction
screen
history
candidate actions
```

---

# 15. Train/test split rule

Avoid random split leakage.

Prefer:

```text
oldest ~70% -> train
next ~15%   -> validation
newest ~15% -> confirm
```

Also hold out entire:

- repositories;
- workflow families;
- task types;
- failure classes;

for OOD evaluation.

The meaningful question is:

> **Can yesterday's behavior improve tomorrow's decisions?**

---

# 16. Fastest useful training path from current data

Because there is no historical Memento corpus yet, the fastest valuable training task is **not vision**.

The best immediate task is:

> Predict the next useful action/tool family from existing Hermes/OMP agent trajectories.

Example action space:

```text
READ_SEARCH
EDIT
EXECUTE
WEB
DELEGATE
VERIFY
RESPOND
ABSTAIN
```

Input may include:

- user request;
- recent tool/action names;
- recent result/error bits;
- cwd/repo identity;
- retry/budget state;
- available tool families.

Output:

- action-family probability distribution.

## Why this is fast

The labels already exist in the trajectories.

No manual labeling.

No screenshot pipeline.

No expensive semantic teacher for every sample.

## Quality tiers

### Gold

Action followed by deterministic success / verifier / test.

### Silver

Action appears in a successful trajectory without immediate correction.

### Teacher

Ambiguous/unlabeled state labeled by Jev or another strong model.

Failures are useful too:

```text
old action -> fail
replacement action -> success
```

creates a valuable negative/positive pair.

---

# 17. Jev's role in the personalized training loop

Do not pay Jev to label everything.

Use observed actions and outcomes when supervision already exists.

Use Jev for:

- missing labels;
- ambiguous decisions;
- specialist-vs-history disagreements;
- high-information active-learning cases;
- soft probability targets.

Important long-term distinction:

## Matching Jev

Teacher distillation:

```text
state + options
      |
      v
Jev distribution
      |
      v
student distribution
```

## Beating Jev

Requires downstream outcome credit.

If Jev is always the ground truth, the student can only imitate Jev.

To exceed Jev on the user's personal distribution, train against:

- verifier outcomes;
- task completion;
- latency;
- later correction;
- fewer tool calls;
- fewer frontier-model calls;
- human acceptance where necessary.

---

# 18. Current `jev_distill.py` lesson

The current Evolution Lab Jev skill-routing distiller is a useful prototype.

It joins:

- OMP Jev logs;
- OMP history DB prompts;

using timestamps, then learns Jev's winning label.

Important next improvements:

- stable trace/decision IDs instead of timestamp-only joins;
- full probability distributions instead of winner + top probability;
- option-conditioned scoring instead of fixed labels;
- real outcome/verifier data;
- no fake placeholder latency values;
- report trainable parameters and total inference footprint separately.

---

# 19. Dynamic-option fly: the Jev-like end state

A fixed classifier does not reproduce Jev's strongest property: runtime-defined choices.

The more general fly should be **option-conditioned**.

Conceptually:

```text
state
  |
  v
sparse KC state code
  |
  +------------------+
  |                  |
option A encoding  option B encoding ...
  |                  |
  +---- compatibility scorer ----+
                                  |
                                  v
                           option probabilities
```

This allows dynamic sets like:

- installed skills;
- tools;
- subagents;
- GUI targets;
- files;
- candidate plans.

For stable fixed domains such as recovery actions, fixed MBON heads remain cheaper.

---

# 20. Multi-head shared-state fly

A natural long-term optimization is:

```text
state -> one sparse encoding
             |
             +--> need_tool?
             +--> which tool family?
             +--> verify?
             +--> retry?
             +--> model tier?
             +--> done?
```

This is the fly equivalent of shared-state parallel Jev questions.

---

# 21. ABAB as the meta-strategy

The current ABAB direction should be preserved.

## A: diagnose

Ask:

- what failure mode is actually limiting us?
- is it a data problem?
- architecture problem?
- label problem?
- temporal-context problem?
- threshold/calibration problem?
- verifier problem?

## B: experiment

Mutate:

- dataset recipe;
- architecture;
- training method;
- thresholds;
- feature projection.

Evaluate against the frozen judge.

## Optional C: discriminating experiment

When the hypothesis space is unclear, run the cheapest test that separates competing explanations instead of another broad sweep.

Example:

```text
Hypothesis A: model capacity is limiting.
Hypothesis B: hard negatives are missing.

C-test:
add hard negatives without increasing model size.
```

---

# 22. Evolve the data recipe

One of the most important new directions is that the **dataset recipe itself becomes part of the genome**.

Potential genome:

```text
data:
  sources
  source weights
  history window
  temporal horizon
  negative sampling
  gold/silver/teacher weights
  pseudo-label threshold
  context fields
  privacy-safe projections

model:
  family
  PN dimension
  KC count
  k-winners
  recurrence
  plasticity
  MaleCNS circuit taps

policy:
  confidence threshold
  abstention threshold
  fallback
```

This lets ABAB discover:

- whether better data beats a bigger model;
- whether a source is harmful;
- whether temporal context matters;
- whether teacher labels are hurting;
- whether repo identity is causing memorization;
- whether hard negatives are missing.

---

# 23. Production promotion

Start in shadow mode.

```text
normal harness ---------> actual production choice
      |
      +--> specialist shadow prediction
                     |
                     v
                 telemetry
```

Measure:

- agreement;
- confidence;
- latency;
- errors;
- downstream outcome.

Then allow limited influence.

Example first production use:

```text
50 model-visible tools
      |
high-confidence local router
      |
      v
4 relevant tools
      |
      v
frontier LLM
```

This already saves context and decision effort without letting the specialist generate arbitrary tool arguments.

Later:

```text
high confidence
+ deterministic arguments
+ verifier available
      |
      v
direct local execution
```

---

# 24. Memento: future-only data source

There is currently **no historical Memento dataset** for this program.

The current public Memento fork records:

- screenshot every ~2 seconds;
- active-window title;
- OCR;
- OCR bounding boxes;
- H.264 video segments;
- image-difference change detection;
- SQLite FTS;
- optional vector embeddings.

It does **not** currently provide clean human action labels.

Therefore Memento is a **prospective** source.

## Desired future instrumentation

Record event-synchronous episodes:

```text
screen_before
+ OCR / AX / app state
      |
human or agent action
      |
screen_after_stable
+ state delta
      |
outcome
```

Action examples:

- click;
- type burst;
- scroll;
- key;
- app switch;
- wait.

Sensitive field typing should record only safe metadata, not raw secrets.

Historical actionless video can later support self-supervised temporal representation learning, but action-grounded training should use newly instrumented traces.

---

# 25. Vision / SVLM role

Do not prioritize broad SVLM fine-tuning until good screen/action data exists.

The original SVLM path remains valuable later.

Use it as:

- a visual representation learner;
- a computer-use candidate;
- a baseline against fly temporal specialists;
- a fallback where semantic screen understanding is required.

It should win through measurement, not assumption.

---

# 26. Intended self-compiling hierarchy

The long-term execution hierarchy is:

```text
deterministic rule / compiled routine
              |
              v
mushroom-body specialist
              |
              v
temporal / fly specialist
              |
              v
OpenJev / local semantic model
              |
              v
Jev / stronger decision model
              |
              v
frontier LLM
```

Repeated behavior migrates downward only after verification.

```text
novel task
  |
  v
frontier reasoning
  |
  v
bounded repeated decision
  |
  v
trained specialist
  |
  v
stable routine
  |
  v
deterministic code
```

The hard tail stays at the top.

---

# 27. Metrics

Primary metrics should include:

- verified task success;
- safe coverage;
- escalation rate;
- p50 / p95 / p99 decision latency;
- end-to-end latency;
- frontier-model calls removed;
- tokens/context removed;
- downstream retries/corrections;
- OOD transfer;
- human intervention;
- deployment cost;
- energy / verified success when actually measurable.

Hard gates:

- no additional policy violations;
- privacy boundary holds;
- sealed holdout does not regress beyond registered margin;
- candidate beats or matches simple controls;
- no secret-bearing state leaks into unauthorized training/inference.

---

# 28. Current critical path

**GPU first.** Use the 3080 Ti as a *population* engine for tiny policies, not a faster executor for one 640-weight fly. One mushroom-body forward pass is launch-overhead-bound; thousands of candidates × episodes is what the GPU is for.

```text
DONE (2026-09-17, evolution-lab nightly b9e4db5)
  1. local_jax backend — exact mushroom algorithm in jnp.float32
  2. parity: CPU local_plasticity ≈ GPU local_jax on locked recovery seeds
  3. population batch P = 256 → 4,096 smoked on 3080 Ti (16,384 not needed)
  4. GPU cheap-filter → frozen CPU judge (ridge + majority; serial rebench)
  5. NVML snapshot + J/candidate estimate
  6. vLLM SLM already on the card as parallel teacher (do not GPU-ify the judge)
  7. Hermes/OMP histories compiled (~76,970 next-action episodes)
  8. ridge vs GPU-MB: empty-text bug fixed (user/tier). Champion recipe:
     n_train=all (61,576), gold_only=false, confirm_frac=0.2
     GPU 0.566 vs ridge 0.363 vs majority 0.353. Gold-only: ridge wins.
  10. gpu-abab: A mutates DataRecipe, B trains P=256, keep iff GPU>ridge+1pp and majority
      8 waves, 2 keeps (wave 4 n=16384; wave 5 full split).

NOW / DONE (2026-09-17 → data-engine pivot)
  9. shadow + confidence/abstain cascade on wave-5 champion (gen-0 locked)
     - wave-5 pack: n_kc=96 k_winners=20 pn_dim=64 confirm=0.572 (beats ridge 0.363)
     - promote-only writes; champion.npz gitignored + history-scrubbed (local only)
  10. safe-offload metric shift: maximize coverage @ ≥95% precision (not raw acc)
     - **FROZEN claim language** → `docs/SCIENCE_CLAIMS.md` + `coverage_metric.frozen_claims()`
     - risk/coverage gen-0: DELEGATE *predictions* ~0.980 precise (n=3063, ~19.9% confirm)
       → prediction-conditional only; report base-rate / lift / FPR with precision
       → absorb **all** DELEGATE preds locally (min_p=0)
     - EXECUTE has **no** ≥0.90 precision slice by margin; keep high-conf only
       (p≥0.60, margin≥0.30 → n≈2931 prec≈0.664)
     - updated cascade eval: cov≈0.389 local_prec≈0.826 (was cov≈0.196 / prec≈0.669)
  10b. weak-family oversample dead end (boost 1/2/4/8): best overall 0.380 ≪ 0.572
  10c. live private stream scaffold (collect first, train async):
     - `~/.z0int/stream/{raw,high_info,outcome_gold}.jsonl`
     - schema: trace_id, session_id, fly probs, Jev when queried, route, latency, disagree
     - flyforge-jev OMP extension: log-only multi-session writer
     - `evolution_lab.live_stream` + `pn_features` structured-cue scaffold

NEXT (capability cartography + residual allocation)
  11. DONE offline 2×2 rich PN → gen-1 (DEV metrics); gen-0 backup kept
  12. DONE Capability Miner (`evolution_lab capability-mine`)
  13. DONE hard boundary:
        z0int = cognition filter (what does NOT need frontier LLM)
        Kerdoios = residual allocator (where remaining cognition runs)
        Do NOT merge; Kerdoios NEVER executes flies; returns ExecutionPlan only
  14. DONE bridge slices:
        a. Kerdoios Observation: capability_id + input/output/cached/context tokens + latency
           aggregate lookup: (model, capability) → family → global
        b. WorkRequirement.capability_id
        c. z0int `preflight` CLI/API → route local|model + residual WorkRequirement
        d. OMP `z0int-bridge` extension (log-only): preflight → kerdoios plan → receipt
  15. TOP3 specialists (still L0 until outcome joins):
      delegate_gating / recovery_action / needs_verification
  16. Measure north-star: frontier tokens per verified task (Astra tokens/Blender milestone)
      counterfactual ledger: avoided vs baseline context
  17. ABAB on token consumption strategy; context typed evidence packs (not weak prose)

LATER
  18. Promote preflight from log-only → host route gate (after measured non-inferior success)
  19. Premium quota reserve policy for Astra; free-quota consume for Groq/Cerebras
  20. Memento / FlyGym / MaleCNS / 5B — only if atlas niches demand them

```

Do **not** start with FlyGym, full MaleCNS hot-path, or a 5B SLM as the fly executor.

Weights scale (151 PN × 96 KC + 96×5 MBON ≈ 15k floats ≈ 58 KB/fly FP32): 10k flies ≈ 0.6 GB weights. Incremental cost collapses if PN→KC is shared.

Judge stays CPU/frozen. GPU proposes; serial rebench promotes.

---

# 28b. Personalization path (kept, second)

The previous shortest path to useful personalized learning remains, *under* GPU evolution:

The current shortest path to useful personalized learning is:

```text
1. compile Hermes/OMP histories into episodes
2. train next-action / tool-family classifier
3. compare ridge/MLP vs mushroom-body
4. add temporal fly candidate
5. time-split evaluation
6. shadow predictions in live harness
7. collect disagreement + failure data
8. use Jev only on ambiguous high-information cases
9. let ABAB mutate dataset recipe + architecture
10. begin Memento collection for future visual track
```

This should come before:

- more AODL integration;
- broad personal SVLM fine-tuning;
- full MaleCNS hot-path inference;
- large manual labeling campaigns.

---

# 29. Repo responsibilities

| Repo | Role |
| --- | --- |
| `kvnloo/z0intelligence` | evidence/state compiler, decision runtime, dispatch/receipt authority, specialist/compiler surfaces |
| `kvnloo/evolution-lab` | experiment genomes, ABAB, DAgger, search, frozen promotion gates |
| `kvnloo/frontier-kb` | research memory, evidence, protocol, kill criteria |
| `kvnloo/aodl` | optional typed intent/orchestration IR |
| `NousResearch/hermes-agent` | execution runtime, state, tools, memory, messaging |
| `can1357/oh-my-pi` | fast coding harness, judgment and decision surfaces |
| `kvnloo/Memento` | prospective local screen/computer-use data source |

---

# 30. Important current links

- z0int: https://github.com/kvnloo/z0intelligence
- Evolution Lab: https://github.com/kvnloo/evolution-lab
- AODL: https://github.com/kvnloo/aodl
- frontier-kb: https://github.com/kvnloo/frontier-kb
- Memento: https://github.com/kvnloo/Memento
- Hermes Agent: https://github.com/NousResearch/hermes-agent
- Oh My Pi: https://github.com/can1357/oh-my-pi

Relevant Hermes decision work:

- bounded DecisionProvider RFC: https://github.com/NousResearch/hermes-agent/issues/113008
- DecisionProvider implementation PR: https://github.com/NousResearch/hermes-agent/pull/113020
- Jev computer-use lane issue: https://github.com/NousResearch/hermes-agent/issues/113850
- Jev computer-use implementation PR: https://github.com/NousResearch/hermes-agent/pull/114365

Relevant OMP work:

- first-class Jev skill suggestion issue: https://github.com/can1357/oh-my-pi/issues/12372
- Jev skill suggestion PR: https://github.com/can1357/oh-my-pi/pull/12373
- Jev computer-use lane: https://github.com/can1357/oh-my-pi/issues/12382

---

# 31. Research questions that remain open

## Data

- Which history source produces the highest marginal gain?
- How much context history is enough?
- Do Jev pseudo-labels improve or contaminate personal specialists?
- What is the best weighting of verifier-gold versus behavior-imitation labels?
- Which personal data should never be used for training even locally?

## Architecture

- When does mushroom-body local plasticity outperform linear controls?
- Which decision families genuinely benefit from recurrence?
- Does any MaleCNS topology provide a causal advantage over matched random/rewired recurrent controls?
- Can dynamic option-conditioned fly heads approach Jev-like generality?
- When does a small SLM/SVLM beat the fly hierarchy after full encoder cost?

## Production

- What confidence thresholds maximize safe coverage?
- Which decisions should be directly executed versus only used to narrow context/tools?
- How much frontier-model work can be removed without lowering task success?
- At what point should a learned behavior be compiled into deterministic code?

## Intent

- Can personal context measurably improve intent extraction?
- Does explicit AODL compilation improve execution over unstructured instructions?
- Which parts of identity are stable enough to learn versus better retrieved?

---

# 32. Anti-goals

Do not:

- treat all personal history as one SFT corpus;
- assume a larger model is better;
- simulate full MaleCNS because biology is interesting;
- hardcode Jev as the permanent teacher;
- let ABAB edit the judge;
- leak private data to public git;
- treat mutable user facts as model weights;
- invent a new AODL ontology for fly internals;
- claim "identity understanding" from text memorization;
- optimize benchmark accuracy while ignoring downstream task outcomes.

---

# 33. One-sentence north star

> **z0int should turn a user's own history into a continuously evolving population of tiny, private, verified specialists that understand recurring intent and context well enough to remove unnecessary work from frontier LLMs.**
