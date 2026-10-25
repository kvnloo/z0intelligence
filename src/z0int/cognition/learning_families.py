"""Exact private OMP atomic jobs, independent of the tool-choice learner.

Integration: calls needs session, call, tool, features (preceding tool names),
arguments (JSON object), outcome (JSON object or NULL), and ts (start epoch).
Optional outcome_ts is the actual result epoch, never the ingestion epoch.
Call derive_jobs, then run_jobs with an injected predict(prompt, choices,
strategy) -> dict. The caller owns transactions and transport timeouts.

Historical predictions are replayed_L1. Only a prediction persisted before an
actual future label can become live_shadow. This module neither executes tools
nor grants policy credit. All reported observations have synthetic=False.
"""
from __future__ import annotations

from collections import Counter
import hashlib
import json
import math
import sqlite3
import time
from typing import Any, Callable, Mapping

FAMILIES = ("omp.skill_choice", "omp.model_choice", "omp.path_kind", "omp.wrapper_result")
BASELINE = "heuristic:prior-transition"
UNKNOWN = "__unseen__"
FIXED_CHOICES = {
    "omp.path_kind": ("skill_uri", "internal_uri", "web_url", "absolute_path", "relative_path"),
    "omp.wrapper_result": ("wrapper_ok", "wrapper_error"),
}
TASKS = {
    "omp.skill_choice": "Predict the exact next selected skill name.",
    "omp.model_choice": "Predict the exact next explicit model argument.",
    "omp.path_kind": "Predict the next path argument class.",
    "omp.wrapper_result": "Predict the joined wrapper error flag, not task success.",
}
Predict = Callable[[str, list[str], dict], dict]


def _json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=True, separators=(",", ":"), allow_nan=False)


def _object(value: Any) -> dict:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (ValueError, TypeError):
            return {}
    return value if isinstance(value, dict) else {}


def _history(value: Any) -> list[str]:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            return []
    if not isinstance(value, list):
        return []
    return [item[:128] for item in value[-8:] if isinstance(item, str)]


def _name(value: Any) -> str | None:
    # Labels are exact. Never truncate one into a different valid label.
    if isinstance(value, str) and value and value == value.strip() and len(value) <= 256:
        return value
    return None


def extract_labels(tool: str, arguments: Any, outcome: Any = None) -> dict[str, str]:
    """Extract only explicit fields. Free text and regex task success are not gold.

    wrapper_result means the OMP wrapper's isError bit, not command exit status
    or completion of the user's task. Missing/contradictory flags are unknown.
    """
    args, result = _object(arguments), _object(outcome)
    name = tool.rsplit(".", 1)[-1]
    labels: dict[str, str] = {}
    if name in ("skill_view", "skill_load", "skill_manage"):
        names = {_name(args[key]) for key in ("name", "skill_name", "skill") if key in args}
        if len(names) == 1 and None not in names:
            labels["omp.skill_choice"] = names.pop()
    path = args.get("path")
    if isinstance(path, str) and path and path == path.strip():
        if path.startswith("skill://"):
            kind = "skill_uri"
            if name in ("read", "read_file"):
                skill = path[len("skill://"):].split("/", 1)[0].split(":", 1)[0]
                if _name(skill) and "?" not in skill and "#" not in skill:
                    labels["omp.skill_choice"] = skill
        elif path.startswith(("https://", "http://")):
            kind = "web_url"
        elif "://" in path:
            kind = "internal_uri"
        elif path.startswith("/"):
            kind = "absolute_path"
        else:
            kind = "relative_path"
        labels["omp.path_kind"] = kind
    model = _name(args.get("model"))
    if model:
        labels["omp.model_choice"] = model
    flags = [result[key] for key in ("tool_error", "isError") if key in result and result[key] is not None]
    if flags and all(type(flag) is bool for flag in flags) and len(set(flags)) == 1:
        labels["omp.wrapper_result"] = "wrapper_error" if flags[0] else "wrapper_ok"
    return labels


