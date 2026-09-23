"""One harness-facing MCP surface for the z0 kernel.

A dependency-free stdio MCP server (JSON-RPC 2.0, newline-delimited) that exposes
the canonical primitives rather than a second retrieval stack. Every tool is a
thin projection of :mod:`z0int.context_resolve` and
:mod:`z0int.context_providers`; nothing is implemented here that those modules do
not already own.

This replaces the transitional `tools/recall_mcp.py` union, which exposed a
*sources* view of the index set. Harnesses should not need to know which indexes
exist.

Tools
-----
``resolve``   needs/query -> full ContextPacket (evidence, gaps, recipe)
``orient``    the same resolution, summarized (packet is large; this is the
              one-shot priming call)
``history``   lexical conversation/tool evidence only, no fact hop
``inspect``   bounded read of one evidence locator's source line
``unknowns``  only the unresolved gaps and contradictions for a query
``verify``    evidence-sufficiency verdict: ABSTAIN when declared-required
              evidence is not present

Design rules carried over from the resolver:

* every result carries provenance (source_id, source_version, locator, trust);
* a failure is reported as ``{ok: false, error}``, never as an empty success;
* no tool loads a model, and none makes a network model call.
"""

from __future__ import annotations

import json
import os
import sys
from typing import Any

SCHEMA = "z0int.mcp.v1"
PROTOCOL_VERSION = "2024-11-05"

TOOLS: list[dict[str, Any]] = [
    {
        "name": "resolve",
        "description": (
            "Find the evidence that bears on a concrete memory question and return it with "
            "locators. Infers the exact values the question needs (PATH, MODEL, "
            "CONFIG_VALUE, COMMAND, URL, REVISION, IDENTIFIER, CLAIM, ...), races the cheap "
            "evidence providers, and returns a candidate value only when it occurs in "
            "returned evidence. NOTE: occurrence in evidence is not proof -- a value can be "
            "real and still not answer the question. Treat a returned value as a lead and "
            "call inspect(locator) to read the passage before relying on it. Set fast=false "
            "(or pass explicit typed needs) to get the full compiled packet instead."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "the concrete memory question"},
                "needs": {"type": "array", "items": {"type": ["string", "object"]}},
                "fast": {"type": "boolean", "default": True,
                         "description": "false forces the full resolver packet"},
                "allow_model": {"type": "boolean", "default": True,
                                "description": "let a small decision backend pick the retrieval operator when rules are silent"},
                "fallback": {"type": "boolean", "default": True,
                             "description": "fall back to the full resolver when slots stay unresolved"},
                "task_id": {"type": "string"},
                "project_root": {"type": "string"},
                "typed_fallback": {"type": "boolean", "default": True},
                "use_cache": {"type": "boolean", "default": True},
            },
        },
    },
    {
        "name": "orient",
        "description": "One-shot priming call: resolve a query and return a compact orientation.",
        "inputSchema": {
            "type": "object",
            "properties": {"query": {"type": "string"}, "limit": {"type": "integer", "default": 8}},
            "required": ["query"],
        },
    },
    {
        "name": "history",
        "description": "Lexical conversation and tool-output evidence for a query (no fact hop).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "limit": {"type": "integer", "default": 8},
                "providers": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["query"],
        },
    },
    {
        "name": "inspect",
        "description": (
            "Open one evidence locator returned by resolve/history and read the original "
            "passage in context: the target row plus neighbouring messages with roles and "
            "timestamps, and a stable citation. Use this after resolve to read the sentence "
            "that decides the answer. Read-only; known secrets are redacted. Locators "
            "resolve/history return are index locators (agentsview:, coverage:, "
            "tool_calls#, messages#, ...), not only filesystem paths."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "locator": {"type": "string"},
                "chars": {"type": "integer", "default": 400,
                          "description": "max characters returned per message"},
                "context": {"type": "integer", "default": 2,
                            "description": (
                                "how many neighbouring messages to include on each side. "
                                "Raise it when the answer may have been superseded later "
                                "in the same session -- a passage that looks decisive in "
                                "isolation is often corrected a few turns on."
                            )},
            },
            "required": ["locator"],
        },
    },
    {
        "name": "unknowns",
        "description": "Only the unresolved gaps and contradictions for a query.",
        "inputSchema": {
            "type": "object",
            "properties": {"query": {"type": "string"}},
            "required": ["query"],
        },
    },
    {
        "name": "verify",
        "description": (
            "Evidence-sufficiency verdict for a query: ABSTAIN when the declared-required "
            "evidence is absent, rather than returning a confident partial answer."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "requires": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "substrings that must be present in the evidence",
                },
            },
            "required": ["query"],
        },
    },
]


