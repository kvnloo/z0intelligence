# Constructive dependent proofs for arbitrary evidence; no assumed axioms.
import Base
import ./root.bend as S
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

def Laws.r1_pass_needs_target(target, before_metric, functional_probe, expiry_valid, w):
  +target = target
  +before_metric = before_metric
  +functional_probe = functional_probe
  +expiry_valid = expiry_valid
  affirmed_truth(target, and_l(S.affirmed(target), Bool.and(S.affirmed(before_metric), Bool.and(S.affirmed(functional_probe), S.affirmed(expiry_valid))), decide_holds(S.SBool{True{}}, Bool.and(S.known(target), Bool.and(S.known(before_metric), Bool.and(S.known(functional_probe), S.known(expiry_valid)))), Bool.and(S.affirmed(target), Bool.and(S.affirmed(before_metric), Bool.and(S.affirmed(functional_probe), S.affirmed(expiry_valid)))), w)))

def Laws.r1_pass_needs_before_metric(target, before_metric, functional_probe, expiry_valid, w):
  +target = target
  +before_metric = before_metric
  +functional_probe = functional_probe
  +expiry_valid = expiry_valid
  affirmed_truth(before_metric, and_l(S.affirmed(before_metric), Bool.and(S.affirmed(functional_probe), S.affirmed(expiry_valid)), and_r(S.affirmed(target), Bool.and(S.affirmed(before_metric), Bool.and(S.affirmed(functional_probe), S.affirmed(expiry_valid))), decide_holds(S.SBool{True{}}, Bool.and(S.known(target), Bool.and(S.known(before_metric), Bool.and(S.known(functional_probe), S.known(expiry_valid)))), Bool.and(S.affirmed(target), Bool.and(S.affirmed(before_metric), Bool.and(S.affirmed(functional_probe), S.affirmed(expiry_valid)))), w))))

def Laws.r1_pass_needs_functional_probe(target, before_metric, functional_probe, expiry_valid, w):
  +target = target
  +before_metric = before_metric
  +functional_probe = functional_probe
  +expiry_valid = expiry_valid
  affirmed_truth(functional_probe, and_l(S.affirmed(functional_probe), S.affirmed(expiry_valid), and_r(S.affirmed(before_metric), Bool.and(S.affirmed(functional_probe), S.affirmed(expiry_valid)), and_r(S.affirmed(target), Bool.and(S.affirmed(before_metric), Bool.and(S.affirmed(functional_probe), S.affirmed(expiry_valid))), decide_holds(S.SBool{True{}}, Bool.and(S.known(target), Bool.and(S.known(before_metric), Bool.and(S.known(functional_probe), S.known(expiry_valid)))), Bool.and(S.affirmed(target), Bool.and(S.affirmed(before_metric), Bool.and(S.affirmed(functional_probe), S.affirmed(expiry_valid)))), w)))))

def Laws.r1_pass_needs_expiry_valid(target, before_metric, functional_probe, expiry_valid, w):
  +target = target
  +before_metric = before_metric
  +functional_probe = functional_probe
  +expiry_valid = expiry_valid
  affirmed_truth(expiry_valid, and_r(S.affirmed(functional_probe), S.affirmed(expiry_valid), and_r(S.affirmed(before_metric), Bool.and(S.affirmed(functional_probe), S.affirmed(expiry_valid)), and_r(S.affirmed(target), Bool.and(S.affirmed(before_metric), Bool.and(S.affirmed(functional_probe), S.affirmed(expiry_valid))), decide_holds(S.SBool{True{}}, Bool.and(S.known(target), Bool.and(S.known(before_metric), Bool.and(S.known(functional_probe), S.known(expiry_valid)))), Bool.and(S.affirmed(target), Bool.and(S.affirmed(before_metric), Bool.and(S.affirmed(functional_probe), S.affirmed(expiry_valid)))), w)))))

