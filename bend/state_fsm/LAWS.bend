# A–E: abstract decision laws, not a live-system proof or effect authorization.
# PROOF.bend supplies dependent proof terms for arbitrary Evidence inputs.
# Each mandatory predicate has its own soundness theorem. Completeness rejects
# an always-Unknown or always-Violation implementation; unknown laws cover every
# position even when other predicates are false. Non-bools normalize to SAbsent.

import Base
import ./state.bend as S

def T(b: Bool) -> Data:
  match b:
    case True{}:
      Unit
    case False{}:
      Empty

# Specifications written independently of the model's known/affirmed helpers.
def Truth(e: S.Evidence) -> Data:
  match e:
    case S.SAbsent{}:
      Empty
    case S.SBool{value}:
      T(value)

def Known(e: S.Evidence) -> Data:
  match e:
    case S.SAbsent{}:
      Empty
    case S.SBool{value}:
      Unit

# A. Current, scoped observation agrees with the claim (including lifecycle claims).
law a_pass_needs_available:
  for available: S.Evidence
  for current: S.Evidence
  for scope_matches: S.Evidence
  for agrees: S.Evidence
  for w: T(S.passed(S.a(available, current, scope_matches, agrees)))
  Truth(available)

law a_pass_needs_current:
  for available: S.Evidence
  for current: S.Evidence
  for scope_matches: S.Evidence
  for agrees: S.Evidence
  for w: T(S.passed(S.a(available, current, scope_matches, agrees)))
  Truth(current)

law a_pass_needs_scope_matches:
  for available: S.Evidence
  for current: S.Evidence
  for scope_matches: S.Evidence
  for agrees: S.Evidence
  for w: T(S.passed(S.a(available, current, scope_matches, agrees)))
  Truth(scope_matches)

law a_pass_needs_agrees:
  for available: S.Evidence
  for current: S.Evidence
  for scope_matches: S.Evidence
  for agrees: S.Evidence
  for w: T(S.passed(S.a(available, current, scope_matches, agrees)))
  Truth(agrees)

law a_positive_complete:
  for available: S.Evidence
  for current: S.Evidence
  for scope_matches: S.Evidence
  for agrees: S.Evidence
  for h_available: Truth(available)
  for h_current: Truth(current)
  for h_scope_matches: Truth(scope_matches)
  for h_agrees: Truth(agrees)
  T(S.passed(S.a(available, current, scope_matches, agrees)))

law a_known_negative_complete:
  for available: S.Evidence
  for current: S.Evidence
  for scope_matches: S.Evidence
  for agrees: S.Evidence
  for h_available: Truth(available)
  for k_current: Known(current)
  for k_scope_matches: Known(scope_matches)
  for k_agrees: Known(agrees)
  for negative: T(Bool.not(Bool.and(S.affirmed(current), Bool.and(S.affirmed(scope_matches), S.affirmed(agrees)))))
  T(S.violated(S.a(available, current, scope_matches, agrees)))

law a_missing_available_unknown:
  for current: S.Evidence
  for scope_matches: S.Evidence
  for agrees: S.Evidence
  T(S.unknown(S.a(S.SAbsent{}, current, scope_matches, agrees)))

law a_missing_current_unknown:
  for available: S.Evidence
  for scope_matches: S.Evidence
  for agrees: S.Evidence
  T(S.unknown(S.a(available, S.SAbsent{}, scope_matches, agrees)))

law a_missing_scope_matches_unknown:
  for available: S.Evidence
  for current: S.Evidence
  for agrees: S.Evidence
  T(S.unknown(S.a(available, current, S.SAbsent{}, agrees)))

law a_missing_agrees_unknown:
  for available: S.Evidence
  for current: S.Evidence
  for scope_matches: S.Evidence
  T(S.unknown(S.a(available, current, scope_matches, S.SAbsent{})))

law a_unavailable_unknown:
  for current: S.Evidence
  for scope_matches: S.Evidence
  for agrees: S.Evidence
  T(S.unknown(S.a(S.SBool{False{}}, current, scope_matches, agrees)))

