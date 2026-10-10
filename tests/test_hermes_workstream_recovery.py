"""UNIT ledger fixtures; no native decisions are ingested by these tests."""
from pathlib import Path
import importlib.util
from z0int.memory.event_log import EventLog
from z0int.memory_contract import MemoryScope


def recovery():
    path = Path(__file__).parents[1]/'adapters/hermes_z0int/workstream.py'
    assert path.is_file(), 'Hermes read-only workstream recovery adapter is missing'
    spec = importlib.util.spec_from_file_location('recovery_under_test', path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_recovery_requires_scoped_execution_lineage(tmp_path):
    scope = MemoryScope(user='unit-controller', project='unit-project', repo='unit/repo', task='unit-task')
    ledger = EventLog(tmp_path/'ledger')
    prepared = ledger.append('reuse.prepared', {'trace_id':'unit-trace', 'scope':scope.to_dict()}, source='bridge.reuse', project='unit-project')
    event = ledger.append('work.outcome', {'workstream':'unit-task', 'trace_id':'unit-trace',
        'tests_passed':True, 'strict_launcher_qualified':False,
        'artifact_sha256':'a'*64}, source='codex-independent-verifier', project='unit-project', parent_event_ids=[prepared.event_id])
    result = recovery().recover_workstream(scope, ledger_root=ledger.root)
    assert result['coverage'] == 'complete'
    assert result['current_outcome']['event_id'] == event.event_id
    assert result['current_outcome']['payload']['strict_launcher_qualified'] is False
    assert result['execution_permission'] is False
    assert result['current_outcome']['checksum'] == event.checksum
    assert result.get('outcome_lineage_rule') == 'distinct artifact/trace outcomes do not implicitly supersede one another'
    assert result['next_legal_action']['gate'] == 'ACT'
    assert result['next_legal_action']['opportunity']['intent']['effects'] == ['read']
    assert result['next_legal_action']['opportunity']['authority']['grants'] == ['read']


def test_unrelated_scope_cannot_receive_bound_outcome(tmp_path):
    scope = MemoryScope(user='unit-controller', project='unit-project', repo='unit/repo', task='unit-task')
    ledger = EventLog(tmp_path/'ledger')
    ledger.append('reuse.prepared', {'trace_id':'unit-trace', 'scope':scope.to_dict()}, source='bridge.reuse', project='unit-project')
    ledger.append('work.outcome', {'workstream':'unit-task','trace_id':'unit-trace','tests_passed':True}, source='codex-independent-verifier', project='unit-project')
    for field in ('user','project','repo','task'):
        fields = {key:getattr(scope,key) for key in ('user','project','repo','task')}
        fields[field] = 'unit-unrelated'
        result = recovery().recover_workstream(MemoryScope(**fields), ledger_root=ledger.root)
        assert result['current_outcome'] is None
        assert result['outcomes'] == []


def test_ambiguous_trace_scope_fails_closed(tmp_path):
    scope = MemoryScope(user='unit-controller', project='unit-project', repo='unit/repo', task='unit-task')
    ledger = EventLog(tmp_path/'ledger')
    for task in ('unit-task','unit-other'):
        other = MemoryScope(user=scope.user,project=scope.project,repo=scope.repo,task=task)
        ledger.append('reuse.prepared', {'trace_id':'unit-trace','scope':other.to_dict()},source='bridge.reuse',project=scope.project)
    ledger.append('work.outcome', {'workstream':scope.task,'trace_id':'unit-trace','tests_passed':True},source='codex-independent-verifier',project=scope.project)
    assert recovery().recover_workstream(scope,ledger_root=ledger.root)['current_outcome'] is None


def test_existing_claim_projection_is_attached_without_fixture_text(tmp_path):
    scope = MemoryScope(user='unit-controller', project='unit-project', repo='unit/repo', task='unit-task')
    ledger = EventLog(tmp_path/'ledger')
    ledger.append('work.ready', {'workstream':'unit-task'}, source='unit', project='unit-project')
    result = recovery().recover_workstream(scope, ledger_root=ledger.root)
    assert result.get('claim_history') == []
    assert result['selected_claim_ids'] == []
    assert result['claim_projection_coverage'] == 'complete'


def test_source_outage_is_unavailable_not_cached_success(tmp_path):
    scope = MemoryScope(user='unit-controller', project='unit-project', repo='unit/repo', task='unit-task')
    result = recovery().recover_workstream(scope, ledger_root=tmp_path/'missing')
    assert result['coverage'] == 'unavailable'
    assert result['current_outcome'] is None
    assert result['outcomes'] == []
    assert result['execution_permission'] is False
