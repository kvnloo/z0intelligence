"""Verification density v0: synthetic transcripts and fixture repos only (no real data)."""
import json

import pytest

from z0int import outcome_verifier as ov
from z0int import verification_density as vd

from test_outcome_verifier import DAY, T0, Transcript, home, observed, repo, run  # noqa: F401  (fixtures)


ALL = frozenset({'checked_later', 'tests_suite_new_tests', 'ended_on_error', 'edit_reverted', 'answer_ungrounded'})


@pytest.fixture(autouse=True)
def promoted(monkeypatch):
    """Logic tests run with every verifier promoted; test_shipped_confidence_is_low checks the real default."""
    monkeypatch.setattr(vd, 'PROMOTED', ALL)


def kinds(row):
    return {(s['kind'], s['polarity'], s['confidence']) for s in row['signals']}


def edit(tr, path, old, new, t, created=None, error=False):
    name = 'Write' if created is not None else 'Edit'
    inp = {'file_path': str(path), 'content': new} if name == 'Write' else \
        {'file_path': str(path), 'old_string': old, 'new_string': new}
    tur = {'type': 'create' if created else 'update', 'filePath': str(path)} if name == 'Write' else {'filePath': str(path)}
    tr.tool(name, inp, 'Error: no match' if error else 'ok', t, is_error=error, tur=tur)


def test_turn_types():
    t = ov.TEST_CMD
    assert vd.turn_type({'harness_message': True}, t) == 'handoff'
    assert vd.turn_type({'agents_spawned': 1, 'tool_names': {'Agent': 1}}, t) == 'orchestration'
    assert vd.turn_type({'tool_names': {'SendMessage': 1}}, t) == 'orchestration'
    assert vd.turn_type({'edits': ['a.py'], 'bash': [{'command': 'pytest -q'}]}, t) == 'edit_tested'
    assert vd.turn_type({'edits': ['a.py'], 'bash': []}, t) == 'edit_untested'
    assert vd.turn_type({'bash': [{'command': 'git push origin x'}], 'tool_calls': 1}, t) == 'ops'
    assert vd.turn_type({'bash': [{'command': 'cd /x && git log --oneline | head'}], 'tool_calls': 1}, t) == 'research'
    assert vd.turn_type({'tool_calls': 0}, t) == 'qa'


def test_check_scope():
    t = ov.TEST_CMD
    assert vd.check_scope('cd /r && .venv/bin/python -m pytest -q', 'test', t) == ('whole', [])
    assert vd.check_scope('pytest -q tests/test_a.py -x', 'test', t) == ('paths', ['tests/test_a.py'])
    assert vd.check_scope('pytest -k foo', 'test', t)[0] == 'filtered'
    assert vd.check_scope('pytest tests/test_a.py::test_x', 'test', t)[0] == 'filtered'
    assert vd.check_scope('ruff check src/z0int/x.py', 'lint', t) == ('paths', ['src/z0int/x.py'])


def test_checked_later_pass_in_a_later_turn(tmp_path, home, repo):
    tr = Transcript(cwd=str(repo))
    tr.prompt('p1', 'change app', T0)
    edit(tr, repo / 'src/app.py', 'a = 1', 'a = 10', T0 + 1)
    tr.say('done', T0 + 3)
    tr.prompt('p2', 'run the tests now', T0 + 60)
    tr.bash('pytest -q', T0 + 61, exit=0)
    tr.say('green', T0 + 70)
    tr.write(tmp_path / 'projects')
    observed(home, 'sess-1', 'p1', 'p2')
    rows = run(home, tmp_path / 'projects', T0 + DAY)
    assert ('checked_later', 1, 'medium') in kinds(rows['p1'])
    assert rows['p1']['verification_state'] == 'verified_success'
    sig = next(s for s in rows['p1']['signals'] if s['kind'] == 'checked_later')
    assert sig['turns_later'] == 1 and sig['scope'] == 'whole' and sig['verifier_set'] == 'density'
    assert rows['p1']['turn']['type'] == 'edit_untested'
    v0 = run(home, tmp_path / 'projects', T0 + DAY, density=False)
    assert v0['p1']['verification_state'] == 'unverified'


def test_checked_later_scoped_elsewhere_or_superseded_or_lint(tmp_path, home, repo):
    tr = Transcript(cwd=str(repo))
    tr.prompt('p1', 'change app', T0)
    edit(tr, repo / 'src/app.py', 'a = 1', 'a = 10', T0 + 1)
    tr.bash('ruff check src/app.py', T0 + 7, exit=0)      # lint covers it: low only
    tr.say('done', T0 + 9)
    tr.prompt('p2', 'more', T0 + 60)
    edit(tr, repo / 'src/app.py', 'b = 2', 'b = 20', T0 + 61)  # another turn changes the file before any test
    tr.bash('pytest -q', T0 + 70, exit=1)
    tr.say('red', T0 + 80)
    tr.write(tmp_path / 'projects')
    observed(home, 'sess-1', 'p1', 'p2')
    rows = run(home, tmp_path / 'projects', T0 + DAY)
    assert ('checked_later', 1, 'low') in kinds(rows['p1'])
    assert rows['p1']['verification_state'] == 'unverified'
    assert ('tests_in_turn', -1, 'medium') in kinds(rows['p2'])


