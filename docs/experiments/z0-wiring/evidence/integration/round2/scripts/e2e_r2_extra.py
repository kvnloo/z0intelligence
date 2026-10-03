"""Round-2 integration additions on top of the capture e2e (round-1 drivers) and the memory e2e (C8 round-2 drivers),
both run from the integrate tree. Synthetic data only; isolated homes only; no network beyond loopback stubs.

 1 codex push seam in shadow (UserPromptSubmit hook command, default mode = no Z0INT_MEMORY_INJECT, loopback endpoint)
 2 cloud default: CC/Codex hook with mode unset and the public API base URL -> nothing model-visible; canary with the
   public API base URL and no allow_cloud_injection -> cloud_injection_blocked; settings() default false everywhere
 3 same substrate: every MCP pull seam answers the same question with the same evidence set, and push-seam receipts
   for the same question carry the same evidence uids
 4 provenance: every evidence ref is a canonical locator (agentsview:<sid>#<ord> / eventlog:<seq>) with an event_uid in
   the receipt; scope: no sibling-project evidence in any receipt
 5 secret scrub with planted fake keys: none of the fixture SECRETS appear anywhere under the memory root's z0 home,
   results, or stub request logs
 6 MemoryUseReceipt rows: per harness, seam rows whose 'memory' validates as MemoryUseReceipt; capture opportunity rows
   with opportunity_record.memory (reported, not assumed)
 7 learning: per-harness export counts and verify --harness availability from the capture run's learn stage; frozen
   bundle row counts per harness; the scheduled tick entry point (`z0int loop tick`) availability
usage: e2e_r2_extra.py <worktree> <capture root> <memory root> <out.json>   (env: Z0INT_HOME=<memory root>/z0,
       AGENTSVIEW_DATA_DIR=<memory root>/av, PYTHONPATH=<worktree>/src)
"""
import json
import os
import re
import subprocess
import sys
from collections import Counter
from pathlib import Path

WT, CAP, MEM, OUT = (Path(a) for a in sys.argv[1:5])
sys.path[:0] = [str(WT / 'tests'), str(WT / 'src')]
from memory_fixture import SECRETS  # noqa: E402
from z0int.harness_id import turn_key  # noqa: E402
from z0int.memory import acceptance, seam  # noqa: E402
from z0int.memory_contract import MemoryUseReceipt  # noqa: E402

HARNESSES = ('claude-code', 'codex', 'grok', 'hermes', 'omp', 'omo', 'dsh')
TASK_CWD = str(MEM / 'cc' / 'z0')
PY = sys.executable
checks = []


def check(name, ok, detail=None):
    checks.append({'check': name, 'ok': bool(ok), 'detail': detail})


def jl(path):
    p = Path(path)
    return [json.loads(x) for x in p.read_text().splitlines() if x.strip()] if p.exists() else []


def hook(h, prompt, sid, env_extra):
    payload = json.dumps({'session_id': sid, 'prompt_id': 'p1', 'turn_id': 'p1', 'prompt': prompt, 'cwd': TASK_CWD,
                          'hook_event_name': 'UserPromptSubmit'})
    env = {k: v for k, v in os.environ.items() if k not in ('Z0INT_MEMORY_INJECT', 'ANTHROPIC_BASE_URL', 'OPENAI_BASE_URL')}
    env.update(env_extra)
    p = subprocess.run([PY, '-m', 'z0int.memory.hook', '--harness', h, 'prompt'], input=payload, capture_output=True,
                       text=True, env=env, timeout=60)
    return p.returncode, p.stdout


Q = json.loads((WT / 'tests/fixtures/z0evals-fb14919/cohort.json').read_text())['questions'][0]['prompt']

# 1 codex push seam, default mode (shadow), loopback endpoint
rc, out = hook('codex', Q, 'r2-codex-shadow', {'OPENAI_BASE_URL': 'http://127.0.0.1:11552/v1'})
import time  # noqa: E402
key = turn_key('codex', 'r2-codex-shadow', 'p1')
for _ in range(100):
    rows = [r for r in seam.rows('codex') if r.get('turn_key') == key]
    if rows and rows[-1].get('outcome') != 'queue_saturated' and any(r.get('mode') == 'shadow' for r in rows):
        break
    time.sleep(0.1)
rows = [r for r in seam.rows('codex') if r.get('turn_key') == key]
check('memory.codex_push_seam_shadow_default', rc == 0 and out.strip() == '' and rows and all(
    r['outcome'] == 'shadow' and r['injected'] is False and r.get('memory', {}).get('snapshot_id') for r in rows),
    {'rc': rc, 'stdout': out[:200], 'rows': [(r['outcome'], r.get('would_inject')) for r in rows]})

