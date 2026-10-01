"""Verified outcomes v0: fixture repos + synthetic transcripts (no real data)."""
import json
import os
import subprocess
from pathlib import Path

import pytest

from z0int import outcome_verifier as ov

DAY = 86400
T0 = 1_780_000_000  # fixed epoch for fixture commits


def iso(t):
    return ov._iso(t).replace('Z', '.000Z')


# ----------------------------------------------------------------------------- fixture builders
class Transcript:
    """Builds a Claude Code-shaped transcript JSONL. Ids are deterministic."""

    def __init__(self, session='sess-1', cwd='/nonexistent'):
        self.session, self.cwd, self.rows, self.n = session, cwd, [], 0

    def _id(self, p):
        self.n += 1
        return f'{p}{self.n}'

    def prompt(self, pid, text, t, meta=False):
        row = {'type': 'user', 'promptId': pid, 'sessionId': self.session, 'cwd': self.cwd, 'timestamp': iso(t),
               'message': {'role': 'user', 'content': text}}
        if meta:
            row['isMeta'] = True
        self.rows.append(row)
        self.pid = pid

    def say(self, text, t):
        self.rows.append({'type': 'assistant', 'sessionId': self.session, 'cwd': self.cwd, 'timestamp': iso(t),
                          'message': {'id': self._id('m'), 'model': 'claude-x', 'content': [{'type': 'text', 'text': text}],
                                      'usage': {}}})

    def tool(self, name, inp, result, t, is_error=False, tur=None, cwd=None, dt=1):
        tid = self._id('tu')
        self.rows.append({'type': 'assistant', 'sessionId': self.session, 'cwd': cwd or self.cwd, 'timestamp': iso(t),
                          'message': {'id': self._id('m'), 'model': 'claude-x',
                                      'content': [{'type': 'tool_use', 'id': tid, 'name': name, 'input': inp}]}})
        row = {'type': 'user', 'promptId': self.pid, 'sessionId': self.session, 'cwd': cwd or self.cwd,
               'timestamp': iso(t + dt),
               'message': {'role': 'user', 'content': [{'type': 'tool_result', 'tool_use_id': tid, 'content': result,
                                                        'is_error': is_error}]}}
        if tur is not None:
            row['toolUseResult'] = tur
        self.rows.append(row)

    def bash(self, command, t, exit=0, out='', cwd=None):
        if exit:
            self.tool('Bash', {'command': command}, f'Exit code {exit}\n{out}', t, is_error=True, cwd=cwd)
        else:
            self.tool('Bash', {'command': command}, out, t, cwd=cwd, tur={'stdout': out, 'stderr': '', 'interrupted': False})

    def pr_link(self, repo, number, t):
        self.rows.append({'type': 'pr-link', 'prNumber': number, 'prRepository': repo, 'timestamp': iso(t),
                          'sessionId': self.session})

    def write(self, projects, slug='-fixture'):
        path = projects / slug / f'{self.session}.jsonl'
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(''.join(json.dumps(r) + '\n' for r in self.rows))
        return path


def gitc(repo, *args, t=None):
    env = {**os.environ, 'GIT_AUTHOR_NAME': 'f', 'GIT_AUTHOR_EMAIL': 'f@x', 'GIT_COMMITTER_NAME': 'f',
           'GIT_COMMITTER_EMAIL': 'f@x', 'GIT_CONFIG_GLOBAL': os.devnull, 'GIT_CONFIG_NOSYSTEM': '1'}
    if t is not None:
        env['GIT_AUTHOR_DATE'] = env['GIT_COMMITTER_DATE'] = f'@{int(t)} +0000'
    return subprocess.run(['git', '-C', str(repo), *args], check=True, capture_output=True, text=True, env=env).stdout


def commit(repo, path, body, msg, t):
    (repo / path).parent.mkdir(parents=True, exist_ok=True)
    (repo / path).write_text(body)
    gitc(repo, 'add', '-A')
    gitc(repo, 'commit', '-q', '-m', msg, t=t)
    return gitc(repo, 'rev-parse', 'HEAD').strip()


