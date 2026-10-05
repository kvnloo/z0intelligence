# z0-memory (OMP / OMO)

The z0 memory seam for OMP and OMO (senpi) on the `context` event, plus the memory-only MCP entry (`mcp.json`).

- `context` fires before each provider request; a returned `{messages}` is model-visible only, so the brief never
  enters history or the session file. Only a request that starts a user turn gets it, inserted just before the
  user's message; tool continuations stay native. A replayed turn is never injected twice.
- `Z0INT_MEMORY_INJECT`: `off`, `shadow` (default: one detached `python -m z0int.memory.seam shadow` child per turn,
  nothing returned), `canary` / `on` (`seam turn` within 300 ms, else native and a counted `timeout`).
- A non-loopback model (`ctx.model.baseUrl`) stays native until the owner sets
  `{"inject": {"omp": {"allow_cloud_injection": true}}}` (or `omo`) in `~/.z0int/config/memory.json`.
- Rows: `$Z0INT_HOME/state/memory/seam/{omp,omo}.jsonl`.

Load: OMP `-e <checkout>/omp-extensions/z0-memory` (index.ts). OMO: activation writes
`~/.omo/agent/extensions/z0-memory.js` as `export { default } from "file:///<checkout>/omp-extensions/z0-memory/omo.ts";`.
A senpi build without a `context` hook is recorded `UNSUPPORTED` in `state/memory/seam/push_status.json`, and
`z0int memory eval --harness omo` then reports B=UNSUPPORTED (stop condition), never a pass.

MCP: merge `mcp.json` (`z0-memory`, `--profile memory`) into `~/.omp/agent/mcp.json` / `~/.omo/agent/mcp.json`.