# B. Disk and loaded process bindings match the canonical deployment manifest.
law b_pass_needs_available:
  for available: S.Evidence
  for disk_matches: S.Evidence
  for process_matches: S.Evidence
  for canonical: S.Evidence
  for w: T(S.passed(S.b(available, disk_matches, process_matches, canonical)))
  Truth(available)

law b_pass_needs_disk_matches:
  for available: S.Evidence
  for disk_matches: S.Evidence
  for process_matches: S.Evidence
  for canonical: S.Evidence
  for w: T(S.passed(S.b(available, disk_matches, process_matches, canonical)))
  Truth(disk_matches)

law b_pass_needs_process_matches:
  for available: S.Evidence
  for disk_matches: S.Evidence
  for process_matches: S.Evidence
  for canonical: S.Evidence
  for w: T(S.passed(S.b(available, disk_matches, process_matches, canonical)))
  Truth(process_matches)

law b_pass_needs_canonical:
  for available: S.Evidence
  for disk_matches: S.Evidence
  for process_matches: S.Evidence
  for canonical: S.Evidence
  for w: T(S.passed(S.b(available, disk_matches, process_matches, canonical)))
  Truth(canonical)

law b_positive_complete:
  for available: S.Evidence
  for disk_matches: S.Evidence
  for process_matches: S.Evidence
  for canonical: S.Evidence
  for h_available: Truth(available)
  for h_disk_matches: Truth(disk_matches)
  for h_process_matches: Truth(process_matches)
  for h_canonical: Truth(canonical)
  T(S.passed(S.b(available, disk_matches, process_matches, canonical)))

law b_known_negative_complete:
  for available: S.Evidence
  for disk_matches: S.Evidence
  for process_matches: S.Evidence
  for canonical: S.Evidence
  for h_available: Truth(available)
  for k_disk_matches: Known(disk_matches)
  for k_process_matches: Known(process_matches)
  for k_canonical: Known(canonical)
  for negative: T(Bool.not(Bool.and(S.affirmed(disk_matches), Bool.and(S.affirmed(process_matches), S.affirmed(canonical)))))
  T(S.violated(S.b(available, disk_matches, process_matches, canonical)))

law b_missing_available_unknown:
  for disk_matches: S.Evidence
  for process_matches: S.Evidence
  for canonical: S.Evidence
  T(S.unknown(S.b(S.SAbsent{}, disk_matches, process_matches, canonical)))

law b_missing_disk_matches_unknown:
  for available: S.Evidence
  for process_matches: S.Evidence
  for canonical: S.Evidence
  T(S.unknown(S.b(available, S.SAbsent{}, process_matches, canonical)))

law b_missing_process_matches_unknown:
  for available: S.Evidence
  for disk_matches: S.Evidence
  for canonical: S.Evidence
  T(S.unknown(S.b(available, disk_matches, S.SAbsent{}, canonical)))

law b_missing_canonical_unknown:
  for available: S.Evidence
  for disk_matches: S.Evidence
  for process_matches: S.Evidence
  T(S.unknown(S.b(available, disk_matches, process_matches, S.SAbsent{})))

law b_unavailable_unknown:
  for disk_matches: S.Evidence
  for process_matches: S.Evidence
  for canonical: S.Evidence
  T(S.unknown(S.b(S.SBool{False{}}, disk_matches, process_matches, canonical)))

# C. Current revocation epoch, no active hits, complete searched coverage.
law c_pass_needs_available:
  for available: S.Evidence
  for epoch_current: S.Evidence
  for no_active_hits: S.Evidence
  for coverage_complete: S.Evidence
  for w: T(S.passed(S.c(available, epoch_current, no_active_hits, coverage_complete)))
  Truth(available)

law c_pass_needs_epoch_current:
  for available: S.Evidence
  for epoch_current: S.Evidence
  for no_active_hits: S.Evidence
  for coverage_complete: S.Evidence
  for w: T(S.passed(S.c(available, epoch_current, no_active_hits, coverage_complete)))
  Truth(epoch_current)

