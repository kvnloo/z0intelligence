"""Work-item lifecycle statechart: the Python twin of bend/work_item_fsm/fsm.bend.

This module is the single machine-readable source for the replay checker,
the Mermaid diagram and the Company OS JSON export. `bend/work_item_fsm`
proves laws about the Bend copy; tests/test_fsm_bend_parity.py replays the
same seeded tapes through both and requires identical final states. Keep
the two in step: change fsm.bend, re-run the parity test, re-verify.

Heads are opaque strings here (full git SHAs) and Nats in Bend; actors are
(id, role) pairs. Nothing in this module grants authorization or marks a
task verified_success. It is a model plus a checker.
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from enum import Enum
from typing import Any


class Role(str, Enum):
    """docs/roles-v0.md. Order matches fsm.bend's Role constructors."""

    OWNER = "owner"  # Kevin: taste, physical world, credentials, irreversible approvals
    OPUS = "opus"  # frontier lane: design, verification, hard code, orchestration
    SOL = "sol"  # Sol on pi/SoL-Pi or omp: bulk, well-specified coding
    CHAT = "chat"  # ChatGPT app, Grok, Muse: advice/specs, no sandbox, no origin actions
    FREE = "free"  # free models: scans, triage, summaries; never code
    JEV = "jev"  # chooser among prevalidated options; never writes
    SLM = "slm"  # shadow network; shadow-only until promoted
    CI = "ci"  # deterministic code: CI, tests, service-branch checks, replay


CODERS = frozenset({Role.OPUS, Role.SOL})
OPERATORS = frozenset({Role.CI, Role.OPUS})  # integrate, deploy
MERGERS = frozenset({Role.CI, Role.OWNER})  # merge to default after acceptance
STEWARDS = frozenset({Role.OWNER, Role.OPUS})  # unblock, abandon


class Avail(str, Enum):
    """Owner availability (self-report or derived signal; local only)."""

    DEEP_WORK = "available_deep_work"
    LOW_ENERGY = "low_energy"
    OFF = "off"


@dataclass(frozen=True)
class Actor:
    id: str
    role: Role


@dataclass(frozen=True)
class Receipt:
    sha: str
    passed: bool
    source: str = ""


class Phase(str, Enum):
    PROPOSED = "Proposed"
    CLAIMED = "Claimed"
    IMPLEMENTING = "Implementing"
    VERIFYING = "Verifying"
    DRAFT_PR = "DraftPR"
    INTEGRATED = "Integrated"
    DEPLOYED = "Deployed"
    ACCEPTED = "OwnerAccepted"
    MERGED = "Merged"
    BLOCKED = "Blocked"
    INTERRUPTED = "Interrupted"
    ABANDONED = "Abandoned"


TERMINAL = frozenset({Phase.MERGED, Phase.ABANDONED})


@dataclass(frozen=True)
class Item:
    """One work item. Fields beyond `phase` are meaningful only in phases
    whose Bend constructor carries them (au, hd, ig, dp, ap)."""

    phase: Phase = Phase.PROPOSED
    au: Actor | None = None
    hd: str | None = None
    ig: str | None = None
    dp: str | None = None
    ap: Actor | None = None


@dataclass(frozen=True)
class Event:
    kind: str  # Claim Start Push Submit Verify Integrate Deploy Observe Accept Merge Block Unblock Interrupt Resume Abandon
    actor: Actor | None = None
    sha: str | None = None  # Push: new head; Integrate: integration head; Deploy/Observe: served head
    receipt: Receipt | None = None
    at: str | None = None  # ISO time, for ordering and reports only
    evidence: dict[str, Any] = field(default_factory=dict)


EVENT_KINDS = (
    "Claim", "Start", "Push", "Submit", "Verify", "Integrate", "Deploy", "Observe",
    "Accept", "Merge", "Block", "Unblock", "Interrupt", "Resume", "Abandon",
)

