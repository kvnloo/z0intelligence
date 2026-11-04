# Ops plane in Bend

The owner's operating rules O1-O13 and O16 as decision
functions with proven laws. The gates that use them are documented in `docs/fsm/ops.md`.

```
ops.bend       decision functions and the O8/O9 machines (constructors are prefixed: names are global)
LAWS.bend      68 claims under O1-O13 and O16 (the owner's spec; do not weaken to pass)
PROOF.bend     proofs (hand-written, except the O18 case splits from gen_o18.py)
mutations.py   76 broken variants of ops.bend; each must make PROOF.bend fail
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

Require `ALL PROOFS CHECK`, a passing unmodified reference, **76/76 mutants
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
| O13 | `o13_option_capacity`, `o13_default_zero`, `o13_exact_scope`, `o13_credited_limit`, `o13_k2_rejected`, `o13_k24_rejected`, `o13_k119_rejected`, `o13_credited_excess_rejected`, `o13_destructive_never`, `o13_unknown_never`, `o13_read_authority`, `o13_reversible_authority` |

| O16 | `o16_sqlite_replace_no_holders`, `o16_sqlite_replace_scan_complete`, `o16_sqlite_no_ghost_holders`, `o16_sqlite_auth_non_decreasing`, `o16_sqlite_auth_known`, `o16_sqlite_sidecar_current_inode`, `o16_sqlite_quiescent_replace`, `o16_sqlite_healthy_holders`, `o16_sqlite_auth_retained`, `o16_sqlite_current_sidecar`, `o16_sqlite_unknown_holders` |

Bend notes from this plane: a law hypothesis of a `Data` type used twice needs a `+` rebind in
the proof (`+ok2 = ok`); a decision function may not `match` a computed value (give it a
helper like `npick`); after matching one parameter, match a sibling only inside a helper def.


## SQLite lifecycle verification (O16)

`sqlite-receipt.json` and `receipt.json` cover the same 68-claim book (Bend
2.0.34, `source_semantics_attested: false`). `sqlite-mutations.json` and
`mutations.json` record 76/76 rejections, including 11 O16 variants. The MBP
fleet run passed 203 tests (ops, SQLite guard, policy guard), with parity not
skipped. `sqlite-parity.json` records 1007 equal cases; the O16 Boolean domains
are exhaustive and credential counts use the bounded set {0,1,9,10}.

```sh
PYTHONPATH=src python3 -m z0int.fsm.guard sqlite-ghosts --db ~/.omp/agent/agent.db
PYTHONPATH=src python3 -m pytest -q tests/test_fsm_sqlite_guard.py
PYTHONPATH=src python3 -m z0int.fsm.ops_replay tests/fixtures/ops_tapes/o16-sqlite-store.json
```

The command only observes; use `--operation replace|unlink|recreate|remove-sidecars`
to refuse a *proposed* action, never execute it. No timer, service or OMP hook
was deployed. Use this checkout's `PYTHONPATH=src` until a stamped backend
containing O16 is deployed; an older installed `z0-guard` may lack the command.

## O18 privacy-tier model routing

`route(tier, task, local, paid, free_ok, free_pub)` picks the model lane for a request. Tier eligibility
is the owner's table in `sft-svlm/data/privacy/README.md` (pol-1.2, approved 2026-10-20):

| tier | lanes allowed |
|---|---|
| T0 public | local, paid, approved free (Cerebras), public free (may train on prompts) |
| T1 personal | local, paid (Claude/ChatGPT/Codex/Grok; Grok approved 2026-11-03), approved free |
| T2 sensitive, T3 third-party, unclassified | local only |
| T4 secret | none (refuse) |

Free lanes take only simple work (no tools, no code). Agentic T0/T1 work uses a paid lane or waits;
it never falls back to the local model. A session's tier is the join (most restrictive) of the
profile floor, every path it read and a secret scan of the outgoing request.

16 laws (`o18_*` in LAWS.bend). The exhaustive case-split proofs are generated by `gen_o18.py` from
the twin and the independent spec in LAWS; Bend re-checks every leaf. Exhaustive parity: 192 routes,
36 joins, 24 tier/lane cells. 10 mutants (`mutations.py --law O18`).

Host side (heuristic, not proven): `src/z0int/fsm/privacy_route.py` (path tiers over the tiered
store, secret/PII content scan, lane availability from the router's `/v1/models`, lane -> virtual
model). Consumer: `adapters/hermes-privacy-route/` (Hermes plugin: `llm_request` middleware rewrites
the model; `pre_tool_call` blocks T4 reads). Router side: z0intelligence `deploy/vllm-sr/privacy_lanes.py`
checks that each lane's pool holds only backends its tiers may reach.

```sh
python3 bend/ops_fsm/gen_o18.py && (cd bend/ops_fsm && "$BEND" PROOF.bend --verdict)
python3 bend/ops_fsm/mutations.py --verdict --law O18
PYTHONPATH=src python3 -m pytest -q tests/test_fsm_privacy_route.py
PYTHONPATH=src python3 -m z0int.fsm.privacy_route decide --tier personal --task agentic --live
```

## O19 spending: no real money without the owner's approval

Owner, 2026-11-04, after an agent launched four Modal H200s on free credit with
nothing stopping it at the credit: "set a law such that u cannot accidentally
even spend money thru a paid LLM or something without approval."

`spend_allows(funding, approved, fits, armed)`:

| funding | allowed when |
| --- | --- |
| free tier (no card behind it) | always |
| flat subscription already paid (Claude, ChatGPT, Grok, Nous, Copilot) | always |
| free credit that could overflow onto a card (Modal $30/month) | owner approval, or month spend + estimate under the cap ($26) **and** the hard stop armed |
| metered pay-as-you-go (API keys, cloud GPUs, HF Jobs) | owner approval only |
| unclassified | owner approval only (fails closed) |

Claims: `o19_free_allowed`, `o19_subscription_allowed`, `o19_metered_needs_approval`,
`o19_unknown_refused`, `o19_credit_unguarded_refused`, `o19_credit_over_cap_refused`,
`o19_credit_guarded_allowed`, `o19_approval_allows` (8). Nine O19 mutants
(`mutations.py --law O19`) each break one row; every one must be rejected.

Host facts and call sites (`src/z0int/fsm/spend_gate.py`, not proven):

- **funding** of a command (`modal run|deploy|serve|shell|launch`, Python that calls
  `modal.Function.from_name(...).remote`, `hf jobs`, RunPod, Vast, SkyPilot, cloud
  CLIs, `https://` calls to paid API hosts) or an LLM provider request (by provider
  name, then base-URL host). Owner overrides: `~/.config/z0-guard/spend-funding.json`.
- **approved**: an unexpired row in `~/.config/z0-guard/spend-approvals.json`, written
  only by `z0-guard spend approve` from a real TTY outside a Hermes session. The
  Hermes plugin blocks any agent tool call that mentions that file, the funding file
  or the O19 switch. Chat text is never an approval.
- **fits**: live `modal billing report` month total (cached 60 s) + estimate < cap;
  unreadable billing counts as not fitting.
- **armed**: `systemctl --user is-active z0-spend-stop@modal.timer`. The timer runs
  `spend_gate stop-tick` every 2 minutes and stops every non-stopped Modal app once the
  month reaches the cap, whoever launched it.

Runtime: `adapters/spend-stop/install.sh [HERMES_HOME]` stamps the kernel to
`~/.local/lib/z0-guard/spend/<sha>`, installs and starts the timer, and copies the
`spend-gate` Hermes plugin (pre_tool_call + llm_request). Not covered: Hermes
auxiliary calls (they skip llm_request middleware), other harnesses (OMP, Codex CLI,
Claude Code) until they get their own adapter, and SDKs the classifier does not know.
