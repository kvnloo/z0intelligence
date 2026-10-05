# Repository instructions

## Onboarding (do this first)

1. Read **`docs/ONBOARDING.md`** (canonical product setup).
2. Prefer **`z0int` CLI** over manually reproducing setup:
   - `z0int doctor --json`
   - `z0int onboard --auto`
   - `z0int status`
   - `z0int models plan` / `z0int models sync`
3. Do not bypass privacy/secret checks. Do not invent unimplemented steps.
4. Thin skill: `skills/z0int-onboard/SKILL.md` (calls CLI only).

## Shared memory (read before changing it)

- Canonical continuity plan and acceptance: [#22](https://github.com/kvnloo/z0intelligence/issues/22).
- Pinned implementation/evidence index: **[`docs/memory-status.md`](docs/memory-status.md)**.
- Distinguish merged source, branch-only wiring, dated host setup and verified model use. A configured backend, branch name or closed setup issue is not proof of integration.
- Reuse existing `EventLog`, `OptMemTree`, State Packet, memory-contract and harness-adapter work. No second memory database, router, scheduler or receipt system.
- Native source archives remain original evidence; z0's ledger owns admitted events/references. Memory and summaries never grant execution authority.
- #116/#121 own the read-only `ctx` pull integration; #120 is an optional semantic/fresh-turn OptChat experiment, not a mandatory master-chat migration.
- Every memory-related change should name its exact source revision, narrow owning issue and activation/verification effect. Do not implement superseded issue prose as if it were current code.

## Verified OSS Loop

This repo follows the [Verified OSS Loop](https://github.com/kvnloo/verified-oss-loop). See **`docs/verified-oss-loop.md`**.

- Kit: `.verified-oss-loop/` (inventory, rollout, kit skills)
- Issues are not claims. Workers never merge `master` or `dev`.
- Day-pass PRs → `preview`; overnight unattended → `nightly` (see `python3 .verified-oss-loop/rollout.py show`).
- Evidence: revision-bound unit + product smoke; mutation is `n/a` unless adopted.

## Runtime / bench

- Run commands from the repository root in an isolated environment installed with `pip install -e '.[test]'` (or `./scripts/bootstrap.sh`).
- FlyForge stack bootstrap (legacy, still valid): `bash scripts/setup-flyforge.sh`. Prefer `z0int onboard` for new setups. See `docs/evolution-lab.md`.
- Route C (vLLM DiffusionGemma structured reads): `docs/vllm-diffusion-route.md`, `openjev-score --mode vllm`, `scripts/setup-vllm-diffusion.sh`. Requires vLLM PR #57250 or equivalent.
- OMP plugins live in `omp-extensions/` and must be symlinked into `~/.omp/agent/extensions/` (`flyforge-recovery`, `flyforge-jev` shadow-only, `vllm-jev`, `openjev`, `openjev-06b`, `z0int-bridge`). `z0int onboard` links them when OMP is present. Do not edit copies only under `~/.omp`. OpenJev 4B: `Qwen/Qwen3.5-4B` @ `851bf6e…`. 0.6B test lane: `Qwen/Qwen3-0.6B` @ `c1899de…`. Do not load both SLMs on 12GB. Shadow: `EVOLUTION_LAB_PYTHON` / `EVOLUTION_LAB_ROOT` optional (`~/.z0int/config/env_hints.json`).
- Validate changes with `pytest -q`, `(cd results/raw && sha256sum -c SHA256SUMS)`, and `python benchmarks/verify_published.py`.
- Benchmark outputs are create-only. Use a new output path and expose exactly one CUDA GPU per scorer process.
- Do not change headline claims or `results/phase1-summary.json` without committing the supporting row-level evidence, regenerating the relevant raw report, updating `results/raw/SHA256SUMS`, and updating the method/results text.
- Preserve exact model and source revisions. Fetch third-party evaluation records only through `benchmarks/fetch_sources.py`; do not commit model weights, caches, or third-party raw records.
- Personalized state lives under **`~/.z0int/`** only (episodes, specialists, stream, research). Never default champions into the git tree.
- `webgpu-demo/` is static and has no build step. Preserve `_headers`, runtime version pins, browser-only inference, and the explicit probability limitations.

## Decision backends

- Contract: `src/z0int/backends/` (`DecisionBackend`, not Provider).
- Local fast path: **Laya 421M** (`laya_421m` / alias `laya`).
- Verification contract: `src/z0int/functions/verify_evidence_sufficiency.py`.
  - `verify()` defaults to Laya.
  - `verify_with_escalation()` defaults to **Laya → Jev** when Laya lands in the abstain band.
  - Expected reference revision: **`jev-1.13.0`**.
- NanoJev is legacy benchmark/reproducibility only. Keep its adapter/tests for historical comparisons; do not make it a production or verification default.
- Agent commands:
  - `z0int backends list --json`
  - `z0int backends doctor --json`
  - `z0int backends eval --backend laya_421m --input tests/fixtures/decision_request.json --json`
- Ordinary doctor/status/list paths must not eagerly load model weights.
- Do not mark backend inference as `verified_success`; ambient turn close ≠ gold.

## Brand (background)

Product name **z0intelligence** (stochastic-parrot play; abundance under finite frontier budgets with Kerdoios). Details: `docs/brand.md`. Do not mass-rename the `z0int` package in drive-by PRs.

## Context resolve (critical path)

- `z0int context resolve --query '…' --json` or `--path file`
- Primitive: `z0int.context_resolve.resolve_context` — provenance packet, not authorization.
- Do not flip z0int-bridge `log_only` live without host consumption + verifier.
- See `docs/critical-path-phase0.md`.
- State Packet v0 (#22): `z0int context packet --repo PATH [--json|--render|--check FILE [--authorize ACTION]]`;
  primitive `z0int.state_packet.build_state_packet`. Read-only adapters: git, repo docs, local Claude Code
  transcripts (opt-in GitHub via `Z0INT_PACKET_GH=1`). Missing required fact => `OBSERVE`; stale packet cannot
  authorize. SessionStart hook: `python -m z0int.state_packet --hook`. Bench + results: `benchmarks/state_packet/`.
  Raw conversation text never enters the repo or fixtures.

## Verified task loop

- Family `coding.bounded_worktree_patch`: `z0int task fixture|authorize|run|status`
- Checkpoints: `$Z0INT_HOME/state/tasks/<id>.json` (default `~/.z0int`)
- `execution_completed` ≠ `verified_success`

## Bridge hot-reload (v2)

- Shim: `omp-extensions/z0int-bridge/index.ts` (immutable after session start)
- Worker: `python -u -m z0int.bridge.worker`
- Docs: `docs/bridge-hot-reload.md`
- After first install: **one OMP restart**, then `/reload-plugins` hot-swaps Python.
