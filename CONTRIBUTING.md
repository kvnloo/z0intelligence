# Contributing to z0intelligence

Thank you for helping. The project front door (what Zer0 is, the open problems,
using a coding agent, where to talk, terms) is
[z0's CONTRIBUTING](https://github.com/kvnloo/z0/blob/main/CONTRIBUTING.md).
This page covers only what is specific to this repo.

## Pick a task

Open issues labeled
[`claimable`](https://github.com/kvnloo/z0intelligence/issues?q=is%3Aopen+label%3Aclaimable)
or [`good first issue`](https://github.com/kvnloo/z0intelligence/issues?q=is%3Aopen+label%3A%22good+first+issue%22).
To take one, comment `/claim` on the issue; a maintainer confirms the claim
(no claim bot yet). Propose new work in an issue first.

## Set up and run the tests

Python 3.11 or 3.13 (what CI runs). On Linux, the CPU build of torch keeps the
install small:

```bash
git clone https://github.com/<you>/z0intelligence && cd z0intelligence
python3 -m venv .venv && . .venv/bin/activate
python -m pip install torch==2.10.0+cpu --index-url https://download.pytorch.org/whl/cpu
python -m pip install -e '.[test]'
python -m pytest -q tests/test_check_receipt.py   # or the test file your issue names
```

On an Apple-silicon Mac, skip the torch line: there is no separate CPU build
for macOS, and the regular torch wheel is used. torch 2.10 has no wheel for Intel Macs, so there, use a
Linux machine or container, or let CI run the tests.

CI runs the full offline suite (`python -m pytest -q -m 'not live'`, about
10,000 tests) on every PR, so you don't have to run it all locally.

## Open the PR

Target `master` from your fork. The template has an Evidence yaml block, and
the `receipt` check fails until it is complete:

1. Push your branch and open the PR.
2. Fill in `issue`, `base_revision` (`git merge-base origin/master HEAD`),
   `head_revision` (`git rev-parse HEAD`), and `tests.red` / `tests.green`
   (the command and its result before and after your change).
3. Save the description. Editing it re-runs the check.

`head_revision` must be the PR's head commit, so update it after every push.
Test results from another commit are not evidence.

Until you have had a PR merged here, CI waits for a maintainer to approve each
workflow run.
"Waiting for approval" just means no maintainer has approved the run yet;
nothing is wrong with your PR.

Your authorship stays in the git history. Terms and credit are described in
z0's CONTRIBUTING.
