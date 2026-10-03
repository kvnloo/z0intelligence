"""C2-loop-consumers isolated e2e. usage: e2e_c2.py <worktree> <e2e-home> <python> <schema.sql>

Everything lives under <e2e-home> (HOME, Z0INT_HOME, CLAUDE_CONFIG_DIR, AGENTSVIEW_DATA_DIR, legacy fixtures):
 1. replay the AgentsView .schema dump (FTS5 shadow tables *_data/_idx/_docsize/_config/_content filtered out) into
    fresh scratch DBs with user_version 74 and 113 (plus a 75 copy and a copy without deepseek-harness)
 2. capture synthetic turns for all seven harnesses through the C1 writer (`python -m z0int.hook_adapter`)
 3. fill the DBs with synthetic sessions for every agent incl. deepseek-harness; write CC transcripts
 4. write synthetic legacy JSONL fixtures (OMP v1 spine, cognition-shadow, DSH jev receipts)
 5. run the CLI: `z0int outcomes verify --harness X`, `z0int loop import ...`, `z0int loop export`,
    `z0int loop merge` over two synthetic host homes; re-run each to show idempotency
Round 2 (host C): OMP/DSH/Hermes shell results without a result event, the real v0.39 schema at user_version 74,
an unreadable schema (reader_unavailable, no traceback), and one turn key across verified and exported omp rows.
Round 3 (hosts D/E/F): exit-like decoys in the output body of a failing test run for every AgentsView harness;
`loop export` on an empty home and on a home holding only failure rows; `loop merge` of two table-less sets; the
omp/legacy manifest's label source; Codex cohorts on the v0.39 host and turn keys merged into more than one cohort.
Prints a counts-only report. No real ~/.z0int, ~/.dsh, transcript or DB row is read.
"""
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

WT, ROOT, PY, SCHEMA = Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[3], Path(sys.argv[4])
sys.path.insert(0, str(WT / 'tests'))
from agentsview_fixture import AVFixture  # noqa: E402  (insert helpers only; the schema here is the full dump)

SECRETS = ['SYNTH-SECRET-PROMPT', 'secret-repo-path', 'SYNTH-TOOL-INPUT']
AGENT = {'hermes': 'hermes', 'codex': 'codex', 'grok': 'grok', 'omp': 'omp', 'omo': 'omo', 'dsh': 'deepseek-harness'}
SHADOW_SUFFIX = re.compile(r"CREATE TABLE '?\w+_(data|idx|docsize|config|content)'?\(")
NOW = time.time()
T0 = NOW - 3 * 3600  # sessions three hours old: the index is fresh (stale-hours 6)
report: dict = {}


def env(home: Path, **extra):
    return {'PATH': '/usr/local/bin:/usr/bin:/bin', 'LANG': 'C.UTF-8', 'HOME': str(home / 'home'),
            'Z0INT_HOME': str(home / 'z0'), 'PYTHONPATH': os.environ.get('PYTHONPATH', str(WT / 'src')),
            'PYTHONPYCACHEPREFIX': str(ROOT / 'pycache'), 'TMPDIR': str(ROOT / 'tmp'),
            'CLAUDE_CONFIG_DIR': str(home / 'claude'), 'AGENTSVIEW_DATA_DIR': str(home / 'av113'),
            'GIT_CONFIG_GLOBAL': os.devnull, 'GIT_CONFIG_NOSYSTEM': '1',
            'Z0INT_SOCKET_GUARD_LOG': os.environ.get('Z0INT_SOCKET_GUARD_LOG', str(ROOT / 'socket-violations.log')),
            **extra}


def z0int(home, *args, ok=True):
    p = subprocess.run([PY, '-m', 'z0int', *map(str, args)], capture_output=True, text=True, env=env(home), timeout=300)
    if ok and p.returncode != 0:
        raise SystemExit(f'z0int {args} -> {p.returncode}\n{p.stdout}\n{p.stderr}')
    return p


def hook(home, harness, event, payload, **extra):
    subprocess.run([PY, '-m', 'z0int.hook_adapter', '--harness', harness, event], input=json.dumps(payload),
                   capture_output=True, text=True, env=env(home, **extra), timeout=60, check=True)


def git(repo, *args, t=None):
    e = env(ROOT, GIT_AUTHOR_NAME='f', GIT_AUTHOR_EMAIL='f@x', GIT_COMMITTER_NAME='f', GIT_COMMITTER_EMAIL='f@x')
    if t is not None:
        e['GIT_AUTHOR_DATE'] = e['GIT_COMMITTER_DATE'] = f'@{int(t)} +0000'
    return subprocess.run(['git', '-C', str(repo), *args], capture_output=True, text=True, env=e, check=True).stdout


# ----------------------------------------------------------------------------- 1. schema replay
def statements(sql: str):
    buf = ''
    for line in sql.splitlines(keepends=True):
        buf += line
        if sqlite3.complete_statement(buf):
            yield buf.strip()
            buf = ''


def replay_schema(path: Path, user_version: int) -> dict:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    kept = skipped = failed = 0
    failures = []
    for st in statements(SCHEMA.read_text()):
        if SHADOW_SUFFIX.match(st):
            skipped += 1
            continue
        try:
            conn.execute(st)
            kept += 1
        except sqlite3.Error as exc:
            failed += 1
            failures.append(f'{st.split("(")[0][:60]}: {exc}')
    conn.execute(f'pragma user_version = {user_version}')
    conn.commit()
    conn.close()
    return {'statements': kept, 'fts5_shadow_tables_filtered': skipped, 'failed': failed, 'failures': failures[:5]}


