"""Per-capability trust.

Trust is a property of a **model × capability × distribution** triple, never of a
model. NanoJev is credible for bounded legal-action choice at a 0.6 threshold
(60.7% coverage at 94.1% success-given-covered) and rejected for generic
next-action on real Hermes traffic (0.143 against a 0.514 majority floor). A
single ``trusted: true`` flag cannot express that, and
:func:`assert_no_global_trust` rejects any attempt to write one.

Every record carries the coverage and the artifact it came from. A record with no
evidence is ``untested``, not ``trusted``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import json
from pathlib import Path

TRUST_SCHEMA = "z0int.capability_trust.v1"

#: Ordered from least to most assurance. `rejected_*` and `quarantined` are
#: terminal states for a given checkpoint, not steps on the way up.
TRUST_STATUSES: tuple[str, ...] = (
    "untested",
    "experimental",
    "exploratory_beta",
    "trusted_shadow",
    "trusted_bounded",
    "quarantined",
    "rejected_for_current_checkpoint",
    "not_applicable",
)

#: Statuses that may gate a production path.
PRODUCTION_STATUSES: frozenset[str] = frozenset({"trusted_bounded"})


@dataclass(frozen=True)
class CapabilityTrust:
    backend_id: str
    capability: str
    status: str
    coverage: float | None = None
    success_given_covered: float | None = None
    threshold: float | None = None
    max_risk: float | None = None
    split_id: str | None = None
    contract_hash: str | None = None
    evidence: tuple[str, ...] = ()
    note: str | None = None

    def __post_init__(self) -> None:
        if self.status not in TRUST_STATUSES:
            raise ValueError(
                f"unknown trust status {self.status!r}; known={', '.join(TRUST_STATUSES)}"
            )
        if self.status in ("exploratory_beta", "trusted_shadow", "trusted_bounded") and not self.evidence:
            raise ValueError(
                f"{self.backend_id}/{self.capability} claims {self.status!r} with no "
                "evidence artifact; a trust claim must cite where it was measured"
            )

    def to_dict(self) -> dict[str, Any]:
        return {
            "backend_id": self.backend_id,
            "capability": self.capability,
            "status": self.status,
            "coverage": self.coverage,
            "success_given_covered": self.success_given_covered,
            "threshold": self.threshold,
            "max_risk": self.max_risk,
            "split_id": self.split_id,
            "contract_hash": self.contract_hash,
            "evidence": list(self.evidence),
            "note": self.note,
        }


def _manifest_path() -> Path:
    return Path(__file__).resolve().parents[3] / "manifests" / "capability_trust.json"


def assert_no_global_trust(payload: dict[str, Any]) -> None:
    """Refuse a global per-model trust flag.

    Accepts either the file shape ``{"models": {id: {"capabilities": {...}}}}`` or
    a flat mapping. Raises if any model carries a bare boolean trust flag instead
    of per-capability records.
    """
    models = payload.get("models") if isinstance(payload.get("models"), dict) else payload
    offenders: list[str] = []
    for model_id, entry in (models or {}).items():
        if not isinstance(entry, dict):
            continue
        for key in ("trusted", "is_trusted", "trust"):
            if isinstance(entry.get(key), bool):
                offenders.append(f"{model_id}.{key}")
        caps = entry.get("capabilities")
        if caps is not None and not isinstance(caps, dict):
            offenders.append(f"{model_id}.capabilities (must be a mapping of capability -> record)")
    if offenders:
        raise ValueError(
            "global model trust is not an expressible primitive; use per-capability "
            f"records instead. Offending keys: {', '.join(sorted(offenders))}"
        )


def load_trust_payload(*, path: Path | None = None) -> dict[str, Any]:
    p = path or _manifest_path()
    if not p.is_file():
        return {"schema": TRUST_SCHEMA, "models": {}}
    payload = json.loads(p.read_text(encoding="utf-8"))
    assert_no_global_trust(payload)
    return payload


def trust_records(*, path: Path | None = None) -> tuple[CapabilityTrust, ...]:
    payload = load_trust_payload(path=path)
    models = payload.get("models") or {}
    out: list[CapabilityTrust] = []
    for backend_id, entry in sorted(models.items()):
        for capability, rec in sorted(((entry or {}).get("capabilities") or {}).items()):
            out.append(
                CapabilityTrust(
                    backend_id=backend_id,
                    capability=capability,
                    status=str(rec.get("status", "untested")),
                    coverage=rec.get("coverage"),
                    success_given_covered=rec.get("success_given_covered"),
                    threshold=rec.get("threshold"),
                    max_risk=rec.get("max_risk"),
                    split_id=rec.get("split_id"),
                    contract_hash=rec.get("contract_hash"),
                    evidence=tuple(rec.get("evidence") or ()),
                    note=rec.get("note"),
                )
            )
    return tuple(out)


def trust_for(backend_id: str, capability: str, *, path: Path | None = None) -> CapabilityTrust | None:
    for r in trust_records(path=path):
        if r.backend_id == backend_id and r.capability == capability:
            return r
    return None


def status_for(backend_id: str, capability: str, *, path: Path | None = None) -> str:
    """The trust status, defaulting to ``untested`` — never to trusted."""
    r = trust_for(backend_id, capability, path=path)
    return r.status if r else "untested"


def may_serve(backend_id: str, capability: str, *, path: Path | None = None) -> bool:
    """True only when the capability is explicitly trusted for a production path."""
    return status_for(backend_id, capability, path=path) in PRODUCTION_STATUSES


def trust_table(*, path: Path | None = None) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for r in trust_records(path=path):
        out.setdefault(r.backend_id, {})[r.capability] = r.to_dict()
    return out
