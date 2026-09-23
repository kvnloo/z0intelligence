#!/usr/bin/env python3
"""LANE A: retrieval-quality bakeoff on LongMemEval_S (non-oracle).

Why this shape
--------------
The brief asks for the official or closest faithful reproducible evaluation, and
for retrieval quality to be measured separately from answer-model quality. The
official LongMemEval harness splits exactly that way:

  * `src/retrieval/run_retrieval.py`  -> Recall/NDCG, no LLM required
  * `src/evaluation/evaluate_qa.py`   -> LLM-judge accuracy (needs gpt-4o)

This machine has no hosted API keys and the only local judge alias the official
harness ships is a 70B model that does not fit a 12 GB card, so the **QA layer is
not measurable here** and is reported as BLOCKED rather than approximated.

The retrieval layer is measurable, and this script measures it for several
backends on the identical protocol. Fidelity note: the metric functions are
imported directly from the upstream repository
(`src/retrieval/eval_utils.evaluate_retrieval`), and the corpus construction
reproduces `process_item_flat_index(..., granularity="session")` verbatim:

    corpus[i] = " ".join(turn.content for turn in session if turn.role == "user")

so a session is one document containing only the user's own turns.

Every arm therefore sees exactly the same documents and is scored by exactly the
same upstream code. What differs is only store/index/retrieve.
"""

from __future__ import annotations

import argparse
import json
import random
import sqlite3
import statistics
import sys
import time
from pathlib import Path

DS = Path("/home/kvn/tmp/mem-bakeoff/LongMemEval")
sys.path.insert(0, str(DS))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "src"))

K_VALUES = (1, 3, 5, 10, 30, 50)


# --------------------------------------------------------------------------- #
# official protocol
# --------------------------------------------------------------------------- #
def session_corpus(entry: dict) -> tuple[list[str], list[str]]:
    """Reproduce `process_item_flat_index(data, 'session', sess_id, ts)`."""
    corpus: list[str] = []
    ids: list[str] = []
    for sess_id, sess in zip(entry["haystack_session_ids"], entry["haystack_sessions"]):
        text = " ".join(t["content"] for t in sess if t["role"] == "user")
        corpus.append(text)
        sid = sess_id
        if "answer" in sess_id and all(
            not t.get("has_answer") for t in sess if t["role"] == "user"
        ):
            sid = sess_id.replace("answer", "noans")
        ids.append(sid)
    return corpus, ids


def _np2_shim() -> None:
    """Upstream `eval_utils.dcg` calls `np.asfarray`, removed in NumPy 2.0.

    `np.asfarray(x)` is exactly `np.asarray(x, dtype=float)`, so aliasing it
    restores the upstream function verbatim. The alternative -- pinning numpy<2
    in this venv -- would perturb the other arms, and editing the clone would
    compromise the fidelity claim. Neither is necessary.
    """
    import numpy as np

    if not hasattr(np, "asfarray"):
        np.asfarray = lambda a, dtype=float: np.asarray(a, dtype=dtype)  # noqa: E731


def official_metric(rankings: list[int], correct: list[str], corpus_ids: list[str], k: int):
    _np2_shim()
    from src.retrieval.eval_utils import evaluate_retrieval

    return evaluate_retrieval(rankings, correct, corpus_ids, k=k)


def rank_to_indices(ranked_ids: list[str], corpus_ids: list[str]) -> list[int]:
    pos = {cid: i for i, cid in enumerate(corpus_ids)}
    out = [pos[c] for c in ranked_ids if c in pos]
    seen = set(out)
    out += [i for i in range(len(corpus_ids)) if i not in seen]
    return out


# --------------------------------------------------------------------------- #
# arms
# --------------------------------------------------------------------------- #
class Bm25Arm:
    """The upstream `flat-bm25` arm: whitespace tokenization, BM25Okapi."""

    name = "longmemeval-flat-bm25"

    def rank(self, query: str, corpus: list[str], corpus_ids: list[str]) -> list[str]:
        from rank_bm25 import BM25Okapi

        bm25 = BM25Okapi([d.split(" ") for d in corpus])
        scores = bm25.get_scores(query.split(" "))
        order = scores.argsort()[::-1]
        return [corpus_ids[i] for i in order]


