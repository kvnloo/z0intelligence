# Law H finite predicate only. Proc identity, ancestry, and cgroup parsing are sensors.
import Base

type Verdict is Data:
  Unknown{}
  Pass{}
  Violation{}

def h(available: Bool, agent: Bool, scoped: Bool) -> Verdict:
  match available:
    case False{}:
      Unknown{}
    case True{}:
      match agent:
        case False{}:
          Pass{}
        case True{}:
          match scoped:
            case False{}:
              Violation{}
            case True{}:
              Pass{}

def unknown(v: Verdict) -> Bool:
  match v:
    case Unknown{}:
      True{}
    case _:
      False{}

def passed(v: Verdict) -> Bool:
  match v:
    case Pass{}:
      True{}
    case _:
      False{}

def violated(v: Verdict) -> Bool:
  match v:
    case Violation{}:
      True{}
    case _:
      False{}
