"""Differential parity between work_item.py and bend/work_item_fsm/fsm.bend.

Seeded random tapes run through the Python `run` and, as a generated Bend
`main`, through Bend's normalizer; the printed final states must be equal.
Agreement is measured evidence, not proof that the two are equivalent.
"""
from __future__ import annotations

import os
import random
import shutil
import subprocess
import tempfile
from pathlib import Path

from .work_item import Actor, Event, Item, Phase, Receipt, Role, run, step

ROLE_CTOR = {
    Role.OWNER: "Owner", Role.OPUS: "Opus", Role.SOL: "Sol", Role.CHAT: "Chat",
    Role.FREE: "Free", Role.JEV: "Jev", Role.SLM: "Slm", Role.CI: "Ci",
}
PHASE_CTOR = {
    Phase.PROPOSED: "Proposed", Phase.CLAIMED: "Claimed", Phase.IMPLEMENTING: "Implementing",
    Phase.VERIFYING: "Verifying", Phase.DRAFT_PR: "DraftPR", Phase.INTEGRATED: "Integrated",
    Phase.DEPLOYED: "Deployed", Phase.ACCEPTED: "Accepted", Phase.MERGED: "Merged",
    Phase.BLOCKED: "Blocked", Phase.INTERRUPTED: "Interrupted", Phase.ABANDONED: "Abandoned",
}
FSM_DIR = Path(__file__).resolve().parents[3] / "bend" / "work_item_fsm"
BEND = os.environ.get("BEND", str(Path.home() / ".bend/bin/bend"))


def _nat(x: str | None) -> str:
    return f"{int(x) if x is not None else 0}n"


def _actor(a: Actor | None, ns: str) -> str:
    assert a is not None
    return f"{ns}Actor{{{_nat(a.id)}, {ns}{ROLE_CTOR[a.role]}{{}}}}"


def _receipt(r: Receipt | None, ns: str) -> str:
    assert r is not None
    return f"{ns}Receipt{{{_nat(r.sha)}, {'True' if r.passed else 'False'}{{}}}}"


def bend_event(e: Event, ns: str = "F.") -> str:
    k = e.kind
    if k in ("Claim", "Start", "Submit", "Accept", "Merge", "Block", "Unblock", "Resume", "Abandon"):
        return f"{ns}{k}{{{_actor(e.actor, ns)}}}"
    if k == "Push":
        return f"{ns}Push{{{_actor(e.actor, ns)}, {_nat(e.sha)}}}"
    if k == "Verify":
        return f"{ns}Verify{{{_actor(e.actor, ns)}, {_receipt(e.receipt, ns)}}}"
    if k == "Integrate":
        return f"{ns}Integrate{{{_actor(e.actor, ns)}, {_nat(e.sha)}, {_receipt(e.receipt, ns)}}}"
    if k == "Deploy":
        return f"{ns}Deploy{{{_actor(e.actor, ns)}, {_nat(e.sha)}}}"
    if k == "Observe":
        return f"{ns}Observe{{{_nat(e.sha)}}}"
    if k == "Interrupt":
        return f"{ns}Interrupt{{}}"
    raise ValueError(k)


def bend_item(s: Item, ns: str = "fsm.") -> str:
    """Render a Python state the way Bend prints the Bend state."""
    c = ns + PHASE_CTOR[s.phase]
    p = s.phase
    if p in (Phase.PROPOSED, Phase.ABANDONED):
        return c + "{}"
    au = _actor(s.au, ns)
    if p is Phase.CLAIMED:
        return f"{c}{{{au}}}"
    if p in (Phase.IMPLEMENTING, Phase.VERIFYING, Phase.DRAFT_PR, Phase.BLOCKED, Phase.INTERRUPTED):
        return f"{c}{{{au}, {_nat(s.hd)}}}"
    if p is Phase.INTEGRATED:
        return f"{c}{{{au}, {_nat(s.hd)}, {_nat(s.ig)}}}"
    if p is Phase.DEPLOYED:
        return f"{c}{{{au}, {_nat(s.hd)}, {_nat(s.ig)}, {_nat(s.dp)}}}"
    return f"{c}{{{au}, {_nat(s.hd)}, {_nat(s.ig)}, {_actor(s.ap, ns)}}}"


