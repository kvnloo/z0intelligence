"""Replay reconstruction (z0int#143 lane D): synthetic AgentsView + transcript fixtures only.

Nothing here reads a real ~/.z0int, ~/.agentsview or transcript, and nothing touches the network.
"""
import fcntl
import json

import pytest

from agentsview_fixture import AVFixture
from test_outcome_verifier import DAY, T0, Transcript
from z0int import episode_split as es
from z0int import episodes as E
from z0int import loop_export as le
from z0int import replay_reconstruct as rr

ROOT_SID = '11111111-aaaa-4bbb-8ccc-000000000001'
SUB_SID = 'agent-sub1'
SECRET = 'zebra-quartz-private-phrase'  # must never appear in any output


_UNPATCHED_WORKTREE_CHECK = rr.in_git_worktree


@pytest.fixture(autouse=True)
def _worktree_check_below_tmp(tmp_path, monkeypatch):
    """A host may have /tmp/.git: bound the (otherwise unchanged) worktree rule to this test's tmp_path."""
    def below(path):
        p = rr.Path(rr.os.path.abspath(path))
        return any((q / '.git').exists() for q in (p, *p.parents) if q == tmp_path or tmp_path in q.parents)
    monkeypatch.setattr(rr, 'in_git_worktree', below)


def _claude(fx, projects, sid=ROOT_SID, *, parent=None, rel='', entry='cli', build=None):
    t = Transcript(session=sid, cwd='/nonexistent/repo')
    (build or _default_turns)(t)
    path = t.write(projects)
    fx.session('claude', sid, started=T0, cwd='/nonexistent/repo', relationship_type=rel, parent_session_id=parent,
               av_id=sid)
    fx.conn.execute('update sessions set file_path = ?, entrypoint = ?, file_hash = ? where id = ?',
                    (str(path), entry, f'h-{sid}', sid))
    return t, path


def _default_turns(t):
    t.prompt('p1', f'run the tests please {SECRET}', T0)
    t.bash('pytest -q', T0 + 5, exit=1, out='1 failed')
    t.say('One test fails.', T0 + 10)
    t.prompt('p2', 'which branch is checked out right now?', T0 + 100)
    t.say('Which repo do you mean?', T0 + 110)
    t.prompt('p3', 'thanks', T0 + 200)
    t.say('ok', T0 + 210)


def _codex(fx):
    sid = fx.session('codex', 'thread-9', started=T0 + 50, cwd='/nonexistent/other')
    fx.user(sid, f'add a helper {SECRET}', T0 + 50)
    fx.tool(sid, 'exec_command', 'Bash', {'command': 'pytest -q'}, 'Process exited with code 0\nOutput:\n3 passed',
            T0 + 55)
    fx.say(sid, 'Added.', T0 + 60)
    return sid


@pytest.fixture
def world(tmp_path):
    home = tmp_path / 'z0home'
    home.mkdir()
    projects = tmp_path / 'projects'
    (tmp_path / 'av').mkdir()
    fx = AVFixture(tmp_path / 'av' / 'sessions.db')
    _claude(fx, projects)
    _codex(fx)
    db = fx.close()
    return {'home': home, 'db': db, 'projects': projects, 'tmp': tmp_path}


def _run(world, **kw):
    return rr.run(world['home'], agentsview=world['db'], as_of=T0 + 30 * DAY, lock=None, host='fixture', **kw)


def _rows(world):
    root = rr.out_root(world['home'])
    return [r['training'] for r in rr.iter_records(root)], [r['episode'] for r in rr.iter_records(root)]


def _all_output_text(world):
    root = rr.out_root(world['home'])
    return '\n'.join(p.read_text() for p in root.rglob('*') if p.is_file())


def test_reconstructs_every_user_turn_claude_first_with_features_and_no_text(world):
    rep = _run(world)
    assert rep['status'] == 'complete'
    assert list(rep['per_harness']) == ['claude-code', 'codex']  # claude sessions are processed first
    assert rep['per_harness']['claude-code']['turns'] == 3 and rep['per_harness']['codex']['turns'] == 1
    rows, eps = _rows(world)
    assert len(rows) == 4 and len(eps) == 4
    for r in rows:
        assert r['provenance'] == 'reconstructed' and r['cohort'] == 'reconstructed'
        assert sorted(r['features']) == sorted(le.FEATURES)  # the live vocabulary: a policy transfers to live rows
        assert r['context']['git'] == rr.GIT_STATE  # git state is unknown, never guessed
        assert r['label']['label_class'].startswith('reconstructed/')
        assert r['observed']['post_decision'] is True
    for e in eps:
        assert e['provenance']['kind'] == 'reconstructed' and not E.is_labelled(e)
        E.validate(e)
    text = _all_output_text(world)
    assert SECRET not in text and 'pytest' not in text and '/nonexistent' not in text and ROOT_SID not in text


