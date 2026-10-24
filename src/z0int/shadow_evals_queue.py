"""Private asynchronous observations of real OMP inputs, never turn middleware.

The caller polls session JSONL files. This module never writes to sessions,
IntentService receipts, OAuth stores, or the central policy ledger. SQLite's
transactions provide create/restart idempotence without stale-lock reaping.
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import stat
import subprocess
from typing import Any, Callable, Iterable

REPO = Path(__file__).resolve().parents[2]
TRIAGE = REPO / "omp-extensions/z0-intent-service/triage.js"
PACKET = Path("/mnt/zer0models/workspace/zer0/data/operations/repo-distill/STATE-PACKET.md")
ASK_AUDIT_LEXICON_SOURCE = "/mnt/zer0models/workspace/zer0/data/operations/ask-audit/audit.py:93"
# Exact AskAudit LEX expressions. A lexical flag is observed evidence, not a
# reviewed causal failure label and never a counterfactual candidate outcome.
ASK_AUDIT_LEXICON = (
    r"\btold you\b", r"\bi said\b", r"\bi (?:already )?asked\b",
    r"\bnot what i (?:asked|want)",
    r"\b(?:did you (?:just )?forget|how many times|every (?:single )?time|babysit)",
    r"\bwhy (?:did|didn.t|don.t|are|aren.t|won.t) you\b", r"\byou said you\b",
    r"\bignor(?:e|ed|ing)\b", r"\bremind(?:er|ing)?\b",
    r"\b(?:already|again|still)\b",
    r"\b(?:wtf|fuck\w*)\b.*\b(?:ask|do|fix|told|said|want|need)\b",
)
NOTICE = re.compile(r"<(system-notice|system-reminder|system_notice|system|irc|subagent[^ >]*)\b[^>]*>.*?</\1>", re.I | re.S)
AUTO = re.compile(r"^\s*(?:\[?(?:ASYNC DELEGATION|System:|system notice|budget notice|IRC |cron |Automated|Task completed|Background (?:job|task))|<system|<irc|You are (?:a |an )?(?:subagent|worker)|\[Telegram [^\]]+\]\s*\[)", re.I)
AUTO_EXTRA = re.compile(r"^\s*(?:\u2063?FIRSTMATE_OP:|: Firstmate instruction waiting|You are the Zer0 log triage watcher|\[CONTEXT COMPACTION|\[Your active task list|\[kanban\]|Double-blind |Judge anon|CORRECTIVE ITERATION|You are the persistent|TEMPLE GUARD WAVE|PRIORITY UPDATE FROM OPERATOR:|CONTINUE THE ALREADY-AUTHORIZED|GREEN (?:PLATE|VIEWSET|V\d+ MASK)|V\d+ (?:RED|PLATE|BLOCKED|STRUCTURAL|EXPORTED|NON-MANIFOLD|EXPORT|POLAR|EXACT)|WAVE 1 V\d+|Reply with exactly|This is an isolated acceptance probe|What does this project do\? Read the code|Which channel does an alert go to\? Check the code)", re.I)
AUTO_OTHER = re.compile(r"^\s*(?:Hermes\s*(?:→|->)|\[Hermes\]|\[from Hermes,|Zer0 PM assigned|\[IMPORTANT: \d+ background|V\d+ [A-Z][A-Z0-9 /-]+(?:—| - )|GREEN V\d+|Read-only feasibility audit:|Open the correct Hermes native-Tern preview|Safely diagnose and complete the reverse Tern)", re.I)
IDENTITY = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,199}\Z")
HASH = re.compile(r"[a-f0-9]{64}\Z")


def _hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _identity(value: Any) -> str:
    if not isinstance(value, str) or not IDENTITY.fullmatch(value) or value in {".", ".."}:
        raise ValueError("Invalid source identity")
    return value


def _timestamp(value: Any) -> str:
    if not isinstance(value, str):
        raise ValueError("Missing source timestamp")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("Source timestamp requires a timezone")
    return value


def content_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(p["text"] for p in content if isinstance(p, dict)
                         and p.get("type") in {"text", "input_text"} and isinstance(p.get("text"), str))
    return ""


def _human_text(text: str) -> bool:
    # Test eligibility on notice-free text, but retain the original bytes.
    stripped = NOTICE.sub("", text).strip()
    stripped = re.sub(r"^\[Wait interrupted by message\]\s*", "", stripped).strip()
    return bool(stripped) and not any(pattern.search(stripped) for pattern in (AUTO, AUTO_EXTRA, AUTO_OTHER))


def _is_user(entry: dict) -> bool:
    message = entry.get("message", {})
    return (entry.get("type") == "message" and message.get("role") == "user"
            and message.get("attribution") in (None, "user")
            and _human_text(content_text(message.get("content"))))


def _is_input(entry: dict) -> bool:
    data = entry.get("data", {})
    return (entry.get("type") == "custom" and entry.get("customType") == "z0-intent-input"
            and data.get("kind") in {"input", "edit"}
            and data.get("source") in {"interactive", "intent-editor"}
            and isinstance(data.get("rawText"), str) and _human_text(data["rawText"]))


def _dispatch_of(entry: dict, custom: dict) -> bool:
    if not _is_user(entry) or not _is_input(custom):
        return False
    raw = custom["data"]["rawText"]
    stripped = re.sub(r"^\s*(/aodl(?:\s+|$)|quick:\s*)", "", raw, count=1, flags=re.I)
    text = content_text(entry["message"].get("content"))
    return text in {raw, stripped} or text.startswith(stripped + "\n\n[Intent context — evidence, not approval]\n")


def read_session(path: str | Path) -> tuple[str, list[dict]]:
    """Read complete JSONL records only. A concurrently appended tail waits."""
    entries: list[dict] = []
    session_id = None
    ids: set[str] = set()
    with Path(path).open(encoding="utf-8") as stream:
        for number, line in enumerate(stream, 1):
            if not line.endswith("\n"):
                break
            if not line.strip():
                continue
            row = json.loads(line)
            if not isinstance(row, dict):
                raise ValueError(f"Session record {number} must be an object")
            if row.get("type") == "session":
                current = _identity(row.get("id"))
                if session_id is not None:
                    raise ValueError("Duplicate session header")
                session_id = current
            elif row.get("id") is not None:
                entry_id = _identity(row["id"])
                if entry_id in ids:
                    raise ValueError("Duplicate session entry identity")
                ids.add(entry_id)
                entries.append(row)
    if session_id is None:
        raise ValueError("Missing session header")
    return session_id, entries


def _ancestors(entry: dict, by_id: dict[str, dict]) -> list[dict]:
    result = []
    seen = {entry["id"]}
    parent = entry.get("parentId")
    while parent is not None:
        if parent in seen:
            raise ValueError("Session parent cycle")
        if parent not in by_id:
            raise ValueError("Missing session parent")
        seen.add(parent)
        row = by_id[parent]
        result.append(row)
        parent = row.get("parentId")
    result.reverse()
    return result


def _receipts(home: Path, session_id: str) -> tuple[dict[str, dict], Path]:
    path = home / "intent" / session_id / "triage.jsonl"
    rows = {}
    if path.exists():
        with path.open(encoding="utf-8") as stream:
            for line in stream:
                if not line.endswith("\n"):
                    break
                if not line.strip():
                    continue
                row = json.loads(line)
                entry_id = _identity(row.get("entry_id"))
                if entry_id in rows and row != rows[entry_id]:
                    raise ValueError("Conflicting triage receipt identity")
                rows[entry_id] = row
    return rows, path


def deterministic_triage(requests: list[dict], *, triage_path: Path = TRIAGE,
                         packet_path: Path = PACKET) -> list[dict]:
    """Invoke only the existing deterministic JS classifier. No service/model."""
    script = """import {readFileSync} from 'node:fs';
