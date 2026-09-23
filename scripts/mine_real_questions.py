#!/usr/bin/env python3
"""Mine REAL user questions from indexed session history and bind each to a
downstream source fact.

Why this exists
---------------
The synthetic generators (`gen_resolve_eval.py`, `gen_resolve_eval_v2.py`)
derive the question from the answer's own tokens, so the question structurally
echoes the answer (PREFIX_ECHO), or -- deriving from schema keys instead --
produces answer-dependent token salad. Both failure modes are structural, not
bugs. The replacement ruler takes the question from a source that never saw the
answer: the user's own turn.

Binding contract
----------------
    question_seq = q                       (real user turn, unmodified)
    answer_evidence_seq > q                (downstream, same session)
    truth established by an INDEPENDENT signal, not by topical overlap

Truth signal used here: the first assistant *text* message inside the turn that
names exactly one slot-shaped candidate, where that candidate is also present in
tool evidence acquired earlier in the same turn. If that assistant message names
more than one candidate the pair is AMBIGUOUS and is not bound.

The truth signal is deliberately NOT the resolver's signal (topical overlap) and
is deliberately not the provenance signal under test (read/edit/tool-arg). That
way the Part 7 provenance experiment is not tautological.

Nothing is silently discarded: every proposed pair leaves with a reason.
"""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

from eval_audit import (  # noqa: E402
    BARE_TOOL,
    MECHANICAL,
    SCRATCH,
    STOP,
    WH,
    classify,
    prefix_echo,
    skeleton,
)
from z0int.context_resolve import SLOT_PATTERNS, infer_slots  # noqa: E402

DEFAULT_DB = "/mnt/zer0models/sft-svlm/data/agentsview/sessions.db"

#: Harness boilerplate and automated dispatcher turns are not user questions.
NOISE = re.compile(
    r"REASONING EFFORT|^\[kanban\]|automatic task-status|You are a worker agent|"
    r"^\s*<[a-z_]+>|^\[image unavailable\]$|^Caveat:|^\s*#\s*AGENTS\.md",
    re.I | re.M,
)
#: Tools whose call means the candidate file was *used*, not merely mentioned.
EDIT_TOOLS = re.compile(r"edit|write|apply_patch|str_replace|create|multiedit|patch", re.I)
READ_TOOLS = re.compile(r"read|view|cat|open|fetch|grep|glob|search|list", re.I)

#: AgentsView folds the raw tool invocation into the *assistant* message for
#: several harnesses (`[Bash]\nconst r = await tools.exec_command({...})`). 74% of
#: assistant messages in this archive are that echo. Reading them as "what the
#: assistant answered" was the single largest defect in the first mining pass: it
#: made tool *arguments* look like answers and manufactured fake ambiguity.
#: `tool_calls.input_json` carries the same information, so echoes are dropped.
#: Two shapes: `[Bash]` on its own line (codex), and `[Read: path:1-20]` /
#: `[Edit: path]` (omp). Both are the harness recording the tool call, not the
#: assistant answering the user.
TOOL_ECHO = re.compile(
    r"\[(Bash|Read|Write|Edit|MultiEdit|Grep|Glob|exec|exec_command|run_terminal_command|"
    r"apply_patch|patch|search_files|read_file|write_file|terminal|process|skill_view|"
    r"vision_analyze|view_image|Task|WebFetch|WebSearch|TodoWrite|wait_agent|update_plan)"
    r"\s*(?::[^\]]*)?\]")
ECHO_JS = re.compile(r"await tools\.|tools\.exec_command\(|tools\.\w+\(\{|const r\s*=\s*await")
THINKING = re.compile(r"^\s*\[Thinking\]")


def assistant_kind(text: str) -> str:
    t = text or ""
    if TOOL_ECHO.search(t) or ECHO_JS.search(t):
        return "tool_echo"
    if THINKING.match(t):
        return "thinking"
    return "answer"

