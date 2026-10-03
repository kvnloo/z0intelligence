"""The z0 memory surface over AgentsView (lexical), the EventLog (temporal) and TencentDB (semantic).

Synthetic fixtures only; the TencentDB gateway is a loopback stub with a fake bearer value.
"""
from __future__ import annotations

import json
import logging
import os
import sqlite3
import subprocess
import sys
import time

import pytest

from memory_fixture import FakeTencentDB, add_message, build_av_db, message_id
from z0int import agentsview_ro
from z0int.memory import surface as ms
from z0int.memory.event_log import EventLog
from z0int.memory_contract import BitemporalClaim, EventIdentity, MemoryScope, derive_event_uid

Z0 = MemoryScope(user='local', project='z0')
TOKEN = 'fake-tdb-bearer-value-123'


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv('Z0INT_HOME', str(tmp_path / 'z0'))
    monkeypatch.setenv('AGENTSVIEW_DATA_DIR', str(tmp_path / 'av'))
    monkeypatch.setenv('Z0INT_MEMORY_SOURCE_INGEST', 'references')
    monkeypatch.setenv('Z0_TEST_TDB_TOKEN', TOKEN)
    build_av_db(tmp_path / 'av' / 'sessions.db')
    return tmp_path


def lexical(query, policy=None, **kw):
    return ms.search(query, policy, layers=('lexical',), config={}, **kw)


# ----------------------------------------------------------------------------- 2. AgentsView capability
def test_agentsview_hit_carries_evidence_ref_and_event_identity(env, monkeypatch):
    mids = {o: message_id(env / 'av' / 'sessions.db', 'h1', o) for o in (0, 1)}
    uris = []
    real = agentsview_ro.sqlite3.connect
    monkeypatch.setattr(agentsview_ro.sqlite3, 'connect', lambda target, *a, **k: uris.append(target) or real(target, *a, **k))
    res = lexical('quokka gateway', ms.ScopePolicy(scope=Z0, requester='hermes', cross_harness=False))
    assert res['ok'] and res['layers']['lexical']['status'] == 'ok'
    hit = res['evidence'][0]
    mid = mids[hit['ordinal']]
    assert hit['locator'] == hit['evidence_ref']['source_id'] == f'agentsview:h1#{mid}'
    assert hit['evidence_ref']['trust_class'] == 'conversation'
    ident = hit['identity']
    assert (ident['source_system'], ident['source_session'], ident['source_event_id']) == ('hermes', 'h1', str(mid))
    assert hit['event_uid'] == ident['event_uid']
    replay = lexical('quokka gateway', ms.ScopePolicy(scope=Z0, requester='hermes', cross_harness=False))
    assert [e['event_uid'] for e in replay['evidence']] == [e['event_uid'] for e in res['evidence']]
    assert uris and all('mode=ro' in u for u in uris)


def test_fts_hits_carry_the_canonical_event_uid_the_ledger_assigns(env):
    hit = next(e for e in lexical('quokka bench')['evidence'] if e['session_id'] == 'x1')
    assert hit['event_uid'] == derive_event_uid(source_system='codex', source_session='x1',
                                                source_event_id=hit['message_id'])
    event = ms.ingest_reference(EventIdentity(**{k: v for k, v in hit['identity'].items()}), hit['locator'])
    assert event.event_identity().event_uid == hit['event_uid']


# ----------------------------------------------------------------------------- 3. scope before ranking
def test_sibling_scope_is_rejected_before_ranking(env):
    seen = []

    def ranker(items):
        seen.extend(items)
        return items

    res = lexical('quokka', ms.ScopePolicy(scope=Z0, requester='hermes', cross_harness=True), ranker=ranker)
    assert seen and all(i['scope']['project'] == 'z0' for i in seen)
    assert 'sib' not in {e['session_id'] for e in res['evidence']}