def job_id(session: str, call: str, family: str) -> str:
    return hashlib.sha256(_json(["atomic-v1", session, call, family]).encode()).hexdigest()


def ensure_schema(db: sqlite3.Connection) -> None:
    """Create independent tables without committing the caller's transaction."""
    statements = (
        """CREATE TABLE IF NOT EXISTS family_sources(session TEXT,call TEXT,
           arguments TEXT,outcome TEXT,ts REAL,outcome_ts REAL,
           PRIMARY KEY(session,call))""",
        """CREATE TABLE IF NOT EXISTS family_jobs(id TEXT PRIMARY KEY,session TEXT,
           call TEXT,family TEXT,ts REAL,label_ts REAL,label TEXT,features TEXT,
           created_at REAL,UNIQUE(session,call,family))""",
        "CREATE INDEX IF NOT EXISTS family_job_order ON family_jobs(family,ts,id)",
        """CREATE TABLE IF NOT EXISTS family_evaluations(id TEXT,arm TEXT,
           prediction TEXT,confidence REAL,predicted_at REAL,prospective INTEGER,
           prompt TEXT,choices TEXT,error TEXT,PRIMARY KEY(id,arm))""",
        """CREATE TABLE IF NOT EXISTS family_strategies(arm TEXT PRIMARY KEY,
           base_arm TEXT,strategy_id TEXT,config TEXT)""",
    )
    for statement in statements:
        db.execute(statement)


def derive_jobs(db: sqlite3.Connection, *, now: float | None = None, max_calls: int = 10000) -> dict:
    """Incrementally join starts/results from the same (session,call) row.

    The parent must join native events by both identifiers before storing calls.
    Newly available arguments/results are revisited, but existing gold is frozen.
    A pending wrapper job permits prediction before its result exists.
    """
    ensure_schema(db)
    now = time.time() if now is None else now
    columns = {row[1] for row in db.execute("PRAGMA table_info(calls)")}
    required = {"session", "call", "tool", "features", "arguments", "outcome", "ts"}
    if not required <= columns:
        raise ValueError("calls missing columns: " + ",".join(sorted(required - columns)))
    result_ts = "c.outcome_ts" if "outcome_ts" in columns else "NULL"
    rows = db.execute(f"""SELECT c.*, {result_ts} AS result_ts FROM calls c
        LEFT JOIN family_sources s ON s.session=c.session AND s.call=c.call
        WHERE s.session IS NULL OR c.arguments IS NOT s.arguments
          OR c.outcome IS NOT s.outcome OR c.ts IS NOT s.ts
          OR {result_ts} IS NOT s.outcome_ts
        ORDER BY c.ts,c.session,c.call LIMIT ?""", (max(0, max_calls),)).fetchall()
    added = labelled = 0
    for row in rows:
        labels = extract_labels(row["tool"], row["arguments"], row["outcome"])
        families = set(labels)
        if row["outcome"] is None:
            families.add("omp.wrapper_result")
        for family in sorted(families):
            label = labels.get(family)
            label_ts = row["result_ts"] if family == "omp.wrapper_result" else row["ts"]
            identity = job_id(row["session"], row["call"], family)
            inserted = db.execute("""INSERT OR IGNORE INTO family_jobs
                VALUES(?,?,?,?,?,?,?,?,?)""", (identity, row["session"], row["call"], family,
                row["ts"], label_ts, label, _json(_history(row["features"])), now)).rowcount
            added += inserted
            if inserted:
                labelled += int(label is not None)
            elif label is not None:
                labelled += db.execute("""UPDATE family_jobs SET label=?,label_ts=?,ts=?
                    WHERE id=? AND label IS NULL""", (label, label_ts, row["ts"], identity)).rowcount
        db.execute("""INSERT INTO family_sources VALUES(?,?,?,?,?,?)
            ON CONFLICT(session,call) DO UPDATE SET arguments=excluded.arguments,
            outcome=excluded.outcome,ts=excluded.ts,outcome_ts=excluded.outcome_ts""",
            (row["session"], row["call"], row["arguments"], row["outcome"], row["ts"], row["result_ts"]))
    return {"calls_scanned": len(rows), "jobs_added": added, "labels_added": labelled}


