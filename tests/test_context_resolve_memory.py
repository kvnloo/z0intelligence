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


@pytest.mark.parametrize('policy', [
    None,
    ms.ScopePolicy(requester='hermes', cross_harness=False),
])
def test_scoped_request_restricts_memory_sources_before_return(env, policy):
    from z0int.memory_contract import MemoryScope

    scope = MemoryScope(user=ms.DEFAULT_USER, project='z0')
    packet = resolve_context(
        needs=[InformationNeed(id='m1', description='quokka', kind='memory', scope=scope)],
        request_scope=scope, memory_policy=policy, turn_key='scoped',
        workstream_revision='fixture-workstream', compiler_revision='fixture-compiler',
        allow_memory=True, allow_fff=False, allow_qmd=False, use_cache=False,
    )
    assert packet.evidence
    assert all(not e.source_id.startswith('agentsview:sib#') for e in packet.evidence)
    if policy is not None:
        assert all(e.source_id.startswith('agentsview:h1#') for e in packet.evidence)
    assert not packet.unresolved_gaps


def test_disjoint_memory_policy_does_not_read_or_claim_injection(env):
    from z0int.memory_contract import MemoryScope

    scope = MemoryScope(user=ms.DEFAULT_USER, project='other')
    packet = resolve_context(
        needs=[InformationNeed(id='m1', description='quokka', kind='memory', scope=scope)],
        request_scope=scope,
        memory_policy=ms.ScopePolicy(scope=MemoryScope(user=ms.DEFAULT_USER, project='z0')),
        turn_key='disjoint', workstream_revision='fixture', compiler_revision='fixture',
        allow_memory=True, allow_fff=False, allow_qmd=False, use_cache=False,
    )
    assert not packet.evidence
    assert any('scope' in gap for gap in packet.unresolved_gaps)
    assert 'quokka' not in str(packet.measurements)
    assert ms.claim_injection('disjoint', 'other-owner')


def test_memory_conflict_cannot_be_hidden_by_a_lexical_hit(env, monkeypatch):
    from memory_fixture import add_message
    from test_memory_claims import _admit
    from z0int.memory_contract import MemoryScope

    add_message(env / 'av' / 'sessions.db', 'h1', 'deployment strategy background')
    scope = MemoryScope(user=ms.DEFAULT_USER, project='z0')
    for event_id, value in [('1', 'blue-green'), ('2', 'canary')]:
        _admit(env / 'z0', monkeypatch, event_id=event_id, value=value, scope=scope,
               observed_at='2026-10-01T00:00:00Z')
    packet = resolve_context(
        needs=[InformationNeed(id='decision', description='deployment', kind='memory')],
        memory_policy=ms.ScopePolicy(scope=scope), turn_key='conflict',
        allow_memory=True, allow_fff=False, allow_qmd=False, use_cache=False,
    )
    assert packet.contradictions
    assert not packet.evidence
    assert any('decision' in gap for gap in packet.unresolved_gaps)


def test_required_memory_outage_cannot_be_hidden_by_temporal_evidence(env, monkeypatch):
    from test_memory_claims import _admit
    from z0int.memory_contract import MemoryScope

    scope = MemoryScope(user=ms.DEFAULT_USER, project='z0')
    _admit(env / 'z0', monkeypatch, event_id='1', value='canary', scope=scope,
           observed_at='2026-10-01T00:00:00Z')
    (env / 'av' / 'sessions.db').unlink()
    packet = resolve_context(
        needs=[InformationNeed(id='decision', description='deployment', kind='memory')],
        memory_policy=ms.ScopePolicy(scope=scope), turn_key='outage',
        allow_memory=True, allow_fff=False, allow_qmd=False, use_cache=False,
    )
    assert not packet.evidence
    assert any('lexical' in gap for gap in packet.unresolved_gaps)


def test_unreadable_temporal_authority_blocks_matching_lexical_evidence(env):
    from z0int.memory.event_log import EventLog

    log = EventLog(env / 'z0' / 'memory')
    log.append('note', {'text': 'quokka gateway'}, source='fixture')
    with log.events_path.open('ab') as stream:
        stream.write(b'{"incomplete":')
    packet = resolve(allow_memory=True, turn_key='corrupt-temporal')
    assert not packet.evidence
    assert packet.unresolved_gaps
    assert ms.verify('quokka gateway', requires=['quokka'], config={})['verdict'] == 'FALLBACK'


@pytest.mark.parametrize('limit', [1, 8])
def test_old_value_query_keeps_current_correction_ahead_of_lexical_history(env, monkeypatch, limit):
    from memory_fixture import add_message
    from test_memory_claims import _admit
    from z0int.memory_contract import MemoryScope

    add_message(env / 'av' / 'sessions.db', 'h1', 'deployment blue-green strategy')
    scope = MemoryScope(user=ms.DEFAULT_USER, project='z0')
    first = _admit(env / 'z0', monkeypatch, event_id='1', value='blue-green',
                   scope=scope, observed_at='2026-10-01T00:00:00Z')
    _admit(env / 'z0', monkeypatch, event_id='2', value='canary',
           scope=scope, observed_at='2026-10-02T00:00:00Z',
           correction_of=first.payload['claim_id'])
    policy = ms.ScopePolicy(scope=scope)
    brief = ms.memory_brief('blue-green', policy, limit=limit, config={}, use_cache=False)
    assert not brief['abstained']
    assert [row['value'] for row in brief['current_claims']] == ['canary']
    packet = resolve_context(
        needs=[InformationNeed(id='decision', description='blue-green', kind='memory')],
        memory_policy=policy, turn_key=f'old-value-{limit}',
        allow_memory=True, allow_fff=False, allow_qmd=False, use_cache=False,
    )
    assert any('canary' in evidence.excerpt for evidence in packet.evidence)


@pytest.mark.parametrize('narrow_policy', [True, False])
def test_request_and_policy_intersection_never_widens_either_scope(env, monkeypatch, narrow_policy):
    from test_memory_claims import _admit
    from z0int.memory_contract import MemoryScope

    user = MemoryScope(user=ms.DEFAULT_USER)
    project = MemoryScope(user=ms.DEFAULT_USER, project='z0')
    _admit(env / 'z0', monkeypatch, event_id='1', value='shared-user-decision', scope=user,
           observed_at='2026-10-01T00:00:00Z')
    _admit(env / 'z0', monkeypatch, event_id='2', value='private-project-decision', scope=project,
           observed_at='2026-10-01T00:00:00Z')
    request_scope, policy_scope = (project, user) if narrow_policy else (user, project)
    packet = resolve_context(
        needs=[InformationNeed(id='decision', description='deployment', kind='memory', scope=request_scope)],
        request_scope=request_scope, memory_policy=ms.ScopePolicy(scope=policy_scope),
        workstream_revision='fixture', compiler_revision='fixture', turn_key='intersection',
        allow_memory=True, allow_fff=False, allow_qmd=False, use_cache=False,
    )
    assert not packet.unresolved_gaps
    assert [e.excerpt for e in packet.evidence] == ['deployment strategy = "shared-user-decision"']
