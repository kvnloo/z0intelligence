# Frozen AODL reference (read-only)

Verbatim copy of four paths from `kvnloo/aodl` at
`a848270356225699fc828e80a0cc93d2754f7413` (`main`, 2026-09-26), used only
as the frozen differential corpus for the Bend structural-gate experiment
(`bend/aodl_gate`, `benchmarks/bend_gate_parity.py`):

- `aodl_contract/__init__.py`, `aodl_contract/validator.py` — the canonical
  fail-closed validator (sha256 of validator.py:
  `60a0c87288b655039687be5bec168318df63eae621f38f506b0e4112c0539e2c`)
- `harnesses/catalog.json` — the harness catalog the validator reads
- `examples/` — the valid / invalid / compile-stop fixtures
- `LICENSE` — AODL's MIT license

Do not edit these files. AODL remains the semantic authority; refresh this
snapshot from upstream (and re-run the parity harness) rather than patching it.
