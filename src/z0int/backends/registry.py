"""Lazy DecisionBackend registry."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Callable

from .base import DecisionBackend


@dataclass(frozen=True)
class BackendSpec:
    id: str
    kind: str
    description: str
    factory: Callable[[], DecisionBackend]
    aliases: tuple[str, ...] = ()
    local: bool = True


_REGISTRY: dict[str, BackendSpec] = {}
_ALIAS: dict[str, str] = {}
_BUILTINS_LOADED = False


def register(spec: BackendSpec) -> None:
    if spec.id in _REGISTRY:
        raise ValueError(f"backend already registered: {spec.id}")
    _REGISTRY[spec.id] = spec
    for a in spec.aliases:
        if a in _ALIAS or a in _REGISTRY:
            raise ValueError(f"backend alias collision: {a}")
        _ALIAS[a] = spec.id


def list_backend_specs() -> tuple[BackendSpec, ...]:
    register_builtin_backends()
    return tuple(_REGISTRY[k] for k in sorted(_REGISTRY))


def resolve_backend_id(name: str) -> str:
    register_builtin_backends()
    if name in _REGISTRY:
        return name
    if name in _ALIAS:
        return _ALIAS[name]
    raise KeyError(f"unknown backend {name!r}; known={sorted(set(_REGISTRY) | set(_ALIAS))}")


def get_backend_spec(name: str) -> BackendSpec:
    return _REGISTRY[resolve_backend_id(name)]


def create_backend(name: str) -> DecisionBackend:
    return get_backend_spec(name).factory()


def backend_status(name: str | None = None, *, load: bool = False) -> list[dict[str, Any]]:
    """Filesystem/config health without importing torch unless load=True for a named backend."""
    register_builtin_backends()
    ids = [resolve_backend_id(name)] if name else sorted(_REGISTRY)
    out: list[dict[str, Any]] = []
    for bid in ids:
        spec = _REGISTRY[bid]
        # Construct adapter (must be cheap / no GPU) then health(load=...)
        backend = spec.factory()
        health = backend.health(load=load)
        caps = backend.capabilities
        out.append(
            {
                "id": bid,
                "kind": spec.kind,
                "description": spec.description,
                "aliases": list(spec.aliases),
                "local": bool(getattr(caps, "local", spec.local)),
                "configured": health.configured,
                "ready": health.ready,
                "loaded": health.loaded,
                "model": health.model,
                "checkpoint": health.checkpoint,
                "detail": health.detail,
                "diagnostics": health.diagnostics,
                "capabilities": asdict(caps),
            }
        )
    return out


def register_builtin_backends() -> None:
    global _BUILTINS_LOADED
    if _BUILTINS_LOADED:
        return
    _BUILTINS_LOADED = True
    if "laya_421m" not in _REGISTRY:
        from .laya import LayaBackend
        register(BackendSpec(id="laya_421m", kind="typed_decision",
            description="Local Laya experimental verifier", factory=LayaBackend.for_manifest_id,
            aliases=("laya",), local=True))
    if "nanojev" not in _REGISTRY:
        from .nanojev import NanoJevBackend

        register(
            BackendSpec(
                id="nanojev",
                kind="semantic_model",
                description="Local NanoJev Qwen3-0.6B typed decision model",
                factory=NanoJevBackend.from_config,
                aliases=("nanojev_06b",),
                local=True,
            )
        )
    if "decider_2b" not in _REGISTRY:
        from .decider import DeciderBackend

        register(
            BackendSpec(
                id="decider_2b",
                kind="calibrated_decision",
                description="Mapika Decider-2B typed decision model",
                factory=lambda: DeciderBackend.for_manifest_id("decider_2b"),
                aliases=("decider",),
                local=True,
            )
        )
    if "llama_http" not in _REGISTRY:
        from .llama_http import LlamaHttpBackend

        register(
            BackendSpec(
                id="llama_http",
                kind="direct_readout",
                description="Option-letter readout over the host llama.cpp server (any device, any GGUF)",
                factory=LlamaHttpBackend.from_env,
                aliases=("slm", "z0-slm"),
                local=True,
            )
        )
    if "openjev_06b" not in _REGISTRY:
        from .openjev_direct import OpenJevDirectBackend

        register(
            BackendSpec(
                id="openjev_06b",
                kind="direct_option_logits",
                description=(
                    "OpenJev direct option-letter logits on Qwen3-0.6B "
                    "(Z0INT_OPENJEV_DEVICE=cuda|cpu)"
                ),
                factory=lambda: OpenJevDirectBackend.for_manifest_id("openjev_06b"),
                aliases=("openjev",),
                local=True,
            )
        )
    if "julia_1" not in _REGISTRY:
        from .julia import JuliaBackend

        register(
            BackendSpec(
                id="julia_1",
                kind="finite_choice_decision",
                description=(
                    "SupersonicLabs Julia-1 144.3M finite-choice decision model "
                    "(choice/score/noul, 2-20 options); CPU-resident by default"
                ),
                factory=lambda: JuliaBackend.for_manifest_id("julia_1"),
                aliases=("julia", "julia-1"),
                local=True,
            )
        )