law c_pass_needs_no_active_hits:
  for available: S.Evidence
  for epoch_current: S.Evidence
  for no_active_hits: S.Evidence
  for coverage_complete: S.Evidence
  for w: T(S.passed(S.c(available, epoch_current, no_active_hits, coverage_complete)))
  Truth(no_active_hits)

law c_pass_needs_coverage_complete:
  for available: S.Evidence
  for epoch_current: S.Evidence
  for no_active_hits: S.Evidence
  for coverage_complete: S.Evidence
  for w: T(S.passed(S.c(available, epoch_current, no_active_hits, coverage_complete)))
  Truth(coverage_complete)

law c_positive_complete:
  for available: S.Evidence
  for epoch_current: S.Evidence
  for no_active_hits: S.Evidence
  for coverage_complete: S.Evidence
  for h_available: Truth(available)
  for h_epoch_current: Truth(epoch_current)
  for h_no_active_hits: Truth(no_active_hits)
  for h_coverage_complete: Truth(coverage_complete)
  T(S.passed(S.c(available, epoch_current, no_active_hits, coverage_complete)))

law c_known_negative_complete:
  for available: S.Evidence
  for epoch_current: S.Evidence
  for no_active_hits: S.Evidence
  for coverage_complete: S.Evidence
  for h_available: Truth(available)
  for k_epoch_current: Known(epoch_current)
  for k_no_active_hits: Known(no_active_hits)
  for k_coverage_complete: Known(coverage_complete)
  for negative: T(Bool.not(Bool.and(S.affirmed(epoch_current), Bool.and(S.affirmed(no_active_hits), S.affirmed(coverage_complete)))))
  T(S.violated(S.c(available, epoch_current, no_active_hits, coverage_complete)))

law c_missing_available_unknown:
  for epoch_current: S.Evidence
  for no_active_hits: S.Evidence
  for coverage_complete: S.Evidence
  T(S.unknown(S.c(S.SAbsent{}, epoch_current, no_active_hits, coverage_complete)))

law c_missing_epoch_current_unknown:
  for available: S.Evidence
  for no_active_hits: S.Evidence
  for coverage_complete: S.Evidence
  T(S.unknown(S.c(available, S.SAbsent{}, no_active_hits, coverage_complete)))

law c_missing_no_active_hits_unknown:
  for available: S.Evidence
  for epoch_current: S.Evidence
  for coverage_complete: S.Evidence
  T(S.unknown(S.c(available, epoch_current, S.SAbsent{}, coverage_complete)))

law c_missing_coverage_complete_unknown:
  for available: S.Evidence
  for epoch_current: S.Evidence
  for no_active_hits: S.Evidence
  T(S.unknown(S.c(available, epoch_current, no_active_hits, S.SAbsent{})))

law c_unavailable_unknown:
  for epoch_current: S.Evidence
  for no_active_hits: S.Evidence
  for coverage_complete: S.Evidence
  T(S.unknown(S.c(S.SBool{False{}}, epoch_current, no_active_hits, coverage_complete)))

# D. Typed, verified output with positive work, good transport and current target.
law d_pass_needs_available:
  for available: S.Evidence
  for typed_receipt: S.Evidence
  for output_verified: S.Evidence
  for work_positive: S.Evidence
  for transport_ok: S.Evidence
  for target_current: S.Evidence
  for w: T(S.passed(S.d(available, typed_receipt, output_verified, work_positive, transport_ok, target_current)))
  Truth(available)

law d_pass_needs_typed_receipt:
  for available: S.Evidence
  for typed_receipt: S.Evidence
  for output_verified: S.Evidence
  for work_positive: S.Evidence
  for transport_ok: S.Evidence
  for target_current: S.Evidence
  for w: T(S.passed(S.d(available, typed_receipt, output_verified, work_positive, transport_ok, target_current)))
  Truth(typed_receipt)

