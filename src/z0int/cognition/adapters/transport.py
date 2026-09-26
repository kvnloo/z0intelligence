"""Stdlib-only transport for local model servers.

Deliberately dependency-free: importing adapter metadata must not load torch,
transformers or any HTTP client library (mirrors the rule in
``z0int.backends.base``). Works against any OpenAI-compatible endpoint
(llama.cpp ``llama-server``, Ollama ``/v1``, vLLM, and remote gateways).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping
import json
import time
import urllib.error
import urllib.request


class TransportError(RuntimeError):
    """Any failure to obtain a model response. Callers must fail open."""


@dataclass(frozen=True)
class ServerConfig:
    base_url: str
    model: str
    api: str = "openai"  # openai | ollama
    api_key: str | None = None
    timeout_s: float = 120.0
    runtime: str = "unknown"
    quantization: str | None = None
    revision: str | None = None
    extra_headers: Mapping[str, str] = field(default_factory=dict)

    def url(self, path: str) -> str:
        return self.base_url.rstrip("/") + path


@dataclass
class ChatOutcome:
    content: str
    tool_call: dict[str, Any] | None
    usage: dict[str, Any]
    timings: dict[str, Any]
    raw: dict[str, Any]
    latency_ms: float
    ttft_ms: float | None = None


class OpenAICompatTransport:
    """Minimal blocking JSON-over-HTTP client with honest latency accounting."""

    def __init__(self, config: ServerConfig) -> None:
        self.config = config

    # ---- low level -------------------------------------------------
    def _post(self, path: str, payload: Mapping[str, Any]) -> dict[str, Any]:
        body = json.dumps(payload).encode("utf-8")
        headers = {"Content-Type": "application/json"}
        if self.config.api_key:
            headers["Authorization"] = f"Bearer {self.config.api_key}"
        headers.update(dict(self.config.extra_headers))
        req = urllib.request.Request(
            self.config.url(path), data=body, headers=headers, method="POST"
        )
        try:
            with urllib.request.urlopen(req, timeout=self.config.timeout_s) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:  # pragma: no cover - network path
            detail = exc.read().decode("utf-8", "replace")[:500]
            raise TransportError(f"HTTP {exc.code} from {path}: {detail}") from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise TransportError(f"{type(exc).__name__} calling {path}: {exc}") from exc
        except json.JSONDecodeError as exc:
            raise TransportError(f"non-JSON response from {path}: {exc}") from exc

    def _get(self, path: str) -> dict[str, Any]:
        headers = {}
        if self.config.api_key:
            headers["Authorization"] = f"Bearer {self.config.api_key}"
        req = urllib.request.Request(self.config.url(path), headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=min(self.config.timeout_s, 20.0)) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except (urllib.error.URLError, urllib.error.HTTPError, OSError, TimeoutError) as exc:
            raise TransportError(f"{type(exc).__name__} calling {path}: {exc}") from exc

    # ---- surface ---------------------------------------------------
    def health(self) -> dict[str, Any]:
        try:
            data = self._get("/v1/models")
        except TransportError as exc:
            return {"ready": False, "detail": str(exc), "model": self.config.model}
        ids = [m.get("id") for m in (data.get("data") or []) if isinstance(m, dict)]
        return {
            "ready": True,
            "detail": "ok",
            "model": self.config.model,
            "served_models": ids,
        }

    def chat(
        self,
        messages: list[dict[str, Any]],
        *,
        tools: list[dict[str, Any]] | None = None,
        tool_choice: Any = None,
        response_format: Mapping[str, Any] | None = None,
        max_tokens: int = 256,
        temperature: float = 0.0,
        seed: int | None = 42,
        logprobs: bool = False,
        top_logprobs: int | None = None,
        stop: tuple[str, ...] | list[str] | None = None,
        extra_body: Mapping[str, Any] | None = None,
    ) -> ChatOutcome:
        payload: dict[str, Any] = {
            "model": self.config.model,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
        }
        if seed is not None:
            payload["seed"] = seed
        if stop:
            payload["stop"] = list(stop)
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = tool_choice if tool_choice is not None else "auto"
        if response_format is not None:
            payload["response_format"] = dict(response_format)
        if logprobs:
            payload["logprobs"] = True
            payload["top_logprobs"] = int(top_logprobs or 5)
        if extra_body:
            payload.update(dict(extra_body))

        started = time.perf_counter()
        data = self._post("/v1/chat/completions", payload)
        latency_ms = (time.perf_counter() - started) * 1000.0

        choices = data.get("choices") or []
        if not choices:
            raise TransportError(f"no choices in response: {json.dumps(data)[:300]}")
        message = choices[0].get("message") or {}
        content = message.get("content") or ""
        tool_calls = message.get("tool_calls") or []
        tool_call = tool_calls[0] if tool_calls else None
        # ``reasoning_content`` is deliberately ignored. Measured on
        # Nemotron-Orchestrator-8B: its reasoning explicitly enumerates
        # alternatives it then rejects ("fs.read ... Alternatively, using
        # shell.run ..."), so parsing the reasoning channel as the decision
        # would record a rejected option as the answer. Only ``content`` and
        # ``tool_calls`` are the model's decision.
        usage = data.get("usage") or {}
        # llama.cpp exposes precise timings; ollama exposes durations in ns.
        timings = data.get("timings") or {}
        if not timings and data.get("total_duration"):
            timings = {
                "prompt_n": data.get("prompt_eval_count"),
                "predicted_n": data.get("eval_count"),
                "prompt_ms": (data.get("prompt_eval_duration") or 0) / 1e6,
                "predicted_ms": (data.get("eval_duration") or 0) / 1e6,
                "load_ms": (data.get("load_duration") or 0) / 1e6,
            }
        ttft = None
        if timings.get("prompt_ms") is not None:
            ttft = float(timings["prompt_ms"])
        outcome = ChatOutcome(
            content=content,
            tool_call=tool_call,
            usage=usage,
            timings=timings,
            raw=data,
            latency_ms=latency_ms,
            ttft_ms=ttft,
        )
        outcome.raw = data
        return outcome
