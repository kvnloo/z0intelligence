"""Canonical local-cognition model capability manifest (z0intelligence#20).

Two kinds of evidence live here and are *never* mixed:

``source_reported_benchmarks``
    What the upstream model card / paper claims. Marketing. Never used to
    select, rank or promote a model at runtime.

``measurements``
    What actually happened on a named machine, recorded by
    :mod:`z0int.cognition.probe` as a reproducible receipt.

Selection helpers only read ``measurements`` and ``local_benchmark_receipt_ids``.
That invariant is enforced by :func:`assert_selection_is_evidence_based` and
covered by tests.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Mapping
import json

SCHEMA = "z0int.local_cognition.v1"

PROMOTION_STATES = (
    "candidate",
    "shadow",
    "credited",
    "promoted",
    "rejected",
    "unavailable",
)

# Roles from z0intelligence#20. A model may hold several.
ROLES = (
    "tiny_action_specialist",
    "bounded_scorer",
    "general_function_caller",
    "semantic_orchestrator",
    "general_local_fallback",
    "remote_specialist_fallback",
)

# Licences that forbid commercial use. Marking one of these commercial_use=True
# is a real deployment hazard, so it fails validation.
NON_COMMERCIAL_LICENSES = frozenset(
    {
        "cc-by-nc-4.0",
        "cc-by-nc-sa-4.0",
        "cc-by-nc-nd-4.0",
        "qwen-research",
        "nvidia-license",
        "non-commercial",
    }
)


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[3]


def default_manifest_path() -> Path:
    return _repo_root() / "manifests" / "local_cognition.v1.json"


@dataclass(frozen=True)
class Measurement:
    """One locally reproduced serving measurement for one (runtime, quant, ctx)."""

    machine: str
    runtime: str
    quantization: str
    context: int
    measured_vram_mib: float | None = None
    vram_idle_mib: float | None = None
    vram_peak_mib: float | None = None
    cold_start_ms: float | None = None
    warm_ttft_ms: float | None = None
    decode_tok_s: float | None = None
    short_decision_ms: float | None = None
    long_decision_ms: float | None = None
    gpu_util_pct: float | None = None
    receipt_id: str | None = None
    measured_at: str | None = None
    notes: str | None = None

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> Measurement:
        known = {f.name for f in cls.__dataclass_fields__.values()}  # type: ignore[attr-defined]
        return cls(**{k: v for k, v in raw.items() if k in known})  # type: ignore[arg-type]

    def to_dict(self) -> dict[str, Any]:
        return {k: v for k, v in self.__dict__.items() if v is not None}

    @property
    def key(self) -> tuple[str, str, int]:
        return (self.runtime, self.quantization, int(self.context))


@dataclass(frozen=True)
class SourceClaim:
    """An upstream-reported number. Recorded, never used for selection."""

    name: str
    value: str
    dataset: str | None = None
    setup_note: str | None = None
    source_url: str | None = None
    unverified: bool = False

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> SourceClaim:
        return cls(
            name=str(raw.get("name") or ""),
            value=str(raw.get("value") or ""),
            dataset=raw.get("dataset"),
            setup_note=raw.get("setup_note"),
            source_url=raw.get("source_url"),
            unverified=bool(raw.get("unverified", False)),
        )

    def to_dict(self) -> dict[str, Any]:
        return {k: v for k, v in self.__dict__.items() if v is not None}


@dataclass(frozen=True)
class ModelCapability:
    model_id: str
    hf: str | None = None
    revision: str | None = None
    role_tags: tuple[str, ...] = ()
    parameter_count: str | None = None
    license: str | None = None
    gated_access: bool = False
    commercial_use: bool | None = None
    supported_runtimes: tuple[str, ...] = ()
    available_quantizations: tuple[str, ...] = ()
    tool_call_template: str | None = None
    tool_parser: str | None = None
    structured_output_support: str | None = None
    capabilities: Mapping[str, bool] = field(default_factory=dict)
    tested_context: int | None = None
    tested_quantization: str | None = None
    supports_confidence: bool | None = None
    supports_abstention: bool | None = None
    measurements: tuple[Measurement, ...] = ()
    source_reported_benchmarks: tuple[SourceClaim, ...] = ()
    local_benchmark_receipt_ids: tuple[str, ...] = ()
    promotion_state: str = "candidate"
    deployment_constraints: tuple[str, ...] = ()
    mechanism: str | None = None
    notes: str | None = None

    # ---- validation -------------------------------------------------
    def __post_init__(self) -> None:
        if not self.model_id.strip():
            raise ValueError("model_id must be nonempty")
        unknown_roles = [r for r in self.role_tags if r not in ROLES]
        if unknown_roles:
            raise ValueError(f"{self.model_id}: unknown role_tags {unknown_roles}")
        if self.promotion_state not in PROMOTION_STATES:
            raise ValueError(
                f"{self.model_id}: unknown promotion_state {self.promotion_state!r}"
            )
        # Gating is about *access*, not commercial rights: a gated model may
        # still be commercially usable (e.g. Gemma under the Gemma Terms).
        # What must never happen is a non-commercial licence silently marked
        # commercial.
        licence = (self.license or "").lower()
        if self.commercial_use is True and licence in NON_COMMERCIAL_LICENSES:
            raise ValueError(
                f"{self.model_id}: licence {self.license!r} is non-commercial but "
                "commercial_use=True"
            )

    # ---- evidence separation ---------------------------------------
    @property
    def measured(self) -> bool:
        """True only when this model has at least one local reproduction."""
        return bool(self.measurements)

    def measurement_for(
        self, *, runtime: str, quantization: str, context: int | None = None
    ) -> Measurement | None:
        for m in self.measurements:
            if m.runtime != runtime or m.quantization != quantization:
                continue
            if context is not None and int(m.context) != int(context):
                continue
            return m
        return None

    def best_measurement(self, *, metric: str = "decode_tok_s") -> Measurement | None:
        scored = [m for m in self.measurements if getattr(m, metric, None) is not None]
        if not scored:
            return None
        return max(scored, key=lambda m: float(getattr(m, metric)))  # type: ignore[arg-type]

    def to_dict(self) -> dict[str, Any]:
        return {
            "model_id": self.model_id,
            "hf": self.hf,
            "revision": self.revision,
            "role_tags": list(self.role_tags),
            "parameter_count": self.parameter_count,
            "license": self.license,
            "gated_access": self.gated_access,
            "commercial_use": self.commercial_use,
            "supported_runtimes": list(self.supported_runtimes),
            "available_quantizations": list(self.available_quantizations),
            "tool_call_template": self.tool_call_template,
            "tool_parser": self.tool_parser,
            "structured_output_support": self.structured_output_support,
            "capabilities": dict(self.capabilities),
            "tested_context": self.tested_context,
            "tested_quantization": self.tested_quantization,
            "supports_confidence": self.supports_confidence,
            "supports_abstention": self.supports_abstention,
            "measurements": [m.to_dict() for m in self.measurements],
            "source_reported_benchmarks": [c.to_dict() for c in self.source_reported_benchmarks],
            "local_benchmark_receipt_ids": list(self.local_benchmark_receipt_ids),
            "promotion_state": self.promotion_state,
            "deployment_constraints": list(self.deployment_constraints),
            "mechanism": self.mechanism,
            "notes": self.notes,
        }


@dataclass(frozen=True)
class LocalCognitionManifest:
    schema: str
    revision: str | None
    machines: Mapping[str, Any]
    models: Mapping[str, ModelCapability]
    role_defaults: Mapping[str, str] = field(default_factory=dict)
    path: Path | None = None

    # ---- lookups ----------------------------------------------------
    def get(self, model_id: str) -> ModelCapability:
        try:
            return self.models[model_id]
        except KeyError as exc:
            raise KeyError(f"unknown model_id {model_id!r}") from exc

    def by_role(self, role: str) -> tuple[ModelCapability, ...]:
        if role not in ROLES:
            raise ValueError(f"unknown role {role!r}")
        return tuple(m for m in self.models.values() if role in m.role_tags)

    def selectable(
        self,
        role: str,
        *,
        machine: str | None = None,
        require_measured: bool = True,
    ) -> tuple[ModelCapability, ...]:
        """Candidates for a role, filtered only by *locally reproduced* evidence."""
        out: list[ModelCapability] = []
        for m in self.by_role(role):
            if m.promotion_state in ("rejected", "unavailable"):
                continue
            if require_measured and not m.measured:
                continue
            if machine is not None:
                if not any(x.machine == machine for x in m.measurements):
                    continue
            out.append(m)
        return tuple(out)

    def role_default(self, role: str) -> str | None:
        return self.role_defaults.get(role)

    def promotion_state(self, model_id: str) -> str:
        return self.get(model_id).promotion_state

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "revision": self.revision,
            "machines": dict(self.machines),
            "role_defaults": dict(self.role_defaults),
            "models": {k: v.to_dict() for k, v in self.models.items()},
        }


def _parse_model(model_id: str, raw: Mapping[str, Any]) -> ModelCapability:
    return ModelCapability(
        model_id=str(raw.get("model_id") or model_id),
        hf=raw.get("hf"),
        revision=raw.get("revision"),
        role_tags=tuple(raw.get("role_tags") or ()),
        parameter_count=raw.get("parameter_count"),
        license=raw.get("license"),
        gated_access=bool(raw.get("gated_access", False)),
        commercial_use=raw.get("commercial_use"),
        supported_runtimes=tuple(raw.get("supported_runtimes") or ()),
        available_quantizations=tuple(raw.get("available_quantizations") or ()),
        tool_call_template=raw.get("tool_call_template"),
        tool_parser=raw.get("tool_parser"),
        structured_output_support=raw.get("structured_output_support"),
        capabilities=dict(raw.get("capabilities") or {}),
        tested_context=raw.get("tested_context"),
        tested_quantization=raw.get("tested_quantization"),
        supports_confidence=raw.get("supports_confidence"),
        supports_abstention=raw.get("supports_abstention"),
        measurements=tuple(Measurement.from_dict(m) for m in raw.get("measurements") or ()),
        source_reported_benchmarks=tuple(
            SourceClaim.from_dict(c) for c in raw.get("source_reported_benchmarks") or ()
        ),
        local_benchmark_receipt_ids=tuple(raw.get("local_benchmark_receipt_ids") or ()),
        promotion_state=str(raw.get("promotion_state") or "candidate"),
        deployment_constraints=tuple(raw.get("deployment_constraints") or ()),
        mechanism=raw.get("mechanism"),
        notes=raw.get("notes"),
    )


def parse_manifest(raw: Mapping[str, Any], *, path: Path | None = None) -> LocalCognitionManifest:
    schema = str(raw.get("schema") or "")
    if schema != SCHEMA:
        raise ValueError(f"unsupported manifest schema {schema!r}; expected {SCHEMA!r}")
    models_raw = raw.get("models") or {}
    if not isinstance(models_raw, Mapping) or not models_raw:
        raise ValueError("manifest must declare at least one model")
    models = {mid: _parse_model(str(mid), m or {}) for mid, m in models_raw.items()}
    defaults = {k: str(v) for k, v in (raw.get("role_defaults") or {}).items()}
    for role, mid in defaults.items():
        if role not in ROLES:
            raise ValueError(f"role_defaults: unknown role {role!r}")
        if mid not in models:
            raise ValueError(f"role_defaults: {role!r} points at unknown model {mid!r}")
        if role not in models[mid].role_tags:
            raise ValueError(
                f"role_defaults: {mid!r} does not carry role {role!r}"
            )
    return LocalCognitionManifest(
        schema=schema,
        revision=raw.get("revision"),
        machines=dict(raw.get("machines") or {}),
        models=models,
        role_defaults=defaults,
        path=path,
    )


def load_local_cognition(path: Path | None = None) -> LocalCognitionManifest:
    target = path or default_manifest_path()
    if not target.is_file():
        raise FileNotFoundError(f"no local cognition manifest at {target}")
    return parse_manifest(json.loads(target.read_text(encoding="utf-8")), path=target)


def assert_selection_is_evidence_based(
    manifest: LocalCognitionManifest, *, roles: Iterable[str] | None = None
) -> list[str]:
    """Fail loudly when a selected model has no local evidence.

    Returns the list of ``model_id`` values that are role-defaulted but never
    measured locally. Callers that promote a model must have a measurement.
    """
    offenders: list[str] = []
    wanted = tuple(roles) if roles is not None else ROLES
    for role in wanted:
        mid = manifest.role_default(role)
        if mid is None:
            continue
        model = manifest.get(mid)
        if not model.measured and not model.local_benchmark_receipt_ids:
            offenders.append(mid)
    return offenders
