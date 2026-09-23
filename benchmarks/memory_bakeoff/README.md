# Memory-capability shortlist on LongMemEval (2026-09-23)

One question: **does any of these implementations clearly deserve a local
bakeoff as a z0 memory capability?** Not: "can we reproduce vendor marketing."

Nothing here changes `context_resolve`, the policy, the verifier, the ontology or
model routing. `z0-fts5-any` is the **existing** z0 lexical operator, used
read-only as the control.

## What was measured

`src/retrieval/run_retrieval.py` and `src/evaluation/evaluate_qa.py` split
LongMemEval into a retrieval layer and a reading layer. Only the first is
reachable without hosted keys:

* **retrieval layer — MEASURED.** No LLM involved. The metric functions are
  imported straight from upstream `src/retrieval/eval_utils.py`
  (`evaluate_retrieval`: `recall_any@k`, `recall_all@k`, `ndcg_any@k`), and the
  corpus construction reproduces `process_item_flat_index(..., "session")`
  verbatim, so every arm sees identical documents and is scored by identical
  code. MRR is not implemented upstream; it is computed here from the ranking.
* **reading layer — BLOCKED, not approximated.** The official judge is
  `gpt-4o-2024-08-06`; the only local alias upstream ships is
  `llama-3.1-70b-instruct`, which does not fit a 12 GB card. No hosted keys are
  available. No end-to-end score is reported, because any number we produced
  would not be the official metric.

## Configuration (recorded per the brief)

| field | value |
|---|---|
| benchmark | LongMemEval (ICLR 2025), `xiaowu0162/LongMemEval` @ `9e0b455f` |
| dataset | `xiaowu0162/longmemeval-cleaned` rev `98d7416c24c778c2fee6e6f3006e7a073259d48f` |
| split | `longmemeval_s_cleaned.json` (LongMemEval_S, mean 47.7 sessions/question) |
| condition | **non-oracle**, session granularity, retrieval-only |
| questions | 500 total, 30 `_abs` abstention skipped → **470 scored** |
| answer model | **none** (retrieval-only arm) |
| judge | **none** (retrieval-only arm) |
| retrieval K | 1, 3, 5, 10, 30, 50 (ranked in full, cut at scoring time) |
| memory extraction model | none — `infer=False`; source text stored verbatim |
| embedding model | `nomic-embed-text` via Ollama (768-d), local |
| reranker | none in any arm |
| hosted vs OSS | **all arms fully local/OSS**; no private data left the machine |
| host | RTX 3080 Ti 12 GB, Python 3.11, Ollama 0.16.1 |

## Results

Full set, n=470 (`recall_any` = at least one evidence session retrieved):

| arm | rA@1 | rA@5 | rA@10 | rAll@10 | ndcg@10 | MRR | ms/question |
|---|---:|---:|---:|---:|---:|---:|---:|
| **z0-fts5-any** (control, existing) | **0.728** | **0.843** | **0.855** | **0.900** | **0.778** | **0.779** | **1.9** |
| longmemeval-flat-bm25 (upstream) | 0.630 | 0.791 | 0.826 | 0.843 | 0.708 | 0.704 | 3.6 |
| sibyl-memory-local | 0.549 | 0.755 | 0.808 | 0.800 | 0.652 | 0.643 | 125.3 |

Matched subset, n=72 — the questions on which Mem0 OSS completed, so all four
arms are compared on identical items:

| arm | rA@1 | rA@5 | rA@10 | rAll@10 | MRR |
|---|---:|---:|---:|---:|---:|
| **z0-fts5-any** | **0.694** | **0.833** | **0.861** | **0.917** | **0.752** |
| mem0-oss-local-qdrant | 0.542 | 0.806 | 0.833 | 0.903 | 0.652 |
| longmemeval-flat-bm25 | 0.569 | 0.792 | 0.806 | 0.875 | 0.655 |
| sibyl-memory-local | 0.486 | 0.681 | 0.778 | 0.764 | 0.579 |

Per-question-type recall_any@10 (full set) shows the same ordering, and shows
where all lexical arms collapse together: `single-session-assistant`
z0 0.089 / bm25 0.071 / sibyl 0.089 — questions answered from what the
*assistant* said, which a user-turn-only corpus cannot retrieve. That is a
corpus-construction property of the official protocol, shared by every arm.

## Required output A — existing memory systems

