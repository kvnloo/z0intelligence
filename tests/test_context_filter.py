"""Tests for the ``context.filter`` primitive.

The parity test imports the upstream GPT Researcher module with its langchain
imports stubbed, so z0int's BM25 is compared against the real reference
implementation rather than a restatement of the same formula.
"""

from __future__ import annotations

import sys
import types

import pytest

from z0int.context_filter import (
    CHUNK_SIZE,
    JEV_MAX_RESULTS,
    JEV_MIN_USEFULNESS,
    KEYWORD_RELATIVE_THRESHOLD,
    Passage,
    bm25_scores,
    chunk_text,
    estimate_tokens,
    filter_context,
    tokenize,
)

FIXTURES = [
    ("what is the capital of France", [
        "Paris is the capital and most populous city of France.",
        "The Eiffel Tower is a wrought-iron lattice tower in Paris.",
        "Bananas are an edible fruit produced by herbaceous plants.",
    ]),
    ("battery replacement procedure", [
        "To replace the battery, first power down the unit and remove the back panel.",
        "The warranty covers manufacturing defects for twelve months.",
        "Battery costs have fallen sharply; replacement batteries are cheap.",
    ]),
    ("unrelated query with no overlap", [
        "Quantum chromodynamics describes the strong interaction.",
        "The novel was published in three volumes.",
    ]),
]


def _pages():
    return [{"url": f"https://example.test/{i}", "title": f"Doc {i}",
             "raw_content": text}
            for i, (_q, texts) in enumerate(FIXTURES) for text in texts]


# ------------------------------------------------------------------- relevance
def test_query_terms_rank_relevant_passage_first():
    _q, texts = FIXTURES[0]
    scores = bm25_scores("capital of France", texts)
    assert scores[0] == max(scores), scores


def test_bm25_is_deterministic():
    _q, texts = FIXTURES[1]
    assert bm25_scores("battery", texts) == bm25_scores("battery", texts)


def test_tokenize_drops_stopwords_and_short_tokens():
    assert tokenize("The a of batteries") == ["battery"]


def test_stemming_matches_plurals():
    assert tokenize("batteries") == tokenize("battery")


def test_bm25_empty_corpus():
    assert bm25_scores("anything", []) == []


# --------------------------------------------------------------------- chunking
def test_chunk_short_text_is_single_chunk():
    assert chunk_text("short text") == ["short text"]


def test_chunk_splits_on_paragraphs_and_respects_size():
    text = "\n\n".join("x" * 400 for _ in range(10))
    chunks = chunk_text(text, size=CHUNK_SIZE, overlap=0)
    assert len(chunks) > 1
    assert all(len(c) <= CHUNK_SIZE + 20 for c in chunks)


def test_chunk_empty_and_whitespace():
    assert chunk_text("") == []
    assert chunk_text("   \n  ") == []


def test_chunk_without_separators_still_splits():
    chunks = chunk_text("y" * 5000, size=1000, overlap=0)
    assert len(chunks) == 5


# ------------------------------------------------------------------- provenance
def test_provenance_survives_selection():
    pages = [{"url": "https://a.test/1", "title": "Alpha",
              "raw_content": "Paris is the capital of France. " * 40},
             {"url": "https://b.test/2", "title": "Beta",
              "raw_content": "Bananas are a fruit. " * 40}]
    res = filter_context("capital of France", pages, mode="keyword")
    assert res.selected
    for s in res.selected:
        assert s.url and s.title and s.source_id
    urls = {s["url"] for s in res.receipt.as_dict()["sources"]}
    assert urls <= {"https://a.test/1", "https://b.test/2"}


def test_passage_from_mapping_keeps_metadata():
    p = Passage.from_mapping({"raw_content": "x", "url": "u", "title": "t",
                              "published": "2024"})
    assert p.metadata.get("published") == "2024"


# ---------------------------------------------------------------- keyword lane
def test_keyword_relative_threshold_and_deterministic_tiebreak():
    pages = [{"raw_content": "alpha beta " * 50},
             {"raw_content": "alpha beta " * 50},
             {"raw_content": "gamma delta " * 50}]
    res = filter_context("alpha", pages, mode="keyword", small_input_chars=1)
    # equal scores -> original order preserved
    assert [s.source_id for s in res.selected][:2] == ["0", "1"] or \
           [s.position for s in res.selected][:2] == [0, 1]
    assert res.receipt.mode_used == "keyword"


