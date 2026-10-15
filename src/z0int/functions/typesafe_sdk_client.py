"""Official TypeSafe SDK transport for z0's bounded Jev decisions.

The SDK owns HTTP/schema parsing. z0 owns authorization, physical-attempt
accounting, served-model pinning, receipts and replay semantics.
"""
from __future__ import annotations

import os
import time
from typing import Any


DEFAULT_BASE_URL = "https://api.typesafe.ai"


def normalize_base_url(raw: str | None) -> str:
    """Accept the legacy /v1 base used by older z0/AgentWeb config."""
    value = (raw or DEFAULT_BASE_URL).strip().rstrip("/")
    if value.endswith("/v1"):
        value = value[:-3].rstrip("/")
    return value or DEFAULT_BASE_URL


def _client(
    *,
    api_key: str,
    model: str,
    timeout: float,
    transport: Any = None,
):
    try:
        from typesafe_sdk import RetryPolicy, TypeSafeClient
    except Exception as exc:  # pragma: no cover - packaging/environment failure
        raise RuntimeError(f"typesafe-sdk unavailable: {exc}") from exc

    return TypeSafeClient(
        api_key=api_key,
        model=model,
        timeout=timeout,
        retry=RetryPolicy(max_retries=0),
        base_url=normalize_base_url(os.environ.get("TYPESAFE_BASE_URL")),
        transport=transport,
    )


def _usage(response: Any) -> dict[str, int]:
    usage = getattr(response, "usage", None)
    out: dict[str, int] = {}
    for key in ("input_tokens", "output_tokens"):
        value = getattr(usage, key, None)
        if isinstance(value, int) and value >= 0:
            out[key] = value
    return out


def ask_noul(
    state: Any,
    *,
    proposition: str,
    api_key: str,
    model: str,
    timeout: float = 15.0,
    transport: Any = None,
) -> dict[str, Any]:
    from typesafe_sdk import Noul

    started = time.monotonic()
    with _client(
        api_key=api_key,
        model=model,
        timeout=timeout,
        transport=transport,
    ) as client:
        response = client.system_one(
            state,
            {"decision": Noul(instructions=proposition)},
            model=model,
            timeout=timeout,
        )
    answer = response.nouls.get("decision")
    if answer is None:
        raise RuntimeError("TypeSafe response missing decision noul")
    return {
        "model": response.model,
        "probability": float(answer.noul),
        "usage": _usage(response),
        "latency_ms": (time.monotonic() - started) * 1000,
    }


def ask_choice(
    state: Any,
    *,
    instructions: str,
    options: dict[str, str],
    api_key: str,
    model: str,
    timeout: float = 15.0,
    transport: Any = None,
) -> dict[str, Any]:
    from typesafe_sdk import Choice

    started = time.monotonic()
    with _client(
        api_key=api_key,
        model=model,
        timeout=timeout,
        transport=transport,
    ) as client:
        response = client.system_one(
            state,
            {
                "decision": Choice(
                    instructions=instructions,
                    criteria=options,
                )
            },
            model=model,
            timeout=timeout,
        )
    answer = response.choices.get("decision")
    if answer is None:
        raise RuntimeError("TypeSafe response missing decision choice")
    return {
        "model": response.model,
        "label": answer.choice,
        "probabilities": dict(answer.probabilities),
        "confidence": float(answer.confidence),
        "usage": _usage(response),
        "latency_ms": (time.monotonic() - started) * 1000,
    }
