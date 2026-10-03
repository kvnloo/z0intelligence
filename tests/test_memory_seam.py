"""C8: the harness-agnostic memory inject seam (``z0int.memory.seam``) and the Claude-compatible hook entry.

Every harness shim (Claude Code / Codex hooks, Hermes ``pre_llm_call``, OMP/OMO ``context``, DSH ``agent/pre-step``)
calls this one core. Synthetic AgentsView fixture only; the TencentDB gateway is the loopback stub from C7.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from memory_fixture import SECRETS, FakeTencentDB, add_message, build_av_db
from z0int import intelligence_mcp
from z0int.harness_id import turn_key
from z0int.memory import seam
from z0int.memory import surface as ms
from z0int.memory_contract import SCHEMA as RECEIPT_SCHEMA
from z0int.memory_contract import BitemporalClaim, MemoryScope

ROOT = Path(__file__).resolve().parents[1]
LOOPBACK = 'http://127.0.0.1:9/v1'
CWD = '/w/z0'  # the task cwd: project z0 (no git root on this path, so its own name, as AgentsView names it)
QUERY = 'how do we deploy the quokka gateway'


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv('Z0INT_HOME', str(tmp_path / 'z0'))
    monkeypatch.setenv('AGENTSVIEW_DATA_DIR', str(tmp_path / 'av'))
    monkeypatch.setenv('PYTHONPATH', os.pathsep.join(p for p in (str(ROOT / 'src'), str(ROOT / 'tests'),
                                                                 os.environ.get('PYTHONPATH', '')) if p))
    for name in ('Z0INT_MEMORY_INJECT', 'Z0INT_MEMORY_ENDPOINT', 'ANTHROPIC_BASE_URL', 'OPENAI_BASE_URL'):
        monkeypatch.delenv(name, raising=False)
    build_av_db(tmp_path / 'av' / 'sessions.db')
    return tmp_path


def rows(harness='claude-code'):
    return seam.rows(harness)


def wait_rows(harness, n=1, timeout=15.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        found = rows(harness)
        if len(found) >= n:
            return found
        time.sleep(0.05)
    return rows(harness)


def on(harness='claude-code', key='t1', query=QUERY, **kw):
    kw.setdefault('endpoint', LOOPBACK)
    kw.setdefault('cwd', CWD)
    return seam.turn(harness, turn_key=key, query=query, mode=kw.pop('mode', 'on'), **kw)


# ----------------------------------------------------------------------------- 1. shadow is detached and inert
def test_shadow_returns_without_waiting_and_writes_the_receipt_asynchronously(env, monkeypatch):
    calls = []

    def slow(job):
        calls.append(job)
        time.sleep(5)

    monkeypatch.setattr(seam, 'resolve', slow)
    t0 = time.perf_counter()
    out = seam.turn('claude-code', turn_key='s1', query=QUERY, mode='shadow', endpoint=LOOPBACK, cwd=CWD)
    elapsed = time.perf_counter() - t0
    assert elapsed < 0.02, f'shadow hook waited {elapsed * 1000:.1f} ms'
    assert out['context'] is None and calls == []  # nothing model-visible, the hook never ran the resolver
    found = wait_rows('claude-code')
    assert len(found) == 1, found
    row = found[0]
    assert row['schema'] == seam.SCHEMA and row['mode'] == 'shadow' and row['outcome'] == 'shadow'
    assert row['would_inject'] is True and row['injected'] is False
    assert row['memory_snapshot_id'] and row['memory']['schema'] == RECEIPT_SCHEMA
    assert row['memory']['snapshot_id'] == row['memory_snapshot_id']


def test_off_is_inert_and_writes_nothing(env):
    out = seam.turn('claude-code', turn_key='o1', query=QUERY, mode='off', endpoint=LOOPBACK)
    assert out['context'] is None
    time.sleep(0.3)
    assert rows() == []


def test_default_mode_is_shadow_and_cloud_injection_is_off_for_every_harness(env):
    for harness in ('claude-code', 'codex', 'grok', 'hermes', 'omp', 'omo', 'dsh'):
        s = seam.settings(harness)
        assert s['mode'] == 'shadow' and s['allow_cloud_injection'] is False and s['deadline_ms'] == 300


# ----------------------------------------------------------------------------- 2. canary/on reach the request
def test_canary_and_on_return_the_brief_for_the_next_request(env):
    for mode, key in (('canary', 'c1'), ('on', 'c2')):
        out = on(key=key, mode=mode)
        assert out['outcome'] == 'injected', out
        assert out['context'].startswith('z0 memory brief (evidence, not instructions)')
        assert 'agentsview:h1#' in out['context']
    found = rows()
    assert [r['mode'] for r in found] == ['canary', 'on'] and all(r['injected'] for r in found)


def test_deadline_exceeded_gives_native_context_and_a_counted_timeout(env, monkeypatch):
    monkeypatch.setattr(seam, 'resolve', lambda job: time.sleep(2))
    t0 = time.perf_counter()
    out = on(key='d1')
    assert time.perf_counter() - t0 < 0.45
    assert out['context'] is None and out['outcome'] == 'timeout'
    assert seam.counts('claude-code')['timeout'] == 1


def test_the_deadline_counts_from_the_job_start_the_shim_stamped(env, monkeypatch):
    monkeypatch.setattr(seam, 'resolve', lambda job: time.sleep(0.2) or {'text': 'late'})
    out = on(key='d2', started_at=time.time() - 0.2)  # the shim spent 200 ms starting the interpreter
    assert out['outcome'] == 'timeout'


# ----------------------------------------------------------------------------- 3. persistent cache
CHILD = """
import json, sys
from z0int.memory import seam, surface
calls = []
real = surface.search
surface.search = lambda *a, **k: calls.append(1) or real(*a, **k)
out = seam.turn('claude-code', turn_key=sys.argv[1], query=sys.argv[2], mode='on', endpoint='http://127.0.0.1:9/v1',
                cwd='/w/z0')
