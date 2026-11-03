"""Tool-free, single-reply calls to the frontier "editor" models.

Every editor gets the same neutral system line the paper used ("Answer the user's
request.") and the prompt on stdin. Each call returns the reply text, usage, the
observed model, and an audit that says whether the run made any tool call. A run
that used a tool is recorded but does not count (``valid`` is False).

Routes (all on the owner's own subscriptions/keys):
  astra    codex exec, gpt-6-astra, the paper's no-tools Codex config
  opus     Anthropic Messages API (owner's Claude OAuth), claude-opus-5-5, no tools offered
  fable    Anthropic Messages API (owner's Claude OAuth), claude-fable-5-1, no tools offered
  argon    agy, gemini-4-argon-high, plan mode + sandbox in an empty dir; the stream is audited
  deepseek DeepSeek API, deepseek-v4-pro (key from bws via bws-env-exec)
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

INSTRUCTIONS = "Answer the user's request."
# Output caps (reasoning included). 64k starved Opus/Fable/DeepSeek on the write task (all 64k spent thinking, no
# answer), so every API route gets the largest cap it accepts; codex and agy manage their own caps.
MAX_OUT = 128000
DEEPSEEK_MAX_OUT = int(os.environ.get("WSP_DEEPSEEK_MAX_OUT", "128000"))
STATE = Path(os.environ.get("Z0INT_HOME", Path.home() / ".z0int")) / "state" / "weight-space"

EDITORS = {
    "astra": {"route": "codex", "model": "gpt-6-astra"},
    "opus": {"route": "claude", "model": "claude-opus-5-5"},
    "fable": {"route": "claude", "model": "claude-fable-5-1"},
    "argon": {"route": "agy", "model": "gemini-4-argon-high"},
    "deepseek": {"route": "deepseek", "model": "deepseek-v4-pro"},
}

CODEX_CONFIG = """\
# The paper's no-tools Codex config (lm_understand-logs harness/codex_config.toml), model/effort set per call.
approval_policy = "never"
sandbox_mode = "read-only"
web_search = "disabled"
model_instructions_file = "{instructions}"

[agents]
enabled = false