def _packet_payload(packet: Any) -> dict[str, Any]:
    return packet.to_dict()


def _tool_resolve(args: dict[str, Any]) -> dict[str, Any]:
    """Concrete memory question -> first complete verified answer, least work.

    `resolve` is the fast path: infer the requested slots, race the cheap
    providers in measured-latency order, verify candidates against evidence, and
    return as soon as every required slot is filled. Unfinished questions fall
    through to the full resolver, which is what keeps the coverage win.

    `fast: false`, or an explicit typed `needs` list, forces the full packet --
    use that when the caller wants a compiled context rather than an answer.
    """
    from z0int.context_resolve import needs_from_mapping, resolve_context, resolve_fast

    needs = None
    if args.get("needs"):
        needs = needs_from_mapping(args["needs"])
    use_fast = bool(args.get("fast", True)) and needs is None and bool(args.get("query"))
    if use_fast:
        answer = resolve_fast(
            str(args["query"]),
            allow_model=bool(args.get("allow_model", True)),
            fallback=bool(args.get("fallback", True)),
        )
        out = answer.to_dict()
        out["schema"] = SCHEMA
        return out
    packet = resolve_context(
        needs=needs,
        query=args.get("query"),
        task_id=args.get("task_id"),
        project_root=args.get("project_root"),
        typed_fallback=bool(args.get("typed_fallback", True)),
        use_cache=bool(args.get("use_cache", True)),
    )
    out = _packet_payload(packet)
    out["schema"] = SCHEMA
    return out


def _tool_orient(args: dict[str, Any]) -> dict[str, Any]:
    from z0int.context_resolve import resolve_context

    limit = int(args.get("limit") or 8)
    packet = resolve_context(query=str(args["query"]), use_cache=True)
    return {
        "schema": SCHEMA,
        "op": "orient",
        "query": args["query"],
        "evidence_count": len(packet.evidence),
        "unresolved_gaps": list(packet.unresolved_gaps),
        "contradictions": list(packet.contradictions),
        "recipe_signature": packet.recipe.request_signature if packet.recipe else None,
        "measurements": dict(packet.measurements),
        "evidence": [e.to_dict() for e in packet.evidence[:limit]],
        "evidence_omitted": max(0, len(packet.evidence) - limit),
    }


def _tool_history(args: dict[str, Any]) -> dict[str, Any]:
    from z0int import context_providers as cp

    only = tuple(args["providers"]) if args.get("providers") else None
    res = cp.search_lexical(str(args["query"]), limit=int(args.get("limit") or 8), only=only)
    return {
        "schema": SCHEMA,
        "op": "history",
        "query": args["query"],
        "hits": [
            {
                "provider": h.provider,
                "locator": h.locator,
                "trust_class": h.trust_class,
                "session_id": h.session_id,
                "timestamp": h.timestamp,
                "tool_name": h.tool_name,
                "excerpt": h.excerpt,
                "bridge": h.bridge,
            }
            for h in res.hits
        ],
        "providers": [
            {"name": s.provider, "ok": s.ok, "hits": s.hits,
             "wall_ms": round(s.wall_ms, 1), "error": s.error}
            for s in res.status
        ],
    }


def _tool_inspect(args: dict[str, Any]) -> dict[str, Any]:
    """Bounded read of the source row behind a locator, with its surroundings.

    This is the *reading* half of memory: `resolve`/`history` return locators,
    and this opens the passage they point at so a caller can read the sentence
    that decides the answer instead of re-running a search.

    Returns the target row plus neighbouring rows with roles and timestamps, and
    a stable citation. Known secret spans and credential shapes are redacted on
    the way out; the read is read-only.

    Only locators this system produced are understood; anything else is an
    explicit error rather than a guess.
    """
    from z0int.context_providers import read_locator
    from z0int.context_resolve import _fetch_exact_path

    locator = str(args["locator"])
    chars = int(args.get("chars") or 400)
    if locator.startswith("file:") or locator.startswith("/"):
        ref = _fetch_exact_path(locator.removeprefix("file:"), None)
        if ref is None:
            return {"schema": SCHEMA, "op": "inspect", "ok": False,
                    "error": f"cannot read {locator}"}
        return {"schema": SCHEMA, "op": "inspect", "ok": True, "kind": "path",
                "locator": ref.locator, "source_version": ref.source_version,
                "excerpt": (ref.excerpt or "")[:chars], "context": []}

    read = read_locator(locator, chars=chars, siblings=int(args.get("context") or 2))
    if read is None:
        return {
            "schema": SCHEMA, "op": "inspect", "ok": False,
            "error": (
                f"unsupported locator {locator!r}. Expected a locator produced by "
                "resolve/history/inspect (agentsview:, claude-extra:, misc-extra:, "
                "hermes/<profile>:, anthropic:, coverage:, tool_calls#, "
                "tool_result_events#, messages#) or a filesystem path."
            ),
        }
    read["schema"] = SCHEMA
    read["op"] = "inspect"
    return read


