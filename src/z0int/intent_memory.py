"""Bounded private evidence retrieval; records are context, never authorization.

Only loopback/read-only APIs are used. Blocking file/SQLite/CLI adapters run in
owned process groups, not executor threads: cancellation kills and reaps them.
The Unii expert has no read-only question API; its authenticated /export is the
only HTTP memory read used here. No chat act, inference, write or install occurs.
"""
from __future__ import annotations

import asyncio
import hashlib
import http.client
import io
import json
import math
import os
from pathlib import Path
import re
import signal
import sqlite3
import stat
import sys
import time
from typing import Any, Awaitable, Callable, Iterable, Mapping

Record = dict[str, Any]
Source = Callable[[str], Awaitable[list[Record]]]
_MAX_BYTES = 1_048_576
_MAX_FILE_BYTES = 262_144
_STOP = frozenset("a an and are as at be by do for from how i in is it me of on or our that the this to was we what when where which with you your".split())


class SourceUnavailable(RuntimeError):
    """Fixed-code failures only; never expose private upstream errors."""


def _terms(text: str) -> list[str]:
    return list(dict.fromkeys(w for w in re.findall(r"[\w.-]{2,}", text.casefold()) if w not in _STOP))[:24]


def _digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _record(source: str, identity: str, text: str, path: Path | None = None, *, evidence_kind: str | None = None) -> Record:
    kind = evidence_kind or ("claimed" if source in {"tencentdb", "hermes", "state_packet", "recall", "statepack", "qmd", "unified_expert"} else "unknown")
    row: Record = {"source": source, "id": identity, "text": text, "evidence_kind": kind}
    if path is not None:
        row["path"] = str(path)
    return row


def _local_path_exists(path: Any) -> bool:
    # Catalog paths are literal local files, not URLs, fragments or expanded aliases.
    try:
        return isinstance(path, str) and bool(path) and Path(path).is_absolute() and Path(path).is_file()
    except (OSError, ValueError):
        return False


def validate_citations(citations: Any, records: Iterable[Mapping[str, Any]]) -> bool:
    """Accept only exact catalog identities/paths; recheck every local file.

    A citation is an exact ID/path string or {source?, id?, path?}. A supplied
    field must match the SAME retrieved record. Ambiguous short IDs fail closed.
    Text/extras cannot create a new identity. Empty citations are valid; callers
    must separately require evidence when the operation needs it.
    """
    if not isinstance(citations, (list, tuple)):
        return False
    catalog = [r for r in records if isinstance(r, Mapping)]
    for citation in citations:
        if isinstance(citation, str) and citation:
            matches = [r for r in catalog if citation in (r.get("id"), r.get("path"))]
        elif isinstance(citation, Mapping):
            if (set(citation) - {"source", "id", "path", "evidence_kind"} or
                    not ("id" in citation or "path" in citation) or
                    any(not isinstance(v, str) or not v for v in citation.values())):
                return False
            matches = [r for r in catalog if all(r.get(k) == v for k, v in citation.items())]
        else:
            return False
        identities = {(r.get("source"), r.get("id"), r.get("path")) for r in matches}
        if len(identities) != 1:
            return False
        if any("path" in r and not _local_path_exists(r["path"]) for r in matches):
            return False
    return True


