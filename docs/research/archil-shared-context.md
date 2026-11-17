# Archil's 128-agent shared-file-system demo, and what z0intelligence should take from it

Status: research note, 2026-10-21. Tracking issue: [#185](https://github.com/kvnloo/z0intelligence/issues/185). Nothing here is implemented. One small
measurement (fleet-build per-call overhead, section 7) was run for this note;
everything else is reading and video observation.

Each claim carries an evidence tier, as in the reverse-engineering method:
**seen** (observed directly: a video frame, a doc line, an SDK source line, a
command we ran), **inferred** (follows from seen facts; the basis is given),
**unknown** (not found; that does not mean it is absent).

## TL;DR

- **What Archil is.** A closed-source, hosted POSIX file system on AWS, built as a
  write-ahead log with S3 tiering. It has strong consistency for connected clients,
  plus checkpoints, branches, Firecracker sandboxes and "serverless execution"
  (`disk.exec`: a short-lived microVM with the disk already mounted). The SDKs are
  MIT-licensed; the service is not open source. (seen: docs, pricing, npm/PyPI
  metadata)
- **What "leasing" means there.** NFSv4-style **delegations**: exclusive,
  recursive, per-path write locks held by one client. In shared mode they are
  granted automatically when a client creates a file or opens one for write, and
  released on close. Orphaned delegations are revoked by API or with `--force`.
  "Conditional" mode skips delegations and executes writes on the server. (seen:
  docs, SDK `Delegation` dataclass)
- **What the demo was.** 128 agents ran as "128 Pi contexts", in a phased run
  (brief, setup, retry, build, integration and QA, final integration) over 81:22
  of active time. They produced 266 agent-created tasks; 87 agents wrote files;
  10,621 model tool calls; a playable "Factory Builder" game. At **$0.05 per
  1,000 tool calls**, 10,621 calls cost **$0.53**, which is the "~$0.50" in the
  post. That figure is the tool-execution bill. It excludes LLM inference, which
  Archil's pricing page also excludes. (seen: video frames; inferred: the
  arithmetic)
