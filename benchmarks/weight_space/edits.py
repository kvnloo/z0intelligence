"""Reader for the "edits" weight language of Experiment 5 (lm_understand-logs).

The language, as the prompt states it: one statement per line, '#' starts a comment, a long
list may continue on the next lines. Targets are W1,b1,W2,b2,W3,b3 indexed with numpy-style
ints / half-open slices (a:b:s, ':' alone = whole axis). Operators: =, +=, -=, *=. A value is
a number (broadcast) or a (nested) list matching the selection's shape. Only the LAST ```edits
block is read; a statement that cannot be read is skipped and recorded.

The original reader is not released; this one is validated by re-reading the paper's three
replies and comparing with the released hand.npz networks (tests/test_weight_space.py).
"""
from __future__ import annotations

import ast
import re

import numpy as np

SHAPES = {"W1": (600, 32), "b1": (32,), "W2": (32, 32), "b2": (32,), "W3": (32, 6), "b3": (6,)}
NAMES = {"W1": "actor/dense1/kernel", "b1": "actor/dense1/bias", "W2": "actor/dense2/kernel",
         "b2": "actor/dense2/bias", "W3": "actor/logits/kernel", "b3": "actor/logits/bias"}

_BLOCK = re.compile(r"```edits[ \t]*\n(.*?)(?:```|\Z)", re.DOTALL)
_STMT = re.compile(r"^\s*(W1|b1|W2|b2|W3|b3)\s*\[(.*?)\]\s*(\+=|-=|\*=|=)\s*(.+)$", re.DOTALL)


def last_block(text: str) -> tuple[str | None, bool]:
    blocks = list(_BLOCK.finditer(text))
    if not blocks:
        return None, False
    m = blocks[-1]
    cut_off = text[m.end() - 3:m.end()] != "```"
    return m.group(1), cut_off


def _strip_comment(line: str) -> str:
    i = line.find("#")
    return line if i < 0 else line[:i]


def _statements(block: str) -> list[str]:
    out, cur, depth = [], "", 0
    for raw in block.splitlines():
        line = _strip_comment(raw).strip()
        if not line and depth == 0:
            continue
        cur = (cur + " " + line).strip() if cur else line
        depth = cur.count("[") - cur.count("]")
        if depth <= 0:
            out.append(cur)
            cur, depth = "", 0
    if cur:
        out.append(cur)
    return out


def _index(spec: str, ndim: int):
    parts = [p.strip() for p in spec.split(",")]
    if len(parts) != ndim:
        raise ValueError(f"expected {ndim} indices, got {len(parts)}")
    idx = []
    for p in parts:
        if ":" in p:
            bits = [b.strip() for b in p.split(":")]
            if len(bits) > 3:
                raise ValueError(f"bad slice {p!r}")
            vals = [int(b) if b else None for b in bits]
            idx.append(slice(*vals))
        else:
            idx.append(int(p))
    return tuple(idx)


def apply(text: str) -> tuple[dict[str, np.ndarray], dict]:
    """Return (weights by short name, report) for the reply text."""
    w = {k: np.zeros(s, dtype=np.float32) for k, s in SHAPES.items()}
    block, cut_off = last_block(text)
    report = {"block": block is not None, "statements": 0, "applied": 0, "errors": [], "cut_off": cut_off}
    if block is None:
        return w, report
    touched = set()
    for st in _statements(block):
        report["statements"] += 1
        try:
            m = _STMT.match(st)
            if not m:
                raise ValueError("not a statement")
            name, spec, op, rhs = m.groups()
            idx = _index(spec, len(SHAPES[name]))
            val = np.asarray(ast.literal_eval(rhs.strip()), dtype=np.float64)
            if np.any(np.abs(val) > 1e6) or not np.all(np.isfinite(val)):
                raise ValueError("value out of range")
            tgt = w[name][idx]
            val = np.broadcast_to(val, np.shape(tgt)) if val.ndim == 0 else val
            if np.shape(val) != np.shape(tgt):
                raise ValueError(f"shape {np.shape(val)} != selection {np.shape(tgt)}")
            if op == "=":
                w[name][idx] = val
            elif op == "+=":
                w[name][idx] = tgt + val
            elif op == "-=":
                w[name][idx] = tgt - val
            else:
                w[name][idx] = tgt * val
            touched.add(name)
            report["applied"] += 1
        except Exception as e:  # noqa: BLE001 - every unreadable statement is skipped and recorded
            report["errors"].append({"statement": st[:200], "error": str(e)[:200]})
    report["tensors_touched"] = sorted(touched)
    report["nonzero_total"] = int(sum(int(np.count_nonzero(a)) for a in w.values()))
    return w, report


def to_npz_dict(w: dict[str, np.ndarray]) -> dict[str, np.ndarray]:
    return {NAMES[k]: v for k, v in w.items()}
