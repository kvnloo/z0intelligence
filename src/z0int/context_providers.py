"""Local evidence providers for :mod:`z0int.context_resolve`.

One registry over the indexes that already exist on this machine. No corpus is
rewritten into a new canonical database, no source is mutated, no new database is
created, and no model is loaded. Every provider opens its store read-only with
``mode=ro`` + ``PRAGMA query_only=1``, is individually bounded, and degrades only
itself: a provider that fails returns its error and the others still answer.

Why this module exists
----------------------
``context_resolve`` used to answer every ``natural_language`` need through the
``qmd`` CLI. qmd is a lexical BM25 index over markdown notes; the actual question
corpus here is conversation and tool output, and the stronger indexes for it were
already built (AgentsView conversational FTS, contentless tool-output FTS,
``coverage.db`` primitives, and the off-root Claude/Cursor indexes). ``qmd``
therefore remains *optional* (``allow_qmd``) but is no longer the canonical
evidence substrate.

Two-hop identifier resolution
-----------------------------
A question almost never shares tokens with the exact identifier it wants::

    "where is the key that the DSH plugin reads stored on disk"
        -> /home/kvn/.hermes/profiles/chiefstaff/.env

Those strings have **zero lexical overlap**, so a direct query for the value can
never find it, and ranking cannot repair that. What *does* overlap is the
conversation around it. So an identifier need is resolved in two hops:

    1. lexically retrieve conversation/tool evidence for the question;
    2. harvest identifier-shaped tokens from that evidence, and look those up in
       the structured fact store, filtered by the need's semantic kind.

The surrounding context is the bridge. This is expressed as an ordinary
``InformationNeed`` path -- there is deliberately no separate mapper object.
"""

from __future__ import annotations

import os
import re
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable, Iterator, Literal

# --------------------------------------------------------------------- roots

AV_DIR = Path(os.environ.get("Z0INT_AV_DIR", "/mnt/zer0models/sft-svlm/data/agentsview"))
SESSIONS_DB = Path(os.environ.get("Z0INT_SESSIONS_DB", AV_DIR / "sessions.db"))
TOOLINDEX_DB = Path(os.environ.get("Z0INT_TOOLINDEX_DB", AV_DIR / "toolindex.db"))
COVERAGE_DB = Path(os.environ.get("Z0INT_COVERAGE_DB", AV_DIR / "coverage.db"))
CLAUDE_EXTRA_DB = Path(os.environ.get("Z0INT_CLAUDE_EXTRA_DB", AV_DIR / "claude-extra.db"))
MISC_EXTRA_DB = Path(os.environ.get("Z0INT_MISC_EXTRA_DB", AV_DIR / "misc-extra.db"))
HERMES_ROOT = Path(os.environ.get("Z0INT_HERMES_ROOT", "/workspace/hermes-home"))
#: Overridable so failure injection can point at a dead endpoint without
#: touching the live daemon.
AGENTSVIEW_URL = os.environ.get("AGENTSVIEW_URL", "http://127.0.0.1:8080")
AGENTSVIEW_TIMEOUT_S = float(os.environ.get("Z0INT_AGENTSVIEW_TIMEOUT_S", "10"))
ANTHROPIC_DB = Path(
    os.environ.get("Z0INT_ANTHROPIC_DB", "/mnt/zer0models/sft-svlm/data/anthropic/anthropic.sqlite3")
)

#: Need kinds that name an exact source file rather than an index lookup.
SOURCE_KINDS = frozenset({"exact_path", "path"})

#: Typed identifier needs. Each maps to the ``coverage.db`` fact ``kind`` values
#: that can legitimately satisfy it. This mapping is the "semantic type" step:
#: the question says *what kind of thing* it wants, not *what the thing is*.
KIND_FACTS: dict[str, tuple[str, ...]] = {
    # A `path` need that names an existing file is served by a direct source
    # read; these entries are what lets a *prose* need that wants a path reach
    # one from the fact store instead.
    "exact_path": ("path",),
    "path": ("path",),
    "exact_symbol": ("identifier", "value"),
    "symbol": ("identifier", "value"),
    "config_value": ("value", "argkey", "identifier"),
    "model": ("value", "identifier"),
    "command": ("command",),
    "url": ("url",),
    "revision": ("sha", "branch"),
    "repository": ("path", "identifier"),
    "issue": ("url", "identifier"),
    "identifier": ("identifier", "value", "path"),
}

#: Fact kinds a ``natural_language`` need may fall back to when it asks for one.
_TYPED_FALLBACK = ("path", "identifier", "value", "url", "command")

TrustClass = Literal[
    "authoritative_task",
    "project_constraint",
    "code",
    "conversation",
    "derived_memory",
    "index_hit",
    "unknown",
]


@dataclass(frozen=True)
class ProviderHit:
    """A normalized hit. ``context_resolve`` maps these into ``EvidenceRef``."""

    provider: str
    locator: str
    source_version: str
    trust_class: TrustClass
    excerpt: str
    session_id: str | None = None
    message_id: str | None = None
    timestamp: str | None = None
    tool_name: str | None = None
    project: str | None = None
    fact_kind: str | None = None
    bridge: str | None = None
    score: float | None = None


@dataclass
class ProviderStatus:
    provider: str
    ok: bool
    hits: int = 0
    error: str | None = None
    wall_ms: float = 0.0


@dataclass
class LexicalResult:
    hits: list[ProviderHit] = field(default_factory=list)
    status: list[ProviderStatus] = field(default_factory=list)


# ------------------------------------------------------------------ plumbing

_TERM_RE = re.compile(r'"[^"]+"|\S+')