def _tool_unknowns(args: dict[str, Any]) -> dict[str, Any]:
    from z0int.context_resolve import resolve_context

    packet = resolve_context(query=str(args["query"]), use_cache=True)
    return {
        "schema": SCHEMA,
        "op": "unknowns",
        "query": args["query"],
        "unresolved_gaps": list(packet.unresolved_gaps),
        "contradictions": list(packet.contradictions),
        "answerable": not packet.unresolved_gaps,
        "evidence_count": len(packet.evidence),
    }


def _tool_verify(args: dict[str, Any]) -> dict[str, Any]:
    """Abstain rather than answer from insufficient evidence.

    The rule is deliberately mechanical and stated in the output: a verdict of
    ``USE`` requires every declared ``requires`` string to be grounded in a
    returned evidence excerpt or locator. With no ``requires`` given, the verdict
    is ``FALLBACK`` whenever the resolver reported any unresolved gap.
    """
    from z0int.context_resolve import resolve_context

    packet = resolve_context(query=str(args["query"]), use_cache=True)
    blob = " ".join(
        f"{e.locator} {e.excerpt or ''}" for e in packet.evidence
    ).lower()
    requires = [str(r) for r in (args.get("requires") or [])]
    missing = [r for r in requires if r.lower() not in blob]
    if requires:
        verdict = "USE" if not missing else "FALLBACK"
    else:
        verdict = "FALLBACK" if packet.unresolved_gaps else "USE"
    return {
        "schema": SCHEMA,
        "op": "verify",
        "query": args["query"],
        "verdict": verdict,
        "requires": requires,
        "missing": missing,
        "grounded": len(requires) - len(missing),
        "evidence_count": len(packet.evidence),
        "unresolved_gaps": list(packet.unresolved_gaps),
        "rule": (
            "USE iff every declared requirement is grounded in returned evidence; "
            "otherwise FALLBACK to raw evidence"
        ),
    }


HANDLERS = {
    "resolve": _tool_resolve,
    "orient": _tool_orient,
    "history": _tool_history,
    "inspect": _tool_inspect,
    "unknowns": _tool_unknowns,
    "verify": _tool_verify,
}


def _ok(req_id: Any, result: Any) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": req_id, "result": result}


def _err(req_id: Any, code: int, message: str) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": req_id, "error": {"code": code, "message": message}}


def handle(req: dict[str, Any]) -> dict[str, Any] | None:
    """Handle one JSON-RPC request. Returns None for notifications."""
    method = req.get("method")
    req_id = req.get("id")
    params = req.get("params") or {}

    if method == "initialize":
        return _ok(req_id, {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "z0int", "version": "0.1.0"},
        })
    if method in ("notifications/initialized", "initialized"):
        return None
    if method == "tools/list":
        return _ok(req_id, {"tools": TOOLS})
    if method == "tools/call":
        name = params.get("name")
        fn = HANDLERS.get(str(name))
        if fn is None:
            return _err(req_id, -32601, f"unknown tool {name!r}")
        try:
            payload = fn(params.get("arguments") or {})
        except Exception as exc:  # noqa: BLE001 - the harness must see the failure
            return _ok(req_id, {
                "content": [{"type": "text", "text": json.dumps(
                    {"ok": False, "error": f"{type(exc).__name__}: {exc}"})}],
                "isError": True,
            })
        return _ok(req_id, {
            "content": [{"type": "text", "text": json.dumps(payload, ensure_ascii=False, default=str)}],
            "isError": False,
        })
    if method == "ping":
        return _ok(req_id, {})
    return _err(req_id, -32601, f"unknown method {method!r}")


def serve(stdin: Any = None, stdout: Any = None) -> int:
    stdin = stdin or sys.stdin
    stdout = stdout or sys.stdout
    for line in stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except json.JSONDecodeError:
            stdout.write(json.dumps(_err(None, -32700, "parse error")) + "\n")
            stdout.flush()
            continue
        resp = handle(req)
        if resp is not None:
            stdout.write(json.dumps(resp, ensure_ascii=False, default=str) + "\n")
            stdout.flush()
    return 0


