"""Labels for every harness: the AgentsView turn reader, one join rule per harness, the cohort classifier.

Synthetic AgentsView databases (tests/agentsview_fixture.py, real schema, no real rows) and synthetic captured
turns written through the C1 capture core. Nothing here reads a real ~/.z0int, ~/.agentsview or transcript.
"""
import json
import sqlite3

import pytest

from agentsview_fixture import AVFixture
from test_outcome_verifier import DAY, T0, Transcript, commit, gitc, observed
from z0int import harness_capture as hc
from z0int import harness_id
from z0int import loop_export as le
from z0int import outcome_verifier as ov

try:
    from z0int import turn_readers as tr
except ImportError:  # red phase: the reader module does not exist yet; each test then fails on its own
    tr = None

AGENT = {'hermes': 'hermes', 'codex': 'codex', 'grok': 'grok', 'omp': 'omp', 'omo': 'omo', 'dsh': 'deepseek-harness'}
AV_HARNESSES = tuple(AGENT)


@pytest.fixture
def home(tmp_path):
    h = tmp_path / 'z0home'
    h.mkdir()
    return h


def capture(home, harness, session, traces, env=None):
    """Captured prompt + outcome rows exactly as the C1 capture core writes them."""
    for tid in traces:
        payload = {'session_id': session, 'turn_id': tid, 'prompt': 'synthetic prompt'}
        ctx = hc.begin_turn(harness, payload, env=env or {}, root=home)
        hc.record_outcome(harness, ctx, hc.payload_behaviour({}), root=home)


def verify(harness, home, db, now=T0 + DAY, gh=None, **kw):
    return ov.verify_harness(harness, root=home, agentsview=db, now=now, gh=gh or ov.GitHub(enabled=False), **kw)


def by_trace(report):
    return {r['trace_id']: r for r in report['rows']}


# Each harness's shell result as its AgentsView parser stores it: (tool name, result_content, tool_result_events
# status; None = no row). Only an exit-code line in the text is exit evidence: Grok's ACP 'completed' only says the
# tool call finished (grok.go maps nothing but 'failed' to 'errored'), oh-my-pi prints its notice only on failure and
# DSH results carry no code at all.
SHELL_SHAPE = {
    'codex': lambda exit, out: ('exec_command', f'Process exited with code {exit}\nOutput:\n{out}', ''),
    'hermes': lambda exit, out: ('terminal', json.dumps({'output': out, 'exit_code': exit, 'error': None}), None),
    'omp': lambda exit, out: ('bash', f'{out}\n\nCommand exited with code {exit}' if exit else out, None),
    'omo': lambda exit, out: ('bash', f'{out}\n\nCommand exited with code {exit}' if exit else out, None),
    'dsh': lambda exit, out: ('bash', out, None),
    'grok': lambda exit, out: ('run_terminal_command', out, 'completed'),
}
# Which test-run polarities each harness's shell results can carry (recorded in the report and table manifests).
TEST_LABEL_POLARITY = {'codex': 'both', 'hermes': 'both', 'omp': 'failure_only', 'omo': 'failure_only',
                       'dsh': 'sparse', 'grok': 'none'}


class AVWriter:
    """The Transcript builder's interface over one AgentsView session (same scenario, other label source)."""

    def __init__(self, fx, sid, harness=None):
        self.fx, self.sid, self.harness = fx, sid, harness

    def prompt(self, pid, text, t):
        self.fx.user(self.sid, text, t)

    def say(self, text, t):
        self.fx.say(self.sid, text, t)

    def bash(self, command, t, exit=0, out=''):
        if self.harness is None:
            return self.fx.bash(self.sid, command, t, exit=exit, out=out)
        name, result, status = SHELL_SHAPE[self.harness](exit, out)
        self.fx.tool(self.sid, name, 'Bash', {'command': command}, result, t, status=status)


def scenario(w, repo):
    """Tests, a reverted commit, a merged PR, a self-merged PR and a correction cue, in one session."""
    w.prompt('p1', 'fix the parser', T0)
    w.bash('pytest tests/', T0 + 5, exit=1, out='2 failed')
    w.prompt('p2', 'tweak the c value', T0 + 100)
    w.bash('git commit -m "feat: tweak c value"', T0 + 109, out=f'[main {repo["sha"][:7]}] feat: tweak c value')
    w.prompt('p3', 'open a pr', T0 + 200)
    w.bash('gh pr create --fill', T0 + 205, out='https://github.com/o/r/pull/7\n')
    w.prompt('p4', 'and one more', T0 + 300)
    w.bash('gh pr create --fill', T0 + 305, out='https://github.com/o/r/pull/9\n')
    w.bash('gh pr merge 9 --squash -R o/r', T0 + 310)
    w.prompt('p5', 'make the parser handle empty input', T0 + 400)
    w.bash('uv run pytest -q', T0 + 410, exit=1, out='1 failed')
    w.bash('uv run pytest -q', T0 + 420, out='5 passed')
    w.say('Done.', T0 + 430)
    w.prompt('p6', "that's wrong, undo it", T0 + 500)
    w.say('ok', T0 + 510)
    w.prompt('p7', 'thanks', T0 + 600)
    w.say('ok', T0 + 610)


