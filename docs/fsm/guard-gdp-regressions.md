# Guard critic regressions: F3, F4 and F1

Local-only work on `fix/guard-gdp-f3-f4-f1`, based on
`feat/sqlite-store-laws-20261025@d2e34614638059b38da77fd5f0bb582af500a503`.
The owning repository is `kvnloo/z0intelligence`; production wrappers are in
`src/z0int/fsm/guard.py`, scalar twins in `src/z0int/fsm/ops.py`, and the
proof family is `bend/ops_fsm/{ops,LAWS,PROOF}.bend`.

## Findings and changes

- **F3 (production bug):** a rollout's decision to roll back was mistaken for
  completed rollback. Require successful reset, previous-HEAD readback,
  successful restart and healthy readback before reporting `rolled-back`.
  Otherwise emit and ledger `rollback-failed`, retaining exit 1 and diagnostics.
  Two new completion laws distinguish intent from observed completion; the
  Python twin checks all 16 Boolean combinations. Wrapper regressions use
  scratch Git repositories and simulate reset failure, unchanged HEAD,
  restart failure, failed health readback and successful restoration.
- **F4 (production bug):** an emitting enforce-mode O8 transition is committed
  only after explicit `delivered: true`. Refusal, absent acknowledgment,
  OSError and subprocess failure retain the original unhealthy episode for
  the next persisted-state probe. Successful delivery consumes the notification;
  recovery starts a new episode. Report-only behavior remains unchanged.
  Three new laws cover state preservation, acknowledged commit and preserved
  retry eligibility; Python assertions mirror each claim.
- **F1 (law gap, not a baseline implementation bug):** the reference already
  rejects risk IDs >=4. The adjudicator's REPORT explicitly distinguishes a
  planted risk=4-only admission mutant from shipped behavior. Do not widen
  the reference's stricter read=0/reversible=1 authority or fabricate baseline
  red. Add universal laws rejecting risk `4 + extra` (every natural ID >=4)
  both directly and for every capacity request. Python twins cover IDs
  4, 5, 16, 255, 2**32 and 2**63-1, including zero-count and credited requests.
  A separate control preserves acceptance at 0/1 and rejection at 2/3.
  No F1 production predicate change is necessary.

## Verification

All substantive tests and proof runs were sequential MBP fleet jobs (one CPU,
2 GiB). No tests run against a live deployment. Raw fetched evidence is outside
Git at `/home/kvn/tmp/guard-fix/evidence/`.

| Finding | Red evidence | Green evidence |
|---|---|---|
| F3 | `f3-red-venv/out/tests.log`: 4 failed, 1 positive control passed | `f3-green/out/tests.log`: 6 passed; `f3-proof/out/proof.log`: ALL PROOFS CHECK |
| F4 | `f4-red/out/tests.log`: all 4 delivery failure regressions failed | `f4-green/out/tests.log`: 7 O8 tests passed; proof ALL PROOFS CHECK |
| F1 | `f1-red/out/mutant-tests.log`: risk=4 mutant fails (1 failed, 6 passed) | `f1-red/out/reference-tests.log`: 7 passed, unmodified reference |

Final job `fleet-mbp-dc94c84373d54764`, fetched as `evidence/final/out/`:

```sh
PYTHONPATH=src BEND="$HOME/.bend/bin/bend" python -m pytest -q \
  tests/test_fsm_ops.py tests/test_fsm_sqlite_guard.py tests/test_guard_policy.py
# 221 passed in 42.75s; no skipped tests (Bend parity included).
(cd bend/ops_fsm && "$HOME/.bend/bin/bend" PROOF.bend --verdict)
# ALL PROOFS CHECK: 75 laws, including the seven added here.
python bend/ops_fsm/mutations.py --verdict --out out/mutations.json
# reference passes; all 76 existing mutants rejected; reference restored.
```

The separate `verify-f1.py` evidence harness applies the exact adjudicated
Bend risk=4-only mutant in a disposable job copy. It independently typechecks
that model; new proofs reject it at `Laws.o13_unrecognized_risk_never`.
Removing only the two new F1 laws/proofs makes the identical mutant pass
`--verdict`. Thus the added theorem detects a real coverage gap, not a parser
failure or an unrelated legacy proof failure. Raw commands and diagnostics are
in `final/out/{commands.json,f1-verification.log,*-proof.log,mutant-*.log}`.
The first attempt at that harness had a law-removal regex error; it was fixed
and the final complete harness passed. This did not change production code.

## Scope and limits

The experiment's other pins (`host-tracked-source@f46ecbf` and
`aodl-intent-contract@d75c944`) own different families; neither was modified.
These lemmas prove decisions over supplied IO facts, not physical Git,
service health, notification transport, or crash-atomic delivery/state writes.
Report-only O8 still models one notification without delivering it. This patch
does not add a distributed exactly-once transport guarantee.

No push or PR, live deployment, service configuration, systemd unit or Hermes
configuration was changed. No unresolved test failure remains in the relevant
suite; the full repository's unrelated ML/product suites were not run.
