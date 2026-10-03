# AgentsView v0.39.0 → v0.44.0 + OMO (2026-10-03)

Upgrade of the unified conversation archive (FTS5 memory substrate) on `<local-host>`.
Live data dir: `/mnt/zer0models/sft-svlm/data/agentsview` (`~/.agentsview` is a symlink to it).

## Status

| Check | Result |
|---|---|
| `PRAGMA user_version` == binary `dataVersion` | 113 == 113 |
| Agents before → after | none lost (table below) |
| `omo` present | 14 sessions |
| `deepseek-harness` (DSH) present | 203 sessions (v0.44 ships the parser; reads `~/.dsh/sessions/**/session.v3.jsonl.zstd`) |
| `~/.claude-home` indexed | 888 sessions (+108 from `~/.claude`) |
| `require_auth` | `true`; the original 356 bytes of config.toml are byte-identical |
| FTS / `agentsview mcp` | FTS `MATCH` ok; MCP `search_content` returns OMO hits |
| Freshness | newest session row 2026-10-03T10:04Z at switch |
| Timer | `agentsview-sync.timer`, every 30 min, enabled |

## Build provenance

- Fork branch: `kvnloo/agentsview` `build/v0.44.0-omo-20261003` @ `987293f4`
  - `v0.44.0` (`413a87f7`)
  - `c648fcb9` cherry-pick of `f2773a80` "feat: index OMO sessions as omo"
  - `34d99a07` cherry-pick of `c275a2d6` "fix(parser): drop OMO entrypoint stamp…"
    (`feat/omo-source` sits on upstream main + 82; the only conflicts were the
    pi-subagents hunks, which don't exist on v0.44.0, plus the changelog)
  - `987293f4` **local fix**: stop retrying Codex forks whose parent is gone for good (see Issues)
- Clone: `/mnt/zer0models/z0-wt/wiring/agentsview-build/agentsview`
- Binary: `/mnt/zer0models/agentsview-builds/v0.44.0-omo-987293f4/agentsview`
  (`v0.44.0-3-g987293f4`, sha256 `2c7a6dbc5c037d67353389a412ab23c16b5c8819be51f646a9dd1631bee8ee1b`;
  `-tags fts5 -trimpath -s -w`, with the frontend embedded)
- Superseded build without the Codex fix: `/mnt/zer0models/agentsview-builds/v0.44.0-omo-34d99a07/` (do not use)
- Tests: `go test -tags fts5 ./...` → 60 packages ok. The only failure is
  `internal/sync TestCodexCheckpointTruncationFallsBackToFullParse`, which also fails
  on pristine v0.44.0 on this host (pre-existing). The OMO fixture tests
  (`TestOMOProviderKeepsPiLineageAndOMOIdentity`, `TestOMOSyncIndexesOneOMORowAndStaysIdempotent`)
  and the new `TestCodexForkWith{Long,Recently}MissingParent*` tests pass. Logs are in `evidence/`.

## Config change (only one)

Appended to `config.toml`. Homes are additive and deduplicated, so every invoker
(timer, DSH MCP, Claude MCP) sees the same Claude roots whether or not
`CLAUDE_CONFIG_DIR` is set:

```toml
[agents.claude]
homes = ["~/.claude", "~/.claude-home"]
```

## Rehearsal (reflink copy at `/mnt/zer0models/z0-wt/wiring/agentsview-rehearsal/data`)

- **Run 1 (binary 34d99a07): failed, nothing swapped.** The resync rebuilt the DB, but the
  swap was discarded because 10 Codex subagent rollouts (children of
  `019f1a1e-e5ee-…`, a parent that exists nowhere on disk) returned `NeedsRetry` → `Deferred`.
  `user_version` stayed 74, and every later sync would have repeated the ~30 min resync.
  Fixed in `987293f4`.
- **Run 2 (binary 987293f4): pass.** 1050 s wall (17.5 min) under the shared lock with nice 19 / ionice idle.
  7266 sessions synced, 5329 archived sessions preserved, swap ok, `user_version` 113.
- Live run: 1075 s wall, same result.

### Per-agent sessions (before = v0.39 archive, after = live after migration)

| agent | before | after | note |
|---|---:|---:|---|
| chatgpt | 2502 | 2502 | imports, preserved |
| omp | 2485 | 2485 | |
| grok | 2411 | 2412 | 2320 were already `source_missing` before; preserved |
| hermes | 1789 | 1789 | 460 preserved from sources no longer found |
| codex | 1690 | 1690 | messages 144072 → 139670: v0.44 drops parent history replayed into 72 subagent rollouts (upstream #643); top-level sessions unchanged |
| claude | 108 | 996 | +888 from `~/.claude-home` (never indexed before) |
| deepseek-harness | 0 | 203 | new |
| cursor-ide | 0 | 127 | new provider split |
| cursor | 9 | 87 | |
| kimi | 121 | 121 | |
| antigravity-cli | 107 | 107 | |
| opencode | 54 | 55 | 45 preserved |
| omo | 13 | 14 | earlier rows came from a dev build; same `omo:` IDs, upserted |
| gemini | 4 | 4 | |
| measure | 1 | 1 | test row |

The CLI's "Database: 9805 sessions" line uses a narrower filter; the table counts
every row in `sessions`. `quackles_orient.py` reads `sessions.transcript_revision`,
`message_count`, and `messages.content`/`ordinal`; all still exist.

## Live switch (2026-10-03 04:42–05:04 CDT)

1. Backup: `cp -a --reflink=always` → `/mnt/zer0models/sft-svlm/data/agentsview.bak-v74-20261003`
   (verified with `cmp`: sessions.db and config.toml identical, v74, 11294 rows)
2. Old binary: `~/.local/bin/agentsview-v0.39.0` (the real ELF, copied out of the uv tool;
   sha256 `e5922afc…`). `~/.local/bin/agentsview` used to be a symlink to
   `~/.local/share/uv/tools/agentsview/bin/agentsview` (saved in `old-symlink-target.txt`).
3. New binary installed as a regular file at `~/.local/bin/agentsview` (atomic rename).
4. Appended the config section above. Ran `AGENTSVIEW_NO_DAEMON=1 agentsview sync` under the shared lock.
5. Verified everything in the Status table. `agentsview mcp` launched from PATH with
   `AGENTSVIEW_DATA_DIR` (exactly how the DSH profile launches it) answered `search_content`.
   The DSH profile itself was not touched.

## Timer

`~/.config/systemd/user/agentsview-sync.{service,timer}` (sources in this directory):

- `OnCalendar=*:00/30`, `RandomizedDelaySec=2min`, `Persistent=true`
- `ExecStart=/usr/bin/flock -s -w 60 -E 0 /mnt/zer0models/cua-lane-tmp/locks/quiet-lane.lock ~/.local/bin/agentsview sync`
- `Nice=19`, `IOSchedulingClass=idle`, `TimeoutStartSec=50min`
- `Environment=AGENTSVIEW_DATA_DIR=/mnt/zer0models/sft-svlm/data/agentsview`
- `Environment=AGENTSVIEW_NO_DAEMON=1`. **Required:** in v0.44, plain `agentsview sync`
  starts a background `serve` daemon that keeps running after the command exits. With this set,
  sync writes directly and exits, or routes through an MCP-started daemon if one is already up.

A first manual run of the service took 15 s (6 sessions), Result=success, with a peak of 5 GB RSS.
The first timer-triggered run (05:31:43) took 36 s wall (18 sessions synced), Result=success, exit 0,
with no daemon left running; user_version is still 113 and the newest row is 10:32Z.
Watch it with `systemctl --user list-timers agentsview-sync.timer` and `journalctl --user -u agentsview-sync`.

## Rollback

```sh
systemctl --user disable --now agentsview-sync.timer
pgrep -x agentsview && AGENTSVIEW_DATA_DIR=/mnt/zer0models/sft-svlm/data/agentsview agentsview daemon stop
L=/mnt/zer0models/sft-svlm/data/agentsview
mv "$L" "$L.v113-rolledback-$(date +%Y%m%d%H%M)"
cp -a --reflink=always /mnt/zer0models/sft-svlm/data/agentsview.bak-v74-20261003 "$L"
ln -sfn ~/.local/share/uv/tools/agentsview/bin/agentsview ~/.local/bin/agentsview   # or: cp ~/.local/bin/agentsview-v0.39.0 ~/.local/bin/agentsview
agentsview version   # expect v0.39.0
rm ~/.config/systemd/user/agentsview-sync.{service,timer} && systemctl --user daemon-reload
```

The backup's config.toml lacks the `[agents.claude] homes` section, so v0.39 goes back to indexing only `~/.claude`.

## Issues / follow-ups

- **Codex orphan-fork bug (upstream v0.44.0 and main):** an explicit Codex fork whose parent
  rollout is permanently missing is `NeedsRetry` forever. That `Deferred` result aborts every
  data-version resync swap and keeps every sync "incomplete". The local fix
  (`internal/sync/codex_fork_retry_window.go`) treats the retry as final once a local source has been
  unchanged for 24 h; S3 sources keep retrying, and upstream's
  `TestResyncAllRejectsDeferredLocalReplacement` guard still holds. Worth raising upstream.
- The CLI printed `Sync complete` with rc=0 even when run 1 logged
  `local sync failed: local sync processing incomplete`, so the exit code alone doesn't prove success.
  Check `user_version` and `debug.log`.
- `uv tool upgrade agentsview` would recreate the symlink and replace this build with PyPI's (no OMO,
  no Codex fix). Don't run it; rebuild from the fork branch instead.
- Harness MCP use (`agentsview mcp`) auto-starts a `serve` daemon that exits after 20 min idle
  (`daemon_idle_timeout`). This is on-demand, as before, not always-on.
- Freshness depends on the shared lock: if exclusive windows exceed ~60 s at both :00 and :30,
  runs are skipped (`-E 0`) and freshness can pass 1 h.
- Pre-existing test failure `TestCodexCheckpointTruncationFallsBackToFullParse` (fails on pristine v0.44.0 here).
