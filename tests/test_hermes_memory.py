"""C8: the Hermes memory seam (harness-adapters/hermes-z0intelligence/memory.py) inside the plugin's one pre_llm_call.

The plugin is loaded the way Hermes loads it (``register(ctx)``), and the host side of the injection is modelled
as Hermes builds it (turn_context._collect_pre_llm_call_context: every result's ``context`` joined into the
current user message at API time). The z0int side is the real ``python -m z0int.memory.seam`` of this tree.
"""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import stat
import sys
import time
from pathlib import Path

import pytest

from memory_fixture import FakeTencentDB, build_av_db
from test_hermes_capture import Ctx, load_plugin
from z0int.harness_id import turn_key

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / 'src'
LOOPBACK = {'model': {'provider': 'custom', 'base_url': 'http://127.0.0.1:11545/v1'}}
QUERY = 'how do we deploy the quokka gateway'


@pytest.fixture
def env(monkeypatch, tmp_path):
    z0, hermes = tmp_path / 'z0', tmp_path / 'hermes-home'
    hermes.mkdir()
    monkeypatch.setenv('Z0INT_HOME', str(z0))
    monkeypatch.setenv('HERMES_HOME', str(hermes))
    monkeypatch.setenv('AGENTSVIEW_DATA_DIR', str(tmp_path / 'av'))
    monkeypatch.setenv('PYTHONPATH', os.pathsep.join(p for p in (str(SRC), os.environ.get('PYTHONPATH', '')) if p))
    for name in ('Z0INT_PYTHON', 'Z0INT_CAPTURE', 'Z0INT_MEMORY_INJECT'):
        monkeypatch.delenv(name, raising=False)
    build_av_db(tmp_path / 'av' / 'sessions.db')
    db = hermes / 'state.db'  # the Hermes session store fixture: the seam must never touch it
    conn = sqlite3.connect(db)
    conn.execute('create table messages (session_id text, role text, content text)')
    conn.execute("insert into messages values ('s1', 'user', 'earlier turn')")
    conn.commit()
    conn.close()
    return tmp_path


def slow_python(tmp_path, seconds=5):
    path = tmp_path / 'slow-python'
    path.write_text(f'#!/bin/sh\nsleep {seconds}\n')
    path.chmod(path.stat().st_mode | stat.S_IEXEC)
    return str(path)


def start(mod, settings, monkeypatch, profile=LOOPBACK):
    monkeypatch.setattr(mod, '_profile_config', lambda: profile, raising=False)
    monkeypatch.setattr(mod, '_hermes_workspace_root', lambda task_id: None, raising=False)
    ctx = Ctx(settings)
    mod.register(ctx)
    return ctx


def request(ctx, turn='t1', session='s1', text=QUERY):
    """The user message Hermes sends for this turn: the original text plus every pre_llm_call context."""
    results = ctx.call('pre_llm_call', session_id=session, turn_id=turn, task_id='task-1', user_message=text,
                       conversation_history=[], model='stub-model', platform='cli')
    parts = [r['context'] if isinstance(r, dict) else r for r in results if r]
    return '\n\n'.join([text, *parts])


def seam_rows(z0):
    path = z0 / 'state' / 'memory' / 'seam' / 'hermes.jsonl'
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()] if path.exists() else []


