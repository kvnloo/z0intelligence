"""Pure evidence-to-policy stage machine. Persistence is a separate operation."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import math
import tempfile
from urllib.parse import urlsplit

from .. import paths
from .findings import FindingsError, canonical, digest, locked, timestamp, validate

SCHEMA = "z0.decision_policy.v1"
STAGES = ("none", "shadow", "canary", "live")
SCOPE_FIELDS = ("device", "quant", "engine", "backend", "model")
QUALITY = {"acc_at_conf", "accuracy", "correctness", "keep_recall_at_20pct", "task_success"}


@dataclass(frozen=True)
class CompilerConfig:
    compiler_revision: str
    bar_shadow: float = .80
    bar: float = .95
    n_shadow: int = 30
    n_canary: int = 100
    n_live: int = 50
    expiry_days: float = 14
    unhealthy_hours: float = 24
    canary_share: float = .1
    abstain_to: str = "jev"
    option_capacity_bar: float = .95
    option_capacity_tasks: tuple[str, ...] = ("T1", "T2", "T3", "T4")


def iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def scope_of(finding: dict) -> dict:
    explicit = finding["subject"].get("scope")
    if explicit is not None:
        if not isinstance(explicit, dict) or any(k not in SCOPE_FIELDS for k in explicit):
            raise FindingsError("Unsupported subject.scope")
        if any(finding["arm"].get(k) not in (None, "", v) for k, v in explicit.items()):
            raise FindingsError("subject.scope conflicts with arm identity")
        # An explicit scope may narrow an arm, but cannot erase its physical boundaries.
        return {**{k: finding["arm"][k] for k in SCOPE_FIELDS if finding["arm"].get(k)}, **explicit}
    return {k: finding["arm"][k] for k in SCOPE_FIELDS if finding["arm"].get(k)}


def rule_key(family: str, scope: dict) -> str:
    return family + "@" + digest(scope)[:12] if scope else family


def resolve_family(policy: dict, family: str, scope: dict | None = None) -> dict | None:
    """Select the narrowest matching rule, including a restrictive none rule."""
    scope = scope or {}
    matches = [(len(row.get("scope", {})), key, row)
               for key, row in policy.get("families", {}).items()
               if row.get("family", key) == family and
               all(scope.get(k) == v for k, v in row.get("scope", {}).items())]
    return max(matches, key=lambda x: (x[0], x[1]))[2] if matches else None


def _expiry(row: dict, config: CompilerConfig) -> datetime:
    return min(timestamp(row["expires_at"]), timestamp(row["ts"]) + timedelta(days=config.expiry_days))


def _noninferior(row: dict) -> bool:
    metric = row["metric"]
    divergence, floor = metric.get("divergence"), metric.get("aa_noise_floor")
    return (type(divergence) in (float, int) and type(floor) in (float, int)
            and 0 <= divergence <= floor)


def _promotion_evidence(row: dict) -> bool:
    if row.get("evidence_kind") == "proxy":
        return False
    if row["source"].get("adapter") == "agent_outcomes":
        return row.get("evidence_kind") == "product_outcome" and isinstance(row.get("verifier_receipt"), dict)
    return True


def option_capacity_key(scope: dict) -> str:
    """Lossless key for an exact physical model scope, shared with O13 readers."""
    if set(scope) != set(SCOPE_FIELDS) or any(not isinstance(v, str) or not v for v in scope.values()):
        raise ValueError("Option capacity requires exact model/quant/engine/backend/device")
    return canonical(scope).decode("utf-8")


def _choice_lower(row: dict) -> float | None:
    metric = row["metric"]
    if (metric.get("ci_method") != "wilson95" or metric.get("higher_is_better") is not True
            or not metric.get("promotion_eligible", True)
            or not 0 <= metric["value"] <= 1 or not 0 <= metric["ci_low"] <= 1):
        return None
    p, n = metric["value"], metric["n"]
    if "correct" in metric:
        correct = metric["correct"]
        if type(correct) is not int or not 0 <= correct <= n or abs(p - correct/n) > 1e-12:
            return None
    z = 1.959963984540054
    lower = (p + z*z/(2*n) - z*math.sqrt(p*(1-p)/n + z*z/(4*n*n))) / (1 + z*z/n)
    return min(metric["ci_low"], lower)


def _compile_option_capacity(findings: list[dict], now: datetime, config: CompilerConfig) -> dict:
    """Bound option counts across every required task, without routing credit."""
    if not math.isfinite(config.option_capacity_bar) or not 0 <= config.option_capacity_bar <= 1:
        raise ValueError("option_capacity_bar must be between zero and one")
    tasks = set(config.option_capacity_tasks)
    if any(not isinstance(task, str) or not task for task in tasks):
        raise ValueError("option_capacity_tasks must contain nonempty task names")
    scopes, cells = {}, {}
    for row in findings:
        scope = scope_of(row)
        if set(scope) != set(SCOPE_FIELDS) or any(not isinstance(v, str) or not v for v in scope.values()):
            continue
        key = option_capacity_key(scope)
        scopes[key] = scope
        if (row["subject"].get("family") != "choice.option_capacity"
                or row["subject"].get("job") != "ChoiceLadder"
                or row["metric"]["name"] != "choice_accuracy"
                or row["evidence_class"] != "measured" or not _promotion_evidence(row) or timestamp(row["ts"]) > now):
            continue
        task = row["subject"].get("task", row["metric"].get("task"))
        k = row["metric"].get("option_count")
        if not isinstance(task, str) or not task or type(k) is not int or k < 2:
            continue
        tasks.add(task)
        identity = (key, task, k)
        old = cells.get(identity)
        if old is None or (timestamp(row["ts"]), row["id"]) > (timestamp(old["ts"]), old["id"]):
            cells[identity] = row
    result = {}
    for key in sorted(scopes):
        maxima, selected = [], []
        for task in sorted(tasks):
            passing = []
            for (cell_key, cell_task, k), row in cells.items():
                if cell_key != key or cell_task != task or _expiry(row, config) <= now:
                    continue
                lower = _choice_lower(row)
                if lower is not None and lower >= config.option_capacity_bar:
                    passing.append((k, lower, row))
            if passing:
                best = max(passing, key=lambda cell: (cell[0], cell[1], cell[2]["id"]))
                maxima.append(best[0])
                selected.append((best[1], best[2]))
            else:
                maxima.append(2)
        complete = bool(tasks) and len(selected) == len(tasks)
        credited = selected if complete else []
        result[key] = {
            "allowed_k": min(maxima) if maxima else 2,
            "finding_ids": sorted(row["id"] for _, row in credited),
            "wilson_lower": min((lower for lower, _ in credited), default=None),
            "task": sorted(tasks), "bar": config.option_capacity_bar,
            "expires_at": iso(min((_expiry(row, config) for _, row in credited), default=now)),
        }
    return result


def compile_policy(findings: list[dict], previous_policy: dict | None, now: datetime | str,
                   config: CompilerConfig | dict) -> dict:
    """No I/O, clock reads, environment reads or mutable global state."""
    config = CompilerConfig(**config) if isinstance(config, dict) else config
    now = timestamp(now) if isinstance(now, str) else now
    if now.tzinfo is None:
        raise ValueError("now requires a timezone")
    groups: dict[str, list[dict]] = {}
    metadata = {}
    for row in findings:
        validate(row)
        scope = scope_of(row)
        family = row["subject"]["family"]
        key = rule_key(family, scope)
        metadata[key] = (family, scope)
        groups.setdefault(key, []).append(row)
    old_families = (previous_policy or {}).get("families", {})
    families, changes = {}, []
    for key in sorted(set(groups) | set(old_families)):
        prior = old_families.get(key, {})
        family, scope = metadata.get(key, (prior.get("family", key), prior.get("scope", {})))
        rows = sorted(groups.get(key, []), key=lambda r: (timestamp(r["ts"]), r["id"]))
        quality = [r for r in rows if r["metric"]["name"] in QUALITY
                   and r["metric"].get("higher_is_better") is True
                   and r["evidence_class"] not in {"inference", "published"}
                   and _promotion_evidence(r)
                   and timestamp(r["ts"]) <= now]
        before = prior.get("stage", "none")
        if before not in STAGES:
            raise ValueError("Unknown previous policy stage")
        arm = prior.get("arm", {})
        if before == "none":
            candidates = [r for r in quality if r["evidence_class"] == "measured"
                          and r["metric"].get("promotion_eligible", True)
                          and _expiry(r, config) > now]
            if candidates:
                best = max(candidates, key=lambda r: (timestamp(r["ts"]), r["metric"]["ci_low"], r["id"]))
                arm = best["arm"]
        same = [r for r in quality if r["arm"] == arm]
        fresh = [r for r in same if _expiry(r, config) > now]
        latest = same[-1] if same else None
        stage, reason = before, None
        # Latest evidence dominates older successes. An expired or absent arm loses all credit.
        if before != "none":
            if not fresh or (latest and _expiry(latest, config) <= now):
                stage, reason = "none", "evidence_expired"
            elif latest["metric"]["ci_low"] < config.bar_shadow:
                stage, reason = "none", "latest_ci_below_shadow_bar"
            elif before in {"canary", "live"} and latest["metric"]["ci_low"] < config.bar:
                stage, reason = "shadow", "latest_ci_below_bar"
        health = [r for r in rows if r["arm"] == arm and r["evidence_class"] == "measured"
                  and _promotion_evidence(r)
                  and r["metric"]["name"] == "endpoint_health" and timestamp(r["ts"]) <= now]
        if health:
            last_health = health[-1]["metric"]
            since = last_health.get("unhealthy_since")
            if last_health["value"] == 0 and since and now - timestamp(since) > timedelta(hours=config.unhealthy_hours):
                stage, reason = "none", "endpoint_unhealthy"
        used = []
        if reason is None and latest and latest["metric"].get("promotion_eligible", True):
            eligible = [r for r in fresh if r["metric"].get("promotion_eligible", True)]
            measured = [r for r in eligible if r["evidence_class"] == "measured"
                        and r["metric"]["ci_low"] >= config.bar_shadow and r["metric"]["n"] >= config.n_shadow]
            shadow = [r for r in eligible if r["evidence_class"] == "measured"
                      and r["metric"].get("live_shadow") is True
                      and r["metric"]["ci_low"] >= config.bar and r["metric"]["n"] >= config.n_canary]
            replay = [r for r in eligible if r["evidence_class"] == "replayed_L1" and _noninferior(r)]
            outcome = [r for r in eligible if r["evidence_class"] in {"canary", "replayed_L2"}
                       and r["metric"]["name"] == "task_success"
                       and r["metric"].get("outcome_ledger_verified") is True
                       and r["metric"]["n"] >= config.n_live and _noninferior(r)]
            supported = ("live" if measured and shadow and replay and outcome else
                         "canary" if measured and shadow and replay else
                         "shadow" if measured else "none")
            if before != "none" and STAGES.index(supported) < STAGES.index(before):
                stage, reason = supported, "stage_evidence_expired"
            if reason is None and before == "none" and measured and latest["metric"]["ci_low"] >= config.bar_shadow:
                stage, used = "shadow", [measured[-1]]
            elif reason is None and before == "shadow" and shadow and replay and latest["metric"]["ci_low"] >= config.bar:
                stage, used = "canary", [shadow[-1], replay[-1]]
            elif reason is None and before == "canary" and outcome and latest["metric"]["ci_low"] >= config.bar:
                stage, used = "live", [outcome[-1]]
            if reason is None:
                used = (measured[-1:] if stage == "shadow" else
                        shadow[-1:] + replay[-1:] if stage == "canary" else
                        shadow[-1:] + replay[-1:] + outcome[-1:] if stage == "live" else used)
        # Carry only fresh evidence for this exact arm. A demotion cites its latest observation too.
        evidence_rows = {r["id"]: r for r in fresh}
        for row in used + ([latest] if reason and latest else []) + (health[-1:] if reason == "endpoint_unhealthy" else []):
            evidence_rows[row["id"]] = row
        evidence = sorted(evidence_rows)
        expiry = min((_expiry(r, config) for r in used), default=max(
            (_expiry(r, config) for r in fresh), default=now))
        rule_id = prior.get("rule_id", "F-" + key + "-1")
        families[key] = {"family": family, "scope": scope, "stage": stage, "arm": arm,
                         "threshold": config.bar, "abstain_to": config.abstain_to,
                         "canary_share": config.canary_share, "evidence": evidence,
                         "rule_id": rule_id, "expires_at": iso(expiry), "demoted_reason": reason}
        families[key]["synthetic"] = any(row.get("synthetic", False) for row in evidence_rows.values())
        if stage != before:
            changes.append({"rule_id": rule_id, "from": before, "to": stage, "because": evidence})
    serving = {}
    for row in sorted(findings, key=lambda r: (timestamp(r["ts"]), r["id"])):
        url = row["source"].get("url")
        if (row["evidence_class"] != "measured" or not _promotion_evidence(row) or not url or _expiry(row, config) <= now
                or timestamp(row["ts"]) > now):
            continue
        parsed = urlsplit(url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
            continue
        arm = row["arm"]
        if not all(arm.get(k) for k in SCOPE_FIELDS):
            continue
        scope = scope_of(row)
        identity = canonical(scope)
        serving[identity] = {**scope, "kind": arm["kind"], "url": url, "evidence": [row["id"]]}
        serving[identity]["synthetic"] = row.get("synthetic", False)
        for field in ("expected_model_id", "engine_build", "model_path", "model_resolved_path", "model_sha256"):
            if field in row["source"]:
                serving[identity][field] = row["source"][field]
    for rule in families.values():
        if rule["stage"] in {"canary", "live"}:
            rule["serving_missing"] = canonical(rule["scope"]) not in serving
    policy = {"schema": SCHEMA, "compiled_at": iso(now),
              "findings_head": findings[-1]["id"] if findings else "",
              "compiler": "z0int.policy.compile@" + config.compiler_revision,
              "families": families, "serving": [serving[k] for k in sorted(serving)], "changes": changes}
    policy["synthetic"] = any(row.get("synthetic", False) for row in findings)
    policy["option_capacity"] = _compile_option_capacity(findings, now, config)
    policy["version"] = digest(policy)
    return policy


def validate_policy(policy: dict) -> None:
    if policy.get("schema") != SCHEMA or policy.get("version") != digest({k: v for k, v in policy.items() if k != "version"}):
        raise ValueError("Policy content hash mismatch")


def _atomic(path: Path, value: dict) -> None:
    fd, name = tempfile.mkstemp(dir=path.parent, prefix=".policy-", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(canonical(value) + b"\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def write_policy(policy: dict, home: Path | None = None) -> Path:
    validate_policy(policy)
    directory = (home or paths.home()) / "policy"
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / "decision-policy.json"
    with locked(directory / "policy.lock"):
        history = directory / "history"
        history.mkdir(exist_ok=True)
        if target.exists():
            old = json.loads(target.read_text(encoding="utf-8"))
            validate_policy(old)
            _atomic(history / (old["version"] + ".json"), old)
        _atomic(history / (policy["version"] + ".json"), policy)
        _atomic(target, policy)
    return target
