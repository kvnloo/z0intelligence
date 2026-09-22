#!/usr/bin/env python3
"""Token-cap sensitivity runner for the Phase 1B defect correction.

Reuses the *exact* Phase 1B producer path (``densify_measurements`` fixture
loader + ``compile_actions`` + the dialect renderer/parser through
``LocalSLMBackend.decide``).  The only changed variable is the completion cap.

Override mechanism
------------------
For every (model, cap) cell we build a *copy* of the dialect that Phase 1B
actually resolved for that model (``dialect_for(tool_parser, tool_call_template)``)
with ``max_tokens`` replaced by the cell cap::

    dataclasses.replace(resolved_dialect, max_tokens=cap)

and hand it to ``LocalSLMBackend`` explicitly.  ``LocalSLMBackend.decide`` then
sends ``min(request.max_tokens, dialect.max_tokens) == cap`` to the transport
because the request also carries ``max_tokens=cap``.  Nothing else about prompt,
tools payload, stop sequences, temperature or parser changes.

Wire verification
-----------------
``WireTransport`` subclasses the production ``OpenAICompatTransport`` and records
the literal ``max_tokens`` field of every ``/v1/chat/completions`` body it POSTs.
Each receipt stores that observed value as ``wire_max_tokens``; a receipt whose
wire value differs from its nominal cap is marked ``invalid_cell``.

Output is append-only ``raw.jsonl`` in this directory (one receipt per call) and
is resumable: existing ``(model, cap, state, rep, kind)`` keys are skipped.
"""

from __future__ import annotations

import argparse
import dataclasses
import hashlib
import importlib.util
import json
import os
import platform
import subprocess
import sys
import time
import urllib.error
import urllib.request
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

REPO = Path("/home/kvn/tmp/openjev")
OUT = REPO / "results" / "token-cap-20260922"
sys.path.insert(0, str(REPO / "src"))

from z0int.cognition.adapters.dialects import dialect_for  # noqa: E402
from z0int.cognition.adapters.local_slm import (  # noqa: E402
    LocalSLMBackend,
    ToolDecisionRequest,
)
from z0int.cognition.adapters.transport import (  # noqa: E402
    OpenAICompatTransport,
    ServerConfig,
)
from z0int.cognition.manifest import load_local_cognition  # noqa: E402
from z0int.cognition.registry import load_serving  # noqa: E402

_spec = importlib.util.spec_from_file_location(
    "densify_measurements", REPO / "scripts" / "densify_measurements.py"
)
densify = importlib.util.module_from_spec(_spec)
sys.modules["densify_measurements"] = densify
_spec.loader.exec_module(densify)

FIXTURE_PATH = REPO / "benchmarks/fixtures/local-cognition-v1/examples.jsonl"
FREEZE_PATH = REPO / "results/phase1b/p1b-20260921T1430Z/baseline/freeze.json"
FREEZE_FIXTURE_KEY = "benchmarks/fixtures/local-cognition-v1/examples.jsonl"
FROZEN_RUN = "p1b-20260921T1430Z"
SEED = 42

#: model -> [caps].  The first entry is the model's *native* (dialect-resolved)
#: cap in the Phase 1B corpus; extra entries are the overridden cells.
MATRIX: dict[str, list[int]] = {
    "nemotron_orchestrator_8b": [256, 512, 1024],
    "hammer2.1_3b": [256, 512, 1024],
    "hammer2.1_7b": [256, 512],
    "functiongemma_270m": [128, 256],
    "qwen3.5_4b": [256, 1024],
    "qwen3.5_9b": [1024, 2048],
}
NATIVE_CAP: dict[str, int] = {
    "nemotron_orchestrator_8b": 256,
    "hammer2.1_3b": 256,
    "hammer2.1_7b": 256,
    "functiongemma_270m": 128,
    "qwen3.5_4b": 1024,
    "qwen3.5_9b": 1024,
}
REPS = 3
BASE_URL = "http://127.0.0.1:11500"


def _rotate(seq, n):
    seq = list(seq)
    if not seq:
        return seq
    n %= len(seq)
    return seq[n:] + seq[:n]


# ---------------------------------------------------------------------------
# transport wrapper: record the literal wire max_tokens and the raw response
# ---------------------------------------------------------------------------


