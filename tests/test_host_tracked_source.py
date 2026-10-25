"""Report-only host source law: absent evidence is never clean."""
import datetime as dt
import json

import pytest

from z0int.fsm.state_laws import audit, sensor_host_drift


def receipt(tmp_path, **changes):
    row = {'schema': 'host-drift.v1', 'host': 'test-host',
           'observed_at': dt.datetime.now(dt.timezone.utc).isoformat(),
           'status': 'clean', 'drift': [], 'unknown': [], 'executed': False}
    row.update(changes)
    path = tmp_path / 'latest.json'
    path.write_text(json.dumps(row))
    return {'path': str(path), 'host': 'test-host'}


def finding():
    return {'kind': 'untracked', 'path': '/home/test/.config/systemd/user/x.service',
            'detail': 'No tracked source'}


def test_clean_current_same_host_receipt_passes(tmp_path):
    result = sensor_host_drift(receipt(tmp_path))
    assert result['status'] == 'pass'
    assert result['law'] == 'I'


def test_known_untracked_source_is_violation(tmp_path):
    result = sensor_host_drift(receipt(tmp_path, status='drift', drift=[finding()]))
    assert result['status'] == 'violation'
    assert result['evidence']['drift'] == [finding()]


@pytest.mark.parametrize('changes', [
    {'status': 'unknown'},
    {'status': 'drift', 'drift': [finding()], 'unknown': [finding()]},
    {'status': 'clean', 'drift': [finding()]},
    {'status': 'drift', 'drift': []},
    {'unknown': [{}]}, {'schema': 'other'}, {'observed_at': 'invalid'},
    {'observed_at': '2000-01-01T00:00:00+00:00'},
    {'observed_at': '2999-01-01T00:00:00+00:00'},
    {'observed_at': '2026-10-24T00:00:00'}, {'host': None},
])
def test_incomplete_or_inconsistent_report_is_unknown(tmp_path, changes):
    assert sensor_host_drift(receipt(tmp_path, **changes))['status'] == 'unknown'


def test_wrong_known_host_is_violation(tmp_path):
    assert sensor_host_drift(receipt(tmp_path, host='other-host'))['status'] == 'violation'


def test_absent_receipt_is_unknown(tmp_path):
    assert sensor_host_drift({'path': str(tmp_path / 'missing'), 'host': 'test-host'})['status'] == 'unknown'


def test_receipt_mutation_is_unknown(tmp_path, monkeypatch):
    from z0int.fsm import state_laws
    spec = receipt(tmp_path)
    original = state_laws._read
    calls = 0

    def changed(path):
        nonlocal calls
        data, binding = original(path)
        calls += 1
        if calls > 1:
            binding['sha256'] = 'changed'
        return data, binding

    monkeypatch.setattr(state_laws, '_read', changed)
    assert sensor_host_drift(spec)['status'] == 'unknown'


def test_audit_consumes_declared_report_only(tmp_path):
    spec = receipt(tmp_path)
    before = open(spec['path']).read()
    rows = audit({'host_drift': spec})
    assert [row['status'] for row in rows if row['law'] == 'I'] == ['pass']
    assert open(spec['path']).read() == before