const module = await import(process.argv[1]);
const requests = JSON.parse(readFileSync(0, 'utf8'));
let projects = [], packetAvailable = false;
try { projects = module.projectsFromPacket(readFileSync(process.argv[2], 'utf8')); packetAvailable = true; } catch {}
console.log(JSON.stringify(requests.map(r => module.triage(r.text, {
  projects, packetAvailable, openContract: r.open_contract, hasImages: r.has_images
}))));"""
    completed = subprocess.run(["node", "--input-type=module", "-e", script,
                                triage_path.resolve().as_uri(), str(packet_path)],
                               input=json.dumps(requests, ensure_ascii=False), text=True,
                               capture_output=True, check=True, timeout=30)
    result = json.loads(completed.stdout)
    if not isinstance(result, list) or len(result) != len(requests):
        raise ValueError("Deterministic triage returned an invalid batch")
    return result


def job_identity(session_id: str, entry_id: str, sha256: str) -> str:
    return _hash("\0".join((_identity(session_id), _identity(entry_id), sha256)))


def _validate_job(job: dict) -> dict:
    if not isinstance(job, dict):
        raise ValueError("Job must be an object")
    session_id, entry_id = _identity(job.get("session_id")), _identity(job.get("entry_id"))
    text = job.get("payload", {}).get("text")
    if not isinstance(text, str) or not text.strip():
        raise ValueError("Exact source text required")
    digest = _hash(text)
    if job.get("sha256") != digest or job.get("tier") not in {"CHECK", "CONTRACT"}:
        raise ValueError("Job source hash or tier mismatch")
    _timestamp(job.get("ts"))
    expected = job_identity(session_id, entry_id, digest)
    if job.get("job_id", expected) != expected:
        raise ValueError("Job identity mismatch")
    if not isinstance(job.get("provenance"), dict):
        raise ValueError("Source provenance required")
    return {**job, "job_id": expected}


def discover_jobs(session_path: str | Path, intent_home: str | Path | None = None, *,
                  triage_runner: Callable | None = None, packet_path: Path = PACKET,
                  triage_path: Path = TRIAGE) -> list[dict]:
    """Observe CHECK/CONTRACT inputs, preserving custom-entry receipt identity.

    Missing receipts use sidecar JS triage, explicitly marked as independent.
    An existing but mismatched receipt is rejected, never replaced by fallback.
    No writes occur. Nested worker histories are not traversed by ingestion.
    """
    path = Path(session_path)
    session_id, entries = read_session(path)
    by_id = {row["id"]: row for row in entries}
    receipts, receipt_path = _receipts(Path(intent_home or Path.home() / ".z0int"), session_id)
    sources = []
    for entry in entries:
        custom = _is_input(entry)
        if not custom and not _is_user(entry):
            continue
        ancestors = _ancestors(entry, by_id)
        if not custom:
            # The extension can transform dispatched text. Observe its exact
            # original custom source instead of injecting a duplicate user turn.
            nearest = next((row for row in reversed(ancestors) if _is_user(row) or _is_input(row)), None)
            if nearest is not None and _dispatch_of(entry, nearest):
                continue
        data = entry.get("data", {})
        text = data["rawText"] if custom else content_text(entry["message"].get("content"))
        digest = _hash(text)
        if custom and (data.get("sha256") != digest or not HASH.fullmatch(data.get("extension_content_sha", ""))):
            raise ValueError("Intent input source hash or extension stamp mismatch")
        stamp = _timestamp(data.get("ts") if custom else entry.get("timestamp"))
        receipt = receipts.get(entry["id"])
        if receipt is not None:
            if receipt.get("sourceHash") != digest or receipt.get("tier") not in {"FAST", "CHECK", "CONTRACT"}:
                raise ValueError("Triage receipt source identity mismatch")
            _timestamp(receipt.get("ts"))
        recent = []
        source_ids = {source["entry"]["id"] for source in sources}
        recent_users = [row["data"]["rawText"] if _is_input(row)
                        else content_text(row["message"].get("content"))
                        for row in ancestors if row["id"] in source_ids]
        for prior in ancestors:
            if prior.get("type") == "message" and prior.get("message", {}).get("role") in {"user", "assistant"}:
                if prior["message"]["role"] == "user" and not _is_user(prior):
                    continue
                prior_text = content_text(prior["message"].get("content"))
                if prior_text:
                    recent.append({"entry_id": prior["id"], "role": prior["message"]["role"], "text": prior_text,
                                   "ts": prior.get("timestamp")})
        states = [row.get("data", {}).get("contract") for row in ancestors if row.get("customType") == "z0-intent-state"]
        message = entry.get("message", {})
        images = isinstance(message.get("content"), list) and any(p.get("type") in {"image", "input_image"} for p in message["content"] if isinstance(p, dict))
        sources.append({"entry": entry, "text": text, "digest": digest, "ts": stamp, "receipt": receipt,
                        "custom": custom, "recent": recent[-8:], "recent_users": recent_users[-4:],
                        "request": {"text": text, "open_contract": bool(states and states[-1]), "has_images": images}})
    missing = [source for source in sources if source["receipt"] is None]
    if missing:
        requests = [source["request"] for source in missing]
        results = (triage_runner(requests) if triage_runner else
                   deterministic_triage(requests, triage_path=triage_path, packet_path=packet_path))
        if not isinstance(results, list) or len(results) != len(missing):
            raise ValueError("Triage batch identity mismatch")
        for source, result in zip(missing, results):
            if not isinstance(result, dict) or result.get("sourceHash") != source["digest"] or result.get("tier") not in {"FAST", "CHECK", "CONTRACT"}:
                raise ValueError("Sidecar triage source identity mismatch")
            source["triage"] = result
    jobs = []
    for source in sources:
        triage = source["receipt"] or source["triage"]
        if triage["tier"] == "FAST":
            continue
        entry = source["entry"]
        provenance = {"harness": "omp", "actor": "kevin", "session_path": str(path.resolve()),
                      "input_source": "intent-custom-entry" if source["custom"] else "session-user-message",
                      "triage_source": "intent-service-receipt" if source["receipt"] else "sidecar-deterministic-js",
                      "triage_path": str(triage_path.resolve()),
                      "receipt_path": str(receipt_path) if source["receipt"] else None,
                      "extension_content_sha": entry.get("data", {}).get("extension_content_sha"),
                      "attribution": entry.get("message", {}).get("attribution"), "synthetic": False}
        jobs.append(_validate_job({"session_id": session_id, "entry_id": entry["id"],
                    "sha256": source["digest"], "ts": source["ts"], "tier": triage["tier"],
                    "provenance": provenance, "triage": triage,
                    "payload": {"text": source["text"], "recent_user_msgs": source["recent_users"],
                                "recent_messages": source["recent"]}}))
    return jobs


def join_outcomes(job: dict, session_entries: Iterable[dict], state_updates: Iterable[dict] = ()) -> dict:
    """Join actual outcomes only. Pending, lexical ambiguity, and silence stay null.

    Only the next actual human turn on a descendant branch is considered.
    AskAudit flags are recorded as corrections, not reviewed failure labels.
    A success requires a verified AODL stateUpdate bound to exact source text.
    """
    job = _validate_job(job)
    entries = list(session_entries)
    by_id = {row["id"]: row for row in entries if isinstance(row, dict) and row.get("id")}
    result = {"job_id": job["job_id"], "label": None, "status": "pending", "synthetic": False,
              "failure_signal": False, "kind": None, "lexicon_matches": [],
              "candidate_success": None, "scope": "actual-observed-turn-only", "evidence": []}
    if job["entry_id"] not in by_id:
        return {**result, "status": "source-unavailable"}
    source = by_id[job["entry_id"]]
    source_text = (source.get("data", {}).get("rawText") if _is_input(source)
                   else content_text(source.get("message", {}).get("content")))
    if not (_is_input(source) or _is_user(source)) or source_text != job["payload"]["text"]:
        raise ValueError("Outcome source identity mismatch")
    next_turns = []
    for entry in entries:
        if entry["id"] == job["entry_id"] or not (_is_user(entry) or _is_input(entry)):
            continue
        ancestors = _ancestors(entry, by_id)
        positions = [i for i, row in enumerate(ancestors) if row["id"] == job["entry_id"]]
        if not positions:
            continue
        between = ancestors[positions[0] + 1:]
        # A dispatched user entry for the same custom input is not a next turn.
        if _dispatch_of(entry, source) and not any(_is_user(row) or _is_input(row) for row in between):
            continue
        humans = [row for row in between if _is_user(row) or _is_input(row)]
        if humans and _dispatch_of(humans[0], source):
            humans = humans[1:]
        if humans:
            continue
        text = entry.get("data", {}).get("rawText") if _is_input(entry) else content_text(entry["message"].get("content"))
        matches = [pattern for pattern in ASK_AUDIT_LEXICON if re.search(pattern, NOTICE.sub("", text), re.I | re.S)]
        next_turns.append(entry["id"])
        if matches:
            result["failure_signal"] = True
            result["kind"] = "correction_lexicon"
            result["lexicon_matches"].extend(pattern for pattern in matches if pattern not in result["lexicon_matches"])
            result["evidence"].append({"kind": "next-kevin-correction", "entry_id": entry["id"],
                                       "sha256": _hash(text), "text": text, "patterns": matches,
                                       "lexicon_source": ASK_AUDIT_LEXICON_SOURCE})
    events = []
    for event in state_updates:
        if not isinstance(event, dict) or event.get("type") != "stateUpdate":
            continue
        payload = event.get("payload", {})
        if (event.get("sourceHash") != job["sha256"] or payload.get("verified") is not True
                or event.get("entry_id") != job["entry_id"] or event.get("session_id") != job["session_id"]
                or type(payload.get("success")) is not bool or not payload.get("verifier")
                or not event.get("eventId")):
            continue
        events.append(event)
    if result["evidence"]:
        result["status"] = "correction-observed-unreviewed"
    elif events and len({event["payload"]["success"] for event in events}) == 1:
        result["label"] = "success" if events[0]["payload"]["success"] else "failure"
        result["status"] = "aodl-verified"
        result["kind"] = "aodl_verified"
        result["failure_signal"] = events[0]["payload"]["success"] is False
    elif events:
        result["status"] = "ambiguous"
    elif next_turns:
        result["status"] = "unlabelled-next-turn"
    result["next_entry_ids"] = next_turns
    result["evidence"].extend({"kind": "aodl-stateUpdate", "event": event} for event in events)
    return result


def verified_runtime_updates(document: dict, jobs: list[dict], runtime_path: Path) -> list[tuple[str, dict]]:
    """Read-only canonical checker joins exact approved sources to all their tasks.

    The checker inspects verifier receipts and causal tool execution evidence.
    Its check() function does not run a verifier, tool, or model.
    """
    script = """import {readFileSync} from 'node:fs';
