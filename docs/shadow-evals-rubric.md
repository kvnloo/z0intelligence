# Shadow evaluation rubric

This evaluator measures a candidate draft, not executed work. It has no model client, tool client, policy writer, or promotion operation.

Private calibration inputs and runtime outputs stay outside the repository. Use `/mnt/zer0models/workspace/zer0/data/operations/shadow-evals` for runtime outputs. Do not commit AskAudit histories, context packets, or provider responses.

## Anchored dimensions

Each dimension uses integer scores from 0 to 2. Higher scores are better. A score below 2 records a miss. Other dimensions cannot compensate for that miss.

| Dimension | 0 | 1 | 2 |
|---|---|---|---|
| Sentence coverage | Drops or substitutes a required sentence or deliverable. | Covers the main ask but omits a qualifier or acceptance condition. | Maps every ask sentence and acceptance condition to the interpretation, contract, or plan. |
| Fact context | Contradicts an available fact or treats stale evidence as current truth. | Uses context but omits a citation, provenance, freshness check, or material uncertainty. | Cites the relevant supplied facts and exact numbered lines. Distinguishes current, stale, and unavailable evidence. |
| Standing constraints | Violates an applicable constraint or invents permission to bypass it. | Mentions constraints but omits a boundary from planned or delegated work. | Preserves applicable phone targeting, no-Codex, privacy, Draft PR, and small-model option-capacity boundaries. |
| Past conflicts | Repeats a known conflict or follows stale history over a newer instruction. | Notices a conflict but does not resolve authority, recency, or its effect. | Cites the conflicting evidence and follows current authority. Marks unresolved uncertainty. |
| Real-target verifier | Uses an unrelated proxy, the wrong target, or unsupported completion claims. | Names a relevant check without the actual target or requested observable. | Identifies the real device, process, recipient, revision, and applicable acceptance observable. Separates proposed checks from exercised proof. |
| Delegation | Loses requirements or authority, or delegates prohibited work. | Omits original scope, target, constraints, ownership, evidence, or integration responsibility. | Preserves those fields across delegation. Explains non-applicability when no delegation is needed. |
| Hallucination penalty | Fabricates harmful facts, permission, citations, execution, tool results, or success. | Asserts an unsupported lower-impact fact or promotes inference to certainty. | Fabricates no facts. Marks inference, unavailable facts, and proposed work explicitly. |

The named standing constraints are checks against supplied authority. A draft must not invent their detailed values or apply stale rules over newer direct instructions. For example, an option-capacity check needs the actual model and applicable capacity. A phone check needs the authorized device and actual target, not a general claim that phone work is safe.

Use `null` when evidence does not support a dimension score. Missing context is not a failure and is not success. An inapplicable dimension can receive 2 only with an explicit applicability explanation.

A draft can propose an acceptance check without executing it. The verifier dimension evaluates that proposal. An original assistant answer that claims execution needs corresponding real-target evidence. A real parent's successful execution never gives success credit to a shadow candidate.

## Judge input and output

`judge_prompt(job, bundle, candidate) -> str` creates a blind text prompt. The caller must supply an empty tool set. The helper makes no provider calls.

For current bundles, the prompt includes these fields once:

- `ask` and `recent_user_msgs`
- Flat numbered `facts`, including source identity and line citations
- `sources`, including retrieval errors and missing-source status
- `privacy` and `bundle_hash`
- The candidate's actual draft content

The helper does not repeat packet, memory, inventory, or fact-catalog text beside flat facts. Historical calibration bundles use their numbered packet and explicit unavailable-memory/inventory status instead.

The helper excludes candidate provider/model identity, gold labels, outcome joins, corrections, and future-message metadata. It does not serialize a whole job. Labels and future turns must not be embedded in caller-supplied text. Candidate content remains intact so that the judge can detect fabricated claims, including invented success claims.

The prompt treats all supplied material as untrusted evidence. It gives that material no instruction authority. Past instructions inside evidence remain facts for comparison, not permission to execute them.

`parse_judgment(content) -> dict` accepts JSON or one fenced JSON object. It requires exactly these seven dimension names:

```json
{
  "dimensions": {
    "coverage": {"score": 2, "evidence": ["ask sentence 1 maps to plan step 1"], "fact_ids": []},
    "context": {"score": null, "evidence": ["historical source snapshot is unavailable"], "fact_ids": []},
    "constraints": {"score": 2, "evidence": ["packet:3 boundary appears in contract"], "fact_ids": ["stable-fact-id"]},
    "conflicts": {"score": 2, "evidence": ["no material conflict appears in supplied context"], "fact_ids": []},
    "verifier": {"score": 2, "evidence": ["plan names the actual device and requested observable"], "fact_ids": []},
    "delegation": {"score": 2, "evidence": ["no delegation is needed for this bounded task"], "fact_ids": []},
    "hallucination": {"score": 2, "evidence": ["draft distinguishes proposed checks from execution"], "fact_ids": []}
  },
  "miss_detected": null,
  "category": "unknown",
  "task_category": "operations"
}
```

The example demonstrates unknown handling, not calibration evidence.

Every dimension needs nonempty evidence strings and a list of unique fact IDs. The parser rejects duplicate JSON keys, Boolean scores, out-of-range scores, missing dimensions, and contradictory miss/category fields. It returns an unknown record for invalid output. It never repairs invalid output into a favorable score.

- Any known score below 2 makes `miss_detected` true. `category` must name a missed dimension.
- Seven known scores of 2 make `miss_detected` false and `category` `none`.
- Otherwise, `miss_detected` is null and `category` is `unknown`.

`task_category` is required and separate from the primary miss `category`. Its bounded values are `coding`, `operations`, `research`, `planning`, `communication`, and `other`. It classifies the original work, not candidate success. A missing or unsupported value makes the judgment invalid.

The parser adds `valid` and `complete` flags. A partial judgment can identify a known miss, but cannot enable scores or produce a finding.

## Frozen calibration selection

`calibration_cases(ask_audit_root) -> list[dict]` reads the existing AskAudit schema. It does not create gold labels.

The source is `/mnt/zer0models/workspace/zer0/data/operations/ask-audit`:

- `labels.jsonl` contains reviewed outcomes and evidence references.
- `final_review_contexts.jsonl` contains target, before, and after windows.
- `dialogue.jsonl` contains persisted source turns.

The inspected schema contains 103 reviewed failures and 192 `not-a-miss` labels. Recognized failure labels are `ignored`, `narrowed/paraphrased-away`, `done-but-claimed-wrongly`, `did-something-else`, `stale-knowledge`, and `invented-constraint`. Unknown outcomes do not become failures by exclusion.

Select all 103 failure labels. Select 50 reviewed `not-a-miss` labels by ascending SHA-256 of canonical JSON `["ask-audit-sha256-v1", harness, session, entry_id]`. Label-file order does not affect membership. Duplicate source identities and incorrect failure counts stop selection with an explicit error.

A reviewed negative means that this audit did not establish an instruction failure. It does not establish completed work, general correctness, or candidate success. Silence and absent reactions are never negative gold by themselves.

Some failure labels cite an assistant acknowledgment after the correction. That acknowledgment is not the original answer. Calibration reconstruction uses the labeled original ask and the last persisted assistant answer strictly before the correction. It excludes the correction and later turns. If the original ask or answer is absent, the case remains `missing_gold`.

For a reviewed negative, reconstruction uses its cited original ask, or the reviewed target when no ask reference exists. It selects the assistant answer before the next user turn. Later reactions and later assistant messages do not enter the judge packet.

Each returned case contains:

- `case_id`, `selection_version`, and private `gold_source` provenance
- `gold_miss` and `gold_scope: reviewed_failure_label_not_task_success`
- `candidate_success: null`
- `status: ready | missing_gold` and explicit `missing_gold` reasons
- Blind `job`, `bundle`, and `candidate` input objects
- `answer_source` when reconstruction succeeds

