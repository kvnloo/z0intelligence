# obspack-long-v1 — pre-registration

Committed before any measured run. Follow-up to z0evals `claude-code-savings-v0` (ObservationPack
−30% on long sessions, n=12, p=0.0068; composing with lean −38%, p=0.031) and z0intelligence
`study/claude-code-savings-v1` (`benchmarks/claude_code_v1`): v1 found no ObservationPack effect on
short tasks (median ~5 tool calls) and −41.5% cost on its single long recall task (n=4, exploratory),
and found that its pre-registered unweighted token metric understates billing savings because the
saving is in cache writes while cache reads grow. v1 could not test the regime ObservationPack is
for: **long, output-heavy sessions**. This study does.

## Question

On long Claude Code sessions (≥25 tool calls expected) that produce large Bash outputs, does adding
the ObservationPack plugin (`harness-adapters/claude-code-z0-obspack`, PostToolUse on Bash: results
over 6000 chars are archived verbatim and replaced by head 40 + tail 20 lines + a `z0obs` recall
handle) lower what the session costs, without lowering task success?

## Arms

All arms: `claude -p` (Claude Code 2.1.286), `--model sonnet`, `--effort medium`,
`--output-format stream-json`, `--no-session-persistence`, `--max-budget-usd 5`,
`--permission-mode bypassPermissions`, and `--settings` that set the user-installed
`z0intelligence` / `z0-obspack` plugins to `enabledPlugins: false`, so z0 enters only through
`--plugin-dir`. The plugin runs from this branch's venv (`Z0INT_PYTHON`, Python 3.11), per-unit
`Z0INT_HOME`. Isolation conventions are v1's.

| arm | flags |
| --- | --- |
| `lean` | `--setting-sources project --strict-mcp-config --disable-slash-commands` |
| `lean+obspack` | lean + `--plugin-dir harness-adapters/claude-code-z0-obspack` |
| `stock` | user settings, hooks, MCP servers, skills as installed (z0 plugins disabled) |
| `stock+obspack` | stock + `--plugin-dir harness-adapters/claude-code-z0-obspack` |

No State Packet arm: the question is ObservationPack alone (v1 measured the packet).

## Tasks (frozen; `tasks/*/task.json`)

Twelve new tasks in four z0 repos at the same pinned SHAs as v1 (z0intelligence `b0724680bc`,
evolution-lab `4cf52bb101`, kerdoios `fd34a48a35`, tokenomics `65e8f2dd9d`), three families. The
**primary population is the ten long tasks** (7 `multi_bug_debug` + 3 `directed_recall`); the two
`log_forensics` tasks turned out short in the pilot (5–6 tool calls) and are an exploratory stratum.

| family | tasks | what makes it long / output-heavy | check |
| --- | --- | --- | --- |
| `multi_bug_debug` (primary) | `multibug-kerdoios`, `multibug-tokenomics`, `multibug-tokenomics-b`, `multibug-zint-a`, `multibug-zint-b`, `multibug-zint-c`, `multibug-evo` | 8 independent single-line bugs per task across 3–8 modules; the prompt asks for the suite to be run verbose with full tracebacks and re-run after each individual fix | restored tests must pass (pytest) + FIXES.md |
| `directed_recall` (primary) | `recall-zint-cognition`, `recall-zint-core`, `recall-evo-lab` | `cat` 25 source files (6–41 kB each) one per command, then answer 10 questions without reopening files (recall helpers allowed) | exact JSON key + AUDIT.md ≥25 lines |
| `log_forensics` (exploratory) | `fx-fleet-logs`, `fx-ci-log` | investigate 2.8 MB of fleet logs / a 50 kB CI log with shell commands, 9–10 questions | exact JSON key + a ≥5-line write-up |

How tasks were built (all before any measured run):

- **multi_bug_debug**: bugs are single-line mutants from a seeded mutation search over the pinned
  source (operator flips such as `==`/`!=`, `<`/`<=`, `and`/`or`, `min`/`max`, `[-1]`/`[0]`), each
  killed by its module's own tests. Validated: the as-given fixture fails, the pristine source passes,
  and **each sabotage alone** fails the check (so every bug must be fixed).
- **directed_recall**: questions are AST-extracted module-level constants and literal parameter
  defaults whose names are unique across the 25 files, biased to lines outside the first 45 and last
  25 of their file (so a head+tail pack hides most of them and the model must recall them). This is
  the v0/v1 long-recall design scaled from 12 to 25 files.
