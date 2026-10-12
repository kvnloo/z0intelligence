"""The one model backend: the locally installed Claude Code CLI (`claude -p`).

Every call is isolated from the owner's setup: fixed empty cwd, no setting
sources, strict MCP config, no skills, no built-in tools unless asked, no
session persistence, never `--resume`, permission mode `dontAsk` (a tool
that is not on the allow list is denied), none of the parent session's
CLAUDE*/Z0INT_* environment except CLAUDE_CONFIG_DIR (the login), and its
temp files under the work directory, not /tmp. A conversation is one
`claude -p` process fed over stream-json stdin, so a compaction's "Too long"
retry stays in the same conversation without resuming a session.

Every send is saved as one JSONL row (flags, effort, view limits, byte sizes,
usage, wall time, raw events). Requests are counted against per-model caps in
one shared ledger: a send reserves its requests before it starts
(`--max-turns` holds it to them) and returns what it did not use, so the caps
cannot be passed, however many sends run at once.

What Claude Code really sends (seen on the wire by the reviewer, 2.1.296; not
something this module controls):

    [tools] [SDK identity line + our system prompt]
    [user: <system-reminder> the account's email address, ... ] [view blocks] [message]
    [Claude Code's own date/environment message]

So the request is not byte for byte the gist's `[tools] [system prompt] [view]
[new message]`: an identity line precedes the gist prompt, and a reminder
block holding the login's real email address precedes the view in every call.
No flag removes either. Both are constant, so they do not move the cache
prefix; the email is real data in an otherwise synthetic trial.
"""

from __future__ import annotations

import fcntl
import json
import os
import shutil
import queue
import subprocess
import tempfile
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path

CAPS = {"haiku": 1500, "sonnet": 250, "opus": 60}
PLACEMENTS = ("user", "user-blocks", "system")
SYSTEM_MODES = ("replace", "append")
SERVER = "chat"
MIN_FREE = 256 << 20                  # refuse to start with less temp space than this
KEEP_ENV = ("CLAUDE_CONFIG_DIR",)     # the login; every other CLAUDE*/Z0INT_* variable is the parent's
_io = threading.Lock()


class BackendError(RuntimeError):
    pass


class LimitError(BackendError):
    """Rate limit, usage limit or the trial's own call cap: stop, never loop."""


class ToolCalled(BackendError):
    """A call that may make one request tried to go on (a compaction called a tool)."""


def child_env(tmp: Path, extra: dict | None = None) -> dict:
    """The parent's environment without its Claude Code session, temp files under `tmp`."""
    env = {k: v for k, v in os.environ.items()
           if k in KEEP_ENV or not (k.startswith("CLAUDE") or k.startswith("Z0INT_"))}
    env.update({"CLAUDE_CODE_DISABLE_AUTO_MEMORY": "1", "DISABLE_AUTOUPDATER": "1",
                "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1", "ENABLE_TOOL_SEARCH": "false",
                "TMPDIR": str(tmp), "CLAUDE_CODE_TMPDIR": str(tmp)})
    env.update(extra or {})
    return env


def family(model: str) -> str:
    low = model.lower()
    for name in CAPS:
        if name in low:
            return name
    return low


@dataclass
class Reply:
    text: str
    usage: dict
    model: str = ""
    requests: int = 1
    wall_s: float = 0.0
    row: dict = field(default_factory=dict)


def blocks_of(view: str, per: int = 4, ttl: str = "1h") -> list[dict]:
    """The view as text blocks of `per` lines, a cache mark on the last whole one."""
    lines = view.split("\n")
    head, body, tail = lines[0], lines[1:-1], lines[-1]
    whole = len(body) // per
    texts = []
    for b in range(whole):
        chunk = "\n".join(body[b * per:(b + 1) * per]) + "\n"
        texts.append((head + "\n" + chunk) if b == 0 else chunk)
    rest = body[whole * per:]
    last = ("" if whole else head + "\n") + "".join(line + "\n" for line in rest) + tail
    out = [{"type": "text", "text": t} for t in texts]
    if out:
        out[-1]["cache_control"] = {"type": "ephemeral", "ttl": ttl}
    out.append({"type": "text", "text": last})
    return out


