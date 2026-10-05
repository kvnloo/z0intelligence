"""Resident OptChat server. One process, JSON lines, no second database.

Request: {"id", "op", "kind"?, "text"?, "msg_id"?, "n"?}
Reply:   {"id", "ok", ...}
"""

from __future__ import annotations

import json
import sys
import threading

from .compact import due, enforce, ollama_complete, source_text
from .log import load_chat

_lock = threading.Lock()
_pumping = False


def _pump_soon(chat) -> None:
    global _pumping
    with _lock:
        if _pumping:
            return
        _pumping = True

    def run() -> None:
        global _pumping
        try:
            for _ in range(4):
                with _lock:
                    pending = due(chat)
                    if not pending:
                        break
                    level, index = pending[0]
                    text = source_text(chat, level, index)
                line = enforce(text, ollama_complete)
                with _lock:
                    chat._store_node(level, index, line)
                    chat.fit()
        except Exception:
            pass
        finally:
            with _lock:
                _pumping = False

    threading.Thread(target=run, name="z0-optchat-pump", daemon=True).start()


def serve() -> int:
    chat = load_chat()
    _pump_soon(chat)
    for raw in sys.stdin:
        line = raw.strip()
        if not line:
            continue
        req_id = None
        start_pump = False
        try:
            with _lock:
                req = json.loads(line)
                req_id = req.get("id")
                op = req.get("op")
                if op == "status":
                    body = {
                        "ok": True,
                        "messages": len(chat.messages),
                        "view_bytes": chat.view_size(),
                        "torn": chat.torn,
                        "placeholders": chat.render().count("(not summarized yet)"),
                    }
                elif op == "append":
                    msg = chat.append(str(req.get("kind")), str(req.get("text") or ""))
                    body = {"ok": True, "i": msg.i, "size": msg.size}
                    start_pump = True
                elif op == "view":
                    body = {"ok": True, "view": chat.render(), "messages": len(chat.messages)}
                elif op == "zoom":
                    body = {"ok": True, "text": chat.zoom(int(req.get("msg_id")), int(req.get("n") or 1))}
                elif op == "date":
                    body = {"ok": True, "text": chat.date_of(int(req.get("msg_id")))}
                else:
                    body = {"ok": False, "error": f"unknown_op:{op}"}
        except Exception as exc:  # noqa: BLE001 - the host must keep going
            body = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
        sys.stdout.write(json.dumps({"id": req_id, **body}, ensure_ascii=False) + "\n")
        sys.stdout.flush()
        if start_pump:
            _pump_soon(chat)
    return 0


if __name__ == "__main__":
    raise SystemExit(serve())
