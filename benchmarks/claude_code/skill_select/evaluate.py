"""Offline recall/precision of skill-family exposure against pre-registered labels.

A miss = a needed family hidden (quality risk). Savings = description chars hidden
(each re-read every request; rewritten every cold session).
"""
import json, sys
from pathlib import Path
from z0int import claude_code_skills as cs

skills = cs.load()
rows = [json.loads(l) for l in (Path(__file__).parent / 'labels.jsonl').read_text().splitlines()]
total_chars = sum(len(d) for d in skills.values())
miss = over = 0; hidden_chars = []
for r in rows:
    exposed, hidden, why = cs.select(r['prompt'], skills)
    fams = {cs.family(n, skills[n]) for n in exposed}
    # Labels name skill-name prefixes; a need is met only if every skill with that prefix is exposed.
    need_skills = {n for n in skills if n.split('-')[0] in r['need']}
    need = {cs.family(n, skills[n]) for n in need_skills}
    missing = need_skills - set(exposed)
    extra = fams - need
    miss += bool(missing); over += len(extra)
    hidden_chars.append(sum(len(skills[n]) for n in hidden))
    if missing or extra or '-v' in sys.argv:
        print(('MISS ' if missing else 'over ') + f"{r['prompt'][:60]:<60} need={sorted(need)} got={sorted(fams)} why={why}")
print(f"\nprompts={len(rows)} family-recall-failures={miss} extra-family-exposures={over} "
      f"mean hidden={sum(hidden_chars)/len(rows)/total_chars:.0%} of {total_chars} desc chars (~{total_chars//4} tok)")