@pytest.fixture
def repo(tmp_path):
    r = tmp_path / 'repo'
    r.mkdir()
    gitc(r, 'init', '-q', '-b', 'main')
    commit(r, 'src/app.py', 'a = 1\nb = 2\nc = 3\n', 'init', T0 - DAY)
    return r


@pytest.fixture
def home(tmp_path):
    h = tmp_path / 'z0home'
    (h / 'state' / 'claude-code').mkdir(parents=True)
    return h


def observed(home, session, *pids, asked=False):
    with (home / 'state' / 'claude-code' / 'outcomes.jsonl').open('a') as fh:
        for pid in pids:
            fh.write(json.dumps({'schema': ov.OBSERVED_SCHEMA, 'session_id': session, 'trace_id': pid,
                                 'label_kind': 'observed_behaviour_not_optimal', 'asked_user': asked,
                                 'asked_via_tool': False, 'tool_calls': 3, 'assistant_messages': 2}) + '\n')


def run(home, projects, now, gh=None, **kw):
    return {r['trace_id']: r for r in ov.verify(root=home, projects=projects, now=now,
                                                gh=gh or ov.GitHub(enabled=False), **kw)}


# ----------------------------------------------------------------------------- tests
def test_tests_in_turn_last_exit_decides_and_correction_contests(tmp_path, home):
    tr = Transcript()
    tr.prompt('p1', 'make the parser handle empty input', T0)
    tr.bash('cd /x && uv run pytest -q', T0 + 10, exit=1, out='1 failed')
    tr.bash('uv run pytest -q', T0 + 20, exit=0, out='5 passed')
    tr.say('Done.', T0 + 30)
    tr.prompt('p2', "that's wrong, undo it", T0 + 100)
    tr.say('ok', T0 + 110)
    tr.prompt('p3', 'thanks', T0 + 200)
    tr.say('ok', T0 + 210)
    tr.write(tmp_path / 'projects')
    observed(home, 'sess-1', 'p1', 'p2')
    rows = run(home, tmp_path / 'projects', T0 + DAY)
    r1 = rows['p1']
    kinds = {s['kind']: s for s in r1['signals']}
    assert kinds['tests_in_turn']['last_exit'] == 0 and kinds['tests_in_turn']['failed_runs'] == 1
    assert kinds['user_correction']['cues'] == ['undo', 'thats_wrong']
    assert r1['verification_state'] == 'contested'
    assert rows['p2']['verification_state'] == 'unverified'
    # no private text anywhere in the row
    blob = json.dumps(rows)
    assert 'parser' not in blob and 'undo it' not in blob and 'pytest' not in blob


def test_failing_final_tests_are_verified_failure(tmp_path, home):
    tr = Transcript()
    tr.prompt('p1', 'fix it', T0)
    tr.bash('pytest tests/', T0 + 5, exit=1)
    tr.write(tmp_path / 'projects')
    observed(home, 'sess-1', 'p1')
    r = run(home, tmp_path / 'projects', T0 + 60)['p1']
    assert (r['verification_state'], r['label_class'], r['label_confidence']) == ('verified_failure', 'negative_gold', 'medium')


def test_agent_edited_tests_downgrade_a_pass_to_soft(tmp_path, home):
    tr = Transcript()
    tr.prompt('p1', 'go', T0)
    tr.tool('Edit', {'file_path': '/r/tests/test_x.py', 'old_string': 'a', 'new_string': 'b'}, 'ok', T0 + 1)
    tr.bash('pytest', T0 + 5)
    tr.write(tmp_path / 'projects')
    observed(home, 'sess-1', 'p1')
    r = run(home, tmp_path / 'projects', T0 + 60)['p1']
    s = r['signals'][0]
    assert s['tests_edited_in_turn'] and s['confidence'] == 'low' and s['label_class'] == 'soft'
    assert r['verification_state'] == 'unverified'


