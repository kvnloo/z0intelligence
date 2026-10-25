# Constructive proofs of LAWS.bend. No finite enumeration or assumed axioms.
import Base
import ./state.bend as S
import ./LAWS.bend as Laws

def and_l(+a: Bool, -b: Bool, w: Laws.T(Bool.and(a, b))) -> Laws.T(a):
  match a:
    case True{}:
      Unit{}
    case False{}:
      Empty.absurd(Laws.T(False{}), w)

def and_r(a: Bool, -b: Bool, w: Laws.T(Bool.and(a, b))) -> Laws.T(b):
  match a:
    case True{}:
      w
    case False{}:
      Empty.absurd(Laws.T(b), w)

def and_i(a: Bool, -b: Bool, ta: Laws.T(a), tb: Laws.T(b)) -> Laws.T(Bool.and(a, b)):
  match a:
    case True{}:
      tb
    case False{}:
      Empty.absurd(Laws.T(Bool.and(False{}, b)), ta)

def and_no_l(a: Bool, -b: Bool, w: Laws.T(Bool.not(a))) -> Laws.T(Bool.not(Bool.and(a, b))):
  match a:
    case True{}:
      Empty.absurd(Laws.T(Bool.not(b)), w)
    case False{}:
      Unit{}

def and_no_r(a: Bool, -b: Bool, w: Laws.T(Bool.not(b))) -> Laws.T(Bool.not(Bool.and(a, b))):
  match a:
    case True{}:
      w
    case False{}:
      Unit{}

def truth_known(e: S.Evidence, w: Laws.Truth(e)) -> Laws.T(S.known(e)):
  match e:
    case S.SAbsent{}:
      Empty.absurd(Laws.T(False{}), w)
    case S.SBool{value}:
      Unit{}

def known_bool(e: S.Evidence, w: Laws.Known(e)) -> Laws.T(S.known(e)):
  match e:
    case S.SAbsent{}:
      Empty.absurd(Laws.T(False{}), w)
    case S.SBool{value}:
      Unit{}

def truth_affirmed(e: S.Evidence, w: Laws.Truth(e)) -> Laws.T(S.affirmed(e)):
  match e:
    case S.SAbsent{}:
      Empty.absurd(Laws.T(False{}), w)
    case S.SBool{value}:
      w

def affirmed_truth(e: S.Evidence, w: Laws.T(S.affirmed(e))) -> Laws.Truth(e):
  match e:
    case S.SAbsent{}:
      w
    case S.SBool{value}:
      w

def finish_holds(holds: Bool, w: Laws.T(S.passed(S.finish(holds)))) -> Laws.T(holds):
  match holds:
    case True{}:
      Unit{}
    case False{}:
      w

def known_holds(complete: Bool, holds: Bool, w: Laws.T(S.passed(S.decide_known(complete, holds)))) -> Laws.T(holds):
  match complete:
    case True{}:
      finish_holds(holds, w)
    case False{}:
      Empty.absurd(Laws.T(holds), w)

def decide_holds(available: S.Evidence, complete: Bool, holds: Bool, w: Laws.T(S.passed(S.decide(available, complete, holds)))) -> Laws.T(holds):
  match available:
    case S.SAbsent{}:
      Empty.absurd(Laws.T(holds), w)
    case S.SBool{value}:
      match value:
        case True{}:
          known_holds(complete, holds, w)
        case False{}:
          Empty.absurd(Laws.T(holds), w)

def decide_available(available: S.Evidence, complete: Bool, holds: Bool, w: Laws.T(S.passed(S.decide(available, complete, holds)))) -> Laws.Truth(available):
  match available:
    case S.SAbsent{}:
      w
    case S.SBool{value}:
      match value:
        case True{}:
          Unit{}
        case False{}:
          w

def known_positive(complete: Bool, holds: Bool, tc: Laws.T(complete), th: Laws.T(holds)) -> Laws.T(S.passed(S.decide_known(complete, holds))):
  match complete:
    case True{}:
      match holds:
        case True{}:
          Unit{}
        case False{}:
          th
    case False{}:
      tc

def decide_positive(available: S.Evidence, complete: Bool, holds: Bool, ta: Laws.Truth(available), tc: Laws.T(complete), th: Laws.T(holds)) -> Laws.T(S.passed(S.decide(available, complete, holds))):
  match available:
    case S.SAbsent{}:
      ta
    case S.SBool{value}:
      match value:
        case True{}:
          known_positive(complete, holds, tc, th)
        case False{}:
          ta

def known_unknown(complete: Bool, holds: Bool, w: Laws.T(Bool.not(complete))) -> Laws.T(S.unknown(S.decide_known(complete, holds))):
  match complete:
    case True{}:
      Empty.absurd(Laws.T(S.unknown(S.finish(holds))), w)
    case False{}:
      Unit{}

def decide_unknown(available: S.Evidence, complete: Bool, holds: Bool, w: Laws.T(Bool.not(complete))) -> Laws.T(S.unknown(S.decide(available, complete, holds))):
  match available:
    case S.SAbsent{}:
      Unit{}
    case S.SBool{value}:
      match value:
        case True{}:
          known_unknown(complete, holds, w)
        case False{}:
          Unit{}

def known_negative(complete: Bool, holds: Bool, tc: Laws.T(complete), th: Laws.T(Bool.not(holds))) -> Laws.T(S.violated(S.decide_known(complete, holds))):
  match complete:
    case True{}:
      match holds:
        case True{}:
          th
        case False{}:
          Unit{}
    case False{}:
      tc

def decide_negative(available: S.Evidence, complete: Bool, holds: Bool, ta: Laws.Truth(available), tc: Laws.T(complete), th: Laws.T(Bool.not(holds))) -> Laws.T(S.violated(S.decide(available, complete, holds))):
  match available:
    case S.SAbsent{}:
      ta
    case S.SBool{value}:
      match value:
        case True{}:
          known_negative(complete, holds, tc, th)
        case False{}:
          ta

# A. Each projection consumes the arbitrary successful evaluation witness.
def Laws.a_pass_needs_available(available, current, scope_matches, agrees, w):
  +current = current
  +scope_matches = scope_matches
  +agrees = agrees
  decide_available(available, Bool.and(S.known(current), Bool.and(S.known(scope_matches), S.known(agrees))), Bool.and(S.affirmed(current), Bool.and(S.affirmed(scope_matches), S.affirmed(agrees))), w)

