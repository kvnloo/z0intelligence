"""Explicit JSON adapters. Prose and unknown layouts never become policy evidence."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import json
import math
import sqlite3
from pathlib import Path

from .. import paths
from .compile import iso
from .findings import Finding, FindingsError, append_findings, canonical, ledger_path, read_findings, timestamp

KINDS = {"shadow_learner", "edge_probe", "engine_bench", "shadow_offload", "z0eval_result", "agent_outcomes"}


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
             expires_at: str | None = None, baseline: dict | None = None,
             evidence_kind: str | None = None, verifier_receipt: dict | None = None) -> dict:
    return Finding(ts=ts, source=source, subject=subject, arm=arm, metric=metric,
                   evidence_class=evidence_class,
                   scope_note=scope_note or spec.get("scope_note", "Only this measured job and exact arm; no cross-device, quant or engine generalization."),
                   expires_at=expires_at or iso(timestamp(ts) + timedelta(days=14)),
                   baseline=baseline or {}, synthetic=spec.get("synthetic", False),
                   evidence_kind=evidence_kind, verifier_receipt=verifier_receipt).to_dict()


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


def _product_verifier(row: dict) -> tuple[dict | None, str | None]:
    """Require an immutable receipt that observed this run's actual target."""
    reference = row.get("verifier_receipt")
    if isinstance(reference, str):
        try:
            reference = json.loads(reference)
        except ValueError:
            return None, "invalid_verifier_receipt"
    if not isinstance(reference, dict):
        return None, "missing_product_verifier"
    try:
        path = Path(reference["path"])
        if not path.is_absolute():
            raise FindingsError("Verifier receipt path must be absolute")
        raw = path.read_bytes()
        sha = hashlib.sha256(raw).hexdigest()
        if sha != reference["sha256"]:
            raise FindingsError("Verifier receipt hash mismatch")
        document = json.loads(raw)
        trace_id = row.get("trace_id") or row.get("run_id")
        if not isinstance(trace_id, str) or not trace_id:
            raise FindingsError("Outcome needs a run/trace id for verifier binding")
        event_id = reference.get("event_id")
        if event_id:
            events = document.get("eventLog", [document])
            event = next((item for item in events if item.get("eventId") == event_id), None)
            if not event or event.get("type") != "stateUpdate":
                raise FindingsError("Verified AODL stateUpdate id not found")
            payload = event["payload"]
            if payload.get("verified") is not True or payload.get("traceId") != trace_id or not payload.get("verifier"):
                raise FindingsError("AODL verifier is not bound to this run")
            source_hash = event.get("sourceHash")
            if not isinstance(source_hash, str) or len(source_hash) != 64 or any(c not in "0123456789abcdef" for c in source_hash):
                raise FindingsError("AODL receipt requires sourceHash")
            if document.get("provenance", {}).get("sourceHash", source_hash) != source_hash:
                raise FindingsError("AODL source hash mismatch")
            target, success, receipt_id = payload.get("observed_target"), payload.get("success"), event_id
            kind = "aodl_state_update"
        else:
            if document.get("schema") != "z0.product_check.v1" or document.get("verified") is not True:
                raise FindingsError("Receipt is not a verified product check")
            if (document.get("trace_id") or document.get("run_id")) != trace_id or not document.get("verifier"):
                raise FindingsError("Product verifier is not bound to this run")
            target, success, receipt_id = document.get("observed_target"), document.get("success"), document.get("id")
            kind = "product_check"
        if (not isinstance(target, dict) or target.get("kind") not in
                {"ui", "api", "runtime", "filesystem", "database", "product"}
                or not isinstance(target.get("id"), str) or not target["id"]
                or target.get("observed") is not True
                or not isinstance(target.get("state"), dict) or not target["state"]):
            raise FindingsError("Verifier did not observe the real product target")
        if type(success) is not bool or not isinstance(receipt_id, str) or not receipt_id:
            raise FindingsError("Product check needs receipt id and boolean success")
        return {"kind": kind, "path": str(path), "sha256": sha, "receipt_id": receipt_id,
                "observed_target": target, "success": success}, None
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
        return None, str(exc)


def _outcome_metric(row: dict, metric: dict, checked: tuple | None = None) -> tuple[dict, str, dict | None]:
    proof, reason = checked if checked is not None else _product_verifier(row)
    metric = {**metric, "outcome_ledger_verified": proof is not None,
              "promotion_eligible": proof is not None and metric.get("promotion_eligible", True)}
    if proof is None:
        metric["proxy_reason"] = reason
    return metric, "product_outcome" if proof is not None else "proxy", proof


def _agent_outcomes(data: dict | list, spec: dict, source: dict, ts: str) -> list[dict]:
    rows = data if isinstance(data, list) else data.get("rows", data.get("findings"))
    if not isinstance(rows, list):
        raise FindingsError("Agent outcomes need rows/findings, not lane priors or aggregate claims")
    result = []
    for row in rows:
        if row.get("dup_of") is not None:
            continue
        proof, reason = _product_verifier(row)
        if isinstance(row.get("metric"), dict):
            metric = row["metric"]
            subject, arm = row["subject"], row["arm"]
        else:
            if not isinstance(row.get("model"), str) or not row["model"] or not row.get("task_kind"):
                raise FindingsError("Agent outcome row needs explicit model and task_kind")
            success = proof["success"] if proof is not None else row.get("outcome") in {"claimed_success", "verified_success"}
            low, high = wilson(int(success), 1)  # One ledger row is one observed run.
            metric = {"name": "task_success", "value": float(success), "n": 1,
                      "ci_low": low, "ci_high": high, "ci_method": "wilson95", "higher_is_better": True}
            subject = {"family": "agent.outcome." + row["task_kind"], "job": row["task_kind"]}
            arm = {"kind": "llm", "model": row["model"]}
        metric, evidence_kind, proof = _outcome_metric(row, metric, (proof, reason))
        row_source = {**source, "adapter": "agent_outcomes", "run_id": row.get("run_id", source["run_id"])}
        result.append(_finding(spec, row_source, _time(row.get("ts") or row.get("ended_at") or ts),
            subject, arm, metric, evidence_class=row.get("evidence_class", "measured"),
            scope_note=row.get("scope_note", "This agent outcome only. Ledger task_kind is heuristic. Push, PR and test signals are proxies, not product truth."),
            expires_at=row.get("expires_at"), baseline=row.get("baseline"),
            evidence_kind=evidence_kind, verifier_receipt=proof))
    return result