def fake_gh():
    states = {7: {'state': 'MERGED'}, 9: {'state': 'MERGED'}}
    return lambda args: states[int(args[2])]


def signals(row):
    return [(s['kind'], s['polarity'], s['confidence'], s['label_class']) for s in row['signals']]


# ----------------------------------------------------------------------------- 1. same signal semantics as CC
@pytest.mark.parametrize('version', [113, 74])  # 74: the real v0.39 schema (no sessions.session_kind)
@pytest.mark.parametrize('harness', AV_HARNESSES)
def test_verify_harness_gives_the_claude_code_signal_semantics_from_agentsview(tmp_path, home, harness, version):
    repo = tmp_path / 'repo'
    repo.mkdir()
    gitc(repo, 'init', '-q', '-b', 'main')
    commit(repo, 'src/app.py', 'a = 1\nb = 2\nc = 3\n', 'init', T0 - DAY)
    sha = commit(repo, 'src/app.py', 'a = 1\nb = 2\nc = 30\n', 'feat: tweak c value', T0 + 110)
    gitc(repo, 'revert', '--no-edit', sha, t=T0 + DAY)
    now = T0 + 2 * DAY

    # the Claude Code verifier on the same scenario is the reference
    cc_home = tmp_path / 'cc'
    (cc_home / 'state' / 'claude-code').mkdir(parents=True)
    t = Transcript(session='cc-1', cwd=str(repo))
    scenario(t, {'sha': sha})
    t.write(tmp_path / 'projects')
    observed(cc_home, 'cc-1', *[f'p{i}' for i in range(1, 8)])
    cc = {r['trace_id']: r for r in ov.verify(root=cc_home, projects=tmp_path / 'projects', now=now,
                                               gh=ov.GitHub(True, fake_gh()))}

    fx = AVFixture(tmp_path / 'sessions.db', user_version=version)
    sid = fx.session(AGENT[harness], 'S1', started=T0, cwd=str(repo))
    scenario(AVWriter(fx, sid, harness), {'sha': sha})  # each harness's own shell result shape
    db = fx.close()
    capture(home, harness, 'S1', [f't{i}' for i in range(1, 8)])
    report = verify(harness, home, db, now=now, gh=ov.GitHub(True, fake_gh()))
    rows = by_trace(report)

    assert report['status'] == 'success' and len(rows) == 7
    polarity = TEST_LABEL_POLARITY[harness]
    exit_derived = ('tests_in_turn', 'ended_on_error')  # the signals only an exit code can give
    tests = lambda r: [s for s in signals(r) if s[0] in exit_derived]
    for i in range(1, 8):
        av, ref = rows[f't{i}'], cc[f'p{i}']
        assert av['schema'] == f'z0int.{harness.replace("-", "_")}.turn_outcome_verified.v0'
        assert av['harness'] == harness and av['join']['state'] == 'joined' and av['join']['ordinal'] == i
        # commit/revert, PR merge (self-merge downgrade) and correction cues: always the CC semantics
        assert [s for s in signals(av) if s[0] not in exit_derived] == \
            [s for s in signals(ref) if s[0] not in exit_derived], f't{i}'
        if polarity == 'both':  # exit evidence on every run: identical to CC, test labels included
            assert (signals(av), av['verification_state'], av['label_class']) == \
                (signals(ref), ref['verification_state'], ref['label_class']), f't{i}'
        elif polarity == 'failure_only':  # a failing run is labelled, a passing one is unknown
            assert all(s[1] in (-1, 0) for s in tests(av)), f't{i}'  # 0: execution_only, no label
        else:  # no exit evidence: a test run is never a label (at most execution_only)
            assert all(s[1] == 0 for s in tests(av)), f't{i}'
    if polarity in ('both', 'failure_only'):
        assert rows['t1']['verification_state'] == 'verified_failure'
    else:
        assert rows['t1']['verification_state'] != 'verified_success'
    assert report['test_label_polarity'] == polarity
    assert any(s['kind'] == 'commit_reverted' for s in rows['t2']['signals'])
    assert rows['t3']['verification_state'] == 'verified_success'
    assert next(s for s in rows['t4']['signals'] if s['kind'] == 'pr_merged')['merged_by_agent']
    assert rows['t4']['verification_state'] == 'unverified'
    # t5: the passing rerun vs the correction cue; without exit evidence for a pass only the correction is left
    assert rows['t5']['verification_state'] == ('contested' if polarity == 'both' else 'verified_failure')
    assert next(s for s in rows['t5']['signals'] if s['kind'] == 'user_correction')['cues'] == ['undo', 'thats_wrong']
    blob = json.dumps(report['rows'])
    assert 'parser' not in blob and 'undo it' not in blob and 'pytest' not in blob


# ----------------------------------------------------------------------------- 2-6. one join rule per harness
def two_turn_session(fx, harness, raw, t=T0, **kw):
    sid = fx.session(AGENT[harness], raw, started=t, **kw)
    fx.user(sid, 'first', t)
    fx.say(sid, 'ok', t + 1)
    fx.user(sid, 'second', t + 10)
    fx.say(sid, 'ok', t + 11)
    return sid