ROLES = list(Role)
KINDS_BY_PHASE = {  # weighted by repetition
    Phase.PROPOSED: ["Claim"] * 5 + ["Abandon"],
    Phase.CLAIMED: ["Start"] * 6 + ["Interrupt", "Abandon"],
    Phase.IMPLEMENTING: ["Push"] * 3 + ["Submit"] * 3 + ["Block", "Interrupt"],
    Phase.VERIFYING: ["Verify"] * 6 + ["Block", "Interrupt"],
    Phase.DRAFT_PR: ["Integrate"], Phase.INTEGRATED: ["Deploy"],
    Phase.DEPLOYED: ["Observe", "Accept", "Accept"], Phase.ACCEPTED: ["Merge"],
    Phase.BLOCKED: ["Unblock"] * 4 + ["Abandon"],
    Phase.INTERRUPTED: ["Resume"] * 4 + ["Abandon"],
}
ALL_KINDS = ["Claim", "Start", "Push", "Submit", "Verify", "Integrate", "Deploy", "Observe",
             "Accept", "Merge", "Block", "Unblock", "Interrupt", "Resume", "Abandon"]


LIKELY_ROLES = {
    "Claim": [Role.OPUS, Role.SOL], "Resume": [Role.OPUS, Role.SOL], "Verify": [Role.CI],
    "Integrate": [Role.CI, Role.OPUS], "Deploy": [Role.CI, Role.OPUS], "Accept": [Role.OWNER],
    "Merge": [Role.CI, Role.OWNER], "Unblock": [Role.OWNER, Role.OPUS], "Abandon": [Role.OWNER, Role.OPUS],
}


def random_event(rng: random.Random, s: Item) -> Event:
    """Mostly plausible moves (to reach deep phases), with a steady share of
    wrong roles, self-approvals, unbound receipts and drifted heads."""
    kinds = KINDS_BY_PHASE.get(s.phase, ALL_KINDS)
    k = rng.choice(kinds if rng.random() < 0.85 else ALL_KINDS)
    if k in ("Start", "Push", "Submit") and s.au is not None and rng.random() < 0.85:
        a = s.au
    elif s.au is not None and rng.random() < 0.15:
        a = s.au  # self-approval attempt
    else:
        roles = LIKELY_ROLES.get(k)
        role = rng.choice(roles) if roles and rng.random() < 0.75 else rng.choice(ROLES)
        a = Actor(str(rng.randint(1, 4)), role)
    rand_sha = str(rng.randint(1, 3))
    if k == "Deploy" or k == "Observe":
        sha = s.ig if s.ig is not None and rng.random() < 0.7 else rand_sha
    else:
        sha = rand_sha
    bound = {"Verify": s.hd, "Integrate": sha}.get(k)
    rsha = bound if bound is not None and rng.random() < 0.7 else rand_sha
    r = Receipt(rsha, rng.random() < 0.8)
    return Event(k, actor=None if k in ("Observe", "Interrupt") else a, sha=sha, receipt=r)


def random_tape(rng: random.Random, max_len: int = 14) -> list[Event]:
    s, tape = Item(), []
    for _ in range(rng.randint(1, max_len)):
        e = random_event(rng, s)
        tape.append(e)
        s = step(s, e)
    return tape


def bend_main(tapes: list[list[Event]]) -> str:
    body = ",\n   ".join(
        "F.run([" + ", ".join(bend_event(e) for e in t) + "], F.Proposed{})" for t in tapes
    )
    return ("import Base\nimport ./fsm.bend as F\n\n"
            "def main() -> List<&2, F.Item>:\n  [" + body + "]\n")


def run_parity(seed: int, n: int, max_len: int = 14) -> dict:
    rng = random.Random(seed)
    tapes = [random_tape(rng, max_len) for _ in range(n)]
    want = "[" + ", ".join(bend_item(run(t)) for t in tapes) + "]"
    with tempfile.TemporaryDirectory(dir=os.environ.get("TMPDIR")) as td:
        shutil.copy(FSM_DIR / "fsm.bend", Path(td) / "fsm.bend")
        (Path(td) / "tapes.bend").write_text(bend_main(tapes))
        p = subprocess.run([BEND, "tapes.bend"], cwd=td, capture_output=True, text=True, timeout=600)
    got = next((ln for ln in p.stdout.splitlines() if ln.startswith("[")), p.stdout + p.stderr)
    phases: dict[str, int] = {}
    for t in tapes:
        ph = run(t).phase.value
        phases[ph] = phases.get(ph, 0) + 1
    mism = []
    if got != want:
        g = got.strip("[]").split(", fsm.")
        w = want.strip("[]").split(", fsm.")
        mism = [i for i, (x, y) in enumerate(zip(g, w)) if x != y][:5]
    return {"seed": seed, "tapes": n, "events": sum(len(t) for t in tapes),
            "equal": got == want, "final_phases": phases, "first_mismatches": mism,
            "bend_exit": p.returncode}
