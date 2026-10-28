"""Pure evidence-to-policy stage machine. Persistence is a separate operation."""
from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import math
import tempfile
from urllib.parse import urlsplit

from .. import paths
from .findings import FindingsError, canonical, digest, locked, outcome_members, timestamp, validate

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
    option_capacity_costs: dict | None = None
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
    # Historical shadow aggregates and OMP learner observations did not bind
    # a trial to loaded model bytes. Flags on those records cannot add proof.
    if (row["source"].get("kind") == "shadow_learner"
            and row["arm"].get("kind") == "slm"
            and (row["subject"].get("job") == "next-action"
                 or row["subject"].get("family") == "omp.tool")):
        return False
    if row["source"].get("adapter") == "agent_outcomes":
        return row.get("evidence_kind") == "product_outcome" and isinstance(row.get("verifier_receipt"), dict)
    return True

def _outcome_cohorts(findings: list[dict], now: datetime, config: CompilerConfig,
                     subject: dict, arm: dict) -> list[list[dict]]:
    """Use only uniquely bound candidate/reference product checks from frozen protocols."""
    rows = []
    for row in findings:
        try:
            proof = row["verifier_receipt"]
            evaluation = proof["evaluation"]
            binding = row["baseline"]["outcome_protocol"]
            protocol = binding["document"]
            role = evaluation["arm_id"]
            margin = protocol["noninferiority_margin"]
            outcome_members(protocol)
            if (row.get("evidence_kind") != "product_outcome"
                    or row["source"].get("adapter") != "agent_outcomes"
                    or row["evidence_class"] not in {"canary", "replayed_L2"}
                    or not row["metric"].get("promotion_eligible", True)
                    or proof["kind"] != "product_check" or type(proof["success"]) is not bool
                    or not all(isinstance(proof.get(k), str) and proof[k] for k in ("trace_id", "receipt_id", "sha256"))
                    or protocol["schema"] != "z0.policy.paired_outcome_protocol.v1"
                    or protocol["subject"] != subject or row["subject"] != subject
                    or protocol["candidate_arm"] != arm or protocol["candidate_arm"] == protocol["reference_arm"]
                    or role not in ("candidate", "reference") or row["arm"] != protocol[role + "_arm"]
                    or evaluation["arm"] != row["arm"] or evaluation["subject"] != subject
                    or evaluation["experiment_id"] != protocol["experiment_id"]
                    or digest(protocol) != binding["sha256"]
                    or evaluation["protocol_sha256"] != binding["sha256"]
                    or evaluation["observed_at"] != row["ts"]
                    or not all(isinstance(evaluation.get(k), str) and evaluation[k]
                               for k in ("experiment_id", "pair_id", "task_snapshot_id"))
                    or type(margin) not in (int, float) or not math.isfinite(margin) or not 0 <= margin < 1
                    or not timestamp(protocol["frozen_at"]) < timestamp(row["ts"]) <= now
                    or timestamp(protocol["expires_at"]) <= now or _expiry(row, config) <= now):
                continue
            rows.append(row)
        except (KeyError, TypeError, ValueError):
            continue
    # Identical receipt reimports count once; conflicting trace or receipt identities
    # invalidate every affected observation rather than choosing a convenient one.
    unique = {}
    for row in findings:
        p = row.get("verifier_receipt")
        if row.get("evidence_kind") != "product_outcome" or not isinstance(p, dict):
            continue
        identity = (p.get("trace_id"), p.get("receipt_id"), p.get("sha256"))
        if not all(isinstance(value, str) and value for value in identity):
            continue
        signature = canonical([p, row["subject"], row["arm"], row["baseline"], row["metric"],
                               row["evidence_class"], row["ts"], row["expires_at"]])
        unique.setdefault(identity, {}).setdefault(signature, row)
    counts = [{}, {}, {}]
    for key in unique:
        for index, value in enumerate(key):
            counts[index][value] = counts[index].get(value, 0) + 1
    allowed = {id(row) for row in rows}
    records = [(key, next(iter(values.values()))) for key, values in unique.items()
               if len(values) == 1 and id(next(iter(values.values()))) in allowed]
    cohorts = {}
    for key, row in records:
        if any(counts[index][value] != 1 for index, value in enumerate(key)):
            continue
        evaluation = row["verifier_receipt"]["evaluation"]
        binding = row["baseline"]["outcome_protocol"]
        cohort = (binding["sha256"], canonical(binding["document"]))
        pair = (evaluation["experiment_id"], evaluation["pair_id"])
        cohorts.setdefault(cohort, {}).setdefault(pair, []).append(row)
    passing = []
    for pairs in cohorts.values():
        first = next(iter(pairs.values()))[0]
        protocol = first["baseline"]["outcome_protocol"]["document"]
        expected = outcome_members(protocol)
        if set(pairs) != {(protocol["experiment_id"], pair_id) for pair_id in expected}:
            continue
        matched = []
        for (_, pair_id), pair in pairs.items():
            if len(pair) != 2:
                break
            by_role = {r["verifier_receipt"]["evaluation"]["arm_id"]: r for r in pair}
            if set(by_role) != {"candidate", "reference"}:
                break
            candidate, reference = by_role["candidate"], by_role["reference"]
            if any(r["verifier_receipt"]["evaluation"]["task_snapshot_id"] != expected[pair_id]
                   for r in pair):
                break
            matched.append((candidate, reference))
        if len(matched) != len(expected):
            continue
        n = len(matched)
        if n < config.n_live:
            continue
        successes = sum(c["verifier_receipt"]["success"] for c, _ in matched)
        losses = sum(not c["verifier_receipt"]["success"] and r["verifier_receipt"]["success"]
                     for c, r in matched)
        # Conservative bound on loss probability, not an asserted A/A noise floor.
        z = 1.959963984540054
        def bounds(count):
            p = count / n
            center = (p + z*z/(2*n)) / (1 + z*z/n)
            half = z*math.sqrt(p*(1-p)/n + z*z/(4*n*n)) / (1 + z*z/n)
            return center-half, center+half
        margin = matched[0][0]["baseline"]["outcome_protocol"]["document"]["noninferiority_margin"]
        if bounds(successes)[0] >= config.bar and bounds(losses)[1] <= margin:
            passing.append([row for pair in matched for row in pair])
    return passing


