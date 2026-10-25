"""Law G adapter and finite-predicate contract; no deletion actors exercised."""
from copy import deepcopy
from itertools import product

import pytest

from z0int.fsm.artifact_leases import REQUIRED, SOURCE_FIELDS, audit, evaluate, inspect, register


def candidate():
    return {
        'path': '/scratch/task', 'node': 'mbp', 'observed_at': 200,
        'lease': {'schema': 'z0.artifact-lease.v1', 'owner': 'agent', 'node': 'mbp',
                  'path': '/scratch/task', 'kind': 'worktree', 'created_at': 100,
                  'ttl': 100, 'keep_receipts': ['receipts/proof.json']},
        'allowlist': ['/scratch'], 'live': 'NONLIVE', 'symlink_free': True,
        'git_worktree': True, 'git_archive_verified': True,
        'sources': {field: f'observation:{field}:snapshot-1' for field in SOURCE_FIELDS},
    }


def test_all_boolean_obligations_match_conjunction():
    for values in product((False, True), repeat=len(REQUIRED)):
        assert evaluate(dict(zip(REQUIRED, values))) == ('pass' if all(values) else 'violation')
    for field in REQUIRED:
        facts = dict.fromkeys(REQUIRED, True)
        for unknown in (None, 1, 'true'):
            facts[field] = unknown
            assert evaluate(facts) == 'unknown'


def test_positive_is_report_only_and_does_not_mutate_input():
    row = candidate()
    original = deepcopy(row)
    report = audit({'source': 'reaper-inventory.json', 'artifacts': [row]})
    assert report['status'] == 'pass'
    assert report['mode'] == 'report-only'
    assert report['enforcement_enabled'] is False
    assert report['action'] == 'none'
    assert row == original


@pytest.mark.parametrize(('field', 'value'), [
    ('live', 'LIVE'), ('symlink_free', False), ('git_archive_verified', False),
    ('path', '/outside/task'), ('path', '/scratch-other/task'), ('path', '/scratch'),
    ('observed_at', 199),
])
def test_required_mutants_never_pass(field, value):
    row = candidate()
    row[field] = value
    if field == 'path':
        row['lease']['path'] = value
    assert inspect(row)['blocked'] is True
    assert inspect(row)['status'] != 'pass'


@pytest.mark.parametrize(('field', 'value'), [
    ('live', 'UNKNOWN'), ('live', None), ('symlink_free', None),
    ('git_worktree', None), ('git_archive_verified', None), ('node', 'groot'),
])
def test_unknown_or_wrong_node_fails_closed(field, value):
    row = candidate()
    row[field] = value
    assert inspect(row)['status'] == 'unknown'


@pytest.mark.parametrize('field', SOURCE_FIELDS)
def test_every_source_is_explicit(field):
    row = candidate()
    del row['sources'][field]
    assert inspect(row)['status'] == 'unknown'


@pytest.mark.parametrize(('field', 'value'), [
    ('schema', 'other'), ('created_at', -1), ('created_at', 201),
    ('created_at', float('nan')), ('ttl', 0), ('ttl', True), ('ttl', float('inf')),
    ('owner', ''), ('keep_receipts', ['/secret']), ('keep_receipts', ['../escape']),
    ('keep_receipts', ['.']), ('keep_receipts', None),
])
def test_malformed_lease_is_unknown(field, value):
    row = candidate()
    row['lease'][field] = value
    assert inspect(row)['status'] == 'unknown'


def test_non_worktree_does_not_need_git_archive():
    row = candidate()
    row['git_worktree'] = False
    row['git_archive_verified'] = None
    assert inspect(row)['status'] == 'pass'


def test_empty_inventory_and_malformed_candidates_unknown():
    for spec in ({}, {'artifacts': []}, {'artifacts': [None]}, None):
        report = audit(spec)
        assert report['status'] == 'unknown'
        assert report['action'] == 'escalate_to_Kevin'


def test_cli_registers_report_only_input(tmp_path, capsys):
    import argparse
    import json
    parser = argparse.ArgumentParser()
    register(parser.add_subparsers())
    path = tmp_path / 'input.json'
    path.write_text(json.dumps({'artifacts': [candidate()]}))
    args = parser.parse_args(['artifact-leases', '--input', str(path)])
    assert args.fn(args) == 0
    assert json.loads(capsys.readouterr().out)['mode'] == 'report-only'
    path.write_text('not json')
    assert args.fn(args) == 0
    assert json.loads(capsys.readouterr().out)['status'] == 'unknown'


def test_standalone_main_reaches_report_only_command(tmp_path, capsys):
    import json
    from z0int.fsm.artifact_leases import main
    path = tmp_path / 'inventory.json'
    path.write_text(json.dumps({'artifacts': [candidate()]}))
    assert main(['--input', str(path)]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report['law'] == 'G'
    assert report['enforcement_enabled'] is False
