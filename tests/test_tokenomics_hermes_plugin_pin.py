"""Pin contract: agent-tokenomics must ship the Hermes usage plugin (#17).

z0intelligence depends on tokenomics for harness usage receipts. The Hermes
plugin landed in tokenomics @ 3fa33ee8 (pin target includes 65e8f2dd). This
test fails closed if the pin regresses behind that surface.
"""

from __future__ import annotations

import importlib
import importlib.metadata


def test_tokenomics_exposes_hermes_plugin_module():
    mod = importlib.import_module("tokenomics.hermes_plugin")
    assert callable(getattr(mod, "register"))
    assert callable(getattr(mod, "on_post_llm_call"))


def test_agent_tokenomics_declares_hermes_entry_point():
    eps = importlib.metadata.entry_points()
    group = (
        list(eps.select(group="hermes_agent.plugins"))
        if hasattr(eps, "select")
        else list(eps.get("hermes_agent.plugins", []))
    )
    assert any(
        ep.name == "tokenomics" and ep.value == "tokenomics.hermes_plugin:register"
        for ep in group
    ), f"missing hermes_agent.plugins entry point; found={group!r}"


def test_hermes_plugin_register_hooks_without_content():
    mod = importlib.import_module("tokenomics.hermes_plugin")

    class FakeContext:
        def __init__(self):
            self.hooks = {}

        def register_hook(self, name, fn):
            self.hooks[name] = fn

    ctx = FakeContext()
    mod.register(ctx)
    assert ctx.hooks["post_api_request"] is mod.on_post_llm_call
    assert ctx.hooks["post_llm_call"] is mod.on_post_llm_call
