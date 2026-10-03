"""C8 round 3 (DoD D3): the MemoryUseReceipt a harness's memory seam produced for a turn lands in that turn's capture
``opportunity_record.memory`` (validated), for all 7 harnesses; and the capture kill switch keeps the memory seam
native too.

The seam and the opportunity build are two detached children of the same user turn, joined on the canonical
``turn_key``. Synthetic AgentsView fixture only.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from memory_fixture import build_av_db
from z0int import harness_capture as hc
from z0int.harness_id import turn_key
from z0int.memory import seam
from z0int.memory_contract import MemoryUseReceipt

ROOT = Path(__file__).resolve().parents[1]
HA = ROOT / 'harness-adapters'
LOOPBACK = 'http://127.0.0.1:9/v1'
QUERY = 'how do we deploy the quokka gateway'
HARNESSES = ('claude-code', 'codex', 'grok', 'hermes', 'omp', 'omo', 'dsh')


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv('Z0INT_HOME', str(tmp_path / 'z0'))
    monkeypatch.setenv('AGENTSVIEW_DATA_DIR', str(tmp_path / 'av'))
    monkeypatch.setenv('PYTHONPATH', os.pathsep.join(p for p in (str(ROOT / 'src'), str(ROOT / 'tests'),
                                                                 os.environ.get('PYTHONPATH', '')) if p))
    for name in ('Z0INT_MEMORY_INJECT', 'Z0INT_MEMORY_ENDPOINT', 'Z0INT_CAPTURE', 'ANTHROPIC_BASE_URL',
                 'OPENAI_BASE_URL', 'GROK_SESSION_ID'):
        monkeypatch.delenv(name, raising=False)
    build_av_db(tmp_path / 'av' / 'sessions.db')
    task = tmp_path / 'z0'  # the task cwd: project z0, as the fixture's sessions
    task.mkdir(exist_ok=True)
    return tmp_path


def payload(harness, n=1, task=None):
    return {'session_id': f'sess-{harness}', 'turn_id': f'turn-{n}', 'prompt': QUERY, 'cwd': str(task)}


def wait_for(fn, timeout=20.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = fn()
        if value:
            return value
        time.sleep(0.05)
    return fn()


# ----------------------------------------------------------------------------- the join, every harness
@pytest.mark.parametrize('harness', HARNESSES)
def test_the_opportunity_record_carries_the_receipt_the_memory_seam_wrote_for_that_turn(env, harness):
    task = env / 'z0'
    p = payload(harness, task=task)
    ctx = hc.begin_turn(harness, p)
    out = seam.turn(harness, turn_key=ctx['turn_key'], query=QUERY, mode='shadow', endpoint=LOOPBACK,
                    cwd=str(task), detach=False)
    assert out['outcome'] == 'shadow'
    [row] = seam.rows(harness)
    assert row['memory'], row
    record = hc.opportunity_record(harness, p, ctx)
    assert record['memory'] == MemoryUseReceipt.from_dict(row['memory']).to_dict()
    assert record['memory']['snapshot_id'] == row['memory_snapshot_id']


def test_a_receipt_from_another_turn_is_never_attached(env):
    task = env / 'z0'
    first = hc.begin_turn('codex', payload('codex', 1, task))
    seam.turn('codex', turn_key=first['turn_key'], query=QUERY, mode='shadow', cwd=str(task), detach=False)
    second = hc.begin_turn('codex', payload('codex', 2, task))
    record = hc.opportunity_record('codex', payload('codex', 2, task), second)
    assert 'memory' not in record


def test_the_opportunity_build_waits_for_a_detached_shadow_brief_still_running(env):
    """Both children start at prompt time; the build attaches the receipt the shadow child writes later."""
    task = env / 'z0'
    p = payload('claude-code', task=task)
    ctx = hc.begin_turn('claude-code', p)
    seam.turn('claude-code', turn_key=ctx['turn_key'], query=QUERY, mode='shadow', cwd=str(task))  # detached
    record = hc.opportunity_record('claude-code', p, ctx)
    assert record.get('memory'), 'the receipt of the detached shadow brief was not attached'
    assert record['memory'] == wait_for(lambda: seam.rows('claude-code'))[0]['memory']


def test_a_seam_child_that_starts_late_still_joins(env):
    """A JS/Hermes shadow child is a cold interpreter: it can mark the turn well after the opportunity build has
    started (seen in the round-3 OMP e2e under load). A seam that has run here before gets a grace for that."""
    task = env / 'z0'
    seam.turn('omp', turn_key='earlier', query=QUERY, mode='shadow', cwd=str(task), detach=False)  # the seam has run
    p = payload('omp', task=task)
    ctx = hc.begin_turn('omp', p)
    late = threading.Timer(1.5, lambda: seam.turn('omp', turn_key=ctx['turn_key'], query=QUERY, mode='shadow',
                                                   cwd=str(task), detach=False))
    late.start()
    try:
        record = hc.opportunity_record('omp', p, ctx)
    finally:
        late.join()
    assert record.get('memory'), 'a seam child that marked the turn 1.5 s late was not joined'


def test_a_pending_receipt_that_never_settles_is_bounded(env, monkeypatch):
    task = env / 'z0'
    p = payload('omp', task=task)
    ctx = hc.begin_turn('omp', p)
    monkeypatch.setattr(seam, '_spawn_shadow', lambda job, slot: None)  # the shadow child dies before writing
    seam.turn('omp', turn_key=ctx['turn_key'], query=QUERY, mode='shadow', cwd=str(task))
    monkeypatch.setattr(seam, 'RECEIPT_WAIT_S', 0.3)
    t0 = time.monotonic()
    record = hc.opportunity_record('omp', p, ctx)
    assert 'memory' not in record and time.monotonic() - t0 < 5


def test_without_a_memory_seam_the_build_neither_waits_nor_adds_a_field(env):
    task = env / 'z0'
    p = payload('dsh', task=task)
    ctx = hc.begin_turn('dsh', p)
    t0 = time.monotonic()
    record = hc.opportunity_record('dsh', p, ctx)
    assert 'memory' not in record and time.monotonic() - t0 < 3


def test_a_late_second_injector_never_replaces_the_turns_receipt(env):
    task = env / 'z0'
    p = payload('hermes', task=task)
    ctx = hc.begin_turn('hermes', p)
    first = seam.turn('hermes', turn_key=ctx['turn_key'], query=QUERY, mode='on', endpoint=LOOPBACK, cwd=str(task))
    assert first['outcome'] == 'injected'
    second = seam.turn('hermes', turn_key=ctx['turn_key'], query=QUERY, mode='on', endpoint=LOOPBACK, cwd=str(task),
                       injector='other')
    assert second['outcome'] == 'double_inject_guard'
    record = hc.opportunity_record('hermes', p, ctx)
    assert record['memory'] == first['memory']


def test_a_turn_with_no_receipt_settles_at_once_and_adds_no_field(env):
    p = {'session_id': 's', 'turn_id': 't', 'prompt': QUERY, 'cwd': None}
    ctx = hc.begin_turn('omo', p)
    assert seam.turn('omo', turn_key=ctx['turn_key'], query=QUERY, mode='shadow', cwd=None)['outcome'] == 'no_scope'
    t0 = time.monotonic()
    assert 'memory' not in hc.opportunity_record('omo', p, ctx)
    assert time.monotonic() - t0 < 3


# ----------------------------------------------------------------------------- the hook processes, real children
@pytest.mark.parametrize('harness', ['claude-code', 'codex', 'grok'])
def test_hook_processes_join_the_capture_opportunity_and_the_memory_receipt(env, harness):
    """The real UserPromptSubmit commands of one turn (capture + memory, separate processes): the detached
    opportunity row carries the detached shadow receipt."""
    task = env / 'z0'
    p = {'session_id': f'sess-{harness}', 'prompt_id': 'p-1', 'prompt': QUERY, 'cwd': str(task),
         'hook_event_name': 'UserPromptSubmit'}
    procs = [subprocess.Popen([sys.executable, '-m', mod, '--harness', harness, 'prompt'], stdin=subprocess.PIPE,
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
             for mod in ('z0int.hook_adapter', 'z0int.memory.hook')]
    for proc in procs:
        proc.communicate(json.dumps(p), timeout=60)
    opp = hc.state_dir(harness) / hc.RECORD_FILES['opportunity_record']
    rows = wait_for(lambda: opp.exists() and [json.loads(x) for x in opp.read_text().splitlines() if x.strip()], 60)
    assert rows and rows[0]['turn_key'] == turn_key(harness, p['session_id'], 'p-1')
    assert rows[0].get('memory'), f'{harness}: opportunity row has no memory receipt'
    assert rows[0]['memory'] == seam.rows(harness)[0]['memory']


# ----------------------------------------------------------------------------- Grok: a shadow-only receipt seam
def test_grok_memory_hook_is_shadow_only_and_never_prints(env, monkeypatch):
    """Grok has no push seam (UserPromptSubmit stdout is ignored): its memory hook only records the shadow
    receipt, whatever mode is configured, so the turn's opportunity can carry it. Nothing is model-visible."""
    from z0int.memory import hook as mhook
    monkeypatch.setenv('Z0INT_MEMORY_INJECT', 'on')
    p = {'session_id': 'sess-g', 'prompt_id': 'p-1', 'prompt': QUERY, 'cwd': str(env / 'z0')}
    assert mhook.handle('prompt', json.dumps(p), 'grok') is None
    found = wait_for(lambda: seam.rows('grok'))
    assert [r['outcome'] for r in found] == ['shadow'] and found[0]['injected'] is False and found[0]['memory']