def Laws.a_pass_needs_current(available, current, scope_matches, agrees, w):
  +current = current
  +scope_matches = scope_matches
  +agrees = agrees
  affirmed_truth(current, and_l(S.affirmed(current), Bool.and(S.affirmed(scope_matches), S.affirmed(agrees)), decide_holds(available, Bool.and(S.known(current), Bool.and(S.known(scope_matches), S.known(agrees))), Bool.and(S.affirmed(current), Bool.and(S.affirmed(scope_matches), S.affirmed(agrees))), w)))

def Laws.a_pass_needs_scope_matches(available, current, scope_matches, agrees, w):
  +current = current
  +scope_matches = scope_matches
  +agrees = agrees
  affirmed_truth(scope_matches, and_l(S.affirmed(scope_matches), S.affirmed(agrees), and_r(S.affirmed(current), Bool.and(S.affirmed(scope_matches), S.affirmed(agrees)), decide_holds(available, Bool.and(S.known(current), Bool.and(S.known(scope_matches), S.known(agrees))), Bool.and(S.affirmed(current), Bool.and(S.affirmed(scope_matches), S.affirmed(agrees))), w))))

def Laws.a_pass_needs_agrees(available, current, scope_matches, agrees, w):
  +current = current
  +scope_matches = scope_matches
  +agrees = agrees
  affirmed_truth(agrees, and_r(S.affirmed(scope_matches), S.affirmed(agrees), and_r(S.affirmed(current), Bool.and(S.affirmed(scope_matches), S.affirmed(agrees)), decide_holds(available, Bool.and(S.known(current), Bool.and(S.known(scope_matches), S.known(agrees))), Bool.and(S.affirmed(current), Bool.and(S.affirmed(scope_matches), S.affirmed(agrees))), w))))

def Laws.a_positive_complete(available, current, scope_matches, agrees, h_available, h_current, h_scope_matches, h_agrees):
  +current = current
  +scope_matches = scope_matches
  +agrees = agrees
  +h_current = h_current
  +h_scope_matches = h_scope_matches
  +h_agrees = h_agrees
  decide_positive(available, Bool.and(S.known(current), Bool.and(S.known(scope_matches), S.known(agrees))), Bool.and(S.affirmed(current), Bool.and(S.affirmed(scope_matches), S.affirmed(agrees))), h_available, and_i(S.known(current), Bool.and(S.known(scope_matches), S.known(agrees)), truth_known(current, h_current), and_i(S.known(scope_matches), S.known(agrees), truth_known(scope_matches, h_scope_matches), truth_known(agrees, h_agrees))), and_i(S.affirmed(current), Bool.and(S.affirmed(scope_matches), S.affirmed(agrees)), truth_affirmed(current, h_current), and_i(S.affirmed(scope_matches), S.affirmed(agrees), truth_affirmed(scope_matches, h_scope_matches), truth_affirmed(agrees, h_agrees))))

def Laws.a_known_negative_complete(available, current, scope_matches, agrees, h_available, k_current, k_scope_matches, k_agrees, negative):
  +current = current
  +scope_matches = scope_matches
  +agrees = agrees
  decide_negative(available, Bool.and(S.known(current), Bool.and(S.known(scope_matches), S.known(agrees))), Bool.and(S.affirmed(current), Bool.and(S.affirmed(scope_matches), S.affirmed(agrees))), h_available, and_i(S.known(current), Bool.and(S.known(scope_matches), S.known(agrees)), known_bool(current, k_current), and_i(S.known(scope_matches), S.known(agrees), known_bool(scope_matches, k_scope_matches), known_bool(agrees, k_agrees))), negative)

def Laws.a_missing_available_unknown(current, scope_matches, agrees):
  Unit{}

def Laws.a_missing_current_unknown(available, scope_matches, agrees):
  +scope_matches = scope_matches
  +agrees = agrees
  decide_unknown(available, Bool.and(S.known(S.SAbsent{}), Bool.and(S.known(scope_matches), S.known(agrees))), Bool.and(S.affirmed(S.SAbsent{}), Bool.and(S.affirmed(scope_matches), S.affirmed(agrees))), and_no_l(S.known(S.SAbsent{}), Bool.and(S.known(scope_matches), S.known(agrees)), Unit{}))

def Laws.a_missing_scope_matches_unknown(available, current, agrees):
  +current = current
  +agrees = agrees
  decide_unknown(available, Bool.and(S.known(current), Bool.and(S.known(S.SAbsent{}), S.known(agrees))), Bool.and(S.affirmed(current), Bool.and(S.affirmed(S.SAbsent{}), S.affirmed(agrees))), and_no_r(S.known(current), Bool.and(S.known(S.SAbsent{}), S.known(agrees)), and_no_l(S.known(S.SAbsent{}), S.known(agrees), Unit{})))

def Laws.a_missing_agrees_unknown(available, current, scope_matches):
  +current = current
  +scope_matches = scope_matches
  decide_unknown(available, Bool.and(S.known(current), Bool.and(S.known(scope_matches), S.known(S.SAbsent{}))), Bool.and(S.affirmed(current), Bool.and(S.affirmed(scope_matches), S.affirmed(S.SAbsent{}))), and_no_r(S.known(current), Bool.and(S.known(scope_matches), S.known(S.SAbsent{})), and_no_r(S.known(scope_matches), S.known(S.SAbsent{}), Unit{})))

def Laws.a_unavailable_unknown(current, scope_matches, agrees):
  Unit{}

# B. Each projection consumes the arbitrary successful evaluation witness.
def Laws.b_pass_needs_available(available, disk_matches, process_matches, canonical, w):
  +disk_matches = disk_matches
  +process_matches = process_matches
  +canonical = canonical
  decide_available(available, Bool.and(S.known(disk_matches), Bool.and(S.known(process_matches), S.known(canonical))), Bool.and(S.affirmed(disk_matches), Bool.and(S.affirmed(process_matches), S.affirmed(canonical))), w)

