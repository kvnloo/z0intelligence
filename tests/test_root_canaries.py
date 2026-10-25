"""Synthetic report-only sensor contracts; parent runs on MBP, never live probes."""
import argparse
import json
import os
import sqlite3
import subprocess
from unittest.mock import patch

import pytest

from z0int.fsm import root_canaries as C


def ledger(path, rows):
    path.write_text(''.join(json.dumps(row)+'\n' for row in rows))
    return path


def finding(at, **changes):
    return dict(at=at, gate='O7', mode='report', status='would-refuse', reason='cancelled provider', **changes)


@pytest.mark.parametrize('age,expired', [(259199, False), (259200, True), (259201, True)])
def test_expiry_boundary(age, expired):
    assert C.report_only_expired(True, True, age) == expired
    assert not C.report_only_expired(False, True, age)
    assert not C.report_only_expired(True, False, age)


def test_inventory_original_age_and_distinct_targets(tmp_path):
    path = ledger(tmp_path/'decisions', [finding(0, target='a'), finding(100, target='a'), finding(200, target='b')])
    inventory = C.r7_inventory(path, {'O7': 'report'}, 259200)
    assert inventory[0]['count'] == 2
    assert inventory[0]['expired']
    assert not inventory[1]['expired']
    assert not C.r7_inventory(path, {'O7': 'enforce'}, 259200)[0]['expired']


def test_success_and_mode_transition_clear_old_findings(tmp_path):
    rows = [finding(0), dict(at=10, gate='O7', mode='report', status='ok'), finding(20)]
    path = ledger(tmp_path/'decisions', rows)
    assert C.r7_inventory(path, {'O7': 'report'}, 259200)[0]['first_at'] == 20
    ledger(path, rows+[dict(at=30, gate='O7', mode='off', status='off')])
    assert C.r7_inventory(path, {'O7': 'report'}, 259200) == []


def test_inventory_iso_time_and_future_rejection(tmp_path):
    path = ledger(tmp_path/'decisions', [finding('2026-10-21T00:00:00Z')])
    assert C.r7_inventory(path, {'O7': 'report'}, C.timestamp('2026-10-24T00:00:00Z'))[0]['expired']
    with pytest.raises(ValueError):
        C.r7_inventory(path, {'O7': 'report'}, 0)


def test_timeout_unavailable_not_no_index():
    with patch.object(C.subprocess, 'run', side_effect=subprocess.TimeoutExpired(['ctx'], 7)):
        observation = C.command(['ctx'], 7)
    assert observation['status'] == 'unavailable'
    assert observation['evidence']['reason'] == 'TimeoutExpired'
    assert 'index' not in json.dumps(observation)


def test_search_deadline_refresh_off_and_percentiles():
    observations = [C.result('command', 'pass', elapsed=1, stdout='{}') for _ in range(19)]
    observations.append(C.result('command', 'pass', elapsed=6, stdout='{}'))
    with patch.object(C, 'command', side_effect=observations) as command:
        observed = C.search_canary('ctx', C.Path('/data'), 'gold', 20, 7)
    assert observed['status'] == 'pass'  # nearest-rank p95 is sample 19
    assert observed['evidence']['p95_seconds'] == 1
    argv, timeout = command.call_args.args
    assert argv[argv.index('--refresh')+1] == 'off'
    assert timeout == 7


def test_search_failure_and_malformed_are_not_success():
    with patch.object(C, 'command', return_value=C.result('command', 'fail', returncode=1)):
        assert C.search_canary('ctx', C.Path('/data'), 'q', 20, 7)['status'] == 'fail'
    with patch.object(C, 'command', return_value=C.result('command', 'pass', elapsed=1, stdout='not json')):
        assert C.search_canary('ctx', C.Path('/data'), 'q', 20, 7)['status'] == 'unavailable'


