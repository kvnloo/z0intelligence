"""Secret scrub applied to every memory output (search results, briefs, MCP responses, receipts, EventLog writes).

AgentsView indexed credential-bearing tool output (owner 09-22), so nothing the memory surface returns or
writes may carry a credential-shaped string. Patterns are the kernel's conservative set
(origin/consolidate/z0-kernel-20260922 ``context_providers._SECRET_RES``) plus Bitwarden Secrets Manager
access tokens and a PEM block that a bounded excerpt may have cut before its END line.
"""

from __future__ import annotations

import re
from typing import Any

REDACTED = '[redacted:credential]'

_SECRET_RES = (
    re.compile(r'-----BEGIN [A-Z ]*PRIVATE KEY-----(?:.*?-----END [A-Z ]*PRIVATE KEY-----|[A-Za-z0-9+/=\s]*)', re.S),
    re.compile(r'\b0\.[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\.[A-Za-z0-9_\-]{8,}:[A-Za-z0-9+/=]{8,}'),
    re.compile(r'\b(?:sk|pk|rk)-[A-Za-z0-9_\-]{16,}'),
    re.compile(r'\bAIza[0-9A-Za-z_\-]{20,}'),
    re.compile(r'\b(?:AKIA|ASIA)[0-9A-Z]{16}\b'),
    re.compile(r'\bgh[pousr]_[A-Za-z0-9]{20,}'),
    re.compile(r'\bxox[baprs]-[A-Za-z0-9\-]{10,}'),
    re.compile(r'(?i)\b(?:api[_-]?key|access[_-]?token|refresh[_-]?token|client[_-]?secret|secret[_-]?access[_-]?key'
               r'|password|passwd)\b\s*[:=]\s*["\']?[^\s"\',;]{8,}'),
    re.compile(r'(?i)\bbearer\s+[A-Za-z0-9._\-]{16,}'),
)


def scrub_text(text: str) -> tuple[str, int]:
    """Blank every credential shape; returns (text, number of spans removed)."""
    n = 0
    for rx in _SECRET_RES:
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