class WireTransport(OpenAICompatTransport):
    def __init__(self, config: ServerConfig):
        super().__init__(config)
        self.wire_max_tokens: int | None = None
        self.last_payload: dict[str, Any] | None = None
        self.last_outcome = None

    def _post(self, path: str, payload: Mapping[str, Any]) -> dict[str, Any]:
        self.last_payload = dict(payload)
        self.wire_max_tokens = payload.get("max_tokens")
        return super()._post(path, payload)

    def chat(self, *a, **k):
        out = super().chat(*a, **k)
        self.last_outcome = out
        return out


class TokenCounter:
    """Exact token counts from the resident llama-server's own ``/tokenize``.

    The supervisor owns at most one child ``llama-server`` on a port in
    ``[18100, 18160)``; we locate it by probing ``/props``.  Falls back to a
    chars/4 estimate (and says so in the receipt) if the endpoint is absent.
    """

    def __init__(self) -> None:
        self._port: int | None = None
        self.method = "llama.cpp /tokenize (exact)"

    def _find_port(self) -> int | None:
        if self._port is not None:
            return self._port
        for port in range(18100, 18160):
            try:
                with urllib.request.urlopen(f"http://127.0.0.1:{port}/props", timeout=1.0) as r:
                    if r.status == 200:
                        self._port = port
                        return port
            except Exception:  # noqa: BLE001
                continue
        return None

    def count(self, text: str) -> tuple[int, str]:
        if not text:
            return 0, self.method
        port = self._find_port()
        if port is not None:
            body = json.dumps({"content": text}).encode("utf-8")
            req = urllib.request.Request(
                f"http://127.0.0.1:{port}/tokenize",
                data=body,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            try:
                with urllib.request.urlopen(req, timeout=10) as r:
                    data = json.loads(r.read().decode("utf-8"))
                toks = data.get("tokens")
                if isinstance(toks, list):
                    return len(toks), self.method
            except Exception:  # noqa: BLE001
                self._port = None
        return max(1, round(len(text) / 4.0)), "chars/4 estimate (tokenize unavailable)"


def supervisor_status(base_url: str) -> dict[str, Any]:
    try:
        with urllib.request.urlopen(f"{base_url.rstrip('/')}/z0int/status", timeout=10) as r:
            return json.loads(r.read().decode("utf-8"))
    except Exception:  # noqa: BLE001
        return {}


# ---------------------------------------------------------------------------
# fixture / freeze
# ---------------------------------------------------------------------------


def load_freeze() -> dict[str, Any]:
    return json.loads(FREEZE_PATH.read_text(encoding="utf-8"))


def fixture_sha() -> str:
    return hashlib.sha256(FIXTURE_PATH.read_bytes()).hexdigest()


def git(*args: str) -> str:
    try:
        return subprocess.run(
            ["git", *args], cwd=REPO, capture_output=True, text=True, timeout=10
        ).stdout.strip()
    except Exception:  # noqa: BLE001
        return ""


# ---------------------------------------------------------------------------
# reasoning helpers
# ---------------------------------------------------------------------------

import re  # noqa: E402

_THINK_RE = re.compile(r"<think(?:ing)?>(.*?)</think(?:ing)?>", re.DOTALL | re.IGNORECASE)


def extract_channels(msg: Mapping[str, Any], content: str) -> tuple[str, str]:
    """Return (reasoning_text, reasoning_channel_kind)."""
    rc = msg.get("reasoning_content")
    if isinstance(rc, str) and rc.strip():
        return rc, "reasoning_content"
    m = _THINK_RE.search(content or "")
    if m and m.group(1).strip():
        return m.group(1), "think_block"
    return "", "none"


def mention_counts(text: str, ids: list[str]) -> dict[str, int]:
    out: dict[str, int] = {}
    lowered = (text or "").lower()
    counts = []
    for i in ids:
        c = lowered.count(i.lower())
        out[i] = c
        if c:
            counts.append((lowered.find(i.lower()), i))
    return out


# ---------------------------------------------------------------------------
# runner
# ---------------------------------------------------------------------------


class Runner:
    def __init__(
        self,
        states_filter: list[str] | None = None,
        models_filter: list[str] | None = None,
        out_path: Path | None = None,
    ):
        self.fixtures = densify.load_fixtures(FIXTURE_PATH)
        if states_filter:
            want = set(states_filter)
            self.fixtures = [f for f in self.fixtures if f.fixture_id in want]
        self.manifest = load_local_cognition()
        self.serving = load_serving()
        self.token_counter = TokenCounter()
        self.counter = TokenCounter()
        self.out_path = out_path or (OUT / "raw.jsonl")
        self.matrix = {
            m: caps for m, caps in MATRIX.items() if not models_filter or m in models_filter
        }
        self._transport_cache: dict[tuple[str, int], WireTransport] = {}
        self._backend_cache: dict[tuple[str, int], LocalSLMBackend] = {}
        self._seen = self._load_seen()

    def _load_seen(self) -> set[tuple]:
        seen: set[tuple] = set()
        if not self.out_path.is_file():
            return seen
        for line in self.out_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                r = json.loads(line)
            except json.JSONDecodeError:
                continue
            seen.add((r.get("model"), r.get("requested_max_tokens"), r.get("state_id"),
                      r.get("repetition"), r.get("kind")))
        return seen

    def _write(self, row: dict[str, Any]) -> None:
        with self.out_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, sort_keys=True, default=str) + "\n")

    def backend(self, model_id: str, cap: int) -> LocalSLMBackend:
        key = (model_id, cap)
        if key not in self._backend_cache:
            capability = self.manifest.get(model_id)
            ep = self.serving[model_id]
            resolved = dialect_for(capability.tool_parser, capability.tool_call_template)
            overridden = dataclasses.replace(resolved, max_tokens=cap)
            cfg = ServerConfig(
                base_url=ep.base_url,
                model=ep.served_model,
                api_key=ep.api_key,
                runtime=ep.runtime,
                quantization=ep.quantization or capability.tested_quantization,
                revision=capability.revision,
            )
            tr = WireTransport(cfg)
            self._transport_cache[key] = tr
            self._backend_cache[key] = LocalSLMBackend(
                backend_id=model_id,
                config=cfg,
                dialect=overridden,
                capability=capability,
                transport=tr,
            )
        return self._backend_cache[key]

    def _legal(self, fx) -> Any:
        return densify.compile_actions(
            graph=fx.graph,
            granted_capabilities=fx.granted_capabilities,
            authority=fx.authority,
            budget_units=fx.budget_units,
            facts=fx.facts,
            satisfied=fx.satisfied,
        )

    def measure(self, fx, model_id: str, cap: int, rep: int, kind: str) -> dict[str, Any]:
        capability = self.manifest.get(model_id)
        ep = self.serving[model_id]
        resolved = dialect_for(capability.tool_parser, capability.tool_call_template)
        backend = self.backend(model_id, cap)
        tr = self._transport_cache[(model_id, cap)]
        legal = self._legal(fx)

        status_before = supervisor_status(BASE_URL)
        resident_before = status_before.get("resident")

        t0 = time.perf_counter()
        error = None
        decision = None
        try:
            decision = backend.decide(
                ToolDecisionRequest(state=fx.state, legal=legal, max_tokens=cap)
            )
        except Exception as exc:  # noqa: BLE001
            error = f"{type(exc).__name__}: {exc}"
        latency_ms = (time.perf_counter() - t0) * 1000.0

        raw = getattr(tr.last_outcome, "raw", None) or {}
        choice = (raw.get("choices") or [{}])[0]
        msg = choice.get("message") or {}
        content = msg.get("content") or ""
        tool_calls = msg.get("tool_calls") or []
        native_tool_call = bool(tool_calls)
        finish_reason = choice.get("finish_reason")
        usage = getattr(tr.last_outcome, "usage", None) or {}
        reasoning_text, reasoning_kind = extract_channels(msg, content)

        completion_tokens = getattr(decision, "completion_tokens", None)
        if completion_tokens is None:
            completion_tokens = usage.get("completion_tokens")
        prompt_tokens = getattr(decision, "prompt_tokens", None)
        if prompt_tokens is None:
            prompt_tokens = usage.get("prompt_tokens")
        wire_max = tr.wire_max_tokens

        # --- parsing facts from the production parser's own diagnostics -----
        diag = dict(getattr(decision, "diagnostics", {}) or {}) if decision else {}
        parse_source = diag.get("parse_source")
        selected = getattr(decision, "selected_action", None) if decision else None
        parse_error = getattr(decision, "parse_error", None) if decision else None
        abstained = bool(getattr(decision, "abstained", True)) if decision else True
        invalid_call = bool(getattr(decision, "invalid_call", False)) if decision else False
        tool_call_parsed = selected is not None
        fallback_parser_used = parse_source is not None and parse_source != "tool_call"

        # "emitted a call" in any syntax the dialect knows
        emitted = native_tool_call
        if not emitted and content:
            for pat in list(resolved.call_patterns) + list(resolved.json_blob_patterns):
                if pat.search(content):
                    emitted = True
                    break
            if not emitted:
                from z0int.cognition.adapters.dialects import extract_json_object

                if extract_json_object(content) is not None:
                    emitted = True

        # --- correctness (identical to Phase 1B ObservationBuilder) ---------
        gold = fx.gold_action
        if gold == "abstain":
            correct = bool(selected == "abstain" or (abstained and selected is None))
        elif gold is not None:
            correct = selected == gold
        else:
            correct = bool(abstained)

        # --- gold mention ----------------------------------------------------
        legal_ids = list(legal.ids())
        reasoning_lower = (reasoning_text or "").lower()
        content_lower = (content or "").lower()
        gold_l = (gold or "").lower()
        gold_in_reasoning = bool(gold and gold_l in reasoning_lower)
        gold_in_content = bool(gold and gold_l in content_lower)
        # The column requested by the brief: raw reasoning text if the model has
        # a reasoning channel, otherwise the raw content it did emit.
        gold_mentioned_in_reasoning = gold_in_reasoning if reasoning_kind != "none" else gold_in_content
        mention_channel = (
            "reasoning" if reasoning_kind != "none" else ("content_fallback" if content else "none")
        )

        reasoning_tokens, tok_method = (0, "n/a")
        content_tokens, _ = (0, tok_method)
        if reasoning_text:
            reasoning_tokens, tok_method = self.counter.count(reasoning_text)
        if content:
            content_tokens, _ = self.counter.count(content)

        # --- truncation ------------------------------------------------------
        hit_cap = bool(
            completion_tokens is not None
            and wire_max
            and completion_tokens >= int(wire_max)
        )
        truncated = bool(finish_reason == "length" or hit_cap)

        # --- residency -------------------------------------------------------
        sup = diag.get("supervisor") or {}
        cold = bool(sup.get("cold")) if sup else None
        if cold:
            cold_or_warm = "cold_load" if resident_before is None else "model_swap"
        elif resident_before == model_id:
            cold_or_warm = "warm_invocation"
        else:
            cold_or_warm = "unknown"
        load_ms = float(sup.get("load_ms") or 0.0)

        cell = f"{model_id}@{cap}"
        row: dict[str, Any] = {
            "schema": "z0int.tokencap.call.v1",
            "run_id": "token-cap-20260922",
            "call_id": uuid.uuid4().hex[:16],
            "observed_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "kind": kind,  # measured | warmup
            # identity
            "state_id": fx.fixture_id,
            "state_family": fx.family,
            "gold_action": gold,
            "expect_abstain": bool(fx.expect_abstain),
            "legal_actions": legal_ids,
            "arm": f"compiler+{model_id}",
            "condition": f"cap={cap}",
            "cell": cell,
            "repetition": rep,
            "model": model_id,
            "model_revision": capability.revision,
            "quant": ep.quantization or capability.tested_quantization,
            # caps
            "requested_max_tokens": cap,
            "dialect_max_tokens": resolved.max_tokens,
            "override_dialect_max_tokens": cap,
            "effective_max_tokens": wire_max,
            "wire_max_tokens": wire_max,
            "native_cap": NATIVE_CAP[model_id],
            "dialect_name": resolved.name,
            "tool_parser": capability.tool_parser,
            "tool_call_template": capability.tool_call_template,
            "invalid_cell": bool(wire_max != cap),
            # response
            "finish_reason": finish_reason,
            "truncated": truncated,
            "hit_cap": hit_cap,
            "reasoning_present": bool(reasoning_text.strip()),
            "reasoning_channel_kind": reasoning_kind,
            "reasoning_tokens": reasoning_tokens,
            "reasoning_token_method": tok_method,
            "reasoning_chars": len(reasoning_text or ""),
            "content_chars": len(content or ""),
            "content_tokens": content_tokens,
            "tool_call_emitted": emitted,
            "native_tool_call": native_tool_call,
            "tool_call_parsed": tool_call_parsed,
            "parse_source": parse_source,
            "fallback_parser_used": fallback_parser_used,
            "gold_mentioned_in_reasoning": gold_mentioned_in_reasoning,
            "gold_mentioned_in_content": gold_in_content,
            "gold_mention_channel": mention_channel,
            "selected_action": selected,
            "abstained": abstained,
            "invalid_call": invalid_call,
            "parse_error": parse_error,
            "correct": bool(correct),
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "latency_ms": latency_ms,
            "cold_or_warm": cold_or_warm,
            "resident_before": resident_before,
            "load_ms": load_ms,
            "ttft_ms": getattr(decision, "ttft_ms", None) if decision else None,
            "decode_tok_s": getattr(decision, "decode_tok_s", None) if decision else None,
            "error": error,
            "seed": SEED,
            "temperature": resolved.temperature,
            # raw text (kept so gold-in-reasoning is auditable)
            "content": content,
            "reasoning_text": reasoning_text,
            "tool_calls_raw": tool_calls,
        }
        return row

    def schedule(self) -> list[dict[str, Any]]:
        states = self.fixtures
        models = list(self.matrix)
        plan: list[dict[str, Any]] = []
        for rep in range(REPS):
            for model in _rotate(models, rep):
                caps = self.matrix[model]
                state_order = _rotate(states, rep)
                # one warmup at block start so the first measured call is warm
                plan.append(
                    {"state": state_order[0], "model": model, "cap": caps[0], "rep": rep,
                     "kind": "warmup"}
                )
                for si, fx in enumerate(state_order):
                    for cap in _rotate(caps, rep + si):
                        plan.append({"state": fx, "model": model, "cap": cap, "rep": rep,
                                     "kind": "measured"})
        return plan

    def run(self, limit: int | None = None) -> int:
        plan = self.schedule()
        written = 0
        print(f"# schedule: {len(plan)} calls ({len(plan)-sum(1 for p in plan if p['kind']=='warmup')} measured)", flush=True)
        for item in plan:
            fx = item["state"]
            key = (item["model"], item["cap"], fx.fixture_id, item["rep"], item["kind"])
            if key in self._seen:
                continue
            if limit is not None and written >= limit:
                print(f"# limit {limit} reached", flush=True)
                break
            row = self.measure(fx, item["model"], item["cap"], item["rep"], item["kind"])
            self._write(row)
            self._seen.add(key)
            written += 1
            print(
                f"# [{written}] {row['kind']:8s} {row['cell']:38s} {row['state_id']:34s} rep={row['repetition']} "
                f"{row['cold_or_warm']:16s} wire={row['wire_max_tokens']} "
                f"ct={row['completion_tokens']} fin={row['finish_reason']} "
                f"trunc={int(row['truncated'])} corr={int(row['correct'])} "
                f"{row['latency_ms']:.0f}ms",
                flush=True,
            )
        return written


