# Owner-review boundary: do not weaken to make PROOF check.
# These quantify over MODEL.project, the abstract form of
# memory_contract.project_memory_evidence (not source authenticity or runtime).
# Memory stage and attempted injected approval MUST NOT create capability.
import Base
import ./MODEL.bend as M

law memory_never_escalates:
  for +explicit: Bool
  for +policy: Bool
  for view: M.MemView
  {M.bounded(M.project(explicit, policy, view)) == M.bounded(M.project(explicit, policy, M.MemAbsent{})) : Bool}

law memory_instruction_is_false:
  for explicit: Bool
  for policy: Bool
  for view: M.MemView
  {M.instruction(M.project(explicit, policy, view)) == False{} : Bool}

law no_explicit_grant_no_capability:
  for policy: Bool
  for view: M.MemView
  {M.bounded(M.project(False{}, policy, view)) == False{} : Bool}

law no_policy_grant_no_capability:
  for explicit: Bool
  for view: M.MemView
  {M.bounded(M.project(explicit, False{}, view)) == False{} : Bool}

law legitimate_explicit_grant_is_preserved:
  for view: M.MemView
  {M.bounded(M.project(True{}, True{}, view)) == True{} : Bool}

law absent_does_not_claim_evidence:
  for explicit: Bool
  for policy: Bool
  {M.evidence(M.project(explicit, policy, M.MemAbsent{})) == False{} : Bool}

law admitted_evidence_survives:
  for explicit: Bool
  for policy: Bool
  for attempt: Bool
  {M.evidence(M.project(explicit, policy, M.MemAdmitted{attempt})) == True{} : Bool}

law retrieved_evidence_survives:
  for explicit: Bool
  for policy: Bool
  for attempt: Bool
  {M.evidence(M.project(explicit, policy, M.MemRetrieved{attempt})) == True{} : Bool}

law replayed_evidence_survives:
  for explicit: Bool
  for policy: Bool
  for attempt: Bool
  {M.evidence(M.project(explicit, policy, M.MemReplayed{attempt})) == True{} : Bool}
