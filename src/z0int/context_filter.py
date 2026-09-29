"""``context.filter`` — select the context an expensive model actually needs.

The primitive, not a GPT Researcher port. Any harness (GPT Researcher, Hermes,
OMP, Pi, retrieval, memory, research or computer-use agents) hands in candidate
passages and gets back BOTH the selected context and a machine-readable receipt.

    raw candidate context
        -> cheap deterministic filtering        (keyword / BM25, always available)
        -> semantic usefulness scoring          (jev, when verified and configured)
        -> threshold / abstention               (relative threshold, fast path)
        -> small high-value context packet
        -> expensive frontier model

Lanes
    ``keyword``  local BM25, deterministic, no network, zero marginal cost
    ``jev``      the canonical z0int Jev transport (no second TypeSafe client)
    ``auto``     the best currently VERIFIED route; always keeps keyword as fallback
    ``none``     explicit unfiltered control, for when broad context is genuinely useful

Fail-safe contract
    A scorer outage must never yield zero useful context. Expected degradation is
    preferred scorer -> keyword -> deterministic opening/source fallback, and the
    receipt always records that the fallback happened.

Provenance is non-negotiable: every selected chunk carries its source url/title,
document id and chunk position, so compression can never destroy citations.

Defaults below mirror GPT Researcher's tested values so the two implementations
can be compared like for like (Apache-2.0 upstream; this module is an independent
implementation of the same standard BM25 ranking and the same configuration).
"""

from __future__ import annotations

import math
import re
import time
from collections import Counter
from dataclasses import dataclass, field
from typing import Any, Callable, Sequence

SCHEMA = "z0int.context_filter.v1"

# --- defaults matched to the upstream reference implementation -------------
CHUNK_SIZE = 1000
CHUNK_OVERLAP = 100
SMALL_INPUT_CHARS = 8000          # below this, filtering cannot pay for itself
JEV_MIN_USEFULNESS = 1.5
JEV_MAX_RESULTS = 10
KEYWORD_RELATIVE_THRESHOLD = 0.5
KEYWORD_MAX_RESULTS = 25
BM25_K1 = 1.5
BM25_B = 0.75

MODES = ("auto", "jev", "keyword", "none")

_TOKEN = re.compile(r"\w+", re.UNICODE)
_STOPWORDS = frozenset("""
a about above after again against all am an and any are as at be because been before being below
between both but by can could did do does doing down during each few for from further had has have
having he her here hers herself him himself his how i if in into is it its itself just me more most
my myself no nor not now of off on once only or other our ours ourselves out over own same she should
so some such than that the their theirs them themselves then there these they this those through to
too under until up very was we were what when where which while who whom why will with would you your
yours yourself yourselves
""".split())


def _stem(token: str) -> str:
    """Light plural stripping, so "batteries" matches "battery"."""
    if len(token) <= 4 or token.endswith("ss"):
        return token
    if token.endswith("ies"):
        return token[:-3] + "y"
    if token.endswith(("sses", "xes", "zes", "ches", "shes")):
        return token[:-2]
    if token.endswith("s"):
        return token[:-1]
    return token


def tokenize(text: str) -> list[str]:
    return [_stem(t) for t in _TOKEN.findall(text.lower()) if len(t) > 1 and t not in _STOPWORDS]


def bm25_scores(query: str, passages: Sequence[str], *, k1: float = BM25_K1,
                b: float = BM25_B) -> list[float]:
    """Standard BM25 with IDF taken from the passages themselves.

    Deterministic: no sampling, no model, no network.
    """
    docs = [Counter(tokenize(p)) for p in passages]
    if not docs:
        return []
    avg_len = sum(sum(d.values()) for d in docs) / len(docs) or 1.0
    query_terms = set(tokenize(query))
    df = {t: sum(1 for d in docs if t in d) for t in query_terms}
    idf = {t: math.log((len(docs) - n + 0.5) / (n + 0.5) + 1) for t, n in df.items()}
    out = []
    for d in docs:
        length = sum(d.values())
        score = 0.0
        for t in query_terms:
            tf = d.get(t, 0)
            if tf:
                score += idf[t] * tf * (k1 + 1) / (tf + k1 * (1 - b + b * length / avg_len))
        out.append(score)
    return out


# --------------------------------------------------------------------- chunking
_SEPARATORS = ("\n\n", "\n", ". ", " ", "")


