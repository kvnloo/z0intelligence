#!/usr/bin/env python3
"""Build a source-grounded resolve eval set from coverage.db facts.

Ground truth is a REAL sampled source fact -- its value comes from the store, not
from a model. Questions are generated from that fact's envelope:

  tokenized : built from the value's own content tokens (realistic lexical
              overlap -- what a person who half-remembers the name would ask)
  context   : built from the fact's tool/session context and DELIBERATELY
              excluding every value token (the zero-lexical-overlap case the
              two-hop bridge exists for)

Controls (an example is rejected, never silently kept):
  answer literal in question / value too short / generic path / secret-shaped
  value / no usable topic tokens / pointer missing.

Splits are assigned by hashing session_id, so project/session/task-family
leakage is bounded. The sealed split's labels are only ever read by the frozen
evaluator script; candidates are generated from train/dev only.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sqlite3
from pathlib import Path

COVERAGE = Path("/mnt/zer0models/sft-svlm/data/agentsview/coverage.db")
OUT = Path("benchmarks/fixtures/resolve-fast-v1")

FACT_SLOT = {
    "path": "PATH",
    "identifier": "IDENTIFIER",
    "value": "CONFIG_VALUE",
    "argkey": "CONFIG_VALUE",
    "command": "COMMAND",
    "url": "URL",
    "sha": "REVISION",
    "branch": "REVISION",
}

FRAMES = {
    "PATH": ("where is the {t} located?", "which file holds the {t}?"),
    "MODEL": ("which model did we use for {t}?",),
    "CONFIG_VALUE": ("what is the configured {t}?", "which setting controls {t}?"),
    "COMMAND": ("what command did we run for {t}?",),
    "URL": ("what is the {t} url?",),
    "REVISION": ("what revision is {t} pinned to?",),
    "IDENTIFIER": ("which identifier is used for {t}?",),
}

#: Values too generic to make a meaningful question about.
GENERIC = re.compile(
    r"^(/dev/null|/tmp|/usr/bin|/usr/lib|/etc|/var|/home|\.|\.\.|true|false|none|null|"
    r".*node_modules.*|.*__pycache__.*|.*\.pyc)$",
    re.I,
)
#: Scratch/ephemeral roots and machine-generated names make meaningless answers.
SCRATCH = re.compile(r"^(/tmp|/dev|/proc|/sys|/run|/var/tmp|/mnt/zer0models/cache)(/|$)", re.I)
MACHINE_NAME = re.compile(r"([0-9a-f]{16,}|[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4})", re.I)
SECRETISH = re.compile(r"(sk-|gsk_|ghp_|AKIA|xox[baprs]-|BEGIN [A-Z ]*PRIVATE KEY|password|passwd|secret|token)", re.I)
SPLIT_STOP = frozenset(
    "the a an and or of to in on for with from at by is are was were be which what where "
    "did do does we us our use used using file files path located stored".split()
)


def content_tokens(value: str) -> list[str]:
    parts = re.split(r"[/\\.\-_:]+", value)
    out: list[str] = []
    for p in parts:
        p = p.strip()
        if len(p) < 3 or p.lower() in SPLIT_STOP or p.isdigit():
            continue
        if p.lower() not in [o.lower() for o in out]:
            out.append(p)
    return out


def assign_split(session_id: str | None, rowid: int) -> str:
    key = session_id or f"row{rowid}"
    h = int(hashlib.sha256(key.encode()).hexdigest()[:8], 16) % 100
    if h < 60:
        return "train"
    if h < 80:
        return "dev"
    return "sealed"


def build(limit_per_kind: int, out_dir: Path) -> dict[str, int]:
    conn = sqlite3.connect(f"file:{COVERAGE}?mode=ro", uri=True)
    conn.execute("PRAGMA query_only=1")
    counts: dict[str, int] = {}
    rows: list[dict] = []
    rejected: dict[str, int] = {}
    facts: list[tuple] = []
    # Sample deterministically but spread across kinds.
    for fact_kind, slot in FACT_SLOT.items():
        sql = (
            "select id, session_id, tool_name, ts, harness, cwd, kind, key, value "
            "from facts where kind=? and n_chars between 6 and 160 "
            "order by id limit ?"
        )
        for fid, sid, tool, ts, harness, cwd, kind, key, value in conn.execute(
            sql, (fact_kind, limit_per_kind * 20)
        ):
            facts.append((fact_kind, fid, sid, tool, harness, value))
        for fact_kind, fid, sid, tool, harness, value in facts:
            if value is None:
                continue
            value = (value or "").strip()
            if len(value) < 6 or GENERIC.match(value):
                rejected["generic"] = rejected.get("generic", 0) + 1
                continue
            if SCRATCH.match(value) or MACHINE_NAME.search(value):
                rejected["scratch_or_machine_name"] = rejected.get("scratch_or_machine_name", 0) + 1
                continue
            if SECRETISH.search(value):
                rejected["secret"] = rejected.get("secret", 0) + 1
                continue
            toks = content_tokens(value)
            if len(toks) < 2:
                rejected["no_topic"] = rejected.get("no_topic", 0) + 1
                continue
            topic = " ".join(toks[:3])
            if len(topic) < 6:
                rejected["weak_topic"] = rejected.get("weak_topic", 0) + 1
                continue
            # CONTROL (answer uniqueness). If several source facts are all
            # answered by this question, the example has no single ground truth
            # and any "wrong" answer it produces is the eval's fault, not the
            # resolver's. Count facts whose value carries every topic token.
            tokl = [t.lower() for t in toks[:3]]
            matches = sum(
                1 for _fk, _fid, _s, _t, _h, v in facts
                if v and all(t in v.lower() for t in tokl)
            )
            if matches > 1:
                rejected["ambiguous_answer"] = rejected.get("ambiguous_answer", 0) + 1
                continue
            # CONTROL: the answer literal must not appear in the question.
            q_tokenized = FRAMES[slot][0].format(t=topic)
            q_context = FRAMES[slot][-1].format(t=f"{tool or 'tool'} work on {harness or 'this project'}")
            for family, q in (("tokenized", q_tokenized), ("context", q_context)):
                if value.lower() in q.lower():
                    rejected["literal_leak"] = rejected.get("literal_leak", 0) + 1
                    continue
                if family == "context" and any(t.lower() in q.lower() for t in toks):
                    rejected["context_leak"] = rejected.get("context_leak", 0) + 1
                    continue
                rows.append(
                    {
                        "id": f"{fact_kind}-{fid}-{family[0]}",
                        "question": q,
                        "slot_kind": slot,
                        "expected_value": value,
                        "evidence_pointer": f"coverage:fact:{fid}",
                        "source_tool": tool,
                        "harness": harness,
                        "session_id": sid,
                        "family": family,
                        "fact_kind": fact_kind,
                        "split": assign_split(sid, fid),
                    }
                )
                counts[slot] = counts.get(slot, 0) + 1
    conn.close()
    # De-duplicate: the same value appears in many fact rows, and a question set
    # full of identical pairs measures nothing.
    seen: set[tuple[str, str]] = set()
    deduped: list[dict] = []
    for r in rows:
        k = (r["question"], r["expected_value"])
        if k in seen:
            rejected["duplicate"] = rejected.get("duplicate", 0) + 1
            continue
        seen.add(k)
        deduped.append(r)
    rows = deduped
    out_dir.mkdir(parents=True, exist_ok=True)
    for split in ("train", "dev", "sealed"):
        sub = [r for r in rows if r["split"] == split]
        with (out_dir / f"{split}.jsonl").open("w", encoding="utf-8") as fh:
            for r in sub:
                fh.write(json.dumps(r, ensure_ascii=False) + "\n")
        print(f"  {split:7} {len(sub):5} examples")
    print("  rejected:", json.dumps(rejected))
    (out_dir / "manifest.json").write_text(
        json.dumps({"counts": counts, "rejected": rejected, "total": len(rows)}, indent=2)
    )
    return counts


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--per-kind", type=int, default=120)
    ap.add_argument("--out", default=str(OUT))
    a = ap.parse_args()
    print("building resolve-fast-v1 eval from source facts")
    build(a.per_kind, Path(a.out))
