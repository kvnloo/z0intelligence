# Donor: process-tooling

Cut from `dd39238` (tag `archive/pre-reorg-dd39238`) as one commit. This branch is a
parts shelf, not a product: nothing here is on master unless it is salvaged later.

## What it is

The contribution-process tooling of the repo: the PR evidence-receipt gate, the
vendored Verified OSS Loop kit (`.verified-oss-loop/`), and the GitHub automation
around it (automerge/promote workflows, claim expiry, labeler, stale bot,
scorecard, dependabot, CODEOWNERS). None of it is product code; it has no
`src/` at all.

## Entry points

| What | Where | Triggered by |
|---|---|---|
| Receipt gate | `.github/scripts/check-receipt.py:131` (`main`) | `.github/workflows/receipt.yml:25`, every `pull_request` event, skipped for dependabot |
| Offline suite | `.github/workflows/core-unit.yml:41` | push / PR touching the listed paths |
| Rollout scheme reader | `.verified-oss-loop/rollout.py:98` (`show`, `get`, `allow-automerge`, `allow-promote`) | by hand; the three workflows below call a wrong path |
| Kit inventory | `.verified-oss-loop/kit-inventory.py:362` (`apply`, `finalize`, `show`, `source`, `mark`) | by hand |
| Issue clusterer | `.verified-oss-loop/scripts/cluster-similar-issues.py:91` | by hand, local fixture only |
| Automerge | `.github/workflows/automerge-nightly.yml:27`, `automerge-preview.yml:27` | PRs into `nightly` / `preview` |
| Promote | `.github/workflows/promote-preview.yml:20` | `workflow_dispatch` |
| Claim expiry | `.github/workflows/claim-expiry.yml:20` -> `.github/scripts/expire-claims.sh` | hourly cron |
| Labeler / stale / scorecard | `.github/workflows/labeler.yml`, `stale.yml`, `scorecard.yml` | PR / daily cron / weekly cron and push to master |
| CI-runner comparison | `.github/workflows/avrea-benchmark.yml` | `workflow_dispatch`, or a PR touching that file |

Checked on this branch: `check-receipt.py --help`, `rollout.py show`,
`kit-inventory.py show --root .` and `cluster-similar-issues.py --help` all run.
The five Python files compile. There is no importable package.

## Tests

```
python -m pytest -m 'not live' -q        # 42 passed (tests/test_check_receipt.py)
```

`tests/test_check_receipt.py` is the only test; it covers `check-receipt.py`.
The kit scripts (`rollout.py`, `kit-inventory.py`, `cluster-similar-issues.py`)
have no tests here or at `dd39238`. No bun tests.

`tests/test_architecture_manifest.py` is dropped: it asserts that every path in
`zer0.repo.yaml` exists, and most do not on a donor. `zer0.repo.yaml` is kept
unmodified as the whole-repo map.

`core-unit.yml` was trimmed (line removals only) to the `offline-suite` job.
It still installs the full pinned dependency set from `pyproject.toml`
(torch CPU wheel included) to run 42 stdlib-only tests. `[project.scripts]` in
`pyproject.toml` is emptied because every target module is gone.

## Cost on a turn's hot path

None. Nothing here runs inside an agent turn. The cost is CI minutes (hourly
claim-expiry cron, daily stale cron, weekly scorecard, receipt check per PR
event) and the lines `AGENTS.md` / `CLAUDE.md` spend describing the process in
every agent session opened in the repo.

## Evidence (quoted from the audit of `dd39238`)

- Receipt gate (`check-receipt.py` + `receipt.yml` + its test, 349 lines):
  "NONE on tokens or latency; it is a process gate for the agent factory."
  Verdict UNSURE, owner decision.
- `.verified-oss-loop/` (968 lines): verdict DELETE, high confidence. "NONE. The
  three workflows that would call rollout.py use the wrong path; the kit skills
  sit outside any harness skill directory, so no harness loads them;
  cluster-similar-issues.py is described in docs as 'local fixture only'."
  "Upstream is kvnloo/verified-oss-loop; the copy here is not executed by
  anything."
- Automerge / promote workflows (89 lines): verdict DELETE, high confidence.
  "scripts/rollout.py does not exist (the file is .verified-oss-loop/rollout.py),
  and origin has no preview, nightly or dev branch, so these can never do
  anything; the shell 'if' swallows the missing-file error." "Broken since they
  were added and silently so."
- Claim expiry, labeler, stale (244 lines): verdict UNSURE. "Process tooling,
  not product ... Keep only if the claim/label workflow is still how agents
  coordinate."