@pytest.mark.parametrize('version', [113, 74])
def test_join_rule_hermes_session_plus_user_message_ordinal(tmp_path, home, version):
    fx = AVFixture(tmp_path / 'sessions.db', user_version=version)
    two_turn_session(fx, 'hermes', '20260930_101010_ab12')
    db = fx.close()
    tids = ['20260930_101010_ab12:task:u1', '20260930_101010_ab12:task:u2']
    capture(home, 'hermes', '20260930_101010_ab12', tids)
    rows = by_trace(verify('hermes', home, db))
    assert [rows[t]['join']['ordinal'] for t in tids] == [1, 2]
    assert rows[tids[0]]['join'] == {'rule': tr.JOIN_RULES['hermes'].name, 'state': 'joined', 'ordinal': 1,
                                     'candidates': 1}
    assert rows[tids[0]]['turn_key'] == harness_id.turn_key('hermes', '20260930_101010_ab12', tids[0])


def test_join_rule_hermes_two_candidate_rows_are_unjoined_and_counted(tmp_path, home):
    fx = AVFixture(tmp_path / 'sessions.db')
    two_turn_session(fx, 'hermes', 'S2')
    two_turn_session(fx, 'hermes', 'S2-copy', source_session_id='S2')  # a second row claims the same session
    db = fx.close()
    capture(home, 'hermes', 'S2', ['S2:a', 'S2:b'])
    report = verify('hermes', home, db, write=True)
    assert {r['join']['state'] for r in report['rows']} == {'unjoined'}
    assert all(r['verification_state'] == 'unverified' and not r['signals'] for r in report['rows'])
    assert report['join']['unjoined'] == 2 and report['join']['joined'] == 0
    fails = [f for f in report['failures'] if f['kind'] == 'unjoined']
    assert len(fails) == 2 and {f['detail']['reason'] for f in fails} == {'ambiguous_session'}
    on_disk = hc._read_jsonl(hc.state_dir('hermes', home) / 'failures.jsonl')
    assert sum(1 for f in on_disk if f['kind'] == 'unjoined') == 2


def test_join_rule_codex_hook_session_and_turn_id_by_user_message_ordinal(tmp_path, home):
    """Codex: hook session_id is the rollout thread id, AgentsView row codex:<thread> (INFERRED, pinned here)."""
    fx = AVFixture(tmp_path / 'sessions.db')
    two_turn_session(fx, 'codex', '019a0c2e-1111-7000-8000-000000000001')
    db = fx.close()
    for tid in ('turn-a', 'turn-b'):
        ctx = hc.begin_turn('codex', {'hook_event_name': 'UserPromptSubmit', 'session_id':
                                      '019a0c2e-1111-7000-8000-000000000001', 'turn_id': tid, 'prompt': 'x'},
                            env={}, root=home)
        hc.record_outcome('codex', ctx, hc.payload_behaviour({}), root=home)
    rows = by_trace(verify('codex', home, db))
    assert (rows['turn-a']['join']['ordinal'], rows['turn-b']['join']['ordinal']) == (1, 2)
    assert 'INFERRED' in tr.JOIN_RULES['codex'].doc


def test_join_rule_codex_ordinal_mismatch_is_unjoined(tmp_path, home):
    fx = AVFixture(tmp_path / 'sessions.db')
    sid = two_turn_session(fx, 'codex', 'th-1')
    fx.user(sid, 'a third message the capture never saw', T0 + 20)
    db = fx.close()
    capture(home, 'codex', 'th-1', ['turn-a', 'turn-b'])
    report = verify('codex', home, db)
    assert {r['join']['state'] for r in report['rows']} == {'unjoined'}
    assert {f['detail']['reason'] for f in report['failures'] if f['kind'] == 'unjoined'} == {'ordinal_mismatch'}


def test_join_rule_grok_session_env_by_user_message_ordinal(tmp_path, home):
    fx = AVFixture(tmp_path / 'sessions.db')
    two_turn_session(fx, 'grok', 'g-42')
    db = fx.close()
    for i in range(2):  # Grok hooks carry no session or turn id: GROK_SESSION_ID + the hashed payload
        ctx = hc.begin_turn('grok', {'hookEventName': 'UserPromptSubmit', 'prompt': f'p{i}', 'timestamp': f'T{i}'},
                            env={'GROK_SESSION_ID': 'g-42'}, root=home)
        hc.record_outcome('grok', ctx, hc.payload_behaviour({}), root=home)
    report = verify('grok', home, db)
    assert sorted(r['join']['ordinal'] for r in report['rows']) == [1, 2]
    assert {r['session_id'] for r in report['rows']} == {'g-42'}


def test_join_rule_grok_ambiguity_is_unjoined(tmp_path, home):
    fx = AVFixture(tmp_path / 'sessions.db')
    two_turn_session(fx, 'grok', 'g-1')
    two_turn_session(fx, 'grok', 'g-1b', source_session_id='g-1')
    db = fx.close()
    capture(home, 'grok', 'g-1', ['x', 'y'])
    assert {r['join']['state'] for r in verify('grok', home, db)['rows']} == {'unjoined'}