- **How it relates to C2C.** Both remove the cost of agents re-deriving context
  that another agent already has, but at different layers. Archil shares the
  **world state** (files) between many agents, using locks and strong consistency.
  C2C ([arXiv 2510.03215](https://arxiv.org/abs/2510.03215), ICLR'26, code
  [thu-nics/C2C](https://github.com/thu-nics/C2C)) shares a model's **internal
  state** (KV cache) between two models through a trained projector. Archil is a
  coordination primitive; C2C is a communication primitive. Neither substitutes
  for the other.
- **What to do.** Three experiments, smallest first: (E1) path-scoped write leases
  over our existing disks, (E2) per-tool-call placement on fleet-build, (E3)
  shared-prefix KV reuse for repeated SLM calls, with C2C as a gated follow-on.
  Do **not** adopt Archil as a dependency for local workers.

## 1. Sources

| source | what | retrieval |
| --- | --- | --- |
| [x.com/jhleath/status/2112616627649077251](https://x.com/jhleath/status/2112616627649077251) | the post, 2026-10-20 18:47 UTC; 36 s 1080² video, no audio | agent-reach / twitter-cli; video sha256 `79d2c3a8…dc3a` |
| [docs.archil.com/guides/agents/codex](https://docs.archil.com/guides/agents/codex) | linked in a reply (Tyler Angert) asking how the agents ran | web extract |
| [archil-agent-city…chatgpt.site](https://archil-agent-city.archil-7626.chatgpt.site/) | replay site for the sister demo (700 sandboxes build a city, 2026-10-18) | Jina reader |
| Hunter Leath's X Articles: "What comes after sandboxes" (2026-10-14), "What is a sandbox company" (2026-10-13), "Beating NVMe by extending POSIX" (2026-10-16) | rationale for serverless tool calling | twitter-cli `article` |
| docs: [sharing-disks](https://docs.archil.com/concepts/sharing-disks), [consistency](https://docs.archil.com/details/consistency), [serverless-execution](https://docs.archil.com/compute/serverless-execution), [troubleshooting / stale locks](https://docs.archil.com/guides/platforms/troubleshooting), [branches](https://docs.archil.com/concepts/branches-and-checkpoints), [pricing](https://archil.com/pricing) | product semantics | `llms.txt` index → `.md` pages |
| blog: [file-system transactions for agents](https://archil.com/post/building-file-system-transactions-for-agents), [serverless execution](https://archil.com/post/serverless-execution), [the last WAL on S3](https://archil.com/post/building-the-last-wal-on-s3) | architecture | Jina reader |
| PyPI `archil==0.18.0` wheel; npm `disk@1.9.0`, `@archildata/*` (MIT, [archil-data/archil-sdk](https://github.com/archil-data/archil-sdk)) | API surface | `pip download`, `npm view` |
| [desplega-ai/agent-swarm research note](https://github.com/desplega-ai/agent-swarm/blob/main/thoughts/taras/research/2026-03-11-archil-shared-disk-write-strategies.md) | independent field report on delegation pitfalls | `gh api` |

No public repository, trajectory dump or model name for the Factorio run was
found (**unknown**). Hunter replied "what part do you want to see open source"
under the city demo; no release has followed.

## 2. What Archil is (the stack, by layer)

| layer | what | tier |
| --- | --- | --- |
| storage core | a durable write-ahead log with POSIX semantics. "Inodes, directory entries" are virtualised over a compacted view; S3 is a tier. Multi-AZ replication by default, configurable write quorum. | seen (WAL post) |
| client | a FUSE client ("soon kernel-mode"); an S3-compatible API, strongly consistent with the FS; a log/append API | seen |
| consistency | strong read-after-write after `fsync`, atomic rename; client caches can lag "seconds" (`archil invalidate-cache`); eventual consistency to a backing bucket | seen (consistency doc) |
| concurrency | delegations (section 3) | seen |
| versioning | copy-on-write checkpoints and branches; "merge" named as the unsolved next step | seen (transactions post) |
| compute | **serverless execution**: `disk.exec(cmd)` → a container/microVM with the disk at `/mnt/archil`, run to completion, return stdout, stderr, exit code and timing. Also persistent Firecracker sandboxes with fork/pause; a fan-out `grep` API. | seen (docs, SDK) |
| integrations | AI SDK, Mastra, LangChain, eve; Daytona/E2B/Superserve mounts; Kubernetes CSI driver; Terraform | seen |
| pricing | Free; Developer $10/mo minimum; Team $500/mo. Tool calls **$0.05 / 1,000** with execution included; sandbox CPU $0.055/vCPU-h; storage $0.20/GiB-mo; egress $0.07/GB. AWS regions only (us-east-1, us-west-2, eu-west-1 for sandboxes). Inference not included. | seen (pricing page, 2026-10-21). In April 2026, serverless exec was priced at $0.18/h active, billed per ms with a 100 ms minimum; that has since changed. |
| openness | the service is closed; the SDKs are MIT. Public repos: `archil-sdk`, `terraform-provider-archil`, an `xfstests-dev` fork | seen |

The pitch, in Hunter's words: "agents are all about data"; the harness should
run multi-tenant outside the sandbox, and "tools are like calling eval" and need
per-call isolation; sandboxes held for a whole session waste "100x to 1000x" the
CPU agents need. His article even imagines a classifier (his name: "tool-jev")
that picks microVM vs. just-bash and sizes the resources for each call. That is
the role our Jev already plays for decisions (section 6).

## 3. Leasing = delegations

Semantics, from the sharing-disks doc and the SDK (**seen**):

- **Exclusive mount** (`archil mount`): one client holds a delegation on `/`; no
  other client can mount.
- **Shared mount** (`--shared`, the default for serverless exec): the disk is
  read-only until the client holds a delegation. `archil checkout <path>` takes an
  exclusive, **recursive** write lock on a subtree. An ancestor of an owned path
  cannot be owned by anyone else. Automatic delegations are granted when a client
  creates a file in an undelegated directory, **opens a file for write (released
  on close)**, or creates a directory. Deleting needs a delegation on the parent,
  so "create works, remove fails with `EROFS`" is a known pitfall.
- **Conditional mount**: no delegations; writes go to the server like NFS, and any
  path not locked by another client is writable. Archil plans to make this the
  default.
- **Release**: `archil checkin`, unmount, or crash. A crashed holder's
  delegations become **orphaned** within about a minute;
  `disk.list_delegations()` / `revoke_delegation()` frees them. `--force` also
  steals live ones, and the evicted client's un-fsynced writes are lost.
- SDK model: `Delegation{client_id, inode_id, path, is_pending, is_orphaned}`;
  exclusive sandbox mounts take a `queue_ms` bound on waiting for the root
  delegation.

The post's "archil implements leasing internally" fits the automatic
open-for-write delegation, or conditional mode. **Which mode the Factorio run used
is unknown.** Delegations are per-path mutual exclusion, not merging: two agents
cannot both edit `main.ts` at once, but a second writer waits or fails instead of
silently overwriting.

Independent field report (**seen**, desplega-ai/agent-swarm, 2026-03): with 3
workers and a lead on a `--shared` disk, `mkdir -p shared/plans` at boot made the
first-booting container own the whole directory; every other agent got `EPERM`,
and `|| true` hid the error. Their fix was per-agent subtrees plus a database for
the genuinely shared state (memory, learnings). Lesson: path leases need a
deliberate layout, with per-agent write areas and explicit hand-off points.
Ambient sharing does not work.

## 4. The Factorio demo (video observation)

The dashboard is labelled "RECORDED EXPERIMENT · ACCELERATED". Panels:
Components ("from recorded file writes"), Final game ("unmodified"),
Agent-created assets, Individual agent status ("128 Pi contexts"; states
Thinking / Tools / Writing / Testing / Art / Waiting), Concurrent vCPU ("est.",
"Allocated · queue excluded"), and three counters.

| video t | phase label | active time | tasks | agents wrote | tool calls | notes |
| --- | --- | --- | --- | --- | --- | --- |
| 0 s | One shared brief | 00:00 | 0 | 0 | 0 | 12 component cards, all "Awaiting file writes" |
| 2 s | Setup | 01:57 | 0 | 0 | 717 | |
| 4 s | Retry | 04:03 | 0 | 4 | 1,072 | Entry point and Game state cards in orange "Write contention" |
| 8–14 s | Agents create and build the work | 07:40 → 13:23 | 140 → 156 | 4 → 67 | 1,912 → 5,193 | at 09:24: Tests 3 files · 3 agents; 6 of 12 cards in contention |
| 18 s | Integration + QA | 34:12 | 243 | 86 | 9,289 | |
| 22–28 s | Final integration | 77:49 | 266 | 87 | 10,587 | Tests **77 files · 63 agents**; Art + sprites 20 files · 19 agents; Renderer 4 · 7 |
| 30–36 s | Their game. Working. | 81:22 | 266 | 87 | **10,621** | "Playable. Built by the agents." "Gameplay verified"; game "Factory Builder": Extractor / Processor / Assembler / Conveyor / Rotate / Remove |

The vCPU readout, sampled at 2 fps, peaks at **75** on screen (series: 30, 57,
28, 31, 33, 35, 24, 32, 25, 75, …; mostly 4–7 between bursts). The post claims a
peak of "over 100 cores". Short spikes between samples may account for the
difference; **not verified**.

What the frames say about the protocol (**seen**): the 12 components were fixed
by the brief. Agents created tasks themselves (266). Fewer than 70% of agents
ever wrote a file (87/128). Write contention was visible and transient. Testing
dominated the end: 63 agents touched 77 test files. An explicit retry phase came
early.

Harness (**inferred**): "Pi contexts" points to the Pi coding-agent harness,
the same lineage as our oh-my-pi/OMP. On 2026-10-13 Hunter ran "1,000 agent loops
on an individual server (by multi-tenanting the code)", sending every tool call to
`disk.exec`: about 180 CPUs at peak versus about 2,000 for agent-in-sandbox. The
Factorio run is that pattern at 128 agents: many Pi sessions in one process, bash
tools on serverless microVMs, one shared disk. The model is **unknown**. In a
reply, Hunter says cost "depends on the model; glm might be less than a dollar".

## 5. Reconstructed coordination protocol

| step | mechanism | tier |
| --- | --- | --- |
| brief | one shared brief file on the disk; 12 component areas are the coarse layout | seen (phase label, cards) / inferred (file) |
| decompose | agents write task files; "agent-created tasks" is counted "from recorded file writes" | seen (counter) / inferred (task = file) |
| claim | create a claim file atomically (`O_EXCL` create or rename) and fsync. Strong read-after-write plus atomic rename make it a test-and-set, and the automatic delegation on create keeps others from rewriting it | inferred from the consistency and delegation docs; the actual claim format is unknown |
| edit | writes take per-file delegations (on open-for-write, released on close); a concurrent writer waits or fails rather than overwriting. The "Write contention" highlights are those collisions. | inferred |
| test | agents run the game's tests via `disk.exec` against the same tree, so they see each other's committed writes without sync or merge | seen (Testing state, 77 test files) / inferred (via exec) |
| conflicts | prevented per file by locks, not resolved by merges. Where an agent lost a race, it retried ("Retry" phase, early) | seen (phase) / inferred |
| integration | a final integration phase with an explicit "Gameplay verified" check | seen; who verified (agent or human) is unknown |

What the demo does not show: how stale claims were reclaimed, whether the final
code was reviewed, wasted or duplicated work, total token cost, how many tasks
were abandoned, and any regression signal beyond "gameplay verified". The
replies question the realism ("128 agents and not one of them fighting over the
conveyor belt? fake"; "That costs 1.50 an hour on ec2"). Treat the demo as a
throughput and cost illustration, not as evidence of output quality.

## 6. Relation to Cache-to-Cache and our earlier discussion

Our earlier C2C threads (no z0intelligence issue mentions C2C; a search across
`kvnloo` issues and PRs found these):

- **[frontier-kb#20](https://github.com/kvnloo/frontier-kb/issues/20)** (Agent OS
  evidence map): C2C is listed under Communication / representation as "direct
  KV-cache semantic communication", "a boundary case… challenges the assumption
  that natural-language text must be the universal machine-to-machine
  representation" (hypothesis H3, "text is overused as systems state").
- **[aodl#22](https://github.com/kvnloo/aodl/issues/22)**: the same framing in the
  Agent OS reference model.
- **[cua#107](https://github.com/kvnloo/cua/issues/107)** (live state mirror vs
  scoped reads): "Model-computation reuse" is a separate lane, and "the
  Cache-to-Cache analogy does not establish DOM coherence, eliminate model input
  processing, or supply a CUA speedup."

The two layers compared:

| | Archil | C2C |
| --- | --- | --- |
| what is shared | world state: files, a POSIX tree | model state: the source model's KV cache, projected into the receiver's |
| between | N agents (any harness, any model) | 2+ specific models with a trained fuser per pair |
| consistency | strong, with locks (delegations) | none needed: a one-shot transfer per query |
| saves | re-reading, syncing, merging; idle CPU (per-call compute) | generating intermediate text; re-prefilling; 2.5x latency and 3–5% accuracy over text handoff (paper) |
| ops cost | a hosted service, egress, AWS | training a projector per model pair; a transformers-level serving path (no llama.cpp/GGUF support) |
| our analogue | worktrees + W1 lease law + GitHub claims; fleet-build | llama.cpp/vLLM prefix caching (same model); C2C fuser (cross-model) |

The useful synthesis for z0int: **coordinate through shared, versioned world
state, and reuse computation only within a model family.** Shared files fix
"agents re-derive what another agent already wrote down". KV reuse fixes "the
same model re-reads the same prefix". C2C is only worth it after plain prefix
reuse has paid off, and only for a fixed pair. The checkpoint
[`nics-efc/C2C_Fuser` `qwen3_0.6b+qwen3_4b`](https://huggingface.co/nics-efc/C2C_Fuser/tree/main/qwen3_0.6b+qwen3_4b_Fuser)
fits exactly the two Qwen3 sizes already staged in groot's llama.cpp router
(`qwen3-0.6b-q8`, `qwen3-4b-q4km`), but needs HF-format weights to run.

## 7. Mapping to our stack

| Archil piece | ours today | gap |
| --- | --- | --- |
| one shared disk | `/mnt/zer0models` on groot, with a git worktree per worker (`z0-wt/`, `hermes-wt/`, …); fleet nodes get **copies** via `fleet-build` | isolation is per worktree. Sharing happens through git push/merge, which is slow but gives a reviewable history. |
| delegations (path write leases) | **W1** in the worker-supervision FSM ([#184](https://github.com/kvnloo/z0intelligence/pull/184), building on [#183](https://github.com/kvnloo/z0intelligence/pull/183)): "one owner per worktree or branch; changes hands only through Free or a Reclaim from a dead holder; only the holder writes". Bend-proven (`lease_no_steal`, `lease_release_by_holder`, `write_needs_lease`). Enforcement: **none yet** (replay only). | the replay found **419 writes inside another session's lease in 19 worktrees** (`products/garden_room` 242: parallel subagents in one checkout). The lease is coarse (a whole worktree) and unenforced. |
| task claims | Verified OSS Loop claim comments with `expires_at`, 24 h, expired hourly by `.github/workflows/claim-expiry.yml`; oss-factory CONTROL_PLANE envelope (`queued/claimed/working/blocked/done`), KITCHEN "one owner per item (24 h lease)" | works at the issue level; GitHub search is not atomic ("a race can still happen", PROMOTION_QUEUES.md). There is no file-level claim. |
| orphan reclaim | W2/W3 laws (Interrupted → Resume/Abandon, reap orphans) | same idea as `is_orphaned` + revoke, also unenforced |
| serverless tool call | `fleet-build` (ships tracked files, runs one command under a `systemd-run` scope with `--mem/--cpus`), and the k3s `factory` queue (Kueue flavours per node, mbp first) for untrusted repo work | an agent must choose to call it. The **W4 replay: 1,695 heavy commands ran on groot**; 118 heavy.lock waits, 21.5 h in total. Shipping is per call. |
| per-call start latency | Archil: "about 70 ms" (Hunter's reply; exact context unknown) | **measured here, 2026-10-21**, `fleet-build -- true` from this 33 MB / 741-file repo: mbp **0.62 / 0.66 / 0.71 / 1.56 s**; omarchy-phone `--light` **9.6–10.0 s** (median 9.73); one call **waited 242 s** because mbp was cordoned by fleet health |
| checkpoints/branches | git commits/branches; Archil's "merge is unsolved" is git's strength | none for code; useful for non-git artifacts (renders, datasets) |
| context sharing | `z0int context resolve` (a provenance packet, not authorization); State Packet ([#22](https://github.com/kvnloo/z0intelligence/issues/22)); ctx/QMD retrieval | already "share context as files with provenance", which is Archil's thesis minus the locks |
| model-state reuse | llama.cpp router on groot (`b11270`, `--parallel 2`, `--models-max 2`, Qwen3 0.6B–14B, Nemotron-8B GGUFs); Laya 421M; Jev; vLLM KV FS tier ([z0evals#85](https://github.com/kvnloo/z0evals/issues/85)); placement ([kerdoios#50](https://github.com/kvnloo/kerdoios/issues/50)) | no measurement of prefix reuse across the many shadow/decision calls over the same screen or state context |

## 8. Experiments (smallest first)

### E1: path-scoped write leases over our own disks (about 1 day)

Archil's useful idea is **per-path, recursive, holder-released write leases with
orphan reclaim**, enforced at the write boundary. We can get that without a new
file system.

- **Build**: a `z0int lease` CLI and library. A lease is an `O_EXCL`-created JSON
  file under the repo's **git common dir** (`git rev-parse --git-common-dir`
  `/z0-leases/<sha256(path)>.json`), shared by every worktree of that repo, with
  `{holder_session, pid, host, path, acquired_at, heartbeat_at, expires_at}`.
  Recursive check: refuse if any ancestor or descendant is held, matching
  Archil's ancestry constraint. Release on holder exit. Reclaim only when the
  holder's pid/session is dead or the heartbeat is older than the TTL, which is
  exactly W1's Free/Reclaim. Call it from the existing Hermes `patch` /
  `write_file` path and from `supervise --worktree` at dispatch (as #184's "Next"
  already proposes). There are no POSIX/NFS locks; groot's local disk is the only
  authority, because fleet nodes write to copies.
- **Hypothesis**: on the same replayed workload, path leases reduce W1
  violations from **419 → 0** on governed write routes. The share of writes that
  wait or are refused stays ≤ 5%, and they let N subagents share one checkout
  (the garden_room pattern) with **zero lost edits**. Compared with forcing a
  worktree per subagent, setup time drops by the `git worktree add` + install
  cost.
- **Measure**: `supervise` W1 count before and after. Lost-edit count: a file
  rewritten by B within A's lease window, detected from the tool logs. Refusal and
  wait rate, and p95 wait. Duplicate-work rate: two sessions holding claims on
  the same issue or branch.
- **Kill criterion**: refusals above 15%, or agents bypassing leases through
  shell writes (count the `ungoverned` routes, as #47 requires).
- **Not**: a lease is not an approval or authority (AODL and the owner still
  gate). Lease directories must not be created ad hoc at boot (desplega's
  `mkdir` trap).

### E2: per-tool-call placement instead of whole-agent placement (2–3 days)

Archil's economic idea: keep the harness loop cheap and multi-tenant, and give
CPUs only to tool calls, sized per call. We already have the parts: harnesses on
groot, `fleet-build` with `--mem/--cpus`, and Kueue flavours. What is missing is
**automatic routing at the tool boundary**, plus cheaper shipping.

- **Build**: a Hermes/OMP bash-tool shim. A deterministic classifier routes each
  command: W4's existing heavy patterns, then a size table (pytest/bun/gradle/make
  → fleet, read-only `rg/git status/cat` → local). Laya or Jev handle only the
  ambiguous rows; that is Hunter's "tool-jev", except we already own the
  classifier. Make shipping incremental: keep a per-repo cache on each node and
  send `git diff` + untracked files against a HEAD the node already has, instead
  of the full tree.
- **Hypothesis**: heavy commands on groot outside fleet drop from **97 (since
  the fleet came up) → ≤ 5**, and heavy.lock waits **(median 3.9 min) → 0**.
  Median added latency per routed call stays **≤ 1.5 s on mbp** (today 0.6–0.7 s
  warm for a 33 MB repo; the phone costs about 10 s and should take only
  `--light` batch work). The incremental ship holds the p50 under 1 s for repos
  at 10x this size.
- **Measure**: per-call wall time (ship, queue, run, fetch) for local vs fleet
  on the same commands; groot CPU-seconds and RSS per session; W4 violation
  count; queue time when mbp is cordoned (we saw a 242 s wait). The cost proxy is
  CPU-seconds used / CPU-seconds reserved, which is Archil's 100x–1000x claim
  measured on our fleet.
- **Kill criterion**: routed calls fail more often than local ones (missing
  toolchain or state), or the p95 added latency exceeds 5 s for interactive
  turns.
- **Not**: a second scheduler. Kueue and fleet-build remain the only placement
  authorities (#131: "autoscale typed capabilities, not sessions").

### E3: shared-prefix KV reuse for repeated SLM calls; C2C only if that pays (1 day, then 3–5 days)

- **E3a (1 day)**: groot's llama.cpp router (`--parallel 2`) serves the
  decision/shadow models. Restructure shadow and decision prompts as **[stable
  system + tool schema + screen/state context] + [variable question]**. Send K
  calls over the same context with `cache_prompt: true` and a pinned `id_slot`,
  then repeat with `--cache-reuse 256`. Variant: vLLM `--enable-prefix-caching`
  for the Jev/OpenJev route.
  - **Hypothesis**: for K ≥ 4 calls over a ≥ 2k-token shared context, the median
    TTFT drops ≥ 50% and the prompt tokens evaluated per call (`timings.prompt_n`)
    drop ≥ 80% versus a cold slot. Outputs stay byte-identical at temperature 0.
  - **Measure**: TTFT, `prompt_n` vs cached tokens, output equality, slot
    eviction under `--parallel 2` with two models resident (GPU work on groot is
    allowed under W4's GPU exception).
- **E3b (3–5 days, gated on E3a showing that context reuse matters)**: a C2C
  spike with the published `qwen3_0.6b+qwen3_4b` fuser in HF transformers on the
  3080 Ti. Run it on the 28 local-cognition fixtures
  (`benchmarks/fixtures/local-cognition-v1`) against: 0.6B alone, 4B alone, and a
  4B → 0.6B text handoff.
  - **Hypothesis (the paper's claim, to be checked on our data)**: C2C beats the
    text handoff by ≥ 3 points of accuracy at ≤ 1/2 its latency. If C2C does not
    beat **4B alone** on accuracy per millisecond, stop; at our sizes the
    receiver is the cheap model, and the sharer's prefill is the cost.
  - **Caveat**: production would need a non-GGUF serving path. That cost belongs
    in the verdict.

### Effort and order

| exp | effort | depends on | first-day deliverable |
| --- | --- | --- | --- |
| E1 | ~1 day | #184 supervise CLI | lease CLI + write-hook refusal + W1 replay before/after |
| E2 | 2–3 days | E1's lease (shipped calls must not race local writes) | tool shim + latency table local vs mbp vs phone |
| E3a | ~1 day | nothing | TTFT / `prompt_n` table on the router |
| E3b | 3–5 days | E3a positive | fixture accuracy and latency, four arms |

## 9. What not to do

- **Do not adopt Archil for local workers.** Our state is on groot's disk; Archil
  is AWS-hosted, charges $0.07/GB egress, and would move private repos off the
  machine. Its value proposition is cross-cloud stateless sandboxes, which we do
  not run. A free-tier trial for a single public demo is harmless; production use
  is not.
- **Do not build a distributed file system or FUSE layer**, and do not rely on
  `flock`/POSIX locks over NFS or sshfs. Use one lease authority on groot; fleet
  nodes work on copies and return results through fleet-build fetch or git.
- **Do not replace git with shared mutable trees for code.** Archil itself says
  merging is unsolved. Keep path leases inside a worktree for parallel subagents,
  and keep git as the integration and review boundary (L1–L4 in #183).
- **Do not let a lease, claim or packet grant authority.** Context resolve, State
  Packets and leases are provenance and mutual exclusion, never approval.
- **Do not reproduce the demo's 128-agent scale** before E1 holds at 4–8.
  87/128 agents writing files and 63 agents on tests suggests most of the swarm's
  value came from parallel testing, not parallel authorship.
- **Do not make KV caches an inter-agent message bus** (cua#107 already ruled the
  analogy out for CUA coherence). Do not train C2C fusers before E3a.

## 10. Open questions

- The Factorio run's model, token bill, abandonment and duplicate-work rates
  (not published).
- Shared or conditional mode, and how claims were encoded (not published).
- Whether "Gameplay verified" was an agent check or a human one.
- Whether E1's path leases should also cover the S25 install lock and device
  input (W5/W6) with the same file format.
