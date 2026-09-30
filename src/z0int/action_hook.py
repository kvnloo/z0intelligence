"""Claude Code PreToolUse hook: action-level authority check in SHADOW (z0int#55).

Reads the hook JSON on stdin, parses the tool call into concrete effects, folds the session's
transcript (incrementally, cached) into grants/prohibitions, adds the authored AODL contract's exact
grants/prohibitions for privileged calls (``aodl_grants``: ``~/.z0int/state/claude-code/intent.aodl.json``
and ``<repo>/.aodl/intent.json``; ``Z0INT_AODL_CONTRACT=0`` disables), runs ``authority_check`` and appends
``{session, prompt_id, tool, effects, decision}`` to ``~/.z0int/state/claude-code/actions.jsonl``.

Shadow means: it prints nothing and always exits 0, so Claude Code's own permission flow decides
exactly as before. Fail-open: any error is swallowed. Kept free of heavy imports (no pathlib, no
z0int.paths) so a call costs one interpreter start plus a few ms.
"""

from __future__ import annotations

import json
import os
import sys
import time

HARNESS = "claude-code"
_KEEP = ('"type":"user"', '"type": "user"')


def _home() -> str:
    return os.path.abspath(os.path.expanduser(os.environ.get("Z0INT_HOME") or "~/.z0int"))


def state_dir() -> str:
    return os.path.join(_home(), "state", HARNESS)


def actions_path() -> str:
    return os.path.join(state_dir(), "actions.jsonl")


def _transcript(hook: dict) -> str | None:
    p = hook.get("transcript_path")
    if not isinstance(p, str) or not p:
        return None
    if "/subagents/" in p:  # subagent calls carry the parent's user intent, never the orchestrator's prompt
        p = p.split("/subagents/")[0] + ".jsonl"
    return p


def _wanted(line: str) -> bool:
    if '"type":"assistant"' in line or '"type": "assistant"' in line:
        return '"text"' in line or '"tool_use"' in line
    if not any(k in line for k in _KEEP):
        return False
    if '"tool_result"' in line:
        return "answered your question" in line or "want to proceed" in line or "rejected" in line
    return True


def _cache_path(hook: dict) -> str:
    session = str(hook.get("session_id") or "unknown").replace("/", "_")
    return os.path.join(state_dir(), "action_sessions", session + ".json")


def save_session(hook: dict, sa) -> None:
    cache = _cache_path(hook)
    os.makedirs(os.path.dirname(cache), exist_ok=True)
    tmp = f"{cache}.{os.getpid()}.tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump({"path": sa._path, "offset": sa._offset, "state": sa.state()}, fh, default=str)
    os.replace(tmp, cache)


def load_session(hook: dict, ctx):
    """The session's SessionAuthority, advanced over transcript rows appended since the last call."""
    from .action_authority import SessionAuthority
    tpath = _transcript(hook)
    cached: dict = {}
    try:
        with open(_cache_path(hook), encoding="utf-8") as fh:
            cached = json.load(fh)
    except (OSError, ValueError):
        cached = {}
    if cached.get("path") != tpath:
        cached = {}
    sa = SessionAuthority(cached.get("state"))
    offset = int(cached.get("offset") or 0)
    sa._path, sa._offset = tpath, offset
    if not tpath:
        return sa
    try:
        size = os.path.getsize(tpath)
        if size < offset:  # rewritten transcript: start over
            sa, offset = SessionAuthority(), 0
            sa._path = tpath
        if size > offset:
            with open(tpath, "rb") as fh:
                fh.seek(offset)
                chunk = fh.read(size - offset)
            end = chunk.rfind(b"\n")
            if end >= 0:
                for raw in chunk[:end].split(b"\n"):
                    line = raw.decode("utf-8", "replace")
                    if not _wanted(line):
                        continue
                    try:
                        row = json.loads(line)
                    except ValueError:
                        continue
                    sa.feed(row, ctx)
                sa._offset = offset + end + 1
                save_session(hook, sa)
    except OSError:
        pass
    return sa


def _slim(e: dict) -> dict:
    return {"class": e.get("class"), "kind": e.get("kind"), "target": e.get("target"), "reason": e.get("reason")}


def on_pretool(hook: dict, *, log: bool = True) -> dict | None:
    t0 = time.perf_counter()
    from .action_effects import Ctx
    cwd = hook.get("cwd") or os.getcwd()
    ctx = Ctx.for_cwd(cwd)
    tool = str(hook.get("tool_name") or "")
    sa = load_session(hook, ctx)
    mode = hook.get("permission_mode") or sa.permission_mode
    contract = None
    if os.environ.get("Z0INT_AODL_CONTRACT", "1") != "0":
        from .aodl_grants import load_contracts
        require_pin = os.environ.get("Z0INT_AODL_REQUIRE_PIN", "0") == "1"

        def contract(c):
            return load_contracts(c, require_pin=require_pin)
    out = sa.check(tool, hook.get("tool_input") or {}, ctx, permission_mode=mode, contract=contract)
    if sa.pending == [] and out["effects"]["privileged"]:
        save_session(hook, sa)  # grants were materialised: keep them so the next call need not redo it
    parsed, decision = out["effects"], out["decision"]
    record = {
        "schema": "z0int.claude_code.action_record.v0", "ts": time.time(), "shadow": True,
        "session": hook.get("session_id"), "prompt_id": hook.get("prompt_id") or sa.prompt_id,
        "tool_use_id": hook.get("tool_use_id"), "agent_id": hook.get("agent_id"), "tool": tool, "cwd": cwd,
        "permission_mode": mode, "effect_class": parsed["effect_class"], "effects": [_slim(e) for e in parsed["effects"]],
        "decision": decision["decision"], "reason": decision["reason"],
        "privileged": [r for r in decision["per_effect"] if r["class"] == "privileged"],
        "grants_n": len(sa.grants), "prohibitions_n": len(sa.prohibitions),
        # authored contracts consulted for this call (only when an effect is privileged)
        "aodl": out.get("aodl"),
        "latency_ms": round((time.perf_counter() - t0) * 1000, 2),
    }
    if log:
        path = actions_path()
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")
    return record


def main() -> None:
    try:
        hook = json.load(sys.stdin)
        if os.environ.get("Z0INT_ACTION_AUTHORITY", "1") != "0":
            on_pretool(hook)
    except Exception:
        pass  # fail-open: the native permission flow is untouched
    # SHADOW: no stdout, exit 0 -> Claude Code decides exactly as it would without this hook.
    sys.exit(0)


if __name__ == "__main__":
    main()
