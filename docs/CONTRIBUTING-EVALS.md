# Contributing evals to z0intelligence

**New here? This is the one page you need.** It takes you from a clean clone to a passing
eval in a few minutes — no ML stack, no model, no secrets.

z0intelligence (`z0int`) turns evidence, state, and observed outcomes into bounded, auditable
decisions. Its whole point is one invariant:

> **evidence ≠ claim/state ≠ authority ≠ action ≠ outcome ≠ learned procedure.**

An **eval** here is not "did the model produce an answer." It is: **state the outcome contract,
then let a verifier decide it.** An eval must say what a correct result *is* (supported by
evidence, or a correct abstention, or an explicit conflict) and never confuse *completion* with
*truth*, or *memory* with *authority*.

---

## 1. Quickstart (clone → passing eval)

You need **Python 3.10–3.13** and `git`. Nothing else for this path.

```bash
# 1. clone
git clone <this-repo-url> z0intelligence && cd z0intelligence

# 2. "setup": for a memory/unified-memory eval there is NOTHING to install.
#    The eval runner is pure Python standard library. Put src on the path:
export PYTHONPATH="$PWD/src"
#    (Only the heavy ML benchmarks need `pip install -e '.[test]'` — see section 7.)

# 3. run the worked example eval against the real runner (self-seeding, offline)
export Z0INT_HOME="$(mktemp -d)/z0"
export AGENTSVIEW_DATA_DIR="$(mktemp -d)/av"
python3 -m z0int.memory.cli eval \
  --harness dsh \
  --cohort evals/examples/deploy-config-recall/cohort.json \
  --revision 0123abcd \
  --out /tmp/my.rows.jsonl \
  --seed-av-db "$AGENTSVIEW_DATA_DIR/sessions.db" \
  --seed-ledger "$Z0INT_HOME/memory" \
  --cwd /w/z0
```

Expected stdout:

```json
{"harness": "dsh", "rows": 3, "schema": "z0eval.unified_memory_receipt.v0", "out": "/tmp/my.rows.jsonl"}
```

That just ran a real eval. `--seed-av-db` / `--seed-ledger` build a **throwaway synthetic**
memory store from the cohort (an existing store is refused, so your real data is never touched),
and `EVAL_ENDPOINT` is a local fixture — **no model is ever called.**

### Confirm every result is a valid receipt

Each line in `--out` is a **receipt**: the verified-result shape. Check them against the schema
(no extra dependency — the runner ships its own validator):

```bash
python3 - <<'PY'
import json
from pathlib import Path
from z0int.memory import acceptance
schema = json.loads(Path("evals/examples/deploy-config-recall/receipt.schema.json").read_text())
rows = [json.loads(l) for l in Path("/tmp/my.rows.jsonl").read_text().splitlines() if l.strip()]
for r in rows:
    errs = acceptance.validate(r, schema)
    print(r["question_id"], "VALID" if not errs else errs,
          "supported=%s verified=%s abstained=%s" % (r["answer_supported"], r["verified"], r["abstained"]))
print("all valid:", all(not acceptance.validate(r, schema) for r in rows))
PY
```

You should see all three rows `VALID`, with `supported=True verified=True` (and
`abstained=True` for `token-abstain`). That is a passing eval.

---

## 2. What a z0int eval *is*

Every unified-memory eval is three files (look at
[`evals/examples/deploy-config-recall/`](../evals/examples/deploy-config-recall/) and the
canonical production fixture [`tests/fixtures/z0evals-fb14919/`](../tests/fixtures/z0evals-fb14919/)):

| File | Role | In one line |
| --- | --- | --- |
| `cohort.json` | **the set of cases** | the evidence the harness may know + the questions you ask |
| `question-contract.json` | **the outcome contract** | what each answer is held to: `supported` / `abstain` / `explicit-conflict` / `newest-supported` |
| `receipt.schema.json` | **the verified-result shape** | the JSON shape the runner emits per question and validates against |

How this maps to the invariant: the **cohort** is *evidence*. The **question-contract** is the
*outcome contract* — the claim about what a right answer is. The **receipt** is the *verified
record* of what actually happened (`answer_supported`, `verified`, `abstained`,
`duplicate_injection`, `evidence_refs`, …). The runner decides each case by a verifier, so a
fluent-but-unsupported answer **fails**, and a correct "I don't know" **passes**.

The cases that make an eval *useful* (not trivial) are exactly the ones that pull these layers
apart:

- **supported** — the answer is present *and* backed by cited evidence.
- **abstention** — the evidence is absent, so the only correct outcome is to refuse (never guess).
- **contradiction** — two sources disagree; report both with provenance, pick no winner.
- **supersession (temporal)** — the newest claim is current, but the old one stays inspectable.

