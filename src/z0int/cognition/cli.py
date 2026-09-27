"""``z0int cognition`` — the local cognition portfolio from the command line.

Thin wrappers only: all logic lives in :mod:`z0int.cognition`.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from .actions import ActionCandidate, ActionGraph, Rule, compile_actions
from .cascade import CascadeContext, CognitionCascade
from .candidates import build_inventory, load_pi_ai_catalog
from .escalation import EscalationPolicy, EscalationThresholds
from .manifest import (
    ROLES,
    assert_selection_is_evidence_based,
    load_local_cognition,
)
from .registry import LocalModelRegistry
from .serving import DEFAULT_GGUF_DIR, DEFAULT_LLAMA_SERVER
from .surface import DecisionSurface, SurfaceThresholds


def _print(payload: Any, *, as_json: bool) -> None:
    if as_json:
        print(json.dumps(payload, indent=2, sort_keys=True, default=str))
        return
    if isinstance(payload, str):
        print(payload)
        return
    print(json.dumps(payload, indent=2, sort_keys=True, default=str))


# --- individual commands ------------------------------------------------


def _cmd_manifest(args: argparse.Namespace) -> int:
    manifest = load_local_cognition()
    if args.json:
        _print(manifest.to_dict(), as_json=True)
        return 0
    print(f"schema      {manifest.schema}")
    print(f"revision    {manifest.revision}")
    print(f"machines    {', '.join(manifest.machines) or '-'}")
    print(f"models      {len(manifest.models)}")
    for model_id, model in manifest.models.items():
        state = "measured" if model.measured else "not-measured"
        claims = len(model.source_reported_benchmarks)
        print(
            f"  {model_id:<28} {model.parameter_count or '?':>6}  "
            f"roles={','.join(model.role_tags) or '-':<52} {state:<13} "
            f"claims={claims} commercial={model.commercial_use}"
        )
    return 0


def _cmd_roles(args: argparse.Namespace) -> int:
    manifest = load_local_cognition()
    offenders = assert_selection_is_evidence_based(manifest)
    rows = []
    for role in ROLES:
        default = manifest.role_default(role)
        measured = [m.model_id for m in manifest.selectable(role, require_measured=True)]
        rows.append(
            {
                "role": role,
                "role_default": default,
                "evidence_status": (
                    "unset"
                    if default is None
                    else ("missing_local_evidence" if default in offenders else "ok")
                ),
                "measured_candidates": measured,
            }
        )
    if args.json:
        _print({"roles": rows, "needs_evidence": offenders}, as_json=True)
        return 0
    for row in rows:
        print(
            f"{row['role']:<26} default={str(row['role_default']):<28} "
            f"{row['evidence_status']:<24} measured={','.join(row['measured_candidates']) or '-'}"
        )
    if offenders:
        print(
            f"\nWARNING: role defaults without local evidence: {', '.join(offenders)}\n"
            "Run `z0int cognition probe --all --write-manifest` on the target machine.",
            file=sys.stderr,
        )
    return 0


def _cmd_serving(args: argparse.Namespace) -> int:
    registry = LocalModelRegistry.from_environment()
    served = registry.served()
    if not served:
        _print(
            {
                "served": [],
                "hint": "write ~/.z0int/config/serving.json or export Z0INT_LOCAL_BASE_URL",
            },
            as_json=args.json,
        )
        return 1
    if args.json:
        _print({"served": list(served), "health": registry.health()}, as_json=True)
        return 0
    for model_id, info in registry.health().items():
        ready = "ready" if info.get("ready") else "DOWN"
        print(f"{model_id:<28} {ready:<6} {info.get('detail', '')}")
    return 0


def _cmd_candidates(args: argparse.Namespace) -> int:
    """Show the shared capability inventory (local manifest + pi-ai catalog)."""
    manifest = load_local_cognition()
    catalog = load_pi_ai_catalog(args.catalog)
    inventory = build_inventory(manifest=manifest, catalog=catalog)
    if args.json:
        _print(inventory.to_dict(), as_json=True)
        return 0
    print(f"providers   {', '.join(inventory.providers()) or '-'}")
    print(f"candidates  {len(inventory)}")
    print(
        f"catalog     {len(catalog)} payload(s) from "
        f"{args.catalog or '$Z0INT_PI_AI_CATALOG'}"
    )
    for candidate in inventory:
        print(
            f"  {candidate.candidate_id:<44} {candidate.quality_class:<9} "
            f"risk<={candidate.max_risk_class:<11} cost={candidate.cost_class:<8} "
            f"tiers={','.join(candidate.serves_tiers) or '-'}"
        )
    return 0


def _cmd_serve(args: argparse.Namespace) -> int:
    """One production endpoint: llama.cpp supervisor, one resident model."""
    from .server import ModelSupervisor, serve_forever

    supervisor = ModelSupervisor(
        binary=args.llama_server or DEFAULT_LLAMA_SERVER,
        gguf_dir=args.gguf_dir or DEFAULT_GGUF_DIR,
        default_context=args.context,
        idle_unload_s=args.idle_unload,
    )
    if args.print_serving_map:
        endpoints = [
            {
                "model_id": m,
                "base_url": f"http://{args.host}:{args.port}",
                "served_model": m,
                "runtime": supervisor.runtime,
                "quantization": None,
                "backend_id": m,
            }
            for m in supervisor.known_models()
        ]
        _print({"schema": "z0int.serving.v1", "endpoints": endpoints}, as_json=True)
        return 0
    serve_forever(host=args.host, port=args.port, supervisor=supervisor)
    return 0


def _cmd_probe(args: argparse.Namespace) -> int:
    """Delegate to scripts/probe_local_cognition.py so there is one implementation."""
    import runpy

    script = Path(__file__).resolve().parents[3] / "scripts" / "probe_local_cognition.py"
    if not script.is_file():
        print(f"probe script missing: {script}", file=sys.stderr)
        return 2
    argv = [str(script)]
    for model in args.model or []:
        argv += ["--model", model]
    if args.all:
        argv.append("--all")
    argv += ["--context", str(args.context)]
    if args.out:
        argv += ["--out", args.out]
    if args.json:
        argv.append("--json")
    if args.write_manifest:
        argv.append("--write-manifest")
    old = sys.argv
    sys.argv = argv
    try:
        runpy.run_path(str(script), run_name="__main__")
    except SystemExit as exc:
        return int(exc.code or 0)
    finally:
        sys.argv = old
    return 0


def _graph_from_payload(payload: dict[str, Any]) -> ActionGraph:
    actions = []
    for raw in payload.get("actions") or []:
        actions.append(
            ActionCandidate(
                action_id=str(raw["action_id"]),
                kind=str(raw.get("kind") or "tool"),
                description=str(raw.get("description") or raw["action_id"]),
                tool=raw.get("tool") or raw["action_id"],
                family=raw.get("family"),
                requires=tuple(raw.get("requires") or ()),
                provides=tuple(raw.get("provides") or ()),
                required_capabilities=tuple(raw.get("required_capabilities") or ()),
                risk_class=str(raw.get("risk_class") or "read"),
                cost_units=int(raw.get("cost_units") or 1),
                parallel_safe=bool(raw.get("parallel_safe", True)),
                arguments_schema=raw.get("arguments_schema"),
                escalates_to=raw.get("escalates_to"),
            )
        )
    rules = tuple(
        Rule(
            id=str(r["id"]),
            when=dict(r.get("when") or {}),
            choose=str(r["choose"]),
            rationale=str(r.get("rationale") or ""),
        )
        for r in payload.get("rules") or []
    )
    return ActionGraph(actions=tuple(actions), rules=rules)


def _cmd_compile(args: argparse.Namespace) -> int:
    payload = json.loads(Path(args.input).read_text(encoding="utf-8"))
    legal = compile_actions(
        graph=_graph_from_payload(payload),
        granted_capabilities=tuple(payload.get("granted_capabilities") or ()),
        authority=tuple(payload.get("authority") or ("read",)),
        budget_units=int(payload.get("budget_units") or 0),
        facts=payload.get("facts") or {},
        satisfied=tuple(payload.get("satisfied") or ()),
    )
    _print(legal.to_dict(), as_json=args.json)
    return 0


def _cmd_decide(args: argparse.Namespace) -> int:
    payload = json.loads(Path(args.input).read_text(encoding="utf-8"))
    registry = LocalModelRegistry.from_environment()
    thresholds = EscalationThresholds(
        credited_families=tuple(payload.get("credited_families") or ())
    )
    # Candidate surfaces are opt-in: without --catalog/--surface the cascade
    # behaves exactly as before (the escalation policy alone picks the tier).
    surface = None
    if args.surface or args.catalog:
        inventory = build_inventory(
            manifest=load_local_cognition(), catalog=load_pi_ai_catalog(args.catalog)
        )
        surface = DecisionSurface(
            inventory.candidates,
            thresholds=SurfaceThresholds(max_cost_class=args.max_cost_class),
        )
    cascade = CognitionCascade(
        tiny=registry.role_backend("tiny_action_specialist") if args.use_models else None,
        jev=None,
        orchestrator=registry.role_backend("semantic_orchestrator") if args.use_models else None,
        general=registry.role_backend("general_local_fallback") if args.use_models else None,
        policy=EscalationPolicy(thresholds),
        surface=surface,
    )
    shadow: list[tuple[str, Any]] = []
    if args.shadow:
        for model_id in args.shadow:
            try:
                shadow.append((model_id, registry.backend_for(model_id)))
            except Exception as exc:  # noqa: BLE001
                print(f"# shadow {model_id} unavailable: {exc}", file=sys.stderr)
    context = CascadeContext(
        state=str(payload.get("state") or ""),
        graph=_graph_from_payload(payload),
        granted_capabilities=tuple(payload.get("granted_capabilities") or ()),
        authority=tuple(payload.get("authority") or ("read",)),
        budget_units=int(payload.get("budget_units") or 0),
        facts=payload.get("facts") or {},
        satisfied=tuple(payload.get("satisfied") or ()),
        objective=payload.get("objective"),
        risk_class=str(payload.get("risk_class") or "read"),
        constraints=payload.get("constraints") or {},
        latency_budget_ms=payload.get("latency_budget_ms"),
        estimated_candidate_latency_ms=payload.get("estimated_candidate_latency_ms"),
        verification_required=bool(payload.get("verification_required", False)),
        novel_tool_combination=bool(payload.get("novel_tool_combination", False)),
        historical_failure_family=bool(payload.get("historical_failure_family", False)),
        state_novelty=payload.get("state_novelty"),
        capability_required=payload.get("capability_required"),
    )
    outcome = cascade.run(context, shadow=tuple(shadow))
    _print(outcome.to_dict(), as_json=args.json)
    return 0 if not outcome.abstained else 1


# --- registration -------------------------------------------------------


def add_cognition_parser(sub: argparse._SubParsersAction) -> None:  # type: ignore[type-arg]
    co = sub.add_parser(
        "cognition",
        help="Local cognition portfolio: capability manifest, compiled cascade, serving probe",
    )
    co_sub = co.add_subparsers(dest="cognition_cmd", required=True)

    cm = co_sub.add_parser("manifest", help="Show the versioned model capability manifest")
    cm.add_argument("--json", action="store_true")

    ccand = co_sub.add_parser(
        "candidates",
        help="Shared capability inventory: local manifest + pi-ai provider catalog",
    )
    ccand.add_argument("--catalog", default=None,
                       help="pi-ai catalog JSON file or directory (default: $Z0INT_PI_AI_CATALOG)")
    ccand.add_argument("--json", action="store_true")

    cr = co_sub.add_parser("roles", help="Role -> model assignment with local-evidence status")
    cr.add_argument("--json", action="store_true")

    cs = co_sub.add_parser("serving", help="Health of the locally served endpoints")
    cs.add_argument("--json", action="store_true")

    cse = co_sub.add_parser(
        "serve",
        help="Run the llama.cpp model supervisor: one OpenAI endpoint, one resident model",
    )
    cse.add_argument("--host", default="127.0.0.1")
    cse.add_argument("--port", type=int, default=11500)
    cse.add_argument("--llama-server", default=None)
    cse.add_argument("--gguf-dir", default=None)
    cse.add_argument("--context", type=int, default=4096)
    cse.add_argument("--idle-unload", type=float, default=300.0)
    cse.add_argument("--print-serving-map", action="store_true",
                     help="print the serving.json endpoints for this supervisor and exit")

    cp = co_sub.add_parser("probe", help="Measure real VRAM/TTFT/tok-s on this machine")
    cp.add_argument("--model", action="append", default=[])
    cp.add_argument("--all", action="store_true")
    cp.add_argument("--context", type=int, default=4096)
    cp.add_argument("--out", default=None)
    cp.add_argument("--write-manifest", action="store_true")
    cp.add_argument("--json", action="store_true")

    cc = co_sub.add_parser("compile", help="Deterministic legal-action compile (no model)")
    cc.add_argument("--input", required=True, help="Request JSON with actions/rules/authority")
    cc.add_argument("--json", action="store_true", default=True)

    cd = co_sub.add_parser("decide", help="Run the compiled cascade (deterministic -> learned)")
    cd.add_argument("--input", required=True)
    cd.add_argument("--use-models", action="store_true", help="Allow learned tiers to run")
    cd.add_argument("--shadow", action="append", default=[],
                    help="model_id to run in shadow (recorded, never executed)")
    cd.add_argument("--surface", action="store_true",
                    help="Enforce the candidate surface (quality/risk gating, fail closed)")
    cd.add_argument("--catalog", default=None,
                    help="pi-ai catalog JSON file or directory (default: $Z0INT_PI_AI_CATALOG)")
    cd.add_argument("--max-cost-class", default=None,
                    help="Fail closed before candidates costlier than this class")
    cd.add_argument("--json", action="store_true", default=True)


def cmd_cognition(args: argparse.Namespace) -> int:
    handlers = {
        "manifest": _cmd_manifest,
        "candidates": _cmd_candidates,
        "roles": _cmd_roles,
        "serving": _cmd_serving,
        "serve": _cmd_serve,
        "probe": _cmd_probe,
        "compile": _cmd_compile,
        "decide": _cmd_decide,
    }
    handler = handlers.get(args.cognition_cmd)
    if handler is None:
        print(f"unknown cognition command: {args.cognition_cmd}", file=sys.stderr)
        return 2
    return handler(args)


__all__ = ["add_cognition_parser", "cmd_cognition"]