def test_sibling_repo_and_task_claims_are_rejected_before_ranking(env):
    def claim(cid, scope, value):
        return BitemporalClaim(claim_id=cid, scope=scope, subject='quokka', predicate='port', value=value,
                               status='observed', observed_at='2026-10-01T00:00:00Z',
                               recorded_at='2026-10-01T00:00:00Z')

    repo_a = MemoryScope(user='local', project='z0', repo='a')
    ms.record_claim(claim('c-a', repo_a, 1))
    ms.record_claim(claim('c-b', MemoryScope(user='local', project='z0', repo='b'), 2))
    ms.record_claim(claim('c-t', MemoryScope(user='local', project='z0', repo='a', task='t2'), 3))
    seen = []
    res = ms.search('quokka port', ms.ScopePolicy(scope=MemoryScope(user='local', project='z0', repo='a', task='t1')),
                    layers=('temporal',), config={}, ranker=lambda items: seen.extend(items) or items)
    assert {i['claim_id'] for i in seen if i.get('claim_id')} == {'c-a'}
    assert {e.get('claim_id') for e in res['evidence']} == {'c-a'}


# ----------------------------------------------------------------------------- 4. unavailable, never empty success
def test_missing_db_is_unavailable_with_a_reason(env):
    (env / 'av' / 'sessions.db').unlink()
    res = lexical('quokka')
    assert res['layers']['lexical'] == {**res['layers']['lexical'], 'status': 'unavailable', 'reason': 'missing'}
    assert res['ok'] is False and res['evidence'] == []


def test_schema_mismatch_is_unavailable(env, tmp_path):
    build_av_db(tmp_path / 'v75' / 'sessions.db', user_version=75)
    res = lexical('quokka', av_db=tmp_path / 'v75' / 'sessions.db')
    assert (res['ok'], res['layers']['lexical']['status'], res['layers']['lexical']['reason']) == (False, 'unavailable', 'schema')
    build_av_db(tmp_path / 'nofts' / 'sessions.db', fts=False)
    res = lexical('quokka', av_db=tmp_path / 'nofts' / 'sessions.db')
    assert (res['ok'], res['layers']['lexical']['reason']) == (False, 'schema')


def test_locked_db_is_unavailable(env):
    holder = sqlite3.connect(env / 'av' / 'sessions.db', isolation_level=None)
    holder.execute('begin exclusive')
    try:
        res = lexical('quokka', av_timeout=0.1)
    finally:
        holder.execute('rollback')
        holder.close()
    assert (res['ok'], res['layers']['lexical']['status'], res['layers']['lexical']['reason']) == (False, 'unavailable', 'locked')


# ----------------------------------------------------------------------------- 5. TencentDB
def test_semantic_layer_is_unavailable_when_not_configured(env, monkeypatch):
    def no_network(*a, **k):
        raise AssertionError('no gateway configured: nothing may connect')

    monkeypatch.setattr('socket.socket.connect', no_network)
    res = ms.search('quokka', layers=('semantic',), config={})
    assert res['layers']['semantic']['status'] == 'unavailable'
    assert res['layers']['semantic']['reason'] == 'not_configured'


def test_slow_gateway_returns_unavailable_within_the_deadline(env):
    with FakeTencentDB(token=TOKEN, items=[{'id': 'm', 'content': 'quokka'}], sleep_s=1.5) as gw:
        t0 = time.monotonic()
        res = ms.search('quokka', layers=('semantic',), config=gw.config(deadline_ms=200))
        elapsed = time.monotonic() - t0
    assert res['layers']['semantic']['status'] == 'unavailable'
    assert res['layers']['semantic']['reason'] == 'timeout'
    assert elapsed < 0.2 + 0.05


