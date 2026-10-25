# Independent root-cause obligations. Numeric thresholds and observation identity
# are Python adapter responsibilities, not claims proved by these Boolean laws.
import Base
import ./root.bend as S

def T(b: Bool) -> Data:
  match b:
    case True{}:
      Unit
    case False{}:
      Empty

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

law r1_pass_needs_target:
  for target: S.Evidence
  for before_metric: S.Evidence
  for functional_probe: S.Evidence
  for expiry_valid: S.Evidence
  for w: T(S.passed(S.r1(target, before_metric, functional_probe, expiry_valid)))
  Truth(target)

law r1_pass_needs_before_metric:
  for target: S.Evidence
  for before_metric: S.Evidence
  for functional_probe: S.Evidence
  for expiry_valid: S.Evidence
  for w: T(S.passed(S.r1(target, before_metric, functional_probe, expiry_valid)))
  Truth(before_metric)

law r1_pass_needs_functional_probe:
  for target: S.Evidence
  for before_metric: S.Evidence
  for functional_probe: S.Evidence
  for expiry_valid: S.Evidence
  for w: T(S.passed(S.r1(target, before_metric, functional_probe, expiry_valid)))
  Truth(functional_probe)

law r1_pass_needs_expiry_valid:
  for target: S.Evidence
  for before_metric: S.Evidence
  for functional_probe: S.Evidence
  for expiry_valid: S.Evidence
  for w: T(S.passed(S.r1(target, before_metric, functional_probe, expiry_valid)))
  Truth(expiry_valid)

law r1_positive_complete:
  for target: S.Evidence
  for before_metric: S.Evidence
  for functional_probe: S.Evidence
  for expiry_valid: S.Evidence
  for h_target: Truth(target)
  for h_before_metric: Truth(before_metric)
  for h_functional_probe: Truth(functional_probe)
  for h_expiry_valid: Truth(expiry_valid)
  T(S.passed(S.r1(target, before_metric, functional_probe, expiry_valid)))

law r1_negative_complete:
  for target: S.Evidence
  for before_metric: S.Evidence
  for functional_probe: S.Evidence
  for expiry_valid: S.Evidence
  for k_target: Known(target)
  for k_before_metric: Known(before_metric)
  for k_functional_probe: Known(functional_probe)
  for k_expiry_valid: Known(expiry_valid)
  for negative: T(Bool.not(Bool.and(S.affirmed(target), Bool.and(S.affirmed(before_metric), Bool.and(S.affirmed(functional_probe), S.affirmed(expiry_valid))))))
  T(S.violated(S.r1(target, before_metric, functional_probe, expiry_valid)))

law r1_missing_target_unknown:
  for before_metric: S.Evidence
  for functional_probe: S.Evidence
  for expiry_valid: S.Evidence
  T(S.unknown(S.r1(S.SAbsent{}, before_metric, functional_probe, expiry_valid)))

law r1_missing_before_metric_unknown:
  for target: S.Evidence
  for functional_probe: S.Evidence
  for expiry_valid: S.Evidence
  T(S.unknown(S.r1(target, S.SAbsent{}, functional_probe, expiry_valid)))

law r1_missing_functional_probe_unknown:
  for target: S.Evidence
  for before_metric: S.Evidence
  for expiry_valid: S.Evidence
  T(S.unknown(S.r1(target, before_metric, S.SAbsent{}, expiry_valid)))

law r1_missing_expiry_valid_unknown:
  for target: S.Evidence
  for before_metric: S.Evidence
  for functional_probe: S.Evidence
  T(S.unknown(S.r1(target, before_metric, functional_probe, S.SAbsent{})))

law r2_pass_needs_same_metric:
  for same_metric: S.Evidence
  for in_bounds_twice: S.Evidence
  for separated_24h: S.Evidence
  for w: T(S.passed(S.r2(same_metric, in_bounds_twice, separated_24h)))
  Truth(same_metric)

law r2_pass_needs_in_bounds_twice:
  for same_metric: S.Evidence
  for in_bounds_twice: S.Evidence
  for separated_24h: S.Evidence
  for w: T(S.passed(S.r2(same_metric, in_bounds_twice, separated_24h)))
  Truth(in_bounds_twice)

