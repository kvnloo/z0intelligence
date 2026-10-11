"""zoom(id, n) and date(id) as a small stdio MCP server over one chat root.

It only reads: the turn's process owns the log. Each call first picks up
the lines written since the last one.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from .chat import Chat
from .prompt import TOOLS


def handle(chat: Chat, request: dict) -> dict | None:
    method, ident = request.get("method"), request.get("id")
    if ident is None:
        return None                          # a notification
    if method == "initialize":
        result = {"protocolVersion": request.get("params", {}).get("protocolVersion", "2024-11-05"),
                  "capabilities": {"tools": {}}, "serverInfo": {"name": "chat", "version": "1"}}
    elif method == "ping":
        result = {}
    elif method == "tools/list":
        result = {"tools": TOOLS}
    elif method == "tools/call":
        params = request.get("params", {})
        args = params.get("arguments") or {}
        chat.refresh()
        if params.get("name") == "zoom":
            text = chat.zoom(args.get("id"), args.get("n"))
        elif params.get("name") == "date":
            text = chat.date(args.get("id"))
        else:
            return {"jsonrpc": "2.0", "id": ident, "error": {"code": -32602, "message": "unknown tool"}}
        result = {"content": [{"type": "text", "text": text}]}
    else:
        return {"jsonrpc": "2.0", "id": ident, "error": {"code": -32601, "message": f"no method {method}"}}
    return {"jsonrpc": "2.0", "id": ident, "result": result}


def serve(root: Path, stdin=None, stdout=None) -> None:
    stdin, stdout = stdin or sys.stdin, stdout or sys.stdout
    chat = Chat(root)
    for raw in stdin:
        raw = raw.strip()
        if not raw:
            continue
        try:
            request = json.loads(raw)
        except json.JSONDecodeError:
            continue
        reply = handle(chat, request)
        if reply is not None:
            stdout.write(json.dumps(reply, ensure_ascii=False) + "\n")
            stdout.flush()
