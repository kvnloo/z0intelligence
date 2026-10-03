"""Harness-generic verifier report: label_source_empty and idempotent re-runs (synthetic state only)."""
import json

from agentsview_fixture import AVFixture
from test_outcome_verifier import DAY, T0, Transcript, observed
from z0int import harness_capture as hc
from z0int import outcome_verifier as ov


def cc_opportunity(home, session, trace):
    hc.append('claude-code', 'opportunity_record', {
        'schema': hc.schema('claude-code', 'opportunity_record'), 'harness': 'claude-code', 'session_id': session,
        'trace_id': trace, 'turn_key': f'k-{trace}', 'cohort': 'interactive', 'capture': {'is_harness_message': False},
        'gate': 'ACT', 'opportunity': {'trace': {'trace_id': trace, 'harness': 'claude-code'}}}, root=home)


def test_label_source_empty_names_the_resolved_dir_and_degrades_the_report(tmp_path):
    home = tmp_path / 'z0home'
    cc_opportunity(home, 'cc-1', 'p1')
    observed(home, 'cc-1', 'p1')
    empty = tmp_path / 'wrong-config' / 'projects'  # e.g. CLAUDE_CONFIG_DIR unset in a timer unit
    empty.mkdir(parents=True)
    report = ov.verify_harness('claude-code', root=home, projects=empty, now=T0 + DAY, gh=ov.GitHub(enabled=False),
                               write=True)
    assert report['status'] == 'degraded'
    (fail,) = [f for f in report['failures'] if f['kind'] == 'label_source_empty']
    assert fail['detail']['resolved_dir'] == str(empty) and fail['detail']['sessions'] == 1
    assert any(f['kind'] == 'label_source_empty'
               for f in hc._read_jsonl(hc.state_dir('claude-code', home) / 'failures.jsonl'))

    t = Transcript(session='cc-1')
    t.prompt('p1', 'hello', T0)
    t.write(tmp_path / 'projects')
    report = ov.verify_harness('claude-code', root=home, projects=tmp_path / 'projects', now=T0 + DAY,
                               gh=ov.GitHub(enabled=False))
    assert report['status'] == 'success' and not report['failures'] and len(report['rows']) == 1


def test_claude_code_rows_through_verify_harness_equal_verify(tmp_path):
    home = tmp_path / 'z0home'
    (home / 'state' / 'claude-code').mkdir(parents=True)
    t = Transcript(session='cc-2')
    t.prompt('p1', 'fix it', T0)
    t.bash('pytest tests/', T0 + 5, exit=1)
    t.write(tmp_path / 'projects')
    observed(home, 'cc-2', 'p1')
    kw = dict(root=home, projects=tmp_path / 'projects', now=T0 + 60, gh=ov.GitHub(enabled=False))
    assert ov.verify_harness('claude-code', **kw)['rows'] == ov.verify(**kw)


def test_reader_rerun_is_idempotent_by_watermark(tmp_path):
    home = tmp_path / 'z0home'
    fx = AVFixture(tmp_path / 'sessions.db')
    sid = fx.session('codex', 'th-1', started=T0)
    fx.user(sid, 'run the tests', T0)
    fx.bash(sid, 'pytest', T0 + 5, exit=1)
    fx.user(sid, 'again', T0 + 50)
    db = fx.close()
    for tid in ('a', 'b', 'c'):  # 'c' was never indexed: one unjoined failure, written once
        ctx = hc.begin_turn('codex', {'session_id': 'th-1' if tid != 'c' else 'th-2', 'turn_id': tid, 'prompt': 'x'},
                            env={}, root=home)
        hc.record_outcome('codex', ctx, hc.payload_behaviour({}), root=home)
    kw = dict(root=home, agentsview=db, gh=ov.GitHub(enabled=False), write=True)
    first = ov.verify_harness('codex', now=T0 + 3600, **kw)
    files = {p.name: p.read_bytes() for p in hc.state_dir('codex', home).glob('*.jsonl')}
    second = ov.verify_harness('codex', now=T0 + 7200, **kw)
    assert first['appended'] == 3 and second['appended'] == 0
    assert first['manifest']['manifest_sha256'] == second['manifest']['manifest_sha256']
    assert {p.name: p.read_bytes() for p in hc.state_dir('codex', home).glob('*.jsonl')} == files
    assert json.loads(json.dumps(first['manifest']))['harness'] == 'codex'


def test_cli_verify_exits_nonzero_when_the_report_is_degraded(tmp_path, monkeypatch, capsys):
    """A timer or script that checks the exit code must not read a degraded label source as success."""
    from z0int.cli import main
    home = tmp_path / 'z0home'
    monkeypatch.setenv('Z0INT_HOME', str(home))
    fx = AVFixture(tmp_path / 'sessions.db')
    fx.session('hermes', 'S1', started=T0)
    db = fx.close()
    ctx = hc.begin_turn('dsh', {'session_id': 'd1', 'turn_id': 't1', 'prompt': 'x'}, env={}, root=home)
    hc.record_outcome('dsh', ctx, hc.payload_behaviour({}), root=home)
    assert main(['outcomes', 'verify', '--harness', 'dsh', '--agentsview-db', str(db), '--no-gh', '--no-gh-cache',
                 '--dry-run', '--json']) == 3  # agent_missing
    assert json.loads(capsys.readouterr().out)['status'] == 'degraded'
    cc_opportunity(home, 'cc-1', 'p1')
    empty = tmp_path / 'projects'
    empty.mkdir()
    assert main(['outcomes', 'verify', '--no-gh', '--no-gh-cache', '--dry-run', '--projects-dir', str(empty)]) == 3
    assert 'status: degraded' in capsys.readouterr().out
