#!/usr/bin/env python3
"""Audit a generated resolve eval set: classify EVERY example with a reason.

Nothing is silently discarded. Every example gets exactly one label from a fixed
vocabulary, so the report can say how much of a measured failure rate was the
resolver and how much was the ruler.
"""

from __future__ import annotations

import argparse
import json
import re
from collections import Counter
from pathlib import Path

SCRATCH = re.compile(r"^(/tmp|/dev|/proc|/sys|/run|/var/tmp)(/|$)", re.I)
#: The v1 `context` family template concatenated a tool name and a harness name
#: with the literal words "work on". That is the mechanical-generation marker.
MECHANICAL = re.compile(r"\bwork on\b")
WH = re.compile(r"\b(where|which|what|who|when|how|why)\b", re.I)
CODEY = re.compile(r"[{}\\]|\n|=>|;\s*$")
BARE_TOOL = re.compile(r"\b(exec|bash|terminal|apply_patch|run_terminal_command|grep|read_file)\b", re.I)
STOP = frozenset(
    "the a an and or of to in on for with from at by is are was were be which what where did do "
    "does we us our use used using file files located stored".split()
)
TRAIL = re.compile(r"[\s`'\".,;:)\]}]+$")
#: Vocabulary additions from the independent critic pass. PREFIX_ECHO was the
#: critic's single biggest finding and my first gate MISSED it entirely: it only
#: tested the literal value, while the generator derives the topic from the
#: value's own tokens, so the question is routinely the answer with punctuation
#: turned into spaces (`CP036_TASK_MISSING` -> "CP036 TASK MISSING").
CORRUPT = re.compile(r"[\n\r\t|`]|\{\{|\}\}")


def skeleton(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (text or "").lower()).strip()


def prefix_echo(question: str, value: str) -> bool:
    """True when the question is a normalised copy/prefix of the answer."""
    qs, vs = skeleton(question), skeleton(value)
    if len(vs) < 8:
        return False
    if vs in qs:
        return True
    vtoks = vs.split()
    if len(vtoks) < 3:
        return False
    # the value's leading tokens appearing, in order, inside the question
    hits = 0
    pos = 0
    for tok in vtoks:
        idx = qs.find(tok, pos)
        if idx >= 0:
            hits += 1
            pos = idx + len(tok)
    return hits >= max(3, int(0.8 * len(vtoks)))


def norm(v: str) -> str:
    return TRAIL.sub("", (v or "").strip()).lower()


def content_tokens(text: str) -> list[str]:
    return [t.lower() for t in re.findall(r"[A-Za-z0-9_][\w.\-]{2,}", text or "")
            if t.lower() not in STOP]


