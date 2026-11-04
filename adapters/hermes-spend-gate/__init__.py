"""spend-gate: O19 for a Hermes profile. No real money without the owner's approval.

Two call sites, one proven decision (z0intelligence bend/ops_fsm O19, twin z0int.fsm.ops.spend_allows,
host facts in z0int.fsm.spend_gate):

- pre_tool_call: a terminal command or execute_code program that would launch paid compute or call a
  paid API (Modal, HF Jobs, RunPod, Vast, SkyPilot, cloud CLIs, api.openai.com, ...) is blocked unless the
  surface is free, a flat subscription, free credit under the cap with the hard stop armed, or carries an
  unexpired owner approval. Any tool call that mentions the approval/funding files or the O19 switch is
  blocked, so an agent cannot forge an approval or reclassify a surface.
- llm_request: a provider request whose provider is metered (pay-as-you-go API key) or unclassified is
  rewritten to a model name no provider serves and a one-token body, so it fails without a charge and the
  turn falls through to the configured fallbacks.

Fail-closed per call: an internal error blocks that tool call / refuses that request. Kernel path:
$HERMES_HOME/spend-gate.json {"kernel": "~/.local/lib/z0-guard/spend/current/src"}.
Receipts: ~/.z0int/state/spend/receipts.jsonl (no message text, no keys).

Not covered: auxiliary calls (titles, compression, vision) skip llm_request middleware; other harnesses
(OMP, Codex CLI, Claude Code) need their own adapter; an SDK this classifier does not recognise.
The Modal hard stop (z0-spend-stop@modal.timer) bounds Modal at its cap regardless of caller.
"""
from __future__ import annotations

import json
import logging
import os
import sys
from pathlib import Path

logger = logging.getLogger(__name__)
_REFUSED_MODEL = "o19-spend-refused"
_WRITE_TOOLS = {"terminal", "execute_code", "write_file", "patch", "process", "mcp__terminal",
                "mcp__execute_code", "mcp__write_file", "mcp__patch"}


def _home() -> Path:
    try:
        from hermes_constants import get_hermes_home  # type: ignore
        return Path(get_hermes_home())
    except Exception:
        return Path(os.environ.get("HERMES_HOME", Path.home() / ".hermes"))


def _config() -> dict:
    try:
        return json.loads((_home() / "spend-gate.json").read_text())
    except Exception:
        return {}


_CFG = _config()
_KERNEL_ERR = None
try:
    sys.path.insert(0, os.path.expanduser(_CFG.get("kernel", "~/.local/lib/z0-guard/spend/current/src")))
    from z0int.fsm import spend_gate as S  # noqa: E402
except Exception as e:  # kernel missing: classify nothing, refuse spend-shaped calls below
    S = None
    _KERNEL_ERR = f"{type(e).__name__}: {e}"


def _ids(kw: dict) -> dict:
    return {k: kw.get(k) for k in ("session_id", "task_id") if kw.get(k)}


def on_pre_tool_call(tool_name: str = "", args: dict | None = None, **kw):
    args = args or {}
    try:
        if S is None:
            text = json.dumps(args)
            if any(w in text for w in ("modal ", "import modal", "spend-approvals", "spend-funding")):
                return {"action": "block", "message": f"O19 spend gate kernel unavailable ({_KERNEL_ERR}); "
                                                      "spend-shaped calls are blocked."}
            return None
        text = json.dumps(args, default=str)
        if tool_name in _WRITE_TOOLS and S.touches_protected(text):
            S.receipt({"event": "protected_blocked", "tool": tool_name, **_ids(kw)})
            return {"action": "block", "message": "O19: agents never write spend approvals, funding classes or "
                                                  "the O19 switch. Ask the owner to approve in his own terminal."}
        cmd = args.get("command") if isinstance(args.get("command"), str) else ""
        code = args.get("code") if isinstance(args.get("code"), str) else None
        if not cmd and code is None:
            return None
        surfaces = S.command_surfaces(cmd, code)
        if not surfaces:
            return None
        res = S.evaluate(surfaces)
        S.receipt({"event": "tool", "tool": tool_name, **res, **_ids(kw)})
        if res["allowed"] or S.mode() != "enforce":
            return None
        return {"action": "block", "message": S.refusal_text(res)}
    except Exception as e:
        logger.warning("spend-gate pre_tool_call error: %s", e)
        return {"action": "block", "message": f"O19 spend gate could not classify this call ({type(e).__name__}); blocked."}


def on_llm_request(request: dict | None = None, provider: str = "", base_url: str = "", model: str = "", **kw):
    req = dict(request or {})
    try:
        if S is None:
            raise RuntimeError(_KERNEL_ERR or "kernel unavailable")
        surface, fund = S.llm_surface(provider, base_url, req.get("model") or model)
        if fund in (S.F.FREE, S.F.SUB):
            return None
        res = S.evaluate([(surface, fund, "provider request")])
        S.receipt({"event": "llm", "provider": provider, "model": req.get("model") or model, **res, **_ids(kw)})
        if res["allowed"] or S.mode() != "enforce":
            return None
        reason = S.refusal_text(res)
    except Exception as e:
        reason = f"O19 refused (spend gate error: {type(e).__name__})"
    req["model"] = _REFUSED_MODEL
    if "messages" in req:
        req["messages"] = [{"role": "user", "content": "."}]
    for k in ("max_tokens", "max_completion_tokens", "max_output_tokens"):
        if k in req:
            req[k] = 1
    req.pop("tools", None)
    return {"request": req, "source": "spend-gate", "reason": reason}


def register(ctx):
    ctx.register_hook("pre_tool_call", on_pre_tool_call)
    ctx.register_middleware("llm_request", on_llm_request)