def option_capacity_key(scope: dict) -> str:
    """Lossless key for an exact physical model scope, shared with O13 readers."""
    if set(scope) != set(SCOPE_FIELDS) or any(not isinstance(v, str) or not v for v in scope.values()):
        raise ValueError("Option capacity requires exact model/quant/engine/backend/device")
    model = scope["model"]
    if (not model.startswith("sha256:") or len(model) != 71
            or any(char not in "0123456789abcdef" for char in model[7:])):
        raise ValueError("Option capacity model must identify GGUF bytes as sha256:<digest>")
    return canonical(scope).decode("utf-8")


def _choice_lower(row: dict) -> float | None:
    metric = row["metric"]
    if (metric.get("ci_method") != "wilson95" or metric.get("higher_is_better") is not True
            or not metric.get("promotion_eligible", True)
            or not 0 <= metric["value"] <= 1 or not 0 <= metric["ci_low"] <= 1):
        return None
    p, n = metric["value"], metric["n"]
    if "answered" in metric and (type(metric["answered"]) is not int or metric["answered"] != n):
        return None
    if "abstained" in metric:
        abstained = metric["abstained"]
        if (type(abstained) is not int or abstained < 0
                or metric.get("denominator") != n + abstained):
            return None
    if "correct" in metric:
        correct = metric["correct"]
        if type(correct) is not int or not 0 <= correct <= n or abs(p - correct/n) > 1e-12:
            return None
    z = 1.959963984540054
    lower = (p + z*z/(2*n) - z*math.sqrt(p*(1-p)/n + z*z/(4*n*n))) / (1 + z*z/n)
    return min(metric["ci_low"], lower)