- Scorecard, dependabot, CODEOWNERS (50 lines): verdict KEEP, medium confidence.
  "Cheap hygiene with no code to maintain."
- `avrea-benchmark.yml` (101 lines): verdict DELETE, high confidence. "A
  CI-vendor comparison experiment duplicating core-unit steps verbatim."
- Owner decision 9 in the plan: "Process tooling for a single-maintainer repo:
  PR receipt gate, hourly claim-expiry, labeler, stale bot, scorecard. Keep or
  drop."

No measurement exists of whether the receipt gate or the loop kit changes
merge quality, review time or defect rate. The audit found none and this cut
adds none.

## Did it run live on the audited host

Not applicable to a host: this is GitHub-side automation. Per the audit,
`receipt.yml`, `core-unit.yml`, `claim-expiry.yml`, `labeler.yml`, `stale.yml`
and `scorecard.yml` fire on the origin repo; the automerge/promote workflows
never did anything; the kit scripts are run by nothing.

## Known defects

- `automerge-nightly.yml:27`, `automerge-preview.yml:27` and
  `promote-preview.yml:20` call `python3 scripts/rollout.py`. That path does not
  exist (here or at `dd39238`); the script is `.verified-oss-loop/rollout.py`.
  Automerge therefore always takes the "scheme forbids" branch. Origin had no
  `nightly` or `preview` branch at the time of the audit.
- `avrea-benchmark.yml` compiles and tests `src/z0int/receipt.py`,
  `tokenomics_emit.py`, `bridge/worker.py`, `bridge/runtime.py`,
  `backends/bench/tokenomics_bridge.py`, `tests/test_receipt.py`,
  `tests/test_bridge_worker.py` and `tests/test_tokenomics_emit.py`. None of
  those are on this donor, so both of its jobs fail if dispatched. It is kept
  only as the record of the experiment; no feature owned it (the assignment
  flags it as an unsettled placement). Keeping the code it tests would have
  pulled in the bridge worker and most of `src/`.
- `.github/labeler.yml` maps `skills/tdd`, `skills/verify`, `skills/orient`,
  `skills/anti-slop`, `skills/pstack`, `skills/dr-eggbot`; those live under
  `.verified-oss-loop/skills/`, so the globs match nothing.
- `.github/scripts/create-labels.sh` has no caller.
- The kit skills reference `skills/*/SKILL.md` and
  `.github/PULL_REQUEST_TEMPLATE.md` paths that do not exist in this repo.
- `AGENTS.md`, `CLAUDE.md` and `README.md` are the whole-repo versions. They
  describe commands (`z0int doctor`, `z0int backends`, setup scripts) that are
  not on this branch. `docs/verified-oss-loop.md` lists `python -m z0int doctor
  --json` as the product smoke command; that module is not here.
- `.verified-oss-loop/inventory.yml` still lists `skills/z0int-onboard/SKILL.md`
  as `source: local`; the file is kept so the inventory stays true.
- `pyproject.toml` still declares the full torch/transformers dependency set and
  `packages.find where = ["src"]` with no `src/` directory. Setuptools resolves
  that to an empty package list (checked with
  `setuptools.config.pyprojecttoml.read_configuration`); a real
  `pip install -e '.[test]'` was not run here.

## Extra files kept beyond the assignment

- `AGENTS.md`, `CLAUDE.md`: they carry the loop rules agents actually read
  (`AGENTS.md:16-20`, `CLAUDE.md:10`), which is the only consumer the kit has.
- `skills/z0int-onboard/SKILL.md`: named by `.verified-oss-loop/inventory.yml`.
- `README.md`, `LICENSE`, `THIRD_PARTY.md`, `requirements.txt`,
  `.python-version`, `.gitignore`, `zer0.repo.yaml`, `pyproject.toml`: kept on
  every donor.

## To earn a place on master

One consumer, not a measurement: the owner answering decision 9 with "yes, PRs
into master must carry a head-bound evidence receipt". If so, the smallest
subset worth taking is three files:

- `.github/scripts/check-receipt.py`
- `.github/workflows/receipt.yml`
- `tests/test_check_receipt.py`

plus `.github/dependabot.yml` and `.github/CODEOWNERS`, which cost nothing.
Everything under `.verified-oss-loop/` should be re-synced from
`kvnloo/verified-oss-loop` rather than taken from here, and the automerge /
promote workflows are only worth taking after the `scripts/rollout.py` path is
fixed and `preview` / `nightly` branches exist. Uncertain: whether anyone reads
the scorecard SARIF output, and whether the hourly claim-expiry cron has ever
expired a claim.
