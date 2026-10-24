# Independent obligations for intent.bend. Proofs concern the abstract gates only.
# They do not authenticate approvals or prove the JS/Python adapters faithful.
import Base
import ./intent.bend as I

def T(b: Bool) -> Data:
  match b:
    case True{}:
      Unit
    case False{}:
      Empty

law i0_mutation_requires_approval:
  for approved: Bool
  for w: T(I.authority_allows(True{}, approved))
  T(approved)

law i0_qa_without_approval:
  {I.authority_allows(False{}, False{}) == True{} : Bool}

law i0_approved_mutation:
  {I.authority_allows(True{}, True{}) == True{} : Bool}

law i1_open_blocks_end:
  for reason: Bool
  for live: Bool
  for verified: Bool
  {I.turn_allows(True{}, I.IOpen{}, reason, live, verified) == False{} : Bool}

law i1_drop_needs_reason:
  for reason: Bool
  for live: Bool
  for verified: Bool
  for w: T(I.turn_allows(True{}, I.IDropped{}, reason, live, verified))
  T(reason)

law i1_delegation_must_be_live:
  for reason: Bool
  for live: Bool
  for verified: Bool
  for w: T(I.turn_allows(True{}, I.IDelegated{}, reason, live, verified))
  T(live)

law i1_verified_needs_receipt:
  for reason: Bool
  for live: Bool
  for verified: Bool
  for w: T(I.turn_allows(True{}, I.IVerified{}, reason, live, verified))
  T(verified)

law i1_work_may_continue:
  for status: I.Status
  for reason: Bool
  for live: Bool
  for verified: Bool
  {I.turn_allows(False{}, status, reason, live, verified) == True{} : Bool}

law i2_needs_verified_update:
  for updated: Bool
  for marked: Bool
  for passed: Bool
  for tool: Bool
  for w: T(I.proof_allows(updated, marked, passed, tool))
  T(updated)

law i2_needs_verified_mark:
  for updated: Bool
  for marked: Bool
  for passed: Bool
  for tool: Bool
  for w: T(I.proof_allows(updated, marked, passed, tool))
  T(marked)

law i2_verifier_actually_passed:
  for updated: Bool
  for marked: Bool
  for passed: Bool
  for tool: Bool
  for w: T(I.proof_allows(updated, marked, passed, tool))
  T(passed)

law i2_tool_run_succeeded:
  for updated: Bool
  for marked: Bool
  for passed: Bool
  for tool: Bool
  for w: T(I.proof_allows(updated, marked, passed, tool))
  T(tool)

law i2_verifier_after_ask:
  for asked: Nat
  for changed: Nat
  for start: Nat
  for finish: Nat
  for recorded: Nat
  for w: T(I.window_allows(asked, changed, start, finish, recorded))
  T(Nat.is_gt(start, asked))

law i2_verifier_after_update:
  for asked: Nat
  for changed: Nat
  for start: Nat
  for finish: Nat
  for recorded: Nat
  for w: T(I.window_allows(asked, changed, start, finish, recorded))
  T(Bool.not(Nat.is_gt(changed, start)))

law i2_verifier_finishes_after_start:
  for asked: Nat
  for changed: Nat
  for start: Nat
  for finish: Nat
  for recorded: Nat
  for w: T(I.window_allows(asked, changed, start, finish, recorded))
  T(Bool.not(Nat.is_gt(start, finish)))

law i2_update_after_verifier:
  for asked: Nat
  for changed: Nat
  for start: Nat
  for finish: Nat
  for recorded: Nat
  for w: T(I.window_allows(asked, changed, start, finish, recorded))
  T(Bool.not(Nat.is_gt(finish, recorded)))

law i2_delegate_result_received:
  for received: Bool
  for result: Nat
  for start: Nat
  for w: T(I.delegate_allows(True{}, received, result, start))
  T(received)

law i2_delegate_result_before_verifier:
  for received: Bool
  for result: Nat
  for start: Nat
  for w: T(I.delegate_allows(True{}, received, result, start))
  T(Bool.not(Nat.is_gt(result, start)))

law i2_claim_requires_proof:
  for proof: Bool
  for fresh: Bool
  for delegate: Bool
  for identity: Bool
  for w: T(I.claim_allows(proof, fresh, delegate, identity))
  T(proof)

law i2_claim_requires_freshness:
  for proof: Bool
  for fresh: Bool
  for delegate: Bool
  for identity: Bool
  for w: T(I.claim_allows(proof, fresh, delegate, identity))
  T(fresh)

law i2_claim_requires_delegate_result:
  for proof: Bool
  for fresh: Bool
  for delegate: Bool
  for identity: Bool
  for w: T(I.claim_allows(proof, fresh, delegate, identity))
  T(delegate)

law i3_verifier_target_exact:
  for task: Nat
  for verifier: Nat
  for result: Nat
  for acceptance: Nat
  for used: Nat
  for w: T(I.identity_allows(task, verifier, result, acceptance, used))
  {task == verifier : Nat}

law i3_result_target_exact:
  for task: Nat
  for verifier: Nat
  for result: Nat
  for acceptance: Nat
  for used: Nat
  for w: T(I.identity_allows(task, verifier, result, acceptance, used))
  {task == result : Nat}

law i3_acceptance_verifier_exact:
  for task: Nat
  for verifier: Nat
  for result: Nat
  for acceptance: Nat
  for used: Nat
  for w: T(I.identity_allows(task, verifier, result, acceptance, used))
  {acceptance == used : Nat}

law i3_claim_requires_identity:
  for proof: Bool
  for fresh: Bool
  for delegate: Bool
  for identity: Bool
  for w: T(I.claim_allows(proof, fresh, delegate, identity))
  T(identity)

law i4_forwarded_ids_are_carried:
  for forwarded: List<&2, Nat>
  for carried: List<&2, Nat>
  for x: Nat
  for w: T(I.relay_allows(forwarded, carried))
  for m: T(I.mem(x, forwarded))
  T(I.mem(x, carried))

law i4_empty_forward_is_allowed:
  for carried: List<&2, Nat>
  {I.relay_allows(Nil{}, carried) == True{} : Bool}

law i4_missing_id_is_rejected:
  {I.relay_allows(1n <> Nil{}, 0n <> Nil{}) == False{} : Bool}

# Positive witnesses exclude an always-refuse implementation.
law i1_reasoned_drop_allowed:
  {I.turn_allows(True{}, I.IDropped{}, True{}, False{}, False{}) == True{} : Bool}

law i1_live_delegation_allowed:
  {I.turn_allows(True{}, I.IDelegated{}, False{}, True{}, False{}) == True{} : Bool}

law i1_verified_end_allowed:
  {I.turn_allows(True{}, I.IVerified{}, False{}, False{}, True{}) == True{} : Bool}

law i2_successful_proof_allowed:
  {I.proof_allows(True{}, True{}, True{}, True{}) == True{} : Bool}

law i2_equal_execution_boundary_allowed:
  {I.window_allows(2n, 3n, 3n, 3n, 3n) == True{} : Bool}

law i2_valid_claim_allowed:
  {I.claim_allows(True{}, True{}, True{}, True{}) == True{} : Bool}

law i3_identical_target_allowed:
  {I.identity_allows(7n, 7n, 7n, 11n, 11n) == True{} : Bool}

law i4_carried_superset_allowed:
  {I.relay_allows(2n <> 1n <> Nil{}, 0n <> 1n <> 2n <> Nil{}) == True{} : Bool}

law i2_execution_at_ask_rejected:
  {I.window_allows(3n, 3n, 3n, 3n, 3n) == False{} : Bool}