def test_git_family_request_is_blocked_on_unknown_git_state_and_plain_request_may_act(world):
    _run(world)
    rows, _ = _rows(world)
    by_q = {r['context']['turn_ordinal']: r for r in rows if r['harness'] == 'claude-code'}
    branch = by_q[2]
    assert branch['features']['family.git.branch'] == 1 and branch['features']['legal.ACT'] == 0
    assert branch['gate'] == 'ASK' and branch['features']['n_unknowns_status=source_unavailable'] >= 1
    assert by_q[3]['gate'] == 'ACT' and by_q[3]['features']['scope_mode=unscoped'] == 1


def test_context_is_prompt_time_only_and_outcome_comes_from_the_verifier(world):
    _run(world)
    rows, _ = _rows(world)
    cc = sorted((r for r in rows if r['harness'] == 'claude-code'), key=lambda r: r['context']['turn_ordinal'])
    assert cc[0]['context']['prior_tool_calls'] == 0 and cc[0]['context']['prior_shell_nonzero_exit'] == 0
    assert cc[1]['context']['prior_shell_nonzero_exit'] == 1 and cc[1]['context']['prior_tests_failed_last'] is True
    assert cc[0]['label']['state'] == 'verified_failure' and cc[0]['label']['y_success'] == 0
    assert cc[0]['label']['label_class'] == 'reconstructed/negative_gold'
    assert cc[1]['observed']['action'] == 'ASK'  # the agent asked back
    codex = next(r for r in rows if r['harness'] == 'codex')
    assert codex['label']['state'] == 'verified_success' and codex['observed']['tool_families'] == {'shell': 1}


def test_watermark_makes_reruns_idempotent_and_a_changed_session_is_recomputed(world):
    _run(world)
    root = rr.out_root(world['home'])
    before = {p: p.read_bytes() for p in (root / 'sessions').rglob('*.jsonl')}
    rep = _run(world)
    assert rep['per_harness']['claude-code']['sessions_unchanged'] == 1
    assert rep['per_harness']['codex']['sessions_unchanged'] == 1
    assert {p: p.read_bytes() for p in (root / 'sessions').rglob('*.jsonl')} == before
    b1 = rr.build(world['home'], agentsview=world['db'], host='fixture')
    b2 = rr.build(world['home'], agentsview=world['db'], host='fixture')
    assert b1['build_id'] == b2['build_id'] and b1['episodes_sha256'] == b2['episodes_sha256']
    t = Transcript(session=ROOT_SID, cwd='/nonexistent/repo')
    _default_turns(t)
    t.prompt('p4', 'one more question about the session history', T0 + 300)
    t.write(world['projects'])
    rep = _run(world)
    assert rep['per_harness']['claude-code']['sessions_reconstructed'] == 1
    assert rep['per_harness']['codex']['sessions_unchanged'] == 1


def test_subagent_session_is_its_own_stratum_on_the_parent_lineage(tmp_path):
    home = tmp_path / 'h'
    home.mkdir()
    (tmp_path / 'av').mkdir()
    fx = AVFixture(tmp_path / 'av' / 'sessions.db')
    _claude(fx, tmp_path / 'p')

    def sub_turns(t):
        t.prompt('s1', 'inspect the module and report', T0 + 20)
        t.say('done', T0 + 30)
    _claude(fx, tmp_path / 'p', sid=SUB_SID, parent=ROOT_SID, rel='subagent', build=sub_turns)
    db = fx.close()
    rr.run(home, agentsview=db, as_of=T0 + DAY, lock=None, host='fixture')
    rows = [r['training'] for r in rr.iter_records(rr.out_root(home))]
    sub = [r for r in rows if r['cohort'] == 'reconstructed-agent']
    root = [r for r in rows if r['cohort'] == 'reconstructed']
    assert len(sub) == 1 and sub[0]['source_cohort'] == 'agent' and sub[0]['features']['cohort=agent'] == 1
    assert {r['group'] for r in root} == {sub[0]['group']}  # lineage: one group per root session


