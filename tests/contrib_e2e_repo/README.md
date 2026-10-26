# Contributor acceptance test: repo-only trial

Driver: `scripts/contrib-e2e-repo` (run it with no arguments for usage). It implements
contributor spec §4.1-4.3 (`docs/contrib/CONTRIBUTOR-LAYER.md`) for the **repo-only path** (spec §1.3): a fresh clone of the
z0intelligence candidate in `ubuntu:24.04`, `./scripts/bootstrap.sh` as a non-root user,
then agent cells that get exactly the prompt `help with this repo`.

| File | Role |
|---|---|
| `Dockerfile` | base OS image: git, python3, venv, curl, CA certificates only |
| `provision.sh` | fresh clone + `./scripts/bootstrap.sh` (records the bootstrap time); `refresh` moves a provisioned image to a new candidate |
| `cell.sh` | one agent cell: fake HOME per profile, canaries, fake `gh`, pre-push refusal |
| `judge.py` | checks A1-A7 without an LLM and writes the `contrib_ready` receipt |
| `det.sh` | deterministic cells: smoke, E1, P1, P2, C1, W1, G1-G5 |

**This is a trial, not the gate.** Spec §5 says a repo-only run "counts as a trial, not as
ready". The gate runs the full path (`./z0 onboard --auto` first) with the kvnloo/z0
harness `scripts/onboard-agent-e2e`. Every receipt here carries `"path": "repo_only"`.

Receipts go to `<work>/receipts/<z0_sha>-<z0int_sha>/`, and
`scripts/contrib-e2e-repo verdict` runs `scripts/contrib_ready.py` on them.

Known differences from the spec (each one keeps the verdict NOT_READY or is noted
in the receipt):
- The harness binaries are mounted read-only from the caller at a pinned version,
  not installed from the public installer inside the container.
- There is no recording egress proxy yet. A5 uses the fake `gh` log, the pre-push
  log and a manifest diff of HOME. A6 checks z0int outputs and the agent transcript.
- Hardware facts use PATH fakebins (`nvidia-smi` prints 12288 MiB for `ml-research`)
  instead of `Z0_TEST_HW`.
- A1 is the z0int repo-only onboarding receipt (S1). The `onboard_container` cell
  (`./z0 onboard --auto` with z0intelligence in the target set) is separate and needs
  onboarding work package Z3.
- E2 needs the Z5 LLM resolver (CW6). Nothing here writes an E2 receipt.
