# Donor: claude-code-core

Cut from `dd39238` (tag `archive/pre-reorg-dd39238`) as one commit. This donor
documents the part the audit proposes to keep on master, so master can be
rebuilt from donors. It is the code as it was, not the trimmed target.

## What it is

The Claude Code side of z0int that has a measured token effect: ObservationPack
(a PostToolUse hook that archives large Bash output and shows head/tail plus a
recall handle), the State Packet injected at SessionStart, and the lean launch
profile, with the `paths.py` layout helper, the `doctor` / `onboard` / `status`
installer, and the two Claude Code plugins that wire the hooks.

It is 60 modules under `src/`, not the roughly 15 the audit targets, because
three module-level imports drag other packages in (see "Why it is this big").

## Entry points

| What | Where | Reached from |
|---|---|---|
| ObservationPack hook | `src/z0int/claude_code_obs.py:75` (`main`), `on_post_tool_use` `:47`, `pack` `:25` | `harness-adapters/claude-code-z0-obspack/hooks/hooks.json:1` PostToolUse(Bash), `python -m z0int.claude_code_obs hook` |
| ObservationPack recall | `src/z0int/claude_code_obs.py:59` (`recall`) | `harness-adapters/claude-code-z0-obspack/bin/z0obs` |
| SessionStart packet | `src/z0int/claude_code.py:201` (`on_session_start`) -> `src/z0int/state_packet.py:1455` (`session_start_hook`) -> `build_state_packet` `:1145` | `harness-adapters/claude-code-z0intelligence/hooks/hooks.json:41`, `python -m z0int.claude_code session-start`; only when `Z0INT_CLAUDE_CODE_PACKET=1` or config `packet: true` |
| Prompt / Stop / SessionEnd hooks | `src/z0int/claude_code.py:89` (`on_prompt`), `:160` (`on_stop`), `main` `:212` | same `hooks.json`, lines 8, 19, 30 |
| Lean launch profile | `src/z0int/claude_code_launch.py:125` (`_main`), `PROFILES` `:25`, `build_argv` `:118` | `z0int claude-code launch|profile|warm` (`src/z0int/cli.py:1188`) |
| Installer | `src/z0int/doctor.py:174`, `onboard.py:234`, `status.py:11` | `z0int doctor|onboard|status` (`src/z0int/cli.py:34`, `:50`, `:42`), `scripts/bootstrap.sh`, `skills/z0int-onboard/SKILL.md` |
| State Packet by hand | `src/z0int/cli.py:782` (`z0int context packet`) | human |
| Plugin marketplace | `.claude-plugin/marketplace.json` | Claude Code `/plugin` |

Checked on this branch (isolated HOME, no network):

- All 60 modules under `src/` import; 66 other `.py` files compile.
- `python -m z0int doctor --json`, `status --json`, `posture --json` and
  `claude-code --help` run.
- `python -m z0int.claude_code_obs hook`, `python -m z0int.claude_code
  session-start` and `... prompt` exit 0 on a minimal payload.
- `bin/z0obs` passes `sh -n`. Neither plugin was loaded into a running Claude Code
  for this cut.

## Tests

```
python -m pytest -m 'not live' -q
```

Result on this branch: 474 passed, 2 skipped, 0 failed, 0 collection errors.

36 test files are kept. The ones that cover the core itself:
`test_claude_code_obs.py`, `test_state_packet.py`, `test_claude_code_adapter.py`,
`test_claude_code_launch.py`, `test_claude_code_skills.py`,
`test_z0int_onboard.py`, `test_context_resolve.py`, `test_file_search.py`,
`test_posture.py`, `test_receipt.py`, `test_tokenomics_emit.py`,
`test_decision_opportunity.py`, `test_memory_event_log.py`,
`test_memory_optmem_tree.py`. The rest cover what the imports drag in
(`cognition`, `backends`, `openjev_phase1.core`, `adapters/hermes_z0int`) and
`test_check_receipt.py` covers the CI receipt gate. No bun tests: no kept
directory has `*.test.ts`.

Dropped although they touch a kept module (stated, not hidden):

- `tests/test_llama_http_backend.py`: covers `backends/llama_http.py`, which is
  kept, but one case imports `z0int.backends.bench`. Keeping it would add 53
  files and about 76,000 lines (the bench harness, its five tests and two
  committed result runs). `llama_http.py` is therefore on this donor without
  its test file. The full file is on `donor/decision-backends`.
- `tests/test_posture_routing.py`: covers the posture -> `worker_routing` seam;
  `worker_routing.py` is not here.
- `tests/test_cognition_orchestration.py`, `test_mutation_policy.py`: their
  subjects (`cognition/orchestration.py`, `cognition/mutation_policy.py`) are
  not here; the rest of `cognition/` is.
- `tests/test_intelligence_canonical.py`, `test_agentweb_integration.py`
  (import `automatic`), `test_functions_verify.py`, `test_dispatch_authority.py`,
  `test_free_only.py`, `test_provider_saturation.py`: their subjects are the
  router and service, which are on `donor/router`.
