"""Check one C4a Hermes e2e home (written by e2e_hermes.sh). Prints a JSON verdict and exits 1 on any FAIL.

Checks: every `hermes chat -q` run exits 0; H1: the recorded model requests of each off run and each shadow run are
identical (canonical JSON, same order) and also identical to the first off run (A/A), with no plugin text
injected; shadow sessions have a joined opportunity + outcome on one turn_key, cohort interactive, model_id and
policy_revision set, the projection on the task repo (Hermes's task cwd); off sessions leave no
row (H6); the unavailable arm completes, writes z0int_unavailable rows and persisted drops that a fresh process
reports; no prompt or State Packet text in any row; exit drain bounded (shadow wall time - off wall time < 2 s);
the live homes were empty tmpfs masks inside the sandbox; every Hermes process closed (unlinked) its capture gate.
usage: e2e_check.py <home> <z0int python wrapper>
"""
import json
import re
import statistics
import subprocess
import sys
from pathlib import Path

H, ZPY = Path(sys.argv[1]), sys.argv[2]
STATE = H / 'z0home' / 'state' / 'hermes'
CANARIES = ('c4a-e2e-canary-7f3a', 'c4a-e2e-open-item', 'c4a-e2e-subject', 'c4a-e2e-branch')
checks, fail = {}, []


def check(name, ok, detail=None):
    checks[name] = {'ok': bool(ok), **({'detail': detail} if detail is not None else {})}
    if not ok:
        fail.append(name)


def rows(name):
    p = STATE / name
    return [json.loads(line) for line in p.read_text().splitlines() if line.strip()] if p.exists() else []


runs = [json.loads(line) for line in (H / 'runs.jsonl').read_text().splitlines()]
check('every_run_exit_0', all(r['rc'] == 0 for r in runs), [(r['arm'], r['idx'], r['rc']) for r in runs])
session = {}
for r in runs:
    m = re.search(r'session_id:\s*(\S+)', (H / 'runs' / f"{r['arm']}-{r['idx']}.err").read_text())
    session[(r['arm'], r['idx'])] = m.group(1) if m else None
check('every_run_has_a_session_id', all(session.values()))

# H1: off vs shadow request identity
requests = {}
for line in (H / 'stub-requests.jsonl').read_text().splitlines():
    r = json.loads(line)
    requests.setdefault(r['arm'], []).append(json.dumps([r['path'], r['body']], sort_keys=True))
off = sorted((k for k in requests if k.startswith('off-')), key=lambda k: int(k.split('-')[1]))
shadow = sorted((k for k in requests if k.startswith('shadow-')), key=lambda k: int(k.split('-')[1]))
ref = requests[off[0]]
pairs = [(o, s, requests[o] == requests[s]) for o, s in zip(off, shadow)]
check('h1_off_vs_shadow_identical_requests', all(p[2] for p in pairs) and len(pairs) == len(off) == len(shadow),
      {'pairs': len(pairs), 'identical': sum(p[2] for p in pairs), 'requests_per_run': len(ref)})
check('h1_all_runs_identical_to_first_off_run', all(requests[k] == ref for k in off + shadow),
      {'runs': len(off) + len(shadow)})
check('h1_no_plugin_context_in_requests', not any('z0int' in x or 'DecisionOpportunity' in x
                                                  for k in off + shadow for x in requests[k]))

# capture rows
opps, outs, events = rows('opportunities.jsonl'), rows('outcomes.jsonl'), rows('events.jsonl')
task_repo = str((H / 'task-repo').resolve())
joined = []
for (arm, idx), sid in session.items():
    if arm != 'shadow':
        continue
    o = [r for r in opps if r.get('session_id') == sid]
    t = [r for r in outs if r.get('session_id') == sid]
    joined.append({'idx': idx, 'opp': len(o), 'out': len(t),
                   'same_turn_key': bool(o and t and o[0]['turn_key'] == t[0]['turn_key']),
                   'repo': o[0]['repo'] if o else None, 'cohort': [r.get('cohort') for r in o + t],
                   'model': {r.get('model_id') for r in o + t}, 'policy': {r.get('policy_revision') for r in o + t},
                   'gate': o[0]['gate'] if o else None, 'packet_text': o[0].get('packet_text') if o else None,
                   'events': sum(1 for e in events if e.get('session_id') == sid)})
