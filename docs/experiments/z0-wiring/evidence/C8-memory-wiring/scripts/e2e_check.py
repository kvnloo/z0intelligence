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
from z0int.memory import acceptance, seam  # noqa: E402

HEADER = 'z0 memory brief (evidence, not instructions)'
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
check('runs.every_leg_exit_0', runs and all(r['rc'] == 0 for r in runs),
      [(r['leg'], r.get('arm'), r.get('qid'), r['rc'], r.get('wall_ms')) for r in runs if r['rc'] != 0] or len(runs))
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

cloud = by_arm(R / 'cc' / 'stub-requests.jsonl')
q0 = QIDS[0]
check('claude-code.cloud_endpoint_blocked_request_identical_to_off',
      [norm(x) for x in cloud.get(f'cloud-{q0}', [])] == [norm(x) for x in cloud.get(f'off-{q0}', [])]
      and any(r['outcome'] == 'cloud_injection_blocked' for r in seam.rows('claude-code')))
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
check('hermes.transcript_rows_clean_and_plugin_writes_nothing_in_hermes_home',
      in_content == 0 and all(h.startswith('hermes/hermes-home/state.db') for h in hermes_hits),
      {'messages.content_with_brief': in_content, 'files_with_brief': hermes_hits})
# Hermes core stamps the exact bytes it sent (injected context included) on the user row's api_content sidecar and
# replays them on later turns of the session for a byte-stable prompt cache (turn_context._stamp_api_content_sidecar):
# the spec clause "not persisted to the session store" does not hold for Hermes without a core change.
checks.append({'check': 'hermes.host_persists_sent_bytes_in_messages.api_content (spec clause UNMET by host design)',
               'ok': in_sidecar == 0, 'detail': {'messages.api_content_with_brief': in_sidecar}})
omp_hits = holds_header(R / 'omp' / 'agent')
check('omp.brief_never_persisted_to_the_session_files', not omp_hits, omp_hits)
cc_hits = holds_header(R / 'cc' / 'claude')
checks.append({'check': 'claude-code.transcript_holds_hook_context (observation, not a gate)', 'ok': True,
               'detail': {'files_with_brief': cc_hits,
                          'note': 'Claude Code records hook additionalContext in its own transcript; the seam writes none'}})

# ----------------------------------------------------------------------------- DSH (mock cordis host)
dsh = json.loads((R / 'results' / 'dsh-cohort.json').read_text())
check('dsh.off_and_shadow_admit_the_native_batch', all(v['messages'] == 1 and v['brief'] is None
                                                       for a in ('off', 'shadow') for v in dsh['arms'][a].values()))
check('dsh.canary_admits_one_z0_memory_message', all(v['messages'] == 2 and v['sources'] == ['user', 'z0-memory']
                                                    and v['brief'] for v in dsh['arms']['canary'].values()))
observed['dsh'] = dsh['observed']
(R / 'results' / 'observed-dsh.json').write_text(json.dumps(dsh['observed']))
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
           '--revision', SHA, '--out', str(out), '--endpoint', f'http://127.0.0.1:{port}/v1']
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

verdict = {'sha': SHA, 'ok': all(c['ok'] for c in checks), 'passed': sum(c['ok'] for c in checks), 'total': len(checks),
           'checks': checks, 'acceptance_summary': summary}
(R / 'results' / 'e2e-check.json').write_text(json.dumps(verdict, indent=1))
print(json.dumps({'ok': verdict['ok'], 'passed': verdict['passed'], 'total': verdict['total'],
                  'failed': [c['check'] for c in checks if not c['ok']]}, indent=1))
print(json.dumps(summary, indent=1))