The label, review reason, and correction stay outside `job`, `bundle`, and `candidate`. Historical memory and inventory are explicitly unavailable, not silently replaced by today's facts. Reconstruction is not evidence that all cases have sufficient gold. The parent must report unresolved gaps instead of substituting a later acknowledgment or guessed answer.

## Counts and score gate

`calibration_metrics(rows, *, expected_positive=103, expected_negative=50) -> dict` expects cases with a `judgment` field. It returns this count-table layout:

| Gold label | Predicted miss | Predicted no miss | Unknown prediction |
|---|---:|---:|---:|
| Reviewed failure | TP | FN | Unknown positive |
| Reviewed nonfailure | FP | TN | Unknown negative |
| Unknown or unsupported gold | Unscored | Unscored | Unscored |

Precision is `TP / (TP + FP)`. Its value is null when that denominator is zero. Recall is `TP / all supported reviewed failures`, including unknown positive predictions in its denominator. `recall_on_scored` is a separate conditional statistic. Do not substitute it for recall.

The result includes `tp`, `fp`, `fn`, `tn`, unknown counts, missing-gold identifiers, duplicate counts, and a content-derived `calibration_id`. It includes Wilson 95% precision/recall intervals and their widths. It contains no invented live measurements.

The original inventory remains 153 cases. The parent subsequently approved a smaller live calibration: a deterministic, stratified 40-case subset. This subset contains 27 failures and 13 reviewed negatives. Select ready-gold cases per class in the preserved deterministic order. Retain excluded and unavailable gold in the inventory report.

The admitted cohorts are:

- `full153`: defaults of 103 failures and 50 reviewed negatives
- `bootstrap_subset40`: explicit `expected_positive=27, expected_negative=13`

Other cohort sizes do not weaken the score gate. A 40-case measurement never represents a full 153-case measurement. The receipt and exported finding metric name the cohort and its sample count. Report the interval widths, including the larger uncertainty of the subset.

The Oct 24 Codex pace exception is restricted to the private `omp-study` root,
ten real messages and the 27/13 calibration subset. It expires at 01:50 CDT
Oct 25 (06:50 UTC). It does not waive exhausted windows, privacy, model identity,
or context limits and earns no O14 credit. Retain exclusions rather than silently
replacing unavailable arms. Atomic JSON persistence streams large audit artifacts
instead of making another full serialized copy in memory.

Scores stay disabled until all these conditions hold:

1. The exact admitted number of unique cases is present.
2. Both supported label counts equal the cohort's declared counts.
3. Every case is `ready`, without missing gold.
4. Every judgment is valid and complete.

Only then do `measurement_complete` and `scores_enabled` become true for that cohort. These flags do not promote policy or assert an unstated judge-quality threshold. Precision, recall, and their interval widths remain visible for parent review.

## Canonical findings

`findings(rows, scope) -> list[dict]` returns finding records. It never appends a ledger, changes policy, grants credit, or promotes a model.

Each eligible row needs:

```text
provider, model, task, category, context_hash, job_id
real_job: true
synthetic: false
calibrated: true
judgment: valid complete parsed judgment
```

Finding-row `category` must equal `judgment.task_category`, not the primary missed dimension. `context_hash` must be a lowercase SHA-256. Exact duplicate job/model/task/context measurements count once. Contradictory duplicates stop export.

The scope needs:

```text
calibration: complete calibration_metrics receipt
source_path: existing absolute persisted results-file path
ts: explicit observation timestamp with timezone
run_id: optional source-run identity
```

The exporter hashes the persisted source bytes. It groups only by exact provider, model, original task, category, and context hash. The metric is `acceptance_rate`, with `accepted`, `n`, and `acceptance_kind: rubric_draft_quality`. This metric does not measure executed-task success. Wilson 95% bounds use `z = 1.959963984540054`.

The subject uses `family: model.routing.acceptance`, `job: ShadowEvals`, and bounded `task: intent.shadow.<judgment.task_category>`. It retains the original task in `task_class` and primary miss dimensions in `miss_categories`. The arm uses `kind: frontier`, plus the exact provider and model. Every finding has `evidence_kind: proxy`.