@pytest.mark.parametrize('harness', ['omp', 'omo'])
def test_join_rule_omp_omo_bridge_session_by_user_message_ordinal(tmp_path, home, harness):
    fx = AVFixture(tmp_path / 'sessions.db')
    two_turn_session(fx, harness, 'bridge-7')
    two_turn_session(fx, harness, 'bridge-8')
    two_turn_session(fx, harness, 'bridge-8b', source_session_id='bridge-8')
    db = fx.close()
    capture(home, harness, 'bridge-7', ['tr-1', 'tr-2'])
    capture(home, harness, 'bridge-8', ['tr-3', 'tr-4'])
    rows = by_trace(verify(harness, home, db))
    assert (rows['tr-1']['join']['ordinal'], rows['tr-2']['join']['ordinal']) == (1, 2)
    assert rows['tr-3']['join']['state'] == rows['tr-4']['join']['state'] == 'unjoined'


def test_join_rule_dsh_lineage_turn_key_joins_deepseek_harness(tmp_path, home):
    fx = AVFixture(tmp_path / 'sessions.db')
    two_turn_session(fx, 'dsh', 'dsh-sess-1')
    db = fx.close()
    capture(home, 'dsh', 'dsh-sess-1', ['lineage-tk-1', 'lineage-tk-2'])
    rows = by_trace(verify('dsh', home, db))
    assert rows['lineage-tk-2']['join'] == {'rule': tr.JOIN_RULES['dsh'].name, 'state': 'joined', 'ordinal': 2,
                                            'candidates': 1}


def test_join_rule_dsh_without_the_deepseek_harness_agent_is_reader_unavailable(tmp_path, home):
    fx = AVFixture(tmp_path / 'sessions.db')
    two_turn_session(fx, 'hermes', 'S1')  # an index with other agents only (AgentsView before G-AV)
    db = fx.close()
    capture(home, 'dsh', 'dsh-sess-1', ['lineage-tk-1'])
    report = verify('dsh', home, db, write=True)
    assert report['status'] == 'degraded' and report['rows'] == []
    (fail,) = [f for f in report['failures'] if f['kind'] == 'reader_unavailable']
    assert fail['detail'] == {'reason': 'agent_missing', 'agent': 'deepseek-harness'}
    assert any(f['kind'] == 'reader_unavailable'
               for f in hc._read_jsonl(hc.state_dir('dsh', home) / 'failures.jsonl'))


# ----------------------------------------------------------------------------- 7. cohort classifier
@pytest.mark.parametrize('harness,meta,expected', [
    ('hermes', {'project': 'hermes-cron'}, 'automated'),
    ('hermes', {'project': 'hermes-kanban'}, 'automated'),
    ('hermes', {'project': 'hermes-cluster'}, 'automated'),
    ('hermes', {'project': 'hermes-cli'}, 'interactive'),
    ('codex', {'session_kind': 'non-interactive'}, 'automated'),
    ('codex', {'session_kind': 'roborev'}, 'automated'),
    ('codex', {'session_kind': ''}, 'interactive'),
    ('grok', {'is_automated': 1}, 'automated'),
    ('omp', {'relationship_type': 'subagent', 'parent_session_id': 'omp:root'}, 'agent'),
    ('omo', {'relationship_type': 'subagent', 'parent_session_id': 'omo:root'}, 'agent'),
    ('dsh', {}, 'interactive'),
])
def test_cohort_classifier_per_harness(harness, meta, expected):
    assert tr.classify_cohort(harness, dict({'agent': AGENT[harness]}, **meta)) == expected


def test_cohort_classifier_capture_flags_win_and_no_metadata_is_unknown():
    assert tr.classify_cohort('codex', {'agent': 'codex'}, capture={'cohort': 'harness'}) == 'harness'
    assert tr.classify_cohort('omp', {'agent': 'omp'}, capture={'cohort': 'agent'}) == 'agent'
    assert tr.classify_cohort('hermes', {'agent': 'hermes', 'project': 'hermes-cli'},
                              capture={'capture': {'is_harness_message': True}, 'cohort': 'interactive'}) == 'harness'
    assert tr.classify_cohort('hermes', None) == 'unknown'


@pytest.mark.parametrize('version', [113, 74])
def test_cohort_lands_on_verified_rows_and_never_pools_into_interactive(tmp_path, home, version):
    fx = AVFixture(tmp_path / 'sessions.db', user_version=version)
    two_turn_session(fx, 'hermes', 'cron_job1_20260930', project='hermes-cron')
    two_turn_session(fx, 'hermes', 'cli-1', project='hermes-cli')
    db = fx.close()
    capture(home, 'hermes', 'cron_job1_20260930', ['c:1', 'c:2'])
    capture(home, 'hermes', 'cli-1', ['i:1', 'i:2'])
    capture(home, 'hermes', 'never-indexed', ['u:1'])
    report = verify('hermes', home, db, now=T0 + 3600, write=True)
    cohorts = {r['trace_id']: r['cohort'] for r in report['rows']}
    assert cohorts['c:1'] == cohorts['c:2'] == 'automated'
    assert cohorts['i:1'] == 'interactive' and cohorts['u:1'] == 'unknown'
    tables = le.build_tables(home, include_unjoined=True)  # outcome-only capture: no feature rows, labels only
    assert {(h, c) for h, c in tables} == {('hermes', 'automated'), ('hermes', 'interactive'), ('hermes', 'unknown')}
    assert len(tables[('hermes', 'interactive')].rows) == 2


