"""Loop 3 pure Python twin. Facts are evidence, never authorization."""
from __future__ import annotations

STATES = ("Branch", "DraftPR", "Queued", "OnIntegrationBranch", "Deployed",
          "OwnerAccepted", "Merged", "Parked", "Conflicted")
EVENTS = {"Draft": (), "Queue": (), "Integrate": ("lease", "stack", "independent"),
          "Deploy": ("equal",), "Accept": ("owner",), "Merge": (),
          "Park": ("reason", "future"), "Conflict": (), "Resume": (), "Observe": ("equal",), "Expire": ("expired",)}


def step(s: str, event: dict) -> str:
    if s not in STATES or event.get("tag") not in EVENTS:
        raise ValueError("unknown state/event")
    tag = event["tag"]
    fields = EVENTS[tag]
    if set(event) != {"tag", *fields} or any(type(event[k]) is not bool for k in fields):
        raise ValueError("event fields must match the ABI, with boolean evidence")
    if s == "Merged":
        return s
    if tag == "Park":
        return "Parked" if event["reason"] and event["future"] else s
    if tag == "Conflict":
        return "Conflicted"
    if tag == "Observe":
        return "OnIntegrationBranch" if s in ("Deployed", "OwnerAccepted") and not event["equal"] else s
    if (s, tag) == ("Branch", "Draft"):
        return "DraftPR"
    if (s, tag) == ("DraftPR", "Queue"):
        return "Queued"
    if (s, tag) == ("Queued", "Integrate") and all(event[k] for k in fields):
        return "OnIntegrationBranch"
    if (s, tag) == ("OnIntegrationBranch", "Deploy") and event["equal"]:
        return "Deployed"
    if (s, tag) == ("Deployed", "Accept") and event["owner"]:
        return "OwnerAccepted"
    if (s, tag) == ("OwnerAccepted", "Merge"):
        return "Merged"
    if s == "Parked" and tag == "Expire" and event["expired"]:
        return "Queued"
    if s in ("Parked", "Conflicted") and tag == "Resume":
        return "Queued"
    return s


def overdue(age: int, contained: bool, parked: bool) -> bool:
    if age < 0:
        raise ValueError("age must be a Nat")
    return age >= 86400 and not (contained or parked)


def acquire(lease: tuple[int, int] | None, actor: int, until: int, now: int):
    if min(actor, until, now) < 0 or (lease is not None and min(lease) < 0):
        raise ValueError("lease ids and times must be Nats")
    if until > now and (lease is None or now >= lease[1]):
        return actor, until
    return lease