def _capacity_costs(config: CompilerConfig, risk: str, findings: list[dict], now: datetime,
                    scope: dict) -> dict:
    """All costs use one common unit (e.g. O14 pace-adjusted milliseconds)."""
    supplied = (config.option_capacity_costs or {}).get(risk)
    defaults = {"read": 20., "mutating_reversible": 100.}
    if risk == "destructive":
        return {"measured": False, "blocked": True, "finding_ids": []}
    if supplied is None:
        costs = {}
        for row in findings:
            field = row["metric"]["name"]
            if (field not in {"cost_call", "cost_frontier", "cost_error", "abstain_rate"}
                    or row["subject"].get("family") != "choice.error_cost"
                    or row["subject"].get("risk_class") != risk
                    or row["evidence_class"] != "measured" or not _promotion_evidence(row)
                    or scope_of(row) != scope
                    or timestamp(row["ts"]) > now or _expiry(row, config) <= now):
                continue
            prior = costs.get(field)
            if prior is None or (timestamp(row["ts"]), row["id"]) > (timestamp(prior["ts"]), prior["id"]):
                costs[field] = row
        units = {row["metric"].get("cost_unit") for row in costs.values()}
        if len(costs) == 4 and len(units) == 1 and units <= {"latency_ms_lambda0", "o14_score_ms"}:
            supplied = {field: row["metric"]["value"] for field, row in costs.items()}
            supplied.update(measured=True, finding_ids=sorted(row["id"] for row in costs.values()))
            supplied["cost_unit"] = next(iter(units))
    inputs = dict(supplied or {"cost_call": .1, "cost_frontier": 1.,
                              "cost_error": defaults[risk], "abstain_rate": 0.,
                              "measured": False, "finding_ids": []})
    if type(inputs.get("measured")) is not bool:
        raise ValueError("Capacity measurement status must be boolean")
    if (not isinstance(inputs.get("finding_ids"), list)
            or any(not isinstance(value, str) or not value for value in inputs["finding_ids"])):
        raise ValueError("Capacity finding_ids must be a string list")
    for field in ("cost_call", "cost_frontier", "cost_error", "abstain_rate"):
        value = inputs.get(field)
        if type(value) not in (int, float) or not math.isfinite(value) or value < 0:
            raise ValueError(f"Invalid option capacity cost: {field}")
    if inputs["cost_error"] <= 0 or inputs["cost_frontier"] <= 0 or inputs["abstain_rate"] > 1:
        raise ValueError("Invalid option capacity error/frontier/abstention cost")
    if inputs.get("measured"):
        ids = inputs.get("finding_ids")
        sources = [row for row in findings if row["id"] in (ids or [])]
        units = {row["metric"].get("cost_unit") for row in sources}
        if (not ids or len(ids) != len(set(ids)) or len(sources) != len(ids) or any(
                row["evidence_class"] != "measured"
                or not _promotion_evidence(row)
                or row["subject"].get("family") != "choice.error_cost"
                or row["subject"].get("risk_class") != risk
                or timestamp(row["ts"]) > now or _expiry(row, config) <= now
                for row in sources)
                or units not in ({"latency_ms_lambda0"}, {"o14_score_ms"})
                or inputs.get("cost_unit") != next(iter(units), None)
                or {row["metric"]["name"] for row in sources} !=
                   {"cost_call", "cost_frontier", "cost_error", "abstain_rate"}):
            raise ValueError("Measured costs require fresh exact-scope, risk-bound findings with one permitted unit")
        source_scopes = [scope_of(row) for row in sources]
        try:
            for source_scope in source_scopes:
                option_capacity_key(source_scope)
        except ValueError as exc:
            raise ValueError("Measured costs require exact physical scope") from exc
        if any(source_scope != source_scopes[0] for source_scope in source_scopes):
            raise ValueError("Measured costs require one exact-scope")
        for field in ("cost_call", "cost_frontier", "cost_error", "abstain_rate"):
            if not any(row["metric"]["name"] == field and row["metric"]["value"] == inputs[field]
                       for row in sources):
                raise ValueError(f"Measured cost has no matching finding: {field}")
        if source_scopes[0] != scope:
            overrides = dict(config.option_capacity_costs or {})
            overrides.pop(risk, None)
            return _capacity_costs(replace(config, option_capacity_costs=overrides), risk, findings, now, scope)
        inputs["expires_at"] = iso(min(_expiry(row, config) for row in sources))
    inputs["status"] = "measured" if inputs.get("measured") else "unmeasured"
    return inputs