| backend | runnable | LongMemEval condition | score (matched n=72, rA@10 / MRR) | retrieval quality | latency | dependencies | provenance | verdict |
|---|---|---|---|---|---|---|---|---|
| **current z0** (`z0-fts5-any`) | yes (control) | non-oracle, retrieval-only, no judge | **0.861 / 0.752** | best of the four; beats upstream BM25 on every metric | 1.9 ms/q | none (stdlib SQLite) | locator `agentsview:<sid>#<mid>` per row | **KEEP** (control retained) |
| **Mem0 OSS** `mem0ai` 2.1.0 | **yes, ran locally** | same | 0.833 / 0.652 | below z0; dense-only because `fastembed` was absent so Mem0's own BM25 hybrid was disabled | 10.4 s/q (includes per-question ingest) | local Qdrant path + Ollama embedder; no Neo4j, no key, no Docker | memory id + metadata we attach | **REJECT** for this lane — does not beat the existing capability on identical data |
| **Sibyl-Memory** `sibyl-memory-client` 0.8.1 (MIT) | **yes, ran locally** | same | 0.778 / 0.579 | worst of the four; below plain upstream BM25 | 125 ms/q | none (SQLite + FTS5, pure Python) | `set_reference(key)` round-trips the session id | **REJECT** — lexical-only and weaker than the lexical baseline we already have |
| **Graphiti** `graphiti-core` 0.30.2 + `kuzu` 0.11.3 | installs, **local smoke fails** | not measurable | — | — | — | embedded Kuzu | n/a | **BLOCKED** — `add_episode` raises `RuntimeError: Parameter group_ids not found`; search then raises `Binder exception: Table RelatesToNode_ doesn't have an index with name edge_name_and_fact`. Its in-repo LongMemEval Oracle eval additionally hardcodes `OpenAIClient(gpt-4.1-mini)` + Neo4j. |
| **hyperb1iss/sibyl** (Apache-2.0) | no | not measurable | — | — | — | SurrealDB + FastAPI | n/a | **BLOCKED** — mandatory Anthropic *and* OpenAI/Gemini keys; no local provider exists anywhere in-tree |
| **Zep** | no | not measurable | — | — | — | — | n/a | **BLOCKED** — hosted `ZEP_API_KEY` only; Community Edition is deprecated and unsupported |

### What this does and does not say

It says: **on the generic memory capability, as it would actually be deployed
here — fully local, open-weight models, no hosted services — nothing tested beats
what z0 already does, and one of them is 6,500× slower per question.**

It does not say the products are weak. Mem0's and Zep's published LongMemEval
numbers use their hosted Platform, frontier extraction models and an LLM judge;
this run used a 3B local extraction model (disabled entirely, `infer=False`), a
local embedder and retrieval-only scoring. That is the deliberate comparison:
the question is not "is the managed product good", it is "does this
implementation clearly deserve a local bakeoff."

It also does not say z0 retrieval is good in absolute terms. 0.861 recall@10
means ~14% of questions have no evidence session in the top 10, before any
reading happens. The reading layer is where the unanswered questions are, and it
is exactly the layer that could not be measured here (see `docs/` for the
separate finding that the DSH-facing `inspect()` cannot open an index locator at
all).

## LoCoMo — assessed, not run

The brief asks for LoCoMo "if already supported cheaply". It was assessed and it
is not cheap here, so it was not run:

* `snap-research/locomo` is **CC BY-NC 4.0** (non-commercial) and pins a conda
  explicit export for Python 3.9 / CUDA 11.7; Python 3.11 support is unvalidated;
* **no local retrieval-only entry point exists.** `rag_utils.get_context_embeddings`
  reads `compressed_text`/`clean_text`, which the released turns do not have, and
  hardcodes `range(1, 20)`, so it cannot see sessions >= 20 (5 of 10 conversations);
  the `contriever` branches reference undefined names. Only the GPT reader path
  computes `_recall`, so producing a retrieval number means reconstructing the
  harness from `gpt_utils.prepare_for_rag` — new benchmark glue, which this
  iteration was explicitly told not to build;
* the local QA path (`evaluate_hf_llm.sh`) needs a 7B model download and a
  HuggingFace token.

LongMemEval alone already answered the decision question ("does any of these
deserve a local bakeoff?"), and it did so with the upstream metric functions
rather than a reconstruction.

## Reproduce

```bash
git clone --depth 1 https://github.com/xiaowu0162/LongMemEval /home/kvn/tmp/mem-bakeoff/LongMemEval
curl -L -o /home/kvn/tmp/mem-bakeoff/LongMemEval/data/longmemeval_s_cleaned.json \
  https://huggingface.co/datasets/xiaowu0162/longmemeval-cleaned/resolve/main/longmemeval_s_cleaned.json

python -m venv /home/kvn/tmp/mem-bakeoff/.venv
/home/kvn/tmp/mem-bakeoff/.venv/bin/pip install mem0ai ollama sibyl-memory-client rank_bm25
ollama pull nomic-embed-text

export PYTHONPATH=$PWD/src
B=/home/kvn/tmp/mem-bakeoff/.venv/bin/python
$B benchmarks/memory_bakeoff/run_bakeoff.py --arm bm25   # official flat-bm25 arm
$B benchmarks/memory_bakeoff/run_bakeoff.py --arm z0-fts5
$B benchmarks/memory_bakeoff/run_bakeoff.py --arm sibyl
$B benchmarks/memory_bakeoff/run_bakeoff.py --arm mem0 --limit 100
```

Two upstream quirks the runner handles without editing the clone:

* `eval_utils.dcg` calls `np.asfarray`, removed in NumPy 2.0. The runner aliases
  it to `np.asarray(..., dtype=float)`, which is exactly what it used to be,
  rather than pinning numpy<2 and perturbing the other arms.
* Mem0 2.1.0 rejects top-level `user_id` in `search()`; the runner passes
  `filters={"user_id": ...}`.

Note on Mem0: 24 of 96 sampled questions failed with a Qdrant `ResponseError`
under per-question collection churn. Those are reported, not hidden, and the
matched table is restricted to the 72 that completed so no arm is advantaged by
another arm's failures.