- **log_forensics**: logs are written at fixture time by a deterministic generator kept in the task
  dir (`gen.py`, never copied into the work dir); the key comes from the same generator.
- `run.py validate` passes for all tasks (as-given fails, oracle passes).

Pilot (single rep, `lean+obspack` only, tags `pilot`/`pilot2`/`pilot3`, excluded from analysis): all
tasks passed except one forensics answer. Tool calls per session: directed_recall 27–52 (25 packs
each); multi_bug_debug with 5 bugs 9–21, so the family was raised to 8 bugs (3 more mutants per task
from the same validated pool, seed 20260930), giving 17–30 (median 23) in the re-pilot;
log_forensics 5–6. Packs in multi_bug_debug were rare (0–3 per session): despite the prompt, the
model often keeps individual outputs under the 6000-char threshold. That is the measured behaviour,
not something this study tunes away. The pilot also showed sessions cost ~$0.1–0.4 and take ~1–2.5
min, so the design below uses 4 reps and all four arms.

Anchor (not part of the primary population): `zint-long-backend-review`, v1's long recall task
copied unchanged (12 files, ~14 tool calls), for continuity with v1's n=4 result.

Honest framing: `multi_bug_debug` and `directed_recall` prompts direct output-heavy behaviour
(verbose suite, whole-file `cat`), because that is the regime under test; `log_forensics` leaves the
method to the model. Results are reported per family as well as pooled.

## Design

- Units = (task, arm) with one persistent work dir; rep 0 is a cold session, reps 1..k reset the
  tree to the fixture commit and run warm (v1 convention). Arm order rotates per task.
- **Primary: `lean` vs `lean+obspack`, 13 tasks × 4 reps** (40 pairs on the 10 long tasks; 8 forensics
  and 4 anchor pairs). Run first.
- **Secondary: `stock` vs `stock+obspack`, same tasks, 4 reps**, run after the primary only if
  quota and time allow (the user's Claude quota resets 03:00 CDT). If it is cut short, whatever
  pairs completed are reported as exploratory and the shortfall is stated.
- ≤3 concurrent `claude` processes (other studies share the account).
- Pilot: before this file was committed, a single-rep `lean+obspack` pilot (`--tag pilot`) checked
  the harness and tool-call counts. Pilot rows are excluded from all analysis.

## Metrics

**Primary metric: Claude Code's own estimated cost, `total_cost_usd`.** Reason: it is
billing-weighted. ObservationPack's mechanism is to shrink what enters the context, which shows up
mostly as fewer cache-creation tokens (≈1.25× input price) while recall calls add turns of cache reads
(≈0.1× input price). An unweighted token sum counts a cache read the same as a cache write and so
understates (or can reverse) the billing effect; v1 saw exactly this (−4.7% tokens vs −41.5% cost).

Secondary: billed tokens (input + output + cache read + cache creation, unweighted); cache creation
and cache read separately; wall time; tool calls; packs per session; `z0obs` recalls per session.

## Analysis (`analyze.py`, pre-registered)

- Pairs = same task and rep across the two arms of a contrast.
- **P1 (primary): lean → lean+obspack on the 10 long tasks (multi_bug_debug + directed_recall).** Effect = ratio of total cost (B/A − 1);
  95% CI by task-cluster bootstrap (10,000 resamples, seed 20260930); two-sided Wilcoxon signed-rank
  on per-pair log cost ratios; median paired change and geometric-mean ratio reported.
- **Supported** if the CI upper bound < 0, Wilcoxon p < 0.05, and the quality guard passes.
- **Quality guard**: success(B) − success(A) ≥ −5 percentage points and not (exact McNemar p < 0.05
  with more A-only successes). Success = the programmatic check, never the model's claim.
- Same statistics for tokens (secondary), and for S1 stock → stock+obspack, S2 stock → lean+obspack
  (secondary, not multiplicity-corrected, reported as such).
- Strata (descriptive): family, cold (rep 0) vs warm, and the anchor task.
- Cost per verified success per arm.

## Data handling

- A trial with no result (crash, timeout without a result event, rate-limit abort) may be rerun once;
  the analysis keeps the later row only if the earlier one had no result. Nothing else is dropped.
- Rate limits: the runner flags rate-limit text, backs off 5 minutes, and records it; flagged trials
  are counted in the results.
- Raw stream JSONL (session ids, local paths, model text) stays under `~/.cache/z0-obspack-long`,
  out of git. Committed results are aggregate-only (`results/results.json`, `results/trials.csv`).
