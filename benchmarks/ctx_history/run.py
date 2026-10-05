"""Real-history bakeoff: current z0 history path vs read-only ctx (#116, handoff step 6).

Local-first. Runs one private question set through every arm and writes two outputs:

* ``--private-dir``: full rows, including the evidence text each arm would inject. Never commit these.
* ``--results-dir``: sanitized rows (question ids, metrics, evidence ids or hashes) and an aggregate
  ``summary.json``. Only these are meant for git.

Arms (each runs in its own child process so the control can import a different z0 tree):

* ``control``: ``context_resolve.resolve_context`` without ctx from ``--control-src``. If that tree has the
  z0 memory surface, a ``memory`` need also goes through it (AgentsView FTS5, read-only), next to qmd.
* ``ctx_lexical``: ``CtxHistoryCapability.search`` (lexical, ``--refresh off``, limit 5, as the seam uses it).
* ``ctx_exact_hydration``: ctx_lexical plus ``show event --window 3`` on the top ``--hydrate-top`` hits.
* ``ctx_hybrid``: only with ``--allow-ctx-semantic`` *and* a ready local semantic backend; a remote
  executor is refused. Otherwise every row records why it did not run.

Grading is deterministic: a question is answered when one injected evidence item matches every ``all``
pattern and at least one ``any`` pattern of its known answer. The first such item is the cited source.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import statistics
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ARMS = ("control", "ctx_lexical", "ctx_exact_hydration", "ctx_hybrid")
CTX_LIMIT = 5  # the resolve_context seam's per-need limit
_CTX_ID = re.compile(r"^ctx:(event|session):[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
_AV_ID = re.compile(r"^agentsview:[\w.:-]{1,80}#\d{1,12}$")
_TOKEN = re.compile(r"^[\w.:;=@/+-]{1,200}$")
_EVT = re.compile(r"^evt_[0-9a-f]{16,64}$")
_KEEP = (
    "question_id", "category", "topic", "question_sha", "arm", "status", "generation", "effective_mode",
    "n_evidence", "evidence_ids", "answered", "answered_fresh", "answered_covered", "cited_id", "cited_rank",
    "cited_source_system", "hydrated_uids", "latency_cold_ms", "latency_warm_ms", "bytes_opened",
    "excerpt_chars", "excerpt_tokens", "abstained", "error", "dropped_after_cutoff", "stable", "repeats",
)


# ----------------------------------------------------------------------------- pure helpers (unit tested)
def _sha(text: str, n: int = 16) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:n]


def grade(items: list[dict[str, Any]], answer: dict[str, Any]) -> dict[str, Any]:
    """First evidence item (1-based rank) whose own text holds the known answer."""
    need_all = [re.compile(p, re.I) for p in answer.get("all") or ()]
    need_any = [re.compile(p, re.I) for p in answer.get("any") or ()]
    if not need_all and not need_any:
        raise ValueError("answer spec needs at least one 'all' or 'any' pattern")
    for rank, item in enumerate(items, 1):
        text = item.get("text") or ""
        if all(p.search(text) for p in need_all) and (not need_any or any(p.search(text) for p in need_any)):
            return {"answered": True, "cited": item.get("locator"), "rank": rank,
                    "source_system": item.get("source_system")}
    return {"answered": False, "cited": None, "rank": None, "source_system": None}


def safe_id(value: Any) -> str | None:
    """Opaque ctx/AgentsView ids pass through; anything else (paths, names) becomes a hash."""
    if value is None:
        return None
    s = str(value)
    return s if _CTX_ID.match(s) or _AV_ID.match(s) else "h:" + _sha(s)


def _safe_token(value: Any) -> Any:
    if value is None or isinstance(value, (bool, int, float)):
        return value
    s = str(value)
    return s if _TOKEN.match(s) else "h:" + _sha(s)


def sanitize(row: dict[str, Any]) -> dict[str, Any]:
    """Whitelist of metrics and ids; no question text, evidence text or error detail survives."""
    out: dict[str, Any] = {}
    for key in _KEEP:
        if key not in row:
            continue
        val = row[key]
        if key == "evidence_ids":
            val = [safe_id(v) for v in val or ()]
        elif key == "cited_id":
            val = safe_id(val)
        elif key == "hydrated_uids":
            val = [v if isinstance(v, str) and _EVT.match(v) else "h:" + _sha(str(v)) for v in val or ()]
        elif key == "error":
            head = str(val).split(":", 1)[0].strip() if val else val
            val = head if head and re.match(r"^[A-Za-z_]\w{0,60}$", head) else ("error" if val else val)
        elif key in ("generation", "cited_source_system", "status", "topic", "category", "question_id",
                     "effective_mode", "question_sha", "arm"):
            val = _safe_token(val)
        out[key] = val
    return out


_SESSION_MARKER = re.compile(r"^(CLAUDECODE$|CLAUDE_(CODE_|PID$|JOB_)|CODEX_|HERMES_SESSION)")


def caller_env(env: dict[str, str], mode: str) -> dict[str, str]:
    """``neutral`` drops the agent-session markers ctx uses to auto-exclude the caller's session tree, so
    results do not depend on which agent session runs the bakeoff (config/data-root vars are kept)."""
    if mode == "inherit":
        return dict(env)
    return {k: v for k, v in env.items() if not _SESSION_MARKER.match(k)}


def hybrid_gate(allow_semantic: bool, status: dict[str, Any]) -> str | None:
    """None when hybrid may run: explicit opt-in, semantic ready, executor local (builtin)."""
    if not allow_semantic:
        return "not_run:semantic_opt_in_absent"
    sem = status.get("semantic") if isinstance(status.get("semantic"), dict) else {}
    if not sem.get("enabled") or sem.get("status") != "ready":
        return "unavailable:" + str(sem.get("reason") or sem.get("status") or "semantic_unknown")
    executor = sem.get("executor")
    if executor not in (None, "builtin"):
        return "unavailable:remote_executor_refused"
    return None


def hydration_text(events: list[dict[str, Any]], target_id: str, *, cap: int) -> str:
    """The target event's text first, then its nearest neighbours (earlier first), cut at ``cap`` chars."""
    idx = next((i for i, e in enumerate(events) if e.get("ctx_event_id") == target_id), 0)
    order = [idx] + [j for d in range(1, len(events)) for j in (idx - d, idx + d) if 0 <= j < len(events)]
    out = ""
    for j in order:
        text = events[j].get("text")
        if not isinstance(text, str) or not text:
            continue
        piece = text if not out else "\n" + text
        room = cap - len(out)
        if room <= 0:
            break
        out += piece[:room]
    return out


