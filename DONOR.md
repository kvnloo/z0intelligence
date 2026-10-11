# Donor: memory

Cut from `dd39238` (tag `archive/pre-reorg-dd39238`) as one commit. Not on master.
`README.md` is the old whole-repo README; this file describes the branch.

## What it is

Three unrelated pieces that the audit grouped as "memory": an append-only,
checksummed event ledger with a summary-tree projection over it
(`src/z0int/memory/`), a set of dataclass contracts for a memory control plane
that was never built (`src/z0int/memory_contract.py`), and an importer that turns
desktop-activity episodes from a separate "workspace-copilot" database into
training files plus a shadow "next operator" predictor (`src/z0int/os_context.py`,
`src/z0int/specialists/`). Only the ledger's `append` had a caller on the audited
host; nothing reads what it writes.

## Entry points

Event ledger:

- `src/z0int/memory/event_log.py:138` `EventLog`; `:359` `append`, `:441`
  `iter_events`, `:500` `get`, `:535` `read_blob`.
- `src/z0int/context_resolve.py:678` `append_context_packet_event`, which imports
  `EventLog` at `:691` and appends one `context.resolve` event. This is the only
  non-test caller of the ledger in the source commit. Its own caller (the bridge
  worker's `file_search` op) is not on this branch; see `donor/file-search`.

Summary tree:

- `src/z0int/memory/optmem_tree.py:126` `OptMemTree`; `:268` `cover`, `:330` `zoom`.
  No caller outside tests.

Contracts:

- `src/z0int/memory_contract.py`. No importer outside `tests/test_memory_contract.py`.

OS episodes:

- `src/z0int/os_context.py:262` `import_from_db`, `:452` `import_via_cli`, `:470`
  `stats`.
- `src/z0int/specialists/next_operator.py:152` `shadow_report`, `:67` `predict`.
- In the source commit these were reached only through `z0int os-context
  import|stats|next-operator` (`cli.py:480-487`, `1145-1160`). `cli.py` is NOT on
  this branch (see "Known defects"); call the functions directly.

All of these import on this branch.

## Tests

```
PYTHONPATH=src python -m pytest -m 'not live' -q
```

86 passed on the trimmed tree in a clean environment:
`test_memory_event_log.py`, `test_memory_optmem_tree.py`, `test_memory_contract.py`,
`test_os_context.py`, plus `test_context_resolve.py` and `test_file_search.py`
(they cover the two extra modules kept for the ledger's caller) and
`test_check_receipt.py` (covers `.github/scripts/check-receipt.py`). All 12
modules under `src/` import. The trimmed `core-unit` CI job (py_compile of three
files and the two ledger unittest classes, 17 tests) also passes locally.

The file-search tests ran against a venv with the `fff` Python binding
installed. They were not tried without it.

## What was kept beyond the assignment, and why

- `src/z0int/context_resolve.py` and `src/z0int/file_search.py` (which it
  imports): kept so the ledger has its one real writer on the branch. Their tests
  came with them.
- `src/z0int/specialists/__init__.py`, `next_operator.py`: imported by
  `tests/test_os_context.py`.
- `src/z0int/receipt.py`: imported by `tests/test_memory_contract.py` for one
  projection test. It is here as a dependency, not as part of this donor.

## Hot-path cost

- Ledger: on the audited host every `z0_file_search` tool call appended one event
  before the tool returned, and "each search pays 3 fsyncs for it" (audit). No
  reader consumes the result.
- `optmem_tree`, `memory_contract`, `os_context`, `specialists`: none. Nothing on
  any turn path calls them.

## Evidence (from the audit of dd39238)

- `memory/event_log.py` (578 lines with `__init__`): "NONE. iter_events/get/read_blob
  have no caller outside the memory package and tests; the log is 1.68 MB / 769
  rows that nothing reads, and each search pays 3 fsyncs for it." Verdict DELETE,
  confidence medium. "Write-only data on a tool's hot path."
- `memory/optmem_tree.py` (353 lines): entry point chain "NONE (only re-exported by
  memory/__init__.py:8 and imported by tests)". Verdict DELETE, confidence high.
  The old README calls it the predecessor of OptChat, which has its own tree.
- `memory_contract.py` (401 lines): "Zero importers anywhere except
  tests/test_memory_contract.py"; "400 lines of schema for a system that was never
  built on top of it." Verdict DELETE, confidence high.
- `os_context.py` + `specialists/` (652 lines): "No hook, unit, CI job or script
  invokes it"; outputs "were last written 2026-09-18". Verdict DELETE, confidence
  high. "One-off shadow specialist experiment from 2026-09-26 with no caller and
  no promoted artifact."
- The audit's skeptic pass added one correction: the ledger write is live code,
  not dead code. Removing `memory/event_log.py` while
  `append_context_packet_event` stays breaks every file search.

There is no measurement showing a token or latency benefit from any file here.

## Did it run live on the audited host?

- `EventLog.append`: yes, through the file-search tool; the ledger file was
  written on the audit day.
- Everything else: no.

## Known defects

- No `z0int os-context` command: `src/z0int/cli.py` is not on this branch. Keeping
  it would pull in about 75 more files (the CLI imports the cognition package at
  module level) for about 20 lines of argument parsing.
- `tests/test_receipt.py` was dropped although `receipt.py` is kept, because that
  test calls `z0int.cli.main`. `receipt.py` is therefore not covered by its own
  test file on this branch. The `turn-receipts` donor is the place for it.
- `import_via_cli` shells out to an external `workspace-copilot` program and
  `import_from_db` reads its SQLite database; neither ships here. The tests use
  synthetic rows.
- `context_resolve.py` on this branch still contains its recipe cache, QMD tier
  and AODL projection, none of which this donor needs.
- `docs/os-episode-compiler.md` and `docs/memory-control-plane-contract.md` may
  name CLI commands and modules that are not on this branch.
- `tests/test_architecture_manifest.py` was dropped: it asserts every path in
  `zer0.repo.yaml` exists, and that file is kept unmodified as the whole-repo map.
- `pyproject.toml` still declares the whole-repo dependency set and the name
  `openjev-phase1`; only console scripts for removed modules were trimmed. The CI
  step name "Receipt and bridge runtime tests" now runs only the ledger tests.

## To earn a place on master

A consumer. The ledger earns a place when something on a turn path reads events
back (`iter_events`, `get`, or an `OptMemTree.cover`) and one paired run shows
fewer frontier tokens per verified task with that read than without it. Until a
reader exists, the write is pure cost and should stay off master.

Smallest subset worth taking, if a reader appears:

- `src/z0int/memory/event_log.py` and a one-line `memory/__init__.py`
- `src/z0int/paths.py` (the only first-party import of `event_log.py`)
- `tests/test_memory_event_log.py`

`optmem_tree.py` only if the reader wants bounded summaries rather than raw
events. `memory_contract.py`, `os_context.py` and `specialists/` have no path to
master on current evidence; they are here for reference.
