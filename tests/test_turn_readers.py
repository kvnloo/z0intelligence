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


class AVWriter:
    """The Transcript builder's interface over one AgentsView session (same scenario, other label source)."""

    def __init__(self, fx, sid):
        self.fx, self.sid = fx, sid

    def prompt(self, pid, text, t):
        self.fx.user(self.sid, text, t)

    def say(self, text, t):
        self.fx.say(self.sid, text, t)

    def bash(self, command, t, exit=0, out=''):
        self.fx.bash(self.sid, command, t, exit=exit, out=out)


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
@pytest.mark.parametrize('harness', AV_HARNESSES)
def test_verify_harness_gives_the_claude_code_signal_semantics_from_agentsview(tmp_path, home, harness):
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

    fx = AVFixture(tmp_path / 'sessions.db')
    sid = fx.session(AGENT[harness], 'S1', started=T0, cwd=str(repo))
    scenario(AVWriter(fx, sid), {'sha': sha})
    db = fx.close()
    capture(home, harness, 'S1', [f't{i}' for i in range(1, 8)])
    report = verify(harness, home, db, now=now, gh=ov.GitHub(True, fake_gh()))
    rows = by_trace(report)

    assert report['status'] == 'success' and len(rows) == 7
    for i in range(1, 8):
        av, ref = rows[f't{i}'], cc[f'p{i}']
        assert av['schema'] == f'z0int.{harness.replace("-", "_")}.turn_outcome_verified.v0'
        assert av['harness'] == harness and av['join']['state'] == 'joined' and av['join']['ordinal'] == i
        assert (signals(av), av['verification_state'], av['label_class']) == \
            (signals(ref), ref['verification_state'], ref['label_class']), f't{i}'
    assert rows['t1']['verification_state'] == 'verified_failure'
    assert any(s['kind'] == 'commit_reverted' for s in rows['t2']['signals'])
    assert rows['t3']['verification_state'] == 'verified_success'
    assert next(s for s in rows['t4']['signals'] if s['kind'] == 'pr_merged')['merged_by_agent']
    assert rows['t4']['verification_state'] == 'unverified'
    assert rows['t5']['verification_state'] == 'contested'
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


def test_join_rule_hermes_session_plus_user_message_ordinal(tmp_path, home):
    fx = AVFixture(tmp_path / 'sessions.db')
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


def test_cohort_lands_on_verified_rows_and_never_pools_into_interactive(tmp_path, home):
    fx = AVFixture(tmp_path / 'sessions.db')
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