def fts_match(query: str, *, mode: Literal["all", "any"] = "all") -> str:
    """Build an FTS5 MATCH expression from individual terms.

    MEASURED DEFECT #1 (ported, with its evidence). Every SQLite adapter in the
    prototype union used to build one whole-query PHRASE, which requires the
    terms to appear adjacent and in order. Across three adapters on 8 multi-term
    probes each that was **108 hits vs 1,357** for the term-conjunction form, and
    on 17 of those 24 probes the phrase form returned literally zero.

    MEASURED DEFECT #2 (found here, same family). The conjunction form is itself
    wrong for *prose* queries. On the 17-miss evaluation set the long questions
    returned **zero lexical hits** -- ``how should agentsview serve be kept alive
    to avoid the idle-timeout shutdown`` requires every one of its ten terms to
    appear in a single message, so misses #1-#3 scored 0 before any ranking
    happened. ``mode="any"`` ORs the terms and lets FTS5's bm25 ``rank`` reward
    documents that match more of them, which is the behaviour a prose query
    actually wants; common terms carry low IDF and cannot dominate.

    ``mode="all"`` remains the default because a *precise* lookup (an exact
    identifier the caller already knows) genuinely wants conjunction.

    Quoting each term individually still protects FTS5 operator characters -- a
    bare ``sft-svlm`` raises ``no such column: svlm``. An already-quoted span is
    preserved, so a caller can still ask for exact adjacency deliberately.
    """
    parts: list[str] = []
    for t in _TERM_RE.findall(query):
        t = t.strip()
        if not t:
            continue
        if len(t) > 2 and t.startswith('"') and t.endswith('"'):
            parts.append(t)
        else:
            parts.append('"' + t.replace('"', '""') + '"')
    if not parts:
        return '""'
    return (" AND " if mode == "all" else " OR ").join(parts)


#: Retrieval mode for prose queries against the lexical providers.
PROSE_MODE: Literal["all", "any"] = "any"


def _ro(path: Path) -> sqlite3.Connection:
    """Read-only connection. Never creates the file: a missing store must fail."""
    if not path.is_file():
        raise FileNotFoundError(f"{path} is not present")
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=20)
    conn.execute("PRAGMA query_only=1")
    return conn


def _clip(s: Any, n: int = 400) -> str:
    if not s:
        return ""
    s = re.sub(r"\s+", " ", str(s)).strip()
    return s[:n] + ("…" if len(s) > n else "")


def _version(path: Path) -> str:
    try:
        st = path.stat()
        return f"mtime={int(st.st_mtime)}:size={st.st_size}"
    except OSError:
        return "missing"


def _timed(fn: Callable[[], list[ProviderHit]], name: str) -> tuple[list[ProviderHit], ProviderStatus]:
    """Run one provider in isolation. Never raises, never returns a silent empty."""
    import time

    t0 = time.perf_counter()
    try:
        hits = fn()
    except Exception as exc:  # noqa: BLE001 - one provider must not sink the packet
        return [], ProviderStatus(
            provider=name, ok=False, error=f"{type(exc).__name__}: {exc}"[:160],
            wall_ms=(time.perf_counter() - t0) * 1000,
        )
    return hits, ProviderStatus(
        provider=name, ok=True, hits=len(hits), wall_ms=(time.perf_counter() - t0) * 1000
    )


# --------------------------------------------------------- lexical providers

def _hits_from_messages(
    *,
    db: Path,
    provider: str,
    query: str,
    limit: int,
    trust: TrustClass,
    sql: str,
    rowmap: Callable[[tuple], dict[str, Any]],
) -> list[ProviderHit]:
    conn = _ro(db)
    try:
        rows = conn.execute(sql, (fts_match(query, mode=PROSE_MODE), limit)).fetchall()
    finally:
        conn.close()
    ver = _version(db)
    out: list[ProviderHit] = []
    for r in rows:
        m = rowmap(r)
        out.append(
            ProviderHit(
                provider=provider,
                locator=str(m.get("locator") or provider),
                source_version=ver,
                trust_class=trust,
                excerpt=_clip(m.get("excerpt")),
                session_id=m.get("session_id"),
                message_id=m.get("message_id"),
                timestamp=m.get("ts"),
                tool_name=m.get("tool_name"),
                project=m.get("project"),
            )
        )
    return out


# AgentsView conversational FTS. `messages` holds ONLY assistant+user rows --
# 348,934 of them and zero `tool` rows -- so this is the *conversation* provider
# and never the tool provider.
_CONV_SQL = """
select m.id, m.session_id, m.timestamp, m.role, s.project, s.agent,
       snippet(messages_fts, 0, '', '', ' … ', 24)
  from messages_fts f
  join messages m on m.id = f.rowid
  left join sessions s on s.id = m.session_id
 where messages_fts match ?
 order by rank
 limit ?
"""


def lexical_conversation(query: str, limit: int = 8) -> list[ProviderHit]:
    """AgentsView conversational evidence — HTTP first, direct SQLite as fallback.

    MEASURED REASON FOR HTTP-FIRST. The AgentsView server searches its index
    *hybrid* (FTS5 + vectors) across more than the `messages` table. On miss #7
    ("where is the key that the DSH plugin reads stored on disk") the HTTP search
    returns the exact answer -- ``The key exists -- it's in
    ~/.hermes/profiles/chiefstaff/.env`` -- while a direct ``messages_fts``
    bm25 query over the same question does not rank that row into the top 8.
    The two are therefore not interchangeable, and the stronger one must lead.

    Direct SQLite is kept as the fallback so that a stopped daemon degrades this
    provider explicitly instead of returning a silent empty.
    """
    try:
        return _conversation_http(query, limit)
    except Exception as exc:  # noqa: BLE001 - fall back, but never silently
        fallback = _hits_from_messages(
            db=SESSIONS_DB, provider="conversation", query=query, limit=limit,
            trust="conversation", sql=_CONV_SQL,
            rowmap=lambda r: {
                "locator": f"agentsview:{r[1]}#{r[0]}",
                "session_id": r[1], "message_id": str(r[0]), "ts": r[2],
                "excerpt": f"[{r[3]}] {r[6]}", "project": r[4],
                "tool_name": None,
            },
        )
        for h in fallback:
            object.__setattr__(h, "bridge", f"http-unavailable:{type(exc).__name__}")
        return fallback