law d_pass_needs_output_verified:
  for available: S.Evidence
  for typed_receipt: S.Evidence
  for output_verified: S.Evidence
  for work_positive: S.Evidence
  for transport_ok: S.Evidence
  for target_current: S.Evidence
  for w: T(S.passed(S.d(available, typed_receipt, output_verified, work_positive, transport_ok, target_current)))
  Truth(output_verified)

law d_pass_needs_work_positive:
  for available: S.Evidence
  for typed_receipt: S.Evidence
  for output_verified: S.Evidence
  for work_positive: S.Evidence
  for transport_ok: S.Evidence
  for target_current: S.Evidence
  for w: T(S.passed(S.d(available, typed_receipt, output_verified, work_positive, transport_ok, target_current)))
  Truth(work_positive)

law d_pass_needs_transport_ok:
  for available: S.Evidence
  for typed_receipt: S.Evidence
  for output_verified: S.Evidence
  for work_positive: S.Evidence
  for transport_ok: S.Evidence
  for target_current: S.Evidence
  for w: T(S.passed(S.d(available, typed_receipt, output_verified, work_positive, transport_ok, target_current)))
  Truth(transport_ok)

law d_pass_needs_target_current:
  for available: S.Evidence
  for typed_receipt: S.Evidence
  for output_verified: S.Evidence
  for work_positive: S.Evidence
  for transport_ok: S.Evidence
  for target_current: S.Evidence
  for w: T(S.passed(S.d(available, typed_receipt, output_verified, work_positive, transport_ok, target_current)))
  Truth(target_current)

law d_positive_complete:
  for available: S.Evidence
  for typed_receipt: S.Evidence
  for output_verified: S.Evidence
  for work_positive: S.Evidence
  for transport_ok: S.Evidence
  for target_current: S.Evidence
  for h_available: Truth(available)
  for h_typed_receipt: Truth(typed_receipt)
  for h_output_verified: Truth(output_verified)
  for h_work_positive: Truth(work_positive)
  for h_transport_ok: Truth(transport_ok)
  for h_target_current: Truth(target_current)
  T(S.passed(S.d(available, typed_receipt, output_verified, work_positive, transport_ok, target_current)))

law d_known_negative_complete:
  for available: S.Evidence
  for typed_receipt: S.Evidence
  for output_verified: S.Evidence
  for work_positive: S.Evidence
  for transport_ok: S.Evidence
  for target_current: S.Evidence
  for h_available: Truth(available)
  for k_typed_receipt: Known(typed_receipt)
  for k_output_verified: Known(output_verified)
  for k_work_positive: Known(work_positive)
  for k_transport_ok: Known(transport_ok)
  for k_target_current: Known(target_current)
  for negative: T(Bool.not(Bool.and(S.affirmed(typed_receipt), Bool.and(S.affirmed(output_verified), Bool.and(S.affirmed(work_positive), Bool.and(S.affirmed(transport_ok), S.affirmed(target_current)))))))
  T(S.violated(S.d(available, typed_receipt, output_verified, work_positive, transport_ok, target_current)))

law d_missing_available_unknown:
  for typed_receipt: S.Evidence
  for output_verified: S.Evidence
  for work_positive: S.Evidence
  for transport_ok: S.Evidence
  for target_current: S.Evidence
  T(S.unknown(S.d(S.SAbsent{}, typed_receipt, output_verified, work_positive, transport_ok, target_current)))

law d_missing_typed_receipt_unknown:
  for available: S.Evidence
  for output_verified: S.Evidence
  for work_positive: S.Evidence
  for transport_ok: S.Evidence
  for target_current: S.Evidence
  T(S.unknown(S.d(available, S.SAbsent{}, output_verified, work_positive, transport_ok, target_current)))

law d_missing_output_verified_unknown:
  for available: S.Evidence
  for typed_receipt: S.Evidence
  for work_positive: S.Evidence
  for transport_ok: S.Evidence
  for target_current: S.Evidence
  T(S.unknown(S.d(available, typed_receipt, S.SAbsent{}, work_positive, transport_ok, target_current)))

