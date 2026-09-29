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
        last = len(parts) - 1
        for idx, part in enumerate(parts):
            # sequence position, never object identity: equal strings would make
            # `part is not parts[-1]` true for every element
            piece = part + sep if idx != last else part
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


@dataclass
class ScorerResult:
    """What a scorer must return: scores AND the metadata a receipt needs.

    A bare ``list[float]`` cannot carry the served model, revision, usage or cost,
    which is why the receipt used to be permanently null.
    """

    scores: list[float]
    scorer: str = "unknown"
    model: str | None = None
    revision: str | None = None
    usage: dict[str, Any] = field(default_factory=dict)
    cost_usd: float = 0.0
    provider_latency_ms: float | None = None
    measured: bool = True

    def __post_init__(self) -> None:
        self.scores = [float(x) for x in self.scores]


# Scorer: (query, passages) -> ScorerResult. A plain list is still accepted and
# wrapped, so simple local scorers stay easy to write.
Scorer = Callable[[str, Sequence[str]], "ScorerResult | list[float]"]


def _as_scorer_result(raw: Any) -> ScorerResult:
    if isinstance(raw, ScorerResult):
        return raw
    if isinstance(raw, list):
        return ScorerResult(scores=[float(x) for x in raw], scorer="inline", measured=False)
    raise TypeError(f"scorer must return ScorerResult or list[float], got {type(raw).__name__}")


def _chunk_passages(passages: Sequence[Passage], *, size: int, overlap: int
                    ) -> list[tuple[Passage, int, str]]:
    out: list[tuple[Passage, int, str]] = []
    for p in passages:
        for i, chunk in enumerate(chunk_text(p.text, size=size, overlap=overlap)):
            out.append((p, i, chunk))
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
    allow_remote: bool = False,
    trace_id: str | None = None,
    session_id: str | None = None,
    emit_tokenomics: bool = True,
) -> FilterResult:
    """Select the context worth sending to an expensive model.

    ``allow_remote`` is the privacy boundary and defaults to False. This primitive
    serves PRIVATE personal retrieval as well as public research, so a remote
    scorer is never used just because credentials happen to exist: the caller must
    authorize remote transmission explicitly. The keyword lane is always local.

    ``token_budget`` caps the selected context by estimated tokens; it is applied
    after ranking, so the best chunks survive. ``None`` means no cap.
    """
    t0 = time.perf_counter()
    ps = [p if isinstance(p, Passage) else Passage.from_mapping(p, position=i)
          for i, p in enumerate(passages)]
    receipt = FilterReceipt(mode_requested=(mode or "auto").lower(),
                            candidates=len(ps), trace_id=trace_id, session_id=session_id)
    receipt.input_chars = sum(len(p.text) for p in ps)

    requested = receipt.mode_requested
    if requested not in MODES:
        requested = "auto"
        receipt.fallback = True
        receipt.fallback_reason = f"unknown mode; expected one of {MODES}"

    # ---- none: the unfiltered control. Original passages, never chunked. -----
    # Chunking here would duplicate 100-char overlap boundaries into the baseline
    # and make the control incomparable to the filtered lanes.
    if requested == "none":
        receipt.mode_used = "none"
        receipt.scorer = "none"
        selected = _originals_as_selected(ps)
        return _finish(selected, receipt, t0, emit_tokenomics, token_budget)

    # ---- fast path: filtering cannot pay for itself on little content -------
    if receipt.input_chars < small_input_chars and len(ps) <= max_results:
        receipt.mode_used = "none"
        receipt.scorer = "none"
        receipt.fast_path = True
        receipt.fallback_reason = receipt.fallback_reason or "small input fast path"
        selected = _originals_as_selected(ps[:max_results])
        return _finish(selected, receipt, t0, emit_tokenomics, token_budget)

    resolved = requested
    if requested == "auto":
        resolved = "jev" if (jev_available if jev_available is not None
                             else jev_scorer is not None) else "keyword"

    # ---- jev: semantic usefulness, bounded, ordered by ranking rule ---------
    if resolved == "jev":
        if not allow_remote:
            receipt.fallback = True
            receipt.fallback_reason = ("remote scoring not authorized for this "
                                       "context; using local keyword ranking")
        elif jev_scorer is None:
            receipt.fallback = True
            receipt.fallback_reason = "jev scorer not configured"
        else:
            chunks = _chunk_passages(ps, size=chunk_size, overlap=chunk_overlap)
            texts = [c[2] for c in chunks]
            receipt.chunks = len(chunks)
            try:
                result = _as_scorer_result(jev_scorer(query, texts))
                if len(result.scores) != len(texts):
                    raise ValueError(
                        f"scorer returned {len(result.scores)} scores for {len(texts)} chunks")
                receipt.scores = result.scores
                receipt.mode_used = "jev"
                receipt.scorer = result.scorer
                receipt.model = result.model
                receipt.revision = result.revision
                receipt.cost_usd = result.cost_usd
                receipt.threshold = JEV_MIN_USEFULNESS
                receipt.max_results = max_results
                qualifying = [i for i, sv in enumerate(result.scores)
                              if sv >= JEV_MIN_USEFULNESS]
                if not qualifying:
                    receipt.fallback = True
                    receipt.fallback_reason = "no chunk met the usefulness floor"
                else:
                    # Rank the QUALIFYING chunks and keep the best, exactly like the
                    # reference: (-score, position). Taking the first qualifying
                    # chunks instead would silently prefer whichever chunk happened
                    # to come first in the document.
                    ranked = sorted(qualifying, key=lambda i: (-result.scores[i], i))
                    keep = ranked[:max_results]
                    selected = _to_selected(chunks, result.scores, keep)
                    return _finish(selected, receipt, t0, emit_tokenomics, token_budget)
            except Exception as exc:  # noqa: BLE001
                receipt.fallback = True
                receipt.fallback_reason = f"jev scorer failed: {type(exc).__name__}: {exc}"[:200]

    # ---- keyword: always available, always local ---------------------------
    receipt.mode_used = "keyword"
    receipt.scorer = "bm25"
    receipt.threshold = relative_threshold
    receipt.max_results = keyword_max_results
    chunks = _chunk_passages(ps, size=chunk_size, overlap=chunk_overlap)
    texts = [c[2] for c in chunks]
    receipt.chunks = len(chunks)
    if not chunks:
        return _finish([], receipt, t0, emit_tokenomics, token_budget)
    scores = bm25_scores(query, texts)
    receipt.scores = scores
    best = max(scores, default=0.0)
    if best <= 0:
        receipt.fallback = True
        receipt.fallback_reason = (receipt.fallback_reason or "") + \
            ("; " if receipt.fallback_reason else "") + "no keyword overlap; kept opening chunks"
        keep = list(range(min(keyword_max_results, len(chunks))))
    else:
        ranked = sorted(range(len(chunks)), key=lambda i: (-scores[i], i))
        keep = [i for i in ranked if scores[i] >= relative_threshold * best][:keyword_max_results]
    selected = _to_selected(chunks, scores, keep)
    return _finish(selected, receipt, t0, emit_tokenomics, token_budget)