def _av_token() -> str | None:
    """Read the daemon token from the 0600 config. Never logged, never on argv."""
    if os.environ.get("AGENTSVIEW_AUTH_TOKEN"):
        return os.environ["AGENTSVIEW_AUTH_TOKEN"]
    try:
        for line in (AV_DIR / "config.toml").read_text().splitlines():
            if line.strip().startswith("auth_token"):
                return line.split("=", 1)[1].strip().strip('"').strip()
    except OSError:
        return None
    return None


def _conversation_http(query: str, limit: int) -> list[ProviderHit]:
    import json as _json
    import urllib.parse
    import urllib.request

    url = f"{AGENTSVIEW_URL}/api/v1/search?" + urllib.parse.urlencode({"q": query, "limit": limit})
    req = urllib.request.Request(url)
    token = _av_token()
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    with urllib.request.urlopen(req, timeout=AGENTSVIEW_TIMEOUT_S) as resp:
        data = _json.loads(resp.read().decode("utf-8"))
    rows = data.get("results") if isinstance(data, dict) else None
    if not isinstance(rows, list):
        raise ValueError("unexpected AgentsView search payload")
    ver = f"agentsview:{_version(AV_DIR / 'sessions.db')}"
    out: list[ProviderHit] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        sid = row.get("session_id") or row.get("sessionId")
        mid = row.get("message_id") or row.get("messageId") or row.get("id")
        ordinal = row.get("ordinal")
        # The daemon identifies a hit by (session_id, ordinal) and does not return
        # a message id. `inspect` cannot open `...#None`, so the ordinal form is
        # emitted explicitly and `read_locator` resolves it.
        if mid is None and ordinal is not None:
            locator = f"agentsview:{sid}@{ordinal}"
        else:
            locator = f"agentsview:{sid}#{mid}"
        out.append(
            ProviderHit(
                provider="conversation",
                locator=locator,
                source_version=ver,
                trust_class="conversation",
                excerpt=_clip(_strip_marks(row.get("snippet") or row.get("content"))),
                session_id=str(sid) if sid is not None else None,
                message_id=str(mid) if mid is not None else None,
                timestamp=row.get("timestamp") or row.get("session_ended_at"),
                project=row.get("project"),
                tool_name=row.get("agent"),
                bridge="agentsview.http.hybrid",
            )
        )
    return out


_MARK_RE = re.compile(r"</?mark>")


def _strip_marks(s: Any) -> str:
    return _MARK_RE.sub("", str(s)) if s else ""


# Hermes profile stores, independent of AgentsView. Schema varies by profile:
# the root db is a plain `fts5(content)` table while each profile indexes
# `content, tool_name, tool_calls` as external content over `messages_fts_src`,
# whose ids line up with `messages.rowid`. Per-database errors are collected
# rather than aborting, so one unreadable profile cannot hide the others.
_HERMES_SQL = """
select m.id, m.session_id, m.role, m.timestamp,
       substr(coalesce(m.content, ''), 1, 400), s.cwd, s.source, s.title
  from messages_fts f
  join messages m on m.rowid = f.rowid
  left join sessions s on s.id = m.session_id
 where messages_fts match ?
 order by rank
 limit ?
"""


def lexical_hermes(query: str, limit: int = 8) -> list[ProviderHit]:
    if not HERMES_ROOT.is_dir():
        raise FileNotFoundError(f"{HERMES_ROOT} is not present")
    dbs = sorted(HERMES_ROOT.glob("profiles/*/state.db")) + [HERMES_ROOT / "state.db"]
    match = fts_match(query, mode=PROSE_MODE)
    out: list[ProviderHit] = []
    errors: list[str] = []
    for db in dbs:
        try:
            if not db.is_file() or db.stat().st_size < 100_000:
                continue
            conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=20)
            conn.execute("PRAGMA query_only=1")
            try:
                rows = conn.execute(_HERMES_SQL, (match, limit)).fetchall()
            finally:
                conn.close()
        except sqlite3.Error as exc:
            errors.append(f"{db.parent.name}:{str(exc)[:40]}")
            continue
        profile = db.parent.name if db.parent.name != "hermes-home" else "root"
        ver = _version(db)
        for mid, sid, role, ts, content, cwd, src, title in rows:
            out.append(
                ProviderHit(
                    provider="hermes",
                    locator=f"hermes/{profile}:{sid}#{mid}",
                    source_version=ver,
                    trust_class="conversation",
                    excerpt=f"[{role}] {_clip(content)}",
                    session_id=sid,
                    message_id=str(mid),
                    timestamp=ts,
                    project=cwd or title,
                    tool_name=src,
                )
            )
    if errors and not out:
        raise RuntimeError("; ".join(errors[:3]))
    return out


_ANTHROPIC_SQL = """
select msg.id, msg.conversation_id, msg.role, msg.created_at,
       substr(coalesce(msg.text, ''), 1, 400), c.title
  from message_fts f
  join message msg on msg.rowid = f.rowid
  left join conversation c on c.id = msg.conversation_id
 where message_fts match ?
 order by msg.created_at desc
 limit ?
"""


def lexical_anthropic(query: str, limit: int = 8) -> list[ProviderHit]:
    """Claude hosted-app export. Distinct from Claude Code harness history."""
    conn = _ro(ANTHROPIC_DB)
    try:
        rows = conn.execute(_ANTHROPIC_SQL, (fts_match(query, mode=PROSE_MODE), limit)).fetchall()
    finally:
        conn.close()
    ver = _version(ANTHROPIC_DB)
    return [
        ProviderHit(
            provider="anthropic",
            locator=f"anthropic:{r[1]}#{r[0]}",
            source_version=ver,
            trust_class="conversation",
            excerpt=f"[{r[2]}] {_clip(r[4])}",
            session_id=r[1],
            message_id=str(r[0]),
            timestamp=r[3],
            project=r[5],
            tool_name="claude-hosted-app",
        )
        for r in rows
    ]