def Laws.b_pass_needs_disk_matches(available, disk_matches, process_matches, canonical, w):
  +disk_matches = disk_matches
  +process_matches = process_matches
  +canonical = canonical
  affirmed_truth(disk_matches, and_l(S.affirmed(disk_matches), Bool.and(S.affirmed(process_matches), S.affirmed(canonical)), decide_holds(available, Bool.and(S.known(disk_matches), Bool.and(S.known(process_matches), S.known(canonical))), Bool.and(S.affirmed(disk_matches), Bool.and(S.affirmed(process_matches), S.affirmed(canonical))), w)))

def Laws.b_pass_needs_process_matches(available, disk_matches, process_matches, canonical, w):
  +disk_matches = disk_matches
  +process_matches = process_matches
  +canonical = canonical
  affirmed_truth(process_matches, and_l(S.affirmed(process_matches), S.affirmed(canonical), and_r(S.affirmed(disk_matches), Bool.and(S.affirmed(process_matches), S.affirmed(canonical)), decide_holds(available, Bool.and(S.known(disk_matches), Bool.and(S.known(process_matches), S.known(canonical))), Bool.and(S.affirmed(disk_matches), Bool.and(S.affirmed(process_matches), S.affirmed(canonical))), w))))

def Laws.b_pass_needs_canonical(available, disk_matches, process_matches, canonical, w):
  +disk_matches = disk_matches
  +process_matches = process_matches
  +canonical = canonical
  affirmed_truth(canonical, and_r(S.affirmed(process_matches), S.affirmed(canonical), and_r(S.affirmed(disk_matches), Bool.and(S.affirmed(process_matches), S.affirmed(canonical)), decide_holds(available, Bool.and(S.known(disk_matches), Bool.and(S.known(process_matches), S.known(canonical))), Bool.and(S.affirmed(disk_matches), Bool.and(S.affirmed(process_matches), S.affirmed(canonical))), w))))

def Laws.b_positive_complete(available, disk_matches, process_matches, canonical, h_available, h_disk_matches, h_process_matches, h_canonical):
  +disk_matches = disk_matches
  +process_matches = process_matches
  +canonical = canonical
  +h_disk_matches = h_disk_matches
  +h_process_matches = h_process_matches
  +h_canonical = h_canonical
  decide_positive(available, Bool.and(S.known(disk_matches), Bool.and(S.known(process_matches), S.known(canonical))), Bool.and(S.affirmed(disk_matches), Bool.and(S.affirmed(process_matches), S.affirmed(canonical))), h_available, and_i(S.known(disk_matches), Bool.and(S.known(process_matches), S.known(canonical)), truth_known(disk_matches, h_disk_matches), and_i(S.known(process_matches), S.known(canonical), truth_known(process_matches, h_process_matches), truth_known(canonical, h_canonical))), and_i(S.affirmed(disk_matches), Bool.and(S.affirmed(process_matches), S.affirmed(canonical)), truth_affirmed(disk_matches, h_disk_matches), and_i(S.affirmed(process_matches), S.affirmed(canonical), truth_affirmed(process_matches, h_process_matches), truth_affirmed(canonical, h_canonical))))

def Laws.b_known_negative_complete(available, disk_matches, process_matches, canonical, h_available, k_disk_matches, k_process_matches, k_canonical, negative):
  +disk_matches = disk_matches
  +process_matches = process_matches
  +canonical = canonical
  decide_negative(available, Bool.and(S.known(disk_matches), Bool.and(S.known(process_matches), S.known(canonical))), Bool.and(S.affirmed(disk_matches), Bool.and(S.affirmed(process_matches), S.affirmed(canonical))), h_available, and_i(S.known(disk_matches), Bool.and(S.known(process_matches), S.known(canonical)), known_bool(disk_matches, k_disk_matches), and_i(S.known(process_matches), S.known(canonical), known_bool(process_matches, k_process_matches), known_bool(canonical, k_canonical))), negative)

def Laws.b_missing_available_unknown(disk_matches, process_matches, canonical):
  Unit{}

def Laws.b_missing_disk_matches_unknown(available, process_matches, canonical):
  +process_matches = process_matches
  +canonical = canonical
  decide_unknown(available, Bool.and(S.known(S.SAbsent{}), Bool.and(S.known(process_matches), S.known(canonical))), Bool.and(S.affirmed(S.SAbsent{}), Bool.and(S.affirmed(process_matches), S.affirmed(canonical))), and_no_l(S.known(S.SAbsent{}), Bool.and(S.known(process_matches), S.known(canonical)), Unit{}))

def Laws.b_missing_process_matches_unknown(available, disk_matches, canonical):
  +disk_matches = disk_matches
  +canonical = canonical
  decide_unknown(available, Bool.and(S.known(disk_matches), Bool.and(S.known(S.SAbsent{}), S.known(canonical))), Bool.and(S.affirmed(disk_matches), Bool.and(S.affirmed(S.SAbsent{}), S.affirmed(canonical))), and_no_r(S.known(disk_matches), Bool.and(S.known(S.SAbsent{}), S.known(canonical)), and_no_l(S.known(S.SAbsent{}), S.known(canonical), Unit{})))

def Laws.b_missing_canonical_unknown(available, disk_matches, process_matches):
  +disk_matches = disk_matches
  +process_matches = process_matches
  decide_unknown(available, Bool.and(S.known(disk_matches), Bool.and(S.known(process_matches), S.known(S.SAbsent{}))), Bool.and(S.affirmed(disk_matches), Bool.and(S.affirmed(process_matches), S.affirmed(S.SAbsent{}))), and_no_r(S.known(disk_matches), Bool.and(S.known(process_matches), S.known(S.SAbsent{})), and_no_r(S.known(process_matches), S.known(S.SAbsent{}), Unit{})))

def Laws.b_unavailable_unknown(disk_matches, process_matches, canonical):
  Unit{}

# C. Each projection consumes the arbitrary successful evaluation witness.
def Laws.c_pass_needs_available(available, epoch_current, no_active_hits, coverage_complete, w):
  +epoch_current = epoch_current
  +no_active_hits = no_active_hits
  +coverage_complete = coverage_complete
  decide_available(available, Bool.and(S.known(epoch_current), Bool.and(S.known(no_active_hits), S.known(coverage_complete))), Bool.and(S.affirmed(epoch_current), Bool.and(S.affirmed(no_active_hits), S.affirmed(coverage_complete))), w)

