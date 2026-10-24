"""Explicit JSON adapters. Prose and unknown layouts never become policy evidence."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import json
import math
from pathlib import Path

from .. import paths
from .compile import iso
from .findings import Finding, FindingsError, append_findings, canonical, ledger_path, read_findings, timestamp

KINDS = {"shadow_learner", "edge_probe", "engine_bench", "shadow_offload", "z0eval_result"}


def wilson(correct: int, n: int) -> tuple[float, float]:
    if type(n) is not int or n <= 0 or not 0 <= correct <= n:
        raise FindingsError("Wilson interval requires 0 <= correct <= n and n > 0")
    z = 1.959963984540054
    p = correct / n
    centre = (p + z*z/(2*n)) / (1 + z*z/n)
    half = z * math.sqrt(p*(1-p)/n + z*z/(4*n*n)) / (1 + z*z/n)
    return centre - half, centre + half


def _time(value) -> str:
    if isinstance(value, (int, float)):
        return iso(datetime.fromtimestamp(value, timezone.utc))
    timestamp(value)
    return value


def _finding(spec: dict, source: dict, ts: str, subject: dict, arm: dict, metric: dict,
             *, evidence_class: str = "measured", scope_note: str | None = None,
             expires_at: str | None = None, baseline: dict | None = None) -> dict:
    return Finding(ts=ts, source=source, subject=subject, arm=arm, metric=metric,
                   evidence_class=evidence_class,
                   scope_note=scope_note or spec.get("scope_note", "Only this measured job and exact arm; no cross-device, quant or engine generalization."),
                   expires_at=expires_at or iso(timestamp(ts) + timedelta(days=14)),
                   baseline=baseline or {}, synthetic=spec.get("synthetic", False)).to_dict()


def _shadow(data: dict, spec: dict, source: dict, ts: str) -> list[dict]:
    if data.get("schema") != "z0int.shadow.learning.v1":
        raise FindingsError("Unknown shadow learner schema")
    result = []
    for family, arms in sorted(data["families"].items()):
        candidates = []
        for label, score in arms.items():
            n, correct = score.get("n"), score.get("correct")
            if type(n) is not int or n <= 0 or type(correct) is not int:
                continue
            low, high = wilson(correct, n)
            candidates.append((low, label, high, correct, n))
        if not candidates:
            continue
        low, label, high, correct, n = max(candidates)
        kind = "slm" if label.startswith("slm:") else "rules" if label == "rules" else "knn"
        arm = {"kind": kind, "model": label.removeprefix("slm:"), **spec.get("arm", {})}
        # The learning schema scores exact prequential live-session joins.
        metric = {"name": "acc_at_conf", "value": correct/n, "n": n, "ci_low": low,
                  "ci_high": high, "ci_method": "wilson95", "higher_is_better": True,
                  "live_shadow": True}
        result.append(_finding(spec, source, ts, {"family": family, "job": "next-action"}, arm, metric))
    return result


def _labelled(rows: list[dict], spec: dict, source: dict, ts: str) -> list[dict]:
    scores = {}
    threshold = spec.get("confidence_threshold", .8)
    for row in rows:
        if row.get("schema") != "z0int.shadow.labelled.v1" or row.get("label_source") != "exact_omp_session_tool_call_join":
            raise FindingsError("Unknown labelled shadow schema or label join")
        for trial in row.get("shadow", []):
            confidence = trial.get("confidence")
            if (trial.get("abstained") or trial.get("selected_action") is None
                    or not isinstance(confidence, (int, float)) or confidence < threshold):
                continue
            label = trial.get("model") or trial.get("label")
            if not label:
                continue
            score = scores.setdefault(row["family"], {}).setdefault("slm:" + label, {"n": 0, "correct": 0})
            score["n"] += 1
            score["correct"] += int(trial["selected_action"] == row["executed_action"])
    return _shadow({"schema": "z0int.shadow.learning.v1", "families": scores}, spec, source, ts)


def _bench(data: dict, spec: dict, source: dict, ts: str) -> list[dict]:
    rows = data.get("results", data.get("models"))
    if not isinstance(rows, list):
        raise FindingsError("Expected structured results/models array; prose is not evidence")
    result = []
    for row in rows:
        if row.get("status", "measured") != "measured":
            continue
        arm = {"kind": "engine", **spec.get("arm", {}), **data.get("arm", {}), **row.get("arm", {})}
        for field in ("device", "engine", "backend", "model", "quant"):
            if field in row:
                arm[field] = row[field]
            elif field in data:
                arm[field] = data[field]
        if row.get("id") and not arm.get("model"):
            arm["model"] = row["id"]
        if not all(arm.get(k) for k in ("device", "engine", "backend", "model")):
            raise FindingsError("Benchmark needs explicit device/engine/backend/model (use source arm metadata)")
        subject = {"family": spec.get("family", "serving.engine"), "job": "engine-benchmark"}
        raw = data.get("raw_counters", {}).get(row.get("id"), [])
        measured_count = sum(1 for counter in raw if not counter.get("warmup", False))
        for name in ("decode_tok_s", "pp_tok_s"):
            if name not in row:
                continue
            n = row.get("n", data.get("n", measured_count))
            value = row[name]
            metric = {"name": name, "value": value, "n": n, "ci_low": value, "ci_high": value,
                      "ci_method": "none", "higher_is_better": True}
            result.append(_finding(spec, source, ts, subject, arm, metric))
        if "classification_total" in row and "classification_correct" in row:
            n, correct = row["classification_total"], row["classification_correct"]
            low, high = wilson(correct, n)
            metric = {"name": "correctness", "value": correct/n, "n": n, "ci_low": low,
                      "ci_high": high, "ci_method": "wilson95", "higher_is_better": True}
            result.append(_finding(spec, source, ts, subject, arm, metric))
    return result


def _offload(data: dict, spec: dict, source: dict, ts: str) -> list[dict]:
    rows = data.get("opportunities")
    if not isinstance(rows, list):
        raise FindingsError("Expected opportunities array")
    result = []
    for row in rows:
        metric = row.get("metric")
        if not metric:
            if "keep_recall" not in row or "n" not in row:
                continue
            metric = {"name": "keep_recall_at_20pct", "value": row["keep_recall"], "n": row["n"],
                      "ci_low": row["ci_low"], "ci_high": row["ci_high"],
                      "ci_method": row.get("ci_method", "none"), "higher_is_better": True}
        if metric.get("name") not in {"keep_recall", "keep_recall_at_20pct"}:
            continue
        if row.get("retained_fraction") != .2:
            raise FindingsError("keep_recall needs explicit retained_fraction=0.2")
        metric = {**metric, "name": "keep_recall_at_20pct"}
        result.append(_finding(spec, source, ts, row["subject"], row["arm"], metric,
                               evidence_class=row["evidence_class"], scope_note=row.get("scope_note")))
    return result


def _eval(data: dict, spec: dict, source: dict, ts: str) -> list[dict]:
    if data.get("schema") != "z0eval.result.v1":
        raise FindingsError("Unknown z0eval result schema")
    if not isinstance(data.get("findings"), list):
        raise FindingsError("z0eval receipt has no explicit findings metrics")
    eligible = data.get("verdict") == "KEEP" and all(
        data.get("gates", {}).get(gate) == "pass"
        for gate in ("contract", "authority", "quality_noninferiority", "latency_tail"))
    return [_finding(spec, source, _time(row.get("ts", ts)), row["subject"], row["arm"],
                     {**row["metric"], "promotion_eligible": eligible},
                     evidence_class=row["evidence_class"], scope_note=row["scope_note"],
                     expires_at=row.get("expires_at"), baseline=row.get("baseline")) for row in data["findings"]]


def ingest_sources(sources: list[dict], home: Path | None = None) -> dict:
    """Source specs contain kind/path plus explicit missing arm/time metadata."""
    home = home or paths.home()
    ledger = ledger_path(home)
    existing = read_findings(ledger)  # Fail closed before any append on corruption.
    seen = {row["source"]["sha256"] for row in existing}
    report = {"schema": "z0.policy.ingest.v1", "added": 0, "sources": []}
    for spec in sources:
        path = Path(spec["path"]).expanduser().resolve()
        status = {"path": str(path), "kind": spec.get("kind")}
        try:
            if spec.get("kind") not in KINDS:
                raise FindingsError("Unknown source kind")
            raw = path.read_bytes()
            sha = hashlib.sha256(raw).hexdigest()
            status["sha256"] = sha
            if sha in seen:
                status.update(status="skipped", reason="source_sha256_already_ingested")
                report["sources"].append(status)
                continue
            if path.suffix not in {".json", ".jsonl"}:
                raise FindingsError("Prose-only source is not parsed")
            data = [json.loads(line) for line in raw.splitlines()] if path.suffix == ".jsonl" else json.loads(raw)
            marked = [spec]
            if isinstance(data, dict):
                marked.append(data)
                for key in ("findings", "models", "results", "opportunities"):
                    if isinstance(data.get(key), list):
                        marked.extend(row for row in data[key] if isinstance(row, dict))
            elif isinstance(data, list):
                marked.extend(row for row in data if isinstance(row, dict))
            if any(type(row.get("synthetic", False)) is not bool for row in marked):
                raise FindingsError("Source synthetic must be boolean")
            spec = {**spec, "synthetic": any(row.get("synthetic", False) for row in marked)}
            source = {"kind": spec["kind"], "path": str(path), "sha256": sha,
                      "run_id": spec.get("run_id", sha[:16])}
            if spec.get("url"):
                source["url"] = spec["url"]
            for field in ("expected_model_id", "engine_build", "model_path", "model_resolved_path", "model_sha256"):
                if field in spec:
                    source[field] = spec[field]
            if isinstance(data, dict):
                value = data.get("ts", spec.get("ts"))
            else:
                value = max((r["ts"] for r in data), default=spec.get("ts"))
            if value is None:
                raise FindingsError("Source needs an explicit observation ts")
            ts = _time(value)
            if spec["kind"] == "shadow_learner":
                rows = _labelled(data, spec, source, ts) if isinstance(data, list) else _shadow(data, spec, source, ts)
            else:
                adapter = {"edge_probe": _bench, "engine_bench": _bench,
                           "shadow_offload": _offload, "z0eval_result": _eval}[spec["kind"]]
                rows = adapter(data, spec, source, ts)
            if not rows:
                raise FindingsError("No supported measured rows with sample counts")
        except (OSError, ValueError, TypeError, KeyError, AttributeError) as exc:
            status.update(status="skipped", reason=str(exc))
        else:
            added = append_findings(rows, ledger, deduplicate_sources=True)
            seen.add(sha)
            status.update(status="ingested", findings=len(added))
            report["added"] += len(added)
        report["sources"].append(status)
    destination = home / "findings" / "ingest-status.json"
    destination.write_bytes(canonical(report) + b"\n")
    return report


def discover_sources(home: Path | None = None, operations_root: Path | None = None,
                     z0evals_root: Path | None = None) -> list[dict]:
    home = home or paths.home()
    sources = [{"kind": "shadow_learner", "path": str(home / "loop" / name)}
               for name in ("shadow-learning-status.json", "labelled-shadow.jsonl")
               if (home / "loop" / name).exists()]
    if operations_root:
        sources.extend({"kind": "edge_probe", "path": str(path)}
                       for path in sorted((operations_root / "edge-probe").rglob("results.json")))
        engine_root = operations_root / "mbp-gpu"
        engine_json = sorted(engine_root.rglob("*.json"))
        sources.extend({"kind": "engine_bench", "path": str(path)} for path in engine_json)
        if not engine_json and (engine_root / "REPORT.md").exists():
            sources.append({"kind": "engine_bench", "path": str(engine_root / "REPORT.md")})
        opportunity = operations_root / "shadow-offload" / "opportunities.json"
        if opportunity.exists():
            sources.append({"kind": "shadow_offload", "path": str(opportunity)})
    if z0evals_root:
        sources.extend({"kind": "z0eval_result", "path": str(path)} for path in sorted(z0evals_root.rglob("*.json")))
    return sources