def test_refresh_requires_complete_recent_job(tmp_path):
    directory = tmp_path/'daemon/jobs'
    directory.mkdir(parents=True)
    job = directory/'core-refresh.json'
    job.write_text(json.dumps(dict(outcome='completed', finished_at_ms=1000000)))
    assert C.refresh_canary(tmp_path, 1000+21600)['status'] == 'pass'
    assert C.refresh_canary(tmp_path, 1000+21601)['status'] == 'fail'
    job.write_text(json.dumps(dict(outcome='completed_with_rejections_and_source_failures', finished_at_ms=1000000)))
    assert C.refresh_canary(tmp_path, 1001)['status'] == 'fail'
    job.unlink()
    assert C.refresh_canary(tmp_path, 1001)['status'] == 'unavailable'


def archive(tmp_path):
    database = tmp_path/'archive.db'
    with sqlite3.connect(database) as db:
        db.execute('CREATE TABLE sessions(agent TEXT, file_mtime INTEGER, deleted_at TEXT)')
        db.execute('INSERT INTO sessions VALUES (?, ?, NULL)', ('codex', 1000*10**9))
    return database


def test_independent_raw_inventory_catches_unindexed_session(tmp_path):
    database = archive(tmp_path)
    raw = tmp_path/'raw'
    raw.mkdir()
    new = raw/'never-indexed.jsonl'
    new.write_text('{}')
    os.utime(new, (9000, 9000))
    before = database.read_bytes()
    observed = C.archive_canary(database, {'codex': [raw]}, 5)
    assert observed['status'] == 'fail'
    assert observed['evidence']['agents']['codex']['lag_seconds'] == 8000
    assert before == database.read_bytes()
    os.utime(new, (8200, 8200))
    assert C.archive_canary(database, {'codex': [raw]}, 5)['status'] == 'pass'


def test_missing_raw_coverage_not_pass(tmp_path):
    database = archive(tmp_path)
    assert C.archive_canary(database, {}, 5)['status'] == 'unavailable'
    assert C.archive_canary(database, {'codex': [tmp_path/'absent']}, 5)['status'] == 'unavailable'


def test_timer_requires_completed_successful_run():
    ok = 'Result=success\nExecMainStatus=0\nExecMainExitTimestampMonotonic=12\n'
    with patch.object(C, 'command', return_value=C.result('command', 'pass', stdout=ok)):
        assert C.timer_canary(7)['status'] == 'pass'
    with patch.object(C, 'command', return_value=C.result('command', 'pass', stdout=ok.replace('=12', '=0'))):
        assert C.timer_canary(7)['status'] == 'fail'


def test_no_snapshot_counter_is_unknown_not_zero():
    with patch.object(C, 'command', return_value=C.result('command', 'pass', stdout='{}')):
        assert C.snapshot_canary('ctx', C.Path('/data'), 7)['status'] == 'unavailable'


def test_unknown_and_failed_open_incidents_but_pass_does_not(tmp_path):
    observations = [C.result('ctx.search', 'pass'), C.result('R7', 'unavailable', reason='missing ledger'),
                    C.result('ctx.refresh', 'fail', age_seconds=99999)]
    with patch('z0int.fsm.root_cause.open_incident') as opened:
        C.record_incidents(tmp_path, observations, 1234)
    assert opened.call_count == 2
    assert {call.args[1]['metric'] for call in opened.call_args_list} == {'R7', 'ctx.refresh'}


def test_register_report_expiry_and_explicit_loop():
    parser = argparse.ArgumentParser()
    C.register(parser.add_subparsers())
    args = parser.parse_args(['canary', '--continuous', '--interval', '3600', '--raw-root', 'codex=/raw'])
    assert args.continuous and args.interval == 3600
    assert args.raw_root == ['codex=/raw']
    audit = parser.parse_args(['report-expiry'])
    assert audit.audit_only and not audit.continuous


@pytest.mark.parametrize('value', ['0', '-1', 'nan', 'inf'])
def test_deadlines_must_be_finite_positive(value):
    with pytest.raises(ValueError):
        C.positive(value)