def test_hung_gateway_bounds_search_and_brief_end_to_end(env):
    """One deadline budget per query: a gateway hung on /health and on search costs at most the deadline,
    however many semantic round-trips (revision probe + search) the call needs."""
    with FakeTencentDB(token=TOKEN, items=[{'id': 'm', 'content': 'quokka'}], sleep_s=1.5) as gw:
        cfg = gw.config(deadline_ms=200)
        t0 = time.monotonic()
        res = ms.search('quokka gateway', ms.ScopePolicy(scope=Z0), config=cfg)
        searched = time.monotonic() - t0
        t0 = time.monotonic()
        brief = ms.memory_brief('quokka gateway', ms.ScopePolicy(scope=Z0), config=cfg)
        briefed = time.monotonic() - t0
    assert (res['layers']['semantic']['status'], res['layers']['semantic']['reason']) == ('unavailable', 'timeout')
    assert res['layers']['lexical']['status'] == 'ok' and res['evidence']
    assert searched < 0.2 + 0.05, searched
    assert brief['abstained'] is False and any(g.startswith('semantic: unavailable') for g in brief['gaps'])
    assert briefed < 0.2 + 0.05, briefed


def test_a_query_without_the_semantic_layer_never_calls_the_gateway(env):
    with FakeTencentDB(token=TOKEN) as gw:
        res = ms.search('quokka', ms.ScopePolicy(scope=Z0), layers=('lexical', 'temporal'), config=gw.config())
    assert res['ok'] and gw.requests == []


def test_bearer_comes_from_the_configured_env_var_and_is_never_logged(env, caplog, capsys):
    caplog.set_level(logging.DEBUG)
    items = [{'id': 'm1', 'content': 'quokka semantic memory without provenance'}]
    with FakeTencentDB(token=TOKEN, items=items) as gw:
        res = ms.search('quokka', layers=('semantic',), config=gw.config())
    assert gw.auth_headers == [f'Bearer {TOKEN}']
    assert res['layers']['semantic']['status'] == 'ok'
    out = capsys.readouterr()
    for blob in (json.dumps(res), caplog.text, out.out, out.err, repr(ms.TencentDBClient(gw.config()))):
        assert TOKEN not in blob


def test_items_without_source_event_ids_are_not_canonical_provenance(env):
    uid = derive_event_uid(source_system='hermes', source_session='s9', source_event_id='9')
    items = [{'id': 'm1', 'content': 'quokka loose memory'},
             {'id': 'm2', 'content': 'quokka grounded memory', 'source_event_ids': [uid]}]
    with FakeTencentDB(token=TOKEN, items=items) as gw:
        res = ms.search('quokka', layers=('semantic',), config=gw.config())
    by_id = {e['semantic_id']: e for e in res['evidence']}
    assert by_id['m1']['provenance_ok'] is False and by_id['m2']['provenance_ok'] is True
    assert by_id['m2']['event_uid'] == uid
    assert res['layers']['semantic']['canonical_provenance'] == 1


def test_semantic_duplicates_of_agentsview_hits_are_removed(env):
    hit = next(e for e in lexical('quokka bench')['evidence'] if e['session_id'] == 'x1')
    items = [{'id': 'dup', 'content': 'quokka bench copy', 'source_event_ids': [hit['event_uid']]}]
    with FakeTencentDB(token=TOKEN, items=items) as gw:
        res = ms.search('quokka bench', layers=('lexical', 'semantic'), config=gw.config())
    uids = [e['event_uid'] for e in res['evidence']]
    assert len(uids) == len(set(uids))
    assert 'dup' not in {e.get('semantic_id') for e in res['evidence']}
    assert res['duplicates_removed'] >= 1


# ----------------------------------------------------------------------------- 7. cross-product recall
def test_hermes_query_with_cross_harness_policy_recalls_claude_codex_and_omp(env):
    policy = ms.ScopePolicy(scope=Z0, requester='hermes', cross_harness=True)
    res = lexical('quokka', policy, limit=20)
    harnesses = {e['harness'] for e in res['evidence']}
    assert {'claude-code', 'codex', 'omp'} <= harnesses
    for e in res['evidence']:
        assert e['source_system'] and e['harness'] and e['session_id'] and e['timestamp'] and e['locator']
    again = lexical('quokka', policy, limit=20)
    assert sorted(e['locator'] for e in again['evidence']) == sorted(e['locator'] for e in res['evidence'])
    own = lexical('quokka', ms.ScopePolicy(scope=Z0, requester='hermes', cross_harness=False), limit=20)
    assert {e['harness'] for e in own['evidence']} == {'hermes'}


