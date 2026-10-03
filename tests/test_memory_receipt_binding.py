"""MemoryUseReceipt rides existing records: DecisionReceipt.extra.memory and opportunity_record.memory."""
from __future__ import annotations

import json

import pytest

from z0int import harness_capture, receipt
from z0int.memory_contract import MemoryUseReceipt

R = MemoryUseReceipt(snapshot_id='mem_abc', capability_ids=('agentsview', 'eventlog'), query_ids=('q1',),
                     evidence_event_uids=('evt_1', 'evt_2'), retrieval_latency_ms=12.5, input_tokens=90,
                     raw_source_reads=4)


@pytest.fixture(autouse=True)
def _hermetic(tmp_path, monkeypatch):
    monkeypatch.setenv('Z0INT_HOME', str(tmp_path / 'z0'))
    monkeypatch.setenv('AGENTSVIEW_DATA_DIR', str(tmp_path / 'av'))


def test_round_trip_through_decision_receipt_extra_without_a_schema_change():
    d = receipt.DecisionReceipt(trace_id='t1', extra=R.to_decision_extra())
    row = json.loads(json.dumps(d.to_dict()))
    assert row['schema'] == receipt.SCHEMA
    back = receipt.DecisionReceipt.from_dict(row)
    assert MemoryUseReceipt.from_dict(back.extra['memory']) == R


def test_opportunity_record_carries_the_memory_use():
    ctx = {'turn_key': 'claude-code:s1:1', 'session_id': 's1', 'trace_id': 't1', 'memory': R}
    row = harness_capture.opportunity_record('claude-code', {'prompt': 'where is the cache'}, ctx)
    assert row['memory'] == R.to_dict()
    json.dumps(row)
    assert MemoryUseReceipt.from_dict(row['memory']) == R


def test_instruction_capability_is_rejected():
    with pytest.raises(ValueError, match='instruction'):
        MemoryUseReceipt(snapshot_id='mem_abc', instruction_capability=True)
    with pytest.raises(ValueError, match='instruction'):
        MemoryUseReceipt.from_dict({**R.to_dict(), 'instruction_capability': True})
    with pytest.raises(ValueError, match='instruction'):
        harness_capture.opportunity_record('claude-code', {'prompt': 'x'},
                                           {'turn_key': 'k', 'memory': {**R.to_dict(), 'instruction_capability': True}})


def test_receipt_binding_runs_in_an_isolated_z0_home(tmp_path):
    """build_state_packet reads $Z0INT_HOME and AgentsView for memory_snapshot_id: never the developer's."""
    from z0int import agentsview_ro, paths
    assert paths.home().is_relative_to(tmp_path.resolve())
    assert agentsview_ro.db_path().is_relative_to(tmp_path)