# Contentless tool-output FTS: the index stores no text, so a rowid is mapped back
# through `tool_doc` and the snippet is re-read from the source. 17.77 Gchars are
# covered by a ~660 MB index as a result, and 6 of 10 contiguous phrases taken
# from real tool output are found ONLY here.
_TOOL_SQL = """
select d.kind, d.src_table, d.src_id, d.session_id, d.tool_name, d.ts,
       substr(coalesce(tc.result_content, tre.content, m.thinking_text), 1, 400)
  from tool_fts f
  join tool_doc d on d.rowid = f.rowid
  left join sessions_db.tool_calls tc
         on d.src_table = 'tool_calls' and tc.id = d.src_id
  left join sessions_db.tool_result_events tre
         on d.src_table = 'tool_result_events' and tre.id = d.src_id
  left join sessions_db.messages m
         on d.src_table = 'messages' and m.id = d.src_id
 where tool_fts match ?
 order by rank
 limit ?
"""


def lexical_tool(query: str, limit: int = 8) -> list[ProviderHit]:
    if not TOOLINDEX_DB.is_file():
        raise FileNotFoundError(f"{TOOLINDEX_DB} is not present")
    if not SESSIONS_DB.is_file():
        raise FileNotFoundError(f"{SESSIONS_DB} is not present")
    conn = sqlite3.connect(f"file:{TOOLINDEX_DB}?mode=ro", uri=True, timeout=20)
    conn.execute("PRAGMA query_only=1")
    try:
        conn.execute("ATTACH DATABASE ? AS sessions_db", (f"file:{SESSIONS_DB}?mode=ro",))
        rows = conn.execute(_TOOL_SQL, (fts_match(query, mode=PROSE_MODE), limit)).fetchall()
    finally:
        conn.close()
    ver = _version(TOOLINDEX_DB)
    return [
        ProviderHit(
            provider="tool",
            locator=f"{r[1]}#{r[2]}",
            source_version=ver,
            trust_class="index_hit",
            excerpt=_clip(r[6]),
            session_id=r[3],
            message_id=f"{r[1]}#{r[2]}",
            timestamp=r[5],
            tool_name=r[4],
            fact_kind=r[0],
        )
        for r in rows
    ]


# Off-root message stores. Both share one shape (external-content FTS over a
# `messages` table keyed by rowid) and differ only in metadata columns.
_EXTRA_SPECS: dict[str, tuple[Path, str, str]] = {
    "claude-extra": (
        CLAUDE_EXTRA_DB,
        """
        select m.id, m.session_id, m.timestamp, m.role, m.project, m.cwd,
               snippet(messages_fts, 0, '', '', ' … ', 24)
          from messages_fts f join messages m on m.id = f.rowid
         where messages_fts match ? order by rank limit ?
        """,
        "claude",
    ),
    "misc-extra": (
        MISC_EXTRA_DB,
        """
        select m.id, m.session_id, m.timestamp, m.role, m.project, m.agent,
               snippet(messages_fts, 0, '', '', ' … ', 24)
          from messages_fts f join messages m on m.id = f.rowid
         where messages_fts match ? order by rank limit ?
        """,
        "misc",
    ),
}


def _extra_provider(name: str, query: str, limit: int) -> list[ProviderHit]:
    db, sql, _ = _EXTRA_SPECS[name]
    return _hits_from_messages(
        db=db, provider=name, query=query, limit=limit,
        trust="conversation", sql=sql,
        rowmap=lambda r: {
            "locator": f"{name}:{r[1]}#{r[0]}",
            "session_id": r[1], "message_id": str(r[0]), "ts": r[2],
            "excerpt": f"[{r[3]}] {r[6]}", "project": r[4],
        },
    )


def lexical_claude_extra(query: str, limit: int = 8) -> list[ProviderHit]:
    return _extra_provider("claude-extra", query, limit)


def lexical_misc_extra(query: str, limit: int = 8) -> list[ProviderHit]:
    return _extra_provider("misc-extra", query, limit)


# ------------------------------------------------------ identifier providers

# Identifier-shaped tokens. Deliberately conservative: a token that never appears
# in the fact store costs one FTS term, but a token that appears everywhere
# dilutes the ranking.
_TOKEN_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"~/[\w.\-+@/]+"),                                 # tilde path
    re.compile(r"(?:/[\w.\-+@]+){2,}(?:\.[A-Za-z0-9]{1,8})?"),   # absolute path
    re.compile(r"\b[\w.\-]+\.(?:env|json|ts|tsx|js|py|sh|ya?ml|toml|db|md|cfg|ini)\b"),
    re.compile(r"\b[A-Z][A-Z0-9]{2,}_[A-Z0-9_]{2,}\b"),           # ENV_VAR
    re.compile(r"\b\d{1,3}(?:\.\d{1,3}){3}:\d{2,5}\b"),           # host:port
    re.compile(r"\b[a-z]+-\d+(?:\.\d+)+[a-z0-9.\-]*\b"),          # model id
    re.compile(r"\b(?:r|p|q|t)\d{2}\b"),                          # round id
    re.compile(r"\b[a-zA-Z_][\w]*(?:[.\-/][\w]+){1,}\b"),         # dotted/pathed name
)
_TOKEN_STOP = frozenset(
    {
        "e.g", "i.e", "etc.", "vs.", "a.m", "p.m", "u.s", "node.js", "next.js",
        "0.0", "1.0", "2.0", "3.0", "4.0", "5.0",
    }
)


