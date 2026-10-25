"""Fail-open, content-verified decision policy consumer (no compiler dependency).

The version hashes the canonical UTF-8 JSON body with only ``version`` removed.
Cache keys include path, mtime_ns and the file digest, so same-mtime replacements
cannot retain stale authority. Expiry is checked on every use, including hits.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import fcntl
import tempfile
import urllib.error
import threading
from urllib.request import urlopen
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SCHEMA = "z0.decision_policy.v1"
STAGES = ("none", "shadow", "canary", "live")

_health: dict[tuple[str, str], dict[str, Any]] = {}
_health_pending: set[tuple[str, str]] = set()
_health_lock = threading.Lock()


def _health_path() -> Path:
    return Path(os.environ.get("Z0INT_HOME", str(Path.home() / ".z0int"))) / "policy" / "serving-health.json"


def _scope_hash(scope: dict[str, Any] | None) -> str:
    return hashlib.sha256(_canonical({k: (scope or {}).get(k) for k in
                                     ("model", "quant", "engine", "backend", "device")})).hexdigest()


def _read_health() -> dict[str, Any]:
    try:
        body = json.loads(_health_path().read_text(encoding="utf-8"))
        if body.get("schema") == "z0.serving_health.v1" and isinstance(body.get("endpoints"), dict):
            return body["endpoints"]
    except Exception:
        pass
    return {}


def _write_health(url: str, entry: dict[str, Any]) -> None:
    """Merge concurrent process updates, then publish with an atomic rename."""
    temporary = None
    try:
        path = _health_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.with_suffix(".lock").open("a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            entries = _read_health()
            entries[url] = entry
            with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                             prefix=".serving-health-", delete=False) as stream:
                temporary = stream.name
                json.dump({"schema": "z0.serving_health.v1", "endpoints": entries}, stream,
                          ensure_ascii=False, allow_nan=False, sort_keys=True)
            os.replace(temporary, path)
            temporary = None
    except Exception:
        pass  # inability to cache health must not break the operator's turn
    finally:
        if temporary:
            try:
                os.unlink(temporary)
            except OSError:
                pass


def _probe_health(url: str, scope: dict[str, Any] | None = None, timeout: float = 1.0) -> dict[str, Any]:
    model = revision = None
    status = "unknown"
    try:
        base = url.rstrip("/")
        if base.endswith("/v1"):
            base = base[:-3]
        with urlopen(base + "/v1/models", timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
            models = payload.get("data") or []
            requested = (scope or {}).get("expected_model_id") or (scope or {}).get("model")
            matched = next((row for row in models if isinstance(row, dict) and
                            (requested is None or row.get("id") == requested)), None)
            observed = matched or next((row for row in models if isinstance(row, dict)), {})
            model = observed.get("id")
            revision = observed.get("revision") or observed.get("model_revision")
            status = "healthy" if response.status == 200 and matched else "unhealthy"
    except urllib.error.HTTPError:
        status = "unhealthy"
    except Exception:
        pass
    entry = {"scope_sha256": _scope_hash(scope), "status": status,
             "checked_at": datetime.now(timezone.utc).isoformat(),
             "model": model, "revision": revision}
    key = (url, entry["scope_sha256"])
    _write_health(url, entry)
    with _health_lock:
        _health[key] = entry
        _health_pending.discard(key)
    return entry


def _fresh_health(entry: Any, scope_sha256: str) -> bool:
    try:
        age = (datetime.now(timezone.utc) - _timestamp(entry["checked_at"])).total_seconds()
        return entry["scope_sha256"] == scope_sha256 and 0 <= age <= 60 and entry["status"] in ("healthy", "unhealthy", "unknown")
    except Exception:
        return False


def serving_health(
    url: str | None, scope: dict[str, Any] | None = None, *, probe: bool = False,
) -> bool | None:
    """Read shared 60 s health. CLI misses probe once, bounded to 1.5 s.

    Receipt stamping keeps its nonblocking path; a resident may refresh in a
    daemon while a standalone CLI requests the bounded synchronous probe.
    """
    if not url:
        return None
    digest = _scope_hash(scope)
    key = (url, digest)
    cached = _read_health().get(url)
    with _health_lock:
        if not _fresh_health(cached, digest):
            cached = _health.get(key)
        if _fresh_health(cached, digest):
            return True if cached["status"] == "healthy" else False if cached["status"] == "unhealthy" else None
    if probe:
        entry = _probe_health(url, scope, timeout=1.5)
        return True if entry["status"] == "healthy" else False if entry["status"] == "unhealthy" else None
    with _health_lock:
        if key not in _health_pending:
            _health_pending.add(key)
            try:
                threading.Thread(target=_probe_health, args=(url, scope), daemon=True).start()
            except Exception:
                _health_pending.discard(key)
    return None


def serving_identity_complete(endpoint: dict[str, Any] | None) -> bool:
    """A physical model call requires measured endpoint identity provenance."""
    if not endpoint or not all(isinstance(endpoint.get(k), str) and endpoint[k] for k in
                               ("expected_model_id", "engine_build", "model_path", "model_sha256")):
        return False
    digest = endpoint["model_sha256"]
    return len(digest) == 64 and all(c in "0123456789abcdef" for c in digest)


_model_hashes: dict[tuple[str, int, int, int, int], str] = {}


def observed_model_scope(url: str, alias: str, scope: dict[str, Any]) -> dict[str, Any] | None:
    """Resolve local serving bytes before consulting capacity; aliases confer no authority."""
    from urllib.parse import quote, urlparse
    try:
        parsed_url = urlparse(url)
        if parsed_url.hostname not in ("localhost", "127.0.0.1", "::1"):
            return None  # a remote filesystem path is not local evidence
        base = url.rstrip("/")
        if base.endswith("/v1"):
            base = base[:-3]
        with urlopen(base + "/props?model=" + quote(alias, safe=""), timeout=1.5) as reply:
            props = json.loads(reply.read().decode("utf-8"))
        if not isinstance(props, dict) or props.get("model_alias") != alias:
            return None
        path, build = props.get("model_path"), props.get("build_info")
        if not isinstance(path, str) or not Path(path).is_absolute() or not isinstance(build, str) or not build:
            return None
        with Path(path).open("rb") as stream:
            before = os.fstat(stream.fileno())
            key = (str(Path(path).resolve()), before.st_mtime_ns, before.st_size,
                   before.st_ino, before.st_ctime_ns)
            digest = _model_hashes.get(key)
            if digest is None:
                hasher = hashlib.sha256()
                for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                    hasher.update(chunk)
                after = os.fstat(stream.fileno())
                if (before.st_mtime_ns, before.st_size, before.st_ino, before.st_ctime_ns) != (
                        after.st_mtime_ns, after.st_size, after.st_ino, after.st_ctime_ns):
                    return None
                digest = hasher.hexdigest()
                if len(_model_hashes) >= 32:
                    _model_hashes.clear()
                _model_hashes[key] = digest
        return {**scope, "model": "sha256:" + digest, "engine": build}
    except Exception:
        return None


def _canonical(body: dict[str, Any]) -> bytes:
    return json.dumps(body, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False, allow_nan=False).encode("utf-8")


def _timestamp(value: Any) -> datetime:
    if not isinstance(value, str):
        raise ValueError("invalid_timestamp")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("timestamp_requires_timezone")
    return parsed


def _number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _strings(value: Any) -> bool:
    return isinstance(value, list) and all(isinstance(x, str) and x for x in value)


def _validate(body: Any) -> None:
    if not isinstance(body, dict) or body.get("schema") != SCHEMA:
        raise ValueError("invalid_schema")
    if "synthetic" in body and not isinstance(body["synthetic"], bool):
        raise ValueError("invalid_synthetic")
    for key in ("version", "compiled_at", "findings_head", "compiler"):
        if not isinstance(body.get(key), str):
            raise ValueError(f"invalid_{key}")
    _timestamp(body["compiled_at"])
    if not isinstance(body.get("families"), dict):
        raise ValueError("invalid_families")
    for family, rule in body["families"].items():
        if not family or not isinstance(rule, dict) or rule.get("stage") not in STAGES:
            raise ValueError("invalid_family")
        if "synthetic" in rule and not isinstance(rule["synthetic"], bool):
            raise ValueError("invalid_synthetic")
        if not isinstance(rule.get("arm"), dict):
            raise ValueError("invalid_arm")
        if rule["stage"] != "none" and (
            not isinstance(rule["arm"].get("kind"), str) or not rule["arm"]["kind"]
        ):
            raise ValueError("incomplete_arm")
        if any(k in rule["arm"] and (not isinstance(rule["arm"][k], str) or not rule["arm"][k])
               for k in ("kind", "model", "quant", "engine", "backend", "device")):
            raise ValueError("invalid_arm_field")
        for key in ("threshold", "canary_share"):
            if not _number(rule.get(key)) or not 0 <= rule[key] <= 1:
                raise ValueError(f"invalid_{key}")
        if rule.get("abstain_to") not in ("jev", "frontier"):
            raise ValueError("invalid_abstain_to")
        if not _strings(rule.get("evidence")) or not isinstance(rule.get("rule_id"), str) or not rule["rule_id"]:
            raise ValueError("invalid_rule_evidence")
        if rule.get("demoted_reason") is not None and not isinstance(rule["demoted_reason"], str):
            raise ValueError("invalid_demoted_reason")
        _timestamp(rule.get("expires_at"))
        scope = rule.get("scope", {})
        if not isinstance(scope, dict) or any(
            k not in ("device", "model", "quant", "engine", "backend")
            or not isinstance(v, str) or not v for k, v in scope.items()
        ) or ("family" in rule and not isinstance(rule["family"], str)):
            raise ValueError("invalid_scope")
    if not isinstance(body.get("serving"), list) or not isinstance(body.get("changes"), list):
        raise ValueError("invalid_lists")
    for endpoint in body["serving"]:
        if not isinstance(endpoint, dict) or not all(
            isinstance(endpoint.get(k), str) and endpoint[k]
            for k in ("device", "model", "engine", "backend", "url")
        ) or not _strings(endpoint.get("evidence")):
            raise ValueError("invalid_serving")
    for change in body["changes"]:
        if not isinstance(change, dict) or not isinstance(change.get("rule_id"), str) or (
            change.get("from") not in STAGES or change.get("to") not in STAGES
            or not _strings(change.get("because"))
        ):
            raise ValueError("invalid_change")


@dataclass(frozen=True)
class Policy:
    version: str = "none"
    sha256: str = "none"
    reason: str | None = None
    _body: dict[str, Any] = field(default_factory=dict, repr=False)
    _scope: dict[str, str] = field(default_factory=dict, repr=False)

    def for_scope(self, scope: dict[str, str]) -> Policy:
        return replace(self, _scope=dict(scope))

    def allowed_k(self, scope: dict[str, Any], risk_class: str | None = None) -> int:
        """Require exact-scope, risk-specific evidence and strict economic benefit."""
        try:
            if risk_class not in ("read", "mutating_reversible"):
                return 0
            fields = ("model", "quant", "engine", "backend", "device")
            if not all(isinstance(scope.get(k), str) and scope[k] for k in fields):
                return 0
            model = scope["model"]
            if not model.startswith("sha256:") or len(model) != 71 or any(c not in "0123456789abcdef" for c in model[7:]):
                return 0
            key = _canonical({k: scope[k] for k in fields}).decode("utf-8")
            entry = self._body.get("option_capacity", {}).get(key, {}).get(risk_class)
            if not isinstance(entry, dict):
                return 0
            count = entry.get("allowed_k")
            if not isinstance(count, int) or isinstance(count, bool) or count < 1:
                return 0
            if _timestamp(entry.get("expires_at")) <= datetime.now(timezone.utc):
                return 0
            if not _strings(entry.get("finding_ids")) or not entry["finding_ids"]:
                return 0
            if not _strings(entry.get("task")) or not entry["task"]:
                return 0
            p, bar = entry.get("wilson_lower"), entry.get("bar_derived")
            inputs = entry.get("inputs")
            if not _number(p) or not 0 <= p <= 1 or not _number(bar) or not isinstance(inputs, dict):
                return 0
            if inputs.get("blocked") or inputs.get("measured") is not True or inputs.get("status") != "measured":
                return 0
            if not _strings(inputs.get("finding_ids")) or not inputs["finding_ids"]:
                return 0
            if not set(inputs["finding_ids"]).issubset(entry["finding_ids"]):
                return 0
            call, frontier, error, abstain = (inputs.get(k) for k in
                ("cost_call", "cost_frontier", "cost_error", "abstain_rate"))
            if not all(_number(v) for v in (call, frontier, error, abstain)):
                return 0
            if call < 0 or frontier <= 0 or error <= 0 or not 0 <= abstain <= 1:
                return 0
            derived = 1 - (frontier * (1 - abstain) - call) / error
            expected = call + (1 - p) * error + abstain * frontier
            if not math.isfinite(derived) or not math.isfinite(expected):
                return 0
            if not math.isclose(bar, derived, rel_tol=1e-12, abs_tol=1e-12):
                return 0
            return count if p > derived and expected < frontier else 0
        except Exception:
            return 0

    def rule_for(self, family: str) -> dict[str, Any]:
        matches = []
        for key, rule in self._body.get("families", {}).items():
            if key != family and rule.get("family") != family:
                continue
            scope = rule.get("scope", {})
            if all(rule.get("arm", {}).get(k) == v for k, v in self._scope.items()):
                matches.append((len(scope), key, rule))
        return max(matches, key=lambda row: (row[0], row[1]))[2] if matches else {}

    def stage(self, family: str) -> str:
        rule = self.rule_for(family)
        if not rule or _timestamp(rule["expires_at"]) <= datetime.now(timezone.utc):
            return "none"
        return str(rule["stage"])

    def credited_families(self, trace_id: str) -> tuple[str, ...]:
        # One deterministic cohort per trace, independent of iteration order.
        cohort = int.from_bytes(hashlib.sha256(str(trace_id).encode("utf-8")).digest()[:8], "big")
        return tuple(sorted(
            family for family in {
                rule.get("family", key)
                for key, rule in self._body.get("families", {}).items()
            }
            if self.stage(family) == "live" or (
                self.stage(family) == "canary"
                and cohort < self.rule_for(family)["canary_share"] * (1 << 64)
            )
        ))

    def serving_for(self, device: str | None = None) -> list[dict[str, Any]]:
        # Return copies: a consumer must not be able to mutate cached authority.
        return [dict(row) for row in self._body.get("serving", [])
                if device in (None, "any") or row["device"] == device]

    def endpoint_for(self, family: str) -> dict[str, Any] | None:
        arm = self.rule_for(family).get("arm", {})
        dimensions = ("model", "quant", "engine", "backend", "device")
        if not all(isinstance(arm.get(k), str) and arm[k] for k in dimensions):
            return None
        return next((dict(row) for row in self.serving_for(arm["device"])
                     if all(row.get(k) == arm[k] for k in dimensions)), None)

    def stamp(self, receipt: dict[str, Any], family: str, rule: str | None = None) -> dict[str, Any]:
        entry = self.rule_for(family)
        stage = self.stage(family)
        receipt.update(policy_version=self.version, policy_sha256=self.sha256,
                       rule_id=entry.get("rule_id", rule or "none"), stage=stage, family=family)
        # Keep synthetic provenance even on fallback/ambiguous-family rows.
        receipt["synthetic"] = receipt.get("synthetic") is True or self._body.get("synthetic") is True or any(
            value.get("synthetic") is True for value in self._body.get("families", {}).values()
        )
        receipt["policy_family"] = next((key for key, value in self._body.get("families", {}).items()
                                        if value is entry), family)
        endpoint = self.endpoint_for(family)
        receipt["serving_healthy"] = serving_health(endpoint["url"], endpoint) if endpoint else None
        reason = self.reason or entry.get("demoted_reason")
        if entry and stage == "none" and entry["stage"] != "none":
            reason = "evidence_expired"
        elif not entry and reason is None:
            reason = "family_not_in_policy"
        if reason:
            receipt["policy_reason"] = reason
        else:
            receipt.pop("policy_reason", None)
        return receipt


# A bounded single-entry cache; Z0INT_HOME can change in embedding processes.
_cache_key: tuple[str, int, str] | None = None
_cache_policy: Policy | None = None


def current() -> Policy:
    """Read current authority; IO/parse/schema/hash failures never escape."""
    global _cache_key, _cache_policy
    try:
        path = Path(os.environ.get("Z0INT_HOME", str(Path.home() / ".z0int"))) / "policy" / "decision-policy.json"
        raw = path.read_bytes()
        file_sha = hashlib.sha256(raw).hexdigest()
        key = (str(path), path.stat().st_mtime_ns, file_sha)
        if key == _cache_key and _cache_policy is not None:
            return _cache_policy
        body = json.loads(raw)
        _validate(body)
        digest = hashlib.sha256(_canonical({k: v for k, v in body.items() if k != "version"})).hexdigest()
        if body["version"] != digest:
            raise ValueError("policy_hash_mismatch")
        policy = Policy(version=digest, sha256=digest, _body=body)
        _cache_key, _cache_policy = key, policy
        return policy
    except FileNotFoundError:
        return Policy(reason="policy_missing")
    except Exception as exc:  # a broken policy must never break an operator turn
        return Policy(reason=f"policy_invalid:{type(exc).__name__}:{exc}")


def credited_families(trace_id: str) -> tuple[str, ...]:
    return current().credited_families(trace_id)


def stage(family: str) -> str:
    return current().stage(family)


def serving_for(device: str | None = None) -> list[dict[str, Any]]:
    return current().serving_for(device)


def stamp(receipt_dict: dict[str, Any], family: str, rule: str | None = None) -> dict[str, Any]:
    return current().stamp(receipt_dict, family, rule)


def allowed_k(scope: dict[str, Any], risk_class: str | None = None) -> int:
    return current().allowed_k(scope, risk_class)
