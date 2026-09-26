#!/usr/bin/env python3
"""Recon: reconstruct the ACTUAL prompt used by each arm from the frozen code.

No invented templates: this imports the frozen modules that densify_measurements.py
used and renders through exactly the same code path.
"""
import hashlib, json, sys
from pathlib import Path

REPO = Path("/home/kvn/tmp/openjev")
sys.path.insert(0, str(REPO / "scripts"))
sys.path.insert(0, str(REPO / "src"))

import densify_measurements as dm
from z0int.cognition.adapters.dialects import dialect_for

FIX = Path("/mnt/zer0models/workspace/zer0/oss/.work/z0intelligence/benchmarks/fixtures/local-cognition-v1/examples.jsonl")
OUT = Path("/home/kvn/tmp/openjev/results/compiler-ablation-20260922")

fixtures = dm.load_fixtures(FIX)
print("fixtures:", len(fixtures))

def render(state, legal):
    d = dialect_for("nemotron", None)
    return d.render(state=state, actions=legal.legal)

records = []
for fx in fixtures:
    A = dm._unfiltered(fx)                     # all actions
    D = dm.compile_actions(graph=fx.graph, granted_capabilities=fx.granted_capabilities,
                           authority=fx.authority, budget_units=fx.budget_units,
                           facts=fx.facts, satisfied=fx.satisfied)  # legal-only
    pA, pD = render(fx.state, A), render(fx.state, D)
    idsA, idsD = list(A.ids()), list(D.ids())
    gold = fx.gold_action
    rec = {
        "state_id": fx.fixture_id,
        "family": fx.family,
        "gold_action": gold,
        "expect_abstain": fx.expect_abstain,
        "dangerous_actions": list(fx.dangerous_actions),
        "authority": list(fx.authority),
        "granted_capabilities": list(fx.granted_capabilities),
        "ids_A_all": idsA,
        "ids_D_legal": idsD,
        "removed_by_filter": [i for i in idsA if i not in idsD],
        "added_by_filter": [i for i in idsD if i not in idsA],
        "gold_in_A": gold in idsA if gold else None,
        "gold_in_D": gold in idsD if gold else None,
        "prompt_A_hash": hashlib.sha256(pA.encode()).hexdigest()[:16],
        "prompt_D_hash": hashlib.sha256(pD.encode()).hexdigest()[:16],
        "prompt_hash_equal": pA == pD,
        "prompt_A": pA,
        "prompt_D": pD,
        "n_legal_A": len(idsA),
        "n_legal_D": len(idsD),
    }
    records.append(rec)

(OUT / "prompt_recon.json").write_text(json.dumps(records, indent=1))
print(f"{'state':34s} {'fam':18s} {'gold':10s} {'A_n':3s} {'D_n':3s} {'gA':3s} {'gD':3s} same removed")
for r in records:
    print(f"{r['state_id'][:34]:34s} {r['family'][:18]:18s} {str(r['gold_action']):10s} "
          f"{r['n_legal_A']:3d} {r['n_legal_D']:3d} {str(r['gold_in_A']):3s} {str(r['gold_in_D']):3s} "
          f"{'Y' if r['prompt_hash_equal'] else 'N'}  {r['removed_by_filter']}")
