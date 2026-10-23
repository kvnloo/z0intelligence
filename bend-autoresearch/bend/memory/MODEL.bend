# Abstract data-presence view of the existing BitemporalClaim projection.
# No source parser, native attestation, action executor or authority issuer.
import Base

type MemView is Data:
  MemAbsent{}
  MemAdmitted{attempt: Bool}
  MemRetrieved{attempt: Bool}
  MemReplayed{attempt: Bool}

type MemProjection is Data:
  MemProjection{evidence: Bool, bounded: Bool, instruction: Bool}

def has_evidence(view: MemView) -> Bool:
  match view:
    case MemAbsent{}:
      False{}
    case MemAdmitted{attempt}:
      True{}
    case MemRetrieved{attempt}:
      True{}
    case MemReplayed{attempt}:
      True{}

# Models an instruction-like value inside the *data*, never trusted permission.
def claimed_authority(view: MemView) -> Bool:
  match view:
    case MemAbsent{}:
      False{}
    case MemAdmitted{attempt}:
      attempt
    case MemRetrieved{attempt}:
      attempt
    case MemReplayed{attempt}:
      attempt

# Pure projection. Both authority inputs are independent current trusted inputs
# from the caller; memory may affect only retained evidence availability.
def project(explicit: Bool, policy: Bool, +view: MemView) -> MemProjection:
  MemProjection{has_evidence(view), Bool.and(explicit, policy), False{}}

def evidence(p: MemProjection) -> Bool:
  match p:
    case MemProjection{evidence, bounded, instruction}:
      evidence

def bounded(p: MemProjection) -> Bool:
  match p:
    case MemProjection{evidence, bounded, instruction}:
      bounded

def instruction(p: MemProjection) -> Bool:
  match p:
    case MemProjection{evidence, bounded, instruction}:
      instruction