def project_prompt(family: str, features: Any, examples: list[dict], *,
                   prompt_bytes: int = 3000, candidate_k: int = 3,
                   history_width: int = 8, model_cap: int = 3) -> tuple[str, list[str]]:
    """Build a byte-bounded projection without any target argument or outcome.

    Examples must be strictly preceding labels. Derivation and run_jobs enforce
    that order. Open-vocabulary candidates come from those examples only.
    The current gold is never injected to repair candidate recall.
    """
    if family not in FAMILIES:
        raise ValueError("unknown atomic family")
    if prompt_bytes < 384 or model_cap not in (3, 5) or not 1 <= candidate_k <= model_cap:
        raise ValueError("candidate_k exceeds model_cap (default 3, explicit larger-model cap 5)")
    if not 1 <= history_width <= 8:
        raise ValueError("history_width must be in 1..8")
    prior = examples[-512:]
    counts = Counter(ex["label"] for ex in prior if _name(ex.get("label")))
    fixed = FIXED_CHOICES.get(family, ())
    ranked = sorted(fixed, key=lambda label: (-counts[label], fixed.index(label))) if fixed else sorted(counts, key=lambda label: (-counts[label], label))
    choices = ranked[:candidate_k]
    if not choices:
        choices = [UNKNOWN]
    # Keep the JSON syntactically valid. Shrink fields, never slice encoded JSON.
    body = {"task": TASKS[family], "previous_tools": _history(features)[-history_width:], "choices": choices, "examples": []}
    while len(_json(body).encode()) > prompt_bytes:
        if body["previous_tools"]:
            body["previous_tools"].pop(0)
        elif len(choices) > 1:
            choices.pop()
        else:
            choices[:] = [UNKNOWN]
    for ex in reversed(prior[-8:]):
        item = {"previous_tools": _history(ex.get("features", []))[-history_width:], "label": ex["label"]}
        body["examples"].insert(0, item)
        if len(_json(body).encode()) > prompt_bytes:
            body["examples"].pop(0)
            break
    return _json(body), choices


def _prior(db: sqlite3.Connection, row: Mapping, limit: int = 64, recent_sessions_n: int = 0) -> list[dict]:
    # Both start and label availability must precede the target start. This also
    # excludes outcomes of unfinished earlier calls and equal-time parallel calls.
    session_clause = ''
    params = [row["family"], row["ts"], row["ts"], row["id"]]
    if recent_sessions_n:
        sessions = [s[0] for s in db.execute('''SELECT session FROM family_jobs
            WHERE family=? AND label IS NOT NULL AND ts<? AND label_ts<?
            GROUP BY session ORDER BY MAX(ts) DESC LIMIT ?''',
            (row["family"], row["ts"], row["ts"], recent_sessions_n))]
        if not sessions:
            return []
        session_clause = ' AND session IN (' + ','.join('?' for _ in sessions) + ')'
        params.extend(sessions)
    examples = db.execute("""SELECT label,features FROM family_jobs WHERE family=?
        AND label IS NOT NULL AND ts<? AND label_ts<? AND id!=?""" +
        session_clause + " ORDER BY ts DESC,id DESC LIMIT ?", (*params, limit)).fetchall()
    return [{"label": ex["label"], "features": _history(ex["features"])} for ex in reversed(examples)]


def _baseline(features: Any, examples: list[dict], choices: list[str],
              history_width: int = 1) -> tuple[str | None, float]:
    history = _history(features)
    matched = [ex for ex in examples if _history(ex["features"])[-history_width:] == history[-history_width:]]
    votes = Counter(ex["label"] for ex in (matched or examples) if ex["label"] in choices)
    if not votes:
        return None, 0.0
    selected = min(votes, key=lambda label: (-votes[label], label))
    return selected, votes[selected] / sum(votes.values())