def Laws.r1_positive_complete(target, before_metric, functional_probe, expiry_valid, h_target, h_before_metric, h_functional_probe, h_expiry_valid):
  +target = target
  +before_metric = before_metric
  +functional_probe = functional_probe
  +expiry_valid = expiry_valid
  +h_target = h_target
  +h_before_metric = h_before_metric
  +h_functional_probe = h_functional_probe
  +h_expiry_valid = h_expiry_valid
  decide_positive(S.SBool{True{}}, Bool.and(S.known(target), Bool.and(S.known(before_metric), Bool.and(S.known(functional_probe), S.known(expiry_valid)))), Bool.and(S.affirmed(target), Bool.and(S.affirmed(before_metric), Bool.and(S.affirmed(functional_probe), S.affirmed(expiry_valid)))), Unit{}, and_i(S.known(target), Bool.and(S.known(before_metric), Bool.and(S.known(functional_probe), S.known(expiry_valid))), truth_known(target, h_target), and_i(S.known(before_metric), Bool.and(S.known(functional_probe), S.known(expiry_valid)), truth_known(before_metric, h_before_metric), and_i(S.known(functional_probe), S.known(expiry_valid), truth_known(functional_probe, h_functional_probe), truth_known(expiry_valid, h_expiry_valid)))), and_i(S.affirmed(target), Bool.and(S.affirmed(before_metric), Bool.and(S.affirmed(functional_probe), S.affirmed(expiry_valid))), truth_affirmed(target, h_target), and_i(S.affirmed(before_metric), Bool.and(S.affirmed(functional_probe), S.affirmed(expiry_valid)), truth_affirmed(before_metric, h_before_metric), and_i(S.affirmed(functional_probe), S.affirmed(expiry_valid), truth_affirmed(functional_probe, h_functional_probe), truth_affirmed(expiry_valid, h_expiry_valid)))))

def Laws.r1_negative_complete(target, before_metric, functional_probe, expiry_valid, k_target, k_before_metric, k_functional_probe, k_expiry_valid, negative):
  +target = target
  +before_metric = before_metric
  +functional_probe = functional_probe
  +expiry_valid = expiry_valid
  decide_negative(S.SBool{True{}}, Bool.and(S.known(target), Bool.and(S.known(before_metric), Bool.and(S.known(functional_probe), S.known(expiry_valid)))), Bool.and(S.affirmed(target), Bool.and(S.affirmed(before_metric), Bool.and(S.affirmed(functional_probe), S.affirmed(expiry_valid)))), Unit{}, and_i(S.known(target), Bool.and(S.known(before_metric), Bool.and(S.known(functional_probe), S.known(expiry_valid))), known_bool(target, k_target), and_i(S.known(before_metric), Bool.and(S.known(functional_probe), S.known(expiry_valid)), known_bool(before_metric, k_before_metric), and_i(S.known(functional_probe), S.known(expiry_valid), known_bool(functional_probe, k_functional_probe), known_bool(expiry_valid, k_expiry_valid)))), negative)

def Laws.r1_missing_target_unknown(before_metric, functional_probe, expiry_valid):
  +before_metric = before_metric
  +functional_probe = functional_probe
  +expiry_valid = expiry_valid
  decide_unknown(S.SBool{True{}}, Bool.and(S.known(S.SAbsent{}), Bool.and(S.known(before_metric), Bool.and(S.known(functional_probe), S.known(expiry_valid)))), Bool.and(S.affirmed(S.SAbsent{}), Bool.and(S.affirmed(before_metric), Bool.and(S.affirmed(functional_probe), S.affirmed(expiry_valid)))), and_no_l(S.known(S.SAbsent{}), Bool.and(S.known(before_metric), Bool.and(S.known(functional_probe), S.known(expiry_valid))), Unit{}))

