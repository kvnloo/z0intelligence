"""owner-canon-gate: runtime enforcement of the proven owner-canon laws (bend/owner_canon) for a Hermes profile.

Host boundary: this file turns a tool call into the typed facts the kernel decides on (kernel.py, the Python
twin of canon.bend). The kernel's refusal is final for the call; nothing here grants authorization.

Rules (law -> canon id):
  gpt_only_via_fx_pi            GPT models only through fx or pi, never Hermes chat/delegate_task/T3 Codex
  gui_never_steals_focus        C03: GUI automation only on a virtual/headless display
  done_needs_receipt            C06: a done/fixed report to a teammate needs evidence (path, sha, URL, exit code)
  quota_backs_off               C15: after a 429/quota error, the same call waits for Retry-After (default 60 s)
  delete_needs_creator_receipt  C40/C25: destructive deletes outside scratch need a finished-creator receipt
  public_needs_owner_ok         C31/C16: upstream PRs/issues and public posts need Kevin's OK

Modes per rule in $HERMES_HOME/owner-canon-gate.json {"mode": {"<law>": "enforce"|"flag"|"off"}}; default
enforce, except done_needs_receipt = flag (text heuristic). Approvals ("owner OK", "creator receipt") are files the
owner writes: $HERMES_HOME/owner-canon-approvals.json {"public": ["<substring>"...]}; any tool call that writes
that file is blocked. Receipts: $HERMES_HOME/owner-canon-gate/receipts.jsonl (law, tool, decision; no args).
Fail-closed per call for enforce rules: a classifier error blocks the call.
"""
from __future__ import annotations

import importlib.util
import json
import logging
import os
import re
import time
from pathlib import Path

logger = logging.getLogger(__name__)
_spec = importlib.util.spec_from_file_location("owner_canon_kernel", Path(__file__).with_name("kernel.py"))
assert _spec and _spec.loader
K = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(K)

DEFAULT_MODE = {law: "enforce" for law in K.RULES} | {"done_needs_receipt": "flag"}
_SHELL_TOOLS = {"terminal", "execute_code", "process", "mcp__terminal", "mcp__execute_code"}
_GPT = re.compile(r"(?i)\b(gpt-?\d[\w.\-]*|o[134](-mini|-pro)?\b|openai/[\w.\-]+|codex-mini[\w.\-]*|"
                  r"gpt-6\.1-(sol|astra|terra|luna)|\b(sol|astra|terra|luna)-gpt)")
_FX_PI = re.compile(r"(^|[;&|]\s*|\bexec\s+|\btimeout\s+\d+\s+)(fx|pi)\s")
_GUI = re.compile(r"(?i)\b(xdotool|ydotool|wmctrl|wtype|xte|hyprctl\s+dispatch|swaymsg\s+focus|osascript|cliclick|"
                  r"adb\b[^|;&]*\bshell\s+input\b|adb\b[^|;&]*\bam\s+start\b|pyautogui|pynput)")
_GUI_ISOLATED = re.compile(r"(?i)(--display[ =]?[1-9]\d*|DISPLAY=:(9\d|[1-9]\d\d+)|xvfb-run|Xvfb|--headless|"
                           r"WAYLAND_DISPLAY=\S*headless|fsdisplay|cage\s+-d|\bweston\b.*--backend=headless)")
_DELETE = re.compile(r"(?:^|[;&|(]\s*|\bsudo\s+)(rm\s+(-[a-zA-Z]*[rRf][a-zA-Z]*\s+)+\S|git\s+branch\s+-D\b|"
                     r"git\s+worktree\s+remove\b|git\s+push\s+\S+\s+--delete\b|git\s+push\s+\S+\s+:\S|"
                     r"gh\s+repo\s+delete\b|docker\s+(rm|rmi|volume\s+rm)\b|hf\s+(repo|buckets)\s+(delete|rm)\b|"
                     r"rclone\s+(purge|delete)\b|\bDROP\s+(TABLE|DATABASE)\b)", re.M)