def test_grok_memory_hook_is_its_own_hooks_file_and_capture_stays_capture_only():
    capture = (HA / 'grok-z0intelligence' / 'hooks' / 'z0-capture.json').read_text()
    assert 'z0int.memory' not in capture
    memory = json.loads((HA / 'grok-z0intelligence' / 'hooks' / 'z0-memory.json').read_text())['hooks']
    assert set(memory) == {'UserPromptSubmit'}
    [entry] = memory['UserPromptSubmit']
    [cmd] = [h['command'] for h in entry['hooks']]
    assert '-m z0int.memory.hook --harness grok prompt' in cmd and cmd.rstrip().endswith('|| true')


# ----------------------------------------------------------------------------- capture kill switch
@pytest.mark.parametrize('switch', ['env', 'config'])
@pytest.mark.parametrize('mode', ['shadow', 'on'])
def test_the_capture_kill_switch_keeps_the_memory_seam_native(env, monkeypatch, switch, mode):
    if switch == 'env':
        monkeypatch.setenv('Z0INT_CAPTURE', '0')
    else:
        cfg = env / 'z0' / 'config'
        cfg.mkdir(parents=True, exist_ok=True)
        (cfg / 'capture.json').write_text('{"enabled": false}')
    out = seam.turn('codex', turn_key='k1', query=QUERY, mode=mode, endpoint=LOOPBACK, cwd=str(env / 'z0'))
    assert out['outcome'] == 'off' and out['context'] is None
    time.sleep(0.3)
    assert seam.rows('codex') == []
    assert not (env / 'z0' / 'state' / 'memory').exists()


