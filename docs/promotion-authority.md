# Promotion Authority v0 (`z0int.promotion_check.v0`)

One rule decides whether a feature branch on the nightly manifest may graduate from `nightly` to the default
branch. It is expressed as a [DecisionOpportunity](decision-opportunity.md), so promotion uses the same
`ACT / OBSERVE / ASK / ABSTAIN / ESCALATE` contract as every other z0int decision.

```
z0int promote check --repo PATH --manifest FILE [--report nightly-report.json] [--json]
                    [--test-cmd CMD] [--root MOD=WHY]... [--branch B]... [--no-gh] [--no-tests] [--full]
```

The check is **shadow-only and read-only**. It reads branch trees with `git archive` into a scratch
directory, and `git merge-tree` writes its objects to a scratch object directory (the repo's store is only an
alternate). No ref, index, working tree or object store is changed (see `test_check_is_read_only`). It never
pushes, merges or opens a PR.

## The rule

Each criterion is a fact with evidence. Missing evidence is `unknown`, never `false`.

| # | criterion | observed when | evidence |
|---|---|---|---|
| 1 | `merge` | `git merge-tree <default> <branch>` is clean **and** the latest nightly report (`verified-oss-loop.nightly-report.v1`) kept the branch (`MERGED`, `WARN` or `CONTAINED`) at the same SHA, with a green final gate | merge-tree output; report entry, mode and final gate |
| 2 | `wired` | every added source module (`.py` under `src/`, or under a top-level package, excluding tests) is reachable from a real entrypoint | import-graph chain from the root |
| 3 | `tested` | the repo's CI test command passes on the branch tree under `coverage`, and every changed source file with added function-body lines has at least one of those lines executed | CI command, pass/fail, covered/uncovered files |
| 4 | `receipt` | a branch whose commit or PR text makes a behaviour/perf claim adds or cites an eval receipt: a `benchmarks/**/*results*.json` in the tree, or a z0evals reference | claim lines, receipt paths |
| 5 | authority | promotion is effect `privileged`. No standing authority grants it, so ACT stays illegal until `with_authority_grant(granted_by_user=…, effect="privileged")` | `authority` block of the opportunity |

**Import graph (criterion 2).** This is a single-tree port of `research/factory/audit.py`. It uses AST imports
(absolute, relative and sibling), importlib-style module strings, `-m pkg.mod` and path references, plus text
references from shell and JS files, `bin/` scripts and plugin manifests. A reached submodule also reaches its
parent packages. The roots are:

- `[project.scripts]` and `[project.entry-points]`;
- GitHub workflows;
- Claude Code plugin manifests (`hooks.json`, `.mcp.json`, `plugin.json`);
- any `--root MOD=WHY` you pass, for example a systemd unit's `python -m` target.

Workflow lines that only run `unittest`, `pytest`, `py_compile` or `compileall` are ignored, so a CI test job
counts as test evidence, not production reach. Test files never propagate reach. Scripts and benchmarks are not
gated.

**Coverage (criterion 3).** The CI test command is taken from `--test-cmd`, then the report's `test_cmd`, then
the manifest's `NIGHTLY_TEST_CMD` header. It must be one `[ENV=…] python … ` invocation, which is re-run as
`coverage run --include <tree>/*`. A shell pipeline cannot be instrumented, so criterion 3 is then `unknown`. As
in the audit, a file that only gets imported is not "tested": an added line must be executed inside a function
body. A file with no functions is the one exception, and any added line counts for it. A branch with no added
function-body code passes vacuously.

**Claims (criterion 4).** A claim is a line with a measured quantity (`2x`, `41%`, `2/30`, `120 ms`) **and** a
claim word such as faster, latency, accuracy, recall, reduces, beats or hides. A bare mention of "benchmark"
or a metric name is not a claim. PR titles and bodies are read with `gh pr list` (read-only). Without `gh`,
only commit text is judged, and the output says so.

## Mapping to the action space

The criteria go into a synthetic State Packet, and `build_decision_opportunity(effects=("read", "privileged"),
scoped=False)` builds the opportunity:

| fact state | packet encoding | effect on the opportunity |
|---|---|---|
| observed / n/a | `current_claims` with evidence ids | none |
| checked negative | blocking unknown, `source_status: no_match` (an answer, not a gap) | ACT blocked; not an ASK reason |
| evidence missing but gatherable | blocking unknown, `unknown`, plus an `OBSERVE` transition | ACT blocked; OBSERVE legal |
| claim without receipt | blocking unknown, `source_unavailable` | ACT blocked; ASK about it |
| two sources disagree | `contradictions` | ESCALATE legal |

`promotion_verdict()` picks the first match in this fixed order:

1. **ACT**, only after a user grant, with every fact observed;
2. **ESCALATE**, when evidence contradicts itself;
3. **ABSTAIN**, when a criterion is checked negative (not ready);
4. **OBSERVE**, when evidence is missing and can be gathered;
5. **ASK**, when the branch is ready and needs the human's grant, or a claim needs a receipt that only the human can supply.

The base `deterministic_gate()` would ASK for every not-ready branch too, because privileged authority is always
missing. The promotion gate narrows that: the human is asked only about branches they could actually approve.

Contradictions that ESCALATE:

- The nightly kept the branch, but it does not merge onto the default branch alone. It only integrates on top of
  earlier nightly entries.
- The nightly smoke was green at this SHA but the CI command fails on the branch tree, or the reverse (`WARN` or
  `DROPPED tests` while the CI command passes).
- The branch or its PR cites a `benchmarks/…results….json` that is not in the branch tree.

A report entry at a different SHA than the branch tip is **stale**: `merge` becomes `unknown`, and the OBSERVE
action is to re-run the nightly. A branch that is already an ancestor of the default branch (graduated), or one
that no longer exists, gets ABSTAIN with the reason given.

## Output

`--json` returns `z0int.promotion_check.v0`. Each branch has:

- `verdict` and `failing`;
- per-criterion `status`, `value`, `reason`, `evidence`, and `observe` or `contradiction` when present;
- `ask_about`;
- `semantic_id` and a compact `action_space`.

`--full` embeds the whole DecisionOpportunity. Identical evidence gives an identical `semantic_id`.

## What v0 does not do

- It cannot grant. The CLI has no grant flag. A grant is an explicit library call with an identified user.
- It does not attribute coverage per test. A red CI command makes `tested` false as a whole.
- It does not parse receipt contents. A cited z0evals receipt is accepted as a reference and marked
  "external, not verified here".
