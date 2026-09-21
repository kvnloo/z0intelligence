"""Local model supervisor: one OpenAI-compatible endpoint, one resident model.

Why this exists
---------------
Measurement and production must be the same stack. A router trained on
llama.cpp latency labels while requests actually execute through a different
runtime learns the runtime, not the model. This supervisor is the single
production endpoint: clients always talk to one base URL, and it swaps the
underlying ``llama-server`` GGUF when the requested model changes.

It also makes the *load* cost part of the label. A cold request against a model
that is not resident pays a real load; the supervisor records it and forwards
the server's own timings, so a router sees the true end-to-end cost of choosing
that model right now.

Placement policy stays deliberately trivial here (one resident model, LRU
eviction, idle unload). Semantic suitability is not decided here -- that is
z0intelligence's cognition plane; residual placement belongs to Kerdoios.
"""

from __future__ import annotations

import json
import os
import threading
import time
from dataclasses import dataclass, field
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Mapping

from .manifest import load_local_cognition
from .serving import (
    DEFAULT_GGUF_DIR,
    DEFAULT_LLAMA_SERVER,
    GGUF_LAYOUT,
    LlamaServer,
    llama_build,
    quant_from_name,
)

SCHEMA = "z0int.local_model_supervisor.v1"


@dataclass
class Residency:
    """Recorded evidence for one load/evict cycle."""

    model_id: str
    runtime: str
    quantization: str
    context: int
    loaded_at: float
    load_ms: float
    evicted_at: float | None = None
    requests: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "model_id": self.model_id,
            "runtime": self.runtime,
            "quantization": self.quantization,
            "context": self.context,
            "loaded_at": self.loaded_at,
            "load_ms": self.load_ms,
            "evicted_at": self.evicted_at,
            "requests": self.requests,
        }


class SupervisorError(RuntimeError):
    pass


