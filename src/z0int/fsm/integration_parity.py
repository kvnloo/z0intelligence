"""Compile unchanged Bend and compare every prefix of deterministic seeded tapes."""
from __future__ import annotations
import itertools
import json
import os
from pathlib import Path
import random
import subprocess
import tempfile
from .integration_owner import STATES, EVENTS, step, overdue, acquire

BEND = os.environ.get("BEND", str(Path.home() / ".bend/bin/bend"))
PROJECT = Path(__file__).resolve().parents[3] / "bend/integration_owner_fsm"


def run_parity(seed=3, n_tapes=400, stepper=step):
    rng = random.Random(seed)
    events = [{"tag": tag, **dict(zip(fields, vals))} for tag, fields in EVENTS.items()
              for vals in itertools.product((False, True), repeat=len(fields))]
    # Exhaustive single edges first, then random tapes from every state.
    tapes = [{"initial": s, "events": [e]} for s in STATES for e in events]
    for _ in range(n_tapes):
        tapes.append({"initial": rng.choice(STATES), "events": [rng.choice(events) for _ in range(40)]})
    deadlines = [[age, c, p] for age in [0, 86399, 86400, 86401, 1000000]
                 for c in (False, True) for p in (False, True)]
    leases = [[rng.choice([None, [1, 20], [2, 40]]), rng.randrange(4), rng.randrange(60), rng.randrange(60)]
              for _ in range(300)]
    payload = {"tapes": tapes, "deadlines": deadlines, "leases": leases}
    with tempfile.TemporaryDirectory(dir=os.environ.get("TMPDIR")) as td:
        td = Path(td)
        subprocess.run([BEND, str(PROJECT / "fsm.bend"), "-o", str(td / "model.mjs")], check=True, capture_output=True)
        (td / "run.mjs").write_text('''import B from './model.mjs';
let input=''; for await (const c of process.stdin) input+=c;
const p=JSON.parse(input); const cv=(x)=>typeof x==='boolean'?x:BigInt(x);
const tapes=p.tapes.map(t=>{let s={$:t.initial}; return t.events.map(e=>{
 const ev={$:e.tag}; for(const [k,v] of Object.entries(e)) if(k!=='tag') ev[k]=cv(v);
 s=B.step(s,ev); return s.$; });});
const deadlines=p.deadlines.map(x=>B.overdue(BigInt(x[0]),x[1],x[2]));
const leases=p.leases.map(([l,a,u,n])=>{const r=B.acquire(l?{$:'Held',owner:BigInt(l[0]),until:BigInt(l[1])}:{$:'Free'},BigInt(a),BigInt(u),BigInt(n));return r.$==='Free'?null:[Number(r.owner),Number(r.until)];});
console.log(JSON.stringify({tapes,deadlines,leases}));''')
        out = subprocess.run(["node", str(td / "run.mjs")], input=json.dumps(payload), text=True,
                             check=True, capture_output=True)
    actual = json.loads(out.stdout)
    prefixes = 0
    for tape, expected in zip(tapes, actual["tapes"], strict=True):
        s = tape["initial"]
        for index, (event, b) in enumerate(zip(tape["events"], expected, strict=True)):
            s = stepper(s, event)
            prefixes += 1
            if s != b:
                return {"equal": False, "seed": seed, "classification": "twin divergence or test-only sabotage; Bend is the reference",
                        "initial": tape["initial"], "tape": tape["events"][:index+1], "python": s, "bend": b}
    assert actual["deadlines"] == [overdue(*x) for x in deadlines]
    assert actual["leases"] == [list(r) if (r := acquire(tuple(x[0]) if x[0] else None, *x[1:])) else None for x in leases]
    return {"equal": True, "seed": seed, "tapes": len(tapes), "prefixes": prefixes,
            "deadline_cases": len(deadlines), "lease_cases": len(leases), "states": list(STATES)}


if __name__ == "__main__":
    print(json.dumps(run_parity(), indent=2))
