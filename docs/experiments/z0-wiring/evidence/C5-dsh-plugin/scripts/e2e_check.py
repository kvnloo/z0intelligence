"""C5 e2e check: joined dsh rows in <z0home>/state/dsh, drops 0, no synthetic prompt/response text, shadow rows,
unchanged replay transcript (capture on vs off)."""
import json, sys, time
from pathlib import Path
from z0int.harness_id import turn_key, turn_key_from_alias

home, on_path, off_path = Path(sys.argv[1]), sys.argv[2], sys.argv[3]
on, off = (json.loads(Path(p).read_text().strip().splitlines()[-1]) for p in (on_path, off_path))
d = home / 'state' / 'dsh'
def rows(n):
    p = d / n
    return [json.loads(l) for l in p.read_text().splitlines()] if p.exists() else []
for _ in range(100):  # the opportunity build is a detached child
    if len(rows('opportunities.jsonl')) >= 3: break
    time.sleep(0.2)
opps, outs, shadow, lineage, drops = (rows(n) for n in ('opportunities.jsonl', 'outcomes.jsonl', 'shadow_decisions.jsonl', 'lineage.jsonl', 'drops.jsonl'))
fails = rows('failures.jsonl')
want = [turn_key_from_alias('dsh.lineage_turn_key', f'session-{s}:{n}', session_id=s) for s, n in (('e2e-s1', 1), ('e2e-s1', 2), ('e2e-s2', 1))]
checks = {
  'alias == canonical': want == [turn_key('dsh', s, f'session-{s}:{n}') for s, n in (('e2e-s1', 1), ('e2e-s1', 2), ('e2e-s2', 1))],
  'plugin keys == python keys': on['keys'] == want,
  '3 opportunity rows, schema': [r['schema'] for r in opps] == ['z0int.dsh.opportunity_record.v0'] * 3,
  '3 outcome rows, schema': [r['schema'] for r in outs] == ['z0int.dsh.turn_outcome.v0'] * 3,
  'joined on turn_key': sorted(r['turn_key'] for r in opps) == sorted(want) == sorted(r['turn_key'] for r in outs),
  'cohort interactive, model': all(r['cohort'] == 'interactive' and r['model_id'] == 'deepseek-flash' for r in opps),
  'work items: s1 two items, s2 one': len({r['work_item_id'] for r in opps}) == 3,
  'drops == 0': sum(int(r.get('count') or 0) for r in drops) == 0,
  'no misattribution/unsupported failures': not [f for f in fails if f['kind'] in ('misattribution', 'unsupported_schema', 'unsupported_harness')],
  'shadow: 3 ok rows, decision kind only': [r['status'] for r in shadow] == ['ok'] * 3 and all(r['decision'] == {'mode': 'shadow', 'executed': False, 'kind': 'PARENT_ONLY'} for r in shadow),
  'shadow requests == 3 to /v1/plan': on['shadow_requests'] == ['/v1/plan'] * 3,
  'lineage rows: 7 (6 root + 1 child)': len(lineage) == 7 and sum(r['role'] == 'subagent' for r in lineage) == 1,
  'no synthetic text in any dsh row': not any(w in (d / n).read_text() for n in [p.name for p in d.iterdir() if p.is_file()] for w in ('SYNTH', 'list the files', 'summarise them')),
  'counters clean': on['counters']['hook_spawn_failed'] == 0 and on['counters']['backend_unavailable'] == 0 and on['counters']['write_failed'] == 0,
  'replay transcript identical (capture on vs off)': on['transcript'] == off['transcript'],
  'capture off: no shadow requests': off['shadow_requests'] == [],
}
print(json.dumps({'counts': {'opportunities': len(opps), 'outcomes': len(outs), 'failures_by_kind': {k: sum(f['kind'] == k for f in fails) for k in sorted({f['kind'] for f in fails})}, 'shadow': len(shadow), 'lineage': len(lineage), 'drops': len(drops)}}, indent=1))
for k, v in checks.items(): print(('PASS ' if v else 'FAIL ') + k)
sys.exit(0 if all(checks.values()) else 1)