_SCRATCH = re.compile(r"(/cache/scratch/|/tmp/|\$TMPDIR|\$\{TMPDIR\}|/\.cache/|__pycache__|/build/|\.pytest_cache)")
_RECEIPT_REF = re.compile(r"canon-creator-receipt[=:]\s*(\S+)")
_PUBLIC = re.compile(r"(?i)\b(gh\s+(pr|issue)\s+(create|comment)\b[^\n]*--repo[ =]([\w.\-]+)/|"
                     r"gh\s+(release\s+create|gist\s+create\b[^\n]*--public)|xurl\s+(post|tweet)|twurl|"
                     r"npm\s+publish|twine\s+upload|cargo\s+publish|gh\s+repo\s+edit\b[^\n]*--visibility\s+public)")
_DONE = re.compile(r"(?i)\b(done|fixed|finished|complete[d]?|works now|verified|shipped|resolved)\b")
_EVIDENCE = re.compile(r"(/[\w.\-]+){2,}|\b[0-9a-f]{7,40}\b|https?://|\bexit(\s*code)?\s*[=:]?\s*\d|\brc=\d|"
                       r"\bsha256\b|\bPASS\b|\d+/\d+\s+(passed|ok)")
_QUOTA = re.compile(r"(?i)(\b429\b|rate.?limit|quota|too many requests|usage limit|retry-after)")
_last_limited: dict[str, float] = {}


def _home() -> Path:
    try:
        from hermes_constants import get_hermes_home  # type: ignore
        return Path(get_hermes_home())
    except Exception:
        return Path(os.environ.get("HERMES_HOME", Path.home() / ".hermes"))


def _json(path: Path) -> dict:
    try:
        return json.loads(path.read_text())
    except Exception:
        return {}


def _mode(law: str) -> str:
    return (_json(_home() / "owner-canon-gate.json").get("mode") or {}).get(law, DEFAULT_MODE[law])


def _receipt(rec: dict) -> None:
    try:
        d = _home() / "owner-canon-gate"
        d.mkdir(parents=True, exist_ok=True)
        with open(d / "receipts.jsonl", "a") as f:
            f.write(json.dumps({"at": time.strftime("%Y-%m-%dT%H:%M:%S"), **rec}) + "\n")
    except Exception:
        pass


def _text(tool: str, args: dict) -> str:
    if tool in _SHELL_TOOLS:
        return "\n".join(str(args.get(k) or "") for k in ("command", "code", "data"))
    return json.dumps(args, default=str)


def _key(tool: str, args: dict) -> str:
    return tool + ":" + _text(tool, args)[:300]


def facts(tool: str, args: dict) -> dict:
    """Host facts for one tool call: {law: (trigger, cleared, why)}."""
    t = _text(tool, args)
    f = {}
    # GPT route: a GPT model named in a dispatch (delegate_task, hermes -m, codex/openai CLIs) not via fx/pi
    # only real launches count: delegate_task, or a shell command line that starts a model CLI. Prose that merely
    # names a GPT model (messages, files being written) is not a dispatch.
    dispatch = tool in ("delegate_task", "mcp__delegate_task") or (tool in _SHELL_TOOLS and bool(
        re.search(r"(?i)\b(hermes|codex|openai|llm|aider|opencode|claude)\b[^\n]*(-m|--model|model[=:])", t)))
    gpt = dispatch and bool(_GPT.search(t))
    f["gpt_only_via_fx_pi"] = (gpt, gpt and bool(_FX_PI.search(t)), "GPT model dispatched outside fx/pi")
    gui = tool in _SHELL_TOOLS and bool(_GUI.search(t))
    f["gui_never_steals_focus"] = (gui, gui and bool(_GUI_ISOLATED.search(t)), "GUI input on the owner's display")
    if tool in ("message_agent", "mcp__message_agent", "send_message"):
        msg = str(args.get("message") or args.get("text") or "")
        claim = bool(_DONE.search(msg))
        f["done_needs_receipt"] = (claim, claim and bool(_EVIDENCE.search(msg)), "done claim without evidence")
    k = _key(tool, args)
    until = _last_limited.get(k, 0)
    f["quota_backs_off"] = (until > 0, time.time() >= until, "retry before the quota/rate-limit back-off ended")
    dels = [m.group(0) for m in _DELETE.finditer(t)] if tool in _SHELL_TOOLS else []
    if dels:
        risky = [d for d in dels if not _SCRATCH.search(t[t.find(d): t.find(d) + 400].split("\n")[0])]
        ref = _RECEIPT_REF.search(t)
        ok = bool(ref and Path(os.path.expanduser(ref.group(1))).is_file())
        f["delete_needs_creator_receipt"] = (bool(risky), ok, "delete outside scratch without a creator receipt")
    pub = _PUBLIC.search(t) if tool in _SHELL_TOOLS else None
    if pub:
        owner = (pub.group(4) or "").lower()
        upstream = not owner or owner != "kvnloo"
        appr = _json(_home() / "owner-canon-approvals.json").get("public") or []
        okd = any(a and a in t for a in appr)
        f["public_needs_owner_ok"] = (upstream, okd, "public/upstream action without the owner's OK")
    return f


