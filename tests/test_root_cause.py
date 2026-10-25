"""Pure law boundaries and report-only persisted incident lifecycle."""
import argparse
import json
from pathlib import Path

import pytest

from z0int.fsm import root_cause as R


def incident():
    return {'id': 'ctx-latency', 'metric': 'ctx.p95', 'units': 'seconds',
            'lower': 0, 'upper': 5, 'opened_at': 0, 'mitigations': ['lease-1']}


def observation(at, value=4):
    return {'metric': 'ctx.p95', 'units': 'seconds', 'at': at, 'value': value}


def lease():
    return {'id': 'lease-1', 'target': 'ctx.service', 'created_at': 0,
            'expires_at': R.DAY, 'before': {'metric': 'ctx.p95', 'units': 'seconds', 'value': 90, 'at': 0},
            'functional_probe': 'ctx search --refresh off'}


def samples():
    return {'process': {'pid': 42, 'starttime': 12, 'end_starttime': 12, 'units': 'bytes',
                        'window_start': 10, 'window_end': 20, 'before': 100, 'after': 130},
            'system': {'units': 'bytes', 'includes_swap': True, 'window_start': 10,
                       'window_end': 20, 'before': 1000, 'after': 1100}}


@pytest.mark.parametrize('function,keys', [
    (R.r1, ('target', 'before_metric', 'functional_probe', 'expiry_valid')),
    (R.r2, ('same_metric', 'in_bounds_twice', 'separated_24h')),
    (R.r4, ('same_window', 'share_at_least_30'))])
def test_boolean_laws(function, keys):
    facts = dict.fromkeys(keys, True)
    assert function(facts) == 'allow'
    for key in keys:
        assert function({**facts, key: False}) == 'refuse'
        assert function({**facts, key: 1}) == 'escalate'
    assert function({}) == 'escalate'


def test_rollback_only_owned_applied_lease():
    assert R.r3({'probe_passed': False, 'owned': True, 'leased': True}) == 'rollback'
    for owned, leased in ((False, True), (True, False), (False, False)):
        assert R.r3({'probe_passed': False, 'owned': owned, 'leased': leased}) == 'escalate'
    assert R.r3({}) == 'escalate'


def test_proposal_cannot_claim_ownership(tmp_path):
    proposal = {**lease(), 'owner': 'z0-guard', 'status': 'applied', 'guard_applied': True}
    result = R.propose_mitigation(tmp_path, proposal, now=0)
    assert result['applied'] is False
    saved = result['lease']
    assert saved['status'] == 'proposed' and saved['owner'] is None and not saved['guard_applied']
    assert R.probe_decision(saved, False) == 'escalate'
    assert R.expiry_decision(saved, R.DAY) == 'escalate'
    owned = {**saved, 'status': 'applied', 'owner': 'z0-guard', 'guard_applied': True}
    assert R.probe_decision(owned, False) == 'rollback'
    assert R.probe_decision(owned, True) == 'allow'
    assert R.expiry_decision(owned, R.DAY) == 'rollback'
    assert not (tmp_path / 'target').exists()


def test_lease_24_hour_boundary():
    assert R.lease_decision(lease(), 0) == 'allow'
    assert R.lease_decision(lease(), R.DAY) == 'refuse'
    assert R.lease_decision({**lease(), 'expires_at': R.DAY + 1}, 0) == 'refuse'
    assert R.lease_decision({**lease(), 'before': {}}, 0) == 'escalate'


@pytest.mark.parametrize('value', [float('nan'), float('inf'), -float('inf'), True, '4'])
def test_nonfinite_unknown(value):
    row = lease()
    row['before']['value'] = value
    assert R.lease_decision(row, 0) == 'escalate'
    data = samples()
    data['process']['after'] = value
    assert R.attribute_decision(data) == 'escalate'
    data = {**incident(), 'observations': [observation(0), observation(R.DAY, value)]}
    assert R.close_decision(data) == 'escalate'


def test_incident_roundtrip(tmp_path):
    opened = R.open_incident(tmp_path, incident())
    assert R.open_incident(tmp_path, incident()) == opened
    assert R.close_incident(tmp_path, 'ctx-latency')['decision'] == 'refuse'
    R.observe_incident(tmp_path, 'ctx-latency', observation(1), now=1)
    R.observe_incident(tmp_path, 'ctx-latency', observation(R.DAY), now=R.DAY)
    assert R.close_incident(tmp_path, 'ctx-latency')['decision'] == 'refuse'
    R.observe_incident(tmp_path, 'ctx-latency', observation(R.DAY + 1), now=R.DAY + 1)
    result = R.close_incident(tmp_path, 'ctx-latency')
    assert result['decision'] == 'allow' and result['incident']['status'] == 'closed'
    assert R.expiry_decision(lease(), R.DAY + 2, [result['incident']]) == 'allow'
    with pytest.raises(ValueError):
        R.observe_incident(tmp_path, 'ctx-latency', observation(R.DAY + 2), now=R.DAY + 2)


