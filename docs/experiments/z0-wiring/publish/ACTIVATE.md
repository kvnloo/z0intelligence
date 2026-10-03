# ACTIVATE: z0 wiring live activation runbook (round 2, 2026-10-03)

**Nothing in this file was executed by the build.** The Publish stage activated nothing, merged nothing to master and opened no PR.

Every step marked *owner approval* changes a live file, link, plugin, service, timer or branch. Do them one step at a time, in the order given.

This rewrite replaces the round-1 runbook, which was pinned to e02bcf5:
- The pin moves to b99bc31, which now also carries C7 and C8 (memory).
- The AgentsView and TencentDB steps are now done.

## Live state now

| Item | State | Source |
|---|---|---|
| AgentsView | **DONE.** v0.44.0-3-g987293f4 (fork build with OMO and the Codex orphan-fork fix). `user_version` 113. Indexes `~/.claude-home` (888 sessions), deepseek-harness (203) and omo (14). `require_auth` is on. | `/mnt/zer0models/z0-wt/wiring/ops/agentsview/README.md` |
| AgentsView sync | **DONE.** `agentsview-sync.timer` is enabled, runs every 30 min, and uses `flock -s -w 60 -E 0` on the quiet-lane lock with `AGENTSVIEW_NO_DAEMON=1`. At 12:00 CDT the last run was 37 s earlier and the newest session was 16:57Z. | same |
| TencentDB Agent Memory gateway | **DONE.** `tencentdb-memory.service` (user unit, enabled, `Restart=always`) listens on `127.0.0.1:8420` only and uses BM25 recall. Hermes chiefstaff, the root profile and memory-lab use it through `memory.provider: memory_tencentdb`, tenant `default`. | `/mnt/zer0models/z0-wt/wiring/ops/tencentdb/README.md` |
| z0 capture and memory plugins in any harness | **NOT DONE** | A1–A5, A8 below |
| Learning tick timer | **NOT DONE. BLOCKED:** C6 was never built and C2 is unverified. | A7 |
| Per-harness memory injection | **NOT DONE.** Opt-in per harness: default off for cloud models, shadow first. | A8 |
| Live z0 service (`127.0.0.1:11501`) | Still runs the closed-PR branch a9cbbed from a dirty tree. | A10 |

## Pin

| | value |
|---|---|
| Repo | `kvnloo/z0intelligence` (origin of `/mnt/zer0models/z0-wt/z0intelligence`) |
| Integrate branch | `integrate/wiring-20261003` (pushed) |
| **Pinned SHA** | **`b99bc316e2d23e3f0d901b5fe7542d4ce1058060`** |
| Contents | C1 914dc99, C3 3f6d139, C4a ee7cae0, C5 73fe3d7, C7 772ba7d, C8 41aec0f |
| Not contained | C2 (unverified, at `feat/wire-loop-consumers-20261003-unverified` f20c2b7) and C6 (not built) |
| How it reaches master | Owner decision: review, then **fast-forward** `master` to b99bc31 (step M). `origin/master` 0159808 is an ancestor of the pin, so there are 48 commits and no merge commit, and master then equals the pin. |
| Pinned checkout | `/mnt/zer0models/z0-wt/pinned/z0intelligence`, a detached worktree at the pin. It does not exist yet and is created in A0. |
| Interpreter | `/mnt/zer0models/z0-wt/venv-wiring/bin/python`. It does not exist yet and is created in A0. |
| Learner pin | evolution-lab `1b80a4e` (`/mnt/zer0models/z0-wt/evolution-lab-verified-loop`) |
| Plugin SHA installed (A1) | _fill in at A1_ |
| Hermes fork SHA checked (A3) | _fill in at A3. The e2e used ad31bbf079f0ee559ce1b61c5f85178c4c3d9396._ |

## Global rules for every step

- **Backup first.** Before each change, copy every file you touch to `<file>.bak-z0wiring-20261003` with `cp -a`. Never overwrite an existing backup: check with `test -e` first.
- **Capture kill switch.** Either of these works, and the next turn needs no restart:
  - `Z0INT_CAPTURE=0` in the harness env;
  - `mkdir -p ~/.z0int/config && echo '{"enabled": false}' > ~/.z0int/config/capture.json`.
- **Memory kill switch.** Either of these works, and takes effect on the next turn:
  - `Z0INT_MEMORY_INJECT=off` in the harness env;
  - `memory_inject: off` in the shim config (Hermes plugin setting, DSH entry config).
- **Memory shadow defaults to ON at the pin.** The memory seam defaults to `shadow` independently of capture. Installing the Claude Code, Codex, Hermes or DSH plugin from this pin (A1, A3, A4, A5) would also start detached memory shadow children (at most 4 at once), which read AgentsView and write `~/.z0int/state/memory/seam/<h>.jsonl`. Shadow is never model-visible.
  - So that memory is activated only in its own approved step (A8), **set memory off in each capture step**:
    - Claude Code and Codex: `Z0INT_MEMORY_INJECT=off` in their env;
    - Hermes: `memory_inject: off`;
    - DSH: `memory_inject: off`.
  - A8b then moves each harness to `shadow`.
  - The OMP/OMO memory extension `omp-extensions/z0-memory` is a separate extension, which A2 does not install.