print(json.dumps({'context': out['context'], 'searches': len(calls), 'cache': out.get('cache')}))
"""


def child(key, query=QUERY):
    proc = subprocess.run([sys.executable, '-c', CHILD, key, query], capture_output=True, text=True, timeout=60)
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout)


def test_a_second_process_serves_the_brief_from_the_snapshot_cache_until_a_source_changes(env):
    first = child('p1')
    assert first['context'] and first['searches'] == 1 and first['cache'] == 'miss'
    second = child('p2')
    assert second['context'] == first['context'] and second['searches'] == 0 and second['cache'] == 'hit'
    add_message(env / 'av' / 'sessions.db', 'h1', 'the quokka gateway moved to a socket unit')
    third = child('p3')
    assert third['searches'] == 1 and third['cache'] == 'miss' and third['context'] != first['context']


# ----------------------------------------------------------------------------- 4. idempotency (#56-E)
def test_replaying_a_turn_key_never_injects_twice_and_has_no_side_effect(env):
    first = on(key='r1')
    assert first['outcome'] == 'injected'
    home = Path(os.environ['Z0INT_HOME'])
    before = sorted((p, p.stat().st_mtime_ns, p.stat().st_size) for p in home.rglob('*') if p.is_file())
    again = on(key='r1')
    assert again['context'] is None and again['outcome'] == 'replay'
    after = sorted((p, p.stat().st_mtime_ns, p.stat().st_size) for p in home.rglob('*') if p.is_file())
    assert after == before


def test_replaying_a_shadow_turn_spawns_nothing(env):
    seam.turn('claude-code', turn_key='r2', query=QUERY, mode='shadow', endpoint=LOOPBACK, cwd=CWD)
    wait_rows('claude-code')
    out = seam.turn('claude-code', turn_key='r2', query=QUERY, mode='shadow', endpoint=LOOPBACK, cwd=CWD)
    time.sleep(1.0)
    assert out['outcome'] == 'replay' and len(rows()) == 1


# ----------------------------------------------------------------------------- 5. single owner
def test_a_second_injector_for_the_same_turn_refuses_and_writes_double_inject_guard(env):
    assert on(key='u1', injector='z0-memory:claude-code')['outcome'] == 'injected'
    second = on(key='u1', injector='z0-memory:claude-code#2')
    assert second['context'] is None and second['outcome'] == 'double_inject_guard'
    assert [r['outcome'] for r in rows()] == ['injected', 'double_inject_guard']


def test_context_resolve_owning_the_turn_blocks_the_seam(env):
    assert ms.claim_injection('claude-code:sess:u2', 'context_resolve')
    out = on(key='claude-code:sess:u2')
    assert out['context'] is None and out['outcome'] == 'double_inject_guard'


def test_tencentdb_items_are_excluded_when_the_host_already_has_the_tencentdb_provider(env, monkeypatch):
    monkeypatch.setenv('Z0_TEST_TDB_TOKEN', 'fake-tdb-bearer-value-123')
    items = [{'id': 'sem-1', 'content': 'quokka gateway semantic memory note'}]
    with FakeTencentDB(items=items) as gw:
        cfg = Path(os.environ['Z0INT_HOME']) / 'config' / 'memory.json'
        cfg.parent.mkdir(parents=True, exist_ok=True)
        cfg.write_text(json.dumps(gw.config()))
        both = on('hermes', key='x1')
        only_z0 = on('hermes', key='x2', exclude_layers=('semantic',))
    # the semantic layer is queried unless the host already injects it; its items carry no project provenance (#63),
    # so a project-scoped brief never shows them either way
    assert 'semantic' in both['memory']['capability_ids'] and 'semantic' not in only_z0['memory']['capability_ids']
    assert 'tencentdb:' not in both['context'] and 'tencentdb:' not in only_z0['context']
    assert 'agentsview:h1#' in only_z0['context']


# ----------------------------------------------------------------------------- 6. fail-open
def test_a_resolver_exception_gives_native_context_and_a_counted_failure(env, monkeypatch):
    def boom(job):
        raise RuntimeError('resolver broke')

    monkeypatch.setattr(seam, 'resolve', boom)
    out = on(key='f1')
    assert out['context'] is None and out['outcome'] == 'error'
    assert seam.counts('claude-code')['error'] == 1


# ----------------------------------------------------------------------------- 7. abstention and supersession
def test_with_the_required_source_removed_the_brief_states_the_gap_and_invents_nothing(env):
    (env / 'av' / 'sessions.db').unlink()
    out = on(key='a1')
    assert out['outcome'] == 'injected'
    assert 'ABSTAIN' in out['context'] and 'gap: lexical: unavailable' in out['context']
    assert 'agentsview:' not in out['context'] and not any(line.startswith(('- [', 'current:'))
                                                           for line in out['context'].splitlines())


def test_supersession_shows_the_newer_fact_as_current_and_history_shows_both(env):
    z0 = MemoryScope(user='local', project='z0')
    ms.record_claim(BitemporalClaim(claim_id='cap-v1', subject='quokka-cap', predicate='value', value=8192, status='observed',
                                    observed_at='2026-01-01T00:00:00Z', recorded_at='2026-01-01T00:00:00Z', scope=z0))
    ms.record_claim(BitemporalClaim(claim_id='cap-v2', subject='quokka-cap', predicate='value', value=20480, status='observed',
                                    observed_at='2026-09-18T00:00:00Z', recorded_at='2026-09-18T00:00:00Z', scope=z0))
    out = on(key='h1', query='which quokka-cap value is current')
    assert 'current: quokka-cap value = 20480 (cap-v2)' in out['context'] and '8192' not in out['context']
    resp = intelligence_mcp.handle({'jsonrpc': '2.0', 'id': 1, 'method': 'tools/call',
                                    'params': {'name': 'history', 'arguments': {'subject': 'quokka-cap'}}}, 'memory')
    history = json.loads(resp['result']['content'][0]['text'])['history']
    assert [(h['claim_id'], h['current']) for h in history] == [('cap-v1', False), ('cap-v2', True)]


# ----------------------------------------------------------------------------- 8. secret scrub at the seam
def test_fixture_secrets_never_reach_the_request_the_receipt_or_the_mcp_response(env):
    out = on(key='k1', query='quokka deploy notes tool output')
    assert out['outcome'] == 'injected' and 'agentsview:c1#' in out['context']
    seam.turn('claude-code', turn_key='k2', query='quokka deploy notes tool output', mode='shadow', endpoint=LOOPBACK,
              cwd=CWD)
    wait_rows('claude-code', 2)
    mcp = intelligence_mcp.handle({'jsonrpc': '2.0', 'id': 2, 'method': 'tools/call',
                                   'params': {'name': 'orient', 'arguments': {'query': 'quokka deploy notes'}}}, 'memory')
    seam_log = (Path(os.environ['Z0INT_HOME']) / 'state' / 'memory' / 'seam' / 'claude-code.jsonl').read_text()
    for blob in (out['context'], seam_log, json.dumps(mcp)):
        for value in SECRETS.values():
            assert value not in blob


# ----------------------------------------------------------------------------- 9. cloud egress
def test_canary_on_refuse_a_non_loopback_endpoint_unless_that_harness_allows_cloud_injection(env):
    for key, endpoint in (('e1', 'https://api.anthropic.com'), ('e2', None), ('e3', 'http://10.0.0.5:8000/v1')):
        out = on(key=key, endpoint=endpoint)
        assert out['context'] is None and out['outcome'] == 'cloud_injection_blocked', (endpoint, out)
    for loop in ('http://127.0.0.1:11545/v1', 'http://localhost:8080', 'http://[::1]:9'):
        assert seam.is_loopback(loop)
    cfg = Path(os.environ['Z0INT_HOME']) / 'config' / 'memory.json'
    cfg.parent.mkdir(parents=True, exist_ok=True)
    cfg.write_text(json.dumps({'inject': {'codex': {'allow_cloud_injection': True}}}))
    assert on(key='e4', endpoint='https://api.anthropic.com')['outcome'] == 'cloud_injection_blocked'
    assert on('codex', key='e5', endpoint='https://api.openai.com/v1')['outcome'] == 'injected'
    assert seam.counts('claude-code')['cloud_injection_blocked'] == 4


# ----------------------------------------------------------------------------- Claude-compatible hook entry
def hook(event, payload, harness='claude-code'):
    from z0int.memory import hook as mhook
    return mhook.handle(event, json.dumps(payload), harness)


PAYLOAD = {'session_id': 'sess-cc', 'prompt_id': 'p-1', 'prompt': QUERY, 'cwd': '/w/z0',
           'hook_event_name': 'UserPromptSubmit'}


def test_claude_code_hook_shadow_prints_nothing_and_returns_fast(env):
    t0 = time.perf_counter()
    assert hook('prompt', PAYLOAD) is None
    assert time.perf_counter() - t0 < 0.02
    assert wait_rows('claude-code')[0]['outcome'] == 'shadow'


@pytest.mark.parametrize('harness,var', [('claude-code', 'ANTHROPIC_BASE_URL'), ('codex', 'OPENAI_BASE_URL')])
def test_claude_compatible_hook_injects_additional_context_in_on_mode(env, monkeypatch, harness, var):
    monkeypatch.setenv('Z0INT_MEMORY_INJECT', 'on')
    monkeypatch.setenv(var, 'http://127.0.0.1:11541')
    out = json.loads(hook('prompt', dict(PAYLOAD, prompt_id=f'p-{harness}'), harness))
    ctx = out['hookSpecificOutput']
    assert ctx['hookEventName'] == 'UserPromptSubmit' and 'agentsview:h1#' in ctx['additionalContext']
    assert rows(harness)[-1]['turn_key'] == turn_key(harness, 'sess-cc', f'p-{harness}')


def test_claude_code_hook_with_the_default_cloud_endpoint_injects_nothing(env, monkeypatch):
    monkeypatch.setenv('Z0INT_MEMORY_INJECT', 'on')
    assert hook('prompt', PAYLOAD) is None
    assert rows()[-1]['outcome'] == 'cloud_injection_blocked'


def test_the_hook_fails_open_on_garbage_input(env, monkeypatch):
    monkeypatch.setenv('Z0INT_MEMORY_INJECT', 'on')
    from z0int.memory import hook as mhook
    assert mhook.handle('prompt', 'not json', 'claude-code') is None
    proc = subprocess.run([sys.executable, '-m', 'z0int.memory.hook', '--harness', 'claude-code', 'prompt'],
                          input='{"broken', capture_output=True, text=True, timeout=30)
    assert proc.returncode == 0 and proc.stdout == ''


def test_grok_has_no_push_seam(env, monkeypatch):
    monkeypatch.setenv('Z0INT_MEMORY_INJECT', 'on')
    monkeypatch.setenv('Z0INT_MEMORY_ENDPOINT', 'http://127.0.0.1:9')
    assert hook('prompt', PAYLOAD, 'grok') is None
    assert rows('grok') == []


def test_a_shadow_spawn_failure_is_a_counted_error_not_an_exception(env, monkeypatch):
    def broken(job, slot=None):
        raise OSError('fork failed')

    monkeypatch.setattr(seam, '_spawn_shadow', broken)
    out = seam.turn('claude-code', turn_key='sp1', query=QUERY, mode='shadow', endpoint=LOOPBACK, cwd=CWD)
    assert out['context'] is None and out['outcome'] == 'error'
    assert seam.counts('claude-code')['error'] == 1


# ----------------------------------------------------------------------------- round 2 (verifier findings)
# The gate's endpoint is the harness's own model setting only: a generic env var never marks a cloud harness loopback.
@pytest.mark.parametrize('harness,var,cloud', [('claude-code', 'ANTHROPIC_BASE_URL', 'https://api.anthropic.com'),
                                               ('codex', 'OPENAI_BASE_URL', 'https://api.openai.com/v1')])
def test_a_generic_endpoint_env_never_turns_a_cloud_harness_into_loopback(env, monkeypatch, harness, var, cloud):
    monkeypatch.setenv('Z0INT_MEMORY_INJECT', 'canary')
    monkeypatch.setenv('Z0INT_MEMORY_ENDPOINT', 'http://127.0.0.1:9')
    monkeypatch.setenv(var, cloud)
    assert hook('prompt', dict(PAYLOAD, prompt_id='e-set'), harness) is None
    monkeypatch.delenv(var)  # unset: the public API default, still cloud
    assert hook('prompt', dict(PAYLOAD, prompt_id='e-unset'), harness) is None
    assert [(r['outcome'], r['endpoint_loopback']) for r in rows(harness)] == [('cloud_injection_blocked', False)] * 2


# A turn without a resolvable task project injects nothing (fail closed) and never surfaces another project.
SIBLING = 'quokka sibling project plan'


@pytest.mark.parametrize('cwd', [None, ''])
def test_a_turn_without_a_task_project_injects_nothing_and_never_surfaces_another_project(env, cwd):
    outs = [on(key=f'n-{mode}-{cwd!r}', query=SIBLING, mode=mode, cwd=cwd) for mode in ('canary', 'on')]
    assert all(o['context'] is None and o['outcome'] == 'no_scope' for o in outs), outs
    shadow = seam.turn('claude-code', turn_key=f'n-shadow-{cwd!r}', query=SIBLING, mode='shadow', endpoint=LOOPBACK,
                       cwd=cwd)
    assert shadow['outcome'] == 'no_scope'
    time.sleep(0.5)  # no detached child may write a brief row for it either
    found = rows()
    assert [r['outcome'] for r in found] == ['no_scope'] * 3 and seam.counts('claude-code')['no_scope'] == 3
    assert not any(r.get('would_inject') or r.get('memory_snapshot_id') for r in found)
    assert 'agentsview:sib#' not in json.dumps(found)
    scoped = on(key='n-scoped', query=SIBLING)  # the same query with the task cwd: project z0 only
    assert scoped['outcome'] == 'injected' and 'agentsview:sib#' not in scoped['context']


def test_the_project_is_named_the_way_agentsview_names_it(tmp_path):
    repo = tmp_path / 'my-repo'
    (repo / '.git').mkdir(parents=True)
    (repo / 'src' / 'pkg').mkdir(parents=True)
    assert seam.project_of(repo / 'src' / 'pkg') == 'my_repo'  # the repo root, not the subdirectory; '-' -> '_'
    linked = tmp_path / 'wt' / 'feature-x'
    linked.mkdir(parents=True)
    (repo / '.git' / 'worktrees' / 'feature-x').mkdir(parents=True)
    (linked / '.git').write_text(f"gitdir: {repo / '.git' / 'worktrees' / 'feature-x'}\n")
    assert seam.project_of(linked) == 'my_repo'  # a linked worktree belongs to its main checkout
    assert seam.project_of('/w/z0') == 'z0'  # no repo: the directory's own name
    assert seam.project_of(None) is None and seam.project_of('') is None and seam.project_of('/') is None


# Shadow children are bounded like C1 capture children: a full pool is a counted row, never another spawn.
def test_a_full_shadow_pool_is_a_counted_queue_saturated_row_and_no_spawn(env, monkeypatch):
    from z0int import harness_capture as hc
    spawned = []
    monkeypatch.setattr(seam, '_spawn_shadow', lambda job, slot=None: spawned.append(job))
    held = [hc.try_slot(pool=seam.SLOT_POOL) for _ in range(hc.MAX_CHILDREN)]
    try:
        assert all(held)
        out = seam.turn('claude-code', turn_key='q1', query=QUERY, mode='shadow', endpoint=LOOPBACK, cwd=CWD)
    finally:
        for fh in held:
            fh.close()
    assert out['outcome'] == 'queue_saturated' and spawned == []
    assert seam.counts('claude-code')['queue_saturated'] == 1
    seam.turn('claude-code', turn_key='q2', query=QUERY, mode='shadow', endpoint=LOOPBACK, cwd=CWD)
    assert len(spawned) == 1  # a free slot spawns again


def test_turn_markers_are_pruned_after_the_ttl(env):
    on(key='m1')
    turns = Path(os.environ['Z0INT_HOME']) / 'state' / 'memory' / 'seam' / 'turns'
    [old] = list(turns.iterdir())
    stale = time.time() - ms.INJECTOR_MARKER_TTL_S - 60
    os.utime(old, (stale, stale))
    on(key='m2')
    assert old not in list(turns.iterdir()) and len(list(turns.iterdir())) == 1


# The Claude-compatible hook counts the 300 ms budget from process start, like the JS and Hermes shims.
def test_the_hook_counts_the_deadline_from_process_start(env, monkeypatch):
    from z0int.memory import hook as mhook
    started = mhook.process_started_at()
    assert started <= mhook.IMPORTED_AT <= time.time()
    monkeypatch.setenv('Z0INT_MEMORY_INJECT', 'on')
    monkeypatch.setenv('ANTHROPIC_BASE_URL', 'http://127.0.0.1:11541')
    monkeypatch.setattr(seam, 'resolve', lambda job: time.sleep(0.15) or {'text': 'late', 'evidence': ['x']})
    late = mhook.handle('prompt', json.dumps(dict(PAYLOAD, prompt_id='dl-1')), 'claude-code', started_at=time.time() - 0.2)
    assert late is None and rows()[-1]['outcome'] == 'timeout'


# Echo guard: a z0 brief a host persisted (DSH admitted message, Hermes api_content, a Claude transcript) and
# AgentsView re-indexed is never evidence for a later brief.
def test_a_persisted_z0_brief_is_never_recalled_as_evidence(env):
    from memory_fixture import SESSIONS
    marker = ms.BRIEF_MARKER
    echo = [('echo', 'deepseek-harness', 'z0', '/w/z0', [('user', f'{marker}\n- [hermes] zebracorn rollout note (agentsview:h1#9)')]),
            ('tail', 'hermes', 'z0', '/w/z0', [('user', f'what about the zebracorn rollout?\n\n{marker}\n- zebracorn echo line')]),
            ('real', 'claude', 'z0', '/w/z0', [('assistant', 'zebracorn rollout finished on tuesday')])]
    db = env / 'av' / 'sessions.db'
    db.unlink()
    build_av_db(db, sessions=[*SESSIONS, *echo])
    out = on(key='echo1', query='zebracorn rollout')
    assert out['outcome'] == 'injected' and 'agentsview:real#' in out['context']
    assert 'agentsview:echo#' not in out['context'] and 'zebracorn echo line' not in out['context']
    assert out['context'].count(marker) == 1
