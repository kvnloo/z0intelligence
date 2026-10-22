"""Bounded, read-only adapters for the memory MCP facade (stdlib only).

Evidence for these narrow transports:
* ``agentsview serve --help``: loopback HTTP port 8080 by default.
  ``agentsview openapi``: GET /api/v1/search/content, DbContentMatch fields.
  Authentication is read by reference from ~/.agentsview/config.toml.
* ``ctx search --help`` / ``ctx docs show search``: lexical, refresh=off.
  Workspace filtering is fuzzy; a returned exact workspace/cwd is REQUIRED.
  Versions omitting it cannot safely expose nonempty results through this facade.
* ``qmd search --help``: lexical search, --format json, -n, -c. File identifiers
  are qmd://COLLECTION/path; no get, update, query, or model-backed operation.
* Tencent MemoryCore/hermes-plugin/memory/memory_tencentdb/client.py and
  src/gateway/v2-router.ts: POST /v3/{atomic,conversation}/search is read-only.
  The local gateway's /openapi.json is unsupported. Search responses must carry
  exact tenancy metadata; versions omitting it are unavailable for those hits.

All unclassified local corpora require an explicit *private* policy grant.
These adapters never dereference a returned locator. Redaction and authorization
of cached results remain the facade's responsibility; no raw records are logged.
"""
from __future__ import annotations

import http.client
import io
import json
import math
import os
from pathlib import Path
import re
import select
import selectors
import shutil
import signal
import socket
import subprocess
import time
try:
    import tomllib
except ImportError:  # Python 3.10
    import tomli as tomllib
from typing import Any
from urllib.parse import urlencode, urlsplit


SOURCES = ("agentsview", "ctx", "qmd", "tencentdb")
MAX_RESULTS = 320
MAX_RESPONSE_BYTES = 262_144
MAX_QUERY_BYTES = 4096
MAX_METADATA_BYTES = 2048
CTX_DATA_ROOT = "/mnt/zer0models/host-offload/home_kvn_.ctx"
_TENANT_ID = re.compile(r"[A-Za-z0-9_-]{1,128}\Z")
_COLLECTION = re.compile(r"[A-Za-z0-9_-]{1,128}\Z")


class BackendUnavailable(RuntimeError):
    """A sanitized failure, never containing upstream output or credentials."""


def _remaining(deadline: float) -> float:
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise BackendUnavailable("backend_timeout")
    return remaining


def _decode_json(raw: bytes) -> Any:
    try:
        return json.loads(raw)
    except (ValueError, UnicodeError, RecursionError):
        raise BackendUnavailable("invalid_backend_json") from None


def _clip(value: Any, size: int = MAX_METADATA_BYTES) -> str:
    if not isinstance(value, str):
        return ""
    return value.encode("utf-8", errors="replace")[:size].decode("utf-8", errors="ignore")


def _bounded_command(argv: list[str], deadline: float, *, env: dict[str, str] | None = None) -> Any:
    """Drain only bounded stdout, discard stderr, kill the process group on exit.

    No communicate()/capture_output, shell, refresh, stdin, or disk spool. Run
    outside the caller's checkout so a project-local qmd configuration cannot
    silently select another index. The configured user CLI is the trust boundary.
    """
    executable = shutil.which(argv[0])
    if executable is None:
        raise BackendUnavailable("backend_command_missing")
    _remaining(deadline)
    try:
        process = subprocess.Popen(
            [executable, *argv[1:]], stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, shell=False,
            env=env, cwd=Path.home(), start_new_session=True,
        )
    except (OSError, ValueError):
        raise BackendUnavailable("backend_command_unavailable") from None
    output = bytearray()
    selector = selectors.DefaultSelector()
    try:
        assert process.stdout is not None
        os.set_blocking(process.stdout.fileno(), False)
        selector.register(process.stdout, selectors.EVENT_READ)
        while selector.get_map():
            for key, _ in selector.select(_remaining(deadline)):
                block = os.read(key.fd, min(8192, MAX_RESPONSE_BYTES + 1 - len(output)))
                if not block:
                    selector.unregister(key.fileobj)
                else:
                    output.extend(block)
                    if len(output) > MAX_RESPONSE_BYTES:
                        raise BackendUnavailable("backend_output_limit")
        try:
            status = process.wait(timeout=_remaining(deadline))
        except subprocess.TimeoutExpired:
            raise BackendUnavailable("backend_timeout") from None
        if status != 0:
            raise BackendUnavailable("backend_command_failed")
        return _decode_json(bytes(output))
    except OSError:
        raise BackendUnavailable("backend_command_failed") from None
    finally:
        selector.close()
        # A CLI may leave descendants holding stdout after its leader exits.
        # A dedicated session makes cleanup local to this invocation.
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        if process.stdout is not None:
            process.stdout.close()
        try:
            process.wait(timeout=0.2)
        except subprocess.TimeoutExpired:
            pass


