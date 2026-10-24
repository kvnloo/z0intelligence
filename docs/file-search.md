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
OMP z0_file_search / Hermes resolve_repository_context
                         |
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
  lives across turns in the same bridge generation.
- **Hermes:** `adapters.hermes_z0int.resolve_repository_context(...)` calls the
  same resolver and EventLog path. Hermes does not get a parallel retrieval
  contract.

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

The live checkout is `/mnt/zer0models/z0-wt/z0intelligence`, on `master`; the
user unit reads `%h/.config/z0intelligence/service.env`. The guard defaults
already enforce the 200,000-file ceiling and 600-second lifetime, so no root
configuration or live environment edit is required. Roots come from requests:
OMP's `z0_file_search` sends `ctx.cwd`; the Hermes Python adapter requires
`project_root`. The live, not-yet-committed Hermes HTTP/MCP adapters require an
explicit `repository_root` rather than defaulting to the current directory.

Do not deploy over the live checkout's uncommitted changes. Kevin must first
decide how to preserve/reconcile them; do not auto-stash, reset, or overwrite
them. After that decision and the PR's merge into `master`, these are the
source-update commands (not executed by this change):

```sh
cd /mnt/zer0models/z0-wt/z0intelligence
test -z "$(git status --porcelain)" || {
  echo "STOP: reconcile the live checkout's edits first" >&2
  exit 1
}
git fetch --no-tags origin master
git merge-base --is-ancestor 33a57a44cf781dfbaeeb50865385f1002fcdd471 origin/master
git merge --ff-only origin/master
```

The existing-unit restart command is `systemctl --user restart
z0intelligence.service`; use it only after the host's server-admission policy
has approved that service. In OMP, run `/reload-plugins` to swap the Python
bridge generation (or restart OMP). A service-only restart does not replace a
resident OMP bridge. No install/model synchronization is needed for this
Python-only change. Health readback is
`curl --fail http://127.0.0.1:11501/healthz`.