EXTRACTABLE = tuple(k for k, v in SLOT_PATTERNS.items() if v is not None)

#: Mining cues are a deliberate SUPERSET of the resolver's `SLOT_CUES`. The ruler
#: may not be limited by the vocabulary of the thing it measures, or "the
#: resolver failed to infer the slot at all" could never show up as a failure.
#: Slot correctness is still enforced by shape agreement in the gate, and the
#: resolver's own inference is recorded per row as `resolver_slot`.
MINE_CUES: tuple[tuple[str, re.Pattern[str]], ...] = (
    # PATH is LAST on purpose: "where" is generic and would otherwise steal
    # URL / REVISION / ISSUE questions that happen to start with it.
    ("MODEL", re.compile(r"\b(which|what)\s+(model|models|llm|checkpoint|weights)\b|\bmodel (id|name|did)\b", re.I)),
    ("URL", re.compile(r"\b(url|uri|endpoint|link|port|localhost|host:port|gateway|tailscale)\b", re.I)),
    ("COMMAND", re.compile(r"\b(which|what)\s+command\b|\bcommand (fixed|ran|to run|did)\b|\bhow (do|would) (i|we) (run|invoke|compress|install|start)\b|\bwhat did you run\b|\bwhat'?s the command\b", re.I)),
    ("REVISION", re.compile(r"\b(commit|revision|sha|branch|tag|pinned version|version pin|what version)\b", re.I)),
    ("ISSUE", re.compile(r"\b(issue|pull request|\bpr\b|ticket)\b", re.I)),
    ("REPOSITORY", re.compile(r"\b(which|what)\s+(repo|repository)\b|\brepo(sitory)? (name|url|is)\b", re.I)),
    ("CONFIG_VALUE", re.compile(r"\b(config|setting|backend|env(?:ironment)? variable|flag|default|which .{0,20}backend|which harness|what harness|which provider)\b", re.I)),
    ("IDENTIFIER", re.compile(r"\b(which|what)\s+(identifier|symbol|function|helper|module|variable|key|id)\b", re.I)),
    ("PATH", re.compile(
        r"\b(where|which file|what file|which one|path|location of|stored|saved|live[sd]? on|"
        r"on disk|directory|folder|worktree|cwd|which dir)\b", re.I)),
)


def mine_slot(question: str) -> str | None:
    for kind, rx in MINE_CUES:
        if rx.search(question):
            return kind
    return None


@dataclass
class Ev:
    """One piece of downstream evidence in the turn."""

    ordinal: int
    pointer: str
    source: str  # file_path | tool_arg | tool_result | assistant
    tool_name: str
    text: str


@dataclass
class Pair:
    session_id: str
    agent: str
    project: str
    question: str
    q_ordinal: int
    slot_kind: str
    value: str
    resolver_slot: str | None
    question_form: str
    answer_ordinal: int
    answer_excerpt: str
    evidence_pointer: str
    evidence_ordinal: int
    tool_name: str
    grounding_scope: str
    candidates: list[dict] = field(default_factory=list)
    reason: str = "VALID"


URL_SPAN = re.compile(r"https?://[^\s\"'`<>)\]}]+")


def slot_values(text: str, kind: str, limit: int = 24) -> list[str]:
    rx = SLOT_PATTERNS.get(kind)
    if rx is None or not text:
        return []
    spans = [m.span() for m in URL_SPAN.finditer(text)]
    out: list[str] = []
    for m in rx.finditer(text):
        v = m.group(0).strip()
        # `http://localhost:5173/dev.html` is a URL, not a path answer; without
        # this the PATH extractor harvests `/dev.html` out of every link.
        if kind == "PATH" and any(a <= m.start() and m.end() <= b for a, b in spans):
            continue
        if v and v not in out:
            out.append(v)
        if len(out) >= limit:
            break
    return out