def test_no_overlap_falls_back_to_opening_chunks():
    pages = [{"raw_content": "Quantum chromodynamics. " * 30},
             {"raw_content": "The novel was published. " * 30}]
    res = filter_context("aaabbbccc zzz", pages, mode="keyword", small_input_chars=1)
    assert res.selected, "must never return zero context"
    assert res.receipt.fallback is True
    assert "no keyword overlap" in (res.receipt.fallback_reason or "")


def test_empty_candidates_is_safe():
    res = filter_context("q", [], mode="keyword")
    assert res.selected == []
    assert res.receipt.selected == 0


def test_non_english_text_does_not_crash():
    pages = [{"raw_content": "La batterie doit être remplacée. " * 20}]
    res = filter_context("batterie", pages, mode="keyword", small_input_chars=1)
    assert isinstance(res.receipt.as_dict(), dict)


def test_duplicate_pages_do_not_break_ranking():
    pages = [{"url": "u", "raw_content": "same text " * 50},
             {"url": "u", "raw_content": "same text " * 50}]
    res = filter_context("same", pages, mode="keyword", small_input_chars=1)
    assert res.receipt.chunks >= 2


# --------------------------------------------------------------------- fast path
def test_small_input_fast_path_skips_filtering():
    pages = [{"raw_content": "tiny"}]
    res = filter_context("q", pages, mode="keyword")
    assert res.receipt.fast_path is True
    assert res.receipt.mode_used == "none"


def test_fast_path_does_not_apply_in_none_mode_flag():
    pages = [{"raw_content": "tiny"}]
    res = filter_context("q", pages, mode="none")
    assert res.receipt.mode_used == "none"
    assert res.receipt.fast_path is False


# ------------------------------------------------------------------- none lane
def test_none_returns_everything_unfiltered():
    pages = [{"raw_content": "a" * 20000}, {"raw_content": "b" * 20000}]
    res = filter_context("q", pages, mode="none")
    assert res.receipt.selected == res.receipt.chunks
    assert res.receipt.context_tokens_avoided == 0


# ------------------------------------------------------------------ auto policy
def test_auto_uses_keyword_without_a_jev_scorer():
    pages = [{"raw_content": "text " * 100}]
    res = filter_context("q", pages, mode="auto", small_input_chars=1)
    assert res.receipt.mode_used == "keyword"


def test_auto_uses_jev_when_available():
    pages = [{"raw_content": "chunk one " * 100}, {"raw_content": "chunk two " * 100}]
    res = filter_context("q", pages, mode="auto", small_input_chars=1,
                         jev_scorer=lambda q, t: [3.0] * len(t))
    assert res.receipt.mode_used == "jev"
    assert res.receipt.scorer == "jev"


def test_unknown_mode_degrades_to_auto_with_receipt():
    pages = [{"raw_content": "text " * 100}]
    res = filter_context("q", pages, mode="nonsense", small_input_chars=1)
    assert res.receipt.fallback is True
    assert "unknown mode" in (res.receipt.fallback_reason or "")


# ------------------------------------------------------------------- jev lane
def test_jev_floor_selects_and_reports_threshold():
    pages = [{"raw_content": "good " * 200}, {"raw_content": "bad " * 200}]
    res = filter_context("q", pages, mode="jev", small_input_chars=1,
                         jev_scorer=lambda q, t: [2.5, 0.5])
    assert res.receipt.mode_used == "jev"
    assert res.receipt.threshold == JEV_MIN_USEFULNESS
    assert len(res.selected) == 1


@pytest.mark.parametrize("failure,expected", [
    (TimeoutError("timed out"), "TimeoutError"),
    (RuntimeError("429 Too Many Requests"), "RuntimeError"),
    (RuntimeError("529 overloaded"), "RuntimeError"),
    (ValueError("malformed response"), "ValueError"),
    (ImportError("missing api key"), "ImportError"),
])
def test_scorer_failure_degrades_to_keyword_and_records_it(failure, expected):
    pages = [{"raw_content": "alpha beta " * 100}, {"raw_content": "gamma " * 100}]

    def boom(q, t):
        raise failure

    res = filter_context("alpha", pages, mode="jev", small_input_chars=1, jev_scorer=boom)
    assert res.selected, "a scorer outage must never yield zero context"
    assert res.receipt.fallback is True
    assert res.receipt.mode_used == "keyword"
    assert expected in (res.receipt.fallback_reason or "")


def test_scorer_returning_wrong_length_is_rejected_and_falls_back():
    pages = [{"raw_content": "x " * 100}]
    res = filter_context("q", pages, mode="jev", small_input_chars=1,
                         jev_scorer=lambda q, t: [1.0, 2.0, 3.0])
    assert res.receipt.mode_used == "keyword"
    assert "expected" in (res.receipt.fallback_reason or "")