The canonical schema originated in `shadow-learning-loop`. The current integration dependency is the `decision-policy` package:

- `src/z0int/policy/findings.py`: `Finding`, `finding_id`, and `validate`
- `src/z0int/policy/ingest.py`: existing evidence adapters and interval conventions

The exporter imports that dependency only when requested. An intent-only installation can still queue jobs, build blind prompts, calibrate, and report. Finding export fails with an actionable dependency error if the canonical package is absent. There is no copied schema or alternate ledger implementation.

Every exported row has `schema: z0.finding.v1`, explicit `synthetic: false`, and `metric.promotion_eligible: false`. The exporter recalculates the canonical finding ID after adding the explicit synthetic marker. Source kind is `shadow_offload`, with adapter `shadow_evals`.

The parent can write the owned artifact with canonical `append_findings`. The helper does not append to the central ledger or call the generic offload ingest adapter. Proxy draft quality can support only a scoped routing prior. It cannot become product-promotion or executed-success evidence.

The scope note limits the finding to the exact real jobs, context, task, category, and model. It states the measured calibration cohort and count. The expiry is 14 days after the observation timestamp. `candidate_execution_success` remains null, regardless of parent outcomes.

## Law candidates

`law_candidates(rows) -> str` returns a private review document, not executable policy. The parent owns `LAW-CANDIDATES` and `REPORT` files.

Law analysis requires the finding row fields plus:

- `compared_models`: the complete expected `provider:model` list for that real job
- `fact_catalog`: cited fact ID to `{text, source}` mapping
- `real_job_id`: optional stable original-job identity when `job_id` identifies a replay

Use the same comparison set and context hash for every model within a job. Missing models, duplicate models, unknown judgments, or missing fact definitions cannot establish agreement. Require at least two compared models. A failed or absent completion is not an affirmative rubric miss.

The latest comparison panel includes the exact Sol reference model. Keep context-failed or missing local offers in `compared_models`. Their missing judgments must prevent all-model law claims.

A proposal needs the same exact sourced fact and rubric dimension missed by the same complete comparison panel on at least two distinct real jobs. Matching fact IDs alone is insufficient. Fact identity uses the canonical hash of its exact text and stable source identity. A different ID can identify the same fact. A reused ID with different text cannot establish recurrence.

Every proposal contains:

- The exact fact and source
- Per-job and per-model evidence, context hashes, and cited fact IDs
- Explicit uncertainty about correlated judge errors and causal claims
- A proposed packet change and a proposed scoped law-review change
- A parent-review-only boundary

No candidate applies a law, writes the central findings ledger, or earns candidate success from a real parent's successful work.

## Sol reference comparison

`reference_comparison(rows, reference_model) -> dict` compares cheaper candidate drafts with an explicitly named `provider:model` reference. The parent supplies the exact Sol identity. The helper does not choose or call a model.

Each pair needs the same real job, original task, context hash, and complete expected comparison panel. Both judgments must be calibrated, complete, and nonsynthetic. Missing references, missing completions, conflicting duplicate rows, or invalid judgments produce unknown comparisons. A context-failed local offer receives no invented score.

The output includes per-dimension candidate and reference scores, their signed deltas, and both evidence lists. Summary rows separate reference task category, original task, rubric dimension, and candidate model. They report paired counts and unknown counts alongside mean scores and mean deltas.

Sol's `task_category` supplies the comparison grouping. The output also reports candidate/reference task-category disagreement. The reference is not factual truth and does not automatically receive a maximum score. The helper does not convert comparative quality into factual acceptance or execution-success credit.

These records remain proxy evidence with `promotion_eligible: false` and `candidate_execution_success: null`. Pairwise comparisons can remain useful when another model is missing. Missing models still block all-model law proposals.

## Verification ownership

The child implementation does not run builds, tests, formatters, inference, or provider calls. The parent runs integrated checks after all shared-worktree changes are present. Suggested focused checks are the judge behavior tests and canonical-policy compatibility in an environment that includes the deployed policy package. Live calibration and replay need the parent's explicit authorization and provider safeguards.