def _eval(data: dict, spec: dict, source: dict, ts: str) -> list[dict]:
    if data.get("schema") != "z0eval.result.v1":
        raise FindingsError("Unknown z0eval result schema")
    if not isinstance(data.get("findings"), list):
        raise FindingsError("z0eval receipt has no explicit findings metrics")
    eligible = data.get("verdict") == "KEEP" and all(
        data.get("gates", {}).get(gate) == "pass"
        for gate in ("contract", "authority", "quality_noninferiority", "latency_tail"))
    result = []
    for row in data["findings"]:
        row_source = {**source, **{key: value for key, value in (row.get("source") or {}).items()
            if key in {"url", "expected_model_id", "engine_build", "model_path", "model_resolved_path", "model_sha256"}}}
        metric = {**row["metric"], "promotion_eligible": eligible}
        evidence_kind, proof = row.get("evidence_kind"), None
        markers = (data.get("source") or {}, row.get("source") or {})
        if evidence_kind == "product_outcome" or any(marker.get("adapter", marker.get("kind")) == "agent_outcomes" for marker in markers):
            metric, evidence_kind, proof = _outcome_metric(row, metric)
            row_source["adapter"] = "agent_outcomes"
        elif evidence_kind == "proxy":
            metric["promotion_eligible"] = False
        result.append(_finding(spec, row_source, _time(row.get("ts", ts)), row["subject"], row["arm"], metric,
            evidence_class=row["evidence_class"], scope_note=row["scope_note"],
            expires_at=row.get("expires_at"), baseline=row.get("baseline"),
            evidence_kind=evidence_kind, verifier_receipt=proof))
    return result


def _load_source(path: Path, raw: bytes, kind: str) -> dict | list:
    if kind == "agent_outcomes" and path.suffix in {".sqlite", ".db"}:
        wal = Path(str(path) + "-wal")
        if wal.exists() and wal.stat().st_size:
            raise FindingsError("Agent outcome SQLite has a live WAL; supply a consistent JSON export")
        database = sqlite3.connect(":memory:")
        try:
            database.deserialize(raw)
            database.row_factory = sqlite3.Row
            return [dict(row) for row in database.execute("SELECT * FROM runs WHERE dup_of IS NULL ORDER BY run_id")]
        finally:
            database.close()
    if path.suffix not in {".json", ".jsonl"}:
        raise FindingsError("Prose-only source is not parsed")
    return [json.loads(line) for line in raw.splitlines()] if path.suffix == ".jsonl" else json.loads(raw)


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
            data = _load_source(path, raw, spec["kind"])
            marked = [spec]
            if isinstance(data, dict):
                marked.append(data)
                for key in ("findings", "models", "results", "opportunities", "rows"):
                    if isinstance(data.get(key), list):
                        marked.extend(row for row in data[key] if isinstance(row, dict))
            elif isinstance(data, list):
                marked.extend(row for row in data if isinstance(row, dict))
            if any(type(row.get("synthetic", False)) is not bool for row in marked):
                raise FindingsError("Source synthetic must be boolean")
            spec = {**spec, "synthetic": any(row.get("synthetic", False) for row in marked)}
            source = {"kind": "manual" if spec["kind"] == "agent_outcomes" else spec["kind"],
                      "path": str(path), "sha256": sha, "run_id": spec.get("run_id", sha[:16])}
            if spec["kind"] == "agent_outcomes":
                source["adapter"] = "agent_outcomes"
            metadata = (data.get("source") or {}) if isinstance(data, dict) else {}
            for field in ("url", "expected_model_id", "engine_build", "model_path", "model_resolved_path", "model_sha256"):
                if field in metadata:
                    source[field] = metadata[field]
                if field in spec:
                    source[field] = spec[field]
            if spec["kind"] == "agent_outcomes":
                outcome_rows = data if isinstance(data, list) else data.get("rows", data.get("findings", []))
                observed = [row.get("ts") or row.get("ended_at") for row in outcome_rows
                            if row.get("ts") is not None or row.get("ended_at") is not None]
                value = max(observed, key=lambda item: timestamp(_time(item))) if observed else spec.get("ts")
            elif isinstance(data, dict):
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
                           "shadow_offload": _offload, "z0eval_result": _eval,
                           "agent_outcomes": _agent_outcomes}[spec["kind"]]
                rows = adapter(data, spec, source, ts)
            if not rows:
                raise FindingsError("No supported measured rows with sample counts")
        except (OSError, ValueError, TypeError, KeyError, AttributeError, sqlite3.Error) as exc:
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
        outcome_ledger = operations_root / "agent-outcomes" / "ledger.sqlite"
        if outcome_ledger.exists():
            sources.append({"kind": "agent_outcomes", "path": str(outcome_ledger)})
    if z0evals_root:
        sources.extend({"kind": "z0eval_result", "path": str(path)} for path in sorted(z0evals_root.rglob("*.json")))
    return sources