def _answer(raw: Any, choices: list[str]) -> tuple[str | None, float, str | None]:
    if not isinstance(raw, dict):
        return None, 0.0, "invalid_prediction_object"
    selected = raw.get("selected_action", raw.get("action"))
    confidence = raw.get("confidence", 0)
    error = raw.get("parse_error") or raw.get("error")
    if error:
        # Never store a transport exception string that can contain private URLs.
        return None, 0.0, "predictor_error"
    if selected is None and raw.get("abstained") is True:
        return None, 0.0, None
    if selected not in choices or selected == UNKNOWN:
        return None, 0.0, "out_of_set"
    if isinstance(confidence, bool) or not isinstance(confidence, (int, float)) or not math.isfinite(confidence) or not 0 <= confidence <= 1:
        return None, 0.0, "invalid_confidence"
    return selected, float(confidence), None


def _strategy(strategy_id: str, prompt_bytes: int, candidate_k: int,
              retrieval_k: int, history_width: int, model_cap: int = 3,
              candidate_source: str = 'history', recent_sessions_n: int = 5) -> dict:
    if not _name(strategy_id) or not 1 <= retrieval_k <= 512 or not 1 <= history_width <= 8:
        raise ValueError("invalid strategy identity or retrieval/history bound")
    if prompt_bytes < 384 or model_cap not in (3, 5) or not 1 <= candidate_k <= model_cap:
        raise ValueError("invalid strategy prompt/candidate model cap")
    if candidate_source not in ('history', 'recent_sessions') or not 1 <= recent_sessions_n <= 64:
        raise ValueError("invalid candidate-source session scope")
    return {"strategy_id": strategy_id, "prompt_bytes": prompt_bytes,
            "candidate_k": candidate_k, "retrieval_k": retrieval_k,
            "history_width": history_width, "model_cap": model_cap,
            "candidate_source": candidate_source, "recent_sessions_n": recent_sessions_n}


def _arm_key(db: sqlite3.Connection, base_arm: str, strategy: dict) -> str:
    default = _strategy("atomic-history-v1", 3000, 3, 64, 8)
    suffix = hashlib.sha256(_json(strategy).encode()).hexdigest()[:16]
    arm = base_arm if strategy == default else base_arm + "@" + strategy["strategy_id"] + ":" + suffix
    recorded = db.execute("SELECT config FROM family_strategies WHERE arm=?", (arm,)).fetchone()
    if recorded is not None and recorded["config"] != _json(strategy):
        arm = base_arm + "@" + strategy["strategy_id"] + ":" + suffix
    db.execute("INSERT OR IGNORE INTO family_strategies VALUES(?,?,?,?)",
               (arm, base_arm, strategy["strategy_id"], _json(strategy)))
    return arm


def _evaluate(db: sqlite3.Connection, row: Mapping, predict: Predict, *, now: float,
              slm_arm: str, strategy: dict, prospective: bool) -> None:
    started = time.monotonic()
    examples = _prior(db, row, strategy["retrieval_k"],
        strategy["recent_sessions_n"] if strategy["candidate_source"] == "recent_sessions" else 0)
    prompt, choices = project_prompt(row["family"], row["features"], examples,
        prompt_bytes=strategy["prompt_bytes"], candidate_k=strategy["candidate_k"],
        history_width=strategy["history_width"], model_cap=strategy["model_cap"])
    for base_arm in (BASELINE, slm_arm):
        arm = _arm_key(db, base_arm, strategy)
        if db.execute("SELECT 1 FROM family_evaluations WHERE id=? AND arm=?", (row["id"], arm)).fetchone():
            continue
        if base_arm == BASELINE:
            selected, confidence = _baseline(row["features"], examples, choices, strategy["history_width"])
            error = None
        else:
            try:
                raw = predict(prompt, choices, {**strategy, "id": arm,
                              "base_arm": base_arm, "family": row["family"]})
                selected, confidence, error = _answer(raw, choices)
            except Exception:
                selected, confidence, error = None, 0.0, "predictor_exception"
        db.execute("INSERT OR IGNORE INTO family_evaluations VALUES(?,?,?,?,?,?,?,?,?)",
                   (row["id"], arm, selected, confidence, now + time.monotonic() - started,
                    int(prospective), prompt, _json(choices), error))