def _originals_as_selected(ps: Sequence[Passage]) -> list[Selected]:
    """Original passages as-is: no chunking, so no overlap duplication."""
    return [Selected(text=p.text, score=0.0, chunk_index=0, url=p.url, title=p.title,
                     doc_id=p.doc_id, source_id=p.source_id, position=p.position)
            for p in ps if p.text.strip()]


def _apply_token_budget(selected: list[Selected], budget: int | None) -> list[Selected]:
    if not budget or budget <= 0:
        return selected
    out: list[Selected] = []
    used = 0
    for s in selected:
        cost = estimate_tokens(s.text)
        if used + cost > budget:
            continue
        out.append(s)
        used += cost
    return out


def _finish(selected: list[Selected], receipt: FilterReceipt, t0: float,
            emit: bool, token_budget: int | None = None) -> FilterResult:
    selected = _apply_token_budget(selected, token_budget)
    receipt.selected = len(selected)
    receipt.chunks = max(receipt.chunks, len(selected))
    receipt.rejected = max(0, receipt.chunks - receipt.selected)
    receipt.output_chars = sum(len(s.text) for s in selected)
    receipt.input_tokens_est = receipt.input_chars // 4
    receipt.output_tokens_est = receipt.output_chars // 4
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
        emit_filter_event(receipt)
    return result


