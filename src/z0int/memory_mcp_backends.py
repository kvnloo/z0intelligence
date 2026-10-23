"""Read-only, exact-source AgentsView adapter for the memory MCP.

The verified AgentsView HTTP contract is documented at
https://www.agentsview.io/docs/session-api/:
GET /api/v1/search/content and GET /api/v1/sessions/{id}/messages.
Search indexes dialogue; fetch re-reads the exact archived message, not a snippet.
The archive's normalized message content (UTF-8) is the revision-bound evidence,
not a claim about the original provider JSONL file.

ctx, QMD and TencentDB remain intentionally unavailable from this MCP until
their own authorized exact-source read APIs are independently qualified.
"""
from __future__ import annotations

import hashlib
import http.client
import io
import json
import math
from pathlib import Path
import re
import select
import socket
import time
try:
    import tomllib
except ImportError:  # pragma: no cover - Python 3.10
    import tomli as tomllib
from typing import Any
from urllib.parse import quote, urlencode


SOURCES = ("agentsview", "ctx", "qmd", "tencentdb")
MAX_RESPONSE_BYTES = 262_144
MAX_QUERY_BYTES = 1024
_SESSION_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:~@+-]{0,200}\Z")
_CONTENT_PATH = re.compile(r"/api/v1/search/content\?[^\r\n#]*\Z")
_DETAIL_PATH = re.compile(r"/api/v1/sessions/(?:[A-Za-z0-9._~-]|%(?:3A|40|2B)){1,603}\Z")
_MESSAGE_PATH = re.compile(
    r"/api/v1/sessions/(?:[A-Za-z0-9._~-]|%(?:3A|40|2B)){1,603}/messages\?around=[0-9]{1,10}&before=0&after=0\Z"
)


class BackendUnavailable(RuntimeError):
    """Fixed-code failure; upstream exception text must never cross the facade."""


class BackendStale(BackendUnavailable):
    """A previously addressed native original is no longer at that cursor."""


class BackendDenied(BackendUnavailable):
    """The native source no longer proves the exact authorized scope or class."""


class BackendIdentityDenied(BackendDenied):
    """A session or stable message identity is missing, foreign, or replaced."""


def _remaining(deadline: float) -> float:
    left = deadline - time.monotonic()
    if left <= 0:
        raise BackendUnavailable("deadline")
    return left


class _BufferedSocket:
    def __init__(self, data: bytes) -> None:
        self.data = data

    def makefile(self, *args: Any, **kwargs: Any) -> io.BytesIO:
        return io.BytesIO(self.data)


def _loopback_json(port: int, path: str, deadline: float, *,
                   headers: dict[str, str] | None = None) -> Any:
    """Only three fixed, read-only localhost routes. No URL, proxy, or redirect."""
    if (type(port) is not int or not 1 <= port <= 65535 or
            not any(pattern.fullmatch(path) for pattern in
                    (_CONTENT_PATH, _DETAIL_PATH, _MESSAGE_PATH))):
        raise BackendUnavailable("unsupported_endpoint")
    request_headers = {
        "Host": f"127.0.0.1:{port}",
        "Accept": "application/json",
        "Connection": "close",
        **(headers or {}),
    }
    if any(not isinstance(k, str) or not isinstance(v, str) or
           "\r" in k + v or "\n" in k + v for k, v in request_headers.items()):
        raise BackendUnavailable("invalid_auth")
    try:
        request = ("GET " + path + " HTTP/1.1\r\n" +
                   "".join(f"{k}: {v}\r\n" for k, v in request_headers.items()) +
                   "\r\n").encode("ascii")
    except UnicodeError:
        raise BackendUnavailable("invalid_auth") from None
    received = bytearray()
    try:
        with socket.create_connection(("127.0.0.1", port),
                                      timeout=_remaining(deadline)) as connection:
            connection.settimeout(_remaining(deadline))
            connection.sendall(request)
            connection.setblocking(False)
            while True:
                ready, _, _ = select.select([connection], [], [], _remaining(deadline))
                if not ready:
                    raise BackendUnavailable("deadline")
                chunk = connection.recv(min(8192, MAX_RESPONSE_BYTES + 1 - len(received)))
                if not chunk:
                    break
                received.extend(chunk)
                if len(received) > MAX_RESPONSE_BYTES:
                    raise BackendUnavailable("output_limit")
        response = http.client.HTTPResponse(_BufferedSocket(bytes(received)))
        try:
            response.begin()
            if response.status == 404 and (_DETAIL_PATH.fullmatch(path) or _MESSAGE_PATH.fullmatch(path)):
                raise BackendStale("native_original_missing")
            if response.status != 200 or response.getheader("Content-Encoding", "identity").lower() != "identity":
                raise BackendUnavailable("http_unavailable")
            payload = response.read(MAX_RESPONSE_BYTES + 1)
            if len(payload) > MAX_RESPONSE_BYTES:
                raise BackendUnavailable("output_limit")
            return json.loads(payload)
        finally:
            response.close()
    except (OSError, ValueError, UnicodeError, RecursionError, http.client.HTTPException):
        raise BackendUnavailable("http_unavailable") from None


