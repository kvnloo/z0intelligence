"""privacy-route: O18 privacy-tier model routing for a Hermes profile.

Every main-turn provider request is re-pointed at the vLLM Semantic Router lane that the proven
O18 table (z0intelligence bend/ops_fsm, twin z0int.fsm.ops.route) picks for the request's data
tier and task; tool calls that would read T4 secrets are blocked.

Fail-closed by construction, because Hermes middleware and hooks fail open:
- every callback catches its own errors and answers with the refusing outcome;
- the profile's own default model must be the local lane (vllm-sr/local), so a request that
  bypasses this middleware entirely still never leaves the host;
- queue/refuse rewrite the model to a name the router does not serve (400, no backend call).

Config: $HERMES_HOME/privacy-route.json
  {"floor": "personal", "kernel": "~/.local/lib/z0-guard/privacy-route/current/src",
   "router": "http://10.43.216.55:8899/v1"}
Receipts: $HERMES_HOME/logs/privacy-route.jsonl (one row per decision; no message text).

Not covered: auxiliary calls (compression, titles, ...) skip llm_request middleware; pin
auxiliary.*.model to vllm-sr/local in the profile. Path and content classification are
heuristics (z0int.fsm.privacy_route), not proven.
"""
from __future__ import annotations

import json
import logging
import os
import sys
import threading
import time
from pathlib import Path

logger = logging.getLogger(__name__)
_LOCK = threading.Lock()
_REFUSE = "vllm-sr/o18-refused"


def _home() -> Path:
    try:
        from hermes_constants import get_hermes_home  # type: ignore
        return Path(get_hermes_home())
    except Exception:
        return Path(os.environ.get("HERMES_HOME", Path.home() / ".hermes"))


def _config() -> dict:
    try:
        return json.loads((_home() / "privacy-route.json").read_text())
    except Exception:
        return {}


_CFG = _config()
_KERNEL_ERR = None
try:
    sys.path.insert(0, os.path.expanduser(_CFG.get("kernel", "~/.local/lib/z0-guard/privacy-route/current/src")))
    if _CFG.get("router"):
        os.environ.setdefault("O18_ROUTER_URL", _CFG["router"])
    from z0int.fsm import ops as P  # noqa: E402
    from z0int.fsm import privacy_route as R  # noqa: E402
except Exception as e:  # kernel missing: refuse everything
    P = R = None
    _KERNEL_ERR = f"{type(e).__name__}: {e}"

_STATE = _home() / "cache" / "privacy-route" / "taint.json"
_RECEIPTS = _home() / "logs" / "privacy-route.jsonl"


def _load_taint() -> dict:
    try:
        return json.loads(_STATE.read_text())
    except Exception:
        return {}


_TAINT: dict = _load_taint()


def _keys(kw: dict) -> list[str]:
    return [f"{k}:{kw[k]}" for k in ("session_id", "task_id") if kw.get(k)]


def _taint_get(kw: dict):
    t = P.Tier.PUBLIC
    for k in _keys(kw):
        if k in _TAINT:
            t = P.tier_join(t, P.Tier[_TAINT[k]])
    return t


def _taint_add(kw: dict, tier) -> None:
    with _LOCK:
        for k in _keys(kw) or ["global:unkeyed"]:
            prev = P.Tier[_TAINT[k]] if k in _TAINT else P.Tier.PUBLIC
            _TAINT[k] = P.tier_join(prev, tier).name
        try:
            _STATE.parent.mkdir(parents=True, exist_ok=True)
            tmp = _STATE.with_suffix(".tmp")
            tmp.write_text(json.dumps(_TAINT))
            os.replace(tmp, _STATE)
        except Exception as e:
            logger.warning("privacy-route: taint not persisted: %s", e)


def _receipt(row: dict) -> None:
    try:
        _RECEIPTS.parent.mkdir(parents=True, exist_ok=True)
        with _RECEIPTS.open("a") as f:
            f.write(json.dumps({"ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"), **row}) + "\n")
    except Exception:
        pass


def on_pre_tool_call(tool_name: str = "", args: dict | None = None, **kw):
    try:
        if R is None:
            return {"action": "block", "message": f"privacy-route kernel unavailable ({_KERNEL_ERR}); tools are blocked."}
        tier = R.tool_tier(tool_name, args or {}, None)
        if tier is P.Tier.SECRET:
            _receipt({"event": "tool_blocked", "tool": tool_name, "tier": "secret", **{k: kw.get(k) for k in ("session_id", "task_id")}})
            return {"action": "block",
                    "message": "O18: this call would read T4 secret data (sealed store, credentials, keys or a "
                               "secret-printing command). Agents never read T4; ask the owner instead."}
        if tier.value > P.Tier.PERSONAL.value:
            _taint_add(kw, tier)
            _receipt({"event": "taint", "tool": tool_name, "tier": tier.name.lower(),
                      **{k: kw.get(k) for k in ("session_id", "task_id")}})
        return None
    except Exception as e:
        logger.warning("privacy-route pre_tool_call error: %s", e)
        return {"action": "block", "message": f"privacy-route could not classify this call ({type(e).__name__}); blocked."}


def on_llm_request(request: dict | None = None, **kw):
    req = dict(request or {})
    try:
        if R is None:
            raise RuntimeError(_KERNEL_ERR or "kernel unavailable")
        floor = R.TIER_NAMES.get(str(_CFG.get("floor", "personal")).lower(), P.Tier.UNKNOWN)
        taint = _taint_get(kw)
        content = R.content_tier(R.request_text(req))
        tier = P.tier_join(P.tier_join(floor, taint), content)
        task = P.Task.AGENTIC if req.get("tools") else P.Task.SIMPLE
        d = R.decide(tier, task, R.lanes_up(R.router_models()))
        req["model"] = d["model"]
        _receipt({"event": "route", **d, "floor": floor.name.lower(), "taint": taint.name.lower(),
                  "content": content.name.lower(), "requested_model": (request or {}).get("model"),
                  **{k: kw.get(k) for k in ("session_id", "task_id", "api_request_id")}})
        return {"request": req, "source": "privacy-route", "reason": f"O18 {d['decision']} tier={d['tier']} -> {d['model']}"}
    except Exception as e:
        req["model"] = _REFUSE
        _receipt({"event": "route_error", "error": f"{type(e).__name__}: {e}", "model": _REFUSE})
        return {"request": req, "source": "privacy-route", "reason": f"O18 refused (error: {type(e).__name__})"}


def register(ctx):
    ctx.register_hook("pre_tool_call", on_pre_tool_call)
    ctx.register_middleware("llm_request", on_llm_request)
