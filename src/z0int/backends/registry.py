"""Lazy DecisionBackend registry."""

from __future__ import annotations

import os
from dataclasses import asdict, dataclass
from typing import Any, Callable

from .base import DecisionBackend


def _linear_from_env() -> DecisionBackend:
    """Build the linear backend, loading fitted weights when pointed at them.

    ``Z0INT_LINEAR_WEIGHTS`` lets a host process and a worker process construct
    the SAME fitted model. Without it a worker always builds an untrained
    backend, so a host-vs-service comparison would compare two different models
    and any difference would be unattributable.
    """
    from .linear import LinearDecisionBackend

    path = os.environ.get("Z0INT_LINEAR_WEIGHTS")
    if path:
        return LinearDecisionBackend.load(path)
    return LinearDecisionBackend(kind="ridge")


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

    if "mushroom" not in _REGISTRY:
        from .mushroom import MushroomBackend

        register(
            BackendSpec(
                id="mushroom",
                kind="local_plasticity_specialist",
                description=(
                    "Mushroom-body recovery specialist (Kenyon cells -> MBON, 640 params). "
                    "Recovery only: consumes an 8x15 Hermes frame tensor."
                ),
                factory=MushroomBackend,
                aliases=("mushroom_body", "mushroom-body"),
                local=True,
            )
        )
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
    # --- registered after the fact -----------------------------------------
    #
    # `laya.py` and `openjev_direct.py` shipped as working adapters with
    # `health().ready is True`, installed runtimes and present weights, but were
    # never added here. Because every roster, bench run, Pareto plot and shadow
    # lane enumerates backends through this registry, both were invisible to the
    # entire evaluation stack. Laya — the cheapest calibrated decision model in
    # the portfolio, and the one the research pass identified as the best
    # fine-tuning substrate — had never appeared in a single comparison.
    #
    # Registration is metadata only: the factories are lazy and `health()` is
    # a filesystem probe that never downloads (see
    # `models_mgmt.require_cached_snapshot`).
    if "laya_421m" not in _REGISTRY:
        from .laya import LayaBackend

        register(
            BackendSpec(
                id="laya_421m",
                kind="calibrated_decision",
                description=(
                    "Laya 421M non-autoregressive typed decision model "
                    "(ModernBERT-large + decision head; choice/score/noul)"
                ),
                factory=lambda: LayaBackend.for_manifest_id("laya_421m"),
                aliases=("laya",),
                local=True,
            )
        )
    if "openjev_06b" not in _REGISTRY:
        from .openjev_direct import OpenJevDirectBackend

        register(
            BackendSpec(
                id="openjev_06b",
                kind="direct_option_logits",
                description="OpenJev direct option-logit readout on Qwen/Qwen3-0.6B",
                factory=lambda: OpenJevDirectBackend.for_manifest_id("openjev_06b"),
                aliases=("openjev",),
                local=True,
            )
        )
    if "openjev_4b" not in _REGISTRY:
        from .openjev_direct import OpenJevDirectBackend

        register(
            BackendSpec(
                id="openjev_4b",
                kind="direct_option_logits",
                description="OpenJev direct option-logit readout on Qwen/Qwen3.5-4B",
                factory=lambda: OpenJevDirectBackend.for_manifest_id("openjev_4b"),
                local=True,
            )
        )
    # The cheap transferred baseline. CPU-resident, numpy-only, no GPU and no
    # weight download, so it is the backend that can actually be served from a
    # host process AND a worker process to prove they agree.
    if "linear" not in _REGISTRY:
        from .linear import LinearDecisionBackend

        register(
            BackendSpec(
                id="linear",
                kind="linear_baseline",
                description=(
                    "ridge/logistic linear baseline over hashed state features. "
                    "Cheap, CPU-resident, abstains on low confidence."
                ),
                factory=_linear_from_env,
                aliases=("ridge", "logistic"),
                local=True,
            )
        )