def test_claude_code_transcript_cohort_is_unchanged(tmp_path):
    t = Transcript(session='cc-9', cwd='/home/someone/project')
    t.prompt('p1', 'hello', T0)
    t.rows[0]['entrypoint'] = 'cli'
    t.write(tmp_path / 'projects')
    assert le.transcript_cohort('cc-9', tmp_path / 'projects') == 'interactive'
    assert le.transcript_cohort('missing', tmp_path / 'projects') == 'unknown'


# ----------------------------------------------------------------------------- 8. reader guards
def test_reader_is_read_only(tmp_path):
    fx = AVFixture(tmp_path / 'sessions.db')
    two_turn_session(fx, 'hermes', 'S1')
    reader = tr.AgentsViewReader(fx.close())
    assert reader.available
    with pytest.raises(sqlite3.OperationalError):
        reader.conn.execute("insert into sessions (id, project) values ('x', 'y')")


@pytest.mark.parametrize('version', [74, 113])
def test_reader_supports_both_known_user_versions(tmp_path, home, version):
    fx = AVFixture(tmp_path / 'sessions.db', user_version=version)
    two_turn_session(fx, 'omp', 'b1')
    db = fx.close()
    capture(home, 'omp', 'b1', ['a', 'b'])
    assert verify('omp', home, db)['status'] == 'success'


def test_unknown_user_version_is_a_reader_unavailable_row_not_empty_success(tmp_path, home):
    fx = AVFixture(tmp_path / 'sessions.db', user_version=75)
    two_turn_session(fx, 'omp', 'b1')
    db = fx.close()
    capture(home, 'omp', 'b1', ['a', 'b'])
    report = verify('omp', home, db)
    assert report['status'] == 'degraded' and report['rows'] == []
    (fail,) = [f for f in report['failures'] if f['kind'] == 'reader_unavailable']
    assert fail['detail']['reason'] == 'schema'


def test_stale_index_gives_pending_index_never_negative(tmp_path, home):
    fx = AVFixture(tmp_path / 'sessions.db')
    two_turn_session(fx, 'grok', 'g-old', t=T0)
    db = fx.close()
    capture(home, 'grok', 'g-new', ['n1'])  # captured after the index was last synced
    stale = verify('grok', home, db, now=T0 + 48 * 3600, stale_hours=6)
    (row,) = stale['rows']
    assert row['verification_state'] == 'pending_index' and row['join']['state'] == 'pending_index'
    assert row['label_class'] == 'unknown' and not row['signals']
    fresh = verify('grok', home, db, now=T0 + 3600, stale_hours=6)
    (row,) = fresh['rows']
    assert row['verification_state'] == 'unverified' and row['join']['state'] == 'unjoined'
    assert le.label_of(stale['rows'][0])['y_success'] is None


def capture_at(monkeypatch, home, harness, session, turns):
    """Captured turns with a fixed capture time each: [(trace, epoch seconds)]."""
    from agentsview_fixture import iso
    for tid, t in turns:
        monkeypatch.setattr(hc, '_now', lambda t=t: iso(t)[:19] + 'Z')
        capture(home, harness, session, [tid])


def test_ordinal_join_checks_capture_time_alignment(tmp_path, home, monkeypatch):
    """AgentsView has FEWER user messages than were captured (an injected prompt it stores as is_system): turns
    must not be joined one position off; the misaligned ones are unjoined(ordinal_misaligned)."""
    fx = AVFixture(tmp_path / 'sessions.db')
    sid = fx.session('codex', 'th-al', started=T0)
    fx.user(sid, '<injected harness context>', T0, is_system=1)
    fx.user(sid, 'run the tests', T0 + 600)
    fx.bash(sid, 'pytest', T0 + 605, exit=1)
    fx.user(sid, 'thanks', T0 + 1200)
    fx.say(sid, 'ok', T0 + 1201)
    db = fx.close()
    capture_at(monkeypatch, home, 'codex', 'th-al', [('inj', T0), ('t1', T0 + 600), ('t2', T0 + 1200)])
    report = verify('codex', home, db, now=T0 + 3600)
    rows = by_trace(report)
    assert rows['inj']['join']['state'] == rows['t1']['join']['state'] == 'unjoined'
    assert rows['inj']['join']['reason'] == rows['t1']['join']['reason'] == 'ordinal_misaligned'
    assert not rows['inj']['signals'] and not rows['t1']['signals']  # no label borrowed from the wrong turn
    assert report['join'] == {'joined': 0, 'unjoined': 3, 'pending_index': 0}


def test_ordinal_join_alignment_accepts_capture_close_to_the_message(tmp_path, home, monkeypatch):
    fx = AVFixture(tmp_path / 'sessions.db')
    two_turn_session(fx, 'grok', 'g-al', t=T0)
    db = fx.close()
    capture_at(monkeypatch, home, 'grok', 'g-al', [('a', T0 - 2), ('b', T0 + 12)])  # hook clock vs message clock
    rows = by_trace(verify('grok', home, db, now=T0 + 3600))
    assert [rows[t]['join'].get('ordinal') for t in ('a', 'b')] == [1, 2]