[features]
code_mode = false
collaboration_modes = false
shell_tool = false
unified_exec = false
unified_exec_tty = false
shell_snapshot = false
view_image = false
image_generation = false
multi_agent = false
apps = false
plugins = false
remote_plugin = false
browser_use = false
browser_use_external = false
browser_use_full_cdp_access = false
in_app_browser = false
computer_use = false
code_mode_host = false
skill_search = false
skill_mcp_dependency_install = false
sleep_tool = false
tool_suggest = false
goals = false
hooks = false
worktrees = false
workspace_dependencies = false
"""


@dataclass
class Reply:
    editor: str
    model_requested: str
    model_observed: str | None
    text: str
    usage: dict
    wall_s: float
    exit_code: int
    tool_free: bool
    audit_note: str
    raw_path: str | None = None
    extra: dict = field(default_factory=dict)

    @property
    def valid(self) -> bool:
        return (self.exit_code == 0 and bool(self.text.strip()) and self.tool_free
                and self.model_observed == self.model_requested)

    def to_json(self) -> dict:
        d = self.__dict__.copy()
        d["valid"] = self.valid
        return d


def _empty_dir(root: Path) -> Path:
    d = Path(tempfile.mkdtemp(prefix="wsp-cwd-", dir=root))
    return d


def _codex_home(root: Path) -> Path:
    home = root / "codex_home"
    home.mkdir(parents=True, exist_ok=True)
    (home / "instructions_neutral.md").write_text(INSTRUCTIONS)
    (home / "config.toml").write_text(CODEX_CONFIG.format(instructions=home / "instructions_neutral.md"))
    auth = home / "auth.json"
    src = Path.home() / ".codex" / "auth.json"
    if not auth.exists():
        # Copy, not symlink: codex may refresh tokens in place; a copy keeps the user's file untouched.
        shutil.copy2(src, auth)
        os.chmod(auth, 0o600)
    return home


def call(editor: str, prompt: str, effort: str, workdir: Path, timeout_s: int = 3600) -> Reply:
    spec = EDITORS[editor]
    workdir.mkdir(parents=True, exist_ok=True)
    fn = {"codex": _codex, "claude": _claude, "agy": _agy, "deepseek": _deepseek}[spec["route"]]
    return fn(editor, spec["model"], prompt, effort, workdir, timeout_s)


def _run(cmd, stdin: str, cwd: Path, env=None, timeout_s=3600):
    t0 = time.time()
    p = subprocess.run(cmd, check=False, input=stdin, capture_output=True, text=True, cwd=cwd, env=env, timeout=timeout_s)
    return p, time.time() - t0


def _codex(editor, model, prompt, effort, workdir, timeout_s):
    home = _codex_home(STATE)
    cwd = _empty_dir(workdir)
    env = {k: v for k, v in os.environ.items() if not k.startswith(("GIT_", "CODEX_"))}
    env["CODEX_HOME"] = str(home)
    cmd = ["codex", "exec", "--json", "--skip-git-repo-check", "-C", str(cwd), "-m", model,
           "-c", f"model_reasoning_effort={effort}", "-c", "model_reasoning_summary=detailed", "-"]
    p, wall = _run(cmd, prompt, cwd, env, timeout_s)
    raw = workdir / "events.jsonl"
    raw.write_text(p.stdout)
    (workdir / "stderr.log").write_text(p.stderr[-20000:])
    text, usage, tool_items, kinds = "", {}, [], set()
    for line in p.stdout.splitlines():
        try:
            ev = json.loads(line)
        except json.JSONDecodeError:
            continue
        t = ev.get("type", "")
        if t == "turn.completed":
            usage = ev.get("usage", {})
        item = ev.get("item") or {}
        it = item.get("type") or item.get("item_type")
        if it:
            kinds.add(it)
        if t == "item.completed" and it in ("agent_message", "assistant_message"):
            text = item.get("text", text)
        if it and it not in ("agent_message", "assistant_message", "reasoning", "error"):
            tool_items.append(it)
    return Reply(editor, model, model if p.returncode == 0 else None, text, usage, wall, p.returncode,
                 not tool_items, f"item kinds={sorted(kinds)}", str(raw))


def _claude(editor, model, prompt, effort, workdir, timeout_s):
    """Anthropic Messages API on the owner's Claude subscription (OAuth token ANTHROPIC_TOKEN, as Hermes
    sends it). No tools are offered; the system is the Claude Code identity line OAuth requires, then the
    paper's neutral line. Adaptive thinking at the requested effort, streamed."""
    import anthropic

    token = os.environ.get("ANTHROPIC_TOKEN")
    if not token:
        raise RuntimeError("ANTHROPIC_TOKEN missing: run under `bws-env-exec --scope hermes -- ...`")
    ver = subprocess.run(["claude", "--version"], check=False, capture_output=True, text=True).stdout.split()[0]
    client = anthropic.Anthropic(auth_token=token, timeout=timeout_s, default_headers={
        "anthropic-beta": "claude-code-20250219,oauth-2025-04-20,interleaved-thinking-2025-05-14",
        "user-agent": f"claude-code/{ver} (external, cli)", "x-app": "cli"})
    system = [{"type": "text", "text": "You are Claude Code, Anthropic's official CLI for Claude."},
              {"type": "text", "text": INSTRUCTIONS}]
    t0 = time.time()
    code, text, usage, observed, stop, blocks = 0, "", {}, None, None, []
    try:
        with client.messages.stream(model=model, max_tokens=MAX_OUT, system=system,
                                    messages=[{"role": "user", "content": prompt}],
                                    thinking={"type": "adaptive", "display": "summarized"},
                                    extra_body={"output_config": {"effort": effort}}) as st:
            msg = st.get_final_message()
        observed, stop = msg.model, msg.stop_reason
        usage = msg.usage.model_dump()
        blocks = [b.model_dump() for b in msg.content]
        text = "".join(b.text for b in msg.content if b.type == "text")
    except anthropic.APIError as e:
        code = getattr(e, "status_code", None) or 1
        blocks = [{"error": str(e)[:4000]}]
    wall = time.time() - t0
    raw = workdir / "response.json"
    raw.write_text(json.dumps({"model": observed, "stop_reason": stop, "usage": usage, "content": blocks}, indent=1))
    tool_uses = sum(1 for b in blocks if b.get("type") == "tool_use")
    return Reply(editor, model, observed, text, usage, wall, code, tool_uses == 0,
                 f"no tools offered (raw API); stop_reason={stop}", str(raw), {"stop_reason": stop})