def _parse_ts(value: Any) -> datetime | None:
    if not value:
        return None
    s = re.sub(r"(\.\d{6})\d+", r"\1", str(value).strip()).replace("Z", "+00:00")
    try:
        ts = datetime.fromisoformat(s)
    except ValueError:
        return None
    return ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)


def apply_cutoff(items: list[dict[str, Any]], cutoff: str | None) -> tuple[list[dict[str, Any]], int]:
    """Drop items observed at/after ``cutoff`` (keeps undated/unparseable items)."""
    limit = _parse_ts(cutoff)
    if limit is None:
        return items, 0
    kept = [i for i in items if (_parse_ts(i.get("observed_at")) or limit.replace(year=1)) < limit]
    return kept, len(items) - len(kept)


def _note_field(note: str | None, key: str) -> str | None:
    for part in (note or "").split(";"):
        k, _, v = part.strip().partition("=")
        if k == key:
            return v
    return None


def ctx_query(cap: Any, q: dict[str, Any], *, mode: str, hydrate_top: int, hydrate_cap: int) -> dict[str, Any]:
    backend = "hybrid" if mode == "ctx_hybrid" else "lexical"
    before = getattr(cap, "bytes_read", 0)
    t0 = time.perf_counter()
    res = cap.search(q["question"], limit=CTX_LIMIT, backend=backend, allow_semantic=backend != "lexical")
    items = [{"locator": e.locator, "text": e.excerpt or "", "observed_at": "",
              "source_system": _note_field(e.note, "provider")} for e in res.evidence]
    hydrated: list[str] = []
    if mode == "ctx_exact_hydration":
        for e in res.evidence[:hydrate_top]:
            if not e.locator.startswith("ctx:event:"):
                continue
            eid = e.locator.removeprefix("ctx:event:")
            h = cap.show_event(eid, window=3)
            hydrated.append(h.identity.event_uid)
            items.append({"locator": e.locator, "text": hydration_text(list(h.window_events), eid, cap=hydrate_cap),
                          "observed_at": "", "source_system": _note_field(e.note, "provider")})
    row = _finish(q, items, latency_ms=(time.perf_counter() - t0) * 1000.0,
                  bytes_opened=getattr(cap, "bytes_read", 0) - before, generation=res.generation_id)
    row.update(effective_mode=res.effective_mode, hydrated_uids=hydrated,
               evidence_ids=list(dict.fromkeys(e.locator for e in res.evidence)), n_evidence=len(res.evidence))
    return row


