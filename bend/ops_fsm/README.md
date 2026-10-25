# Ops plane in Bend

The owner's operating rules O1-O13 and O15 as decision
functions with proven laws. The gates that use them are documented in `docs/fsm/ops.md`.

```
ops.bend       decision functions and the O8/O9 machines (constructors are prefixed: names are global)
LAWS.bend      71 claims under O1-O13, O15 (the owner's spec; do not weaken to pass)
PROOF.bend     proofs (hand-written; no generator)
mutations.py   79 broken variants of ops.bend; each must make PROOF.bend fail
receipt.json   `hermes bend verify` receipt
```

Run through the MBP fleet queue, with the matching Bend toolchain and its
precompiled Lean-kernel checker supplied as a private job input. Do not install
a host toolchain or run the checks on groot. Set `BEND` to the job's absolute
checker path and place its directory on `PATH`. From the repository root:

```bash
(cd bend/ops_fsm && "$BEND" PROOF.bend --verdict)
python3 bend/ops_fsm/mutations.py --verdict --out /tmp/ops-mutations.json
PYTHONPATH=src python3 -m pytest -q tests/test_fsm_ops.py -k parity
```

Require `ALL PROOFS CHECK`, a passing unmodified reference, **79/79 mutants
rejected**, and both parity tests passed (not skipped). Five mutants cover O10,
nine cover O11/O12. The risk-capacity revision adds two O13 risk mutants
and changes absent evidence from binary capacity to zero capacity.
Policy mutants preserve expression types; rejection must occur in a proof,
not because a mutant fails to parse.

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
| O10 | `o10_phone_no_agent_resource_caps`, `o10_phone_placement_no_agent_resource_caps` |
| O11 | `o11_evidence_bound_route`, `o11_named_family`, `o11_named_arm`, `o11_none_not_credited`, `o11_shadow_not_credited`, `o11_canary_routes`, `o11_live_routes` |
| O12 | `o12_offload_obligation`, `o12_unhealthy_can_fallback`, `o12_abstain_can_fallback` |
| O15 | `o15_sway_always`, `o15_look_without_moving`, `o15_open_needs_collab`, `o15_input_needs_collab`, `o15_input_needs_ask`, `o15_moving_look_is_input`, `o15_collab_opens`, `o15_collab_drives`, `o15_collab_routes_live`, `o15_unattended_routes_sway`, `o15_no_live_ask_routes_sway`, `o15_collab_resolves`, `o15_solo_reports`, `o15_no_ask_reports` |
| O13 | `o13_option_capacity`, `o13_default_zero`, `o13_exact_scope`, `o13_credited_limit`, `o13_k2_rejected`, `o13_k24_rejected`, `o13_k119_rejected`, `o13_credited_excess_rejected`, `o13_destructive_never`, `o13_unknown_never`, `o13_read_authority`, `o13_reversible_authority` |

Bend notes from this plane: a law hypothesis of a `Data` type used twice needs a `+` rebind in
the proof (`+ok2 = ok`); a decision function may not `match` a computed value (give it a
helper like `npick`); after matching one parameter, match a sibling only inside a helper def.
