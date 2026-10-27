# Worked example: deploy-config-recall

A tiny, complete z0int unified-memory eval that runs against the **real runner**
(`z0int memory eval`) with no install, no model, and no secrets. It verifies a real
outcome contract on three cases that are the heart of z0int:

| Question | kind | Contract | Why it matters |
| --- | --- | --- | --- |
| `port-supported` | exact | **supported** — answer the gateway port only because evidence says so | an answer must be backed by evidence, not produced because it sounds right |
| `token-abstain` | abstention | **abstain** — no evidence for an admin token, so refuse (and never emit `sk-FAKE-GUESSED-TOKEN`) | missing evidence must produce abstention, not a confident guess |
| `canary-conflict` | contradiction | **explicit-conflict** — report *both* conflicting canary policies with provenance, pick no winner | conflicting sources are surfaced, not silently resolved into false authority |

Together they exercise the core invariant: **evidence ≠ claim ≠ authority ≠ action ≠ outcome.**
Completion is never treated as truth.

## Run it

From the repo root (see [`docs/CONTRIBUTING-EVALS.md`](../../../docs/CONTRIBUTING-EVALS.md) for the
full walkthrough):

```bash
export Z0INT_HOME="$(mktemp -d)/z0" AGENTSVIEW_DATA_DIR="$(mktemp -d)/av" PYTHONPATH="$PWD/src"
python3 -m z0int.memory.cli eval \
  --harness dsh \
  --cohort evals/examples/deploy-config-recall/cohort.json \
  --revision 0123abcd \
  --out /tmp/deploy-config-recall.rows.jsonl \
  --seed-av-db "$AGENTSVIEW_DATA_DIR/sessions.db" \
  --seed-ledger "$Z0INT_HOME/memory" \
  --cwd /w/z0
```

Expected stdout:

```json
{"harness": "dsh", "rows": 3, "schema": "z0eval.unified_memory_receipt.v0", "out": "/tmp/deploy-config-recall.rows.jsonl"}
```

Each of the 3 receipt rows in the `--out` file validates against `receipt.schema.json`, with
`answer_supported=true` and `verified=true` for all three (and `abstained=true` for
`token-abstain`). Validate them yourself with the snippet in the contributing guide.
