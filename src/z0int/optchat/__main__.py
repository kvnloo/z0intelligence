"""Resident OptChat server. One process, JSON lines, no second database.

Request: {"id", "op", "kind"?, "text"?, "msg_id"?, "n"?}
Reply:   {"id", "ok", ...}
"""
from __future__ import annotations

import json
from pathlib import Path
import socket
import sys
import threading
import time

from .browse import write_html
from .compact import JOBS, RETRY_S, pump
from .log import PLACEHOLDER, load_chat
from .persist import persist
from .switch import enabled, set_enabled
from .turn import Queue, begin, leave_unanswered, record, subagent_text

_lock = threading.Lock()
_holder: socket.socket | None = None
_queue = Queue()


def _pump_forever(chat) -> None:
    while True:
        if enabled():
            with _lock:
                pump(chat, limit=JOBS)
        time.sleep(RETRY_S)


def take_lock(path) -> socket.socket:
    """One writer. A listening socket is live. A refusing socket is stale."""
    path.parent.mkdir(parents=True, exist_ok=True)
    probe = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        probe.connect(str(path))
    except FileNotFoundError:
        pass
    except ConnectionRefusedError:
        path.unlink(missing_ok=True)
    else:
        raise SystemExit("another OptChat writer holds the lock")
    finally:
        probe.close()
    holder = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    holder.bind(str(path))
    holder.listen(1)
    return holder


def serve() -> int:
    global _holder
    chat = load_chat()
    _holder = take_lock(chat.root / "lock")
    threading.Thread(target=_pump_forever, args=(chat,), daemon=True, name="optchat-pump").start()
    if enabled():
        pump(chat, limit=JOBS)
    for raw in sys.stdin:
        line = raw.strip()
        if not line:
            continue
        req_id = None
        try:
            req = json.loads(line)
            req_id = req.get("id")
            op = req.get("op")
            if op == "enabled":
                if "on" in req:
                    set_enabled(bool(req.get("on")))
                body = {"ok": True, "enabled": enabled()}
            elif not enabled() and op not in {"status", "enabled"}:
                body = {"ok": False, "error": "optchat_off"}
            elif op == "status":
                body = {
                    "ok": True,
                    "enabled": enabled(),
                    "messages": len(chat.messages),
                    "view_bytes": chat.view_size(),
                    "torn": chat.torn,
                    "placeholders": chat.render().count(PLACEHOLDER),
                }
            elif op == "append":
                with _lock:
                    msg = chat.append(str(req.get("kind")), str(req.get("text") or ""))
                body = {"ok": True, "i": msg.i, "size": msg.size}
                pump(chat, limit=JOBS)
            elif op == "begin":
                texts = req.get("texts")
                if isinstance(texts, str):
                    texts = [texts]
                if not isinstance(texts, list) or not all(isinstance(item, str) for item in texts):
                    body = {"ok": False, "error": "texts required"}
                else:
                    with _lock:
                        opened = begin(chat, texts, str(req.get("agents") or ""))
                    body = {"ok": True, **opened}
                    if opened.get("ready"):
                        pump(chat, limit=JOBS)
            elif op == "record":
                with _lock:
                    saved = record(chat, str(req.get("kind") or ""), str(req.get("text") or ""))
                body = {"ok": True, **saved}
                if saved.get("logged"):
                    pump(chat, limit=JOBS)
            elif op == "settle":
                deadline = time.time() + float(req.get("timeout") or 20)
                while time.time() < deadline and PLACEHOLDER in chat.render():
                    if pump(chat, limit=JOBS) == 0:
                        break
                ready = PLACEHOLDER not in chat.render()
                body = {"ok": True, "ready": ready, "view": chat.render()}
            elif op == "view":
                body = {"ok": True, "view": chat.render(), "messages": len(chat.messages)}
            elif op == "zoom":
                body = {"ok": True, "text": chat.zoom(int(req.get("msg_id")), int(req.get("n") or 1))}
            elif op == "date":
                body = {"ok": True, "text": chat.date_of(int(req.get("msg_id")))}
            elif op == "submit":
                body = {"ok": True, "action": _queue.submit(str(req.get("text") or ""))}
            elif op == "take":
                body = {"ok": True, "texts": _queue.take()}
            elif op == "boundary":
                body = {"ok": True, "texts": _queue.boundary()}
            elif op == "finish":
                body = {"ok": True, "texts": _queue.finish()}
            elif op == "abort":
                texts = req.get("texts") if isinstance(req.get("texts"), list) else []
                with _lock:
                    n = leave_unanswered(chat, [str(item) for item in texts])
                body = {"ok": True, "logged": n}
            elif op == "browse":
                path = Path(str(req.get("path") or chat.root / "memory.html"))
                with _lock:
                    write_html(chat, path)
                body = {"ok": True, "path": str(path)}
            elif op == "persist":
                body = {"ok": True, "rev": persist(chat.root)}
            elif op == "subagent":
                ident = str(req.get("ident") or "")
                text = subagent_text(ident, str(req.get("report") or ""))
                with _lock:
                    msg = chat.append("user", text)
                body = {"ok": True, "i": msg.i, "text": text}
            else:
                body = {"ok": False, "error": f"unknown_op:{op}"}
        except Exception as exc:  # noqa: BLE001 - the host must keep going
            body = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
        sys.stdout.write(json.dumps({"id": req_id, **body}, ensure_ascii=False) + "\n")
        sys.stdout.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(serve())
