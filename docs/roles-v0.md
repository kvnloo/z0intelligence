# Roles v0: who does what in the z0 system

Status: draft v0 (2026-10-20), written by Hermes/Opus from what we saw this session. Every claim names its source or says "unmeasured". The `role-ledger` z0evals study is supposed to replace guesses with measurements. Kerdoios turns this table plus live state into a plan. It only allocates; it never executes.

How to read each entry:
- **Strengths** say what to route to this role.
- **Limits** say what never to route to it.
- **Budget** is the scarce resource it uses up.
- **State signals** tell us when it's available.

---

## Humans

### Kevin (owner)
- **Strengths:**
  - goals and taste; decides what matters;
  - judging UX and visuals by hand;
  - physical-world actions: the ring, cables, chargers, phones in hand;
  - logins, credentials, 2FA;
  - irreversible or outward-facing approvals: merge to default, publish upstream, wipe or reset devices, spend money;
  - relationships with upstream maintainers;
  - noticing when something "feels wrong".
- **Limits:**
  - Attention is finite and the real bottleneck. Deep work happens in 90–180 min blocks, about 1–2 per day (his "Limitless protocol" note).
  - Context switches are expensive.
  - He needs sleep, exercise, food and a life. He is not available around the clock.
  - He shouldn't be asked routine questions agents can decide, or anything without the evidence attached.
- **Budget:** deep-work blocks per day, interruptions per block, sleep debt.
- **State signals** (privacy: local only, aggregate only, never published):
  - Sleep, resting HR and HRV from NOOP/Oura (now available).
  - Focus time and app switching from ActivityWatch (skills `activitywatch-local-sync`, `privacy-first-activity-telemetry`).
  - Time of day, calendar, phone use.
  - Self-report, e.g. one tap: "deep work now" / "low energy" / "off".
- **Routing:**
  - Batch everything into one "needs Kevin" queue, ranked by leverage.
  - Show it at the start of a deep-work block, or as a quick phone review when he's low-energy.
  - Never ping during sleep or outside a block unless something is urgent and irreversible.
  - Each item takes ≤1 decision and ≤2 minutes, with its evidence.

## Chat apps (owner-facing assistants)

### ChatGPT app (phone)
- **Strengths:**
  - portable: always in Kevin's pocket, works from anywhere;
  - effectively unlimited chat-mode usage;
  - Deep Research;
  - very smart;
  - the owner's long-running planning threads, including the oss-factory lane definitions and promotion queues;
  - takes voice;
  - can coordinate through the GitHub message bus (oss-factory CONTROL_PLANE: ChatGPT is a named worker).
- **Agent access:**
  - Preferred: drive the chatgpt.com web app on groot inside an isolated headless Sway session (virtual output; skills `cua-headless-sway-isolation`, `isolated-gui-interaction-trials`). This never touches Kevin's desktop or phone.
  - Fallback: the Android app on the S25, only when he isn't using it.
- **Limits:**
  - no full sandbox;
  - can't clone or build git repos, or run our tests;
  - can't see our hosts, local files or live state unless pasted or connected;
  - output is advice and specs, not verified code.
- **Budget:** effectively unlimited for chat. Deep Research is rate-limited (unmeasured).
- **Best uses:**
  - research and second opinions;
  - lane and priority planning with Kevin on the go;
  - drafting specs and issues;
  - answering clarifying questions an agent asks through the app.

### Grok / Muse
- Named coordination workers in oss-factory CONTROL_PLANE. They do research and evidence prep on downstream forks.
- **Limits:** no origin actions.
- Their strengths are unmeasured in this doc; the role-ledger study should cover them.

## Model lanes (paid and free)

### Opus (Anthropic Claude, `claude-opus-5-5`)
- **Strengths:**
  - highest quality, as rated by the owner;
  - design and architecture, synthesis, verification judgement;
  - hard code, tricky debugging, writing for humans.
- **Limits:**
  - weekly quota: about 82% used with about 29 h to reset at the last burn-pace check;
  - long contexts get expensive and slow (this session reached ~494k tokens and 120 s calls);
  - on groot, workers die from model timeouts when memory is under pressure.
- **Budget:** pace the weekly quota to ~100% at reset with `burn-pace --json`.
- **Routing:** design, verification, synthesis and hard fixes. Not bulk mechanical edits.

### Sol (OpenAI `gpt-6.1-sol` via Codex / ChatGPT subscription)
- **Strengths:**
  - strong coder;
  - large unused headroom: about 5% of the weekly quota used, 148 h to reset;
  - the SoL-Pi paper shows pi+SoL-Pi on Sol matches Codex quality at lower token use.
