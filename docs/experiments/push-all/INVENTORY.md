# Push-all inventory: 2026-10-03

The owner asked: "can u make sure everything relevant is pushed to my forks downstream so that i can see all the experiment data and reason about what to do next at a meta level?"

Scope: every git repo and worktree under the paths named in the task, plus the experiment folders outside git. I checked each branch against a live `git ls-remote` of its kvnloo fork. Local remote-tracking refs were stale in several repos and over-reported unpushed work, so I did not rely on them. Pushes went only to kvnloo/* repos, as new branches, with `--no-follow-tags`. I did not force-push, open any PR or issue, write any comment, or touch any upstream remote.

## 1. Where to look (start here)

| What | Fork / branch | Path |
|---|---|---|
| z0 wiring spec, evidence, PR bodies, ACTIVATE runbook, Bend seam RFC, bend-perf, TencentDB model select, shadow-loop counts, workflow scripts, this inventory | `kvnloo/z0intelligence` @ `experiments/session-20261003` (new) | `docs/experiments/` (start with `README.md`) |
| CUA loop state, wave syntheses, lane summaries, drafts, autoresearch/stack/bend-stack/i107 summaries, workflow scripts | `kvnloo/cua` @ `evidence/cua-lane-20261003-local` (new; continues `evidence/cua-lane-20260929-local`) | `scripts/repro/handoff/lanes-artifacts-20261003/README.md` |
| Per-lane CUA packets (PREREG, harness, raw tarballs, verdicts) | `kvnloo/cua` @ `exp/*`, `docs/*`, `ar/*` … (84 branches newly pushed, see §3) | `docs/experiments/<lane>/` on each branch |
| Hermes stack and Bend integration code | `kvnloo/hermes-agent` @ `exp/stack-*-20261002`, `exp/bend-stack-integration-20261002` | already pushed before this run |
| Bend plugin / patched Bend | `kvnloo/bend-native` @ `exp/{aodl,b389,b390,contract,e2e}-20261002`, `kvnloo/bend` @ `exp/b389-patchonly-20261002` | already pushed before this run |

## 2. Pushed in this run

| Repo | Ref | Kind | Content |
|---|---|---|---|
| kvnloo/cua | 84 local branches (list in §3) | new branches | Existing commits only (68 commits not on any fork ref). All authored by kvnloo noreply. |
| kvnloo/cua | `evidence/cua-lane-20261003-local` @ `8bbdd1ce4` | new branch (parent `0e13bf6d2`) | 285 text files, 3.6 MB, from `cua-lanes/artifacts` |
| kvnloo/z0intelligence | `experiments/session-20261003` | new branch (parent master `0159808`) | `docs/experiments/**` |

Pre-push scan of every new commit or file:
- No secret-shaped strings (AWS, OpenAI/Anthropic, GitHub, Slack, JWT, private-key, Google, HF patterns) except the known false positives listed under §5.
- No `.env`, `auth.json`, keys or pem files.
- No emails other than veeman961@gmail.com, noreply addresses and `example.invalid`.
- No home-directory paths: `~` replaced them in the copied artifacts, and `<local-host>` replaced the host name.
- No blob over 20 MB. The largest new packet blob is about 6.7 MB (`b-07…/raw/browser/*.tar.gz`). The 5 to 13 MB images listed come from the upstream tree.

## 3. Repos and branches (session scope)

### kvnloo/cua (`/mnt/zer0models/github/cua`, 178 worktrees under `github/cua-lanes/`)
- Remotes: `origin` = kvnloo/cua (owner's fork), `upstream` = trycua/cua (never pushed to).
- Local branches with commits in the last 8 days: 169. Before this run, 83 matched the fork and 86 were missing from it. After this run, 167 match.
- Pushed now (84): see `cua-push-list.txt`. This includes `ar/calib/*` and `ar/calib2/*` (19 calibration candidates each, which the autoresearch proposer had kept local "for owner review"), `ar/dryrun/settle-watch-120`, every `exp/*-20261002/3` packet branch, `docs/accounting-10-queue-74-r2-20261003`, `docs/packet-template-audit-20261003`, `docs/rfc3963-rewrite-draft-r2-20261003`, `evidence/cua-lane-20260928-local`, `perf/guarded-completion-main-v3`, `perf/jev-use-native-timing-parity-20260928-local` and `research/guarded-{economics,predicate-audit,receipt-audit}-20260929`.
- **Deferred, in use** (live processes have their cwd in these worktrees, from the CUA RFC loop's FRESH-07 Phase 2 and R2-07g lanes):
  - `exp/fresh-07-main-9a2b1d99e-20261003` (`cua-lanes/w7-fresh07`)
  - `exp/r2-07g-live-fallback-ln-20261003` (`cua-lanes/w7-r2-07g`)
- Dirty worktrees (21 worktrees, 45 paths; not committed, left for the lane owners):
  - Red-check scratch, deliberately uncommitted: `w3-fix02-red`, `w4-own09r2-m`, `w5-own09r2-m`, `w6-fix03-red1`, `w6-fix03-red2`, `w7-fix04-redc`, all on a detached HEAD.
  - Packet leftovers from attempts that a later `-a2`/`-r1c` branch superseded: `w4-b04-obs-reconcile`, `w4-b05-mcp-transport`, `w4-n03-native-closure`, `w4-own20g-guard`, `w4-pkt-audit`, `w4-pkt-r2-10` (7 paths, including rescrubbed raw logs), `w4-r2-07c-compiled`, `w4-r2-10r`, `w4-fix-recert`, `w2-own75-parity` (untracked `raw/`).
  - Untracked test app or research folders: `cmain`, `p75b` (`harness-gtk3/`), `speed-frontier` (`research/work-deletion/semantic-e0-e1-20261001/`), `evidence` (`4316-maintainer-proof-review/`).
  - In use: `w7-r2-07g` (1 path).
- `main`/default branches: not touched.

### kvnloo/z0intelligence (`/mnt/zer0models/z0-wt/z0intelligence`, 17 worktrees)
| Branch | vs fork | Action |
|---|---|---|
| `integrate/wiring-20261003`, `feat/wire-{loop-core,omp-capture,hermes-capture,dsh-plugin,memory-core}-20261003`, `feat/shadow-loop-v0` | same | none |
| `feat/wire-loop-consumers-20261003` (C2) | missing (fork has `…-unverified`) | **deferred, in use.** The round-3 Publish step pushes it under its verified or `-unverified` name. |
| `feat/wire-memory-harnesses-20261003` (C8) | local is a fast-forward of the fork, 12 commits ahead | **deferred, in use** (round 3 is fixing C8) |
| `feat/wire-learning-tick-20261003` (C6) | missing | **deferred, in use.** Worktree `wiring/wt/C6-learning-tick` has 10 files modified in the last 30 minutes. |
| `experiments/session-20261003` | new | **pushed** |
| `master` | fork is ahead (local is stale) | none |
- Detached worktrees: `bend-aodl-gate`, `bend-service`, `stack-z0`, `z0int-claude-code` and `wiring/bend-perf/wt/gate` (all clean). `wiring/wt/C6-learning-tick-base` is registered but missing on disk (prunable).

### Other session repos (all clean and all already on their fork)
| Repo | Fork | Branches | State |
|---|---|---|---|
| `github/bend-native` (+ `bend-native-wt/*`) | kvnloo/bend-native | `exp/{aodl,b389,b390,contract,e2e}-20261002`, `main` | same |
| `github/bend-fork` (+ `bend-fork-wt/*`, `bend-fork-v2.0.34`) | kvnloo/bend | `exp/b389-patchonly-20261002`, `main` | same |
| `hermes-wt/h.git` (bare; upstream NousResearch is `origin`, the fork is reached by URL) | kvnloo/hermes-agent | `exp/stack-{integration,multiseat,samples,smoke,addr,confirm}-20261002`, `exp/bend-stack-integration-20261002`, `claude/ledger` | same |
| `z0-wt/evolution-lab` (+ `evolution-lab-verified-loop`) | kvnloo/evolution-lab | `exp/verified-loop-v0-run` | Its content is on the fork as `exp/verified-loop-v0` (upstream ref, 0 commits ahead). No push needed. |
| `z0-wt/hermes-jev-skills-sync` | kvnloo/hermes-jev-skills | `upstream-pr/outcome-grounded-promotion-docs` | same (the PR body is in the experiments branch) |
| `z0-wt/wiring/agentsview-build/agentsview` | kvnloo/agentsview | `build/v0.44.0-omo-20261003` | same |
| `z0-wt/ro/*` (11 read-only mirrors) | various kvnloo | default branches | clean, nothing local |

### Hermes branches outside this session (inventory only)
`hermes-wt/h.git` also holds kvnloo branches from the Hermes upstream-contributing lane:
- 15 `ready/*` branches have **diverged** from their fork copies. The fork versions are newer rebuilds. They were not pushed, because that would need a force push.
- About 20 `staging/*` refs are local-only by design: the fork has a legacy `staging` branch, so the convention is to push them as `staged/<id>`.
- `feat/tui-model-search-flat` and `pr-126847-rebase`: the first is already contained in upstream PR #126847 (head branch `feat/tui-session-model-hop-v2` on the fork), and the second is a retired version. Not pushed.

## 4. Repos inventoried but not pushed (outside session scope or owner trees)

| Path | Fork | Notes |
|---|---|---|
| `~/tmp/dsh` | kvnloo/deepseek-harness | Owner tree. 1 recent branch, already on the fork. |
| `~/tmp/openjev` (+ worktrees incl. `~/tmp/z0int-canonical`, `z0int-*`, `.work/z0intelligence`) | kvnloo/openjev → z0intelligence | Owner tree. 13 recent branches, all on the fork. `z0int-canonical` is on `feat/promote-local-cognition-extension` with 11 dirty paths and **2 live processes**, so it is in use. `.work/z0intelligence` is on `feat/local-cognition-portfolio` with 44 dirty paths (last commit 2026-09-22). 8 registered worktrees are missing on disk. |
| `~/tmp/bluemira-4499`, `~/tmp/gptr`, `~/zer0/oss/oh-my-pi` | kvnloo forks | Agent branches `fix/4499-ndiscr-inheritance`, `feat/z0int-context-filter` and `feat/cognitive-state-shadow-rfc79` are already on their forks (`ls-remote` matches). |
| `github/kvnloo/newton` | kvnloo/newton | 18 evidence/fix branches from 2026-09-27, all on the fork. On 3 of them (`fix/builder-rotation-velocities`, `fix/kamino-scalar-sparse-blocks`, `fix/sparse-cuda-pointer-device`) the fork has moved past local: the fork head is not in the local repo, so the fork is newer. 20 worktrees are registered but missing on disk. |
| `/workspace/hermes-home/hermes-agent` (owner's live Hermes install) | fork = kvnloo/hermes-agent | Not in the task's scope. `promote/terminal-ambiguous-421` (2026-10-03), `study/hermes-unified-memory-56` (2 commits ahead of fork/main) and `local/rebuild-127143/127152/127170` are not on the fork. These belong to the Hermes agent's lane, so they are left to the owner or that agent. 7 worktrees are dirty (55 paths). |
| `/mnt/zer0models/workspace/zer0/oss/hermes-agent` (146 worktrees) | kvnloo/hermes-agent | Kanban/Hermes-agent worktrees, mostly older than 5 days. 39 dirty worktrees, 8 missing. Not this session's work. |
| `github/kvnloo/{o8,opencode,omnara,Hands-On-…,noop,…}`, `~/zer0/oss/{o8,hermes-k8s-lab,…}`, `/mnt/zer0models/oss/{osf.io,cursor/plugins,TencentDB-Agent-Memory}`, `.work/llama.cpp`, other `~/tmp/*` | various | No session work. `opencode` (6,608 dirty) and `o8/.next/standalone` (1,621 dirty) are build trees. `TencentDB-Agent-Memory` is upstream Tencent and has no kvnloo remote. |

## 5. Non-git experiment data

| Artifact | Size | Target | in_use | Privacy risk |
|---|---|---|---|---|
| `z0-wt/wiring/spec/` (excluding `scratch/`) | 2.0 MB | z0intelligence `experiments/session-20261003:docs/experiments/z0-wiring/spec/` (pushed) | no | low |
| `z0-wt/wiring/evidence/` | 2.5 MB | `…/z0-wiring/evidence/` (pushed snapshot). All of `C6-learning-tick/` was excluded, which covers the 13 files written in the last 30 minutes. | **yes** (round 3) | low (isolated homes; dummy keys) |
| `z0-wt/wiring/publish/` | 66 KB | `…/z0-wiring/publish/` (pushed snapshot) | yes (round 3 will rewrite it) | low |
| `z0-wt/wiring/{round2,prior-findings.md,c3verify-diff-*.patch,*.js}` | 0.1 MB | `…/z0-wiring/` and `workflows/` (pushed). The `.bak` copies were not pushed. | no | low |
| `z0-wt/wiring/ops/agentsview/` | 5 MB | `…/z0-wiring/ops-agentsview/` (pushed) | no | medium. Logs list real session ids and counts but no message text; home paths are redacted. |
| `z0-wt/wiring/ops/tencentdb/` (README, unit, yaml, bin, e2e) | 0.2 MB | `…/tencentdb-model-select/service-e2e/` (pushed). `tencentdb-memory.env` was excluded under the `.env` rule (it holds only BWS ids). | no | low |
| `z0-wt/wiring/ops/tencentdb/model-select` (local git, `d49fad1`, no remote) | 171 MB | `…/tencentdb-model-select/` (committed HEAD snapshot pushed). `runs/` (165 MB), `results/` and `scratch/` were not pushed. `bench/corpus/corpus.json` was **excluded** because it contains synthetic credential traps. | **yes** (6 live processes) | medium |
| `z0-wt/wiring/bend-perf` (local git, `5844252`, no remote, 4 untracked files) | 86 MB | `…/bend-perf/` (committed HEAD snapshot pushed). Untracked `results/TABLE.md`, `single.json` and `harness/{make_table.py,run_timed.sh}` are **deferred**, and so are binaries and `bend-strings.txt`. | **yes** (3 live processes) | low |
| `z0-wt/wiring/bend-seam/*.md` | 0.3 MB | `…/bend-seam/` (pushed) | no | low |
| `z0-wt/wiring/bend-seam/{src,scratch}` | 384 MB | not pushed (a Bend checkout and issue dumps) | no | low |
| `z0-wt/wiring/{homes,scratch,venv-integrate,agentsview-build,backups,scratch-*}` | about 18 GB | not pushed (sandbox homes, venvs, build trees) | homes/scratch: yes | **high** for `homes/`, which are copies of harness homes |
| `z0-wt/shadow-loop-data/` | 40 KB | `…/shadow-loop/` (pushed) | no | low (counts and hashed ids only) |
| `z0-wt/upstream-pr-bodies/` | 2 KB | `…/upstream-pr-bodies/` (pushed) | no | low |
| `github/cua-lanes/artifacts/**` | 5.9 GB | kvnloo/cua `evidence/cua-lane-20261003-local` gets a text subset of 285 files. `env/` (6 files) was excluded by the repo `.gitignore`. Raw run dirs (`r2` 3.1 GB, `stack` 2.3 GB, `bend-stack` 248 MB, `i107` 201 MB) stay on the host; their verified packets are on the `exp/*` branches. | partly (`r2/loop/STATE.json` and the FRESH-07/R2-07g dirs are live) | medium |
| `/mnt/zer0models/cua-lane-tmp/ar-calib2` | 1.4 GB | not pushed: it is a raw run dir, and its packet (CALIBRATION2, PREREG and amendments) is on `exp/ar-pilot2-20261002` | no | low |
| Session workflow scripts (`~/.claude-home/projects/-home-kvn-zer0/5bc3424d-…/workflows/scripts/*.js`) | 120 KB | Alongside each topic in `docs/experiments/*/` (pushed). The `.bak*` copies were not pushed. | the push-all script is live | low |
| `~/.claude-home/jobs/5bc3424d/tmp` (research dumps: `cc_*.txt`, `dsh_sessions*.txt`, `av/`, PR/issue dumps) | 15 MB | **not pushed** | no | **high** (real conversation and session text) |
| `/mnt/zer0models/bend-stack/` (Bend builds, Lean toolchain, ollama home, logs) | 4 GB | not pushed (binaries and toolchains). `logs/` is 3.9 MB. The results synthesis is in `cua-lanes/artifacts/bend-stack/SYNTHESIS.md` and is pushed. | ollama may be live | low |
| `/mnt/zer0models/agentsview-builds/v0.44.0-omo-*` | 282 MB | not pushed (binaries; the source is on kvnloo/agentsview `build/v0.44.0-omo-20261003`) | no | low |

Known false positives kept after the scan:
- `sk-ant-dummy-*` placeholders in the z0 e2e scripts.
- `admission-<sha256>` lease tokens inside pytest failure traces.
- The literal `-----BEGIN OPENSSH PRIVATE KEY` string in the corpus generator, which builds keys from a seeded RNG.
- `"token": "MS-p…"` task markers in `stack/multiseat/grade-local.json`.

## 6. To finish later (after the live workflows end)
1. **Wiring round 3:** its Publish step pushes C2, C8 and C6 plus `integrate/wiring-20261003` itself. Afterwards, refresh `docs/experiments/z0-wiring/{evidence,publish}` on `experiments/session-20261003` with a fast-forward commit, including `evidence/C6-learning-tick/` and `E2E-round3.md`.
2. **TencentDB model select and bend-perf:** when each run ends, commit its results in its local repo, then copy `results/` (and for bend-perf, `results/TABLE.md`) into the experiments branch.
3. **CUA:** push `exp/fresh-07-main-9a2b1d99e-20261003` and `exp/r2-07g-live-fallback-ln-20261003` once those lanes finish. Re-snapshot `r2/loop/STATE.json` and the syntheses as a fast-forward of `evidence/cua-lane-20261003-local`.
4. **Owner decisions:**
   - The 15 diverged hermes `ready/*` branches: the fork copies look newer.
   - The un-pushed hermes-home branches (`promote/terminal-ambiguous-421`, `study/hermes-unified-memory-56`).
   - The dirty CUA lane leftovers listed in §3.
   - Pruning missing worktrees (z0intelligence 1, openjev 8, newton 20, workspace hermes-agent 8, and others).

## 7. Hermes-agent addendum (2026-10-03, later pass)
- `/workspace/hermes-home/hermes-agent` -> kvnloo/hermes-agent, new branches: `promote/terminal-ambiguous-421` (e414eff), `study/hermes-unified-memory-56` (c846638), `local/rebuild-127143` (c3d70f9), `local/rebuild-127152` (85efed2), `local/rebuild-127170` (62bee68; same patch as fork `promote/tui-theme-leaves-v2`; 127143/127152 are earlier drafts of fork `promote/tui-queue-scope-v2` / `promote/tui-agents-idle-clock-v2`).
- `oss/hermes-agent` (kanban lane, Aug-Sep): 261 local branches missing on fork; 142 hold commits not on any fork/origin ref (422 commits). Pushed 126 as new branches (list: `hermes-oss/push-list.txt`). 119 with no unique commits not pushed (nothing new).
- **Excluded (16 branches, `hermes-oss/bad-branches.txt`)**: they carry raw evidence that failed the privacy scan: `evidence/firstmate-causality/v3/raw/*` (raw model responses incl. encrypted reasoning), `.cu-perf-report/phase9/**` (incl. `live/lease.env`), `plugins/kanban/evidence/attention-responsive/electron-e2e-v2/**` (kanban.db, emails), `tests/kanban_hold_harness/artifacts/matrix-results.json` and `finalize_v3.py` (home paths). Owner decision: push after sanitising (new branch without those paths) or keep local.