def chunk_text(text: str, *, size: int = CHUNK_SIZE, overlap: int = CHUNK_OVERLAP) -> list[str]:
    """Recursive character splitter with the upstream defaults.

    Implemented locally rather than pulling langchain into z0int: the primitive
    must stay dependency-light and importable without the research stack.
    """
    text = text or ""
    if not text.strip():
        return []
    if len(text) <= size:
        return [text]

    def split(block: str, seps: tuple[str, ...]) -> list[str]:
        if len(block) <= size:
            return [block]
        if not seps:
            return [block[i:i + size] for i in range(0, len(block), size)]
        sep, rest = seps[0], seps[1:]
        if sep == "":
            # the terminal "" separator means "split by character", as in the
            # reference splitter; str.split("") would raise
            return [block[i:i + size] for i in range(0, len(block), size)]
        parts = block.split(sep)
        out: list[str] = []
        buf = ""
        for part in parts:
            piece = part + sep if part is not parts[-1] else part
            if len(piece) > size:
                if buf:
                    out.append(buf)
                    buf = ""
                out.extend(split(piece, rest))
            elif len(buf) + len(piece) <= size:
                buf += piece
            else:
                out.append(buf)
                buf = piece
        if buf:
            out.append(buf)
        return out

    pieces = split(text, _SEPARATORS)
    if overlap <= 0 or len(pieces) < 2:
        return [p for p in pieces if p.strip()]
    merged: list[str] = []
    for i, piece in enumerate(pieces):
        if i == 0:
            merged.append(piece)
            continue
        tail = merged[-1][-overlap:]
        merged.append((tail + piece)[:size + overlap])
    return [p for p in merged if p.strip()]


# ----------------------------------------------------------------------- types
@dataclass(frozen=True)
class Passage:
    """A candidate passage plus the provenance that must survive selection."""

    text: str
    source_id: str = ""
    url: str = ""
    title: str = ""
    doc_id: str = ""
    position: int = 0
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_mapping(cls, raw: dict[str, Any], *, position: int = 0) -> "Passage":
        text = raw.get("raw_content") or raw.get("content") or raw.get("text") or ""
        return cls(
            text=str(text),
            source_id=str(raw.get("source_id") or raw.get("id") or raw.get("url") or ""),
            url=str(raw.get("url") or ""),
            title=str(raw.get("title") or ""),
            doc_id=str(raw.get("doc_id") or raw.get("id") or ""),
            position=int(raw.get("position", position)),
            metadata={k: v for k, v in raw.items()
                      if k not in ("raw_content", "content", "text", "url", "title",
                                   "doc_id", "id", "source_id", "position")},
        )


@dataclass(frozen=True)
class Selected:
    """One chunk that survived, with its score and full provenance."""

    text: str
    score: float
    chunk_index: int
    url: str
    title: str
    doc_id: str
    source_id: str
    position: int

    def context_line(self) -> str:
        label = self.title or self.url or self.source_id or "source"
        return f"[{label}] {self.text}"


# --------------------------------------------------------------------- receipt
def estimate_tokens(text: str) -> int:
    """Character/4 estimate. Explicitly an estimate: no tokenizer is loaded here,
    and the receipt labels it as such so nobody mistakes it for a measurement."""
    return max(0, len(text) // 4)


@dataclass
class FilterReceipt:
    schema: str = SCHEMA
    mode_requested: str = "auto"
    mode_used: str = "keyword"
    fallback: bool = False
    fallback_reason: str | None = None
    fast_path: bool = False
    scorer: str = "bm25"
    model: str | None = None
    revision: str | None = None
    threshold: float | None = None
    max_results: int | None = None
    candidates: int = 0
    chunks: int = 0
    selected: int = 0
    rejected: int = 0
    scores: list[float] = field(default_factory=list)
    input_chars: int = 0
    output_chars: int = 0
    input_tokens_est: int = 0
    output_tokens_est: int = 0
    context_tokens_avoided: int = 0
    retention_ratio: float = 0.0
    latency_ms: float = 0.0
    cost_usd: float = 0.0
    trace_id: str | None = None
    session_id: str | None = None
    sources: list[dict[str, Any]] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "mode_requested": self.mode_requested,
            "mode_used": self.mode_used,
            "fallback": self.fallback,
            "fallback_reason": self.fallback_reason,
            "fast_path": self.fast_path,
            "scorer": self.scorer,
            "model": self.model,
            "revision": self.revision,
            "threshold": self.threshold,
            "max_results": self.max_results,
            "candidates": self.candidates,
            "chunks": self.chunks,
            "selected": self.selected,
            "rejected": self.rejected,
            "input_chars": self.input_chars,
            "output_chars": self.output_chars,
            "input_tokens_est": self.input_tokens_est,
            "output_tokens_est": self.output_tokens_est,
            "context_tokens_avoided": self.context_tokens_avoided,
            "retention_ratio": round(self.retention_ratio, 4),
            "latency_ms": round(self.latency_ms, 3),
            "cost_usd": self.cost_usd,
            "trace_id": self.trace_id,
            "session_id": self.session_id,
            "sources": self.sources,
            "token_estimate_method": "chars//4 (estimate, not a tokenizer measurement)",
        }


