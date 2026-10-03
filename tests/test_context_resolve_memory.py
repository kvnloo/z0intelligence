"""context_resolve kind=memory: resolves through the memory surface only with allow_memory=True and one injector."""
from __future__ import annotations

import pytest

from memory_fixture import build_av_db
from z0int.context_resolve import InformationNeed, resolve_context
from z0int.memory import surface as ms

NEED = [InformationNeed(id='m1', description='quokka gateway', kind='memory')]


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv('Z0INT_HOME', str(tmp_path / 'z0'))
    monkeypatch.setenv('AGENTSVIEW_DATA_DIR', str(tmp_path / 'av'))
    build_av_db(tmp_path / 'av' / 'sessions.db')
    return tmp_path


def resolve(**kw):
    return resolve_context(needs=NEED, use_cache=False, allow_qmd=False, **kw)


def test_memory_stays_off_without_allow_memory(env):
    packet = resolve(allow_memory=False, turn_key='t0')
    assert not packet.evidence
    assert any('memory recall disabled' in g for g in packet.unresolved_gaps)


def test_memory_resolves_with_allow_memory_and_no_other_injector(env):
    packet = resolve(allow_memory=True, turn_key='t1')
    sources = [e.source_id for e in packet.evidence]
    assert sources and all(s.startswith('agentsview:') for s in sources)
    assert not packet.unresolved_gaps
    assert any(op.get('op') == 'memory_resolve' for op in packet.recipe.operations)
    assert packet.measurements.get('memory_snapshot_id', '').startswith('mem_')


def test_memory_does_not_resolve_when_another_injector_owns_the_turn(env):
    assert ms.claim_injection('t2', 'tencentdb_proxy') is True
    packet = resolve(allow_memory=True, turn_key='t2')
    assert not packet.evidence
    assert any('double_inject_guard' in g for g in packet.unresolved_gaps)
    assert ms.claim_injection('t2', 'tencentdb_proxy') is True
    assert ms.claim_injection('t2', 'context_resolve') is False


def test_memory_without_a_turn_key_is_skipped_because_the_guard_cannot_run(env):
    packet = resolve(allow_memory=True)
    assert not packet.evidence
    assert any('double_inject_guard' in g and 'turn_key' in g for g in packet.unresolved_gaps)
    assert not any(op.get('op') == 'memory_resolve' for op in packet.recipe.operations)
