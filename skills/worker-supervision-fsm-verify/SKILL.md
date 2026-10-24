---
name: worker-supervision-fsm-verify
description: Use when changing the worker supervision statechart or supervise CLI. Re-prove laws, re-check parity.
---

# Verify the worker supervision statechart

## Covered files

| file | check |
| --- | --- |
| `bend/worker_supervision_fsm/fsm.bend` (`step`, `run`, `lstep`, `holds`, `peff`, `pstep`) | `bend PROOF.bend --verdict`, `mutations.py --verdict`, parity test |
| `bend/worker_supervision_fsm/LAWS.bend` | the owner's spec: change a law only with the owner's explicit agreement, never to get a PASS |
| `src/z0int/fsm/worker_supervision.py` | `tests/test_fsm_worker_supervision.py` (twin, Bend parity, diagram freshness) |
| `src/z0int/fsm/supervise.py`, `ws_parity.py` | `tests/test_fsm_worker_supervision.py` (synthetic delegation logs, state.db, fleet runs, git) |

Sync anchor: receipt `c1de0af04307442bb636885eac5f0f9e`. If `hermes bend
replay` reports changed inputs, re-run the proof before relying on it.

## Run

Bend and Lean exist only on groot (the declared toolchain exception); the rest
goes to the fleet.

```bash
cd bend/worker_supervision_fsm
python3 gen_proof.py
flock ~/.hermes/profiles/clean/cache/scratch/heavy.lock nice -n 10 sh -c '
  bend PROOF.bend && PATH=/usr/bin:$PATH:~/.elan/bin bend PROOF.bend --verdict &&
  python3 mutations.py --verdict --out mutations.json &&           # 17/17 rejected
  rm -f receipt.json && hermes bend verify $PWD --receipt receipt.json'
cd ../.. && PYTHONPATH=src python3 -m pytest -q tests/test_fsm_worker_supervision.py -k parity   # groot (Bend)
cd /workspace/.files-k3s && scripts/fleet run --node mbp --src $OLDPWD -- \
  sh -c 'python3 -m venv /tmp/v && /tmp/v/bin/pip install -q pytest && PYTHONPATH=src /tmp/v/bin/python -m pytest -q tests/test_fsm_worker_supervision.py'
PYTHONPATH=src python -m z0int.fsm.supervise --mermaid docs/fsm/worker-supervision.mmd   # if TRANSITIONS changed
```

## Pitfalls

- Deferred tools (process_manage, phone_exec) are wrapped in `tool_call` in
  state.db; unwrap them or background-process ends go missing.
- "Child left N background process(es)" notices sit at the end of long
  delegate_task results; read them in full, not from a truncated column.
- Sibling tasks of one delegation name each other's worktrees in their
  kickoff; a successor must start after the old worker's last write.
- Shared checkouts (`.files-k3s`) carry other sessions' edits: count only
  files the worker itself wrote as its unique work.

## Not covered

The event mapping is heuristic and only unit-tested. A PASS is never an
authorization, an AODL admission or verified_success.