class ClaudeCLI:
    """Factory for conversations. `work` holds the fixed cwd and prompt files."""

    def __init__(self, work: Path, *, ledger: Path, calls: Path | None = None,
                 experiment: str = "", placement: str = "user", system_mode: str = "replace",
                 effort: str | None = None, caps: dict | None = None, timeout: float = 900.0,
                 extra: list[str] | None = None, partial: bool = False, binary: str = "claude",
                 meta: dict | None = None, min_free: int = MIN_FREE):
        if placement not in PLACEMENTS or system_mode not in SYSTEM_MODES:
            raise ValueError("bad placement or system mode")
        if not ledger:
            raise ValueError("a ledger is required: the call caps hold only when every run shares one")
        self.work = Path(work).resolve()
        self.cwd = self.work / "cwd"  # fixed and empty: the cwd may be named in the request
        self.cwd.mkdir(parents=True, exist_ok=True)
        if any(self.cwd.iterdir()):
            raise BackendError(f"{self.cwd} must stay empty")
        self.tmp = self.work / "tmp"  # Claude Code's temp files: never the shared /tmp
        self.tmp.mkdir(exist_ok=True)
        free = shutil.disk_usage(self.tmp).free
        if free < min_free:
            raise BackendError(f"{self.tmp} has {free} bytes free, under {min_free}: free space first")
        self.calls = Path(calls) if calls else None
        self.ledger = Path(ledger)
        self.experiment, self.placement, self.system_mode = experiment, placement, system_mode
        self.effort, self.caps, self.timeout = effort, dict(CAPS if caps is None else caps), timeout
        self.extra, self.partial, self.binary = list(extra or []), partial, binary
        self.meta = dict(meta or {})  # copied into every row (the chat's view limits)

    def _ledger(self, fam: str, n: int, label: str, kind: str, want: int = 0) -> int:
        """One locked read-and-append on the ledger, safe across threads and processes.

        kind "reserve": grant up to `want` requests (at least `n`) if the cap
        has room, write them down, return how many. kind "settle": add `n`
        (negative returns what a send did not use).
        """
        self.ledger.parent.mkdir(parents=True, exist_ok=True)
        with _io, self.ledger.open("a+") as fh:
            fcntl.flock(fh, fcntl.LOCK_EX)
            if kind == "reserve":
                fh.seek(0)
                spent = 0
                for line in fh.read().splitlines():
                    try:
                        row = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if row.get("family") == fam:
                        spent += int(row.get("n", 0))
                cap = self.caps.get(fam, 0)
                if spent + n > cap:
                    raise LimitError(f"call cap reached for {fam}: {spent} of {cap} used, {n} needed")
                n = min(want, cap - spent)
            if n:
                fh.write(json.dumps({"ts": time.time(), "family": fam, "n": n, "kind": kind,
                                     "experiment": self.experiment, "label": label}) + "\n")
                fh.flush()
            return n

    def spent(self, fam: str) -> int:
        if not self.ledger.exists():
            return 0
        rows = (json.loads(line) for line in self.ledger.read_text().splitlines() if line.strip())
        return sum(int(row.get("n", 0)) for row in rows if row.get("family") == fam)

    def open(self, model: str, system: str, view: str = "", tools: dict | None = None,
             label: str = "", effort: str | None = None, max_requests: int = 1) -> "Session":
        """`max_requests`: the most API requests one send may make (1 = no tool can be used)."""
        return Session(self, model, system, view, tools, label, effort or self.effort, max_requests)

    def complete(self, model: str, system: str, user_text: str, tools_config: dict | None = None,
                 view: str = "", label: str = "", max_requests: int = 1) -> Reply:
        with self.open(model, system, view, tools_config, label, max_requests=max_requests) as session:
            return session.send(user_text)


