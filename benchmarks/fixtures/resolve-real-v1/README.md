# resolve-real-v1 — the real-user-question ruler

This ruler is mined from a **private** session archive and its rows contain real
user turns, private host names and session identifiers. This repository is
public, so the rows are **not committed**. They are versioned by generator and by
content hash instead:

```
manifest.json          committed — counts, yield chain, critic protocol, sha256
train/dev/sealed.jsonl generated locally, gitignored
candidates.jsonl       generated locally, gitignored (full audit pool, one row per reason)
```

## Reproduce

```bash
export PYTHONPATH=$PWD/src
python scripts/mine_real_questions.py --require-tools \
    --out .work/real-q-candidates.jsonl --multi-out .work/real-q-multi.jsonl
python scripts/gate_real_questions.py \
    --in .work/real-q-candidates.jsonl --out .work/real-q-gated.jsonl
python scripts/build_critic_pack.py
# run the independent critic passes over .work/critic-batch-*.json, then:
python scripts/freeze_real_questions.py
```

`manifest.json:dataset_sha256` must equal the sha256 of the concatenated
`json.dumps(row, sort_keys=True) + "\n"` of `dev.jsonl`. `tests/test_real_question_ruler.py`
enforces that, plus citation resolvability and split disjointness.

## What it is not

Four rows, three distinct question texts, all from **one** session, all URL
questions. The mechanical binder produced **zero** pairs that survived
independent critique; these four come from the separately adjudicated ambiguity
bucket. Read `manifest.json:yield_chain` before treating any number measured on
this ruler as a general result.
