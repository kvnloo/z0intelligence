# AODL compatibility target

z0int's Routine/Cascade compiler targets the **AODL intent-contract semantics**
at the exact `aodl-contract` commit pinned in `pyproject.toml`. Main and the
rolling channels are references, not implicit runtime upgrades.

## Relevant upstream refs checked

- `kvnloo/aodl@main` — `416736c80a727757202bdeedb02579c3c820f7f6`
  (Sep 30). Main now contains `profiles/intent-contract.md`, the importable
  validator, `observedGraph`, fail-closed harness validation, closed event
  types, and the intent/plan/observed separation. The older note that main
  lacked the intent-contract profile is obsolete.
- z0int's package dependency is pinned to
  `68231658f0ec0338464c0916a2329b9587444312` (Oct 1), which is the current
  runtime semantic authority for this repo. It adds `aodl-canon-1` canonical
  semantic fingerprints on top of the Sep 30 main head.
- AODL `nightly` / `preview` can move ahead of that pin. Treat newer channel
  work as research until the z0int dependency pin is intentionally advanced and
  its contract tests pass.

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
