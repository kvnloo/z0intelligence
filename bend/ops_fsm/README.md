# Ops plane in Bend

The owner's operating rules O1-O9 (agreed 2026-10-21 from that day's incidents) as decision
functions with proven laws. The gates that use them are documented in `docs/fsm/ops.md`.

```
ops.bend       decision functions and the O8/O9 machines (constructors are prefixed: names are global)
LAWS.bend      32 claims under O1-O9 (the owner's spec; do not weaken to pass)
PROOF.bend     proofs (hand-written; no generator)
mutations.py   41 broken variants of ops.bend; each must make PROOF.bend fail
receipt.json   `hermes bend verify` receipt
```

Run on groot (Bend 2.0.34, Lean 4.34.0):

```bash
cd bend/ops_fsm
bend PROOF.bend
PATH=/usr/bin:$PATH:~/.elan/bin bend PROOF.bend --verdict
rm -f receipt.json && hermes bend verify $PWD --receipt receipt.json
python3 mutations.py --verdict --out mutations.json      # 41/41 must be rejected
```

| law | claims (Bend names) |
| --- | --- |
| O1 | `o1_groot_admits_only`, `o1_never_groot_fallback`, `o1_full_waits`, `o1_mbp_takes_heavy` |
| O2 | `o2_served_refuses`, `o2_txn_can_ff`, `o2_txn_can_rollback`, `o2_rollout_ff_idle` |
| O3 | `o3_promoted_needs_clean`, `o3_promoted_needs_check`, `o3_failed_check_rolls_back`, `o3_admissible_fail_rolls_back`, `o3_admissible_pass_promotes`, `o3_promoted_needs_review`, `o3_no_device_work_in_call` |
| O4 | `o4_private_stays_private`, `o4_nonprivate_free` |
| O5 | `o5_touch_needs_intent`, `o5_touch_needs_foreground`, `o5_no_chain_install_in_call`, `o5_no_chain_restart_in_call` |
| O6 | `o6_a11y_superset` (per element, by induction over the pre-install list) |
| O7 | `o7_no_cancelled_provider`, `o7_no_sol_design` |
| O8 | `o8_no_silent_strike`, `o8_notify_once`, `o8_notified_sticks`, `o8_emit_marks_notified` |
| O9 | `o9_restart_interrupts`, `o9_restart_never_fails`, `o9_resume_needs_session`, `o9_interrupted_exits` |

Bend notes from this plane: a law hypothesis of a `Data` type used twice needs a `+` rebind in
the proof (`+ok2 = ok`); a decision function may not `match` a computed value (give it a
helper like `npick`); after matching one parameter, match a sibling only inside a helper def.