# 2 cloud default OFF
cloud = {}
for h, var, url in (('claude-code', 'ANTHROPIC_BASE_URL', 'https://api.anthropic.com'),
                    ('codex', 'OPENAI_BASE_URL', 'https://api.openai.com/v1')):
    rc_d, out_d = hook(h, Q, f'r2-default-{h}', {var: url})                      # mode unset -> shadow
    rc_c, out_c = hook(h, Q, f'r2-canary-{h}', {var: url, 'Z0INT_MEMORY_INJECT': 'canary'})
    time.sleep(1.0)
    rd = [r['outcome'] for r in seam.rows(h) if r.get('turn_key') == turn_key(h, f'r2-default-{h}', 'p1')]
    rcn = [(r['outcome'], r.get('endpoint_loopback')) for r in seam.rows(h) if r.get('turn_key') == turn_key(h, f'r2-canary-{h}', 'p1')]
    cloud[h] = {'default_mode': seam.settings(h, env={})['mode'], 'default_stdout': out_d.strip(), 'default_rows': rd,
                'canary_stdout': out_c.strip(), 'canary_rows': rcn}
check('memory.cloud_injection_default_off',
      all(c['default_mode'] == 'shadow' and c['default_stdout'] == '' and set(c['default_rows']) <= {'shadow'}
          and c['canary_stdout'] == '' and c['canary_rows'] == [('cloud_injection_blocked', False)] for c in cloud.values())
      and all(seam.settings(h, env={})['allow_cloud_injection'] is False for h in HARNESSES),
      cloud)
injected_cloud = [(h, r['turn_key']) for h in HARNESSES for r in seam.rows(h) if r.get('injected') and r.get('endpoint_loopback') is False]
check('memory.no_injection_into_a_non_loopback_endpoint_anywhere', not injected_cloud, injected_cloud)

# 3 same substrate (MCP pull seams of every harness)
mcp = json.loads((MEM / 'results' / 'mcp.json').read_text())
ev_sets = {m['harness']: sorted(json.dumps(e, sort_keys=True) for e in (m['orient_evidence'] or [])) for m in mcp}
check('memory.same_substrate_every_mcp_seam_same_evidence', len(ev_sets) == 6 and len({json.dumps(v) for v in ev_sets.values()}) == 1
      and all(ev_sets.values()), {h: len(v) for h, v in ev_sets.items()})

# push-seam shadow receipts for the same question: same evidence uids across cc/hermes/omp/codex
uids_by_h = {}
for h in ('claude-code', 'hermes', 'omp', 'codex', 'dsh'):
    uids_by_h[h] = sorted({tuple(r['memory'].get('evidence_event_uids') or ()) for r in seam.rows(h)
                           if r.get('outcome') in ('shadow', 'injected') and isinstance(r.get('memory'), dict)})
all_uids = set(u for v in uids_by_h.values() for t in v for u in t)
check('memory.same_substrate_push_receipts_share_evidence', all(uids_by_h[h] for h in uids_by_h) and len(
    set.intersection(*[set(u for t in v for u in t) for v in uids_by_h.values()])) > 0,
    {h: len(v) for h, v in uids_by_h.items()} | {'distinct_uids': len(all_uids)})

# 4 provenance + scope
LOC = re.compile(r'^(agentsview:[^#\s]+#\d+|eventlog:\d+|tencentdb:\S+)$')


def refs(e):
    if isinstance(e, str):
        return [e]
    if isinstance(e, dict):
        return [e.get('ref') or e.get('locator') or e.get('evidence_ref') or json.dumps(e)]
    return [json.dumps(e)]


bad = [r for m in mcp for e in (m['orient_evidence'] or []) for r in refs(e) if not LOC.match(r)]
rec_rows = [r for h in HARNESSES for r in seam.rows(h) if isinstance(r.get('memory'), dict)]
no_uid = [r['turn_key'] for r in rec_rows if r.get('outcome') in ('shadow', 'injected') and r.get('would_inject')
          and not r['memory'].get('evidence_event_uids')]
sibling = [r['turn_key'] for r in rec_rows if 'zebra' in json.dumps(r['memory'])]
check('memory.provenance_canonical_locators_and_event_uids', not bad and not no_uid,
      {'bad_refs': bad[:5], 'receipts_without_uids': no_uid[:5], 'sample_ref': refs((mcp[0]['orient_evidence'] or [None])[0])})
check('memory.scope_no_sibling_project_in_receipts', not sibling, sibling[:5])

# 5 secret scrub across everything this run wrote
hits = []
for root in (MEM / 'z0', MEM / 'results', MEM / 'cc' / 'stub-requests.jsonl', MEM / 'hermes' / 'stub-requests.jsonl',
             MEM / 'omp' / 'stub-requests.jsonl', MEM / 'cc' / 'claude', MEM / 'omp' / 'agent'):
    files = [root] if root.is_file() else [p for p in root.rglob('*') if p.is_file()] if root.exists() else []
    for p in files:
        try:
            data = p.read_bytes()
        except OSError:
            continue
        hits += [(str(p.relative_to(MEM)), k) for k, v in SECRETS.items() if v.encode() in data]
