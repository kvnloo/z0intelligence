# Pure specification only. This is not wired into OMP and has no effects.
# The host must classify each state using trustworthy, current evidence. These
# constructors are attestations, not evidence verification: in particular a
# registered wake ID does not establish that its source will ever deliver.
# The host must preserve permission gates and explicit user control.
import Base

# The original goal and its acceptance criteria stay with the host. Complete
# means the host checked every criterion against that goal, not merely that
# an assistant answered or marked a task blocked.
type Goal is Data:
  Paused{}
  Stopped{}
  Complete{}
  Active{receipts: Receipts}

type Receipts is Data:
  Missing{}
  Loaded{next: Next}

# The host attests that work is authorized, or that a dependency is genuinely
# external. An exhausted plan is reconsidered against the original goal.
type Next is Data:
  AuthorizedWork{}
  ExhaustedPlan{}
  ExternalDependency{wake: Wake}

type Wake is Data:
  Registered{id: Nat}
  Unregistered{}

type Decision is Data:
  RespectPause{}
  RespectStop{}
  Finish{}
  # Load pstack and poteto before further goal work.
  LoadWorkflow{}
  Execute{}
  Replan{}
  Wait{id: Nat}
  Idle{}

def active(receipts: Receipts) -> Decision:
  match receipts:
    case Missing{}:
      LoadWorkflow{}
    case Loaded{next}:
      match next:
        case AuthorizedWork{}:
          Execute{}
        case ExhaustedPlan{}:
          Replan{}
        case ExternalDependency{wake}:
          match wake:
            case Registered{id}:
              Wait{id}
            case Unregistered{}:
              Replan{}

def decide(goal: Goal) -> Decision:
  match goal:
    case Paused{}:
      RespectPause{}
    case Stopped{}:
      RespectStop{}
    case Complete{}:
      Finish{}
    case Active{receipts}:
      active(receipts)