law r2_pass_needs_separated_24h:
  for same_metric: S.Evidence
  for in_bounds_twice: S.Evidence
  for separated_24h: S.Evidence
  for w: T(S.passed(S.r2(same_metric, in_bounds_twice, separated_24h)))
  Truth(separated_24h)

law r2_positive_complete:
  for same_metric: S.Evidence
  for in_bounds_twice: S.Evidence
  for separated_24h: S.Evidence
  for h_same_metric: Truth(same_metric)
  for h_in_bounds_twice: Truth(in_bounds_twice)
  for h_separated_24h: Truth(separated_24h)
  T(S.passed(S.r2(same_metric, in_bounds_twice, separated_24h)))

law r2_negative_complete:
  for same_metric: S.Evidence
  for in_bounds_twice: S.Evidence
  for separated_24h: S.Evidence
  for k_same_metric: Known(same_metric)
  for k_in_bounds_twice: Known(in_bounds_twice)
  for k_separated_24h: Known(separated_24h)
  for negative: T(Bool.not(Bool.and(S.affirmed(same_metric), Bool.and(S.affirmed(in_bounds_twice), S.affirmed(separated_24h)))))
  T(S.violated(S.r2(same_metric, in_bounds_twice, separated_24h)))

law r2_missing_same_metric_unknown:
  for in_bounds_twice: S.Evidence
  for separated_24h: S.Evidence
  T(S.unknown(S.r2(S.SAbsent{}, in_bounds_twice, separated_24h)))

law r2_missing_in_bounds_twice_unknown:
  for same_metric: S.Evidence
  for separated_24h: S.Evidence
  T(S.unknown(S.r2(same_metric, S.SAbsent{}, separated_24h)))

law r2_missing_separated_24h_unknown:
  for same_metric: S.Evidence
  for in_bounds_twice: S.Evidence
  T(S.unknown(S.r2(same_metric, in_bounds_twice, S.SAbsent{})))

law r3_pass_needs_probe_failed:
  for probe_failed: S.Evidence
  for owned: S.Evidence
  for leased: S.Evidence
  for w: T(S.passed(S.r3(probe_failed, owned, leased)))
  Truth(probe_failed)

law r3_pass_needs_owned:
  for probe_failed: S.Evidence
  for owned: S.Evidence
  for leased: S.Evidence
  for w: T(S.passed(S.r3(probe_failed, owned, leased)))
  Truth(owned)

law r3_pass_needs_leased:
  for probe_failed: S.Evidence
  for owned: S.Evidence
  for leased: S.Evidence
  for w: T(S.passed(S.r3(probe_failed, owned, leased)))
  Truth(leased)

law r3_positive_complete:
  for probe_failed: S.Evidence
  for owned: S.Evidence
  for leased: S.Evidence
  for h_probe_failed: Truth(probe_failed)
  for h_owned: Truth(owned)
  for h_leased: Truth(leased)
  T(S.passed(S.r3(probe_failed, owned, leased)))

law r3_negative_complete:
  for probe_failed: S.Evidence
  for owned: S.Evidence
  for leased: S.Evidence
  for k_probe_failed: Known(probe_failed)
  for k_owned: Known(owned)
  for k_leased: Known(leased)
  for negative: T(Bool.not(Bool.and(S.affirmed(probe_failed), Bool.and(S.affirmed(owned), S.affirmed(leased)))))
  T(S.violated(S.r3(probe_failed, owned, leased)))

law r3_missing_probe_failed_unknown:
  for owned: S.Evidence
  for leased: S.Evidence
  T(S.unknown(S.r3(S.SAbsent{}, owned, leased)))

law r3_missing_owned_unknown:
  for probe_failed: S.Evidence
  for leased: S.Evidence
  T(S.unknown(S.r3(probe_failed, S.SAbsent{}, leased)))

law r3_missing_leased_unknown:
  for probe_failed: S.Evidence
  for owned: S.Evidence
  T(S.unknown(S.r3(probe_failed, owned, S.SAbsent{})))