def open_fixture(path: Path) -> AVFixture:
    fx = AVFixture.__new__(AVFixture)
    fx.path, fx.conn, fx.ordinal = path, sqlite3.connect(path), {}
    fx.columns = {r[1] for r in fx.conn.execute('pragma table_info(sessions)')}
    return fx


# ----------------------------------------------------------------------------- 2-3. capture + sessions
def scenario(fx, sid, repo, sha, t):
    """Three user turns: failing tests; passing tests + a commit later reverted; a correction cue."""
    fx.user(sid, 'SYNTH-SECRET-PROMPT fix the parser', t)
    fx.bash(sid, 'pytest tests/', t + 5, exit=1, out='2 failed')
    fx.user(sid, 'now tweak c', t + 100)
    fx.bash(sid, 'uv run pytest -q', t + 105, out='5 passed')
    fx.bash(sid, 'git commit -m "feat: tweak c value"', t + 109, out=f'[main {sha[:7]}] feat: tweak c value')
    fx.say(sid, 'Done.', t + 110)
    fx.user(sid, "that's wrong, undo it", t + 200)
    fx.say(sid, 'ok', t + 210)


def capture(home, harness, session, n, *, repo, grok=False):
    for i in range(1, n + 1):
        payload = {'session_id': session, 'turn_id': f'{session}:turn:{i}', 'cwd': str(repo),
                   'prompt': f'SYNTH-SECRET-PROMPT turn {i}', 'hook_event_name': 'UserPromptSubmit'}
        extra = {}
        if grok:
            payload = {'hookEventName': 'UserPromptSubmit', 'prompt': f'SYNTH-SECRET-PROMPT {i}', 'cwd': str(repo),
                       'timestamp': f'2026-10-03T00:00:0{i}Z'}
            extra = {'GROK_SESSION_ID': session, 'GROK_HOOK_EVENT': 'user_prompt_submit'}
        if harness in ('omp', 'omo', 'dsh', 'hermes'):  # bridges send no hook_event_name (Codex-shaped otherwise)
            payload.pop('hook_event_name')
        hook(home, harness, 'prompt', payload, **extra)
        stop = {**{k: v for k, v in payload.items() if k != 'prompt'}, 'last_assistant_message': 'Done.'}
        if 'hook_event_name' in payload:
            stop['hook_event_name'] = 'Stop'
        if grok:
            stop = {'hookEventName': 'Stop', 'cwd': str(repo), 'lastAssistantMessage': 'Done.'}
            extra['GROK_HOOK_EVENT'] = 'stop'
        hook(home, harness, 'stop', stop, **extra)


def wait_builds(home, expected, timeout=120):
    deadline = time.time() + timeout
    while time.time() < deadline:
        got = sum(len((p).read_text().splitlines()) for p in (home / 'z0' / 'state').glob('*/opportunities.jsonl'))
        if got >= expected:
            return got
        time.sleep(0.5)
    return got


