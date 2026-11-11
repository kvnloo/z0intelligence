"""Synthetic session state, including slow create-capable opens on shared runners."""
import builtins
import io
import json
import time
from concurrent.futures import ThreadPoolExecutor

import pytest

from z0int import harness_capture as hc
from z0int import hook_entry as he


@pytest.mark.parametrize('harness', ['claude-code', 'codex', 'grok'])
def test_prompt_handler_slow_create(tmp_path, monkeypatch, harness):
    monkeypatch.setenv('Z0INT_HOME', str(tmp_path / 'z0'))
    monkeypatch.setattr(hc, 'spawn_detached', lambda *args: None)
    from z0int import automatic
    monkeypatch.setattr(automatic, 'handle_event', lambda event: {'action': 'native'})
    job = {'session_id': 'slow-create', 'prompt': 'synthetic request', 'cwd': str(tmp_path)}
    job['hookEventName' if harness == 'grok' else 'hook_event_name'] = 'UserPromptSubmit'
    id_field = 'prompt_id' if harness == 'claude-code' else 'turn_id'
    assert he.handle('prompt', json.dumps(dict(job, **{id_field: 'warmup'})), harness, env={}) is None
    creates = []

    def slow_open(path, mode='r', *args, **kwargs):
        # a/a+ request O_CREAT even for an existing file. The hot path must reopen
        # existing state without that flag, not depend on cheap metadata operations.
        if any(flag in mode for flag in 'wax'):
            creates.append(str(path))
            time.sleep(0.005)
        return builtins.open(path, mode, *args, **kwargs)

    monkeypatch.setattr(hc, 'open', slow_open, raising=False)
    samples = []
    for i in range(200):
        raw = json.dumps(dict(job, **{id_field: str(i)}))
        start = time.perf_counter()
        assert he.handle('prompt', raw, harness, env={}) is None
        samples.append(time.perf_counter() - start)
    assert sorted(samples)[int(len(samples) * 0.99) - 1] < 0.005, (harness, sorted(samples)[-3:], creates[:3])
    assert not creates
    assert hc.outcome_context(harness, job)['trace_id'] == '199'


def test_session_state_is_append_only_and_reads_legacy_state(tmp_path):
    path = hc._session_path('codex', 'legacy', tmp_path)
    path.parent.mkdir(parents=True)
    legacy = json.dumps({'model': 'legacy-model', 'ordinal': 7, 'attempt': 0})
    path.write_text(legacy)
    first = hc.begin_turn('codex', {'session_id': 'legacy', 'turn_id': 'one', 'prompt': 'request'}, root=tmp_path)
    prefix = path.read_bytes()
    assert prefix.startswith(legacy.encode())
    second = hc.begin_turn('codex', {'session_id': 'legacy', 'turn_id': 'two', 'retry': True,
                                    'prompt': 'retry'}, root=tmp_path)
    assert path.read_bytes().startswith(prefix)
    assert second['work_item_id'] == first['work_item_id']
    assert second['attempt_id'] == 1 and second['model_id'] == 'legacy-model'
    assert hc.outcome_context('codex', {'session_id': 'legacy', 'turn_id': 'one'}, root=tmp_path)['work_item_id'] == first['work_item_id']


def test_session_journal_recovers_partial_tail_and_large_snapshot(tmp_path):
    path = hc._session_path('codex', 'partial', tmp_path)
    path.parent.mkdir(parents=True)
    snapshot = {'model': 'm' * 20000, 'ordinal': 9}
    path.write_text(json.dumps(snapshot) + '\n{"ordinal":')
    ctx = hc.begin_turn('codex', {'session_id': 'partial', 'turn_id': 'next', 'prompt': 'request'}, root=tmp_path)
    assert ctx['model_id'] == snapshot['model']
    assert hc._read_session('codex', 'partial', tmp_path)['ordinal'] == 10


def test_session_journal_reads_only_the_latest_snapshot():
    class Counted(io.BytesIO):
        bytes_read = 0

        def read(self, size=-1):
            data = super().read(size)
            self.bytes_read += len(data)
            return data

    stream = Counted(b'{"ordinal": 1}\n' * 100000 + b'{"ordinal": 2}\n')
    assert hc._session_state(stream) == {'ordinal': 2}
    assert stream.bytes_read <= 8192


def test_session_journal_serializes_writers(tmp_path):
    def begin(i):
        return hc.begin_turn('codex', {'session_id': 'shared', 'turn_id': str(i), 'prompt': 'request'}, root=tmp_path)

    with ThreadPoolExecutor(max_workers=8) as pool:
        contexts = list(pool.map(begin, range(80)))
    assert len({ctx['work_item_id'] for ctx in contexts}) == 80
    state = hc._read_session('codex', 'shared', tmp_path)
    assert state['ordinal'] == 80 and len(state['turns']) == hc.KEEP
