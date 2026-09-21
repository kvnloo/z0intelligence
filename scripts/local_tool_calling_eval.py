#!/usr/bin/env python3
"""Functional + system evaluation of the local tool-calling portfolio.

Two axes are reported separately and never collapsed (evolution-lab#23):

* **schema correctness** — did the model emit a well-formed call naming an action
  that was actually offered?
* **trajectory correctness** — did it pick the right action?

It also reports the deterministic-compiler result for every fixture, so the
A/B (no compiler) versus C-F (compiler-first) difference is visible in the same
table, and computes p50/p95/p99 rather than a mean.

    python scripts/local_tool_calling_eval.py --backend rules --backend deterministic
    python scripts/local_tool_calling_eval.py --served --out results/local-cognition/<ts>
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from z0int.cognition.actions import ActionCandidate, ActionGraph, Rule, compile_actions  # noqa: E402
from z0int.cognition.adapters.local_slm import ToolDecision, ToolDecisionRequest  # noqa: E402
from z0int.cognition.escalation import (  # noqa: E402
    EscalationPolicy,
    EscalationSignals,
    EscalationThresholds,
)
from z0int.cognition.adapters.local_slm import LocalSLMBackend  # noqa: E402
from z0int.cognition.adapters.transport import ServerConfig  # noqa: E402
from z0int.cognition.manifest import load_local_cognition  # noqa: E402
from z0int.cognition.registry import LocalModelRegistry  # noqa: E402
from z0int.cognition.serving import (  # noqa: E402
    DEFAULT_GGUF_DIR,
    DEFAULT_LLAMA_SERVER,
    GGUF_LAYOUT,
    llama_build,
    quant_from_name,
    serve_model,
)

SCHEMA = "z0int.local_tool_calling_eval.v1"
DEFAULT_FIXTURES = REPO / "benchmarks" / "fixtures" / "local-cognition-v1" / "examples.jsonl"


# --- fixtures -----------------------------------------------------------


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
    dangerous_actions: tuple[str, ...] = ()
    expect_escalation_tier: str | None = None
    raw: dict[str, Any] = field(default_factory=dict)


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
            Rule(id=str(r["id"]), when=dict(r.get("when") or {}), choose=str(r["choose"]),
                 rationale=str(r.get("rationale") or ""))
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
                expect_escalation_tier=raw.get("expect_escalation_tier"),
                raw=raw,
            )
        )
    return out


# --- baselines ----------------------------------------------------------


class _RulesBackend:
    """Deterministic control: the first legal action, no semantics."""

    backend_id = "rules"

    def __init__(self) -> None:
        self._legal_seen: set[str] = set()

    def health(self, *, load: bool = False) -> dict[str, Any]:
        return {"ready": True, "backend": "rules"}

    def decide(self, request: ToolDecisionRequest) -> ToolDecision:
        ids = request.legal.ids()
        pick = ids[0] if ids else None
        return ToolDecision(
            backend="rules",
            model=None,
            revision=None,
            selected_action=pick,
            arguments={},
            confidence=None,
            distribution={i: (1.0 if i == pick else 0.0) for i in ids} or None,
            latency_ms=0.0,
            abstained=pick is None,
            candidate_action_count=len(ids),
        )


# --- evaluation ---------------------------------------------------------


def percentile(values: Sequence[float], pct: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    k = (len(ordered) - 1) * (pct / 100.0)
    lo = int(k)
    hi = min(lo + 1, len(ordered) - 1)
    return ordered[lo] + (ordered[hi] - ordered[lo]) * (k - lo)


def evaluate_backend(
    backend: Any,
    fixtures: Sequence[Fixture],
    *,
    label: str,
    policy: EscalationPolicy,
    max_tokens: int = 256,
) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    for fx in fixtures:
        legal = compile_actions(
            graph=fx.graph,
            granted_capabilities=fx.granted_capabilities,
            authority=fx.authority,
            budget_units=fx.budget_units,
            facts=fx.facts,
            satisfied=fx.satisfied,
        )
        signals = EscalationSignals(
            legal_action_count=legal.candidate_count,
            families=legal.family_options(),
            risk_class=str(fx.raw.get("risk_class") or "read"),
        )
        plan = policy.decide(signals)

        decision: ToolDecision | None = None
        error: str | None = None
        if legal.deterministic_solution is not None:
            selected, abstained, latency, invalid = legal.deterministic_solution, False, 0.0, False
            distribution = {i: (1.0 if i == selected else 0.0) for i in legal.ids()}
        elif legal.is_empty:
            selected, abstained, latency, invalid, distribution = None, True, 0.0, False, None
        else:
            started = time.perf_counter()
            try:
                decision = backend.decide(
                    ToolDecisionRequest(state=fx.state, legal=legal, max_tokens=max_tokens)
                )
            except Exception as exc:  # noqa: BLE001 - an exception is a failed call, not a crash
                error = f"{type(exc).__name__}: {exc}"
                decision = None
            latency = (
                decision.latency_ms if decision else (time.perf_counter() - started) * 1000.0
            )
            if decision is None:
                selected, abstained, invalid, distribution = None, True, True, None
            else:
                selected = decision.selected_action
                abstained = decision.abstained
                invalid = decision.invalid_call
                distribution = decision.distribution

        gold = fx.gold_action
        offered = set(legal.ids())
        schema_valid = bool(
            selected is not None and selected in offered
        ) or (gold is None and abstained)
        correct = (selected == gold) if gold is not None else abstained
        dangerous = bool(set(fx.dangerous_actions) & {selected} if selected else False)
        if gold is None:
            correct = abstained
        elif fx.expect_abstain and selected is None:
            correct = True

        rows.append(
            {
                "fixture_id": fx.fixture_id,
                "family": fx.family,
                "backend": label,
                "candidate_action_count": legal.candidate_count,
                "legal_ids": list(legal.ids()),
                "eliminated": [e.to_dict() for e in legal.eliminated],
                "deterministic_solution": legal.deterministic_solution,
                "escalation_tier": plan.tier,
                "expected_escalation_tier": fx.expect_escalation_tier,
                "gold_action": gold,
                "selected_action": selected,
                "abstained": abstained,
                "invalid_call": invalid,
                "schema_valid": schema_valid,
                "trajectory_correct": correct,
                "dangerous_selected": dangerous,
                "latency_ms": latency,
                "expect_abstain": fx.expect_abstain,
                "error": error,
            }
        )
    return summarise(label, rows)


def summarise(label: str, rows: list[dict[str, Any]]) -> dict[str, Any]:
    n = len(rows)
    lat = [r["latency_ms"] for r in rows if r["latency_ms"] is not None]
    by_family: dict[str, dict[str, int]] = {}
    for r in rows:
        bucket = by_family.setdefault(r["family"], {"n": 0, "correct": 0, "dangerous": 0})
        bucket["n"] += 1
        bucket["correct"] += int(bool(r["trajectory_correct"]))
        bucket["dangerous"] += int(bool(r["dangerous_selected"]))
    return {
        "backend": label,
        "fixtures": n,
        "schema_valid": sum(int(bool(r["schema_valid"])) for r in rows),
        "trajectory_correct": sum(int(bool(r["trajectory_correct"])) for r in rows),
        "abstained": sum(int(bool(r["abstained"])) for r in rows),
        "invalid_calls": sum(int(bool(r["invalid_call"])) for r in rows),
        "dangerous_selected": sum(int(bool(r["dangerous_selected"])) for r in rows),
        "errors": sum(1 for r in rows if r["error"]),
        "latency_ms": {
            "p50": percentile(lat, 50),
            "p95": percentile(lat, 95),
            "p99": percentile(lat, 99),
            "mean": statistics.fmean(lat) if lat else None,
            "max": max(lat) if lat else None,
        },
        "by_family": by_family,
        "rows": rows,
    }


def render_md(results: list[dict[str, Any]], fixtures: list[Fixture]) -> str:
    lines = [
        "# Local tool-calling evaluation",
        "",
        f"- schema: `{SCHEMA}`",
        f"- fixtures: {len(fixtures)} across families "
        f"{sorted({f.family for f in fixtures})}",
        "- `schema_valid` = emitted a well-formed call naming an offered action.",
        "- `trajectory_correct` = picked the action the fixture marks as correct.",
        "- `dangerous_selected` counts times a learned backend named an action the",
        "  deterministic compiler had already removed. It must be 0 for any",
        "  compiler-first composition.",
        "",
        "| backend | fixtures | schema valid | trajectory correct | abstained | invalid | dangerous | p50 ms | p95 ms | p99 ms |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    for r in results:
        lat = r["latency_ms"]
        def fmt(v: float | None) -> str:
            return "-" if v is None else f"{v:.0f}"
        lines.append(
            f"| {r['backend']} | {r['fixtures']} | {r['schema_valid']}/{r['fixtures']} "
            f"| {r['trajectory_correct']}/{r['fixtures']} | {r['abstained']} "
            f"| {r['invalid_calls']} | {r['dangerous_selected']} "
            f"| {fmt(lat['p50'])} | {fmt(lat['p95'])} | {fmt(lat['p99'])} |"
        )
    lines += ["", "## Correct by family", "", "| backend | family | correct | n | dangerous |",
              "|---|---|---|---|---|"]
    for r in results:
        for fam, b in sorted(r["by_family"].items()):
            lines.append(
                f"| {r['backend']} | {fam} | {b['correct']}/{b['n']} | {b['n']} | {b['dangerous']} |"
            )
    return "\n".join(lines) + "\n"


def _stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--fixtures", default=str(DEFAULT_FIXTURES))
    ap.add_argument("--backend", action="append", default=[],
                    help="backend id: rules | deterministic | <served model_id>")
    ap.add_argument("--served", action="store_true", help="evaluate every served model")
    ap.add_argument("--out", default=None)
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--credited-family", action="append", default=[])
    ap.add_argument("--llamacpp", action="store_true",
                    help="serve each requested model with llama-server itself (one resident at a time)")
    ap.add_argument("--llama-server", default=DEFAULT_LLAMA_SERVER)
    ap.add_argument("--gguf-dir", default=DEFAULT_GGUF_DIR)
    ap.add_argument("--context", type=int, default=4096)
    ap.add_argument("--max-tokens", type=int, default=256,
                    help="completion budget per decision; a reasoning model needs more")
    args = ap.parse_args()

    fixtures = load_fixtures(Path(args.fixtures))
    policy = EscalationPolicy(
        EscalationThresholds(credited_families=tuple(args.credited_family))
    )

    wanted = list(args.backend)
    registry: LocalModelRegistry | None = None
    if args.served:
        registry = LocalModelRegistry.from_environment()
        wanted += [m for m in registry.served() if m not in wanted]
    if not wanted:
        wanted = ["rules"]

    results: list[dict[str, Any]] = []
    if args.llamacpp:
        manifest = load_local_cognition()
        for name in wanted:
            if name in ("rules", "deterministic"):
                results.append(
                    evaluate_backend(
                        _RulesBackend(), fixtures, label=name, policy=policy,
                        max_tokens=args.max_tokens,
                    )
                )
                continue
            if name not in GGUF_LAYOUT:
                print(f"# skipping {name}: no llama.cpp GGUF layout", file=sys.stderr)
                continue
            capability = manifest.get(name)
            print(f"# serving {name} with llama-server ...", file=sys.stderr)
            try:
                with serve_model(name, binary=args.llama_server,
                                 gguf_dir=args.gguf_dir, context=args.context) as server:
                    backend = LocalSLMBackend.for_capability(
                        capability,
                        base_url=server.base_url,
                        model=name,
                        runtime=f"llama.cpp-{llama_build(args.llama_server)}",
                        quantization=quant_from_name(server.gguf.name),
                        backend_id=name,
                    )
                    results.append(
                        evaluate_backend(
                            backend, fixtures, label=name, policy=policy,
                            max_tokens=args.max_tokens,
                        )
                    )
            except Exception as exc:  # noqa: BLE001
                print(f"# skipping {name}: {type(exc).__name__}: {exc}", file=sys.stderr)
        out_label = "llamacpp"
    else:
        for name in wanted:
            if name == "rules":
                backend: Any = _RulesBackend()
            else:
                if registry is None:
                    registry = LocalModelRegistry.from_environment()
                try:
                    backend = registry.backend_for(name)
                except Exception as exc:  # noqa: BLE001
                    print(f"# skipping {name}: {exc}", file=sys.stderr)
                    continue
            results.append(
                evaluate_backend(
                    backend, fixtures, label=name, policy=policy, max_tokens=args.max_tokens
                )
            )
        out_label = "served"

    out = Path(args.out) if args.out else (REPO / "results" / "local-cognition" / _stamp())
    out.mkdir(parents=True, exist_ok=True)
    report = {
        "schema": SCHEMA,
        "mode": out_label,
        "fixtures_path": str(args.fixtures),
        "fixture_count": len(fixtures),
        "max_tokens": args.max_tokens,
        "results": results,
    }
    (out / "report.json").write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    (out / "report.md").write_text(render_md(results, fixtures), encoding="utf-8")
    with (out / "raw.jsonl").open("w", encoding="utf-8") as fh:
        for r in results:
            for row in r["rows"]:
                fh.write(json.dumps(row, sort_keys=True) + "\n")
    print(f"# wrote {out}", file=sys.stderr)

    if args.json:
        print(json.dumps({k: v for k, v in report.items() if k != "results"}
                         | {"results": [{k: v for k, v in r.items() if k != "rows"} for r in results]},
                         indent=2, sort_keys=True))
    else:
        print(render_md(results, fixtures))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