def _agentsview_config() -> tuple[int, dict[str, str]]:
    try:
        with (Path.home() / ".agentsview" / "config.toml").open("rb") as stream:
            data = stream.read(65_537)
        if len(data) > 65_536:
            raise BackendUnavailable("config_limit")
        config = tomllib.loads(data.decode("utf-8"))
    except (OSError, UnicodeError, ValueError, TypeError):
        raise BackendUnavailable("config_unavailable") from None
    port = config.get("port", 8080)
    token = config.get("auth_token")
    if type(port) is not int or not 1 <= port <= 65535 or not isinstance(token, str) or not 0 < len(token) <= 8192:
        raise BackendUnavailable("config_unavailable")
    return port, {"Authorization": "Bearer " + token}


def _supported_grant(grant: dict[str, Any]) -> None:
    if (grant.get("source") != "agentsview" or
            grant.get("collection") != "history" or
            not isinstance(grant.get("scope"), str) or not grant["scope"] or
            grant["scope"].strip().casefold() in {"*", "unknown", "none", "null"} or
            "private" not in grant.get("privacy", [])):
        raise BackendUnavailable("unsupported_grant")


def _native_id(session_id: Any, ordinal: Any) -> dict[str, Any]:
    if (not isinstance(session_id, str) or not _SESSION_ID.fullmatch(session_id) or
            type(ordinal) is not int or not 0 <= ordinal <= 2_147_483_647):
        raise BackendUnavailable("unsupported_native_identity")
    return {"session_id": session_id, "ordinal": ordinal}


def _message_id(value: Any) -> int:
    """AgentsView db.Message.id is a positive signed int64, not an ordinal."""
    if type(value) is not int or not 1 <= value <= (1 << 63) - 1:
        raise BackendIdentityDenied("native_message_id_denied")
    return value


def agentsview_locator(native: Any) -> str:
    """Lossless native locator; both stable ID and ordinal survive content hashing."""
    if not isinstance(native, dict) or set(native) != {"session_id", "ordinal", "id"}:
        raise BackendIdentityDenied("native_identity_denied")
    try:
        address = _native_id(native.get("session_id"), native.get("ordinal"))
    except BackendUnavailable:
        raise BackendIdentityDenied("native_identity_denied") from None
    message_id = _message_id(native.get("id"))
    locator = (
        f"agentsview:{address['session_id']}:message:{message_id}:"
        f"ordinal:{address['ordinal']}"
    )
    # A long native identifier must be denied, never truncated into a collision.
    if len(locator.encode("utf-8")) > 256:
        raise BackendIdentityDenied("native_locator_too_long")
    return locator