@dataclass
class FilterResult:
    """Selected context AND the receipt. Never just a concatenated string."""

    selected: list[Selected]
    receipt: FilterReceipt

    def context(self) -> str:
        return "\n\n".join(s.context_line() for s in self.selected)

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema": SCHEMA,
            "context": self.context(),
            "selected": [
                {"text": s.text, "score": round(s.score, 6), "chunk_index": s.chunk_index,
                 "url": s.url, "title": s.title, "doc_id": s.doc_id,
                 "source_id": s.source_id, "position": s.position}
                for s in self.selected
            ],
            "receipt": self.receipt.as_dict(),
        }


# Scorer protocol: (query, passages) -> list of scores, one per passage.
Scorer = Callable[[str, Sequence[str]], list[float]]


def _chunk_passages(passages: Sequence[Passage], *, size: int, overlap: int
                    ) -> list[tuple[Passage, int, str]]:
    out: list[tuple[Passage, int, str]] = []
    for p in passages:
        for i, chunk in enumerate(chunk_text(p.text, size=size, overlap=overlap)):
            out.append((p, i, chunk))
    return out


def _to_selected(chunks, scores, keep: list[int]) -> list[Selected]:
    out = []
    for i in keep:
        p, chunk_index, text = chunks[i]
        out.append(Selected(text=text, score=float(scores[i]) if i < len(scores) else 0.0,
                            chunk_index=chunk_index, url=p.url, title=p.title,
                            doc_id=p.doc_id, source_id=p.source_id, position=p.position))
    return out


