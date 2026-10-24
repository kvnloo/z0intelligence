# Repository file-search plane

z0intelligence separates **discovery latency** from **evidence authority**.

## Priority order

1. **Exact path** — direct bounded read when the caller already knows the file.
2. **FFF (high priority)** — resident Rust path/content index for codebases. One
   `fff.FileFinder` is kept warm per admitted Git toplevel (bounded process-local
   LRU), with content indexing enabled and **all filesystem watching disabled**.
   Repeated searches reuse it for up to ten minutes; the first query after
   expiry lazily rebuilds it. Rebuilds are serialized and use a dedicated
   nice-10, idle-IO thread, not the HTTP thread.
3. **QMD (medium priority)** — markdown/notes retrieval. The existing QMD lane
   remains a complementary fallback after repository search misses. Hybrid
   FTS5/vector/rerank promotion is a separate measured tier, not something FFF
   needs to own or replace.
4. **Memory/state projections** — separate evidence sources under the existing
   memory contract.

FFF and QMD are retrieval mechanisms. Neither can authorize execution, mint a
verified outcome, or replace the canonical source file.

## Evidence flow

~~~text
Codex / Claude Code / DSH MCP file_search     Hermes native z0_file_search
                         |                       |
                         +-----------+-----------+
                                     v
                     existing HTTP service /v1/file-search
                                     |
OMP native z0_file_search             |      Python resolve_repository_context
             |                       |                 |
             +-----------------------+-----------------+
                                     v
              z0int.file_search.v1
          resident FFF index (no watchers)
                         |
                         v
              ContextPacket EvidenceRef
          trust_class = index_hit
                         |
                         v
              EventLog context.resolve
                         |
                         v
              harness/model consumption
~~~

The `ContextPacket` carries the source locator, current file stat fingerprint,
FFF package/index epoch, bounded excerpt, gaps, and measurements. Harness
adapters record the packet as a `context.resolve` EventLog observation with
`execution_completed=false` and `verified_success=null`.

## Harness surfaces

- **OMP:** the resident Python bridge accepts `file_search`; the extension
  exposes it as the essential `z0_file_search` tool. The Rust index therefore
  lives across turns in the same bridge generation. Its **actual bridge Python**
  must have `fff-search==0.11.0`; installing the wheel in some other Python is
  insufficient. This change does not edit the immutable OMP shim or its startup.
- **Codex / Claude Code / DSH:** `python -m z0int.intelligence_mcp` exposes
  `file_search`. The trusted launcher sets `Z0INT_HARNESS` to `codex`,
  `claude-code`, or `dsh`. Tool arguments cannot override it. `Z0INT_SHARED_ONLY=1`
  retains **both `route_worker` and `file_search`**, not provider-specific tools.
  DSH's reduced schema still relies on the service for authoritative bounds.
- **Hermes native:** [`harness-adapters/hermes-file-search`](../harness-adapters/hermes-file-search/README.md)
  is a separate, opt-in, stdlib-only plugin. It registers `z0_file_search` using
  `register(ctx)` and a static priority instruction. It does **not** register
  routing/turn/LLM hooks or import z0int/adapters/FFF. It calls the shared HTTP
  service; that service owns the index and EventLog. Existing pre-LLM plugins
  are untouched and do not install this tool.
- **Python library:** `adapters.hermes_z0int.resolve_repository_context(...)`
  remains available to source consumers; a library export is not native Hermes
  tool registration and the adapters tree is not part of the installed package.

## Shared HTTP contract

`POST /v1/file-search` on the **existing** intelligence service (default
`http://127.0.0.1:11501`). No new daemon or backend. Example request:

```json
{
  "repository_root": "/absolute/repository/on/service/host",
  "query": "resolve_context",
  "kind": "exact_symbol",
  "harness": "codex",
  "trace_id": "caller-trace",
  "session_id": "caller-session",
  "limit": 5,
  "time_budget_ms": 120
}
```