# ----------------------------------------------------------------------------- 9. one query across all layers
def test_one_query_resolves_temporal_lexical_and_semantic_without_duplicates(env):
    lex = next(e for e in lexical('quokka cache')['evidence'] if e['session_id'] == 'c1')
    ms.ingest_reference(EventIdentity(**lex['identity']), lex['locator'])
    EventLog().append('memory.note', {'text': 'quokka cache was moved last week'}, source='z0')
    items = [{'id': 'dup', 'content': 'quokka cache', 'source_event_ids': [lex['event_uid']]},
             {'id': 'own', 'content': 'quokka cache semantic summary',
              'source_event_ids': [derive_event_uid(source_system='omp', source_session='o9', source_event_id='1')]}]
    with FakeTencentDB(token=TOKEN, items=items) as gw:
        res = ms.search('quokka cache', config=gw.config(), limit=20)
    uids = [e['event_uid'] for e in res['evidence']]
    assert len(uids) == len(set(uids))
    layers = {layer for e in res['evidence'] for layer in e['layers']}
    assert layers == {'temporal', 'lexical', 'semantic'}
    merged = next(e for e in res['evidence'] if e['event_uid'] == lex['event_uid'])
    assert {'lexical', 'temporal'} <= set(merged['layers'])
    assert res['duplicates_removed'] >= 1


# ----------------------------------------------------------------------------- 11. memory_snapshot_id
def _git(repo, *args):
    subprocess.run(['git', '-C', str(repo), *args], check=True, capture_output=True,
                   env={**os.environ, 'GIT_AUTHOR_NAME': 't', 'GIT_AUTHOR_EMAIL': 't@t', 'GIT_COMMITTER_NAME': 't',
                        'GIT_COMMITTER_EMAIL': 't@t'})


def test_memory_snapshot_id_tracks_every_source_revision(env, tmp_path):
    repo = tmp_path / 'repo'
    repo.mkdir()
    _git(repo, 'init', '-q')
    _git(repo, 'commit', '-q', '--allow-empty', '-m', 'one')
    with FakeTencentDB(token=TOKEN) as gw:
        cfg = gw.config()
        first = ms.memory_snapshot_id(repo=repo, config=cfg)
        assert ms.memory_snapshot_id(repo=repo, config=cfg) == first
        add_message(env / 'av' / 'sessions.db', 'h1', 'a new quokka message')
        second = ms.memory_snapshot_id(repo=repo, config=cfg)
        assert second != first
        EventLog().append('memory.note', {'text': 'quokka ledger note'}, source='z0')
        third = ms.memory_snapshot_id(repo=repo, config=cfg)
        assert third != second
        gw.version = '0.9.2'  # a gateway build is not a data revision: the snapshot must not key on it
        assert ms.memory_snapshot_id(repo=repo, config=cfg) == third
        _git(repo, 'commit', '-q', '--allow-empty', '-m', 'two')
        fourth = ms.memory_snapshot_id(repo=repo, config=cfg)
        assert fourth != third
        assert ms.memory_snapshot_id(repo=repo, config=cfg) == fourth


def test_state_packet_and_decision_opportunity_carry_the_snapshot_id(env, tmp_path):
    from z0int.decision_opportunity import build_decision_opportunity
    from z0int.state_packet import build_state_packet

    repo = tmp_path / 'repo'
    repo.mkdir()
    _git(repo, 'init', '-q')
    _git(repo, 'commit', '-q', '--allow-empty', '-m', 'one')
    packet = build_state_packet(repo, use_cache=False, store=False, adapters=('git',), projects_root=tmp_path / 'none')
    assert packet['memory_snapshot_id'] == ms.memory_snapshot_id(repo=repo)
    opp = build_decision_opportunity(repo, 'where is the quokka cache', packet=packet)
    assert opp['memory_snapshot_id'] == packet['memory_snapshot_id']
    add_message(env / 'av' / 'sessions.db', 'h1', 'another quokka message')
    again = build_state_packet(repo, use_cache=False, store=False, adapters=('git',), projects_root=tmp_path / 'none')
    assert again['memory_snapshot_id'] != packet['memory_snapshot_id']


