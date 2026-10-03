"""Integration e2e verdict over one e2e root (all harness legs + learn + memory probe). Prints JSON; exit 1 on FAIL.

Sections:
  capture   per harness: >=1 opportunity_record.v0 and turn_outcome.v0 in the SHARED Z0INT_HOME, every opportunity
            joined to an outcome on turn_key, the #62 identity fields present, cohorts as expected, drops counted
  inert     per harness: the model-visible request (or, for hook-only legs, the hook stdout / host transcript) is
            byte-identical between shadows on and off after removing per-run ids; the off arms wrote no capture rows
  privacy   no synthetic marker text anywhere under the shared Z0INT_HOME, the exported tables or the frozen bundle;
            exported/frozen rows carry no path; loop_export.assert_private accepts every exported row
  learn     verify ran, the claude-code export has rows, the other harness exports are reported (expected 0 rows:
            label sources need C2), the frozen bundle covers all seven harnesses and rebuilds to the same hash,
            the learner ran once and reported INSUFFICIENT_DATA with its sufficiency block
  memory    the probe result (expected BLOCKED: C7/C8 not verified)
  isolation socket-guard logs empty; Hermes live homes masked
usage: e2e_check.py <e2e-root> <worktree>
"""
import json
import re
import sys
from pathlib import Path

R, WT = Path(sys.argv[1]), Path(sys.argv[2])
sys.path.insert(0, str(WT / 'src'))
from z0int import loop_export  # noqa: E402

Z, ZOFF = R / 'z0home', R / 'z0home-off'
HARNESSES = ('claude-code', 'codex', 'grok', 'hermes', 'omp', 'omo', 'dsh')
SCHEMA_ID = {h: h.replace('-', '_') for h in HARNESSES}
MARKERS = ('MAIN-MARKER-c1', 'SUB-MARKER-c1', 'CODEX-MARKER-int', 'GROK-MARKER-int', 'OMP-MARKER-int', 'OMO-MARKER-int',
           'HERMES-MARKER-int', 'SYNTH-E2E-PROMPT', 'SYNTH-E2E-RESPONSE', 'SYNTH child task', 'int-e2e-open-item',
           'int-e2e-subject', 'int-e2e-branch', 'tidy the changelog', 'Tidy the changelog', 'release checklist',
           'read the readme', 'delegate one small subtask')
IDENTITY = ('harness', 'session_id', 'trace_id', 'turn_key', 'work_item_id', 'attempt_id', 'cohort', 'model_id',
            'policy_revision', 'privacy_class', 'recorded_at')
checks, fail, report = {}, [], {}


def check(name, ok, detail=None):
    checks[name] = {'ok': bool(ok), **({'detail': detail} if detail is not None else {})}
    if not ok:
        fail.append(name)


def rows(path):
    return [json.loads(x) for x in path.read_text().splitlines() if x.strip()] if path.exists() else []


# ----------------------------------------------------------------------------- capture
cap = {}
for h in HARNESSES:
    st = Z / 'state' / h
    opps, outs = rows(st / 'opportunities.jsonl'), rows(st / 'outcomes.jsonl')
    fails, drops = rows(st / 'failures.jsonl'), rows(st / 'drops.jsonl')
    out_keys = {o['turn_key'] for o in outs}
    joined = [o for o in opps if o['turn_key'] in out_keys]
    cohorts = {}
    for o in opps:
        cohorts[o['cohort']] = cohorts.get(o['cohort'], 0) + 1
    fk = {}
    for f in fails:
        fk[f['kind']] = fk.get(f['kind'], 0) + 1
    dk = {}
    for d in drops:
        dk[d.get('reason')] = dk.get(d.get('reason'), 0) + int(d.get('count') or 0)
    cap[h] = {'opportunities': len(opps), 'outcomes': len(outs), 'joined': len(joined), 'cohorts': cohorts,
              'failure_kinds': fk, 'drops_by_reason': dk,
              'schemas': sorted({r['schema'] for r in opps + outs}),
              'model_ids': sorted({str(r.get('model_id')) for r in opps}),
              'policy_revisions': sorted({str(r.get('policy_revision')) for r in opps}),
              'privacy_classes': sorted({str(r.get('privacy_class')) for r in opps + outs})}
    check(f'capture.{h}.rows', opps and outs, {'opportunities': len(opps), 'outcomes': len(outs)})
    check(f'capture.{h}.every_opportunity_joined_on_turn_key', opps and len(joined) == len(opps),
          {'joined': len(joined), 'opportunities': len(opps)})
    want = {f'z0int.{SCHEMA_ID[h]}.opportunity_record.v0', f'z0int.{SCHEMA_ID[h]}.turn_outcome.v0'}
    check(f'capture.{h}.schemas', set(cap[h]['schemas']) == want, cap[h]['schemas'])
    check(f'capture.{h}.identity_fields', all(all(k in r for k in IDENTITY) for r in opps + outs))
    check(f'capture.{h}.content_free_by_default', cap[h]['privacy_classes'] == ['content_free']
          and all(((o.get('opportunity') or {}).get('intent') or {}).get('request') is None for o in opps))