def build_host(home: Path, *, extra_codex: bool = False) -> dict:
    """One synthetic host: a work repo, captured turns for all seven harnesses, AgentsView DBs, CC transcripts."""
    repo = home / 'work' / 'secret-repo-path'
    repo.mkdir(parents=True)
    git(repo, 'init', '-q', '-b', 'main')
    (repo / 'src').mkdir()
    (repo / 'src' / 'app.py').write_text('a = 1\nc = 3\n')
    git(repo, 'add', '-A')
    git(repo, 'commit', '-q', '-m', 'init', t=T0 - 86400)
    (repo / 'src' / 'app.py').write_text('a = 1\nc = 30\n')
    git(repo, 'commit', '-qam', 'feat: tweak c value', t=T0 + 110)
    sha = git(repo, 'rev-parse', 'HEAD').strip()
    git(repo, 'revert', '--no-edit', sha, t=T0 + 1800)
    info = {}
    info['schema74'] = replay_schema(home / 'av74' / 'sessions.db', 74)
    info['schema113'] = replay_schema(home / 'av113' / 'sessions.db', 113)
    fx = open_fixture(home / 'av113' / 'sessions.db')
    sessions = {  # harness -> [(session id, AgentsView options, captured turns)]
        'hermes': [('20261003_090000_ab12', {'project': 'hermes-cli'}, 3),
                   ('cron_digest_20261003', {'project': 'hermes-cron'}, 3)],
        'codex': [('019a0c2e-0000-7000-8000-00000000c0de', {}, 3),
                  ('019a0c2e-0000-7000-8000-0000000e0ec0', {'session_kind': 'non-interactive'}, 3)],
        'grok': [('grok-sess-1', {}, 3), ('grok-sess-amb', {}, 3)],
        'omp': [('omp-bridge-1', {}, 3), ('omp-bridge-sub', {'relationship_type': 'subagent',
                                                             'parent_session_id': 'omp:omp-bridge-1'}, 3)],
        'omo': [('omo-bridge-1', {}, 3), ('omo-never-indexed', None, 2)],
        'dsh': [('dsh-root-1', {}, 3)],
    }
    if extra_codex:
        sessions['codex'].append(('019a0c2e-0000-7000-8000-00000000b0b0', {}, 3))
    expected = 0
    for harness, rows in sessions.items():
        for sid, opts, n in rows:
            capture(home, harness, sid, n, repo=repo, grok=harness == 'grok')
            expected += n
            if opts is None:
                continue
            av = fx.session(AGENT[harness], sid, started=T0, cwd=str(repo), **opts)
            scenario(fx, av, repo, sha, T0)
            if sid == 'grok-sess-amb':  # a second row claims the same Grok session: ambiguous -> unjoined
                av2 = fx.session('grok', 'grok-sess-amb-copy', started=T0, cwd=str(repo), source_session_id=sid)
                scenario(fx, av2, repo, sha, T0)
    for agent in ('claude', 'chatgpt', 'kimi'):  # other agents present in the index, never read by a harness rule
        fx.session(agent, f'{agent}-noise', started=T0)
    fx.close()
    shutil.copy(home / 'av113' / 'sessions.db', home / 'av75.db')
    c = sqlite3.connect(home / 'av75.db')
    c.execute('pragma user_version = 75')
    c.commit()
    c.close()
    shutil.copy(home / 'av113' / 'sessions.db', home / 'av-no-dsh.db')
    c = sqlite3.connect(home / 'av-no-dsh.db')
    c.execute("delete from sessions where agent = 'deepseek-harness'")
    c.commit()
    c.close()
    fx74 = open_fixture(home / 'av74' / 'sessions.db')  # the v0.39 copy holds the Hermes interactive session only
    av = fx74.session('hermes', '20261003_090000_ab12', started=T0, cwd=str(repo), project='hermes-cli')
    scenario(fx74, av, repo, sha, T0)
    fx74.close()
    # Claude Code: hook replay + transcripts in the isolated CLAUDE_CONFIG_DIR
    proj = home / 'claude' / 'projects' / '-work'
    proj.mkdir(parents=True)
    tpath = proj / 'cc-sess-1.jsonl'
    rows = []
    for i, (text, cmd, code) in enumerate([('SYNTH-SECRET-PROMPT fix it', 'pytest tests/', 1),
                                           ('run them again', 'pytest tests/', 0)], 1):
        t = T0 + 100 * i
        rows.append({'type': 'user', 'promptId': f'cc-p{i}', 'sessionId': 'cc-sess-1', 'cwd': str(repo),
                     'entrypoint': 'cli', 'timestamp': time.strftime('%Y-%m-%dT%H:%M:%S.000Z', time.gmtime(t)),
                     'message': {'role': 'user', 'content': text}})
        rows.append({'type': 'assistant', 'sessionId': 'cc-sess-1', 'cwd': str(repo),
                     'timestamp': time.strftime('%Y-%m-%dT%H:%M:%S.000Z', time.gmtime(t + 1)),
                     'message': {'id': f'm{i}', 'model': 'claude-x', 'content': [
                         {'type': 'tool_use', 'id': f'tu{i}', 'name': 'Bash', 'input': {'command': cmd}}]}})
        rows.append({'type': 'user', 'promptId': f'cc-p{i}', 'sessionId': 'cc-sess-1', 'cwd': str(repo),
                     'timestamp': time.strftime('%Y-%m-%dT%H:%M:%S.000Z', time.gmtime(t + 2)),
                     'message': {'role': 'user', 'content': [{'type': 'tool_result', 'tool_use_id': f'tu{i}',
                                                              'content': f'Exit code {code}' if code else 'ok',
                                                              'is_error': bool(code)}]}})
        tpath.write_text(''.join(json.dumps(r) + '\n' for r in rows))
        base = {'hook_event_name': 'UserPromptSubmit', 'session_id': 'cc-sess-1', 'prompt_id': f'cc-p{i}',
                'transcript_path': str(tpath), 'cwd': str(repo)}
        hook(home, 'claude-code', 'prompt', dict(base, prompt=text))
        hook(home, 'claude-code', 'stop', dict(base, hook_event_name='Stop', stop_hook_active=False,
                                               last_assistant_message='Done.'))
        expected += 1
    info['opportunity_rows'] = wait_builds(home, expected)
    info['expected_turns'] = expected
    return info