def run_jobs(db: sqlite3.Connection, predict: Predict, *, now: float | None = None,
             max_jobs: int = 16, deadline: float | None = None,
             prompt_bytes: int = 3000, candidate_k: int = 3,
             slm_arm: str = "slm:qwen3-0.6b-q8",
             strategy_id: str = "atomic-history-v1", retrieval_k: int = 64,
             history_width: int = 8, families: tuple[str, ...] = FAMILIES,
             strategies: Mapping[str, dict] | None = None, model_cap: int = 3) -> dict:
    """Evaluate at most max_jobs SLM jobs, fairly across atomic families.

    Each family can override strategy_id, retrieval_k, history_width,
    prompt_bytes, candidate_k and explicit model_cap through strategies[family]. Config changes
    produce distinct arm identities, scores and immutable observations.
    Predictors must enforce their own RPC timeout. The monotonic deadline only
    stops scheduling calls. Repeated ticks never regrade an (id,arm).
    """
    ensure_schema(db)
    if not slm_arm.startswith("slm:") or any(family not in FAMILIES for family in families):
        raise ValueError("invalid SLM or family")
    now = time.time() if now is None else now
    started = time.monotonic()
    queues = []
    for family in families:
        config = {"strategy_id": strategy_id, "prompt_bytes": prompt_bytes,
                  "candidate_k": candidate_k, "retrieval_k": retrieval_k,
                  "history_width": history_width, "model_cap": model_cap}
        config.update((strategies or {}).get(family, {}))
        strategy = _strategy(**config)
        arm = _arm_key(db, slm_arm, strategy)
        queue = db.execute("""SELECT j.* FROM family_jobs j WHERE family=?
            AND NOT EXISTS(SELECT 1 FROM family_evaluations e WHERE e.id=j.id AND e.arm=?)
            ORDER BY ts,id LIMIT ?""", (family, arm, max(0, max_jobs))).fetchall()
        queues.append((queue, strategy))
    db.commit()
    processed = 0
    for index in range(max(0, max_jobs)):
        for queue, strategy in queues:
            if processed >= max_jobs or deadline is not None and time.monotonic() >= deadline:
                return {"jobs_evaluated": processed, "families": family_metrics(db, now=now), "synthetic": False}
            if index >= len(queue):
                continue
            row = queue[index]
            prospective = row["label"] is None and row["family"] == "omp.wrapper_result" and 0 < row["ts"] <= now
            _evaluate(db, row, predict, now=now + time.monotonic() - started, slm_arm=slm_arm,
                      strategy=strategy, prospective=prospective)
            db.commit()
            processed += 1
    return {"jobs_evaluated": processed, "families": family_metrics(db, now=now), "synthetic": False}


def predict_future(db: sqlite3.Connection, session: str, call: str, features: Any,
                   predict: Predict, *, now: float, families: tuple[str, ...] = FAMILIES,
                   slm_arm: str = "slm:qwen3-0.6b-q8", prompt_bytes: int = 3000,
                   candidate_k: int = 3, strategy_id: str = "atomic-history-v1",
                   retrieval_k: int = 64, history_width: int = 8, model_cap: int = 3) -> int:
    """Persist predictions for an identified future call before its start exists.

    This optional hook is for a true pre-call observer. Arguments contain no
    current gold or candidates derived from that gold. Existing starts are
    refused. derive_jobs later attaches labels by (session,call,family).
    """
    ensure_schema(db)
    started = time.monotonic()
    if db.execute("SELECT 1 FROM calls WHERE session=? AND call=?", (session, call)).fetchone():
        raise ValueError("future prediction requires an unobserved call")
    if any(family not in FAMILIES for family in families) or not slm_arm.startswith("slm:"):
        raise ValueError("invalid prospective family or arm")
    strategy = _strategy(strategy_id, prompt_bytes, candidate_k, retrieval_k, history_width, model_cap)
    added = 0
    for family in families:
        identity = job_id(session, call, family)
        added += db.execute("INSERT OR IGNORE INTO family_jobs VALUES(?,?,?,?,?,?,?,?,?)",
            (identity, session, call, family, now, None, None, _json(_history(features)), now)).rowcount
        row = db.execute("SELECT * FROM family_jobs WHERE id=?", (identity,)).fetchone()
        _evaluate(db, row, predict, now=now + time.monotonic() - started,
                  slm_arm=slm_arm, strategy=strategy, prospective=True)
    return added


