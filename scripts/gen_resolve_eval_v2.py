#!/usr/bin/env python3
"""v2 resolve eval: generation-time quality gate, frozen and versioned.

Changes from v1, all driven by the v1 audit:

  * the `context` family is REMOVED. Every one of its 1,389 dev examples was
    labelled ANSWER_DEPENDENT: the template concatenated a tool name and a
    harness name into "what is the exec work on codex url?", which is only
    meaningful if you already know which source field the generator read.
    It is not replaced with an invented zero-overlap family, because a
    deterministic generator cannot make such a question both natural and
    uniquely answerable, and a fabricated one would reintroduce BAD_GROUND_TRUTH.
    v2 therefore measures single-hop lexical addressing only, and says so.
  * the full audit vocabulary is applied AT GENERATION, so a bad question never
    enters a split.
  * `REVISION`/`URL`/`PATH`/`MODEL` slot/value shape agreement is enforced at
    generation; v1 mislabelled 520 dev examples this way.

Ground truth is still the sampled source fact. Questions are phrased from the
value's own content tokens, never the value literal.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from eval_audit import classify  # noqa: E402  the frozen quality gate

COVERAGE = Path("/mnt/zer0models/sft-svlm/data/agentsview/coverage.db")
OUT = ROOT / "benchmarks" / "fixtures" / "resolve-fast-v2"

FACT_SLOT = {
    "path": "PATH",
    "identifier": "IDENTIFIER",
    "value": "CONFIG_VALUE",
    "argkey": "CONFIG_VALUE",
    "command": "COMMAND",
    "url": "URL",
    "sha": "REVISION",
}
#: One natural frame per slot. No tool/harness concatenation.
FRAMES = {
    "PATH": "where is the {t} located?",
    "CONFIG_VALUE": "what is the configured {t}?",
    "COMMAND": "what command did we run for {t}?",
    "URL": "what is the {t} endpoint address?",
    "REVISION": "what commit is {t} pinned to?",
    "IDENTIFIER": "which identifier is used for {t}?",
}
SAFE = {
    "PATH": lambda v: "/" in v and re_ok_path(v),
    "URL": lambda v: v.startswith(("http://", "https://")),
    "REVISION": lambda v: bool(__import__("re").fullmatch(r"[0-9a-fA-F]{7,40}", v.strip())),
    "MODEL": lambda v: "/" not in v and len(v) <= 60,
    "CONFIG_VALUE": lambda v: len(v) <= 120,
    "COMMAND": lambda v: len(v) <= 160,
    "IDENTIFIER": lambda v: len(v) <= 120,
}
MACHINE_NAME = __import__("re").compile(r"([0-9a-f]{16,}|[0-9a-f]{8}-[0-9a-f]{4}-)", __import__("re").I)
SCRATCH = __import__("re").compile(r"^(/tmp|/dev|/proc|/sys|/run|/var/tmp)(/|$)", __import__("re").I)
SECRETISH = __import__("re").compile(r"(sk-|gsk_|ghp_|AKIA|xox[baprs]-|PRIVATE KEY|password|secret|token)", __import__("re").I)


def re_ok_path(v: str) -> bool:
    return bool(__import__("re").search(r"\.\w{1,6}$", v))


def content_tokens(value: str) -> list[str]:
    import re as _re

    parts = _re.split(r"[/\\.\-_:]+", value)
    seen: list[str] = []
    for p in parts:
        p = p.strip()
        if len(p) < 3 or p.isdigit():
            continue
        if p.lower() not in [s.lower() for s in seen]:
            seen.append(p)
    return seen


def assign_split(session_id: str | None, rowid: int) -> str:
    key = session_id or f"row{rowid}"
    h = int(hashlib.sha256(key.encode()).hexdigest()[:8], 16) % 100
    return "train" if h < 60 else ("dev" if h < 80 else "sealed")


def main() -> int:
    conn = sqlite3.connect(f"file:{COVERAGE}?mode=ro", uri=True)
    conn.execute("PRAGMA query_only=1")
    facts: list[tuple] = []
    for fact_kind in FACT_SLOT:
        facts.extend(
            conn.execute(
                "select id, session_id, tool_name, harness, kind, value from facts "
                "where kind=? and n_chars between 6 and 160 order by id limit 3000",
                (fact_kind,),
            ).fetchall()
        )
    conn.close()

    cand: list[dict] = []
    rejected = Counter()
    for fid, sid, tool, harness, kind, value in facts:
        value = (value or "").strip()
        slot = FACT_SLOT[kind]
        if len(value) < 6 or SCRATCH.match(value) or MACHINE_NAME.search(value) or SECRETISH.search(value):
            rejected["scratch_or_secret"] += 1
            continue
        if not SAFE[slot](value):
            rejected["slot_shape"] += 1
            continue
        toks = content_tokens(value)
        if len(toks) < 2:
            rejected["no_topic"] += 1
            continue
        topic = " ".join(toks[:3])
        if len(topic) < 6:
            rejected["weak_topic"] += 1
            continue
        q = FRAMES[slot].format(t=topic)
        # answer uniqueness under the sampled corpus
        tokl = [t.lower() for t in toks[:3]]
        if sum(1 for _f, _s, _t, _h, _k, v in facts if v and all(t in v.lower() for t in tokl)) > 1:
            rejected["ambiguous_answer"] += 1
            continue
        cand.append(
            {
                "id": f"{kind}-{fid}",
                "question": q,
                "slot_kind": slot,
                "expected_value": value,
                "evidence_pointer": f"coverage:fact:{fid}",
                "source_tool": tool,
                "harness": harness,
                "session_id": sid,
                "family": "tokenized",
                "fact_kind": kind,
                "split": assign_split(sid, fid),
            }
        )

    dup = Counter((r["question"], r["expected_value"]) for r in cand)
    kept: list[dict] = []
    labels = Counter()
    for r in cand:
        lab = classify(r, dup)
        labels[lab] += 1
        if lab == "VALID":
            kept.append({**r, "quality_gate": "v2"})
        else:
            rejected[f"gate:{lab.lower()}"] += 1

    OUT.mkdir(parents=True, exist_ok=True)
    for split in ("train", "dev", "sealed"):
        sub = [r for r in kept if r["split"] == split]
        (OUT / f"{split}.jsonl").write_text(
            "\n".join(json.dumps(r, ensure_ascii=False) for r in sub) + ("\n" if sub else "")
        )
        print(f"  {split:7} {len(sub):5}")
        labels_by_split = Counter(r["slot_kind"] for r in sub)
        print(f"          slots: {dict(labels_by_split)}")

    dataset_hash = hashlib.sha256(
        b"".join(sorted(json.dumps(r, sort_keys=True).encode() for r in kept))
    ).hexdigest()
    manifest = {
        "version": "resolve-fast-v2",
        "generator": "scripts/gen_resolve_eval_v2.py",
        "quality_gate": "scripts/eval_audit.py::classify (frozen vocabulary)",
        "split_policy": "sha256(session_id)%100 -> train<60, dev<80, sealed>=80",
        "family": ["tokenized"],
        "known_limitation": "no zero-overlap (two-hop) family: measures single-hop lexical addressing only; the regression-17 suite remains the adversarial suite for two-hop",
        "ground_truth": "sampled coverage.db source fact; templates never contain the value literal",
        "counts": {s: sum(1 for r in kept if r["split"] == s) for s in ("train", "dev", "sealed")},
        "gate_labels": dict(labels),
        "rejected": dict(rejected),
        "dataset_sha256": dataset_hash,
    }
    (OUT / "manifest.json").write_text(json.dumps(manifest, indent=2))
    print(f"\n  gate labels: {dict(labels)}")
    print(f"  rejected: {dict(rejected)}")
    print(f"  dataset_sha256: {dataset_hash}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