def clean_question(raw: str) -> str:
    t = (raw or "").replace("\r", "\n")
    t = re.sub(r"\[image unavailable\]\s*", "", t)
    t = re.sub(r"<[^>]{0,40}>", " ", t)
    t = re.sub(r"[ \t]+", " ", t)
    t = re.sub(r"\n{3,}", "\n\n", t).strip()
    return t


WH_OPEN = re.compile(r"^\s*(where|which|what|who|when|why|how)\b", re.I)
AUX_OPEN = re.compile(r"^\s*(is|are|was|were|do|does|did|has|have|had)\b", re.I)
#: Template/code artefacts. NOTE: deliberately not `eval_audit.CODEY`, which
#: matches any newline and would silently reject every multi-line user question.
ARTEFACT = re.compile(r"```|\{\{|\}\}|=>|<tool_use|<function_calls|\[image unavailable\]")


def question_form(q: str) -> str | None:
    """`wh` / `aux` / `action` -- or None when this is not an utterance at all."""
    if not (12 <= len(q) <= 600):
        return None
    if NOISE.search(q):
        return None
    if ARTEFACT.search(q):
        return None
    if WH_OPEN.match(q):
        return "wh"
    if AUX_OPEN.match(q):
        return "aux"
    if "?" in q:
        return "action"
    return None


def question_ok(q: str) -> bool:
    return question_form(q) is not None


def load_session(c: sqlite3.Connection, sid: str) -> tuple[list[dict], list[dict]]:
    msgs = [
        {"id": r[0], "ordinal": r[1], "role": r[2], "content": r[3], "has_tool_use": r[4]}
        for r in c.execute(
            "select id,ordinal,role,content,has_tool_use from messages "
            "where session_id=? and is_system=0 order by ordinal",
            (sid,),
        )
    ]
    ord_by_mid = {m["id"]: m["ordinal"] for m in msgs}
    tcs = []
    for r in c.execute(
        "select id,message_id,tool_name,file_path,input_json,substr(result_content,1,8000) "
        "from tool_calls where session_id=? order by id",
        (sid,),
    ):
        tcs.append(
            {
                "id": r[0],
                "ordinal": ord_by_mid.get(r[1], -1),
                "tool_name": r[2] or "",
                "file_path": r[3],
                "input": r[4] or "",
                "result": r[5] or "",
            }
        )
    tcs.sort(key=lambda t: (t["ordinal"], t["id"]))
    return msgs, tcs


def turn_evidence(msgs: list[dict], tcs: list[dict], q_ord: int, end_ord: int) -> list[Ev]:
    evs: list[Ev] = []
    for t in tcs:
        if not (q_ord < t["ordinal"] <= end_ord):
            continue
        if t["file_path"]:
            evs.append(Ev(t["ordinal"], f"tool_calls#{t['id']}", "file_path", t["tool_name"], t["file_path"]))
        if t["input"]:
            evs.append(Ev(t["ordinal"], f"tool_calls#{t['id']}", "tool_arg", t["tool_name"], t["input"]))
        if t["result"]:
            evs.append(Ev(t["ordinal"], f"tool_calls#{t['id']}", "tool_result", t["tool_name"], t["result"]))
    for m in msgs:
        if q_ord < m["ordinal"] <= end_ord and m["role"] == "assistant" and (m["content"] or "").strip():
            ak = assistant_kind(m["content"])
            if ak == "tool_echo":
                continue  # already represented by tool_calls.input_json
            evs.append(Ev(m["ordinal"], f"messages#{m['id']}", "assistant" if ak == "answer" else "thinking", "", m["content"]))
    evs.sort(key=lambda e: e.ordinal)
    return evs


def content_tokens(text: str) -> set[str]:
    return {
        t.lower()
        for t in re.findall(r"[A-Za-z0-9_][\w.\-]{2,}", text or "")
        if t.lower() not in STOP
    }