def write_run_metadata(runner: Runner, started_at: str, argv: list[str]) -> None:
    freeze = load_freeze()
    models_meta = {m["model_id"]: m for m in freeze.get("models", [])}
    serving = {k: v.to_dict() for k, v in runner.serving.items()}
    meta = {
        "schema": "z0int.tokencap.run_metadata.v1",
        "run_id": "token-cap-20260922",
        "created_at": started_at,
        "argv": argv,
        "git": {
            "branch": git("rev-parse", "--abbrev-ref", "HEAD"),
            "head": git("rev-parse", "HEAD"),
            "dirty": bool(git("status", "--porcelain")),
        },
        "python": sys.version,
        "platform": platform.platform(),
        "frozen_run_reference": FROZEN_RUN,
        "frozen_run_mutated": False,
        "fixture": {
            "path": str(FIXTURE_PATH),
            "sha256": fixture_sha(),
            "frozen_sha256": next(
                (f.get("sha256") for f in freeze.get("fixtures", []) if f.get("path") == FREEZE_FIXTURE_KEY),
                None,
            ),
            "n_states": len(runner.fixtures),
        },
        "base_url": BASE_URL,
        "supervisor_status_pre_run": supervisor_status(BASE_URL),
        "override_mechanism": {
            "how": "dataclasses.replace(resolved_dialect, max_tokens=cap) passed to "
                   "LocalSLMBackend(dialect=...), with ToolDecisionRequest(max_tokens=cap) so "
                   "min(request, dialect) == cap",
            "why_needed": "dialect_for(tool_parser, tool_call_template) matches tool_parser first; "
                          "nemotron/hammer rows resolve to HERMES_DIALECT (256) and functiongemma to "
                          "FUNCTIONGEMMA_DIALECT (128), so the requested 1024 never reached the wire",
            "wire_verification": "WireTransport._post records the literal payload['max_tokens']; stored "
                                 "per receipt as wire_max_tokens / effective_max_tokens; invalid_cell "
                                 "flags any mismatch",
        },
        "matrix": runner.matrix,
        "native_caps_from_phase1b": NATIVE_CAP,
        "settings": {
            "seed": SEED,
            "temperature": "dialect default (0.0 for all six dialects)",
            "context": 4096,
            "repetitions": REPS,
            "arm": "compiler+<model> (compiler-first, identical to Phase 1B core arms)",
            "top_p": "server default (not sent by the adapter)",
        },
        "schedule_design": {
            "order": "rep-major; models rotated by rep; within a model block, states rotated by rep "
                     "and the caps of each state rotated by (rep+state_index); one warmup call per "
                     "model block (recorded with kind=warmup, excluded from analysis)",
            "purpose": "interleave + time-rotate caps so a warm/cold or time-of-day artifact cannot "
                       "masquerade as a cap effect",
        },
        "reasoning_tokens_method": "exact count of reasoning_text under the resident llama-server's own "
                                   "/tokenize endpoint (child port 18100-18159); falls back to chars/4 "
                                   "and records reasoning_token_method per call",
        "gold_mentioned_in_reasoning": "gold action id found in raw reasoning text (reasoning_content or "
                                       "<think> block); for models with no reasoning channel it falls back "
                                       "to raw content; gold_mention_channel records which",
        "models": {
            mid: {
                "revision": runner.manifest.get(mid).revision,
                "tool_parser": runner.manifest.get(mid).tool_parser,
                "tool_call_template": runner.manifest.get(mid).tool_call_template,
                "resolved_dialect": dialect_for(
                    runner.manifest.get(mid).tool_parser,
                    runner.manifest.get(mid).tool_call_template,
                ).name,
                "native_dialect_max_tokens": dialect_for(
                    runner.manifest.get(mid).tool_parser,
                    runner.manifest.get(mid).tool_call_template,
                ).max_tokens,
                "gguf_sha256": (models_meta.get(mid, {}).get("gguf") or {}).get("sha256"),
                "quant": (models_meta.get(mid, {}).get("gguf") or {}) and models_meta.get(mid, {}).get("quant"),
                "hf": models_meta.get(mid, {}).get("hf"),
                "hf_revision": models_meta.get(mid, {}).get("hf_revision"),
            }
            for mid in runner.matrix
        },
        "serving": serving,
    }
    (OUT / "run_metadata.json").write_text(
        json.dumps(meta, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8"
    )


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--states", action="append", default=[])
    ap.add_argument("--models", action="append", default=[])
    ap.add_argument("--out", default=None)
    args = ap.parse_args(argv)

    started_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    out_path = Path(args.out) if args.out else None
    runner = Runner(
        states_filter=args.states or None,
        models_filter=args.models or None,
        out_path=out_path,
    )
    # Only a full (unfiltered) invocation owns run_metadata.json.
    if not args.states and not args.models and out_path is None:
        write_run_metadata(runner, started_at, sys.argv)
    n = runner.run(limit=args.limit)
    print(f"# wrote {n} new calls -> {runner.out_path}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