def Laws.c_pass_needs_epoch_current(available, epoch_current, no_active_hits, coverage_complete, w):
  +epoch_current = epoch_current
  +no_active_hits = no_active_hits
  +coverage_complete = coverage_complete
  affirmed_truth(epoch_current, and_l(S.affirmed(epoch_current), Bool.and(S.affirmed(no_active_hits), S.affirmed(coverage_complete)), decide_holds(available, Bool.and(S.known(epoch_current), Bool.and(S.known(no_active_hits), S.known(coverage_complete))), Bool.and(S.affirmed(epoch_current), Bool.and(S.affirmed(no_active_hits), S.affirmed(coverage_complete))), w)))

def Laws.c_pass_needs_no_active_hits(available, epoch_current, no_active_hits, coverage_complete, w):
  +epoch_current = epoch_current
  +no_active_hits = no_active_hits
  +coverage_complete = coverage_complete
  affirmed_truth(no_active_hits, and_l(S.affirmed(no_active_hits), S.affirmed(coverage_complete), and_r(S.affirmed(epoch_current), Bool.and(S.affirmed(no_active_hits), S.affirmed(coverage_complete)), decide_holds(available, Bool.and(S.known(epoch_current), Bool.and(S.known(no_active_hits), S.known(coverage_complete))), Bool.and(S.affirmed(epoch_current), Bool.and(S.affirmed(no_active_hits), S.affirmed(coverage_complete))), w))))

def Laws.c_pass_needs_coverage_complete(available, epoch_current, no_active_hits, coverage_complete, w):
  +epoch_current = epoch_current
  +no_active_hits = no_active_hits
  +coverage_complete = coverage_complete
  affirmed_truth(coverage_complete, and_r(S.affirmed(no_active_hits), S.affirmed(coverage_complete), and_r(S.affirmed(epoch_current), Bool.and(S.affirmed(no_active_hits), S.affirmed(coverage_complete)), decide_holds(available, Bool.and(S.known(epoch_current), Bool.and(S.known(no_active_hits), S.known(coverage_complete))), Bool.and(S.affirmed(epoch_current), Bool.and(S.affirmed(no_active_hits), S.affirmed(coverage_complete))), w))))

def Laws.c_positive_complete(available, epoch_current, no_active_hits, coverage_complete, h_available, h_epoch_current, h_no_active_hits, h_coverage_complete):
  +epoch_current = epoch_current
  +no_active_hits = no_active_hits
  +coverage_complete = coverage_complete
  +h_epoch_current = h_epoch_current
  +h_no_active_hits = h_no_active_hits
  +h_coverage_complete = h_coverage_complete
  decide_positive(available, Bool.and(S.known(epoch_current), Bool.and(S.known(no_active_hits), S.known(coverage_complete))), Bool.and(S.affirmed(epoch_current), Bool.and(S.affirmed(no_active_hits), S.affirmed(coverage_complete))), h_available, and_i(S.known(epoch_current), Bool.and(S.known(no_active_hits), S.known(coverage_complete)), truth_known(epoch_current, h_epoch_current), and_i(S.known(no_active_hits), S.known(coverage_complete), truth_known(no_active_hits, h_no_active_hits), truth_known(coverage_complete, h_coverage_complete))), and_i(S.affirmed(epoch_current), Bool.and(S.affirmed(no_active_hits), S.affirmed(coverage_complete)), truth_affirmed(epoch_current, h_epoch_current), and_i(S.affirmed(no_active_hits), S.affirmed(coverage_complete), truth_affirmed(no_active_hits, h_no_active_hits), truth_affirmed(coverage_complete, h_coverage_complete))))

def Laws.c_known_negative_complete(available, epoch_current, no_active_hits, coverage_complete, h_available, k_epoch_current, k_no_active_hits, k_coverage_complete, negative):
  +epoch_current = epoch_current
  +no_active_hits = no_active_hits
  +coverage_complete = coverage_complete
  decide_negative(available, Bool.and(S.known(epoch_current), Bool.and(S.known(no_active_hits), S.known(coverage_complete))), Bool.and(S.affirmed(epoch_current), Bool.and(S.affirmed(no_active_hits), S.affirmed(coverage_complete))), h_available, and_i(S.known(epoch_current), Bool.and(S.known(no_active_hits), S.known(coverage_complete)), known_bool(epoch_current, k_epoch_current), and_i(S.known(no_active_hits), S.known(coverage_complete), known_bool(no_active_hits, k_no_active_hits), known_bool(coverage_complete, k_coverage_complete))), negative)

def Laws.c_missing_available_unknown(epoch_current, no_active_hits, coverage_complete):
  Unit{}

def Laws.c_missing_epoch_current_unknown(available, no_active_hits, coverage_complete):
  +no_active_hits = no_active_hits
  +coverage_complete = coverage_complete
  decide_unknown(available, Bool.and(S.known(S.SAbsent{}), Bool.and(S.known(no_active_hits), S.known(coverage_complete))), Bool.and(S.affirmed(S.SAbsent{}), Bool.and(S.affirmed(no_active_hits), S.affirmed(coverage_complete))), and_no_l(S.known(S.SAbsent{}), Bool.and(S.known(no_active_hits), S.known(coverage_complete)), Unit{}))

def Laws.c_missing_no_active_hits_unknown(available, epoch_current, coverage_complete):
  +epoch_current = epoch_current
  +coverage_complete = coverage_complete
  decide_unknown(available, Bool.and(S.known(epoch_current), Bool.and(S.known(S.SAbsent{}), S.known(coverage_complete))), Bool.and(S.affirmed(epoch_current), Bool.and(S.affirmed(S.SAbsent{}), S.affirmed(coverage_complete))), and_no_r(S.known(epoch_current), Bool.and(S.known(S.SAbsent{}), S.known(coverage_complete)), and_no_l(S.known(S.SAbsent{}), S.known(coverage_complete), Unit{})))

def Laws.c_missing_coverage_complete_unknown(available, epoch_current, no_active_hits):
  +epoch_current = epoch_current
  +no_active_hits = no_active_hits
  decide_unknown(available, Bool.and(S.known(epoch_current), Bool.and(S.known(no_active_hits), S.known(S.SAbsent{}))), Bool.and(S.affirmed(epoch_current), Bool.and(S.affirmed(no_active_hits), S.affirmed(S.SAbsent{}))), and_no_r(S.known(epoch_current), Bool.and(S.known(no_active_hits), S.known(S.SAbsent{})), and_no_r(S.known(no_active_hits), S.known(S.SAbsent{}), Unit{})))

