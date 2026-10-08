"""Resident OptChat server. One process, JSON lines, no second database.

Request: {"id", "op", "kind"?, "text"?, "msg_id"?, "n"?}
Reply:   {"id", "ok", ...}
"""
from __future__ import annotations

import json
import os
from collections.abc import Callable
from pathlib import Path
import signal
import socket
import sys
import threading

from .browse import write_html
from .compact import NodeKey, Scheduler
from .log import ChatLog, load_chat
from .persist import persist
from .switch import enabled, set_enabled
from .turn import Queue, begin, leave_unanswered, record, subagent_text, wait_ready



def take_lock(path: Path) -> socket.socket:
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


class Resident:
    """Own the chat, scheduler and asynchronous wait registry for one server."""

    def __init__(self, chat: ChatLog, emit: Callable[[dict[str, object]], None], scheduler: Scheduler) -> None:
        self.chat = chat
        self.emit = emit
        self.scheduler = scheduler
        self.queue = Queue()
        self.waits: dict[str, threading.Event] = {}
        self.wait_threads: set[threading.Thread] = set()
        self.wait_serial = 0
        self.scheduler.on_error = self._report_error

    def _error_notice(self, key: NodeKey, error: str) -> dict[str, object]:
        return {
            "event": "compactor_error",
            "level": key[0],
            "index": key[1],
            "error": error,
            "backend": self.scheduler.backend,
            "model": self.scheduler.model,
        }

    def _report_error(self, key: NodeKey, error: str) -> None:
        self.emit(self._error_notice(key, error))

    def status(self) -> dict[str, object]:
        return {
            "ok": True,
            "enabled": self.scheduler.on,
            "ready": self.chat.missing() == 0,
            "messages": len(self.chat.messages),
            "view_bytes": self.chat.view_size(),
            "torn": self.chat.torn,
            "placeholders": self.chat.missing(),
            "pending": len(self.queue.pending),
            "running": self.queue.running,
            "backend": self.scheduler.backend,
            "model": self.scheduler.model,
            "errors": [
                self._error_notice(key, error)
                for key, error in sorted(self.scheduler.errors.items())
            ],
        }

    def _cancel(self, wait_id: str | None = None) -> int:
        targets = list(self.waits.values()) if wait_id is None else [self.waits[wait_id]] if wait_id in self.waits else []
        for signal in targets:
            signal.set()
        self.chat.condition.notify_all()
        return len(targets)

    def _settle(self, req: dict[str, object]) -> None:
        self.wait_serial += 1
        wait_id = str(req.get("wait_id") or f"rpc:{req.get('id')}:{self.wait_serial}")
        if wait_id in self.waits:
            raise ValueError("wait_id already active")
        timeout = None if req.get("timeout") is None else float(req["timeout"])
        signal = threading.Event()
        self.waits[wait_id] = signal
        worker = threading.Thread(
            target=self._wait,
            args=(req.get("id"), wait_id, timeout, signal),
            name="optchat-settle",
        )
        self.wait_threads.add(worker)
        worker.start()

    def _wait(self, req_id: object, wait_id: str, timeout: float | None, signal: threading.Event) -> None:
        try:
            waited = wait_ready(self.chat, timeout, signal)
            with self.chat.condition:
                if signal.is_set() or not self.scheduler.on:
                    waited.update(ready=False, cancelled=True)
                self.waits.pop(wait_id, None)
                body = {"id": req_id, "ok": True, "wait_id": wait_id, **waited}
                if not waited["ready"] and not waited["cancelled"] and self.scheduler.reports:
                    body["error"] = self.scheduler.reports[-1]
            self.emit(body)
        finally:
            with self.chat.condition:
                self.waits.pop(wait_id, None)
                self.wait_threads.discard(threading.current_thread())

    def dispatch(self, req: dict[str, object]) -> dict[str, object] | None:
        with self.chat.condition:
            op = req.get("op")
            if op == "enabled":
                if "on" in req:
                    on = bool(req["on"])
                    set_enabled(on)
                    self.scheduler.set_enabled(on)
                    if not on:
                        self._cancel()
                        leave_unanswered(self.chat, self.queue.cancel())
                return {"ok": True, "enabled": self.scheduler.on}
            if op == "status":
                return self.status()
            if op == "cancel":
                count = self._cancel(str(req.get("wait_id") or ""))
                logged = leave_unanswered(self.chat, self.queue.cancel()) if count else 0
                return {"ok": True, "cancelled": bool(count), "logged": logged}
            if op == "abort":
                texts = req.get("texts")
                if texts is not None and (not isinstance(texts, list) or not all(isinstance(item, str) for item in texts)):
                    raise ValueError("texts must be strings")
                self._cancel()
                pending = self.queue.cancel()
                logged = leave_unanswered(self.chat, (texts or []) + pending)
                return {"ok": True, "logged": logged}
            if op == "finish":
                if not self.scheduler.on:
                    logged = leave_unanswered(self.chat, self.queue.cancel())
                    return {"ok": True, "texts": [], "logged": logged}
                return {"ok": True, "texts": self.queue.finish()}
            if not self.scheduler.on:
                return {"ok": False, "error": "optchat_off"}
            if op == "append":
                msg = self.chat.append(str(req.get("kind")), str(req.get("text") or ""))
                return {"ok": True, "i": msg.i, "size": msg.size}
            if op == "begin":
                texts = req.get("texts")
                if isinstance(texts, str):
                    texts = [texts]
                if not isinstance(texts, list) or not all(isinstance(item, str) for item in texts):
                    raise ValueError("texts required")
                return {"ok": True, **begin(self.chat, texts, str(req.get("agents") or ""))}
            if op == "record":
                saved = record(self.chat, str(req.get("kind") or ""), str(req.get("text") or ""))
                return {"ok": True, **saved}
            if op == "settle":
                self._settle(req)
                return None
            if op == "view":
                return {"ok": True, "view": self.chat.render(), "messages": len(self.chat.messages)}
            if op == "zoom":
                return {"ok": True, "text": self.chat.zoom(int(req.get("msg_id")), int(req.get("n") or 1))}
            if op == "date":
                return {"ok": True, "text": self.chat.date_of(int(req.get("msg_id")))}
            if op == "submit":
                return {"ok": True, "action": self.queue.submit(str(req.get("text") or ""))}
            if op == "take":
                return {"ok": True, "texts": self.queue.take()}
            if op == "boundary":
                return {"ok": True, "texts": self.queue.boundary()}
            if op == "browse":
                path = Path(str(req.get("path") or self.chat.root / "memory.html"))
                write_html(self.chat, path)
                return {"ok": True, "path": str(path)}
            if op == "persist":
                return {"ok": True, "rev": persist(self.chat.root)}
            if op == "subagent":
                text = subagent_text(str(req.get("ident") or ""), str(req.get("report") or ""))
                msg = self.chat.append("user", text)
                return {"ok": True, "i": msg.i, "text": text}
            return {"ok": False, "error": f"unknown_op:{op}"}

    def close(self) -> None:
        with self.chat.condition:
            self._cancel()
            leave_unanswered(self.chat, self.queue.cancel())
            workers = list(self.wait_threads)
        self.scheduler.stop()
        for worker in workers:
            worker.join()


