#!/usr/bin/env python3
"""Quality gate for mined real-question pairs (Parts 3 and 6 of the brief).

Input : `.work/real-q-candidates.jsonl` (from `mine_real_questions.py`)
Output: `.work/real-q-gated.jsonl`   -- every pair, kept or rejected, with one reason
        `.work/real-q-critic-input.json` -- redacted view for the independent critic

The gate is deterministic and must be able to be WRONG: it is validated against an
independent critic that never sees this file's logic (see `--apply-critique`).

Nothing is silently discarded: every pair leaves with exactly one reason code.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sqlite3
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

from mine_real_questions import DEFAULT_DB, content_tokens  # noqa: E402

#: A turn that asks the agent to *change* the world is not a memory question.
CHANGE_VERB = re.compile(
    r"^\s*(can|could|would|will|please|let'?s?|make|add|run|create|fix|update|write|build|set|"
    r"commit|push|merge|install|spin|start|move|rename|delete|copy|help|continue|refactor|"
    r"remove|kill|restart|deploy|test|check|look|show|give|tell|find|remind|list|explain)\b",
    re.I,
)
#: ...but "can you tell me where X is" IS an information request.
INFO_VERB = re.compile(
    r"^\s*(can|could|would|will)\s+you\s+(tell|show|find|remind|check|confirm|list|give|explain|look)"
    r"|\b(what|which|where)\b",
    re.I,
)
#: An action-form turn is still a lookup when it *names the value it wants*
#: ("spin up the dev server and get the link"). Without this the gate throws away
#: genuine URL/path requests because they also ask for an action.
REQ_VALUE = re.compile(
    r"\b(link|url|path|file|folder|directory|command|commit|branch|sha|port|endpoint|"
    r"location|repo|repository|config)\b", re.I)
REQ_VERB = re.compile(r"\b(what|which|where|get|give|send|link|show|find|tell|drop|paste|see)\b", re.I)
CHANGE_ONLY = re.compile(
    r"^\s*(can|could|would|will|please)\s+you\s+(make|add|run|create|fix|update|write|build|set|"
    r"commit|push|merge|install|spin|start|move|rename|delete|copy|refactor|remove|kill|restart|deploy|test|continue)\b",
    re.I,
)
HOSTLIKE = re.compile(r"^/?(?:\d{1,3}\.){3}\d{1,3}(:\d+)?$|^/(localhost|127\.|0\.0\.0\.0)")
ENV_NAME = re.compile(r"^[A-Z][A-Z0-9_]{2,}$")
DOTTED = re.compile(r"^[\w-]+(?:\.[\w-]+)+:?=\s*[\w.\-/]+$")
HEXISH = re.compile(r"^[0-9a-fA-F]{7,40}$")
CODE_EXT = re.compile(
    r"\.(md|py|yaml|yml|json|jsonl|js|mjs|cjs|ts|tsx|jsx|sh|bash|zsh|go|rs|toml|txt|db|"
    r"sqlite|sql|html|css|scss|cfg|ini|env|lock|proto|rb|java|kt|c|h|cpp|hpp|xml|csv)$", re.I)
HAS_ALPHA = re.compile(r"[a-fA-F]")


def value_shape_ok(kind: str, value: str) -> str | None:
    """Return a reject code when the value cannot be an answer of this slot."""
    v = value.strip()
    if not v or len(v) > 200:
        return "VALUE_SHAPE"
    if kind == "PATH":
        if HOSTLIKE.match(v):
            return "VALUE_SHAPE_PATH_HOST"
        if v.count("/") < 2 and not CODE_EXT.search(v):
            return "VALUE_SHAPE_PATH_FRAGMENT"
        if re.search(r"\.(png|jpg|jpeg|gif|webp|mp4|mov|wav|mp3|zip|pdf|ico|svg)$", v, re.I):
            return "VALUE_SHAPE_PATH_ASSET"
        return None
    if kind == "URL":
        if re.match(r"^https?://[^\s/]+(:\d+)?(/|$)", v):
            return None
        return "VALUE_SHAPE_URL"
    if kind == "REVISION":
        if not HEXISH.match(v):
            return "VALUE_SHAPE_REVISION"
        if not HAS_ALPHA.search(v):
            # pure digits of length 8 are dates (20260712), not revisions
            return "VALUE_SHAPE_REVISION_DIGITS"
        return None
    if kind == "ISSUE":
        if not re.fullmatch(r"#\d+", v):
            return "VALUE_SHAPE_ISSUE"
        return None
    if kind == "CONFIG_VALUE":
        if ENV_NAME.match(v) or DOTTED.match(v):
            return None
        return "VALUE_SHAPE_CONFIG"
    if kind == "MODEL":
        if "/" in v or len(v) > 60 or not re.search(r"\d", v):
            return "VALUE_SHAPE_MODEL"
        return None
    if kind == "COMMAND":
        if re.fullmatch(r"[\w.\-/]+\.(sh|py|mjs|js|ts|rs|go)", v) or " " in v:
            return None
        return "VALUE_SHAPE_COMMAND"
    if kind == "REPOSITORY":
        if re.fullmatch(r"[\w.\-]+/[\w.\-]+", v):
            return None
        return "VALUE_SHAPE_REPO"
    if kind == "IDENTIFIER":
        if len(v) >= 4 and re.search(r"[A-Za-z_]", v):
            return None
        return "VALUE_SHAPE_IDENTIFIER"
    return None


def question_is_information_request(q: str, form: str) -> str | None:
    """Is this a request for information, or a request for action?"""
    if form == "action":
        # an action turn that also names the value it wants is a lookup
        if not (REQ_VALUE.search(q) and REQ_VERB.search(q)):
            return "Q_ACTION_REQUEST"
    # the question must actually ask for the slot object, not merely be a
    # question that happens to sit next to a value of that shape
    if not re.search(r"\?", q) and not re.match(r"^\s*(where|which|what|who|when|how)\b", q, re.I):
        return "Q_ACTION_REQUEST"
    return None


#: The question must name the *kind of thing* it wants. Without this, a status
#: turn ("continue working on your goal") binds to whatever path the assistant
#: mentioned last, which is not an answer to anything.
SLOT_OBJECT: dict[str, re.Pattern[str]] = {
    "PATH": re.compile(r"\b(file|files|path|paths|folder|director(y|ies)|dir|worktree|cwd|"
                       r"location|located|stored|saved|where)\b", re.I),
    "URL": re.compile(r"\b(url|link|endpoint|port|localhost|address|host)\b", re.I),
    "MODEL": re.compile(r"\b(model|models|llm|checkpoint|weights)\b", re.I),
    "COMMAND": re.compile(r"\b(command|script|install|invoke|how do i run|how to run)\b", re.I),
    "REVISION": re.compile(r"\b(commit|revision|sha|branch|tag|version)\b", re.I),
    "ISSUE": re.compile(r"\b(issue|pr|pull request|ticket)\b", re.I),
    "REPOSITORY": re.compile(r"\b(repo|repository)\b", re.I),
    "CONFIG_VALUE": re.compile(r"\b(config|setting|backend|env(?:ironment)?|variable|flag|"
                               r"harness|provider|default)\b", re.I),
    "IDENTIFIER": re.compile(r"\b(identifier|symbol|function|helper|module|variable|key|id)\b", re.I),
}


def slot_object_ok(q: str, kind: str) -> bool:
    rx = SLOT_OBJECT.get(kind)
    return bool(rx and rx.search(q))


def classify_raw(row: dict) -> str | None:
    """Reuse the v3 audit vocabulary plus a real-question-specific check."""
    from eval_audit import classify

    label = classify(row, Counter())
    # UNNATURAL / ANSWER_DEPENDENT are synthetic-generator markers: `classify`
    # flags any newline as UNNATURAL, which would reject every real multi-line
    # user question. Naturalness for real turns is enforced by `question_form`.
    if label in ("PREFIX_ECHO", "CORRUPT_VALUE", "ANSWER_LEAK"):
        return f"AUDIT_{label}"
    if label == "DUPLICATE_NEAR_COPY":
        return "AUDIT_DUPLICATE"
    if label == "WRONG_SLOT":
        return "AUDIT_WRONG_SLOT"
    if label in ("MULTI_ANSWER", "UNDERSPECIFIED"):
        return f"AUDIT_{label}"
    return None


def snippet_for(conn: sqlite3.Connection, pointer: str, value: str, width: int = 400) -> str:
    kind, _, raw = pointer.partition("#")
    if kind == "tool_calls":
        r = conn.execute(
            "select tool_name, file_path, input_json, substr(result_content,1,20000) from tool_calls where id=?",
            (int(raw),),
        ).fetchone()
        if not r:
            return ""
        blob = "\n".join([r[1] or "", r[2] or "", r[3] or ""])
    else:
        r = conn.execute("select content from messages where id=?", (int(raw),)).fetchone()
        blob = (r[0] if r else "") or ""
    i = blob.find(value)
    if i < 0:
        return blob[:width]
    lo = max(0, i - width // 3)
    return blob[lo : lo + width]


def verify_pointer(conn: sqlite3.Connection, pointer: str, value: str) -> bool:
    return value in snippet_for(conn, pointer, value, width=10**9)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--in", dest="inp", default=".work/real-q-candidates.jsonl")
    ap.add_argument("--out", default=".work/real-q-gated.jsonl")
    ap.add_argument("--critic-input", default=".work/real-q-critic-input.json")
    ap.add_argument("--db", default=DEFAULT_DB)
    ap.add_argument("--primary-slot", default="PATH")
    a = ap.parse_args()

    conn = sqlite3.connect(f"file:{a.db}?mode=ro", uri=True)
    rows = [json.loads(l) for l in Path(a.inp).read_text().splitlines() if l.strip()]
    reasons: Counter = Counter()
    kept: list[dict] = []
    for r in rows:
        q = r["question"]
        kind = r["slot_kind"]
        val = r["value"]
        fail = None
        if fail is None:
            fail = question_is_information_request(q, r.get("question_form") or "action")
        if fail is None and not slot_object_ok(q, kind):
            fail = "Q_SLOT_OBJECT_MISSING"
        if fail is None:
            fail = value_shape_ok(kind, val)
        if fail is None:
            fail = classify_raw(
                {
                    "question": q,
                    "expected_value": val,
                    "slot_kind": kind,
                    "family": "real",
                    "source_tool": r.get("tool_name", ""),
                }
            )
        if fail is None and not verify_pointer(conn, r["evidence_pointer"], val):
            fail = "CITATION_INSUFFICIENT"
        # answer-uniqueness: several *used* rivals with topical overlap
        if fail is None:
            qt = content_tokens(q)
            rivals = []
            for c in r["candidates"]:
                if c["is_truth"]:
                    continue
                if not (c["in_tool_args"] or c["is_file_path"] or c["in_tool_result"]):
                    continue
                if value_shape_ok(kind, c["value"]):
                    continue
                shared = sorted(qt & content_tokens(c["value"]))
                if not shared:
                    continue
                rivals.append({**c, "shared_terms": shared, "topical_overlap": len(shared)})
            r["plausible_rivals"] = rivals
            r["candidate_count"] = len(r["candidates"])
            r["plausible_candidate_count"] = len(rivals) + 1
        if fail is not None:
            r["gate"] = fail
            reasons[fail] += 1
            kept.append(r)
            continue
        r["gate"] = "ACCEPT"
        r["critic"] = None
        kept.append(r)
        reasons["ACCEPT"] += 1

    # Near-duplicate collapse: the same question is asked in several forked or
    # resumed sessions. Keep the best-binding, earliest copy of each distinct turn.
    SCOPE_RANK = {"turn": 0, "session_after": 1, "session_before": 2,
                  "quote_user_before": 3, "quote_assistant_before": 4}
    from mine_real_questions import skeleton

    seen: dict[tuple, dict] = {}
    for r in kept:
        if r["gate"] != "ACCEPT":
            continue
        key = (skeleton(r["question"]), r["slot_kind"])
        rank = (SCOPE_RANK.get(r["grounding_scope"], 9), r["q_ordinal"])
        cur = seen.get(key)
        if cur is None:
            seen[key] = r
        elif rank < (SCOPE_RANK.get(cur["grounding_scope"], 9), cur["q_ordinal"]):
            cur["gate"] = "DEDUP_NEAR_COPY"
            reasons["DEDUP_NEAR_COPY"] += 1
            seen[key] = r
        else:
            r["gate"] = "DEDUP_NEAR_COPY"
            reasons["DEDUP_NEAR_COPY"] += 1
    reasons["ACCEPT_UNIQUE"] = len(seen)

    out = Path(a.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w") as fh:
        for r in kept:
            fh.write(json.dumps(r) + "\n")

    accepted = list(seen.values())
    critic_input = []
    for r in accepted:
        critic_input.append(
            {
                "id": r["id"] if "id" in r else hashlib.sha1(
                    (r["session_id"] + str(r["q_ordinal"])).encode()).hexdigest()[:12],
                "question": r["question"],
                "slot_kind": r["slot_kind"],
                "proposed_answer": r["value"],
                "cited_evidence": {
                    "pointer": r["evidence_pointer"],
                    "tool": r.get("tool_name", ""),
                    "snippet": snippet_for(conn, r["evidence_pointer"], r["value"]),
                },
                "rivals": [
                    {
                        "value": c["value"],
                        "tool_names": c.get("tool_names", []),
                        "shared_terms": c.get("shared_terms", []),
                    }
                    for c in r.get("plausible_rivals", [])[:8]
                ],
            }
        )
    Path(a.critic_input).write_text(json.dumps(critic_input, indent=2))
    print(json.dumps({"total": len(kept), "reasons": reasons.most_common()}, indent=2))
    print(f"accepted -> {a.out}  critic input -> {a.critic_input}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