Aim your eval at one of these real contracts. An eval that only checks "returned non-empty
string" teaches nothing.

---

## 3. Write your own

```bash
cp -r evals/templates/my-first-eval evals/examples/<your-eval-name>
```

Edit the three files. Every field in the template has an inline `_comment_*` key explaining it;
the runner ignores keys starting with `_`, so delete them once you understand the field.

Minimum to get a real eval:

1. In `cohort.json`, add `evidence[]` items (each a fact with `source_id`, `claim`, `locator`,
   `observed_at`, `trust_class`, `required_need`). Keep it **synthetic / public-safe** — never
   paste private transcripts.
2. Add `questions[]`. For each: a `prompt` whose keywords overlap the claim you expect it to
   recall, an `expected_action` (`answer` / `abstain` / `observe`), `expected_support`
   (substrings that must appear), `forbidden` (substrings that must not), and `needs[]` tying
   the question to evidence via `required_need`.
   - For an **abstain** question, give it a need with **no** matching evidence.
   - For a **contradiction**, give two evidence items with the **same** `required_need` and claim
     text *without* an `=` (plain text like `canary-policy: manual-approval`), use
     `expected_action: "observe"`, list both in `expected_support`, and put the wrong "winner"
     in `forbidden`.
   - For **supersession**, use two `name=value` claims with the same name and different
     `observed_at`.
3. Mirror each question id into `question-contract.json` with its contracted `expected`.
4. Leave `receipt.schema.json` as copied (it is the pinned study schema).

---

## 4. Run your eval

Same command as the quickstart, pointing `--cohort` at your file:

```bash
export PYTHONPATH="$PWD/src"
export Z0INT_HOME="$(mktemp -d)/z0" AGENTSVIEW_DATA_DIR="$(mktemp -d)/av"
python3 -m z0int.memory.cli eval \
  --harness dsh \
  --cohort evals/examples/<your-eval-name>/cohort.json \
  --revision 0123abcd \
  --out /tmp/<your-eval-name>.rows.jsonl \
  --seed-av-db "$AGENTSVIEW_DATA_DIR/sessions.db" \
  --seed-ledger "$Z0INT_HOME/memory" \
  --cwd /w/z0
```

Then validate the rows with the snippet in section 1. Iterate on `prompt` / `expected_support`
until each case lands on the outcome its contract promises. `--harness` may be `dsh`, `hermes`,
`omo`, or `omp` (these emit the pinned `z0eval.unified_memory_receipt.v0`); `claude-code`,
`codex`, and `grok` emit the sibling `z0int.memory_acceptance.v0` rows.

---

## 5. Submit a PR

```bash
git checkout -b evals/<your-eval-name>
git add evals/examples/<your-eval-name>
git commit -m "evals: add <your-eval-name> unified-memory eval"
git push origin evals/<your-eval-name>
```

Open a PR that says, in a line or two: **which outcome contract** your eval verifies and **why
it is non-trivial** (which of supported / abstain / contradiction / supersession it pulls apart).
Paste the runner stdout and the `all valid: True` line from the validator. That is a complete,
reviewable contribution.

---

## 6. Where everything lives

- `evals/templates/my-first-eval/` — copy-paste skeleton, every field commented.
- `evals/examples/deploy-config-recall/` — the tiny worked example this guide runs.
- `tests/fixtures/z0evals-fb14919/` — the canonical production cohort (6 cases) this format comes
  from; byte-pinned to the `z0evals` study. Read it to see the full pattern.
- `src/z0int/memory/acceptance.py` — the runner (`seed_cohort`, `run`, `validate`).
- `src/z0int/memory/cli.py` — the `z0int memory eval` command.
- `benchmarks/` + `benchmarks/README.md` — the separate ML/decision benchmarks (see below).

---

## 7. What needs more than this (the honest blockers)

- **Memory / unified-memory evals** (this guide): run **cold with zero install** — Python stdlib
  only. This is the friend-ready path.
- **The ML/decision benchmarks** under `benchmarks/` (e.g. `shape777.py`,
  `decision_vs_generation.py`) are a different thing. They need `pip install -e '.[test]'`, which
  pulls a heavy stack (`torch`, `transformers`, `accelerate`) **and** two private git
  dependencies (`aodl-contract`, `agent-tokenomics` from `github.com/kvnloo/...`) plus model
  weights. An outside contributor without access to those repos/weights **cannot** run those
  cold. Stick to memory evals unless the owner has granted you that access.
- The `z0int memory eval` runner calls **no external model** and needs **no network** — only the
  flags above. If a command asks you for a token, endpoint, or model, you are off the friend-ready
  path.
