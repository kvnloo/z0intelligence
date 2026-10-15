# Repository file-search plane

z0intelligence separates **discovery latency** from **evidence authority**.

## Priority order

1. **Exact path** — direct bounded read when the caller already knows the file.
2. **FFF (high priority)** — resident Rust path/content index for codebases. One
   `fff.FileFinder` is kept warm per repository root (bounded process-local LRU),
   with content indexing and filesystem watching enabled. Repeated OMP/Hermes
   searches reuse the index instead of rescanning the tree.
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
          resident FFF index + watcher
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

- `watch=True`
- `ai_mode=True`
- content indexing enabled
- symlink following disabled
- initial scan wait: 5 s
- content query budget: 120 ms
- at most 4 resident repository roots per process

Environment overrides:

- `Z0INT_FFF_DISABLE=1` — fail open to the later retrieval layer.
- `Z0INT_FFF_SCAN_TIMEOUT_MS=<n>` — initial scan wait.
- `Z0INT_FFF_MAX_ROOTS=<n>` — process-local resident-root bound.

A watcher event increments the packet source epoch. Index failures are returned
as explicit layer status and never become permission to fabricate evidence.

## QMD boundary

QMD remains the medium-latency docs/notes lane. The current critical resolver
uses its lexical `search` command only after FFF misses. QMD's full `query`
pipeline adds vector search/query expansion/reranking and can load local model
resources, so enabling that hybrid path should be benchmarked and gated
separately rather than silently inserting it into every code-search turn.