def Laws.c_unavailable_unknown(epoch_current, no_active_hits, coverage_complete):
  Unit{}

# D. Each projection consumes the arbitrary successful evaluation witness.
def Laws.d_pass_needs_available(available, typed_receipt, output_verified, work_positive, transport_ok, target_current, w):
  +typed_receipt = typed_receipt
  +output_verified = output_verified
  +work_positive = work_positive
  +transport_ok = transport_ok
  +target_current = target_current
  decide_available(available, Bool.and(S.known(typed_receipt), Bool.and(S.known(output_verified), Bool.and(S.known(work_positive), Bool.and(S.known(transport_ok), S.known(target_current))))), Bool.and(S.affirmed(typed_receipt), Bool.and(S.affirmed(output_verified), Bool.and(S.affirmed(work_positive), Bool.and(S.affirmed(transport_ok), S.affirmed(target_current))))), w)

def Laws.d_pass_needs_typed_receipt(available, typed_receipt, output_verified, work_positive, transport_ok, target_current, w):
  +typed_receipt = typed_receipt
  +output_verified = output_verified
  +work_positive = work_positive
  +transport_ok = transport_ok
  +target_current = target_current
  affirmed_truth(typed_receipt, and_l(S.affirmed(typed_receipt), Bool.and(S.affirmed(output_verified), Bool.and(S.affirmed(work_positive), Bool.and(S.affirmed(transport_ok), S.affirmed(target_current)))), decide_holds(available, Bool.and(S.known(typed_receipt), Bool.and(S.known(output_verified), Bool.and(S.known(work_positive), Bool.and(S.known(transport_ok), S.known(target_current))))), Bool.and(S.affirmed(typed_receipt), Bool.and(S.affirmed(output_verified), Bool.and(S.affirmed(work_positive), Bool.and(S.affirmed(transport_ok), S.affirmed(target_current))))), w)))

def Laws.d_pass_needs_output_verified(available, typed_receipt, output_verified, work_positive, transport_ok, target_current, w):
  +typed_receipt = typed_receipt
  +output_verified = output_verified
  +work_positive = work_positive
  +transport_ok = transport_ok
  +target_current = target_current
  affirmed_truth(output_verified, and_l(S.affirmed(output_verified), Bool.and(S.affirmed(work_positive), Bool.and(S.affirmed(transport_ok), S.affirmed(target_current))), and_r(S.affirmed(typed_receipt), Bool.and(S.affirmed(output_verified), Bool.and(S.affirmed(work_positive), Bool.and(S.affirmed(transport_ok), S.affirmed(target_current)))), decide_holds(available, Bool.and(S.known(typed_receipt), Bool.and(S.known(output_verified), Bool.and(S.known(work_positive), Bool.and(S.known(transport_ok), S.known(target_current))))), Bool.and(S.affirmed(typed_receipt), Bool.and(S.affirmed(output_verified), Bool.and(S.affirmed(work_positive), Bool.and(S.affirmed(transport_ok), S.affirmed(target_current))))), w))))

def Laws.d_pass_needs_work_positive(available, typed_receipt, output_verified, work_positive, transport_ok, target_current, w):
  +typed_receipt = typed_receipt
  +output_verified = output_verified
  +work_positive = work_positive
  +transport_ok = transport_ok
  +target_current = target_current
  affirmed_truth(work_positive, and_l(S.affirmed(work_positive), Bool.and(S.affirmed(transport_ok), S.affirmed(target_current)), and_r(S.affirmed(output_verified), Bool.and(S.affirmed(work_positive), Bool.and(S.affirmed(transport_ok), S.affirmed(target_current))), and_r(S.affirmed(typed_receipt), Bool.and(S.affirmed(output_verified), Bool.and(S.affirmed(work_positive), Bool.and(S.affirmed(transport_ok), S.affirmed(target_current)))), decide_holds(available, Bool.and(S.known(typed_receipt), Bool.and(S.known(output_verified), Bool.and(S.known(work_positive), Bool.and(S.known(transport_ok), S.known(target_current))))), Bool.and(S.affirmed(typed_receipt), Bool.and(S.affirmed(output_verified), Bool.and(S.affirmed(work_positive), Bool.and(S.affirmed(transport_ok), S.affirmed(target_current))))), w)))))

def Laws.d_pass_needs_transport_ok(available, typed_receipt, output_verified, work_positive, transport_ok, target_current, w):
  +typed_receipt = typed_receipt
  +output_verified = output_verified
  +work_positive = work_positive
  +transport_ok = transport_ok
  +target_current = target_current
  affirmed_truth(transport_ok, and_l(S.affirmed(transport_ok), S.affirmed(target_current), and_r(S.affirmed(work_positive), Bool.and(S.affirmed(transport_ok), S.affirmed(target_current)), and_r(S.affirmed(output_verified), Bool.and(S.affirmed(work_positive), Bool.and(S.affirmed(transport_ok), S.affirmed(target_current))), and_r(S.affirmed(typed_receipt), Bool.and(S.affirmed(output_verified), Bool.and(S.affirmed(work_positive), Bool.and(S.affirmed(transport_ok), S.affirmed(target_current)))), decide_holds(available, Bool.and(S.known(typed_receipt), Bool.and(S.known(output_verified), Bool.and(S.known(work_positive), Bool.and(S.known(transport_ok), S.known(target_current))))), Bool.and(S.affirmed(typed_receipt), Bool.and(S.affirmed(output_verified), Bool.and(S.affirmed(work_positive), Bool.and(S.affirmed(transport_ok), S.affirmed(target_current))))), w))))))