- Required: `repository_root`, `query`, `harness`. Roots must be absolute,
  existing directories other than the filesystem root (maximum 4096 characters).
  Query: nonempty, maximum 2048 characters. Control characters and surrogate
  codepoints are rejected. Optional session/trace IDs are at most 256 characters.
- Kinds: `exact_path`, `exact_symbol`, `natural_language` (default). Exact paths
  are checked after symlink resolution and cannot escape the specified root.
  Known paths should normally go straight to the harness's `read_file` tool.
- `limit`: integer 1–20 (default 5); `time_budget_ms`: integer 10–1000 (default
  120), applied to each native content-search pass, **not an end-to-end deadline**.
  Initial indexing, pool contention, path search and EventLog I/O are additional.
- The shared endpoint disables QMD and memory to keep evidence repository-scoped.
  A miss is an explicit gap; it does not search unrelated notes. General resolver
  and OMP QMD policies are unchanged. No caller `record=false` bypass is accepted.
- Returns `z0int.harness_context.v1` with `harness_id`, `event_id`, and the existing
  `z0int.context_resolve.v1` ContextPacket. Records `context.resolve` with
  `source=harness:<caller>`, project, session and trace (`packet.task_id`).
  Retrieval is an observation: `execution_completed=false`, `verified_success=null`.
- Invalid input: HTTP 400; existing body limit/admission/drain policies still
  apply (413/503). Missing/disabled/failed FFF yields `ok=false`, explicit gaps,
  and `file_search_unavailable`. A normal zero-hit search has `ok=true` with gaps.
  EventLog failure preserves the packet but returns `event_id=null` and
  `event_log_error`; it never claims successful persistence.
- MCP/Hermes clients have a 15-second socket timeout and return explicit fallback
  guidance on transport failure. They never start another index, route a worker,
  run a model, or silently substitute invented results. Retrying a search may
  create another observation, not a worker execution receipt.

**Trust boundary:** this is the existing trusted-host service, not an authenticated
filesystem sandbox. Roots name files on the service host, not a remote harness's
machine. A raw HTTP caller supplies its harness identity; the allowlist validates
attribution, not authentication. Do not expose the endpoint to untrusted callers.
Containment checks do not provide isolation against a hostile same-UID process
concurrently replacing filesystem components. Evidence is not authorization.

For system metadata, disk usage (`stat`, `du`) and Git worktree enumeration
(`git worktree list`), use their canonical tools, not FFF discovery.

## Reproducible integration gate

Use a Python with `fff-search==0.11.0` and test dependencies. Set `PYTHONPATH=src`,
isolated `HOME`/`Z0INT_HOME`/`HERMES_HOME`, and a canonical (resolved) scratch
`TMPDIR`; then run from the repository root:

```bash
python -m pytest -q tests/test_shared_file_search.py tests/test_hermes_file_search_plugin.py
```

For the real Hermes `PluginContext.register_tool` → registry handler probe, also
set `HERMES_PLUGIN_TEST_PYTHON` to its installed interpreter and
`HERMES_PLUGIN_TEST_ROOT` to its source/SDK directory. That optional probe skips
without these explicit paths. It creates only scratch profile state, imports no
z0int backend in the Hermes process, and performs no inference. `-s` prints
native response receipts. The tests bind an owned service to port 0, exercise
real FFF **content** matches (not exact-path bypass), prove cross-harness pool
reuse and EventLog attribution, and shut the service down on completion.

## FFF runtime policy

`fff-search==0.11.0` is installed with z0intelligence. Defaults:

- `watch=False` (including build/cache directories and nested worktrees)
- `ai_mode=True`
- content indexing enabled
- symlink following disabled
- initial scan wait: 5 s; timeout closes the index before another build can start
- content query budget: 120 ms
- at most 4 resident repository roots per process
- only Git toplevels are admitted; `/mnt/zer0models`, the shared workspace root,
  and filesystem roots are always rejected
