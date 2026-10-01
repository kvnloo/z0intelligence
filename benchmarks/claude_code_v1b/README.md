# claude-code-savings-v1b — prompt-gated State Packet

Measures the fix for claude-code-savings-v1's finding that the SessionStart State Packet saves
35.5% on current-work questions but costs +10.8% on fresh coding tasks. The design is
pre-registered in [PREREG.md](PREREG.md) (`02eff0c`, committed and pushed before any measured
run). The code under test is `ebae4e3`: `packet: "gated"` plus the lean `--mcp-config`. Aggregates
are in `results/` (`results.json`, `trials.csv`). These hold no prompts, answers, session ids or paths.

- **Runs:** 372 trials on 2026-09-30/10-01, using Claude Code 2.1.286 and Sonnet. Arms: lean, lean+packet (SessionStart, v1 behaviour) and lean+packet-gated. Suites and tasks were the frozen v1 ones: 11 qa × 4 reps and 20 repo × 4 reps. `run.py validate` passed 20/20.
- **Run health:** 0 infrastructure failures and 0 error results. The session packet was seen in 124/124 of its trials. The gate fired exactly as predicted: 44/44 qa trials, and 20/80 repo trials (the 5 predicted tasks), with a median of 1,140 chars (qa) and 965 chars (repo) injected. The session packet is about 3.6k–5.3k chars.

## Pre-registered result: **FAIL** (both hypotheses)

| | contrast | tokens (ratio of totals, 95% task-cluster CI) | median paired | B cheaper | Wilcoxon p | cost | success | verdict |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| **H1** qa | session → gated | **+16.6%** [−4.8, +45.6] | −4.7% | 28/44 | 0.72 | +10.6% | 44 → 44 | **FAIL** (needs CI upper < +5%) |
| **H2** repo | session → gated | **−7.3%** [−13.8, **+0.8**] | −5.6% | 67/80 | 1.4e-5 | −7.0% | 80 → 80 | **FAIL** (needs CI upper < 0; p and guard pass) |
| S1 qa | lean → gated | −17.8% [−37.4, +5.7] | −21.8% | 28/44 | 0.002 | −15.9% | 43 → 44 | |
| S2 repo | lean → gated | −2.9% [−13.6, +6.7] | +0.1% | 35/80 | 0.64 | −1.1% | 80 → 80 | equivalence ±5% not shown |
| S3 qa | lean → session | −29.5% [−40.6, −21.0] | −28.6% | 32/44 | 3e-6 | −24.0% | 43 → 44 | replicates v1 (−35.5%) |
| S4 repo | lean → session | +4.7% [−5.3, +13.5] | +6.9% | 15/80 | 9e-6 | +6.3% | 80 → 80 | v1 +10.8%; short tasks +9.7% [+4.9, +14.9] |

Repo strata for session → gated: cold −7.8%, warm −7.2%, short −7.0% [−14.1, +2.0]. On the 5 gate-fired tasks it was −9.5% [−15.8, −2.3]. On the 15 gate-silent tasks it was −6.4% [−15.2, +4.8], 49/60 cheaper.

## What this says

1. **On coding tasks, gating removes most of the packet's overhead, but not by the pre-registered margin.** 67/80 pairs were cheaper, the median fell 5.6% and p=1e-5, yet the task-cluster CI reaches +0.8%. The pre-registered test therefore fails. Against plain lean, gated is flat (median +0.1%, 35/80 cheaper), while the session packet was +6.9% median (15/80 cheaper). Equivalence to lean within ±5% is not established (CI −13.6 to +6.7).
2. **On current-work questions, the gated packet is worse than the full SessionStart packet.** It still beats lean (median −21.8%, p=0.002) and kept 44/44 correct. But in total tokens it costs 16.6% more than the session packet, with a long right tail. Per-question (exploratory) results:
   - Gated wins where the scoped facts suffice: p02, p07 and p09.
   - Gated loses on p08 (session-file recall: 145k vs 75k tokens) and p11 (CI abstention: 188k vs 99k), and somewhat on p03 and p10.
   Two likely causes, neither tested here. (a) With the `conv` family in scope, the scoped text hits its 800-token cap (2,794 chars), so session facts are truncated. (b) The scoped packet drops the full packet's coverage list, DECISION line and abstention RULE, so on an abstention question the model explores instead of abstaining.
3. **Worker offload is now possible in lean sessions (exploratory, no savings claim).** `z0int claude-code launch --profile lean` lists `mcp__z0intelligence__route_worker` (server `z0intelligence`, connected), and all other MCP servers stay stripped. In 3/3 probe sessions the model loaded and called it. Each call returned PARENT_ONLY with a receipt ("no validated zero-cost candidate"). So the router is reachable, but it delegated nothing here.

## Implications (not tested)

- Do not switch the live default to `"gated"` on this evidence: it gives back about half the qa saving.
- A v1c candidate: a larger `conv` budget, plus carrying the coverage/abstention rule lines in the scoped packet. Such a version would need its own pre-registration. The gate vocabulary was deliberately left untuned against these suites.

## Caveats

- One host, one model, one night. The v1 numbers come from a different night, so S3/S4 are the same-night comparators.
- The qa cwd is shared across arms, as in v0 and v1.
- The `"once per session"` dedupe is only unit-tested, because every trial is a single `-p` prompt.
- `analyze.py` gained a guard so it runs on a single suite before the verdict function. The statistics did not change, and the guard was added before the repo suite finished.

Reproduce:
```
.venv/bin/python benchmarks/claude_code_v1b/run.py validate
.venv/bin/python benchmarks/claude_code_v1b/run.py run --suite qa   --reps 4 --jobs 4 --tag main --out RAW/qa-main.jsonl
.venv/bin/python benchmarks/claude_code_v1b/run.py run --suite repo --reps 4 --jobs 4 --tag main --out RAW/repo-main.jsonl
.venv/bin/python benchmarks/claude_code_v1b/run.py route-probe --n 3 --out RAW/route-probe.jsonl
.venv/bin/python benchmarks/claude_code_v1b/analyze.py RAW/qa-main.jsonl RAW/repo-main.jsonl --probe RAW/route-probe.jsonl --out benchmarks/claude_code_v1b/results
```
