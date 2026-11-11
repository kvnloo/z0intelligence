"""Session updates preserve locked state without discarding the previous record first."""
from concurrent.futures import ThreadPoolExecutor
import json

import pytest

from z0int import harness_capture as hc


def test_failed_serialization_preserves_the_previous_session(tmp_path):
    hc._update_session('codex', 'session', tmp_path, lambda state: {'model': 'previous'})
    path = hc._session_path('codex', 'session', tmp_path)
    before = path.read_bytes()

    with pytest.raises(TypeError):
        hc._update_session('codex', 'session', tmp_path, lambda state: {'model': object()})

    assert path.read_bytes() == before


@pytest.mark.parametrize('model', ['short', '模型', 'long' * 200])
def test_session_rewrite_preserves_the_inode_and_removes_the_old_tail(tmp_path, model):
    hc._update_session('codex', 'session', tmp_path, lambda state: {'model': 'x' * 1000})
    path = hc._session_path('codex', 'session', tmp_path)
    inode = path.stat().st_ino

    updated = hc._update_session('codex', 'session', tmp_path, lambda state: {'model': model})

    assert path.stat().st_ino == inode
    assert json.loads(path.read_text()) == updated == {'model': model}


def test_concurrent_session_updates_keep_every_increment(tmp_path):
    def increment(_):
        return hc._update_session('codex', 'session', tmp_path,
                                  lambda state: {'count': state.get('count', 0) + 1})

    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(increment, range(40)))

    assert hc._read_session('codex', 'session', tmp_path) == {'count': 40}