def test_since_windows_every_captured_turn_and_the_join_counts(tmp_path, home, monkeypatch):
    fx = AVFixture(tmp_path / 'sessions.db')
    sid = fx.session('omp', 'w1', started=T0)
    fx.user(sid, 'old turn', T0)
    fx.say(sid, 'ok', T0 + 1)
    fx.user(sid, 'new turn', T0 + 2 * DAY)
    fx.say(sid, 'ok', T0 + 2 * DAY + 1)
    db = fx.close()
    capture_at(monkeypatch, home, 'omp', 'w1', [('old', T0), ('new', T0 + 2 * DAY)])
    capture_at(monkeypatch, home, 'omp', 'never-indexed', [('gone', T0)])  # unjoined, but outside the window
    report = verify('omp', home, db, now=T0 + 2 * DAY + 3600, since=T0 + DAY)
    assert sorted(by_trace(report)) == ['new']
    assert report['join'] == {'joined': 1, 'unjoined': 0, 'pending_index': 0}
    assert not [f for f in report['failures'] if f['kind'] == 'unjoined']


# ----------------------------------------------------------------------------- 9. shell results without a status
# The pi (OMP/OMO), Hermes and deepseek-harness parsers write no tool_result_events row: the only evidence is
# tool_calls.result_content in each harness's own shape. A run without a status and without an exit code is
# unknown, never a pass.
SHELL_TOOL = {'omp': 'bash', 'omo': 'bash', 'hermes': 'terminal', 'dsh': 'bash', 'grok': 'run_terminal_command',
              'codex': 'exec_command'}


def one_command_turn(tmp_path, home, harness, command, result, status=None):
    fx = AVFixture(tmp_path / 'sessions.db')
    sid = fx.session(AGENT[harness], 'S1', started=T0, cwd=str(tmp_path))
    fx.user(sid, 'run the tests', T0)
    fx.tool(sid, SHELL_TOOL[harness], 'Bash', {'command': command}, result, T0 + 5, status=status)
    fx.say(sid, 'Done.', T0 + 30)
    db = fx.close()
    capture(home, harness, 'S1', ['t1'])
    (row,) = verify(harness, home, db, now=T0 + DAY)['rows']
    assert row['join']['state'] == 'joined'
    return row


OMP_FAILURES = [  # oh-my-pi bash tool: formatExitCodeNotice -> "Command exited with code N"
    ('pytest -q', '..F..\n1 failed, 4 passed in 0.12s\n\nCommand exited with code 1'),
    ('make test', 'make: *** [Makefile:3: test] Error 2\n\nCommand exited with code 2'),
    ('cargo test', 'test result: FAILED. 3 passed; 1 failed\n\nCommand exited with code 101'),
    ('npm test', 'npm ERR! Test failed.  See above for more details.\n\nCommand exited with code 1'),
]


@pytest.mark.parametrize('harness', ['omp', 'omo'])
@pytest.mark.parametrize('command,result', OMP_FAILURES, ids=[c for c, _ in OMP_FAILURES])
def test_omp_failing_test_run_without_result_event_is_a_verified_failure(tmp_path, home, harness, command, result):
    row = one_command_turn(tmp_path, home, harness, command, result)
    assert row['verification_state'] == 'verified_failure', (command, row['signals'])


def test_exit_code_reads_the_omp_notice_and_is_unknown_without_status_or_code():
    assert tr.exit_code('1 failed\n\nCommand exited with code 1', None, 'omp') == 1
    assert tr.exit_code('oops\nCommand exited with code 101', 'completed', 'omo') == 101
    assert tr.exit_code('5 passed', None, 'omp') is None  # no status, no code: unknown, never a pass
    # a 'completed' status is not exit evidence: Grok/Codex record it for a call that finished, whatever its exit
    assert tr.exit_code('5 passed', 'completed', 'grok') is None
    assert tr.exit_code('..F..\n1 failed, 4 passed', 'completed', 'codex') is None
    assert tr.exit_code('{"output": "1 failed", "exit_code": 1, "error": null}', None, 'hermes') == 1


@pytest.mark.parametrize('harness,command,result', [
    ('omp', 'pytest -q', '..F..\n1 failed, 4 passed in 0.12s'),  # no notice at all
    ('omo', 'npm test', 'npm ERR! Test failed.'),
    ('hermes', 'pytest -q', '{"output": "..F..\\n1 failed, 4 passed in 0.12s", "error": null}'),  # no exit_code
    ('dsh', 'pytest -q', '..F..\n1 failed, 4 passed in 0.12s'),  # DSH: isError never becomes a status
    ('dsh', 'cargo test', 'error: test failed, to rerun pass `--lib`'),
], ids=['omp-no-notice', 'omo-no-notice', 'hermes-json-no-exit-code', 'dsh-pytest', 'dsh-cargo'])
def test_shell_result_without_status_or_exit_code_is_never_verified_success(tmp_path, home, harness, command,
                                                                            result):
    row = one_command_turn(tmp_path, home, harness, command, result)
    assert row['verification_state'] != 'verified_success', row['signals']
    assert not [s for s in row['signals'] if s['kind'] == 'tests_in_turn' and s['polarity'] == 1]