def _original(grant: dict[str, Any], native: dict[str, Any],
              port: int, headers: dict[str, str], deadline: float) -> dict[str, Any]:
    """Verify native project both before and after the bounded original read."""
    _supported_grant(grant)
    if not isinstance(native, dict):
        raise BackendIdentityDenied("native_identity_denied")
    # Search starts with a session/ordinal cursor. Fetch must carry the stable
    # native ID returned by the first original read, not just that cursor.
    expected_id = _message_id(native["id"]) if "id" in native else None
    address = _native_id(native.get("session_id"), native.get("ordinal"))
    session_id, ordinal = address["session_id"], address["ordinal"]
    segment = quote(session_id, safe="")
    detail_path = f"/api/v1/sessions/{segment}"
    message_path = f"/api/v1/sessions/{segment}/messages?around={ordinal}&before=0&after=0"

    def check_scope() -> dict[str, Any]:
        session = _loopback_json(port, detail_path, deadline, headers=headers)
        if not isinstance(session, dict) or session.get("id") != session_id or session.get("project") != grant["scope"]:
            raise BackendDenied("scope_denied")
        return session

    session = check_scope()
    page = _loopback_json(port, message_path, deadline, headers=headers)
    if not isinstance(page, dict) or not isinstance(page.get("messages"), list):
        raise BackendUnavailable("invalid_messages")
    matches = [item for item in page["messages"] if isinstance(item, dict) and
               item.get("ordinal") == ordinal and type(item.get("ordinal")) is int]
    if len(page["messages"]) != 1 or len(matches) != 1:
        raise BackendStale("message_missing")
    message = matches[0]
    # This check precedes reading *any* content or display metadata. A matching
    # ordinal from another session is not the requested source, and an ID swap
    # with identical text is still a distinct native message.
    message_id = _message_id(message.get("id"))
    if (message.get("session_id") != session_id or
            (expected_id is not None and message_id != expected_id)):
        raise BackendIdentityDenied("native_identity_denied")
    proven_native = {**address, "id": message_id}
    locator = agentsview_locator(proven_native)
    content = message.get("content")
    # The full session content may contain rendered thinking/tool blocks, while
    # FTS search snippets do not. Exclude those originals rather than exposing
    # reasoning or tool arguments through exact hydration.
    if (message.get("role") not in ("user", "assistant") or
            message.get("is_system") is not False or
            message.get("has_thinking") is not False or
            message.get("has_tool_use") is not False or
            message.get("thinking_text") != "" or
            not isinstance(content, str) or not content or
            "[Thinking]" in content or "[/Thinking]" in content or
            (message.get("privacy") not in (None, "private"))):
        raise BackendDenied("message_class_denied")
    check_scope()
    return {
        "source": "agentsview", "collection": "history",
        "scope": grant["scope"], "privacy": "private",
        "host": session.get("machine") if isinstance(session.get("machine"), str) else "unknown",
        "date": message.get("timestamp") if isinstance(message.get("timestamp"), str) else "unknown",
        "text": content, "locator": locator, "native": proven_native,
        "source_version": "sha256:" + hashlib.sha256(content.encode("utf-8")).hexdigest(),
    }


def search_backend(source: str, query: str, grants: list[dict[str, Any]], limit: int,
                   timeout_s: float) -> list[dict[str, Any]]:
    """Search and prove originals under ONE absolute deadline per source."""
    if source != "agentsview":
        raise BackendUnavailable("exact_source_unavailable")
    if (not isinstance(query, str) or not query.strip() or
            len(query.encode("utf-8")) > MAX_QUERY_BYTES or
            type(limit) is not int or not 1 <= limit <= 10 or
            type(timeout_s) not in (int, float) or
            not math.isfinite(timeout_s) or not 0 < timeout_s <= 10):
        raise BackendUnavailable("invalid_arguments")
    deadline = time.monotonic() + timeout_s
    port, headers = _agentsview_config()
    output: list[dict[str, Any]] = []
    if not isinstance(grants, list) or len(grants) > 32:
        raise BackendUnavailable("invalid_grants")
    for grant in grants:
        _supported_grant(grant)
        path = "/api/v1/search/content?" + urlencode({
            "pattern": query, "mode": "fts", "in": "messages",
            "exclude_system": "true", "project": grant["scope"],
            "limit": limit, "context": 0, "reveal": "false",
        })
        page = _loopback_json(port, path, deadline, headers=headers)
        if not isinstance(page, dict) or not isinstance(page.get("matches"), list):
            raise BackendUnavailable("invalid_matches")
        for row in page["matches"][:limit]:
            if not isinstance(row, dict) or row.get("project") != grant["scope"] or row.get("location") != "message":
                continue
            try:
                native = _native_id(row.get("session_id"), row.get("ordinal"))
                original = _original(grant, native, port, headers, deadline)
            except BackendIdentityDenied:
                raise  # No candidate can be disclosed after an identity violation.
            except BackendDenied:
                continue
            except BackendUnavailable as exc:
                if str(exc) == "unsupported_native_identity":
                    continue
                raise
            output.append(original)
            if len(output) >= limit:
                return output
    return output


def fetch_backend(source: str, identity: dict[str, Any], grant: dict[str, Any],
                  timeout_s: float) -> dict[str, Any]:
    """Re-authorize and re-read one source-native original; never use a snippet."""
    if source != "agentsview":
        raise BackendUnavailable("exact_source_unavailable")
    _supported_grant(grant)
    if not isinstance(identity, dict) or identity.get("locator") != agentsview_locator(identity.get("native")):
        raise BackendIdentityDenied("native_identity_denied")
    if type(timeout_s) not in (int, float) or not math.isfinite(timeout_s) or not 0 < timeout_s <= 10:
        raise BackendUnavailable("invalid_timeout")
    deadline = time.monotonic() + timeout_s
    port, headers = _agentsview_config()
    return _original(grant, identity["native"], port, headers, deadline)