# ----------------------------------------------------------------------------- 4. legacy fixtures
def legacy_fixtures(home: Path) -> Path:
    leg = home / 'legacy'
    (leg / 'receipts').mkdir(parents=True)
    rec = lambda tr: {'schema': 'z0int.decision_receipt.v1', 'trace_id': tr, 'session_id': 'omp-legacy-1',  # noqa
                      'capability_id': 'route.code', 'provider': 'local_mb', 'route': 'local', 'execution': 'shadow',
                      'prediction': 'SYNTH-SECRET-PROMPT', 'ts': T0, 'extra': {'prompt': 'SYNTH-SECRET-PROMPT'}}
    (leg / 'receipts' / 'decisions.jsonl').write_text(''.join(json.dumps(rec(f'lt-{i}')) + '\n' for i in range(5)))
    joins = [({'test_pass': True, 'verification_source': 'ci'}, 'gold'), ({'user_correction': True}, 'negative'),
             ({'success': True, 'test_pass': True, 'source': 'bridge_turn_end'}, 'gold'),
             ({'execution_completed': True, 'source': 'omp_turn_end'}, 'execution')]
    (leg / 'receipts' / 'outcomes.jsonl').write_text(''.join(
        json.dumps({'schema': 'z0int.outcome_join.v1', 'ts': T0 + 60, 'trace_id': f'lt-{i}', 'outcome': oc,
                    'outcome_tier': tier, 'receipt': rec(f'lt-{i}')}) + '\n' for i, (oc, tier) in enumerate(joins)))
    shadow = []
    for i in range(300):
        served = i % 100 == 7
        row = {'label': 'nemotron_orchestrator_8b', 'backend': 'local_slm' if served else None, 'model': 'nemotron-8b',
               'selected_action': 'read' if served else None, 'abstained': not served, 'invalid_call': False,
               'latency_ms': 9.0}
        if not served:
            row['error'] = 'transport_error: <urlopen error [Errno 111] Connection refused>'
        shadow.append({'schema': 'z0int.cognition.shadow.receipt.v1', 'ts': T0 + i, 'trace_id': f'cs{i}',
                       'session_id': 'omp-legacy-1', 'state': 'OMP tool_call: bash\ninput: {"command": "SYNTH-TOOL-INPUT"}',
                       'candidate_action_count': 3, 'legal_ids': ['read', 'bash'], 'shadow': [row],
                       'selected_action': None, 'executed_action': None})
    (leg / 'cognition-shadow.jsonl').write_text(''.join(json.dumps(r) + '\n' for r in shadow))
    lin = {'session_id': 'dsh-legacy-1', 'trace_id': 'x', 'root_resolution': 'self_root', 'role': 'root', 'attempt': 0}
    # an orphan jev_decision (crash before its model_request) first: it must not stall the rows after it
    jev = [{'ts': '2026-09-29T09:59:59Z', 'harness': 'dsh', 'type': 'jev_decision', 'agentId': 'crashed', 'turn': 1,
            'tier': 'hard'}]
    for turn in (1, 2, 3):
        jev.append({'ts': '2026-09-29T10:00:0%dZ' % turn, 'harness': 'dsh', 'type': 'jev_decision', 'agentId': 'a1',
                    'turn': turn, 'tier': 'hard', 'specialty': 'code', 'confidence': 0.6, 'route_changed': True})
        jev.append({'ts': '2026-09-29T10:00:0%dZ' % turn, 'harness': 'dsh', 'type': 'model_request', 'agentId': 'a1',
                    'turn': turn, 'step': 1, 'purpose': 'root', 'routed': True, **lin})
    (leg / 'jev-receipts.jsonl').write_text(''.join(json.dumps(r) + '\n' for r in jev))
    return leg


# ----------------------------------------------------------------------------- 4b. round 2: harness result shapes
def shape_sessions(fx, repo, sha):
    """Shell results exactly as the pi (OMP), Hermes and DSH parsers store them (no tool_result_events row), and as
    the Grok parser does: ACP status 'completed' whatever the exit, no exit-code line."""
    omp = fx.session('omp', 'omp-shape-1', started=T0, cwd=str(repo))
    fx.user(omp, 'SYNTH-SECRET-PROMPT run the tests', T0)
    fx.tool(omp, 'bash', 'Bash', {'command': 'pytest -q'}, '..F..\n1 failed, 4 passed\n\nCommand exited with code 1',
            T0 + 5, status=None)
    fx.say(omp, 'Done.', T0 + 10)
    fx.user(omp, 'again', T0 + 100)
    fx.tool(omp, 'bash', 'Bash', {'command': 'cargo test'}, 'test result: FAILED. 3 passed; 1 failed\n\n'
            'Command exited with code 101', T0 + 105, status=None)
    fx.say(omp, 'Done.', T0 + 110)
    fx.user(omp, 'and once more', T0 + 200)
    fx.tool(omp, 'bash', 'Bash', {'command': 'pytest -q'}, '5 passed', T0 + 205, status=None)  # no notice, no status
    fx.say(omp, 'Done.', T0 + 210)
    dsh = fx.session('deepseek-harness', 'dsh-shape-1', started=T0, cwd=str(repo))
    fx.user(dsh, 'SYNTH-SECRET-PROMPT run the tests', T0)
    fx.tool(dsh, 'bash', 'Bash', {'command': 'pytest -q'}, '1 failed, 4 passed', T0 + 5, status=None)
    fx.say(dsh, 'Done.', T0 + 10)
    her = fx.session('hermes', '20261003_100000_cd34', started=T0, cwd=str(repo), project='hermes-cli')
    fx.user(her, 'SYNTH-SECRET-PROMPT run the tests', T0)
    fx.tool(her, 'terminal', 'Bash', {'command': 'pytest -q'},
            '{"output": "1 failed, 4 passed", "exit_code": 1, "error": null}', T0 + 5, status=None)
    fx.say(her, 'Done.', T0 + 10)
    grok = fx.session('grok', 'grok-shape-1', started=T0, cwd=str(repo))
    for i, (cmd, out) in enumerate([('pytest -q', '..F..\n1 failed, 4 passed in 0.12s'),
                                    ('pytest -q', 'Traceback (most recent call last):\n  File "t.py", line 1\n'
                                                  'ImportError: no module named y'),
                                    ('git commit -qam "feat: tweak c value"', f'[main {sha[:7]}] feat: tweak c value')]):
        t = T0 + 100 * i
        fx.user(grok, f'SYNTH-SECRET-PROMPT grok {i}', t)
        fx.tool(grok, 'run_terminal_command', 'Bash', {'command': cmd}, out, t + 5, status='completed')
        fx.say(grok, 'Done.', t + 10)