class Z0FtsArm:
    """Current z0 lexical retrieval: SQLite FTS5 + z0's own `fts_match`."""

    name = "z0-fts5-any"

    def __init__(self) -> None:
        from z0int.context_providers import fts_match  # read-only reuse

        self._fts_match = fts_match

    def rank(self, query: str, corpus: list[str], corpus_ids: list[str]) -> list[str]:
        con = sqlite3.connect(":memory:")
        con.execute("create virtual table docs using fts5(cid unindexed, body)")
        con.executemany("insert into docs(cid,body) values (?,?)", list(zip(corpus_ids, corpus)))
        match = self._fts_match(query, mode="any")
        if not match:
            return list(corpus_ids)
        try:
            rows = con.execute(
                "select cid from docs where docs match ? order by rank limit ?",
                (match, len(corpus_ids)),
            ).fetchall()
        except sqlite3.OperationalError:
            return list(corpus_ids)
        finally:
            con.close()
        ranked = [r[0] for r in rows]
        seen = set(ranked)
        ranked += [c for c in corpus_ids if c not in seen]
        return ranked


class SibylArm:
    """Sibyl-Labs/Sibyl-Memory (MIT): local SQLite + FTS5."""

    name = "sibyl-memory-local"

    def __init__(self, root: Path) -> None:
        import os

        os.environ["SIBYL_MEMORY_TELEMETRY"] = "0"
        from sibyl_memory_client import MemoryClient

        self._client = MemoryClient
        self._root = root

    def rank(self, query: str, corpus: list[str], corpus_ids: list[str]) -> list[str]:
        import shutil

        db = self._root / "sibyl"
        shutil.rmtree(db, ignore_errors=True)
        db.mkdir(parents=True, exist_ok=True)
        c = self._client.local(db / "mem.db")
        for cid, text in zip(corpus_ids, corpus):
            c.set_reference(cid, text or "(empty)")
        res = c.search(query, limit=len(corpus_ids))
        ranked: list[str] = []
        for item in res:
            key = item.get("key") if isinstance(item, dict) else None
            if key in corpus_ids and key not in ranked:
                ranked.append(key)
        ranked += [c for c in corpus_ids if c not in ranked]
        return ranked


class Mem0Arm:
    """Mem0 OSS 2.1.0: local Ollama embedder + on-disk Qdrant, no LLM extraction.

    `infer=False` is deliberate. With extraction enabled, Mem0's LLM rewrites the
    stored text, which would confound "does the memory engine index/retrieve
    well" with "is a 3B local model a good extractor". The brief asks for those
    layers to be separated, so this arm measures the store/index/retrieve layer
    with the source text preserved verbatim.
    """

    name = "mem0-oss-local-qdrant"

    def __init__(self, root: Path, model: str = "nomic-embed-text") -> None:
        import os

        os.environ["MEM0_TELEMETRY"] = "False"
        from mem0 import Memory

        self.Memory = Memory
        self._root = root
        self._model = model

    def rank(self, query: str, corpus: list[str], corpus_ids: list[str]) -> list[str]:
        import shutil
        import uuid

        store = self._root / f"qdrant-{uuid.uuid4().hex[:8]}"
        shutil.rmtree(store, ignore_errors=True)
        cfg = {
            "llm": {
                "provider": "ollama",
                "config": {"model": "qwen2.5:3b", "ollama_base_url": "http://127.0.0.1:11434"},
            },
            "embedder": {
                "provider": "ollama",
                "config": {
                    "model": self._model,
                    "ollama_base_url": "http://127.0.0.1:11434",
                    "embedding_dims": 768,
                },
            },
            "vector_store": {
                "provider": "qdrant",
                "config": {
                    "collection_name": "lme",
                    "path": str(store),
                    "embedding_model_dims": 768,
                },
            },
        }
        try:
            m = self.Memory.from_config(cfg)
            for cid, text in zip(corpus_ids, corpus):
                m.add(text or "(empty)", user_id="lme", infer=False, metadata={"cid": cid})
            hits = m.search(query, filters={"user_id": "lme"}, limit=len(corpus_ids))
        finally:
            shutil.rmtree(store, ignore_errors=True)
        ranked: list[str] = []
        for h in hits.get("results", hits) if isinstance(hits, dict) else hits:
            cid = (h.get("metadata") or {}).get("cid") if isinstance(h, dict) else None
            if cid in corpus_ids and cid not in ranked:
                ranked.append(cid)
        ranked += [c for c in corpus_ids if c not in ranked]
        return ranked


def build_arm(name: str, root: Path):
    if name == "bm25":
        return Bm25Arm()
    if name == "z0-fts5":
        return Z0FtsArm()
    if name == "sibyl":
        return SibylArm(root)
    if name == "mem0":
        return Mem0Arm(root)
    raise SystemExit(f"unknown arm {name}")


