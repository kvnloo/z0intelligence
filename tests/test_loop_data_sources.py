"""Loop data sources (z0int#143 lane A): where the tick finds Claude transcripts and which learner it runs.

A host can run several CLAUDE_CONFIG_DIRs; the timer sees none of them. The verifier then finds a captured session's
transcript through ``$Z0INT_CLAUDE_PROJECTS_DIRS`` or the AgentsView index (``sessions.file_path``, read-only). The
learner can be pinned to an evolution-lab checkout with ``learner_path``. Synthetic homes and databases only.
"""
import hashlib
import json

import pytest

from agentsview_fixture import AVFixture
from test_loop_tick import cc_opportunities, learner, set_config, tick, world  # noqa: F401  (fixtures)
from test_outcome_verifier import DAY, T0, Transcript, observed
from test_verify_harness import cc_opportunity
from z0int import agentsview_ro as av
from z0int import outcome_verifier as ov


def indexed(db, sid, file_path, *, agent='claude', deleted=False):
    """One AgentsView session row for a Claude session (raw id, no agent prefix) pointing at ``file_path``."""
    fx = AVFixture(db)
    fx.session(agent, sid, av_id=sid)
    fx.conn.execute('update sessions set file_path = ?, deleted_at = ? where id = ?',
                    (str(file_path), '2026-10-01T00:00:00Z' if deleted else None, sid))
    return fx.close()


def transcript(projects, sid='cc-1', pid='p1'):
    t = Transcript(session=sid)
    t.prompt(pid, 'hello', T0)
    return t.write(projects)


@pytest.fixture
def env(tmp_path, monkeypatch):
    """CLAUDE_CONFIG_DIR is an empty tree (the timer's view); the session lives in another config dir."""
    monkeypatch.setenv('CLAUDE_CONFIG_DIR', str(tmp_path / 'timer-claude'))
    (tmp_path / 'timer-claude' / 'projects').mkdir(parents=True)
    monkeypatch.delenv(ov.EXTRA_PROJECTS_ENV, raising=False)
    av_dir = tmp_path / 'av'
    av_dir.mkdir()
    monkeypatch.setenv('AGENTSVIEW_DATA_DIR', str(av_dir))
    home = tmp_path / 'z0home'
    cc_opportunity(home, 'cc-1', 'p1')
    observed(home, 'cc-1', 'p1')
    return home, av_dir / 'sessions.db', tmp_path / 'other-claude' / 'projects'


# ----------------------------------------------------------------------------- the AgentsView locator
def test_claude_transcript_returns_the_indexed_file_and_never_writes(tmp_path):
    path = transcript(tmp_path / 'cfg' / 'projects')
    db = indexed(tmp_path / 'sessions.db', 'cc-1', path)
    before = hashlib.sha256(db.read_bytes()).hexdigest()
    assert av.claude_transcript('cc-1', db) == path
    assert av.claude_transcript('cc-unknown', db) is None
    assert hashlib.sha256(db.read_bytes()).hexdigest() == before


@pytest.mark.parametrize('case', ['file_gone', 'not_a_projects_tree', 'other_name', 'other_agent', 'deleted'])
def test_claude_transcript_refuses_anything_but_an_existing_claude_transcript(tmp_path, case):
    path = transcript(tmp_path / 'cfg' / 'projects')
    if case == 'file_gone':
        path.unlink()
    elif case == 'not_a_projects_tree':
        path = transcript(tmp_path / 'cfg' / 'elsewhere')
    elif case == 'other_name':
        path = path.rename(path.with_name('cc-2.jsonl'))
    db = indexed(tmp_path / 'sessions.db', 'cc-1', path, agent='codex' if case == 'other_agent' else 'claude',
                 deleted=case == 'deleted')
    assert av.claude_transcript('cc-1', db) is None


def test_claude_transcript_without_agentsview_is_none(tmp_path):
    assert av.claude_transcript('cc-1', tmp_path / 'missing.db') is None


# ----------------------------------------------------------------------------- the verifier
def test_verify_finds_a_transcript_outside_claude_config_dir_through_agentsview(env):
    home, db, other = env
    indexed(db, 'cc-1', transcript(other))
    report = ov.verify_harness('claude-code', root=home, now=T0 + DAY, gh=ov.GitHub(enabled=False))
    assert report['status'] == 'success' and not report['failures']
    assert report['transcripts'] == {'projects_dir': 0, 'extra_projects_dir': 0, 'agentsview_index': 1, 'missing': 0}
    (row,) = report['rows']
    assert row['measurement'].get('transcript') != 'missing' and row['turn'] is not None


def test_verify_searches_the_extra_projects_dirs(env, monkeypatch):
    home, _, other = env
    transcript(other)
    monkeypatch.setenv(ov.EXTRA_PROJECTS_ENV, str(other))
    report = ov.verify_harness('claude-code', root=home, now=T0 + DAY, gh=ov.GitHub(enabled=False))
    assert report['status'] == 'success' and report['transcripts']['extra_projects_dir'] == 1


def test_an_explicit_projects_dir_is_the_only_tree_searched(env):
    home, db, other = env
    indexed(db, 'cc-1', transcript(other))
    empty = other.parent.parent / 'explicit' / 'projects'
    empty.mkdir(parents=True)
    report = ov.verify_harness('claude-code', root=home, projects=empty, now=T0 + DAY, gh=ov.GitHub(enabled=False))
    (fail,) = [f for f in report['failures'] if f['kind'] == 'label_source_empty']
    assert fail['detail']['searched_dirs'] == 1 and fail['detail']['agentsview_index'] is False


def test_no_source_holding_any_session_is_still_label_source_empty(env):
    home, _, _ = env
    report = ov.verify_harness('claude-code', root=home, now=T0 + DAY, gh=ov.GitHub(enabled=False))
    assert report['status'] == 'degraded'
    (fail,) = [f for f in report['failures'] if f['kind'] == 'label_source_empty']
    assert fail['detail']['agentsview_index'] is True and fail['detail']['sessions'] == 1
    assert report['transcripts']['missing'] == 1
    assert 'hello' not in json.dumps(report['failures'])


# ----------------------------------------------------------------------------- the tick
def test_tick_labels_from_an_agentsview_indexed_transcript(world, learner):  # noqa: F811
    cc_opportunities(world.home)
    path = transcript(world.root / 'claude-home' / 'projects', sid='cc-s1', pid='p0')
    (world.root / 'av').mkdir()
    indexed(world.root / 'av' / 'sessions.db', 'cc-s1', path)
    rep = tick(world)
    assert 'label_source_empty' not in rep['degraded_by'] and rep['status'] == 'success'
    assert rep['harnesses']['claude-code']['transcripts']['agentsview_index'] == 1
    assert 'hello' not in json.dumps(rep)


def test_learner_path_pins_the_checkout_without_pythonpath(world, learner, monkeypatch):  # noqa: F811
    monkeypatch.delenv('PYTHONPATH')
    set_config(world.home, learner_path=str(learner.dir))
    cc_opportunities(world.home)
    rep = tick(world)
    assert rep['learner']['status'] == 'ok' and rep['learner']['sha'] == learner.sha
    assert len(learner.calls()) == 1 and 'learner_missing' not in rep['degraded_by']


def test_a_learner_path_without_the_learner_is_learner_missing(world, learner, tmp_path):  # noqa: F811
    set_config(world.home, learner_path=str(tmp_path / 'no-such-checkout'))
    cc_opportunities(world.home)
    rep = tick(world)
    assert rep['learner'] == {'status': 'learner_missing', 'reason': 'learner_path_missing', 'pinned': learner.sha}
    assert learner.calls() == []