def test_recurrence_resets_healthy_window():
    row = {**incident(), 'observations': [observation(1), observation(R.DAY + 1),
                                          observation(R.DAY + 2, 10), observation(2 * R.DAY + 2)]}
    assert R.close_decision(row) == 'refuse'
    row['observations'].append(observation(3 * R.DAY + 2))
    assert R.close_decision(row) == 'allow'


def test_wrong_metric_future_and_traversal_rejected(tmp_path):
    R.open_incident(tmp_path, incident())
    for row in ({**observation(1), 'metric': 'other'}, {**observation(1), 'units': 'ms'}, observation(10)):
        with pytest.raises(ValueError):
            R.observe_incident(tmp_path, 'ctx-latency', row, now=2)
    with pytest.raises(ValueError):
        R.open_incident(tmp_path, {**incident(), 'id': '../escape'})
    with pytest.raises(ValueError):
        R.open_incident(tmp_path, {**incident(), 'upper': 6})


def test_attribution_boundary_and_counter_contract():
    assert R.attribute_decision(samples()) == 'allow'
    data = samples()
    data['process']['after'] = 129
    assert R.attribute_decision(data) == 'refuse'
    data = samples()
    data['system']['window_end'] = 21
    assert R.attribute_decision(data) == 'refuse'
    for section, key, value in [('system', 'units', 'blocks'), ('system', 'includes_swap', False),
                                 ('process', 'end_starttime', 13), ('process', 'after', 99),
                                 ('system', 'after', 1000), ('process', 'pid', True),
                                 ('process', 'before', -1), ('process', 'after', 300)]:
        data = samples()
        data[section][key] = value
        assert R.attribute_decision(data) == 'escalate'


def test_fixture_closes():
    fixture = Path(__file__).parent / 'fixtures/root-cause-incident.json'
    assert R.close_decision(json.loads(fixture.read_text())) == 'allow'


def test_registered_commands_report_only(tmp_path, capsys):
    parser = argparse.ArgumentParser()
    R.register(parser.add_subparsers(required=True))
    path = tmp_path / 'input.json'
    path.write_text(json.dumps(lease()))
    args = parser.parse_args(['mitigate', 'propose', '--input', str(path), '--store', str(tmp_path / 'store')])
    # Use current timestamps for the CLI's real clock.
    proposal = lease()
    proposal['created_at'] = R.time.time()
    proposal['expires_at'] = proposal['created_at'] + R.DAY
    path.write_text(json.dumps(proposal))
    assert args.fn(args) == 0
    result = json.loads(capsys.readouterr().out)
    assert result['mode'] == 'report' and result['applied'] is False
    path.write_text('{"process": {}}')
    args = parser.parse_args(['attribute', '--input', str(path)])
    assert args.fn(args) == 4
    assert json.loads(capsys.readouterr().out)['decision'] == 'escalate'


@pytest.mark.parametrize('delay,expected', [(86399, 'refuse'), (86400, 'allow'), (86401, 'allow')])
def test_recovery_exact_day_boundary(delay, expected):
    row = {**incident(), 'observations': [observation(1, 0), observation(1 + delay, 5)]}
    assert R.close_decision(row) == expected


@pytest.mark.parametrize('share,expected', [(29, 'refuse'), (30, 'allow'), (31, 'allow')])
def test_attribution_exact_share_boundary(share, expected):
    data = samples()
    data['process']['after'] = data['process']['before'] + share
    assert R.attribute_decision(data) == expected


def test_proposed_lease_defaults_to_day(tmp_path):
    proposal = lease()
    del proposal['expires_at']
    result = R.propose_mitigation(tmp_path, proposal, now=0)
    assert result['lease']['expires_at'] == R.DAY


