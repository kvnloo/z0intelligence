import json

import httpx2
import pytest
import typesafe_sdk

from z0int.functions.contract import EXPECTED_JEV_MODEL, VerifierUnavailable
from z0int.functions import jev


def install_transport(monkeypatch, payload, requests):
    original = typesafe_sdk.TypeSafeClient

    def handler(request):
        requests.append(request)
        return httpx2.Response(
            200,
            json=payload,
            headers={"x-typesafe-request-id": "req-z0-test"},
        )

    def factory(**kwargs):
        return original(
            **kwargs,
            transport=httpx2.MockTransport(handler),
        )

    monkeypatch.setattr(typesafe_sdk, "TypeSafeClient", factory)


def prepare_key(monkeypatch):
    monkeypatch.setenv("TYPESAFE_API_KEY", "test-typesafe-key")
    jev.reset_credential_cache()


def decode_request(request):
    return json.loads(request.content.decode("utf-8"))


def test_choice_uses_official_sdk_wire_contract_once(monkeypatch):
    prepare_key(monkeypatch)
    requests = []
    install_transport(
        monkeypatch,
        {
            "model": EXPECTED_JEV_MODEL,
            "answers": {
                "decision": {
                    "type": "choice",
                    "choice": "performance",
                    "confidence": 0.98,
                    "probabilities": {
                        "creative": 0.01,
                        "performance": 0.98,
                        "research": 0.01,
                    },
                }
            },
            "usage": {"input_tokens": 42, "output_tokens": 3},
        },
        requests,
    )

    result = jev.decide_choice(
        {"filename": "paid-media.html", "excerpt": "ROAS CTR CPC"},
        instructions="Classify the report.",
        options={
            "creative": "marketing copy",
            "performance": "paid media metrics",
            "research": "general research",
        },
    )

    assert result["label"] == "performance"
    assert result["model"] == EXPECTED_JEV_MODEL
    assert result["revision"] == EXPECTED_JEV_MODEL
    assert result["usage"] == {"input_tokens": 42, "output_tokens": 3}
    assert result["client"] == "typesafe-sdk"
    assert len(requests) == 1

    body = decode_request(requests[0])
    assert body["model"] == EXPECTED_JEV_MODEL
    assert body["state"]["filename"] == "paid-media.html"
    assert body["questions"]["decision"]["type"] == "choice"
    assert body["questions"]["decision"]["criteria"] == {
        "creative": "marketing copy",
        "performance": "paid media metrics",
        "research": "general research",
    }


def test_noul_uses_official_sdk_wire_contract_once(monkeypatch):
    prepare_key(monkeypatch)
    requests = []
    install_transport(
        monkeypatch,
        {
            "model": EXPECTED_JEV_MODEL,
            "answers": {
                "decision": {
                    "type": "noul",
                    "noul": 0.97,
                }
            },
            "usage": {"input_tokens": 19, "output_tokens": 1},
        },
        requests,
    )

    result = jev.decide_noul(
        {"message": "stop now"},
        proposition="Is the user explicitly asking the agent to stop?",
    )

    assert result["probability"] == pytest.approx(0.97)
    assert result["usage"] == {"input_tokens": 19, "output_tokens": 1}
    assert result["client"] == "typesafe-sdk"
    assert len(requests) == 1

    body = decode_request(requests[0])
    assert body["model"] == EXPECTED_JEV_MODEL
    assert body["questions"]["decision"]["type"] == "noul"
    assert body["questions"]["decision"]["instructions"].startswith(
        "Is the user explicitly"
    )


def test_reference_verifier_uses_native_noul_probability(monkeypatch):
    prepare_key(monkeypatch)
    requests = []
    install_transport(
        monkeypatch,
        {
            "model": EXPECTED_JEV_MODEL,
            "answers": {
                "sufficient": {
                    "type": "noul",
                    "noul": 0.73,
                }
            },
            "usage": {"input_tokens": 31, "output_tokens": 1},
        },
        requests,
    )

    p_true, meta = jev.JevVerifier().verify("bounded evidence")

    assert p_true == pytest.approx(0.73)
    assert meta["model"] == EXPECTED_JEV_MODEL
    assert meta["model_matches_validated"] is True
    assert meta["usage"] == {"input_tokens": 31, "output_tokens": 1}
    assert meta["client"] == "typesafe-sdk"
    assert len(requests) == 1


def test_wrong_served_revision_fails_closed(monkeypatch):
    prepare_key(monkeypatch)
    requests = []
    install_transport(
        monkeypatch,
        {
            "model": "jev-future-unvalidated",
            "answers": {
                "decision": {
                    "type": "noul",
                    "noul": 0.9,
                }
            },
            "usage": {"input_tokens": 3, "output_tokens": 1},
        },
        requests,
    )

    with pytest.raises(VerifierUnavailable, match="served revision"):
        jev.decide_noul("state", proposition="proposition")

    assert len(requests) == 1


def test_assess_claim_preserves_three_way_contract(monkeypatch):
    prepare_key(monkeypatch)
    requests = []
    install_transport(
        monkeypatch,
        {
            "model": EXPECTED_JEV_MODEL,
            "answers": {
                "assess": {
                    "type": "choice",
                    "choice": "contradicted",
                    "confidence": 0.94,
                    "probabilities": {
                        "supported": 0.03,
                        "insufficient": 0.03,
                        "contradicted": 0.94,
                    },
                }
            },
            "usage": {"input_tokens": 28, "output_tokens": 3},
        },
        requests,
    )

    result = jev.assess_claim({"claim": "x", "evidence": "opposite"})

    assert result["status"] == "UNSUPPORTED"
    assert result["model"] == EXPECTED_JEV_MODEL
    assert result["answers"][0]["choice"] == "contradicted"
    assert result["diagnostics"]["client"] == "typesafe-sdk"
    assert len(requests) == 1


def test_missing_sdk_is_reported_as_backend_unavailable(monkeypatch):
    prepare_key(monkeypatch)

    def broken_symbols():
        raise VerifierUnavailable("jev", "official typesafe-sdk unavailable: fixture")

    monkeypatch.setattr(jev, "_sdk_symbols", broken_symbols)
    with pytest.raises(VerifierUnavailable, match="official typesafe-sdk unavailable"):
        jev.decide_noul("state", proposition="p")
