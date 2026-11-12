import json
import pytest
from z0int.worker_activity import project_activity, read_activity


def row(trace='a', status='completed', index=0, **values):
    return {'trace_id':trace,'provider':'test','model':'test', 'extra':{'parent_agent':'parent','subagent_id':'worker','status':status,'attempt_index':index}, **values}


def test_duplicate_terminal_and_late_start():
    done=row(input_tokens=10,output_tokens=3,estimated_frontier_tokens_avoided=8)
    worker=project_activity([row(status='started'),done,done,row(status='started')])['workers'][0]
    assert len(worker['attempts']) == 1
    assert worker['known_input_tokens'] == 10
    assert worker['estimated_frontier_tokens_avoided'] == 8
    assert worker['status'] == 'completed'


def test_retry_preserves_failed_usage_and_partial_unknown():
    failed=row(status='incomplete',input_tokens=10,output_tokens=20,estimated_frontier_tokens_avoided=999)
    done=row('b',index=1,input_tokens=5,estimated_frontier_tokens_avoided=7)
    worker=project_activity([done,failed])['workers'][0]
    assert worker['status'] == 'completed'
    assert worker['fallback']
    assert worker['known_input_tokens'] == 15
    assert worker['known_output_tokens'] == 20
    assert worker['unknown_usage_attempts'] == 1
    assert worker['estimated_frontier_tokens_avoided'] == 7


def test_started_is_unknown_and_not_acknowledged():
    worker=project_activity([row(status='started')])['workers'][0]
    assert worker['unknown_usage_attempts'] == 1
    assert worker['estimated_frontier_tokens_avoided'] is None
    assert worker['parent_consumption'] == 'not_recorded'
    assert not worker['quality_verified']


def test_latest_terminal_replaces_usage():
    worker=project_activity([row(input_tokens=10),row(status='incomplete',input_tokens=12)])['workers'][0]
    assert worker['status'] == 'incomplete'
    assert worker['known_input_tokens'] == 12
    assert worker['estimated_frontier_tokens_avoided'] is None


def test_parent_scope_and_unrelated_records():
    other=row();other['extra']['parent_agent']='other'
    assert len(project_activity([row(),other,{}])['workers']) == 2
    assert len(project_activity([row(),other], 'parent')['workers']) == 1


def test_partial_append_and_malformed_committed_line(tmp_path):
    path=tmp_path/'receipts.jsonl'
    path.write_text(json.dumps(row())+'\n'+ '{"trace_id":')
    assert len(read_activity(path)['workers']) == 1
    path.write_text('{bad}\n')
    with pytest.raises(json.JSONDecodeError):read_activity(path)
