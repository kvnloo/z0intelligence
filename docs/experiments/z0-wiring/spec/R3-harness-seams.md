# R3: Harness inventory and integration seams (host <local-host>, 2026-10-03)

Surveyor R3 of 4 (read-only). Extends `/mnt/zer0models/z0-wt/wiring/prior-findings.md`; it does not redo those audits.
Everything below was read from this host's files, `git -C ... show/grep`, read-only `sqlite3 "file:...?mode=ro"`,
`systemctl --user cat/list-*` and `gh ... view/list`. **INFERRED** marks anything not directly verified.

Owner direction applied throughout (2026-10-03): no harness gets core code changes. Every harness integrates only
through its own extension seam (plugin / hook / extension / MCP / z0 HTTP API). Integration code lives in z0-owned repos
(z0intelligence `harness-adapters/` / `omp-extensions/`, or a standalone plugin repo such as kvnloo/bend-native).
Turning it on is a reversible config or install step.

**Answer to the relayed owner question ("it shouldn't require any changes to hermes directly, ya? plugin / mcp / api?"):
yes, verified.** Upstream Hermes's plugin API covers per-turn capture (`pre_llm_call`/`post_llm_call`/`on_session_end` and
9 more hooks) and memory injection (`pre_llm_call` may return `{"context": ...}`, which is injected into the user message;
`agent/turn_context.py:781`), and so do memory-provider plugins and `mcp_servers`. `hermes plugins install` supports
`--ref <sha>` and a repo subdirectory. kvnloo/bend-native ran on Hermes with no core change. Its CONTRACT lane measured
0 Hermes-built request divergences between plugin off and shadow, and zero footprint when the plugin is disabled. The
only z0 code still inside a Hermes fork is `lab/z0_hermes_observer` (hermes-agent#385-387, fork merge `0d60437a`). It is
already vendored byte-for-byte into bend-native and should be retired from the fork. The same holds for every other
harness: none needs a core change (see section 4).

---

## 1. Inventory at a glance

"Last used" comes from file mtimes (now) and agentsview. Note that **agentsview has not synced since 2026-10-01 ~07:37Z**:
sessions.db mtime is 10-01 02:37 CDT, and no daemon, unit or timer exists.

| Harness | Installed (version, path) | Active? last used (fs / agentsview) | Transcripts | agentsview ingests? | Extension mechanism | Current z0 wiring | z0 tree it points at |
|---|---|---|---|---|---|---|---|
| Claude Code | 2.1.288 `~/.local/bin/claude` -> `~/.local/share/claude/versions/2.1.288`; `CLAUDE_CONFIG_DIR=~/.claude-home` | **Y** 10-03 01:54 / 10-01 00:52Z | `~/.claude-home/projects/**.jsonl` (8 main + 902 subagent files since 09-29) | **NO for claude-home** (108 rows, all `~/.claude/projects`) | plugins (marketplace), hooks, MCP, skills | plugins `z0intelligence@z0intelligence` (cache @0159808) + `z0-obspack`; env `Z0INT_PYTHON`, `Z0INT_CLAUDE_CODE_PACKET=1` | venv-claude-code -> **shadow-loop worktree** (`feat/shadow-loop-v0` 6fee859) |
| Codex CLI | 0.153.4 (musl standalone, offload path) | **Y** 09-30 21:53 / 10-01 02:53Z | `~/.codex/sessions/YYYY/MM/DD/rollout-*.jsonl` | yes (1,690) | plugins (`.codex-plugin`, also reads `.claude-plugin`), hooks (`features.hooks=true`), MCP | plugin `z0intelligence@personal` (MCP route_worker only); `mcp_servers.agentsview` | `~/plugins/z0intelligence/scripts/server.py` (not git) -> **`.work/z0intelligence/src`** (78869d9, 44 dirty) |
| OMP (oh-my-pi) | @oh-my-pi/pi-coding-agent 18.4.2 (bun global) | **Y** 10-01 03:24 / 10-01 07:10Z | `~/.omp/agent/sessions` -> `/workspace/kvn-home/.omp/agent/sessions/*.jsonl` | yes (2,485) | TS extensions (`~/.omp/agent/extensions/*`), `mcp.json`, plugins, memory backend | 8 symlinked z0 extensions + local exts; automatic.json `omp: on` | **z0int-canonical** (a9cbbed, dirty); bridge worker = system python3 3.14.7 + `PYTHONPATH=canonical/src` |
| Hermes | wrapper `~/.local/bin/hermes` -> live venv (version not read: live install off-limits) | **Y** (7 hermes user services running) / 10-01 03:54Z (profile `clean`) | `$HERMES_HOME/profiles/{clean,chiefstaff,intake}/state.db` | yes (1,789) | native plugins (`plugin.yaml` + `register(ctx)`), memory-provider plugins, `mcp_servers`, shell hooks | `plugins.enabled` includes `hermes-z0intelligence` (pre_llm_call automatic); automatic.json `hermes: off`; **no capture** (`~/.z0int/state/hermes/` absent) | unknown (live plugin dir not inspected); service PYTHONPATH includes `~/zer0/oss/hermes-jev-skills` |
| DSH | npm @deepseek-ai/dsh 0.1.5-rc.2 (`/usr/bin/dsh`; wrapper `~/.local/bin/dsh` does a BWS refresh; **never run**) | **Y** 09-30 12:18 / **not ingested** | `~/.dsh/sessions/**/session.v3.jsonl.zstd` (203) | **NO**: installed agentsview v0.39.0 lacks the DSH parser (v1 from v0.41.0, v3 only in v0.44.0, e6627ee7 / agentsview#1792) | cordis plugins (profile `package.json` bundles + `cordis.patch.yml` inserts), MCP client plugin | web profile: `memory-agentsview` MCP, `intelligence-z0intelligence` MCP, `automatic-z0intelligence` (dsh-z0intelligence), bundle `hermes-jev-dsh` | MCP: openjev venv python + **`.work` tree**; adapter symlink -> `.work/.../harness-adapters/dsh-z0intelligence`; hermes-jev-dsh -> hermes-jev-skills `hermes-jev/omp-adapter` 9ea5777 |
| OMO | omo-ai 5.0.1 (+ @code-yeongyu/senpi 2026.9.27) | **Y** 09-29 16:50 / 09-27 13:32Z | `~/.omo/agent/sessions/**.jsonl` | yes (13; stale) | pi-style JS extensions (`~/.omo/agent/extensions/*.js`), `mcp.json`, skills | `mcp.json`: agentsview, recall (`oss/tools/recall_mcp.py`, untracked), z0intelligence (`Z0INT_HARNESS=omo`, 11501); skills from canonical + `~/plugins` | **`.work` tree** (via the same server.py) + canonical skills |
| Grok CLI | 1.0.46 (`~/.grok/downloads/grok-1.0.46-linux-x86_64`) | **Y** 09-30 23:13 / 10-01 03:10Z | `~/.grok/sessions/<cwd>/<id>/summary.json` + messages | yes (2,411) | hooks (`~/.grok/hooks/*.json`, config.toml, and **by default `~/.claude/settings.json` + `~/.cursor/hooks.json`**), plugins/marketplace, MCP | `mcp_servers.agentsview` only | none |
| antigravity-cli (`agy`) | ELF `~/.local/bin/agy` (version not probed) | partial: 10-01 02:13 / 09-27 05:29Z | `~/.gemini/antigravity-cli/conversations/*.db`, `implicit/*.pb` | yes (107) | `mcp_config.json`, `hooks.json` (binary strings; **INFERRED**), settings.json | none (also used *by* OMP via the local `agy-executor` extension) | none |
| OpenCode | 1.18.23 (pacman) | low: opencode.db mtime 09-29 18:41 / 09-12 | `~/.local/share/opencode/opencode.db` | yes (54) | JS plugins `~/.config/opencode/plugins/*.js`; `mcp` in opencode.jsonc | none (3 axi ambient-context plugins) | none |
| Gemini CLI | 0.50.0 (pacman) | **N** 08-26 (tmp) / 07-31 | `~/.gemini/tmp/<hash>/chats/*.json` | yes (4) | hooks in settings.json (BeforeAgent/AfterAgent/SessionStart/SessionEnd), extensions, MCP | none (agent-deck hooks only) | none |
| Kimi Code | `~/.kimi-code/bin/kimi` (ELF) | **N** 07-14 | `~/.kimi-code/sessions/**/wire.jsonl` | yes (121) | zip plugins (`plugins/installed.json`), `mcp.json`, Pre/PostToolUse hooks (strings, **INFERRED**), `KIMI_CODE_HOME` | none | none |
| Cursor (IDE + cursor-agent) | cursor-bin 3.17.21; cursor-agent 2026.09.10-fd3934a | **N** 07-24 | `~/.cursor/projects/**/agent-transcripts/*.jsonl` | yes (9) | `~/.cursor/hooks.json` (absent), `mcp.json` (absent), plugins dir | none | none |
| (ChatGPT export) | n/a | 08-25 | imported | yes (2,502) | n/a | n/a | n/a |

Activity ranking for wiring priority (agentsview session counts plus recency): **OMP, Grok, Hermes, Codex, Claude Code, DSH,
OMO** are live. agy is intermittent. OpenCode, Gemini, Kimi and Cursor are dormant: defer them, and let agentsview
post-hoc capture cover them.

---

## 2. z0int trees, live service, systemd

### 2.1 Which tree each wiring imports (extends prior-findings "4 divergent trees" to a 5th consumer path)

| Tree | Branch @ head | State | Who imports it |
|---|---|---|---|
| `~/tmp/z0int-canonical` | `feat/promote-local-cognition-extension` a9cbbed (closed PR #35) | 4 modified + 7 untracked (julia backend); **22 behind, 1 ahead of origin/master 0159808** | **live z0 service** (`PYTHONPATH=~/tmp/z0int-canonical/src:~/zer0/oss/hermes-jev-skills` from service.env); all OMP z0 extensions (symlinks); OMP bridge worker; OMO skills |
| `~/tmp/openjev` | `feat/steal-sweep` c76f621 (PR #21 open) | clean | **its venv's python** runs the live service, and the DSH intelligence MCP; the editable install points at openjev/src, but PYTHONPATH wins for the service |
| `/mnt/zer0models/workspace/zer0/oss/.work/z0intelligence` (= `~/zer0/oss/.work/...`) | `feat/local-cognition-portfolio` 78869d9 | 44 dirty | Codex + OMO MCP (`~/plugins/z0intelligence/scripts/server.py` does `sys.path.insert(0, '.work/z0intelligence/src')`); DSH intelligence MCP (`PYTHONPATH`); DSH `dsh-z0intelligence` adapter (node_modules symlink) |
| `/mnt/zer0models/z0-wt/shadow-loop` | `feat/shadow-loop-v0` 6fee859 (pushed, no PR) | clean | Claude Code hooks (`venv-claude-code`, editable `openjev_phase1` -> shadow-loop/src); `~/.z0int/bin/python` symlink |
| `/mnt/zer0models/z0-wt/z0intelligence` | master 0563ed7 local; **origin/master 0159808** | clean | nothing live; Claude plugin *files* cached from 0159808 |

Drift that matters for wiring. Live vs origin/master, compared by sha256:
- `omp-extensions/z0int-bridge/index.ts`, `z0int-intelligence/index.ts`, `src/z0int/bridge/worker.py`,
  `intelligence_service.py` and `automatic.py` all **differ**.
- `local-cognition`, `openjev` and `flyforge-jev` extensions are the **same**.
- On origin/master, `DecisionOpportunity` is emitted only by `claude_code.py`, `hermes_decisions.py` and
  `harness-adapters/hermes-z0int-decisions/`. OMP (oh-my-pi#109) and DSH (z0int#62) emit none.

### 2.2 Live z0 service (`systemctl --user cat z0intelligence.service`, read only, never called)
- Unit `~/.config/systemd/user/z0intelligence.service` -> `~/.config/z0intelligence/z0intelligence.service`. Active since
  Tue 2026-09-29 10:27:25 CDT, MainPID 5889.
- `ExecStart` = live **Hermes venv python** `dsh-env-exec.py --allow GROQ/CEREBRAS/DEEPSEEK/OPENROUTER/AI_GATEWAY/NVIDIA_API_KEY --
  ~/tmp/openjev/.venv/bin/python -m z0int.intelligence_service --bind 127.0.0.1 --bind 172.19.0.1`,
  `WorkingDirectory=~/tmp/z0int-canonical`.
- **Not on master.** It imports z0int-canonical/src (a9cbbed + dirty) via service.env PYTHONPATH. That tree lacks
  decision_opportunity, state_packet, outcome_observation and memory_contract (prior-findings).
- Disclosure: I grep'd only the *key names* of `~/.config/z0intelligence/service.env` (Z0INT_PYTHON, Z0INT_BIND, Z0INT_BIND_2,
  PYTHONPATH, OMP_NUM_THREADS, MKL_NUM_THREADS, HF_HUB_OFFLINE, HERMES_HOME) and printed only the PYTHONPATH value. The file
  holds no credential keys (credentials are injected by dsh-env-exec).

### 2.3 systemd user units/timers (list/cat only)
- `z0intelligence.service` (active).
- `z0-farm-llama.service` (active; llama.cpp router on tailnet 100.113.138.100:11530, OFFLOAD tier).
- `quackles-arbiter.timer` (every 30 s) -> `quackles-arbiter.service`, which unloads idle llama router children. This is
  RAM management, not unified memory.
- `kvnloo-fork-sync.timer` (daily fork sync, non-force). This is a second reason to keep integration code out of harness forks.
- `zer0-storage-autotier.timer`.
- 7 hermes units running (cluster, web, mesh-keel, intake-progress-watch, voice-web, 2 proxies); `hermes-gateway` inactive.
- **No agentsview unit or timer** (sync and daemon are not running).

---

## 3. Cross-cutting findings (these shape the build plan)

1. **Four seam families cover all 12 harnesses with no core change:**
   - (a) **Claude-compatible command hooks**: Claude Code, Codex, Grok, and Cursor-compat. Codex 0.153.4 hook payloads carry
     `hook_event_name`, `transcript_path`, `turn_id`, `last_assistant_message`, `stop_hook_active` and
     `hookSpecificOutput.additionalContext`, and the binary knows `CLAUDE_PLUGIN_ROOT` and `.claude-plugin/plugin.json`.
     Grok reads `~/.claude/settings.json` hooks by default and sets `GROK_HOOK_EVENT` / `GROK_SESSION_ID`. Gemini (BeforeAgent /
     AfterAgent), Kimi and agy are close cousins (**INFERRED**).
   - (b) **pi-family TS/JS extensions**: OMP and OMO/senpi, with the same event names: input, before_agent_start, turn_end,
     agent_end, tool_result, context, session_*.
   - (c) **Hermes Python plugin** (`register(ctx)` + VALID_HOOKS).
   - (d) **DSH cordis plugin** (`ctx.on('agent/request' | 'llm/stream' | 'agent/pre-step' ...)`).
   - OpenCode JS plugins form a fifth, minor family.

   One z0-owned **generic hook adapter** (`python -m z0int.<hook_adapter> --harness <id> {prompt,stop,session-start}`) can
   serve family (a). Today `claude_code.py` hardcodes `HARNESS='claude-code'`, and `loop_export.py`/`outcome_verifier.py`
   hardcode Claude transcripts (`HARNESS = 'claude-code'`, lines 38/46).
2. **Harness identity must come from the payload or env, never from the hook location.** Grok fires `~/.claude/settings.json`
   and `~/.cursor/hooks.json` hooks by default (`[compat.claude] hooks = true`). Any z0 hook placed in those legacy files
   would be attributed to the wrong harness. `~/.claude/settings.json` today holds only the axi SessionStart hooks; the
   z0 Claude hooks live in the plugin, so there is no collision yet.
3. **agentsview is the natural uniform transcript and outcome reader, but it is stale and partially blind:**
   - (i) There is no sync daemon or unit; the last sync was 10-01.
   - (ii) The current Claude config dir `~/.claude-home/projects` is not ingested. Only `~/.claude/projects` is, because
     sync ran without `CLAUDE_CONFIG_DIR`. v0.39.0 supports a `claude_project_dirs` key in config.toml, so this is a config fix.
   - (iii) DSH is not parsed at v0.39.0 and needs agentsview >= v0.44.0 (install step).
   - (iv) The installed agentsview is v0.39.0 (uv tool), but the ro clone is at 430b853c (09-28, past v0.44.0).
   - agentsview MCP tools (read-only): search_sessions, search_content, query_recall, list_sessions, get_session_overview,
     get_messages, get_usage_summary.
   - Session-level `outcome`, `outcome_confidence`, `health_score` and `tool_failure_signal_count` columns exist and can
     serve as weak labels for every harness (**INFERRED** usefulness).
4. **Universal post-hoc capture fallback.** A z0-owned read-only importer over agentsview sessions.db (memory-control-plane
   "next slice (1): EventLog/AgentsView importers emit EventIdentity") can mint DecisionOpportunity-lite and outcome
   records for *every* ingested harness with zero harness config. This covers dormant harnesses. It cannot replace live
   capture: it has no State Packet at decision time and only sync latency. Make it the floor; per-harness hooks are the
   live layer.
5. **Pull vs push memory.** agentsview MCP is already configured in Codex, OMP, OMO, Grok and DSH, but **not in Claude Code**
   (user MCP has only chrome-devtools) and **not usable in Hermes as configured** (`toolsets` contains `no_mcp`; **INFERRED**
   meaning). MCP is pull-only: the model must call it. "Unified memory wired" therefore needs a push seam per harness:
   - Claude/Codex: `UserPromptSubmit`/`SessionStart` `additionalContext`.
   - Hermes: `pre_llm_call` returning `{"context"}`.
   - OMP/OMO: `before_agent_start` / `context`.
   - DSH: `agent/pre-step` / `system-prompt/assemble`.
   - OpenCode: `experimental.chat.system.transform`.
   - Grok is the exception. `UserPromptSubmit` stdout is discarded and `SessionStart` stdout is ignored (docs
     `10-hooks.md:113,506`), so push is limited to `PostToolUse.additionalContext` or a z0-written `$GROK_HOME/rules/*.md`
     file plus MCP.
6. **Integration code currently outside z0-owned, pushed repos (must move, per owner direction):**
   - `~/plugins/z0intelligence` (Codex/OMO MCP shim + skills; not git).
   - `/mnt/zer0models/workspace/zer0/oss/tools/recall_mcp.py` (+ recall.py, statepack.py; not git).
   - `~/tmp/omp-ext-{cognitive-state,z0-live-runtime,agy-executor}` (git, **no remote**).
   - `~/.omp/agent/extensions/{hermes-memory,tencentdb-memory,typesafe-jev}` (loose dirs).
   - In **harness forks**: oh-my-pi `origin/feat/cognitive-state-shadow-rfc79` (7fbdce67a, 3ae2fe265: example extension
     under `packages/coding-agent/examples/extensions/cognitive-state`, oh-my-pi#107) and hermes-agent `lab/z0_hermes_observer`
     (#385-387, merged into fork stack branches via 0d60437a).
   - hermes-jev-skills `hermes-jev/omp-adapter` 9ea5777 (pushed branch, unmerged; writes DSH `~/.dsh/jev/receipts.jsonl`).
7. **Capture volume today.** Only Claude Code writes canonical `~/.z0int/state/<harness>/` files (opportunities 6, outcomes 19,
   outcomes_verified 9 lines at survey time). OMP writes legacy v1 spine files (decisions.jsonl and outcomes.jsonl last
   written 10-01 03:23). DSH writes a private schema. Hermes, Codex, Grok and OMO write nothing.
   **INFERRED:** Claude Code workflow-subagent turns are not captured. `UserPromptSubmit` does not fire for subagents and
   the plugin hooks no `SubagentStop`. There are 902 subagent transcripts in claude-home since 09-29.

---

## 4. Per-harness seams

Template per harness: **active** | **capture seam** (per-turn DecisionOpportunity + outcome, no core change) | **memory
seam** (unified-memory read/inject) | **test seam** (isolated E2E without the live install) | **live activation** (exact
reversible config change + backup path). Backup convention: `<file>.bak-z0wiring-20261003` (matches existing
`*.bak-20261003-z0`, `*.bak-pre-agentsview-*` patterns).

### 4.1 Claude Code
- **Active:** Y, 10-03 01:54.
- **Current wiring:** marketplace `kvnloo/z0intelligence` (`.claude-plugin/marketplace.json`), plugin
  `harness-adapters/claude-code-z0intelligence`:
  - hooks UserPromptSubmit -> `-m z0int.claude_code prompt`; Stop/SessionEnd -> `stop`; SessionStart -> `session-start`;
  - `.mcp.json` `z0intelligence` (`bin/z0int-mcp`, `Z0INT_SHARED_ONLY=1`);
  - `z0-obspack` (PostToolUse Bash -> `z0int.claude_code_obs`).
  - Installed 10-03 02:48Z @0159808. Hook code runs from `Z0INT_PYTHON=/mnt/zer0models/z0-wt/venv-claude-code/bin/python`
    (shadow-loop src).
  - automatic.json has no `claude-code` key, so `automatic.handle_event` returns native and the live service is not called.
- **Capture seam:** in place (UserPromptSubmit -> async `on_opportunity` -> `~/.z0int/state/claude-code/opportunities.jsonl`;
  Stop -> outcomes.jsonl; `z0int outcomes verify` -> outcomes_verified.jsonl). Gap: add `SubagentStop` (and a subagent
  opportunity path) to the plugin's hooks.json.
- **Memory seam:**
  - SessionStart/UserPromptSubmit `hookSpecificOutput.additionalContext`. Already used for the State Packet
    (`Z0INT_CLAUDE_CODE_PACKET=1`, `claude_code.py:203-209`) and automatic context (`:108`).
  - Add a unified-memory brief here, plus an agentsview/recall MCP entry in the plugin's `.mcp.json`. Plugin-owned, so it
    needs no user settings edit.
- **Test seam:**
  - Fake hook JSON on stdin to `python -m z0int.claude_code {prompt,stop,session-start}` with `Z0INT_HOME=<scratch>`
    (pattern in `tests/test_claude_code_adapter.py`).
  - Real E2E: `CLAUDE_CONFIG_DIR=<scratch> claude -p ... --plugin-dir <worktree>/harness-adapters/claude-code-z0intelligence`.
    This uses the subscription, so it needs owner approval.
- **Activation:** `/plugin marketplace update z0intelligence` then reinstall at the reviewed SHA. Alternatively, change
  `env` in `~/.claude-home/settings.json` (backup already present: `settings.json.bak-20261003-z0`). Revert:
  `enabledPlugins."z0intelligence@z0intelligence": false`.

### 4.2 Codex CLI
- **Active:** Y, 09-30 21:53.
- **Current wiring:**
  - `[plugins."z0intelligence@personal"] enabled = true`. Cache
    `~/.codex/plugins/cache/personal/z0intelligence/0.1.0+codex.20260927001037/` has only `.mcp.json` (live Hermes venv
    python + dsh-env-exec + `~/plugins/z0intelligence/scripts/server.py` -> `.work` tree) and a `delegate` skill.
  - `[mcp_servers.agentsview]` (with `AGENTSVIEW_DATA_DIR`).
  - `[features] hooks = true`. `~/.codex/hooks.json` has only axi SessionStart hooks.
- **Capture seam:** Codex hooks `UserPromptSubmit`, `Stop`, `SessionEnd`, `SubagentStop`, `PreCompact`, Pre/PostToolUse,
  with Claude-compatible payloads (binary strings).
  - Ship them as **plugin hooks** (`hooks/hooks.json` in the plugin; binary: "plugin hooks config", "failed to trust
    materialized plugin hooks"), from a z0int `harness-adapters/codex-z0intelligence/` (or reuse the Claude plugin dir:
    Codex reads `.claude-plugin/plugin.json`, **INFERRED**).
  - Invoke the generic hook adapter with `--harness codex`. Outcome verification reads `~/.codex/sessions` rollouts (or
    agentsview).
- **Memory seam:** UserPromptSubmit/SessionStart `additionalContext` (**INFERRED** Claude parity) plus the existing
  agentsview MCP.
- **Test seam:**
  - Fake payloads to the adapter.
  - `CODEX_HOME=<scratch> codex exec "<prompt>"` with a local OSS provider (`--oss`, **INFERRED**; otherwise paid).
- **Activation:** install the z0 plugin from the kvnloo/z0intelligence marketplace into `~/.codex/config.toml`
  (`[plugins."<name>@<marketplace>"] enabled = true`). Codex records a `trusted_hash` under `[hooks.state]` on first
  trust. Backup `~/.codex/config.toml.bak-z0wiring-20261003` (prior backup `config.toml.bak-agentsview-20260922T131223`
  exists). Revert: `enabled = false`. Repoint `~/plugins/z0intelligence` off the dirty `.work` tree.

### 4.3 OMP (oh-my-pi)
- **Active:** Y, 10-01 03:24.
- **Current wiring:**
  - `~/.omp/agent/extensions/` symlinks -> `~/tmp/z0int-canonical/omp-extensions/{z0int-bridge, z0int-intelligence,
    local-cognition, openjev, openjev-06b, vllm-jev, flyforge-jev, flyforge-recovery}`.
  - Plus `hermes-jev` (-> hermes-jev-skills omp/extension) and local non-repo `agy-executor`, `cognitive-state`,
    `z0-live-runtime`, `hermes-memory`, `tencentdb-memory`, `typesafe-jev`.
  - `z0int-bridge` spawns `${Z0INT_PYTHON:-python3} -u -m z0int.bridge.worker` with `PYTHONPATH=<owning tree>/src` (protocol
    `z0int.bridge.v2`; `~/.z0int/runtime/bridge-current.json` gen 1, 10-01).
  - `z0int-intelligence` fetches `Z0INT_SERVICE_URL || http://127.0.0.1:11501/v1/intelligence` (live service).
  - `~/.omp/agent/mcp.json`: agentsview. `config.yml`: `memory.backend: local`, `autolearn.enabled: true`.
- **Capture seam:** extension events already hooked by z0int-bridge (`input`, `before_agent_start` -> `turn_open`,
  `turn_end`/`agent_end` -> `turn_close` with `execution_completed: true`, `verified_success` null). Missing: emitting
  canonical `DecisionOpportunity` + outcome records (oh-my-pi#109, z0int#62) instead of only `z0int.decision_receipt.v1`.
  This is an extension/worker change in the z0int repo only.
- **Memory seam:** `before_agent_start` result `{message, systemPrompt[]}` and the `context` event result `{messages}` (oh-my-pi
  `extensibility/extensions/types.ts:1246,1293`). #107's cognitive-state Stage B uses `context`. agentsview MCP is present.
- **Test seam:**
  - `PI_CODING_AGENT_DIR=<scratch>` (utils `dirs.ts:481`) with `omp -p --no-session --no-extensions -e <ext> --mode json`,
    against a private local model endpoint.
  - Unit: bun test with a fake `pi` object (`omp-extensions/local-cognition/index.test.ts`).
- **Activation:** re-point the symlinks from the dirty canonical tree to a pinned z0int worktree at the reviewed SHA:
  `ln -sfn <wt>/omp-extensions/<ext> ~/.omp/agent/extensions/<ext>`. Backup:
  `ls -l ~/.omp/agent/extensions > ~/.omp/agent/extensions.links.bak-z0wiring-20261003`. Revert: restore the old link targets.
  Set `Z0INT_PYTHON` for the bridge (today it is system python3 3.14).

### 4.4 Hermes (detail in section 5)
- **Active:** Y (services running; agentsview last profile `clean` 10-01).
- **Capture seam:** the native plugin hooks `pre_llm_call`, `post_llm_call`, `on_session_end`, `pre_approval_request`.
  z0int master already ships `harness-adapters/hermes-z0int-decisions` (writes canonical `~/.z0int/state/hermes/`).
  bend-native ships a 12-hook superset that writes to `$HERMES_HOME/plugin-data/bend/z0/`.
- **Memory seam:** `pre_llm_call` returns `{"context": "..."}`, which is injected into the user message
  (`agent/turn_context.py:781-836`, oversized output spilled to disk). This coexists with `memory.provider: memory_tencentdb`.
  Alternatives: a MemoryProvider plugin (single slot, which would displace TencentDB) or MCP (blocked by `no_mcp` in toolsets,
  **INFERRED**).
- **Test seam:**
  - `HERMES_HOME=<scratch>` (the wrapper only pins it when unset) + `hermes plugins install <path|url#subdir> --ref <sha>`
    + `hermes chat -q "<prompt>"` (`hermes_cli/_parser.py:231`) with a private local model.
  - Unit: z0int `tests/test_hermes_decisions.py` (13 tests driving the plugin with fake hook kwargs); bend-native
    `tests/test_native_workflow.py` + `scripts/smoke.py`.
  - Use a venv built from the fork worktree, not `~/.hermes/hermes-agent/venv`.
- **Activation:** see section 5.4.

### 4.5 DSH (never run the binary or wrapper)
- **Active:** Y, 09-30 12:18. Not in agentsview.
- **Current wiring** (`~/.dsh/profiles/web/cordis.patch.yml`, 62 lines, `patchReload: live`):
  - `memory-agentsview` (`@deepseek-ai/dsh-mcp-client` stdio `agentsview mcp`, `AGENTSVIEW_DATA_DIR`, 180 s timeout);
  - `intelligence-z0intelligence` (`~/tmp/openjev/.venv/bin/python -m z0int.intelligence_mcp`,
    `PYTHONPATH=.work/z0intelligence/src`, `Z0INT_SERVICE_URL=http://127.0.0.1:11501`, `failOnStartupError: true`);
  - `automatic-z0intelligence` (`name: dsh-z0intelligence` -> node_modules symlink into `.work`
    `harness-adapters/dsh-z0intelligence`, `ctx.on('llm/stream')`).
  - `package.json` bundles: dsh-base, dsh-web-app, pi2dsh, **hermes-jev-dsh** (link -> hermes-jev-skills `dsh/plugin`,
    `ctx.on('agent/request')`, writes `~/.dsh/jev/receipts.jsonl`, last 09-30 12:18), dsh-savings-dock, dsh-codex-bridge.
  - Other profiles: headless (`dsh-base`, `dsh-headless`), sdk-minimal, deepseek-sdk.
- **Capture seam:** a z0-owned cordis plugin (in z0int `harness-adapters/`) observing `agent/request` (middleware,
  `yield* next()`), `llm/stream`, `agent/turn-stopping`, `tools/post-execute`, `subagent/end`, `session/event`,
  `session/disposed`. It emits canonical records to `~/.z0int/state/dsh/`, the same record shape as Hermes and Claude
  (z0int#62).
- **Memory seam:**
  - `agent/pre-step` waterfall (pre-step admission seam declared in kvnloo/deepseek-harness `zer0.repo.yaml`, 70215673,
    deepseek-harness#4 docs).
  - `system-prompt/assemble`.
  - Durable inbox `inject()`.
  - agentsview MCP is already present.
- **Test seam:**
  - Unit: `apply(ctx)` with a fake cordis ctx recording `ctx.on` registrations (node test).
  - E2E: the deepseek-harness *source tree* headless profile tests (`apps/cli/tests/profiles/headless/*.expected.e2e.ts`)
    with `packages/test-support/llm-replay` (offline replay), `DSH_HOME=<scratch>` and `DSH_SNAPSHOT`. This needs owner
    approval: it runs DSH code, though never the `~/.local/bin/dsh` BWS-refresh wrapper.
- **Activation:** add `- insert: [{id: capture-z0intelligence, name: <pkg>}]` to `~/.dsh/profiles/web/cordis.patch.yml`
  and a `link:` dep plus bundle entry in `package.json`. Backup `cordis.patch.yml.bak-z0wiring-20261003`; earlier backups
  `.bak-pre-z0-mcp-*`, `.bak-pre-agentsview-*`, `.pre-z0intelligence-*` and `.pre-automatic-*` exist. Repoint the
  intelligence MCP and adapter off the `.work` tree.

### 4.6 OMO (omo-ai / senpi)
- **Active:** Y, 09-29.
- **Current wiring:** `~/.omo/agent/mcp.json` lists agentsview (lazy, `exposure: search`), recall (`recall_mcp.py`) and
  z0intelligence (route_worker, list_models; `Z0INT_HARNESS=omo`). `settings.json` skills come from
  `z0int-canonical/skills` and `~/plugins/z0intelligence/skills`. Extensions dir has only 4 OmO-generated builtin
  re-export shims (`tps.js`, `diff.js`, `files.js`, `prompt-url-widget.js`).
- **Capture seam:** a senpi extension (events in `senpi/dist/core/agent-session.js`: session_start, input, turn_start,
  turn_end, agent_start, agent_end, tool_call, tool_result, message_end, session_before_compact, session_shutdown). Reuse the
  OMP bridge via a one-line re-export shim, in the style of `tps.js`: `export { default } from "file:///<z0 wt>/..."`.
  Pi-API compatibility is **INFERRED**; senpi types were not checked.
- **Memory seam:** MCP (agentsview, recall present); `before_agent_start` (**INFERRED**, the pi-family API).
- **Test seam:** isolated agent dir env not verified (**INFERRED** pi-style `PI_CODING_AGENT_DIR`); unit with a fake `pi`.
- **Activation:** drop `~/.omo/agent/extensions/z0-capture.js` (a shim); delete it to revert.

### 4.7 Grok CLI
- **Active:** Y, 09-30 23:13.
- **Current wiring:** `~/.grok/config.toml` `[mcp_servers.agentsview]` only. No `~/.grok/hooks/`. `installed-plugins` is empty.
- **Capture seam:** global hooks `~/.grok/hooks/z0.json` (always trusted) or a Grok plugin (`grok plugin install <path>
  --trust`). Events: `UserPromptSubmit`, `Stop`, `StopFailure`, `StopCancelled`, `SessionEnd`, `SubagentStop`. Payloads
  are Claude-style JSON on stdin; env `GROK_HOOK_EVENT` / `GROK_SESSION_ID` identify the harness. Note the compat scan
  (finding 2).
- **Memory seam:** **no push via prompt hooks.** `UserPromptSubmit` stdout is discarded and `SessionStart` stdout is ignored.
  The options are `PostToolUse.additionalContext` (late), a z0-maintained `$GROK_HOME/rules/z0-memory.md` (static,
  always scanned) and the agentsview MCP (present). Grok's own memory is `memory-v2` / `GROK_MEMORY`.
- **Test seam:**
  - `GROK_HOME=<scratch> grok -p "<prompt>" --output-format json` (xAI, paid/OAuth, needs owner approval).
  - `GROK_CONFIG` overlay.
  - Fake payloads to the adapter.
- **Activation:** write `~/.grok/hooks/z0-capture.json`; `rm` it to revert. No backup is needed because the file is new.

### 4.8 antigravity-cli (agy), OpenCode, Gemini, Kimi, Cursor (dormant or low; defer)
- **agy:**
  - Seams: `~/.gemini/antigravity-cli/mcp_config.json` and `hooks.json` with PreToolUse/PostToolUse (binary strings,
    **INFERRED**), settings.json (model/permissions/trustedWorkspaces only).
  - Capture: post-hoc via agentsview (conversations/*.db and implicit/*.pb are ingested).
- **OpenCode:**
  - Seams: plugins `~/.config/opencode/plugins/*.js`. Proven hook: `experimental.chat.system.transform` (axi plugins), which
    serves as the memory seam. Capture hooks `event` (session.idle), `chat.message` and `tool.execute.after` are **INFERRED**.
  - MCP: `"mcp"` in opencode.jsonc (currently only `$schema`).
  - Test: `XDG_CONFIG_HOME`/`XDG_DATA_HOME` scratch + `opencode run` (**INFERRED**).
- **Gemini CLI:** settings.json hooks (BeforeAgent/AfterAgent/SessionStart/SessionEnd currently run `agent-deck hook-handler`).
  BeforeAgent `additionalContext` and extensions with `mcpServers` are **INFERRED**.
- **Kimi:** `KIMI_CODE_HOME`, `mcp.json`, zip plugins (`kimi-datasource`), Pre/PostToolUse (**INFERRED**).
- **Cursor:** `~/.cursor/hooks.json` (beforeSubmitPrompt, stop, afterAgentResponse...) and `~/.cursor/mcp.json`. Both are
  absent today.
- Recommended handling: agentsview post-hoc capture (finding 4) now; live hooks only when a harness becomes active again.

---

## 5. Hermes specifically

### 5.1 Plugin mechanism (fork source `/mnt/zer0models/hermes-wt/bend-integration`, `exp/bend-stack-integration-20261002` @ad31bbf079 = upstream CI pin 50a6abca + 3 computer-use fixes; origin = NousResearch)
- **Discovery** (`hermes_cli/plugins.py:1-8`, `plugins_discovery.py:189-231`), later source wins:
  - bundled `<repo>/plugins/<name>/`;
  - user `$HERMES_HOME/plugins/<name>/`;
  - project `./.hermes/plugins/<name>/` (only with `HERMES_ENABLE_PROJECT_PLUGINS`);
  - pip entry points `hermes_agent.plugins`.
  - A directory plugin needs `plugin.yaml` + `__init__.py: register(ctx)`.
- **Opt-in:** `plugins.enabled` allow-list (missing = nothing enabled); `plugins.disabled` deny-list wins. Settings live in
  `plugins.entries.<name>.settings`, read with `ctx.get_config`.
- **ctx API used by z0 plugins:** `register_hook`, `register_tool`, `register_cli_command`, `register_skill`, `on_unload`,
  `get_config`.
- **VALID_HOOKS** (`plugins.py:109-135`):
  - tool: pre/post_tool_call, transform_terminal_output, transform_tool_result;
  - LLM: transform_llm_output, pre_llm_call, post_llm_call, on_stream_start/delta/end, on_interim_message, pre_verify;
  - API: pre/post_api_request, api_request_error, pre/post_auxiliary_call, transform_api_error_classification;
  - session: on_session_start/end/finalize/reset;
  - other: on_skill_lifecycle, subagent_start, subagent_stop, and more.
- **Install** (`plugins_cmd_install.py:458`, `plugins_cmd.py:281-300`): `hermes plugins install <catalog-name | owner/repo |
  git URL | URL#subdir | https://github.com/o/r/tree/<ref>/<subdir>> [--ref <40-char SHA>] [--enable] [--force]`. It is an
  atomic clone-scan-publish into `$HERMES_HOME/plugins/<name>` and asks dependency consent unless `--yes-deps`/`--no-deps`.
- **Context injection:** `pre_llm_call` results of `{"context": str}` or a bare string are joined and injected into the
  **user message**, never the system prompt (`agent/turn_context.py:781-836`).

### 5.2 MCP and memory providers
- **MCP:** `mcp_servers` map in config.yaml (`hermes_cli/mcp_config.py:209-282`; `hermes mcp` CLI). The live config has an
  `mcp_servers` key (values not read). `toolsets` = `["kanban","memory","session_search","clarify","no_mcp"]`, which
  **INFERRED** keeps MCP tools out of the default toolset.
- **Memory providers** (`plugins/memory/__init__.py`), bundled-first precedence: bundled `plugins/memory/<name>` (byterover,
  holographic, mem0, openviking, retaindb) > user `$HERMES_HOME/plugins/<name>` > project > entry point
  `hermes_agent.memory_providers`.
  - Exactly one is active via `memory.provider`.
  - `MemoryProvider` ABC (`agent/memory_provider.py:84-203`): `is_available, initialize, system_prompt_block, prefetch,
    queue_prefetch, recall_status, sync_turn, get_tool_schemas, handle_tool_call, on_turn_start, on_session_end,
    on_session_switch, on_pre_compress, on_delegation, on_memory_write, backup_paths`.
- **Live** (`~/.hermes/config.yaml`, allowed keys only): `memory.provider: memory_tencentdb`, `memory_enabled: true`,
  `user_profile_enabled: true`, `flush_min_turns: 6`, `nudge_interval: 10`. `plugins.enabled: [hermes-handoff,
  dashboard_auth/basic, hermes-agent-cluster, kanban, hermes-z0intelligence]`, `disabled: []`.
  - `memory_tencentdb` is not in any kvnloo clone, the fork or z0int (**INFERRED**: a third-party entry-point package).
  - The live `hermes-z0intelligence` plugin (z0int `harness-adapters/hermes-z0intelligence`) runs
    `python -m z0int.automatic event` as a **subprocess on every turn** (30 s timeout) and gets `native` back because
    automatic.json has `hermes: off`. This is a per-turn cost with no capture (**INFERRED** cost).
  - That adapter resolves `Path(__file__).resolve().parents[2]/'src'`, so it only works as a symlink into a z0int tree.
    Where it points was not inspected.
- **Profiles:** agentsview sees `profiles/{clean,chiefstaff,intake}/state.db`. Plugin enablement is per profile config.yaml,
  and the active profile (`~/.hermes/active_profile`) was not read. The build phase must target the active profile's
  config, which may not be `~/.hermes/config.yaml` (**INFERRED**).

### 5.3 kvnloo/bend-native as the Hermes capture vehicle (evaluated @e85e65e5, v0.4.0)

**What it is:** a standalone native Hermes plugin named `bend`.
- `register()` (`__init__.py`) registers 12 hooks (`provides_hooks` in `plugin.yaml`), the `bend_verify` tool, the
  `hermes bend` and `hermes z0` CLIs, and the `bend:workflow` and `bend:z0-stack` skills.
- It vendors, unchanged with sha256 pins (`stack/sources.json`): the hermes-agent `lab/z0_hermes_observer` (@0d60437a,
  #385-387), and z0int `state_packet.py`, `decision_opportunity.py`, `context_resolve.py`, `paths.py` and
  `harness-adapters/hermes-z0int-decisions/__init__.py` (as `stack/observer/decision_hooks.py`) @f80da337.
- **Verified:** the four z0 files are byte-identical to z0int **origin/master 0159808** (sha256 1aef6c69 / 6dc904d6 /
  04d3da59 / 77a5527f). There is no drift yet.
- **Activation is config-only:** `hermes plugins install kvnloo/bend-native --ref <sha>`, `plugins.enabled: [bend]`, and
  `plugins.entries.bend.settings.{stack_mode: shadow, stack_opportunities: true}`. The default is `off`.
- **Evidence:** CONTRACT H1 0/36 Hermes-built divergences; H6 zero footprint when disabled; E2E joins receipt->trace 17/17,
  opportunities 40/40 (SYNTHESIS.md sections 4 and 6).

| Criterion | bend-native `bend` plugin (shadow) | z0int master `harness-adapters/hermes-z0int-decisions` (`z0int-decisions`) |
|---|---|---|
| No Hermes core change | yes | yes |
| Hooks | 12 (llm, api attempt, aux, tool, subagent_stop, session) | 4 (pre_llm_call, post_llm_call, on_session_end, pre_approval_request) |
| Output location | `$HERMES_HOME/plugin-data/bend/z0/{events,opportunities}.jsonl` (per profile; **not** where `z0int outcomes`/loop export look) | canonical `$Z0INT_HOME/state/hermes/{opportunities,outcomes}.jsonl` (`z0int.hermes.opportunity_record.v0`, `z0int.hermes.turn_outcome.v0`) |
| Reducer runtime | vendored stdlib copy, Hermes's own python | spawns `Z0INT_PYTHON -m z0int.hermes_decisions opportunity` (config `$Z0INT_HOME/config/hermes.json`) |
| Coupling | capture is bundled with the Bend tool, CLI, skills and Lean/Bend expectations; Bend release cadence | single purpose |
| Tests | 6/6 + smoke 7/7; 5 preregistered lanes | z0int `tests/test_hermes_decisions.py` (13 tests incl. <5 ms hook, fail-open, bounded fan-out, trace parity) |
| Known bugs | 12 unfixed (P-1..P-12). For capture-only, the relevant ones are P-2 (`close()` vs full queue), P-3 (drops not persisted), P-7 (projection uses process cwd -> ACT on unknown facts), P-9 (State Packet persists README/commit text, undisclosed), P-10 (hooks registered even when `off`: dispatch cost 18->162 us p50 and every tool call routed through the fail-closed pre_tool_call gate), P-11. P-1 (default port 11501 = live service) bites only `hermes z0 runtime`; P-4/5/6 bite scoring/replay | uses process cwd for the repo too (**INFERRED**, same `classify`/projection pattern), so P-7 likely applies; no other known defects |
| Extra signal | API-attempt shadow question (#386, degenerate at 0/178 positives), Bend proof evidence receipts | none |
| Memory inject | none (shadow hooks always return None) | none |

**Recommendation:**
1. Make `z0int-decisions` the always-on Hermes capture vehicle. It already exists in a z0-owned repo, writes the canonical
   layout the shadow loop consumes, and is single-purpose.
2. Keep bend-native as the Bend proof + API-attempt lab plugin, in `stack_mode: off` until the plugin fix PR (SYNTHESIS
   section 11 step 1: P-1, P-2, P-4, P-5/6, P-7, then P-3, P-8..P-12) lands.
3. Do not enable both stacks' opportunity emission on one profile, or each turn produces two DecisionOpportunity rows.
4. For unified-memory inject, add a z0-owned `pre_llm_call` context provider (a new hook in `z0int-decisions`, or a sibling
   plugin) rather than displacing `memory_tencentdb`.
5. Retire `lab/z0_hermes_observer` from the hermes fork branches; bend-native is now its home.

### 5.4 Hermes live-activation step (for the build phase, active profile only)
1. Backup: `cp $HERMES_HOME/config.yaml $HERMES_HOME/config.yaml.bak-z0wiring-20261003`, where `$HERMES_HOME` is the active
   profile dir.
2. `hermes plugins install https://github.com/kvnloo/z0intelligence/tree/<sha>/harness-adapters/hermes-z0int-decisions --ref <sha> --enable`.
   Alternatively, symlink the reviewed worktree dir into `$HERMES_HOME/plugins/z0int-decisions` and add it to `plugins.enabled`.
3. Write `~/.z0int/config/hermes.json` = `{"python": "<pinned z0int venv python>", "opportunities": true}`.
4. Revert: `hermes plugins disable z0int-decisions` (or remove it from `plugins.enabled`), then restore the backup.

---

## 6. Suggested TDD order (seam-level; read with R1/R2/R4)

1. **Generic hook adapter** (z0int): `--harness` parameter and harness detection from env/payload (GROK_*, Codex `turn_id`,
   Claude `prompt_id`). Fixtures: one recorded payload per harness for prompt/stop/session-start, round-tripping to the
   same semantic record (z0int#62 acceptance).
2. **Outcome verification decoupled from Claude transcripts.** Read agentsview sessions.db read-only, or per-harness
   transcript readers. Red test: `loop_export` and `outcome_verifier` reject non-claude harnesses today.
3. **Hermes:** `z0int-decisions` + memory `pre_llm_call` context, tested in an isolated `HERMES_HOME`.
4. **OMP bridge:** emit DecisionOpportunity (oh-my-pi#109); repoint the extensions to a pinned worktree.
5. **DSH cordis capture plugin** (unit with a fake ctx; E2E via llm-replay only with owner approval).
6. **Codex/Grok** hook activation (plugin hooks / `~/.grok/hooks`).
7. **agentsview:** add `claude_project_dirs` (claude-home), upgrade to >= v0.44.0 for DSH, and add a z0-owned sync unit.
   That unit is a service start, so it needs owner approval.
8. **Post-hoc importer** over agentsview for dormant harnesses.

---

## 7. Open questions / INFERRED items to verify in build phase
- The active Hermes profile, and where the live `hermes-z0intelligence` plugin dir resolves (live install was not inspected).
- Whether `no_mcp` in Hermes `toolsets` disables `mcp_servers` for the active platform.
- Codex: are plugin-bundled hooks and `.claude-plugin` manifests honored at 0.153.4, and is `additionalContext` honored on
  UserPromptSubmit? (Binary strings suggest yes.)
- OMO/senpi extension API parity with OMP (`before_agent_start`, `context`), and an OMO isolated-dir env var.
- agy/Kimi/Gemini/Cursor hook payloads and context-injection support.
- Claude Code subagent turns: confirm `UserPromptSubmit` does not fire for them and add `SubagentStop` capture.
- `memory_tencentdb` provenance (pip package) and its 8 s critical-path cost (prior-findings).
- The live service runs a tree lacking `decision_opportunity`/`state_packet`/`memory_contract`. Any adapter that calls
  `/v1/automatic` gets the old semantics until the service is repointed to master; doing that is an owner decision.
