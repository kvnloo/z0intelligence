# Template: my-first-eval

Copy this whole directory to `evals/examples/<your-eval-name>/` and edit the three files.
This is a **z0int unified-memory eval**: it checks that the memory surface answers
**only** what the evidence supports, and abstains otherwise.

Three files, three jobs:

| File | What it is | You edit it? |
| --- | --- | --- |
| `cohort.json` | the **set of cases** — the evidence the harness may know, and the questions you ask | yes |
| `question-contract.json` | the **outcome contract** each answer is held to (supported / abstain / explicit-conflict) | yes |
| `receipt.schema.json` | the **verified-result shape** the runner emits and validates against | usually no — leave as-is |

Every field is inline-commented with a `_comment_*` key. Those keys are **documentation only**;
the runner ignores anything starting with `_`. Delete them when your eval is clean.

Run it exactly like the worked example in [`../../examples/deploy-config-recall/`](../../examples/deploy-config-recall/):
no install, no model, no secrets — see [`docs/CONTRIBUTING-EVALS.md`](../../../docs/CONTRIBUTING-EVALS.md).

The one rule that makes an eval a z0int eval: **state the outcome contract and let a verifier
decide it.** Never score "the model produced an answer" — score "the answer was supported by
evidence, or the harness correctly abstained." Completion is not truth.