def Laws.r1_missing_before_metric_unknown(target, functional_probe, expiry_valid):
  +target = target
  +functional_probe = functional_probe
  +expiry_valid = expiry_valid
  decide_unknown(S.SBool{True{}}, Bool.and(S.known(target), Bool.and(S.known(S.SAbsent{}), Bool.and(S.known(functional_probe), S.known(expiry_valid)))), Bool.and(S.affirmed(target), Bool.and(S.affirmed(S.SAbsent{}), Bool.and(S.affirmed(functional_probe), S.affirmed(expiry_valid)))), and_no_r(S.known(target), Bool.and(S.known(S.SAbsent{}), Bool.and(S.known(functional_probe), S.known(expiry_valid))), and_no_l(S.known(S.SAbsent{}), Bool.and(S.known(functional_probe), S.known(expiry_valid)), Unit{})))

def Laws.r1_missing_functional_probe_unknown(target, before_metric, expiry_valid):
  +target = target
  +before_metric = before_metric
  +expiry_valid = expiry_valid
  decide_unknown(S.SBool{True{}}, Bool.and(S.known(target), Bool.and(S.known(before_metric), Bool.and(S.known(S.SAbsent{}), S.known(expiry_valid)))), Bool.and(S.affirmed(target), Bool.and(S.affirmed(before_metric), Bool.and(S.affirmed(S.SAbsent{}), S.affirmed(expiry_valid)))), and_no_r(S.known(target), Bool.and(S.known(before_metric), Bool.and(S.known(S.SAbsent{}), S.known(expiry_valid))), and_no_r(S.known(before_metric), Bool.and(S.known(S.SAbsent{}), S.known(expiry_valid)), and_no_l(S.known(S.SAbsent{}), S.known(expiry_valid), Unit{}))))

def Laws.r1_missing_expiry_valid_unknown(target, before_metric, functional_probe):
  +target = target
  +before_metric = before_metric
  +functional_probe = functional_probe
  decide_unknown(S.SBool{True{}}, Bool.and(S.known(target), Bool.and(S.known(before_metric), Bool.and(S.known(functional_probe), S.known(S.SAbsent{})))), Bool.and(S.affirmed(target), Bool.and(S.affirmed(before_metric), Bool.and(S.affirmed(functional_probe), S.affirmed(S.SAbsent{})))), and_no_r(S.known(target), Bool.and(S.known(before_metric), Bool.and(S.known(functional_probe), S.known(S.SAbsent{}))), and_no_r(S.known(before_metric), Bool.and(S.known(functional_probe), S.known(S.SAbsent{})), and_no_r(S.known(functional_probe), S.known(S.SAbsent{}), Unit{}))))

def Laws.r2_pass_needs_same_metric(same_metric, in_bounds_twice, separated_24h, w):
  +same_metric = same_metric
  +in_bounds_twice = in_bounds_twice
  +separated_24h = separated_24h
  affirmed_truth(same_metric, and_l(S.affirmed(same_metric), Bool.and(S.affirmed(in_bounds_twice), S.affirmed(separated_24h)), decide_holds(S.SBool{True{}}, Bool.and(S.known(same_metric), Bool.and(S.known(in_bounds_twice), S.known(separated_24h))), Bool.and(S.affirmed(same_metric), Bool.and(S.affirmed(in_bounds_twice), S.affirmed(separated_24h))), w)))

def Laws.r2_pass_needs_in_bounds_twice(same_metric, in_bounds_twice, separated_24h, w):
  +same_metric = same_metric
  +in_bounds_twice = in_bounds_twice
  +separated_24h = separated_24h
  affirmed_truth(in_bounds_twice, and_l(S.affirmed(in_bounds_twice), S.affirmed(separated_24h), and_r(S.affirmed(same_metric), Bool.and(S.affirmed(in_bounds_twice), S.affirmed(separated_24h)), decide_holds(S.SBool{True{}}, Bool.and(S.known(same_metric), Bool.and(S.known(in_bounds_twice), S.known(separated_24h))), Bool.and(S.affirmed(same_metric), Bool.and(S.affirmed(in_bounds_twice), S.affirmed(separated_24h))), w))))