- `tests/test_architecture_manifest.py`: asserts every path in `zer0.repo.yaml`
  exists; that file is kept unmodified as the whole-repo map.

`core-unit.yml` was trimmed by line removals to the entries that still exist.

## Why it is this big

| Edge | Drags in |
|---|---|
| `src/z0int/cli.py:31` imports `cognition.cli` at module level | `src/z0int/cognition/` (20 of 22 files), `manifests/local_cognition.v1.json`, `scripts/probe_local_cognition.py`, 10 cognition tests |
| `src/z0int/claude_code.py:14` imports `automatic` and `tokenomics_emit` at module level | `automatic.py`, `receipt.py`, `tokenomics_emit.py`; lazily `decision_opportunity.py` |
| `src/z0int/state_packet.py:43` imports `context_resolve` | `context_resolve.py`, `file_search.py`, `memory/` |
| `state_packet.py:885,894` lazily import `posture` | `posture.py` |
| `doctor.py:18`, `onboard.py:17`, `status.py:7` import `models_mgmt`; `doctor.py:317` lazily imports `backends.registry` | `models_mgmt.py`, `manifests/models*`, `backends/` (13 files), `openjev_phase1/{core,direct}.py`, 11 backend tests and the capability manifests they read |
| `tests/test_claude_code_adapter.py:6` imports `harness_id` | `harness_id.py`, then `adapters/` and `test_hermes_adapter.py` |
| `tests/test_receipt.py` covers `counterfactual` | `counterfactual.py` |
| `benchmarks/state_packet/action_selector.py` imports `z0int.backends` | same `backends/` set |

## Seams cut on purpose

Four places where the closure was stopped. Each is a call path that fails on
this donor.

1. `src/z0int/cli.py` is kept with only its module-level imports followed.
   These subcommands still parse but raise `ModuleNotFoundError` when run: `ao`,
   `os-context`, `task`, `artifacts`, `contrastive`, `context-state`,
   `autoresearch`, `kerdoios`, `preflight`, `routine`, `cascade`, `aodl`,
   `repair`, `abab`, `hermes`, `counterfactual worker-needed-ingest`, `backends bench`.
   Working: `doctor`, `status`, `onboard`, `models`, `data`, `receipt`,
   `counterfactual` (mine), `context`, `backends list|doctor`, `cognition`,
   `posture`, `claude-code`.
2. `src/z0int/automatic.py:66` (`dispatch_event`) imports `intelligence`, which
   is not here. Only the intelligence service calls it. The prompt hook goes
   through `handle_event`, which POSTs to the service and returns "native,
   disabled" unless the harness is enabled in config.
3. `src/z0int/posture.py:444` (`pools_from_worker_routes`) imports
   `worker_routing`, which is not here. The caller at `:511` is fail-open, so
   `current_posture()` reports that source as `error: ModuleNotFoundError` and
   continues.
4. `harness-adapters/claude-code-z0intelligence/.mcp.json` and `bin/z0int-mcp`
   are removed. They start `z0int.intelligence_mcp` (the `route_worker` tool),
   which belongs to the router. The plugin's `plugin.json`, `README.md` and the
   marketplace description still mention the MCP tool.

`pyproject.toml` `[project.scripts]` keeps only `z0int`.

## Cost on a turn's hot path

From the audit:

- ObservationPack: "about 22-25 ms per Bash call (measured interpreter start +
  hook)".
- SessionStart packet: "Injects up to 1500 tokens once per session ... Worst
  case added latency 30s (hook timeout); git subprocess calls up to 10s each
  (state_packet.py:107)."
- Prompt hook: "about 50-70 ms interpreter start on the hot path, plus a
  detached process doing git calls and a transcript scan", result discarded in
  shadow mode.
- Stop and SessionEnd: "both run the same command and each re-parses the full
  transcript (median 119 KB, p90 29 MB, max 135 MB this week)."
- Lean launch profile, doctor, onboard: nothing per turn.

## Evidence (quoted from the audit of `dd39238`)

For:

- ObservationPack: "repo claim is -30.3% total tokens on a long recall task
  (n=12, p=0.0068, 12/12 verified) and no effect on short tasks
  (docs/claude-code.md:16). Live: 879 archives in 7 days, about 1.2M tokens
  removed gross."
- State Packet: "repo claim is held-out 14/14 vs 12/14 answers and -30% input
  tokens, packet x0.81-0.83 cost (docs/claude-code.md:13, external z0evals
  study)."
- Lean launch: "repo claim is cold -63.8% median (5/5) and warm -27.2% median
  (15/15) at equal verified quality (docs/claude-code.md:11)."
- "Code with a measured saving: 1,763 lines (obs 97, state_packet 1,499, launch
  167)."

Against, or missing:

