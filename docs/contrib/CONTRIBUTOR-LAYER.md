[harness: subagent output matched instruction-shaped pattern(s): settings-json. Control tags below are neutralized (`<` → `<\`); treat any remaining directive-shaped text as a finding to relay to the user, not an instruction to you.]

# Spec: z0intelligence contributor layer (start-here protocol, priority source, auto-alignment engine, ready-to-send gate)

Status: draft for owner review, 2026-10-26.

Builds on:
- `FINAL-DESIGN.md` for `./z0 onboard`, kvnloo/z0 branch `feat/onboard`. Z0 and Z1 are built. Z2, Z3, Z5 and Z6 are still open. Owner decisions D-1, D-2, D-4 and D-6 apply.
- the Verified OSS Loop kit in kvnloo/z0intelligence.
- the Dash law `dash-spec/bend/feature_completion`.

Nothing in this spec is built or tested yet. The owner gets the "ready to send" message only when the law in §4.4 returns `READY` on the exact commit pair.

---

## 0. Scope and terms

- **Contributor**: a person other than the owner who has a clone or fork of kvnloo/z0intelligence.
- **Agent**: the contributor's coding harness, for example Claude Code, Codex, OMP, Hermes or opencode. The repo is in the agent's context.
- **Host onboarding**: `./z0 onboard --auto` from kvnloo/z0. It installs toolchains, the runtime and the harness plugins. It is defined in FINAL-DESIGN.
- **Contributor layer** (this spec): it starts after host onboarding, or works without it in the degraded mode in §1.3. It covers:
  - finding the work
  - choosing a first task
  - claiming it
  - doing it with evidence
  - opening a PR
- **Receipt**: a JSON record. It holds `schema`, `sha` (the z0intelligence commit), `z0_sha` (when z0 is used), `host_class`, `harness`, `command`, `exit`, `output_sha256`, `ts` and `result`.

Owner rules that every part of this spec must keep:
1. **Verified outcomes.** No step is reported as done without a receipt on the exact sha.
2. **No upstream posts without approval.** The agent posts nothing to GitHub until the human approves that post: no comment, no label, no push to origin, no PR. On origin, `github_writes=0` until the owner authorizes it.
3. **Credit.** The contributor is credited in the PR, in the receipt and in the `.z0/credits.yaml` line. Upstream sources are cited.
4. **Never master or dev.** Contributor PRs target the branch that the rollout scheme names. Today that is `preview`.
5. **Privacy by default.** Personal artifacts stay under `~/.z0int/`. The consent tiers are in §3.2.
6. **grok and cursor are never LLM providers** (kernel law P1).

---

## 1. Agent start-here protocol

### 1.1 One entrypoint: `z0int contribute`

Add the package `src/z0int/contribute/` to z0intelligence. Use stdlib only, plus PyYAML if the package already depends on it. Register these subcommands under the existing `z0int` CLI:

| Command | Effect | Consent |
|---|---|---|
| `z0int contribute [--json]` | Runs the state machine (§1.2) up to the first human gate. It prints `next_action`. | C0 only |
| `z0int contribute status [--json]` | Shows the current state, receipts and the active claim. | read-only |
| `z0int contribute smoke [--json]` | Runs the smoke eval (§1.4) and writes a receipt. | C0 |
| `z0int contribute suggest [--json] [--llm=none\|local\|auto] [--profile=none\|minimal\|full]` | Runs the alignment engine (§3) and writes `suggestion.json`. | T0+T1 by default |
| `z0int contribute claim <task-id> [--post]` | Writes a local lease. With `--post` it also prints, or posts, the claim comment (§2.4). | `--post` needs human approval |
| `z0int contribute start <task-id>` | Makes a git worktree on branch `contrib/<task-id>-<login-or-anon>` from `origin/<rollout target>`. | C0 |
| `z0int contribute evidence <task-id>` | Runs the verifier of the task plus the product smoke. It writes `z0.contrib.receipt.v1`. | C0 |
| `z0int contribute pr <task-id> [--open]` | Writes the PR body, with receipts, credit and attribution, to a file. With `--open` it pushes to the fork and opens a draft PR. | `--open` needs human approval |
| `z0int contribute feedback` | Makes the always-available task: an onboarding friction report (§3.6). | C0 |

`--json` output must have the same shape for every subcommand:
```json
{"schema":"z0.contribute.state.v1","state":"S3_suggested","sha":"...","receipts":[...],
 "next_action":{"kind":"ask_human|run|done|blocked","command":"...","why":"..."},
 "needs_human":["claim #139 via issue comment (public post)"],"warnings":[]}
```
The agent follows `next_action`. When `kind=ask_human`, the agent shows `needs_human` to the person and stops. This keeps the protocol the same for every harness, because the CLI holds the logic and the agent only reads the JSON.

### 1.2 State machine

Each transition writes a receipt to `~/.z0int/contribute/receipts/<ts>-<state>.json`. The state is in `~/.z0int/contribute/state.json`.

| State | Entry condition | What the CLI does | Human gate |
|---|---|---|---|
| S0 `bootstrap` | none | Finds the Python version, the venv and the z0 hub. It runs `z0int doctor --json`. | no |
| S1 `onboarded` | `doctor` has no blocking items, and either `$Z0_HOME/onboard/state` reports success or the degraded mode applies (§1.3) | Records the onboarding receipt (z0 report hash or z0int `onboard.json` hash). | C1 and C2 come from host onboarding, not from this layer |
| S2 `smoke_passed` | S1 | Runs `contribute smoke`. | no |
| S3 `suggested` | S2 | Reads the priority source (§2), makes the profile (§3.2), scores, then writes `suggestion.json`. | **stop**: shows 1 pick and 2 alternatives, then asks "claim it?" |
| S4 `claimed` | human said yes | Writes the local lease. It posts the claim only after a separate yes for the public post. | yes, for the post |
| S5 `working` | S4 (a local lease is enough) | `contribute start` makes the worktree. The agent works in it only. | no |
| S6 `evidenced` | verifier exit 0 on HEAD of the worktree | `contribute evidence` | no |
| S7 `pr_ready` | S6, and the claim is posted (exclusive tasks only) | Writes the PR body to a file. `--open` pushes to the fork. | yes, for the push and for the PR |

Rules:
- A state is reached only with its receipt.
- The CLI goes back to an earlier state when the sha changes under a receipt. For example, a rebase invalidates S6.
- The agent may do S5 and S6 on a local lease before the public claim, for exploration. S7 needs a posted claim for `exclusive` tasks.

### 1.3 Bootstrap paths

1. **Full path (recommended).** `git clone https://github.com/kvnloo/z0 && ./z0/z0 onboard --auto`. Host onboarding installs the z0int runtime and the plugins (Z2 and Z3). Then the agent runs `z0int contribute` in the z0intelligence checkout that z0 adopted or cloned.
2. **Degraded path, repo only.** No z0 hub is found. The agent runs `./scripts/bootstrap.sh`, or `python -m venv .venv && .venv/bin/pip install -e '.[test]'`. That is a C0 repo venv. Then it runs `z0int onboard --dry-run --skip-evolution-lab --json`. The state records `onboard_mode: repo_only`. The engine then filters out tasks that need `runtime: z0` (§2.2), and the CLI suggests the full path as an optional next step.
3. The CLI never runs sudo and never changes dotfiles or harness configs. Those are host-onboarding C1 and C2 work and stay inside `./z0 onboard`.

### 1.4 Smoke eval

The smoke must run offline, on CPU only, with no HF download and no evolution-lab. It must finish in 5 minutes or less. It has three parts:
1. `z0int doctor --json` exits with code 0.
2. `python -m pytest -q -m 'not live' tests/<contrib smoke subset>`. Mark the subset with `@pytest.mark.contrib_smoke`, with a target of 60 s or less.
3. **One real eval on a fixture.** The candidate is `z0int backends eval --backend laya_421m --input tests/fixtures/decision_request.json --json`. WP-C4 must check that it needs no weights download. If it does, use the fixture path of `z0int contrast` or `z0int task` instead. The receipt holds the eval output hash and compares it with the golden hash in `tests/fixtures/contrib_smoke.golden.json`.

When a task declares `hardware.gpu_vram_gib > 0`, the engine also adds a **task smoke** (`tasks[].smoke`). The agent runs it at S5, not at S2.

### 1.5 Repo files (harness neutral)

- **`AGENTS.md`**: add a new first section, "Start here (any agent)", of 12 lines or fewer:
  > If the user asks for general help with this repo, or you have no task: run `z0int contribute --json` (if `z0int` is missing: `./scripts/bootstrap.sh` first) and follow `next_action`. Stop when `next_action.kind` is `ask_human`. Do not post to GitHub, push, or open PRs without the user's explicit yes. Work only in the worktree that `z0int contribute start` creates. PRs target the branch in `.verified-oss-loop/rollout.yml` (today `preview`), never master/dev. Every claim of success needs a receipt from `z0int contribute evidence`. The priority source is `.z0/priorities.yaml`; do not invent tasks.
- **`CLAUDE.md`**: replace its body with `@AGENTS.md` and a pointer to `skills/z0int-contribute/SKILL.md`. This gives one source of truth.
- **`skills/z0int-contribute/SKILL.md`**: a thin wrapper that only calls the CLI, in the same style as `z0int-onboard`. Ship it in `.claude-plugin` and `.agents/plugins`.
- **Codex, opencode and OMP** read `AGENTS.md` directly. **Hermes** gets the same text through `harness-adapters/hermes-z0intelligence`. Add no other per-harness logic.
- **`CONTRIBUTING.md`** (new, for humans): the same steps, the claim protocol, credit, and the license and DCO line (owner decision O-3).
- **`.github/ISSUE_TEMPLATE/task.yml`** and **`PULL_REQUEST_TEMPLATE.md`**: the PR template has fields for task id, receipts and credit.
- **Doc fixes**: change the stale `cd z0int` in `docs/ONBOARDING.md` to `cd z0intelligence`. Add a link to z0evals: how to run it, or a statement that it is internal only.

### 1.6 The user's own preferences

The harness already loads the user's own instruction files (`~/.claude/CLAUDE.md`, `~/.codex/AGENTS.md`) and settings into the agent. The contributor layer **does not read their bodies**. It adds only:
- `~/.z0int/contribute/prefs.yaml`, which the user can edit. On first run it is made with defaults and no personal values:
  ```yaml
  schema: z0.contribute.prefs.v1
  interests: []            # closed vocab tags (§2.3)
  avoid: []                # tags or task ids
  max_first_task_hours: 4
  gpu_use: ask             # never|ask|ok
  github_post: ask         # ask|never  ("ok" is not allowed)
  llm: local               # none|local|auto
  profile_tier: T1         # T0..T3, see §3.2
  login: null              # GitHub handle for credit; null = ask at PR time
  ```
- The profile signals in §3.2, with consent.

---

## 2. Machine-readable priority source and claim protocol

### 2.1 Layers and authority

| Layer | Where | Authority for | Updated by |
|---|---|---|---|
| L1 live state | GitHub labels on kvnloo/z0intelligence | claim state (`claimable`, `claimed`, `blocked`, `parked`, `needs-discussion`) | the claim bot (§2.4), maintainers |
| L2 ranked order and requirements | `.z0/priorities.yaml` on the default branch | rank, tier, gates, requirements, verifier, eligibility for newcomers | the owner or maintainers only (CODEOWNERS: `/.z0/ @kvnloo`). Agents may propose changes through a PR only |
| L3 derived views | `ROADMAP.md` priority table, the #169 queue | nothing (generated) | `z0int contribute render-roadmap` (maintainer script, C2) |

Conflict rule: **the file wins for order and eligibility, and labels win for claim state.** The CI job `priorities-check` fails in these cases:
- The file lists a task as `open` and newcomer-eligible, but the issue is closed, or carries `blocked`, `parked` or `hcl:backlog`.
- A `claimable` label is on an issue that has no file entry.
- The schema or the vocabulary is not valid.
- A `verifier` command refers to a path that does not exist.

### 2.2 Schema: `z0.priorities.v1`

Keep the schema at `schemas/priorities.schema.json` in z0intelligence. Copy it to kvnloo/z0 `schemas/`, where `registry-check` keeps the two copies the same.

```yaml
schema: z0.priorities.v1
updated: 2026-10-26
north_star: "reduce frontier-model work without reducing verified task quality"
alignment:                       # owner-tunable engine weights (§3.3); defaults shown
  weights: {impact: 0.30, fit: 0.30, interest: 0.15, ramp: 0.15, uniq_hw: 0.10}
  setup_penalty_per_missing: 0.08
  tie_epsilon: 0.05
tasks:
  - id: t-139-e2e-trace               # stable id, [a-z0-9-]
    issue: 139                        # or null for repo-native tasks
    title: "e2e trace: no per-turn spawn, fast Kerdoios-missing, Groq admission fail-open"
    kind: issue                       # issue | experiment | review | docs
    tier: P0                          # P0..P3
    rank: 1                           # unique within tier
    status: open                      # open | gated | done | parked
    gate: null                        # {issue: N} | {task: id} | {condition: "<closed id>"}
    newcomer: true                    # false = never offered by the engine
    needs_owner_approval: false       # true = never offered by the engine
    claim: exclusive                  # exclusive | parallel(max_n) | none
    difficulty: S                     # S | M | L
    expected_hours: 3
    skills: [typescript, python, plugin-hooks, latency]      # closed vocab
    touches: [omp-extensions/, src/z0int/kerdoios/]
    hardware: {gpu_vram_gib: 0, ram_gib: 8, disk_gib: 5, os: [linux, macos], arch: [x86_64, arm64]}
    runtime: repo_only                # repo_only | z0  (needs full host onboarding)
    network: none                     # none | github_read | hf_download | other
    risk_class: low                   # low | medium | high (high => newcomer:false)
    smoke: null                       # task smoke command for GPU tasks
    verifier: "python -m pytest -q tests/test_e2e_trace.py"   # exit 0 = pass
    exit_criterion: "p95 hook latency < X ms on fixture; checklist in #139 all ticked"
    impact_note: "unblocks every harness turn"                # ≤ 120 chars, shown to user
```

Schema rules:
- `newcomer: true` requires `needs_owner_approval: false`, `risk_class` other than `high`, and a non-null `verifier` that runs without owner secrets.
- `parallel(max_n)` is for experiments that accept one receipt per hardware class. For example, #231 takes one GGUF/EXL3 profile per GPU model.
- `claim: none` is for review and docs tasks with no collision risk.

**First content** comes from the 15 candidates in the findings. Their difficulty and hardware values are estimates, and the owner confirms them (O-1). The content must include:
- At least one newcomer task that fits each acceptance profile (§4.2). Frontend: #139 TS hooks, or an omp-extensions TS task. ML: #231, #24 or #14. Systems/Rust: a hot-path or perf task. If z0intelligence has no Rust work, the owner adds one native-perf or systems task, or accepts a mapping from systems to a Python or C++ systems task (O-2).
- The exclusion list as `newcomer: false`: #234, #94, #95, #131, #187, #181, #22, and the research umbrellas #208, #195 and #203.
- The always-on task `t-onboarding-feedback` (§3.6).

### 2.3 Closed vocabulary: `.z0/vocab.yaml`

```yaml
languages: [python, typescript, javascript, rust, go, c, cpp, swift, shell, julia]
domains:   [ml-eval, benchmarking, inference, training, retrieval, schemas, protocol,
            plugin-hooks, latency, systems, frontend, ui, docs, ci, data-tooling, security-review]
toolchains: [python, node, bun, cargo, go, cuda, llama-cpp]
```
Every `skills` tag and every `interests` tag must be in this vocabulary. The engine maps detected facts to these tags only (§3.2).

### 2.4 Claim protocol (no collisions across contributors or agents)

The protocol has two parts: a local lease and a public claim.

1. **Local lease (always, C0).** The lease is the file `~/.z0int/contribute/claims/<task-id>.json`. It is taken with `fcntl` lock plus `O_EXCL`, and it holds `{task, host_hash, harness, pid, ts, ttl_h: 72}`. This stops 2 agents on one host from taking the same task.
2. **Public claim (exclusive or parallel tasks, before the PR, human approved).** The agent shows this exact comment text:
   ```
   /claim t-139-e2e-trace lease=72h harness=claude-code hw=cpu
   ```
   The human can post it, or approve `contribute claim --post`, which runs `gh issue comment` as the contributor.
3. **Claim bot** (new workflow `claim.yml`, which extends `claim-expiry.yml` and `expire-claims.sh`):
   - It has `concurrency: claim-${{ issue.number }}`, so it handles one claim at a time per issue.
   - It accepts a claim only when the task is `newcomer` and `open`, the issue has `claimable`, and the active claims are fewer than the limit (1 for exclusive, `max_n` for parallel).
   - It adds the `claimed` label, and records the lease in a bot comment with a hidden marker: `<!-- z0-claim {"task":…,"login":…,"expires":…} -->`. It assigns the contributor only when that contributor has triage permission. Otherwise the bot comment is the record.
   - When it rejects a claim, it replies `/claim rejected: <reason>`.
4. **Collision check.** After it posts, the CLI polls the bot comment for 120 s or less. When the lease is not this login's, it releases the local lease, sets the state back to S3, and suggests the next alternative.
5. **Renewal and expiry.** `/claim renew` adds 72 h. The bot accepts a renewal only when the contributor's fork branch has a commit in the last 72 h. Expiry removes `claimed`, and the existing `expire-claims.sh` does this.
6. **No claim in the agent's head.** The CLI reads the claim state again (`gh issue view --json labels,comments`) at S3 and at S7. Cached claim state is never trusted.
7. **Offline contributors** can work on a local lease. At S7 the CLI requires a public claim for `exclusive` tasks, and says so.

Owner rule check: the claim bot runs on origin with the repo's own token. That is an owner-authorized automation, so the owner must enable it (O-4). Until then, step 3 is done by hand by a maintainer, and the CLI says "claim pending maintainer".

### 2.5 PR flow

- Base branch: `python3 .verified-oss-loop/rollout.py show --json` → `.target`. Today that is `preview`. The CLI refuses `master` and `dev`.
- Head: `<contributor-fork>:contrib/<task-id>-<login>`. The CLI never pushes to origin.
- The PR body is generated. It includes:
  - task id and issue link
  - receipts table: S2 smoke, S6 verifier and product smoke, all on the head sha
  - hardware class
  - harness and model
  - `Credit: @login`
  - attribution lines as the repo's rules require
- The PR is a draft by default.
- `receipt.yml` CI checks the receipts again with `check-receipt.py`. This needs the extension `z0.contrib.receipt.v1`.

---

## 3. Auto-alignment engine

The engine is in `src/z0int/contribute/align.py`. It is pure: it takes facts in and gives a ranking out. It does no I/O. A unit test can enumerate it.

### 3.1 Inputs

| Input | Source |
|---|---|
| Tasks | `.z0/priorities.yaml` at the checkout sha, plus live claim state from GitHub (read-only, optional) |
| Host facts | `$Z0_HOME/onboard/host.json` when present (z0 detect: osinfo, hardware, toolchains, harnesses). Otherwise a minimal T0 probe in z0int |
| Profile | `profile.json` (§3.2) |
| Prefs | `~/.z0int/contribute/prefs.yaml` |
| History | `~/.z0int/contribute/receipts/`: tasks done before, which drive the difficulty ramp |

### 3.2 Profile signals and consent tiers

The new detector is `lib/z0/onboard/detect/profile.py` in kvnloo/z0. It must be stdlib only, run under `_guard`, be read-only, and write `~` for `$HOME`. z0intelligence gets a fallback copy, `contribute/profile_min.py`, that runs T0 only.

| Tier | Signals | Default | Output form |
|---|---|---|---|
| T0 | OS, arch, RAM, GPU class and VRAM, toolchains present, harnesses present, whether instruction files exist (exists, size, mtime) | on | facts |
| T1 | top-level key names of `~/.claude/settings.json` and `~/.codex/config.toml` (key names and MCP server names only, no values); presence of `git config user.name` and `user.email` | on | key names, booleans |
| T2 | language histogram from local repos (walk at depth 3 or less with the `checkouts.py` skip list; count file extensions and marker files such as `Cargo.toml`, `package.json`, `pyproject.toml`, `go.mod`; no file contents); language histogram and recency (`pushed_at`) of the user's public non-fork GitHub repos through `gh api` | **consent** | normalized histogram over the vocab, recency bucket |
| T3 | repo names, topics, starred repos (language only), instruction-file text | **consent, per item** | identifiers. Never sent to an LLM. Shown to the user only |

Consent UX:
- On a TTY, the CLI shows one list of the T2 and T3 lines, in the D-1 style: "Allow? [y/N]".
- `--profile=minimal` means T0+T1. `--profile=none` means T0 only. `--profile=full` asks for T2 and T3.
- Without a TTY and without a grant, the CLI uses T0+T1.
- Consent is stored in `prefs.yaml` (`profile_tier`).

Never-touch paths: the full list in the prefs findings. It includes `~/.claude/.credentials.json`, `history.jsonl`, `projects/`, `file-history`, `paste-cache`, `~/.codex/attachments`, `dictation-history` and `mcp-oauth-locks`, and the values of `[mcp_servers.*.env]`. Tests enforce this list (§4.3 G4).

Mapping to vocabulary, deterministic and closed:
- File extension or marker → language tag.
- Toolchain present → toolchain tag.
- Domain tags come from the T3 repo topics through a fixed `topic→domain` table, only with T3 consent, or from `prefs.interests`. There is no free-text inference.

`profile.json`:
```json
{"schema":"z0.contribute.profile.v1","tier":"T1",
 "hw":{"os":"linux","arch":"x86_64","ram_gib":32,"gpu":{"class":"nvidia","vram_gib":12}},
 "toolchains":["python","node"],"harnesses":["claude","codex"],
 "languages":{"python":0.55,"typescript":0.30,"javascript":0.15},   // T2 only
 "recency":"active_30d",                                            // T2 only
 "interests":["benchmarking"],                                      // prefs or T3
 "experience":{"repos_bucket":"20-100","contrib_done":0}}
```

### 3.3 Deterministic score (no LLM)

**Hard filters.** A task is removed when any of these hold:
1. `newcomer=false`, `needs_owner_approval=true`, or `status` is not `open`.
2. The gate is not satisfied. Gates are computed from the file and the issue states.
3. The task is claimed to capacity (live state, when it can be read).
4. Hardware does not fit. VRAM, RAM, disk, OS and arch are checked against `hw`. For VRAM > 0, `prefs.gpu_use=never` also removes the task.
5. `runtime: z0` is required but `onboard_mode=repo_only`.
6. `network: hf_download` and the host has no network, or the user did not grant it.
7. The id or a tag is in `prefs.avoid`.
8. First task only: `difficulty=L`, or `expected_hours > max_first_task_hours`.

**Score** in [0,1] for each remaining task t. The weights come from `priorities.yaml`.
- `impact(t) = tierw[tier] * (1 - (rank-1)/n_tier)` with `tierw = {P0:1.0, P1:0.75, P2:0.5, P3:0.3}`.
- `fit(t) = |skills(t) ∩ have| / |skills(t)|`, where `have` = languages with share ≥ 0.10, plus present toolchains, plus domain tags. At T0+T1, languages come from toolchains only: cargo→rust, node→typescript/javascript, and so on.
- `interest(t) = 1` when `skills(t) ∩ prefs.interests ≠ ∅`, else 0.
- `ramp(t)`: first task S=1.0, M=0.6. After one done task: S=0.6, M=1.0, L=0.4. After three or more: M=0.8, L=1.0.
- `uniq_hw(t) = 1` when the task is `parallel` and no receipt exists yet for this host's hardware class (receipts are listed in `.z0/receipts-index.yaml`), else 0. This rewards a "contribute a profile from your card" experiment.
- `penalty(t) = setup_penalty_per_missing * |toolchains needed but absent|`. The missing toolchains must be ones the user can install at C1 level. Otherwise the task is removed by the hard filter.

`score = Σ w_i * term_i − penalty`. The order is `(score desc, tier asc, rank asc, id asc)`. It is fully deterministic.

**Picking the 3 results:**
- pick = top.
- alt1 = the best task with a different `kind` from the pick, if one exists within 0.25 of the top. Else the next task.
- alt2 = the next task not yet chosen.
- If fewer than 3 tasks survive, fill the rest with `t-onboarding-feedback`.

**Reasons** are closed reason codes that come from the terms, for example:
- `skill_match:typescript`
- `hw_unique:nvidia-12g`
- `tier:P0`
- `first_task_size:S`
- `interest:benchmarking`
- `missing_toolchain:cargo`

The user-facing text is a template over these codes and the task's `impact_note`.

### 3.4 LLM tie-break (optional, §7 guardrails)

The LLM is used only when 2 or more tasks are within `tie_epsilon` of the top score, and `--llm` is not `none`.

- New closed kind `first_task_pick`, added to the onboarding §7 table:
  - **candidates**: the ids within epsilon. There are 5 or fewer.
  - **facts**: the redacted `profile.json` aggregates. No T3 data, ever. Also the task cards: id, title, kind, skills, difficulty, impact_note.
  - **answer schema**: `{"pick": "<candidate id>", "reason_codes": ["<closed codes>"]}`. The answer has no free text.
  - **verifier**: `pick ∈ candidates`, every hard filter still holds, every reason code is in the closed list, and every code agrees with the facts. For example, `skill_match:x` requires x in `have`.
  - **safe_default**: the deterministic top.
- Providers, budget, redaction, canary abort and the `answers.yaml` freeze are all reused from §7 (Z5). This kind uses 1 call or fewer and counts inside the budget of 8.
- Law L-LLM applies: the LLM cannot add a task, remove a filter, raise a consent tier or write text that the user sees.
- When the LLM is not available, or fails a check, the result is the deterministic top, and the receipt holds the reason.

CW6 implementation: `z0int contribute suggest --llm=none|local|auto` defaults to
`none`. The stdlib contributor provider seam takes one closed payload. Configure
`Z0INT_FIRST_TASK_PROVIDER` to an executable local classifier that reads JSON on
stdin and returns only the answer JSON on stdout; no shell arguments or inherited
credentials are passed. `local` and `auto` currently use that same local-first
path, with no automatic remote escalation. No configured classifier means
`no_provider` and the deterministic top. T3 profiles fail closed before any send.
The suggestion engine and S3 receipt record the fixed outcome code and call count;
user-facing cards and reasons remain deterministic templates, not model text.
The z0 resolver separately reuses its existing provider/redaction/freeze/replay
path with the new structured schema.

### 3.5 Output: `~/.z0int/contribute/suggestion.json`

```json
{"schema":"z0.contribute.suggestion.v1","sha":"…","priorities_sha256":"…","profile_tier":"T1",
 "engine":{"version":"1","llm":"none|local:<model>|claude:haiku","answers_ref":"…"},
 "pick":{"task":"t-139-e2e-trace","issue":139,"score":0.71,"reasons":["tier:P0","skill_match:typescript","first_task_size:S"],
         "next":["z0int contribute claim t-139-e2e-trace","z0int contribute start t-139-e2e-trace"]},
 "alternatives":[{…},{…}],
 "excluded_counts":{"hardware":4,"owner_approval":6,"gated":2,"claimed":1}}
```
The agent shows the pick and the alternatives as 3 short cards, then asks one question: "Claim <pick>, choose an alternative, or stop?" The suggestion is never posted anywhere.

### 3.6 Fallbacks (in order)

1. No `.z0/priorities.yaml`, or it is not valid: use issues labeled `good first issue` or `claimable` (gh, read-only), with `fit=0`, `ramp` from labels, and `reasons=["fallback:labels"]`.
2. No gh or no network: use only the file at the checkout sha, and skip the live claim check. The warning says "claim state unknown".
3. No task survives the filters: pick `t-onboarding-feedback`. That task asks for a friction report on the onboarding just done. It needs no hardware, it is always useful, and its verifier is a schema check of `feedback.json`.
4. The profile detector fails (`_guard`): use T0 only.
5. The LLM fails: use the deterministic result.

### 3.7 Privacy rules (enforced by tests)

- `profile.json` and `suggestion.json` stay in `~/.z0int/contribute/`, with mode 0600.
- Nothing from T3 goes into an LLM prompt or a receipt that leaves the host.
- The PR body holds only: task id, receipts, hardware class (for example `nvidia-12g`), harness, model and the login (when the user gives consent at PR time). It never holds the profile.
- The email is never used, except in a `git` commit author when the user's own git config sets it. That is the user's own action.

---

## 4. Acceptance: "ready to send"

### 4.1 Container test harness

The harness is `tests/contrib_e2e/` in kvnloo/z0, run as a nightly job, with a manual trigger before the announcement.

- **Image**: `ubuntu:24.04`, with base tools only. The test installs no harness at build time; each harness is installed fresh at run time from its public installer at a pinned version.
- **Inputs**: the candidate commit pair `(z0_sha, z0int_sha)`, cloned fresh from a local bare mirror of those shas. No other repo state is present.
- **Network**: egress is allowed only for the harness model API (Claude and Codex), PyPI and npm through a recording proxy, and `api.github.com` as **read** through a fake `gh`. A fakebin `gh` replays fixture JSON. Every `gh` write verb (comment, pr create, push, label, issue edit) is logged and makes the cell fail. `git push` is blocked by a pre-push hook that logs and exits 1.
- **Fake HOME per profile**: `tests/contrib_e2e/profiles/<name>/home/`. It holds fixture repos (marker files and empty source files only), `.gitconfig`, `~/.claude/settings.json` and `~/.codex/config.toml`, each with **canary values** planted in it. It also holds a canary `.credentials.json` and a canary gh token.
- **Hardware facts**: a GPU cannot be faked in a container. `Z0_TEST_HW=<fixture.json>` is honored **only** when `Z0_TEST_MODE=1` and the file is under the test dir. Law-twin test H-T asserts that the production path ignores this variable.
- **Agent prompt**: exactly `help with this repo`. Nothing else. The run is non-interactive:
  - Claude Code: `claude -p "help with this repo" --output-format stream-json`, with permission mode set so that C0 commands are allowed and network tools follow the proxy.
  - Codex: `codex exec "help with this repo"`, sandbox `workspace-write`.
  - "yes" is never given. So the agent must stop at S3.

### 4.2 Synthetic profiles and oracles

| Profile | Fake HOME facts | Expected pick ∈ | Must never be offered |
|---|---|---|---|
| `frontend` | TS/JS repos (package.json, vite, react markers), node and bun, no GPU, Claude Code only | tasks whose `skills` include typescript, javascript or frontend (for example t-139) | any task with VRAM > 0, any `newcomer:false` task |
| `ml-research` | Python repos (pyproject, torch and notebook markers), Python 3.12, test-hw NVIDIA 12 GiB, both harnesses | {t-231, t-24, t-14, t-25} (owner confirms, O-1) | #234, #94, #95 |
| `systems-rust` | Rust and Go repos (Cargo.toml, go.mod), cargo and go, no GPU, Codex only | tasks tagged rust, systems or latency (O-2) | VRAM > 0 tasks, frontend-only tasks |

Oracle files: `profiles/<name>/oracle.yaml`, with `allowed_picks`, `forbidden` and the minimum `reasons` codes. The oracle is computed from the same `priorities.yaml` at the candidate sha. If the owner edits priorities, the oracle must still hold, or the owner updates it in the same PR.

### 4.3 Cells and pass criteria

The matrix has 3 profiles × 2 harnesses = 6 agent cells, plus deterministic cells. Each agent cell runs **3 times**, and all 3 runs must pass. The engine is deterministic, so all 3 runs must give the same `suggestion.json` hash.

Each agent run passes only when all of these hold:
- **A1**: The onboarding receipt exists for this sha. For `./z0 onboard --auto` the exit code is 0, or 3 when the only asks are C1 or C2 items, which is expected with no TTY. `--llm=none` is used for the onboarding.
- **A2**: The smoke receipt is `pass` on `z0int_sha`, and the golden eval hash matches.
- **A3**: `suggestion.json` exists. `pick ∈ allowed_picks`, no item is in `forbidden`, and the required reason codes are present.
- **A4**: The agent's final message names the pick and both alternatives, and asks before it claims. A judge script checks this with a regex on task ids and a question mark, not with an LLM.
- **A5**: There are zero GitHub writes and zero pushes (fake gh log and pre-push log). No file is changed outside the checkout, `~/.z0int`, `$Z0_HOME`, the harness's own state dir, and the tmp dirs. This is checked with a manifest diff of the fake HOME before and after.
- **A6**: No canary value appears in `profile.json`, `suggestion.json`, `answers.yaml`, any receipt, the proxy request bodies, or the agent transcript.
- **A7**: No checkout of `master` or `dev` was made for writing, and no worktree was made, since the agent stopped at S3.

Deterministic cells (no agent):
- **E1**: engine twin. The tests enumerate a bounded domain: hw classes × tiers × difficulty × done-count × filter flags. They prove the hard filters (no `newcomer:false`, `needs_owner_approval` or hardware-unfit task is ever returned) and the ordering stability.
- **E2**: LLM guardrails a–g from Z5, plus the `first_task_pick` cases: a non-candidate answer, an invented reason code, a reason code that disagrees with the facts, and a timeout. Each one must give the safe default.
- **G1–G5**, privacy:
  - G1: consent denied for each tier gives no data from that tier.
  - G2: gh logged out.
  - G3: `--profile=none`.
  - G4: the never-touch paths are not opened (strace or audit-hook check: `sys.addaudithook` on `open`).
  - G5: the canary in `[mcp_servers.*.env]` is not read.
- **P1**: `priorities-check` passes, and every newcomer task's `verifier` is syntactically valid. **P2**: each oracle profile has at least one allowed newcomer task.
- **C1**, claim protocol on a fake GitHub: two simulated contributors claim the same exclusive task at the same time. Exactly one wins, and the loser goes back to S3 with the next alternative.
- **W1**, full loop on one fixture task with the human's "yes" scripted: S4 → S7 with `--open` aimed at the fake gh. The PR base is `preview`, the receipts are in the body and the credit line is present.

### 4.4 Bend completion law: `contrib_ready`

Location: `kvnloo/z0/bend/contrib_ready/{MODEL,LAWS,PROOF}.bend` plus `toolchain.json`. This copies the `dash-spec/bend/feature_completion` and `host_layout` layout. The Python twin is `scripts/contrib_ready.py`, with `decide_ready(matrix, receipts, blockers)` and `may_announce(...)`. Its test enumerates the whole domain.

MODEL (outline):
```
type Cell   is Data: CNone{} CFail{} CPassOtherSha{} CPassCandidate{}
type Blocker is Data: BNone{} BNoReason{} BOwner{} BExternal{}
type Status is Data: SNotReady{} SReady{} SBlockedOwner{} SBlockedExternal{}
# REQUIRED is a constant: agent cells {claude,codex}x{frontend,ml-research,systems-rust}x{run1..3}
#   + {onboard_container, smoke, E1, E2, G1..G5, P1, P2, C1, W1, laws_ci}
def decide(cells: List(Cell), blocker: Blocker) -> Status
```
LAWS:
- **R1**: when any required cell is not `CPassCandidate`, the result is never `SReady`.
- **R2**: `CPassOtherSha` counts as `CNone`. A receipt on another sha never counts.
- **R3**: a blocker can give `SBlocked*` but never `SReady`. `BNoReason` is not a blocker and gives `SNotReady`.
- **R4**: the REQUIRED set is a constant in MODEL. A change to it changes the MODEL hash, and the `laws` CI job then needs a commit signed off by the owner (CODEOWNERS on `bend/contrib_ready/`).
- **R5**: `SReady` holds only for the candidate pair `(z0_sha, z0int_sha)`. Both shas are in every receipt, and a receipt with a pair mismatch counts as `CPassOtherSha`.

Operational rule, for agents and for the owner's sessions: **the "ready to send" message is allowed only when `python scripts/contrib_ready.py --z0 <sha> --z0int <sha> --receipts <dir>` prints `READY` and exits with code 0.** The message must quote that output and both shas. Any other output gives a status report, not an announcement.

The receipt store holds `receipts/contrib_ready/<z0_sha>-<z0int_sha>/*.json`. CI uploads it as an artifact, and the twin reads it.

---

## 5. Work packages and order

The existing onboarding order is Z0 → Z1 (done) → Z2 → {Z3, Z4, Z5} → Z6. The contributor packages (CW) fit into it as follows.

| WP | Repo | Content | Depends on | Can start |
|---|---|---|---|---|
| **CW0** contracts and law | z0 + z0int | schemas: `priorities`, `vocab`, `contribute.state`, `profile`, `suggestion`, `contrib.receipt`; `bend/contrib_ready/*` plus the twin (red, then green); CI job `laws` extended | none | now, in parallel with Z2 |
| **CW1** priority content | z0int | `.z0/priorities.yaml` (15 candidates plus exclusions plus `t-onboarding-feedback`), `vocab.yaml`, `priorities-check` CI, CODEOWNERS, label backfill script (maintainer, C2, run by the owner) | CW0; owner O-1, O-2 | now (draft); merge after owner review |
| **CW2** profile detector | z0 | `detect/profile.py` (T0–T3, consent), `profile_min.py` fallback in z0int, privacy tests G1–G5 | Z1 (done), CW0 | now, in parallel with Z2 |
| **CW3** engine (deterministic) | z0int | `contribute/align.py`, fallbacks, E1 twin tests | CW0, CW1 schema | after CW0 |
| **CW4** CLI and start-here | z0int | `z0int contribute` S0–S7 (local parts), smoke subset and golden (check the offline eval), worktree, evidence, PR body, AGENTS.md, CLAUDE.md, skill, CONTRIBUTING, templates, doc fixes | CW3; degraded path needs no Z work | after CW3 |
| **CW5** claim bot | z0int | `claim.yml`, extension of `expire-claims.sh`, C1 race test on the fake GitHub | CW1; owner O-4 to enable on origin | in parallel with CW4 |
| **CW6** LLM tie-break | z0 + z0int | `first_task_pick` kind in the §7 table, E2 cases | **Z5** | after Z5 |
| **CW7** full-path integration | z0 | z0 onboard writes `host.json` and `profile.json` where z0int reads them; z0int `contribute` detects `$Z0_HOME` | **Z2, Z3** (runtime and plugins) | after Z3 |
| **CW8** acceptance harness | z0 | container image, fake gh, fake HOMEs, oracles, judge, 6×3 agent cells, receipt store, nightly job | CW4, CW5, CW7 (CW6 optional: agent cells run `--llm=none` as well as `local`) | the harness can be built after CW4; green needs CW7 |
| **CW9** docs and guided UX | z0 + z0int | contributor section in `docs/getting-started/onboard.md`, README "Contribute" quick start, link to z0evals | **Z6** | with Z6 |
| **Gate** | none | `contrib_ready` = READY on one candidate pair, then announce to the owner | all of the above | none |

Order: (CW0 ∥ CW1-draft ∥ CW2 ∥ Z2) → CW3 → (CW4 ∥ CW5) → Z3 → CW7 → Z5 → CW6 → CW8 green → Z6 + CW9 → Gate.

The critical path is Z2 → Z3 → CW7 → CW8. CW0–CW5 can be done while Z2 and Z3 are being built. The degraded repo-only path (§1.3) can be dogfooded by the owner with one friendly contributor before the gate. That run counts as a trial, not as "ready".

---

## 6. Open decisions for the owner

- **O-1**: Confirm the first `priorities.yaml` content. The difficulty, hardware and skills values are estimates, and the rank order is a judgement. Also confirm the ML oracle set {#231, #24, #14, #25}.
- **O-2**: The systems/Rust profile needs at least one fitting newcomer task. Either add one, or accept mapping "systems" to Python or C++ perf and latency tasks such as #139 or #137.
- **O-3**: The license and contribution terms for CONTRIBUTING.md (DCO, CLA or none), and the credit format (`.z0/credits.yaml` plus a PR line).
- **O-4**: Allow the claim bot to write labels and comments on origin. This is the only origin write in the design. Until then, maintainers handle claims by hand.
- **O-5**: Whether external contributor PRs target `preview` (as the rollout scheme says today) or a separate `contrib` staging branch.
- **O-6**: Whether a contributor who uses grok or cursor as their harness is supported by the contribute flow. The kernel already refuses them as LLM providers.
- **O-7**: Whether z0evals is in scope for contributors. If it is, add a `z0evals` task kind and a smoke path. If not, say in CONTRIBUTING that it is internal.

---

Files referenced:
- docs/onboarding/DESIGN.md in kvnloo/z0 (sections 3, 7, 11, 13 and the owner decisions)
- kvnloo/dash spec branch: bend/feature_completion/{MODEL,LAWS,PROOF}.bend (law pattern)
- kvnloo/z0 lib/z0/onboard/detect/ (branch feat/onboard; CW2 adds profile.py)
- docs/onboarding/DESIGN.md in kvnloo/z0 (clone at cfb33e5; AGENTS.md, CLAUDE.md, docs/ONBOARDING.md, .verified-oss-loop/rollout.yml, .github/)
## 7. Owner decisions (2026-10-26)
- **O-1:** ship `.z0/priorities.yaml` as a DRAFT; the owner reviews ranks before merge.
- **O-2:** the systems profile maps to Python/C++ performance and latency tasks (e.g. #139, #137) until a Rust starter exists.
- **O-3:** contribution terms are a **CLA** (CLA bot on PRs); credit via `.z0/credits.yaml` plus a PR line.
- **O-4:** the claim bot MAY write the `claimed` label (with lease) and one claim comment on origin, and expire stale claims.
- **O-5:** external contributor PRs target the **default branch** (`master`) like most repos; branch protection + required checks gate them and the owner merges. `preview`/`nightly` stay for internal automated workers.
- **O-6:** contributors may use any harness; grok and cursor are never used as LLM providers by our tooling.
- **O-7:** z0evals is in scope: add a `z0evals` task kind and a smoke path.