class _BufferedResponseSocket:
    def __init__(self, raw: bytes) -> None:
        self.raw = raw

    def makefile(self, *args: Any, **kwargs: Any) -> io.BytesIO:
        return io.BytesIO(self.raw)


def _loopback_json(port: int, path: str, deadline: float, *,
                   headers: dict[str, str] | None = None,
                   body: dict[str, Any] | None = None) -> Any:
    """Fixed loopback HTTP only, no proxy/redirect/DNS, absolute wall deadline.

    Receive a bounded HTTP message before parsing it in memory. Unlike a socket
    timeout per read, select plus a monotonic deadline also bounds slow-drip
    headers and bodies. Connection: close makes EOF an explicit message boundary.
    """
    if not (
        type(port) is int and 1 <= port <= 65535 and body is None and path.startswith("/api/v1/search/content?")
        or port == 8420 and body is not None and path in (
            "/v3/atomic/search", "/v3/conversation/search",
        )
    ):
        raise BackendUnavailable("unsupported_backend_endpoint")
    method = "POST" if body is not None else "GET"
    payload = json.dumps(body, ensure_ascii=True).encode("utf-8") if body is not None else b""
    request_headers = {
        "Host": f"127.0.0.1:{port}", "Accept": "application/json",
        "Connection": "close", **(headers or {}),
    }
    if body is not None:
        request_headers.update({"Content-Type": "application/json", "Content-Length": str(len(payload))})
    if any("\r" in key + value or "\n" in key + value for key, value in request_headers.items()):
        raise BackendUnavailable("invalid_backend_authentication")
    try:
        request = (f"{method} {path} HTTP/1.1\r\n" + "".join(
            f"{key}: {value}\r\n" for key, value in request_headers.items()
        ) + "\r\n").encode("ascii") + payload
    except UnicodeError:
        raise BackendUnavailable("invalid_backend_authentication") from None
    wire = bytearray()
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=_remaining(deadline)) as connection:
            connection.settimeout(_remaining(deadline))
            connection.sendall(request)
            connection.setblocking(False)
            while True:
                readable, _, _ = select.select([connection], [], [], _remaining(deadline))
                if not readable:
                    raise BackendUnavailable("backend_timeout")
                block = connection.recv(min(8192, MAX_RESPONSE_BYTES + 1 - len(wire)))
                if not block:
                    break
                wire.extend(block)
                if len(wire) > MAX_RESPONSE_BYTES:
                    raise BackendUnavailable("backend_output_limit")
        response = http.client.HTTPResponse(_BufferedResponseSocket(bytes(wire)))
        try:
            response.begin()
            if response.status != 200:
                # 3xx is a failure, never followed, even to another loopback URL.
                raise BackendUnavailable("backend_http_unavailable")
            if response.getheader("Content-Encoding", "identity").lower() != "identity":
                raise BackendUnavailable("unsupported_backend_encoding")
            raw = response.read(MAX_RESPONSE_BYTES + 1)
            if len(raw) > MAX_RESPONSE_BYTES:
                raise BackendUnavailable("backend_output_limit")
            return _decode_json(raw)
        finally:
            response.close()
    except (OSError, ValueError, UnicodeError, http.client.HTTPException):
        raise BackendUnavailable("backend_http_unavailable") from None


def _rows(value: Any, key: str | None = None) -> list[dict[str, Any]]:
    if key is not None:
        if not isinstance(value, dict):
            raise BackendUnavailable("unsupported_backend_shape")
        value = value.get(key)
    if not isinstance(value, list) or any(not isinstance(row, dict) for row in value):
        raise BackendUnavailable("unsupported_backend_shape")
    return value[:MAX_RESULTS]


