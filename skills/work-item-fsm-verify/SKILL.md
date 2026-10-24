---
name: work-item-fsm-verify
description: Use when changing the work-item statechart (bend/work_item_fsm, src/z0int/fsm). Re-proves its laws and re-checks Python/Bend parity.
---

# Verify the work-item statechart

## Does my change touch it?

Covered files and what checks them:

| file | check |
| --- | --- |
| `bend/work_item_fsm/fsm.bend` (`step`, `run`, `astep`, `board_step`, role guards) | `bend PROOF.bend --verdict`, `mutations.py --verdict`, parity test |
| `bend/work_item_fsm/LAWS.bend` | the owner's spec: change a law only with the owner's explicit agreement, never to get a PASS |
| `src/z0int/fsm/work_item.py` (`step`, `astep`, `TRANSITIONS`, `LAWS`) | `tests/test_fsm_work_item.py` (incl. Bend parity, diagram freshness) |
| `src/z0int/fsm/replay.py`, `export.py` | `tests/test_fsm_work_item.py` (replay fixtures) |

Sync anchor: the verified inputs have `input_manifest_sha256
66fd97dbcc22fee9ba9f9ac4f48ad46be499095036a4d6501b5d6f16e815c89f` (receipt
`9cebaacdfcd24e5383994d2d4601ad2b`). If `hermes bend replay` reports changed
inputs, the proof must be re-run before anyone relies on it. Move the anchor
(new receipt) only after the steps below pass on the new commit.

## Run

```bash
cd bend/work_item_fsm
python3 gen_proof.py                                    # regenerate if proof logic changed
bend PROOF.bend                                         # fast gate (~1 s)
PATH=/usr/bin:$PATH:~/.elan/bin bend PROOF.bend --verdict
python3 mutations.py --verdict                          # must print 10/10 rejected
rm -f receipt.json && hermes bend verify $PWD --receipt receipt.json
cd ../.. && PYTHONPATH=src python -m pytest -q tests/test_fsm_work_item.py
PYTHONPATH=src python -m z0int.fsm.export --mermaid docs/fsm/work-item.mmd   # if TRANSITIONS changed
```

Heavy steps on groot: prefix with `flock ~/.hermes/profiles/clean/cache/scratch/heavy.lock nice -n 10`.

## Reading a failure

- `SOME PROOFS FAIL` at a `Laws.*` def or at an `inv_*`/`*_lem` helper:
  either the change broke a law (a real finding: describe it as a sequence of
  events, e.g. "Deployed, Observe(other head) stays Deployed") or the proof
  needs the new case. Add a mutant for the new behaviour to `mutations.py` and
  confirm it is rejected before trusting a fixed proof.
- Parity test fails: Python and Bend disagree. Bend is the reference; the
  first mismatching tape index is in the assertion. Fix the twin, not the tape.
- A new event constructor: add it to `EVENTS` in `gen_proof.py`, to
  `owner_event` in fsm.bend, and to `bend_parity.bend_event`.
- `unsupported_bend` from `hermes bend verify`: the `bend` wrapper must not
  depend on `$HOME` (the adapter isolates it).

## Not covered

The replay checker's event mapping (GitHub, services, delegation logs) is
heuristic and only unit-tested. A PASS is never an authorization, an AODL
admission or verified_success.