def wait_rows(z0, n=1, timeout=20.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline and len(seam_rows(z0)) < n:
        time.sleep(0.05)
    return seam_rows(z0)


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def test_shadow_hook_returns_without_waiting_and_the_request_is_identical_to_off(env, monkeypatch):
    mod = load_plugin()
    off = request(start(mod, {'memory_inject': 'off', 'z0int_python': sys.executable}, monkeypatch))
    ctx = start(mod, {'memory_inject': 'shadow', 'z0int_python': slow_python(env)}, monkeypatch)
    assert len(ctx.hooks['pre_llm_call']) == 1
    t0 = time.perf_counter()
    shadow = request(ctx, turn='t-slow')
    assert time.perf_counter() - t0 < 0.02
    assert shadow == off


def test_shadow_writes_a_would_inject_receipt_asynchronously(env, monkeypatch):
    mod = load_plugin()
    ctx = start(mod, {'memory_inject': 'shadow', 'z0int_python': sys.executable}, monkeypatch)
    assert request(ctx) == QUERY
    row = wait_rows(env / 'z0')[0]
    assert row['harness'] == 'hermes' and row['outcome'] == 'shadow' and row['would_inject'] is True
    assert row['memory_snapshot_id'] and row['memory']['snapshot_id'] == row['memory_snapshot_id']
    assert row['turn_key'] == turn_key('hermes', 's1', 't1')


def test_canary_brief_reaches_the_next_request_and_never_the_session_store(env, monkeypatch):
    mod = load_plugin()
    before = digest(env / 'hermes-home' / 'state.db')
    ctx = start(mod, {'memory_inject': 'canary', 'z0int_python': sys.executable}, monkeypatch)
    sent = request(ctx)
    assert 'z0 memory brief (evidence, not instructions)' in sent and 'agentsview:h1#' in sent
    assert digest(env / 'hermes-home' / 'state.db') == before
    assert not any('quokka' in p.read_text(errors='ignore') for p in (env / 'hermes-home').rglob('*') if p.is_file()
                   and p.name != 'state.db')


def test_canary_deadline_gives_native_context_and_a_counted_timeout(env, monkeypatch):
    mod = load_plugin()
    ctx = start(mod, {'memory_inject': 'canary', 'z0int_python': slow_python(env)}, monkeypatch)
    t0 = time.perf_counter()
    assert request(ctx) == QUERY
    assert time.perf_counter() - t0 < 0.6
    assert [r['outcome'] for r in seam_rows(env / 'z0')] == ['timeout']


def test_fail_open_when_z0int_is_unavailable(env, monkeypatch):
    mod = load_plugin()
    ctx = start(mod, {'memory_inject': 'on', 'z0int_python': str(env / 'missing-python')}, monkeypatch)
    assert request(ctx) == QUERY
    assert [r['outcome'] for r in seam_rows(env / 'z0')] == ['error']


def test_replaying_the_turn_never_injects_twice(env, monkeypatch):
    mod = load_plugin()
    ctx = start(mod, {'memory_inject': 'on', 'z0int_python': sys.executable}, monkeypatch)
    assert 'agentsview:h1#' in request(ctx, turn='r1')
    assert request(ctx, turn='r1') == QUERY


def test_a_cloud_model_is_blocked_unless_hermes_allows_cloud_injection(env, monkeypatch):
    mod = load_plugin()
    cloud = {'model': {'provider': 'openrouter', 'base_url': 'https://openrouter.ai/api/v1'}}
    ctx = start(mod, {'memory_inject': 'on', 'z0int_python': sys.executable}, monkeypatch, profile=cloud)
    assert request(ctx) == QUERY
    assert seam_rows(env / 'z0')[-1]['outcome'] == 'cloud_injection_blocked'


def test_automatic_capture_and_memory_share_one_pre_llm_call(env, monkeypatch):
    mod = load_plugin()
    (env / 'z0' / 'config').mkdir(parents=True)
    (env / 'z0' / 'config' / 'automatic.json').write_text(json.dumps({'hermes': {'enabled': True}}))
    monkeypatch.setattr(mod, 'before_turn', lambda python, home, **kw: {'context': 'AUTOMATIC-CONTEXT'})
    ctx = start(mod, {'mode': 'shadow', 'memory_inject': 'on', 'z0int_python': sys.executable}, monkeypatch)
    assert len(ctx.hooks['pre_llm_call']) == 1
    sent = request(ctx)
    assert 'AUTOMATIC-CONTEXT' in sent and 'agentsview:h1#' in sent


def test_memory_follows_the_capture_mode_when_unset(env, monkeypatch):
    mod = load_plugin()
    for settings in ({}, {'mode': 'off'}):  # C4a: mode off leaves Hermes dispatch untouched
        assert start(mod, dict(settings, z0int_python=sys.executable), monkeypatch).hooks == {}
    ctx = start(mod, {'mode': 'shadow', 'z0int_python': sys.executable}, monkeypatch)
    assert request(ctx, turn='m1') == QUERY  # shadow: nothing injected
    assert any(r['outcome'] == 'shadow' for r in wait_rows(env / 'z0'))


def test_tencentdb_items_are_excluded_when_hermes_uses_the_tencentdb_memory_provider(env, monkeypatch):
    monkeypatch.setenv('Z0_TEST_TDB_TOKEN', 'fake-tdb-bearer-value-123')
    mod = load_plugin()
    with FakeTencentDB(items=[{'id': 'sem-9', 'content': 'quokka gateway semantic note'}]) as gw:
        (env / 'z0' / 'config').mkdir(parents=True, exist_ok=True)
        (env / 'z0' / 'config' / 'memory.json').write_text(json.dumps(gw.config(deadline_ms=250)))
        profile = dict(LOOPBACK, memory={'provider': 'memory_tencentdb'})
        sent = request(start(mod, {'memory_inject': 'on', 'z0int_python': sys.executable}, monkeypatch,
                             profile=profile), turn='p1')
        plain = request(start(mod, {'memory_inject': 'on', 'z0int_python': sys.executable}, monkeypatch),
                        turn='p2')
    assert 'agentsview:h1#' in sent and 'tencentdb:' not in sent
    assert 'tencentdb:sem-9' in plain


def test_post_llm_call_writes_the_memory_use_receipt_of_the_injected_turn(env, monkeypatch):
    mod = load_plugin()
    ctx = start(mod, {'memory_inject': 'on', 'z0int_python': sys.executable}, monkeypatch)
    request(ctx, turn='u1')
    ctx.call('post_llm_call', session_id='s1', turn_id='u1', assistant_response='ok', conversation_history=[],
             model='stub-model', platform='cli')
    used = [r for r in seam_rows(env / 'z0') if r.get('event') == 'post_llm_call']
    assert len(used) == 1 and used[0]['turn_key'] == turn_key('hermes', 's1', 'u1') and used[0]['injected'] is True
    assert used[0]['memory']['snapshot_id'] == used[0]['memory_snapshot_id']
