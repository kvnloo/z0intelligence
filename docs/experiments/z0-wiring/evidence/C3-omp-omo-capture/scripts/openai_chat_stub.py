"""C3 e2e stub: deterministic OpenAI-compatible chat server (salvaged from 815c680 omp_bridge_fake_llm.py).

usage: openai_chat_stub.py <port> <request-log>
The first completion of a turn asks for one `read` tool call (so OMP fires tool_call and the local-cognition
shadow lane runs); once a tool result is in the conversation it answers with plain text. Streams end with a
usage chunk. The request log keeps only path, model and message roles (no message text).
"""
import json
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

MODEL_ID = "bridge-fake"
TOOL_ARG = "E2E_TOOL_INPUT_MARKER_C3.md"
USAGE = {"prompt_tokens": 123, "completion_tokens": 7, "total_tokens": 130}
PORT, LOG = int(sys.argv[1]), sys.argv[2]


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_a):
        return

    def _json(self, code, body):
        raw = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self):  # noqa: N802
        if self.path.rstrip("/").endswith("/models"):
            return self._json(200, {"object": "list", "data": [{"id": MODEL_ID, "object": "model"}]})
        self._json(404, {"error": "not_found"})

    def do_POST(self):  # noqa: N802
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
        msgs = body.get("messages") or []
        roles = [m.get("role") for m in msgs if isinstance(m, dict)]
        with open(LOG, "a") as fh:
            fh.write(json.dumps({"ts": time.time(), "path": self.path, "model": body.get("model"), "roles": roles,
                                 "tools": len(body.get("tools") or [])}) + "\n")
        if not self.path.rstrip("/").endswith("/chat/completions"):
            return self._json(404, {"error": "not_found"})
        want_tool = bool(body.get("tools")) and "tool" not in roles
        created = int(time.time())
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()

        def chunk(delta, finish=None, usage=False):
            obj = {"id": "chatcmpl-c3", "object": "chat.completion.chunk", "created": created, "model": MODEL_ID,
                   "choices": [{"index": 0, "delta": delta, "finish_reason": finish}] if delta or finish else []}
            if usage:
                obj["usage"] = USAGE
            self.wfile.write(b"data: " + json.dumps(obj).encode() + b"\n\n")
            self.wfile.flush()

        chunk({"role": "assistant", "content": ""})
        if want_tool:
            chunk({"tool_calls": [{"index": 0, "id": "call_c3_1", "type": "function",
                                   "function": {"name": "read", "arguments": json.dumps({"path": TOOL_ARG})}}]})
            chunk({}, finish="tool_calls")
        else:
            chunk({"content": "c3-e2e-ok"})
            chunk({}, finish="stop")
        chunk({}, usage=True)
        self.wfile.write(b"data: [DONE]\n\n")
        self.wfile.flush()


if __name__ == "__main__":
    ThreadingHTTPServer(("127.0.0.1", PORT), Handler).serve_forever()