- a conservative admission ceiling of 200,000 files, counting build/cache and
  nested repository files too (excluding `.git` metadata and symlink traversal)
- admission errors fail closed before FFF construction and log the root/reason
- ten-minute index lifetime, rebuilt lazily with at most one rebuild at a time

Environment overrides:

- `Z0INT_FFF_DISABLE=1` — fail open to the later retrieval layer.
- `Z0INT_FFF_SCAN_TIMEOUT_MS=<n>` — initial scan wait.
- `Z0INT_FFF_MAX_ROOTS=<n>` — process-local resident-root bound.
- `Z0INT_FFF_MAX_FILES=<n>` — conservative per-root file admission ceiling.
- `Z0INT_FFF_INDEX_TTL_SECONDS=<n>` — index lifetime (default 600 seconds).

Each rebuilt index has a distinct packet source epoch. There are no watch
subscriptions: edits can remain absent from discovery until TTL expiry.
Index failures are explicit layer statuses and never permission to fabricate
evidence. FFF 0.11 has no constructor path exclusions; disabling watching avoids
recursive watcher registration altogether rather than filtering events after
registration. Its normal Git-ignore scan policy still applies, while admission
counts conservatively bound trees even if cache directories are not ignored.

The shared `/v1/providers` response is cached for 30 seconds; concurrent misses
share one rebuild. Provider snapshots can therefore be up to 30 seconds old.
This display/discovery cache does not replace execution-time admission checks.
The repository's provider discovery callers (`remote_executor.py` readiness and
`scripts/prove-intelligence.py`) make single GET attempts; there is no discovery
retry loop to back off. Consequential POST calls are not retried automatically.

## QMD boundary

QMD remains the medium-latency docs/notes lane. The current critical resolver
uses its lexical `search` command only after FFF misses. QMD's full `query`
pipeline adds vector search/query expansion/reranking and can load local model
resources, so enabling that hybrid path should be benchmarked and gated
separately rather than silently inserting it into every code-search turn.

## Deployment gate

The live checkout is `/mnt/zer0models/z0-wt/z0intelligence`; the user unit reads
`%h/.config/z0intelligence/service.env`. Kevin selected preservation and integration
on `live-wip-20261023`, tracked by Draft PR #207. Commit `500ee41` preserves every
original edit and untracked file as-is above the previous live commit `4de1aa9`.
The branch then merges #206, retaining the live adapters and OptChat changes
alongside bounded indexing and provider caching. Upstream free-only safeguards
take precedence over WIP bypasses; no new provider authority is granted.

The guard defaults enforce the 200,000-file ceiling and 600-second lifetime, so
no root configuration or live environment edit is required. Roots come from
requests: OMP's `z0_file_search` sends `ctx.cwd`; the Hermes Python adapter
requires `project_root`, and Hermes HTTP/MCP adapters require an explicit
`repository_root`.

Run the merged revision's tests off-host before restarting only the existing
`z0intelligence.service`. Do not overwrite future uncommitted changes.
Rollback to the previous runtime while preserving the WIP/integration branch
is this single shell command (it refuses incompatible uncommitted changes):

```sh
git -C /mnt/zer0models/z0-wt/z0intelligence switch --detach 4de1aa962cf4c9a19b18a6120ea27309b908da65 && systemctl --user restart z0intelligence.service
```

To return to the preserved integrated runtime, switch back to
`live-wip-20261023` and restart the same unit.

The existing-unit restart command is `systemctl --user restart
z0intelligence.service`; use it only after the host's server-admission policy
has approved that service. In OMP, run `/reload-plugins` to swap the Python
bridge generation (or restart OMP). A service-only restart does not replace a
resident OMP bridge. No install/model synchronization is needed for this
Python-only change. Health readback is
`curl --fail http://127.0.0.1:11501/healthz`.
