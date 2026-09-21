"""Bind model ids and roles to runnable backends.

The registry is the only place that knows "which served endpoint is which model".
Semantic selection (:mod:`z0int.cognition.cascade`) and placement (Kerdoios) stay
elsewhere.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping
import json
import os
from pathlib import Path

from .adapters.local_slm import LocalSLMBackend, ToolDecisionBackend
from .cascade import CognitionCascade
from .escalation import EscalationPolicy, EscalationThresholds
from .manifest import LocalCognitionManifest, ModelCapability, load_local_cognition

SERVING_SCHEMA = "z0int.serving.v1"


@dataclass(frozen=True)
class ServingEndpoint:
    """Where a model actually answers requests right now."""

    model_id: str
    base_url: str
    served_model: str
    runtime: str
    quantization: str | None = None
    api_key: str | None = None
    backend_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        out = {
            "model_id": self.model_id,
            "base_url": self.base_url,
            "served_model": self.served_model,
            "runtime": self.runtime,
        }
        if self.quantization:
            out["quantization"] = self.quantization
        if self.backend_id:
            out["backend_id"] = self.backend_id
        return out


def default_serving_path() -> Path:
    from .. import paths

    return paths.home() / "config" / "serving.json"


def load_serving(path: Path | None = None) -> dict[str, ServingEndpoint]:
    """Read the serving map. Missing file is not an error — it means nothing is served."""
    target = path or default_serving_path()
    if not target.is_file():
        return {}
    raw = json.loads(target.read_text(encoding="utf-8"))
    endpoints: dict[str, ServingEndpoint] = {}
    for entry in raw.get("endpoints") or []:
        mid = str(entry.get("model_id") or "")
        if not mid:
            continue
        endpoints[mid] = ServingEndpoint(
            model_id=mid,
            base_url=str(entry["base_url"]),
            served_model=str(entry.get("served_model") or mid),
            runtime=str(entry.get("runtime") or "unknown"),
            quantization=entry.get("quantization"),
            api_key=entry.get("api_key") or os.environ.get("Z0INT_LOCAL_API_KEY"),
            backend_id=entry.get("backend_id"),
        )
    return endpoints


def serving_from_env(model_ids: Mapping[str, str] | None = None) -> dict[str, ServingEndpoint]:
    """Minimal env-driven fallback: ``Z0INT_LOCAL_BASE_URL`` + ``Z0INT_LOCAL_MODEL``."""
    base = os.environ.get("Z0INT_LOCAL_BASE_URL")
    model = os.environ.get("Z0INT_LOCAL_MODEL")
    if not base or not model:
        return {}
    mid = (model_ids or {}).get(model, model)
    return {
        mid: ServingEndpoint(
            model_id=mid,
            base_url=base,
            served_model=model,
            runtime=os.environ.get("Z0INT_LOCAL_RUNTIME", "unknown"),
            quantization=os.environ.get("Z0INT_LOCAL_QUANT"),
        )
    }


def build_backend(
    capability: ModelCapability,
    endpoint: ServingEndpoint,
    *,
    backend_id: str | None = None,
) -> LocalSLMBackend:
    return LocalSLMBackend.for_capability(
        capability,
        base_url=endpoint.base_url,
        model=endpoint.served_model,
        api_key=endpoint.api_key,
        runtime=endpoint.runtime,
        quantization=endpoint.quantization or capability.tested_quantization,
        backend_id=backend_id or endpoint.backend_id or capability.model_id,
    )


@dataclass
class LocalModelRegistry:
    """Manifest + serving map -> runnable per-role backends."""

    manifest: LocalCognitionManifest
    endpoints: Mapping[str, ServingEndpoint] = field(default_factory=dict)
    _cache: dict[str, ToolDecisionBackend] = field(default_factory=dict)

    @classmethod
    def from_environment(
        cls, *, manifest_path: Path | None = None, serving_path: Path | None = None
    ) -> LocalModelRegistry:
        manifest = load_local_cognition(manifest_path)
        endpoints = load_serving(serving_path)
        if not endpoints:
            endpoints = serving_from_env()
        return cls(manifest=manifest, endpoints=endpoints)

    def served(self) -> tuple[str, ...]:
        return tuple(sorted(self.endpoints))

    def backend_for(self, model_id: str, *, backend_id: str | None = None) -> ToolDecisionBackend:
        key = backend_id or model_id
        if key in self._cache:
            return self._cache[key]
        capability = self.manifest.get(model_id)
        endpoint = self.endpoints.get(model_id)
        if endpoint is None:
            raise RuntimeError(
                f"{model_id} is not served; add it to "
                f"{default_serving_path()} or export Z0INT_LOCAL_BASE_URL"
            )
        backend = build_backend(capability, endpoint, backend_id=backend_id)
        self._cache[key] = backend
        return backend

    def role_backend(self, role: str, *, require_measured: bool = False) -> ToolDecisionBackend | None:
        model_id = self.manifest.role_default(role)
        if model_id is None:
            return None
        if require_measured:
            candidates = self.manifest.selectable(role, require_measured=True)
            if not candidates:
                return None
            # Prefer the declared default only if it has local evidence.
            if model_id not in {c.model_id for c in candidates}:
                model_id = candidates[0].model_id
        if model_id not in self.endpoints:
            return None
        return self.backend_for(model_id)

    def cascade(
        self,
        *,
        jev: ToolDecisionBackend | None = None,
        tiny: ToolDecisionBackend | None = None,
        remote: ToolDecisionBackend | None = None,
        thresholds: EscalationThresholds | None = None,
    ) -> CognitionCascade:
        return CognitionCascade(
            tiny=tiny or self.role_backend("tiny_action_specialist"),
            jev=jev or self.role_backend("bounded_scorer"),
            orchestrator=self.role_backend("semantic_orchestrator"),
            general=self.role_backend("general_local_fallback"),
            remote=remote,
            policy=EscalationPolicy(thresholds),
        )

    def health(self) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for mid in self.served():
            try:
                out[mid] = self.backend_for(mid).health()
            except Exception as exc:  # noqa: BLE001
                out[mid] = {"ready": False, "detail": f"{type(exc).__name__}: {exc}"}
        return out


__all__ = [
    "LocalModelRegistry",
    "SERVING_SCHEMA",
    "ServingEndpoint",
    "build_backend",
    "default_serving_path",
    "load_serving",
    "serving_from_env",
]