# The statechart: (from, event) -> (to, allowed roles, required evidence).
# A human-readable restatement of fsm.bend; diagram and export read it.
TRANSITIONS: list[dict[str, Any]] = [
    {"from": "Proposed", "event": "Claim", "to": "Claimed", "roles": ["opus", "sol"],
     "evidence": "claimant is a coding lane (Opus or Sol)"},
    {"from": "Claimed", "event": "Start", "to": "Implementing", "roles": ["author"],
     "evidence": "the claimant starts"},
    {"from": "Implementing", "event": "Push", "to": "Implementing", "roles": ["author"],
     "evidence": "new head SHA"},
    {"from": "Implementing", "event": "Submit", "to": "Verifying", "roles": ["author"],
     "evidence": "head SHA submitted for checks"},
    {"from": "Verifying", "event": "Verify(pass, sha==head)", "to": "DraftPR", "roles": ["ci"],
     "evidence": "passing test receipt bound to the head SHA; verifier != author", "law": 3},
    {"from": "Verifying", "event": "Verify(fail or other sha)", "to": "Implementing", "roles": ["ci"],
     "evidence": "failing or unbound receipt sends work back"},
    {"from": "DraftPR", "event": "Integrate", "to": "Integrated", "roles": ["ci", "opus"],
     "evidence": "passing receipt bound to the integration-branch head; integrator != author", "law": 3},
    {"from": "Integrated", "event": "Deploy", "to": "Deployed", "roles": ["ci", "opus"],
     "evidence": "served head == integrated head", "law": 4},
    {"from": "Deployed", "event": "Observe(served != integrated)", "to": "Integrated", "roles": ["ci"],
     "evidence": "service observed on another head: deploy is void", "law": 4},
    {"from": "Deployed", "event": "Accept", "to": "OwnerAccepted", "roles": ["owner"],
     "evidence": "owner's hands-on acceptance; owner != author; queued while owner is off",
     "law": 7, "needs_owner": True},
    {"from": "OwnerAccepted", "event": "Merge", "to": "Merged", "roles": ["ci", "owner"],
     "evidence": "recorded owner acceptance", "law": 1},
    {"from": "Implementing", "event": "Block", "to": "Blocked", "roles": ["any"], "evidence": "blocker named"},
    {"from": "Verifying", "event": "Block", "to": "Blocked", "roles": ["any"], "evidence": "blocker named"},
    {"from": "Blocked", "event": "Unblock", "to": "Implementing", "roles": ["owner", "opus"],
     "evidence": "blocker cleared"},
    {"from": "Claimed", "event": "Interrupt", "to": "Interrupted", "roles": ["any"],
     "evidence": "worker died / timed out"},
    {"from": "Implementing", "event": "Interrupt", "to": "Interrupted", "roles": ["any"],
     "evidence": "worker died / timed out"},
    {"from": "Verifying", "event": "Interrupt", "to": "Interrupted", "roles": ["any"],
     "evidence": "worker died / timed out"},
    {"from": "Interrupted", "event": "Resume", "to": "Implementing", "roles": ["opus", "sol"],
     "evidence": "resumer keeps the pushed head and becomes author", "law": 5},
    {"from": "Interrupted", "event": "Abandon", "to": "Abandoned", "roles": ["owner", "opus"],
     "evidence": "explicit decision", "law": 5},
    {"from": "Blocked", "event": "Abandon", "to": "Abandoned", "roles": ["owner", "opus"],
     "evidence": "explicit decision"},
    {"from": "Claimed", "event": "Abandon", "to": "Abandoned", "roles": ["owner", "opus"],
     "evidence": "explicit decision"},
    {"from": "Proposed", "event": "Abandon", "to": "Abandoned", "roles": ["owner", "opus"],
     "evidence": "explicit decision"},
]

LAWS: list[dict[str, Any]] = [
    {"id": 1, "name": "merged_needs_owner_acceptance",
     "text": "A merged item was accepted by the owner, who is not its author.", "bend": ["merged_needs_owner_acceptance"]},
    {"id": 2, "name": "no_self_approval",
     "text": "Verification (CI), integration (CI/Opus) and acceptance (owner) are never passed by the item's author.",
     "bend": ["verify_gate_not_author", "integrate_gate_not_author", "accept_gate_owner_not_author"]},
    {"id": 3, "name": "receipt_bound_to_head",
     "text": "Verifying->DraftPR needs a passing receipt for the head SHA; Integrated needs one for the integration head.",
     "bend": ["draft_needs_bound_receipt", "integration_needs_bound_receipt"]},
    {"id": 4, "name": "deployed_equals_integrated",
     "text": "A deployed item's service serves exactly its integrated head; observing another head voids the deploy.",
     "bend": ["deployed_serves_integrated_head", "drift_undeploys"]},
    {"id": 5, "name": "interrupted_not_dropped",
     "text": "Interrupted work leaves only by Resume (head kept) or explicit Abandon; the replay checker flags any left sitting.",
     "bend": ["interrupted_can_resume", "interrupted_can_abandon", "interrupted_exits_keep_head"]},
    {"id": 6, "name": "only_coders_implement",
     "text": "Only Opus or Sol lanes implement; free models, Jev, SLMs and chat apps never do.",
     "bend": ["implementer_is_coder"]},
    {"id": 7, "name": "owner_is_a_queue_not_a_blocker",
     "text": "An owner action while Kevin is off is held in the batched needs-Kevin queue; every other event proceeds unchanged.",
     "bend": ["owner_off_holds", "queue_never_blocks", "board_frame"]},
]