def _record(source: str, grant: dict[str, Any], row: dict[str, Any], *,
            text: Any, locator: Any, date: Any = None, host: Any = None) -> dict[str, str] | None:
    # No native privacy taxonomy is verified for these transports. An operator
    # may classify the grant private, never public. Explicit secret/unknown
    # native privacy markings still deny rather than being reclassified.
    privacy = row.get("privacy")
    visibility = row.get("visibility")
    if privacy is not None and privacy not in ("public", "personal", "private"):
        return None
    if isinstance(visibility, str) and visibility.lower() in ("secret", "restricted", "confidential"):
        return None
    if not isinstance(text, str) or not text:
        return None
    result = {
        "source": source, "collection": grant["collection"], "scope": grant["scope"],
        "privacy": "private", "host": host if isinstance(host, str) and host else "unknown",
        "date": date if isinstance(date, str) and date else "unknown",
        # Do not truncate secrets before the facade redacts them. The entire
        # upstream response already has a hard 256 KiB transport bound.
        "text": text, "locator": _clip(locator) or "unknown",
    }
    return result


def _agentsview(query: str, grant: dict[str, Any], limit: int, deadline: float) -> list[dict[str, str]]:
    if grant["collection"] != "history":
        raise BackendUnavailable("unsupported_backend_collection")
    try:
        with (Path.home() / ".agentsview/config.toml").open("rb") as handle:
            config_bytes = handle.read(65_537)
        if len(config_bytes) > 65_536:
            raise BackendUnavailable("backend_configuration_limit")
        config = tomllib.loads(config_bytes.decode("utf-8"))
    except (OSError, ValueError, UnicodeError):
        raise BackendUnavailable("agentsview_configuration_unavailable") from None
    token = config.get("auth_token")
    if not isinstance(token, str) or not token or len(token) > 8192:
        raise BackendUnavailable("agentsview_authentication_unavailable")
    port = config.get("port", 8080)
    if type(port) is not int or not 1 <= port <= 65535:
        raise BackendUnavailable("agentsview_configuration_unavailable")
    path = "/api/v1/search/content?" + urlencode({
        "pattern": query, "mode": "fts", "project": grant["scope"],
        "limit": limit, "context": 0, "reveal": "false",
    })
    value = _loopback_json(port, path, deadline, headers={"Authorization": "Bearer " + token})
    records = []
    for row in _rows(value, "matches")[:limit]:
        if row.get("project") != grant["scope"]:
            continue
        session = row.get("session_id")
        ordinal = row.get("ordinal")
        locator = f"agentsview:{session}:{ordinal}" if isinstance(session, str) and type(ordinal) is int else None
        record = _record("agentsview", grant, row, text=row.get("snippet"), locator=locator,
                         date=row.get("timestamp"), host=row.get("machine"))
        if record:
            records.append(record)
    return records


def _ctx(query: str, grant: dict[str, Any], limit: int, deadline: float) -> list[dict[str, str]]:
    if grant["collection"] != "history":
        raise BackendUnavailable("unsupported_backend_collection")
    value = _bounded_command([
        "ctx", "search", "--format", "json", "--refresh", "off", "--backend", "lexical",
        "--workspace", grant["scope"], "--limit", str(min(limit, 200)), "--", query,
    ], deadline, env={**os.environ, "CTX_DATA_ROOT": CTX_DATA_ROOT})
    records = []
    for row in _rows(value, "results")[:limit]:
        # --workspace matches repo-name/path text, not an authorization boundary.
        workspace = row.get("workspace") or row.get("cwd")
        if not isinstance(workspace, str) or not workspace:
            raise BackendUnavailable("ctx_exact_scope_metadata_unavailable")
        if workspace != grant["scope"]:
            continue
        event = row.get("ctx_event_id")
        locator = "ctx:event:" + event if isinstance(event, str) and event else None
        record = _record("ctx", grant, row, text=row.get("snippet"), locator=locator,
                         date=row.get("timestamp"), host=row.get("host"))
        if record:
            records.append(record)
    return records


def _qmd(query: str, grant: dict[str, Any], limit: int, deadline: float) -> list[dict[str, str]]:
    collection = grant["collection"]
    if not _COLLECTION.fullmatch(collection) or grant["scope"] != "qmd://" + collection:
        raise BackendUnavailable("unsupported_qmd_scope")
    value = _bounded_command([
        "qmd", "search", "--format", "json", "-n", str(limit), "-c", collection, "--", query,
    ], deadline)
    records = []
    for row in _rows(value)[:limit]:
        locator = row.get("file")
        if not isinstance(locator, str):
            raise BackendUnavailable("qmd_exact_scope_metadata_unavailable")
        parsed = urlsplit(locator)
        if parsed.scheme != "qmd" or parsed.netloc != collection or not parsed.path.startswith("/"):
            continue
        record = _record("qmd", grant, row, text=row.get("snippet"), locator=locator,
                         date=row.get("date"), host="unknown")
        if record:
            records.append(record)
    return records


