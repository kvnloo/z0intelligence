#!/usr/bin/env python3
"""Ops plane: are the laws load-bearing? Apply one broken variant of ops.bend at a time
(in a scratch copy), run `bend PROOF.bend`, and require SOME PROOFS FAIL.

Each mutation is the bug a law exists to catch, written the way it would show
up in the gate. Every law O1-O13 has at least one mutant. The reference
ops.bend is never edited in place.

    python3 bend/ops_fsm/mutations.py [--verdict] [--out mutations.json]
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
BEND = os.environ.get("BEND", str(Path.home() / ".bend/bin/bend"))

# (law, description, old text, new text)
PHONE_ADMITS = ("def phone_admits(ready: Bool, -memory: Bool, -cpu: Bool, "
                "-battery: Bool, -thermal: Bool) -> Bool:\n  ready")
MUTATIONS = [
    # O1 compute placement
    ("O1 o1_groot_admits_only", "groot admits every class",
     "    case LGroot{}:\n      groot_class(c)", "    case LGroot{}:\n      True{}"),
    ("O1 o1_groot_admits_only", "unknown classes count as groot classes",
     "    case CTiny{}:\n      True{}\n    case _:\n      False{}", "    case CTiny{}:\n      True{}\n    case _:\n      True{}"),
    ("O1 o1_never_groot_fallback", "no room off groot: fall back to groot",
     "    case False{}:\n      Wait{}", "    case False{}:\n      RunOn{LGroot{}}"),
    ("O1 o1_full_waits", "no room anywhere: squeeze onto a phone anyway",
     "    case False{}:\n      Wait{}", "    case False{}:\n      RunOn{LPhone{}}"),
    ("O1 o1_never_groot_fallback", "placement ignores the class",
     "  place_by(groot_class(c), m, p)", "  place_by(True{}, m, p)"),
    ("O1 o1_mbp_takes_heavy", "heavy jobs wait even when the MBP has room",
     "    case True{}:\n      RunOn{LMbp{}}", "    case True{}:\n      Wait{}"),
    # O2 served worktree
    ("O2 o2_served_refuses", "served worktree accepts any op",
     "    case GRollback{}:\n      txn\n    case _:\n      False{}", "    case GRollback{}:\n      txn\n    case _:\n      True{}"),
    ("O2 o2_served_refuses", "rollback (moving the served branch backwards) outside the transaction",
     "    case GRollback{}:\n      txn", "    case GRollback{}:\n      True{}"),
    ("O2 o2_txn_can_rollback", "the transaction cannot roll back",
     "    case GRollback{}:\n      txn", "    case GRollback{}:\n      False{}"),
    ("O2 o2_served_refuses", "ff-merge outside the rollout transaction",
     "    case GMergeFF{}:\n      txn", "    case GMergeFF{}:\n      True{}"),
    ("O2 o2_served_refuses", "the served lease is ignored",
     "    case True{}:\n      served_allows(op, txn)", "    case True{}:\n      True{}"),
    ("O2 o2_txn_can_ff", "the transaction itself cannot merge",
     "    case GMergeFF{}:\n      txn", "    case GMergeFF{}:\n      False{}"),
    ("O2 o2_rollout_ff_idle", "rollout accepts a non-fast-forward merge",
     "  Bool.and(clean, Bool.and(ff, review_ok(gated, receipt)))", "  Bool.and(clean, review_ok(gated, receipt))"),
    ("O2 o2_rollout_ff_idle", "restart without the idle check",
     "  Bool.and(Bool.not(call), idle)", "  Bool.not(call)"),
    # O3 device rollout
    ("O3 o3_promoted_needs_clean", "candidate built in the served worktree is promoted",
     "  Bool.and(clean, Bool.and(ff, review_ok(gated, receipt)))", "  Bool.and(ff, review_ok(gated, receipt))"),
    ("O3 o3_promoted_needs_check", "a failed launch check is promoted anyway",
     "    case False{}:\n      ORolledBack{}", "    case False{}:\n      OPromoted{}"),
    ("O3 o3_admissible_fail_rolls_back", "a failed launch check is reported as deferred, not rolled back",
     "    case False{}:\n      ORolledBack{}", "    case False{}:\n      ODeferred{}"),
    ("O3 o3_promoted_needs_review", "review receipt ignored for review-gated PRs",
     "  Bool.or(Bool.not(gated), receipt)", "  True{}"),
    ("O3 o3_no_device_work_in_call", "restart and launch check run during the owner's call",
     "  Bool.and(Bool.not(call), idle)", "  idle"),
    ("O3 o3_no_device_work_in_call", "a deferred rollout runs anyway",
     "    case False{}:\n      ODeferred{}", "    case False{}:\n      finish(check)"),
    ("O3 o3_admissible_pass_promotes", "every rollout is refused",
     "    case True{}:\n      go(r, check)", "    case True{}:\n      ORefused{}"),
    # O4 privacy
    ("O4 o4_private_stays_private", "private artifacts push anywhere",
     "    case True{}:\n      vis_private(v)", "    case True{}:\n      True{}"),
    ("O4 o4_private_stays_private", "unknown visibility counts as private",
     "    case VPrivate{}:\n      True{}\n    case _:\n      False{}", "    case VPrivate{}:\n      True{}\n    case _:\n      True{}"),
    ("O4 o4_nonprivate_free", "non-private pushes are refused",
     "    case True{}:\n      vis_private(v)\n    case False{}:\n      True{}", "    case True{}:\n      vis_private(v)\n    case False{}:\n      False{}"),
    # O5 owner phone
    ("O5 o5_touch_needs_intent", "tap without an owner phone-operation intent",
     "      Bool.and(intent, fg)", "      fg"),
    ("O5 o5_touch_needs_foreground", "tap into whatever app is in the foreground",
     "      Bool.and(intent, fg)", "      intent"),
    ("O5 o5_no_chain_install_in_call", "install the call-chain app during a live call",
     "    case PInstall{chain}:\n      Bool.not(Bool.and(chain, call))", "    case PInstall{chain}:\n      True{}"),
    ("O5 o5_no_chain_restart_in_call", "restart the call-chain app during a live call",
     "    case PRestart{chain}:\n      Bool.not(Bool.and(chain, call))", "    case PRestart{chain}:\n      True{}"),
    # O6 accessibility
    ("O6 o6_a11y_superset", "post-state not consulted for the first service",
     "      Bool.and(Bool.or(mem(h, post), mem(h, rm)), a11y_ok(t, post, rm))", "      a11y_ok(t, post, rm)"),
    ("O6 o6_a11y_superset", "only the first service is checked",
     "      Bool.and(Bool.or(mem(h, post), mem(h, rm)), a11y_ok(t, post, rm))", "      Bool.or(mem(h, post), mem(h, rm))"),
    ("O6 o6_a11y_superset", "any service counts as owner-removed",
     "      Bool.and(Bool.or(mem(h, post), mem(h, rm)), a11y_ok(t, post, rm))", "      Bool.and(Bool.or(mem(h, post), True{}), a11y_ok(t, post, rm))"),
    # O7 routing
    ("O7 o7_no_cancelled_provider", "cancelled providers still route",
     "  Bool.and(Bool.not(cancelled), Bool.not(Bool.and(design, sol)))", "  Bool.not(Bool.and(design, sol))"),
    ("O7 o7_no_sol_design", "design roles may route to Sol",
     "  Bool.and(Bool.not(cancelled), Bool.not(Bool.and(design, sol)))", "  Bool.not(cancelled)"),
    # O8 node health
    ("O8 o8_no_silent_strike", "strikes past the limit stay silent",
     "      Bool.and(Bool.not(ok), late(now, s, lim))", "      False{}"),
    ("O8 o8_emit_marks_notified", "emission does not mark the node notified (repeats)",
     "      npick(ok, NHealthy{}, npick(late(now, s, lim), NNotified{s}, NBad{s}))", "      npick(ok, NHealthy{}, NBad{s})"),
    ("O8 o8_notify_once", "a notified node notifies on every strike",
     "    case NBad{since}:\n      bad_eff(since, e, lim)\n    case _:", "    case NBad{since}:\n      bad_eff(since, e, lim)\n    case NNotified{since}:\n      bad_eff(since, e, lim)\n    case _:"),
    ("O8 o8_notified_sticks", "a notified node re-arms without recovering",
     "      npick(ok, NHealthy{}, NNotified{s})", "      npick(ok, NHealthy{}, NBad{now})"),
    # O9 gateway restart
    ("O9 o9_restart_never_fails", "gateway restart marks runs Failed",
     "      rpick(Nat.is_eq(x, g), RInterrupted{g}, RActive{g})", "      RFailed{}"),
    ("O9 o9_restart_interrupts", "gateway restart is ignored (runs look active)",
     "      rpick(Nat.is_eq(x, g), RInterrupted{g}, RActive{g})", "      RActive{g}"),
    ("O9 o9_interrupted_exits", "an interrupted run silently fails on error",
     "    case ReportRun{}:\n      RReported{}\n    case _:\n      RInterrupted{g}", "    case ReportRun{}:\n      RReported{}\n    case RunError{}:\n      RFailed{}\n    case _:\n      RInterrupted{g}"),
    ("O9 o9_resume_needs_session", "resume without the run's session",
     "      rpick(session, RResumed{}, RInterrupted{g})", "      RResumed{}"),
    # O10: each resource must independently remain irrelevant.
    *[("O10 o10_phone_no_agent_resource_caps", f"phone admission gated by {resource}",
       PHONE_ADMITS, PHONE_ADMITS.replace(f"-{resource}:", f"{resource}:").replace(
           "\n  ready", f"\n  Bool.and(ready, {resource})"))
      for resource in ("memory", "cpu", "battery", "thermal")],
    ("O10 o10_phone_placement_no_agent_resource_caps", "phone placement gated by memory",
     "  place_phone(phone_admits(ready, memory, cpu, battery, thermal))",
     "  place_phone(Bool.and(ready, memory))"),
    # O11/O12: these change only Bool-valued expressions, not their types.
    ("O11 o11_none_not_credited", "none stage receives offload credit",
     "    case StageNone{}:\n      False{}", "    case StageNone{}:\n      True{}"),
    ("O11 o11_shadow_not_credited", "shadow stage receives offload credit",
     "    case StageShadow{}:\n      False{}", "    case StageShadow{}:\n      True{}"),
    ("O11 o11_named_family", "a policy cell credits another family",
     "  Bool.and(Nat.is_eq(family, named_family), Bool.and(policy_credited(stage), Nat.is_eq(arm, named_arm)))",
     "  Bool.and(True{}, Bool.and(policy_credited(stage), Nat.is_eq(arm, named_arm)))"),
    ("O11 o11_named_arm", "a credited family routes to an unnamed arm",
     "  Bool.and(Nat.is_eq(family, named_family), Bool.and(policy_credited(stage), Nat.is_eq(arm, named_arm)))",
     "  Bool.and(Nat.is_eq(family, named_family), Bool.and(policy_credited(stage), True{}))"),
    ("O11 o11_evidence_bound_route", "offload bypasses evidence stage",
     "  Bool.and(Nat.is_eq(family, named_family), Bool.and(policy_credited(stage), Nat.is_eq(arm, named_arm)))",
     "  Bool.and(Nat.is_eq(family, named_family), Bool.and(True{}, Nat.is_eq(arm, named_arm)))"),
    ("O12 o12_offload_obligation", "live frontier fallback ignores abstention",
     "      Bool.or(Bool.not(healthy), abstain)", "      True{}"),
    ("O12 o12_offload_obligation", "healthy endpoints permit silent frontier fallback",
     "      Bool.or(Bool.not(healthy), abstain)", "      Bool.or(healthy, abstain)"),
    ("O12 o12_abstain_can_fallback", "recorded abstentions cannot fall back",
     "      Bool.or(Bool.not(healthy), abstain)", "      Bool.not(healthy)"),
    ("O11 o11_named_family", "offload dispatcher bypasses the policy cell",
     "      policy_local(family, named_family, stage, named_arm, arm)", "      True{}"),
    ("O13 o13_default_zero", "missing evidence permits 24 candidates",
     "    case False{}:\n      0n", "    case False{}:\n      24n"),
    ("O13 o13_k2_rejected", "missing evidence permits a binary choice",
     "    case False{}:\n      0n", "    case False{}:\n      2n"),
    ("O13 o13_exact_scope", "capacity transfers across physical scopes",
     "Bool.and(credited, Nat.is_eq(actual_scope, named_scope))", "credited"),
    ("O13 o13_default_zero", "scope match substitutes for measured evidence",
     "Bool.and(credited, Nat.is_eq(actual_scope, named_scope))", "Nat.is_eq(actual_scope, named_scope)"),
    ("O13 o13_credited_limit", "measured capacity is ignored",
     "    case True{}:\n      measured", "    case True{}:\n      2n"),
    ("O13 o13_option_capacity", "request dispatcher ignores capacity",
     "  Bool.not(Nat.is_gt(count, option_limit(credited, actual_scope, named_scope, measured)))",
     "  True{}"),
    ("O13 o13_k2_rejected", "comparison rejects the boundary and admits excess",
     "  Bool.not(Nat.is_gt(count, option_limit(credited, actual_scope, named_scope, measured)))",
     "  Nat.is_gt(count, option_limit(credited, actual_scope, named_scope, measured))"),
    ("O13 o13_credited_excess_rejected", "capacity has an off-by-one allowance",
     "  Bool.not(Nat.is_gt(count, option_limit(credited, actual_scope, named_scope, measured)))",
     "  Bool.not(Nat.is_gt(count, Nat.add(1n, option_limit(credited, actual_scope, named_scope, measured))))"),
    ("O13 o13_destructive_never", "destructive acquires SLM authority",
     "  Nat.is_gt(2n, risk)", "  Nat.is_gt(3n, risk)"),
    ("O13 o13_unknown_never", "unknown risk acquires SLM authority",
     "  Nat.is_gt(2n, risk)", "  True{}"),
    ('O16 o16_sqlite_replace_no_holders', 'ignore holders', '  Bool.and(complete, empty)', '  complete'),
    ('O16 o16_sqlite_replace_scan_complete', 'ignore scan completeness', '  Bool.and(complete, empty)', '  empty'),
    ('O16 o16_sqlite_no_ghost_holders', 'ignore ghosts', '  Bool.and(complete, Bool.not(ghosts))', '  complete'),
    ('O16 o16_sqlite_auth_non_decreasing', 'lose credentials', '  Bool.and(known, Bool.not(Nat.is_gt(before, after)))', '  known'),
    ('O16 o16_sqlite_auth_known', 'unknown baseline earns credit', '  Bool.and(known, Bool.not(Nat.is_gt(before, after)))', '  Bool.not(Nat.is_gt(before, after))'),
    ('O16 o16_sqlite_sidecar_current_inode', 'stale inode removes sidecars', 'def sqlite_sidecar_allows(current: Bool) -> Bool:\n  current', 'def sqlite_sidecar_allows(current: Bool) -> Bool:\n  True{}'),
    ('O16 o16_sqlite_quiescent_replace', 'always refuse replacement', '  Bool.and(complete, empty)', '  False{}'),
    ('O16 o16_sqlite_healthy_holders', 'always refuse health', '  Bool.and(complete, Bool.not(ghosts))', '  False{}'),
    ('O16 o16_sqlite_auth_retained', 'always refuse auth', '  Bool.and(known, Bool.not(Nat.is_gt(before, after)))', '  False{}'),
    ('O16 o16_sqlite_current_sidecar', 'always refuse sidecars', 'def sqlite_sidecar_allows(current: Bool) -> Bool:\n  current', 'def sqlite_sidecar_allows(current: Bool) -> Bool:\n  False{}'),
    ('O16 o16_sqlite_unknown_holders', 'unknown scan declared healthy', '  Bool.and(complete, Bool.not(ghosts))', '  Bool.not(ghosts)'),
]


def check(proj: Path, verdict: bool) -> tuple[bool, str]:
    env = dict(os.environ)
    # host clang before Lean's bundled clang (bend-reachable-cuda-rule-gates)
    env["PATH"] = "/usr/bin:" + env.get("PATH", "") + ":" + str(Path.home() / ".elan/bin")
    cmd = [BEND, "PROOF.bend"] + (["--verdict"] if verdict else [])
    p = subprocess.run(cmd, cwd=proj, capture_output=True, text=True, env=env, timeout=600)
    out = (p.stdout + p.stderr).strip()
    return ("ALL PROOFS CHECK" in out and "SOME PROOFS FAIL" not in out), out


def first_location(out: str) -> str:
    for line in out.splitlines():
        if line.startswith("Location:"):
            return line.split(":", 1)[1].strip()
    lines = out.splitlines()
    return lines[1] if len(lines) > 1 else out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--verdict", action="store_true")
    ap.add_argument("--out")
    args = ap.parse_args()
    rows = []
    with tempfile.TemporaryDirectory(dir=os.environ.get("TMPDIR")) as td:
        work = Path(td)
        for f in ("ops.bend", "LAWS.bend", "PROOF.bend"):
            shutil.copy(HERE / f, work / f)
        ok, out = check(work, args.verdict)
        rows.append({"law": "-", "mutation": "reference (unmodified)", "proofs_check": ok,
                     "where": "" if ok else first_location(out)})
        ref = (HERE / "ops.bend").read_text()
        for law, desc, old, new in MUTATIONS:
            if ref.count(old) != 1:
                rows.append({"law": law, "mutation": desc, "proofs_check": None,
                             "where": f"MUTATION DID NOT APPLY (matches: {ref.count(old)})"})
                continue
            (work / "ops.bend").write_text(ref.replace(old, new))
            ok, out = check(work, args.verdict)
            rows.append({"law": law, "mutation": desc, "proofs_check": ok,
                         "where": "" if ok else first_location(out)})
    ref_ok = rows[0]["proofs_check"] is True
    caught = sum(1 for r in rows[1:] if r["proofs_check"] is False)
    laws = sorted({r["law"].split()[0] for r in rows[1:]})
    for r in rows:
        status = {True: "PASS", False: "FAIL", None: "N/A"}[r["proofs_check"]]
        print(f"{status:4}  {r['law']:34} {r['mutation']}  {r['where']}")
    print(f"reference passes: {ref_ok}; mutations rejected: {caught}/{len(MUTATIONS)}; laws covered: {' '.join(laws)}")
    if args.out:
        Path(args.out).write_text(json.dumps({"verdict_mode": args.verdict, "rows": rows,
                                              "rejected": caught, "total": len(MUTATIONS),
                                              "reference_restored": True}, indent=2) + "\n")
    return 0 if ref_ok and caught == len(MUTATIONS) else 1


if __name__ == "__main__":
    raise SystemExit(main())