def fact_tokens(text: str, *, limit: int = 24) -> list[str]:
    """Harvest identifier-shaped tokens from surrounding evidence.

    Ordered by specificity (earlier patterns are stronger identifiers) and
    de-duplicated case-insensitively, so the bridge leads with paths and env-var
    names rather than with dotted prose fragments.
    """
    seen: set[str] = set()
    out: list[str] = []
    for pat in _TOKEN_PATTERNS:
        for m in pat.finditer(text or ""):
            tok = m.group(0).strip().strip(".,;:)]}\"'`")
            low = tok.lower()
            if low in _TOKEN_STOP or len(tok) < 2 or low in seen:
                continue
            seen.add(low)
            out.append(tok)
            if len(out) >= limit:
                return out
    return out


def _fact_hits(
    rows: Iterable[tuple],
    *,
    db: Path,
    bridge: str,
    score_base: float = 0.0,
) -> list[ProviderHit]:
    ver = _version(db)
    out: list[ProviderHit] = []
    for i, r in enumerate(rows):
        session_id, kind, key, value, tool_name, ts, cwd, harness = r
        out.append(
            ProviderHit(
                provider="facts",
                locator=f"coverage:{session_id or '?'}#{kind}:{value[:120]}",
                source_version=ver,
                trust_class="derived_memory",
                excerpt=_clip(value, 360),
                session_id=session_id,
                timestamp=ts,
                tool_name=tool_name,
                project=cwd,
                fact_kind=kind,
                bridge=bridge,
                score=score_base - i * 0.001,
            )
        )
    return out


# qualified: facts_fts also exposes value/key/tool_name, so the join would
# otherwise make those column references ambiguous.
_FACT_COLS = (
    "f.session_id, f.kind, f.key, f.value, f.tool_name, f.ts, f.cwd, f.harness"
)


def facts_for_tokens(tokens: list[str], *, kinds: tuple[str, ...], limit: int = 12) -> list[ProviderHit]:
    """Hop 2: look the harvested tokens up in the structured fact store.

    OR-matched so a single bad token does not zero the result; FTS5's ``rank``
    already rewards rows matching more of them.
    """
    if not tokens or not kinds:
        return []
    conn = _ro(COVERAGE_DB)
    try:
        expr = " OR ".join('"' + t.replace('"', '""') + '"' for t in tokens)
        marks = ",".join("?" for _ in kinds)
        rows = conn.execute(
            f"""select {_FACT_COLS} from facts_fts ff join facts f on f.id = ff.rowid
                 where facts_fts match ? and f.kind in ({marks})
                 order by rank limit ?""",
            (expr, *kinds, limit),
        ).fetchall()
    finally:
        conn.close()
    return _fact_hits(rows, db=COVERAGE_DB, bridge=f"tokens:{','.join(tokens[:4])}")


def facts_lexical(query: str, *, kinds: tuple[str, ...], limit: int = 8) -> list[ProviderHit]:
    """Direct fact lookup, for the case where the question *does* quote the value."""
    if not kinds:
        return []
    conn = _ro(COVERAGE_DB)
    try:
        marks = ",".join("?" for _ in kinds)
        rows = conn.execute(
            f"""select {_FACT_COLS} from facts_fts ff join facts f on f.id = ff.rowid
                 where facts_fts match ? and f.kind in ({marks})
                 order by rank limit ?""",
            (fts_match(query), *kinds, limit),
        ).fetchall()
    finally:
        conn.close()
    return _fact_hits(rows, db=COVERAGE_DB, bridge="direct")


def facts_for_sessions(session_ids: list[str], *, kinds: tuple[str, ...], limit: int = 8) -> list[ProviderHit]:
    """Same-session lookup, kept as an explicit weaker hop for callers that have
    already pinned a session. Measured NOT to be sufficient on its own: the
    session that lexically matches a question routinely differs from the session
    that holds the identifier, and its fact rows are dominated by noise such as
    ``/dev/null``."""
    if not session_ids or not kinds:
        return []
    conn = _ro(COVERAGE_DB)
    try:
        smarks = ",".join("?" for _ in session_ids)
        kmarks = ",".join("?" for _ in kinds)
        rows = conn.execute(
            f"""select {_FACT_COLS} from facts f
                 where f.session_id in ({smarks}) and f.kind in ({kmarks})
                 order by f.n_chars desc limit ?""",
            (*session_ids, *kinds, limit),
        ).fetchall()
    finally:
        conn.close()
    return _fact_hits(rows, db=COVERAGE_DB, bridge="same-session")


# ------------------------------------------------------------------- registry

#: Lexical providers in descent order of corpus value. Every provider has the
#: uniform signature ``(query, limit) -> list[ProviderHit]``.
LEXICAL_PROVIDERS: tuple[tuple[str, Callable[[str, int], list[ProviderHit]]], ...] = (
    ("conversation", lexical_conversation),
    ("hermes", lexical_hermes),
    ("anthropic", lexical_anthropic),
    ("tool", lexical_tool),
    ("claude-extra", lexical_claude_extra),
    ("misc-extra", lexical_misc_extra),
)


def search_lexical(
    query: str, *, limit: int = 8, only: Iterable[str] | None = None
) -> LexicalResult:
    """Fan out over the lexical providers. Order is stable; failures are reported."""
    want = set(only) if only is not None else None
    res = LexicalResult()
    for name, fn in LEXICAL_PROVIDERS:
        if want is not None and name not in want:
            continue
        hits, status = _timed(lambda fn=fn: fn(query, limit), name)
        res.hits.extend(hits)
        res.status.append(status)
    return res


#: Provider stages ordered by MEASURED warm latency and yield on this machine.
#: The point of the ordering is that the fastest provider is also the one that
#: holds the answer for the identifier failure this fast path exists to fix:
#:
#:   conversation (AgentsView HTTP)   69 ms   <- held the #7 answer at rank 1
#:   misc-extra                        8 ms
#:   anthropic                        28 ms
#:   claude-extra                     92 ms
#:   hermes                          291 ms
#:   tool                            358 ms
#:
#: A full fan-out pays ~1.4 s because `tool` and `hermes` are slow and, for
#: identifier questions, empty. Staging lets the caller stop once its slots are
#: filled instead of buying the whole union.
PROVIDER_STAGES: tuple[tuple[str, ...], ...] = (
    ("conversation", "misc-extra", "anthropic"),
    ("claude-extra", "hermes"),
    ("tool",),
)