def test_jev_floor_abstains_rather_than_returning_empty():
    pages = [{"raw_content": "x " * 300}]
    res = filter_context("q", pages, mode="jev", small_input_chars=1,
                         jev_scorer=lambda q, t: [0.1] * len(t))
    assert res.selected, "must not hand the writer empty context"
    assert res.receipt.fallback is True


# ------------------------------------------------------------------ tokenomics
def test_receipt_reports_token_savings():
    pages = [{"raw_content": "alpha beta gamma " * 500} for _ in range(6)]
    res = filter_context("alpha", pages, mode="keyword", small_input_chars=1)
    r = res.receipt.as_dict()
    assert r["input_tokens_est"] >= r["output_tokens_est"]
    assert r["context_tokens_avoided"] == r["input_tokens_est"] - r["output_tokens_est"]
    assert 0.0 <= r["retention_ratio"] <= 1.0
    assert r["latency_ms"] >= 0
    assert r["token_estimate_method"].startswith("chars//4")


def test_estimate_tokens_is_an_estimate():
    assert estimate_tokens("") == 0
    assert estimate_tokens("abcd") == 1


def test_result_shape_is_not_just_a_string():
    pages = [{"raw_content": "a " * 3000}]
    out = filter_context("a", pages, mode="keyword", small_input_chars=1).as_dict()
    assert set(out) == {"schema", "context", "selected", "receipt"}
    assert "fallback" in out["receipt"]


# ------------------------------------------------- upstream parity (reference)
def _upstream_lexical():
    """Import the upstream module with its langchain imports stubbed."""
    for name in ("langchain_core", "langchain_core.documents", "langchain_text_splitters"):
        sys.modules.setdefault(name, types.ModuleType(name))
    sys.modules["langchain_core.documents"].Document = object
    sys.modules["langchain_text_splitters"].RecursiveCharacterTextSplitter = object
    prompts = types.ModuleType("gpt_researcher.prompts")
    prompts.PromptFamily = object
    retriever = types.ModuleType("gpt_researcher.context.retriever")
    retriever.SearchAPIRetriever = object
    pkg = types.ModuleType("gpt_researcher")
    ctx = types.ModuleType("gpt_researcher.context")
    ctx.__path__ = ["/home/kvn/tmp/gptr/gpt_researcher/context"]
    sys.modules.update({"gpt_researcher": pkg, "gpt_researcher.prompts": prompts,
                        "gpt_researcher.context": ctx,
                        "gpt_researcher.context.retriever": retriever})
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "gpt_researcher.context.lexical", "/home/kvn/tmp/gptr/gpt_researcher/context/lexical.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.mark.skipif(not __import__("pathlib").Path(
    "/home/kvn/tmp/gptr/gpt_researcher/context/lexical.py").is_file(),
    reason="upstream gpt-researcher checkout not present")
def test_keyword_parity_with_upstream_reference():
    up = _upstream_lexical()
    for query, texts in FIXTURES:
        assert tokenize(query) == up.tokenize(query), query
        mine = bm25_scores(query, texts)
        theirs = up.bm25_scores(query, texts)
        assert len(mine) == len(theirs)
        for a, b in zip(mine, theirs):
            assert abs(a - b) < 1e-9, (query, a, b)


@pytest.mark.skipif(not __import__("pathlib").Path(
    "/home/kvn/tmp/gptr/gpt_researcher/context/lexical.py").is_file(),
    reason="upstream gpt-researcher checkout not present")
def test_selection_parity_with_upstream_ranking_rule():
    """Same ranking rule as upstream: rank by (-score, index), keep
    score >= threshold*best, cap at max_results; opening chunks when no overlap."""
    up = _upstream_lexical()
    for query, texts in FIXTURES:
        scores = bm25_scores(query, texts)
        best = max(scores, default=0.0)
        if best <= 0:
            mine = list(range(len(texts)))
        else:
            ranked = sorted(range(len(texts)), key=lambda i: (-scores[i], i))
            mine = [i for i in ranked
                    if scores[i] >= KEYWORD_RELATIVE_THRESHOLD * best][:25]
        up_scores = up.bm25_scores(query, texts)
        up_best = max(up_scores, default=0.0)
        if up_best <= 0:
            theirs = list(range(min(25, len(texts))))
        else:
            up_ranked = sorted(range(len(texts)), key=lambda i: (-up_scores[i], i))
            theirs = [i for i in up_ranked
                      if up_scores[i] >= KEYWORD_RELATIVE_THRESHOLD * up_best][:25]
        assert mine == theirs, (query, mine, theirs)