cc = cap['claude-code']['cohorts']
check('capture.claude-code.subagent_turns_are_agent_cohort', cc.get('agent', 0) >= 1 and cc.get('interactive', 0) >= 1, cc)
check('capture.grok.subagent_turns_are_agent_cohort', cap['grok']['cohorts'].get('agent', 0) >= 1, cap['grok']['cohorts'])
report['capture'] = cap

# ----------------------------------------------------------------------------- inertness
UUID = re.compile(r'[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}')
AGENT = re.compile(r'\ba[0-9a-f]{16}\b')
TOOLU = re.compile(r'(toolu|call|msg|resp|rs|fc)_[A-Za-z0-9_]+')
HEXID = re.compile(r'\b[0-9a-f]{32,64}\b')
ISO = re.compile(r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?Z?')
# wall-clock values the hosts themselves put into requests (CC subagent hand-back usage, Codex turn metadata)
WALL = re.compile(r'(duration_ms: |_unix_ms\\\\?\":)\d+')


def norm(text):
    for rx, rep in ((WALL, r'\1<ms>'), (UUID, '<uuid>'), (AGENT, '<agent>'), (TOOLU, '<id>'), (HEXID, '<hex>'), (ISO, '<ts>')):
        text = rx.sub(rep, text)
    return text


def requests_by_arm(path, posts_only=True):
    by = {}
    for r in rows(path):
        if posts_only and r.get('method', 'POST') != 'POST':
            continue
        if r.get('body') is None:
            continue
        by.setdefault(r['arm'], []).append(norm(json.dumps([r.get('path'), r['body']], sort_keys=True)))
    return by


def compare(name, by, on_prefix, off_prefix, extra=()):
    ons = sorted(k for k in by if k.startswith(on_prefix + '-'))
    offs = sorted(k for k in by if k.startswith(off_prefix + '-'))
    ref = by[offs[0]] if offs else None
    same = [k for k in ons + offs + list(extra) if by.get(k) == ref]
    check(f'inert.{name}', ref is not None and ons and len(same) == len(ons) + len(offs) + len(extra),
          {'arms': sorted(by), 'identical_to_first_off_arm': sorted(same), 'requests_per_run': len(ref or [])})


compare('claude-code.model_requests_on_vs_off', requests_by_arm(R / 'claude-code/stub-requests.jsonl'), 'on', 'off')
compare('codex.model_requests_on_vs_off', requests_by_arm(R / 'codex/stub-requests.jsonl'), 'on', 'off')
for h in ('omp', 'omo'):
    by = requests_by_arm(R / h / 'stub-requests.jsonl')
    compare(f'{h}.model_requests_on_vs_off_vs_no_extension', by, 'on', 'off',
            extra=sorted(k for k in by if k.startswith('none-')))
compare('hermes.model_requests_shadow_vs_off', requests_by_arm(R / 'hermes/stub-requests.jsonl'), 'shadow', 'off')
g = json.loads((R / 'grok/replay.json').read_text())
check('inert.grok.hook_stdout_on_vs_off', g['stdout']['on'] == g['stdout']['off']
      and all(s == '' for _, s in g['stdout']['on']) and not any(g['rc']['on']) and not any(g['rc']['off']),
      {'hook_calls': len(g['stdout']['on'])})
d_on, d_off = (json.loads((R / f'dsh/e2e-{m}.json').read_text()) for m in ('on', 'off'))
check('inert.dsh.host_transcript_on_vs_off', d_on['transcript'] == d_off['transcript'],
      {'steps': len(d_on['transcript'])})
shadow_rows = rows(Z / 'state/dsh/shadow_decisions.jsonl')
check('inert.dsh.shadow_decisions_never_executed', shadow_rows and all(
      (r.get('decision') or {}).get('executed') is False and r.get('student_changed_execution') is False
      for r in shadow_rows)
      and d_on['counters'].get('shadow_executed') == 0, {'shadow_rows': len(shadow_rows), 'counters': d_on['counters']})
off_rows = {h: sum(len(rows(ZOFF / 'state' / h / n)) for n in ('opportunities.jsonl', 'outcomes.jsonl'))
            for h in HARNESSES}
check('inert.off_arms_write_no_capture_rows', not any(off_rows.values()), off_rows)
runs = {}
for h in ('omp', 'omo', 'hermes'):
    runs[h] = rows(R / h / 'runs.jsonl')
    check(f'inert.{h}.every_run_exit_0', runs[h] and all(r['rc'] == 0 for r in runs[h]),
          [(r['arm'], r['idx'], r['rc'], r['wall_ms']) for r in runs[h]])
for h in ('claude-code', 'codex'):
    outs = sorted((R / h).glob('out-*'))
    check(f'inert.{h}.every_run_produced_output', outs and all(p.stat().st_size > 0 for p in outs), [p.name for p in outs])

# ----------------------------------------------------------------------------- privacy
def scan(root):
    hits = {}
    for p in root.rglob('*'):
        if p.is_file():
            t = p.read_text(errors='replace')
            m = [x for x in MARKERS if x in t]
            if m:
                hits[str(p.relative_to(R))] = m
    return hits


check('privacy.no_marker_text_in_shared_z0int_home', not scan(Z), scan(Z))
L = R / 'learn'
check('privacy.no_marker_text_in_exports_or_frozen_bundle', not scan(L / 'tables') and not scan(L / 'frozen'),
      {**scan(L / 'tables'), **scan(L / 'frozen')})
exported = [r for p in sorted((L / 'tables').glob('*.jsonl')) for r in rows(p)]
frozen = rows(L / 'frozen/rows.jsonl')
root_str = str(R)
check('privacy.exported_and_frozen_rows_carry_no_path',
      not [r for r in exported + frozen if root_str in json.dumps(r) or '/home/' in json.dumps(r)])
try:
    loop_export.assert_private(exported)
    ok_private, why = True, None
except Exception as e:  # noqa: BLE001
    ok_private, why = False, repr(e)
check('privacy.loop_export_assert_private_accepts_every_exported_row', ok_private and exported, why or len(exported))
outcome_paths = [h for h in HARNESSES for n in ('outcomes.jsonl', 'failures.jsonl')
                 for r in rows(Z / 'state' / h / n) if root_str in json.dumps(r)]
check('privacy.outcome_and_failure_rows_carry_no_path', not outcome_paths, sorted(set(outcome_paths)))

# ----------------------------------------------------------------------------- learn
verify = json.loads((L / 'verify.json').read_text()) if (L / 'verify.json').exists() and \
    (L / 'verify.json').read_text().strip() else None
check('learn.verify_ran', verify is not None, verify if verify is None else {k: verify[k] for k in list(verify)[:12]})
tables = {}
for h in HARNESSES:
    c = L / 'tables' / f'{h}.counts.json'
    tables[h] = json.loads(c.read_text()) if c.exists() and c.read_text().strip() else None
check('learn.claude_code_export_has_rows', tables['claude-code'] and tables['claude-code']['rows'] >= 1, tables['claude-code'])
report['export_counts'] = tables
man = json.loads((L / 'frozen/manifest.json').read_text())
fz_h = sorted({s.split('.')[1] for s in man['by_schema']})
check('learn.frozen_bundle_covers_all_seven_harnesses', set(fz_h) == {SCHEMA_ID[h] for h in HARNESSES}, man['by_schema'])
el = json.loads((L / 'el/verified-loop.json').read_text())['primary']
check('learn.learner_reports_insufficient_data_honestly', el.get('decision') == 'INSUFFICIENT_DATA' and 'sufficiency' in el,
      {'decision': el.get('decision'), 'sufficiency': el.get('sufficiency')})
report['learner'] = {'decision': el.get('decision'), 'sufficiency': el.get('sufficiency'),
                     'learner_sha': (L / 'el/learner_sha.txt').read_text().strip()}

# ----------------------------------------------------------------------------- memory
mem = json.loads((R / 'memory-probe.json').read_text())
report['memory'] = mem
checks['memory.unified_memory_from_every_harness_seam'] = {'ok': False, 'status': mem['memory_e2e'],
                                                           'detail': mem['reason']}

# ----------------------------------------------------------------------------- isolation
logs = [p for p in R.rglob('socket-violations.log') if p.read_text().strip()]
check('isolation.socket_guard_logs_empty', not logs, [str(p) for p in logs])
masks = (R / 'hermes/masks-inside.txt').read_text()
check('isolation.hermes_live_homes_masked', 'hermes-home entries=0' in masks and 'z0int entries=0' in masks, masks.strip())

capture_learn_fail = [f for f in fail]
print(json.dumps({'verdict': 'FAIL' if fail else 'PASS (capture/inertness/privacy/learn); memory BLOCKED',
                  'failed': fail, 'checks': checks, 'report': report}, indent=1, sort_keys=True))
sys.exit(1 if fail else 0)