GROK_COMPLETED = [  # Grok's real shape: run_terminal_command, ACP status 'completed', no exit-code line
    ('pytest -q', '..F..\n1 failed, 4 passed in 0.12s'),
    ('pytest -q', 'Traceback (most recent call last):\n  File "x.py", line 1\nImportError: no module y'),
    ('git push', 'fatal: not a git repository (or any of the parent directories): .git'),
    ('cargo test', 'test result: FAILED. 3 passed; 1 failed; 0 ignored'),
    ('pytest -q', '.....\n5 passed in 0.10s'),  # even a passing run: the exit is unknown
]


@pytest.mark.parametrize('command,result', GROK_COMPLETED, ids=['pytest-failed', 'traceback', 'fatal', 'cargo-failed',
                                                                 'pytest-passed'])
def test_grok_completed_tool_call_without_exit_line_is_never_a_pass(tmp_path, home, command, result):
    row = one_command_turn(tmp_path, home, 'grok', command, result, status='completed')
    assert row['verification_state'] != 'verified_success', row['signals']
    assert row['label_class'] != 'deterministic_gold', row['signals']
    assert not [s for s in row['signals'] if s['kind'] == 'tests_in_turn' and s['polarity'] == 1]


def test_codex_completed_status_is_not_exit_evidence_but_its_exit_line_is(tmp_path, home):
    # codex custom_tool_call_output gets 'completed' by default (codex.go); only the text's code counts
    fx = AVFixture(tmp_path / 'sessions.db')
    sid = fx.session('codex', 'S1', started=T0, cwd=str(tmp_path))
    fx.user(sid, 'run the tests', T0)
    fx.tool(sid, 'shell', 'Bash', {'command': 'pytest -q'}, '1 failed, 4 passed', T0 + 5, status='completed')
    fx.say(sid, 'Done.', T0 + 30)
    fx.user(sid, 'again', T0 + 100)
    fx.tool(sid, 'exec_command', 'Bash', {'command': 'pytest -q'}, 'Process exited with code 0\nOutput:\n5 passed',
            T0 + 105, status='')
    fx.say(sid, 'Done.', T0 + 130)
    db = fx.close()
    capture(home, 'codex', 'S1', ['t1', 't2'])
    rows = by_trace(verify('codex', home, db))
    assert rows['t1']['verification_state'] != 'verified_success'
    assert not [s for s in rows['t1']['signals'] if s['kind'] == 'tests_in_turn' and s['polarity'] == 1]
    assert [s['polarity'] for s in rows['t2']['signals'] if s['kind'] == 'tests_in_turn'] == [1]


@pytest.mark.parametrize('harness', AV_HARNESSES)
def test_report_and_table_manifests_record_the_test_label_polarity(tmp_path, home, harness):
    # one-sided test labels (OMP/OMO failures only, DSH rare, Grok none) are recorded, so A0/G-SUFF do not read
    # their class balance as real
    fx = AVFixture(tmp_path / 'sessions.db')
    two_turn_session(fx, harness, 'k1')
    db = fx.close()
    capture(home, harness, 'k1', ['k1:x', 'k1:y'])
    report = verify(harness, home, db, write=True)
    assert report['test_label_polarity'] == TEST_LABEL_POLARITY[harness]
    le.export_tables(tmp_path / 'out', root=home, host='h', include_unjoined=True)
    mans = [json.loads(p.read_text()) for p in (tmp_path / 'out' / harness).glob('*.manifest.json')]
    assert mans and all(m['test_label_polarity'] == TEST_LABEL_POLARITY[harness] for m in mans)


def test_hermes_terminal_json_exit_code_gives_the_label(tmp_path, home):
    row = one_command_turn(tmp_path, home, 'hermes', 'pytest -q',
                           '{"output": "1 failed, 4 passed", "exit_code": 1, "error": null}')
    assert row['verification_state'] == 'verified_failure'


# Exit-like text in the command's OWN output (a JSON-printing assertion diff, a nested process's notice) is never
# the harness's exit evidence: each harness's frame is read where the harness puts it, never the output body.
DECOYS = ['Exit code 0', '"exit_code": 0, "ok": true', 'Process exited with code 0', 'Command exited with code 0']
BODY = 'FAILED tests/test_x.py::test_y - assert {decoy} == {{"exit_code": 1}}\n1 failed, 4 passed in 0.12s'
FRAMED = {  # (harness, result with the harness's own frame around BODY, the frame's exit code or None)
    'omp': lambda body: (f'{body}\n\nCommand exited with code 1', 1),
    'omo': lambda body: (f'{body}\n\nCommand exited with code 1', 1),
    'omp-no-notice': lambda body: (body, None),
    'codex': lambda body: (f'Process exited with code 1\nOutput:\n{body}', 1),
    'codex-exec-header': lambda body: (f'Exit code: 1\nWall time: 0.2 seconds\nOutput:\n{body}', 1),
    'hermes': lambda body: (json.dumps({'output': body, 'exit_code': 1, 'error': None}), 1),
    'hermes-no-code': lambda body: (json.dumps({'output': body, 'error': None}), None),
    'dsh': lambda body: (body, None),
    'dsh-json': lambda body: (json.dumps({'output': body, 'exit_code': 1}), 1),
    'grok': lambda body: (body, None),
}