def round2(root: Path) -> dict:
    """Host C: the OMP/DSH/Hermes result shapes, the real v0.39 schema, an unreadable schema, one turn key."""
    home = root / 'host-c'
    repo = home / 'work' / 'secret-repo-path'
    repo.mkdir(parents=True)
    git(repo, 'init', '-q', '-b', 'main')
    (repo / 'app.py').write_text('c = 3\n')
    git(repo, 'add', 'app.py')
    git(repo, 'commit', '-q', '-m', 'init', t=T0 - 86400)
    (repo / 'app.py').write_text('c = 30\n')
    git(repo, 'commit', '-qam', 'feat: tweak c value', t=T0 + 205)
    sha = git(repo, 'rev-parse', 'HEAD').strip()
    git(repo, 'revert', '--no-edit', sha, t=T0 + 1800)
    out: dict = {}
    replay_schema(home / 'av113' / 'sessions.db', 113)
    fx = open_fixture(home / 'av113' / 'sessions.db')
    shape_sessions(fx, repo, sha)
    fx.close()
    fx39 = AVFixture(home / 'av39.db', user_version=74)
    out['v039_has_session_kind'] = 'session_kind' in fx39.columns
    shape_sessions(fx39, repo, sha)
    fx39.close()
    shutil.copy(home / 'av113' / 'sessions.db', home / 'av-broken.db')
    c = sqlite3.connect(home / 'av-broken.db')
    c.execute('drop table tool_result_events')
    c.commit()
    c.close()
    for harness, sid, n in (('omp', 'omp-shape-1', 3), ('dsh', 'dsh-shape-1', 1), ('hermes', '20261003_100000_cd34', 1)):
        capture(home, harness, sid, n, repo=repo)
    capture(home, 'grok', 'grok-shape-1', 3, repo=repo, grok=True)
    out['opportunity_rows'] = wait_builds(home, 8)

    def run(harness, db, *extra):
        p = z0int(home, 'outcomes', 'verify', '--harness', harness, '--agentsview-db', db, '--no-gh', '--json', *extra,
                  ok=False)
        rep = json.loads(p.stdout) if p.stdout.strip().startswith('{') else {}
        return dict({k: rep.get(k) for k in ('turns', 'states', 'status', 'failures', 'label_join',
                                             'test_label_polarity')}, exit=p.returncode,
                    traceback='Traceback' in p.stderr)
    out['shapes_v113'] = {h: run(h, home / 'av113' / 'sessions.db') for h in ('omp', 'dsh', 'hermes', 'grok')}
    out['shapes_v039_user_version_74'] = {h: run(h, home / 'av39.db', '--dry-run')
                                          for h in ('omp', 'dsh', 'hermes', 'grok')}
    grok_rows = [json.loads(x) for x in (home / 'z0' / 'state' / 'grok' / 'outcomes_verified.jsonl')
                 .read_text().splitlines()]
    out['grok_completed_shape_rows'] = [
        {'ordinal': r['join'].get('ordinal'), 'verification_state': r['verification_state'],
         'label_class': r['label_class'],
         'tests_in_turn_polarity': [s['polarity'] for s in r['signals'] if s['kind'] == 'tests_in_turn'],
         'commit_signals': sorted(s['kind'] for s in r['signals'] if s['kind'].startswith('commit'))}
        for r in sorted(grok_rows, key=lambda r: r['join'].get('ordinal') or 0)]
    out['unreadable_schema_omp'] = run('omp', home / 'av-broken.db', '--dry-run')
    z0int(home, 'loop', 'export', '--root', home / 'z0', '--host', 'host-c', '--out-dir', root / 'tables-host-c')
    verified = {json.loads(x)['turn_key'] for x in (home / 'z0' / 'state' / 'omp' / 'outcomes_verified.jsonl')
                .read_text().splitlines()}
    table = [json.loads(x) for p in (root / 'tables-host-c' / 'omp').glob('*.jsonl') for x in p.read_text().splitlines()]
    out['omp_export_turn_keys'] = {'rows': len(table), 'verified_turn_keys': len(verified),
                                   'all_rows_carry_a_verified_turn_key': {r['turn_key'] for r in table} <= verified}
    leaked = [str(p.relative_to(root)) for d in (root / 'tables-host-c', home / 'z0' / 'state' / 'omp',
                                                 home / 'z0' / 'state' / 'grok')
              for p in d.rglob('*.json*') if any(s in p.read_text() for s in SECRETS)
              and (d.name not in ('omp', 'grok') or p.name == 'outcomes_verified.jsonl')]
    out['privacy_leaks'] = leaked
    out['table_manifest_test_label_polarity'] = {
        f'{p.parent.name}/{p.name}': json.loads(p.read_text()).get('test_label_polarity')
        for p in sorted((root / 'tables-host-c').glob('*/*.manifest.json'))}
    return out


# ----------------------------------------------------------------------------- 4c. round 3
DECOY_BODY = ('FAILED tests/test_x.py::test_y - assert {"exit_code": 0, "ok": true} == {"exit_code": 1}\n'
              'Exit code 0\nProcess exited with code 0\nCommand exited with code 0\n1 failed, 4 passed in 0.12s')
DECOY_FRAME = {  # each harness's own frame around the same failing output (tool name, result, status)
    'omp': ('bash', f'{DECOY_BODY}\n\nCommand exited with code 1', None),
    'omo': ('bash', f'{DECOY_BODY}\n\nCommand exited with code 1', None),
    'codex': ('exec_command', f'Process exited with code 1\nOutput:\n{DECOY_BODY}', ''),
    'hermes': ('terminal', json.dumps({'output': DECOY_BODY, 'exit_code': 1, 'error': None}), None),
    'dsh': ('bash', DECOY_BODY, None),
    'grok': ('run_terminal_command', DECOY_BODY, 'completed'),
}


