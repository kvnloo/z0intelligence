# claude-code-savings-v1b — pre-registration

Committed before any measured run. Follow-up to `claude-code-savings-v1`
(`benchmarks/claude_code_v1`, z0evals `study/claude-code-savings-v1`). v1 found the State Packet,
injected at every SessionStart, cuts tokens **−35.5%** [−45.4, −23.4] over lean on current-work
questions (qa suite) but **adds +10.8%** [+6.2, +15.0] on fresh coding/navigation tasks (repo
suite). v1b measures the fix on branch `feat/packet-gating-v0` (off
`integrate/claude-code-z0-stack` @ `029c3ad`, feature commit `ebae4e3`): `packet: "gated"`
injects nothing at SessionStart and, at UserPromptSubmit, only the facts of the families that
DecisionOpportunity question scoping (`decision_opportunity.required_families`, vocabulary
`FACT_FAMILIES` unchanged from the stack) names for the prompt, each family at most once per
session.

## Arms

Everything the v1 runner does is reused unchanged (`benchmarks/claude_code_v1/run.py`, imported
by `benchmarks/claude_code_v1b/run.py`): `claude -p`, `--model sonnet`, stream-json,
`--no-session-persistence`, `--max-budget-usd 2`, installed z0 plugins disabled, per-unit
`Z0INT_HOME`, this branch's venv, the same flags per suite. Only the arm table differs.

| arm | flags / env |
| --- | --- |
| `lean` | `--setting-sources project --strict-mcp-config --disable-slash-commands` (reference) |
| `lean+packet` | lean + `--plugin-dir harness-adapters/claude-code-z0intelligence`, `Z0INT_CLAUDE_CODE_PACKET=1` (v1 SessionStart behaviour) |
| `lean+packet-gated` | same plugin, `Z0INT_CLAUDE_CODE_PACKET=gated` |

Both packet arms set `Z0INT_CLAUDE_CODE_OFFLOAD_HINT=0`, so the SessionStart text of
`lean+packet` matches v1's (`b072468` had no offload hint) and the two packet arms differ only in
where and what packet is injected. Neither packet arm passes the new lean `--mcp-config` (that is
(b), checked separately below), so `route_worker` is absent from all three arms as in v1.
Scoped-packet budget: 800 tokens (`Z0INT_CLAUDE_CODE_GATED_TOKENS`); session packet 1500 (v1).

## Suites, tasks, scoring (frozen, = v1)

qa: the 11 v0 pinned held-out questions, scored with v0 `score()`; repo: the 20 v1 tasks (19 fresh +
the long recall replication) verified by v1 `check.py`. `run.py validate` passed 20/20 (as-given
fails, oracle passes) on this branch before this commit. **4 reps** per (task, arm) (v1 design):
qa rep units run in place in the pinned repos; repo rep 0 cold, reps 1–3 warm. n = 44 qa + 80 repo
pairs per contrast; 3 arms × 124 = 372 trials. ≤ 4 concurrent `claude` processes (`--jobs 4`).
Order: v1's (seed 20260930 shuffle, Latin-square arm rotation). qa suite first, then repo.

## Deterministic gate prediction (computed from prompts only, before any run)

`required_families` over the exact prompts the runner sends:

- qa: fires on **11/11** questions (families per question: p01 git.branch,git.worktrees; p02/p06
  git.branch,git.head,git.branches_ahead,git.worktrees; p03 + git.upstream,conv; p04
  git.branch,git.stash; p05 git.branch,git.head,git.dirty,git.upstream,git.branches_ahead; p07
  docs.priority; p08/p09 git.worktrees,git.history,conv; p10 git.branch,git.worktrees,git.history,conv;
  p11 git.branch,gh).
- repo: fires on **5/20** tasks (substring false positives on coding prompts):
  evo-feature-capacity-max-calls (docs.priority, gh), evo-nav-capacity-plan (docs.priority),
  tok-fix-trace-aggregate-doublecount (conv), zint-long-backend-review (git.head),
  zint-nav-plugin-hooks (conv); silent on the other 15.

The gate vocabulary is NOT tuned on these suites (no change after seeing this list or any result).

## Metrics

Same as v1. Primary: billed tokens per trial = input + cache_read + cache_creation + output from
Claude Code's `modelUsage`. Secondary: `total_cost_usd`, success (`verified`), tool calls, turns,
and (new) the gate ledger per trial: families fired, families injected, injected chars.

## Hypotheses and tests (A = `lean+packet`, B = `lean+packet-gated`)

Effect = ratio of total billed tokens B/A − 1 over (suite, task, rep) pairs; 95% CI by
task-cluster bootstrap (10k, seed 20260930); Wilcoxon signed-rank on per-pair log(B/A). Quality
guard per contrast as v1: PASS iff success(B) − success(A) ≥ −5 pp AND NOT (exact McNemar
p < 0.05 with more A-only successes).

- **H1 (qa, non-inferiority):** CI upper bound < **+5%** AND quality guard PASS.
- **H2 (repo, superiority):** CI upper bound < 0 AND Wilcoxon p < 0.05 AND quality guard PASS.
- **Primary verdict PASS iff H1 and H2 both pass** (intersection–union: no multiplicity
  adjustment).

Secondary (reported, not part of the verdict): S1 lean → gated (qa; does gated keep v1's
saving?), S2 lean → gated (repo; "removes the +10.8%" judged as equivalence: CI within ±5%),
S3/S4 lean → session packet (replication of v1 C3 on qa/repo). Strata: repo cold / warm / short,
and repo split by the frozen gate prediction above (gate-fired 5 tasks vs gate-silent 15).

## Exploratory (no savings claim): route_worker in lean

`run.py route-probe --n 3`: three `claude -p` sessions launched with exactly the argv/env of
`z0int claude-code launch --profile lean` (`build_argv('lean', …)` + `launch_env`), each asked to
call route_worker once on a fixed public sentence. Reported: is `--mcp-config` passed, which MCP
servers the init event lists, is a route_worker tool listed, was it called, and whether the call
returned an error, PARENT_ONLY or a receipt. Plus one interactive-path check that the real CLI
(`z0int claude-code launch --profile lean -- -p …`) lists the tool.

## Handling

Fixed n; no extension after looking. A smoke run (tag `smoke`: zint-fix-warm-ttl and qa p02,
1 rep, all arms) checks plumbing only and is excluded. A trial with no result event is re-run
once; budget/turn-limit endings count. Raw JSONL stays outside git
(`~/.z0int/research/claude-code-savings-v1b/`); git gets aggregates (`results/`) only.

## Known limitations (before running)

Same host/model/version family as v1 but a different night, so v1 numbers are context, not a
paired comparator; S3/S4 re-measure the session-packet effect on the same night. The qa suite
shares one pinned cwd per repo across arms (as v0/v1). The gate is a substring matcher: its
false positives on coding prompts (5/20 here) are part of what is measured, not corrected.
"Once per session" is untested by these single-prompt `-p` trials (unit-tested only).
