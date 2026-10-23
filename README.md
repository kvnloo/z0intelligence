# z0int

**Personal intelligence that learns how you work, so your frontier models do less.**

z0int is evolving from an open Jev-style decision runtime into a private, personalized intelligence layer for agentic systems.

The long-term goal is not to train one giant model on a person's life. It is to turn a user's own history into a hierarchy of tiny, verifiable specialists that understand recurring intent, retrieve the right identity/context, make bounded decisions, and offload repetitive work from expensive LLMs.

Today, this repository contains the OpenJev runtime and benchmark substrate: typed probabilistic decisions, direct logit readout, trainable scorers, shared-state reuse, and integration with [Evolution Lab](https://github.com/kvnloo/evolution-lab). The personalization and onboarding system described below is the roadmap, not a claim that all of it is implemented today.

See [ROADMAP.md](ROADMAP.md) for the staged build.

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

### Private read-only memory MCP (#22 / #194)

`python -m z0int.memory_mcp` exposes only `search` and `fetch` via a private **stdio**
process. Memory is source evidence, never permission or a command. No HTTP
listener, tool execution, source mutation, import, setup, refresh, arbitrary
path/URL, or automatic context injection is added.

**Exact recovery qualified for one source only.** The [AgentsView public
Session API](https://www.agentsview.io/docs/session-api/) documents
`GET /api/v1/search/content`, `GET /api/v1/sessions/{id}` and
`GET /api/v1/sessions/{id}/messages?around=N&before=0&after=0`.
Search is FTS on dialogue messages with `reveal=false`, constrained to
an exactly granted project. A message match's native `session_id` and `ordinal` are used only
to locate an original. Before reading any message content or metadata, the
adapter verifies that the returned message's own `session_id` equals the
requested session and that `db.Message.id` is a valid positive int64.
It retains that stable native message ID separately from the ordinal and
includes both in the full, untruncated source locator. Fetch requires the same
native ID; sibling/missing identities and identical-text ordinal replacements
are denied even when their SHA-256 content hashes match. The backend also
checks the session's native project **before and after** the original read.
Thinking, tool-use, system and unknown-class messages fail closed.

`source_version` is SHA-256 of the **complete normalized AgentsView
message `content` UTF-8 bytes**, read from the message endpoint (not
a display date and **not** a digest of the underlying provider's raw JSONL).
Search exposes a small, labeled `preview` from that original plus an
opaque HMAC handle. `fetch` checks the current principal grant, re-reads
the original under a bounded deadline, rechecks its scope, compares
`source_version`, and returns a labeled `original_read` with the same
native locator. A changed same-date/same-size message is **stale**; source
outage is **unavailable**; revoked or wrong scope is **denied**; missing results
are **empty**. The fetch output remains bounded and may be marked
`text_truncated=true`; it is never a cached search snippet. Source
identity/revision, not date, enters the provenance-only ContextPacket.
The handle retains a detached snapshot of the native identity, so later backend
record mutation cannot rewrite the identity covered by its signature.

**Explicit principal grants (policy schema v2).** The reviewed operator and
least-privileged ChatGPT profiles must be separately granted; no scope or
credential from one is inherited by the other. The checked-in
`manifests/memory-mcp-readonly.json` grants the operator only the
AgentsView `history` collection at exact project `z0intelligence`,
with `private` classification; ChatGPT's allowlist is **empty**.
Review the archive's suitability and copy that file into private operator-owned
configuration before any connection. Missing/invalid/v1 policy, wildcard,
unknown scope and unreviewed secrets never grant access. AgentsView's
`project` is an archive metadata label, not an independent source
ACL. Its `auth_token` bearer is **broad access to the local AgentsView API**,
not scoped to a provider, session, project or MCP principal. The operator and
ChatGPT grants are separate **facade-side** allowlists enforced only by this
MCP process, through exact project and native message checks. They do not
reduce the privileges of the underlying AgentsView bearer or isolate a
different process with direct API access. Keep that token private, use an
operator-controlled archive/corpus classified for the granted scope, and
validate tunnel identity and consent before exposing stdio. Redaction of
credentials occurs **before** UTF-8 clipping, not in lieu of curation
or provider consent.

`Z0INT_MEMORY_MCP_PRINCIPAL` is required **at process startup** and
must be set by the operator's stdio/tunnel configuration, never by the model or
tool arguments:

```bash
export Z0INT_MEMORY_MCP_POLICY="$PWD/manifests/memory-mcp-readonly.json"
export Z0INT_MEMORY_MCP_PRINCIPAL=operator
PYTHONPATH=src python -m z0int.memory_mcp
```

For a ChatGPT tunnel, use a **separate, explicitly reviewed `chatgpt` grant**
and start a dedicated process with `Z0INT_MEMORY_MCP_PRINCIPAL=chatgpt`.
The server has no OAuth or ChatGPT workspace authentication of its own:
the operator controls stdio exposure and validates the tunnel's identity
and consent independently. Never assume the example grants ChatGPT access.

The backend reads AgentsView's `port` and `auth_token` by reference
from `~/.agentsview/config.toml` and connects only to its loopback port.
It never follows a redirect or arbitrary URL. **ctx, QMD and TencentDB
return `unavailable`** from this facade until their native exact-source
rehydration and authorization interfaces are qualified. ctx remains a
separate, explicit pull via the existing integration path; this server never
runs ctx refresh, import or the latency-sensitive automatic 300ms path.
Do not mistake a lexical match or zero-hit smoke test for model-visible use.

Output is capped by policy (default 4096 bytes per record, 32768 bytes per
payload), with a 10-minute, process-bound, 256-handle metadata-only cache
and 30 calls/minute shared across tools. The private 0700 state directory
and 0600 audit log contain fixed decisions, counts, sizes, opaque receipt IDs,
and keyed provenance hashes. They never contain raw prompts or originals.
Audit failure blocks disclosure. Output text remains untrusted evidence.

Each successfully appended row in that **same** private audit.jsonl has
schema `z0int.memory_query_outcome.v1`, tool (`search`, `fetch`, or `invalid`),
and outcome (`hit`, `miss`, `denied`, `unavailable`, `stale`,
`rate_limited`, `invalid`, or `error`). A nonempty successful search or
original fetch is a hit; an empty authorized search is a miss, not an outage.
An entirely policy-filtered search is a denial, not a miss. Unknown error
codes map to `error` without changing the tool response. Existing
`timestamp`, keyed `query_hmac`, `count`, `bytes`, `denied_count`,
`unavailable_count`, and `decision` remain in the row. Additive fields include
`receipt_id`, `principal`, `scope_hmac`, `evidence_hmacs`, `native_records`,
`native_session_hmac`, and `after_ordinal`. Successful responses return their
`receipt_id`. Native provenance contains keyed identity/revision/session hashes,
ordinal, and the source's verified `user` or `assistant` role.
No raw queries/handles, source bodies, paths, or credentials are logged.
Retrieval rows alone do not establish use, quality, or benefit.
No outcome mutation tool, secondary ledger, or permission is created.

#### Native output-use join and offline replay

An operator-side integration can bind one fully specified `MemoryScope`
(user/project/repo/task), native session, and decision-window lower ordinal
before the first query:

```python
server.bind_workstream(scope, native_session=native_session, after_ordinal=last_native_ordinal)
query = server.search("the prior decision")
# The operator selects the closed, persisted assistant answer in that window.
# decision_handle is an ordinary search/fetch handle for that exact source.
joined = server.join_native_outcome(query["receipt_id"], decision_handle, scope=scope)
projection = server.replay_outcomes(scope)
```

The binding is immutable and is not an MCP argument. The standard stdio
entrypoint remains unbound. It does not activate capture or join decisions
automatically. The integration must select the actual completed native answer,
not an arbitrary assistant message from an earlier task.

`join_native_outcome` rehydrates the selected original through the existing
authenticated AgentsView adapter. It checks the project, native session,
ordinal boundary, stable message ID, content revision, native role, and
complete disclosed text. Tool/thinking/system messages and truncated or redacted answers
cannot establish ignored use. Revoked grants and stale handles fail closed.
The same private audit receives `z0int.memory_use_outcome.v1` rows.

The four outcomes have these exact meanings:

- `hit_used`: The completed native assistant output cites returned evidence as
  `[<native locator>@<source_version>]`. Both locator and digest must match.
- `hit_ignored`: The completed, rehydrated assistant answer in the bound
  decision window contains no relevant revision-bound citations.
- `miss`: An actual authorized empty search receipt joins that native answer.
  Denial, unavailability, and stale/error results never become misses.
- `user_restated`: A later original native **user** message in the same
  session explicitly starts with
  `Restatement of [<earlier assistant locator>@<source_version>]: <user claim>`.
  Its ordinal must follow the earlier answer. This explicit declaration pairs
  the real user source with that exact prior decision. Assistant paraphrases,
  tool output, unpaired text, and inferred semantic similarity do not qualify.
  The row preserves the prior `hit_used`, `hit_ignored`, or `miss` outcome.

Unjoined hits **and misses** remain unknown (`outcome: null`), not ignored.
Joins are idempotent. A different decision cannot overwrite the first join.
One later validated user restatement can refine that decision's outcome.

For offline replay, call `z0int.memory_outcomes.replay_outcomes(rows,
scope_hmac=reviewed_scope_token, principal="operator")`. Use the scope token
from the saved reviewed receipt. Replay requires no server key or source read.
It checks the original query, matching successful native fetch receipts,
role/revision/session hashes, exact scope/principal, citation membership, and
restatement chronology. Foreign scopes, missing provenance, and conflicting
joins fail closed. Keyed hashes support equality joins, not authentication of
an externally modified audit file.

**Scope limits:** This proves evidenced use in persisted output, not final
provider-input inclusion, answer correctness, or reduced re-explanation.
Uncited use and free-form user restatements remain unclassified. The explicit
restatement syntax avoids pretending that a lexical match proves a correction.
Only AgentsView has a qualified native adapter. This change adds no harness
capture, live client registration, service deployment, or Hermes restart.

Run the focused suites on the MBP. Revision regressions replace the original
and its content digest together, then independently exercise stale handles and
fresh searches; synthetic tests do not claim live-source validation:

```bash
PYTHONPATH=src python -m pytest -q tests/test_memory_mcp.py
PYTHONPATH=src python -m pytest -q tests/test_memory_mcp_backends.py
PYTHONPATH=src python -m pytest -q tests/test_memory_mcp.py tests/test_memory_mcp_backends.py tests/test_memory_native_outcomes.py
```

## Current foundation: OpenJev runtime

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