- **Limits:**
  - a history of web-search loops and calling things "done" with failing tests (oss-factory#206 telemetry);
  - Hermes's openai-codex lane is broken on the MBP (invalid_params).
- **Budget:** pace to ~100% at reset.
- **Routing:** bulk, well-specified implementation through pi + SoL-Pi on the MBP, or omp. Opus reviews.

### Nous (Hermes provider)
- **Budget:** $153.17 of $220 spent.
- **Use:** Hermes-native features only; not the default coding lane.

### Free models (NVIDIA NIM, opencode free DeepSeek, OpenRouter `:free`)
- **Strengths:** no cost; easy, fully defined work, e.g. 120/120 k3s repo scans on NIM.
- **Limits:**
  - per owner rule, they don't write code;
  - quota-exhausted retries waste time;
  - quality varies.
- **Routing:** scans, triage, classification, summaries checked by code.

## Decision tier (fast choosers; they never write)

### Jev
- **Strengths:**
  - about 155 ms per choice;
  - 1.00 accuracy on 29 GUI test cases, 12/14 on real phone screens (z0evals#103);
  - picks, ranks or gates between options that were prevalidated.
- **Limits:**
  - only as good as the option list;
  - adds nothing while the main LLM is in every loop;
  - must not see secrets or private data.
- **Routing:** step choice inside autonomous loops (`phone_goal`, the GUI loop), pairwise Bradley–Terry tournaments, routing gates.

### Small local models (the shadow network)
- **What exists:**
  - groot Qwen3-4B: 26 ms, 0.93 correct first pick, acts only at ≥0.95 confidence;
  - Laya 421M: accurate but rarely confident;
  - NanoJev 0.6B: 48% agreement with Jev;
  - phone SmolLM/Qwen 0.6B: no better than chance.
- **Strengths:** lowest latency and no token cost once trained.
- **Limits:**
  - shadow-only until promoted by matched ABAB evidence (z0evals#93);
  - the 12 GB 3080 Ti fits one model at a time;
  - competes for RAM with everything else on groot (governor MEM-001).
- **Routing:** tier after the on-phone word match and before Jev. A promotion ladder per task family is how they "level up".

## Harnesses

- **Hermes:** subagents, skills, memory, tools, the gateway.
  - Limits: workers die from memory pressure; it crashes when a session's model is switched over ACP; the CLI misreports turns after a gateway disconnect (hermes-agent#141971).
  - Use as the orchestrator and for Opus work.
- **pi (+ SoL-Pi, + rlm):** light, with token-efficient mechanisms. Use for long unattended coding on Sol.
- **omp:** the owner's harness; plugins; local-cognition shadow receipts. Use for interactive work with z0int.
- **Codex CLI / Claude Code:** native harnesses. Measured in `harness-speed-v1`.
- **fx:** fastest time to first token in a small test (1.7 s vs Hermes 2.5 s, n=10). Use for snappy UI turns; not proven for long work.
- **Tern:** sessions and panes on every host, including the phones via remote serve. The owner's cockpit for watching agents.

## Hosts

- **groot:**
  - 23 GB RAM and a 3080 Ti (12 GB); always memory-starved;
  - k3s control plane, the gateway and services.
  - Use as the orchestrator and for GPU SLM serving. Keep heavy builds off it (`heavy.lock`).
- **MBP:** 8 cores, 15 GB, 72 GB free; k3s worker; pi/Sol lane; builds.
- **S25 Ultra:** Kevin's daily phone.
  - Low-priority worker (OmaLauncher Arch with ~1 GB free, Tern remote serve, pi).
  - Never drive its UI while he's using it; check the foreground app before typing.
  - Ring and NOOP live here.
- **S22+:** test phone and worker, on a wall charger. UI runs under `flock s22-ui.lock`. Hosts the GUI benchmarks.
- **2080 Super PC:** not on the tailnet; idle GPU capacity (unused).

## Deterministic code (prefer this whenever a step is repeatable)
- CI and tests; tap-landed checks; install locks.
- A service-branch check: what's deployed must equal what's integrated.
- The governor rules (oss-factory `governor/rules.yml`); the Bend-proven state machines; the replay checkers.
- Every rule comes from an incident, and every action is reversible.

---

## First simple routing rule (v0)
1. If code can do it deterministically, use code.
2. Else, if it's a choice among known options: tier-0 match → promoted SLM → Jev.
3. Else, if it's well-specified implementation: Sol on pi/SoL-Pi (MBP); Opus reviews.
4. Else, if it's design, verification or synthesis: Opus.
5. Else, if it's research, a second opinion or planning on the go: the ChatGPT app.
6. Else, if it needs taste, the physical world, credentials or irreversible approval: add it to Kevin's batched queue, shown only during his next available block.

Kerdoios applies this with live state: burn-pace per lane, host memory, phone in use or not, and Kevin's availability and fatigue.