def test_the_kill_switch_silences_the_hook_in_on_mode(env, monkeypatch):
    from z0int.memory import hook as mhook
    monkeypatch.setenv('Z0INT_CAPTURE', '0')
    monkeypatch.setenv('Z0INT_MEMORY_INJECT', 'on')
    monkeypatch.setenv('ANTHROPIC_BASE_URL', 'http://127.0.0.1:11541')
    p = {'session_id': 's', 'prompt_id': 'p', 'prompt': QUERY, 'cwd': str(env / 'z0')}
    assert mhook.handle('prompt', json.dumps(p), 'claude-code') is None
    assert seam.rows('claude-code') == []


def test_the_js_memory_client_reads_the_same_kill_switch(env):
    script = ("import {memoryMode} from %s;\n"
              "console.log(JSON.stringify([memoryMode('on', {Z0INT_CAPTURE: '0'}), memoryMode('on', {}),"
              " memoryMode(undefined, {Z0INT_HOME: %s})]))") % (
        json.dumps((HA / 'memory-client.mjs').as_uri()), json.dumps(str(env / 'z0')))
    cfg = env / 'z0' / 'config'
    cfg.mkdir(parents=True, exist_ok=True)
    (cfg / 'capture.json').write_text('{"enabled": false}')
    proc = subprocess.run(['node', '--input-type=module', '-e', script], capture_output=True, text=True, timeout=30)
    assert proc.returncode == 0, proc.stderr
    assert json.loads(proc.stdout) == ['off', 'on', 'off']


# ----------------------------------------------------------------------------- key agreement (regression guards)
def test_hermes_memory_job_and_capture_derive_the_same_turn_key(env):
    from z0int import hermes_capture
    import importlib.util
    spec = importlib.util.spec_from_file_location('hz_memory', HA / 'hermes-z0intelligence' / 'memory.py')
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    from types import SimpleNamespace
    shim = SimpleNamespace(mode='shadow', endpoint=None, injector=None, exclude_layers=[])
    job = mod.Memory._job(shim, 'sess-h', 7, QUERY, str(env / 'z0'))
    seam_key = turn_key('hermes', job['session_id'], job['turn_id'])
    cap = hc.begin_turn('hermes', dict(hermes_capture._ids({'session_id': 'sess-h', 'turn_id': 7}),
                                       user_message=QUERY, cwd=str(env / 'z0')))
    assert seam_key == cap['turn_key']


@pytest.mark.parametrize('switch', ['env', 'config'])
def test_the_hermes_memory_seam_reads_the_capture_kill_switch(env, monkeypatch, switch):
    import importlib.util
    spec = importlib.util.spec_from_file_location('hz_memory_ks', HA / 'hermes-z0intelligence' / 'memory.py')
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    home = env / 'z0'
    assert mod.create({'memory_inject': 'on'}, home, sys.executable, {}) is not None
    if switch == 'env':
        monkeypatch.setenv('Z0INT_CAPTURE', '0')
    else:
        (home / 'config').mkdir(parents=True, exist_ok=True)
        (home / 'config' / 'capture.json').write_text('{"enabled": false}')
    assert mod.create({'memory_inject': 'on'}, home, sys.executable, {}) is None