def Laws.r2_pass_needs_separated_24h(same_metric, in_bounds_twice, separated_24h, w):
  +same_metric = same_metric
  +in_bounds_twice = in_bounds_twice
  +separated_24h = separated_24h
  affirmed_truth(separated_24h, and_r(S.affirmed(in_bounds_twice), S.affirmed(separated_24h), and_r(S.affirmed(same_metric), Bool.and(S.affirmed(in_bounds_twice), S.affirmed(separated_24h)), decide_holds(S.SBool{True{}}, Bool.and(S.known(same_metric), Bool.and(S.known(in_bounds_twice), S.known(separated_24h))), Bool.and(S.affirmed(same_metric), Bool.and(S.affirmed(in_bounds_twice), S.affirmed(separated_24h))), w))))

def Laws.r2_positive_complete(same_metric, in_bounds_twice, separated_24h, h_same_metric, h_in_bounds_twice, h_separated_24h):
  +same_metric = same_metric
  +in_bounds_twice = in_bounds_twice
  +separated_24h = separated_24h
  +h_same_metric = h_same_metric
  +h_in_bounds_twice = h_in_bounds_twice
  +h_separated_24h = h_separated_24h
  decide_positive(S.SBool{True{}}, Bool.and(S.known(same_metric), Bool.and(S.known(in_bounds_twice), S.known(separated_24h))), Bool.and(S.affirmed(same_metric), Bool.and(S.affirmed(in_bounds_twice), S.affirmed(separated_24h))), Unit{}, and_i(S.known(same_metric), Bool.and(S.known(in_bounds_twice), S.known(separated_24h)), truth_known(same_metric, h_same_metric), and_i(S.known(in_bounds_twice), S.known(separated_24h), truth_known(in_bounds_twice, h_in_bounds_twice), truth_known(separated_24h, h_separated_24h))), and_i(S.affirmed(same_metric), Bool.and(S.affirmed(in_bounds_twice), S.affirmed(separated_24h)), truth_affirmed(same_metric, h_same_metric), and_i(S.affirmed(in_bounds_twice), S.affirmed(separated_24h), truth_affirmed(in_bounds_twice, h_in_bounds_twice), truth_affirmed(separated_24h, h_separated_24h))))

def Laws.r2_negative_complete(same_metric, in_bounds_twice, separated_24h, k_same_metric, k_in_bounds_twice, k_separated_24h, negative):
  +same_metric = same_metric
  +in_bounds_twice = in_bounds_twice
  +separated_24h = separated_24h
  decide_negative(S.SBool{True{}}, Bool.and(S.known(same_metric), Bool.and(S.known(in_bounds_twice), S.known(separated_24h))), Bool.and(S.affirmed(same_metric), Bool.and(S.affirmed(in_bounds_twice), S.affirmed(separated_24h))), Unit{}, and_i(S.known(same_metric), Bool.and(S.known(in_bounds_twice), S.known(separated_24h)), known_bool(same_metric, k_same_metric), and_i(S.known(in_bounds_twice), S.known(separated_24h), known_bool(in_bounds_twice, k_in_bounds_twice), known_bool(separated_24h, k_separated_24h))), negative)

def Laws.r2_missing_same_metric_unknown(in_bounds_twice, separated_24h):
  +in_bounds_twice = in_bounds_twice
  +separated_24h = separated_24h
  decide_unknown(S.SBool{True{}}, Bool.and(S.known(S.SAbsent{}), Bool.and(S.known(in_bounds_twice), S.known(separated_24h))), Bool.and(S.affirmed(S.SAbsent{}), Bool.and(S.affirmed(in_bounds_twice), S.affirmed(separated_24h))), and_no_l(S.known(S.SAbsent{}), Bool.and(S.known(in_bounds_twice), S.known(separated_24h)), Unit{}))