def search_staged(
    query: str,
    *,
    limit: int = 8,
    stages: tuple[tuple[str, ...], ...] = PROVIDER_STAGES,
) -> Iterator[tuple[int, LexicalResult]]:
    """Yield cumulative results after each stage, so a caller can stop early.

    This is the "race to fill the slots" primitive. Each stage is strictly
    additive: the caller sees every hit found so far and decides whether it has
    enough. Nothing is thrown away by stopping, because the caller only stops
    when its own completeness test passes.
    """
    cumulative = LexicalResult()
    index: dict[str, Callable[[str, int], list[ProviderHit]]] = dict(LEXICAL_PROVIDERS)
    for stage_no, names in enumerate(stages):
        for name in names:
            fn = index.get(name)
            if fn is None:
                continue
            hits, status = _timed(lambda fn=fn: fn(query, limit), name)
            cumulative.hits.extend(hits)
            cumulative.status.append(status)
        yield stage_no, cumulative


def kinds_for(need_kind: str) -> tuple[str, ...]:
    """Map an ``InformationNeed.kind`` onto ``coverage.db`` fact kinds."""
    if need_kind in KIND_FACTS:
        return KIND_FACTS[need_kind]
    if need_kind == "natural_language":
        return _TYPED_FALLBACK
    return ()