def _finish(q: dict[str, Any], items: list[dict[str, Any]], *, latency_ms: float, bytes_opened: int,
            generation: str, fresh_cutoff: str | None = None, covered: tuple[str, ...] = (),
            dropped: int = 0) -> dict[str, Any]:
    g = grade(items, q["answer"])
    chars = sum(len(i.get("text") or "") for i in items)
    fresh_items, _ = apply_cutoff(items, fresh_cutoff)
    cov_items = [i for i in items if not covered or i.get("source_system") in covered]
    return {
        "status": "ok", "generation": generation, "items": items,
        "evidence_ids": list(dict.fromkeys(i["locator"] for i in items)), "n_evidence": len(items),
        "answered": g["answered"], "cited_id": g["cited"], "cited_rank": g["rank"],
        "cited_source_system": g["source_system"],
        "answered_fresh": grade(fresh_items, q["answer"])["answered"],
        "answered_covered": grade(cov_items, q["answer"])["answered"],
        "latency_ms": round(latency_ms, 1), "bytes_opened": int(bytes_opened),
        "excerpt_chars": chars, "excerpt_tokens": math.ceil(chars / 4), "abstained": not items,
        "dropped_after_cutoff": dropped,
    }


def _pct(values: list[float], p: float) -> float | None:
    if not values:
        return None
    s = sorted(values)
    return round(s[min(len(s) - 1, math.ceil(p * len(s)) - 1)], 1)


def summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    by_arm: dict[str, list[dict[str, Any]]] = {}
    for r in rows:
        by_arm.setdefault(r["arm"], []).append(r)
    control = {r["question_id"]: r for r in by_arm.get("control", ()) if r.get("status") == "ok"}
    arms: dict[str, Any] = {}
    for arm, rs in by_arm.items():
        ok = [r for r in rs if r.get("status") == "ok"]
        counts: dict[str, int] = {}
        for r in rs:
            counts[r.get("status") or "unknown"] = counts.get(r.get("status") or "unknown", 0) + 1
        cats: dict[str, dict[str, int]] = {}
        for r in ok:
            c = cats.setdefault(r.get("category") or "uncategorized", {"n": 0, "answered": 0})
            c["n"] += 1
            c["answered"] += bool(r.get("answered"))
        entry: dict[str, Any] = {
            "rows": len(rs), "status_counts": counts, "ok": len(ok),
            "answered": sum(bool(r.get("answered")) for r in ok),
            "answered_fresh": sum(bool(r.get("answered_fresh")) for r in ok),
            "answered_covered": sum(bool(r.get("answered_covered")) for r in ok),
            "abstained": sum(bool(r.get("abstained")) for r in ok),
            "by_category": cats,
        }
        for key in ("latency_cold_ms", "latency_warm_ms", "bytes_opened", "excerpt_chars", "excerpt_tokens"):
            vals = [float(r[key]) for r in ok if r.get(key) is not None]
            entry[key] = {"median": round(statistics.median(vals), 1) if vals else None, "p90": _pct(vals, 0.9),
                          "sum": round(sum(vals), 1) if vals else 0}
        if arm != "control" and ok and control:
            wins, losses, ties = [], [], 0
            for r in ok:
                c = control.get(r["question_id"])
                if c is None:
                    continue
                a, b = bool(r.get("answered")), bool(c.get("answered"))
                if a and not b:
                    wins.append(r["question_id"])
                elif b and not a:
                    losses.append(r["question_id"])
                else:
                    ties += 1
            entry["vs_control"] = {"wins": sorted(wins), "losses": sorted(losses), "ties": ties}
        arms[arm] = entry
    return {"arms": arms}


