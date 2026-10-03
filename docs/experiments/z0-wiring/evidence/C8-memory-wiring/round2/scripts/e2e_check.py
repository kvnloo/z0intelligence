"""C8 e2e verdict: request comparison per arm, seam rows, persistence probes, MCP round-trips, then the acceptance
rows of every harness (`z0int memory eval`) validated against the pinned z0evals fb14919 receipt schema.
usage: e2e_check.py <worktree> <e2e root>   -> <root>/results/e2e-check.json (+ rows-<harness>.jsonl)"""
import json
import re
import sqlite3
import subprocess
import sys
from pathlib import Path

WT, R = Path(sys.argv[1]), Path(sys.argv[2])
sys.path[:0] = [str(WT / 'tests'), str(WT / 'src')]
from memory_fixture import SECRETS  # noqa: E402
from z0int.harness_id import turn_key  # noqa: E402
from z0int.memory import acceptance, seam  # noqa: E402

HEADER = 'z0 memory brief (evidence, not instructions)'
LEAK = 'SIBLINGLEAKTOKEN7'
TASK_CWD = str(R / 'cc' / 'z0')  # project z0, the substrate's project
SCHEMA = json.loads((WT / 'tests/fixtures/z0evals-fb14919/receipt.schema.json').read_text())
COHORT = WT / 'tests/fixtures/z0evals-fb14919/cohort.json'
QIDS = [q['id'] for q in json.loads(COHORT.read_text())['questions']]
SHA = (R / 'sha.txt').read_text().strip()
checks = []


def check(name, ok, detail=None):
    checks.append({'check': name, 'ok': bool(ok), 'detail': detail})


def rows(path):
    p = Path(path)
    return [json.loads(line) for line in p.read_text().splitlines() if line.strip()] if p.exists() else []