def resolve_identifier_need(
    description: str,
    *,
    need_kind: str = "natural_language",
    limit: int = 12,
    lexical_limit: int = 6,
    lex: LexicalResult | None = None,
) -> tuple[list[ProviderHit], LexicalResult]:
    """Two-hop resolution of a need whose answer is an exact identifier.

    Returns the fact hits plus the lexical result, so the caller can record *how*
    the identifier was reached (the bridge) rather than presenting it as a direct
    lexical match.

    ``lex`` lets a caller that has already fanned out over the lexical providers
    hand its result in. Without it the fan-out is repeated, which measured as
    roughly half the total latency of a resolve.
    """
    kinds = kinds_for(need_kind)
    if lex is None:
        lex = search_lexical(description, limit=lexical_limit)
    # Harvest tokens from *long* snippets, and harvest them FIRST. The
    # AgentsView HTTP snippets are short marked fragments; harvesting from them
    # ahead of the direct-SQLite text measurably zeroed the fact hop, because
    # their tokens filled the token budget before the richer snippets -- which
    # carry the paths, filenames and round ids -- were reached. Order here is
    # therefore load-bearing, not cosmetic.
    harvest: list[ProviderHit] = []
    try:
        harvest += _hits_from_messages(
            db=SESSIONS_DB, provider="conversation-harvest", query=description,
            limit=lexical_limit, trust="conversation", sql=_CONV_SQL,
            rowmap=lambda r: {"excerpt": f"[{r[3]}] {r[6]}", "session_id": r[1]},
        )
    except Exception:  # noqa: BLE001 - harvest is best-effort; ranking already has hits
        pass
    harvest += lex.hits
    tokens: list[str] = []
    for h in harvest:
        tokens.extend(fact_tokens(h.excerpt))
    # De-duplicate while keeping specificity order.
    seen: set[str] = set()
    ordered: list[str] = []
    for t in tokens:
        low = t.lower()
        if low not in seen:
            seen.add(low)
            ordered.append(t)
    ordered = ordered[:32]

    # The fact store is a provider like any other and must degrade like one. It
    # is reached *after* the lexical fan-out, so an unguarded failure here would
    # discard evidence that had already been successfully retrieved.
    def _guarded(fn: Callable[[], list[ProviderHit]], name: str) -> list[ProviderHit]:
        hits, status = _timed(fn, name)
        lex.status.append(status)
        return hits

    hits = _guarded(
        lambda: facts_for_tokens(ordered, kinds=kinds, limit=limit), "facts"
    )
    direct = _guarded(
        lambda: facts_lexical(description, kinds=kinds, limit=max(2, limit // 3)),
        "facts.direct",
    )
    if direct:
        hits = direct + hits
    if lex.hits:
        sids = [h.session_id for h in lex.hits if h.session_id][:6]
        hits.extend(
            _guarded(
                lambda: facts_for_sessions(sids, kinds=kinds, limit=max(2, limit // 4)),
                "facts.same-session",
            )
        )
    return hits, lex


# --------------------------------------------------------------------------- #
# Reading an index locator back to its source
# --------------------------------------------------------------------------- #
# The provider layer is where the locator grammar and the stores that define it
# live, so it is where "open this locator" belongs. `mcp_server.inspect` used to
# answer this with "re-read through `resolve`", which sent the caller back to
# search instead of letting it read the passage it had already found.
#
# Locator grammar produced by this module:
#   agentsview:<session_id>#<message_id>      (row id)
#   agentsview:<session_id>@<ordinal>          (what the daemon returns)
#   claude-extra:<session_id>#<message_id>      misc-extra:<session_id>#<message_id>
#   hermes/<profile>:<session_id>#<message_id>  anthropic:<conversation_id>#<message_id>
#   coverage:<session_id>#<kind>:<value>
#   tool_calls#<id>   tool_result_events#<id>   messages#<id>      (AgentsView)
#   /abs/path  file:/abs/path                                        (filesystem)

#: locator prefix -> (db, table, id_col, session_col, role_col, ts_col, text_col, order_col)
_MESSAGE_SOURCES: dict[str, tuple[Path, str, str, str, str, str, str, str]] = {
    "agentsview": (SESSIONS_DB, "messages", "id", "session_id", "role", "timestamp", "content", "ordinal"),
    "claude-extra": (CLAUDE_EXTRA_DB, "messages", "id", "session_id", "role", "timestamp", "content", "ordinal"),
    "misc-extra": (MISC_EXTRA_DB, "messages", "id", "session_id", "role", "timestamp", "content", "ordinal"),
    "anthropic": (ANTHROPIC_DB, "message", "id", "conversation_id", "role", "created_at", "text", "ordinal"),
}

#: Same, for the tables the `tool` provider names directly.
_TOOL_TABLES: dict[str, tuple[str, str]] = {
    "tool_calls": ("result_content", "input_json"),
    "tool_result_events": ("content", "source"),
    "messages": ("content", "thinking_text"),
}

_MAX_CONTEXT_CHARS = 2000

#: Conservative credential shapes. Applied to everything this module returns:
#: a bounded read of a real transcript can otherwise hand a live key to a model.
_SECRET_RES = (
    re.compile(r"\b(?:sk|pk|rk)-[A-Za-z0-9_\-]{16,}"),
    re.compile(r"\bAIza[0-9A-Za-z_\-]{20,}"),
    re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b"),
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}"),
    re.compile(r"\bxox[baprs]-[A-Za-z0-9\-]{10,}"),
    re.compile(r"(?i)\b(api[_-]?key|access[_-]?token|refresh[_-]?token|client[_-]?secret|password|passwd)\b\s*[:=]\s*[\"']?([^\s\"',;]{8,})"),
    re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._\-]{16,}"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----", re.S),
)


def _redact_secrets(
    text: str, *, session_id: str | None = None, ordinal: Any = None, locator_kind: str | None = None
) -> tuple[str, int]:
    """Blank known secret spans, then obvious credential shapes. Returns (text, n).

    `secret_findings` (AgentsView) already knows the *exact* offsets of definite
    findings, so those are used when they line up; the patterns are a backstop
    for stores that carry no such table.
    """
    n = 0
    if text and session_id and ordinal is not None and SESSIONS_DB.is_file():
        try:
            conn = _ro(SESSIONS_DB)
            try:
                rows = conn.execute(
                    "select match_start, match_end, location_kind from secret_findings "
                    "where session_id=?",
                    (session_id,),
                ).fetchall()
            finally:
                conn.close()
            spans = [
                (int(a), int(b))
                for a, b, _kind in rows
                if a is not None and b is not None and 0 <= int(a) < int(b) <= len(text)
            ]
            for a, b in sorted(spans, reverse=True):
                text = text[:a] + "[redacted:secret]" + text[b:]
                n += 1
        except sqlite3.Error:
            pass
    for rx in _SECRET_RES:
        text, k = rx.subn("[redacted:credential]", text)
        n += k
    return text, n


def _message_source(locator: str):
    """Resolve a locator to (spec, session_id, key, by_ordinal), or None.

    `#<id>` addresses the row id; `@<ordinal>` addresses the (session, ordinal)
    pair, which is what the AgentsView daemon actually returns. Both appear.
    """
    if locator.startswith("hermes/"):
        head, _, tail = locator.partition(":")
        profile = head.split("/", 1)[1]
        db = (HERMES_ROOT / "state.db") if profile == "root" else (HERMES_ROOT / "profiles" / profile / "state.db")
        spec = (db, "messages", "id", "session_id", "role", "timestamp", "content", "rowid")
    else:
        head, _, tail = locator.partition(":")
        if head not in _MESSAGE_SOURCES:
            return None
        spec = _MESSAGE_SOURCES[head]
    if "@" in tail:
        sid, _, key = tail.partition("@")
        return spec, sid, key, True
    sid, _, key = tail.partition("#")
    return spec, sid, key, False


def read_locator(locator: str, *, chars: int = 400, siblings: int = 2) -> dict[str, Any] | None:
    """Open one locator and return bounded original text with its surroundings.

    Returns ``None`` when the locator is not one of this system's -- the caller
    must then report an error rather than guess. The returned dict carries the
    target row, the neighbouring rows with their roles and timestamps, and a
    stable citation string, so a reader can see the passage in context instead of
    receiving an extracted identifier.
    """
    chars = max(80, min(int(chars or 400), 4000))
    siblings = max(0, min(int(siblings if siblings is not None else 2), 20))
    parts = _message_source(locator)
    if parts is not None:
        spec, sid, key, by_ordinal = parts
        db, table, id_col, sess_col, role_col, ts_col, text_col, order_col = spec
        if not db.is_file():
            return {"ok": False, "error": f"source store missing for locator {locator!r}: {db}"}
        conn = _ro(db)
        try:
            if by_ordinal:
                if key in ("", "None"):
                    return {"ok": False, "error": f"locator {locator!r} carries no ordinal"}
                row = conn.execute(
                    f"select {id_col},{sess_col},{role_col},{ts_col},{order_col},"
                    f"substr({text_col},1,8000) from {table} where {sess_col}=? and {order_col}=?",
                    (sid, key),
                ).fetchone()
            else:
                if key in ("", "None"):
                    return {"ok": False, "error": (
                        f"locator {locator!r} carries no row id; it cannot be opened. "
                        "Re-run resolve/history so the locator is emitted in @ordinal form.")}
                row = conn.execute(
                    f"select {id_col},{sess_col},{role_col},{ts_col},{order_col},"
                    f"substr({text_col},1,8000) from {table} where {id_col}=?",
                    (key,),
                ).fetchone()
            if row is None:
                return {"ok": False, "error": f"locator {locator!r} resolves to no row"}
            rid, rsid, role, ts, order, body = row
            around = []
            if order is not None:
                lo, hi = int(order) - siblings, int(order) + siblings
                around = conn.execute(
                    f"select {id_col},{role_col},{ts_col},{order_col},substr({text_col},1,{_MAX_CONTEXT_CHARS}) "
                    f"from {table} where {sess_col}=? and {order_col} between ? and ? order by {order_col}",
                    (rsid, lo, hi),
                ).fetchall()
        finally:
            conn.close()
        target_text, redactions = _redact_secrets(
            str(body or ""), session_id=str(rsid), ordinal=order, locator_kind="message")
        context = []
        for cid, crole, cts, corder, ctext in around:
            ctext, k = _redact_secrets(str(ctext or ""), session_id=str(rsid), ordinal=corder,
                                        locator_kind="message")
            redactions += k
            context.append(
                {
                    "locator": f"{locator.split(':', 1)[0]}:{rsid}#{cid}" if ":" in locator else f"{rsid}#{cid}",
                    "role": crole,
                    "timestamp": cts,
                    "ordinal": corder,
                    "text": ctext[:chars],
                    "is_target": corder == order,
                }
            )
        return {
            "ok": True,
            "kind": "message",
            "locator": locator,
            "citation": f"{db.name}:{table}#{rid} (session {rsid}, ordinal {order}, {ts or 'no timestamp'})",
            "source_version": _version(db),
            "role": role,
            "timestamp": ts,
            "session_id": rsid,
            "message_id": str(rid),
            "excerpt": target_text[:chars],
            "context": context,
            "redactions": redactions,
        }

    if locator.startswith("coverage:"):
        if not COVERAGE_DB.is_file():
            return {"ok": False, "error": f"source store missing for locator {locator!r}"}
        body = locator.split("#", 1)[1] if "#" in locator else ""
        kind, _, value = body.partition(":")
        conn = _ro(COVERAGE_DB)
        try:
            row = conn.execute(
                "select id,session_id,kind,key,value,tool_name,ts,harness,cwd,src_table,src_id "
                "from facts where kind=? and value=? order by id limit 1",
                (kind, value),
            ).fetchone()
        finally:
            conn.close()
        if row is None:
            return {"ok": False, "error": f"locator {locator!r} resolves to no fact row"}
        text, redactions = _redact_secrets(str(row[4] or ""))
        return {
            "ok": True,
            "kind": "fact",
            "locator": locator,
            "citation": f"{COVERAGE_DB.name}:facts#{row[0]} ({row[7] or 'harness?'} {row[8] or ''}, {row[6] or 'no timestamp'})",
            "source_version": _version(COVERAGE_DB),
            "fact_kind": row[2],
            "key": row[3],
            "excerpt": _clip(text, chars),
            "session_id": row[1],
            "tool_name": row[5],
            "timestamp": row[6],
            "source_pointer": f"{row[9]}#{row[10]}" if row[9] else None,
            "context": [],
            "redactions": redactions,
        }

    if "#" in locator and locator.split("#", 1)[0] in _TOOL_TABLES:
        table, mid = locator.split("#", 1)
        if not SESSIONS_DB.is_file():
            return {"ok": False, "error": f"source store missing for locator {locator!r}"}
        text_col, aux_col = _TOOL_TABLES[table]
        conn = _ro(SESSIONS_DB)
        try:
            if table == "tool_calls":
                row = conn.execute(
                    "select id,session_id,message_id,tool_name,file_path,substr(input_json,1,4000),"
                    "substr(result_content,1,8000) from tool_calls where id=?",
                    (mid,),
                ).fetchone()
            elif table == "tool_result_events":
                row = conn.execute(
                    "select id,session_id,tool_call_message_ordinal,agent_id,source,substr(content,1,8000) "
                    "from tool_result_events where id=?",
                    (mid,),
                ).fetchone()
            else:
                row = conn.execute(
                    "select id,session_id,ordinal,role,timestamp,substr(coalesce(content,''),1,8000) "
                    "from messages where id=?",
                    (mid,),
                ).fetchone()
            if row is None:
                return {"ok": False, "error": f"locator {locator!r} resolves to no row"}
            if table == "tool_calls":
                rid, rsid, mmsg, tool, fpath, raw_input, result = row
                ordinal = conn.execute(
                    "select ordinal from messages where id=?", (mmsg,)
                ).fetchone()
                ordinal = ordinal[0] if ordinal else None
                around = []
                if ordinal is not None:
                    around = conn.execute(
                        "select id,role,timestamp,ordinal,substr(coalesce(content,''),1,{}) from messages "
                        "where session_id=? and ordinal between ? and ? order by ordinal".format(_MAX_CONTEXT_CHARS),
                        (rsid, ordinal - 2, ordinal + 2),
                    ).fetchall()
                body = result or raw_input or ""
                extra = {"tool_name": tool, "file_path": fpath, "input": _clip(raw_input, 1200)}
            elif table == "tool_result_events":
                rid, rsid, mmsg, agent, src, content = row
                ordinal, around = mmsg, []
                body = content or ""
                extra = {"agent_id": agent, "source": src}
            else:
                rid, rsid, ordinal, role, ts, body = row
                around = conn.execute(
                    "select id,role,timestamp,ordinal,substr(coalesce(content,''),1,{}) from messages "
                    "where session_id=? and ordinal between ? and ? order by ordinal".format(_MAX_CONTEXT_CHARS),
                    (rsid, (ordinal or 0) - 2, (ordinal or 0) + 2),
                ).fetchall()
                extra = {"role": role, "timestamp": ts}
        finally:
            conn.close()
        target_text, redactions = _redact_secrets(
            str(body or ""), session_id=str(rsid), ordinal=ordinal, locator_kind="tool")
        context = []
        for cid, crole, cts, corder, ctext in around:
            ctext, k = _redact_secrets(str(ctext or ""), session_id=str(rsid), ordinal=corder,
                                        locator_kind="message")
            redactions += k
            context.append(
                {"locator": f"messages#{cid}", "role": crole, "timestamp": cts,
                 "ordinal": corder, "text": ctext[:chars]}
            )
        return {
            "ok": True,
            "kind": "tool",
            "locator": locator,
            "citation": f"{SESSIONS_DB.name}:{table}#{rid} (session {rsid}, ordinal {ordinal})",
            "source_version": _version(SESSIONS_DB),
            "session_id": rsid,
            "ordinal": ordinal,
            "excerpt": target_text[:chars],
            "context": context,
            "redactions": redactions,
            **extra,
        }
    return None
