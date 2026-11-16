"""Secret scrub applied to every memory output (search results, briefs, MCP responses, receipts, EventLog writes).

AgentsView indexed credential-bearing tool output (owner 09-22), so nothing the memory surface returns or
writes may carry a credential-shaped string. Patterns are the kernel's conservative set
(origin/consolidate/z0-kernel-20260922 ``context_providers._SECRET_RES``) plus Bitwarden Secrets Manager
access tokens, xAI/Groq/Hugging Face/fine-grained GitHub tokens, env-dump and JSON ``*_API_KEY=``/``"token":``
pairs, and a PEM block that a bounded excerpt may have cut before its END line. AgentsView's own scanner
findings (exact spans, ``redact_spans``) are applied first; the patterns are the backstop.
"""

from __future__ import annotations

import re
from typing import Any

REDACTED = '[redacted:credential]'
REDACTED_SPAN = '[redacted:secret]'

_SECRET_RES = (
    re.compile(r'-----BEGIN [A-Z ]*PRIVATE KEY-----(?:.*?-----END [A-Z ]*PRIVATE KEY-----|[A-Za-z0-9+/=\s]*)', re.S),
    re.compile(r'\b0\.[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\.[A-Za-z0-9_\-]{8,}:[A-Za-z0-9+/=]{8,}'),
    re.compile(r'\b(?:sk|pk|rk)-[A-Za-z0-9_\-]{16,}'),
    re.compile(r'\bAIza[0-9A-Za-z_\-]{20,}'),
    re.compile(r'\b(?:AKIA|ASIA)[0-9A-Z]{16}\b'),
    re.compile(r'\bgh[pousr]_[A-Za-z0-9]{20,}'),
    re.compile(r'\bxox[baprs]-[A-Za-z0-9\-]{10,}'),
    re.compile(r'\b(?:xai-|gsk_|hf_|github_pat_)[A-Za-z0-9_]{20,}'),
    # key=value / "key": "value" with any name prefix (XAI_API_KEY=, HF_TOKEN=, "access_token": ...). No leading
    # \b, so a prefix joined by "_" still matches; the name must end the identifier, so max_tokens: 600 does not.
    re.compile(r'(?i)(?:api[_-]?key|access[_-]?key|token|secret|password|passwd)["\']?\s*[:=]\s*["\']?'
               r'(?!\[redacted)[^\s"\',;]{8,}'),
    re.compile(r'(?i)\bbearer\s+[A-Za-z0-9._\-]{16,}'),
)
# For each pattern above, in order: the literal substrings that must appear somewhere in the text for that
# pattern to have any chance of matching (never a stricter condition than the pattern itself), and whether
# to check them case-insensitively. A redaction can only erase characters (replacing a match with the fixed
# REDACTED literal, which contains none of these substrings) and this pattern set's matches never straddle
# or start right after an earlier REDACTED marker (verified for every pair here, including the generic
# key=value pattern's own ``(?!\[redacted)`` guard), so checking every pattern's literals against the one
# pristine, never-mutated copy of the input is always at least as inclusive as checking the live, partially
# redacted text would be — it only ever over-triggers (an extra, harmless subn on a span already consumed by
# an earlier pattern), never under-triggers. This is the fast path for the common case (no credential shape
# present at all): a handful of substring checks replace 9 regex scans. Combining the patterns into one
# alternation instead was tried and rejected — it silently reorders which shape wins at a shared position
# (e.g. "Bearer sk-..." redacts the whole phrase instead of just the key) and was not even faster in CPython.
_TRIGGERS = (
    (('-----BEGIN ',), False),
    (('0.',), False),
    (('sk-', 'pk-', 'rk-'), False),
    (('AIza',), False),
    (('AKIA', 'ASIA'), False),
    (('ghp_', 'gho_', 'ghu_', 'ghs_', 'ghr_'), False),
    (('xoxb-', 'xoxa-', 'xoxp-', 'xoxr-', 'xoxs-'), False),
    (('xai-', 'gsk_', 'hf_', 'github_pat_'), False),
    (('key', 'token', 'secret', 'password', 'passwd'), True),
    (('bearer',), True),
)


def redact_spans(text: str, spans) -> tuple[str, int]:
    """Blank exact spans AgentsView's scanner recorded (``secret_findings``: UTF-8 byte offsets into the
    message). Spans that no longer line up with the text are ignored; returns (text, number blanked)."""
    raw, n = text.encode('utf-8'), 0
    for a, b in sorted({(int(a), int(b)) for a, b in spans if a is not None and b is not None}, reverse=True):
        if 0 <= a < b <= len(raw):
            raw, n = raw[:a] + REDACTED_SPAN.encode() + raw[b:], n + 1
    return (raw.decode('utf-8', errors='replace'), n) if n else (text, 0)


def scrub_text(text: str) -> tuple[str, int]:
    """Blank every credential shape; returns (text, number of spans removed)."""
    original, low, n = text, None, 0
    for rx, (lits, case_insensitive) in zip(_SECRET_RES, _TRIGGERS):
        if case_insensitive:
            low = low if low is not None else original.lower()
            haystack = low
        else:
            haystack = original
        if not any(lit in haystack for lit in lits):
            continue
        text, k = rx.subn(REDACTED, text)
        n += k
    return text, n


def scrub_obj(obj: Any) -> tuple[Any, int]:
    """Scrub every string in a JSON-shaped value (keys included); returns (copy, count)."""
    if isinstance(obj, str):
        return scrub_text(obj)
    if isinstance(obj, dict):
        out, n = {}, 0
        for k, v in obj.items():
            k2, a = scrub_obj(k) if isinstance(k, str) else (k, 0)
            v2, b = scrub_obj(v)
            out[k2], n = v2, n + a + b
        return out, n
    if isinstance(obj, (list, tuple)):
        items = [scrub_obj(v) for v in obj]
        return type(obj)(v for v, _ in items) if isinstance(obj, tuple) else [v for v, _ in items], sum(c for _, c in items)
    return obj, 0
