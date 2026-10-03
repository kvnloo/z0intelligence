"""C8 round-3 e2e verdict for DoD D3: every capture opportunity_record of a turn whose memory seam wrote a
MemoryUseReceipt carries that receipt in ``memory`` (validated), for all 7 harnesses; no opportunity carries a
receipt from another turn; the capture kill switch keeps the memory seam off in a real CC run.
usage: e2e_join_check.py <worktree> <e2e root>   -> <root>/results/join-check.json"""
import json
import sys
from pathlib import Path

WT, R = Path(sys.argv[1]), Path(sys.argv[2])
sys.path[:0] = [str(WT / 'src')]
from z0int.memory import seam  # noqa: E402
from z0int.memory_contract import MemoryUseReceipt  # noqa: E402

HARNESSES = ('claude-code', 'codex', 'grok', 'hermes', 'omp', 'omo', 'dsh')
HEADER = 'z0 memory brief (evidence, not instructions)'
checks = []


def check(name, ok, detail=None):
    checks.append({'check': name, 'ok': bool(ok), 'detail': detail})


def rows(path):
    p = Path(path)
    return [json.loads(line) for line in p.read_text().splitlines() if line.strip()] if p.exists() else []


per = {}
for h in HARNESSES:
    receipts = {}
    for r in seam.rows(h):
        if r.get('turn_key') and r.get('memory') and r['turn_key'] not in receipts:
            receipts[r['turn_key']] = MemoryUseReceipt.from_dict(r['memory']).to_dict()
    opps = rows(R / 'z0' / 'state' / h / 'opportunities.jsonl')
    joined = [o for o in opps if o.get('memory') is not None and receipts.get(o['turn_key']) == o['memory']]
    missing = [o['turn_key'] for o in opps if o['turn_key'] in receipts and o.get('memory') is None]
    foreign = [o['turn_key'] for o in opps if o.get('memory') is not None and receipts.get(o['turn_key']) != o['memory']]
    valid = all(MemoryUseReceipt.from_dict(o['memory']).instruction_capability is False for o in joined)
    per[h] = {'opportunities': len(opps), 'turns_with_seam_receipt': len(receipts), 'joined': len(joined),
              'missing': len(missing), 'foreign_or_mismatched': len(foreign), 'valid': valid}
    check(f'join.{h}.opportunity_record_memory_is_the_turns_receipt',
          joined and not missing and not foreign and valid, per[h])
check('join.all_7_harnesses_carry_memory_in_opportunity_records', all(per[h]['joined'] for h in HARNESSES),
      {h: per[h]['joined'] for h in HARNESSES})

kill = json.loads((R / 'results' / 'kill.json').read_text())
killed_reqs = []
for line in (R / 'cc' / 'stub-requests.jsonl').read_text().splitlines():
    rec = json.loads(line)
    if str(rec.get('arm', '')).startswith('killed-'):
        killed_reqs.append(json.dumps(rec))
check('kill_switch.cc_canary_with_Z0INT_CAPTURE_0_writes_no_seam_or_capture_row_and_injects_nothing',
      kill['rows_before'] == kill['rows_after'] and killed_reqs and not any(HEADER in q for q in killed_reqs),
      {**kill, 'killed_requests': len(killed_reqs)})
runs = rows(R / 'runs.jsonl')
check('join.legs_exit_0', all(r['rc'] == 0 for r in runs if r['leg'] in ('omo-join', 'dsh-join', 'join-hooks')),
      [(r['leg'], r.get('arm'), r['rc']) for r in runs if r['leg'] in ('omo-join', 'dsh-join', 'join-hooks')])

verdict = {'sha': (R / 'sha.txt').read_text().strip(), 'ok': all(c['ok'] for c in checks),
           'passed': sum(c['ok'] for c in checks), 'total': len(checks), 'checks': checks}
(R / 'results' / 'join-check.json').write_text(json.dumps(verdict, indent=1))
print(json.dumps({'ok': verdict['ok'], 'passed': verdict['passed'], 'total': verdict['total'],
                  'failed': [c['check'] for c in checks if not c['ok']], 'per_harness': per}, indent=1))