def Laws.d_pass_needs_target_current(available, typed_receipt, output_verified, work_positive, transport_ok, target_current, w):
  +typed_receipt = typed_receipt
  +output_verified = output_verified
  +work_positive = work_positive
  +transport_ok = transport_ok
  +target_current = target_current
  affirmed_truth(target_current, and_r(S.affirmed(transport_ok), S.affirmed(target_current), and_r(S.affirmed(work_positive), Bool.and(S.affirmed(transport_ok), S.affirmed(target_current)), and_r(S.affirmed(output_verified), Bool.and(S.affirmed(work_positive), Bool.and(S.affirmed(transport_ok), S.affirmed(target_current))), and_r(S.affirmed(typed_receipt), Bool.and(S.affirmed(output_verified), Bool.and(S.affirmed(work_positive), Bool.and(S.affirmed(transport_ok), S.affirmed(target_current)))), decide_holds(available, Bool.and(S.known(typed_receipt), Bool.and(S.known(output_verified), Bool.and(S.known(work_positive), Bool.and(S.known(transport_ok), S.known(target_current))))), Bool.and(S.affirmed(typed_receipt), Bool.and(S.affirmed(output_verified), Bool.and(S.affirmed(work_positive), Bool.and(S.affirmed(transport_ok), S.affirmed(target_current))))), w))))))

def Laws.d_positive_complete(available, typed_receipt, output_verified, work_positive, transport_ok, target_current, h_available, h_typed_receipt, h_output_verified, h_work_positive, h_transport_ok, h_target_current):
  +typed_receipt = typed_receipt
  +output_verified = output_verified
  +work_positive = work_positive
  +transport_ok = transport_ok
  +target_current = target_current
  +h_typed_receipt = h_typed_receipt
  +h_output_verified = h_output_verified
  +h_work_positive = h_work_positive
  +h_transport_ok = h_transport_ok
  +h_target_current = h_target_current
  decide_positive(available, Bool.and(S.known(typed_receipt), Bool.and(S.known(output_verified), Bool.and(S.known(work_positive), Bool.and(S.known(transport_ok), S.known(target_current))))), Bool.and(S.affirmed(typed_receipt), Bool.and(S.affirmed(output_verified), Bool.and(S.affirmed(work_positive), Bool.and(S.affirmed(transport_ok), S.affirmed(target_current))))), h_available, and_i(S.known(typed_receipt), Bool.and(S.known(output_verified), Bool.and(S.known(work_positive), Bool.and(S.known(transport_ok), S.known(target_current)))), truth_known(typed_receipt, h_typed_receipt), and_i(S.known(output_verified), Bool.and(S.known(work_positive), Bool.and(S.known(transport_ok), S.known(target_current))), truth_known(output_verified, h_output_verified), and_i(S.known(work_positive), Bool.and(S.known(transport_ok), S.known(target_current)), truth_known(work_positive, h_work_positive), and_i(S.known(transport_ok), S.known(target_current), truth_known(transport_ok, h_transport_ok), truth_known(target_current, h_target_current))))), and_i(S.affirmed(typed_receipt), Bool.and(S.affirmed(output_verified), Bool.and(S.affirmed(work_positive), Bool.and(S.affirmed(transport_ok), S.affirmed(target_current)))), truth_affirmed(typed_receipt, h_typed_receipt), and_i(S.affirmed(output_verified), Bool.and(S.affirmed(work_positive), Bool.and(S.affirmed(transport_ok), S.affirmed(target_current))), truth_affirmed(output_verified, h_output_verified), and_i(S.affirmed(work_positive), Bool.and(S.affirmed(transport_ok), S.affirmed(target_current)), truth_affirmed(work_positive, h_work_positive), and_i(S.affirmed(transport_ok), S.affirmed(target_current), truth_affirmed(transport_ok, h_transport_ok), truth_affirmed(target_current, h_target_current))))))

def Laws.d_known_negative_complete(available, typed_receipt, output_verified, work_positive, transport_ok, target_current, h_available, k_typed_receipt, k_output_verified, k_work_positive, k_transport_ok, k_target_current, negative):
  +typed_receipt = typed_receipt
  +output_verified = output_verified
  +work_positive = work_positive
  +transport_ok = transport_ok
  +target_current = target_current
  decide_negative(available, Bool.and(S.known(typed_receipt), Bool.and(S.known(output_verified), Bool.and(S.known(work_positive), Bool.and(S.known(transport_ok), S.known(target_current))))), Bool.and(S.affirmed(typed_receipt), Bool.and(S.affirmed(output_verified), Bool.and(S.affirmed(work_positive), Bool.and(S.affirmed(transport_ok), S.affirmed(target_current))))), h_available, and_i(S.known(typed_receipt), Bool.and(S.known(output_verified), Bool.and(S.known(work_positive), Bool.and(S.known(transport_ok), S.known(target_current)))), known_bool(typed_receipt, k_typed_receipt), and_i(S.known(output_verified), Bool.and(S.known(work_positive), Bool.and(S.known(transport_ok), S.known(target_current))), known_bool(output_verified, k_output_verified), and_i(S.known(work_positive), Bool.and(S.known(transport_ok), S.known(target_current)), known_bool(work_positive, k_work_positive), and_i(S.known(transport_ok), S.known(target_current), known_bool(transport_ok, k_transport_ok), known_bool(target_current, k_target_current))))), negative)

def Laws.d_missing_available_unknown(typed_receipt, output_verified, work_positive, transport_ok, target_current):
  Unit{}

def Laws.d_missing_typed_receipt_unknown(available, output_verified, work_positive, transport_ok, target_current):
  +output_verified = output_verified
  +work_positive = work_positive
  +transport_ok = transport_ok
  +target_current = target_current
  decide_unknown(available, Bool.and(S.known(S.SAbsent{}), Bool.and(S.known(output_verified), Bool.and(S.known(work_positive), Bool.and(S.known(transport_ok), S.known(target_current))))), Bool.and(S.affirmed(S.SAbsent{}), Bool.and(S.affirmed(output_verified), Bool.and(S.affirmed(work_positive), Bool.and(S.affirmed(transport_ok), S.affirmed(target_current))))), and_no_l(S.known(S.SAbsent{}), Bool.and(S.known(output_verified), Bool.and(S.known(work_positive), Bool.and(S.known(transport_ok), S.known(target_current)))), Unit{}))