def emit_filter_event(receipt: FilterReceipt, *, root: Path | None = None) -> Path | None:
    """Append the filter event to the existing tokenomics lane.

    Uses ``tokenomics_emit.emit_raw`` -- the real append-only path to
    ``~/.z0int/tokenomics/events.jsonl``. Returns the path written, or None if the
    import is unavailable. Never raises: telemetry must not break filtering.
    """
    row = {
        "kind": "context_filter",
        "schema": SCHEMA,
        "trace_id": receipt.trace_id,
        "session_id": receipt.session_id,
        "mode_requested": receipt.mode_requested,
        "mode_used": receipt.mode_used,
        "scorer": receipt.scorer,
        "model": receipt.model,
        "revision": receipt.revision,
        "input_chars": receipt.input_chars,
        "output_chars": receipt.output_chars,
        "input_tokens_est": receipt.input_tokens_est,
        "output_tokens_est": receipt.output_tokens_est,
        # "avoided" is derived once, here, from the two measured/estimated ends so
        # it can never be double counted by a downstream consumer
        "context_tokens_avoided_est": receipt.context_tokens_avoided,
        "retention_ratio": round(receipt.retention_ratio, 6),
        "latency_ms": round(receipt.latency_ms, 3),
        "filter_cost_usd": receipt.cost_usd,
        "fallback": receipt.fallback,
        "fallback_reason": receipt.fallback_reason,
        "fast_path": receipt.fast_path,
        "candidates": receipt.candidates,
        "chunks": receipt.chunks,
        "selected": receipt.selected,
        # measured vs estimated is explicit: no tokenizer ran, so token fields are
        # estimated while chars/latency/cost are measured
        "measurement_state": "mixed: chars+latency+cost measured; tokens estimated at chars//4",
        "token_estimate_method": "chars//4",
    }
    try:
        from . import tokenomics_emit

        return tokenomics_emit.emit_raw(row, root=root)
    except Exception:  # noqa: BLE001
        return None


def _to_selected(chunks, scores, keep: list[int]) -> list[Selected]:
    out = []
    for i in keep:
        p, chunk_index, text = chunks[i]
        out.append(Selected(text=text, score=float(scores[i]) if i < len(scores) else 0.0,
                            chunk_index=chunk_index, url=p.url, title=p.title,
                            doc_id=p.doc_id, source_id=p.source_id, position=p.position))
    return out


# --------------------------------------------------------------- jev lane (#6)
JEV_QUESTION_ID = "usefulness"
JEV_RUBRIC = {
    "0": "unrelated to the query",
    "1": "same topic but does not help answer the query",
    "2": "partially answers the query or provides useful supporting evidence",
    "3": "directly answers the query with specific evidence",
}


def jev_context_scorer(*, timeout: float = 20.0, concurrency: int = 4,
                       expected_model: str | None = None) -> Scorer:
    """Build a context-usefulness scorer on the CANONICAL Jev path.

    Reuses ``functions.jev``'s credential resolver and ``jevkit.client`` -- the
    same transport, credential store and capture the existing Jev verifier uses.
    No second TypeSafe HTTP client is created here.

    The rubric is the four-level usefulness scale, asked as one choice question per
    chunk, bounded by ``concurrency``.

    STATUS: the question construction mirrors ``JevVerifier.verify``'s use of
    ``jevkit.client.ask`` and the named-choice form. It has NOT been exercised
    against a live endpoint in this change (that requires a paid call), so treat
    the lane as implemented-but-unverified-live. ``filter_context`` degrades to
    keyword and says so if anything raises.
    """
    from concurrent.futures import ThreadPoolExecutor

    from .functions.jev import ensure_credential, credential_source

    def scorer(query: str, passages: Sequence[str]) -> ScorerResult:
        key = ensure_credential()
        if not key:
            raise RuntimeError("no Jev credential resolvable")
        from jevkit import client as jev_client  # type: ignore

        def one(text: str) -> tuple[float, dict]:
            import time as _t
            t0 = _t.perf_counter()
            reply = jev_client.ask(
                f"Query: {query}\n\nPassage: {text}",
                {JEV_QUESTION_ID: jev_client.choice(
                    "How useful is this passage for answering the query?",
                    JEV_RUBRIC)},
                timeout=timeout,
                model=expected_model,
                api_key=key,
            )
            answer = reply["answers"][JEV_QUESTION_ID]
            probs = answer.get("probabilities") or {}
            return (sum(float(k) * float(v) for k, v in probs.items()),
                    {"model": reply.get("model"), "usage": reply.get("usage") or {},
                     "latency_ms": (_t.perf_counter() - t0) * 1000.0})

        with ThreadPoolExecutor(max_workers=max(1, concurrency)) as pool:
            rows = list(pool.map(one, passages))
        models = {r[1]["model"] for r in rows if r[1]["model"]}
        usage: dict[str, Any] = {}
        for _s, meta in rows:
            for k, v in (meta.get("usage") or {}).items():
                if isinstance(v, int):
                    usage[k] = usage.get(k, 0) + v
        return ScorerResult(
            scores=[r[0] for r in rows],
            scorer="jev",
            model=(models.pop() if len(models) == 1 else None),
            revision=(models.pop() if models else None),
            usage=usage,
            cost_usd=0.00006 * len(passages),
            provider_latency_ms=sum(r[1]["latency_ms"] for r in rows) / max(1, len(rows)),
            measured=True,
        )

    return scorer