def _agy(editor, model, prompt, effort, workdir, timeout_s):
    cwd = _empty_dir(workdir)
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    msg = json.dumps({"event": "user", "message": {"content": INSTRUCTIONS + "\n\n" + prompt}})
    cmd = ["agy", "--model", model, "--mode", "plan", "--sandbox", "--input-format", "stream-json",
           "--output-format", "stream-json", "--print-timeout", f"{timeout_s}s", "--disable-slash-commands"]
    p, wall = _run(cmd, msg + "\n", cwd, env, timeout_s + 60)
    raw = workdir / "events.jsonl"
    raw.write_text(p.stdout)
    (workdir / "stderr.log").write_text(p.stderr[-20000:])
    text, usage, observed, step_types, status = "", {}, None, set(), None
    for line in p.stdout.splitlines():
        try:
            ev = json.loads(line)
        except json.JSONDecodeError:
            continue
        if ev.get("event") == "init":
            observed = ev["init"].get("model")
        su = ev.get("step_update")
        if su:
            step_types.add(su.get("step_type"))
        if ev.get("event") == "result":
            r = ev["result"]
            text, usage, status = r.get("response", ""), r.get("usage", {}) | {"num_turns": r.get("num_turns")}, r.get("status")
    bad = step_types - {"user_input", "agent_response", "planner_response", "error_message", None}
    # agy auto-continues once a reply hits its output cap ("error_message" step, then another agent_response) and
    # reports status ERROR even when that continuation finished the answer; a non-empty, tool-free reply counts.
    code = 0 if (status == "SUCCESS" or (text.strip() and not bad)) else (p.returncode or 1)
    return Reply(editor, model, observed, text, usage, wall, code, not bad,
                 f"status={status} step_types={sorted(t for t in step_types if t)}", str(raw))