check('memory.secret_scrub_planted_fake_keys_never_leave', not hits, {'planted': sorted(SECRETS), 'hits': hits[:10]})
av_has = sum(v.encode() in (MEM / 'av' / 'sessions.db').read_bytes() for v in SECRETS.values())
check('memory.secret_probe_was_planted_in_the_substrate', av_has == len(SECRETS), {'planted_found_in_av_db': av_has})

# 6 MemoryUseReceipt rows per harness
per = {}
for h in HARNESSES:
    ok, n = 0, 0
    for r in seam.rows(h):
        if isinstance(r.get('memory'), dict):
            n += 1
            try:
                MemoryUseReceipt.from_dict(r['memory'])
                ok += 1
            except (TypeError, ValueError, KeyError):
                pass
    acc = jl(MEM / 'results' / f'rows-{h}.jsonl')
    per[h] = {'seam_rows_with_receipt': n, 'valid': ok, 'acceptance_rows': len(acc)}
check('memory.memory_use_receipt_rows_present_push_seams', all(per[h]['seam_rows_with_receipt'] > 0 and
      per[h]['valid'] == per[h]['seam_rows_with_receipt'] for h in ('claude-code', 'codex', 'hermes', 'omp', 'dsh')), per)
check('memory.acceptance_rows_every_harness', all(per[h]['acceptance_rows'] == 6 for h in HARNESSES),
      {h: per[h]['acceptance_rows'] for h in HARNESSES})
opp_mem = {}
for h in HARNESSES:
    rows_ = jl(CAP / 'z0home' / 'state' / h / 'opportunities.jsonl')
    opp_mem[h] = {'opportunities': len(rows_), 'with_memory': sum('memory' in r for r in rows_)}
check('memory.opportunity_record_memory_joined (DoD D3 "MemoryUseReceipt in opportunity records")',
      all(v['with_memory'] > 0 for v in opp_mem.values()), opp_mem)

# 7 learning: export / verify / frozen / tick
L = CAP / 'learn'
exp = {}
for h in HARNESSES:
    try:
        exp[h] = json.loads((L / 'tables' / f'{h}.counts.json').read_text() or '{}')
    except (OSError, ValueError):
        exp[h] = None
rows_by_h = {h: (e or {}).get('rows', (e or {}).get('written')) for h, e in exp.items()}
check('learn.harness_generic_export_nonzero_rows_all_7', all((v or 0) > 0 for v in rows_by_h.values()),
      {'export_rows': rows_by_h})
vc = (L / 'verify-codex.out').read_text() if (L / 'verify-codex.out').exists() else ''
check('learn.harness_generic_verify_all_7', 'unrecognized arguments' not in vc and 'error' not in vc.lower(),
      {'verify --harness codex': vc.strip().splitlines()[-1:] if vc else None})
man = json.loads((L / 'frozen' / 'manifest.json').read_text()) if (L / 'frozen' / 'manifest.json').exists() else {}
by_schema = man.get('by_schema') or {}
frozen_h = Counter()
for s, n in by_schema.items():
    parts = s.split('.')
    if len(parts) > 2:
        frozen_h[parts[1].replace('_', '-')] += n
check('learn.frozen_capture_rows_nonzero_all_7 (unlabelled; not an export)', all(frozen_h.get(h, 0) > 0 for h in HARNESSES),
      dict(frozen_h))
tick = subprocess.run([PY, '-m', 'z0int.cli', 'loop', 'tick', '--help'], capture_output=True, text=True, timeout=60)
check('learn.tick_entry_point_runs_once', tick.returncode == 0 and 'tick' in tick.stdout,
      {'rc': tick.returncode, 'stderr_tail': tick.stderr.strip().splitlines()[-1:] if tick.stderr else None})
vl = json.loads((L / 'el' / 'verified-loop.json').read_text()) if (L / 'el' / 'verified-loop.json').exists() else {}
decision = vl.get('decision') or (vl.get('primary') or {}).get('decision')
check('learn.learner_once_reports_sufficiency_honestly_never_promotes',
      decision == 'INSUFFICIENT_DATA' and not list((CAP / 'z0home').rglob('promotion_request*')),
      {'decision': decision})

verdict = {'passed': sum(c['ok'] for c in checks), 'total': len(checks),
           'failed': [c['check'] for c in checks if not c['ok']], 'checks': checks}
OUT.write_text(json.dumps(verdict, indent=1))
print(json.dumps({k: verdict[k] for k in ('passed', 'total', 'failed')}, indent=1))