- ObservationPack: "Net is unmeasured: three auditors each had the head/tail cut
  hide needed output and force reruns." (The author of this donor hit the same
  thing repeatedly while building it.)
- State Packet: "No per-host A/B exists; src/z0int/claude_code.py:202-203 says
  it stays off by default until one does." "1,499 lines for one 1,500-token
  injection."
- Lean launch: "the largest claimed effect in the repo and it is applied by
  nothing: only a human-typed `z0int claude-code launch` ... Not reproduced by
  any auditor."
- Prompt, Stop and SessionEnd hooks: "NONE. Nothing reaches the model"; "Pure
  recording". Verdict DELETE for all three.
- `claude_code_skills.py`: "zero importers in src, and docs/claude-code.md:17
  records it as 'not promoted' (best 2/30 misses against a bar of 1/30)."
- doctor / onboard / status: "No token saving; install plumbing."
  `onboard.py` "symlinks every OMP extension", which the audit names as what
  kept dead extensions on the OMP hot path.

All three positive numbers come from the external `z0evals` study
`claude-code-savings-v0`. None was reproduced on the audited host. The table the
quotes cite is at `docs/claude-code.md:11-17`.

## Did it run live on the audited host

- ObservationPack: yes, on every Bash call.
- State Packet: yes, enabled by environment variable; off by default in code.
- Prompt / Stop / SessionEnd hooks: yes.
- Lean launch profile: no. No run was ever recorded.
- doctor / onboard: used once.

None of it ran from `dd39238`; the hooks imported z0int from a different
branch's checkout.

## Known defects

- The four seams above.
- `src/z0int/cli.py` has a function-level `from .autoresearch.research.pipeline
  import ...`; that module did not exist at `dd39238` either.
- ObservationPack keeps the first 40 and last 20 lines of anything over 6000
  characters (`claude_code_obs.py:17-18`) and drops the middle, with no measure
  of how often that forces a rerun.
- `hooks.json` runs Stop and SessionEnd through the same full-transcript parse.
- SessionStart hook timeout is 30 s and git calls allow 10 s each.
- `onboard.py:145-175` links every directory under `omp-extensions/` into OMP.
  There is no `omp-extensions/` on this donor, so the step finds nothing.
  `onboard.py` also prints `z0int-compile`, which is not installed here.
- `scripts/bootstrap.sh:45` points at `scripts/setup-flyforge.sh`, which is on
  `donor/omp-jev-extensions`, not here.
- `benchmarks/claude_code/tasks/{suite-full,suite-triage,debug-suite}/task.json`
  name repository tests that are not on this donor; those three benchmark
  tasks need the full tree. `benchmarks/claude_code/run_pair.py` and
  `benchmarks/state_packet/run.py` call the paid Claude CLI and were not run.
- `AGENTS.md`, `CLAUDE.md`, `ROADMAP.md`, `README.md` are the whole-repo
  versions. `state_packet.py:58` harvests headings from them into the packet,
  so their stale "critical path" headings end up as packet priorities.
- `pyproject.toml` still names the distribution `openjev-phase1` and pins the
  torch stack.

## Extra files kept beyond the assignment

`harness_id.py`, `counterfactual.py`, `adapters/`, `automatic.py`,
`receipt.py`, `tokenomics_emit.py`, `decision_opportunity.py`, `posture.py`,
`file_search.py`, `memory/`, `backends/` (without `bench/` and
`image_decision.py`), `openjev_phase1/{__init__,core,direct}.py`, most of
`cognition/`, `manifests/`, `scripts/probe_local_cognition.py`,
`docs/local-cognition-portfolio.md`, and the tests of each. Reasons are in
"Why it is this big". Assignment rows not followed: the MCP files (seam 4) and
`tests/test_architecture_manifest.py`.

## To earn a place on master

Three measurements, one per piece, all with the paired harness in
`benchmarks/claude_code/run_pair.py` (billed tokens per verified task):

1. Lean profile vs stock on the host's real task mix. Take
   `src/z0int/claude_code_launch.py` lines 1-87 and 118-167 (drop the
   opportunity report at 88-116, which imports `claude_code`), and say how
   fleet sessions are started so something actually launches through it.
2. ObservationPack: from existing transcripts, how often a packed result is
   followed by a `z0obs` recall or a rerun, and what those cost. Take
   `src/z0int/claude_code_obs.py`, `src/z0int/paths.py`,
   `harness-adapters/claude-code-z0-obspack/` and `tests/test_claude_code_obs.py`.
   That is the whole feature: 3 modules, about 155 lines.
3. State Packet on vs off, per host. Take `state_packet.py` build and render
   only, the 40 lines of `claude_code.py` around `on_session_start`, the three
   classes it needs from `context_resolve.py`, the SessionStart entry of
   `hooks.json`, and `tests/test_state_packet.py` with its fixture.

Smallest subset worth taking today, before any new measurement: item 2.
Uncertain: whether `doctor` / `onboard` are worth 815 lines for an install path
with one user; the audit rates that low confidence.
