"""Build the 22,079-case corpus once; record Python verdicts and host encodings (no timing)."""
from __future__ import annotations

import json
import pickle
from collections import Counter

from common import WORK, bg, build_cases, corpus_sha, line_of, par

cases = build_cases()
rows = []
lines = []
for i, (name, doc) in enumerate(cases):
    p_ok, p_codes, p_exc = par.python_verdict(doc)
    enc = bg.encode_document(doc, par.CATALOG)
    row = {"i": i, "case": name, "kind": name.split(":")[0], "python_ok": p_ok, "python_codes": dict(p_codes),
           "python_exception": p_exc}
    if enc.representable:
        row["route"] = "kernel"
        row["line_index"] = len(lines)
        lines.append(line_of(enc.tokens or []))
    else:
        row["route"] = "unsupported" if enc.unsupported else "host-shape"
        row["shape_codes"] = dict(Counter(c for c, _ in enc.shape))
    rows.append(row)
sha = corpus_sha(cases)
(WORK / "corpus.pkl").write_bytes(pickle.dumps({"cases": cases, "rows": rows, "lines": lines, "sha": sha}))
(WORK / "kernel_lines.txt").write_bytes(b"".join(lines))
summary = {"cases": len(cases), "kinds": dict(Counter(r["kind"] for r in rows)),
           "routes": dict(Counter(r["route"] for r in rows)), "corpus_sha256": sha,
           "python_accept": sum(r["python_ok"] for r in rows),
           "python_exceptions": sum(r["python_exception"] is not None for r in rows),
           "kernel_lines_bytes": sum(len(x) for x in lines),
           "tokens_per_line": {"mean": round(sum(len(x.split()) for x in lines) / len(lines), 1),
                               "max": max(len(x.split()) for x in lines)}}
print(json.dumps(summary, indent=1))
(WORK / "corpus_summary.json").write_text(json.dumps(summary, indent=1) + "\n")
