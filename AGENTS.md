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
