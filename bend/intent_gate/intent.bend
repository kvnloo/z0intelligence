# Intent decision model. Inputs come from canonical AODL bindings and event receipts.
# Exact target identities are injective Nat encodings, not paths or fuzzy names.
# Parsing, timestamp normalization, provenance and sentence extraction remain adapter obligations.
import Base

# I0: read-only Q&A is allowed, but mutation and delegation require Kevin approval.
# The adapter must authenticate the approval, not trust an agent-supplied flag.
def authority_allows(mutating: Bool, approved: Bool) -> Bool:
  match mutating:
    case True{}:
      approved
    case False{}:
      True{}

type Status is Data:
  IOpen{}
  IDropped{}
  IDelegated{}
  IVerified{}

# I1: only a verified task, a reasoned drop, or a live delegation may close a turn.
def terminal(status: Status, reason: Bool, live: Bool, verified: Bool) -> Bool:
  match status:
    case IOpen{}:
      False{}
    case IDropped{}:
      reason
    case IDelegated{}:
      live
    case IVerified{}:
      verified

def turn_allows(ending: Bool, status: Status, reason: Bool, live: Bool, verified: Bool) -> Bool:
  match ending:
    case True{}:
      terminal(status, reason, live, verified)
    case False{}:
      True{}

# I2: a claim requires the verified update and a successful actual verifier run.
def proof_allows(updated: Bool, marked: Bool, passed: Bool, tool_ok: Bool) -> Bool:
  Bool.and(updated, Bool.and(marked, Bool.and(passed, tool_ok)))

# Execution starts strictly after the ask. Other timestamp boundaries allow equality.
def window_allows(asked: Nat, changed: Nat, start: Nat, finish: Nat, recorded: Nat) -> Bool:
  Bool.and(Nat.is_gt(start, asked), Bool.and(Bool.not(Nat.is_gt(changed, start)), Bool.and(Bool.not(Nat.is_gt(start, finish)), Bool.not(Nat.is_gt(finish, recorded)))))

# A delegated result must arrive before execution, not merely before the claim.
def delegate_allows(delegated: Bool, received: Bool, result: Nat, start: Nat) -> Bool:
  match delegated:
    case True{}:
      Bool.and(received, Bool.not(Nat.is_gt(result, start)))
    case False{}:
      True{}

# I3: task, acceptance verifier and actual result use the same exact identity.
def identity_allows(task: Nat, verifier: Nat, result: Nat, acceptance: Nat, used: Nat) -> Bool:
  Bool.and(Nat.is_eq(task, verifier), Bool.and(Nat.is_eq(task, result), Nat.is_eq(acceptance, used)))

# The four arguments are the four independent receipt checks above.
def claim_allows(proof: Bool, fresh: Bool, delegate: Bool, identity: Bool) -> Bool:
  Bool.and(proof, Bool.and(fresh, Bool.and(delegate, identity)))

# I4: every forwarded task ID must be carried. Extra carried IDs are harmless.
def mem(+x: Nat, ids: List<&2, Nat>) -> Bool:
  match ids:
    case Nil{}:
      False{}
    case h <> t:
      Bool.or(Nat.is_eq(h, x), mem(x, t))

def relay_allows(forwarded: List<&2, Nat>, +carried: List<&2, Nat>) -> Bool:
  match forwarded:
    case Nil{}:
      True{}
    case +h <> t:
      Bool.and(mem(h, carried), relay_allows(t, carried))