law r4_pass_needs_same_window:
  for same_window: S.Evidence
  for share_at_least_30: S.Evidence
  for w: T(S.passed(S.r4(same_window, share_at_least_30)))
  Truth(same_window)

law r4_pass_needs_share_at_least_30:
  for same_window: S.Evidence
  for share_at_least_30: S.Evidence
  for w: T(S.passed(S.r4(same_window, share_at_least_30)))
  Truth(share_at_least_30)

law r4_positive_complete:
  for same_window: S.Evidence
  for share_at_least_30: S.Evidence
  for h_same_window: Truth(same_window)
  for h_share_at_least_30: Truth(share_at_least_30)
  T(S.passed(S.r4(same_window, share_at_least_30)))

law r4_negative_complete:
  for same_window: S.Evidence
  for share_at_least_30: S.Evidence
  for k_same_window: Known(same_window)
  for k_share_at_least_30: Known(share_at_least_30)
  for negative: T(Bool.not(Bool.and(S.affirmed(same_window), S.affirmed(share_at_least_30))))
  T(S.violated(S.r4(same_window, share_at_least_30)))

law r4_missing_same_window_unknown:
  for share_at_least_30: S.Evidence
  T(S.unknown(S.r4(S.SAbsent{}, share_at_least_30)))

law r4_missing_share_at_least_30_unknown:
  for same_window: S.Evidence
  T(S.unknown(S.r4(same_window, S.SAbsent{})))

law r7_pass_needs_report_only:
  for report_only: S.Evidence
  for same_finding: S.Evidence
  for age_expired: S.Evidence
  for w: T(S.passed(S.r7(report_only, same_finding, age_expired)))
  Truth(report_only)

law r7_pass_needs_same_finding:
  for report_only: S.Evidence
  for same_finding: S.Evidence
  for age_expired: S.Evidence
  for w: T(S.passed(S.r7(report_only, same_finding, age_expired)))
  Truth(same_finding)

law r7_pass_needs_age_expired:
  for report_only: S.Evidence
  for same_finding: S.Evidence
  for age_expired: S.Evidence
  for w: T(S.passed(S.r7(report_only, same_finding, age_expired)))
  Truth(age_expired)

law r7_positive_complete:
  for report_only: S.Evidence
  for same_finding: S.Evidence
  for age_expired: S.Evidence
  for h_report_only: Truth(report_only)
  for h_same_finding: Truth(same_finding)
  for h_age_expired: Truth(age_expired)
  T(S.passed(S.r7(report_only, same_finding, age_expired)))

law r7_negative_complete:
  for report_only: S.Evidence
  for same_finding: S.Evidence
  for age_expired: S.Evidence
  for k_report_only: Known(report_only)
  for k_same_finding: Known(same_finding)
  for k_age_expired: Known(age_expired)
  for negative: T(Bool.not(Bool.and(S.affirmed(report_only), Bool.and(S.affirmed(same_finding), S.affirmed(age_expired)))))
  T(S.violated(S.r7(report_only, same_finding, age_expired)))

law r7_missing_report_only_unknown:
  for same_finding: S.Evidence
  for age_expired: S.Evidence
  T(S.unknown(S.r7(S.SAbsent{}, same_finding, age_expired)))

law r7_missing_same_finding_unknown:
  for report_only: S.Evidence
  for age_expired: S.Evidence
  T(S.unknown(S.r7(report_only, S.SAbsent{}, age_expired)))

law r7_missing_age_expired_unknown:
  for report_only: S.Evidence
  for same_finding: S.Evidence
  T(S.unknown(S.r7(report_only, same_finding, S.SAbsent{})))


# The full-result adapter cannot recommend rollback without a passing gate.
law r3_result_rollback_sound:
  for v: S.Verdict
  for failed: Bool
  for w: T(Nat.is_eq(S.r3_finish(v, failed), 3n))
  T(S.passed(v))

law r3_result_rollback_complete:
  for failed: Bool
  T(Nat.is_eq(S.r3_finish(S.SPass{}, failed), 3n))