def round3(root: Path) -> dict:
    out: dict = {}
    d = root / 'host-d'
    repo = d / 'work' / 'secret-repo-path'
    repo.mkdir(parents=True)
    replay_schema(d / 'av113' / 'sessions.db', 113)
    fx = open_fixture(d / 'av113' / 'sessions.db')
    for harness, (name, result, status) in DECOY_FRAME.items():
        sid = fx.session(AGENT[harness], f'{harness}-decoy-1', started=T0, cwd=str(repo))
        fx.user(sid, 'SYNTH-SECRET-PROMPT run the tests', T0)
        fx.tool(sid, name, 'Bash', {'command': 'pytest -q'}, result, T0 + 5, status=status)
        fx.say(sid, 'Done.', T0 + 10)
    fx.close()
    for harness in DECOY_FRAME:
        capture(d, harness, f'{harness}-decoy-1', 1, repo=repo, grok=harness == 'grok')
    out['decoy_opportunity_rows'] = wait_builds(d, len(DECOY_FRAME))
    decoys = {}
    for harness in DECOY_FRAME:
        p = z0int(d, 'outcomes', 'verify', '--harness', harness, '--agentsview-db', d / 'av113' / 'sessions.db',
                  '--no-gh', '--json', ok=False)
        rep = json.loads(p.stdout)
        rows = [json.loads(x) for x in (d / 'z0' / 'state' / harness / 'outcomes_verified.jsonl').read_text()
                .splitlines()]
        decoys[harness] = {'exit': p.returncode, 'states': rep['states'], 'label_join': rep['label_join'],
                           'label_classes': sorted({str(r.get('label_class')) for r in rows})}
    out['decoy_failing_runs'] = decoys
    # E: a fresh host with no state at all; F: a host whose only state is a dsh reader_unavailable failure row
    e, f = root / 'host-e', root / 'host-f'
    e.mkdir()
    pe = z0int(e, 'loop', 'export', '--root', e / 'z0', '--host', 'host-e', '--out-dir', root / 'tables-host-e',
               ok=False)
    replay_schema(f / 'av-empty.db', 113)
    pv = z0int(f, 'outcomes', 'verify', '--harness', 'dsh', '--agentsview-db', f / 'av-empty.db', '--no-gh', '--json',
               ok=False)
    pf = z0int(f, 'loop', 'export', '--root', f / 'z0', '--host', 'host-f', '--out-dir', root / 'tables-host-f',
               ok=False)
    idx_f = json.loads((root / 'tables-host-f' / 'manifest.json').read_text()) if pf.returncode == 0 else {}
    pm = z0int(f, 'loop', 'merge', '--in', root / 'tables-host-e', '--in', root / 'tables-host-f',
               '--out', root / 'merged-empty', ok=False)
    idx_m = json.loads((root / 'merged-empty' / 'manifest.json').read_text()) if pm.returncode == 0 else {}
    out['table_less'] = {
        'export_empty_home': {'exit': pe.returncode, 'traceback': 'Traceback' in pe.stderr,
                              'manifest_written': (root / 'tables-host-e' / 'manifest.json').is_file()},
        'verify_dsh_no_agent': {'exit': pv.returncode, 'failures': json.loads(pv.stdout).get('failures')},
        'export_failure_rows_only': {'exit': pf.returncode, 'traceback': 'Traceback' in pf.stderr,
                                     'tables': idx_f.get('tables'),
                                     'dsh_failures': (idx_f.get('records') or {}).get('dsh', {}).get('failures')},
        'merge_two_table_less_sets': {'exit': pm.returncode, 'traceback': 'Traceback' in pm.stderr,
                                      'tables': idx_m.get('tables'), 'merged': idx_m.get('merged')}}
    # the omp/legacy table names its label source; AgentsView-labelled omp tables keep their polarity
    mans = {p.name: json.loads(p.read_text()) for p in (root / 'tables-host-a' / 'omp').glob('*.manifest.json')}
    out['omp_manifests'] = {n: {'label_source': m.get('label_source'), 'test_label_polarity': m.get('test_label_polarity')}
                            for n, m in sorted(mans.items())}
    # Codex cohorts per host (B has no session_kind) and turn keys that the merge placed in more than one cohort
    out['codex_cohort_rows'] = {h: {p.stem: len(p.read_text().splitlines())
                                    for p in sorted((root / f'tables-{h}' / 'codex').glob('*.jsonl'))}
                                for h in ('host-a', 'host-b')}
    seen: dict = {}
    for p in (root / 'merged-1').glob('*/*.jsonl'):
        if p.name.endswith('.shadow.jsonl'):
            continue
        for x in p.read_text().splitlines():
            seen.setdefault(json.loads(x)['turn_key'], set()).add(p.relative_to(root / 'merged-1').as_posix())
    out['merged_turn_keys_in_more_than_one_cohort'] = sum(len(v) > 1 for v in seen.values())
    # the same Codex sessions on a v0.39 host (G: real v0.39 schema, no session_kind) and a v0.44 host (H: exec run
    # flagged non-interactive): G never pools either into interactive; the merge counts the cohort disagreement
    hosts = {}
    for name, v039 in (('host-g', True), ('host-h', False)):
        h = root / name
        crepo = h / 'work' / 'secret-repo-path'
        crepo.mkdir(parents=True)
        db = h / 'av.db'
        if v039:
            cfx = AVFixture(db, user_version=74)
            out['host_g_has_session_kind'] = 'session_kind' in cfx.columns
        else:
            replay_schema(db, 113)
            cfx = open_fixture(db)
        for sid, kind in (('019a0c2e-0000-7000-8000-0000000e0ec0', 'non-interactive'),
                          ('019a0c2e-0000-7000-8000-00000000cafe', '')):
            av = cfx.session('codex', sid, started=T0, cwd=str(crepo), session_kind=kind)
            cfx.user(av, 'SYNTH-SECRET-PROMPT run the tests', T0)
            cfx.say(av, 'Done.', T0 + 10)
        cfx.close()
        for sid in ('019a0c2e-0000-7000-8000-0000000e0ec0', '019a0c2e-0000-7000-8000-00000000cafe'):
            capture(h, 'codex', sid, 1, repo=crepo)
        wait_builds(h, 2)
        rep = json.loads(z0int(h, 'outcomes', 'verify', '--harness', 'codex', '--agentsview-db', db, '--no-gh',
                               '--json').stdout)
        rows = [json.loads(x) for x in (h / 'z0' / 'state' / 'codex' / 'outcomes_verified.jsonl').read_text()
                .splitlines()]
        z0int(h, 'loop', 'export', '--root', h / 'z0', '--host', name, '--out-dir', root / f'tables-{name}')
        hosts[name] = {'label_join': rep['label_join'], 'cohorts': sorted(r['cohort'] for r in rows)}
    pm = z0int(root, 'loop', 'merge', '--in', root / 'tables-host-g', '--in', root / 'tables-host-h',
               '--out', root / 'merged-gh')
    idx = json.loads((root / 'merged-gh' / 'manifest.json').read_text())
    hosts['merge'] = {'exit': pm.returncode, 'tables': {k: v['rows'] for k, v in idx['tables'].items()},
                      'cross_cohort_turn_keys': idx.get('cross_cohort_turn_keys')}
    out['codex_v039_vs_v044'] = hosts
    return out


