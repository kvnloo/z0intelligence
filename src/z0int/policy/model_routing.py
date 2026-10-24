"""O14 enforces Kerdoios's approved objective from a compiled policy snapshot.

No live catalog/log scans and no duplicate optimizer. Unknown objective metrics
may execute only as explicitly unscored receipts; they earn no O14 compliance credit.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import math

from .findings import digest, timestamp

RULE_ID = "O14.model_routing"
FORMULA_ID = "kerdoios.latency_burn_acceptance.v1"


class RoutingDenied(ValueError):
    pass


def _number(value):
    return type(value) in (int, float) and math.isfinite(value) and value >= 0


def opencode_allowed(entry):
    transport = entry.get("offer", {}).get("transport") or entry.get("offer", {}).get("provider", "")
    if transport not in ("opencode", "opencode-zen", "opencode-go"):
        return True
    access = entry.get("opencode_access", {})
    return (access.get("program") in ("go", "zen") and access.get("free") is True
            and bool(access.get("source")) and bool(access.get("timestamp")))


def compile_routing(bundle: dict | None, now: datetime) -> dict:
    """Bind reviewed source-backed offers and task floors to the policy hash."""
    if bundle is None:
        return {"rule_id": RULE_ID, "formula_id": FORMULA_ID, "offers": [], "task_classes": {}}
    result = deepcopy(bundle)
    if result.get("formula_id") != FORMULA_ID:
        raise ValueError("O14 requires the approved Kerdoios objective")
    if timestamp(result["expires_at"]) <= now:
        raise ValueError("O14 routing evidence expired")
    if not isinstance(result.get("offers"), list) or not isinstance(result.get("task_classes"), dict):
        raise ValueError("O14 offers and task classes required")
    coefficient = result.setdefault("lambda", {"value": 1, "status": "unmeasured",
        "source": "Kevin confirmed objective/default 2026-10-24", "timestamp": "2026-10-24T00:00:00Z"})
    if not _number(coefficient.get("value")) or not coefficient.get("source") or not coefficient.get("timestamp"):
        raise ValueError("O14 lambda needs value, source and timestamp")
    if coefficient["value"] != 1 and coefficient.get("status") != "measured":
        raise ValueError("O14 non-default lambda requires measured evidence")
    seen = set()
    for entry in result["offers"]:
        offer = entry["offer"]
        identity = (offer.get("transport") or offer["provider"], offer["model"])
        if identity in seen or not all(isinstance(x, str) and x for x in identity):
            raise ValueError("O14 duplicate/incomplete transport/model identity")
        seen.add(identity)
        if not opencode_allowed(entry):
            raise ValueError("O14 OpenCode is restricted to sourced Go/Zen free models")
        if not entry.get("evidence") or timestamp(entry["observed_at"]) > now:
            raise ValueError("O14 source evidence required; future evidence rejected")
        for axis in ("coding", "reasoning", "tool_use", "vision"):
            if not _number(offer["capabilities"].get(axis)):
                raise ValueError("O14 capability floor cannot use unknown capability")
        if entry.get("subscription") and not entry.get("quota_windows"):
            raise ValueError("O14 subscription requires all shared quota windows")
        for window in entry.get("quota_windows", []):
            if not window.get("group") or not window.get("unit") or not window.get("source"):
                raise ValueError("O14 quota window needs shared group/unit/source")
            if not all(_number(window.get(k)) for k in ("remaining", "burn_per_hour", "reserved", "request_upper_bound")):
                raise ValueError("O14 quota numbers unknown")
            if timestamp(window["reset_at"]) <= timestamp(entry["observed_at"]):
                raise ValueError("O14 reset must follow its observation")
        metrics = entry.get("metrics", {})
        for key, metric in metrics.items():
            if key == "observed_acceptance":
                continue
            if not isinstance(metric, dict) or not metric.get("source") or not metric.get("timestamp"):
                raise ValueError("O14 every objective metric needs source/timestamp")
    for name, requirement in result["task_classes"].items():
        if not isinstance(name, str) or not name or not isinstance(requirement, dict):
            raise ValueError("O14 invalid task class")
        if not any(_number(requirement.get(k)) and requirement[k] > 0 for k in ("coding", "reasoning")):
            raise ValueError("O14 explicit hard task capability floor required")
        if requirement.get("parallelism", 1) != 1:
            raise ValueError("O14 routes one LLM call at a time")
    result["rule_id"] = RULE_ID
    result["snapshot_id"] = digest(result)
    return result


def quota_allows(entry: dict, now: datetime) -> bool:
    """Reserve a bounded next call, account for observation age, never assume reset.

    The process admission quota ledger additionally serializes real reservations;
    this gate refuses projected exhaustion of ANY shared subscription window.
    """
    observed = timestamp(entry["observed_at"])
    if now < observed:
        return False
    for window in entry.get("quota_windows", []):
        reset = timestamp(window["reset_at"])
        if now >= reset:
            return False
        elapsed = (now - observed).total_seconds() / 3600
        horizon = (reset - now).total_seconds() / 3600
        remaining = window["remaining"] - window["reserved"] - elapsed * window["burn_per_hour"]
        if remaining - window["request_upper_bound"] <= window["burn_per_hour"] * horizon:
            return False
    return True


def _kerdoios():
    try:
        from kerdoios.routing_objective import plan_routing
        from kerdoios.optimize import hard_filter
        from kerdoios.types import (ResourceOffer, CapabilityProfile, Capacity, Economics,
                                    Telemetry, Access, AccessClass, AccessEvidence, WorkRequirement, Mode)
    except ImportError as exc:
        raise RoutingDenied("O14 approved Kerdoios allocator unavailable; no substitute") from exc
    return locals()


def allocation(policy: dict, task_class: str, failed: list[dict], now: datetime | str,
               request_tokens: dict | None = None) -> dict:
    from .compile import validate_policy
    validate_policy(policy)
    now = timestamp(now) if isinstance(now, str) else now.astimezone(timezone.utc)
    rule = policy.get("model_routing", {})
    if rule.get("rule_id") != RULE_ID or not rule.get("offers"):
        raise RoutingDenied("O14 no compiled eligible offer evidence")
    if timestamp(rule["expires_at"]) <= now:
        raise RoutingDenied("O14 routing snapshot expired")
    requirement = rule.get("task_classes", {}).get(task_class)
    if requirement is None:
        raise RoutingDenied("O14 unknown task class")
    excluded = set()
    for failure in failed:
        if failure.get("status") != "failed" or not isinstance(failure.get("reason"), str) or not failure["reason"].strip():
            raise RoutingDenied("O14 fallback requires an honestly recorded failed attempt")
        excluded.add((failure["provider"], failure["model"]))
    kd = _kerdoios()
    req_values = deepcopy(requirement)
    if request_tokens is not None:
        if (set(request_tokens) != {"estimated_input_tokens", "estimated_output_tokens"}
                or any(type(v) is not int or v < 0 for v in request_tokens.values())):
            raise RoutingDenied("O14 invalid host-derived token estimates")
        req_values.update(request_tokens)
    req_values["mode"] = kd["Mode"](req_values.get("mode", "balanced"))
    req_values["capability_id"] = task_class
    req = kd["WorkRequirement"](**req_values)
    offers, metrics, entries = [], {}, {}
    for entry in rule["offers"]:
        raw = deepcopy(entry["offer"])
        identity = (raw.get("transport") or raw["provider"], raw["model"])
        if identity in excluded or not quota_allows(entry, now):
            continue
        for field, kind in (("capabilities", "CapabilityProfile"), ("capacity", "Capacity"),
                            ("economics", "Economics"), ("telemetry", "Telemetry")):
            raw[field] = kd[kind](**raw[field])
        if raw.get("access"):
            access = raw["access"]
            access["access_class"] = kd["AccessClass"](access["access_class"])
            access["evidence"] = kd["AccessEvidence"](access["evidence"])
            raw["access"] = kd["Access"](**access)
        offer = kd["ResourceOffer"](**raw)
        offers.append(offer)
        entries[offer.id] = entry
        metrics[offer.id] = {key: value["value"] for key, value in entry.get("metrics", {}).items()
                             if key != "observed_acceptance"}
        if entry.get("metrics", {}).get("p_accept", {}).get("task_class") != task_class:
            metrics[offer.id]["p_accept"] = None
        if "observed_acceptance" in entry.get("metrics", {}):
            metrics[offer.id]["observed_acceptance"] = entry["metrics"]["observed_acceptance"]
    coefficient = rule["lambda"]
    plan = kd["plan_routing"](offers, req, metrics, lambda_value=coefficient["value"],
                               lambda_evidence={k: v for k, v in coefficient.items() if k != "value"})
    eligible, _ = kd["hard_filter"](offers, req)
    if not eligible:
        raise RoutingDenied("O14 Kerdoios has no eligible offer")
    ranked = plan["ranked"]
    best = ranked[0].offer if ranked else None
    return {"provider": (best.transport or best.provider) if best else None,
            "model": best.model if best else None, "ranked": ranked,
            "eligible": eligible, "unscored": plan["unscored"], "entries": entries, "rule": rule}


def enforce(policy: dict, task_class: str, provider: str, model: str, *, failed=None,
            now: datetime | str, reason: str | None = None, request_tokens: dict | None = None) -> dict:
    result = allocation(policy, task_class, failed or [], now, request_tokens=request_tokens)
    identity = (provider, model)
    offer = next((o for o in result["eligible"] if (o.transport or o.provider, o.model) == identity), None)
    if offer is None:
        raise RoutingDenied("O14 selected offer is ineligible")
    selected = next((s for s in result["ranked"] if s.offer.id == offer.id), None)
    best = (result["provider"], result["model"])
    minimum = result["ranked"][0].score if result["ranked"] else None
    if selected is not None and selected.score != minimum and not (isinstance(reason, str) and reason.strip()):
        raise RoutingDenied("O14 non-argmin offer without recorded reason")
    dominated_by = []
    if selected:
        dominated_by = [s.offer.id for s in result["ranked"] if s.latency <= selected.latency
                        and s.pace <= selected.pace and s.p_accept >= selected.p_accept
                        and (s.latency < selected.latency or s.pace < selected.pace or s.p_accept > selected.p_accept)]
    return {"rule_id": RULE_ID, "formula_id": FORMULA_ID, "policy_version": policy["version"],
            "snapshot_id": result["rule"]["snapshot_id"], "provider": provider, "model": model,
            "task_class": task_class, "offer_id": offer.id,
            "request_tokens": deepcopy(request_tokens),
            "score_status": "scored" if selected else "unscored",
            "o14_credit": selected is not None and selected.score == minimum,
            "formula_inputs": {"selected": selected.inputs if selected else result["entries"][offer.id].get("metrics", {}),
                               "offers": [result["entries"][o.id] for o in result["eligible"]]},
            "score": selected.score if selected else None,
            "argmin": {"provider": best[0], "model": best[1]},
            "minimum_score": minimum,
            "dominated_by": dominated_by, "reason": reason, "failed_attempts": deepcopy(failed or [])}


def audit_stamp(stamp: dict) -> list[str]:
    issues = []
    if stamp.get("rule_id") != RULE_ID or not stamp.get("formula_inputs"):
        issues.append("O14 missing routing receipt inputs")
    offers = stamp.get("formula_inputs", {}).get("offers", [])
    entry = next((e for e in offers if e.get("offer", {}).get("id") == stamp.get("offer_id")), {})
    if stamp.get("provider") in ("opencode", "opencode-zen", "opencode-go") and not opencode_allowed(
            {**entry, "offer": {**entry.get("offer", {}), "transport": stamp["provider"]}}):
        issues.append("O14 OpenCode non-Go/Zen-free model")
    if stamp.get("score_status") == "unscored" and stamp.get("o14_credit") is not False:
        issues.append("O14 unknown metrics falsely credited")
    if stamp.get("score_status") == "scored" and not stamp.get("reason"):
        if stamp.get("dominated_by"):
            issues.append("O14 dominated offer without recorded reason")
        if stamp.get("score") != stamp.get("minimum_score"):
            issues.append("O14 non-argmin offer without recorded reason")
    return issues


def reserve_admission(admission, provider, call_id, context, routing, now):
    """Serialize shared quota groups through existing canonical admission receipts.

    Reservations are charged at their upper bound until reset, even if a caller
    crashes. No new ledger and no optimistic refund for unmetered calls.
    """
    from ..dispatch_authority import locked
    from ..provider_saturation import _rows
    selected = next(e for e in routing["formula_inputs"]["offers"]
                    if e["offer"]["id"] == routing["offer_id"])
    windows = selected.get("quota_windows", [])
    with locked("model-routing-shared-quota"):
        rows = _rows()
        used = {}
        seen = set()
        for row in rows:
            if row["trace_id"] in seen or row.get("extra", {}).get("status") != "acquired":
                continue
            seen.add(row["trace_id"])
            for charge in row["extra"].get("o14_reservations", []):
                if row["ts"] >= timestamp(selected["observed_at"]).timestamp():
                    key = (charge["group"], charge["reset_at"])
                    used[key] = used.get(key, 0) + charge["amount"]
        adjusted = deepcopy(selected)
        charges = []
        for window in adjusted.get("quota_windows", []):
            bound = window["request_upper_bound"]
            if window["unit"] == "tokens" and bound < context["quota_reserved_tokens"]:
                raise RoutingDenied("O14 token bound is smaller than the actual request")
            if window["unit"] == "requests" and bound < 1:
                raise RoutingDenied("O14 request bound is smaller than one call")
            if window["unit"] not in ("tokens", "requests"):
                raise RoutingDenied("O14 unsupported consumption bound; cannot guarantee quota")
            window["reserved"] += used.get((window["group"], window["reset_at"]), 0)
            charges.append({"group": window["group"], "reset_at": window["reset_at"], "amount": bound})
        if not quota_allows(adjusted, now):
            raise RoutingDenied("O14 shared burn-rate reservation would exhaust window")
        return admission.acquire(provider, call_id, {**context, "o14_reservations": charges})


def audit_receipt(receipt: dict, policy: dict) -> list[str]:
    """Recompute a physical call against its archived, content-addressed policy."""
    stamp = receipt.get("extra", {}).get("model_routing")
    if not isinstance(stamp, dict):
        return ["O14 missing model-routing stamp"]
    issues = audit_stamp(stamp)
    if (stamp.get("provider"), stamp.get("model")) != (receipt.get("provider"), receipt.get("model")):
        issues.append("O14 provider/model stamp differs from actual transport")
    if stamp.get("policy_version") != policy.get("version"):
        return issues + ["O14 policy version unavailable or mismatched"]
    try:
        now = datetime.fromtimestamp(receipt["ts"], timezone.utc) if isinstance(receipt["ts"], (int, float)) else receipt["ts"]
        expected = enforce(policy, stamp["task_class"], receipt["provider"], receipt["model"],
                           failed=stamp.get("failed_attempts", []), now=now, reason=stamp.get("reason"),
                           request_tokens=stamp.get("request_tokens"))
        if expected != stamp:
            issues.append("O14 receipt formula inputs/result differ from compiled allocation")
    except (RoutingDenied, ValueError, TypeError, KeyError) as exc:
        issues.append("O14 allocation violation: " + str(exc))
    return issues