def test_state_packet_memory_probe_never_calls_the_gateway_on_the_hook_path(env, tmp_path):
    """build_state_packet runs inside synchronous harness hooks: its memory_snapshot_id uses the gateway
    revision last observed by a real query, never a fresh network call."""
    from z0int.state_packet import build_state_packet

    repo = tmp_path / 'repo'
    repo.mkdir()
    _git(repo, 'init', '-q')
    _git(repo, 'commit', '-q', '--allow-empty', '-m', 'one')

    def packet_snapshot():
        return build_state_packet(repo, use_cache=False, store=False, adapters=('git',),
                                  projects_root=tmp_path / 'none')['memory_snapshot_id']

    with FakeTencentDB(token=TOKEN) as gw:
        (env / 'z0' / 'config').mkdir(parents=True, exist_ok=True)
        (env / 'z0' / 'config' / 'memory.json').write_text(json.dumps(gw.config()))
        unprobed = packet_snapshot()
        assert gw.requests == []
        ms.search('quokka', layers=('semantic',))  # a real query observes the gateway (unversioned)
        seen = len(gw.requests)
        probed = packet_snapshot()
        assert probed != unprobed and packet_snapshot() == probed
        assert len(gw.requests) == seen


def test_a_gateway_without_a_data_revision_is_unversioned_not_its_software_version(env):
    """The real gateway reports only its software version on /health and no revision on search."""
    with FakeTencentDB(token=TOKEN, version='0.9.1') as gw:
        revs = ms.source_revisions(config=gw.config())
        client = ms.TencentDBClient(gw.config())
        status, _, _ = client.search('quokka')
    assert revs['tencentdb'] == 'unversioned'
    assert '0.9.1' not in json.dumps(revs) and '0.9.1' not in json.dumps(status)


def test_brief_never_serves_stale_semantic_evidence_when_only_the_gateway_content_changes(env):
    """AgentsView, the ledger and the repo stay unchanged; a new gateway item must reach the next brief."""
    with FakeTencentDB(token=TOKEN) as gw:
        cfg = gw.config()
        first = ms.memory_brief('quokka wombat lantern', ms.ScopePolicy(scope=Z0), config=cfg)
        assert first['abstained'] is False and first['evidence']
        assert not any(e.startswith('tencentdb:') for e in first['evidence'])
        gw.items.append({'id': 'w1', 'content': 'wombat lantern semantic memory', 'version': 2, 'score': 100.0,
                         'updated_at': '2026-10-03T00:00:00.000Z'})
        second = ms.memory_brief('quokka wombat lantern', ms.ScopePolicy(scope=Z0), config=cfg)
        res = ms.search('quokka wombat lantern', ms.ScopePolicy(scope=Z0), config=cfg)
    assert second['cache'] != 'hit'
    assert 'tencentdb:w1' in second['evidence']
    ref = next(e['evidence_ref'] for e in res['evidence'] if e['locator'] == 'tencentdb:w1')
    assert ref['source_version'] == 'v2@2026-10-03T00:00:00.000Z'  # the item's own version, not the gateway's


def test_injection_markers_are_pruned_by_age(env):
    d = env / 'z0' / 'state' / 'memory' / 'injectors'
    assert ms.claim_injection('turn-old', 'a') is True
    old = next(d.glob('*.json'))
    stale = time.time() - 3 * 86400
    os.utime(old, (stale, stale))
    assert ms.claim_injection('turn-new', 'a') is True
    assert not old.exists() and len(list(d.glob('*.json'))) == 1


# ----------------------------------------------------------------------------- 13. memory_brief
def test_brief_stays_within_its_token_bound(env):
    brief = ms.memory_brief('quokka', ms.ScopePolicy(scope=Z0), max_tokens=80, config={})
    assert brief['abstained'] is False and brief['tokens'] <= 80
    assert ms.estimate_tokens(brief['text']) <= 80
    assert brief['receipt']['snapshot_id'] == brief['memory_snapshot_id']


