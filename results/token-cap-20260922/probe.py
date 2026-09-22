#!/usr/bin/env python3
"""Throwaway probe: inspect one real wire response + timing for each model.

Not part of the deliverable. Confirms (a) the override mechanism reaches the
wire, (b) what reasoning/usage/finish_reason fields llama.cpp actually returns.
"""
from __future__ import annotations

import dataclasses
import json
import sys
import time
from pathlib import Path

REPO = Path("/home/kvn/tmp/openjev")
sys.path.insert(0, str(REPO / "src"))

from z0int.cognition.adapters.dialects import dialect_for  # noqa: E402
from z0int.cognition.adapters.local_slm import (  # noqa: E402
    LocalSLMBackend,
    ToolDecisionRequest,
)
from z0int.cognition.adapters.transport import (  # noqa: E402
    ChatOutcome,
    OpenAICompatTransport,
    ServerConfig,
)
from z0int.cognition.manifest import load_local_cognition  # noqa: E402
from z0int.cognition.registry import load_serving  # noqa: E402

import importlib.util  # noqa: E402

spec = importlib.util.spec_from_file_location("densify", REPO / "scripts" / "densify_measurements.py")
densify = importlib.util.module_from_spec(spec)
sys.modules["densify"] = densify
spec.loader.exec_module(densify)


class WireTransport(OpenAICompatTransport):
    def __init__(self, config):
        super().__init__(config)
        self.last_payload = None
        self.last_outcome = None

    def _post(self, path, payload):
        self.last_payload = dict(payload)
        return super()._post(path, payload)

    def chat(self, *a, **k):
        out = super().chat(*a, **k)
        self.last_outcome = out
        return out


def main() -> int:
    fixtures = densify.load_fixtures(REPO / "benchmarks/fixtures/local-cognition-v1/examples.jsonl")
    fx = fixtures[0]
    print("fixture:", fx.fixture_id, "gold:", fx.gold_action)
    manifest = load_local_cognition()
    serving = load_serving()
    targets = sys.argv[1:] or ["nemotron_orchestrator_8b"]
    cap = 1024
    for mid in targets:
        capability = manifest.get(mid)
        ep = serving[mid]
        d = dialect_for(capability.tool_parser, capability.tool_call_template)
        over = dataclasses.replace(d, max_tokens=cap)
        cfg = ServerConfig(
            base_url=ep.base_url, model=ep.served_model, api_key=ep.api_key,
            runtime=ep.runtime, quantization=ep.quantization or capability.tested_quantization,
            revision=capability.revision,
        )
        tr = WireTransport(cfg)
        backend = LocalSLMBackend(backend_id=mid, config=cfg, dialect=over,
                                 capability=capability, transport=tr)
        legal = densify.compile_actions(
            graph=fx.graph, granted_capabilities=fx.granted_capabilities,
            authority=fx.authority, budget_units=fx.budget_units,
            facts=fx.facts, satisfied=fx.satisfied,
        )
        t0 = time.perf_counter()
        dec = backend.decide(ToolDecisionRequest(state=fx.state, legal=legal, max_tokens=cap))
        dt = time.perf_counter() - t0
        raw = tr.last_outcome.raw if tr.last_outcome else {}
        choice = (raw.get("choices") or [{}])[0]
        msg = choice.get("message") or {}
        print("=" * 70)
        print(f"{mid}: wire_max_tokens={tr.last_payload.get('max_tokens')} "
              f"requested={cap} native_dialect={d.name}/{d.max_tokens} wall={dt:.1f}s")
        print("finish_reason:", choice.get("finish_reason"))
        print("usage:", json.dumps(tr.last_outcome.usage))
        print("timings:", json.dumps(tr.last_outcome.timings))
        print("content len:", len(msg.get("content") or ""), "repr head:",
              repr((msg.get("content") or "")[:200]))
        rc = msg.get("reasoning_content")
        print("reasoning_content present:", rc is not None, "len:", len(rc) if rc else 0)
        if rc:
            print("reasoning head:", repr(rc[:300]))
        print("tool_calls:", json.dumps(msg.get("tool_calls"))[:400])
        print("selected:", dec.selected_action, "abstained:", dec.abstained,
              "correct:", (dec.selected_action == fx.gold_action),
              "completion_tokens:", dec.completion_tokens,
              "prompt_tokens:", dec.prompt_tokens)
        print("message keys:", sorted(msg.keys()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