def Laws.d_missing_output_verified_unknown(available, typed_receipt, work_positive, transport_ok, target_current):
  +typed_receipt = typed_receipt
  +work_positive = work_positive
  +transport_ok = transport_ok
  +target_current = target_current
  decide_unknown(available, Bool.and(S.known(typed_receipt), Bool.and(S.known(S.SAbsent{}), Bool.and(S.known(work_positive), Bool.and(S.known(transport_ok), S.known(target_current))))), Bool.and(S.affirmed(typed_receipt), Bool.and(S.affirmed(S.SAbsent{}), Bool.and(S.affirmed(work_positive), Bool.and(S.affirmed(transport_ok), S.affirmed(target_current))))), and_no_r(S.known(typed_receipt), Bool.and(S.known(S.SAbsent{}), Bool.and(S.known(work_positive), Bool.and(S.known(transport_ok), S.known(target_current)))), and_no_l(S.known(S.SAbsent{}), Bool.and(S.known(work_positive), Bool.and(S.known(transport_ok), S.known(target_current))), Unit{})))

def Laws.d_missing_work_positive_unknown(available, typed_receipt, output_verified, transport_ok, target_current):
  +typed_receipt = typed_receipt
  +output_verified = output_verified
  +transport_ok = transport_ok
  +target_current = target_current
  decide_unknown(available, Bool.and(S.known(typed_receipt), Bool.and(S.known(output_verified), Bool.and(S.known(S.SAbsent{}), Bool.and(S.known(transport_ok), S.known(target_current))))), Bool.and(S.affirmed(typed_receipt), Bool.and(S.affirmed(output_verified), Bool.and(S.affirmed(S.SAbsent{}), Bool.and(S.affirmed(transport_ok), S.affirmed(target_current))))), and_no_r(S.known(typed_receipt), Bool.and(S.known(output_verified), Bool.and(S.known(S.SAbsent{}), Bool.and(S.known(transport_ok), S.known(target_current)))), and_no_r(S.known(output_verified), Bool.and(S.known(S.SAbsent{}), Bool.and(S.known(transport_ok), S.known(target_current))), and_no_l(S.known(S.SAbsent{}), Bool.and(S.known(transport_ok), S.known(target_current)), Unit{}))))

def Laws.d_missing_transport_ok_unknown(available, typed_receipt, output_verified, work_positive, target_current):
  +typed_receipt = typed_receipt
  +output_verified = output_verified
  +work_positive = work_positive
  +target_current = target_current
  decide_unknown(available, Bool.and(S.known(typed_receipt), Bool.and(S.known(output_verified), Bool.and(S.known(work_positive), Bool.and(S.known(S.SAbsent{}), S.known(target_current))))), Bool.and(S.affirmed(typed_receipt), Bool.and(S.affirmed(output_verified), Bool.and(S.affirmed(work_positive), Bool.and(S.affirmed(S.SAbsent{}), S.affirmed(target_current))))), and_no_r(S.known(typed_receipt), Bool.and(S.known(output_verified), Bool.and(S.known(work_positive), Bool.and(S.known(S.SAbsent{}), S.known(target_current)))), and_no_r(S.known(output_verified), Bool.and(S.known(work_positive), Bool.and(S.known(S.SAbsent{}), S.known(target_current))), and_no_r(S.known(work_positive), Bool.and(S.known(S.SAbsent{}), S.known(target_current)), and_no_l(S.known(S.SAbsent{}), S.known(target_current), Unit{})))))

def Laws.d_missing_target_current_unknown(available, typed_receipt, output_verified, work_positive, transport_ok):
  +typed_receipt = typed_receipt
  +output_verified = output_verified
  +work_positive = work_positive
  +transport_ok = transport_ok
  decide_unknown(available, Bool.and(S.known(typed_receipt), Bool.and(S.known(output_verified), Bool.and(S.known(work_positive), Bool.and(S.known(transport_ok), S.known(S.SAbsent{}))))), Bool.and(S.affirmed(typed_receipt), Bool.and(S.affirmed(output_verified), Bool.and(S.affirmed(work_positive), Bool.and(S.affirmed(transport_ok), S.affirmed(S.SAbsent{}))))), and_no_r(S.known(typed_receipt), Bool.and(S.known(output_verified), Bool.and(S.known(work_positive), Bool.and(S.known(transport_ok), S.known(S.SAbsent{})))), and_no_r(S.known(output_verified), Bool.and(S.known(work_positive), Bool.and(S.known(transport_ok), S.known(S.SAbsent{}))), and_no_r(S.known(work_positive), Bool.and(S.known(transport_ok), S.known(S.SAbsent{})), and_no_r(S.known(transport_ok), S.known(S.SAbsent{}), Unit{})))))

def Laws.d_unavailable_unknown(typed_receipt, output_verified, work_positive, transport_ok, target_current):
  Unit{}

# E. Each projection consumes the arbitrary successful evaluation witness.
def Laws.e_pass_needs_available(available, measured, scope_matches, agrees, w):
  +measured = measured
  +scope_matches = scope_matches
  +agrees = agrees
  decide_available(available, Bool.and(S.known(measured), Bool.and(S.known(scope_matches), S.known(agrees))), Bool.and(S.affirmed(measured), Bool.and(S.affirmed(scope_matches), S.affirmed(agrees))), w)

def Laws.e_pass_needs_measured(available, measured, scope_matches, agrees, w):
  +measured = measured
  +scope_matches = scope_matches
  +agrees = agrees
  affirmed_truth(measured, and_l(S.affirmed(measured), Bool.and(S.affirmed(scope_matches), S.affirmed(agrees)), decide_holds(available, Bool.and(S.known(measured), Bool.and(S.known(scope_matches), S.known(agrees))), Bool.and(S.affirmed(measured), Bool.and(S.affirmed(scope_matches), S.affirmed(agrees))), w)))

def Laws.e_pass_needs_scope_matches(available, measured, scope_matches, agrees, w):
  +measured = measured
  +scope_matches = scope_matches
  +agrees = agrees
  affirmed_truth(scope_matches, and_l(S.affirmed(scope_matches), S.affirmed(agrees), and_r(S.affirmed(measured), Bool.and(S.affirmed(scope_matches), S.affirmed(agrees)), decide_holds(available, Bool.and(S.known(measured), Bool.and(S.known(scope_matches), S.known(agrees))), Bool.and(S.affirmed(measured), Bool.and(S.affirmed(scope_matches), S.affirmed(agrees))), w))))