# ----------------------------------------------------------------------------- 5. the CLI
def verify_all(home: Path, tag: str) -> dict:
    out = {}
    for harness in AGENT:
        p = z0int(home, 'outcomes', 'verify', '--harness', harness, '--agentsview-db', home / 'av113' / 'sessions.db',
                  '--no-gh', '--json')
        rep = json.loads(p.stdout)
        out[harness] = {k: rep[k] for k in ('turns', 'states', 'status', 'label_join', 'failures', 'appended')}
    p = z0int(home, 'outcomes', 'verify', '--harness', 'claude-code', '--no-gh', '--json')
    rep = json.loads(p.stdout)
    out['claude-code'] = {k: rep[k] for k in ('turns', 'states', 'status', 'failures', 'appended')}
    report[f'verify_{tag}'] = out
    return out


def main():
    a, b = ROOT / 'host-a', ROOT / 'host-b'
    report['host_a_build'] = build_host(a)
    report['host_b_build'] = build_host(b, extra_codex=True)
    leg = legacy_fixtures(a)
    first = verify_all(a, 'host_a_run1')
    second = verify_all(a, 'host_a_run2')
    report['verify_rerun_appended'] = {h: second[h]['appended'] for h in second}
    verify_all(b, 'host_b')
    # guards on the CLI path
    g = {}
    for name, db, harness in (('v74_hermes', a / 'av74' / 'sessions.db', 'hermes'), ('v75_omp', a / 'av75.db', 'omp'),
                              ('no_dsh_agent', a / 'av-no-dsh.db', 'dsh')):
        p = z0int(a, 'outcomes', 'verify', '--harness', harness, '--agentsview-db', db, '--no-gh', '--dry-run', '--json',
                  ok=False)
        rep = json.loads(p.stdout)
        g[name] = dict({k: rep[k] for k in ('turns', 'states', 'status', 'failures', 'label_join')}, exit=p.returncode)
    p = z0int(a, 'outcomes', 'verify', '--harness', 'claude-code', '--projects-dir', a / 'empty-projects',
              '--no-gh', '--dry-run', '--json', ok=False)
    rep = json.loads(p.stdout)
    g['cc_wrong_projects_dir'] = dict({k: rep[k] for k in ('turns', 'status', 'failures')}, exit=p.returncode)
    # --since windows every captured turn: a window after every capture leaves 0 turns and 0 join counts
    rep = json.loads(z0int(a, 'outcomes', 'verify', '--harness', 'grok', '--agentsview-db', a / 'av113' / 'sessions.db',
                           '--since', '2099-01-01', '--no-gh', '--dry-run', '--json').stdout)
    g['grok_since_future'] = {k: rep[k] for k in ('turns', 'status', 'failures', 'label_join')}
    report['guards'] = g
    # legacy imports, twice each
    imports = {}
    for name, args in (('omp-v1', ['--decisions', leg / 'receipts' / 'decisions.jsonl',
                                   '--outcomes', leg / 'receipts' / 'outcomes.jsonl']),
                       ('omp-cognition-shadow', ['--src', leg / 'cognition-shadow.jsonl']),
                       ('dsh-jev', ['--src', leg / 'jev-receipts.jsonl'])):
        runs = [json.loads(z0int(a, 'loop', 'import', name, *args).stdout) for _ in range(2)]
        imports[name] = {'run1_rows_added': runs[0]['rows_added'], 'run2_rows_added': runs[1]['rows_added'],
                         'counts': runs[0]['counts'], 'same_manifest_sha256': runs[0]['manifest_sha256'] ==
                         runs[1]['manifest_sha256']}
    # a scrub correction (downgraded outcome_join.v1 for the gold turn) replaces the stale label on export
    with open(leg / 'receipts' / 'outcomes.jsonl', 'a') as fh:
        fh.write(json.dumps({'schema': 'z0int.outcome_join.v1', 'ts': T0 + 999, 'trace_id': 'lt-0',
                             'outcome': {'user_correction': True, 'source': 'scrub'}, 'outcome_tier': 'negative',
                             'receipt': {'schema': 'z0int.decision_receipt.v1', 'trace_id': 'lt-0',
                                         'session_id': 'omp-legacy-1', 'ts': T0}}) + '\n')
    scrub = json.loads(z0int(a, 'loop', 'import', 'omp-v1', '--decisions', leg / 'receipts' / 'decisions.jsonl',
                             '--outcomes', leg / 'receipts' / 'outcomes.jsonl').stdout)
    stored = [json.loads(x) for x in (a / 'z0' / 'state' / 'omp' / 'imported_turns.jsonl').read_text().splitlines()]
    imports['omp-v1_scrub'] = {'rows_added': scrub['rows_added'], 'stored_rows': len(stored),
                               'stored_turn_keys': len({r['turn_key'] for r in stored})}
    # a source that is a symlink into /workspace/hermes-home is refused before any open
    link = leg / 'innocent-link.jsonl'
    link.symlink_to('/workspace/hermes-home/jev/receipts.jsonl')
    r = z0int(a, 'loop', 'import', 'dsh-jev', '--src', link, ok=False)
    imports['dsh-jev_symlink_into_hermes_home'] = {'exit': r.returncode,
                                                   'refused': 'refusing to read' in r.stderr}
    report['imports'] = imports
    # export per host, merge twice
    for home, host in ((a, 'host-a'), (b, 'host-b')):
        z0int(home, 'loop', 'export', '--root', home / 'z0', '--host', host, '--out-dir', ROOT / f'tables-{host}')
    idx = json.loads((ROOT / 'tables-host-a' / 'manifest.json').read_text())
    legacy = [json.loads(x) for x in (ROOT / 'tables-host-a' / 'omp' / 'legacy.jsonl').read_text().splitlines()]
    report['export_omp_legacy'] = {'rows': len(legacy), 'turn_keys': len({r['turn_key'] for r in legacy}),
                                   'y_success': sorted(str(r['label']['y_success']) for r in legacy)}
    report['export_host_a'] = {'tables': {k: v['rows'] for k, v in idx['tables'].items()},
                               'shadow_tables': {k: v['rows'] for k, v in idx['shadow_tables'].items()},
                               'unsupported_schema': {h: r['unsupported_schema'] for h, r in idx['records'].items()},
                               'failures': {h: r['failures'] for h, r in idx['records'].items()}}
    m1 = z0int(a, 'loop', 'merge', '--in', ROOT / 'tables-host-a', '--in', ROOT / 'tables-host-b', '--out', ROOT / 'merged-1')
    m2 = z0int(a, 'loop', 'merge', '--in', ROOT / 'tables-host-b', '--in', ROOT / 'tables-host-a', '--out', ROOT / 'merged-2')
    j1, j2 = json.loads(m1.stdout), json.loads(m2.stdout)
    merged_codex = [json.loads(x) for x in (ROOT / 'merged-1' / 'codex' / 'interactive.jsonl').read_text().splitlines()]
    report['merge'] = {'hosts': j1['hosts'], 'tables': {k: v['rows'] for k, v in j1['tables'].items()},
                       'identical_manifest_sha256': j1['manifest_sha256'] == j2['manifest_sha256'],
                       'codex_interactive_hosts': sorted({tuple(r['hosts']) for r in merged_codex})}
    # a cross-version merge is refused (exit 2, nothing written)
    bad = ROOT / 'tables-host-bad'
    shutil.copytree(ROOT / 'tables-host-b', bad)
    man = bad / 'codex' / 'interactive.manifest.json'
    man.write_text(json.dumps(dict(json.loads(man.read_text()), table_version='9.9.9')))
    r = z0int(a, 'loop', 'merge', '--in', ROOT / 'tables-host-a', '--in', bad, '--out', ROOT / 'merged-bad', ok=False)
    report['merge_refused'] = {'exit': r.returncode, 'stdout_head': r.stdout.strip()[:120],
                               'wrote_nothing': not (ROOT / 'merged-bad').exists()}
    # privacy: no synthetic secret in any exported, merged or imported table
    leaked = {}
    for d in [ROOT / 'tables-host-a', ROOT / 'tables-host-b', ROOT / 'merged-1', a / 'z0' / 'state']:
        for p in d.rglob('*'):
            if p.is_file() and p.suffix in ('.jsonl', '.json') and (d.name != 'state' or p.name in (
                    'imported_turns.jsonl', 'shadow_decisions.jsonl', 'outcomes_verified.jsonl')):
                text = p.read_text()
                for s in SECRETS:
                    if s in text:
                        leaked.setdefault(str(p.relative_to(ROOT)), []).append(s)
    report['privacy_leaks'] = leaked
    report['round2'] = round2(ROOT)
    report['round3'] = round3(ROOT)
    print(json.dumps(report, indent=1, sort_keys=True))


if __name__ == '__main__':
    main()