class ModelSupervisor:
    """Owns at most one llama-server child and proxies to it."""

    def __init__(
        self,
        *,
        binary: str = DEFAULT_LLAMA_SERVER,
        gguf_dir: str | Path = DEFAULT_GGUF_DIR,
        context_by_model: Mapping[str, int] | None = None,
        default_context: int = 4096,
        idle_unload_s: float = 300.0,
        max_tokens_cap: int = 4096,
    ) -> None:
        self.binary = binary
        self.gguf_dir = Path(gguf_dir)
        self.context_by_model = dict(context_by_model or {})
        self.default_context = default_context
        self.idle_unload_s = idle_unload_s
        self.max_tokens_cap = max_tokens_cap

        self._lock = threading.RLock()
        self._server: LlamaServer | None = None
        self._model_id: str | None = None
        self._last_use = 0.0
        self._history: list[Residency] = []
        self._current: Residency | None = None
        self._runtime = f"llama.cpp-{llama_build(binary)}"

        manifest = load_local_cognition()
        self._known = [m for m in GGUF_LAYOUT if m in manifest.models]

    # ---- inventory --------------------------------------------------
    @property
    def runtime(self) -> str:
        return self._runtime

    def known_models(self) -> list[str]:
        return list(self._known)

    @property
    def resident(self) -> str | None:
        return self._model_id

    def history(self) -> list[dict[str, Any]]:
        with self._lock:
            return [r.to_dict() for r in self._history]

    def status(self) -> dict[str, Any]:
        with self._lock:
            return {
                "schema": SCHEMA,
                "runtime": self._runtime,
                "resident": self._model_id,
                "known_models": list(self._known),
                "idle_unload_s": self.idle_unload_s,
                "history": self.history(),
            }

    # ---- residency --------------------------------------------------
    def _context_for(self, model_id: str) -> int:
        return int(self.context_by_model.get(model_id, self.default_context))

    def ensure(self, model_id: str) -> LlamaServer:
        """Make ``model_id`` resident, evicting whatever is there."""
        if model_id not in self._known:
            raise SupervisorError(f"unknown model {model_id!r}")
        with self._lock:
            if self._server is not None and self._model_id == model_id:
                self._last_use = time.time()
                return self._server
            self._evict_locked("swap")
            gguf = self.gguf_dir / GGUF_LAYOUT[model_id]
            if not gguf.is_file():
                raise SupervisorError(f"missing GGUF for {model_id}: {gguf}")
            started = time.time()
            server = LlamaServer(
                binary=self.binary,
                gguf=gguf,
                context=self._context_for(model_id),
                extra_args=("--no-webui",),
            ).start()
            load_ms = (time.time() - started) * 1000.0
            self._server = server
            self._model_id = model_id
            self._last_use = time.time()
            self._current = Residency(
                model_id=model_id,
                runtime=self._runtime,
                quantization=quant_from_name(gguf.name),
                context=self._context_for(model_id),
                loaded_at=time.time(),
                load_ms=load_ms,
            )
            return server

    def _evict_locked(self, reason: str) -> None:
        if self._server is None:
            return
        self._server.stop()
        if self._current is not None:
            self._current.evicted_at = time.time()
            self._history.append(self._current)
        self._server = None
        self._current = None
        self._model_id = None

    def maybe_idle_unload(self) -> bool:
        with self._lock:
            if self._server is None or self.idle_unload_s <= 0:
                return False
            if time.time() - self._last_use >= self.idle_unload_s:
                self._evict_locked("idle")
                return True
            return False

    def shutdown(self) -> None:
        with self._lock:
            self._evict_locked("shutdown")

    # ---- proxying ---------------------------------------------------
    def forward(self, model_id: str, payload: Mapping[str, Any]) -> tuple[int, dict[str, Any]]:
        """Send one chat completion to the resident model and return its reply."""
        t_start = time.time()
        try:
            server = self.ensure(model_id)
        except (SupervisorError, RuntimeError) as exc:
            return 503, {"error": {"message": str(exc), "type": "model_unavailable"}}
        load_ms = 0.0
        with self._lock:
            if self._current is not None:
                self._current.requests += 1
                load_ms = self._current.load_ms if self._current.requests == 1 else 0.0

        body = dict(payload)
        body["model"] = model_id  # llama-server ignores it, but keep it coherent
        if body.get("max_tokens"):
            body["max_tokens"] = min(int(body["max_tokens"]), self.max_tokens_cap)
        data = json.dumps(body).encode("utf-8")
        req = Request(
            f"{server.base_url}/v1/chat/completions",
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urlopen(req, timeout=600) as resp:
                out = json.loads(resp.read().decode("utf-8"))
        except HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")[:400]
            return exc.code, {"error": {"message": detail, "type": "upstream_error"}}
        except (URLError, OSError, TimeoutError) as exc:
            return 502, {"error": {"message": f"{type(exc).__name__}: {exc}",
                                   "type": "upstream_unreachable"}}
        except json.JSONDecodeError as exc:
            return 502, {"error": {"message": f"non-JSON upstream: {exc}",
                                   "type": "upstream_error"}}

        # Attach supervisor-level facts so a router can see residency cost.
        out.setdefault("z0int_supervisor", {})
        out["z0int_supervisor"].update(
            {
                "schema": SCHEMA,
                "runtime": self._runtime,
                "resident_model": model_id,
                "load_ms": load_ms,
                "cold": load_ms > 0,
                "wall_ms": (time.time() - t_start) * 1000.0,
            }
        )
        return 200, out


# --- HTTP surface -------------------------------------------------------


def _handler_factory(supervisor: ModelSupervisor):
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"
        server_version = "z0int-supervisor/1"

        def log_message(self, fmt: str, *args: Any) -> None:  # noqa: A003
            if os.environ.get("Z0INT_SUPERVISOR_QUIET"):
                return
            super().log_message(fmt, *args)

        def _send(self, code: int, payload: Any) -> None:
            body = json.dumps(payload).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _read_json(self) -> dict[str, Any]:
            length = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(length) if length else b"{}"
            parsed = json.loads(raw.decode("utf-8") or "{}")
            if not isinstance(parsed, dict):
                raise ValueError("body must be a JSON object")
            return parsed

        def do_GET(self) -> None:  # noqa: N802
            path = self.path.split("?", 1)[0]
            if path in ("/health", "/api/version"):
                self._send(200, {"status": "ok", "schema": SCHEMA,
                                 "runtime": supervisor.runtime,
                                 "resident": supervisor.resident})
                return
            if path == "/v1/models":
                self._send(
                    200,
                    {
                        "object": "list",
                        "data": [
                            {"id": m, "object": "model", "owned_by": "z0int"}
                            for m in supervisor.known_models()
                        ],
                    },
                )
                return
            if path == "/z0int/status":
                self._send(200, supervisor.status())
                return
            if path == "/z0int/history":
                self._send(200, {"schema": SCHEMA, "history": supervisor.history()})
                return
            self._send(404, {"error": {"message": f"no route {path}"}})

        def do_POST(self) -> None:  # noqa: N802
            path = self.path.split("?", 1)[0]
            if path != "/v1/chat/completions":
                self._send(404, {"error": {"message": f"no route {path}"}})
                return
            try:
                payload = self._read_json()
            except (ValueError, json.JSONDecodeError) as exc:
                self._send(400, {"error": {"message": f"bad request: {exc}"}})
                return
            model_id = str(payload.get("model") or "")
            if not model_id:
                self._send(400, {"error": {"message": "model is required"}})
                return
            code, out = supervisor.forward(model_id, payload)
            self._send(code, out)

    return Handler


def serve_forever(
    *,
    host: str = "127.0.0.1",
    port: int = 11500,
    supervisor: ModelSupervisor | None = None,
) -> None:
    sup = supervisor or ModelSupervisor(
        idle_unload_s=float(os.environ.get("Z0INT_IDLE_UNLOAD_S", "300"))
    )

    def reaper() -> None:
        while True:
            time.sleep(30)
            try:
                sup.maybe_idle_unload()
            except Exception:  # noqa: BLE001 - the reaper must never die
                pass

    threading.Thread(target=reaper, daemon=True).start()
    httpd = ThreadingHTTPServer((host, port), _handler_factory(sup))
    print(f"z0int model supervisor on http://{host}:{port} runtime={sup.runtime}", flush=True)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        sup.shutdown()


__all__ = ["SCHEMA", "ModelSupervisor", "Residency", "SupervisorError", "serve_forever"]