def Laws.e_pass_needs_agrees(available, measured, scope_matches, agrees, w):
  +measured = measured
  +scope_matches = scope_matches
  +agrees = agrees
  affirmed_truth(agrees, and_r(S.affirmed(scope_matches), S.affirmed(agrees), and_r(S.affirmed(measured), Bool.and(S.affirmed(scope_matches), S.affirmed(agrees)), decide_holds(available, Bool.and(S.known(measured), Bool.and(S.known(scope_matches), S.known(agrees))), Bool.and(S.affirmed(measured), Bool.and(S.affirmed(scope_matches), S.affirmed(agrees))), w))))

def Laws.e_positive_complete(available, measured, scope_matches, agrees, h_available, h_measured, h_scope_matches, h_agrees):
  +measured = measured
  +scope_matches = scope_matches
  +agrees = agrees
  +h_measured = h_measured
  +h_scope_matches = h_scope_matches
  +h_agrees = h_agrees
  decide_positive(available, Bool.and(S.known(measured), Bool.and(S.known(scope_matches), S.known(agrees))), Bool.and(S.affirmed(measured), Bool.and(S.affirmed(scope_matches), S.affirmed(agrees))), h_available, and_i(S.known(measured), Bool.and(S.known(scope_matches), S.known(agrees)), truth_known(measured, h_measured), and_i(S.known(scope_matches), S.known(agrees), truth_known(scope_matches, h_scope_matches), truth_known(agrees, h_agrees))), and_i(S.affirmed(measured), Bool.and(S.affirmed(scope_matches), S.affirmed(agrees)), truth_affirmed(measured, h_measured), and_i(S.affirmed(scope_matches), S.affirmed(agrees), truth_affirmed(scope_matches, h_scope_matches), truth_affirmed(agrees, h_agrees))))

def Laws.e_known_negative_complete(available, measured, scope_matches, agrees, h_available, k_measured, k_scope_matches, k_agrees, negative):
  +measured = measured
  +scope_matches = scope_matches
  +agrees = agrees
  decide_negative(available, Bool.and(S.known(measured), Bool.and(S.known(scope_matches), S.known(agrees))), Bool.and(S.affirmed(measured), Bool.and(S.affirmed(scope_matches), S.affirmed(agrees))), h_available, and_i(S.known(measured), Bool.and(S.known(scope_matches), S.known(agrees)), known_bool(measured, k_measured), and_i(S.known(scope_matches), S.known(agrees), known_bool(scope_matches, k_scope_matches), known_bool(agrees, k_agrees))), negative)

def Laws.e_missing_available_unknown(measured, scope_matches, agrees):
  Unit{}

def Laws.e_missing_measured_unknown(available, scope_matches, agrees):
  +scope_matches = scope_matches
  +agrees = agrees
  decide_unknown(available, Bool.and(S.known(S.SAbsent{}), Bool.and(S.known(scope_matches), S.known(agrees))), Bool.and(S.affirmed(S.SAbsent{}), Bool.and(S.affirmed(scope_matches), S.affirmed(agrees))), and_no_l(S.known(S.SAbsent{}), Bool.and(S.known(scope_matches), S.known(agrees)), Unit{}))

def Laws.e_missing_scope_matches_unknown(available, measured, agrees):
  +measured = measured
  +agrees = agrees
  decide_unknown(available, Bool.and(S.known(measured), Bool.and(S.known(S.SAbsent{}), S.known(agrees))), Bool.and(S.affirmed(measured), Bool.and(S.affirmed(S.SAbsent{}), S.affirmed(agrees))), and_no_r(S.known(measured), Bool.and(S.known(S.SAbsent{}), S.known(agrees)), and_no_l(S.known(S.SAbsent{}), S.known(agrees), Unit{})))

def Laws.e_missing_agrees_unknown(available, measured, scope_matches):
  +measured = measured
  +scope_matches = scope_matches
  decide_unknown(available, Bool.and(S.known(measured), Bool.and(S.known(scope_matches), S.known(S.SAbsent{}))), Bool.and(S.affirmed(measured), Bool.and(S.affirmed(scope_matches), S.affirmed(S.SAbsent{}))), and_no_r(S.known(measured), Bool.and(S.known(scope_matches), S.known(S.SAbsent{})), and_no_r(S.known(scope_matches), S.known(S.SAbsent{}), Unit{})))

def Laws.e_unavailable_unknown(measured, scope_matches, agrees):
  Unit{}


# I has the same strict tri-state shape as A. Reuse constructive proof terms,
# not assumptions: conversion must still reduce the independent S.i body.
def Laws.i_pass_needs_available(available, tracked_source, scope_matches, agrees, w):
  Laws.a_pass_needs_available(available, tracked_source, scope_matches, agrees, w)

def Laws.i_pass_needs_tracked_source(available, tracked_source, scope_matches, agrees, w):
  Laws.a_pass_needs_current(available, tracked_source, scope_matches, agrees, w)

def Laws.i_pass_needs_scope_matches(available, tracked_source, scope_matches, agrees, w):
  Laws.a_pass_needs_scope_matches(available, tracked_source, scope_matches, agrees, w)

def Laws.i_pass_needs_agrees(available, tracked_source, scope_matches, agrees, w):
  Laws.a_pass_needs_agrees(available, tracked_source, scope_matches, agrees, w)

def Laws.i_positive_complete(available, tracked_source, scope_matches, agrees, h_available, h_tracked_source, h_scope_matches, h_agrees):
  Laws.a_positive_complete(available, tracked_source, scope_matches, agrees, h_available, h_tracked_source, h_scope_matches, h_agrees)

def Laws.i_known_negative_complete(available, tracked_source, scope_matches, agrees, h_available, k_tracked_source, k_scope_matches, k_agrees, negative):
  Laws.a_known_negative_complete(available, tracked_source, scope_matches, agrees, h_available, k_tracked_source, k_scope_matches, k_agrees, negative)

def Laws.i_missing_available_unknown(tracked_source, scope_matches, agrees):
  Unit{}

def Laws.i_missing_tracked_source_unknown(available, scope_matches, agrees):
  Laws.a_missing_current_unknown(available, scope_matches, agrees)

def Laws.i_missing_scope_matches_unknown(available, tracked_source, agrees):
  Laws.a_missing_scope_matches_unknown(available, tracked_source, agrees)

def Laws.i_missing_agrees_unknown(available, tracked_source, scope_matches):
  Laws.a_missing_agrees_unknown(available, tracked_source, scope_matches)

def Laws.i_unavailable_unknown(tracked_source, scope_matches, agrees):
  Unit{}