def Laws.r2_missing_in_bounds_twice_unknown(same_metric, separated_24h):
  +same_metric = same_metric
  +separated_24h = separated_24h
  decide_unknown(S.SBool{True{}}, Bool.and(S.known(same_metric), Bool.and(S.known(S.SAbsent{}), S.known(separated_24h))), Bool.and(S.affirmed(same_metric), Bool.and(S.affirmed(S.SAbsent{}), S.affirmed(separated_24h))), and_no_r(S.known(same_metric), Bool.and(S.known(S.SAbsent{}), S.known(separated_24h)), and_no_l(S.known(S.SAbsent{}), S.known(separated_24h), Unit{})))

def Laws.r2_missing_separated_24h_unknown(same_metric, in_bounds_twice):
  +same_metric = same_metric
  +in_bounds_twice = in_bounds_twice
  decide_unknown(S.SBool{True{}}, Bool.and(S.known(same_metric), Bool.and(S.known(in_bounds_twice), S.known(S.SAbsent{}))), Bool.and(S.affirmed(same_metric), Bool.and(S.affirmed(in_bounds_twice), S.affirmed(S.SAbsent{}))), and_no_r(S.known(same_metric), Bool.and(S.known(in_bounds_twice), S.known(S.SAbsent{})), and_no_r(S.known(in_bounds_twice), S.known(S.SAbsent{}), Unit{})))

def Laws.r3_pass_needs_probe_failed(probe_failed, owned, leased, w):
  +probe_failed = probe_failed
  +owned = owned
  +leased = leased
  affirmed_truth(probe_failed, and_l(S.affirmed(probe_failed), Bool.and(S.affirmed(owned), S.affirmed(leased)), decide_holds(S.SBool{True{}}, Bool.and(S.known(probe_failed), Bool.and(S.known(owned), S.known(leased))), Bool.and(S.affirmed(probe_failed), Bool.and(S.affirmed(owned), S.affirmed(leased))), w)))

def Laws.r3_pass_needs_owned(probe_failed, owned, leased, w):
  +probe_failed = probe_failed
  +owned = owned
  +leased = leased
  affirmed_truth(owned, and_l(S.affirmed(owned), S.affirmed(leased), and_r(S.affirmed(probe_failed), Bool.and(S.affirmed(owned), S.affirmed(leased)), decide_holds(S.SBool{True{}}, Bool.and(S.known(probe_failed), Bool.and(S.known(owned), S.known(leased))), Bool.and(S.affirmed(probe_failed), Bool.and(S.affirmed(owned), S.affirmed(leased))), w))))

def Laws.r3_pass_needs_leased(probe_failed, owned, leased, w):
  +probe_failed = probe_failed
  +owned = owned
  +leased = leased
  affirmed_truth(leased, and_r(S.affirmed(owned), S.affirmed(leased), and_r(S.affirmed(probe_failed), Bool.and(S.affirmed(owned), S.affirmed(leased)), decide_holds(S.SBool{True{}}, Bool.and(S.known(probe_failed), Bool.and(S.known(owned), S.known(leased))), Bool.and(S.affirmed(probe_failed), Bool.and(S.affirmed(owned), S.affirmed(leased))), w))))

def Laws.r3_positive_complete(probe_failed, owned, leased, h_probe_failed, h_owned, h_leased):
  +probe_failed = probe_failed
  +owned = owned
  +leased = leased
  +h_probe_failed = h_probe_failed
  +h_owned = h_owned
  +h_leased = h_leased
  decide_positive(S.SBool{True{}}, Bool.and(S.known(probe_failed), Bool.and(S.known(owned), S.known(leased))), Bool.and(S.affirmed(probe_failed), Bool.and(S.affirmed(owned), S.affirmed(leased))), Unit{}, and_i(S.known(probe_failed), Bool.and(S.known(owned), S.known(leased)), truth_known(probe_failed, h_probe_failed), and_i(S.known(owned), S.known(leased), truth_known(owned, h_owned), truth_known(leased, h_leased))), and_i(S.affirmed(probe_failed), Bool.and(S.affirmed(owned), S.affirmed(leased)), truth_affirmed(probe_failed, h_probe_failed), and_i(S.affirmed(owned), S.affirmed(leased), truth_affirmed(owned, h_owned), truth_affirmed(leased, h_leased))))