def serve_http(host: str = "127.0.0.1", port: int = 8791, path: str = "/mcp") -> int:
    """Streamable-HTTP transport, implementing the MCP single-JSON-response mode.

    WHY HTTP RATHER THAN STDIO. DSH's MCP stdio transport spawns the server with
    only a scrubbed parent environment but with DSH's own OS authority -- it does
    not go through the harness sandbox. A long-lived localhost service that
    already reads local indexes read-only should not be handed write authority
    over the whole machine just to answer questions, so this is the transport the
    resolver is registered with.

    Only a single JSON response per request is implemented (no SSE stream): the
    MCP spec permits a server to answer ``application/json`` instead of
    ``text/event-stream``, and nothing here needs server-initiated messages.
    """
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    def _send(handler: Any, status: int, payload: Any) -> None:
        body = b"" if payload is None else json.dumps(
            payload, ensure_ascii=False, default=str
        ).encode("utf-8")
        handler.send_response(status)
        handler.send_header("Content-Type", "application/json")
        handler.send_header("Content-Length", str(len(body)))
        handler.end_headers()
        if body:
            handler.wfile.write(body)

    class _Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *args: Any) -> None:  # keep stdout clean for the harness
            pass

        def _trace(self, method: str, detail: str = "") -> None:
            """One line per request on stderr, so a harness mount is provable."""
            print(f"mcp {method} {detail}".rstrip(), file=sys.stderr, flush=True)

        def do_POST(self) -> None:  # noqa: N802 - http.server API
            if self.path.rstrip("/") != path.rstrip("/"):
                _send(self, 404, {"error": f"POST {path} only"})
                return
            try:
                length = int(self.headers.get("Content-Length") or 0)
            except ValueError:
                length = 0
            raw = self.rfile.read(length) if length else b""
            try:
                request = json.loads(raw or b"{}")
            except json.JSONDecodeError:
                _send(self, 200, _err(None, -32700, "parse error"))
                return
            batch = isinstance(request, list)
            requests = request if batch else [request]
            for item in requests:
                if isinstance(item, dict):
                    params = item.get("params") or {}
                    self._trace(str(item.get("method")),
                                str(params.get("name") or params.get("query") or "")[:80])
            responses = [r for r in (handle(x) for x in requests if isinstance(x, dict)) if r]
            if not responses:
                # A notification-only POST: 202 with no body, per the spec.
                self.send_response(202)
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            _send(self, 200, responses if batch else responses[0])

        def do_GET(self) -> None:  # noqa: N802
            # No server-initiated stream is offered, so GET is refused explicitly
            # rather than left to hang.
            _send(self, 405, {"error": "GET unsupported; POST JSON-RPC to " + path})

        def do_DELETE(self) -> None:  # noqa: N802
            self.send_response(200)
            self.send_header("Content-Length", "0")
            self.end_headers()

    server = ThreadingHTTPServer((host, port), _Handler)
    print(f"z0int mcp: streamable-http on http://{host}:{port}{path}", file=sys.stderr, flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] == "selftest":
        return selftest()
    if argv and argv[0] == "--http":
        port = int(argv[1]) if len(argv) > 1 else 8791
        host = os.environ.get("Z0INT_MCP_HOST", "127.0.0.1")
        return serve_http(host, port)
    return serve()


def selftest() -> int:
    """Protocol-level check that does not depend on any index being present."""
    problems: list[str] = []
    init = handle({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})
    if not init or "result" not in init:
        problems.append("initialize failed")
    listed = handle({"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}})
    names = [t["name"] for t in listed["result"]["tools"]]
    if set(names) != set(HANDLERS):
        problems.append(f"tool list mismatch: {names}")
    unknown = handle({"jsonrpc": "2.0", "id": 3, "method": "tools/call",
                      "params": {"name": "nope", "arguments": {}}})
    if "error" not in unknown:
        problems.append("unknown tool did not error")
    inspect = handle({"jsonrpc": "2.0", "id": 4, "method": "tools/call",
                      "params": {"name": "inspect", "arguments": {"locator": "not-a-path"}}})
    body = json.loads(inspect["result"]["content"][0]["text"])
    if body.get("ok") is not False:
        problems.append("inspect accepted an unsupported locator")
    if problems:
        print("SELFTEST FAIL: " + "; ".join(problems), file=sys.stderr)
        return 1
    print(f"selftest ok: {len(names)} tools")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