check('shadow_runs_have_joined_opportunity_and_outcome',
      joined and all(j['opp'] == 1 and j['out'] == 1 and j['same_turn_key'] for j in joined),
      [{k: j[k] for k in ('idx', 'opp', 'out', 'same_turn_key', 'events')} for j in joined])
check('projection_uses_the_hermes_task_cwd', all(j['repo'] == task_repo for j in joined),
      sorted({str(j['repo']) for j in joined}))
check('cohort_interactive_model_and_policy_on_rows',
      all(set(j['cohort']) == {'interactive'} and j['model'] == {'c4a-stub'}
          and len(j['policy']) == 1 and 'unknown' not in j['policy'] for j in joined),
      {'model': sorted({m for j in joined for m in j['model']}), 'policy': sorted({p for j in joined for p in j['policy']})})
check('packet_text_redacted_by_default', all(j['packet_text'] == 'redacted' for j in joined))
off_sids = {sid for (arm, _), sid in session.items() if arm == 'off'}
leaked = [r for name in ('opportunities.jsonl', 'outcomes.jsonl', 'events.jsonl', 'failures.jsonl')
          for r in rows(name) if r.get('session_id') in off_sids]
check('h6_mode_off_runs_leave_no_rows', not leaked, len(leaked))

# fail-open arm
unavailable_sid = session.get(('unavailable', 1))
failures = rows('failures.jsonl')
unavail = [r for r in failures if r.get('kind') == 'z0int_unavailable']
drops = rows('drops.jsonl')
fresh = subprocess.run([ZPY, '-m', 'z0int.cli', 'hermes', 'decisions'], capture_output=True, text=True,
                       env={'Z0INT_HOME': str(H / 'z0home'), 'PATH': '/usr/bin:/bin'})
reported = json.loads(fresh.stdout).get('rows_dropped') if fresh.returncode == 0 else None
check('unavailable_arm_completes_and_is_counted',
      unavail and sum(r['detail']['jobs'] for r in unavail) == sum(d['count'] for d in drops
                                                                   if d['reason'] == 'z0int_unavailable'),
      {'z0int_unavailable_rows': len(unavail), 'jobs': sum(r['detail']['jobs'] for r in unavail)})
check('drops_persisted_and_fresh_process_reports_them', reported == sum(d['count'] for d in drops) and reported > 0,
      {'persisted': sum(d['count'] for d in drops), 'fresh_process_rows_dropped': reported,
       'by_reason': {r: sum(d['count'] for d in drops if d['reason'] == r) for r in {d['reason'] for d in drops}}})
check('unavailable_session_wrote_no_capture_rows', not [r for r in opps + outs if r.get('session_id') == unavailable_sid])

# privacy and bounded exit
text = '\n'.join(p.read_text(errors='replace') for p in (H / 'z0home').rglob('*') if p.is_file())
check('no_prompt_or_packet_text_in_rows', not [c for c in CANARIES if c in text])
wall = {arm: [r['wall_ms'] for r in runs if r['arm'] == arm] for arm in ('off', 'shadow', 'unavailable')}
delta = statistics.median(wall['shadow']) - statistics.median(wall['off'])
check('exit_drain_bounded_shadow_minus_off_median_under_2s', delta < 2000,
      {'off_ms': wall['off'], 'shadow_ms': wall['shadow'], 'unavailable_ms': wall['unavailable'],
       'median_delta_ms': delta})
masks = (H / 'masks-inside.txt').read_text()
check('live_homes_masked_inside_the_sandbox', 'hermes-home entries=0' in masks and 'z0int entries=0' in masks,
      masks.strip())
gates = list((H / 'z0home' / 'runtime').rglob('*.gate'))
check('every_hermes_process_closed_its_capture_gate', not gates, len(gates))
print(json.dumps({'verdict': 'FAIL' if fail else 'PASS', 'failed': fail, 'checks': checks}, indent=1))
sys.exit(1 if fail else 0)
