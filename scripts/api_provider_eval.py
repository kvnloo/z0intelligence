#!/usr/bin/env python3
"""Run the local-cognition decision battery against API-served models.

WHY THIS EXISTS
    `local_tool_calling_eval.py` evaluates *local* models (llama-server, one
    resident at a time, 12 GB card). This runs the SAME 28 fixtures, the SAME
    graders, and the SAME report shape against API providers, so a hosted model
    and a local one land in one comparable table.

    The cohort is declared in `benchmarks/manifests/api-provider-registry.json`.
    Adding a model there is the only edit needed to include it in a run — that is
    the point, so "any new model we run" is a one-line change, not a patch.

WHAT IT MEASURES
    Two axes, never collapsed (evolution-lab#23):
      * schema correctness    — well-formed call naming an action actually offered
      * trajectory correctness — picked the right action
    Plus the safety axes the battery already records: `abstained`,
    `invalid_call`, and `dangerous_selected` (the model named an action it was
    not authorised to take). `dangerous_selected` is the failure that matters
    most, because it is the one a confident-but-wrong model produces.

SECRETS
    Keys are read from the environment ONLY, by the names in the registry
    (`GROQ_API_KEY`, `CEREBRAS_API_KEY`, ...). This script never reads, writes,
    prints, or logs a credential file. If a key is absent it reports the name and
    skips that provider rather than substituting anything. Populate the
    environment however your host already does that.

COST
    Every call here is a real billed or quota-metered call. The registry records
    each provider's `cohort`: `renewable` (free plan, resets daily),
    `trial-credit` (draws down a finite one-time grant), `subscription-exempt`
    (does not consume Paygo/trial credit), or `paid-api`. Use `--dry-run` first.

OUTPUT
    Create-only, one directory per model+rep, matching the existing layout:
        results/local-cognition/<stamp>-api-<slug>/{report.json,report.md,raw.jsonl}
    With more than one model it also writes `comparison.md` / `comparison.json`
    into the same stamp directory. An existing path is never overwritten.

USAGE
    python scripts/api_provider_eval.py --dry-run
    python scripts/api_provider_eval.py --provider groq --model openai/gpt-oss-120b --reps 2
    python scripts/api_provider_eval.py --all --max-tokens 700
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))
sys.path.insert(0, str(REPO / "scripts"))

from z0int.cognition.adapters.dialects import DIALECTS  # noqa: E402
from z0int.cognition.adapters.local_slm import LocalSLMBackend  # noqa: E402
from z0int.cognition.adapters.transport import (  # noqa: E402
    OpenAICompatTransport,
    ServerConfig,
    TransportError,
)
from z0int.cognition.escalation import (  # noqa: E402
    EscalationPolicy,
    EscalationThresholds,
)
from z0int.cognition.manifest import ModelCapability  # noqa: E402
from local_tool_calling_eval import (  # noqa: E402
    DEFAULT_FIXTURES,
    SCHEMA,
    evaluate_backend,
    load_fixtures,
    render_md,
)

REGISTRY = REPO / "benchmarks" / "manifests" / "api-provider-registry.json"

# Cloudflare fronts both providers and rejects the stock Python-urllib signature
# with "HTTP 403 error code: 1010". The default transport sends exactly that, so
# EVERY call fails at the edge -- and the adapter's contract ("any transport or
# parse failure fails open as abstained=True") turns that outage into a report
# that looks like a model which refused to choose. A real User-Agent returns 200.
DEFAULT_USER_AGENT = "z0int-api-eval/1.0 (+https://github.com/kvnloo/z0intelligence)"
COHORT_MEANING = {
    "renewable": "free plan, resets daily",
    "trial-credit": "draws down a finite one-time grant",
    "subscription-exempt": "does not consume paygo/trial credit",
    "paid-api": "billed per token",
}


def slugify(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")


def stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def load_registry(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def resolve_wanted(registry: dict[str, Any], providers: Sequence[str],
                   models: Sequence[str], run_all: bool) -> list[tuple[str, dict[str, Any]]]:
    """-> ordered [(provider_name, model_entry)], deduplicated, registry-validated."""
    out: list[tuple[str, dict[str, Any]]] = []
    seen: set[tuple[str, str]] = set()

    def add(provider: str, model_id: str) -> None:
        entry = registry["providers"].get(provider)
        if entry is None:
            raise SystemExit(f"unknown provider: {provider} (have: {', '.join(registry['providers'])})")
        match = next((m for m in entry["models"] if m["id"] == model_id), None)
        if match is None:
            known = ", ".join(m["id"] for m in entry["models"])
            raise SystemExit(f"unknown model for {provider}: {model_id} (have: {known})")
        key = (provider, model_id)
        if key not in seen:
            seen.add(key)
            out.append((provider, {**match, "_provider": provider,
                                   "_base_url": entry["base_url"],
                                   "_api_key_env": entry["api_key_env"],
                                   "_cohort": entry["cohort"],
                                   "_dialect": match.get("dialect") or entry.get("dialect") or "qwen"}))

    if run_all:
        for provider, entry in registry["providers"].items():
            for model in entry["models"]:
                add(provider, model["id"])
    for spec in models:
        if "/" in spec and spec.split("/", 1)[0] in registry["providers"]:
            provider, model_id = spec.split("/", 1)
            add(provider, model_id)
            continue
        targets = [p for p in providers] or list(registry["providers"])
        for provider in targets:
            entry = registry["providers"][provider]
            hits = [m for m in entry["models"] if m["id"] == spec or m["label"] == spec]
            if hits:
                add(provider, hits[0]["id"])
                break
        else:
            raise SystemExit(f"could not resolve model {spec!r}; use provider/model or add it to the registry")
    for provider in providers:
        entry = registry["providers"].get(provider)
        if entry is None:
            raise SystemExit(f"unknown provider: {provider}")
        if not any(p == provider for p, _ in out):
            for model in entry["models"]:
                add(provider, model["id"])
    return out


def build_backend(model: dict[str, Any], api_key: str, backend_id: str,
                  dialect_override: str | None = None) -> LocalSLMBackend:
    capability = ModelCapability(
        model_id=model["id"],
        # Must come from the manifest ROLES vocabulary: a hosted general model
        # acting as a bounded chooser over a compiled action graph is a
        # general_function_caller, reached as a remote_specialist_fallback.
        role_tags=("general_function_caller", "remote_specialist_fallback"),
        supported_runtimes=("api",),
        tested_quantization="api",
        tested_context=model.get("context"),
        structured_output_support="native",
        supports_abstention=True,
        promotion_state="candidate",
        notes=model.get("notes"),
    )
    # The dialect is load-bearing, not cosmetic: it selects the tool-call shape AND
    # caps the completion budget (hermes=256, qwen=1024). Leaving it to the default
    # silently starves a reasoning model into a parse failure, which the adapter
    # records as `abstained` — an artefact that looks exactly like a model that
    # refused to choose.
    name = dialect_override or model.get("dialect") or "qwen"
    dialect = DIALECTS.get(name)
    if dialect is None:
        raise SystemExit(f"unknown dialect {name!r} for {model['id']} (have: {', '.join(DIALECTS)})")
    config = ServerConfig(
        base_url=model["_base_url"],
        model=model["id"],
        api_key=api_key,
        runtime="api",
        quantization="api",
        revision=None,
        extra_headers={"User-Agent": model.get("_user_agent") or DEFAULT_USER_AGENT},
    )
    return LocalSLMBackend(backend_id=backend_id, config=config, dialect=dialect,
                           capability=capability)


def preflight(model: dict[str, Any], api_key: str) -> tuple[bool, str]:
    """One real tool-call request. Distinguishes 'the model abstained' from
    'the endpoint never answered', which the battery alone cannot do."""
    config = ServerConfig(
        base_url=model["_base_url"], model=model["id"], api_key=api_key,
        runtime="api", quantization="api", revision=None,
        extra_headers={"User-Agent": model.get("_user_agent") or DEFAULT_USER_AGENT},
    )
    tools = [{"type": "function", "function": {
        "name": "probe", "description": "report readiness",
        "parameters": {"type": "object", "properties": {}, "additionalProperties": False}}}]
    try:
        outcome = OpenAICompatTransport(config).chat(
            [{"role": "user", "content": "Call the probe tool."}],
            tools=tools, tool_choice="required", max_tokens=32, temperature=0.0,
        )
    except TransportError as exc:
        return False, f"transport error: {exc}"
    except Exception as exc:  # noqa: BLE001
        return False, f"{type(exc).__name__}: {exc}"
    return True, f"ok (tool_call={'yes' if outcome.tool_call else 'no'})"



class ThrottledBackend:
    """Pace calls to a metered endpoint and make transport failures visible.

    Two problems this solves, both of which corrupt the measurement rather than
    the model:

    1. `LocalSLMBackend.decide` catches TransportError and returns an ABSTENTION.
       Its contract is deliberate ("any transport or parse failure fails open as
       abstained=True"), but it means a 429 storm and a model that refuses to
       choose are indistinguishable in the output. A free tier whose TPM binds
       (Groq: 8000 TPM with ~2K-token fixtures) therefore reports as a very
       conservative model. This wrapper separates them again and counts them.
    2. Token buckets refill continuously, so the correct client behaviour is to
       wait and retry, not to record a failure.
    """

    def __init__(self, inner: Any, *, min_interval_s: float = 1.0,
                 max_retries: int = 5, retry_base_s: float = 2.0) -> None:
        self._inner = inner
        self._min_interval_s = min_interval_s
        self._max_retries = max_retries
        self._retry_base_s = retry_base_s
        self._last_call = 0.0
        self.transport_failures = 0
        self.retries = 0

    @property
    def backend_id(self) -> str:
        return self._inner.backend_id

    @property
    def dialect(self) -> Any:
        return self._inner.dialect

    @property
    def config(self) -> Any:
        return self._inner.config

    def health(self, **kwargs: Any) -> dict[str, Any]:
        return self._inner.health(**kwargs)

    def decide(self, request: Any) -> Any:
        decision = None
        for attempt in range(self._max_retries + 1):
            gap = self._min_interval_s - (time.perf_counter() - self._last_call)
            if gap > 0:
                time.sleep(gap)
            self._last_call = time.perf_counter()
            decision = self._inner.decide(request)
            reason = decision.parse_error or ""
            if not reason.startswith("transport_error"):
                return decision
            if attempt < self._max_retries:
                self.retries += 1
                time.sleep(self._retry_base_s * (2 ** attempt))
                continue
            self.transport_failures += 1
            return decision
        return decision


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--registry", default=str(REGISTRY))
    ap.add_argument("--fixtures", default=str(DEFAULT_FIXTURES))
    ap.add_argument("--provider", action="append", default=[],
                    help="provider key from the registry; repeatable")
    ap.add_argument("--model", action="append", default=[],
                    help="model id or label; provider/<id> also accepted; repeatable")
    ap.add_argument("--all", action="store_true", help="every model in the registry")
    ap.add_argument("--reps", type=int, default=1, help="repeat the whole battery N times")
    ap.add_argument("--max-tokens", type=int, default=700,
                    help="completion budget per decision; reasoning models need more than the local default of 256")
    ap.add_argument("--out", default=None, help="output directory (default: results/local-cognition/<stamp>-api)")
    ap.add_argument("--dialect", default=None,
                    help="override the tool-call dialect for every model (" + ", ".join(DIALECTS) + ")")
    ap.add_argument("--min-interval", type=float, default=1.0,
                    help="minimum seconds between calls; a metered free tier needs this (default 1.0)")
    ap.add_argument("--max-retries", type=int, default=5,
                    help="retries on a transport error, exponential backoff (default 5)")
    ap.add_argument("--no-preflight", dest="preflight", action="store_false", default=True,
                    help="skip the one-call reachability probe (default: probe first)")
    ap.add_argument("--dry-run", action="store_true", help="resolve the plan and check keys; make no calls")
    ap.add_argument("--json", action="store_true", help="print the summary JSON instead of markdown")
    args = ap.parse_args()

    registry = load_registry(Path(args.registry))
    wanted = resolve_wanted(registry, args.provider, args.model, args.all)
    if not wanted:
        raise SystemExit("nothing selected; pass --provider, --model or --all")

    import os
    planned: list[tuple[str, dict[str, Any], str]] = []
    missing: list[str] = []
    for provider, model in wanted:
        key = os.environ.get(model["_api_key_env"])
        if not key:
            missing.append(f"{provider} ({model['_api_key_env']})")
            continue
        planned.append((provider, model, key))

    if args.dry_run:
        print(f"registry : {args.registry}")
        print(f"fixtures : {args.fixtures} ({len(load_fixtures(Path(args.fixtures)))} fixtures)")
        print(f"reps     : {args.reps}   max_tokens: {args.max_tokens}\n")
        for provider, model, _ in planned:
            print(f"  READY  {provider}/{model['id']}  cohort={model['_cohort']} "
                  f"dialect={model.get('_dialect')} "
                  f"({COHORT_MEANING.get(model['_cohort'], '?')}) ctx={model.get('context')}")
        for entry in missing:
            print(f"  SKIP   {entry} — environment variable not set")
        calls = len(planned) * args.reps * len(load_fixtures(Path(args.fixtures)))
        print(f"\nplanned calls: {calls}  (models={len(planned)} x reps={args.reps} x fixtures)")
        return 0

    if not planned:
        raise SystemExit(f"no provider has a key set; missing: {', '.join(missing)}")
    if missing:
        print(f"# skipping (no key): {', '.join(missing)}", file=sys.stderr)

    fixtures = load_fixtures(Path(args.fixtures))
    policy = EscalationPolicy(EscalationThresholds())
    out_root = Path(args.out) if args.out else (REPO / "results" / "local-cognition" / f"{stamp()}-api")
    if out_root.exists():
        raise SystemExit(f"refusing to overwrite existing output: {out_root}")

    results: list[dict[str, Any]] = []
    provenance: list[dict[str, Any]] = []
    for provider, model, key in planned:
        for rep in range(1, args.reps + 1):
            label = f"{provider}/{model['id']}" + (f"#r{rep}" if args.reps > 1 else "")
            print(f"# running {label} ...", file=sys.stderr)
            if args.preflight:
                ok, detail = preflight(model, key)
                print(f"# preflight {label}: {detail}", file=sys.stderr)
                if not ok:
                    results.append({"backend": label, "error": f"PREFLIGHT_FAILED: {detail}",
                                    "fixtures": len(fixtures), "rows": []})
                    provenance.append({
                        "label": label, "provider": provider, "model": model["id"],
                        "cohort": model["_cohort"], "base_url": model["_base_url"],
                        "context_declared": model.get("context"), "max_tokens": args.max_tokens,
                        "rep": rep, "preflight": detail, "preflight_ok": False,
                    })
                    continue
            try:
                backend = ThrottledBackend(
                    build_backend(model, key, label, args.dialect),
                    min_interval_s=args.min_interval, max_retries=args.max_retries)
                results.append(evaluate_backend(backend, fixtures, label=label, policy=policy,
                                                max_tokens=args.max_tokens))
            except Exception as exc:  # noqa: BLE001 - a failed provider is a row, not a crash
                print(f"# FAILED {label}: {type(exc).__name__}: {exc}", file=sys.stderr)
                results.append({"backend": label, "error": f"{type(exc).__name__}: {exc}",
                                "fixtures": len(fixtures), "rows": []})
            provenance.append({
                "label": label, "provider": provider, "model": model["id"],
                "cohort": model["_cohort"], "base_url": model["_base_url"],
                "context_declared": model.get("context"), "max_tokens": args.max_tokens,
                "rep": rep, "dialect": args.dialect or model.get("_dialect"),
                "transport_failures": getattr(backend, "transport_failures", None),
                "retries": getattr(backend, "retries", None),
            })

    out_root.mkdir(parents=True, exist_ok=False)
    report = {
        "schema": SCHEMA + ".api",
        "mode": "api",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "fixtures_path": str(args.fixtures),
        "fixture_count": len(fixtures),
        "max_tokens": args.max_tokens,
        "reps": args.reps,
        "registry": str(args.registry),
        "provenance": provenance,
        "results": results,
    }
    (out_root / "report.json").write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    # render_md is the house renderer for *successful* backends; a provider that
    # failed has no latency block, so it is recorded in report.json/comparison.md
    # instead of being forced through it.
    succeeded = [r for r in results if not r.get("error") and r.get("rows")]
    (out_root / "report.md").write_text(
        render_md(succeeded, fixtures) if succeeded else
        "# API provider run — no backend completed\n\n"
        "Every selected model failed before producing rows. See `report.json` for the errors.\n",
        encoding="utf-8",
    )
    with (out_root / "raw.jsonl").open("w", encoding="utf-8") as fh:
        for r in results:
            for row in r.get("rows", []):
                fh.write(json.dumps({**row, "backend_label": r["backend"]}, sort_keys=True) + "\n")

    summary = comparison(results, fixtures, provenance)
    (out_root / "comparison.json").write_text(json.dumps(summary, indent=2, sort_keys=True), encoding="utf-8")
    (out_root / "comparison.md").write_text(render_comparison(summary), encoding="utf-8")
    print(f"# wrote {out_root}", file=sys.stderr)

    print(json.dumps(summary, indent=2, sort_keys=True) if args.json else render_comparison(summary))
    return 0


def comparison(results: list[dict[str, Any]], fixtures: Sequence[Any],
               provenance: list[dict[str, Any]]) -> dict[str, Any]:
    """Per-model safety/quality/latency summary, plus the cohort context."""
    prov = {p["label"]: p for p in provenance}
    models: list[dict[str, Any]] = []
    for r in results:
        rows = r.get("rows", []) or []
        n = len(rows)
        lat = sorted(row["latency_ms"] for row in rows
                     if isinstance(row.get("latency_ms"), (int, float)) and row["latency_ms"] > 0)

        def pct(p: float) -> float | None:
            if not lat:
                return None
            idx = min(len(lat) - 1, int(round((p / 100.0) * (len(lat) - 1))))
            return round(lat[idx], 1)

        models.append({
            "label": r["backend"],
            "provider": prov.get(r["backend"], {}).get("provider"),
            "cohort": prov.get(r["backend"], {}).get("cohort"),
            "fixtures": r.get("fixtures", len(fixtures)),
            "n": n,
            "error": r.get("error"),
            "schema_valid": sum(1 for row in rows if row.get("schema_valid")),
            "trajectory_correct": sum(1 for row in rows if row.get("trajectory_correct")),
            "abstained": sum(1 for row in rows if row.get("abstained")),
            "invalid_call": sum(1 for row in rows if row.get("invalid_call")),
            "dangerous_selected": sum(1 for row in rows if row.get("dangerous_selected")),
            "fabrication_proxy": sum(1 for row in rows
                                     if row.get("invalid_call") and not row.get("schema_valid")),
            "latency_ms": {"p50": pct(50), "p95": pct(95), "p99": pct(99), "n": len(lat)},
            "dialect": prov.get(r["backend"], {}).get("dialect"),
            "transport_failures": prov.get(r["backend"], {}).get("transport_failures"),
            "retries": prov.get(r["backend"], {}).get("retries"),
        })
    return {
        "fixture_count": len(fixtures),
        "aggregation_rule": "Per-model rows only. Never pool models into one universal score; "
                            "report each model against the same fixture set separately.",
        "models": models,
    }


def render_comparison(summary: dict[str, Any]) -> str:
    lines = [
        "# API provider comparison — local-cognition-v1",
        "",
        f"{summary['fixture_count']} fixtures per model. `dangerous_selected` is the count of times a",
        "model named an action it was not authorised to take — the confident-but-wrong failure.",
        "`fabrication_proxy` counts calls that were invalid AND schema-invalid, i.e. the model",
        "produced something malformed rather than abstaining.",
        "",
        "`transport_failures` counts calls that never got a usable HTTP response after retries.",
        "If it is non-zero the row is a LOWER BOUND on that model, not a measurement of it.",
        "",
        "| model | provider | cohort | dialect | schema ok | trajectory ok | abstained | dangerous | transport fail | p50 ms | p95 ms |",
        "|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for m in summary["models"]:
        if m["error"]:
            lines.append(f"| {m['label']} | {m['provider']} | {m['cohort']} | - "
                         f"| ERROR: {m['error'][:70]} | | | | | | |")
            continue
        n = max(m["n"], 1)
        lat = m["latency_ms"]
        lines.append(
            f"| {m['label']} | {m['provider']} | {m['cohort']} | {m.get('dialect')} "
            f"| {m['schema_valid']}/{n} | {m['trajectory_correct']}/{n} | {m['abstained']} "
            f"| {m['dangerous_selected']} | {m.get('transport_failures')} "
            f"| {lat['p50'] if lat['p50'] is not None else '-'} "
            f"| {lat['p95'] if lat['p95'] is not None else '-'} |"
        )
    lines += ["", "Cohorts: " + "; ".join(f"`{k}` = {v}" for k, v in COHORT_MEANING.items()), ""]
    return "\n".join(lines)


if __name__ == "__main__":
    raise SystemExit(main())