def test_loop_runs_repeatedly_without_service_install():
    args = argparse.Namespace(continuous=True, interval=2)
    with patch.object(C, 'run_once', return_value={'mode': 'report'}) as run, \
         patch.object(C.time, 'sleep', side_effect=[None, KeyboardInterrupt]), \
         patch('builtins.print'):
        assert C.cmd_canaries(args) == 130
    assert run.call_count == 2


def test_control_dropins_are_unleased_and_never_mutated(tmp_path):
    directory = tmp_path/'ctx.service.d'
    directory.mkdir()
    dropin = directory/'50-CPUWeight.conf'
    dropin.write_text('# managed by systemd\n[Service]\nCPUWeight=1\n')
    before = dropin.read_bytes()
    observed = C.control_inventory(tmp_path)
    assert len(observed) == 1
    assert observed[0]['status'] == 'fail'
    evidence = observed[0]['evidence']
    assert evidence['classification'] == 'UN-LEASED'
    assert evidence['unit'] == 'ctx.service'
    assert evidence['properties'] == [{'property': 'CPUWeight', 'value': '1'}]
    assert evidence['mtime'] == dropin.stat().st_mtime
    assert dropin.read_bytes() == before


def test_missing_control_inventory_unknown(tmp_path):
    assert C.control_inventory(tmp_path/'missing')[0]['status'] == 'unavailable'


def test_recurrence_after_closed_incident_opens_new_record(tmp_path):
    with patch('z0int.fsm.root_cause.open_incident',
               side_effect=[ValueError('incident already closed'), {}]) as opened:
        C.record_incidents(tmp_path, [C.result('ctx.search', 'fail')], 1234)
    assert opened.call_count == 2


def test_audit_only_does_not_invoke_live_sensors(tmp_path):
    args = argparse.Namespace(store=tmp_path, decisions=tmp_path/'missing', audit_only=True)
    with patch('z0int.fsm.root_cause.audit_leases', return_value={'mode': 'report'}) as audit, \
         patch.object(C, 'record_incidents') as incidents, \
         patch.object(C, 'command') as command:
        observed = C.run_once(args)
    audit.assert_called_once()
    incidents.assert_called_once()
    command.assert_not_called()
    assert observed['mode'] == 'report'
    assert observed['observations'][0]['status'] == 'unavailable'


def test_raw_roots_discovered_independently_of_archive(tmp_path):
    codex = tmp_path/'.codex/sessions'
    codex.mkdir(parents=True)
    hermes = tmp_path/'.hermes/profiles/new-profile/sessions'
    hermes.mkdir(parents=True)
    roots = C.discover_raw_roots(tmp_path)
    assert roots['codex'] == [codex]
    assert roots['hermes'] == [hermes]


@pytest.mark.parametrize('value', ['codex', '=path', 'codex='])
def test_malformed_raw_root_spec_rejected(value):
    with pytest.raises(ValueError):
        C.raw_root_spec(value)


@pytest.mark.parametrize('status,payload,expected', [
    ('pass', '{}', 'pass'),
    ('pass', 'malformed', 'unavailable'),
    ('fail', '', 'fail'),
    ('unavailable', '', 'unavailable'),
])
def test_agentsview_search_is_bounded_and_reports_latency(status, payload, expected):
    observation = C.result('command', status, stdout=payload, returncode=0 if status == 'pass' else None,
                           reason='TimeoutExpired' if status == 'unavailable' else None)
    with patch.object(C, 'command', return_value=observation) as command:
        observed = C.agentsview_search_canary('/bin/agentsview', 'gold', 7)
    command.assert_called_once_with(
        ['/bin/agentsview', 'session', 'search', 'gold', '--limit', '1', '--format', 'json'], 7)
    assert observed['sensor'] == 'agentsview.search'
    assert observed['status'] == expected
    assert isinstance(observed['evidence']['elapsed_seconds'], (int, float))
    assert observed['evidence']['elapsed_seconds'] >= 0
    assert observed['evidence']['deadline_seconds'] == 7
    assert 'stdout' not in observed['evidence']