def serve() -> int:
    home = Path(os.environ.get("Z0INT_HOME", Path.home() / ".z0int"))
    root = home / "optchat" / "chat"
    holder = take_lock(root / "lock")
    output_lock = threading.Lock()

    def emit(body: dict[str, object]) -> None:
        with output_lock:
            sys.stdout.write(json.dumps(body, ensure_ascii=False) + "\n")
            sys.stdout.flush()

    resident: Resident | None = None
    try:
        def interrupted(signum: int, _frame: object) -> None:
            raise SystemExit(128 + signum)

        previous_handler = signal.signal(signal.SIGTERM, interrupted)
        chat = load_chat(root)
        scheduler = Scheduler(chat, on=enabled())
        resident = Resident(chat, emit, scheduler)
        scheduler.start()
        if chat.torn:
            print(f"OptChat skipped {chat.torn} torn rows", file=sys.stderr, flush=True)
        for raw in sys.stdin:
            line = raw.strip()
            if not line:
                continue
            req_id = None
            try:
                req = json.loads(line)
                if not isinstance(req, dict):
                    raise ValueError("request must be an object")
                req_id = req.get("id")
                body = resident.dispatch(req)
            except Exception as exc:  # noqa: BLE001 - the host must keep going
                body = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
            if body is not None:
                emit({"id": req_id, **body})
    finally:
        if resident is not None:
            resident.close()
        holder.close()
        (root / "lock").unlink(missing_ok=True)
        signal.signal(signal.SIGTERM, previous_handler)
    return 0


if __name__ == "__main__":
    raise SystemExit(serve())