const {check} = await import(process.argv[1]);
const {document: doc, jobs} = JSON.parse(readFileSync(0, 'utf8'));
const b = doc.plan?.bindings ?? {};
const rows = jobs.map(job => {
  if (b.sessionId !== job.session_id || b.approval?.session_id !== job.session_id ||
      b.sources?.[job.entry_id]?.texts?.length !== 1 ||
      b.sources[job.entry_id].texts[0] !== job.payload.text) return null;
  const ids = (doc.intentGraph?.nodes ?? []).filter(n =>
    n.kind === 'task' && b[n.id]?.entryId === job.entry_id).map(n => n.id);
  if (!ids.length || ids.some(id => b[id].status !== 'verified')) return null;
  const checked = check(doc, {claims: ids, mutating: true, sessionId: job.session_id});
  if (!checked.ok) return null;
  const events = ids.map(id => doc.eventLog.filter(e =>
    e.type === 'stateUpdate' && e.payload?.kind === 'verified' &&
    e.payload.taskId === id).at(-1));
  return {job_id: job.job_id, entry_id: job.entry_id, session_id: job.session_id,
    sourceHash: job.sha256, type: 'stateUpdate', eventId: events.at(-1).eventId,
    payload: {verified: true, success: true, verifier: 'canonical-intent-checker'},
    document_sourceHash: doc.provenance.sourceHash, original_events: events};
}).filter(Boolean);
console.log(JSON.stringify(rows));"""
    checker = REPO / "omp-extensions/z0-relay-guard/checker.js"
    completed = subprocess.run(["node", "--input-type=module", "-e", script, checker.as_uri()],
                               input=json.dumps({"document": document, "jobs": jobs}, ensure_ascii=False),
                               text=True, capture_output=True, check=True, timeout=30)
    rows = json.loads(completed.stdout)
    if not isinstance(rows, list):
        raise ValueError("Canonical checker returned invalid outcomes")
    return [(row["job_id"], {**row, "document_path": str(runtime_path)}) for row in rows]


def _private_directory(path: Path) -> None:
    path.mkdir(parents=True, mode=0o700, exist_ok=True)
    info = path.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid():
        raise ValueError("Queue directory must be owned and not a symlink")
    path.chmod(0o700)


class ShadowQueue:
    """Durable spool. Pending jobs survive restarts, including interrupted finish."""

    def __init__(self, root: str | Path, *, packet_path: Path = PACKET, triage_path: Path = TRIAGE,
                 latest_only: bool = False):
        self.root = Path(root)
        self.packet_path, self.triage_path = Path(packet_path), Path(triage_path)
        self.latest_only = latest_only
        _private_directory(self.root)
        self.db_path = self.root / "queue.sqlite3"
        descriptor = os.open(self.db_path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        try:
            info = os.fstat(descriptor)
            if info.st_uid != os.getuid() or not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                raise ValueError("Queue database must be a private owned regular file")
            os.fchmod(descriptor, 0o600)
        finally:
            os.close(descriptor)
        with self._connect() as connection:
            connection.executescript("""
                CREATE TABLE IF NOT EXISTS jobs (
                    job_id TEXT PRIMARY KEY, session_id TEXT NOT NULL,
                    entry_id TEXT NOT NULL, sha256 TEXT NOT NULL, job TEXT NOT NULL,
                    result TEXT, outcome TEXT, UNIQUE(session_id, entry_id)
                );
            """)

    @contextmanager
    def _connect(self):
        connection = sqlite3.connect(self.db_path, timeout=5)
        try:
            connection.execute("PRAGMA synchronous=FULL")
            with connection:
                yield connection
        finally:
            connection.close()

    def enqueue(self, job: dict) -> bool:
        job = _validate_job(job)
        encoded = json.dumps(job, ensure_ascii=False, sort_keys=True)
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            prior = connection.execute("SELECT sha256,job FROM jobs WHERE session_id=? AND entry_id=?",
                                       (job["session_id"], job["entry_id"])).fetchone()
            if prior:
                old = json.loads(prior[1])
                if prior[0] != job["sha256"] or old["payload"]["text"] != job["payload"]["text"]:
                    raise ValueError("Source entry identity changed")
                return False
            connection.execute("INSERT INTO jobs(job_id,session_id,entry_id,sha256,job) VALUES(?,?,?,?,?)",
                               (job["job_id"], job["session_id"], job["entry_id"], job["sha256"], encoded))
        return True

    def pending(self) -> list[dict]:
        with self._connect() as connection:
            return [json.loads(row[0]) for row in connection.execute("SELECT job FROM jobs WHERE result IS NULL ORDER BY rowid")]

    def finish(self, job_id: str, result: dict) -> None:
        if not isinstance(result, dict) or not isinstance(job_id, str) or not HASH.fullmatch(job_id):
            raise ValueError("Invalid result or job identity")
        if result.get("job_id", job_id) != job_id:
            raise ValueError("Result identity mismatch")
        encoded = json.dumps(result, ensure_ascii=False, sort_keys=True, allow_nan=False)
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            prior = connection.execute("SELECT result FROM jobs WHERE job_id=?", (job_id,)).fetchone()
            if prior is None:
                raise ValueError("Unknown queue job")
            if prior[0] is not None and prior[0] != encoded:
                raise ValueError("Completed job result changed")
            connection.execute("UPDATE jobs SET result=? WHERE job_id=?", (encoded, job_id))

    def requeue(self, job_id: str) -> bool:
        """Explicit operator retry only. Successful results are immutable."""
        if not isinstance(job_id, str) or not HASH.fullmatch(job_id):
            raise ValueError("Invalid retry job identity")
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute("SELECT result FROM jobs WHERE job_id=?", (job_id,)).fetchone()
            if row is None:
                raise ValueError("Unknown queue job")
            if row[0] is None:
                return False
            result = json.loads(row[0])
            if result.get("status") not in {"blocked", "error"}:
                raise ValueError("Only blocked or error jobs can be retried")
            connection.execute("UPDATE jobs SET result=NULL WHERE job_id=?", (job_id,))
        return True

    def records(self) -> list[dict]:
        """Return jobs with separately joined result/outcome observations."""
        with self._connect() as connection:
            return [{"job": json.loads(job), "result": json.loads(result) if result else None,
                     "outcome": json.loads(outcome) if outcome else None}
                    for job, result, outcome in connection.execute("SELECT job,result,outcome FROM jobs ORDER BY rowid")]

    def ingest_sessions(self, session_root: str | Path, intent_home: str | Path) -> dict:
        return ingest_sessions(session_root, intent_home, queue=self,
                               packet_path=self.packet_path, triage_path=self.triage_path,
                               latest_only=self.latest_only)


def ingest_sessions(session_root: str | Path, intent_home: str | Path, *, queue: ShadowQueue | None = None,
                    triage_runner: Callable | None = None, packet_path: Path = PACKET,
                    triage_path: Path = TRIAGE, latest_only: bool = False) -> dict:
    """Poll only root-level or one-directory-deep OMP session files.

    Without queue, return discovered jobs in ``jobs``. With queue, persist them
    and refresh outcomes for completed and pending jobs. Errors contain paths
    and exception classes only, never source text or subprocess stderr.
    """
    root = Path(session_root)
    paths = [root] if root.is_file() else sorted(set(root.glob("*.jsonl")) | set(root.glob("*/*.jsonl")))
    if latest_only and paths:
        paths = [max(paths, key=lambda path: (path.stat().st_mtime_ns, str(path)))]
    report = {"sessions": 0, "discovered": 0, "enqueued": 0, "existing": 0,
              "outcomes_updated": 0, "errors": [], "jobs": []}
    for path in paths:
        try:
            jobs = discover_jobs(path, intent_home, triage_runner=triage_runner,
                                 packet_path=packet_path, triage_path=triage_path)
            report["sessions"] += 1
            report["discovered"] += len(jobs)
            if queue is None:
                report["jobs"].extend(jobs)
                continue
            for job in jobs:
                report["enqueued" if queue.enqueue(job) else "existing"] += 1
            session_id, entries = read_session(path)
            with queue._connect() as connection:
                stored = [json.loads(row[0]) for row in connection.execute(
                    "SELECT job FROM jobs WHERE session_id=?", (session_id,))]
            updates = []
            directory = Path(intent_home) / "intent" / session_id / "runtime"
            for runtime in sorted(directory.glob("*.json")):
                document = json.loads(runtime.read_text(encoding="utf-8"))
                updates.extend(verified_runtime_updates(document, stored, runtime))
            with queue._connect() as connection:
                for (encoded,) in connection.execute("SELECT job FROM jobs WHERE session_id=?", (session_id,)).fetchall():
                    job = json.loads(encoded)
                    outcome = join_outcomes(job, entries, [event for job_id, event in updates if job_id == job["job_id"]])
                    value = json.dumps(outcome, ensure_ascii=False, sort_keys=True)
                    changed = connection.execute("UPDATE jobs SET outcome=? WHERE job_id=? AND (outcome IS NULL OR outcome != ?)",
                                                 (value, job["job_id"], value)).rowcount
                    report["outcomes_updated"] += changed
        except (OSError, ValueError, TypeError, KeyError, sqlite3.Error, subprocess.SubprocessError) as exc:
            report["errors"].append({"path": str(path), "error": type(exc).__name__})
    return report