def filter_context(
    query: str,
    passages: Sequence[Passage | dict[str, Any]],
    *,
    mode: str = "auto",
    max_results: int = JEV_MAX_RESULTS,
    token_budget: int | None = None,
    chunk_size: int = CHUNK_SIZE,
    chunk_overlap: int = CHUNK_OVERLAP,
    relative_threshold: float = KEYWORD_RELATIVE_THRESHOLD,
    keyword_max_results: int = KEYWORD_MAX_RESULTS,
    small_input_chars: int = SMALL_INPUT_CHARS,
    jev_scorer: Scorer | None = None,
    jev_available: bool | None = None,
    local_scorer: Scorer | None = None,
    trace_id: str | None = None,
    session_id: str | None = None,
    emit_tokenomics: bool = True,
) -> FilterResult:
    """Select the context worth sending to an expensive model.

    ``jev_scorer`` is injected rather than imported so this module stays free of
    network dependencies and remains testable offline; the CLI wires the canonical
    z0int Jev transport in. ``local_scorer`` is for experimental/shadow local
    backends and is never selected by ``auto`` until it earns promotion.
    """
    t0 = time.perf_counter()
    ps = [p if isinstance(p, Passage) else Passage.from_mapping(p, position=i)
          for i, p in enumerate(passages)]
    receipt = FilterReceipt(mode_requested=(mode or "auto").lower(),
                            candidates=len(ps), trace_id=trace_id, session_id=session_id)
    receipt.input_chars = sum(len(p.text) for p in ps)

    chunks = _chunk_passages(ps, size=chunk_size, overlap=chunk_overlap)
    texts = [c[2] for c in chunks]
    receipt.chunks = len(chunks)
    receipt.max_results = max_results

    # ---- lane resolution -------------------------------------------------
    requested = receipt.mode_requested
    if requested not in MODES:
        requested = "auto"
        receipt.fallback = True
        receipt.fallback_reason = f"unknown mode; expected one of {MODES}"

    if requested == "auto":
        resolved = "jev" if (jev_available if jev_available is not None
                             else jev_scorer is not None) else "keyword"
    else:
        resolved = requested

    # ---- fast path: filtering cannot pay for itself on little content ----
    if requested != "none" and receipt.input_chars < small_input_chars and len(ps) <= max_results:
        keep = list(range(len(chunks)))
        receipt.mode_used = "none"
        receipt.fast_path = True
        receipt.fallback_reason = receipt.fallback_reason or "small input fast path"
        receipt.scores = [0.0] * len(chunks)
        selected = _to_selected(chunks, receipt.scores, keep)
        return _finish(selected, receipt, chunks, t0, emit_tokenomics)

    # ---- none: explicit control ------------------------------------------
    if requested == "none":
        receipt.mode_used = "none"
        receipt.scores = [0.0] * len(chunks)
        selected = _to_selected(chunks, receipt.scores, list(range(len(chunks))))
        return _finish(selected, receipt, chunks, t0, emit_tokenomics)

    # ---- jev: semantic usefulness, bounded, with local fallback ----------
    if resolved == "jev":
        scorer = jev_scorer
        if scorer is None:
            receipt.fallback = True
            receipt.fallback_reason = "jev scorer not configured"
        else:
            try:
                raw = scorer(query, texts)
                if not isinstance(raw, list) or len(raw) != len(texts):
                    raise ValueError(
                        f"scorer returned {type(raw).__name__} of length "
                        f"{len(raw) if isinstance(raw, list) else 'n/a'}, expected {len(texts)}")
                receipt.scores = [float(x) for x in raw]
                receipt.mode_used = "jev"
                receipt.scorer = "jev"
                keep = [i for i, s in enumerate(receipt.scores)
                        if s >= JEV_MIN_USEFULNESS][:max_results]
                if not keep:
                    # Every chunk below the usefulness floor: abstain from filtering
                    # rather than hand the writer an empty context.
                    receipt.fallback = True
                    receipt.fallback_reason = "no chunk met the usefulness floor"
                else:
                    receipt.threshold = JEV_MIN_USEFULNESS
                    selected = _to_selected(chunks, receipt.scores, keep)
                    return _finish(selected, receipt, chunks, t0, emit_tokenomics)
            except Exception as exc:  # noqa: BLE001
                receipt.fallback = True
                receipt.fallback_reason = f"jev scorer failed: {type(exc).__name__}: {exc}"[:200]

    # ---- keyword: always available ---------------------------------------
    receipt.mode_used = "keyword"
    receipt.scorer = "bm25"
    receipt.threshold = relative_threshold
    receipt.max_results = keyword_max_results
    scores = bm25_scores(query, texts)
    receipt.scores = scores
    best = max(scores, default=0.0)
    if not chunks:
        return _finish([], receipt, chunks, t0, emit_tokenomics)
    if best <= 0:
        # no lexical overlap at all -> deterministic opening/source fallback
        receipt.fallback = True
        receipt.fallback_reason = (receipt.fallback_reason or "") + \
            ("; " if receipt.fallback_reason else "") + "no keyword overlap; kept opening chunks"
        keep = list(range(min(keyword_max_results, len(chunks))))
    else:
        ranked = sorted(range(len(chunks)), key=lambda i: (-scores[i], i))
        keep = [i for i in ranked if scores[i] >= relative_threshold * best][:keyword_max_results]
    selected = _to_selected(chunks, scores, keep)
    return _finish(selected, receipt, chunks, t0, emit_tokenomics)


def _finish(selected: list[Selected], receipt: FilterReceipt, chunks, t0: float,
            emit: bool) -> FilterResult:
    receipt.selected = len(selected)
    receipt.rejected = max(0, receipt.chunks - receipt.selected)
    receipt.output_chars = sum(len(s.text) for s in selected)
    receipt.input_tokens_est = estimate_tokens(" " * receipt.input_chars)
    receipt.output_tokens_est = estimate_tokens(" " * receipt.output_chars)
    receipt.context_tokens_avoided = max(0, receipt.input_tokens_est - receipt.output_tokens_est)
    receipt.retention_ratio = (receipt.output_chars / receipt.input_chars
                               if receipt.input_chars else 0.0)
    receipt.latency_ms = (time.perf_counter() - t0) * 1000.0
    seen: dict[str, dict[str, Any]] = {}
    for s in selected:
        key = s.source_id or s.url or s.doc_id
        seen.setdefault(key, {"source_id": s.source_id, "url": s.url, "title": s.title,
                              "doc_id": s.doc_id, "position": s.position})
    receipt.sources = list(seen.values())
    result = FilterResult(selected=selected, receipt=receipt)
    if emit:
        _emit_tokenomics(receipt)
    return result


def _emit_tokenomics(receipt: FilterReceipt) -> None:
    """Best-effort bridge into the existing tokenomics lane. Never fatal."""
    try:
        from . import tokenomics_emit

        fn = getattr(tokenomics_emit, "context_filter_event", None)
        if callable(fn):
            fn(receipt.as_dict())
            return
        fn = getattr(tokenomics_emit, "emit", None)
        if callable(fn):
            fn({"kind": "context_filter", **receipt.as_dict()})
    except Exception:  # noqa: BLE001
        pass