def test_commit_szz_fix_within_window_is_verified_failure(tmp_path, home, repo):
    tr = Transcript(cwd=str(repo))
    tr.prompt('p1', 'change b', T0)
    sha = commit(repo, 'src/app.py', 'a = 1\nb = 20\nc = 3\n', 'feat: change b', T0 + 10)
    tr.bash('git commit -m "feat: change b"', T0 + 9, out=f'[main {sha[:7]}] feat: change b\n 1 file changed')
    tr.write(tmp_path / 'projects')
    commit(repo, 'src/app.py', 'a = 1\nb = 21\nc = 3\n', 'fix: b off by one', T0 + 2 * DAY)
    observed(home, 'sess-1', 'p1')
    r = run(home, tmp_path / 'projects', T0 + 3 * DAY, fix_days=7)['p1']
    s = next(s for s in r['signals'] if s['kind'] == 'commit_szz_fixed')
    assert s['sha'] == sha and s['confidence'] == 'high'
    assert r['verification_state'] == 'verified_failure'
    # outside the window the same fix does not count, and the commit survived
    r = run(home, tmp_path / 'projects', T0 + 30 * DAY, fix_days=1)['p1']
    assert [s['kind'] for s in r['signals']] == ['commit_survived']
    assert (r['verification_state'], r['label_class']) == ('unverified', 'soft')


def test_fix_touching_other_lines_is_not_blamed(tmp_path, home, repo):
    commit(repo, 'src/app.py', 'a = 1\nb = 2\nc = 3\nd = 4\ne = 5\n', 'grow', T0 - 100)
    tr = Transcript(cwd=str(repo))
    tr.prompt('p1', 'x', T0)
    sha = commit(repo, 'src/app.py', 'a = 1\nb = 2\nc = 3\nd = 4\ne = 50\n', 'feat: e', T0 + 10)
    tr.bash('git commit -qm feat', T0 + 9)  # quiet: resolved by the call's time window
    tr.write(tmp_path / 'projects')
    commit(repo, 'src/app.py', 'a = 10\nb = 2\nc = 3\nd = 4\ne = 50\n', 'fix: a', T0 + DAY)
    observed(home, 'sess-1', 'p1')
    r = run(home, tmp_path / 'projects', T0 + 2 * DAY)['p1']
    assert r['measurement']['commits'] == {'produced': 1, 'resolved': 1, 'matched_by_time_window': 1}
    assert [s['kind'] for s in r['signals']] == ['commit_created']
    assert r['signals'][0]['sha'] == sha and r['signals'][0]['survival_window'] == 'pending'
    assert (r['verification_state'], r['label_class']) == ('unverified', 'execution_only')


def test_revert_is_detected(tmp_path, home, repo):
    tr = Transcript(cwd=str(repo))
    tr.prompt('p1', 'x', T0)
    sha = commit(repo, 'src/app.py', 'a = 1\nb = 2\nc = 30\n', 'feat: tweak c value', T0 + 10)
    tr.bash(f'git -C {repo} commit -m t', T0 + 9, out=f'[main {sha[:7]}] feat: tweak c value')
    tr.write(tmp_path / 'projects', slug='-elsewhere')
    gitc(repo, 'revert', '--no-edit', sha, t=T0 + DAY)
    observed(home, 'sess-1', 'p1')
    r = run(home, tmp_path / 'projects', T0 + 2 * DAY)['p1']
    assert any(s['kind'] == 'commit_reverted' for s in r['signals'])
    assert r['verification_state'] == 'verified_failure'


