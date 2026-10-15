import json

import httpx2
import pytest

from z0int.functions.contract import EXPECTED_JEV_MODEL
from z0int.functions import jev
from z0int.functions.typesafe_sdk_client import (
    ask_choice,
    ask_noul,
    normalize_base_url,
)


def response(answer):
    return {
        "model": EXPECTED_JEV_MODEL,
        "usage": {"input_tokens": 12, "output_tokens": 3},
        "answers": {"decision": answer},
    }


def test_legacy_v1_base_url_normalizes_to_official_sdk_root(monkeypatch):
    assert normalize_base_url(None) == "https://api.typesafe.ai"
    assert normalize_base_url("https://api.typesafe.ai/v1") == "https://api.typesafe.ai"
    assert normalize_base_url("https://example.test/prefix/v1/") == "https://example.test/prefix"

    seen = []

    def handler(request):
        seen.append(request)
        return httpx2.Response(
            200,
            json=response({"type": "noul", "noul": 0.91}),
        )

    monkeypatch.setenv("TYPESAFE_BASE_URL", "https://api.typesafe.ai/v1")
    result = ask_noul(
        {"message": "stop now"},
        proposition="Did the user explicitly ask to stop?",
        api_key="test-key",
        model=EXPECTED_JEV_MODEL,
        transport=httpx2.MockTransport(handler),
    )

    assert result["probability"] == 0.91
    assert result["model"] == EXPECTED_JEV_MODEL
    assert result["usage"] == {"input_tokens": 12, "output_tokens": 3}
    assert len(seen) == 1
    assert str(seen[0].url) == "https://api.typesafe.ai/v1/systemone"
    body = json.loads(seen[0].content)
    assert body["model"] == EXPECTED_JEV_MODEL
    assert body["questions"]["decision"]["type"] == "noul"


def test_choice_uses_official_typed_response_and_one_physical_request():
    calls = 0

    def handler(request):
        nonlocal calls
        calls += 1
        return httpx2.Response(
            200,
            json=response({
                "type": "choice",
                "choice": "performance",
                "confidence": 0.97,
                "probabilities": {
                    "creative": 0.01,
                    "performance": 0.97,
                    "research": 0.02,
                },
            }),
        )

    result = ask_choice(
        {"filename": "q3.html", "excerpt": "ROAS CTR spend"},
        instructions="Classify the report.",
        options={
            "creative": "marketing creative",
            "performance": "paid media metrics",
            "research": "research report",
        },
        api_key="test-key",
        model=EXPECTED_JEV_MODEL,
        transport=httpx2.MockTransport(handler),
    )

    assert calls == 1
    assert result["label"] == "performance"
    assert result["confidence"] == 0.97
    assert result["probabilities"]["performance"] == 0.97
    assert result["usage"] == {"input_tokens": 12, "output_tokens": 3}


def test_sdk_retries_are_disabled_even_on_retryable_503():
    calls = 0

    def handler(request):
        nonlocal calls
        calls += 1
        return httpx2.Response(
            503,
            json={"detail": "synthetic unavailable"},
        )

    with pytest.raises(Exception):
        ask_noul(
            "fixture",
            proposition="Fixture?",
            api_key="test-key",
            model=EXPECTED_JEV_MODEL,
            transport=httpx2.MockTransport(handler),
        )
    assert calls == 1


def test_jev_semantic_wrapper_rejects_wrong_served_revision(monkeypatch):
    monkeypatch.setenv("TYPESAFE_API_KEY", "test-key")
    monkeypatch.setattr(jev, "_host_env_loader", lambda: None)
    jev.reset_credential_cache()
    monkeypatch.setattr(
        jev,
        "ask_noul",
        lambda *a, **kw: {
            "model": "jev-other",
            "probability": 0.9,
            "usage": {},
            "latency_ms": 1.0,
        },
    )

    with pytest.raises(Exception, match="served revision"):
        jev.decide_noul("fixture", proposition="Fixture?")
    jev.reset_credential_cache()


def test_jev_semantic_wrapper_preserves_choice_usage_and_credential_source(monkeypatch):
    monkeypatch.setenv("TYPESAFE_API_KEY", "test-key")
    monkeypatch.setattr(jev, "_host_env_loader", lambda: None)
    jev.reset_credential_cache()
    monkeypatch.setattr(
        jev,
        "ask_choice",
        lambda *a, **kw: {
            "model": EXPECTED_JEV_MODEL,
            "label": "performance",
            "probabilities": {
                "creative": 0.01,
                "performance": 0.98,
                "research": 0.01,
            },
            "confidence": 0.98,
            "usage": {"input_tokens": 42, "output_tokens": 3},
            "latency_ms": 4.0,
        },
    )

    result = jev.decide_choice(
        {"filename": "q3.html"},
        instructions="Classify.",
        options={
            "creative": "creative",
            "performance": "performance",
            "research": "research",
        },
    )
    assert result["model"] == EXPECTED_JEV_MODEL
    assert result["label"] == "performance"
    assert result["usage"] == {"input_tokens": 42, "output_tokens": 3}
    assert result["credential_source"] == "environment"
    jev.reset_credential_cache()


def test_missing_key_fails_before_network_semantics(monkeypatch):
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
    monkeypatch.setattr(jev, "_host_env_loader", lambda: None)
    jev.reset_credential_cache()
    with pytest.raises(Exception, match="no credential resolvable"):
        jev.decide_noul("fixture", proposition="Fixture?")
    jev.reset_credential_cache()
