# feat(memory): wire the z0 memory surface into every harness (C8)

Branch `feat/wire-memory-harnesses-20261003` @ `41aec0fa5795ba77054347813c7b1303ba0b6868`. Its base is 3028e0c, which is C7 772ba7d merged with integrate e02bcf5. Component C8-memory-wiring.

**Status:** verified in round 2 and merged into `integrate/wiring-20261003` (b99bc31). Staged only; no PR has been opened.

## What

There is one memory inject seam, `python -m z0int.memory.seam`, with modes `off | shadow | canary | on`. Every harness reaches it through a thin shim. No harness core is changed.

| harness | push seam | pull (MCP `z0-memory`, memory profile only) |
|---|---|---|
| Claude Code | `UserPromptSubmit` second hook entry → `additionalContext` | plugin `.mcp.json` |
| Codex | `UserPromptSubmit` hook → `additionalContext` | plugin `.mcp.json` |
| Hermes | the one shared `pre_llm_call` (capture + automatic + memory) | `z0-memory` MCP for MCP-enabled profiles |
| OMP / OMO | `omp-extensions/z0-memory` `context` event (model-visible only) | `mcp.json` into `~/.omp|.omo/agent/mcp.json` |
| DSH | `agent/pre-step` (`memory.mjs` → `memory-client.mjs`), registered once in `index.mjs` | `z0-memory.cordis.yml` |
| Grok | none: pull-only, case B UNSUPPORTED by default | `mcp/z0-memory.toml`; opt-in project rules via `z0int memory rules` |

**Safety defaults:**
- **Default mode is `shadow`.** A detached child computes the brief and nothing is model-visible. Shadow children are capped at 4 in flight; past that, a `queue_saturated` row is written.
- **Canary and on** inject within 300 ms, counted from process start. On a timeout or error the turn stays native and the seam writes a counted row.
- **Egress gate:**
  - The endpoint is the harness's own model setting only:
    - Claude Code `ANTHROPIC_BASE_URL`;
    - Codex `OPENAI_BASE_URL`;
    - Hermes `model.base_url`;
    - OMP `ctx.model.baseUrl`;
    - DSH `model_endpoint`.
  - If the endpoint is missing, it counts as cloud.
  - A cloud endpoint needs `{"inject": {"<h>": {"allow_cloud_injection": true}}}` in `$Z0INT_HOME/config/memory.json`. No env var can bypass this.
- **Fail-closed scope:** a turn with no resolvable project gets a `no_scope` row and no brief. The project is named the way AgentsView names it.
- **Echo guard:** briefs that a host persisted and AgentsView re-indexed are never recalled as evidence.
- **Single-injector guard:** a second injector for the same turn is refused with `double_inject_guard`, and a replayed turn is never injected twice.
- **Receipts:** every seam turn writes a `z0int.memory_seam.v0` row with a MemoryUseReceipt to `$Z0INT_HOME/state/memory/seam/<h>.jsonl`.
- **`z0int memory eval`** writes the per-harness A–F acceptance rows.

## Why

z0evals#56 (A–F) asks for one memory surface across all harnesses, with opt-in injection per harness. Owner decision: memory injection into cloud models is opt-in per harness, default off, shadow first.

## Tests

- **Round 2:** red tests 55cbb4d, fix 41aec0f. Green is 67 passed / 1 xfailed (pytest), plus node 11 pass and bun.
- **Full suite:**

  | tree | pytest |
  |---|---|
  | base 3028e0c | 11 failed / 1151 passed |
  | head | 12 failed / 1224 passed / 1 xfailed |

  The extra failure is `test_hook_subprocess_wall_time_is_within_bare_python_plus_40ms`, a load-timing test. In isolated reruns it passed 3/3 on both head and base (`round2/suite-timing-rerun.txt`). On the integrate tree the failure set is identical to the base.
- **Isolated e2e:** 45/45 checks pass (`evidence/C8-memory-wiring/round2/e2e.txt`). It covers:
  - for Claude Code, Hermes and OMP:
    - shadow requests are identical to off;
    - a canary brief appears in the next model request;
    - off requests carry no brief;
    - a secret probe is scrubbed;
  - the cloud generic-env arm;
  - the sibling-project canary;
  - the echo guard;
  - persistence per shim.

## Recorded deviations (owner decision; none redefined as met)

1. **Hermes:** Hermes core stamps the sent bytes, brief included, on the `api_content` sidecar in `state.db`, so the brief is persisted. This is pinned by a strict xfail. The options are to accept it, or to keep Hermes at shadow.
2. **Claude Code:** hook `additionalContext` is recorded in the transcript (host design).
3. **DSH:** there is no non-durable request seam, so the brief is in the DSH log. The echo guard cuts it from recall.
4. **No SessionStart memory seam** for Claude Code or Codex; `UserPromptSubmit` covers case B.
5. **`opportunity_record.memory` is not populated.** The seam receipt is not joined into the capture ctx, so DoD D3 is UNMET for opportunity rows (found by the round-2 integration e2e). This is an open item for C8.
6. **#63 TencentDB provenance is UNMET.** Gateway items have no project scope, so they are queried but never enter a scoped brief.

## Activation

ACTIVATE.md A8, done per harness, shadow first. Cloud injection stays off unless the owner sets `allow_cloud_injection` for that harness. Nothing has been activated.

🤖 Generated with [Claude Code](https://claude.com/claude-code)