UUID = re.compile(r'\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b')
HEXID = re.compile(r'\b[0-9a-f]{32,64}\b')
ISO = re.compile(r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?Z?')
TOOLU = re.compile(r'toolu_[A-Za-z0-9]+')
WALL = re.compile(r'(duration_ms: |_unix_ms\\\\?\":|"timestamp": )\d+')


def norm(text):
    for rx, rep in ((WALL, r'\1<ms>'), (UUID, '<uuid>'), (TOOLU, '<id>'), (HEXID, '<hex>'), (ISO, '<ts>')):
        text = rx.sub(rep, text)
    return text


def by_arm(path):
    out = {}
    for r in rows(path):
        if r.get('method', 'POST') != 'POST' or r.get('body') is None:
            continue
        out.setdefault(r['arm'], []).append(json.dumps([r.get('path'), r['body']], sort_keys=True))
    return out


# ----------------------------------------------------------------------------- runs
runs = rows(R / 'runs.jsonl')
gated = [r for r in runs if not (r['leg'] == 'cc' and r.get('arm') == 'cloud')]  # cloud CC run: no reachable model
check('runs.every_leg_exit_0', gated and all(r['rc'] == 0 for r in gated),
      [(r['leg'], r.get('arm'), r.get('qid'), r['rc'], r.get('wall_ms')) for r in gated if r['rc'] != 0] or len(gated))
check('isolation.masks_empty_inside', 'hermes-home entries=0 z0int entries=0' in (R / 'masks-inside.txt').read_text())

# ----------------------------------------------------------------------------- push seams: CC, Hermes, OMP
observed = {}
for leg, harness in (('cc', 'claude-code'), ('hermes', 'hermes'), ('omp', 'omp')):
    req = by_arm(R / leg / 'stub-requests.jsonl')
    same, differ, seen, prompts = [], [], {}, dict(line.split('\t', 1) for line in (R / 'questions.tsv').read_text().splitlines())
    for qid in QIDS:
        off, shadow, canary = (req.get(f'{a}-{qid}') for a in ('off', 'shadow', 'canary'))
        (same if off and [norm(x) for x in off] == [norm(x) for x in (shadow or [])] else differ).append(qid)
        seen[qid] = bool(canary) and any(HEADER in body and prompts[qid].split('?')[0][:30] in body for body in canary)
    check(f'{harness}.shadow_requests_identical_to_off', not differ and len(same) == len(QIDS), {'differ': differ})
    check(f'{harness}.canary_brief_in_next_model_request', all(seen.values()), seen)
    check(f'{harness}.off_requests_carry_no_brief', not any(HEADER in b for a, bs in req.items() if a.startswith(('off-', 'shadow-'))
                                                         for b in bs))
    probe = req.get('probe-probe-secret') or []
    check(f'{harness}.secret_probe_brief_is_scrubbed', any(HEADER in b for b in probe)
          and not any(v in b for b in probe for v in SECRETS.values()),
          {'requests': len(probe), 'brief': any(HEADER in b for b in probe)})
    srows = seam.rows(harness)
    shadow_rows = [r for r in srows if r.get('mode') == 'shadow']
    check(f'{harness}.shadow_rows_would_inject_with_snapshot', len(shadow_rows) >= len(QIDS) and all(
        r['outcome'] == 'shadow' and r['memory_snapshot_id'] and r['memory']['snapshot_id'] == r['memory_snapshot_id']
        for r in shadow_rows), {'shadow_rows': len(shadow_rows), 'would_inject': sum(r.get('would_inject', False) for r in shadow_rows)})
    check(f'{harness}.no_secret_in_seam_rows', not any(v in json.dumps(srows) for v in SECRETS.values()))
    observed[harness] = seen
    (R / 'results' / f'observed-{harness}.json').write_text(json.dumps(seen))

# Round 2: a non-loopback harness endpoint stays blocked even when Z0INT_MEMORY_ENDPOINT says loopback.
direct = {}
for h in ('claude-code', 'codex'):
    key = turn_key(h, f'cloud-{h}', 'p1')
    direct[h] = {'rows': [(r['outcome'], r.get('endpoint_loopback')) for r in seam.rows(h) if r.get('turn_key') == key],
                 'hook_stdout': (R / 'results' / f'hook-cloud-{h}.out').read_text()}
check('cloud.generic_env_never_marks_cc_or_codex_loopback (hook command, direct)',
      all(d['rows'] == [('cloud_injection_blocked', False)] and d['hook_stdout'] == '' for d in direct.values()), direct)
cc_cloud_run = [r for r in runs if r['leg'] == 'cc' and r.get('arm') == 'cloud']
blocked_cc = [r for r in seam.rows('claude-code') if r['outcome'] == 'cloud_injection_blocked'
              and r.get('endpoint_loopback') is False and r.get('turn_key') != turn_key('claude-code', 'cloud-claude-code', 'p1')]
injected_nonloop = [r for h in ('claude-code', 'codex') for r in seam.rows(h) if r.get('injected') and not r.get('endpoint_loopback')]
check('cloud.real_cc_run_with_cloud_base_url_and_loopback_env_is_blocked',
      len(blocked_cc) == 1 and not injected_nonloop and not any(a.startswith('cloud-') for a in by_arm(R / 'cc' / 'stub-requests.jsonl')),
      {'cc_cloud_run': cc_cloud_run, 'blocked_rows': len(blocked_cc), 'injected_non_loopback': len(injected_nonloop)})
check('default.allow_cloud_injection_false_for_every_harness',
      all(seam.settings(h)['allow_cloud_injection'] is False for h in (*acceptance.STUDY_HARNESSES, *acceptance.Z0_HARNESSES)))


# ----------------------------------------------------------------------------- persistence probes
def holds_header(root):
    hits = []
    for p in Path(root).rglob('*'):
        if p.is_file():
            try:
                if HEADER.encode() in p.read_bytes():
                    hits.append(str(p.relative_to(R)))
            except OSError:
                pass
    return hits


hermes_hits = holds_header(R / 'hermes' / 'hermes-home')
db = sqlite3.connect(f"file:{R / 'hermes' / 'hermes-home' / 'state.db'}?mode=ro", uri=True)
cols = {c[1] for c in db.execute('pragma table_info(messages)')}
in_content = db.execute('select count(*) from messages where content like ?', (f'%{HEADER}%',)).fetchone()[0]
in_sidecar = db.execute('select count(*) from messages where api_content like ?', (f'%{HEADER}%',)).fetchone()[0] \
    if 'api_content' in cols else 0
db.close()
check('hermes.plugin_writes_nothing_in_hermes_home_and_transcript_content_is_clean',
      in_content == 0 and all(h.startswith('hermes/hermes-home/state.db') for h in hermes_hits),
      {'messages.content_with_brief': in_content, 'files_with_brief': hermes_hits})
omp_hits = holds_header(R / 'omp' / 'agent')
cc_hits = holds_header(R / 'cc' / 'claude')
dsh_r = json.loads((R / 'results' / 'dsh-cohort.json').read_text())
dsh_admitted = sum('z0-memory' in v['sources'] for v in dsh_r['arms']['canary'].values())
# Per-shim persistence of the brief, asserted against the recorded deviations (NOTES.md): spec C8 red test 2 ("not
# persisted to the transcript or session store") holds for OMP only; DSH, Hermes and Claude Code persist by host
# design (owner decision pending). A change in either direction fails this check.
EXPECTED_PERSISTENCE = {'omp': False, 'hermes': True, 'claude-code': True, 'dsh': True}
observed_persistence = {'omp': bool(omp_hits), 'hermes': in_sidecar > 0, 'claude-code': bool(cc_hits), 'dsh': dsh_admitted > 0}
check('persistence.per_shim_matches_recorded_deviations (spec clause met for omp only)',
      observed_persistence == EXPECTED_PERSISTENCE,
      {'observed': observed_persistence, 'expected': EXPECTED_PERSISTENCE,
       'hermes.messages.api_content_with_brief': in_sidecar, 'claude-code.files_with_brief': len(cc_hits),
       'omp.files_with_brief': omp_hits, 'dsh.canary_admitted_briefs (durable in the DSH log)': dsh_admitted})

# Sibling project leak canary: never in any model request, any DSH brief or any seam row.
leaks = {leg: sum(LEAK in b for bs in by_arm(R / leg / 'stub-requests.jsonl').values() for b in bs) for leg in ('cc', 'hermes', 'omp')}
leaks['dsh'] = sum(LEAK in (v['brief'] or '') for arm in dsh_r['arms'].values() for v in arm.values())
leaks['seam_rows'] = sum(LEAK in json.dumps(seam.rows(h)) for h in ('claude-code', 'hermes', 'omp', 'dsh'))
sib_asked = {leg: bool(by_arm(R / leg / 'stub-requests.jsonl').get('sibling-sibling')) for leg in ('cc', 'hermes', 'omp')}
check('scope.sibling_project_canary_never_reaches_a_model_request', not any(leaks.values()) and all(sib_asked.values()),
      {'leaks': leaks, 'sibling_question_asked': sib_asked})

# ----------------------------------------------------------------------------- DSH (mock cordis host)
dsh = json.loads((R / 'results' / 'dsh-cohort.json').read_text())
check('dsh.off_and_shadow_admit_the_native_batch', all(v['messages'] == 1 and v['brief'] is None
                                                       for a in ('off', 'shadow') for v in dsh['arms'][a].values()))
check('dsh.canary_admits_one_z0_memory_message', all(v['messages'] == 2 and v['sources'] == ['user', 'z0-memory']
                                                    and v['brief'] for v in dsh['arms']['canary'].values()))
observed['dsh'] = dsh['observed']
(R / 'results' / 'observed-dsh.json').write_text(json.dumps(dsh['observed']))
dsh_rows = seam.rows('dsh')
n_q = len(QIDS) + 1
check('dsh.generic_env_never_marks_a_profile_without_model_endpoint_loopback',
      all(v['messages'] == 1 and v['brief'] is None for v in dsh['arms']['cloud-env'].values())
      and sum(r['outcome'] == 'cloud_injection_blocked' and r.get('endpoint_loopback') is False for r in dsh_rows) == n_q)
check('dsh.a_session_without_cwd_gets_no_brief_and_a_counted_no_scope',
      all(v['messages'] == 1 and v['brief'] is None for v in dsh['arms']['nocwd'].values())
      and sum(r['outcome'] == 'no_scope' for r in dsh_rows) == n_q, {'no_scope_rows': sum(r['outcome'] == 'no_scope' for r in dsh_rows)})
node_test = (R / 'dsh-node-test.txt').read_text()
check('dsh.memory_node_tests_pass', 'ℹ fail 0' in node_test, node_test.splitlines()[-8:])

# ----------------------------------------------------------------------------- pull seams (MCP)
mcp = json.loads((R / 'results' / 'mcp.json').read_text())
MEMORY_TOOLS = ['history', 'inspect', 'memory_search', 'orient', 'unknowns', 'verify']
check('mcp.every_harness_has_z0_memory', sorted(m['harness'] for m in mcp) == sorted(
    ['claude-code', 'codex', 'grok', 'omp', 'omo', 'dsh']) and all(m['server'] == 'z0-memory' for m in mcp))
check('mcp.memory_tools_only_no_route_worker', all(m['tools'] == MEMORY_TOOLS and m['route_worker_is_error'] for m in mcp))
check('mcp.orient_returns_cohort_evidence', all(m['orient_evidence'] for m in mcp),
      {m['harness']: len(m['orient_evidence'] or []) for m in mcp})
check('mcp.no_secret_in_responses', not any(v in json.dumps(m) for m in mcp for v in SECRETS.values()))

# ----------------------------------------------------------------------------- acceptance rows per harness
summary = {}
for harness in (*acceptance.STUDY_HARNESSES, *acceptance.Z0_HARNESSES):
    out = R / 'results' / f'rows-{harness}.jsonl'
    out.unlink(missing_ok=True)
    port = {'claude-code': 11550, 'hermes': 11551}.get(harness, 11552)
    cmd = [sys.executable, '-m', 'z0int.memory.cli', 'eval', '--harness', harness, '--cohort', str(COHORT),
           '--revision', SHA, '--out', str(out), '--endpoint', f'http://127.0.0.1:{port}/v1', '--cwd', TASK_CWD]
    if harness in observed:
        cmd += ['--observed', str(R / 'results' / f'observed-{harness}.json')]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
    got = rows(out)
    study = harness in acceptance.STUDY_HARNESSES
    errs = [acceptance.validate(r, SCHEMA) for r in got] if study else []
    check(f'rows.{harness}.written_and_{"valid_against_fb14919_schema" if study else "labelled_z0_memory_acceptance"}',
          proc.returncode == 0 and len(got) == len(QIDS) and (all(e == [] for e in errs) if study else all(
              r['schema'] == acceptance.ACCEPTANCE_SCHEMA and r['label'] == acceptance.LABEL for r in got)),
          {'rc': proc.returncode, 'stderr': proc.stderr[-300:], 'errors': [e for e in errs if e][:2]})
    b = {r['question_id']: next((n.split(':')[0] for n in r['notes'] if n.startswith('B=')), '?') for r in got}
    summary[harness] = {
        'schema': got[0]['schema'] if got else None,
        'A_retrieval_ok': sum(r['retrieval_ok'] for r in got), 'B': sorted(set(b.values())),
        'B_injected': sum(r['injected'] for r in got), 'C_answer_supported': sum(r['answer_supported'] for r in got),
        'C_verified': sum(r['verified'] for r in got),
        'D_missing_evidence_abstained': next((r['abstained'] for r in got if r['question_id'] == 'missing-evidence'), None),
        'E_duplicate_injection': sum(r['duplicate_injection'] for r in got),
        'F_superseded_answer': next((r.get('superseded_answer') for r in got if r['question_id'] == 'supersession'), None),
        'rows': len(got)}
check('rows.refuse_study_label_for_claude_code_codex_grok', all(
    subprocess.run([sys.executable, '-m', 'z0int.memory.cli', 'eval', '--harness', h, '--cohort', str(COHORT), '--revision',
                    SHA, '--out', str(R / 'results' / 'refused.jsonl'), '--schema', acceptance.STUDY_SCHEMA],
                   capture_output=True, text=True).returncode != 0 for h in acceptance.Z0_HARNESSES)
      and not (R / 'results' / 'refused.jsonl').exists())

# ----------------------------------------------------------------------------- echo guard (after the acceptance rows)
# AgentsView v0.44 indexes every DSH user message whatever its source.kind, and a CC transcript / Hermes api_content
# re-index would carry the brief too: put the briefs this run produced back into the substrate as those hosts persist
# them, then a later brief must not cite them.
brief_text = next(v['brief'] for v in dsh['arms']['canary'].values() if v['brief'])
av = Path(seam.paths.home()).parent / 'av' / 'sessions.db'
conn = sqlite3.connect(av)
for sid, agent, text in (('echo-dsh', 'deepseek-harness', brief_text),
                         ('echo-hermes', 'hermes', 'how do we roll the quokka cap?\n\n' + brief_text),
                         ('echo-cc', 'claude', brief_text)):
    conn.execute("insert into sessions (id, project, agent, started_at, cwd, message_count) values (?,?,?,?,?,1)",
                 (sid, 'z0', agent, '2026-10-02T00:00:00Z', '/w/z0'))
    conn.execute("insert into messages (session_id, ordinal, role, content, timestamp) values (?,?,?,?,?)",
                 (sid, 0, 'user', text, '2026-10-02T00:00:00Z'))
conn.commit()
conn.close()
echo_q = json.loads(COHORT.read_text())['questions'][0]['prompt']
echo = seam.turn('hermes', turn_key='hermes:echo-probe:1', query=echo_q, mode='on', endpoint='http://127.0.0.1:9',
                 cwd=TASK_CWD)
ctx_text = echo.get('context') or ''
check('echo_guard.reindexed_briefs_are_never_evidence',
      echo['outcome'] == 'injected' and 'agentsview:echo-dsh#' not in ctx_text and 'agentsview:echo-cc#' not in ctx_text
      and ctx_text.count(HEADER) == 1,
      {'outcome': echo['outcome'], 'evidence': (echo.get('brief') or {}).get('evidence')})

# ----------------------------------------------------------------------------- observations (counted, not gated)
turn_markers = len(list((Path(seam.paths.home()) / 'state' / 'memory' / 'seam' / 'turns').iterdir()))
outcomes = {h: dict(seam.counts(h)) for h in ('claude-code', 'codex', 'hermes', 'omp', 'dsh')}
checks.append({'check': 'observation.seam_outcomes_and_turn_markers', 'ok': True,
               'detail': {'outcomes': outcomes, 'turn_markers': turn_markers}})

verdict = {'sha': SHA, 'ok': all(c['ok'] for c in checks), 'passed': sum(c['ok'] for c in checks), 'total': len(checks),
           'checks': checks, 'acceptance_summary': summary}
(R / 'results' / 'e2e-check.json').write_text(json.dumps(verdict, indent=1))
print(json.dumps({'ok': verdict['ok'], 'passed': verdict['passed'], 'total': verdict['total'],
                  'failed': [c['check'] for c in checks if not c['ok']]}, indent=1))
print(json.dumps(summary, indent=1))
