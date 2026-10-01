# claude-code-savings-v1

A re-measurement, with real headless `claude -p` runs, of how many billed tokens the **current**
z0 Claude Code integration (`integrate/claude-code-z0-stack` @ `b072468`) saves. The design is
pre-registered in [PREREG.md](PREREG.md), which was committed (`28caf90`, then `ef3f828` to freeze
the evolution-lab tasks) before any measured run. Aggregates are in `results/`: `results.json`
and `trials.csv`, one row per trial with no prompts, answers, session ids or paths.

- **Runs:** 496 trials on 2026-09-30, using Claude Code 2.1.286 and Sonnet. There were 4 arms × 124 paired units: the qa suite had 11 v0 pinned held-out questions × 4 reps, and the repo suite had 20 tasks × 4 reps.
- **Repo tasks:** 19 were fresh, spread over z0intelligence, tokenomics, kerdoios and evolution-lab. The 20th was the v0 long recall task, rerun as a replication.
- **Run health:** 0 infrastructure failures and 0 error results. Every packet arm saw the SessionStart packet (248/248).
- **Primary metric:** billed tokens = input + cache_read + cache_creation + output, taken from Claude Code's own `modelUsage`.

## Results (pooled, n = 124 pairs per contrast)

| contrast | tokens (ratio of totals, 95% task-cluster CI) | median paired | B cheaper | Wilcoxon p (Holm) | cost_usd | success A → B | guard |
| --- | --- | --- | --- | --- | --- | --- | --- |
| **C1 stock → lean+packet+obspack** (primary) | **−34.2%** [−40.3, −28.1] | −30.2% | 117/124 | 3e-20 | −45.4% | 121 → 124 | PASS |
| C2 stock → lean | −31.8% [−36.2, −26.9] | −32.8% | 118/124 | 9e-21 | −42.8% | 121 → 121 | PASS |
| C3 lean → lean+packet | −6.5% [−17.0, +4.1] | +5.9% | 46/124 | 0.55 | +1.0% | 121 → 124 | PASS |
| C4 lean+packet → +obspack | +3.0% [−2.9, +9.0] | +0.1% | 55/124 | 0.20 | −5.5% | 124 → 124 | PASS |
| S1 stock → lean+packet | −36.2% [−42.6, −30.4] | −30.8% | 120/124 | — | −42.2% | 121 → 124 | PASS |

The pooled packet effect is close to zero because it is an average of two opposite effects:

| stratum | C2 lean | C3 packet | C4 obspack | C1 full |
| --- | --- | --- | --- | --- |
| qa (current-work questions, n=44) | −25.5% | **−35.5%** [−45.4, −23.4], p(Holm)=1e-5, 42→44 correct | +7.6% [+0.2, +14.2], p=0.81, 0 packs | **−48.3%** (cost −60.4%), 41→44 |
| repo, all (n=80) | −35.0% | **+10.8%** [+6.2, +15.0], p(Holm)=2.5e-4 | +1.4%, n.s. | −27.0% (cost −38.6%), 80→80 |
| repo cold, rep 0 (n=20) | −38.5% (cost −54.2%) | +16.7% | −2.2% | −29.7% |
| repo warm, reps 1–3 (n=60) | −33.8% (cost −32.0%) | +8.9% | +2.6% | −26.1% |
| long recall task (n=4, exploratory) | −21.0% | +3.1% | −4.7% tokens, **−41.5% cost**, 4/4 | −22.3% (cost −48.3%) |

## What this says

1. **Lean is the dependable lever.** It saved 32% of tokens pooled and 43% of cost, and every stratum and repo pointed the same way. Its quality was unchanged in aggregate: one repo trial failed under lean that passed under stock, and one qa answer went the other way. This is in line with v0, which measured −27% warm and −64% cold on cost. Here, cost fell 32% warm and 54% cold on repo tasks.
2. **Whether the State Packet helps depends on the task.** On current-work questions it cut tokens by 35% and fixed every remaining miss (42→44). That replicates v0's held-out result: the qa set's stock → lean+packet cost fell 62.7%, against −61% in v0. On fresh coding and navigation tasks in a new directory, the packet has no history to report. It added +10.8% tokens (+13% cost), and quality did not change (79→80). A shipped default that turns it on for every session costs money on coding work.
3. **ObservationPack does nothing on short tasks.** It made 0 packs across all 44 qa trials and 52 packs across the 80 repo trials. Of those 52, 28 came from the long task (7 per run) and the rest from short repo tasks. The token effect on short tasks was not significant. The qa +7.6% happened with zero packs, so it is noise: the plugin never changed anything the model saw.
4. **On the long recall task, packing cut cache writes by about 55% and cost by 41.5%, but total tokens by only 4.7%.** The run made one extra turn of cheap cache reads. The unweighted token metric undervalues this saving, while cost reflects it. This result rests on n=4 (4/4 cheaper), so it is supporting evidence for v0's long-session result (−38% cost vs lean), not a new confirmation of it.
5. **The full current integration, C1, passes the pre-registered test.** Tokens fell 34.2% (CI −40% to −28%) and cost fell 45%. Success went from 121/124 to 124/124. The quality guard passed for every contrast and every stratum.

## Caveats

- **The primary metric is unweighted tokens.** Cache reads cost roughly 10× less than input, and cache writes cost more. So `cost_usd` (Claude Code's own estimate) is the better proxy for billing. Both are reported.
- **Worker routing was never measured.** The plugin's `route_worker` MCP server does not load under `--strict-mcp-config`, which the lean profile uses. As shipped, no lean arm exposes worker (groot) routing. DecisionOpportunity emission runs off the hot path in shadow mode and never injects anything, so it cannot change token use. The posture fact is part of the packet.
- **The qa suite shares one pinned cwd per repo across arms**, as v0 did, so its cache state is not controlled per arm.
- **This is one host, one model and one night.**
- **The repo tasks are short**, with a median of about 5 tool calls. Long coding sessions may behave differently.

Reproduce:
```
.venv/bin/python benchmarks/claude_code_v1/run.py validate
.venv/bin/python benchmarks/claude_code_v1/run.py run --suite qa   --reps 4 --jobs 4 --tag main --out RAW/qa-main.jsonl
.venv/bin/python benchmarks/claude_code_v1/run.py run --suite repo --reps 4 --jobs 4 --tag main --out RAW/repo-main.jsonl
.venv/bin/python benchmarks/claude_code_v1/analyze.py RAW/qa-main.jsonl RAW/repo-main.jsonl --out benchmarks/claude_code_v1/results
```