@pytest.mark.parametrize('decoy', DECOYS)
@pytest.mark.parametrize('case', sorted(FRAMED))
def test_exit_code_comes_from_the_harness_frame_never_the_output_body(tmp_path, home, case, decoy):
    harness = case.split('-')[0]
    result, code = FRAMED[case](BODY.format(decoy=decoy))
    status = 'completed' if harness == 'grok' else '' if harness == 'codex' else None
    row = one_command_turn(tmp_path, home, harness, 'pytest -q', result, status=status)
    assert row['verification_state'] != 'verified_success', (case, row['signals'])
    if code == 1:
        assert row['verification_state'] == 'verified_failure', (case, row['signals'])
    assert tr.exit_code(result, status, harness) == code


@pytest.mark.parametrize('harness', ['omp', 'omo'])
def test_omp_notice_counts_only_as_the_trailing_line(harness):
    assert tr.exit_code('Command exited with code 2', None, harness) == 2  # no output at all
    assert tr.exit_code('x\nCommand exited with code 1\n', None, harness) == 1  # trailing newline
    assert tr.exit_code('Command exited with code 1\nmore output after it', None, harness) is None


# ----------------------------------------------------------------------------- 10. reader errors and v0.39
def test_v039_schema_without_session_kind_joins_and_classifies(tmp_path, home):
    fx = AVFixture(tmp_path / 'sessions.db', user_version=74)
    assert 'session_kind' not in fx.columns  # the real v0.39 schema
    two_turn_session(fx, 'codex', 'th-39')
    two_turn_session(fx, 'codex', 'th-39-auto', is_automated=1)
    two_turn_session(fx, 'omp', 'sub-39', relationship_type='subagent', parent_session_id='omp:root')
    db = fx.close()
    capture(home, 'codex', 'th-39', ['a', 'b'])
    capture(home, 'codex', 'th-39-auto', ['c', 'd'])
    capture(home, 'omp', 'sub-39', ['e', 'f'])
    codex = verify('codex', home, db)
    assert codex['status'] == 'success'
    # v0.39 has no session_kind: a `codex exec` session cannot be told from an interactive one, so a Codex session
    # AgentsView does not flag as automated is 'unknown' there (never pooled into interactive), not a guess
    assert {r['trace_id']: r['cohort'] for r in codex['rows']} == {'a': 'unknown', 'b': 'unknown',
                                                                    'c': 'automated', 'd': 'automated'}
    assert {r['join']['state'] for r in codex['rows']} == {'joined'}
    assert {r['cohort'] for r in verify('omp', home, db)['rows']} == {'agent'}


def test_codex_cohort_without_session_kind_is_unknown_never_interactive():
    v039 = {'id': 'codex:th', 'agent': 'codex', 'is_automated': 0}  # no session_kind column at all
    assert tr.classify_cohort('codex', v039) == 'unknown'
    assert tr.classify_cohort('codex', dict(v039, is_automated=1)) == 'automated'
    assert tr.classify_cohort('codex', dict(v039, session_kind='')) == 'interactive'  # v0.44: the kind decides
    assert tr.classify_cohort('codex', dict(v039, session_kind='non-interactive')) == 'automated'
    assert tr.classify_cohort('omp', {'id': 'omp:s', 'is_automated': 0}) == 'interactive'  # only Codex has exec runs


@pytest.mark.parametrize('broken', ['drop table tool_result_events', 'drop table messages',
                                    'alter table sessions drop column cwd'])
def test_reader_sqlite_error_is_a_reader_unavailable_row_never_an_exception(tmp_path, home, broken):
    fx = AVFixture(tmp_path / 'sessions.db')
    two_turn_session(fx, 'omp', 'b1')
    fx.conn.execute(broken)
    db = fx.close()
    capture(home, 'omp', 'b1', ['a', 'b'])
    report = verify('omp', home, db, write=True)
    assert report['status'] == 'degraded' and report['rows'] == []
    (fail,) = [f for f in report['failures'] if f['kind'] == 'reader_unavailable']
    assert fail['detail']['reason'] in ('error', 'schema')
    assert any(f['kind'] == 'reader_unavailable' for f in hc._read_jsonl(hc.state_dir('omp', home) / 'failures.jsonl'))


# ----------------------------------------------------------------------------- 11. one turn key per harness
@pytest.mark.parametrize('harness', ['omp', 'hermes', 'dsh'])
def test_non_cc_training_rows_carry_the_canonical_turn_key(tmp_path, home, harness):
    fx = AVFixture(tmp_path / 'sessions.db')
    two_turn_session(fx, harness, 'k1')
    db = fx.close()
    capture(home, harness, 'k1', ['k1:x', 'k1:y'])
    report = verify(harness, home, db, write=True)
    canonical = {harness_id.turn_key(harness, 'k1', t) for t in ('k1:x', 'k1:y')}
    assert {r['turn_key'] for r in report['rows']} == canonical
    rows = [r for (h, _), t in le.build_tables(home, include_unjoined=True).items() if h == harness for r in t.rows]
    assert len(rows) == 2 and {r['turn_key'] for r in rows} == canonical