def test_pr_merged_closed_and_self_merged(tmp_path, home):
    tr = Transcript()
    tr.prompt('p1', 'open a pr', T0)
    tr.bash('gh pr create --fill', T0 + 5, out='https://github.com/o/r/pull/7\n')
    tr.prompt('p2', 'another', T0 + 100)
    tr.pr_link('o/r', 8, T0 + 105)
    tr.prompt('p3', 'and one more', T0 + 200)
    tr.pr_link('o/r', 9, T0 + 205)
    tr.bash('gh pr merge 9 --squash -R o/r', T0 + 210)
    tr.write(tmp_path / 'projects')
    observed(home, 'sess-1', 'p1', 'p2', 'p3')
    states = {7: {'state': 'MERGED'}, 8: {'state': 'CLOSED'}, 9: {'state': 'MERGED'}}
    calls = []

    def fake(args):
        calls.append(args)
        assert args[:2] == ['pr', 'view']
        return states[int(args[2])]
    rows = run(home, tmp_path / 'projects', T0 + DAY, gh=ov.GitHub(True, fake))
    assert rows['p1']['verification_state'] == 'verified_success'
    assert rows['p2']['verification_state'] == 'verified_failure'
    p3 = rows['p3']['signals'][0]
    assert p3['merged_by_agent'] and p3['label_class'] == 'execution_only'
    assert rows['p3']['verification_state'] == 'unverified'
    assert all(a[0] in ('pr', 'api') for a in calls)  # read-only surface


def test_ask_answered_ignored(tmp_path, home):
    tr = Transcript()
    tr.prompt('p1', 'q', T0)
    tr.say('Which one do you want?', T0 + 1)
    tr.prompt('p2', 'the first', T0 + 50)
    tr.say('Shall I push?', T0 + 60)
    tr.write(tmp_path / 'projects')
    observed(home, 'sess-1', 'p1', 'p2', asked=True)
    rows = run(home, tmp_path / 'projects', T0 + DAY)
    assert rows['p1']['signals'][0]['kind'] == 'ask_answered'
    assert rows['p2']['signals'][0]['kind'] == 'ask_ignored'
    assert run(home, tmp_path / 'projects', T0 + 120)['p2']['signals'][0]['kind'] == 'ask_pending'


def test_reask_and_meta_prompts_and_subagents(tmp_path, home):
    tr = Transcript()
    tr.prompt('p1', 'what is the current branch of the repo please', T0)
    tr.tool('Agent', {'prompt': 'x'}, 'launched', T0 + 1, tur={'agentId': 'abc', 'isAsync': True, 'status': 'async_launched'})
    tr.say('main', T0 + 2)
    tr.prompt('p2', '<agent-message from="x">done</agent-message>', T0 + 30, meta=True)
    tr.say('ok', T0 + 31)
    tr.prompt('p3', 'what is the current branch of this repo please', T0 + 60)
    tr.say('main', T0 + 61)
    path = tr.write(tmp_path / 'projects')
    sub = Transcript(session='sess-1')
    sub.prompt('s1', 'run tests', T0 + 2)
    sub.bash('cargo test', T0 + 3, exit=101)
    subpath = path.with_suffix('') / 'subagents' / 'agent-abc.jsonl'
    subpath.parent.mkdir(parents=True)
    subpath.write_text(''.join(json.dumps(r) + '\n' for r in sub.rows))
    observed(home, 'sess-1', 'p1', 'p2', 'p3')
    rows = run(home, tmp_path / 'projects', T0 + DAY)
    r1 = rows['p1']
    assert r1['turn']['bash_calls_via_subagents'] == 1
    assert r1['measurement']['subagents_inspected'] == 1 and r1['measurement']['subagents_not_inspected'] == 0
    weak = next(s for s in r1['signals'] if s['kind'] == 'user_weak_correction')
    assert weak['re_ask'] is True  # p2 is a harness message, p3 is the next user prompt
    assert r1['verification_state'] == 'verified_failure'  # subagent's final test run failed
    assert rows['p2']['turn']['harness_message'] is True