def test_probe_and_expiry_audit_persist_evidence_only(tmp_path):
    R.propose_mitigation(tmp_path, lease(), now=0)
    assert R.audit_leases(tmp_path, now=1)['decision'] == 'escalate'
    recorded = R.record_probe(tmp_path, 'lease-1', {'at': 1, 'passed': True, 'evidence': 'receipt:probe-1'}, now=1)
    assert recorded['decision'] == 'allow' and recorded['executed'] is False
    assert R.audit_leases(tmp_path, now=2)['decision'] == 'allow'
    expired = R.audit_leases(tmp_path, now=R.DAY)
    assert expired['decision'] == 'escalate'
    assert expired['findings'][0]['expired'] is True
    incident_path = tmp_path / 'incidents/lease-review-lease-1.json'
    assert json.loads(incident_path.read_text())['status'] == 'open'
    saved = json.loads((tmp_path / 'mitigations/lease-1.json').read_text())
    assert saved['status'] == 'proposed' and saved['guard_applied'] is False
    failed = R.record_probe(tmp_path, 'lease-1', {'at': 3, 'passed': False, 'evidence': 'receipt:failure'}, now=3)
    assert failed['decision'] == 'escalate' and failed['executed'] is False


def test_mitigation_lifecycle_cli(tmp_path, capsys):
    parser = argparse.ArgumentParser()
    R.register(parser.add_subparsers(required=True))
    store = tmp_path / 'store'
    proposal = lease()
    proposal['created_at'] = R.time.time()
    proposal['expires_at'] = proposal['created_at'] + R.DAY
    path = tmp_path / 'proposal.json'
    path.write_text(json.dumps(proposal))
    args = parser.parse_args(['mitigate', 'propose', '--input', str(path), '--store', str(store)])
    assert args.fn(args) == 0
    capsys.readouterr()
    path.write_text(json.dumps({'at': R.time.time(), 'passed': True, 'evidence': 'receipt:cli'}))
    args = parser.parse_args(['mitigate', 'probe', '--id', 'lease-1', '--input', str(path), '--store', str(store)])
    assert args.fn(args) == 0
    assert json.loads(capsys.readouterr().out)['executed'] is False
    args = parser.parse_args(['mitigate', 'audit', '--store', str(store)])
    assert args.fn(args) == 0
    assert json.loads(capsys.readouterr().out)['findings'][0]['decision'] == 'allow'


def manual_receipt(tmp_path):
    artifact = tmp_path / 'evidence.txt'
    artifact.write_text('synthetic test evidence')
    binding = {'path': artifact.name, 'sha256': R.hashlib.sha256(artifact.read_bytes()).hexdigest()}
    row = {**lease(), 'schema': 'z0.manual_mitigation.v1', 'owner': 'Hermes',
           'approval': {'approved_by': 'Kevin', 'reference': 'synthetic-test-approval'},
           'artifacts': {'baseline': binding, 'probe': binding},
           'probe_observation': {'at': 1, 'passed': True, 'evidence': 'synthetic-probe'}}
    receipt = tmp_path / 'receipt.json'
    receipt.write_text(json.dumps(row))
    return receipt, row


def test_import_manual_does_not_acquire_rollback_ownership(tmp_path):
    receipt, row = manual_receipt(tmp_path)
    result = R.import_manual(tmp_path / 'store', receipt, now=2)
    assert result['decision'] == 'allow' and result['executed'] is False
    saved = result['lease']
    assert saved['owner'] == 'Hermes' and saved['status'] == 'manual'
    assert saved['guard_applied'] is False
    assert R.probe_decision(saved, False) == 'escalate'
    assert R.expiry_decision(saved, R.DAY) == 'escalate'
    assert saved['source_receipt']['sha256'] == R.hashlib.sha256(receipt.read_bytes()).hexdigest()


@pytest.mark.parametrize('change', ['approval', 'expired', 'hash', 'escape', 'owner', 'probe'])
def test_manual_import_requires_complete_valid_receipt(tmp_path, change):
    receipt, row = manual_receipt(tmp_path)
    if change == 'approval':
        row['approval']['approved_by'] = 'unknown'
    elif change == 'expired':
        row['expires_at'] = 1
    elif change == 'hash':
        row['artifacts']['baseline']['sha256'] = 'bad'
    elif change == 'escape':
        row['artifacts']['baseline']['path'] = '../outside.txt'
    elif change == 'owner':
        row['owner'] = 'z0-guard'
    else:
        row['probe_observation']['passed'] = None
    receipt.write_text(json.dumps(row))
    with pytest.raises((ValueError, OSError)):
        R.import_manual(tmp_path / 'store', receipt, now=2)
    assert not (tmp_path / 'store/mitigations').exists()
