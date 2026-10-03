"""Regression guard (C2): dump the Claude Code verifier rows for a synthetic CC fixture set as canonical JSON.

Run with PYTHONPATH=<tree>/src and argv[1] = a tests dir that provides the Transcript/commit builders (the
builders are test code only; the verifier under test comes from PYTHONPATH). argv[2] = scratch dir.
Covers: tests in turn + correction cues, failing tests, agent-edited tests, SZZ fix, revert, PRs merged/closed/
self-merged (fake read-only gh), ask answered/ignored, re-ask + meta prompts + subagents, all_turns sweep.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, sys.argv[1])
from test_outcome_verifier import DAY, T0, Transcript, commit, gitc, observed  # noqa: E402
from z0int import outcome_verifier as ov  # noqa: E402

out = Path(sys.argv[2])
home, projects, repo = out / 'z0home', out / 'projects', out / 'repo'
(home / 'state' / 'claude-code').mkdir(parents=True)
repo.mkdir()
gitc(repo, 'init', '-q', '-b', 'main')
commit(repo, 'src/app.py', 'a = 1\nb = 2\nc = 3\n', 'init', T0 - DAY)

t = Transcript(session='s-tests', cwd=str(repo))
t.prompt('p1', 'make the parser handle empty input', T0)
t.bash('cd /x && uv run pytest -q', T0 + 10, exit=1, out='1 failed')
t.bash('uv run pytest -q', T0 + 20, exit=0, out='5 passed')
t.say('Done.', T0 + 30)
t.prompt('p2', "that's wrong, undo it", T0 + 100)
t.tool('Edit', {'file_path': f'{repo}/tests/test_x.py', 'old_string': 'a', 'new_string': 'b'}, 'ok', T0 + 101)
t.bash('pytest', T0 + 105)
t.say('Should I also update the docs?', T0 + 110)
t.prompt('p3', 'yes please, and make the parser handle empty input', T0 + 200)
t.tool('AskUserQuestion', {'question': 'which?'}, 'answered', T0 + 205)
t.bash('pytest tests/', T0 + 210, exit=1)
t.write(projects)

sha = commit(repo, 'src/app.py', 'a = 1\nb = 20\nc = 3\n', 'feat: change b', T0 + 1010)
t2 = Transcript(session='s-git', cwd=str(repo))
t2.prompt('q1', 'change b', T0 + 1000)
t2.bash('git commit -m "feat: change b"', T0 + 1009, out=f'[main {sha[:7]}] feat: change b\n 1 file changed')
sha2 = commit(repo, 'src/other.py', 'x = 1\n', 'feat: tweak other value', T0 + 1110)
t2.prompt('q2', 'tweak other', T0 + 1100)
t2.bash(f'git -C {repo} commit -m t', T0 + 1109, out=f'[main {sha2[:7]}] feat: tweak other value')
t2.prompt('q3', 'open a pr', T0 + 1200)
t2.bash('gh pr create --fill', T0 + 1205, out='https://github.com/o/r/pull/7\n')
t2.prompt('q4', 'another', T0 + 1300)
t2.pr_link('o/r', 8, T0 + 1305)
t2.prompt('q5', 'and one more', T0 + 1400)
t2.pr_link('o/r', 9, T0 + 1405)
t2.bash('gh pr merge 9 --squash -R o/r', T0 + 1410)
t2.prompt('q6', '<task-notification>agent done</task-notification>', T0 + 1500, meta=True)
t2.write(projects)
commit(repo, 'src/app.py', 'a = 1\nb = 21\nc = 3\n', 'fix: b off by one', T0 + 2 * DAY)
gitc(repo, 'revert', '--no-edit', sha2, t=T0 + DAY)

observed(home, 's-tests', 'p1', 'p2', 'p3')
observed(home, 's-git', 'q1', 'q2', 'q3', 'q4', 'q5')
states = {7: {'state': 'MERGED'}, 8: {'state': 'CLOSED'}, 9: {'state': 'MERGED'}}
gh = ov.GitHub(True, lambda a: states[int(a[2])] if a[:2] == ['pr', 'view'] else None)
rows = ov.verify(root=home, projects=projects, now=T0 + 3 * DAY, gh=gh, all_turns=True)
ov.append_new(rows, home)
join = ov.credit_join(home)
blob = {'rows': rows, 'join': join, 'summary': ov.summarize(rows, join)}
text = json.dumps(blob, sort_keys=True, indent=1, default=str)
for p in (str(out), str(repo)):  # tmp paths differ between the two runs; they never appear in rows, but be sure
    text = text.replace(p, '<scratch>')
print(text)
