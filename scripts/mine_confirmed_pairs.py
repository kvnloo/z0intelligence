#!/usr/bin/env python3
"""LANE B: build the personal ruler from CONFIRMED outcomes only.

The previous binder inferred answerhood from post-question proximity, and an
independent critic rejected 100% of what it produced. That causal assumption is
false and is not used here.

A pair enters this ruler only through one of:

  Tier A  mechanically verified outcome -- the candidate was *consumed* by a real
          command whose success depends on it being the requested value
  Tier B  explicit user confirmation or correction
  Tier C  authoritative source-native relation (git/config/symbol authority)

Tier D (assistant says so; an LLM judge believes it) may be counted but is never
written to the sealed split.

Question wording always comes from the real user turn. Truth never comes from the
assistant's answer text alone.

Ambiguity is preserved: when two values are equally defensible the item goes to
the ambiguity bucket, not to ground truth.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sqlite3
import subprocess
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

from gate_real_questions import (  # noqa: E402
    question_is_information_request,
    slot_object_ok,
    value_shape_ok,
)
from mine_real_questions import (  # noqa: E402
    DEFAULT_DB,
    assistant_kind,
    load_session,
    question_form,
    skeleton,
    slot_values,
)

#: Harness-injected blocks are not the user's question.
INJECTED = re.compile(
    r"<in-app-browser-context|^\s*\[Replying to:|^\s*json:\[|\[The user sent a|"
    r"\[CONTEXT COMPACTION|REFERENCE ONLY\]|^\s*\[Voice input|Your active task list was preserved",
    re.I,
)
PLACEHOLDER = re.compile(r"\bexample\.(com|org|net)\b|client\.example|/nonexistent|/var/invented|/mnt/fake")
#: Generic directory words do not establish that a path answers a question.
GENERIC = frozenset(
    "scripts script workspace zer0 docs doc src lib bin tools tool config configs "
    "products product home user users tmp data cache venv python bin ops files file".split()
)
EXEC_TOOLS = re.compile(r"^(exec|terminal|bash|exec_command|run_terminal_command|process|shell)$", re.I)
FETCH_HINT = re.compile(r"\b(curl|wget|http|https)\b", re.I)
#: A command is treated as *succeeded* unless its own output says otherwise.
FAIL = re.compile(
    r"command not found|No such file or directory|not recognized|cannot access|"
    r"exit code:? [1-9]|exited with code [1-9]|-> exit [1-9]|Traceback \(most recent|No such file|"
    r"permission denied|Connection refused|Could not resolve host|HTTP/\S+ [45]\d\d|"
    r"\b404\b|\b500\b|\b403\b",
    re.I,
)
#: A fetch is "consumed successfully" only on a real status line, not on the
#: number 200 appearing somewhere in the output.
HTTP_OK = re.compile(r"HTTP/[\d.]\s+2\d\d", re.I)
RUNNERS = re.compile(
    r"(?:^|[;&|]\s*|\bthen\s+)(?:sudo\s+)?(?:bash|sh|zsh|python3?|node|deno|bun|uv|ruby|perl)\s+$"
)
#: "no, I meant X" / "that's not the one" / "yes exactly" -- corrections are the
#: highest-value Tier-B signal because they carry a rejection *and* an acceptance.
CORRECTION = re.compile(
    r"\b(no,? (?:that'?s|thats|this is|it'?s|its) not|that'?s not (?:the|what)|i meant|"
    r"i did not mean|i didn'?t mean|not that one|the other (?:one|file|path|repo|branch|url)|"
    r"wrong (?:file|path|one|repo|branch)|not what i (?:asked|meant|wanted|said)|no,? i (?:said|meant))\b",
    re.I,
)
CONFIRMATION = re.compile(
    r"^\s*(?:yes[,!.]?\s*)?(?:that'?s (?:the one|it|correct|right|exactly)|exactly|"
    r"yes,? (?:exactly|that'?s the one|that'?s it|correct)|perfect,? that'?s|right,? that'?s|"
    r"that is (?:the one|correct|right))\b",
    re.I,
)
SLOT_Q = {
    "PATH": re.compile(r"\b(where|which file|what file|path|location|located|stored|saved)\b", re.I),
    "URL": re.compile(r"\b(url|link|endpoint|port|localhost|address)\b", re.I),
}


@dataclass
class Item:
    question: str
    slot: str
    correct: str
    confirmation_type: str
    confidence_tier: str
    evidence_refs: list[str] = field(default_factory=list)
    rejected_candidates: list[str] = field(default_factory=list)
    session_id: str = ""
    harness: str = ""
    project: str = ""
    q_ordinal: int = 0
    detail: dict = field(default_factory=dict)


def argv_paths(cmd: str) -> list[str]:
    """Paths in command position: executed, not passed as data."""
    out: list[str] = []
    for m in re.finditer(r"[^\s;&|'\"]+", cmd):
        tok = m.group(0)
        if not tok.startswith(("/", "~", "./")) or "/" not in tok:
            continue
        before = cmd[: m.start()]
        tail = re.split(r"[;&|]", before)[-1].strip()
        if re.search(r"(^|\s)cd\s*$", tail):
            continue
        if re.search(r"\b(grep|cat|ls|git|rg|sed|awk|find|echo|cp|mv|rm|diff|wc|head|tail)\b[^;&|]*$", tail):
            continue
        if RUNNERS.search(before) or tail == "" or tail.endswith(("&&", "||", ";")):
            out.append(tok)
    return out


def artifact_terms(question: str) -> set[str]:
    from mine_real_questions import content_tokens

    return {t for t in content_tokens(question) if len(t) > 3}


def path_answers_question(path: str, question: str) -> list[str]:
    """Terms that tie the path to the artifact the question names.

    Strict on purpose. A shared *directory* word ("scripts", "workspace",
    "hermes") is not evidence that this file is the one being asked about, and
    neither is a term that only appears in a parent directory. The term has to be
    distinctive AND live in the path's own basename -- the file that would be
    executed. Everything weaker produced 47 "verified" pairs of which an
    independent read found zero valid.
    """
    qt = {t for t in artifact_terms(question) if t not in GENERIC and len(t) > 4}
    base = path.rstrip("/").rsplit("/", 1)[-1].lower()
    parts = {p for p in re.split(r"[_.\-]+", base) if len(p) > 2}
    return sorted(qt & parts)


def clean_url(u: str) -> str | None:
    """Reject values that are extraction artifacts rather than URLs."""
    if any(c in u for c in ("\n", "\t", "`", "<", ">", "\\")):
        return None
    u = u.rstrip(".,;:'\")]}")
    if PLACEHOLDER.search(u):
        return None
    if not re.match(r"^https?://[^\s/]+", u):
        return None
    m = re.match(r"^(https?://[^/\s]+)(/.*)?$", u)
    if not m:
        return None
    host, rest = m.group(1), (m.group(2) or "")
    # `/google/callback` parsed out of a URL is not a PATH answer either
    if not rest or rest == "/":
        return None
    return host + rest


def mine_tier_a(msgs, tcs, item_sink, reject_sink, meta) -> None:
    users = [(m["ordinal"], m["content"] or "") for m in msgs if m["role"] == "user" and not m["has_tool_use"]]
    if not users:
        return
    for t in tcs:
        cmd = t["input"] or ""
        res = t["result"] or ""
        if not cmd or not res or FAIL.search(res[:2000]):
            continue
        prev = [(o, txt) for o, txt in users if o < t["ordinal"]]
        if not prev:
            continue
        for slot, rx in SLOT_Q.items():
            qo, q = None, None
            for o, txt in reversed(prev):
                form = question_form(txt)
                if (
                    form
                    and not INJECTED.search(txt)
                    and rx.search(txt)
                    and slot_object_ok(txt, slot)
                    and question_is_information_request(txt, form) is None
                ):
                    qo, q = o, txt
                    break
            if q is None:
                reject_sink[f"TIER_A_NO_{slot}_QUESTION"] += 1
                continue
            raw_cands = argv_paths(cmd) if slot == "PATH" else slot_values(cmd, "URL")
            for cand in raw_cands:
                if slot == "URL":
                    cand = clean_url(cand) or ""
                if not cand or value_shape_ok(slot, cand) or PLACEHOLDER.search(cand):
                    continue
                if cand in q:
                    continue  # answer leaked into the question
                # -- discovery must follow the question
                disc = None
                for t2 in tcs:
                    if t2["ordinal"] >= t["ordinal"]:
                        break
                    if t2["ordinal"] <= qo:
                        continue
                    if cand in "\n".join([t2["result"] or "", t2["input"] or "", t2["file_path"] or ""]):
                        disc = t2
                        break
                if disc is None:
                    continue
                # -- and the candidate must answer the question, not merely run
                if slot == "PATH":
                    shared = path_answers_question(cand, q)
                else:
                    qt = {t for t in artifact_terms(q) if t not in GENERIC and len(t) > 4}
                    m2 = re.match(r"^https?://([^/]+)(/.*)?$", cand)
                    host = re.sub(r"^[^.]+\.|:\d+$", "", m2.group(1)) if m2 else ""
                    tail = (m2.group(2) or "") if m2 else ""
                    shared = sorted(qt & ({p for p in re.split(r"[_.\-]+", host + tail) if len(p) > 2}))
                if not shared:
                    reject_sink["TIER_A_NO_QUESTION_MATCH"] += 1
                    continue
                if slot == "URL" and not (FETCH_HINT.search(cmd) and HTTP_OK.search(res[:2000])):
                    reject_sink["TIER_A_NO_SUCCESS_RECEIPT"] += 1
                    continue
                item_sink.append(
                    Item(
                        question=q.strip(), slot=slot, correct=cand,
                        confirmation_type="verified_execution",
                        confidence_tier="A",
                        evidence_refs=[f"tool_calls#{disc['id']}", f"tool_calls#{t['id']}"],
                        session_id=meta["session_id"], harness=meta["agent"],
                        project=meta["project"], q_ordinal=qo,
                        detail={
                            "exec_tool": t["tool_name"], "exec_command": cmd[:300],
                            "receipt_head": res[:300], "shared_terms": shared,
                            "discovery_pointer": f"tool_calls#{disc['id']}",
                        },
                    )
                )
                reject_sink["TIER_A_ACCEPTED"] += 1


def mine_tier_b(msgs, item_sink, reject_sink, meta) -> None:
    KINDS = ("PATH", "URL", "REVISION", "MODEL", "CONFIG_VALUE", "COMMAND", "ISSUE", "REPOSITORY")
    for i, m in enumerate(msgs):
        if m["role"] != "user" or m["has_tool_use"]:
            continue
        text = m["content"] or ""
        if not (4 <= len(text) <= 500):
            continue
        is_corr = bool(CORRECTION.search(text))
        is_conf = bool(CONFIRMATION.search(text))
        if not (is_corr or is_conf):
            continue
        prev = None
        for j in range(i - 1, -1, -1):
            if msgs[j]["role"] == "assistant" and assistant_kind(msgs[j]["content"] or "") == "answer":
                prev = msgs[j]
                break
        if prev is None:
            reject_sink["TIER_B_NO_PRIOR_ANSWER"] += 1
            continue
        prev_vals = {}
        for k in KINDS:
            vs = slot_values(prev["content"] or "", k)
            if vs:
                prev_vals[k] = vs
        if not prev_vals:
            reject_sink["TIER_B_NO_PRIOR_CANDIDATE"] += 1
            continue
        # the confidence statement must be about one slot with one candidate
        singles = {k: v for k, v in prev_vals.items() if len(v) == 1}
        if len(singles) != 1:
            reject_sink["TIER_B_AMBIGUOUS_PRIOR"] += 1
            continue
        slot, (cand,) = next(iter(singles.items()))
        what = "correction" if is_corr else "confirmation"
        # a correction must name the accepted value, else the accepted value is
        # unknown and the item is only a hard negative
        if is_corr:
            new_vals = [v for v in slot_values(text, slot) if v != cand]
            q = None
            for j in range(i - 1, -1, -1):
                if msgs[j]["role"] == "user" and not msgs[j]["has_tool_use"]:
                    q = msgs[j]["content"] or ""
                    break
            if not new_vals:
                reject_sink["TIER_B_CORRECTION_WITHOUT_ACCEPTED_VALUE"] += 1
                continue
            if len(set(new_vals)) > 1:
                reject_sink["TIER_B_MULTIPLE_ACCEPTED"] += 1
                continue
            if q is None or not question_form(q):
                reject_sink["TIER_B_NO_QUESTION"] += 1
                continue
            item_sink.append(
                Item(question=q.strip(), slot=slot, correct=new_vals[0],
                     confirmation_type="user_correction", confidence_tier="B",
                     evidence_refs=[f"messages#{prev['id']}", f"messages#{m['id']}"],
                     rejected_candidates=[cand],
                     session_id=meta["session_id"], harness=meta["agent"],
                     project=meta["project"], q_ordinal=m["ordinal"],
                     detail={"correction_text": text[:300]})
            )
            reject_sink["TIER_B_CORRECTION_ACCEPTED"] += 1
        else:
            q = None
            for j in range(i - 1, -1, -1):
                if msgs[j]["role"] == "user" and not msgs[j]["has_tool_use"] and msgs[j]["ordinal"] < prev["ordinal"]:
                    q = msgs[j]["content"] or ""
                    break
            if q is None or not question_form(q):
                reject_sink["TIER_B_NO_QUESTION"] += 1
                continue
            item_sink.append(
                Item(question=q.strip(), slot=slot, correct=cand,
                     confirmation_type="user_confirmation", confidence_tier="B",
                     evidence_refs=[f"messages#{prev['id']}", f"messages#{m['id']}"],
                     session_id=meta["session_id"], harness=meta["agent"],
                     project=meta["project"], q_ordinal=m["ordinal"],
                     detail={"confirmation_text": text[:200]})
            )
            reject_sink["TIER_B_CONFIRMATION_ACCEPTED"] += 1


def mine_tier_c(msgs, item_sink, reject_sink, meta) -> None:
    """Source-native relations, verified against the live authority right now.

    Nothing here consults an assistant message. The relation is defined by the
    source: git defines ref -> commit, the filesystem defines a symbol's
    definition file, a manifest defines a pinned version.
    """
    for m in msgs:
        if m["role"] != "user" or m["has_tool_use"]:
            continue
        q = (m["content"] or "").strip()
        if not question_form(q) or len(q) > 300:
            continue
        mg = re.search(r"\b(?:what|which|what'?s)\s+(?:the\s+)?(git\s+)?(commit|sha|revision|branch)\b", q, re.I)
        if not mg:
            continue
        want = mg.group(2).lower()
        repo = re.search(r"(/[\w.\-/]{4,})", q)
        if not repo:
            reject_sink["TIER_C_NO_REPO_IN_QUESTION"] += 1
            continue
        d = Path(repo.group(1).rstrip("/."))
        if not (d / ".git").exists():
            reject_sink["TIER_C_REPO_MISSING"] += 1
            continue
        argv = ["git", "-C", str(d), "branch", "--show-current"] if want == "branch" \
            else ["git", "-C", str(d), "rev-parse", "HEAD"]
        try:
            out = subprocess.run(argv, capture_output=True, text=True, timeout=10)
        except Exception:
            reject_sink["TIER_C_GIT_ERROR"] += 1
            continue
        value = out.stdout.strip()
        if out.returncode != 0 or not value:
            reject_sink["TIER_C_GIT_NO_VALUE"] += 1
            continue
        # The authority supplies the value. The archive's own answer is used ONLY
        # as a temporal-stability check: for a "current state" question the live
        # value is the truth only if it has not moved since. Disagreement means
        # the question is stale, not that the assistant was wrong.
        said = None
        for later in msgs:
            if later["ordinal"] <= m["ordinal"] or later["role"] != "assistant":
                continue
            vs = slot_values(later["content"] or "", "REVISION") if want != "branch" else [
                x for x in re.findall(r"[A-Za-z0-9._\-/]+", later["content"] or "")
                if x.strip() == value or x.strip() == value]
            if value in (later["content"] or ""):
                said = value
                break
        if said is None:
            reject_sink["TIER_C_NO_STABILITY_EVIDENCE"] += 1
            continue
        item_sink.append(
            Item(question=q, slot="REVISION", correct=value,
                 confirmation_type="source_relation", confidence_tier="C",
                 evidence_refs=[f"authority: {' '.join(argv[1:])}"],
                 session_id=meta["session_id"], harness=meta["agent"],
                 project=meta["project"], q_ordinal=m["ordinal"],
                 detail={"authority": "git", "cwd": str(d), "asked_for": want,
                         "observed_at": "mining_time",
                         "stability": "live value also appears later in the same session"})
        )
        reject_sink["TIER_C_ACCEPTED"] += 1


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=DEFAULT_DB)
    ap.add_argument("--out", default=".work/confirmed-ruler.jsonl")
    ap.add_argument("--ambiguous-out", default=".work/confirmed-ambiguous.jsonl")
    ap.add_argument("--stats-out", default=".work/confirmed-stats.json")
    a = ap.parse_args()

    conn = sqlite3.connect(f"file:{a.db}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    # Tier C reads only the user turn and the live authority, so it is scanned
    # across every session, not just tool-using ones. It costs nothing.
    sess = conn.execute(
        "select id,agent,project,message_count from sessions "
        "where coalesce(is_automated,0)=0 and deleted_at is null "
        "and message_count>=4 and rowid in (select min(rowid) from sessions group by id)"
    ).fetchall()

    items: list[Item] = []
    rejects: Counter = Counter()
    tier_d_excluded = 0
    for si, s in enumerate(sess):
        if si % 500 == 0:
            print(f"..{si}/{len(sess)} items={len(items)}", file=sys.stderr)
        msgs, tcs = load_session(conn, s["id"])
        if not msgs:
            continue
        meta = {"session_id": s["id"], "agent": s["agent"], "project": s["project"] or ""}
        mine_tier_a(msgs, tcs, items, rejects, meta)
        mine_tier_b(msgs, items, rejects, meta)
        mine_tier_c(msgs, items, rejects, meta)
        # Tier D: an assistant narration naming a slot value shortly after a
        # question. Counted for the report, never used as truth.
        for t in tcs:
            if t["input"] and any(v in t["input"] for v in slot_values(t["input"], "PATH")):
                tier_d_excluded += 1
                break

    # -- ambiguity first (B5): one question with several equally consumed
    # candidates is not ground truth, it is an abstention example.
    by_q: dict[tuple, list[Item]] = defaultdict(list)
    for it in items:
        by_q[(skeleton(it.question), it.slot)].append(it)
    ambiguous: list[dict] = []
    kept: list[Item] = []
    for (qk, slot), group in by_q.items():
        values = sorted({g.correct for g in group})
        if len(values) > 1:
            base = group[0]
            ambiguous.append(
                {
                    "question": base.question,
                    "slot": slot,
                    "candidates": values,
                    "reason": "MULTIPLE_VERIFIED_CANDIDATES",
                    "session_id": base.session_id,
                    "harness": base.harness,
                    "rejected_candidates": [],
                    "confidence_tier": "AMBIGUOUS",
                }
            )
            continue
        kept.append(group[0])

    # -- de-duplicate near copies (same question skeleton + same correct value)
    seen: set[tuple] = set()
    uniq: list[Item] = []
    dupes = 0
    for it in kept:
        key = (skeleton(it.question), it.slot, it.correct)
        if key in seen:
            dupes += 1
            continue
        seen.add(key)
        uniq.append(it)

    out = Path(a.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w") as fh:
        for it in uniq:
            fh.write(json.dumps(it.__dict__) + "\n")
    with Path(a.ambiguous_out).open("w") as fh:
        for r in ambiguous:
            fh.write(json.dumps(r) + "\n")

    tiers = Counter(it.confidence_tier for it in uniq)
    slots = Counter(it.slot for it in uniq)
    kinds = Counter(it.confirmation_type for it in uniq)
    stats = {
        "sessions_scanned": len(sess),
        "tier_A": tiers.get("A", 0),
        "tier_B": tiers.get("B", 0),
        "tier_C": tiers.get("C", 0),
        "tier_D_excluded": tier_d_excluded,
        "correction_pairs": kinds.get("user_correction", 0),
        "verified_execution_pairs": kinds.get("verified_execution", 0),
        "duplicates_removed": dupes,
        "ambiguous": len(ambiguous),
        "final": len(uniq),
        "slot_distribution": dict(slots),
        "confirmation_types": dict(kinds),
        "reject_reasons": dict(rejects.most_common()),
        "dataset_sha256": hashlib.sha256(
            "".join(json.dumps(it.__dict__, sort_keys=True) + "\n" for it in uniq).encode()
        ).hexdigest(),
    }
    Path(a.stats_out).write_text(json.dumps(stats, indent=2))
    print(json.dumps(stats, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