def classify(row: dict, dup_index: Counter) -> str:
    q, val, slot = row["question"], row["expected_value"], row["slot_kind"]
    ql, vl = q.lower(), val.lower()
    base = vl.rsplit("/", 1)[-1]

    if vl and vl in ql:
        return "ANSWER_LEAK"
    if base and len(base) > 5 and base in ql:
        return "ANSWER_LEAK"
    if CORRUPT.search(val) or len(val) > 160:
        return "CORRUPT_VALUE"
    if prefix_echo(q, val):
        return "PREFIX_ECHO"
    if dup_index[(q, val)] > 1:
        return "DUPLICATE_NEAR_COPY"
    if CODEY.search(q):
        return "UNNATURAL"
    if MECHANICAL.search(q):
        # "what is the exec work on codex url?" -- only meaningful if you already
        # know which source field the generator read.
        return "ANSWER_DEPENDENT"
    if not WH.search(q):
        return "UNNATURAL"
    toks = content_tokens(q)
    if len(toks) < 2:
        return "UNDERSPECIFIED"
    if SCRATCH.match(val):
        return "MULTI_ANSWER"
    # Slot/value shape agreement.
    if slot == "PATH" and "/" not in val:
        return "WRONG_SLOT"
    if slot == "URL" and not val.startswith(("http://", "https://")):
        return "WRONG_SLOT"
    if slot == "REVISION" and not re.fullmatch(r"[0-9a-fA-F]{7,40}", val.strip()):
        return "WRONG_SLOT"
    if slot == "MODEL" and ("/" in val or len(val) > 60):
        return "WRONG_SLOT"
    # A question whose only distinctive token is a bare tool name is answering
    # "which tool?", not the declared slot.
    distinctive = [t for t in toks if len(t) > 4]
    if distinctive and all(BARE_TOOL.match(t) for t in distinctive):
        return "UNDERSPECIFIED"
    return "VALID"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--fixtures", default="benchmarks/fixtures/resolve-fast-v1")
    ap.add_argument("--split", default="dev")
    ap.add_argument("--out", default=None)
    ap.add_argument("--write-valid", default=None, help="freeze the VALID subset into this dir")
    a = ap.parse_args()
    path = Path(a.fixtures) / f"{a.split}.jsonl"
    rows = [json.loads(l) for l in path.read_text().splitlines() if l.strip()]
    dup = Counter((r["question"], r["expected_value"]) for r in rows)
    labels = Counter()
    by_family: dict[str, Counter] = {}
    by_slot: dict[str, Counter] = {}
    annotated = []
    for r in rows:
        lab = classify(r, dup)
        labels[lab] += 1
        by_family.setdefault(r["family"], Counter())[lab] += 1
        by_slot.setdefault(r["slot_kind"], Counter())[lab] += 1
        annotated.append({**r, "audit_label": lab})
    n = len(rows)
    print(f"audited {a.split}: {n} examples")
    for lab, c in labels.most_common():
        print(f"  {lab:22} {c:5}  {c/n:6.1%}")
    print("\nby family:")
    for fam, c in by_family.items():
        print(f"  {fam:12} n={sum(c.values()):5}  valid={c['VALID']:5}  " +
              " ".join(f"{k}={v}" for k, v in c.most_common() if k != "VALID"))
    print("\nby slot:")
    for slot, c in by_slot.items():
        print(f"  {slot:14} n={sum(c.values()):5}  valid={c['VALID']:5}  " +
              " ".join(f"{k}={v}" for k, v in c.most_common() if k != "VALID"))
    if a.write_valid:
        import hashlib
        out = Path(a.write_valid)
        out.mkdir(parents=True, exist_ok=True)
        keep = [r for r in annotated if r["audit_label"] == "VALID"]
        for split in ("train", "dev", "sealed"):
            src = Path(a.fixtures) / f"{split}.jsonl"
            if not src.is_file():
                continue
            rows_s = [json.loads(l) for l in src.read_text().splitlines() if l.strip()]
            dup_s = Counter((r["question"], r["expected_value"]) for r in rows_s)
            sub = [r for r in rows_s if classify(r, dup_s) == "VALID"]
            (out / f"{split}.jsonl").write_text(
                "\n".join(json.dumps(r, ensure_ascii=False) for r in sub) + ("\n" if sub else "")
            )
            print(f"  frozen {split:7} {len(sub)}")
        hl = hashlib.sha256()
        for split in ("train", "dev", "sealed"):
            f = out / f"{split}.jsonl"
            if f.is_file():
                hl.update(f.read_bytes())
        (out / "manifest.json").write_text(json.dumps({
            "version": "resolve-fast-v3",
            "derived_from": "resolve-fast-v2",
            "gate": "scripts/eval_audit.py::classify v3 vocabulary (adds PREFIX_ECHO, CORRUPT_VALUE)",
            "independent_critique": "subagent pass found PREFIX_ECHO; gate was extended to agree with it",
            "counts": {s: sum(1 for l in (out/f"{s}.jsonl").read_text().splitlines() if l.strip())
                       for s in ("train","dev","sealed") if (out/f"{s}.jsonl").is_file()},
            "known_limitation": "no zero-overlap family; measures single-hop lexical addressing only. regression-17 remains the two-hop adversarial suite.",
            "dataset_sha256": hl.hexdigest(),
        }, indent=2))
        print(f"  dataset_sha256: {hl.hexdigest()}")
    if a.out:
        Path(a.out).write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in annotated))
        print(f"\nwrote annotated -> {a.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
