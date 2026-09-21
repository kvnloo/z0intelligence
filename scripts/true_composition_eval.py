#!/usr/bin/env python3
"""Phase 1B section D -- TRUE composition: each stage's output is the next stage's input.

Phase 1's "composition" matrix (A-F) was a *cascade*: every tier received the same
state and the same legal action set, and the tiers only differed by who answered
first.  That cannot show whether an earlier component genuinely helped, because
nothing it produced ever reached a later component.

This harness makes the data flow real and observable:

    compiler  ->  {legal set, eliminations, deterministic solution}
                     |
                     v   legal set is *constrained* to what the compiler admitted
                  JEV / bounded scorer
                     |   -> {shortlist, distribution, confidence, margin, entropy}
                     v   downstream legal set is *pruned* to the shortlist
              specialist (Hammer2.1-3B)
                     |   -> {candidate action, rationale text}
                     v   rationale is injected into the downstream prompt
              generalist (Qwen3.5-4B / 9B)
                     |   -> final action

Every stage emits a receipt that names exactly what it contributed and what the
next stage's input became.  Three things keep this from degenerating into
"independent model calls with a copied label":

1. the downstream prompt is built *from* the upstream artifact and records the
   artifact's digest, so an unconsumed artifact is detectable;
2. the final stage is also run **ablated** (same state, same legal set, no
   upstream artifact).  The difference between the two is the only honest
   measure of incremental value -- anything else is overhead;
3. agreement between the downstream answer and the upstream suggestion is
   recorded separately from correctness, so "it ignored the hint and got lucky"
   is visible.

    python scripts/true_composition_eval.py --chains basic --reps 3 --out <dir>
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from z0int.cognition.actions import (  # noqa: E402
    ActionCandidate,
    ActionGraph,
    Rule,
    compile_actions,
)
from z0int.cognition.adapters.transport import (  # noqa: E402
    OpenAICompatTransport,
    ServerConfig,
    TransportError,
)
from z0int.cognition.registry import LocalModelRegistry, load_serving  # noqa: E402

SCHEMA = "z0int.true_composition_eval.v1"
DEFAULT_FIXTURES = REPO / "benchmarks" / "fixtures" / "local-cognition-v1" / "examples.jsonl"
STAGE_SCHEMA = "z0int.true_composition_stage.v1"
ALL_RISKS = ("read", "write", "destructive", "publish", "credential", "payment")

COMPILER = "compiler"
JEV = "jev"


@dataclass(frozen=True)
class Component:
    name: str
    kind: str  # compiler | scorer | specialist | generalist
    model: str | None = None
    role: str = ""


def stage_catalog() -> dict[str, Component]:
    return {
        COMPILER: Component(COMPILER, "compiler"),
        JEV: Component(JEV, "scorer", model="nanojev_06b"),
        "hammer2.1_3b": Component("hammer2.1_3b", "specialist", model="hammer2.1_3b"),
        "qwen3.5_4b": Component("qwen3.5_4b", "generalist", model="qwen3.5_4b"),
        "qwen3.5_9b": Component("qwen3.5_9b", "generalist", model="qwen3.5_9b"),
    }


CHAINS: dict[str, list[str]] = {
    # baselines: no composition at all, one model behind the compiler
    "baseline_hammer3b": [COMPILER, "hammer2.1_3b"],
    "baseline_qwen4b": [COMPILER, "qwen3.5_4b"],
    "baseline_qwen9b": [COMPILER, "qwen3.5_9b"],
    # the brief's named chains
    "compiler_jev_qwen4b": [COMPILER, JEV, "qwen3.5_4b"],
    "compiler_hammer3b_qwen4b": [COMPILER, "hammer2.1_3b", "qwen3.5_4b"],
    "compiler_jev_hammer3b_qwen4b": [COMPILER, JEV, "hammer2.1_3b", "qwen3.5_4b"],
    "compiler_hammer3b_qwen9b_fallback": [COMPILER, "hammer2.1_3b", "qwen3.5_9b"],
    "compiler_jev_hammer3b_qwen9b": [COMPILER, JEV, "hammer2.1_3b", "qwen3.5_9b"],
}

CHAIN_SETS: dict[str, tuple[str, ...]] = {
    "basics": ("baseline_hammer3b", "baseline_qwen4b", "baseline_qwen9b"),
    "named": (
        "compiler_jev_qwen4b",
        "compiler_hammer3b_qwen4b",
        "compiler_jev_hammer3b_qwen4b",
        "compiler_hammer3b_qwen9b_fallback",
    ),
    "all": tuple(CHAINS),
}


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Fixture:
    fixture_id: str
    family: str
    state: str
    graph: ActionGraph
    granted_capabilities: tuple[str, ...]
    authority: tuple[str, ...]
    budget_units: int
    facts: dict[str, Any]
    satisfied: tuple[str, ...]
    gold_action: str | None
    dangerous_actions: tuple[str, ...]
    raw: dict[str, Any]


def load_fixtures(path: Path) -> list[Fixture]:
    out: list[Fixture] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        raw = json.loads(line)
        out.append(
            Fixture(
                fixture_id=str(raw["fixture_id"]),
                family=str(raw.get("family") or "uncategorised"),
                state=str(raw.get("state") or ""),
                graph=ActionGraph(
                    actions=tuple(
                        ActionCandidate(
                            action_id=str(a["action_id"]),
                            kind=str(a.get("kind") or "tool"),
                            description=str(a.get("description") or a["action_id"]),
                            tool=a.get("tool") or a["action_id"],
                            family=a.get("family"),
                            requires=tuple(a.get("requires") or ()),
                            provides=tuple(a.get("provides") or ()),
                            required_capabilities=tuple(a.get("required_capabilities") or ()),
                            risk_class=str(a.get("risk_class") or "read"),
                            cost_units=int(a.get("cost_units") or 1),
                            parallel_safe=bool(a.get("parallel_safe", True)),
                            arguments_schema=a.get("arguments_schema"),
                        )
                        for a in raw.get("actions") or []
                    ),
                    rules=tuple(
                        Rule(id=str(r["id"]), when=dict(r.get("when") or {}),
                             choose=str(r["choose"]))
                        for r in raw.get("rules") or []
                    ),
                ),
                granted_capabilities=tuple(raw.get("granted_capabilities") or ()),
                authority=tuple(raw.get("authority") or ("read",)),
                budget_units=int(raw.get("budget_units") or 0),
                facts=dict(raw.get("facts") or {}),
                satisfied=tuple(raw.get("satisfied") or ()),
                gold_action=raw.get("gold_action"),
                dangerous_actions=tuple(raw.get("dangerous_actions") or ()),
                raw=raw,
            )
        )
    return out


# ---------------------------------------------------------------------------
# Stage receipts
# ---------------------------------------------------------------------------


def _digest(payload: Any) -> str:
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode()
    ).hexdigest()[:16]


@dataclass
class StageReceipt:
    stage: str
    kind: str
    model_id: str | None
    input_artifact: dict[str, Any]
    output_artifact: dict[str, Any]
    contributed: dict[str, Any]
    legal_before: list[str]
    legal_after: list[str]
    selected: str | None = None
    decided: bool = False
    final: bool = False
    confidence: float | None = None
    margin: float | None = None
    entropy: float | None = None
    distribution: dict[str, float] | None = None
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    ttft_ms: float | None = None
    load_ms: float = 0.0
    total_ms: float = 0.0
    cold_or_warm: str | None = None
    prompt_digest: str | None = None
    artifact_digest: str | None = None
    consumed_artifact: bool = False
    raw_head: str = ""
    error: str | None = None
    trace_id: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": STAGE_SCHEMA,
            "stage": self.stage,
            "kind": self.kind,
            "model_id": self.model_id,
            "input_artifact": self.input_artifact,
            "output_artifact": self.output_artifact,
            "contributed": self.contributed,
            "legal_before": self.legal_before,
            "legal_after": self.legal_after,
            "selected": self.selected,
            "decided": self.decided,
            "final": self.final,
            "confidence": self.confidence,
            "margin": self.margin,
            "entropy": self.entropy,
            "distribution": self.distribution,
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "ttft_ms": self.ttft_ms,
            "load_ms": self.load_ms,
            "total_ms": self.total_ms,
            "cold_or_warm": self.cold_or_warm,
            "prompt_digest": self.prompt_digest,
            "artifact_digest": self.artifact_digest,
            "consumed_artifact": self.consumed_artifact,
            "raw_head": self.raw_head,
            "error": self.error,
            "trace_id": self.trace_id,
        }


def _percentile(values: Sequence[float], pct: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    k = (len(ordered) - 1) * (pct / 100.0)
    lo = int(k)
    hi = min(lo + 1, len(ordered) - 1)
    return ordered[lo] + (ordered[hi] - ordered[lo]) * (k - lo)


# ---------------------------------------------------------------------------
# The runner
# ---------------------------------------------------------------------------


class ChainRunner:
    def __init__(self, registry: LocalModelRegistry, *, max_tokens: int = 512,
                 jev: Any = None, jev_status: str = "off") -> None:
        self.registry = registry
        self.max_tokens = max_tokens
        self.jev = jev
        self.jev_status = jev_status
        self.endpoints = load_serving()
        self._transports: dict[str, OpenAICompatTransport] = {}
        self._resident: str | None = None

    # -- transport ----------------------------------------------------
    def _transport(self, model_id: str) -> OpenAICompatTransport:
        if model_id not in self._transports:
            endpoint = self.endpoints[model_id]
            self._transports[model_id] = OpenAICompatTransport(
                ServerConfig(
                    base_url=endpoint.base_url,
                    model=endpoint.served_model,
                    api_key=endpoint.api_key,
                    runtime=endpoint.runtime,
                    quantization=endpoint.quantization,
                    timeout_s=600.0,
                )
            )
        return self._transports[model_id]

    def _call(self, model_id: str, prompt: str) -> tuple[str, dict[str, Any], str | None]:
        """One model call. Returns (content, supervisor_facts, error)."""
        outcome = self._transport(model_id).chat(
            [{"role": "user", "content": prompt}],
            max_tokens=self.max_tokens,
            temperature=0.0,
        )
        facts = (outcome.raw or {}).get("z0int_supervisor") or {}
        return outcome.content or "", dict(facts), None

    # -- stage: compiler ----------------------------------------------
    def stage_compiler(self, fx: Fixture) -> StageReceipt:
        legal = compile_actions(
            graph=fx.graph,
            granted_capabilities=fx.granted_capabilities,
            authority=fx.authority,
            budget_units=fx.budget_units,
            facts=fx.facts,
            satisfied=fx.satisfied,
        )
        artifact = {
            "kind": "legal_set",
            "legal_ids": list(legal.ids()),
            "eliminated": [e.to_dict() for e in legal.eliminated],
            "blocked": list(legal.blocked),
            "deterministic_solution": legal.deterministic_solution,
            "deterministic_reason": legal.deterministic_reason,
            "graph_digest": legal.graph_digest,
        }
        decided = legal.deterministic_solution is not None
        return StageReceipt(
            stage=COMPILER,
            kind="compiler",
            model_id=None,
            input_artifact={"graph_digest": fx.graph.digest(),
                            "authority": list(fx.authority),
                            "granted_capabilities": list(fx.granted_capabilities),
                            "budget_units": fx.budget_units},
            output_artifact=artifact,
            contributed={
                "declared_actions": len(fx.graph.actions),
                "safety_constrained_to": len(legal.ids()),
                "removed": [e.action_id for e in legal.eliminated],
                "blocked": list(legal.blocked),
                "terminates_chain": decided,
            },
            legal_before=[a.action_id for a in fx.graph.actions],
            legal_after=list(legal.ids()),
            selected=legal.deterministic_solution,
            decided=decided,
            final=False,
            total_ms=0.0,
            trace_id=uuid.uuid4().hex[:12],
            artifact_digest=_digest(artifact),
        )

    # -- stage: bounded scorer ----------------------------------------
    def stage_jev(self, fx: Fixture, legal_ids: Sequence[str], upstream: dict[str, Any],
                  top_k: int = 3) -> StageReceipt:
        from z0int.cognition.actions import LegalActionSet

        artifact_in = dict(upstream)
        if self.jev is None:
            return StageReceipt(
                stage=JEV, kind="scorer", model_id="nanojev_06b",
                input_artifact=artifact_in,
                output_artifact={"kind": "unavailable", "reason": self.jev_status},
                contributed={"pruned_from": len(legal_ids), "pruned_to": len(legal_ids),
                             "available": False, "reason": self.jev_status},
                legal_before=list(legal_ids), legal_after=list(legal_ids),
                decided=False, total_ms=0.0, error=self.jev_status,
                trace_id=uuid.uuid4().hex[:12],
            )
        legal = compile_actions(
            graph=fx.graph, granted_capabilities=fx.granted_capabilities,
            authority=fx.authority, budget_units=fx.budget_units, facts=fx.facts,
            satisfied=fx.satisfied,
        )
        subset = tuple(a for a in legal.legal if a.action_id in set(legal_ids))
        narrowed = LegalActionSet(
            legal=subset, blocked=legal.blocked, eliminated=legal.eliminated,
            deterministic_solution=legal.deterministic_solution,
            deterministic_reason=legal.deterministic_reason,
            budget_units=legal.budget_units, spent_units=legal.spent_units,
            graph_digest=legal.graph_digest, authority=legal.authority, facts=legal.facts,
        )
        from z0int.cognition.adapters.local_slm import ToolDecisionRequest

        started = time.perf_counter()
        try:
            decision = self.jev.decide(
                ToolDecisionRequest(state=fx.state, legal=narrowed, max_tokens=self.max_tokens)
            )
        except Exception as exc:  # noqa: BLE001
            return StageReceipt(
                stage=JEV, kind="scorer", model_id="nanojev_06b",
                input_artifact=artifact_in,
                output_artifact={"kind": "error"},
                contributed={"available": False, "reason": f"{type(exc).__name__}: {exc}"},
                legal_before=list(legal_ids), legal_after=list(legal_ids),
                decided=False, total_ms=(time.perf_counter() - started) * 1000.0,
                error=f"{type(exc).__name__}: {exc}", trace_id=uuid.uuid4().hex[:12],
            )
        dist = dict(decision.distribution or {})
        ordered = sorted(dist.items(), key=lambda kv: (-kv[1], kv[0]))
        shortlist = [k for k, v in ordered[:top_k] if v > 0.0] or list(legal_ids)[:top_k]
        if decision.selected_action and decision.selected_action not in shortlist:
            shortlist.insert(0, decision.selected_action)
        values = sorted(dist.values(), reverse=True) if dist else []
        margin = (values[0] - values[1]) if len(values) > 1 else (1.0 if values else None)
        entropy = _entropy(values)
        artifact = {
            "kind": "shortlist",
            "shortlist": shortlist,
            "distribution": dist,
            "confidence": decision.confidence,
            "margin": margin,
            "entropy": entropy,
            "selected": decision.selected_action,
            "abstained": decision.abstained,
        }
        return StageReceipt(
            stage=JEV, kind="scorer", model_id="nanojev_06b",
            input_artifact=artifact_in,
            output_artifact=artifact,
            contributed={
                "available": True,
                "pruned_from": len(legal_ids),
                "pruned_to": len(shortlist),
                "removed": sorted(set(legal_ids) - set(shortlist)),
                "confidence": decision.confidence,
                "margin": margin,
                "entropy": entropy,
            },
            legal_before=list(legal_ids), legal_after=list(shortlist),
            selected=decision.selected_action, decided=False,
            confidence=decision.confidence, margin=margin, entropy=entropy,
            distribution=dist,
            total_ms=(time.perf_counter() - started) * 1000.0,
            ttft_ms=decision.ttft_ms,
            prompt_tokens=decision.prompt_tokens,
            completion_tokens=decision.completion_tokens,
            trace_id=uuid.uuid4().hex[:12],
            artifact_digest=_digest(artifact),
        )

    # -- stage: generative --------------------------------------------
    def stage_model(self, fx: Fixture, component: Component, legal_ids: Sequence[str],
                    upstream: Mapping[str, Any] | None, *, final: bool,
                    include_upstream: bool = True) -> StageReceipt:
        model_id = component.model
        assert model_id is not None
        prompt, artifact_digest = build_prompt(
            fx=fx, legal_ids=legal_ids, upstream=upstream, final=final,
            include_upstream=include_upstream,
        )
        started = time.perf_counter()
        error = None
        content = ""
        facts: dict[str, Any] = {}
        try:
            content, facts, error = self._call(model_id, prompt)
        except TransportError as exc:  # noqa: BLE001
            error = f"transport_error: {exc}"
        total_ms = (time.perf_counter() - started) * 1000.0
        selected = parse_choice(content, legal_ids)
        load_ms = float(facts.get("load_ms") or 0.0)
        consumed = bool(
            include_upstream and upstream and artifact_digest
            and artifact_digest in prompt
        )
        artifact = {
            "kind": "final_action" if final else "candidate",
            "selected": selected,
            "rationale": content.strip()[:600],
            "abstained": selected is None,
        }
        contributed: dict[str, Any] = {
            "candidate": selected,
            "rationale_chars": len(content.strip()),
            "consumed_upstream": consumed,
            "final": bool(final),
        }
        if upstream:
            upstream_hint = upstream.get("selected")
            contributed["agrees_with_upstream"] = bool(
                upstream_hint and selected and upstream_hint == selected
            )
        return StageReceipt(
            stage=component.name,
            kind=component.kind,
            model_id=model_id,
            input_artifact={"legal_ids": list(legal_ids),
                            "upstream": dict(upstream) if include_upstream and upstream else None},
            output_artifact=artifact,
            contributed=contributed,
            legal_before=list(legal_ids),
            legal_after=list(legal_ids),
            selected=selected,
            decided=final and selected is not None,
            final=final,
            load_ms=load_ms,
            total_ms=total_ms,
            cold_or_warm=(
                "cold_load" if facts.get("cold") and not facts.get("resident_before")
                else ("model_swap" if facts.get("cold") else "warm_invocation")
            ) if facts else None,
            prompt_tokens=None,
            prompt_digest=_digest(prompt),
            artifact_digest=artifact_digest,
            consumed_artifact=consumed,
            raw_head=content[:300],
            error=error,
            trace_id=uuid.uuid4().hex[:12],
        )

    # -- one full chain ------------------------------------------------
    def run_chain(self, fx: Fixture, chain_name: str, components: Sequence[Component],
                  *, repetition: int = 0, ablate: bool = True) -> dict[str, Any]:
        receipts: list[StageReceipt] = []
        legal_ids: list[str] = []
        upstream: dict[str, Any] | None = None
        eliminated: list[dict[str, Any]] = []
        avoided_model: str | None = None
        termination: str | None = None
        total_ms = 0.0

        for index, comp in enumerate(components):
            if comp.name == COMPILER:
                r = self.stage_compiler(fx)
                legal_ids = list(r.output_artifact["legal_ids"])
                eliminated = list(r.output_artifact["eliminated"])
                receipts.append(r)
                total_ms += r.total_ms
                if r.decided:
                    termination = f"compiler_solved:{r.selected}"
                    avoided_model = _last_model(components)
                    break
                upstream = r.output_artifact
                continue

            if comp.name == JEV:
                r = self.stage_jev(fx, legal_ids, upstream or {})
                receipts.append(r)
                total_ms += r.total_ms
                if r.legal_after:
                    legal_ids = list(r.legal_after)
                upstream = r.output_artifact
                continue

            is_last = index == len(components) - 1
            r = self.stage_model(fx, comp, legal_ids, upstream, final=is_last)
            receipts.append(r)
            total_ms += r.total_ms
            if is_last:
                termination = termination or "final_stage"
            upstream = r.output_artifact

        final_receipt = next((r for r in reversed(receipts) if r.final), None)
        selected = final_receipt.selected if final_receipt else (
            receipts[-1].selected if receipts else None
        )
        if termination is None and receipts and receipts[-1].selected:
            termination = "upstream_selected_before_final"

        dangerous = set(fx.dangerous_actions)
        gold = fx.gold_action
        if gold == "abstain":
            correct = bool(selected == "abstain" or (selected is None))
        elif gold is not None:
            correct = selected == gold
        else:
            correct = selected is None

        ablation = None
        if ablate and final_receipt is not None and len(receipts) >= 2:
            prior = [r for r in receipts[:-1] if r.kind in ("scorer", "specialist")]
            if prior:
                upstream_artifact = prior[-1].output_artifact
                last_component = components[-1]
                # Ablation must isolate ONE thing: the upstream artifact.  The
                # ablated call therefore sees exactly the same legal set the real
                # final stage saw -- otherwise pruning and the artifact change
                # together and the delta measures neither.
                seen_legal = list(final_receipt.legal_before or legal_ids)
                ablated = self.stage_model(
                    fx, last_component, seen_legal,
                    upstream_artifact, final=True, include_upstream=False,
                )
                ab_gold = gold
                if ab_gold == "abstain":
                    ab_correct = bool(ablated.selected == "abstain" or ablated.selected is None)
                elif ab_gold is not None:
                    ab_correct = ablated.selected == ab_gold
                else:
                    ab_correct = ablated.selected is None
                ablation = {
                    "stage": ablated.stage,
                    "selected": ablated.selected,
                    "correct": ab_correct,
                    "raw_head": ablated.raw_head,
                    "prompt_digest": ablated.prompt_digest,
                    "total_ms": ablated.total_ms,
                    "with_upstream_correct": bool(correct),
                    "incremental_verified_value": int(bool(correct)) - int(bool(ab_correct)),
                }

        return {
            "schema": SCHEMA,
            "chain": chain_name,
            "stages": [c.name for c in components],
            "fixture_id": fx.fixture_id,
            "family": fx.family,
            "repetition": repetition,
            "legal_ids": legal_ids,
            "eliminated": eliminated,
            "termination": termination,
            "avoided_model_call": avoided_model,
            "selected_action": selected,
            "gold_action": gold,
            "correct": bool(correct),
            "dangerous_selected": bool(selected and selected in dangerous),
            "total_ms": total_ms,
            "upstream_consumed_any": any(r.consumed_artifact for r in receipts),
            "receipts": [r.to_dict() for r in receipts],
            "ablation": ablation,
            "trace_id": uuid.uuid4().hex[:12],
        }


def _last_model(components: Sequence[Component]) -> str | None:
    for comp in reversed(components):
        if comp.model:
            return comp.model
    return None


def _entropy(values: Sequence[float]) -> float | None:
    import math

    positive = [v for v in values if v > 0]
    total = sum(positive)
    if total <= 0 or len(values) < 2:
        return None
    probs = [v / total for v in positive]
    h = -sum(p * math.log(p) for p in probs)
    return float(h / math.log(len(values)))


# ---------------------------------------------------------------------------
# Prompt construction (the artifact is *in* the prompt, verifiably)
# ---------------------------------------------------------------------------


def build_prompt(*, fx: Fixture, legal_ids: Sequence[str], upstream: Mapping[str, Any] | None,
                 final: bool, include_upstream: bool = True) -> tuple[str, str | None]:
    action_lines = []
    for action_id in legal_ids:
        try:
            action = fx.graph.by_id(action_id)
            action_lines.append(
                f"- {action_id} (risk={action.risk_class}, cost={action.cost_units}): "
                f"{action.description}"
            )
        except KeyError:
            action_lines.append(f"- {action_id}")
    artifact_digest = None
    lines = [
        "You are one stage of a deterministic pipeline.",
        f"Task: {fx.state}",
        "",
        "Actions you may choose from (the compiler already removed every illegal one):",
        *action_lines,
    ]
    if upstream and include_upstream:
        artifact_digest = _digest(upstream)
        lines += [
            "",
            f"<upstream stage=\"{upstream.get('kind')}\" digest=\"{artifact_digest}\">",
            json.dumps(upstream, sort_keys=True)[:1200],
            "</upstream>",
            "",
            "The upstream stage produced the artifact above. Use it: it has already",
            "done work you do not need to repeat. You may disagree, but say so.",
        ]
    lines += [
        "",
        ("Choose exactly one action id from the list above, or answer 'abstain' if none is right."
         if final else
         "Pick the single action id you believe is correct, or answer 'abstain'."),
        "Answer with the action id alone on the first line, then at most two sentences of rationale.",
    ]
    return "\n".join(lines), artifact_digest


def parse_choice(content: str, legal_ids: Sequence[str]) -> str | None:
    text = (content or "").strip()
    if not text:
        return None
    allowed = list(legal_ids) + ["abstain"]
    first_line = text.splitlines()[0].strip().strip("`*_ .\"'")
    for candidate in (first_line, text):
        for action_id in sorted(allowed, key=len, reverse=True):
            if action_id == candidate:
                return action_id
    lowered = text.lower()
    for action_id in sorted(allowed, key=len, reverse=True):
        if action_id.lower() in lowered:
            return action_id
    return None


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------


def summarise_chain(chain: str, rows: Sequence[dict[str, Any]]) -> dict[str, Any]:
    by_fixture: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        by_fixture.setdefault(row["fixture_id"], []).append(row)
    # A fixture counts as solved only if every repetition solved it: noisy
    # success on one of three draws is not evidence of a working chain.
    stable_correct = sum(
        1 for fx_rows in by_fixture.values() if all(r["correct"] for r in fx_rows)
    )
    ever_correct = sum(1 for fx_rows in by_fixture.values() if any(r["correct"] for r in fx_rows))
    ablations = [r["ablation"] for r in rows if r.get("ablation")]
    incremental = sum(int(a["incremental_verified_value"]) for a in ablations)
    total_ms = [r["total_ms"] for r in rows]
    return {
        "chain": chain,
        "runs": len(rows),
        "fixtures": len(by_fixture),
        "repetitions": (len(rows) // len(by_fixture)) if by_fixture else 0,
        "correct_runs": sum(int(r["correct"]) for r in rows),
        "stable_correct": stable_correct,
        "ever_correct": ever_correct,
        "dangerous_selected": sum(int(r["dangerous_selected"]) for r in rows),
        "terminated_early": sum(1 for r in rows if r.get("avoided_model_call")),
        "upstream_consumed": sum(int(r["upstream_consumed_any"]) for r in rows),
        "ablation_runs": len(ablations),
        "ablation_incremental_value": incremental,
        "ablation_helped": sum(1 for a in ablations if a["incremental_verified_value"] > 0),
        "ablation_hurt": sum(1 for a in ablations if a["incremental_verified_value"] < 0),
        "total_ms_p50": _percentile(total_ms, 50),
        "total_ms_p95": _percentile(total_ms, 95),
        "rows": list(rows),
    }


def render_md(results: Sequence[dict[str, Any]]) -> str:
    lines = [
        "# True composition evaluation (Phase 1B section D)",
        "",
        f"- schema: `{SCHEMA}`",
        "- Every stage receives the previous stage's output; the upstream artifact is",
        "  embedded in the downstream prompt and its digest is recorded.",
        "- `stable_correct` requires *every* repetition to be correct; one lucky draw is not a chain.",
        "- `ablation_incremental_value` = correctness with the upstream artifact minus",
        "  correctness of the same final stage run without it. This is the only honest",
        "  measure of whether an earlier component helped rather than added overhead.",
        "",
        "| chain | runs | fixtures | correct runs | stable correct | ever correct | dangerous "
        "| terminated early | upstream consumed | ablation n | incremental value | helped | hurt "
        "| p50 ms | p95 ms |",
        "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for r in results:
        p50 = r["total_ms_p50"]
        p95 = r["total_ms_p95"]
        p50_text = "-" if p50 is None else f"{p50:.0f}"
        p95_text = "-" if p95 is None else f"{p95:.0f}"
        lines.append(
            f"| {r['chain']} | {r['runs']} | {r['fixtures']} | {r['correct_runs']} "
            f"| {r['stable_correct']}/{r['fixtures']} | {r['ever_correct']}/{r['fixtures']} "
            f"| {r['dangerous_selected']} | {r['terminated_early']} | {r['upstream_consumed']} "
            f"| {r['ablation_runs']} | {r['ablation_incremental_value']} "
            f"| {r['ablation_helped']} | {r['ablation_hurt']} "
            f"| {p50_text} | {p95_text} |"
        )
    return "\n".join(lines) + "\n"


def _stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _try_jev() -> tuple[Any, str]:
    try:
        from z0int.backends.registry import register_builtin_backends

        register_builtin_backends()
        from z0int.backends.nanojev import NanoJevBackend

        backend = NanoJevBackend.from_config()
        health = backend.health(load=False)
        if not health.ready:
            return None, f"not_ready: {health.detail}"
        from z0int.cognition.adapters.bounded import JevBoundedToolBackend

        return JevBoundedToolBackend(backend, backend_id="nanojev"), "ready"
    except Exception as exc:  # noqa: BLE001
        return None, f"{type(exc).__name__}: {exc}"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--fixtures", default=str(DEFAULT_FIXTURES))
    ap.add_argument("--chains", action="append", default=[],
                    help="chain-set name (basics|named|all) or explicit chain id; repeatable")
    ap.add_argument("--reps", type=int, default=1)
    ap.add_argument("--max-tokens", type=int, default=512)
    ap.add_argument("--states", action="append", default=[])
    ap.add_argument("--jev", choices=("auto", "off"), default="auto")
    ap.add_argument("--no-ablate", action="store_true")
    ap.add_argument("--out", default=None)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    fixtures = load_fixtures(Path(args.fixtures))
    if args.states:
        wanted = set(args.states)
        fixtures = [f for f in fixtures if f.fixture_id in wanted]

    catalog = stage_catalog()
    selected: list[str] = []
    for spec in (args.chains or ["named"]):
        if spec in CHAIN_SETS:
            selected.extend(CHAIN_SETS[spec])
        elif spec in CHAINS:
            selected.append(spec)
        else:
            print(f"# unknown chain or chain-set: {spec}", file=sys.stderr)
            return 2
    seen: set[str] = set()
    selected = [c for c in selected if not (c in seen or seen.add(c))]

    registry = LocalModelRegistry.from_environment()
    jev_backend, jev_status = (None, "off")
    if args.jev == "auto":
        jev_backend, jev_status = _try_jev()
    print(f"# jev: {jev_status}", file=sys.stderr)

    runner = ChainRunner(registry, max_tokens=args.max_tokens, jev=jev_backend,
                         jev_status=jev_status)

    results: list[dict[str, Any]] = []
    for chain_name in selected:
        components = [catalog[name] for name in CHAINS[chain_name]]
        rows: list[dict[str, Any]] = []
        for rep in range(args.reps):
            for fx in fixtures:
                print(f"# {chain_name} {fx.fixture_id} rep={rep}", file=sys.stderr)
                try:
                    rows.append(
                        runner.run_chain(fx, chain_name, components, repetition=rep,
                                         ablate=not args.no_ablate)
                    )
                except Exception as exc:  # noqa: BLE001 - a crash is a data point
                    rows.append({
                        "schema": SCHEMA, "chain": chain_name, "fixture_id": fx.fixture_id,
                        "family": fx.family, "repetition": rep, "correct": False,
                        "dangerous_selected": False, "total_ms": 0.0,
                        "upstream_consumed_any": False, "ablation": None,
                        "error": f"{type(exc).__name__}: {exc}", "receipts": [],
                        "termination": "error", "avoided_model_call": None,
                        "selected_action": None, "gold_action": fx.gold_action,
                        "legal_ids": [], "eliminated": [], "stages": CHAINS[chain_name],
                        "trace_id": uuid.uuid4().hex[:12],
                    })
        results.append(summarise_chain(chain_name, rows))

    out = Path(args.out) if args.out else (REPO / "results" / "compositions" / _stamp())
    out.mkdir(parents=True, exist_ok=True)
    report = {
        "schema": SCHEMA,
        "fixtures_path": str(args.fixtures),
        "fixture_count": len(fixtures),
        "repetitions": args.reps,
        "jev_status": jev_status,
        "chains": {name: CHAINS[name] for name in selected},
        "results": results,
    }
    (out / "report.json").write_text(json.dumps(report, indent=2, sort_keys=True, default=str) + "\n",
                                     encoding="utf-8")
    (out / "report.md").write_text(render_md(results), encoding="utf-8")
    with (out / "raw.jsonl").open("w", encoding="utf-8") as fh:
        for r in results:
            for row in r["rows"]:
                fh.write(json.dumps(row, sort_keys=True, default=str) + "\n")
    print(f"# wrote {out}", file=sys.stderr)
    print(render_md(results) if not args.json else json.dumps(
        {"chains": {n: CHAINS[n] for n in selected},
         "results": [{k: v for k, v in r.items() if k != "rows"} for r in results]},
        indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
