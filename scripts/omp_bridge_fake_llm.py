"""Deterministic OpenAI-compatible chat server for the OMP bridge end-to-end check.

Stdlib only. Serves ``POST /v1/chat/completions`` (SSE stream or plain JSON) and
``GET /v1/models`` so a sandboxed ``omp`` can run a full agent turn without any
real provider credentials. Every streamed completion ends with a usage chunk so
the bridge sees provider usage on ``turn_end``.

    python scripts/omp_bridge_fake_llm.py --port 0 --port-file PATH
"""

from __future__ import annotations

import argparse
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

MODEL_ID = "bridge-fake"
REPLY = "bridge-e2e-ok"
PROMPT_TOKENS = 123
COMPLETION_TOKENS = 7


class _Handler(BaseHTTPRequestHandler):
    server_version = "omp-bridge-fake/1"
    requests_seen: list[dict[str, Any]] = []

    def log_message(self, *_args: Any) -> None:  # keep stderr quiet
        return

    def _json(self, code: int, body: dict[str, Any]) -> None:
        raw = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self) -> None:  # noqa: N802
        if self.path.rstrip("/").endswith("/models"):
            self._json(200, {"object": "list", "data": [{"id": MODEL_ID, "object": "model", "owned_by": "sandbox"}]})
            return
        self._json(404, {"error": "not_found"})

    def do_POST(self) -> None:  # noqa: N802
        length = int(self.headers.get("Content-Length") or 0)
        try:
            body = json.loads(self.rfile.read(length) or b"{}")
        except json.JSONDecodeError:
            body = {}
        _Handler.requests_seen.append({"path": self.path, "ts": time.time(), "model": body.get("model")})
        if not self.path.rstrip("/").endswith("/chat/completions"):
            self._json(404, {"error": "not_found"})
            return
        created = int(time.time())
        usage = {
            "prompt_tokens": PROMPT_TOKENS,
            "completion_tokens": COMPLETION_TOKENS,
            "total_tokens": PROMPT_TOKENS + COMPLETION_TOKENS,
        }
        if not body.get("stream"):
            self._json(
                200,
                {
                    "id": "chatcmpl-fake",
                    "object": "chat.completion",
                    "created": created,
                    "model": MODEL_ID,
                    "choices": [
                        {"index": 0, "message": {"role": "assistant", "content": REPLY}, "finish_reason": "stop"}
                    ],
                    "usage": usage,
                },
            )
            return
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()

        def chunk(delta: dict[str, Any], finish: str | None = None, with_usage: bool = False) -> None:
            obj: dict[str, Any] = {
                "id": "chatcmpl-fake",
                "object": "chat.completion.chunk",
                "created": created,
                "model": MODEL_ID,
                "choices": [{"index": 0, "delta": delta, "finish_reason": finish}] if delta or finish else [],
            }
            if with_usage:
                obj["usage"] = usage
            self.wfile.write(b"data: " + json.dumps(obj).encode() + b"\n\n")
            self.wfile.flush()

        chunk({"role": "assistant", "content": ""})
        chunk({"content": REPLY})
        chunk({}, finish="stop")
        chunk({}, with_usage=True)
        self.wfile.write(b"data: [DONE]\n\n")
        self.wfile.flush()


def serve(port: int = 0) -> ThreadingHTTPServer:
    """Start the server on 127.0.0.1 in a daemon thread and return it."""
    httpd = ThreadingHTTPServer(("127.0.0.1", port), _Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--port", type=int, default=0)
    ap.add_argument("--port-file", type=Path)
    args = ap.parse_args()
    httpd = serve(args.port)
    if args.port_file:
        args.port_file.write_text(str(httpd.server_address[1]))
    print(json.dumps({"port": httpd.server_address[1]}), flush=True)
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        httpd.shutdown()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