def _other(a: Actor, au: Actor | None) -> bool:
    return au is None or a.id != au.id


def _binds(r: Receipt | None, sha: str | None) -> bool:
    return r is not None and r.passed and sha is not None and r.sha == sha


def step(s: Item, e: Event) -> Item:
    """One transition. A refused event returns `s` unchanged (fsm.bend `step`)."""
    a, p, k = e.actor, s.phase, e.kind
    if p is Phase.PROPOSED:
        if k == "Claim" and a and a.role in CODERS:
            return Item(Phase.CLAIMED, au=a)
        if k == "Abandon" and a and a.role in STEWARDS:
            return Item(Phase.ABANDONED)
        return s
    if p is Phase.CLAIMED:
        if k == "Start" and a and s.au and a.id == s.au.id:
            return Item(Phase.IMPLEMENTING, au=s.au, hd=None)
        if k == "Interrupt":
            return Item(Phase.INTERRUPTED, au=s.au, hd=None)
        if k == "Abandon" and a and a.role in STEWARDS:
            return Item(Phase.ABANDONED)
        return s
    if p is Phase.IMPLEMENTING:
        if k == "Push" and a and s.au and a.id == s.au.id:
            return replace(s, hd=e.sha)
        if k == "Submit" and a and s.au and a.id == s.au.id:
            return replace(s, phase=Phase.VERIFYING)
        if k == "Block":
            return replace(s, phase=Phase.BLOCKED)
        if k == "Interrupt":
            return replace(s, phase=Phase.INTERRUPTED)
        return s
    if p is Phase.VERIFYING:
        if k == "Verify" and a:
            if not (a.role is Role.CI and _other(a, s.au)):
                return s
            return replace(s, phase=Phase.DRAFT_PR if _binds(e.receipt, s.hd) else Phase.IMPLEMENTING)
        if k == "Block":
            return replace(s, phase=Phase.BLOCKED)
        if k == "Interrupt":
            return replace(s, phase=Phase.INTERRUPTED)
        return s
    if p is Phase.DRAFT_PR:
        if (k == "Integrate" and a and a.role in OPERATORS and _other(a, s.au)
                and _binds(e.receipt, e.sha)):
            return replace(s, phase=Phase.INTEGRATED, ig=e.sha)
        return s
    if p is Phase.INTEGRATED:
        if k == "Deploy" and a and a.role in OPERATORS and e.sha == s.ig:
            return replace(s, phase=Phase.DEPLOYED, dp=e.sha)
        return s
    if p is Phase.DEPLOYED:
        if k == "Observe":
            return s if e.sha == s.ig else replace(s, phase=Phase.INTEGRATED, dp=None)
        if k == "Accept" and a and a.role is Role.OWNER and _other(a, s.au):
            return replace(s, phase=Phase.ACCEPTED, dp=None, ap=a)
        return s
    if p is Phase.ACCEPTED:
        if k == "Merge" and a and a.role in MERGERS:
            return replace(s, phase=Phase.MERGED)
        return s
    if p is Phase.BLOCKED:
        if k == "Unblock" and a and a.role in STEWARDS:
            return replace(s, phase=Phase.IMPLEMENTING)
        if k == "Abandon" and a and a.role in STEWARDS:
            return Item(Phase.ABANDONED)
        return s
    if p is Phase.INTERRUPTED:
        if k == "Resume" and a and a.role in CODERS:
            return Item(Phase.IMPLEMENTING, au=a, hd=s.hd)
        if k == "Abandon" and a and a.role in STEWARDS:
            return Item(Phase.ABANDONED)
        return s
    return s  # Merged, Abandoned absorb


def owner_event(e: Event) -> bool:
    return e.actor is not None and e.actor.role is Role.OWNER and e.kind not in ("Observe", "Interrupt")


def needs_kevin(s: Item) -> bool:
    """In the batched needs-Kevin queue: the next move is the owner's acceptance."""
    return s.phase is Phase.DEPLOYED


def astep(av: Avail, s: Item, e: Event) -> Item:
    """fsm.bend `astep`: an owner action while he is off is held."""
    if av is Avail.OFF and owner_event(e):
        return s
    return step(s, e)


def run(events: list[Event], s: Item | None = None) -> Item:
    s = s or Item()
    for e in events:
        s = step(s, e)
    return s


def refused(s: Item, e: Event) -> bool:
    """The event was not a legal move from s (and was not a no-op Observe/Push)."""
    t = step(s, e)
    if t != s:
        return False
    if e.kind == "Observe" and s.phase is Phase.DEPLOYED:
        return False
    if e.kind == "Push" and s.phase is Phase.IMPLEMENTING and s.hd == e.sha:
        return False
    return True
