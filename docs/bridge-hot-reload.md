# z0int bridge hot-reload (v2)

## Architecture

```
OMP session
  └── omp-extensions/z0int-bridge/index.ts   # IMMUTABLE shim (loaded once)
        ├── input("/reload-plugins") → transactional worker swap
        ├── before_agent_start → mint trace_id → worker.turn_open
        ├── turn_end / agent_end → worker.turn_close(explicit trace_id)
        └── python -u -m z0int.bridge.worker   # hot-swappable generation
```

## One-restart caveat

OMP does **not** reload extension modules on `/reload-plugins`.

After installing this shim (or changing `index.ts`), **restart OMP sessions once**.

After that:

| Change | Restart OMP? |
|--------|--------------|
| `src/z0int/bridge/*.py` | **No** — `/reload-plugins` or `/z0int-bridge-reload` |
| `index.ts` shim | **Yes** — one restart |
| skills / MCP / commands | No — normal `/reload-plugins` |

## Transactional reload

1. Spawn generation N+1  
2. `hello` + protocol check  
3. `self_check`  
4. Atomic pointer swap + publish `~/.z0int/runtime/bridge-current.json`  
5. Drain/stop generation N  

If N+1 fails: keep N. Never accept traffic on a broken generation.

Reload requested during a turn is queued and run when that turn closes (`turn_in_flight`). It is not a failed reload.

## Per-session trace ownership

- Shim owns `trace_id` (not the worker).  
- Open state: `~/.z0int/stream/sessions/<session>__pid<omp_pid>/open.json`  
- **No** shared global `last_open.json` as authority (eliminates multi-OMP collisions).

## Stamps

Every bridge row includes:

- `bridge_protocol` (`z0int.bridge.v2`)
- `bridge_generation`
- `bridge_instance_id`
- `bridge_build_id` (working-tree fingerprint, not HEAD alone)
- `omp_session_id`, `omp_pid`, `trace_id`

Stale writers (generation < current) are quarantined to  
`~/.z0int/stream/bridge_quarantine.jsonl`.

## Env

| Var | Default |
|-----|---------|
| `Z0INT_ROOT` | tree with `src/z0int` (integrate tip) |
| `Z0INT_PYTHON` | venv python |
| `Z0INT_BRIDGE_TIMEOUT_MS` | 45000 |

## Still log_only

Host execution is not flipped live. `execution_completed` ≠ `verified_success`.