# ----------------------------------------------------------------------------- workers (child processes)
def _repeat(fn, q: dict[str, Any], repeats: int) -> dict[str, Any]:
    runs = []
    for rep in range(max(1, repeats)):
        try:
            runs.append(fn(q, rep))
        except Exception as exc:  # noqa: BLE001 - an arm failure is a row, not a crash
            runs.append({"status": "error", "error": f"{type(exc).__name__}: {exc}"[:300],
                         "abstained": True, "answered": False})
    row = dict(runs[0])
    lat = [r.get("latency_ms") for r in runs if r.get("status") == "ok"]
    row["latency_cold_ms"] = runs[0].get("latency_ms")
    row["latency_warm_ms"] = min(lat[1:]) if len(lat) > 1 else None
    row["stable"] = all(r.get("evidence_ids") == runs[0].get("evidence_ids") for r in runs)
    row["repeats"] = len(runs)
    row.pop("latency_ms", None)
    return row


def _ctx_worker(arm: str, questions: list[dict[str, Any]], args: argparse.Namespace) -> list[dict[str, Any]]:
    from z0int.capabilities.ctx_history import CtxHistoryCapability

    commands: list[list[str]] = []

    def runner(argv, **kw):
        proc = subprocess.run(argv, **kw)
        commands.append(list(argv[1:3]))
        cap.bytes_read += len((proc.stdout or "").encode("utf-8"))
        return proc

    cap = CtxHistoryCapability(runner=runner)
    cap.bytes_read = 0  # type: ignore[attr-defined]
    rows = [_repeat(lambda q, _rep: ctx_query(cap, q, mode=arm, hydrate_top=args.hydrate_top,
                                             hydrate_cap=args.hydrate_cap), q, args.repeats) for q in questions]
    kinds: dict[str, int] = {}
    for c in commands:
        kind = " ".join(c[:2]) if c[:1] == ["show"] else c[0]
        kinds[kind] = kinds.get(kind, 0) + 1
    return rows + [{"_meta": {"ctx_commands": kinds}}]


class _CountingCursor:
    def __init__(self, cur, box):
        self._cur, self._box = cur, box

    def _count(self, rows):
        for row in rows:
            for v in row if isinstance(row, (tuple, list)) else (row,):
                if isinstance(v, (str, bytes)):
                    self._box["bytes"] += len(v.encode("utf-8") if isinstance(v, str) else v)
        return rows

    def fetchall(self):
        return self._count(self._cur.fetchall())

    def fetchone(self):
        row = self._cur.fetchone()
        if row is not None:
            self._count([row])
        return row

    def __getattr__(self, name):
        return getattr(self._cur, name)


class _CountingConn:
    def __init__(self, conn, box):
        self._conn, self._box = conn, box

    def execute(self, *a, **kw):
        return _CountingCursor(self._conn.execute(*a, **kw), self._box)

    def __getattr__(self, name):
        return getattr(self._conn, name)