def test_reconstructed_rows_are_never_pooled_with_observed_rows(world):
    _run(world)
    rows, eps = _rows(world)
    with pytest.raises(ValueError):
        le.Table('claude-code', 'interactive', [r for r in rows if r['harness'] == 'claude-code'])
    observed = dict(rows[0], provenance=None, cohort='reconstructed')
    with pytest.raises(ValueError):
        le.Table(rows[0]['harness'], 'reconstructed', [observed])
    le.Table('claude-code', 'reconstructed', [r for r in rows if r['harness'] == 'claude-code'])
    bad = json.loads(json.dumps(eps[0]))
    bad['outcome'] = {'observed': True, 'y_success': 1, 'state': 'verified_success', 'independent': True,
                      'source': 'x', 'label_class': 'deterministic_gold'}
    with pytest.raises(E.EpisodeInvalid):
        E.validate(bad)
    live = json.loads(json.dumps(bad))
    live['provenance'] = {'kind': 'observed'}
    live['outcome']['label_class'] = 'reconstructed/deterministic_gold'
    with pytest.raises(E.EpisodeInvalid):
        E.validate(live)
    obs = dict(json.loads(json.dumps(eps[0])), provenance={'kind': 'observed'})
    obs['outcome'] = E.no_outcome('x')
    with pytest.raises(ValueError):
        es.split([eps[0], obs], eval_start='2026-10-07', eval_end='2026-10-15', provenance='reconstructed')
    ids, man = es.split([obs] + eps, eval_start='2026-10-07', eval_end='2026-10-15')  # observed split ignores them
    assert man['removed'].get('other_provenance:reconstructed') == len(eps)


def test_split_quarantines_groups_with_live_capture_and_writes_stratum_tables(world):
    state = world['home'] / 'state' / 'claude-code'
    state.mkdir(parents=True)
    (state / 'opportunities.jsonl').write_text(json.dumps(
        {'schema': 'z0int.claude_code.opportunity_record.v0', 'session_id': ROOT_SID}) + '\n')
    _run(world)
    rec = rr.build(world['home'], agentsview=world['db'], host='fixture')
    assert rec['strata']['claude-code/reconstructed']['live_capture_group_rows'] == 3
    assert rec['provenance'] == 'reconstructed' and rec['sources']['sessions']['count'] == 2
    man = rr.split(world['home'], eval_start='2026-05-01', eval_end='2026-06-30')
    assert man['removed']['live_capture_group'] == 3
    assert man['strata']['codex/reconstructed']['eval']['turns'] == 1
    with pytest.raises(es.LockConflict):
        rr.split(world['home'], eval_start='2026-10-07', eval_end='2026-10-15')


def test_quiet_lane_exclusive_window_is_respected(world, tmp_path):
    lock = tmp_path / 'quiet-lane.lock'
    holder = open(lock, 'a')
    fcntl.flock(holder, fcntl.LOCK_EX)
    try:
        rep = rr.run(world['home'], agentsview=world['db'], as_of=T0, lock=lock, lock_wait_s=0.5, host='fixture')
        assert rep['status'] == 'skipped_exclusive_window'
        assert not (rr.out_root(world['home']) / 'sessions').exists()
    finally:
        fcntl.flock(holder, fcntl.LOCK_UN)
        holder.close()
    rep = rr.run(world['home'], agentsview=world['db'], as_of=T0, lock=lock, host='fixture')
    assert rep['status'] == 'complete' and rep['lock']['slices'] == 1


def test_output_root_inside_a_git_worktree_is_refused(tmp_path):
    (tmp_path / 'repo' / '.git').mkdir(parents=True)
    assert _UNPATCHED_WORKTREE_CHECK(tmp_path / 'repo' / 'home')
    with pytest.raises(ValueError):
        rr.out_root(tmp_path / 'repo' / 'home')


def test_unmapped_agents_are_counted_not_emitted(tmp_path):
    home = tmp_path / 'h'
    home.mkdir()
    (tmp_path / 'av').mkdir()
    fx = AVFixture(tmp_path / 'av' / 'sessions.db')
    sid = fx.session('cursor', 'c1', started=T0)
    fx.user(sid, 'hello', T0)
    db = fx.close()
    rep = rr.run(home, agentsview=db, as_of=T0, lock=None, host='fixture')
    assert rep['per_harness'] == {}
    assert rr._agentsview_source(db)['unmapped_agents'] == ['cursor']