def decide(tool: str, args: dict) -> tuple[bool, list[tuple[str, str]]]:
    """Returns (block, violations[(law, why)]). Uses the twin kernel predicates, then per-law mode."""
    f = facts(tool, args)
    full = {law: f.get(law, (False, False, ""))[:2] for law in K.RULES}
    flat = [b for law in ("gpt_only_via_fx_pi", "gui_never_steals_focus", "done_needs_receipt", "quota_backs_off",
                          "delete_needs_creator_receipt", "public_needs_owner_ok") for b in full[law]]
    if K.admits(*flat):
        return False, []
    viol = [(law, f[law][2]) for law, (trg, clr) in full.items() if not K.RULES[law][1](trg, clr)]
    block = any(_mode(law) == "enforce" for law, _ in viol)
    viol = [(law, why) for law, why in viol if _mode(law) != "off"]
    return block and bool(viol), viol


def on_pre_tool_call(tool_name: str = "", args: dict | None = None, **kw):
    args = args or {}
    try:
        raw = json.dumps(args, default=str)
        if "owner-canon-approvals.json" in raw and (tool_name in ("write_file", "patch", "mcp__write_file", "mcp__patch")
                                                    or (tool_name in _SHELL_TOOLS and
                                                        re.search(r"(write|>|tee |cp |mv |sed -i|open\()", raw))):
            _receipt({"law": "approval_file_protected", "tool": tool_name, "decision": "block"})
            return {"action": "block", "message": "owner-canon: agents never write the owner's approvals file."}
        block, viol = decide(tool_name, args)
        if not viol:
            return None
        _receipt({"laws": [v[0] for v in viol], "tool": tool_name, "decision": "block" if block else "flag",
                  "session_id": kw.get("session_id")})
        text = "; ".join(f"{law} ({why})" for law, why in viol)
        if block:
            return {"action": "block", "message": f"owner-canon gate blocked this call: {text}. "
                    "See bend/owner_canon/LAWS.bend. Fix the call (fx/pi for GPT, a virtual display, back off, "
                    "a creator receipt, or ask the owner) instead of retrying it."}
        logger.warning("owner-canon flag: %s", text)
        return None
    except Exception as e:
        _receipt({"law": "classifier_error", "tool": tool_name, "decision": "block", "error": type(e).__name__})
        return {"action": "block", "message": f"owner-canon gate could not classify this call ({type(e).__name__}); blocked."}


def on_post_tool_call(tool_name: str = "", args: dict | None = None, result=None, **kw):
    try:
        r = result if isinstance(result, str) else json.dumps(result, default=str)
        if r and _QUOTA.search(r[:4000]):
            m = re.search(r"(?i)retry.?after\D{0,5}(\d+)", r)
            _last_limited[_key(tool_name, args or {})] = time.time() + (int(m.group(1)) if m else 60)
    except Exception:
        pass


def on_pre_llm_call(model: str = "", **kw):
    """Flag (context note) when this Hermes session itself runs on a GPT model: the owner rule forbids it."""
    if model and _GPT.search(model) and _mode("gpt_only_via_fx_pi") != "off":
        _receipt({"law": "gpt_only_via_fx_pi", "tool": "pre_llm_call", "decision": "flag", "model": model})
        return {"context": "[owner-canon] This session runs on a GPT model inside Hermes, which the owner forbids "
                           "(GPT only via fx/pi). Stop and hand the task to fx or pi with a contract brief."}
    return None


def register(ctx):
    ctx.register_hook("pre_tool_call", on_pre_tool_call)
    ctx.register_hook("post_tool_call", on_post_tool_call)
    ctx.register_hook("pre_llm_call", on_pre_llm_call)