law d_missing_work_positive_unknown:
  for available: S.Evidence
  for typed_receipt: S.Evidence
  for output_verified: S.Evidence
  for transport_ok: S.Evidence
  for target_current: S.Evidence
  T(S.unknown(S.d(available, typed_receipt, output_verified, S.SAbsent{}, transport_ok, target_current)))

law d_missing_transport_ok_unknown:
  for available: S.Evidence
  for typed_receipt: S.Evidence
  for output_verified: S.Evidence
  for work_positive: S.Evidence
  for target_current: S.Evidence
  T(S.unknown(S.d(available, typed_receipt, output_verified, work_positive, S.SAbsent{}, target_current)))

law d_missing_target_current_unknown:
  for available: S.Evidence
  for typed_receipt: S.Evidence
  for output_verified: S.Evidence
  for work_positive: S.Evidence
  for transport_ok: S.Evidence
  T(S.unknown(S.d(available, typed_receipt, output_verified, work_positive, transport_ok, S.SAbsent{})))

law d_unavailable_unknown:
  for typed_receipt: S.Evidence
  for output_verified: S.Evidence
  for work_positive: S.Evidence
  for transport_ok: S.Evidence
  for target_current: S.Evidence
  T(S.unknown(S.d(S.SBool{False{}}, typed_receipt, output_verified, work_positive, transport_ok, target_current)))

# E. Measured claim agrees at the exact observation scope (including rates/capabilities).
law e_pass_needs_available:
  for available: S.Evidence
  for measured: S.Evidence
  for scope_matches: S.Evidence
  for agrees: S.Evidence
  for w: T(S.passed(S.e(available, measured, scope_matches, agrees)))
  Truth(available)

law e_pass_needs_measured:
  for available: S.Evidence
  for measured: S.Evidence
  for scope_matches: S.Evidence
  for agrees: S.Evidence
  for w: T(S.passed(S.e(available, measured, scope_matches, agrees)))
  Truth(measured)

law e_pass_needs_scope_matches:
  for available: S.Evidence
  for measured: S.Evidence
  for scope_matches: S.Evidence
  for agrees: S.Evidence
  for w: T(S.passed(S.e(available, measured, scope_matches, agrees)))
  Truth(scope_matches)

law e_pass_needs_agrees:
  for available: S.Evidence
  for measured: S.Evidence
  for scope_matches: S.Evidence
  for agrees: S.Evidence
  for w: T(S.passed(S.e(available, measured, scope_matches, agrees)))
  Truth(agrees)

law e_positive_complete:
  for available: S.Evidence
  for measured: S.Evidence
  for scope_matches: S.Evidence
  for agrees: S.Evidence
  for h_available: Truth(available)
  for h_measured: Truth(measured)
  for h_scope_matches: Truth(scope_matches)
  for h_agrees: Truth(agrees)
  T(S.passed(S.e(available, measured, scope_matches, agrees)))

law e_known_negative_complete:
  for available: S.Evidence
  for measured: S.Evidence
  for scope_matches: S.Evidence
  for agrees: S.Evidence
  for h_available: Truth(available)
  for k_measured: Known(measured)
  for k_scope_matches: Known(scope_matches)
  for k_agrees: Known(agrees)
  for negative: T(Bool.not(Bool.and(S.affirmed(measured), Bool.and(S.affirmed(scope_matches), S.affirmed(agrees)))))
  T(S.violated(S.e(available, measured, scope_matches, agrees)))

law e_missing_available_unknown:
  for measured: S.Evidence
  for scope_matches: S.Evidence
  for agrees: S.Evidence
  T(S.unknown(S.e(S.SAbsent{}, measured, scope_matches, agrees)))

law e_missing_measured_unknown:
  for available: S.Evidence
  for scope_matches: S.Evidence
  for agrees: S.Evidence
  T(S.unknown(S.e(available, S.SAbsent{}, scope_matches, agrees)))

