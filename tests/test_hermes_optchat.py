"""Hermes OptChat adapter: optional, model-invisible in shadow, isolated from the OMP log root."""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

import pytest

from test_hermes_capture import Ctx, load_plugin

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"


class ToolCtx(Ctx):
    def __init__(self, settings=None):
        super().__init__(settings)
        self.tools = {}

    def register_tool(self, **entry):
        self.tools[entry["name"]] = entry


@pytest.fixture
def env(monkeypatch, tmp_path):
    z0 = tmp_path / "z0"
    hermes = tmp_path / "hermes"
    hermes.mkdir()
    monkeypatch.setenv("Z0INT_HOME", str(z0))
    monkeypatch.setenv("HERMES_HOME", str(hermes))
    monkeypatch.setenv(
        "PYTHONPATH",
        os.pathsep.join(p for p in (str(SRC), os.environ.get("PYTHONPATH", "")) if p),
    )
    monkeypatch.delenv("Z0INT_OPTCHAT_ROOT", raising=False)
    return z0


def start(monkeypatch, mode):
    mod = load_plugin()
    monkeypatch.setattr(mod, "_profile_config", lambda: {}, raising=False)
    monkeypatch.setattr(mod, "_hermes_workspace_root", lambda task_id: "/w/repo", raising=False)
    ctx = ToolCtx({"optchat_mode": mode, "z0int_python": sys.executable})
    mod.register(ctx)
    return mod, ctx


def pre(ctx, turn, text, platform="cli"):
    results = ctx.call(
        "pre_llm_call",
        session_id="s1",
        turn_id=turn,
        task_id="task-1",
        user_message=text,
        conversation_history=[],
        model="stub-model",
        platform=platform,
    )
    return "\n".join(r["context"] for r in results if isinstance(r, dict) and r.get("context"))


def post(ctx, turn, text="ok"):
    ctx.call(
        "post_llm_call",
        session_id="s1",
        turn_id=turn,
        assistant_response=text,
        conversation_history=[],
        model="stub-model",
        platform="cli",
    )


def close(ctx):
    for callback in ctx.unloads:
        callback()


def chat_text(z0):
    root = z0 / "optchat" / "hermes" / "chat"
    return "\n".join(p.read_text(errors="replace") for p in root.rglob("*.jsonl")) if root.exists() else ""


def test_on_injects_prior_view_registers_tools_and_uses_a_hermes_local_root(env, monkeypatch):
    _, ctx = start(monkeypatch, "on")
    assert pre(ctx, "t1", "keep the Hermes OptChat path local") == ""
    post(ctx, "t1", "done")
    view = pre(ctx, "t2", "what did I say?")
    assert "OptChat view of earlier Hermes messages" in view
    assert "user: keep the Hermes OptChat path local" in view
    assert "talk: done" in view
    assert set(ctx.tools) == {"optchat_zoom", "optchat_date"}
    opened = ctx.tools["optchat_zoom"]["handler"]({"id": 0, "n": 1})
    assert "keep the Hermes OptChat path local" in opened
    close(ctx)
    assert (env / "optchat" / "hermes" / "chat" / "main").is_dir()
    assert not (env / "optchat" / "chat").exists()


def test_tool_and_result_appends_do_not_block_the_hot_hook(env, monkeypatch):
    _, ctx = start(monkeypatch, "on")
    pre(ctx, "t1", "run the check")
    t0 = time.perf_counter()
    ctx.call(
        "post_tool_call",
        session_id="s1",
        turn_id="t1",
        tool_name="terminal",
        args={"command": "pytest -q"},
        result={"content": [{"type": "text", "text": "3 passed"}]},
        status="ok",
    )
    assert time.perf_counter() - t0 < 0.1
    post(ctx, "t1", "green")
    view = pre(ctx, "t2", "status?")
    assert "tool: terminal" in view
    assert "echo: 3 passed" in view
    assert "talk: green" in view
    close(ctx)


def test_shadow_records_but_is_not_model_visible(env, monkeypatch):
    _, ctx = start(monkeypatch, "shadow")
    assert pre(ctx, "t1", "shadow-only message") == ""
    post(ctx, "t1", "shadow reply")
    assert pre(ctx, "t2", "next") == ""
    assert ctx.tools == {}
    close(ctx)
    text = chat_text(env)
    assert "shadow-only message" in text and "shadow reply" in text


def test_off_keeps_the_plugin_inert_for_optchat(env, monkeypatch):
    _, ctx = start(monkeypatch, "off")
    assert ctx.tools == {}
    assert ctx.hooks == {}
    assert ctx.unloads == []
    assert not (env / "optchat").exists()


def test_non_user_turn_never_enters_the_optchat_log(env, monkeypatch):
    _, ctx = start(monkeypatch, "on")
    assert pre(ctx, "cron-1", "scheduled secret", platform="cron") == ""
    ctx.call(
        "post_tool_call",
        session_id="s1",
        turn_id="cron-1",
        tool_name="terminal",
        args={"command": "echo no"},
        result="no",
        status="ok",
    )
    post(ctx, "cron-1", "not logged")
    close(ctx)
    assert "scheduled secret" not in chat_text(env)
    assert "not logged" not in chat_text(env)


def test_bad_mode_fails_open_without_hooks_or_tools(env, monkeypatch):
    _, ctx = start(monkeypatch, "wat")
    assert ctx.tools == {}
    assert ctx.hooks == {}
    assert ctx.unloads == []