def test_checked_later_failure_and_self_judged(tmp_path, home, repo):
    tr = Transcript(cwd=str(repo))
    tr.prompt('p1', 'change app', T0)
    edit(tr, repo / 'src/app.py', 'a = 1', 'a = 10', T0 + 1)
    tr.say('done', T0 + 3)
    tr.prompt('p2', 'test it', T0 + 60)
    tr.bash('pytest src/app.py', T0 + 61, exit=1)
    tr.say('fails', T0 + 70)
    tr.prompt('p3', 'other', T0 + 100)
    edit(tr, repo / 'tests/test_app.py', 'x', 'y', T0 + 101)
    edit(tr, repo / 'src/app.py', 'c = 3', 'c = 30', T0 + 102)
    tr.say('ok', T0 + 103)
    tr.prompt('p4', 'run', T0 + 200)
    tr.bash('pytest', T0 + 201, exit=0)
    tr.say('ok', T0 + 210)
    tr.write(tmp_path / 'projects')
    observed(home, 'sess-1', 'p1', 'p2', 'p3', 'p4')
    rows = run(home, tmp_path / 'projects', T0 + DAY)
    assert ('checked_later', -1, 'medium') in kinds(rows['p1'])
    assert rows['p1']['verification_state'] == 'verified_failure'
    assert ('checked_later', 1, 'low') in kinds(rows['p3'])  # it edited an existing test: self-judged


def test_suite_pass_with_only_new_test_files(tmp_path, home, repo):
    tr = Transcript(cwd=str(repo))
    tr.prompt('p1', 'add feature with tests', T0)
    edit(tr, repo / 'src/app.py', 'a = 1', 'a = 10', T0 + 1)
    edit(tr, repo / 'tests/test_new.py', None, 'def test_x():\n    assert True\n', T0 + 2, created=True)
    tr.bash('python -m pytest -q', T0 + 5, exit=0)
    tr.say('done', T0 + 9)
    tr.prompt('p2', 'again', T0 + 60)
    edit(tr, repo / 'tests/test_new.py', 'True', 'False', T0 + 61)
    tr.bash('python -m pytest -q', T0 + 65, exit=0)
    tr.say('done', T0 + 69)
    tr.write(tmp_path / 'projects')
    observed(home, 'sess-1', 'p1', 'p2')
    rows = run(home, tmp_path / 'projects', T0 + DAY)
    assert ('tests_in_turn', 1, 'low') in kinds(rows['p1'])
    assert ('tests_suite_new_tests', 1, 'medium') in kinds(rows['p1'])
    assert rows['p1']['verification_state'] == 'verified_success'
    assert rows['p2']['verification_state'] == 'unverified'  # edited an existing test file


def test_ended_on_error(tmp_path, home):
    tr = Transcript()
    tr.prompt('p1', 'deploy', T0)
    tr.bash('make deploy', T0 + 1, exit=2)
    tr.bash('cat log.txt', T0 + 3, exit=0)  # probe after the failure does not rescue it
    tr.say('deploy failed because X', T0 + 5)
    tr.prompt('p2', 'retry', T0 + 60)
    tr.bash('make deploy', T0 + 61, exit=2)
    tr.bash('make deploy FORCE=1', T0 + 63, exit=0)
    tr.say('ok', T0 + 65)
    tr.prompt('p3', 'probe only', T0 + 100)
    tr.bash('grep -r foo .', T0 + 101, exit=1)
    tr.say('none', T0 + 102)
    tr.prompt('p4', 'ask', T0 + 200)
    tr.tool('AskUserQuestion', {'questions': []}, "The user doesn't want to proceed", T0 + 201, is_error=True)
    tr.say('ok', T0 + 210)
    tr.write(tmp_path / 'projects')
    observed(home, 'sess-1', 'p1', 'p2', 'p3', 'p4')
    rows = run(home, tmp_path / 'projects', T0 + DAY)
    assert ('ended_on_error', -1, 'medium') in kinds(rows['p1'])
    assert rows['p1']['verification_state'] == 'verified_failure'
    for p in ('p2', 'p3', 'p4'):
        assert not any(s['kind'] == 'ended_on_error' for s in rows[p]['signals']), p