def _control_worker(questions: list[dict[str, Any]], args: argparse.Namespace) -> list[dict[str, Any]]:
    import inspect

    from z0int import context_resolve as cr

    box = {"bytes": 0}
    surface = captured = None
    try:
        from z0int import agentsview_ro
        from z0int.memory import surface

        real_connect = agentsview_ro.connect

        def connect(*a, **kw):
            conn = real_connect(*a, **kw)
            return _CountingConn(conn, box) if conn else conn

        agentsview_ro.connect = connect
        real_search = surface.search
        captured = {}

        def search(*a, **kw):
            res = real_search(*a, **kw)
            captured["res"] = res
            return res

        surface.search = search
    except ImportError:
        surface = None
    real_run = subprocess.run

    def run(argv, *a, **kw):
        proc = real_run(argv, *a, **kw)
        if argv and str(argv[0]).endswith("qmd"):
            box["bytes"] += len((proc.stdout or "").encode("utf-8") if isinstance(proc.stdout, str) else proc.stdout or b"")
        return proc

    subprocess.run = run
    with_memory = surface is not None and "turn_key" in inspect.signature(cr.resolve_context).parameters
    covered = tuple(p for p in args.covered_providers.split(",") if p)

    def one(q: dict[str, Any], rep: int) -> dict[str, Any]:
        needs = [cr.InformationNeed(id="q0", description=q["question"], kind="natural_language")]
        kw: dict[str, Any] = {}
        if with_memory:
            needs.append(cr.InformationNeed(id="m0", description=q["question"], kind="memory"))
            kw = {"allow_memory": True, "turn_key": f"ctxbake-{args.run_id}-{q['id']}-{rep}",
                  "memory_policy": surface.ScopePolicy()}
        if captured is not None:
            captured.clear()
        before = box["bytes"]
        t0 = time.perf_counter()
        packet = cr.resolve_context(needs=needs, allow_qmd=True, use_cache=False, **kw)
        latency = (time.perf_counter() - t0) * 1000.0
        systems = {e["locator"]: e.get("source_system") for e in ((captured or {}).get("res") or {}).get("evidence", ())}
        items = []
        for e in packet.evidence:
            is_av = e.locator.startswith("agentsview:")
            items.append({"locator": e.locator, "text": e.excerpt or "",
                          "observed_at": e.observed_at if is_av else "",
                          "source_system": systems.get(e.locator) or ("qmd" if e.trust_class == "index_hit" else e.trust_class)})
        kept, dropped = apply_cutoff(items, args.cutoff)
        layers = ((captured or {}).get("res") or {}).get("layers") or {}
        gen = ";".join(x for x in (
            f"av={(layers.get('lexical') or {}).get('revision')}" if layers else "",
            f"snap={packet.measurements.get('memory_snapshot_id')}" if packet.measurements.get("memory_snapshot_id") else "",
            f"qmd={packet.recipe.source_epochs.get('qmd_status_sha')}" if packet.recipe and packet.recipe.source_epochs.get("qmd_status_sha") else "",
        ) if x) or "none"
        row = _finish(q, kept, latency_ms=latency, bytes_opened=box["bytes"] - before, generation=gen,
                      fresh_cutoff=args.fresh_cutoff, covered=covered + ("qmd",), dropped=dropped)
        row["layers"] = {k: v.get("status") + (":" + v["reason"] if v.get("reason") else "") for k, v in layers.items()}
        row["qmd_hits"] = sum(1 for i in kept if i["source_system"] == "qmd")
        return row

    rows = [_repeat(one, q, args.repeats) for q in questions]
    return rows + [{"_meta": {"control_memory_surface": with_memory, "z0int_file": cr.__file__}}]


# ----------------------------------------------------------------------------- orchestration
def load_questions(path: str | Path) -> list[dict[str, Any]]:
    qs = [json.loads(line) for line in Path(path).read_text(encoding="utf-8").splitlines() if line.strip()]
    for q in qs:
        if not q.get("id") or not q.get("question") or not isinstance(q.get("answer"), dict):
            raise ValueError(f"question row needs id/question/answer: {q.get('id')!r}")
        grade([], q["answer"])  # validates the spec
    if len({q["id"] for q in qs}) != len(qs):
        raise ValueError("duplicate question id")
    return qs


def _read_only_env() -> dict[str, str]:
    return {**os.environ, "CTX_LOCAL_USAGE_ENABLED": "false", "CTX_ANALYTICS_ENABLED": "false"}


def _ctx_status() -> dict[str, Any]:
    try:
        proc = subprocess.run(["ctx", "status", "--format", "json"], capture_output=True, text=True, timeout=60,
                              check=False, env=_read_only_env())
        return json.loads(proc.stdout) if proc.returncode == 0 else {"error": f"exit {proc.returncode}"}
    except (OSError, ValueError, subprocess.TimeoutExpired) as exc:
        return {"error": type(exc).__name__}


