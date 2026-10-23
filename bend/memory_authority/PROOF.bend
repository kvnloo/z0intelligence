# Dependent proofs inhabit the exact imported LAWS, not a duplicated spec.
import Base
import ./MODEL.bend as M
import ./LAWS.bend as Laws

def Laws.memory_never_escalates(explicit, policy, view):
  match view:
    case M.MemAbsent{}:
      {==}
    case M.MemAdmitted{attempt}:
      {==}
    case M.MemRetrieved{attempt}:
      {==}
    case M.MemReplayed{attempt}:
      {==}

def Laws.memory_instruction_is_false(explicit, policy, view):
  {==}

def Laws.no_explicit_grant_no_capability(policy, view):
  match policy:
    case True{}:
      {==}
    case False{}:
      {==}

def Laws.no_policy_grant_no_capability(explicit, view):
  match explicit:
    case True{}:
      {==}
    case False{}:
      {==}

def Laws.legitimate_explicit_grant_is_preserved(view):
  {==}

def Laws.absent_does_not_claim_evidence(explicit, policy):
  {==}

def Laws.admitted_evidence_survives(explicit, policy, attempt):
  {==}

def Laws.retrieved_evidence_survives(explicit, policy, attempt):
  {==}

def Laws.replayed_evidence_survives(explicit, policy, attempt):
  {==}
