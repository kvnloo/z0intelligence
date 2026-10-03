"""C7-memory-core isolated e2e (synthetic data only; isolated homes; private port; never the live DB or service).

1. Build a synthetic AgentsView sessions.db from the real v0.39 schema (copied read-only by C2 into
   homes/C2-loop-consumers/schema/sessions.schema.sql) with the FTS5 shadow tables filtered out, user_version 74,
   synthetic sessions across hermes/claude/codex/omp/deepseek-harness plus a sibling project, and one tool-output
   message carrying API-key, bearer, BWS, AWS and PEM shaped strings (assembled at runtime).
2. Start a fake TencentDB gateway on 127.0.0.1:11545 with a bearer check (fake token from an env var).
3. Seed the z0 ledger: two superseding claims and one AgentsView reference (references-only flag on).
4. Drive `python -m z0int.intelligence_mcp --profile memory` over stdio with JSON-RPC: initialize, tools/list,
   memory_search, orient, history, verify, inspect,
   unknowns; then remove sessions.db and check abstention and isError.
5. The same server with the network namespace removed (unshare -rn): still answers.
6. Fix round (verifier REVISE): env-dump/JSON credential shapes and an AgentsView secret_findings span never
   surface; inspect never leaks a credential cut by its bound (agentsview: and eventlog:); a gateway hung on every
   endpoint (private port PORT+1) costs search and orient at most deadline+50 ms; build_state_packet (hook path)
   makes no gateway call; context_resolve without a turn_key skips memory; unscoped tools say they span all projects.
7. `z0int memory doctor` against the fixture: fails on a stale DB, passes on a fresh one; no config secret printed.
Round 2 (verdict REVISE, major: TencentDB revision): the fake gateway answers with the real MemoryCore shapes
(/health: version only; search: items with their own id/version/updated_at, no revision). With it reachable the
snapshot records `unversioned`, briefs bypass the cache, and new gateway content reaches the next brief in a second
process although AgentsView, the ledger and the repo are unchanged. The persistent cache hit (process A miss,
process B hit) and the snapshot change on new content are checked with the gateway not configured.
Prints one CHECK line per assertion and exits non-zero on any failure.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

WT = Path(sys.argv[1])
HOME = Path(sys.argv[2])
PORT = int(sys.argv[3]) if len(sys.argv) > 3 else 11545
sys.path.insert(0, str(WT / 'tests'))
from memory_fixture import SECRET_MESSAGE, SECRETS, FakeTencentDB  # noqa: E402

PY = sys.executable
SCHEMA_SRC = Path('/mnt/zer0models/z0-wt/wiring/homes/C2-loop-consumers/schema/sessions.schema.sql')
TOKEN = 'fake-e2e-tdb-bearer-0123456789'
AUTH_VALUE = 'FAKE-E2E-AGENTSVIEW-AUTH-0001'
FAILS: list[str] = []


def check(name: str, ok: bool, detail: str = '') -> None:
    print(f"CHECK {'PASS' if ok else 'FAIL'} {name}" + (f' :: {detail}' if detail else ''), flush=True)
    if not ok:
        FAILS.append(name)


def iso(t: float) -> str:
    return time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime(t))


def build_db(path: Path, *, age_hours: float) -> dict:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        path.unlink()
    raw = SCHEMA_SRC.read_text()
    kept, dropped, buf = [], 0, ''
    for line in raw.splitlines(keepends=True):
        buf += line
        if sqlite3.complete_statement(buf):
            if re.match(r"\s*CREATE TABLE '\w+_fts_(data|idx|docsize|config)'", buf):
                dropped += 1
            else:
                kept.append(buf)
            buf = ''
    conn = sqlite3.connect(path)
    conn.executescript(''.join(kept))
    t0 = time.time() - age_hours * 3600
    sessions = [('hermes:e1', 'hermes', 'z0', ['how is the walrus relay configured',
                                                'the walrus relay listens on a unix socket']),
                ('claude:e2', 'claude', 'z0', ['walrus relay cache sits under state memory', SECRET_MESSAGE]),
                ('codex:e3', 'codex', 'z0', ['walrus relay bench numbers for codex']),
                ('omp:e4', 'omp', 'z0', ['walrus relay routing through the omp bridge']),
                ('deepseek-harness:e5', 'deepseek-harness', 'z0', ['dsh walrus relay worker done']),
                ('claude:e6', 'claude', 'other', ['walrus relay sibling project plan'])]
    mid = 0
    for n, (sid, agent, project, msgs) in enumerate(sessions):
        conn.execute('insert into sessions (id, project, agent, started_at, message_count) values (?,?,?,?,?)',
                     (sid, project, agent, iso(t0 + n), len(msgs)))
        for o, content in enumerate(msgs):
            mid += 1
            conn.execute('insert into messages (id, session_id, ordinal, role, content, timestamp) values (?,?,?,?,?,?)',
                         (mid, sid, o, 'assistant' if o else 'user', content, iso(t0 + n + o / 10)))
    conn.execute('pragma user_version = 74')
    conn.commit()
    conn.close()
    return {'statements': len(kept), 'fts_shadow_tables_filtered': dropped}


def mcp(lines: list[dict], env: dict, prefix: list[str] = ()) -> list[dict]:
    proc = subprocess.run([*prefix, PY, '-m', 'z0int.intelligence_mcp', '--profile', 'memory'],
                          input=''.join(json.dumps(x) + '\n' for x in lines), capture_output=True, text=True,
                          timeout=120, env=env)
    if proc.returncode != 0:
        print(proc.stderr[-2000:])
    return [json.loads(line) for line in proc.stdout.splitlines() if line.strip()]


def call(i: int, name: str, args: dict) -> dict:
    return {'jsonrpc': '2.0', 'id': i, 'method': 'tools/call', 'params': {'name': name, 'arguments': args}}


def body(resp: dict):
    r = resp['result']
    text = r['content'][0]['text']
    return r['isError'], (text if r['isError'] else json.loads(text))


def no_secrets(blob: str) -> bool:
    parts = [*SECRETS.values(), SECRETS['bws'].split(':')[-1], TOKEN, AUTH_VALUE]
    return not any(p in blob for p in parts)


def main() -> int:
    if HOME.exists():
        shutil.rmtree(HOME)
    av, z0, home = HOME / 'av', HOME / 'z0home', HOME / 'home'
    claude_home = home / '.claude-home'
    claude_home.mkdir(parents=True)
    info = build_db(av / 'sessions.db', age_hours=1)
    print(f'fixture: {info}')
    check('fixture: FTS5 shadow tables filtered from the schema copy', info['fts_shadow_tables_filtered'] == 12)
    (av / 'config.toml').write_text(f'require_auth = true\nauth_token = "{AUTH_VALUE}"\n'
                                    f'[agents.claude]\nhomes = ["~/.claude", "{claude_home}"]\n')
    fake_bin = HOME / 'bin' / 'agentsview'
    fake_bin.parent.mkdir()
    fake_bin.write_text('#!/bin/sh\necho "agentsview v0.39.0 (commit fake)"\n')
    fake_bin.chmod(0o755)
    (z0 / 'config').mkdir(parents=True)
    env = {**os.environ, 'HOME': str(home), 'Z0INT_HOME': str(z0), 'AGENTSVIEW_DATA_DIR': str(av),
           'Z0INT_MEMORY_SOURCE_INGEST': 'references', 'Z0_E2E_TDB_TOKEN': TOKEN, 'CLAUDE_CONFIG_DIR': str(claude_home)}
    os.environ.update(env)

    sys.path.insert(0, str(WT / 'src'))
    from z0int.memory import surface as ms
    from z0int.memory_contract import BitemporalClaim, EventIdentity, MemoryScope

    z0scope = MemoryScope(user='local', project='z0')
    for cid, value, at in (('relay-old', 'unix:/run/old.sock', '2026-09-01T00:00:00Z'),
                           ('relay-new', 'unix:/run/walrus.sock', '2026-10-02T00:00:00Z')):
        ms.record_claim(BitemporalClaim(claim_id=cid, scope=z0scope, subject='walrus relay', predicate='socket',
                                        value=value, status='observed', observed_at=at, recorded_at=at))
    hit = next(e for e in ms.search('walrus relay bench', layers=('lexical',), config={})['evidence']
               if e['session_id'] == 'codex:e3')
    ref = ms.ingest_reference(EventIdentity(**hit['identity']), hit['locator'], scope=z0scope)
    again = ms.ingest_reference(EventIdentity(**hit['identity']), hit['locator'], scope=z0scope)
    check('ledger: reference ingest is idempotent on event_uid', again.event_id == ref.event_id)
    events_before = (z0 / 'memory' / 'events.jsonl').read_bytes()

    items = [{'id': 'tdb-dup', 'content': 'walrus relay bench copy', 'source_event_ids': [hit['event_uid']]},
             {'id': 'tdb-loose', 'content': 'walrus relay recollection without provenance'}]
    with FakeTencentDB(token=TOKEN, items=items, port=PORT) as gw:  # real gateway response shapes (no revision)
        (z0 / 'config' / 'memory.json').write_text(json.dumps(
            {'tencentdb': {'url': gw.url, 'auth_env': 'Z0_E2E_TDB_TOKEN', 'deadline_ms': 500}}))
        print(f'fake TencentDB on {gw.url}')
        out = mcp([{'jsonrpc': '2.0', 'id': 1, 'method': 'initialize', 'params': {}},
                   {'jsonrpc': '2.0', 'method': 'notifications/initialized'},
                   {'jsonrpc': '2.0', 'id': 2, 'method': 'tools/list'},
                   call(3, 'memory_search', {'query': 'walrus relay', 'project': 'z0', 'harness': 'hermes',
                                             'cross_harness': True, 'limit': 20}),
                   call(4, 'orient', {'query': 'walrus relay socket', 'project': 'z0'}),
                   call(5, 'history', {'subject': 'walrus relay', 'predicate': 'socket', 'project': 'z0'}),
                   call(6, 'verify', {'query': 'walrus relay', 'project': 'z0', 'requires': ['unix socket']}),
                   call(7, 'verify', {'query': 'walrus relay', 'project': 'z0', 'requires': ['kubernetes']}),
                   call(8, 'inspect', {'locator': next(e['locator'] for e in ms.search(
                       'deploy notes', layers=('lexical',), config={})['evidence'] if e['session_id'] == 'claude:e2')}),
                   call(9, 'unknowns', {'query': 'walrus relay', 'project': 'z0'}),
                   call(10, 'memory_search', {'query': 'walrus relay', 'limit': 20}),
                   call(11, 'memory_search', {'query': 'deploy notes', 'project': 'z0'})], env)
        stdout_a = json.dumps(out)
        check('mcp: one response per request (notification silent)', [o['id'] for o in out] == list(range(1, 12)))
        check('mcp: serverInfo is z0-memory', out[0]['result']['serverInfo']['name'] == 'z0-memory')
        names = {t['name'] for t in out[1]['result']['tools']}
        check('mcp: tools/list is exactly the memory tools',
              names == {'memory_search', 'orient', 'inspect', 'history', 'unknowns', 'verify'}, str(sorted(names)))
        err, s = body(out[2])
        ev = s['evidence']
        harnesses = {e['harness'] for e in ev}
        check('search: cross-harness recall returns claude-code, codex and omp evidence for a hermes query',
              {'claude-code', 'codex', 'omp', 'hermes'} <= harnesses, str(sorted(h or '-' for h in harnesses)))
        check('search: sibling project rejected', all(e.get('session_id') != 'claude:e6' for e in ev))
        lex = [e for e in ev if 'lexical' in e['layers']]
        check('provenance: every lexical hit has event_uid, agentsview:<sid>#<mid>, harness, session, timestamp',
              all(e['event_uid'].startswith('evt_') and re.fullmatch(r'agentsview:.+#\d+', e['locator'])
                  and e['harness'] and e['session_id'] and e['timestamp'] and e['identity']['source_system']
                  for e in lex), f'{len(lex)} lexical hits')
        uids = [e['event_uid'] for e in ev]
        check('dedupe: no duplicate event_uid across layers', len(uids) == len(set(uids)))
        check('scope: semantic items (no canonical scope) are rejected before ranking under a project scope',
              not any('semantic' in e['layers'] for e in ev) and s['out_of_scope'] >= 2, f"out_of_scope={s['out_of_scope']}")
        check('semantic: layer ok through the bearer-checked stub', s['layers']['semantic']['status'] == 'ok')
        err, s = body(out[9])  # unscoped: the three layers meet
        ev = s['evidence']
        uids = [e['event_uid'] for e in ev]
        check('dedupe (unscoped): no duplicate event_uid across temporal, lexical and semantic', len(uids) == len(set(uids)))
        merged = next((e for e in ev if e['event_uid'] == hit['event_uid']), {})
        check('dedupe: the ingested reference and the TencentDB copy merge into the lexical hit',
              {'lexical', 'temporal', 'semantic'} <= set(merged.get('layers', [])), str(merged.get('layers')))
        check('semantic: bearer came from the configured env var', gw.auth_headers and
              all(h == f'Bearer {TOKEN}' for h in gw.auth_headers), f'{len(gw.auth_headers)} POSTs')
        loose = [e for e in ev if e.get('semantic_id') == 'tdb-loose']
        check('semantic: item without source_event_ids has provenance_ok=false (not canonical provenance)',
              bool(loose) and loose[0]['provenance_ok'] is False and s['layers']['semantic']['canonical_provenance'] == 1)
        err, s_sec = body(out[10])
        check('scrub: memory_search over the secret-bearing message reports scrubbed spans',
              not err and s_sec['scrubbed'] >= 5, f"scrubbed={s_sec['scrubbed']}")
        err, brief = body(out[3])
        check('brief: within token bound and not abstained', not err and brief['tokens'] <= brief['max_tokens']
              and not brief['abstained'], f"tokens={brief['tokens']}")
        check('supersession: brief shows the newer claim as current',
              'unix:/run/walrus.sock' in brief['text'] and 'old.sock' not in brief['text'])
        err, hist = body(out[4])
        check('supersession: history returns both versions',
              [h['claim_id'] for h in hist['history']] == ['relay-old', 'relay-new']
              and hist['history'][0]['superseded_by'] == 'relay-new', json.dumps([h['claim_id'] for h in hist['history']]))
        check('verify: USE when grounded, FALLBACK when not',
              body(out[5])[1]['verdict'] == 'USE' and body(out[6])[1]['verdict'] == 'FALLBACK')
        err, insp = body(out[7])
        check('inspect: reads the secret-bearing message scrubbed', not err and 'deploy notes' in json.dumps(insp)
              and '[redacted:credential]' in json.dumps(insp))
        err, unk = body(out[8])
        check('unknowns: answerable with no gaps while every layer is up', unk['answerable'] and not unk['unresolved_gaps'])
        check('scrub: no secret-shaped fixture string, bearer or config value in any MCP response', no_secrets(stdout_a))
        # round 2: the real gateway has no data revision, so with it reachable the brief cache is bypassed
        revs = ms.source_revisions(config=json.loads((z0 / 'config' / 'memory.json').read_text()))
        check('r2 semantic: a reachable gateway is unversioned in the snapshot, never its software version',
              revs['tencentdb'] == 'unversioned' and gw.version not in json.dumps(revs), revs['tencentdb'])
        check('r2 cache: with the gateway reachable the first orient bypasses the cache',
              brief['cache'] == 'bypass:tencentdb_unversioned', brief['cache'])
        out_s1 = mcp([call(1, 'orient', {'query': 'walrus relay lantern', 'limit': 20})], env)  # unscoped: semantic admitted
        err, brief_s1 = body(out_s1[0])
        gw.items.append({'id': 'tdb-new', 'content': 'walrus relay lantern fresh gateway memory', 'version': 3,
                         'score': 100.0, 'updated_at': '2026-10-03T12:00:00.000Z'})
        out_s2 = mcp([call(1, 'orient', {'query': 'walrus relay lantern', 'limit': 20})], env)  # second process
        err, brief_s2 = body(out_s2[0])
        check('r2 stale: only the gateway content changed (AgentsView, ledger, repo unchanged); the next brief in a '
              'second process carries the new item and is not a cache hit',
              'tencentdb:tdb-new' not in brief_s1['evidence'] and 'tencentdb:tdb-new' in brief_s2['evidence']
              and brief_s2['cache'] != 'hit' and brief_s2['memory_snapshot_id'] == brief_s1['memory_snapshot_id'],
              f"cache={brief_s2['cache']}")
        cfg = json.loads((z0 / 'config' / 'memory.json').read_text())
        snap_before = ms.memory_snapshot_id(config=cfg)
        gw.version = '0.9.2'
        check('r2 semantic: a gateway build change is not a data revision (snapshot id unchanged)',
              ms.memory_snapshot_id(config=cfg) == snap_before)
        err, s_ref = body(mcp([call(1, 'memory_search', {'query': 'walrus relay lantern', 'limit': 20})], env)[0])
        ref = next((e['evidence_ref'] for e in s_ref['evidence'] if e['locator'] == 'tencentdb:tdb-new'), {})
        check('r2 semantic: evidence_ref.source_version is the item version@updated_at',
              ref.get('source_version') == 'v3@2026-10-03T12:00:00.000Z', str(ref.get('source_version')))
        out_doc = subprocess.run([PY, '-m', 'z0int.cli', 'memory', 'doctor', '--json', '--av-dir', str(av),
                                  '--agentsview-bin', str(fake_bin)], capture_output=True, text=True, timeout=120,
                                 env=env)
        doc = out_doc.stdout + out_doc.stderr
        tdb = next((c for c in json.loads(doc[:doc.rindex('}') + 1])['checks'] if c['name'] == 'tencentdb'), {})
        check('r2 doctor: a reachable real-shape gateway is reported available (unversioned), no crash',
              tdb.get('status') == 'reachable' and 'unversioned' in tdb.get('detail', ''), str(tdb.get('detail')))
    # the persistent snapshot cache, with the gateway not configured (a versioned view: AgentsView + ledger + repo)
    (z0 / 'config' / 'memory.json').write_text('{}')
    out_a = mcp([call(1, 'orient', {'query': 'walrus relay socket', 'project': 'z0'})], env)
    err, brief_a = body(out_a[0])
    out_b = mcp([call(1, 'orient', {'query': 'walrus relay socket', 'project': 'z0'})], env)
    err, brief_b = body(out_b[0])
    check('cache: first orient was a miss, a second process with the same snapshot id hits the persistent cache',
          brief_a['cache'] == 'miss' and brief_b['cache'] == 'hit'
          and brief_b['memory_snapshot_id'] == brief_a['memory_snapshot_id'] and brief_b['text'] == brief_a['text'])
    conn = sqlite3.connect(av / 'sessions.db')
    conn.execute('insert into messages (session_id, ordinal, role, content, timestamp) values (?,?,?,?,?)',
                 ('claude:e2', 9, 'user', 'walrus relay socket moved again', iso(time.time())))
    conn.commit()
    conn.close()
    out_c = mcp([call(1, 'orient', {'query': 'walrus relay socket', 'project': 'z0'})], env)
    err, brief_c = body(out_c[0])
    check('cache: an AgentsView change gives a new snapshot id and a fresh brief',
          brief_c['memory_snapshot_id'] != brief_a['memory_snapshot_id'] and brief_c['cache'] == 'miss')

    check('ledger: events.jsonl unchanged by every read tool', (z0 / 'memory' / 'events.jsonl').read_bytes() == events_before)
    receipts = json.dumps([brief['receipt'], brief_b['receipt'], brief_c['receipt']])
    check('scrub: MemoryUseReceipt rows carry no secret', no_secrets(receipts) and no_secrets(events_before.decode()))

    # network namespace removed: TencentDB unreachable, everything else answers
    unshare = shutil.which('unshare')
    if unshare:
        out_n = mcp([call(1, 'memory_search', {'query': 'walrus relay', 'project': 'z0'})], env, prefix=[unshare, '-rn'])
        if out_n:
            err, s_n = body(out_n[0])
            check('offline: memory_search answers with the network disabled (unshare -rn)',
                  not err and s_n['evidence'] and s_n['layers']['semantic']['status'] == 'unavailable',
                  f"semantic={s_n['layers']['semantic'].get('reason')}")
        else:
            check('offline: unshare -rn run produced output', False)

    # ---------------------------------------------------------------- fix round (verifier REVISE findings)
    val = 'Fk3' + 'q9ZxR7vLm2Tb8NcW4yHd'  # synthetic credential body, assembled at runtime
    opaque = 'zq' + 'Opaque7Credential9Value'  # matches no regex: only the AgentsView finding can blank it
    shapes = ['XAI_API_KEY=xai-' + val, 'GROQ_API_KEY=gsk_' + val, 'OPENROUTER_API_KEY=' + val, 'HF_TOKEN=hf_' + val,
              '{"api_key": "' + val + '"}', '{"access_token": "' + val + '"}', 'github_pat_11ABCDEFG0' + val]
    prefix = 'walrus envdump café vault '
    conn = sqlite3.connect(av / 'sessions.db')
    conn.execute('insert into messages (session_id, ordinal, role, content, timestamp) values (?,?,?,?,?)',
                 ('claude:e2', 2, 'assistant', prefix + opaque + ' ' + ' '.join(shapes), iso(time.time())))
    start = len(prefix.encode())
    conn.execute('insert into secret_findings (session_id, rule_name, confidence, location_kind, message_ordinal, '
                 'match_start, match_end, match_index, redacted_match, rules_version) values (?,?,?,?,?,?,?,?,?,?)',
                 ('claude:e2', 'generic', 'definite', 'message', 2, start, start + len(opaque), 0, '****', 'e2e'))
    filler = 'walrus straddle ' + 'x' * 380 + ' '
    conn.execute('insert into messages (session_id, ordinal, role, content, timestamp) values (?,?,?,?,?)',
                 ('omp:e4', 1, 'assistant', filler + SECRETS['aws'] + ' tail', iso(time.time())))
    conn.commit()
    straddle_mid = conn.execute("select id from messages where session_id='omp:e4' and ordinal=1").fetchone()[0]
    env_mid = conn.execute("select id from messages where session_id='claude:e2' and ordinal=2").fetchone()[0]
    conn.close()
    from z0int.memory.event_log import EventLog
    note = EventLog(z0 / 'memory').append('memory.note', {'text': 'walrus ' + 'y' * 380 + ' ' + SECRETS['aws']},
                                          source='z0')
    note_raw = json.dumps(note.payload, ensure_ascii=False)
    out_f = mcp([call(1, 'memory_search', {'query': 'walrus envdump vault', 'project': 'z0'}),
                 call(2, 'inspect', {'locator': f'agentsview:claude:e2#{env_mid}', 'context': 0, 'chars': 4000}),
                 call(3, 'inspect', {'locator': f'agentsview:omp:e4#{straddle_mid}', 'context': 0,
                                     'chars': len(filler) + 12}),
                 call(4, 'inspect', {'locator': f'eventlog:{note.event_id}',
                                     'chars': note_raw.index(SECRETS['aws']) + 12}),
                 {'jsonrpc': '2.0', 'id': 5, 'method': 'tools/list'}], env)
    blob = json.dumps(out_f)
    err, s_env = body(out_f[0])
    check('fix scrub: env-dump/JSON/github_pat shapes never reach memory_search or inspect',
          not err and 'envdump' in blob and val not in blob, f"scrubbed={s_env['scrubbed']}")
    err, insp_env = body(out_f[1])
    check('fix scrub: the AgentsView secret_findings span (byte offsets, after non-ASCII) is blanked',
          not err and opaque not in blob and insp_env['messages'][0]['text'].startswith(prefix + '[redacted:secret]'))
    err3, insp3 = body(out_f[2])
    err4, insp4 = body(out_f[3])
    check('fix inspect: a credential straddling the chars bound never leaks (agentsview: and eventlog:)',
          not err3 and not err4 and SECRETS['aws'][:12] not in json.dumps([insp3, insp4]),
          f"tail={insp3['messages'][0]['text'][-14:]!r} / {insp4['payload'][-14:]!r}")
    proj = [t['inputSchema']['properties']['project'].get('description', '') for t in out_f[4]['result']['tools']
            if 'project' in t['inputSchema']['properties']]
    check('fix mcp: every scoped tool documents that an omitted project spans all projects',
          proj and all('all projects' in d for d in proj), f'{len(proj)} tools')

    with FakeTencentDB(token=TOKEN, items=[{'id': 'h', 'content': 'walrus'}], sleep_s=3.0, port=PORT + 1) as hung:
        (z0 / 'config' / 'memory.json').write_text(json.dumps(
            {'tencentdb': {'url': hung.url, 'auth_env': 'Z0_E2E_TDB_TOKEN', 'deadline_ms': 200}}))
        print(f'hung fake TencentDB on {hung.url} (sleeps 3 s on every endpoint)')
        pol = ms.ScopePolicy(scope=z0scope)
        t0 = time.monotonic()
        s_h = ms.search('walrus relay', pol)
        t_search = time.monotonic() - t0
        t0 = time.monotonic()
        b_h = ms.memory_brief('walrus relay hung', pol)
        t_brief = time.monotonic() - t0
        check('fix deadline: search against a gateway hung on /health and search returns within deadline+50 ms',
              t_search < 0.25 and s_h['layers']['semantic'].get('reason') == 'timeout' and s_h['evidence'],
              f'{t_search * 1000:.0f} ms')
        check('fix deadline: memory_brief (snapshot probe + search) stays within deadline+50 ms',
              t_brief < 0.25 and not b_h['abstained'] and any('semantic' in g for g in b_h['gaps']),
              f'{t_brief * 1000:.0f} ms')
        out_h = mcp([call(1, 'memory_search', {'query': 'walrus relay', 'project': 'z0'}),
                     call(2, 'orient', {'query': 'walrus relay hung mcp', 'project': 'z0'})], env)
        (e1, r1), (e2, r2) = body(out_h[0]), body(out_h[1])
        check('fix deadline: over MCP the hung gateway is semantic=unavailable(timeout), lexical evidence still served',
              not e1 and r1['layers']['semantic'].get('reason') == 'timeout' and r1['evidence'] and not e2
              and not r2['abstained'], f"search latency_ms={r1['latency_ms']}")
        repo = HOME / 'repo'
        repo.mkdir()
        genv = {**env, 'GIT_AUTHOR_NAME': 't', 'GIT_AUTHOR_EMAIL': 't@t', 'GIT_COMMITTER_NAME': 't',
                'GIT_COMMITTER_EMAIL': 't@t'}
        subprocess.run(['git', '-C', str(repo), 'init', '-q'], check=True, env=genv)
        subprocess.run(['git', '-C', str(repo), 'commit', '-q', '--allow-empty', '-m', 'one'], check=True, env=genv)
        from z0int.state_packet import build_state_packet
        before = list(hung.requests)
        t0 = time.monotonic()
        pk = build_state_packet(repo, use_cache=False, store=False, adapters=('git',), projects_root=HOME / 'none')
        t_pk = time.monotonic() - t0
        check('fix hook path: build_state_packet makes no gateway call (configured, hung gateway)',
              hung.requests == before and pk.get('memory_snapshot_id', '').startswith('mem_'),
              f'{t_pk * 1000:.0f} ms, gateway requests during build: {len(hung.requests) - len(before)}')

    from z0int.context_resolve import InformationNeed, resolve_context
    (z0 / 'config' / 'memory.json').write_text('{}')
    need = [InformationNeed(id='m1', description='walrus relay', kind='memory')]
    no_turn = resolve_context(needs=need, use_cache=False, allow_qmd=False, allow_memory=True)
    with_turn = resolve_context(needs=need, use_cache=False, allow_qmd=False, allow_memory=True, turn_key='e2e-t1')
    check('fix guard: allow_memory without a turn_key skips memory with an explicit double_inject_guard gap',
          not no_turn.evidence and any('double_inject_guard' in g and 'turn_key' in g for g in no_turn.unresolved_gaps)
          and with_turn.evidence)

    # doctor: fresh passes, stale fails, never prints a secret
    def doctor(av_dir: Path) -> tuple[int, str]:
        p = subprocess.run([PY, '-m', 'z0int.cli', 'memory', 'doctor', '--json', '--av-dir', str(av_dir),
                            '--agentsview-bin', str(fake_bin)], capture_output=True, text=True, timeout=120, env=env)
        return p.returncode, p.stdout + p.stderr

    (z0 / 'config' / 'memory.json').unlink()
    rc, text = doctor(av)
    rep = json.loads(text[:text.rindex('}') + 1])
    print('doctor fresh:', json.dumps({c['name']: [c['ok'], c['detail']] for c in rep['checks']}))
    check('doctor: passes on the fresh fixture (exit 0); TencentDB reported not_configured',
          rc == 0 and rep['ok'] and next(c for c in rep['checks'] if c['name'] == 'tencentdb')['status'] == 'not_configured')
    check('doctor: output has no config secret value', no_secrets(text))
    stale = HOME / 'av-stale'
    build_db(stale / 'sessions.db', age_hours=96)
    shutil.copy(av / 'config.toml', stale / 'config.toml')
    rc, text = doctor(stale)
    rep = json.loads(text[:text.rindex('}') + 1])
    check('doctor: fails on the stale fixture (exit 1, freshness)', rc == 1 and not next(
        c for c in rep['checks'] if c['name'] == 'freshness')['ok'])

    # abstention: required source removed
    (av / 'sessions.db').unlink()
    out_d = mcp([call(1, 'orient', {'query': 'walrus relay socket', 'project': 'z0'}),
                 call(2, 'memory_search', {'query': 'walrus relay', 'project': 'z0'})], env)
    err, brief_d = body(out_d[0])
    check('abstain: brief abstains with an explicit gap when AgentsView is removed',
          brief_d['abstained'] and any('lexical' in g and 'missing' in g for g in brief_d['gaps'])
          and 'unix socket' not in brief_d['text'])
    err2, text2 = body(out_d[1])
    check('abstain: memory_search is isError (unavailable, missing), never an empty success',
          err2 and 'unavailable' in text2 and 'missing' in text2)
    print(f'RESULT {"PASS" if not FAILS else "FAIL"} ({len(FAILS)} failed: {FAILS})')
    return 1 if FAILS else 0


if __name__ == '__main__':
    raise SystemExit(main())
