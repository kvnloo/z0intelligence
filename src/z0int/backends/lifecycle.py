"""Decision-runtime lifecycle plane.

`serving.py` owns the **generative** runtime: llama.cpp / GGUF, one resident
model at a time. That plane is correct and stays as it is.

It is not the only plane. Laya, Decider-2B, NanoJev, OpenJev-direct, the
mushroom readouts and any fitted linear head are non-generative: they load
in-process, answer without decoding, and never touch the llama.cpp supervisor.
Because only the generative plane had lifecycle accounting, every one of those
models was invisible to residency, load-latency and placement reporting — which
is how a portfolio that reads as a 270M→9B size ladder came to exclude every
decision-native model it owned.

This module gives the second plane explicit ownership without introducing a
second gateway. It owns:

* which decision backend is currently resident,
* load / forward / serialisation / total latency per invocation,
* an :class:`InvocationReceipt` per call carrying the runtime-identity fields the
  evidence contract requires,
* ``runtime_status()``, which reports **both** planes in one structure so a
  single status command covers the whole fleet.

It never downloads weights: loading goes through the adapters, whose health and
resolution paths are download-free by construction.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import hashlib
import json
import time

DECISION_RUNTIME_ID = "decision-runtime"
RUNTIME_SCHEMA = "z0int.decision_runtime.v1"


def _now_ms(t0: float) -> float:
    return (time.perf_counter() - t0) * 1000.0


def structural_checkpoint_digest(path: str | None, *, limit: int = 4096) -> str | None:
    """A *structural* digest of a checkpoint directory.

    Hashes the sorted ``(relative path, size)`` pairs rather than file contents,
    because content-hashing a 2.4 GB `best.safetensors` on every inventory call is
    not affordable. The kind is recorded alongside the value so a structural
    digest is never mistaken for a content hash.

    Recorded honestly: this detects a changed or incomplete checkpoint layout. It
    does **not** detect two different weight tensors of identical byte length.
    """
    if not path:
        return None
    from pathlib import Path

    root = Path(path)
    if not root.exists():
        return None
    if root.is_file():
        return "sha256:" + hashlib.sha256(f"file:{root.name}:{root.stat().st_size}".encode()).hexdigest()
    entries: list[tuple[str, int]] = []
    for p in sorted(root.rglob("*")):
        if p.is_file():
            try:
                entries.append((str(p.relative_to(root)), p.stat().st_size))
            except OSError:
                continue
        if len(entries) >= limit:
            break
    blob = json.dumps(entries, separators=(",", ":"), ensure_ascii=False)
    return "sha256:" + hashlib.sha256(blob.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class InvocationReceipt:
    """Runtime-identity and latency accounting for one backend invocation."""

    runtime_id: str
    backend_id: str
    model_id: str | None
    revision: str | None
    checkpoint_digest: str | None
    checkpoint_digest_kind: str
    device: str
    resident_before: str | None
    resident_after: str | None
    load_ms: float
    forward_ms: float
    serialization_ms: float
    total_ms: float
    network_model_calls: int
    autoregressive_decode_steps: int | None
    trace_id: str | None = None
    candidate_count: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": RUNTIME_SCHEMA,
            "runtime_id": self.runtime_id,
            "backend_id": self.backend_id,
            "model_id": self.model_id,
            "revision": self.revision,
            "checkpoint_digest": self.checkpoint_digest,
            "checkpoint_digest_kind": self.checkpoint_digest_kind,
            "device": self.device,
            "resident_before": self.resident_before,
            "resident_after": self.resident_after,
            "load_ms": self.load_ms,
            "forward_ms": self.forward_ms,
            "serialization_ms": self.serialization_ms,
            "total_ms": self.total_ms,
            "network_model_calls": self.network_model_calls,
            "autoregressive_decode_steps": self.autoregressive_decode_steps,
            "trace_id": self.trace_id,
            "candidate_count": self.candidate_count,
        }


class DecisionRuntime:
    """Owns the decision plane: one resident in-process backend at a time.

    Deliberately mirrors the generative supervisor's shape — one resident model,
    swap on request — so the two planes can eventually be offered to Kerdoios as
    comparable ResourceOffers without a gateway in between.
    """

    def __init__(self) -> None:
        self._resident: str | None = None
        self._loaded: dict[str, Any] = {}
        self._last_load_ms: float | None = None
        self.receipts: list[InvocationReceipt] = []

    # -- introspection ------------------------------------------------------
    @property
    def resident(self) -> str | None:
        return self._resident

    def loaded_ids(self) -> list[str]:
        return sorted(self._loaded)

    # -- lifecycle ----------------------------------------------------------
    def load(self, backend_id: str) -> tuple[Any, float]:
        """Load (or reuse) a backend. Returns ``(backend, load_ms)``."""
        from . import registry

        resolved = registry.resolve_backend_id(backend_id)
        # Short-circuit BEFORE constructing. Reviewer A noticed the adapter was
        # built and thrown away on a same-backend reload; the dead-code half of
        # that finding was wrong (the branch is reachable), the waste was not.
        if self._resident == resolved and resolved in self._loaded:
            return self._loaded[resolved], 0.0
        if self._resident is not None:
            # One resident at a time, same as the generative plane.
            self.unload()
        backend = registry.create_backend(resolved)
        t0 = time.perf_counter()
        backend.health(load=True)
        load_ms = _now_ms(t0)
        self._loaded[resolved] = backend
        self._resident = resolved
        self._last_load_ms = load_ms
        return backend, load_ms

    def unload(self) -> str | None:
        """Drop the resident backend. Returns the id that was released."""
        prev = self._resident
        if prev is not None:
            self._loaded.pop(prev, None)
        self._resident = None
        return prev

    # -- invocation ---------------------------------------------------------
    def evaluate(self, backend_id: str, request: Any, *, trace_id: str | None = None) -> tuple[Any, InvocationReceipt]:
        from . import registry

        resolved = registry.resolve_backend_id(backend_id)
        resident_before = self._resident

        t_all = time.perf_counter()

        # serialisation of the request boundary (state + questions -> backend form)
        t_ser = time.perf_counter()
        try:
            json.dumps(request.state, ensure_ascii=False, allow_nan=False, sort_keys=True)
        except (TypeError, ValueError):
            pass
        serialization_ms = _now_ms(t_ser)

        backend, load_ms = self.load(resolved)
        caps = backend.capabilities

        t_fwd = time.perf_counter()
        result = backend.evaluate(request)
        forward_ms = _now_ms(t_fwd)

        total_ms = _now_ms(t_all)

        diag = getattr(result, "diagnostics", None) or {}
        network_calls = int(diag.get("network_model_calls", 0) or 0)
        if caps.autoregressive_decode:
            decode_steps: int | None = diag.get("autoregressive_decode_steps")
        else:
            decode_steps = 0

        candidates: int | None = None
        try:
            q = request.questions[0]
            if q.type == "choice":
                candidates = len(q.options)
            elif q.type == "score":
                candidates = len(q.levels)
            elif q.type == "boolean":
                candidates = 2
        except (AttributeError, IndexError, TypeError):
            candidates = None

        status = backend.health(load=False)
        receipt = InvocationReceipt(
            runtime_id=DECISION_RUNTIME_ID,
            backend_id=resolved,
            model_id=getattr(status, "model", None),
            revision=(status.diagnostics or {}).get("revision"),
            checkpoint_digest=structural_checkpoint_digest(getattr(status, "checkpoint", None)),
            checkpoint_digest_kind="structural",
            device=str((status.diagnostics or {}).get("device", "unknown")),
            resident_before=resident_before,
            resident_after=self._resident,
            load_ms=load_ms,
            forward_ms=forward_ms,
            serialization_ms=serialization_ms,
            total_ms=total_ms,
            network_model_calls=network_calls,
            autoregressive_decode_steps=decode_steps,
            trace_id=trace_id,
            candidate_count=candidates,
        )
        self.receipts.append(receipt)
        return result, receipt

    # -- reporting ----------------------------------------------------------
    def status(self) -> dict[str, Any]:
        from . import registry

        rows = []
        for b in registry.backend_status():
            rows.append(
                {
                    "backend_id": b["id"],
                    "kind": b["kind"],
                    "ready": b["ready"],
                    "loaded": b["id"] in self._loaded,
                    "model_id": b.get("model"),
                    "checkpoint": b.get("checkpoint"),
                    "detail": b.get("detail"),
                    # Self-declared; may disagree with measurement. See
                    # docs/model-inventory.md on nanojev declaring
                    # supports_batch_questions=False while answering a batch of 8.
                    "supports_batch_questions": b["capabilities"].get("supports_batch_questions"),
                    "autoregressive_decode": b["capabilities"].get("autoregressive_decode"),
                }
            )
        return {
            "runtime_id": DECISION_RUNTIME_ID,
            "kind": "decision",
            "implementation": "in-process DecisionBackend workers",
            "resident": self._resident,
            "resident_since_load_ms": self._last_load_ms,
            "backends": rows,
            "invocations": len(self.receipts),
        }


def generative_plane_status(*, timeout_s: float = 3.0) -> dict[str, Any]:
    """Report the llama.cpp / GGUF plane. Read-only; never starts a server."""
    from z0int import paths

    cfg_path = paths.home() / "config" / "serving.json"
    out: dict[str, Any] = {
        "runtime_id": "llama.cpp-supervisor",
        "kind": "generative",
        "implementation": "llama.cpp / GGUF",
        "config": str(cfg_path),
        "reachable": False,
        "resident": None,
        "models": [],
    }
    if not cfg_path.is_file():
        out["detail"] = "serving.json not found"
        return out
    try:
        cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        out["detail"] = f"serving.json unreadable: {exc}"
        return out

    out["supervisor_base_url"] = (cfg.get("supervisor") or {}).get("base_url")
    out["models"] = [
        {
            "model_id": e.get("model_id"),
            "quantization": e.get("quantization"),
            "runtime": e.get("runtime"),
            "backend_id": e.get("backend_id"),
        }
        for e in (cfg.get("endpoints") or [])
    ]

    base = (cfg.get("supervisor") or {}).get("base_url")
    if not base:
        return out
    try:
        import urllib.request

        with urllib.request.urlopen(f"{base.rstrip('/')}/health", timeout=timeout_s) as fh:
            health = json.loads(fh.read().decode("utf-8"))
        out["reachable"] = True
        out["runtime_version"] = health.get("runtime")
        # `resident` is what a live runtime reports is loaded *now*, not what a
        # config file claims should be.
        out["resident"] = health.get("resident")
    except Exception as exc:  # noqa: BLE001
        out["detail"] = f"supervisor unreachable: {exc}"
    return out


def runtime_status(*, decision: DecisionRuntime | None = None) -> dict[str, Any]:
    """One command, both planes.

    ``resident`` everywhere in this structure means *a live runtime reports it
    loaded now*. A config file stating an intention is not residency.
    """
    plane = decision or DecisionRuntime()
    return {
        "schema": "z0int.runtime_status.v1",
        "planes": {
            "generative": generative_plane_status(),
            "decision": plane.status(),
        },
        "note": (
            "generative = llama.cpp/GGUF (serving.json); decision = in-process "
            "DecisionBackend workers. One ownership layer, two inference "
            "substrates. `resident` is reported by the live runtime, never by config."
        ),
    }
