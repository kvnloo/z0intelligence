#!/usr/bin/env python3
"""Phase 1B step B -- densify the ``(state, arm)`` evidence table.

Phase 1 produced 522 ``(state, arm)`` cells of which 460 held a single
observation.  A frozen non-inferiority gate cannot tell "this arm is worse here"
from "we measured this arm once", so every Phase 1 ownership decision is
currently sample noise until the cells are filled.

This harness re-measures the cells through the **production** path
(``z0int cognition serve`` on the supervisor endpoint) and preserves one raw
receipt per call.  It never aggregates: distributions are computed later, by the
frontier analysis, from the receipts this writes.

Design rules that come from the Phase 1B brief:

* one receipt per model call, append-only ``observations.jsonl``, resumable;
* cold load, model swap, already-resident and warm invocation are distinguished
  from the supervisor's own residency facts, never inferred from wall clock;
* physical accounting (tokens, ms) is recorded as observed.  Cost is **not**
  computed here: Tokenomics owns measurement and economics, and a second
  interpretation in Q-Route would be exactly the duplication the boundaries
  forbid;
* the sampling is adaptive: everything gets ``--min-reps``, and only cells that
  can change a decision are topped up to ``--max-reps`` by a separate pass.

    python scripts/densify_measurements.py --run-id p1b-... --arms core --reps 3
    python scripts/densify_measurements.py --run-id p1b-... --arms cold --reps 3
    python scripts/densify_measurements.py --run-id p1b-... --topup
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import statistics
import subprocess
import sys
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from z0int.cognition.actions import (  # noqa: E402
    ActionCandidate,
    ActionGraph,
    Rule,
    compile_actions,
)
from z0int.cognition.adapters.local_slm import ToolDecisionRequest  # noqa: E402
from z0int.cognition.cascade import CascadeContext, CognitionCascade  # noqa: E402
from z0int.cognition.escalation import EscalationPolicy, EscalationThresholds  # noqa: E402
from z0int.cognition.manifest import load_local_cognition  # noqa: E402
from z0int.cognition.registry import LocalModelRegistry, load_serving  # noqa: E402

SCHEMA = "z0int.phase1b.observation.v1"
SCHEDULE_SCHEMA = "z0int.phase1b.schedule.v1"
RESULTS_ROOT = REPO / "results" / "phase1b"
FREEZE_FIXTURE_KEY = "benchmarks/fixtures/local-cognition-v1/examples.jsonl"

#: Frozen completion budget for Phase 1B.  Phase 1 measured the same fixtures at
#: 256 (``eval-llamacpp``) and at 1024 (``compositions``, ``eval-budget1024``) and
#: got materially different answers for the reasoning models, so the budget is
#: part of the measurement contract, not a knob.  1024 matches the composition
#: headline (compiler + Hammer2.1-3B 24/28) that Phase 1B is re-testing.
DEFAULT_MAX_TOKENS = 1024

ALL_RISKS = ("read", "write", "destructive", "publish", "credential", "payment")


# ---------------------------------------------------------------------------
# Fixtures (referenced from the owning repo, hash-checked against the freeze)
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
    expect_abstain: bool
    dangerous_actions: tuple[str, ...]
    raw: dict[str, Any]


def load_fixtures(path: Path) -> list[Fixture]:
    out: list[Fixture] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        raw = json.loads(line)
        actions = tuple(
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
        )
        rules = tuple(
            Rule(
                id=str(r["id"]),
                when=dict(r.get("when") or {}),
                choose=str(r["choose"]),
                rationale=str(r.get("rationale") or ""),
            )
            for r in raw.get("rules") or []
        )
        out.append(
            Fixture(
                fixture_id=str(raw["fixture_id"]),
                family=str(raw.get("family") or "uncategorised"),
                state=str(raw.get("state") or ""),
                graph=ActionGraph(actions=actions, rules=rules),
                granted_capabilities=tuple(raw.get("granted_capabilities") or ()),
                authority=tuple(raw.get("authority") or ("read",)),
                budget_units=int(raw.get("budget_units") or 0),
                facts=dict(raw.get("facts") or {}),
                satisfied=tuple(raw.get("satisfied") or ()),
                gold_action=raw.get("gold_action"),
                expect_abstain=bool(raw.get("expect_abstain", False)),
                dangerous_actions=tuple(raw.get("dangerous_actions") or ()),
                raw=raw,
            )
        )
    return out


def state_features(fx: Fixture, legal: Any) -> dict[str, Any]:
    """The feature vector available to a router *before* it picks an arm.

    Deliberately wider than the frozen Q-Route v0 four-feature map: section G of
    the brief asks which variables are missing, and the only honest way to answer
    is to record the ones that were cheap to record and see which of them
    separate states that currently collide.
    """
    risk_counts: dict[str, int] = {}
    family_counts: dict[str, int] = {}
    for action in legal.legal:
        risk_counts[action.risk_class] = risk_counts.get(action.risk_class, 0) + 1
        key = action.family or action.kind
        family_counts[key] = family_counts.get(key, 0) + 1
    return {
        "candidate_action_count": legal.candidate_count,
        "family": fx.family,
        "legal_family_count": len(family_counts),
        "legal_families": sorted(family_counts),
        "legal_risk_classes": sorted(risk_counts),
        "max_risk_class": _max_risk(sorted(risk_counts)),
        "has_deterministic_solution": legal.deterministic_solution is not None,
        "is_empty": bool(legal.is_empty),
        "expect_abstain": bool(fx.expect_abstain),
        "gold_present": fx.gold_action is not None,
        "declared_dangerous_count": len(fx.dangerous_actions),
        "authority_breadth": len(fx.authority),
        "budget_units": fx.budget_units,
        "satisfied_count": len(fx.satisfied),
    }


_RISK_ORDER = ("read", "write", "destructive", "publish", "credential", "payment")


def _max_risk(classes: Sequence[str]) -> str | None:
    best = None
    for rank, name in enumerate(_RISK_ORDER):
        if name in classes:
            best = name
    return best


# ---------------------------------------------------------------------------
# Arms
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Arm:
    """One measurable mechanism applied to one state."""

    name: str
    kind: str  # deterministic | bounded | unfiltered | cascade
    compiler: bool = True
    model: str | None = None
    #: True when the arm routes through the local NanoJev (non-generative, zero
    #: decode) scorer rather than a generative model. Historically the arm's
    #: model field held the literal string "JEV" and every emission rewrote it to
    #: `nanojev_06b`; the display name leaked into published docs and into
    #: z0evals, where the phase 1B arm's 45/84 was narrated as TypeSafe Jev's
    #: score. It never was: `observations.jsonl` records `model_id=nanojev_06b`,
    #: revision 4a19595eada0857133c0d2be024f879a4077054b, quant bfloat16 for
    #: every `compiler+jev` row. The flag is kept (it also marks "not a
    #: supervisor resident"), but the model field is now honest and the emitted
    #: `scorer_type` states the scorer explicitly.
    jev: bool = False
    scorer_type: str | None = None
    cascade: tuple[str, ...] = ()
    alias: str = ""
    note: str = ""

    def models(self) -> tuple[str, ...]:
        if self.kind == "cascade":
            return self.cascade
        # The NanoJev scorer is not servable by the llama.cpp supervisor, so it is
        # excluded from residency planning — unchanged behaviour, now stated.
        return (self.model,) if self.model and not self.jev else ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "kind": self.kind,
            "compiler": self.compiler,
            "model": self.model,
            "jev": self.jev,
            "scorer_type": self.scorer_type,
            "cascade": list(self.cascade),
            "alias": self.alias,
            "note": self.note,
        }


def _arms() -> dict[str, Arm]:
    arms = [
        Arm("deterministic.compiler_only", "deterministic", model=None, alias="rules_baseline",
            note="compiler answers when it can, abstains otherwise; no model call"),
        # --- compiler-first single-model arms (the section C candidate frontier)
        Arm("compiler+hammer2.1_3b", "bounded", model="hammer2.1_3b",
            alias="SUB_compiler_hammer3b"),
        Arm("compiler+hammer2.1_7b", "bounded", model="hammer2.1_7b",
            alias="SUB_compiler_hammer7b"),
        Arm("compiler+functiongemma_270m", "bounded", model="functiongemma_270m",
            alias="SUB_compiler_functiongemma"),
        Arm("compiler+qwen3.5_4b", "bounded", model="qwen3.5_4b",
            alias="SUB_compiler_qwen4b"),
        Arm("compiler+qwen3.5_9b", "bounded", model="qwen3.5_9b",
            alias="SUB_compiler_qwen9b"),
        Arm("compiler+nemotron_orchestrator_8b", "bounded", model="nemotron_orchestrator_8b",
            alias="C_compiler_nemotron"),
        # --- compiler-first bounded scorer
        # `model` was the literal string "JEV"; it is our local NanoJev 0.6B.
        Arm("compiler+jev", "bounded", model="nanojev_06b", jev=True,
            scorer_type="nanojev"),
        Arm("compiler+jev+qwen3.5_4b", "cascade", jev=True, scorer_type="nanojev",
            cascade=("qwen3.5_4b",)),
        # --- unfiltered controls: the model sees every action, incl. dangerous
        Arm("unfiltered+hammer2.1_3b", "unfiltered", compiler=False, model="hammer2.1_3b"),
        Arm("unfiltered+qwen3.5_4b", "unfiltered", compiler=False, model="qwen3.5_4b",
            alias="SUB_qwen4b_alone"),
        Arm("unfiltered+qwen3.5_9b", "unfiltered", compiler=False, model="qwen3.5_9b",
            alias="A_qwen9b_alone"),
        Arm("unfiltered+nemotron_orchestrator_8b", "unfiltered", compiler=False,
            model="nemotron_orchestrator_8b", alias="B_nemotron_alone"),
        # --- Phase 1 ladder arms, preserved for comparability (multi-model cascades)
        Arm("compiler+jev+nemotron_orchestrator_8b", "cascade", jev=True, scorer_type="nanojev",
            cascade=("nemotron_orchestrator_8b",), alias="D_compiler_jev_nemotron"),
        Arm("compiler+jev+nemotron+qwen3.5_9b", "cascade", jev=True, scorer_type="nanojev",
            cascade=("nemotron_orchestrator_8b", "qwen3.5_9b"),
            alias="E_compiler_jev_nemotron_qwen"),
        Arm("compiler+hammer3b+jev+nemotron+qwen3.5_9b", "cascade", jev=True, scorer_type="nanojev",
            cascade=("hammer2.1_3b", "nemotron_orchestrator_8b", "qwen3.5_9b"),
            alias="F_compiler_tiny_jev_nemotron_qwen"),
    ]
    return {a.name: a for a in arms}


ARM_SETS: dict[str, tuple[str, ...]] = {
    # the section-C candidate frontier: single-model, compiler-first, plus controls
    "core": (
        "deterministic.compiler_only",
        "compiler+hammer2.1_3b",
        "compiler+hammer2.1_7b",
        "compiler+functiongemma_270m",
        "compiler+qwen3.5_4b",
        "compiler+qwen3.5_9b",
        "compiler+nemotron_orchestrator_8b",
        "unfiltered+hammer2.1_3b",
        "unfiltered+qwen3.5_4b",
        "unfiltered+qwen3.5_9b",
        "unfiltered+nemotron_orchestrator_8b",
    ),
    # cost of the old ladder, measured on the production path
    "ladder": (
        "compiler+jev",
        "compiler+jev+qwen3.5_4b",
        "compiler+jev+nemotron_orchestrator_8b",
        "compiler+jev+nemotron+qwen3.5_9b",
        "compiler+hammer3b+jev+nemotron+qwen3.5_9b",
    ),
    "all": tuple(_arms()),
}


# ---------------------------------------------------------------------------
# Residency / GPU observation
# ---------------------------------------------------------------------------


def _nvidia_smi(query: str) -> str | None:
    try:
        proc = subprocess.run(
            ["nvidia-smi", f"--query-gpu={query}", "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return proc.stdout.strip() if proc.returncode == 0 else None


def gpu_memory_used_mib() -> int | None:
    raw = _nvidia_smi("memory.used")
    try:
        return int(raw.splitlines()[0]) if raw else None
    except (ValueError, IndexError):
        return None


def supervisor_status(base_url: str) -> dict[str, Any]:
    """Resident model + runtime, read from the production endpoint."""
    import urllib.request

    try:
        with urllib.request.urlopen(f"{base_url.rstrip('/')}/z0int/status", timeout=10) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception:  # noqa: BLE001 - status is advisory, never fatal
        return {}


def supervisor_release(base_url: str) -> dict[str, Any]:
    import urllib.request

    req = urllib.request.Request(
        f"{base_url.rstrip('/')}/z0int/release", data=b"{}", method="POST",
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception as exc:  # noqa: BLE001
        return {"error": f"{type(exc).__name__}: {exc}"}


# ---------------------------------------------------------------------------
# One observation
# ---------------------------------------------------------------------------


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


@dataclass
class ObservationBuilder:
    run_id: str
    trace_prefix: str
    provenance: dict[str, Any]

    def build(
        self,
        *,
        fx: Fixture,
        arm: Arm,
        features: dict[str, Any],
        legal_ids: Sequence[str],
        eliminated: Sequence[dict[str, Any]],
        decision: Any,
        selected: str | None,
        abstained: bool,
        invalid: bool,
        latency_ms: float,
        resident_before: str | None,
        residency: dict[str, Any],
        invocation_index: int,
        cold_or_warm: str,
        vram_mib: int | None,
        attempts: int,
        error: str | None,
        distribution: Mapping[str, float] | None,
        confidence: float | None,
        max_tokens: int,
        trajectory: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        diag = dict(getattr(decision, "diagnostics", {}) or {}) if decision is not None else {}
        supervisor = diag.get("supervisor") or {}
        load_ms = float(supervisor.get("load_ms") or 0.0)
        wall_ms = float(supervisor.get("wall_ms") or latency_ms)
        ttft_ms = getattr(decision, "ttft_ms", None)
        tok_s = getattr(decision, "decode_tok_s", None)
        tokens_in = getattr(decision, "prompt_tokens", None)
        tokens_out = getattr(decision, "completion_tokens", None)

        dangerous_set = set(fx.dangerous_actions)
        dangerous_selected = bool(selected and selected in dangerous_set)
        dangerous_exposed = bool(dangerous_set & set(legal_ids))

        if fx.gold_action == "abstain":
            # The fixtures model abstention as a real, offered action.  Refusing
            # to act (no action at all) is the same decision and is scored the
            # same way, matching ``local_tool_calling_eval``.
            correct = bool(selected == "abstain" or (abstained and selected is None))
        elif fx.gold_action is not None:
            correct = selected == fx.gold_action
        else:
            correct = bool(abstained)

        dist = dict(distribution) if distribution else None
        margin, entropy = _distribution_shape(dist)

        return {
            "schema": SCHEMA,
            "run_id": self.run_id,
            "trace_id": f"{self.trace_prefix}-{uuid.uuid4().hex[:12]}",
            "observed_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            # --- state identity
            "state_id": fx.fixture_id,
            "state_family": fx.family,
            "state_features": features,
            "legal_actions": list(legal_ids),
            "candidate_action_count": len(legal_ids),
            "eliminated": list(eliminated),
            "deterministic_solution": None,
            # --- arm identity
            "arm": arm.name,
            "arm_alias": arm.alias or None,
            "arm_kind": arm.kind,
            "compiler_first": bool(arm.compiler),
            "router_in_loop": False,
            # --- model identity
            "model_id": arm.model,
            # Explicit scorer identity. This field is what was missing when the
            # phase 1B arm's 45/84 was published as TypeSafe Jev's score: the
            # scorer was only ever encoded in the arm's display name, and that
            # name said "jev" while the run used local NanoJev. Never infer a
            # scorer from an arm name.
            "scorer_type": arm.scorer_type,
            "scorer_backend": "nanojev" if arm.jev else "generative",
            "model_revision": self.provenance["models"].get(
                arm.model or "", {}
            ).get("hf_revision"),
            "quant": self.provenance["models"].get(
                arm.model or "", {}
            ).get("quant"),
            "compiler_revision": self.provenance["compiler_revision"],
            "router_revision": self.provenance["router_revision"],
            "source_fixture_revision": self.provenance["fixture_sha256"],
            "max_tokens": max_tokens,
            # --- residency
            "cold_or_warm": cold_or_warm,
            "resident_before": resident_before,
            "resident_after": residency.get("resident"),
            "supervisor_cold": bool(supervisor.get("cold")) if supervisor else None,
            "invocation_index": invocation_index,
            "load_ms": load_ms,
            "ttft_ms": ttft_ms,
            "decision_ms": max(0.0, wall_ms - load_ms),
            "total_ms": wall_ms,
            "harness_latency_ms": latency_ms,
            "tokens_in": tokens_in,
            "tokens_out": tokens_out,
            "cached_tokens": getattr(decision, "cached_tokens", None),
            "tok_s": tok_s,
            "peak_vram_mib": vram_mib,
            # --- decision
            "selected_action": selected,
            "abstained": bool(abstained),
            "distribution": dist,
            "distribution_source": (
                "scorer" if dist and any(0.0 < v < 1.0 for v in dist.values())
                else ("one_hot" if dist else "none")
            ),
            "confidence": confidence,
            "margin": margin,
            "entropy": entropy,
            "invalid_call": bool(invalid),
            "tool_result": None,
            # --- verification
            "verification": {
                "kind": "fixture_gold",
                "gold_action": fx.gold_action,
                "expect_abstain": bool(fx.expect_abstain),
            },
            "correct": bool(correct),
            "dangerous_exposed": dangerous_exposed,
            "dangerous_selected": dangerous_selected,
            "failure_retry": {"attempts": int(attempts), "error": error},
            # --- raw utility components only; the scalar belongs to evolution-lab
            "utility_components": {
                "success": bool(correct),
                "dangerous": dangerous_selected,
                "invalid": bool(invalid),
                "abstained": bool(abstained),
                "latency_ms": wall_ms,
                "decision_ms": max(0.0, wall_ms - load_ms),
                "tokens_out": tokens_out,
            },
            "accounting_authority": "tokenomics",
            "cost_usd": None,
            "trajectory": dict(trajectory) if trajectory else None,
            "raw_decision": decision.to_dict() if decision is not None and hasattr(decision, "to_dict") else None,
        }


def _distribution_shape(dist: Mapping[str, float] | None) -> tuple[float | None, float | None]:
    """Top-1/top-2 margin and normalised Shannon entropy of a choice distribution."""
    if not dist:
        return None, None
    values = sorted((float(v) for v in dist.values()), reverse=True)
    if not values:
        return None, None
    margin = values[0] - values[1] if len(values) > 1 else 1.0
    total = sum(v for v in values if v > 0.0)
    entropy = None
    if total > 0 and len(values) > 1:
        import math

        probs = [v / total for v in values if v > 0.0]
        h = -sum(p * math.log(p) for p in probs)
        entropy = float(h / math.log(len(values))) if len(values) > 1 else 0.0
    return float(margin), entropy


# ---------------------------------------------------------------------------
# The measurement driver
# ---------------------------------------------------------------------------


@dataclass
class Campaign:
    registry: LocalModelRegistry
    fixtures: list[Fixture]
    arms: dict[str, Arm]
    out_dir: Path
    run_id: str
    base_url: str
    max_tokens: int = DEFAULT_MAX_TOKENS
    provenance: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.observations_path = self.out_dir / "observations.jsonl"
        self.observations_path.parent.mkdir(parents=True, exist_ok=True)
        self._seen = self._load_seen()
        self._builder = ObservationBuilder(
            run_id=self.run_id,
            trace_prefix="p1b",
            provenance=self.provenance,
        )
        self._backend_cache: dict[str, Any] = {}
        self._jev = None
        self._resident: str | None = None
        self._invocation_index = 0

    # -- resumability -------------------------------------------------
    def _load_seen(self) -> set[tuple[str, str, int]]:
        seen: set[tuple[str, str, int]] = set()
        if not self.observations_path.is_file():
            return seen
        with self.observations_path.open(encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                seen.add((row.get("state_id"), row.get("arm"), int(row.get("repetition", -1))))
        return seen

    def _write(self, row: dict[str, Any]) -> None:
        with self.observations_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, sort_keys=True, default=str) + "\n")

    # -- backends -----------------------------------------------------
    def backend(self, model_id: str) -> Any:
        if model_id not in self._backend_cache:
            self._backend_cache[model_id] = self.registry.backend_for(model_id)
        return self._backend_cache[model_id]

    def jev(self) -> Any:
        if self._jev is None:
            self._jev = _try_jev()
        return self._jev

    # -- residency ----------------------------------------------------
    def note_resident(self, model_id: str | None) -> None:
        if model_id != self._resident:
            self._invocation_index = 0
        self._resident = model_id

    def _classify(self, *, model_id: str | None, resident_before: str | None,
                  supervisor: Mapping[str, Any], invocation_index: int, kind: str) -> str:
        if kind == "deterministic" or model_id is None:
            return "no_model_call"
        if resident_before == model_id:
            return "already_resident" if invocation_index == 0 else "warm_invocation"
        if supervisor.get("cold"):
            return "cold_load" if resident_before is None else "model_swap"
        return "warm_invocation"

    # -- one (state, arm, repetition) ---------------------------------
    def measure(self, fx: Fixture, arm: Arm, repetition: int) -> dict[str, Any]:
        legal = (
            compile_actions(
                graph=fx.graph,
                granted_capabilities=fx.granted_capabilities,
                authority=fx.authority,
                budget_units=fx.budget_units,
                facts=fx.facts,
                satisfied=fx.satisfied,
            )
            if arm.compiler
            else _unfiltered(fx)
        )
        features = state_features(fx, legal)

        status = supervisor_status(self.base_url)
        resident_before = status.get("resident")
        self.note_resident(resident_before)

        # The NanoJev scorer is not a supervisor resident, so it contributes no
        # residency class — but it IS a model and must still be named.
        model_id = arm.model if not arm.jev else None
        if arm.kind == "cascade":
            # A cascade calls several models; the last tier is the one whose
            # residency the final decision came from, and it is the only model
            # this row can honestly attribute its residency class to.
            roles = self._cascade_roles(arm)
            model_id = roles["general"] or roles["orchestrator"] or roles["tiny"]
        if arm.kind in ("bounded", "unfiltered") and model_id:
            self.note_resident(model_id)

        started = time.perf_counter()
        attempts = 0
        error: str | None = None
        decision = None
        selected: str | None = None
        abstained = False
        invalid = False
        distribution = None
        confidence = None
        trajectory = None

        try:
            if arm.kind == "deterministic":
                if legal.deterministic_solution is not None:
                    selected, abstained = legal.deterministic_solution, False
                elif legal.is_empty:
                    abstained = True
                else:
                    abstained = True
                distribution = {i: (1.0 if i == selected else 0.0) for i in legal.ids()} or None
            elif arm.kind == "bounded":
                if arm.jev:
                    backend = self.jev()
                    if backend is None:
                        error = "jev_unavailable"
                        abstained = True
                    else:
                        attempts = 1
                        decision = backend.decide(
                            ToolDecisionRequest(state=fx.state, legal=legal, max_tokens=self.max_tokens)
                        )
                else:
                    attempts = 1
                    decision = self.backend(model_id).decide(
                        ToolDecisionRequest(state=fx.state, legal=legal, max_tokens=self.max_tokens)
                    )
            elif arm.kind == "unfiltered":
                attempts = 1
                decision = self.backend(model_id).decide(
                    ToolDecisionRequest(state=fx.state, legal=legal, max_tokens=self.max_tokens)
                )
            elif arm.kind == "cascade":
                cascade = self._cascade(arm)
                outcome = cascade.run(
                    CascadeContext(
                        state=fx.state,
                        graph=fx.graph,
                        granted_capabilities=fx.granted_capabilities,
                        authority=fx.authority,
                        budget_units=fx.budget_units,
                        facts=fx.facts,
                        satisfied=fx.satisfied,
                        max_tokens=self.max_tokens,
                    )
                )
                decision = outcome.decision
                selected = outcome.selected_action
                abstained = outcome.abstained
                invalid = bool(outcome.decision.invalid_call if outcome.decision else False)
                trajectory = {
                    "tier": outcome.tier,
                    "path": list(getattr(outcome, "path", []) or []),
                    "roles": self._cascade_roles(arm),
                }
            else:  # pragma: no cover - arm kinds are fixed above
                raise RuntimeError(f"unknown arm kind {arm.kind!r}")
        except Exception as exc:  # noqa: BLE001 - a failed call is a data point
            error = f"{type(exc).__name__}: {exc}"
            abstained = True

        latency_ms = (time.perf_counter() - started) * 1000.0
        if decision is not None:
            selected = decision.selected_action
            abstained = bool(decision.abstained)
            invalid = bool(decision.invalid_call)
            distribution = decision.distribution
            confidence = decision.confidence
        if distribution is None and selected is not None:
            distribution = {i: (1.0 if i == selected else 0.0) for i in legal.ids()}

        after = supervisor_status(self.base_url)
        resident_after = after.get("resident")
        supervisor = {}
        if decision is not None:
            supervisor = (decision.diagnostics or {}).get("supervisor") or {}
        if not supervisor:
            # deterministic / failed arms: no call was made, so no residency change
            supervisor = {"cold": False, "load_ms": 0.0, "wall_ms": latency_ms,
                          "resident_model": resident_after}

        cold_or_warm = self._classify(
            model_id=model_id,
            resident_before=resident_before,
            supervisor=supervisor,
            invocation_index=self._invocation_index,
            kind=arm.kind,
        )
        if resident_before == model_id:
            self._invocation_index += 1
        elif model_id is not None:
            self._invocation_index = 1
        self._resident = resident_after

        # Resident VRAM is stable across a residency, so sample it once per
        # residency rather than paying an nvidia-smi spawn on every call.
        vram = gpu_memory_used_mib() if cold_or_warm in ("cold_load", "model_swap", "already_resident") else None

        row = self._builder.build(
            fx=fx,
            arm=arm,
            features=features,
            legal_ids=legal.ids(),
            eliminated=[e.to_dict() for e in legal.eliminated],
            decision=decision,
            selected=selected,
            abstained=abstained,
            invalid=invalid,
            latency_ms=latency_ms,
            resident_before=resident_before,
            residency={"resident": resident_after},
            invocation_index=self._invocation_index,
            cold_or_warm=cold_or_warm,
            vram_mib=vram,
            attempts=attempts,
            error=error,
            distribution=distribution,
            confidence=confidence,
            max_tokens=self.max_tokens,
            trajectory=trajectory,
        )
        row["repetition"] = repetition
        row["deterministic_solution"] = legal.deterministic_solution
        return row

    @staticmethod
    def _cascade_roles(arm: Arm) -> dict[str, str | None]:
        """Map an arm's model list onto the cascade's four fixed tiers.

        The mapping is explicit rather than positional: ``compiler+jev+qwen4b``
        has no orchestrator, so the 4B must become the general tier instead of
        being dropped on the floor (which is exactly what a positional mapping
        did before this was written down).
        """
        present = set(arm.cascade)
        tiny = "hammer2.1_3b" if "hammer2.1_3b" in present else None
        if "nemotron_orchestrator_8b" in present:
            orchestrator = "nemotron_orchestrator_8b"
        elif "qwen3.5_4b" in present:
            orchestrator = "qwen3.5_4b"
        else:
            orchestrator = None
        if "qwen3.5_9b" in present:
            general = "qwen3.5_9b"
        elif "qwen3.5_4b" in present and orchestrator != "qwen3.5_4b":
            general = "qwen3.5_4b"
        else:
            general = None
        return {"tiny": tiny, "orchestrator": orchestrator, "general": general}

    def _cascade(self, arm: Arm) -> CognitionCascade:
        roles = self._cascade_roles(arm)
        return CognitionCascade(
            tiny=self.backend(roles["tiny"]) if roles["tiny"] else None,
            jev=self.jev() if arm.jev else None,
            orchestrator=self.backend(roles["orchestrator"]) if roles["orchestrator"] else None,
            general=self.backend(roles["general"]) if roles["general"] else None,
            policy=EscalationPolicy(EscalationThresholds()),
        )


def _unfiltered(fx: Fixture) -> Any:
    return compile_actions(
        graph=fx.graph,
        granted_capabilities=tuple(
            sorted({c for a in fx.graph.actions for c in a.required_capabilities})
        ),
        authority=ALL_RISKS,
        budget_units=10_000,
        facts=fx.facts,
        satisfied=fx.satisfied,
    )


def _try_jev() -> Any:
    try:
        from z0int.backends.registry import register_builtin_backends

        register_builtin_backends()
        from z0int.backends.nanojev import NanoJevBackend

        backend = NanoJevBackend.from_config()
        if backend.health(load=False).ready:
            from z0int.cognition.adapters.bounded import JevBoundedToolBackend

            return JevBoundedToolBackend(backend, backend_id="nanojev")
    except Exception:  # noqa: BLE001 - absence is a finding, not an error
        pass
    return None


# ---------------------------------------------------------------------------
# Cold-load probe
# ---------------------------------------------------------------------------


def run_cold_probe(
    campaign: Campaign, models: Sequence[str], repetitions: int
) -> list[dict[str, Any]]:
    """Deliberately evict, then measure the first call: the true load cost.

    A router that ignores load cost will happily bounce between two 4-8B models
    on a 12 GB card and pay a full load every turn.  These rows are the evidence
    for that cost, kept separate from the warm sweep so the two are never mixed
    into one distribution.
    """
    rows: list[dict[str, Any]] = []
    fx = campaign.fixtures[0]
    for rep in range(repetitions):
        for model in models:
            supervisor_release(campaign.base_url)
            time.sleep(1.0)
            arm = Arm(
                name="coldprobe.single_tool",
                kind="bounded",
                model=model,
            )
            row = campaign.measure(fx, arm, rep)
            row["arm"] = f"coldprobe+{model}"
            row["arm_kind"] = "cold_probe"
            row["repetition"] = rep
            campaign._write(row)
            rows.append(row)
    return rows


# ---------------------------------------------------------------------------
# Driver
# ---------------------------------------------------------------------------


def load_freeze(run_dir: Path) -> dict[str, Any]:
    return json.loads((run_dir / "baseline" / "freeze.json").read_text(encoding="utf-8"))


def provenance_from_freeze(freeze: Mapping[str, Any]) -> dict[str, Any]:
    models = {m["model_id"]: m for m in freeze.get("models", [])}
    repos = {r["label"]: r for r in freeze.get("repos", [])}
    fixture = next(
        (f for f in freeze.get("fixtures", []) if f.get("path") == FREEZE_FIXTURE_KEY),
        {},
    )
    return {
        "models": models,
        "repos": repos,
        "compiler_revision": (
            f"z0intelligence@{repos.get('z0intelligence', {}).get('sha', '?')[:12]}"
            f"+aodl@{repos.get('aodl', {}).get('sha', '?')[:12]}"
        ),
        "router_revision": (
            f"evolution-lab@{repos.get('evolution-lab', {}).get('sha', '?')[:12]}"
            "(router not in the measurement loop)"
        ),
        "fixture_sha256": fixture.get("sha256"),
        "freeze_run_id": freeze.get("run_id"),
    }


def verify_fixture(freeze: Mapping[str, Any], path: Path) -> str | None:
    """Refuse to measure against a fixture that is not the frozen revision."""
    expected = next(
        (f.get("sha256") for f in freeze.get("fixtures", []) if f.get("path") == FREEZE_FIXTURE_KEY),
        None,
    )
    if expected is None:
        return "fixture not present in the freeze"
    actual = hashlib.sha256(path.read_bytes()).hexdigest()
    if actual != expected:
        return f"fixture hash drift: frozen={expected[:16]} actual={actual[:16]}"
    return None


def build_schedule(
    *, fixtures: Sequence[Fixture], arms: Mapping[str, Arm], reps: int, arm_names: Sequence[str]
) -> list[dict[str, Any]]:
    """Order the work model-major so residency is used, not thrashed."""
    plan: list[dict[str, Any]] = []
    for rep in range(reps):
        for arm_name in arm_names:
            arm = arms[arm_name]
            for fx in fixtures:
                plan.append({"state_id": fx.fixture_id, "arm": arm_name, "repetition": rep,
                             "models": list(arm.models())})
    return plan


def main(argv: Iterable[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--run-id", required=True, help="frozen Phase 1B run id")
    ap.add_argument("--run-dir", default=None, help="override results/phase1b/<run-id>")
    ap.add_argument("--arms", action="append", default=[],
                    help="arm-set name (core|ladder|all) or explicit arm id; repeatable")
    ap.add_argument("--reps", type=int, default=3, help="repetitions per (state, arm)")
    ap.add_argument("--max-tokens", type=int, default=DEFAULT_MAX_TOKENS)
    ap.add_argument("--limit", type=int, default=None, help="stop after N new observations")
    ap.add_argument("--states", action="append", default=[], help="restrict to these fixture ids")
    ap.add_argument("--cold-probe", action="store_true",
                    help="measure deliberate cold loads instead of the warm sweep")
    ap.add_argument("--cold-models", action="append", default=[])
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(list(argv) if argv is not None else None)

    run_dir = Path(args.run_dir) if args.run_dir else (RESULTS_ROOT / args.run_id)
    freeze = load_freeze(run_dir)
    provenance = provenance_from_freeze(freeze)

    fixture_path = None
    # Resolve the bounded fixture through the freeze's recorded absolute path.
    record = next(
        (f for f in freeze["fixtures"] if f.get("path") == FREEZE_FIXTURE_KEY), None
    )
    if record is None:
        print("# frozen fixture record missing", file=sys.stderr)
        return 2
    fixture_path = Path(record["resolved"])
    drift = verify_fixture(freeze, fixture_path)
    if drift:
        print(f"# refusing to measure: {drift}", file=sys.stderr)
        return 3

    fixtures = load_fixtures(fixture_path)
    if args.states:
        wanted = set(args.states)
        fixtures = [f for f in fixtures if f.fixture_id in wanted]

    all_arms = _arms()
    selected: list[str] = []
    for spec in (args.arms or ["core"]):
        if spec in ARM_SETS:
            selected.extend(ARM_SETS[spec])
        elif spec in all_arms:
            selected.append(spec)
        else:
            print(f"# unknown arm or arm-set: {spec}", file=sys.stderr)
            return 2
    seen: set[str] = set()
    selected = [a for a in selected if not (a in seen or seen.add(a))]

    serving = load_serving()
    base_url = (serving.get("qwen3.5_4b").base_url if serving.get("qwen3.5_4b") else "http://127.0.0.1:11500")
    registry = LocalModelRegistry.from_environment()

    campaign = Campaign(
        registry=registry,
        fixtures=fixtures,
        arms=all_arms,
        out_dir=run_dir,
        run_id=args.run_id,
        base_url=base_url,
        max_tokens=args.max_tokens,
        provenance=provenance,
    )

    if args.dry_run:
        plan = build_schedule(fixtures=fixtures, arms=all_arms, reps=args.reps, arm_names=selected)
        print(json.dumps({
            "schema": SCHEDULE_SCHEMA,
            "run_id": args.run_id,
            "fixtures": len(fixtures),
            "arms": selected,
            "repetitions": args.reps,
            "planned_observations": len(plan),
            "cells": len(fixtures) * len(selected),
        }, indent=2))
        return 0

    if args.cold_probe:
        models = args.cold_models or [m for m in registry.served() if m in ("qwen3.5_4b",)]
        rows = run_cold_probe(campaign, models, args.reps)
        print(f"# cold probe: {len(rows)} observations -> {campaign.observations_path}",
              file=sys.stderr)
        return 0

    print(
        f"# run={args.run_id} fixtures={len(fixtures)} arms={len(selected)} reps={args.reps}",
        file=sys.stderr,
    )
    written = 0
    started = time.time()
    # Arm-major ordering: every repetition of one arm runs back to back.  That
    # keeps the three draws under one residency (one cold load instead of three)
    # and, more importantly, keeps them adjacent in time, so "variance" is the
    # model's variance rather than the machine's drift between passes.
    for arm_name in selected:
        arm = all_arms[arm_name]
        for rep in range(args.reps):
            for fx in fixtures:
                key = (fx.fixture_id, arm_name, rep)
                if key in campaign._seen:
                    continue
                if args.limit is not None and written >= args.limit:
                    print(f"# limit reached ({args.limit})", file=sys.stderr)
                    _finalise(campaign, selected, args.reps)
                    return 0
                row = campaign.measure(fx, arm, rep)
                campaign._write(row)
                campaign._seen.add(key)
                written += 1
                print(
                    f"#   [{written}] {arm_name} {fx.fixture_id} rep={rep} "
                    f"{row['cold_or_warm']} {row['total_ms']:.0f}ms "
                    f"correct={row['correct']}",
                    file=sys.stderr,
                )
    _finalise(campaign, selected, args.reps)
    print(
        f"# wrote {written} observations in {time.time() - started:.0f}s -> "
        f"{campaign.observations_path}",
        file=sys.stderr,
    )
    return 0


def _finalise(campaign: Campaign, arms: Sequence[str], reps: int) -> None:
    summary = {
        "schema": "z0int.phase1b.densify_run.v1",
        "run_id": campaign.run_id,
        "arms": list(arms),
        "repetitions": reps,
        "max_tokens": campaign.max_tokens,
        "observations_path": str(campaign.observations_path),
        "n_observations": sum(1 for _ in campaign.observations_path.open(encoding="utf-8"))
        if campaign.observations_path.is_file() else 0,
        "finished_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
    }
    (campaign.out_dir / "densify_summary.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


if __name__ == "__main__":
    raise SystemExit(main())