def _deepseek(editor, model, prompt, effort, workdir, timeout_s):
    key = os.environ.get("DEEPSEEK_API_KEY")
    if not key:
        raise RuntimeError("DEEPSEEK_API_KEY missing: run under `bws-env-exec DEEPSEEK_API_KEY -- ...`")
    body = {"model": model, "messages": [{"role": "system", "content": INSTRUCTIONS},
                                         {"role": "user", "content": prompt}],
            "stream": False, "max_tokens": DEEPSEEK_MAX_OUT}
    if effort:
        body["reasoning_effort"] = effort
    req = urllib.request.Request("https://api.deepseek.com/chat/completions", data=json.dumps(body).encode(),
                                 headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"})
    t0 = time.time()
    code, data = 0, {}  # type: dict
    try:
        with urllib.request.urlopen(req, timeout=timeout_s) as r:
            data = json.loads(r.read())
    except urllib.error.HTTPError as e:  # keep the body for the record
        code, data = e.code, {"error": e.read().decode(errors="replace")[:4000]}
    wall = time.time() - t0
    raw = workdir / "response.json"
    raw.write_text(json.dumps(data, indent=1))
    choice = (data.get("choices") or [{}])[0].get("message", {})
    text = choice.get("content") or ""
    return Reply(editor, model, data.get("model"), text, data.get("usage", {}), wall, code,
                 not choice.get("tool_calls"), "no tools offered (raw API)", str(raw),
                 {"finish_reason": (data.get("choices") or [{}])[0].get("finish_reason")})


class Chat:
    """A multi-turn, tool-free conversation with one editor (the Experiment-8 style loop sends tool output
    back as the next user message). History lives in the provider session (codex thread, agy conversation)
    or here (Anthropic, DeepSeek). Every turn's raw record goes to workdir/tNNN/."""

    def __init__(self, editor: str, effort: str, workdir: Path, timeout_s: int = 3600):
        self.editor, self.effort, self.workdir, self.timeout_s = editor, effort, workdir, timeout_s
        self.spec = EDITORS[editor]
        self.history: list[dict] = []
        self.session_id: str | None = None
        self.turn = 0
        workdir.mkdir(parents=True, exist_ok=True)
        self.cwd = _empty_dir(workdir)

    def send(self, message: str) -> Reply:
        self.turn += 1
        tdir = self.workdir / f"t{self.turn:03d}"
        tdir.mkdir(parents=True, exist_ok=True)
        (tdir / "prompt.txt").write_text(message)
        route = self.spec["route"]
        if route in ("claude", "deepseek"):
            r = self._stateless(message, tdir)
        elif route == "codex":
            r = self._codex(message, tdir)
        else:
            r = self._agy(message, tdir)
        (tdir / "answer.md").write_text(r.text)
        (tdir / "run.json").write_text(json.dumps(r.to_json(), indent=1, default=str))
        return r

    def reset(self):
        """Start a fresh conversation (the caller sends a handoff message next)."""
        self.history, self.session_id = [], None

    def _stateless(self, message, tdir):
        msgs = self.history + [{"role": "user", "content": message}]
        fn = _claude_msgs if self.spec["route"] == "claude" else _deepseek_msgs
        r = fn(self.editor, self.spec["model"], msgs, self.effort, tdir, self.timeout_s)
        if r.exit_code == 0 and r.text.strip():
            self.history = msgs + [{"role": "assistant", "content": r.text}]
        return r

    def _codex(self, message, tdir):
        home = _codex_home(STATE)
        env = {k: v for k, v in os.environ.items() if not k.startswith(("GIT_", "CODEX_"))}
        env["CODEX_HOME"] = str(home)
        opts = ["-c", f"model_reasoning_effort={self.effort}", "-c", "model_reasoning_summary=detailed"]
        if self.session_id:
            cmd = ["codex", "exec", "resume", "--json", "--skip-git-repo-check", *opts, self.session_id, "-"]
        else:
            cmd = ["codex", "exec", "--json", "--skip-git-repo-check", "-C", str(self.cwd), "-m",
                   self.spec["model"], *opts, "-"]
        p, wall = _run(cmd, message, self.cwd, env, self.timeout_s)
        (tdir / "events.jsonl").write_text(p.stdout)
        (tdir / "stderr.log").write_text(p.stderr[-20000:])
        text, usage, tools = "", {}, []
        for line in p.stdout.splitlines():
            try:
                ev = json.loads(line)
            except json.JSONDecodeError:
                continue
            if ev.get("type") == "thread.started":
                self.session_id = ev.get("thread_id")
            if ev.get("type") == "turn.completed":
                usage = ev.get("usage", {})
            item = ev.get("item") or {}
            it = item.get("type")
            if ev.get("type") == "item.completed" and it == "agent_message":
                text = item.get("text", text)
            if it and it not in ("agent_message", "reasoning", "error"):
                tools.append(it)
        return Reply(self.editor, self.spec["model"], self.spec["model"] if p.returncode == 0 else None, text,
                     usage, wall, p.returncode, not tools, f"thread={self.session_id} tools={tools}")

    def _agy(self, message, tdir):
        env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
        content = message if self.session_id else INSTRUCTIONS + "\n\n" + message
        msg = json.dumps({"event": "user", "message": {"content": content}})
        cmd = ["agy", "--model", self.spec["model"], "--mode", "plan", "--sandbox",
               "--input-format", "stream-json", "--output-format", "stream-json",
               "--print-timeout", f"{self.timeout_s}s", "--disable-slash-commands"]
        if self.session_id:
            cmd += ["--conversation", self.session_id]
        p, wall = _run(cmd, msg + "\n", self.cwd, env, self.timeout_s + 60)
        (tdir / "events.jsonl").write_text(p.stdout)
        (tdir / "stderr.log").write_text(p.stderr[-20000:])
        text, usage, steps, status = "", {}, set(), None
        for line in p.stdout.splitlines():
            try:
                ev = json.loads(line)
            except json.JSONDecodeError:
                continue
            if ev.get("event") == "init":
                self.session_id = self.session_id or ev["init"].get("conversation_id")
            su = ev.get("step_update")
            if su:
                steps.add(su.get("step_type"))
                self.session_id = self.session_id or su.get("conversation_id")
            if ev.get("event") == "result":
                r = ev["result"]
                text, usage, status = r.get("response", ""), r.get("usage", {}), r.get("status")
                self.session_id = self.session_id or r.get("conversation_id")
        bad = steps - {"user_input", "agent_response", "planner_response", "error_message", None}
        code = 0 if (status == "SUCCESS" or (text.strip() and not bad)) else (p.returncode or 1)
        return Reply(self.editor, self.spec["model"], self.spec["model"], text, usage, wall, code, not bad,
                     f"status={status} steps={sorted(s for s in steps if s)} conv={self.session_id}")


def _claude_msgs(editor, model, msgs, effort, workdir, timeout_s):
    import anthropic

    token = os.environ["ANTHROPIC_TOKEN"]
    ver = subprocess.run(["claude", "--version"], check=False, capture_output=True, text=True).stdout.split()[0]
    client = anthropic.Anthropic(auth_token=token, timeout=timeout_s, default_headers={
        "anthropic-beta": "claude-code-20250219,oauth-2025-04-20,interleaved-thinking-2025-05-14",
        "user-agent": f"claude-code/{ver} (external, cli)", "x-app": "cli"})
    system = [{"type": "text", "text": "You are Claude Code, Anthropic's official CLI for Claude."},
              {"type": "text", "text": INSTRUCTIONS, "cache_control": {"type": "ephemeral"}}]
    # cache the conversation prefix: mark the last user turn
    msgs = [dict(m) for m in msgs]
    msgs[-1] = {"role": "user", "content": [{"type": "text", "text": msgs[-1]["content"],
                                             "cache_control": {"type": "ephemeral"}}]}
    t0, code, text, usage, observed, stop, blocks = time.time(), 0, "", {}, None, None, []
    for attempt in range(4):
        try:
            with client.messages.stream(model=model, max_tokens=MAX_OUT, system=system, messages=msgs,
                                        thinking={"type": "adaptive", "display": "summarized"},
                                        extra_body={"output_config": {"effort": effort}}) as st:
                msg = st.get_final_message()
            observed, stop, usage = msg.model, msg.stop_reason, msg.usage.model_dump()
            blocks = [b.model_dump() for b in msg.content]
            text = "".join(b.text for b in msg.content if b.type == "text")
            code = 0
            break
        except anthropic.APIError as e:
            code = getattr(e, "status_code", None) or 1
            blocks = [{"error": str(e)[:4000]}]
            if code in (400, 401, 403):
                break
            time.sleep(30 * (attempt + 1))
    (workdir / "response.json").write_text(json.dumps({"model": observed, "stop_reason": stop, "usage": usage,
                                                       "content": blocks}, indent=1))
    return Reply(editor, model, observed, text, usage, time.time() - t0, code, True, f"stop={stop}")


def _deepseek_msgs(editor, model, msgs, effort, workdir, timeout_s):
    key = os.environ["DEEPSEEK_API_KEY"]
    body = {"model": model, "messages": [{"role": "system", "content": INSTRUCTIONS}] + msgs, "stream": False,
            "max_tokens": DEEPSEEK_MAX_OUT, "reasoning_effort": effort}
    t0, code, data = time.time(), 0, {}
    for attempt in range(4):
        req = urllib.request.Request("https://api.deepseek.com/chat/completions", data=json.dumps(body).encode(),
                                     headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=timeout_s) as r:
                data, code = json.loads(r.read()), 0
            break
        except urllib.error.HTTPError as e:
            code, data = e.code, {"error": e.read().decode(errors="replace")[:4000]}
            if code in (400, 401, 403):
                break
        except (urllib.error.URLError, TimeoutError) as e:
            code, data = 1, {"error": str(e)}
        time.sleep(30 * (attempt + 1))
    (workdir / "response.json").write_text(json.dumps(data, indent=1))
    choice = (data.get("choices") or [{}])[0].get("message", {})
    return Reply(editor, model, data.get("model"), choice.get("content") or "", data.get("usage", {}),
                 time.time() - t0, code, not choice.get("tool_calls"), "raw API")
