# Contributing to z0intelligence

Thank you for helping. This page is for people. Coding agents follow the same
steps through `z0int contribute --json` (see `AGENTS.md`, "Start here").
The full design is in [docs/contrib/CONTRIBUTOR-LAYER.md](docs/contrib/CONTRIBUTOR-LAYER.md).

## 1. Set up

Recommended (full path, installs the runtime and the harness plugins):

```bash
git clone https://github.com/kvnloo/z0 && ./z0/z0 onboard --auto
```

Repo only (degraded path; tasks that need the full runtime are not offered):

```bash
git clone https://github.com/<you>/z0intelligence && cd z0intelligence
./scripts/bootstrap.sh            # or: python -m venv .venv && .venv/bin/pip install -e '.[test]'
```

Then run:

```bash
z0int contribute
```

It checks your setup, runs an offline smoke eval (CPU only, no model downloads),
and suggests one task plus two alternatives that fit your machine. It then stops
and asks you. Nothing is posted anywhere.

## 2. Pick and claim a task

- The only task list is [`.z0/priorities.yaml`](.z0/priorities.yaml). Tasks with
  `newcomer: true` are open to you. Please do not open PRs for tasks that are not
  listed; propose new tasks in an issue instead.
- `z0int contribute claim <task-id>` takes a **local lease** (so two agents on one
  machine do not take the same task). You can explore on the local lease.
- For an `exclusive` or `parallel(n)` task, post the public claim on its issue
  before the PR. The CLI prints the exact text, for example:

  ```
  /claim t-139-e2e-trace lease=72h harness=claude-code hw=cpu
  ```

  The claim bot answers with the lease (72 h) or `/claim rejected: <reason>`.
  `/claim renew` extends a lease when your fork branch has a commit in the last 72 h.
  `/claim release` gives the task back. Expired leases are released automatically.
  If the bot has not answered, a maintainer handles the claim by hand.
- Tasks with `claim: none` (docs, reviews, the onboarding feedback task) need no public claim.

## 3. Do the work with evidence

```bash
z0int contribute start <task-id>      # makes a git worktree from master
# work and commit inside that worktree only
z0int contribute evidence <task-id>   # task verifier + product smoke on the exact HEAD
```

A new commit or a rebase needs new evidence. Evidence is a receipt bound to the
commit sha; "it works on my machine" without a receipt is not enough.

## 4. Open the pull request

```bash
z0int contribute pr <task-id>                 # writes the PR body (receipts, credit) to a file
z0int contribute pr <task-id> --open --yes    # pushes to YOUR fork, opens a DRAFT PR
```

- PRs come from your fork and target the default branch **`master`**. Branch
  protection and the required checks gate them; the owner reviews and merges.
  (`preview` and `nightly` are for internal automated workers only.)
- The PR body holds the task id, the receipts, your hardware class (for example
  `nvidia-12g`), harness and model, and a `Credit: @you` line. It never holds your
  profile or anything from your home directory.

## 5. Contributor License Agreement (CLA)

Contributions are accepted under the z0intelligence **Contributor License
Agreement**. A CLA bot checks every pull request: on your first PR it posts a
link to the agreement and asks you to sign it once. A PR cannot be merged until
the CLA check passes.

## 6. Credit

You are credited in two places:

- the `Credit: @<login>` line in your PR (written by `z0int contribute pr`), and
- an entry in [`.z0/credits.yaml`](.z0/credits.yaml): `{login, tasks, prs, since}`.
  Add your own entry in your PR, or a maintainer adds it when the PR merges.

Upstream sources you build on must be cited in the PR.

## 7. z0evals

[z0evals](https://github.com/kvnloo/z0evals) holds the frozen evaluations and
public results. It is in scope for contributors: tasks of `kind: z0evals` in
`.z0/priorities.yaml` have a smoke path you can run offline, for example
`python -m pytest -q tests/test_memory_acceptance.py` (the frozen memory cohort in
`tests/fixtures/z0evals-fb14919/`). To run a full study, clone z0evals and point
z0int at it with `--z0evals-root` / `Z0INT_Z0EVALS_ROOT` (see README).

## 8. Privacy and safety rules

- Your personal state stays in `~/.z0int/` (mode 0600). The contributor tooling
  never reads your credentials, transcripts, or instruction-file contents. Richer
  profile signals (for example a language histogram of your repos) are used only
  after you say yes.
- Nothing is posted to GitHub, pushed, or opened as a PR without your explicit yes.
- Our tooling never uses grok or cursor as an LLM provider. You may use any coding
  harness you like.

## Always-available task

If nothing fits, `z0int contribute feedback` gives you `t-onboarding-feedback`: a
short friction report on the setup you just did. It needs no special hardware
and it is always useful.
