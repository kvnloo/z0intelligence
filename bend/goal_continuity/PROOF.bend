# Proofs of the model's decisions, not host evidence or actual progress.
import Base
import ./contract.bend as C
import ./LAWS.bend as Laws

def Laws.unfinished_not_idle(r):
  match r:
    case C.Missing{}:
      Unit{}
    case C.Loaded{next}:
      match next:
        case C.AuthorizedWork{}:
          Unit{}
        case C.ExhaustedPlan{}:
          Unit{}
        case C.ExternalDependency{wake}:
          match wake:
            case C.Registered{id}:
              Unit{}
            case C.Unregistered{}:
              Unit{}

def Laws.pause_authoritative():
  {==}

def Laws.stop_authoritative():
  {==}

def Laws.all_checks_complete():
  {==}

def Laws.missing_receipts_load():
  {==}

def Laws.authorized_work_executes():
  {==}

def Laws.exhausted_plan_replans():
  {==}

def Laws.registered_dependency_waits(id):
  {==}

def Laws.unregistered_dependency_replans():
  {==}
