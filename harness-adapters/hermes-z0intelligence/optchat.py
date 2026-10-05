"""Optional Hermes client for the shared z0intelligence OptChat engine.

This is a harness-local UX log, not a replacement for the z0 memory plane or
Hermes's transcript. The resident child is the existing `python -m z0int.optchat`
server. Hermes gets its own root so an OMP process and a Hermes process never
race on `ChatLog` message indices.

`optchat_mode`:
- `off`: no hooks or tools.
- `shadow`: append root user-turn events, never return model-visible context.
- `on`: inject the prior bounded view and register `optchat_zoom` /
  `optchat_date`.

Tool/result/assistant appends go through one bounded worker queue, so Hermes hot
hooks never wait for the child. A synchronous `prepare` request is used only
before an interactive LLM turn; it atomically renders the prior view and appends
the current user message.
"""
from __future__ import annotations

import json
import os
import queue
import subprocess
import threading
import time
import uuid
from pathlib import Path

MODES = frozenset({"off", "shadow", "on"})
CALL_TIMEOUT_S = 1.5
MAX_JOBS = 1024
MAX_TOOL_CHARS = 30_000


def _text(value) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        parts = []
        for item in value:
            if isinstance(item, dict) and isinstance(item.get("text"), str):
                parts.append(item["text"])
            else:
                parts.append(_text(item))
        return "\n".join(part for part in parts if part)
    if isinstance(value, dict):
        content = value.get("content")
        if isinstance(content, (str, list)):
            rendered = _text(content)
            if rendered:
                return rendered
        try:
            return json.dumps(value, ensure_ascii=False, default=str)
        except Exception:
            return str(value)
    if value is None:
        return ""
    return str(value)


def _turn_key(session_id="", turn_id=None) -> tuple[str, str]:
    return (str(session_id or "hermes"), str(turn_id) if turn_id is not None else "")