# --------------------------------------------------------------------------- #
def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default=str(DS / "data" / "longmemeval_s_cleaned.json"))
    ap.add_argument("--arm", required=True, choices=("bm25", "z0-fts5", "sibyl", "mem0"))
    ap.add_argument("--limit", type=int, default=0, help="0 = all 500")
    ap.add_argument("--seed", type=int, default=20260923)
    ap.add_argument("--out", default=None)
    ap.add_argument("--work", default="/home/kvn/tmp/mem-bakeoff/armwork")
    a = ap.parse_args()

    data = json.loads(Path(a.data).read_text())
    scored = [e for e in data if not e["question_id"].endswith("_abs")]
    if a.limit:
        by_type: dict[str, list[dict]] = {}
        for e in scored:
            by_type.setdefault(e["question_type"], []).append(e)
        rnd = random.Random(a.seed)
        per = max(1, a.limit // len(by_type))
        sample: list[dict] = []
        for t, group in sorted(by_type.items()):
            rnd.shuffle(group)
            sample += group[:per]
        sample = sample[: a.limit]
    else:
        sample = scored

    root = Path(a.work)
    root.mkdir(parents=True, exist_ok=True)
    arm = build_arm(a.arm, root)

    per_q: list[dict] = []
    started = time.perf_counter()
    for i, e in enumerate(sample):
        corpus, corpus_ids = session_corpus(e)
        correct = [c for c in corpus_ids if "answer" in c]
        t0 = time.perf_counter()
        try:
            ranked = arm.rank(e["question"], corpus, corpus_ids)
            err = None
        except Exception as exc:  # noqa: BLE001
            ranked, err = list(corpus_ids), f"{type(exc).__name__}: {exc}"
        ms = (time.perf_counter() - t0) * 1000
        rankings = rank_to_indices(ranked, corpus_ids)
        metrics = {}
        for k in K_VALUES:
            ra, rall, nd = official_metric(rankings, correct, corpus_ids, k)
            metrics[f"recall_any@{k}"] = ra
            metrics[f"recall_all@{k}"] = rall
            metrics[f"ndcg_any@{k}"] = nd
        # MRR is not implemented upstream; compute it from the ranking.
        rr = 0.0
        for rank_i, idx in enumerate(rankings, 1):
            if corpus_ids[idx] in correct:
                rr = 1.0 / rank_i
                break
        per_q.append(
            {
                "question_id": e["question_id"],
                "question_type": e["question_type"],
                "n_docs": len(corpus),
                "n_correct": len(correct),
                "metrics": metrics,
                "mrr": rr,
                "latency_ms": ms,
                "error": err,
            }
        )
        if (i + 1) % 25 == 0:
            print(f"  {i+1}/{len(sample)}", file=sys.stderr, flush=True)

    def mean(key: str) -> float:
        return round(statistics.mean(q["metrics"][key] for q in per_q), 4)

    by_type: dict[str, list[dict]] = {}
    for q in per_q:
        by_type.setdefault(q["question_type"], []).append(q)

    out = {
        "arm": arm.name,
        "arm_key": a.arm,
        "dataset": "xiaowu0162/longmemeval-cleaned :: longmemeval_s_cleaned.json",
        "dataset_sha256_note": "see memory-bakeoff README",
        "condition": "non-oracle (LongMemEval_S), session granularity, retrieval-only",
        "n_questions": len(per_q),
        "n_skipped_abstention": 500 - len(scored),
        "metrics": {
            **{f"recall_any@{k}": mean(f"recall_any@{k}") for k in K_VALUES},
            **{f"recall_all@{k}": mean(f"recall_all@{k}") for k in K_VALUES},
            **{f"ndcg_any@{k}": mean(f"ndcg_any@{k}") for k in K_VALUES},
            "mrr": round(statistics.mean(q["mrr"] for q in per_q), 4),
            "mean_latency_ms": round(statistics.mean(q["latency_ms"] for q in per_q), 1),
        },
        "per_question_type": {
            t: {
                "n": len(qs),
                f"recall_any@10": round(statistics.mean(q["metrics"]["recall_any@10"] for q in qs), 4),
                "mrr": round(statistics.mean(q["mrr"] for q in qs), 4),
            }
            for t, qs in sorted(by_type.items())
        },
        "errors": sum(1 for q in per_q if q["error"]),
        "wall_seconds": round(time.perf_counter() - started, 1),
        "per_question": per_q,
    }
    dest = Path(a.out) if a.out else root / f"result-{a.arm}.json"
    dest.write_text(json.dumps(out, indent=1))
    print(json.dumps({k: v for k, v in out.items() if k != "per_question"}, indent=2))
    print(f"-> {dest}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