class MemoryRetriever:
    """Retrieve all unified sources concurrently under one absolute deadline.

    ``source_roots`` overrides named filesystem locations (see defaults below).
    ``sources`` replaces the adapter map for isolated tests/embedding. Source
    callables must be cancellation-cooperative; built-ins own all subprocesses
    and sockets. A successful empty search is answered, not invented evidence.
    """

    def __init__(self, *, root: Path | str | None = None, timeout_s: float = 4.0,
                 source_timeout_s: float | Mapping[str, float] = 3.5,
                 source_roots: Mapping[str, Path | str] | None = None,
                 sources: Mapping[str, Source] | None = None,
                 max_records: int = 32, per_source_limit: int = 5,
                 record_chars: int = 2400, total_chars: int = 32_000,
                 tencentdb_port: int = 8420, expert_port: int = 8798):
        if not math.isfinite(timeout_s) or timeout_s <= 0:
            raise ValueError("positive timeout_s required")
        limits = source_timeout_s.values() if isinstance(source_timeout_s, Mapping) else [source_timeout_s]
        if any(not math.isfinite(v) or v <= 0 for v in limits):
            raise ValueError("positive source timeout required")
        if any(type(v) is not int or v < 1 for v in (max_records, per_source_limit, record_chars, total_chars)):
            raise ValueError("positive record bounds required")
        if any(type(v) is not int or not 1 <= v <= 65535 for v in (tencentdb_port, expert_port)):
            raise ValueError("invalid loopback port")
        base = Path(root or os.environ.get("Z0INT_HOME", "~/.z0int")).expanduser().absolute()
        home = Path.home()
        runtime = Path(os.environ.get("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}"))
        operations = Path("/mnt/zer0models/workspace/zer0/data/operations")
        self.roots = {
            "memory_mcp": runtime / "z0int-memory-mcp/operator.sock",
            "events": base / "memory/events.jsonl",
            "hermes": home / ".hermes/memories",
            "chiefstaff": home / ".hermes/profiles/chiefstaff/memories",
            "agentsview": Path(os.environ.get("AGENTSVIEW_DATA_DIR", "/mnt/zer0models/sft-svlm/data/agentsview")),
            "recall": Path("/mnt/zer0models/workspace/zer0/oss/tools/recall.py"),
            "statepack": Path("/mnt/zer0models/workspace/zer0/oss/tools/statepack.py"),
            "state_packet": operations / "repo-distill/STATE-PACKET.md",
            "unified_index": operations / "unified-memory/INDEX.md",
            "qmd": Path(os.environ.get("Z0INT_INTENT_QMD", str(home / ".local/bin/qmd"))),
        }
        self.roots.update({k: Path(v).expanduser().absolute() for k, v in (source_roots or {}).items()})
        self.timeout_s = timeout_s
        self.source_timeout_s = source_timeout_s
        self.max_records = max_records
        self.per_source_limit = min(per_source_limit, 10)
        self.record_chars = record_chars
        self.total_chars = total_chars
        self.tencentdb_port = tencentdb_port
        self.expert_port = expert_port
        self.sources: dict[str, Source] = dict(sources) if sources is not None else {
            "memory_mcp": self._mcp,
            "events": lambda q: self._local("events", q),
            "tencentdb": self._tencentdb,
            "hermes": lambda q: self._local("hermes", q),
            "agentsview": lambda q: self._local("agentsview", q),
            "recall": self._recall,
            "statepack": self._statepack,
            "qmd": self._qmd,
            "unified_expert": self._expert,
            "state_packet": lambda q: self._local("state_packet", q),
        }

    async def retrieve(self, text: str, recent_user_msgs: Iterable[str] = ()) -> dict[str, Any]:
        started = time.monotonic()
        if not isinstance(text, str):
            raise TypeError("text must be a string")
        # Current ask first. Context is bounded and only sent to local readers.
        context = [m[-1000:] for m in list(recent_user_msgs)[-3:] if isinstance(m, str)]
        query = " ".join([text, *context]).strip().encode("utf-8")[:1000].decode("utf-8", "ignore")
        tasks = {name: asyncio.create_task(self._run(name, fn, query)) for name, fn in self.sources.items()}
        try:
            if tasks:
                await asyncio.wait(tasks.values(), timeout=max(0, self.timeout_s - (time.monotonic() - started)))
        finally:
            pending = {name for name, task in tasks.items() if not task.done()}
            for name in pending:
                tasks[name].cancel()
            await asyncio.gather(*tasks.values(), return_exceptions=True)
        records: list[Record] = []
        statuses: list[dict[str, Any]] = []
        seen: set[tuple[str, str]] = set()
        used = 0
        for name, task in tasks.items():
            if name in pending:
                statuses.append({"source": name, "status": "timeout", "latency_ms": round((time.monotonic() - started) * 1000, 2), "error": "total_deadline"})
                continue
            rows, status = task.result()
            statuses.append(status)
            for row in rows:
                if not isinstance(row, dict) or any(not isinstance(row.get(k), str) or not row[k] for k in ("source", "id", "text")):
                    continue
                if "path" in row and not _local_path_exists(row["path"]):
                    continue
                key = (row["source"], row["id"])
                if key in seen or len(records) >= self.max_records:
                    continue
                size = min(self.record_chars, self.total_chars - used)
                if size <= 0:
                    break
                normalized = {k: row[k] for k in ("source", "id", "path") if k in row}
                normalized["evidence_kind"] = row.get("evidence_kind", "unknown")
                normalized["text"] = row["text"][:size]
                records.append(normalized)
                used += len(normalized["text"])
                seen.add(key)
        return {"records": records, "sources": statuses, "latency_ms": round((time.monotonic() - started) * 1000, 2)}

    async def _run(self, name: str, fn: Source, query: str) -> tuple[list[Record], dict[str, Any]]:
        started = time.monotonic()
        status: dict[str, Any] = {"source": name, "status": "answered"}
        rows: list[Record] = []
        try:
            timeout = self.source_timeout_s.get(name, self.timeout_s) if isinstance(self.source_timeout_s, Mapping) else self.source_timeout_s
            if not query:
                raise SourceUnavailable("empty_query")
            rows = await asyncio.wait_for(fn(query), timeout=min(timeout, self.timeout_s))
            if not isinstance(rows, list):
                raise SourceUnavailable("invalid_records")
            rows = rows[:self.per_source_limit]
        except asyncio.TimeoutError:
            status.update(status="timeout", error="source_deadline")
        except Exception as exc:
            status.update(status="unavailable", error=str(exc) if isinstance(exc, SourceUnavailable) else type(exc).__name__)
        status["latency_ms"] = round((time.monotonic() - started) * 1000, 2)
        return rows, status

    async def _command(self, argv: list[str], payload: Any = None) -> bytes:
        env = dict(os.environ)
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        env["AGENTSVIEW_DATA_DIR"] = str(self.roots["agentsview"])
        proc = await asyncio.create_subprocess_exec(*argv, stdin=asyncio.subprocess.PIPE,
                    stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
                    start_new_session=True, env=env)
        try:
            assert proc.stdin is not None and proc.stdout is not None
            if payload is not None:
                proc.stdin.write(json.dumps(payload).encode())
                await proc.stdin.drain()
            proc.stdin.close()
            output = bytearray()
            while True:
                chunk = await proc.stdout.read(min(65536, _MAX_BYTES + 1 - len(output)))
                if not chunk:
                    break
                output.extend(chunk)
                if len(output) > _MAX_BYTES:
                    raise SourceUnavailable("output_limit")
            if await proc.wait() != 0:
                raise SourceUnavailable("reader_failed")
            return bytes(output)
        finally:
            # Kill the whole group, including children retaining stdout after a
            # CLI exits; no abandoned to_thread work or background refreshes.
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            # Resume a flow-controlled pipe before wait(): asyncio subprocess
            # wait can otherwise stall after an output-limit kill.
            if proc.stdout is not None:
                while await proc.stdout.read(65536):
                    pass
            await proc.wait()

    async def _local(self, source: str, query: str) -> list[Record]:
        payload = {"source": source, "query": query, "roots": {k: str(v) for k, v in self.roots.items()}, "limit": self.per_source_limit, "record_chars": self.record_chars}
        output = json.loads(await self._command([sys.executable, str(Path(__file__).resolve()), "--local"], payload))
        if output.get("error"):
            raise SourceUnavailable(output["error"])
        return output["records"]

    async def _http(self, port: int, path: str, *, body: Any = None,
                    headers: Mapping[str, str] | None = None) -> Any:
        if not path.startswith("/") or "\r" in path or "\n" in path:
            raise SourceUnavailable("invalid_endpoint")
        payload = b"" if body is None else json.dumps(body, ensure_ascii=False).encode()
        fields = {"Host": f"127.0.0.1:{port}", "Accept": "application/json", "Connection": "close", **(headers or {})}
        if body is not None:
            fields.update({"Content-Type": "application/json", "Content-Length": str(len(payload))})
        if any("\r" in k + v or "\n" in k + v for k, v in fields.items()):
            raise SourceUnavailable("invalid_headers")
        reader, writer = await asyncio.open_connection("127.0.0.1", port)
        try:
            request = (("GET" if body is None else "POST") + f" {path} HTTP/1.1\r\n" + "".join(f"{k}: {v}\r\n" for k, v in fields.items()) + "\r\n").encode("ascii")
            writer.write(request + payload)
            await writer.drain()
            data = bytearray()
            while True:
                chunk = await reader.read(min(65536, _MAX_BYTES + 1 - len(data)))
                if not chunk:
                    break
                data.extend(chunk)
                if len(data) > _MAX_BYTES:
                    raise SourceUnavailable("output_limit")
            class BufferedSocket:
                def makefile(self, *_args: Any, **_kwargs: Any) -> io.BytesIO:
                    return io.BytesIO(data)
            response = http.client.HTTPResponse(BufferedSocket())
            try:
                response.begin()
                if response.status != 200 or response.getheader("Content-Encoding", "identity") != "identity":
                    raise SourceUnavailable("http_unavailable")
                return json.loads(response.read(_MAX_BYTES + 1))
            finally:
                response.close()
        finally:
            writer.close()
            await writer.wait_closed()

    async def _mcp(self, query: str) -> list[Record]:
        path = self.roots["memory_mcp"]
        info = path.stat()
        if not stat.S_ISSOCK(info.st_mode) or info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) & 0o077:
            raise SourceUnavailable("private_socket_required")
        reader, writer = await asyncio.open_unix_connection(str(path), limit=_MAX_BYTES)
        async def call(number: int, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
            message = {"jsonrpc": "2.0", "id": number, "method": "tools/call", "params": {"name": name, "arguments": arguments}}
            writer.write(json.dumps(message).encode() + b"\n")
            await writer.drain()
            frame = json.loads(await reader.readline())
            if frame.get("id") != number or "error" in frame:
                raise SourceUnavailable("mcp_unavailable")
            blocks = frame.get("result", {}).get("content", [])
            if len(blocks) != 1 or blocks[0].get("type") != "text":
                raise SourceUnavailable("invalid_mcp_response")
            result = json.loads(blocks[0]["text"])
            if result.get("error") or result.get("status") not in ("ok", "empty"):
                raise SourceUnavailable("mcp_denied_or_unavailable")
            return result
        try:
            result = await call(1, "search", {"query": query, "limit": self.per_source_limit})
            records = []
            for n, row in enumerate(result.get("results", [])[:self.per_source_limit], 2):
                original = (await call(n, "fetch", {"id": row["id"]}))["record"]
                if original.get("locator") != row.get("locator") or original.get("source_version") != row.get("source_version"):
                    raise SourceUnavailable("mcp_source_changed")
                records.append(_record(original["source"], original["locator"], original["text"]))
            return records
        finally:
            writer.close()
            await writer.wait_closed()

    async def _tencentdb(self, query: str) -> list[Record]:
        data = await self._http(self.tencentdb_port, "/v3/atomic/search", body={
            "team_id": os.environ.get("TDAI_MEMORY_TEAM_ID", "default"),
            "agent_id": os.environ.get("TDAI_MEMORY_AGENT_ID", "default"),
            "user_id": os.environ.get("TDAI_MEMORY_USER_ID", "default"),
            "query": query, "limit": self.per_source_limit,
        }, headers={"Authorization": "Bearer " + os.environ.get("TDAI_MEMORY_API_KEY", "local"),
                    "x-tdai-service-id": os.environ.get("TDAI_MEMORY_INSTANCE_ID", "default")})
        if data.get("code") != 0 or not isinstance(data.get("data", {}).get("items"), list):
            raise SourceUnavailable("invalid_tencentdb_response")
        # Never manufacture a remote ID from content when native identity is absent.
        return [_record("tencentdb", str(row["id"]), row["content"])
                for row in data["data"]["items"][:self.per_source_limit]
                if isinstance(row, dict) and isinstance(row.get("id"), (str, int)) and not isinstance(row["id"], bool)
                and isinstance(row.get("content"), str) and row["content"]]

    async def _recall(self, query: str) -> list[Record]:
        data = json.loads(await self._command([sys.executable, str(self.roots["recall"]), "search", query, "--limit", str(self.per_source_limit), "--json"]))
        if not any(s.get("ok") for s in data.get("sources", [])):
            raise SourceUnavailable("recall_sources_unavailable")
        records = []
        for row in data.get("hits", []):
            if row.get("pointer") and row.get("snippet"):
                path = row.get("source_path")
                records.append(_record("recall", row["pointer"], row["snippet"], Path(path) if path else None))
        return records

    async def _statepack(self, query: str) -> list[Record]:
        # The CLI defaults to typed_fallback=True. Use its existing API with
        # typed=False so retrieval cannot start local or metered inference.
        script = "import json,runpy,sys; m=runpy.run_path(sys.argv[1]); q=json.load(sys.stdin)['query']; print(json.dumps(m['orient'](q,typed=False)))"
        data = json.loads(await self._command([sys.executable, "-c", script, str(self.roots["statepack"])], {"query": query}))
        coverage = data.get("coverage", {})
        if not data.get("evidence") and coverage.get("provider_errors"):
            raise SourceUnavailable("statepack_sources_unavailable")
        return [_record("statepack", row["source_id"] + ":" + row["locator"], row["excerpt"],
                        Path(row["locator"]) if row["locator"].startswith("/") else None)
                for row in data.get("evidence", []) if all(isinstance(row.get(k), str) and row[k] for k in ("source_id", "locator", "excerpt"))]

    async def _qmd(self, query: str) -> list[Record]:
        # Lexical mode only: no query expansion, embeddings or local inference.
        binary = str(self.roots["qmd"])
        rows = json.loads(await self._command([binary, "search", " ".join(_terms(query)), "--json", "-n", str(self.per_source_limit)]))
        records = []
        for row in rows[:self.per_source_limit]:
            if not isinstance(row.get("docid"), str) or not re.fullmatch(r"#[a-fA-F0-9]{6,64}", row["docid"]):
                continue
            # Read the source, not merely its search snippet. QMD owns resolution.
            text = (await self._command([binary, "get", row["docid"], "--no-line-numbers", "-l", "80"])).decode()
            records.append(_record("qmd", row["docid"], text))
        return records

    async def _expert(self, query: str) -> list[Record]:
        token = os.environ.get("Z0INT_INTENT_UNII_TOKEN")
        if not token:
            raise SourceUnavailable("expert_read_auth_unavailable")
        data = await self._http(self.expert_port, "/export", headers={"Authorization": "Bearer " + token})
        rows = []
        for row in data.get("messages", []):
            if isinstance(row, dict) and type(row.get("id")) is int and isinstance(row.get("text"), str) and row.get("kind") in ("user", "unii", "work", "note"):
                rows.append(_record("unified_expert", str(row["id"]), row["text"]))
        return _rank(rows, query, self.per_source_limit, self.record_chars)


def _rank(rows: Iterable[Record], query: str, limit: int, chars: int) -> list[Record]:
    terms = _terms(query)
    scored = []
    for row in rows:
        text = row["text"]
        low = text.casefold()
        score = sum(term in low for term in terms)
        if score:
            # Excerpt starts near the first relevant term, never invents prose.
            anchor = min(low.find(term) for term in terms if term in low)
            start = max(0, anchor - chars // 4)
            scored.append((score, row["id"], {**row, "text": text[start:start + chars]}))
    scored.sort(key=lambda item: (-item[0], item[1]))
    return [row for _, _, row in scored[:limit]]


def _read_file(path: Path, max_bytes: int = _MAX_FILE_BYTES) -> str:
    # O_NONBLOCK prevents a substituted FIFO/device from hanging a reader.
    fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK)
    with os.fdopen(fd, "rb") as stream:
        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
            raise SourceUnavailable("regular_file_required")
        raw = stream.read(max_bytes + 1)
        if len(raw) > max_bytes:
            raise SourceUnavailable("file_limit")
        return raw.decode("utf-8")


def _read_local(request: Mapping[str, Any]) -> list[Record]:
    source, query = request["source"], request["query"]
    roots = {k: Path(v) for k, v in request["roots"].items()}
    limit, chars = request["limit"], request["record_chars"]
    rows: list[Record] = []
    if source in ("hermes", "state_packet"):
        files = ([p for root in (roots["hermes"], roots["chiefstaff"]) for p in sorted(root.glob("*.md"))[:64]]
                 if source == "hermes" else [roots["state_packet"], roots["unified_index"]])
        available = False
        for path in files:
            if not path.is_file():
                continue
            text = _read_file(path)
            available = True
            # STATE-PACKET headings are text evidence, never a project registry.
            for index, chunk in enumerate(re.split(r"\n\s*\n", text)):
                if chunk.strip():
                    rows.append(_record(source, f"{path}:block:{index}:sha256:{_digest(chunk)}", chunk, path))
        if not available:
            raise SourceUnavailable("source_files_missing")
    elif source == "events":
        # Read only; do not instantiate EventLog (it creates layout/index files).
        path = roots["events"]
        fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK)
        with os.fdopen(fd, "rb") as stream:
            if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                raise SourceUnavailable("regular_file_required")
            while True:
                line = stream.readline(_MAX_BYTES + 1)
                if not line:
                    break
                if len(line) > _MAX_BYTES:
                    raise SourceUnavailable("event_limit")
                if not line.endswith(b"\n"):
                    break  # Canonical log ignores an incomplete trailing append.
                row = json.loads(line)
                checksum = row.get("checksum")
                canonical = json.dumps({k: v for k, v in row.items() if k != "checksum"}, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)
                if row.get("schema") != "z0int.memory.event.v1" or checksum != _digest(canonical) or type(row.get("event_id")) is not int:
                    raise SourceUnavailable("corrupt_event_log")
                payload = row.get("payload")
                if payload is None and row.get("blob") is not None:
                    ref = row["blob"]
                    sha = ref.get("sha256", "")
                    if not isinstance(sha, str) or not re.fullmatch(r"[0-9a-f]{64}", sha):
                        raise SourceUnavailable("invalid_blob_reference")
                    blob_text = _read_file(path.parent / "blobs" / f"{sha}.json", _MAX_BYTES)
                    if _digest(blob_text) != sha or len(blob_text.encode("utf-8")) != ref.get("bytes"):
                        raise SourceUnavailable("corrupt_blob")
                    payload = json.loads(blob_text)
                if payload is None:
                    continue
                text = payload if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False)
                observed = (isinstance(payload, dict) and payload.get("verified") is True and
                            any(isinstance(payload.get(k), str) and payload[k] for k in ("toolCallId", "verifier_receipt_id", "receipt_id")))
                rows.append(_record(source, str(row["event_id"]), text, path,
                                    evidence_kind="observed" if observed else "unknown"))
                # Bound candidate memory without changing the canonical ledger.
                if len(rows) >= 256:
                    rows = _rank(rows, query, limit * 4, chars)
    elif source == "agentsview":
        path = roots["agentsview"] / "sessions.db"
        if not path.is_file():
            raise SourceUnavailable("archive_missing")
        terms = _terms(query)
        if not terms:
            return []
        match = " OR ".join('"' + term.replace('"', '""') + '"' for term in terms)
        with sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=0.1) as db:
            db.execute("PRAGMA query_only=ON")
            hits = db.execute("SELECT m.id,m.session_id,m.ordinal,m.content FROM messages_fts JOIN messages m ON m.id=messages_fts.rowid WHERE messages_fts MATCH ? AND m.role IN ('user','assistant') AND m.is_system=0 AND m.has_thinking=0 AND m.has_tool_use=0 ORDER BY rank,m.id LIMIT ?", (match, limit))
            for identity, session, ordinal, text in hits:
                if isinstance(text, str) and text:
                    rows.append(_record(source, f"agentsview:{session}:message:{identity}:ordinal:{ordinal}", text, path))
    else:
        raise SourceUnavailable("unsupported_local_source")
    return _rank(rows, query, limit, chars)


def _worker() -> None:
    try:
        request = json.loads(sys.stdin.buffer.read(65_537))
        result: dict[str, Any] = {"records": _read_local(request)}
    except Exception as exc:
        result = {"error": str(exc) if isinstance(exc, SourceUnavailable) else type(exc).__name__}
    sys.stdout.write(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__" and sys.argv[1:] == ["--local"]:
    _worker()
