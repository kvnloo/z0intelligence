# Hermes + Jev + Bend + z0intelligence dogfood (2026-10-04)

Status board and operating notes for the local stack. Evidence index only; raw receipts stay on the
host (they contain local paths). Nothing here adds a scheduler, router, receipt ledger or authority.

## Revisions
| component | revision |
| --- | --- |
| bend-native | `e85e65e5d` installed; fix on branch `feat/task-repo-identity` (`bcea8cbb4`) |
| z0intelligence | `0159808f6` (master) |
| Bend | 2.0.34, binary sha256 `7fafb749...`; fork `17db447a8` |
| aodl | `416736c80`, `aodl-canon-1` |
| hermes-jev-skills | `9ea57773e` |

## Files
- `dogfood` - thin wrapper over existing CLIs: `doctor [--full]`, `up`, `hermes`.
- `z0intelligence.service.d-dogfood.conf` - user-systemd override that runs the current checkout; delete + `daemon-reload` to revert.
- `gpu-smoke-receipt.json` - `pow2!(20n)` CPU vs GPU, compute evidence only.
- `doctor-final.json` - the 7-row board at the end of the run.

## Verified
Bend PASS / deliberate FAIL / replay / stale-replay (`receipt_stale`); GPU result equality and no silent
fallback; z0 `/healthz` `/readyz` (AODL ready) `/v1/providers` `/metrics`; Hermes shadow events carrying
session/trace/turn/api-request/tool-call identity with Bend evidence labelled
`scoped_proof_evidence_not_task_success`; z0-down, bad-Jev-key and GPU-broken degrade explicitly.

## Not yet done
No real z0 plan/admission/outcome observation was sent; `laya_421m` backend not installed; DSH execution
and DAG measurements not started. The `bend-native` repo-identity fix (task repository instead of process
cwd) is on its own branch.