def test_edit_reverted_and_rewritten(tmp_path, home, repo):
    tr = Transcript(cwd=str(repo))
    tr.prompt('p1', 'change', T0)
    edit(tr, repo / 'src/app.py', 'a = 1', 'a = 10', T0 + 1)
    tr.say('done', T0 + 2)
    tr.prompt('p2', 'put it back', T0 + 60)
    tr.bash(f'cd {repo} && git checkout -- src/app.py', T0 + 61, exit=0)
    tr.say('ok', T0 + 62)
    new = 'def helper_one():\n    return 1111\n\ndef helper_two():\n    return 2222\n'
    tr.prompt('p3', 'add helpers', T0 + 100)
    edit(tr, repo / 'src/app.py', 'c = 3', 'c = 3\n' + new, T0 + 101)
    tr.say('done', T0 + 102)
    tr.prompt('p4', 'different approach', T0 + 200)
    edit(tr, repo / 'src/app.py', new, 'X = 1\n', T0 + 201)
    tr.say('done', T0 + 202)
    tr.write(tmp_path / 'projects')
    observed(home, 'sess-1', 'p1', 'p2', 'p3', 'p4')
    rows = run(home, tmp_path / 'projects', T0 + DAY)
    assert ('edit_reverted', -1, 'medium') in kinds(rows['p1'])
    assert ('edit_rewritten', -1, 'low') in kinds(rows['p3'])


def test_grounding(tmp_path, home, repo):
    tr = Transcript(cwd=str(repo))
    tr.prompt('p1', 'where is a set', T0)
    tr.say('It is set at src/app.py:1 and used in src/app.py:3.', T0 + 1)
    tr.prompt('p2', 'and b?', T0 + 60)
    tr.say('See src/nothere.py:12 and src/app.py:900.', T0 + 61)
    tr.prompt('p3', 'url?', T0 + 100)
    tr.say('See https://example.com/src/zzz.py:5 for docs.', T0 + 101)
    tr.write(tmp_path / 'projects')
    observed(home, 'sess-1', 'p1', 'p2', 'p3')
    import os
    os.utime(repo / 'src/app.py', (T0 - 10, T0 - 10))
    rows = run(home, tmp_path / 'projects', T0 + DAY)
    g1 = next(s for s in rows['p1']['signals'] if s['kind'] == 'answer_grounded')
    assert g1['confidence'] == 'low' and g1['citations'] == 2
    g2 = next(s for s in rows['p2']['signals'] if s['kind'] == 'answer_ungrounded')
    assert g2['missing'] == 1 and g2['beyond_eof'] == 1 and rows['p2']['verification_state'] == 'verified_failure'
    assert not any(s['oracle'] == 'grounding' for s in rows['p3']['signals'])


def test_user_cues_v2_are_low_and_ids_only(tmp_path, home):
    tr = Transcript()
    tr.prompt('p1', 'do it', T0)
    tr.say('done', T0 + 1)
    tr.prompt('p2', 'redo the report and please listen to me this time', T0 + 60)
    tr.say('done', T0 + 61)
    tr.prompt('p3', 'great work, push the branch', T0 + 120)
    tr.say('done', T0 + 121)
    tr.write(tmp_path / 'projects')
    observed(home, 'sess-1', 'p1', 'p2', 'p3')
    rows = run(home, tmp_path / 'projects', T0 + DAY)
    c = next(s for s in rows['p1']['signals'] if s['kind'] == 'user_correction_v2')
    assert c['cues'] == ['listen'] and c['confidence'] == 'low'
    a = next(s for s in rows['p2']['signals'] if s['kind'] == 'user_approval')
    assert a['strength'] == 'strong' and a['confidence'] == 'low'
    blob = json.dumps(rows)
    assert 'listen to me' not in blob and 'great work' not in blob


def test_correction_v2_patterns():
    assert vd.correction_cues_v2("why do you keep failing at this, ugh") == ['why_cant_you', 'frustration']
    assert vd.correction_cues_v2('it still isn’t working') == ['didnt_work', 'still_broken']
    assert vd.correction_cues_v2('how is it going? any results?') == []
    assert vd.correction_cues_v2('why do you think that is?') == []
    assert vd.approval_cues('yes that makes sense, continue') == (True, True)
    assert vd.approval_cues('okay proceed') == (False, True)


def test_gh_cache_and_prefetch(tmp_path):
    calls = []

    def fake(args):
        calls.append(tuple(args))
        if args[0] == 'pr':
            return {'state': 'MERGED' if args[2] == '1' else 'OPEN'}
        return {'check_runs': [{'name': 'ci', 'status': 'completed', 'conclusion': 'success'}]}

    cache = tmp_path / 'gh.json'
    gh = ov.GitHub(runner=fake, cache_path=cache)
    gh.prefetch([['pr', 'view', '1'], ['pr', 'view', '2'], ['api', 'repos/a/b/commits/s/check-runs']])
    assert len(calls) == 3
    gh.flush()
    gh2 = ov.GitHub(runner=fake, cache_path=cache, ttl=0)
    assert gh2._get(['pr', 'view', '1'])['state'] == 'MERGED'  # final: reused even with ttl=0
    assert gh2._get(['api', 'repos/a/b/commits/s/check-runs'])['check_runs']
    assert gh2.disk_hits == 2
    gh2._get(['pr', 'view', '2'])  # open PR with ttl=0: looked up again
    assert calls.count(('pr', 'view', '2')) == 2


