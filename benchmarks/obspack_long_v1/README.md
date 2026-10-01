# obspack-long-v1 — ObservationPack on long, output-heavy Claude Code sessions

Pre-registered in [PREREG.md](PREREG.md) (commit `69f4ca2`, pushed before any measured run). This
follows up `benchmarks/claude_code_v1`, whose tasks were too short to test ObservationPack: their
median was about 5 tool calls. ObservationPack (`harness-adapters/claude-code-z0-obspack`) archives
any Bash result over 6000 chars and shows the model only its first 40 and last 20 lines, plus a
`z0obs` handle the model can use to recall the rest.

**Runs.** 208 real `claude -p` sessions (Sonnet, Claude Code 2.1.286, effort medium) across 13 tasks,
4 arms and 4 reps. Claude Code's own estimated spend was $70.35. The primary population is ten long
tasks:

- 7 `multi_bug_debug` tasks: 8 independent bugs each, with a verbose test suite.
- 3 `directed_recall` tasks: `cat` 25 source files, then answer 10 questions.

Also run, but outside the primary population:

- 2 `log_forensics` tasks. The pilot showed these are short, so they are exploratory.
- v1's long recall task, run as an anchor.

**Primary metric.** The primary metric is `total_cost_usd`, because it weights tokens the way they
are billed. Billed tokens (an unweighted sum) are reported as a secondary metric.

## Result

| contrast (10 long tasks, n=40 pairs) | cost | 95% CI (task-cluster) | pairs cheaper | Wilcoxon p | tokens | success | guard |
| --- | --- | --- | --- | --- | --- | --- | --- |
| **P1 lean → lean+obspack** (primary) | **−30.7%** | −50.0 … −1.9 | 26/40 | **0.012** | −9.5% (CI −27.9 … +6.7, p=0.28) | 39 → 39 | PASS |
| S1 stock → stock+obspack | −26.9% | −48.1 … +3.4 | 21/40 | 0.089 | −9.5% (p=0.28) | 40 → 40 | PASS |
| S2 stock → lean+obspack | −39.9% | −54.5 … −20.4 | 35/40 | 8e-9 | −28.6% | 40 → 39 | PASS |

**The pre-registered criterion is met for P1.** The CI upper bound is below 0, p < 0.05, and the
quality guard passed. Over the 40 primary pairs, cache-creation tokens fell 48.8% while cache reads
fell 5.4%. Wall time rose 4%. Recall calls were rare: 48 `z0obs` calls in 40 sessions that made 334
packs.

The pooled number hides a split by task type:

| family | P1 lean → +obspack, cost | S1 stock → +obspack, cost | packs per session | success (P1) |
| --- | --- | --- | --- | --- |
| directed_recall (n=12) | **−64.2%** (CI −71.9 … −55.0; 12/12 cheaper) | **−66.5%** (12/12) | 25 | 12 → 11 (stratum guard FAIL) |
| multi_bug_debug (n=28) | +1.3% (CI −4.2 … +6.7, p=0.66) | **+5.3%** (CI +2.4 … +8.0, p=0.07) | 1.2 | 27 → 28 |
| log_forensics (n=8, exploratory) | −8.4% | −4.2% | 0.25 | 8 → 8 |
| v1 anchor (n=4) | −52.9% (v1 measured −41.5%) | −25.9% | 7 | 4 → 4 |

## What this says

1. **ObservationPack saves money only when large Bash outputs actually reach the context.** On the
   recall tasks, every session packed all 25 file dumps. Cache writes fell about 76% (134k → 32k per
   session) and cost fell by about two thirds under both lean and stock. On the multi-bug tasks the
   model ran a verbose suite after each fix, as the prompt asked, but it usually kept each output
   under the 6000-char threshold: it targeted single test files, failures got shorter as bugs were
   fixed, and it used the Read tool, which is not packed. Packing fired about once per session, and
   cost did not change under lean. Under stock it rose 5%, because sessions made about 2.5 more tool
   calls. Log forensics took 4–5 calls with grep and awk, so there was almost nothing to pack.
2. **The pooled −31% is real but it comes from one task family.** It meets the pre-registered bar,
   but it is an average of a large effect on dump-heavy work and no effect on debugging work.
   Someone deciding whether to enable ObservationPack should expect the per-family numbers, not the
   pooled one.
3. **The token metric understates the saving again.** Tokens fell 9.5% where cost fell 30.7%. As in
   v1, the saving is in cache writes, which are the expensive tokens. On v1's anchor task, tokens fell
   64% under lean but rose 24% under stock (the extra turns add cache reads), while cost fell in both.
4. **Quality.** Pooled success was unchanged (39/40 in both arms). There was one recall failure under
   lean+obspack: `recall-evo-lab` rep 3 got 1 of 10 answers wrong after 13 recall calls. The other
   lean failure was in multi-bug, where one `multibug-tokenomics-b` session left a bug unfixed. The
   directed_recall stratum alone fails the guard at 12 → 11, so n is too small to rule out a small
   recall-accuracy cost.
5. **Lean still composes.** stock → lean+obspack cut cost 39.9% (35/40 cheaper). That is in line with
   v0's −38% for lean plus ObservationPack.

## Deviations and caveats

- **Session limit.** The primary run hit the account's session limit at 20:51 CDT. Other studies
  were also using the account. 44 cells returned "You've hit your session limit" with zero tokens.
  The runner was fixed in `53b259b` to wait for the reset and retry the same rep, and `--skip`
  resumed the run. 65 cells, 15 of them rep 0, ran after the 22:10 reset. As PREREG specifies, the
  zero-token aborts count as no-result, and every cell has exactly one real session. Some of the
  resumed "warm" reps ran after a gap of more than an hour, so their prompt cache was effectively
  cold. Cold and warm splits are reported in `results/results.json`.
- **Tasks were shaped by a pilot.** The multi-bug tasks went from 5 to 8 bugs after a one-arm
  pilot, before the pre-registration commit. log_forensics was moved to exploratory because it was
  short in the pilot.
- **Two families direct the method.** The multi_bug_debug and directed_recall prompts tell the model
  how to work (`cat` each file; run the suite verbose and don't pipe it). directed_recall is the
  regime ObservationPack is designed for, so its effect is close to an upper bound. Read-tool output
  is never packed.
- **Session length.** The expected length was ≥25 tool calls. The median was 23 under lean and 25.5
  under lean+obspack: recall sessions made 27–63 calls, multi-bug sessions 14–31.
- **Scope.** One host, one model, one night. The CIs are clustered on 10 tasks, and only 3 of those
  are recall tasks.

Reproduce (raw JSONL stays out of git):
```
.venv/bin/python benchmarks/obspack_long_v1/run.py validate
.venv/bin/python benchmarks/obspack_long_v1/run.py run --arms lean lean+obspack --reps 4 --tag main --out RAW/main-lean.jsonl
.venv/bin/python benchmarks/obspack_long_v1/run.py run --arms stock stock+obspack --reps 4 --tag main --out RAW/main-stock.jsonl
.venv/bin/python benchmarks/obspack_long_v1/analyze.py RAW/*.jsonl --out benchmarks/obspack_long_v1/results
```