def test_append_only_idempotent_and_join(tmp_path, home):
    tr = Transcript()
    tr.prompt('p1', 'secret request text', T0)
    tr.bash('pytest', T0 + 5)
    tr.write(tmp_path / 'projects')
    observed(home, 'sess-1', 'p1', 'missing-turn')
    sdir = home / 'state' / 'claude-code'
    opp = {'schema': 'z0int.claude_code.opportunity_record.v0', 'session_id': 'sess-1', 'gate': 'ACT',
           'opportunity': {'semantic_id': 'sem1', 'intent': {'request': 'secret request text', 'revision': 'rev1'},
                           'trace': {'trace_id': 'p1', 'opportunity_id': 'opp1'}, 'authority': {'source': 'harness-default'},
                           'action_space': [{'kind': 'ACT', 'legal': True}, {'kind': 'ASK', 'legal': False}]}}
    (sdir / 'opportunities.jsonl').write_text(json.dumps(opp) + '\n')
    before = (sdir / 'outcomes.jsonl').read_bytes()
    rows = ov.verify(root=home, projects=tmp_path / 'projects', now=T0 + 60, gh=ov.GitHub(False))
    assert ov.append_new(rows, home)[0] == 2
    assert ov.append_new(rows, home)[0] == 0  # unchanged content is not re-appended
    assert (sdir / 'outcomes.jsonl').read_bytes() == before  # observed rows untouched
    missing = next(r for r in rows if r['trace_id'] == 'missing-turn')
    assert missing['measurement']['transcript'] == 'turn_not_found' and missing['verification_state'] == 'unverified'
    joined = {j['trace_id']: j for j in ov.credit_join(home)}
    j = joined['p1']
    assert j['opportunity']['opportunity_id'] == 'opp1' and j['gate_decision'] == 'ACT'
    assert j['observed']['action'] == 'ACT' and j['verified']['state'] == 'verified_success'
    assert j['gate_agrees_with_observed'] is True and j['credit']['counterfactual_available'] is False
    assert joined['missing-turn']['join'] == {'opportunity': False, 'verified': True}
    assert 'secret' not in json.dumps(list(joined.values()))
    md = ov.format_markdown(ov.summarize(rows, list(joined.values())))
    assert 'secret' not in md and '| verified_success | 1 |' in md


def test_cli_verify_dry_run(tmp_path, home, monkeypatch, capsys):
    tr = Transcript()
    tr.prompt('p1', 'x', T0)
    tr.bash('pytest', T0 + 5)
    tr.write(tmp_path / 'projects')
    observed(home, 'sess-1', 'p1')
    monkeypatch.setenv('Z0INT_HOME', str(home))
    from z0int.cli import main
    report = tmp_path / 'r.md'
    assert main(['outcomes', 'verify', '--no-gh', '--dry-run', '--projects-dir', str(tmp_path / 'projects'),
                 '--report', str(report)]) == 0
    assert 'verified_success=1' in capsys.readouterr().out
    assert not (home / 'state' / 'claude-code' / 'outcomes_verified.jsonl').exists()
    assert '| verified_success | 1 |' in report.read_text()


def test_decide_rules():
    s = ov.signal
    assert ov.decide([]) == ('unverified', 'unknown', None)
    assert ov.decide([s('a', 1, 'low', 'soft', 'x')]) == ('unverified', 'soft', 'low')
    assert ov.decide([s('a', 1, 'high', 'deterministic_gold', 'x'), s('b', -1, 'low', 'soft', 'x')])[0] == 'verified_success'
    assert ov.decide([s('a', 1, 'medium', 'deterministic_gold', 'x'), s('b', -1, 'high', 'negative_gold', 'x')])[0] == 'contested'
    assert ov.parse_since('2d', now=10 * DAY) == 8 * DAY


def test_ci_verdict_ignores_non_code_workflows():
    assert ov.ci_verdict([{'name': 'tests', 'status': 'completed', 'conclusion': 'success'},
                          {'name': 'auto-label', 'status': 'completed', 'conclusion': 'failure'}]) == 'success'
    assert ov.ci_verdict([{'name': 'tests', 'status': 'completed', 'conclusion': 'failure'}]) == 'failure'
    assert ov.ci_verdict([{'name': 'tests', 'status': 'in_progress', 'conclusion': None}]) == 'pending'
    assert ov.ci_verdict([]) is None