def test_brief_abstains_with_an_explicit_gap_when_a_required_source_is_removed(env):
    (env / 'av' / 'sessions.db').unlink()
    brief = ms.memory_brief('quokka gateway', ms.ScopePolicy(scope=Z0), required=('lexical',), config={})
    assert brief['abstained'] is True
    assert any('lexical' in g and 'missing' in g for g in brief['gaps'])
    assert 'systemd' not in brief['text'] and brief['receipt']['evidence_event_uids'] == []


def test_supersession_shows_the_newer_claim_while_history_returns_both(env):
    def claim(cid, value, at):
        return BitemporalClaim(claim_id=cid, scope=Z0, subject='quokka gateway', predicate='port', value=value,
                               status='observed', observed_at=at, recorded_at=at)

    ms.record_claim(claim('old', 8420, '2026-09-01T00:00:00Z'))
    ms.record_claim(claim('new', 8421, '2026-10-01T00:00:00Z'))
    brief = ms.memory_brief('quokka gateway port', ms.ScopePolicy(scope=Z0), config={})
    current = [c for c in brief['current_claims'] if c['subject'] == 'quokka gateway']
    assert [c['value'] for c in current] == [8421]
    assert '8421' in brief['text'] and '8420' not in brief['text']
    hist = ms.claim_history('quokka gateway', 'port', ms.ScopePolicy(scope=Z0))
    assert [h['claim_id'] for h in hist] == ['old', 'new']
    assert hist[0]['superseded_by'] == 'new' and hist[1]['current'] is True


def test_second_process_with_the_same_snapshot_hits_the_persistent_cache(env):
    first = ms.memory_brief('quokka routing', ms.ScopePolicy(scope=Z0), config={})
    assert first['cache'] == 'miss' and first['abstained'] is False
    code = (
        'import json\n'
        'from z0int.memory import surface as ms\n'
        'from z0int.memory_contract import MemoryScope\n'
        'def boom(*a, **k):\n'
        '    raise AssertionError("resolver called")\n'
        'ms.search = boom\n'
        'b = ms.memory_brief("quokka routing", ms.ScopePolicy(scope=MemoryScope(user="local", project="z0")), config={})\n'
        'print(json.dumps({"cache": b["cache"], "text": b["text"], "sid": b["memory_snapshot_id"]}))\n'
    )
    proc = subprocess.run([sys.executable, '-c', code], capture_output=True, text=True, timeout=60,
                          env={**os.environ, 'PYTHONPATH': os.pathsep.join(p for p in sys.path if p)})
    assert proc.returncode == 0, proc.stderr
    out = json.loads(proc.stdout)
    assert out == {'cache': 'hit', 'text': first['text'], 'sid': first['memory_snapshot_id']}


# ----------------------------------------------------------------------------- canonical ingest cost (#23)
def test_bulk_reference_ingest_reads_each_ledger_line_once_and_duplicates_need_no_scan(env, monkeypatch):
    """One process ingesting n references decodes O(n) ledger lines, not O(n^2); a duplicate is returned from
    the offset it was recorded at, without a full ledger scan."""
    decoded = []
    real = EventLog._decode_committed_line
    monkeypatch.setattr(EventLog, '_decode_committed_line',
                        lambda self, raw, **kw: decoded.append(1) or real(self, raw, **kw))
    idents = [EventIdentity.from_source(source_system='codex', source_session='bulk', source_event_id=str(i),
                                        payload_hash=f'sha256:{i:064x}') for i in range(60)]
    for i, ident in enumerate(idents):
        ms.ingest_reference(ident, f'agentsview:bulk#{i}')
    assert len(decoded) < 3 * len(idents), len(decoded)

    def no_scan(*a, **k):
        raise AssertionError('duplicate ingest scanned the whole ledger')

    monkeypatch.setattr(EventLog, '_scan_locked', no_scan)
    again = ms.ingest_reference(idents[7], 'agentsview:bulk#7')
    assert again.event_identity().event_uid == idents[7].event_uid and again.payload['locator'] == 'agentsview:bulk#7'
