# Weight-space study — execution contract

## Requested outcome

Compare Astra, Argon, DeepSeek, Opus and Fable on native weight reading/writing and on improving
several small language models by direct weight edits. Implement in z0intelligence with reproducible
row-level evidence. Do not confuse a working harness with completed SLM evaluations.

## Fixed tracks

### Released tasks
- Four game identifications, three diffusion shape identifications, three MinAtar network-writing tasks.
- Released prompts/networks pinned to `lm_understand-logs@4d09ba6`.
- `20261102-grid`: paper effort (high reading / xhigh writing); Argon high (native tier).
- `20261103-high`: separate high-effort API-model writing follow-up; does not overwrite the first track.
- Metric: read-task exact keyword-family correctness; MinAtar mean episode score, n=1000 per policy.
- Empty responses count as no answer, not credited zero-policy scores. Invalid edit statements are logged.

### SLM pilot
- Targets: Qwen3.5-0.8B, Qwen3-0.6B, Gemma3-270M-it (pinned unsloth mirror).
- Cross-product: three targets × five editors, four feedback passes per cell.
- `20261103-pilot`: frozen 32 MATH-500 + 32 GSM8K test IDs, one sample each, 4096-token ceiling.
- Metric: accuracy per benchmark, paired accuracy difference vs unedited; truncation and tokens separately.
- Editor sees practice sets only. No training; direct tensor editing. Code must be audited for protocol compliance.
- No promotion of an edit based solely on fewer loops, practice accuracy, or a small noisy holdout delta.
- Report null/no-edit/aborted cells explicitly. A submitted file is not an improvement.

## Completion gate

- [x] Released-task grid executed; scorer validated against released logs; parser matches three reference networks.
- [x] Unicode scoring fix tested and old Fable Pac-Man row rescored.
- [x] Separate high-effort follow-up dispatched; no original score silently replaced by this track.
- [ ] All 15 SLM sessions completed or assigned a terminal infrastructure/provider failure with raw evidence.
- [ ] Completed SLM edits independently audited and scored on held-out data against matched baselines.
- [ ] Report includes actual counts, uncertainty and protocol limitations; chart rendered in the conversation.
- [ ] Verification, source/artifact hashes, commit and PR.

## Resource policy

Use existing hardware/accounts. Do not kill unrelated GPU services, weaken the memory guard, or purchase compute.
Host GPU worker is serialized and uses batches of two. Both host RAM and VRAM failures remain visible.
Fleet MBP was attempted for CPU checks; admission returned exit 75 (queue held). Resource waiting is not measured
progress and must be reported as blocked, never as successful evaluation.

## State locations

- Worktree: `/mnt/zer0models/z0-wt/z0int-weight-space`, branch `feat/weight-space-evals`.
- Read/write results: `benchmarks/weight_space/results/20261102-grid/`.
- Follow-up results: `benchmarks/weight_space/results/20261103-high/`.
- SLM private workspace + raw turns: `~/.z0int/state/weight-space/slm/20261103-pilot/`.
- Frozen SLM IDs/protocol: the run's `protocol.json` (not visible inside the editor sandbox).
- Runner logs: `~/.z0int/state/weight-space/20261103-high.log`, SLM run's `matrix.log`.

Resume requires reading these files and checking live PID identity; do not launch duplicate grids.