def Laws.r3_negative_complete(probe_failed, owned, leased, k_probe_failed, k_owned, k_leased, negative):
  +probe_failed = probe_failed
  +owned = owned
  +leased = leased
  decide_negative(S.SBool{True{}}, Bool.and(S.known(probe_failed), Bool.and(S.known(owned), S.known(leased))), Bool.and(S.affirmed(probe_failed), Bool.and(S.affirmed(owned), S.affirmed(leased))), Unit{}, and_i(S.known(probe_failed), Bool.and(S.known(owned), S.known(leased)), known_bool(probe_failed, k_probe_failed), and_i(S.known(owned), S.known(leased), known_bool(owned, k_owned), known_bool(leased, k_leased))), negative)

def Laws.r3_missing_probe_failed_unknown(owned, leased):
  +owned = owned
  +leased = leased
  decide_unknown(S.SBool{True{}}, Bool.and(S.known(S.SAbsent{}), Bool.and(S.known(owned), S.known(leased))), Bool.and(S.affirmed(S.SAbsent{}), Bool.and(S.affirmed(owned), S.affirmed(leased))), and_no_l(S.known(S.SAbsent{}), Bool.and(S.known(owned), S.known(leased)), Unit{}))

def Laws.r3_missing_owned_unknown(probe_failed, leased):
  +probe_failed = probe_failed
  +leased = leased
  decide_unknown(S.SBool{True{}}, Bool.and(S.known(probe_failed), Bool.and(S.known(S.SAbsent{}), S.known(leased))), Bool.and(S.affirmed(probe_failed), Bool.and(S.affirmed(S.SAbsent{}), S.affirmed(leased))), and_no_r(S.known(probe_failed), Bool.and(S.known(S.SAbsent{}), S.known(leased)), and_no_l(S.known(S.SAbsent{}), S.known(leased), Unit{})))

def Laws.r3_missing_leased_unknown(probe_failed, owned):
  +probe_failed = probe_failed
  +owned = owned
  decide_unknown(S.SBool{True{}}, Bool.and(S.known(probe_failed), Bool.and(S.known(owned), S.known(S.SAbsent{}))), Bool.and(S.affirmed(probe_failed), Bool.and(S.affirmed(owned), S.affirmed(S.SAbsent{}))), and_no_r(S.known(probe_failed), Bool.and(S.known(owned), S.known(S.SAbsent{})), and_no_r(S.known(owned), S.known(S.SAbsent{}), Unit{})))

def Laws.r4_pass_needs_same_window(same_window, share_at_least_30, w):
  +same_window = same_window
  +share_at_least_30 = share_at_least_30
  affirmed_truth(same_window, and_l(S.affirmed(same_window), S.affirmed(share_at_least_30), decide_holds(S.SBool{True{}}, Bool.and(S.known(same_window), S.known(share_at_least_30)), Bool.and(S.affirmed(same_window), S.affirmed(share_at_least_30)), w)))

def Laws.r4_pass_needs_share_at_least_30(same_window, share_at_least_30, w):
  +same_window = same_window
  +share_at_least_30 = share_at_least_30
  affirmed_truth(share_at_least_30, and_r(S.affirmed(same_window), S.affirmed(share_at_least_30), decide_holds(S.SBool{True{}}, Bool.and(S.known(same_window), S.known(share_at_least_30)), Bool.and(S.affirmed(same_window), S.affirmed(share_at_least_30)), w)))