class Session:
    def __init__(self, cli: ClaudeCLI, model: str, system: str, view: str, tools: dict | None,
                 label: str, effort: str | None, max_requests: int = 1):
        self.cli, self.model, self.view, self.label = cli, model, view, label
        self.effort, self.max_requests, self.granted = effort, max(1, max_requests), 0
        self.first, self.sends, self.proc = True, 0, None
        self.dir = Path(tempfile.mkdtemp(prefix="call-", dir=cli.work))
        text = system + ("\n\n" + view if view and cli.placement == "system" else "")
        self.system_bytes = len(text.encode())
        (self.dir / "system.txt").write_text(text)
        tools = tools or {}
        (self.dir / "mcp.json").write_text(json.dumps({"mcpServers": tools.get("mcp", {})}))
        flag = "--system-prompt-file" if cli.system_mode == "replace" else "--append-system-prompt-file"
        self.argv = [
            cli.binary, "-p", "--model", model,
            "--input-format", "stream-json", "--output-format", "stream-json", "--verbose",
            flag, str(self.dir / "system.txt"),
            "--setting-sources", "", "--strict-mcp-config", "--mcp-config", str(self.dir / "mcp.json"),
            "--disable-slash-commands", "--no-session-persistence", "--permission-mode", "dontAsk",
            "--tools", tools.get("builtin", ""),
        ]
        if tools.get("allowed"):
            self.argv += ["--allowedTools", ",".join(tools["allowed"])]
        if effort:
            self.argv += ["--effort", effort]
        if cli.partial:
            self.argv += ["--include-partial-messages"]
        self.argv += cli.extra
        self.env = child_env(cli.tmp, tools.get("env"))
        self.events: queue.Queue = queue.Queue()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def _start(self) -> None:
        self.err = (self.dir / "stderr.txt").open("w")
        self.proc = subprocess.Popen(self.argv, cwd=self.cli.cwd, env=self.env, stdin=subprocess.PIPE,
                                     stdout=subprocess.PIPE, stderr=self.err, text=True, bufsize=1)

        def pump(out, sink):
            for line in out:
                sink.put(line)
            sink.put(None)

        threading.Thread(target=pump, args=(self.proc.stdout, self.events), daemon=True).start()

    def _content(self, text: str):
        if not (self.first and self.view) or self.cli.placement == "system":
            return [{"type": "text", "text": text}]
        if self.cli.placement == "user-blocks":
            return blocks_of(self.view) + [{"type": "text", "text": text}]
        return [{"type": "text", "text": self.view}, {"type": "text", "text": text}]

    def send(self, text: str, on_start=None, on_block=None) -> Reply:
        """One user message; returns when Claude Code reports the result.

        on_block(kind, text) fires as replies ("unii"), tool calls ("tool") and
        tool results ("echo") arrive. Thinking blocks are never passed on.
        """
        fam = family(self.model)
        # Reserve before sending. The first send takes what the cap still allows, up to
        # max_requests, and the process is held to it; a later send needs the same again.
        if self.proc is None:
            self.granted = self.cli._ledger(fam, 1, self.label, "reserve", want=self.max_requests)
            self.argv += ["--max-turns", str(self.granted)]
        else:
            self.cli._ledger(fam, self.granted, self.label, "reserve", want=self.granted)
        content = self._content(text)
        user_bytes = sum(len(b["text"].encode()) for b in content)
        if self.proc is None:
            self._start()
        self.first = False
        self.sends += 1
        began = time.time()
        self.proc.stdin.write(json.dumps({"type": "user", "message": {"role": "user", "content": content}}) + "\n")
        self.proc.stdin.flush()
        raw, texts, per_request, result, started, model = [], [], {}, None, False, ""
        while result is None:
            try:
                line = self.events.get(timeout=max(1.0, self.cli.timeout - (time.time() - began)))
            except queue.Empty:
                line = None
            if line is None:
                self.close(kill=True)
                break
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            raw.append(event)
            kind = event.get("type")
            if not started and kind in ("assistant", "stream_event"):
                started = True
                if on_start:
                    on_start()
            if kind == "assistant":
                message = event.get("message", {})
                model = message.get("model", model)
                per_request[message.get("id", len(per_request))] = message.get("usage", {})
                for block in message.get("content", []):
                    if block.get("type") == "text" and block.get("text", "").strip():
                        texts.append(block["text"])
                        if on_block:
                            on_block("unii", block["text"])
                    elif block.get("type") == "tool_use" and on_block:
                        name = block.get("name", "").removeprefix(f"mcp__{SERVER}__")
                        on_block("tool", f"{name} {json.dumps(block.get('input', {}), ensure_ascii=False)}")
            elif kind == "user" and on_block:
                body = event.get("message", {}).get("content", [])
                for block in body if isinstance(body, list) else []:
                    if block.get("type") == "tool_result":
                        inner = block.get("content", "")
                        if isinstance(inner, list):
                            inner = "\n".join(p.get("text", "") for p in inner if p.get("type") == "text")
                        on_block("echo", str(inner))
            elif kind == "result":
                result = event
        if on_start and not started:
            on_start()
        wall = time.time() - began
        n = max(1, len(per_request))
        self.cli._ledger(fam, n - self.granted, self.label, "settle")
        usage = (result or {}).get("usage", {})
        cut = (result or {}).get("subtype") == "error_max_turns"
        row = {
            "ts": began, "experiment": self.cli.experiment, "label": self.label, "send": self.sends,
            "model_requested": self.model, "model": model, "placement": self.cli.placement,
            "system_mode": self.cli.system_mode, "effort": self.effort, **self.cli.meta,
            "reserved": self.granted, "max_turns_hit": cut,
            "argv": self.argv, "system_bytes": self.system_bytes, "user_bytes": user_bytes,
            "view_bytes": len(self.view.encode()), "blocks": len(content),
            "usage": usage, "model_usage": (result or {}).get("modelUsage"), "requests": n,
            "per_request": list(per_request.values()), "wall_s": round(wall, 3),
            "duration_api_ms": (result or {}).get("duration_api_ms"),
            "cost_usd_reported": (result or {}).get("total_cost_usd"),
            "is_error": result is None or bool(result.get("is_error")),
            "result": (result or {}).get("result"), "events": raw,
        }
        if self.cli.calls:
            with _io:
                self.cli.calls.parent.mkdir(parents=True, exist_ok=True)
                with self.cli.calls.open("a") as fh:
                    fh.write(json.dumps(row, ensure_ascii=False) + "\n")
        if cut and self.granted > 1:
            pass                            # a turn stopped at its request allowance: keep what it said
        elif cut:
            raise ToolCalled(f"{self.label}: stopped after {self.granted} request(s) with a tool call pending")
        elif row["is_error"]:
            detail = str(row["result"] or (self.dir / "stderr.txt").read_text()[-2000:])
            limited = any(e.get("type") == "rate_limit_event"
                          and e.get("rate_limit_info", {}).get("status") == "rejected" for e in raw)
            low = detail.lower()
            if limited or "429" in low or "rate limit" in low or "usage limit" in low or "limit reached" in low:
                raise LimitError(detail)
            raise BackendError(detail or "claude -p ended without a result")
        return Reply("\n".join(texts) if texts else str(row["result"] or ""),
                     usage, model, n, wall, row)

    def close(self, kill: bool = False) -> None:
        proc = self.proc
        if proc is None:
            return
        try:
            proc.stdin.close()
        except OSError:
            pass
        try:
            if kill:
                proc.kill()
            proc.wait(timeout=30)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
        self.err.close()
        self.proc = None


def new_label() -> str:
    return uuid.uuid4().hex[:8]
