"""EventLog.append(identity=EventIdentity): the one canonical source-derived ingestion path (z0int#23, #63 P0)."""
from __future__ import annotations

import pytest

from z0int.memory import surface as ms
from z0int.memory import event_log as el
from z0int.memory.event_log import EventLog
from z0int.memory.optmem_tree import OptMemTree
from z0int.memory_contract import EventIdentity


@pytest.fixture
def refs_on(monkeypatch):
    monkeypatch.setenv('Z0INT_MEMORY_SOURCE_INGEST', 'references')


def ident(event_id='42', payload_hash='h1', system='hermes', session='s1'):
    return EventIdentity.from_source(source_system=system, source_session=session, source_event_id=event_id,
                                     payload_hash=payload_hash)


def test_same_identity_twice_is_a_noop_and_bytes_are_never_rewritten(tmp_path, refs_on):
    log = EventLog(tmp_path / 'm')
    a = log.append('source.reference', {'locator': 'agentsview:s1#42'}, source='agentsview', identity=ident())
    before = log.events_path.read_bytes()
    again = log.append('source.reference', {'locator': 'agentsview:s1#42'}, source='agentsview', identity=ident())
    assert again.event_id == a.event_id == 0
    assert log.events_path.read_bytes() == before
    b = log.append('source.reference', {'locator': 'agentsview:s1#43'}, source='agentsview', identity=ident('43'))
    assert b.event_id == 1
    assert log.events_path.read_bytes().startswith(before)
    assert [e.event_id for e in log.iter_events()] == [0, 1]


def test_ledger_seq_differs_while_event_uid_is_stable(tmp_path, refs_on):
    one, two = EventLog(tmp_path / 'one'), EventLog(tmp_path / 'two')
    two.append('memory.note', {'text': 'earlier'}, source='z0')
    a = one.append('source.reference', {'locator': 'agentsview:s1#42'}, source='agentsview', identity=ident())
    b = two.append('source.reference', {'locator': 'agentsview:s1#42'}, source='agentsview', identity=ident())
    ia, ib = a.event_identity(), b.event_identity()
    assert ia.event_uid == ib.event_uid
    assert (ia.ledger_seq, ib.ledger_seq) == (0, 1)


def test_same_uid_with_a_different_payload_hash_is_a_conflict(tmp_path, refs_on):
    log = EventLog(tmp_path / 'm')
    log.append('source.reference', {'locator': 'agentsview:s1#42'}, source='agentsview', identity=ident())
    before = log.events_path.read_bytes()
    with pytest.raises(el.EventIdentityConflict):
        log.append('source.reference', {'locator': 'agentsview:s1#42'}, source='agentsview',
                   identity=ident(payload_hash='h2'))
    assert log.events_path.read_bytes() == before


def test_identity_survives_reopen(tmp_path, refs_on):
    EventLog(tmp_path / 'm').append('source.reference', {'locator': 'x'}, source='agentsview', identity=ident())
    log = EventLog(tmp_path / 'm')
    again = log.append('source.reference', {'locator': 'x'}, source='agentsview', identity=ident())
    assert again.event_id == 0 and len(list(log.iter_events())) == 1


@pytest.mark.parametrize('field', ['content', 'body', 'text', 'prompt', 'response'])
def test_harness_transcript_sources_take_references_only(tmp_path, refs_on, field):
    log = EventLog(tmp_path / 'm')
    with pytest.raises(ValueError, match='references only'):
        log.append('source.reference', {'locator': 'agentsview:s1#42', field: 'hello'}, source='agentsview',
                   identity=ident())
    assert not log.events_path.exists() or not log.events_path.read_bytes()


def test_source_ingest_is_off_until_the_owner_confirms(tmp_path, monkeypatch):
    monkeypatch.delenv('Z0INT_MEMORY_SOURCE_INGEST', raising=False)
    monkeypatch.setenv('Z0INT_HOME', str(tmp_path / 'z0'))
    log = EventLog(tmp_path / 'm')
    with pytest.raises(el.SourceIngestDisabled):
        log.append('source.reference', {'locator': 'x'}, source='agentsview', identity=ident())
    log.append('memory.note', {'text': 'native events still append'}, source='z0')


def test_worker_facing_ledger_cannot_write(tmp_path, refs_on):
    EventLog(tmp_path / 'm').append('memory.note', {'text': 'x'}, source='z0')
    worker = ms.worker_ledger(tmp_path / 'm')
    before = (tmp_path / 'm' / 'events.jsonl').read_bytes()
    with pytest.raises(PermissionError):
        worker.append('memory.note', {'text': 'y'}, source='worker')
    with pytest.raises(PermissionError):
        worker.rebuild_index()
    assert [e.payload for e in worker.iter_events()] == [{'text': 'x'}]
    assert (tmp_path / 'm' / 'events.jsonl').read_bytes() == before


def test_held_out_facts_recoverable_after_a_1024_event_nap_cascade(tmp_path):
    log = EventLog(tmp_path / 'm', blob_threshold=4096)
    tree = OptMemTree(log, raw_tail_events=8)
    held = {3: 'heldfact0003', 500: 'heldfact0500', 1000: 'heldfact1000'}
    originals = {}
    for i in range(1024):
        payload = {'text': f'held-out fact {held[i]} pinned' if i in held else f'filler turn {i}', 'i': i}
        if i in held:
            originals[i] = payload
        log.append('conversation.turn', payload, source='fixture')
        if (i + 1) % 128 == 0:
            tree.nap()
    manifest = tree.nap()
    assert manifest['event_count'] == 1024 and manifest['coarse_end'] == 1016
    for event_id, token in held.items():
        found = ms.search(token, layers=('temporal',), ledger_root=tmp_path / 'm')
        hits = [e for e in found['evidence'] if e['locator'] == f'eventlog:{event_id}']
        assert hits, f'{token} not recovered'
        assert tree.zoom(event_id, event_id + 1)['events'][0]['payload'] == originals[event_id]