def _compile_option_capacity(findings: list[dict], now: datetime, config: CompilerConfig) -> dict:
    """Bound selective decisions by risk-specific expected cost, not a fixed bar."""
    tasks = set(config.option_capacity_tasks)
    if any(not isinstance(task, str) or not task for task in tasks):
        raise ValueError("option_capacity_tasks must contain nonempty task names")
    scopes, cells = {}, {}
    for row in findings:
        scope = scope_of(row)
        if set(scope) != set(SCOPE_FIELDS) or any(not isinstance(v, str) or not v for v in scope.values()):
            continue
        try:
            key = option_capacity_key(scope)
        except ValueError:
            continue
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
        result[key] = {}
        for risk in ("read", "mutating_reversible", "destructive"):
            inputs = _capacity_costs(config, risk, findings, now, scopes[key])
            blocked = inputs.get("blocked", False)
            bar = None if blocked else 1 - (
                inputs["cost_frontier"] * (1 - inputs["abstain_rate"]) -
                inputs["cost_call"]) / inputs["cost_error"]
            maxima, selected = [], []
            for task in sorted(tasks):
                passing = []
                for (cell_key, cell_task, k), row in cells.items():
                    if (cell_key != key or cell_task != task or _expiry(row, config) <= now
                            or blocked or not inputs.get("measured")):
                        continue
                    lower = _choice_lower(row)
                    if lower is not None and lower > bar:
                        passing.append((k, lower, row))
                if passing:
                    best = max(passing, key=lambda cell: (cell[0], cell[1], cell[2]["id"]))
                    maxima.append(best[0])
                    selected.append((best[1], best[2]))
                else:
                    maxima.append(0)
            complete = bool(tasks) and len(selected) == len(tasks)
            credited = selected if complete else []
            result[key][risk] = {
                "allowed_k": min(maxima) if complete else 0,
                "bar_derived": bar, "inputs": inputs,
                "finding_ids": sorted({row["id"] for _, row in credited} |
                                      set(inputs.get("finding_ids", []))),
                "wilson_lower": min((lower for lower, _ in credited), default=None),
                "task": sorted(tasks),
                "expires_at": iso(min(
                    [_expiry(row, config) for _, row in credited] +
                    ([timestamp(inputs["expires_at"])] if inputs.get("expires_at") else []),
                    default=now)),
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
                   and timestamp(r["ts"]) <= now
                   and r.get("evidence_kind") != "product_outcome"]
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
        product = [r for r in rows if r["arm"] == arm
                   and r.get("evidence_kind") == "product_outcome"
                   and _promotion_evidence(r) and timestamp(r["ts"]) <= now
                   and _expiry(r, config) > now]
        latest_product = product[-1] if product else None
        stage, reason = before, None
        # Latest evidence dominates older successes. An expired or absent arm loses all credit.
        if before != "none":
            if not fresh or (latest and _expiry(latest, config) <= now):
                stage, reason = "none", "evidence_expired"
            elif latest["metric"]["ci_low"] < config.bar_shadow:
                stage, reason = "none", "latest_ci_below_shadow_bar"
            elif before in {"canary", "live"} and latest["metric"]["ci_low"] < config.bar:
                stage, reason = "shadow", "latest_ci_below_bar"
        if latest_product and latest_product["verifier_receipt"].get("success") is False:
            stage, reason = "none", "latest_product_failure"
        health = [r for r in rows if r["arm"] == arm and r["evidence_class"] == "measured"
                  and _promotion_evidence(r)
                  and r["metric"]["name"] == "endpoint_health" and timestamp(r["ts"]) <= now]
        if health:
            last_health = health[-1]["metric"]
            since = last_health.get("unhealthy_since")
            if last_health["value"] == 0 and since and now - timestamp(since) > timedelta(hours=config.unhealthy_hours):
                stage, reason = "none", "endpoint_unhealthy"
        used, outcome = [], []
        if reason is None and latest and latest["metric"].get("promotion_eligible", True):
            eligible = [r for r in fresh if r["metric"].get("promotion_eligible", True)]
            measured = [r for r in eligible if r["evidence_class"] == "measured"
                        and r["metric"]["ci_low"] >= config.bar_shadow and r["metric"]["n"] >= config.n_shadow]
            shadow = [r for r in eligible if r["evidence_class"] == "measured"
                      and r["metric"].get("live_shadow") is True
                      and r["metric"]["ci_low"] >= config.bar and r["metric"]["n"] >= config.n_canary]
            replay = [r for r in eligible if r["evidence_class"] == "replayed_L1" and _noninferior(r)]
            cohorts = _outcome_cohorts(findings, now, config, latest["subject"], arm)
            outcome = cohorts[-1] if cohorts else []
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
                stage, used = "live", outcome
            if reason is None:
                used = (measured[-1:] if stage == "shadow" else
                        shadow[-1:] + replay[-1:] if stage == "canary" else
                        shadow[-1:] + replay[-1:] + outcome if stage == "live" else used)
        # Carry only fresh evidence for this exact arm. A demotion cites its latest observation too.
        evidence_rows = {r["id"]: r for r in fresh}
        for row in (used + ([latest] if reason and latest else [])
                    + ([latest_product] if reason == "latest_product_failure" else [])
                    + (health[-1:] if reason == "endpoint_unhealthy" else [])):
            evidence_rows[row["id"]] = row
        evidence = sorted(evidence_rows)
        expiry = min((_expiry(r, config) for r in used), default=max(
            (_expiry(r, config) for r in fresh), default=now))
        if stage == "live" and outcome:
            expiry = min(expiry, *(timestamp(r["baseline"]["outcome_protocol"]["document"]["expires_at"])
                                   for r in outcome))
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
