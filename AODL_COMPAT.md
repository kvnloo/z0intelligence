# AODL compatibility target

z0int's future Routine/Cascade compiler targets the **AODL intent-contract
semantics**, not `aodl/main` as of 2026-09-18.

## Relevant upstream refs checked

- `kvnloo/aodl@main` — `8e41ef024262339d28a968d71113fc52819d4bd4`
  (Sep 10; **does not contain the intent-contract profile**).
- `cursor/aodl-intent-contract-e30f` — contains the intent-and-participation
  contract (`profiles/intent-contract.md`, `intent-loop.json`, executor harness
  binding). This branch is 12 commits ahead of main.
- `nightly` — `9e8d61673b1fca39ffe453609d763996dce94cbb`.
  Contains the intent-contract work plus the stricter current formal contract:
  `observedGraph`, fail-closed harness catalog validation, control-room rejection,
  verifier-vs-humanGate authority separation, closed event types, and Hermes
  dry-run compiler semantics.
- `preview` — `ed73b7ee2e600cf1f575a57afd435e548665404d`.
  Contains the intent/Hermes work plus Mesh Registry and Jev catalog additions.
  It diverges from nightly; the registry additions are catalog-only and do not
  replace the nightly intent/authority semantics used here.
- `feat/validator-importable-api-12` — exposes an importable validator, but is
  based directly on the old main head. Do not use it as the semantic authority
  until rebased onto the current formal contract.

## z0int mapping

The latest intent-contract profile distinguishes three objects:

1. **Intent contract** — `intentGraph + policies + constraints + provenance`.
2. **Compiled strategy** — `plan`.
3. **Observed runtime** — `eventLog` and optional `observedGraph`.

Therefore z0int keeps the stable capability/participation contract in
`intentGraph` and moves the concrete routine -> specialist -> local-SLM ->
frontier cascade entirely into `plan`.

A promoted routine, new MB checkpoint, changed confidence threshold, or Kerdoios
provider decision changes `plan.planHash`; it does **not** change the AODL
intent `provenance.sourceHash`.

The intent executor carries an AODL harness id and fails closed for unknown ids
or control rooms (`o8`). Current executor ids are:

`hermes`, `omp`, `grok`, `codex`, `claude`, `pi`, `fx`.

Observed concrete routing can be projected into `observedGraph`; route and
verified outcomes are also emitted as closed AODL `route` / `stateUpdate`
events. Gamma budgets include token, premium-token, latency, USD, joule and
human-attention dimensions while observed spend remains separate.

No z0int implementation concept becomes a new AODL node kind.

## Action-level enforcement (feat/aodl-grants-v0)

Per-action grants compile from node `authorityScopes` / `prohibitions`, added on
`kvnloo/aodl@feat/authority-scopes` (`spec/authority-scopes.md`, on top of
`feat/canonical-fingerprint` + `fix/validate-non-scalar-fields`). Install that
branch editable so `harnesses/catalog.json` resolves; without `aodl_contract`
every contract is rejected (no grants). See `docs/aodl-enforcement.md`.