def _ctx_version() -> str | None:
    try:
        out = subprocess.run(["ctx", "--version"], capture_output=True, text=True, timeout=30, check=False,
                             env=_read_only_env()).stdout.strip()
    except (OSError, subprocess.TimeoutExpired):
        return None
    return out or None


def _tree_snapshot(root: str | None) -> dict[str, tuple[int, int]]:
    snap: dict[str, tuple[int, int]] = {}
    if not root or not Path(root).is_dir():
        return snap
    for dirpath, _dirs, files in os.walk(root):
        for f in files:
            p = os.path.join(dirpath, f)
            try:
                st = os.stat(p)
            except OSError:
                continue
            snap[p] = (st.st_size, st.st_mtime_ns)
    return snap


def _git_head(src: Path) -> str | None:
    try:
        proc = subprocess.run(["git", "--no-optional-locks", "-C", str(src), "rev-parse", "HEAD"],
                              capture_output=True, text=True, timeout=10, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return proc.stdout.strip() or None


def _spawn(arm: str, src: Path, args: argparse.Namespace) -> tuple[list[dict[str, Any]], dict[str, Any], str]:
    cmd = [sys.executable, str(Path(__file__).resolve()), "--worker", arm, "--questions", args.questions,
           "--repeats", str(args.repeats), "--hydrate-top", str(args.hydrate_top),
           "--hydrate-cap", str(args.hydrate_cap), "--covered-providers", args.covered_providers,
           "--run-id", args.run_id, "--ctx-caller", args.ctx_caller]
    for flag, val in (("--cutoff", args.cutoff), ("--fresh-cutoff", args.fresh_cutoff)):
        if val:
            cmd += [flag, val]
    env = {**caller_env(_read_only_env(), args.ctx_caller), "PYTHONPATH": str(src)}
    proc = subprocess.run(cmd, capture_output=True, text=True, env=env, check=False)
    rows, meta = [], {}
    for line in proc.stdout.splitlines():
        obj = json.loads(line)
        if "_meta" in obj:
            meta.update(obj["_meta"])
        else:
            rows.append(obj)
    if proc.returncode != 0:
        meta["worker_exit"] = proc.returncode
    return rows, meta, proc.stderr


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--questions", required=True, help="private JSONL: id, category, topic, question, answer, source")
    ap.add_argument("--arms", default=",".join(ARMS))
    ap.add_argument("--private-dir", help="full rows incl. evidence text (never commit)")
    ap.add_argument("--results-dir", help="sanitized rows + summary.json (committable)")
    ap.add_argument("--control-src", default=str(Path(__file__).resolve().parents[2] / "src"),
                    help="src/ of the z0 tree the control arm imports (default: this repo)")
    ap.add_argument("--allow-ctx-semantic", action="store_true", help="explicit opt-in for ctx_hybrid")
    ap.add_argument("--cutoff", help="ISO time; control evidence observed at/after it is dropped (contamination guard)")
    ap.add_argument("--fresh-cutoff", help="ISO time for the freshness-matched control grade (ctx publish time)")
    ap.add_argument("--covered-providers", default="", help="ctx-indexed providers, e.g. claude,hermes")
    ap.add_argument("--ctx-caller", choices=("neutral", "inherit"), default="neutral",
                    help="neutral: hide the calling agent session from ctx's auto-exclusion (replayable); "
                         "inherit: as shipped, results depend on the calling session")
    ap.add_argument("--repeats", type=int, default=2, help="1st run is cold, best later run is warm")
    ap.add_argument("--hydrate-top", type=int, default=3)
    ap.add_argument("--hydrate-cap", type=int, default=2000, help="chars injected per hydrated window")
    ap.add_argument("--run-id", default=time.strftime("%Y%m%dT%H%M%SZ", time.gmtime()))
    ap.add_argument("--worker", choices=ARMS, help=argparse.SUPPRESS)
    args = ap.parse_args(argv)
    questions = load_questions(args.questions)

    if args.worker:
        rows = (_control_worker(questions, args) if args.worker == "control"
                else _ctx_worker(args.worker, questions, args))
        for r in rows:
            print(json.dumps(r, ensure_ascii=False))
        return 0

    if not args.private_dir or not args.results_dir:
        ap.error("--private-dir and --results-dir are required")
    private, results = Path(args.private_dir), Path(args.results_dir)
    private.mkdir(parents=True, exist_ok=True)
    results.mkdir(parents=True, exist_ok=True)
    repo_src = Path(__file__).resolve().parents[2] / "src"
    status = _ctx_status()
    ctx_root = os.environ.get("CTX_DATA_ROOT")
    before = _tree_snapshot(ctx_root)
    all_rows: list[dict[str, Any]] = []
    meta: dict[str, Any] = {}
    for arm in [a for a in args.arms.split(",") if a]:
        if arm not in ARMS:
            ap.error(f"unknown arm {arm}")
        gate = hybrid_gate(args.allow_ctx_semantic, status) if arm == "ctx_hybrid" else None
        if gate:
            rows, arm_meta = [{"status": gate, "abstained": True, "answered": False} for _ in questions], {}
            ordered = list(zip(questions, rows))
        else:
            src = Path(args.control_src) if arm == "control" else repo_src
            rows, arm_meta, stderr = _spawn(arm, src, args)
            (private / f"{arm}.stderr.txt").write_text(stderr, encoding="utf-8")
            ordered = list(zip(questions, rows))
            if len(rows) != len(questions):
                arm_meta["row_count_mismatch"] = [len(rows), len(questions)]
        meta[arm] = arm_meta
        for q, r in ordered:
            r.update(question_id=q["id"], category=q.get("category"), topic=q.get("topic"),
                     question_sha=_sha(q["question"]), arm=arm)
            all_rows.append(r)
    after = _tree_snapshot(ctx_root)
    changed = sorted(p for p in set(before) | set(after) if before.get(p) != after.get(p))

    with (private / "rows.full.jsonl").open("w", encoding="utf-8") as fh:
        for r in all_rows:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")
    (private / "meta.json").write_text(json.dumps({"arms": meta, "ctx_root_changed": changed}, indent=1))
    clean = [sanitize(r) for r in all_rows]
    with (results / "rows.jsonl").open("w", encoding="utf-8") as fh:
        for r in clean:
            fh.write(json.dumps(r, sort_keys=True) + "\n")
    sem = status.get("semantic") if isinstance(status.get("semantic"), dict) else {}
    lex = status.get("lexical") if isinstance(status.get("lexical"), dict) else {}
    summary = {
        "schema": "z0int.ctx_history_bakeoff.v1",
        "run_id": args.run_id,
        "question_set_sha": _sha("\n".join(json.dumps(q, sort_keys=True) for q in questions)),
        "n_questions": len(questions),
        "arms_requested": args.arms.split(","),
        "ctx": {"version": _ctx_version(), "generation_id": lex.get("generation_id"),
                "indexed_documents": lex.get("indexed_documents"), "certified_sources": lex.get("certified_sources"),
                "indexing_mode": (status.get("indexing") or {}).get("mode"),
                "semantic_status": sem.get("status"), "semantic_reason": sem.get("reason"),
                "covered_providers": [p for p in args.covered_providers.split(",") if p],
                "commands": (meta.get("ctx_lexical") or {}).get("ctx_commands"),
                "hydration_commands": (meta.get("ctx_exact_hydration") or {}).get("ctx_commands"),
                "data_root_changed_files": len(changed) if ctx_root else None},
        "control": {"src_head": _git_head(Path(args.control_src)),
                    "memory_surface": (meta.get("control") or {}).get("control_memory_surface")},
        "harness_head": _git_head(repo_src),
        "params": {"repeats": args.repeats, "ctx_limit": CTX_LIMIT, "hydrate_top": args.hydrate_top,
                   "hydrate_cap": args.hydrate_cap, "cutoff": args.cutoff, "fresh_cutoff": args.fresh_cutoff,
                   "allow_ctx_semantic": args.allow_ctx_semantic, "ctx_caller": args.ctx_caller},
        **summarize(all_rows),
    }
    (results / "summary.json").write_text(json.dumps(summary, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({k: summary[k] for k in ("run_id", "n_questions", "arms")}, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
