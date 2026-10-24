"""Canonical evidence records and a locked, append-only hash chain."""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from datetime import datetime
import fcntl
import hashlib
import json
import math
import os
from pathlib import Path
from typing import Any, Iterable

from .. import paths

SCHEMA = "z0.finding.v1"
CLASSES = {"measured", "replayed_L1", "replayed_L2", "canary", "published", "inference"}


class FindingsError(ValueError):
    """Evidence is invalid. Do not compile or overwrite a policy from it."""


def canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
                      allow_nan=False).encode("utf-8")


def digest(value: Any) -> str:
    return hashlib.sha256(canonical(value)).hexdigest()


def timestamp(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise FindingsError("Timestamp must include a timezone")
    return parsed


@dataclass(frozen=True)
class Finding:
    ts: str
    source: dict[str, Any]
    subject: dict[str, Any]
    arm: dict[str, Any]
    metric: dict[str, Any]
    evidence_class: str
    scope_note: str
    expires_at: str
    baseline: dict[str, Any] = field(default_factory=dict)
    schema: str = SCHEMA
    id: str = ""
    prev: str = ""
    synthetic: bool = False
    evidence_kind: str | None = None
    verifier_receipt: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        row = asdict(self)
        if row["synthetic"] is False:
            del row["synthetic"]
        for key in ("evidence_kind", "verifier_receipt"):
            if row[key] is None:
                del row[key]
        row["id"] = finding_id(row)
        validate(row)
        return row


def finding_id(row: dict) -> str:
    return digest({k: v for k, v in row.items() if k not in {"id", "prev"}})


def validate(row: dict) -> None:
    try:
        if row["schema"] != SCHEMA or row["evidence_class"] not in CLASSES:
            raise FindingsError("Unknown finding schema or evidence class")
        if type(row.get("synthetic", False)) is not bool:
            raise FindingsError("Finding synthetic must be boolean")
        if row.get("evidence_kind") not in (None, "proxy", "product_outcome"):
            raise FindingsError("Unknown finding evidence_kind")
        if row.get("evidence_kind") == "product_outcome" and not isinstance(row.get("verifier_receipt"), dict):
            raise FindingsError("Product outcome requires a verifier receipt")
        source = row["source"]
        if source["kind"] not in {"z0eval_result", "shadow_learner", "edge_probe", "engine_bench", "shadow_offload", "manual"}:
            raise FindingsError("Unknown finding source kind")
        if not source["path"] or not Path(source["path"]).is_absolute():
            raise FindingsError("Finding requires an absolute source.path")
        sha = source["sha256"]
        if not isinstance(sha, str) or len(sha) != 64 or any(c not in "0123456789abcdef" for c in sha):
            raise FindingsError("Finding requires a source.sha256")
        n = row["metric"]["n"]
        if type(n) is not int or n < 1:
            raise FindingsError("Finding requires a positive integer metric.n")
        if not row["metric"]["name"] or row["metric"]["ci_method"] not in {"wilson95", "bootstrap95", "none"}:
            raise FindingsError("Finding requires a metric name and supported ci_method")
        if type(row["metric"]["higher_is_better"]) is not bool:
            raise FindingsError("Finding requires boolean higher_is_better")
        for key in ("value", "ci_low", "ci_high"):
            value = row["metric"][key]
            if type(value) not in (int, float) or not math.isfinite(value):
                raise FindingsError("Finding metrics must be finite numbers")
        if row["metric"]["ci_low"] > row["metric"]["ci_high"]:
            raise FindingsError("Finding confidence interval is reversed")
        if not row["subject"]["family"] or not row["arm"]["kind"]:
            raise FindingsError("Finding requires a family and arm kind")
        if not isinstance(row["scope_note"], str) or not row["scope_note"].strip():
            raise FindingsError("Finding requires an explicit scope_note")
        timestamp(row["ts"])
        timestamp(row["expires_at"])
        if row.get("id") != finding_id(row):
            raise FindingsError("Finding content hash mismatch")
    except (KeyError, TypeError, ValueError) as exc:
        if isinstance(exc, FindingsError):
            raise
        raise FindingsError(f"Invalid finding: {exc}") from exc


def ledger_path(home: Path | None = None) -> Path:
    return (home or paths.home()) / "findings" / "findings.jsonl"


@contextmanager
def locked(path: Path, *, exclusive: bool = True):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as stream:
        fcntl.flock(stream, fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH)
        try:
            yield
        finally:
            fcntl.flock(stream, fcntl.LOCK_UN)


def _read(path: Path) -> list[dict]:
    if not path.exists():
        return []
    rows = []
    prev = ""
    try:
        with path.open("rb") as stream:
            for number, line in enumerate(stream, 1):
                if not line.endswith(b"\n"):
                    raise FindingsError(f"Truncated findings line {number}")
                row = json.loads(line)
                validate(row)
                if line != canonical(row) + b"\n":
                    raise FindingsError(f"Noncanonical findings line {number}")
                if row.get("prev") != prev:
                    raise FindingsError(f"Broken findings chain at line {number}")
                rows.append(row)
                prev = hashlib.sha256(canonical(row)).hexdigest()
    except (ValueError, TypeError, OSError) as exc:
        raise FindingsError(f"Cannot read findings {path}: {exc}") from exc
    return rows


def read_findings(path: Path | None = None) -> list[dict]:
    path = path or ledger_path()
    with locked(path.with_suffix(".lock"), exclusive=False):
        return _read(path)


def append_findings(findings: Iterable[Finding | dict], path: Path | None = None,
                    *, deduplicate_sources: bool = False) -> list[dict]:
    """Validate the entire batch before writing. Never repair a corrupt chain."""
    path = path or ledger_path()
    with locked(path.with_suffix(".lock")):
        existing = _read(path)
        ids = {row["id"] for row in existing}
        sources = {row["source"]["sha256"] for row in existing}
        prev = hashlib.sha256(canonical(existing[-1])).hexdigest() if existing else ""
        pending = []
        for finding in findings:
            row = finding.to_dict() if isinstance(finding, Finding) else dict(finding)
            row["id"] = finding_id(row)
            validate(row)
            if row["id"] in ids or (deduplicate_sources and row["source"]["sha256"] in sources):
                continue
            row["prev"] = prev
            prev = hashlib.sha256(canonical(row)).hexdigest()
            ids.add(row["id"])
            pending.append(row)
        if pending:
            with path.open("ab") as stream:
                stream.write(b"".join(canonical(row) + b"\n" for row in pending))
                stream.flush()
                os.fsync(stream.fileno())
        return pending