class OptChat:
    def __init__(self, mode: str, home: Path, python: str | None):
        self.mode = mode
        self.home = Path(home)
        self.python = str(python or "")
        self.root = self.home / "optchat" / "hermes" / "chat"
        self._jobs: queue.Queue = queue.Queue(MAX_JOBS)
        self._responses: queue.Queue = queue.Queue()
        self._proc = None
        self._closed = False
        self._active: set[tuple[str, str]] = set()
        self._active_lock = threading.Lock()
        self._worker = threading.Thread(target=self._run, name="z0-optchat-hermes", daemon=True)
        self._worker.start()

    @property
    def model_visible(self) -> bool:
        return self.mode == "on"

    def _ensure(self):
        if self._proc is not None and self._proc.poll() is None:
            return self._proc
        if not self.python:
            raise RuntimeError("z0int_python is not configured")
        env = dict(os.environ, Z0INT_HOME=str(self.home), Z0INT_OPTCHAT_ROOT=str(self.root))
        self._proc = subprocess.Popen(
            [self.python, "-m", "z0int.optchat"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            bufsize=1,
            env=env,
            start_new_session=True,
        )
        threading.Thread(
            target=self._read_responses,
            args=(self._proc,),
            name="z0-optchat-hermes-reader",
            daemon=True,
        ).start()
        return self._proc

    def _read_responses(self, proc) -> None:
        try:
            for line in proc.stdout:
                try:
                    row = json.loads(line)
                except Exception:
                    continue
                if isinstance(row, dict):
                    self._responses.put(row)
        except Exception:
            return

    def _raw_call(self, op: str, extra: dict) -> dict:
        req_id = uuid.uuid4().hex
        proc = self._ensure()
        if proc.stdin is None:
            raise RuntimeError("OptChat stdin unavailable")
        proc.stdin.write(json.dumps({"id": req_id, "op": op, **extra}, ensure_ascii=False) + "\n")
        proc.stdin.flush()
        deadline = time.monotonic() + CALL_TIMEOUT_S
        while True:
            left = deadline - time.monotonic()
            if left <= 0:
                raise TimeoutError(f"OptChat {op} timed out")
            row = self._responses.get(timeout=left)
            if str(row.get("id") or "") == req_id:
                return row

    def _run(self) -> None:
        try:
            while True:
                job = self._jobs.get()
                try:
                    if job is None:
                        return
                    op, extra, reply = job
                    try:
                        row = self._raw_call(op, extra)
                    except Exception as exc:
                        self._stop_proc()
                        row = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
                    if reply is not None:
                        try:
                            reply.put_nowait(row)
                        except queue.Full:
                            pass
                finally:
                    self._jobs.task_done()
        finally:
            self._stop_proc()

    def _submit(self, op: str, extra: dict, *, wait: bool) -> dict:
        if self._closed:
            return {"ok": False, "error": "closed"}
        reply = queue.Queue(maxsize=1) if wait else None
        try:
            self._jobs.put_nowait((op, extra, reply))
        except queue.Full:
            return {"ok": False, "error": "queue_saturated"}
        if reply is None:
            return {"ok": True, "queued": True}
        try:
            return reply.get(timeout=CALL_TIMEOUT_S + 0.5)
        except queue.Empty:
            return {"ok": False, "error": "timeout"}

    def _is_active(self, session_id="", turn_id=None) -> bool:
        with self._active_lock:
            return _turn_key(session_id, turn_id) in self._active

    def pre_llm_call(self, session_id="", turn_id=None, user_message="", **_) -> dict | None:
        text = _text(user_message).strip()
        if not text:
            return None
        key = _turn_key(session_id, turn_id)
        with self._active_lock:
            self._active.add(key)
        if self.mode == "shadow":
            self._submit("append", {"kind": "user", "text": text}, wait=False)
            return None
        row = self._submit("prepare", {"kind": "user", "text": text}, wait=True)
        view = row.get("view")
        if row.get("ok") is not True or not isinstance(view, str) or "+" not in view:
            return None
        return {
            "context": (
                "OptChat view of earlier Hermes messages. "
                "Use optchat_zoom before acting on a summary.\n" + view
            )
        }

    def post_tool_call(self, session_id="", turn_id=None, tool_name="", args=None, result=None, **_) -> None:
        if not self._is_active(session_id, turn_id):
            return None
        try:
            rendered_args = json.dumps(args if args is not None else {}, ensure_ascii=False, default=str)
        except Exception:
            rendered_args = str(args)
        rendered_args = rendered_args[:MAX_TOOL_CHARS]
        self._submit("append", {"kind": "tool", "text": f"{tool_name or 'tool'} {rendered_args}"}, wait=False)
        self._submit("append", {"kind": "echo", "text": _text(result)}, wait=False)
        return None

    def post_llm_call(self, session_id="", turn_id=None, assistant_response="", **_) -> None:
        key = _turn_key(session_id, turn_id)
        with self._active_lock:
            active = key in self._active
            self._active.discard(key)
        if not active:
            return None
        text = _text(assistant_response).strip()
        if text:
            self._submit("append", {"kind": "talk", "text": text}, wait=False)
        return None

    def register_tools(self, ctx) -> None:
        if not self.model_visible:
            return

        zoom_schema = {
            "name": "optchat_zoom",
            "description": (
                "Open one OptChat line. n must be a power of 2; id is the message index "
                "printed before +n. n=1 returns the original message."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "id": {"type": "integer", "minimum": 0},
                    "n": {"type": "integer", "minimum": 1},
                },
                "required": ["id", "n"],
            },
        }

        def zoom(params, **kwargs):
            del kwargs
            row = self._submit(
                "zoom",
                {"msg_id": int(params.get("id", 0)), "n": int(params.get("n", 1))},
                wait=True,
            )
            return str(row.get("text") or row.get("error") or "")

        date_schema = {
            "name": "optchat_date",
            "description": "Return the local date and time of one OptChat message id.",
            "parameters": {
                "type": "object",
                "properties": {"id": {"type": "integer", "minimum": 0}},
                "required": ["id"],
            },
        }

        def date(params, **kwargs):
            del kwargs
            row = self._submit("date", {"msg_id": int(params.get("id", 0))}, wait=True)
            return str(row.get("text") or row.get("error") or "")

        ctx.register_tool(name="optchat_zoom", toolset="z0-optchat", schema=zoom_schema, handler=zoom)
        ctx.register_tool(name="optchat_date", toolset="z0-optchat", schema=date_schema, handler=date)

    def _stop_proc(self) -> None:
        proc, self._proc = self._proc, None
        if proc is None:
            return
        try:
            if proc.stdin is not None:
                proc.stdin.close()
        except Exception:
            pass
        try:
            proc.terminate()
            proc.wait(timeout=0.2)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            self._jobs.put(None, timeout=0.2)
        except queue.Full:
            self._stop_proc()
            return
        self._worker.join(timeout=2.0)
        if self._worker.is_alive():
            self._stop_proc()


def create(settings: dict, home: Path, python: str | None) -> OptChat | None:
    mode = str(settings.get("optchat_mode") or "off").strip().lower()
    if mode == "off":
        return None
    if mode not in MODES:
        raise ValueError(f"invalid optchat_mode: {mode}")
    return OptChat(mode=mode, home=home, python=python)