law e_missing_scope_matches_unknown:
  for available: S.Evidence
  for measured: S.Evidence
  for agrees: S.Evidence
  T(S.unknown(S.e(available, measured, S.SAbsent{}, agrees)))

law e_missing_agrees_unknown:
  for available: S.Evidence
  for measured: S.Evidence
  for scope_matches: S.Evidence
  T(S.unknown(S.e(available, measured, scope_matches, S.SAbsent{})))

law e_unavailable_unknown:
  for measured: S.Evidence
  for scope_matches: S.Evidence
  for agrees: S.Evidence
  T(S.unknown(S.e(S.SBool{False{}}, measured, scope_matches, agrees)))


# I. Live config must have a tracked source. No installation authority.
law i_pass_needs_available:
  for available: S.Evidence
  for tracked_source: S.Evidence
  for scope_matches: S.Evidence
  for agrees: S.Evidence
  for w: T(S.passed(S.i(available, tracked_source, scope_matches, agrees)))
  Truth(available)

law i_pass_needs_tracked_source:
  for available: S.Evidence
  for tracked_source: S.Evidence
  for scope_matches: S.Evidence
  for agrees: S.Evidence
  for w: T(S.passed(S.i(available, tracked_source, scope_matches, agrees)))
  Truth(tracked_source)

law i_pass_needs_scope_matches:
  for available: S.Evidence
  for tracked_source: S.Evidence
  for scope_matches: S.Evidence
  for agrees: S.Evidence
  for w: T(S.passed(S.i(available, tracked_source, scope_matches, agrees)))
  Truth(scope_matches)

law i_pass_needs_agrees:
  for available: S.Evidence
  for tracked_source: S.Evidence
  for scope_matches: S.Evidence
  for agrees: S.Evidence
  for w: T(S.passed(S.i(available, tracked_source, scope_matches, agrees)))
  Truth(agrees)

law i_positive_complete:
  for available: S.Evidence
  for tracked_source: S.Evidence
  for scope_matches: S.Evidence
  for agrees: S.Evidence
  for h_available: Truth(available)
  for h_tracked_source: Truth(tracked_source)
  for h_scope_matches: Truth(scope_matches)
  for h_agrees: Truth(agrees)
  T(S.passed(S.i(available, tracked_source, scope_matches, agrees)))

law i_known_negative_complete:
  for available: S.Evidence
  for tracked_source: S.Evidence
  for scope_matches: S.Evidence
  for agrees: S.Evidence
  for h_available: Truth(available)
  for k_tracked_source: Known(tracked_source)
  for k_scope_matches: Known(scope_matches)
  for k_agrees: Known(agrees)
  for negative: T(Bool.not(Bool.and(S.affirmed(tracked_source), Bool.and(S.affirmed(scope_matches), S.affirmed(agrees)))))
  T(S.violated(S.i(available, tracked_source, scope_matches, agrees)))

law i_missing_available_unknown:
  for tracked_source: S.Evidence
  for scope_matches: S.Evidence
  for agrees: S.Evidence
  T(S.unknown(S.i(S.SAbsent{}, tracked_source, scope_matches, agrees)))

law i_missing_tracked_source_unknown:
  for available: S.Evidence
  for scope_matches: S.Evidence
  for agrees: S.Evidence
  T(S.unknown(S.i(available, S.SAbsent{}, scope_matches, agrees)))

law i_missing_scope_matches_unknown:
  for available: S.Evidence
  for tracked_source: S.Evidence
  for agrees: S.Evidence
  T(S.unknown(S.i(available, tracked_source, S.SAbsent{}, agrees)))

law i_missing_agrees_unknown:
  for available: S.Evidence
  for tracked_source: S.Evidence
  for scope_matches: S.Evidence
  T(S.unknown(S.i(available, tracked_source, scope_matches, S.SAbsent{})))

law i_unavailable_unknown:
  for tracked_source: S.Evidence
  for scope_matches: S.Evidence
  for agrees: S.Evidence
  T(S.unknown(S.i(S.SBool{False{}}, tracked_source, scope_matches, agrees)))