def wilson_interval(correct: int, n: int) -> tuple[float, float]:
    if not n:
        return 0.0, 1.0
    z = 1.959963984540054
    p = correct / n
    denominator = 1 + z*z/n
    center = (p + z*z/(2*n)) / denominator
    radius = z * math.sqrt(p*(1-p)/n + z*z/(4*n*n)) / denominator
    return max(0.0, center-radius), min(1.0, center+radius)


def family_metrics(db: sqlite3.Connection, *, now: float | None = None) -> dict:
    """Report event-time labels/day, separate replay/live scores and Wilson95.

    n and total count all graded predictions. covered_n excludes abstentions.
    Accuracy and Wilson intervals use covered_n only. Zero coverage is blocked.
    Event-time labels/day do not turn old trace ingestion into fresh labels.
    """
    ensure_schema(db)
    now = time.time() if now is None else now
    report = {family: {"labels": 0, "labels_day": 0, "unknown_time_labels": 0,
                      "pending": 0, "arms": {}, "synthetic": False} for family in FAMILIES}
    for row in db.execute("""SELECT family,COUNT(*) AS n,
        SUM(CASE WHEN label_ts>? AND label_ts<=? THEN 1 ELSE 0 END) AS day,
        SUM(CASE WHEN label_ts IS NULL OR label_ts<=0 THEN 1 ELSE 0 END) AS unknown
        FROM family_jobs WHERE label IS NOT NULL GROUP BY family""", (now-86400, now)):
        report[row["family"]].update(labels=row["n"], labels_day=row["day"], unknown_time_labels=row["unknown"])
    for row in db.execute("SELECT family,COUNT(*) AS n FROM family_jobs WHERE label IS NULL GROUP BY family"):
        report[row["family"]]["pending"] = row["n"]
    for row in db.execute("""SELECT j.family,e.arm,s.base_arm,s.strategy_id,s.config,
        CASE WHEN e.prospective=1 AND e.predicted_at<j.label_ts
          THEN 'live_shadow' ELSE 'replayed_L1' END AS evidence,
        COUNT(*) AS n,SUM(e.prediction IS NOT NULL) AS covered,
        SUM(CASE WHEN e.prediction=j.label THEN 1 ELSE 0 END) AS correct,
        SUM(e.error IS NOT NULL) AS errors
        FROM family_jobs j JOIN family_evaluations e ON e.id=j.id
        LEFT JOIN family_strategies s ON s.arm=e.arm
        WHERE j.label IS NOT NULL GROUP BY j.family,e.arm,evidence"""):
        low, high = wilson_interval(row["correct"], row["covered"])
        report[row["family"]]["arms"].setdefault(row["arm"], {})[row["evidence"]] = {
            "n": row["n"], "total": row["n"], "covered_n": row["covered"], "correct": row["correct"],
            "accuracy": row["correct"] / row["covered"] if row["covered"] else 0.0,
            "coverage": row["covered"] / row["n"],
            "status": "graded" if row["covered"] else "blocked",
            "ci_low": low, "ci_high": high, "ci_method": "wilson95", "errors": row["errors"],
            "evidence_class": row["evidence"], "synthetic": False,
            "base_arm": row["base_arm"] or row["arm"],
            "strategy_id": row["strategy_id"] or "atomic-history-v1",
            "strategy_config": json.loads(row["config"]) if row["config"] else {},
        }
    return report
