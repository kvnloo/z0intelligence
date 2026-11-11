# Circadian notice gate: the laws (CIRCADIAN-SPEC §5 with revisions R2, R7).
# A human states them; PROOF.bend proves them; `bend PROOF.bend` checks.
#
# Scope: these are theorems about gate.bend. That the Python and TypeScript
# twins compute the same table is measured (tests/test_circadian_bend.py),
# not proven; that a notice script actually calls the gate is a lint.

import Base
import ./gate.bend as G

def T(b: Bool) -> Data:
  match b:
    case True{}:
      Unit
    case False{}:
      Empty

# C2 no-decisions-near-bed: in a quiet phase a decision is never delivered
law quiet_no_decisions:
  for +p: G.Phase
  for +a: Bool
  for +i: Bool
  for +o: Bool
  for +t: Bool
  for +k: Bool
  for q: T(G.quiet(p))
  {G.is_deliver(G.gate(p, G.Decision{}, a, i, o, t, k)) == False{} : Bool}

# C10 disengage-honored / quiet after freeze: no report in a quiet phase
law quiet_no_reports:
  for +p: G.Phase
  for +a: Bool
  for +i: Bool
  for +o: Bool
  for +t: Bool
  for +k: Bool
  for q: T(G.quiet(p))
  {G.is_deliver(G.gate(p, G.Report{}, a, i, o, t, k)) == False{} : Bool}

# quiet phases hold digests for the morning too
law quiet_no_digests:
  for +p: G.Phase
  for +a: Bool
  for +i: Bool
  for +o: Bool
  for +t: Bool
  for +k: Bool
  for q: T(G.quiet(p))
  {G.is_deliver(G.gate(p, G.Digest{}, a, i, o, t, k)) == False{} : Bool}

# C5 + R2: a critical delivered in a quiet phase is allowlisted,
# irreversible, and the owner opted in
law quiet_critical_narrow:
  for +p: G.Phase
  for +a: Bool
  for +i: Bool
  for +o: Bool
  for +t: Bool
  for +k: Bool
  for q: T(G.quiet(p))
  for w: T(G.is_deliver(G.gate(p, G.Critical{}, a, i, o, t, k)))
  T(Bool.and(a, Bool.and(i, o)))

# C3 interrupt-cap: with no capacity left a report is not delivered
law cap_holds_reports:
  for +p: G.Phase
  for +a: Bool
  for +i: Bool
  for +o: Bool
  for +t: Bool
  {G.is_deliver(G.gate(p, G.Report{}, a, i, o, t, False{})) == False{} : Bool}

# C3 for decisions
law cap_holds_decisions:
  for +p: G.Phase
  for +a: Bool
  for +i: Bool
  for +o: Bool
  for +t: Bool
  {G.is_deliver(G.gate(p, G.Decision{}, a, i, o, t, False{})) == False{} : Bool}

# C14 no-sound-in-quiet: a channel that sounds is used only in PEAK
law sound_only_in_peak:
  for +p: G.Phase
  for w: T(G.sounds(G.channel(p)))
  {p == G.Peak{} : G.Phase}

# C2 by day: a decision is delivered only in PEAK, or in SECOND for tonight's plan
law decisions_only_peak_or_plan:
  for +p: G.Phase
  for +a: Bool
  for +i: Bool
  for +o: Bool
  for +t: Bool
  for +k: Bool
  for w: T(G.is_deliver(G.gate(p, G.Decision{}, a, i, o, t, k)))
  T(Bool.or(G.is_peak(p), Bool.and(G.is_second(p), t)))
