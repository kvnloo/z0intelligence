# Law G finite obligations only. Sources, timestamps, containment and archive
# identity are adapter responsibilities. This model is not an actor or authority.
import Base

def eligible(+expired: Bool, +allowlisted: Bool, +known_nonlive: Bool, +no_symlink: Bool, +archive_ready: Bool) -> Bool:
  Bool.and(expired, Bool.and(allowlisted, Bool.and(known_nonlive, Bool.and(no_symlink, archive_ready))))