def Laws.r4_positive_complete(same_window, share_at_least_30, h_same_window, h_share_at_least_30):
  +same_window = same_window
  +share_at_least_30 = share_at_least_30
  +h_same_window = h_same_window
  +h_share_at_least_30 = h_share_at_least_30
  decide_positive(S.SBool{True{}}, Bool.and(S.known(same_window), S.known(share_at_least_30)), Bool.and(S.affirmed(same_window), S.affirmed(share_at_least_30)), Unit{}, and_i(S.known(same_window), S.known(share_at_least_30), truth_known(same_window, h_same_window), truth_known(share_at_least_30, h_share_at_least_30)), and_i(S.affirmed(same_window), S.affirmed(share_at_least_30), truth_affirmed(same_window, h_same_window), truth_affirmed(share_at_least_30, h_share_at_least_30)))

def Laws.r4_negative_complete(same_window, share_at_least_30, k_same_window, k_share_at_least_30, negative):
  +same_window = same_window
  +share_at_least_30 = share_at_least_30
  decide_negative(S.SBool{True{}}, Bool.and(S.known(same_window), S.known(share_at_least_30)), Bool.and(S.affirmed(same_window), S.affirmed(share_at_least_30)), Unit{}, and_i(S.known(same_window), S.known(share_at_least_30), known_bool(same_window, k_same_window), known_bool(share_at_least_30, k_share_at_least_30)), negative)

def Laws.r4_missing_same_window_unknown(share_at_least_30):
  +share_at_least_30 = share_at_least_30
  decide_unknown(S.SBool{True{}}, Bool.and(S.known(S.SAbsent{}), S.known(share_at_least_30)), Bool.and(S.affirmed(S.SAbsent{}), S.affirmed(share_at_least_30)), and_no_l(S.known(S.SAbsent{}), S.known(share_at_least_30), Unit{}))

def Laws.r4_missing_share_at_least_30_unknown(same_window):
  +same_window = same_window
  decide_unknown(S.SBool{True{}}, Bool.and(S.known(same_window), S.known(S.SAbsent{})), Bool.and(S.affirmed(same_window), S.affirmed(S.SAbsent{})), and_no_r(S.known(same_window), S.known(S.SAbsent{}), Unit{}))

def Laws.r7_pass_needs_report_only(report_only, same_finding, age_expired, w):
  +report_only = report_only
  +same_finding = same_finding
  +age_expired = age_expired
  affirmed_truth(report_only, and_l(S.affirmed(report_only), Bool.and(S.affirmed(same_finding), S.affirmed(age_expired)), decide_holds(S.SBool{True{}}, Bool.and(S.known(report_only), Bool.and(S.known(same_finding), S.known(age_expired))), Bool.and(S.affirmed(report_only), Bool.and(S.affirmed(same_finding), S.affirmed(age_expired))), w)))

def Laws.r7_pass_needs_same_finding(report_only, same_finding, age_expired, w):
  +report_only = report_only
  +same_finding = same_finding
  +age_expired = age_expired
  affirmed_truth(same_finding, and_l(S.affirmed(same_finding), S.affirmed(age_expired), and_r(S.affirmed(report_only), Bool.and(S.affirmed(same_finding), S.affirmed(age_expired)), decide_holds(S.SBool{True{}}, Bool.and(S.known(report_only), Bool.and(S.known(same_finding), S.known(age_expired))), Bool.and(S.affirmed(report_only), Bool.and(S.affirmed(same_finding), S.affirmed(age_expired))), w))))

def Laws.r7_pass_needs_age_expired(report_only, same_finding, age_expired, w):
  +report_only = report_only
  +same_finding = same_finding
  +age_expired = age_expired
  affirmed_truth(age_expired, and_r(S.affirmed(same_finding), S.affirmed(age_expired), and_r(S.affirmed(report_only), Bool.and(S.affirmed(same_finding), S.affirmed(age_expired)), decide_holds(S.SBool{True{}}, Bool.and(S.known(report_only), Bool.and(S.known(same_finding), S.known(age_expired))), Bool.and(S.affirmed(report_only), Bool.and(S.affirmed(same_finding), S.affirmed(age_expired))), w))))