def test_diagnose_counts_only():
    rows = [
        {'session_id': 's1', 'turn': {'type': 'qa'}, 'verification_state': 'unverified', 'label_class': 'unknown', 'signals': []},
        {'session_id': 's1', 'turn': {'type': 'orchestration'}, 'verification_state': 'verified_success',
         'label_class': 'deterministic_gold', 'signals': [{'kind': 'checked_later', 'polarity': 1, 'confidence': 'medium'}]},
        {'session_id': 's2', 'turn': {'type': 'ops'}, 'verification_state': 'unverified', 'label_class': 'unknown', 'signals': []},
        {'session_id': 's1', 'turn': {'type': 'handoff', 'harness_message': True}, 'verification_state': 'unverified',
         'signals': []},
    ]
    rep = vd.diagnose(rows, lambda sid: 'interactive' if sid == 's1' else 'harness')
    assert rep['live']['turns'] == 2 and rep['live']['resolved'] == 1 and rep['live']['verified_share'] == 0.5
    assert rep['live']['unverified_reasons'] == {'qa:no_signal': 1}
    assert rep['live']['handoff_turns_excluded'] == {'unverified': 1}
    assert rep['harness']['turns'] == 1


def test_shipped_confidence_is_low_until_promoted(tmp_path, home, repo, monkeypatch):
    monkeypatch.setattr(vd, 'PROMOTED', frozenset())
    tr = Transcript(cwd=str(repo))
    tr.prompt('p1', 'change app', T0)
    edit(tr, repo / 'src/app.py', 'a = 1', 'a = 10', T0 + 1)
    tr.say('done', T0 + 3)
    tr.prompt('p2', 'run the tests now', T0 + 60)
    tr.bash('pytest -q', T0 + 61, exit=0)
    tr.say('green', T0 + 70)
    tr.write(tmp_path / 'projects')
    observed(home, 'sess-1', 'p1', 'p2')
    rows = run(home, tmp_path / 'projects', T0 + DAY)
    sig = next(s for s in rows['p1']['signals'] if s['kind'] == 'checked_later')
    assert sig['confidence'] == 'low' and sig['design_confidence'] == 'medium' and sig['label_class'] == 'soft'
    assert rows['p1']['verification_state'] == 'unverified'


def test_piped_test_output_uses_runner_summary(tmp_path, home):
    ce = ov.CHECK_ANY
    assert ov.effective_exit('pytest -q', 0, '', ce) == (0, False)
    assert ov.effective_exit('pytest -q 2>&1 | tail -5', 0, '=== 2 failed, 10 passed in 1s ===', ce) == (1, True)
    assert ov.effective_exit('pytest -q 2>&1 | tail -5', 0, '=== 12 passed in 1s ===', ce) == (0, True)
    assert ov.effective_exit('pytest -q | head -3', 0, 'collecting ...', ce) == (None, True)
    assert ov.effective_exit('set -o pipefail; pytest | tail', 1, '', ce) == (1, False)
    assert ov.effective_exit('pytest | grep passed', 1, '', ce) == (None, True)  # grep's exit, no summary
    assert ov.effective_exit('ruff check . | tail -3', 0, 'Found 3 errors.', ce) == (1, True)
    tr = Transcript()
    tr.prompt('p1', 'run tests', T0)
    tr.bash('cd /x && python -m pytest -q 2>&1 | tail -3', T0 + 1, exit=0, out='ERROR collecting tests/test_a.py\n1 error in 0.2s')
    tr.say('done', T0 + 5)
    tr.write(tmp_path / 'projects')
    observed(home, 'sess-1', 'p1')
    rows = run(home, tmp_path / 'projects', T0 + DAY)
    assert ('tests_in_turn', -1, 'medium') in kinds(rows['p1'])


def test_installing_or_locating_a_runner_is_not_a_test_run():
    assert not ov.TEST_CMD.search('uv pip install -q pytest')
    assert not ov.TEST_CMD.search('which pytest && pytest --version')
    assert ov.TEST_CMD.search('uv pip install -q pytest && python -m pytest -q')
    assert ov.TEST_CMD.search('cargo test --workspace')


def test_label_sample_refuses_git_work_tree(tmp_path, repo):
    with pytest.raises(SystemExit):
        vd.label_sample(repo / 'out', since=0, now=T0, projects=tmp_path / 'none')