def overlap(qtoks: set[str], value: str) -> tuple[int, list[str]]:
    vt = content_tokens(value)
    # also credit the path segments / stem, which is how a real reader matches
    parts = {p.lower() for p in re.split(r"[/_.\-]+", value) if len(p) > 2} - STOP
    shared = sorted((qtoks & (vt | parts)))
    return len(shared), shared


def propose(c: sqlite3.Connection, sess: sqlite3.Row, max_pairs: int = 200) -> tuple[list[Pair], list[dict], Counter]:
    sid, agent = sess["id"], sess["agent"]
    project = sess["project"] or ""
    msgs, tcs = load_session(c, sid)
    if not msgs:
        return [], [], Counter()
    multi_records: list[dict] = []
    user_ords = [m["ordinal"] for m in msgs if m["role"] == "user" and not m["has_tool_use"]]
    pairs: list[Pair] = []
    reasons: Counter = Counter()
    for m in msgs:
        if m["role"] != "user" or m["has_tool_use"]:
            continue
        if len(pairs) >= max_pairs:
            break
        raw = m["content"] or ""
        q = clean_question(raw)
        q_ord = m["ordinal"]
        form = question_form(q)
        if form is None:
            reasons["Q_UNNATURAL_OR_EMPTY"] += 1
            continue
        slots = infer_slots(q)
        resolver_slot = slots[0].kind if slots else None
        kind = mine_slot(q)
        if kind is None:
            reasons["Q_NO_SLOT_CUE"] += 1
            continue
        if kind not in EXTRACTABLE:
            reasons["Q_SLOT_NOT_EXTRACTABLE"] += 1
            continue
        # The answer window tolerates ONE user interjection ("wait", "ok") but
        # not a topic change: evidence is bounded by the second following user
        # turn. The question is unmodified, so the binding stays causal.
        nxt = [o for o in user_ords if o > q_ord]
        end_ord = (nxt[1] - 1) if len(nxt) > 1 else 10**9
        evs = turn_evidence(msgs, tcs, q_ord, end_ord)
        if not evs:
            reasons["NO_DOWNSTREAM_EVIDENCE"] += 1
            continue

        # ---- independent truth signal: the turn's LAST assistant text message
        # naming exactly one candidate. Not topical overlap; not the provenance
        # signal under test. More than one candidate named -> AMBIGUOUS, not bound.
        vmap: dict[str, list[Ev]] = defaultdict(list)
        for e in evs:
            for v in slot_values(e.text, kind):
                vmap[v].append(e)
        if not vmap:
            reasons["NO_CANDIDATE_VALUE"] += 1
            continue
        truth = None
        answer_ev = None
        multi_vals: list[str] = []
        for e in reversed([x for x in evs if x.source == "assistant"]):  # narration only
            vals = sorted({v for v in slot_values(e.text, kind) if v in vmap})
            if not vals:
                continue
            if len(vals) == 1:
                truth, answer_ev = vals[0], e
            else:
                reasons["MULTI_CANDIDATE_ANSWER"] += 1
                multi_vals = vals
            break
        # An ambiguous answer is not scored, but it is the only place real
        # candidate ambiguity can be observed, so it is kept for Part 6.
        if multi_vals and not truth:
            multi_records.append(
                {
                    "session_id": sid, "agent": agent, "project": project,
                    "question": q, "q_ordinal": q_ord, "slot_kind": kind,
                    "multi_values": multi_vals,
                    "answer_ordinal": next(
                        (x.ordinal for x in reversed(evs) if x.source == "assistant" and
                         any(v in x.text for v in multi_vals)), -1),
                    "answer_excerpt": next(
                        (x.text[:800] for x in reversed(evs) if x.source == "assistant" and
                         any(v in x.text for v in multi_vals)), ""),
                    "candidates": [],
                }
            )
        if truth is None or answer_ev is None:
            if not any(e.source == "assistant" for e in evs):
                reasons["NO_ASSISTANT_ANSWER_TEXT"] += 1
            elif not multi_vals:
                reasons["NO_SINGLE_ANSWER_IN_TEXT"] += 1
            continue

        # Truth must be grounded in non-assistant evidence. Scope is ranked and
        # recorded, because a fact the USER supplied earlier and later asked
        # about is a different (and legitimate) memory question from one the
        # agent looked up in a tool.
        tool_hits = [e for e in vmap[truth] if e.source in ("file_path", "tool_arg", "tool_result")]
        grounding_scope = "turn"
        if not tool_hits:
            later, earlier = [], []
            for t in tcs:
                blob = "\n".join([t["file_path"] or "", t["input"] or "", t["result"] or ""])
                if truth not in blob:
                    continue
                src = "file_path" if truth in (t["file_path"] or "") else (
                    "tool_arg" if truth in (t["input"] or "") else "tool_result")
                ev = Ev(t["ordinal"], f"tool_calls#{t['id']}", src, t["tool_name"], blob[:400])
                (later if t["ordinal"] > q_ord else earlier).append(ev)
            if later:
                tool_hits, grounding_scope = later, "session_after"
            elif earlier:
                tool_hits, grounding_scope = earlier, "session_before"
        if not tool_hits:
            # A fact the user (or an earlier assistant turn) supplied and that is
            # asked about later is a legitimate memory question, not a tool one.
            # The scope label keeps the two classes separable in the report.
            prior = [
                Ev(m["ordinal"], f"messages#{m['id']}",
                   "user_text" if m["role"] == "user" else "assistant_text", "",
                   m["content"][:400])
                for m in msgs
                if m["ordinal"] < q_ord and m["role"] in ("user", "assistant")
                and truth in (m["content"] or "")
            ]
            if prior:
                tool_hits, grounding_scope = prior[-1:], (
                    "quote_user_before" if prior[-1].source == "user_text"
                    else "quote_assistant_before")
        if not tool_hits:
            reasons["ANSWER_NOT_TOOL_GROUNDED"] += 1
            continue
        grounding = tool_hits[0]

        # ---- gates
        ql = q.lower()
        base = truth.rsplit("/", 1)[-1]
        if truth.lower() in ql or (len(base) > 5 and base.lower() in ql):
            reasons["ANSWER_LEAK"] += 1
            continue
        if prefix_echo(q, truth):
            reasons["PREFIX_ECHO"] += 1
            continue
        if SCRATCH.match(truth):
            reasons["MULTI_ANSWER_SCRATCH"] += 1
            continue

        qtoks = content_tokens(q)
        if len(qtoks) < 2:
            reasons["UNDERSPECIFIED"] += 1
            continue

        # ---- candidate ambiguity profile
        first_seen: dict[str, int] = {}
        for v, es in vmap.items():
            first_seen[v] = min(e.ordinal for e in es)
        cands = []
        plausible = 0
        for v, es in vmap.items():
            n_ov, shared = overlap(qtoks, v)
            is_plausible = n_ov > 0 or v == truth
            if is_plausible and v != truth:
                plausible += 1
            cands.append(
                {
                    "value": v,
                    "is_truth": v == truth,
                    "sources": sorted({e.source for e in es}),
                    "tool_names": sorted({e.tool_name for e in es if e.tool_name}),
                    "first_seen_ordinal": first_seen[v],
                    "distance_from_question": first_seen[v] - q_ord,
                    "in_tool_args": any(e.source == "tool_arg" for e in es),
                    "is_file_path": any(e.source == "file_path" for e in es),
                    "in_tool_result": any(e.source == "tool_result" for e in es),
                    "edited": any(e.source in ("tool_arg", "file_path") and EDIT_TOOLS.search(e.tool_name) for e in es),
                    "read": any(e.source in ("tool_arg", "file_path") and READ_TOOLS.search(e.tool_name) for e in es),
                    "assistant_mentioned": any(e.source == "assistant" for e in es),
                    "topical_overlap": n_ov,
                    "shared_terms": shared,
                    "evidence_pointer": es[0].pointer,
                }
            )
        cands.sort(key=lambda x: (not x["is_truth"], x["first_seen_ordinal"]))

        pairs.append(
            Pair(
                session_id=sid,
                agent=agent,
                project=project,
                question=q,
                q_ordinal=q_ord,
                slot_kind=kind,
                resolver_slot=resolver_slot,
                question_form=form,
                value=truth,
                answer_ordinal=answer_ev.ordinal,
                answer_excerpt=answer_ev.text[:600],
                evidence_pointer=grounding.pointer,
                evidence_ordinal=grounding.ordinal,
                tool_name=grounding.tool_name,
                grounding_scope=grounding_scope,
                candidates=cands,
            )
        )
    return pairs, multi_records, reasons


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=DEFAULT_DB)
    ap.add_argument("--out", default=".work/real-q-candidates.jsonl")
    ap.add_argument("--multi-out", default=".work/real-q-multi.jsonl")
    ap.add_argument("--require-tools", action="store_true", default=False)
    ap.add_argument("--max-sessions", type=int, default=0)
    ap.add_argument("--min-msgs", type=int, default=2)
    a = ap.parse_args()

    c = sqlite3.connect(f"file:{a.db}?mode=ro", uri=True)
    c.row_factory = sqlite3.Row
    sess_rows = c.execute(
        "select id,agent,project,message_count,is_automated,has_tool_calls from sessions "
        "where coalesce(is_automated,0)=0 and deleted_at is null "
        + ("and has_tool_calls=1 " if a.require_tools else "")
        + "and message_count>=? and rowid in (select min(rowid) from sessions group by id) "
        "order by id",
        (a.min_msgs,),
    ).fetchall()
    if a.max_sessions:
        sess_rows = sess_rows[: a.max_sessions]
    print(f"sessions scanned: {len(sess_rows)}", file=sys.stderr)

    out_rows: list[dict] = []
    all_multi: list[dict] = []
    all_reasons: Counter = Counter()
    seen_q: Counter = Counter()
    for i, s in enumerate(sess_rows):
        if i % 500 == 0:
            print(f"  ..{i}/{len(sess_rows)}  pairs={len(out_rows)}", file=sys.stderr)
        try:
            pairs, multis, reasons = propose(c, s)
        except Exception as exc:  # noqa: BLE001
            all_reasons[f"ERR:{type(exc).__name__}"] += 1
            continue
        all_reasons.update(reasons)
        all_multi.extend(multis)
        for p in pairs:
            key = skeleton(p.question)
            seen_q[key] += 1
            r = p.__dict__.copy()
            r["expected_value"] = r["value"]
            r["dup_index"] = seen_q[key]
            r["quality_gate"] = "mine-v1"
            out_rows.append(r)

    # second pass: audit labels + duplicate filter
    dup = Counter()
    kept = []
    for r in out_rows:
        r["audit_label"] = classify(r, dup)
        dup[(r["question"], r["value"])] += 1
        kept.append(r)

    outp = Path(a.out)
    outp.parent.mkdir(parents=True, exist_ok=True)
    with outp.open("w") as fh:
        for r in kept:
            fh.write(json.dumps(r) + "\n")

    mp = Path(a.multi_out)
    mp.parent.mkdir(parents=True, exist_ok=True)
    with mp.open("w") as fh:
        for r in all_multi:
            fh.write(json.dumps(r) + "\n")

    print(json.dumps({"proposed": len(kept), "ambiguity_cases": len(all_multi),
                      "reasons": dict(all_reasons.most_common(30))}, indent=2))
    print(f"ambiguity cases -> {mp}", file=sys.stderr)
    print(f"wrote {outp}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