def Laws.r7_positive_complete(report_only, same_finding, age_expired, h_report_only, h_same_finding, h_age_expired):
  +report_only = report_only
  +same_finding = same_finding
  +age_expired = age_expired
  +h_report_only = h_report_only
  +h_same_finding = h_same_finding
  +h_age_expired = h_age_expired
  decide_positive(S.SBool{True{}}, Bool.and(S.known(report_only), Bool.and(S.known(same_finding), S.known(age_expired))), Bool.and(S.affirmed(report_only), Bool.and(S.affirmed(same_finding), S.affirmed(age_expired))), Unit{}, and_i(S.known(report_only), Bool.and(S.known(same_finding), S.known(age_expired)), truth_known(report_only, h_report_only), and_i(S.known(same_finding), S.known(age_expired), truth_known(same_finding, h_same_finding), truth_known(age_expired, h_age_expired))), and_i(S.affirmed(report_only), Bool.and(S.affirmed(same_finding), S.affirmed(age_expired)), truth_affirmed(report_only, h_report_only), and_i(S.affirmed(same_finding), S.affirmed(age_expired), truth_affirmed(same_finding, h_same_finding), truth_affirmed(age_expired, h_age_expired))))

def Laws.r7_negative_complete(report_only, same_finding, age_expired, k_report_only, k_same_finding, k_age_expired, negative):
  +report_only = report_only
  +same_finding = same_finding
  +age_expired = age_expired
  decide_negative(S.SBool{True{}}, Bool.and(S.known(report_only), Bool.and(S.known(same_finding), S.known(age_expired))), Bool.and(S.affirmed(report_only), Bool.and(S.affirmed(same_finding), S.affirmed(age_expired))), Unit{}, and_i(S.known(report_only), Bool.and(S.known(same_finding), S.known(age_expired)), known_bool(report_only, k_report_only), and_i(S.known(same_finding), S.known(age_expired), known_bool(same_finding, k_same_finding), known_bool(age_expired, k_age_expired))), negative)

def Laws.r7_missing_report_only_unknown(same_finding, age_expired):
  +same_finding = same_finding
  +age_expired = age_expired
  decide_unknown(S.SBool{True{}}, Bool.and(S.known(S.SAbsent{}), Bool.and(S.known(same_finding), S.known(age_expired))), Bool.and(S.affirmed(S.SAbsent{}), Bool.and(S.affirmed(same_finding), S.affirmed(age_expired))), and_no_l(S.known(S.SAbsent{}), Bool.and(S.known(same_finding), S.known(age_expired)), Unit{}))

def Laws.r7_missing_same_finding_unknown(report_only, age_expired):
  +report_only = report_only
  +age_expired = age_expired
  decide_unknown(S.SBool{True{}}, Bool.and(S.known(report_only), Bool.and(S.known(S.SAbsent{}), S.known(age_expired))), Bool.and(S.affirmed(report_only), Bool.and(S.affirmed(S.SAbsent{}), S.affirmed(age_expired))), and_no_r(S.known(report_only), Bool.and(S.known(S.SAbsent{}), S.known(age_expired)), and_no_l(S.known(S.SAbsent{}), S.known(age_expired), Unit{})))

def Laws.r7_missing_age_expired_unknown(report_only, same_finding):
  +report_only = report_only
  +same_finding = same_finding
  decide_unknown(S.SBool{True{}}, Bool.and(S.known(report_only), Bool.and(S.known(same_finding), S.known(S.SAbsent{}))), Bool.and(S.affirmed(report_only), Bool.and(S.affirmed(same_finding), S.affirmed(S.SAbsent{}))), and_no_r(S.known(report_only), Bool.and(S.known(same_finding), S.known(S.SAbsent{})), and_no_r(S.known(same_finding), S.known(S.SAbsent{}), Unit{})))


def Laws.r3_result_rollback_sound(v, failed, w):
  match v:
    case S.SUnknown{}:
      w
    case S.SPass{}:
      Unit{}
    case S.SViolation{}:
      match failed:
        case True{}:
          w
        case False{}:
          w

def Laws.r3_result_rollback_complete(failed):
  Unit{}