- **Cloud injection stays off.** No step sets `allow_cloud_injection` except A8c, which the owner does per harness.
- **Request text:** never stored unless `Z0INT_CAPTURE_PRIVACY=request_opt_in`. Leave it unset.
- **Gated by #95; never part of this runbook:**
  - `automatic.json` hermes/dsh on;
  - DSH `router: true`;
  - any new route_worker or delegate_worker exposure.
- **Credentials and payment:** never run the `dsh` wrapper as a check. The owner runs every turn that needs credentials or costs money (DSH, Grok, Codex, OMO).
- **CPU:** heavy steps (venv build, tick) run under `flock -s /mnt/zer0models/cua-lane-tmp/locks/quiet-lane.lock`, with `nice -n 19 ionice -c3`, outside experiment windows.
- **Expected rows:** `missing_verifier` and `partial_measurement` are expected for non-CC harnesses until C2 lands. They are not activation failures.

## Order

| Step | What | Status |
|---|---|---|
| M | Review, then fast-forward master to the pin | **owner action, next** |
| A0 | Pinned checkout, venv and read-only preflight (incl. `z0int memory doctor`) | ready after M |
| A1 | Claude Code plugin (capture; z0-memory MCP comes with it) | ready after A0 |
| A2 | OMP + OMO capture | ready after A0 |
| A3 | **Hermes chiefstaff: replace the routing-only `hermes-z0intelligence` plugin** | ready after A0 |
| A4 | DSH plugin | ready after A0 |
| A5 | Codex + Grok | ready after A0 |
| A6 | AgentsView v0.44, resync and sync timer | **DONE**; check only |
| A7 | Learning tick timer | **BLOCKED: C2 unverified, C6 not built** |
| A8 | Unified memory: MCP entries, shadow, then per-harness injection opt-in | ready after A1–A5 |
| A8-sem | z0 reads from the TencentDB gateway | gateway **DONE**; z0 config optional, low value today (#63) |
| A9 | Legacy route_worker seams | owner decision per seam |
| A10 | Unify the live z0 service onto the pin | owner decision Q12; separate window |

---

## M: review, then fast-forward master (owner action)

The owner chose to merge integrate into master after review, so do this before installing anything.

**Touches:** `kvnloo/z0intelligence` `master` (remote) and the local master worktree `/mnt/zer0models/z0-wt/z0intelligence`. That worktree is clean and 2 commits behind origin.

1. Review the staged PR bodies in `/mnt/zer0models/z0-wt/wiring/publish/`. Start with `pr-z0intelligence-integrate-wiring-20261003.md`, then the C7 and C8 bodies.
2. Fast-forward only, so that no merge commit can appear and master equals the pin:
   ```bash
   cd /mnt/zer0models/z0-wt/z0intelligence
   git fetch origin
   git merge-base --is-ancestor origin/master b99bc316e2d23e3f0d901b5fe7542d4ce1058060 || echo STOP-master-moved
   git merge --ff-only b99bc316e2d23e3f0d901b5fe7542d4ce1058060
   test "$(git rev-parse HEAD)" = b99bc316e2d23e3f0d901b5fe7542d4ce1058060 && git push origin master --no-follow-tags
   ```
   If master has moved, stop. Merge master into integrate on the integrate branch, re-run the integration suite, and re-pin.

**Check:** `git ls-remote origin refs/heads/master` shows b99bc31.

**Rollback:** do not rewrite master. If needed, revert with a normal `git revert -m` on a new branch, then review it. Nothing live depends on master until A1.

---

## A0: pinned checkout and read-only preflight (owner approval; no live change)

**Touches:** only new directories under `/mnt/zer0models/z0-wt/` (`pinned/`, `venv-wiring/`) and scratch. It reads the live AgentsView DB read-only.

**Backup:** none needed, because nothing live changes.

1. Create the pinned checkout. It is detached, so no branch is created.
   ```bash
   git -C /mnt/zer0models/z0-wt/z0intelligence worktree add --detach \
       /mnt/zer0models/z0-wt/pinned/z0intelligence b99bc316e2d23e3f0d901b5fe7542d4ce1058060
   test "$(git -C /mnt/zer0models/z0-wt/pinned/z0intelligence rev-parse HEAD)" = b99bc316e2d23e3f0d901b5fe7542d4ce1058060
   ```
2. Build venv-wiring. These are the same steps as the tested `venv-integrate`.
   ```bash
   flock -s /mnt/zer0models/cua-lane-tmp/locks/quiet-lane.lock nice -n 19 ionice -c3 sh -c '
     uv venv --python 3.13 /mnt/zer0models/z0-wt/venv-wiring &&
     uv pip install --python /mnt/zer0models/z0-wt/venv-wiring/bin/python --torch-backend cpu \
        -e "/mnt/zer0models/z0-wt/pinned/z0intelligence[test]" &&
     uv pip install --python /mnt/zer0models/z0-wt/venv-wiring/bin/python \
        -e /mnt/zer0models/z0-wt/evolution-lab-verified-loop'
   git -C /mnt/zer0models/z0-wt/evolution-lab-verified-loop rev-parse --short HEAD   # expect 1b80a4e
   ```
3. Check the pin:
   ```bash
   git -C /mnt/zer0models/z0-wt/pinned/z0intelligence status --porcelain | wc -l     # expect 0
   /mnt/zer0models/z0-wt/venv-wiring/bin/python -c 'import z0int.hook_entry, z0int.harness_capture, z0int.memory.seam; print("ok")'
   ```
4. Run the read-only baseline: counts only, no appends.
   ```bash
   S=/mnt/zer0models/z0-wt/wiring/scratch/a0-baseline; mkdir -p $S
   CLAUDE_CONFIG_DIR=~/.claude-home Z0INT_HOME=~/.z0int /mnt/zer0models/z0-wt/venv-wiring/bin/z0int \
      outcomes verify --no-gh --dry-run --json > $S/cc-verify.json
   Z0INT_HOME=~/.z0int /mnt/zer0models/z0-wt/venv-wiring/bin/z0int outcomes backfill-capture \
      --harness claude-code --dry-run > $S/cc-backfill-dryrun.json
   AGENTSVIEW_DATA_DIR=/mnt/zer0models/sft-svlm/data/agentsview Z0INT_HOME=~/.z0int \
      /mnt/zer0models/z0-wt/venv-wiring/bin/z0int memory doctor > $S/memory-doctor.txt
   ```
   - `memory doctor` is read-only. It opens `sessions.db` with mode=ro and never prints `require_auth`.
   - Expected: freshness ok, deepseek-harness present, `~/.claude-home` indexed, and user_version 113 equal to the dataVersion.
   - The semantic layer shows UNAVAILABLE until A8-sem.
   - C2's `outcomes verify --harness <h>` and `loop import` do not exist at this pin.

**Check:**
- The SHA matches, the tree is clean, and the import prints `ok`.
- Doctor is green apart from the semantic layer.
- Record the counts here.

**Rollback:**
```bash
git -C /mnt/zer0models/z0-wt/z0intelligence worktree remove /mnt/zer0models/z0-wt/pinned/z0intelligence; rm -rf /mnt/zer0models/z0-wt/venv-wiring
```

---

## A1: Claude Code plugin (C1 capture, C8 seam held off) (owner approval)

**Touches:**
- `~/.claude-home/settings.json`
- the Claude plugin cache and known marketplaces
- `~/.z0int/state/claude-code/opportunities.jsonl` (the backfill rewrites it in place)

**Backup:**
```bash
for f in ~/.claude-home/settings.json ~/.z0int/state/claude-code/opportunities.jsonl; do
  test -e "$f.bak-z0wiring-20261003" || cp -a "$f" "$f.bak-z0wiring-20261003"; done
```

**Steps.** Run every `claude` command with `CLAUDE_CONFIG_DIR=~/.claude-home`.

1. List the marketplaces with `claude plugin marketplace list`.
   - If `z0intelligence` points at GitHub `kvnloo/z0intelligence`, record its source, then run `claude plugin marketplace remove z0intelligence`.
   - INFERRED: removing it may uninstall `z0intelligence` and `z0-obspack`. Reinstall both below.
2. Install from the pinned checkout:
   ```bash
   claude plugin marketplace add /mnt/zer0models/z0-wt/pinned/z0intelligence
   claude plugin install z0intelligence@z0intelligence
   ```
   Reinstall `z0-obspack@z0intelligence` if it was installed before.
3. Add these to `"env"` in `settings.json`:
   ```json
   "Z0INT_PYTHON": "/mnt/zer0models/z0-wt/venv-wiring/bin/python",
   "Z0INT_MEMORY_INJECT": "off"
   ```
   - `Z0INT_MEMORY_INJECT=off` holds the C8 `UserPromptSubmit` memory seam until A8b.
   - The plugin's `.mcp.json` also adds the `z0-memory` MCP server (memory profile, read-only tools, no route_worker). This is the Claude Code part of A8a. If the owner wants capture strictly without the memory tools, disable that server with `/mcp` until A8.
4. Do **not** also register `PreToolUse`/`PostToolUse(Agent|Task)` capture hooks. SubagentStart and SubagentStop already cover subagents, so both pairs would double-capture.
5. Run the one-shot legacy backfill. It is idempotent and holds the writers' lock.
   ```bash
   Z0INT_HOME=~/.z0int /mnt/zer0models/z0-wt/venv-wiring/bin/python -m z0int.cli outcomes backfill-capture --harness claude-code --dry-run
   Z0INT_HOME=~/.z0int /mnt/zer0models/z0-wt/venv-wiring/bin/python -m z0int.cli outcomes backfill-capture --harness claude-code
   ```
6. The owner runs one interactive turn and one turn that spawns a subagent.

**Check:**
- `~/.z0int/state/claude-code/{opportunities,outcomes,failures}.jsonl` holds a joined interactive pair and an `agent`-cohort pair.
- `partial_measurement`/`late_messages` and one `duplicate_event` per turn are expected.
- `~/.z0int/state/memory/seam/claude-code.jsonl` is absent or has only `off` rows.
- Record the installed plugin SHA in the Pin table (expect b99bc31).

**Rollback:**
```bash
cp -a ~/.claude-home/settings.json.bak-z0wiring-20261003 ~/.claude-home/settings.json; claude plugin marketplace remove z0intelligence; claude plugin marketplace add kvnloo/z0intelligence; claude plugin install z0intelligence@z0intelligence
```
Restore `opportunities.jsonl` from its backup only if the backfill must be undone.

---

## A2: OMP and OMO capture (C3) (owner approval)

**Touches:**
- `~/.omp/agent/extensions/{z0int-bridge,local-cognition}` (symlinks)
- `~/.omo/agent/extensions/z0-capture.js` (new)
- the OMP/OMO launch env

**Backup:**
```bash
ls -l ~/.omp/agent/extensions > ~/.omp/agent/extensions.ls.bak-z0wiring-20261003
```
The install script also writes `extensions.links.bak-z0wiring-<YYYYMMDD>` itself, before it changes anything.

**Steps:**
1. Dry run:
   ```bash
   cd /mnt/zer0models/z0-wt/pinned/z0intelligence && /mnt/zer0models/z0-wt/venv-wiring/bin/python scripts/omp_bridge_install.py --target /mnt/zer0models/z0-wt/pinned/z0intelligence --dry-run
   ```
   - Only `z0int-bridge` and `local-cognition` may be retargeted.
   - `z0int-intelligence` must be `kept`.
   - If the script refuses because no `z0int-intelligence` link exists, **stop and ask the owner**. `--allow-no-routing` is for an explicit owner decision only.
2. Run the same command without `--dry-run`. It refuses a dirty pinned tree.
3. Make `Z0INT_PYTHON=/mnt/zer0models/z0-wt/venv-wiring/bin/python` visible to OMP and OMO, through their launcher env or a `~/.z0int/bin/python` symlink. If that symlink already exists, record `readlink -f ~/.z0int/bin/python` here first.
4. Restart OMP. Live routing is unchanged. `automatic.json` `omp=true` is left untouched (Q16).
5. OMO (capture only):
   ```bash
   test -e ~/.omo/agent/extensions/z0-capture.js && echo EXISTS-STOP || \
   printf '%s\n' 'export { default } from "file:///mnt/zer0models/z0-wt/pinned/z0intelligence/omp-extensions/z0int-bridge/omo.ts";' > ~/.omo/agent/extensions/z0-capture.js
   ```
   Restart OMO.
6. The owner runs one OMP turn and one OMO turn.

**Check:**
- Joined pairs in `~/.z0int/state/{omp,omo}`.
- `drops.jsonl` reasons are known: `worker_unavailable`, `unclosed_turn` or `stale_turn`.
- `bridge.jsonl` holds no prompt text.

**Kill switches:** `Z0INT_CAPTURE=0`, `OMP_Z0INT_COGNITION_SHADOW=0`.

**Rollback:**
```bash
ln -sfn <links.z0int-bridge.target from backup> ~/.omp/agent/extensions/z0int-bridge; ln -sfn <links.local-cognition.target> ~/.omp/agent/extensions/local-cognition; rm ~/.omo/agent/extensions/z0-capture.js
```
Then restart both.

**Still open:**
- Q13: the hermes-jev OMP `execSync` prompt-shell injection.
- Whether to purge historical prompt text from the live `bridge.jsonl` and `cognition-shadow.jsonl`.

---

## A3: Hermes chiefstaff, replace the plugin (C4a capture + C8 seam held off) (owner approval)

The owner chose activation profile **chiefstaff/clean**.
- Do `chiefstaff` first, with `P=~/.hermes/profiles/chiefstaff`.
- If the owner meant the `clean` profile as well, repeat this whole step with `P=<clean profile dir>`, with its own backups.

This replaces the routing-only plugin of the same name, `plugins/hermes-z0intelligence`, with the capture and memory plugin from the pin. Automatic behaviour does not change.

**Touches:**
- `$P/config.yaml`
- `$P/plugins/hermes-z0intelligence`
- the owner's Hermes services (restart)

1. Confirm the profile: `cat ~/.hermes/active_profile`. Check `hermes --version` against the fork SHA and record it in the Pin table.
2. **Backup:**
   ```bash
   test -e $P/config.yaml.bak-z0wiring-20261003 || cp -a $P/config.yaml $P/config.yaml.bak-z0wiring-20261003
   readlink -f $P/plugins/hermes-z0intelligence | tee -a /mnt/zer0models/z0-wt/wiring/publish/ACTIVATE.hermes-readlink.txt
   test -e $P/plugins/hermes-z0intelligence.bak-z0wiring-20261003 || cp -a $P/plugins/hermes-z0intelligence $P/plugins/hermes-z0intelligence.bak-z0wiring-20261003
   ```
3. Install. This is UNVERIFIED: the package manager syncs from GitHub, and the pin is pushed. Never pass a local path, because Hermes parses it as GitHub shorthand.
   ```bash
   hermes plugins install https://github.com/kvnloo/z0intelligence/tree/b99bc316e2d23e3f0d901b5fe7542d4ce1058060/harness-adapters/hermes-z0intelligence --ref b99bc316e2d23e3f0d901b5fe7542d4ce1058060 --force
   ```
   **FALLBACK** (the layout the e2e ran):
   ```bash
   S=/mnt/zer0models/z0-wt/wiring/scratch/a3; mkdir -p $S
   git -C /mnt/zer0models/z0-wt/pinned/z0intelligence archive b99bc316e2d23e3f0d901b5fe7542d4ce1058060 harness-adapters/hermes-z0intelligence | tar -x -C $S
   rm -rf $P/plugins/hermes-z0intelligence && mv $S/harness-adapters/hermes-z0intelligence $P/plugins/
   ```
   Then make sure `hermes-z0intelligence` is in `plugins.enabled`.
4. Set `plugins.entries.hermes-z0intelligence.settings`:
   ```yaml
   mode: shadow
   opportunities: true
   z0int_python: /mnt/zer0models/z0-wt/venv-wiring/bin/python
   z0int_home: ~/.z0int
   persist_packet_text: false
   memory_inject: "off"      # A8b sets shadow. Left empty, it would follow mode and run shadow.
   ```
   - Do not add a host or port key.
   - `memory.provider: memory_tencentdb` stays as it is. When it is set, the z0 brief leaves TencentDB out, so nothing is double-injected.
5. Confirm that `z0int-decisions` is not in `plugins.enabled`, and that bend does not have `stack_opportunities: true`. Otherwise the plugin writes `double_capture_guard` or `retired_vehicle` rows.
6. Keep `automatic.json` hermes off (#95).
7. The owner restarts their Hermes services; plugins load at process start (INFERRED). List them with `systemctl --user list-units 'hermes*'`.
8. The owner runs one real turn.

**Check:**
- `~/.z0int/state/hermes/{events,opportunities,outcomes,failures}.jsonl` are joined.
- `z0int hermes decisions` shows `rows_dropped 0`.
- `~/.z0int/runtime/hermes-gates` is empty after exit.
- There are no memory seam rows for hermes.
- Record the hook latency.

**Kill switch:** `mode: off`, `Z0INT_CAPTURE=0` or `capture.json`.

**Rollback:**
```bash
cp -a $P/config.yaml.bak-z0wiring-20261003 $P/config.yaml; rm -rf $P/plugins/hermes-z0intelligence; cp -a $P/plugins/hermes-z0intelligence.bak-z0wiring-20261003 $P/plugins/hermes-z0intelligence
```
Then restart. C4b stays gated.

---

## A4: DSH plugin (C5 capture + C8 seam held off) (owner approval)

**Touches:**
- `~/.dsh/profiles/{web,headless,sdk-minimal,deepseek-sdk}/{cordis.patch.yml,package.json}`
- each profile's `automatic-z0intelligence` / `dsh-z0intelligence` node_modules link

**Backup:**
```bash
for p in web headless sdk-minimal deepseek-sdk; do d=~/.dsh/profiles/$p
  for f in cordis.patch.yml package.json; do test -e $d/$f.bak-z0wiring-20261003 || cp -a $d/$f $d/$f.bak-z0wiring-20261003; done
  find $d/node_modules -maxdepth 2 -type l \( -name 'automatic-z0intelligence' -o -name 'dsh-z0intelligence' \) \
    -printf "$p %p -> %l\n" >> ~/.dsh/profiles/links.bak-z0wiring-20261003.txt; done
```

**Steps:**
1. Repoint each recorded link to `/mnt/zer0models/z0-wt/pinned/z0intelligence/harness-adapters/dsh-z0intelligence` with `ln -sfn`.
2. Set the entry config:
   ```yaml
   capture: true
   router: false
   memory_inject: off
   ```
   - Leave `shadow.url` unset (Q10).
   - Never set `router: true` or `automatic.json` `dsh.enabled` before #95.
   - Put `Z0INT_PYTHON=/mnt/zer0models/z0-wt/venv-wiring/bin/python` in the DSH env.
3. Remove the `hermes-jev-dsh` bundle entry from `~/.dsh/profiles/web/package.json`.
4. Leave the `intelligence-z0intelligence` MCP rows alone (A9).
5. The owner runs one DSH turn. This runbook never runs `dsh`.

**Check:**
- `~/.z0int/state/dsh` has a joined pair.
- `lineage.jsonl` has rows.
- `shadow_decisions.jsonl` has `shadow_plane=off`.
- `drops.jsonl` is empty, or holds only `turn_errored`.
- `index.mjs` registers exactly one memory pre-step, which is idle while `off`.

**Rollback:** restore `cordis.patch.yml` and `package.json` from their backups, then `ln -sfn` each link back to its target in `links.bak-z0wiring-20261003.txt`.

---

## A5: Codex and Grok (C1 capture; Codex C8 seam held off) (owner approval)

**Touches:**
- `~/.codex/config.toml` and the Codex plugin cache
- `~/.grok/hooks/z0-capture.json` (new)

**Backup:**
```bash
test -e ~/.codex/config.toml.bak-z0wiring-20261003 || cp -a ~/.codex/config.toml ~/.codex/config.toml.bak-z0wiring-20261003
```

**Codex:**
1. Install:
   ```bash
   codex plugin marketplace add /mnt/zer0models/z0-wt/pinned/z0intelligence
   codex plugin add codex-z0intelligence@z0intelligence
   ```
   Review and trust its hooks once. Its `.mcp.json` brings the `z0-memory` MCP server (this is A8a for Codex).
2. Put `Z0INT_PYTHON` and `Z0INT_MEMORY_INJECT=off` into Codex's env. `~/.z0int/bin/python` works as a fallback for the interpreter.
3. Leave `z0intelligence@personal` alone (A9).
4. The owner runs one turn.

**Check (Codex):** a joined pair in `~/.z0int/state/codex`. Cohort `automated` for `codex exec` turns needs C2.

**Grok:**
1. Install:
   ```bash
   test -e ~/.grok/hooks/z0-capture.json && echo EXISTS-STOP || { mkdir -p ~/.grok/hooks; cp /mnt/zer0models/z0-wt/pinned/z0intelligence/harness-adapters/grok-z0intelligence/hooks/z0-capture.json ~/.grok/hooks/z0-capture.json; }
   ```
   Grok has no memory push seam.
2. The owner runs one turn. Grok is paid.

**Check (Grok):** a joined pair in `~/.z0int/state/grok`.

**Rollback:**
```bash
cp -a ~/.codex/config.toml.bak-z0wiring-20261003 ~/.codex/config.toml; rm ~/.grok/hooks/z0-capture.json
```
If needed, also run `codex plugin marketplace remove z0intelligence` (INFERRED syntax).

---

## A6: AgentsView v0.44 and sync timer (DONE; check only)

This was done by the owner-approved ops change on 2026-10-03. See `/mnt/zer0models/z0-wt/wiring/ops/agentsview/README.md` for the build provenance, the rehearsal, the backup at `/mnt/zer0models/sft-svlm/data/agentsview.bak-v74-20261003`, and the rollback.

**Re-check before A7 and A8.** All of these are read-only:
```bash
sqlite3 "file:/mnt/zer0models/sft-svlm/data/agentsview/sessions.db?mode=ro" 'pragma user_version; select max(started_at) from sessions;'   # 113; within ~1 h
systemctl --user list-timers agentsview-sync.timer --no-pager
journalctl --user -u agentsview-sync -n 20 --no-pager
```

**Watch for:**
- A `Sync complete` message with rc 0 does not prove success. Check `user_version` and `debug.log`.
- Never run `uv tool upgrade agentsview`: it replaces the fork build.
- If exclusive lock windows last more than about 60 s at both :00 and :30, sync runs are skipped (`-E 0`), and freshness can exceed 1 h. Doctor reports this as stale.

---

## A7: continuous-learning tick timer (owner approval) — BLOCKED

**Blocked:**
- C6-learning-tick was never built: there is no branch.
- It depends on C2-loop-consumers, which is still unverified after round 2 (f20c2b7).
- The pin has no `z0int loop tick` and no `deploy/systemd/z0int-loop-tick.*`.

Install nothing until all of these are done:
1. C2 and C6 are verified.
2. They are merged into integrate.
3. Integrate is merged into master (as in M).
4. The pin is re-cut.

**Preconditions:**
- A6 (done).
- The learner pin is installed in A0.

**Touches:**
- `~/.config/systemd/user/z0int-loop-tick.{service,timer}`
- `~/.z0int/config/loop.json`
- `~/.z0int/loop/`

**Planned units (SPEC §11).**
- The tick takes `flock(2)` LOCK_SH on the quiet-lane lock in-process. That is the same kernel lock `flock -s` takes.
- On a lock timeout it writes `skipped_exclusive_window` and exits 0.
- So ExecStart has no outer `flock` wrapper (C6 red test 15).

```ini
# z0int-loop-tick.service
[Unit]
Description=z0int continuous shadow-learning tick (never promotes)
After=agentsview-sync.service
[Service]
Type=oneshot
Environment=CLAUDE_CONFIG_DIR=%h/.claude-home
Environment=Z0INT_HOME=%h/.z0int
Environment=AGENTSVIEW_DATA_DIR=/mnt/zer0models/sft-svlm/data/agentsview
ExecStart=/mnt/zer0models/z0-wt/venv-wiring/bin/z0int loop tick --lock /mnt/zer0models/cua-lane-tmp/locks/quiet-lane.lock --lock-wait-s 1800 --budget-s 600
Nice=19
IOSchedulingClass=idle
```
```ini
# z0int-loop-tick.timer
[Timer]
OnCalendar=hourly
RandomizedDelaySec=300
Persistent=true
[Install]
WantedBy=timers.target
```

**Backup:**
```bash
test -e ~/.z0int/config/loop.json && cp -a ~/.z0int/config/loop.json ~/.z0int/config/loop.json.bak-z0wiring-20261003
```

**Steps, once unblocked:**
1. Record `learner_sha: 1b80a4e` in `~/.z0int/config/loop.json`.
2. Copy the units from the re-pinned checkout's `deploy/systemd/`.
3. Enable the timer:
   ```bash
   systemctl --user daemon-reload && systemctl --user enable --now z0int-loop-tick.timer
   ```

**Check:**
- The first `~/.z0int/loop/reports/<day>/summary.json` is not degraded.
- The second tick adds 0 rows, and the manifest hashes are identical.
- Only `promotion_request.v0` appears under `~/.z0int/loop/requests`.
- Nothing is promoted.

**Rollback:**
```bash
systemctl --user disable --now z0int-loop-tick.timer; rm ~/.config/systemd/user/z0int-loop-tick.{service,timer}; systemctl --user daemon-reload
```

Until C6 lands, any learning run is a manual, owner-run `python -m evolution_lab.verified_loop` under `flock -s`. Expect it to report INSUFFICIENT_DATA (rows 0/300). Six of the seven harnesses also export 0 labelled rows until C2.

---

## A8: unified memory, per-harness opt-in (owner approval for each sub-step and each harness)

Owner decision: memory injection into cloud models is **opt-in per harness, default off, shadow first**.

**Preconditions:**
- A6 (done).
- A0 `z0int memory doctor` is green, except the semantic layer.
- The harness's capture step (A1–A5) is done.

**Back up** each file before you touch it.

### A8a: `z0-memory` MCP entries (memory profile only: read-only tools, no route_worker)

| harness | where | from the pin |
|---|---|---|
| Claude Code | already in the plugin `.mcp.json` (A1) | — |
| Codex | already in the plugin `.mcp.json` (A5) | — |
| OMP | merge into `~/.omp/agent/mcp.json` | `omp-extensions/z0-memory/mcp.json` |
| OMO | merge into `~/.omo/agent/mcp.json` | `omp-extensions/z0-memory/mcp.json` |
| DSH | every `~/.dsh/profiles/*/cordis.patch.yml` | `harness-adapters/dsh-z0intelligence/z0-memory.cordis.yml` |
| Grok | `~/.grok/config.toml` | `harness-adapters/grok-z0intelligence/mcp/z0-memory.toml` |
| Hermes | MCP-enabled profiles only (Hermes toolsets carry `no_mcp`) | — |

**Check:** the harness lists exactly one `z0-memory` server, and `tools/list` returns memory_search, orient, inspect, history, unknowns and verify.

### A8b: shadow, nothing model-visible

Per harness, move the seam from `off` to `shadow`:

| harness | how |
|---|---|
| Claude Code, Codex | `Z0INT_MEMORY_INJECT=shadow`, or remove the `off` |
| Hermes chiefstaff | `memory_inject: shadow` |
| DSH | `memory_inject: shadow`, and set `model_endpoint` to the profile's model URL |
| OMP | install the extension: `-e /mnt/zer0models/z0-wt/pinned/z0intelligence/omp-extensions/z0-memory`, or link it under `~/.omp/agent/extensions` |
| OMO | write `~/.omo/agent/extensions/z0-memory.js` as `export { default } from "file:///mnt/zer0models/z0-wt/pinned/z0intelligence/omp-extensions/z0-memory/omo.ts";` |

`allow_cloud_injection` stays false.

**Check:**
- `~/.z0int/state/memory/seam/<h>.jsonl` gets `shadow` rows with `would_inject` and a valid MemoryUseReceipt.
- `no_scope` and `queue_saturated` are counted.
- The model requests are unchanged.

### A8c: acceptance, then the owner opts in per harness

1. Run the live acceptance eval per harness. Never use `--seed-av-db` against live data: it refuses an existing DB.
   ```bash
   Z0INT_HOME=~/.z0int AGENTSVIEW_DATA_DIR=/mnt/zer0models/sft-svlm/data/agentsview \
   /mnt/zer0models/z0-wt/venv-wiring/bin/z0int memory eval --harness <h> --cohort <cohort.json> \
     --revision b99bc316e2d23e3f0d901b5fe7542d4ce1058060 --out <rows> --cwd <project dir>
   ```
2. The owner reviews A–F and decides the recorded persistence deviations:
   - Hermes: `api_content` sidecar;
   - Claude Code: transcript;
   - DSH: session log.
   The options are to accept a deviation or to keep that harness at shadow.
3. Only for a harness the owner opts in, back up `~/.z0int/config/memory.json`, then set:
   ```json
   {"inject": {"<h>": {"allow_cloud_injection": true}}}
   ```
   - A loopback model does not need this.
   - No env var can bypass this setting.
4. Flip that harness to `canary`, then later to `on`, one harness at a time:
   - `Z0INT_MEMORY_INJECT` for Claude Code, Codex, OMP and OMO;
   - `memory_inject` for Hermes and DSH.
   - Grok stays pull-only. Per-project rules are opt-in only, via `z0int memory rules --harness grok --scope project --owner-approved ...`.

**Check:**
- canary/on rows show `injected`, with latency within 300 ms.
- `timeout`/`error` rows are counted and the turn stays native.
- A cloud harness without the opt-in shows `cloud_injection_blocked`.

**Kill switch / rollback:** `off` on that harness takes effect immediately. Restore `memory.json` and the shim configs from their backups.

**Known limitation:** capture opportunity rows carry no `memory` field. C8 does not join the receipt (DoD D3 is UNMET for opportunity rows). Read the receipts from the seam rows.

### A8-sem: z0 reads from the TencentDB gateway (optional, owner)

**The gateway is DONE:** `tencentdb-memory.service`, on `127.0.0.1:8420`, with no gateway API key.

To let z0 query it, back up `memory.json`, then add:
```json
{"tencentdb": {"url": "http://127.0.0.1:8420", "deadline_ms": 300}}
```
- No `auth_env` is needed, because the gateway has no key.

**Caveats before you enable it:**
- **No project scope.** Gateway items carry no project provenance (#63 is UNMET). With fail-closed scope they are queried but **never enter a project brief**, so this adds latency and no content today.
- **Default tenant.** The client uses team, agent and user `default`, which is the same tenant as the owner's Hermes and OMP memory. Make sure that is intended.
- **Double injection is avoided for Hermes.** With `memory.provider: memory_tencentdb`, the Hermes z0 brief already leaves TencentDB out.
- **No cache.** The gateway has no data revision (`unversioned`), so z0 bypasses the brief cache whenever this layer is on.

**Check:** `z0int memory doctor` reports the semantic layer reachable (`unversioned`).

**Rollback:** remove the `tencentdb` key.

EventLog source ingest stays off until the owner answers Q3: `"eventlog_source_ingest": "references"` in `memory.json`, or `Z0INT_MEMORY_SOURCE_INGEST=references`.

**Sync:** the memory index stays fresh through `agentsview-sync.timer`. There is no separate memory daemon, port or DB.

---

## A9: legacy route_worker seams (owner decision per seam)

These seams are left unchanged by A1–A5:

| seam | what it does |
|---|---|
| Codex `z0intelligence@personal` | route_worker via the untracked `dsh-env-exec.py` with the live Hermes venv and HERMES_HOME |
| OMO `~/.omo/agent/mcp.json` `z0intelligence` | route_worker from the `.work` tree against :11501, plus skill paths into z0int-canonical and the owner's plugins dir |
| the four DSH profiles' `intelligence-z0intelligence` MCP rows | route_worker with the openjev venv |

For each seam:
1. Back up the file.
2. Then either keep the seam, recording it here as a pre-#95 owner exception, or replace it with the `z0-memory` entry from A8a.

**Rollback:** restore the backup.

---

## A10: unify the live z0 service onto the pin (owner decision Q12; its own window)

The live `z0intelligence.service` (:11501) runs from `~/tmp/z0int-canonical`:
- branch `feat/promote-local-cognition-extension` @ a9cbbed, which is closed PR #35;
- the tree is dirty.

Nothing in A1–A9 needs this service. Capture uses file spools and memory uses stdio MCP.

**Touches:**
- `~/.config/z0intelligence/service.env` (`Z0INT_PYTHON`)
- the running service
- every route_worker client

**Preflight (read-only):**
```bash
systemctl --user cat z0intelligence.service                        # do not cat service.env
git -C ~/tmp/z0int-canonical status --porcelain | wc -l
git -C /mnt/zer0models/z0-wt/z0intelligence fetch origin feat/promote-local-cognition-extension 2>/dev/null
git -C /mnt/zer0models/z0-wt/z0intelligence cherry -v b99bc316e2d23e3f0d901b5fe7542d4ce1058060 a9cbbed | grep '^+'
```
- Every `+` commit and every dirty file is behaviour the service would lose. The owner decides each one before unifying.

**Backup** (code only, never `.env` content):
```bash
B=/mnt/zer0models/z0-wt/wiring/backups/z0int-canonical-20261003; mkdir -p $B
git -C ~/tmp/z0int-canonical rev-parse HEAD > $B/HEAD
git -C ~/tmp/z0int-canonical status --porcelain > $B/status.txt
git -C ~/tmp/z0int-canonical diff --binary HEAD > $B/dirty.patch
test -e ~/.config/z0intelligence/service.env.bak-z0wiring-20261003 || cp -a ~/.config/z0intelligence/service.env ~/.config/z0intelligence/service.env.bak-z0wiring-20261003
```
Never stash or reset the canonical tree: it is the rollback target.

**Steps:**
1. Change only `Z0INT_PYTHON` in `service.env` to `/mnt/zer0models/z0-wt/venv-wiring/bin/python`.
2. Keep `automatic.json` hermes and dsh off.
3. The owner runs `systemctl --user restart z0intelligence.service`.

**Check:**
- The service is `is-active`.
- The journal has no tracebacks.
- One route_worker call from an existing client and one OMP turn still route.

**Rollback:**
```bash
cp -a ~/.config/z0intelligence/service.env.bak-z0wiring-20261003 ~/.config/z0intelligence/service.env && systemctl --user restart z0intelligence.service
```