def _tencentdb(query: str, grant: dict[str, Any], limit: int, deadline: float) -> list[dict[str, str]]:
    collection = grant["collection"]
    if collection not in ("atomic", "conversation"):
        raise BackendUnavailable("unsupported_backend_collection")
    parts = grant["scope"].split(";")
    if len(parts) != 3 or any(not part.startswith(prefix) for part, prefix in zip(parts, ("team=", "agent=", "user="))):
        raise BackendUnavailable("unsupported_tencentdb_scope")
    identifiers = [part.split("=", 1)[1] for part in parts]
    if any(not _TENANT_ID.fullmatch(identifier) for identifier in identifiers):
        raise BackendUnavailable("unsupported_tencentdb_scope")
    tenancy = dict(zip(("team_id", "agent_id", "user_id"), identifiers))
    token = os.environ.get("MEMORY_TENCENTDB_GATEWAY_API_KEY") or os.environ.get("TDAI_GATEWAY_API_KEY") or "local"
    if len(token) > 8192:
        raise BackendUnavailable("tencentdb_authentication_unavailable")
    value = _loopback_json(
        8420, f"/v3/{collection}/search", deadline,
        headers={"Authorization": "Bearer " + token, "x-tdai-service-id": "default"},
        body={**tenancy, "query": query, "limit": limit},
    )
    if not isinstance(value, dict) or value.get("code") != 0 or not isinstance(value.get("data"), dict):
        raise BackendUnavailable("tencentdb_search_unavailable")
    records = []
    for row in _rows(value["data"], "items" if collection == "atomic" else "messages")[:limit]:
        if any(not isinstance(row.get(key), str) or not row[key] for key in tenancy):
            raise BackendUnavailable("tencentdb_exact_scope_metadata_unavailable")
        if any(row[key] != expected for key, expected in tenancy.items()):
            continue
        identifier = row.get("id")
        locator = f"tencentdb:{collection}:{identifier}" if isinstance(identifier, str) and identifier else None
        record = _record("tencentdb", grant, row, text=row.get("content"), locator=locator,
                         date=row.get("updated_at") or row.get("created_at") or row.get("timestamp"),
                         host="127.0.0.1")
        if record:
            records.append(record)
    return records


def search_backend(source: str, query: str, grants: list[dict[str, Any]], limit: int,
                   timeout_s: float) -> list[dict[str, str]]:
    """Search granted exact scopes; one absolute timeout covers all source work.

    At most 320 records and 256 KiB wire/stdout per request. Unsupported
    transports/metadata raise only sanitized fixed errors.
    Grants never allow arbitrary executables, filesystem reads, get paths, URLs,
    API methods, remote hosts, refreshes, semantic inference, or public relabeling.
    """
    if source not in SOURCES:
        raise BackendUnavailable("unsupported_backend")
    if not isinstance(query, str) or not query.strip() or len(query.encode("utf-8")) > MAX_QUERY_BYTES:
        raise BackendUnavailable("invalid_backend_query")
    if type(limit) is not int or not 1 <= limit <= MAX_RESULTS:
        raise BackendUnavailable("invalid_backend_limit")
    if type(timeout_s) not in (int, float) or not math.isfinite(timeout_s) or not 0 < timeout_s <= 30:
        raise BackendUnavailable("invalid_backend_timeout")
    if not isinstance(grants, list) or len(grants) > MAX_RESULTS:
        raise BackendUnavailable("invalid_backend_grants")
    selected = []
    for grant in grants:
        if not isinstance(grant, dict) or grant.get("source") != source:
            continue
        if any(not isinstance(grant.get(key), str) or not grant[key] or
               len(grant[key].encode("utf-8")) > MAX_METADATA_BYTES for key in ("collection", "scope")):
            continue
        privacy = grant.get("privacy")
        if not isinstance(privacy, list) or not privacy or any(p not in ("public", "personal", "private") for p in privacy):
            continue
        if "private" not in privacy:
            continue
        selected.append(grant)
    deadline = time.monotonic() + timeout_s
    adapter = {"agentsview": _agentsview, "ctx": _ctx, "qmd": _qmd, "tencentdb": _tencentdb}[source]
    records = []
    for grant in selected:
        if len(records) >= limit:
            break
        _remaining(deadline)
        records.extend(adapter(query, grant, limit - len(records), deadline))
    return records[:limit]
