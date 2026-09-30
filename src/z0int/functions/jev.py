"""Canonical TypeSafe Jev implementation.

Network requests use the official typesafe-sdk package. z0 owns physical
attempt accounting, so SDK retries are disabled for every call.

Credential resolution stays compatible with older local setups: an optional
jevkit.keystore may still supply a key, but jevkit.client is no longer a
runtime dependency.
"""

from __future__ import annotations

import importlib.util
import math
import os
import time
from pathlib import Path
from typing import Any, Mapping

from .contract import (
    EXPECTED_JEV_MODEL,
    FUNCTION_ID,
    PROPOSITION,
    BackendCapabilities,
    VerifierUnavailable,
)

_QUESTION_ID = "sufficient"

VALIDATION = {
    "authored144": {"n": 144, "family_balanced_accuracy": 0.9514, "ece_top1": 0.0334},
    "perturbations108": {"n": 108, "family_balanced_accuracy": 1.0000, "ece_top1": 0.0029},
    "evidence_sufficiency_controls": {"answer_present": 0.97, "answer_absent": 0.01},
}


def _repo_script(name: str) -> Path:
    return Path(__file__).resolve().parents[3] / "scripts" / name


def _host_env_loader():
    path = _repo_script("race-omp-backends.py")
    if not path.is_file():
        return None
    spec = importlib.util.spec_from_file_location("z0int_host_env_loader", path)
    if spec is None or spec.loader is None:
        return None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_RESOLVED: tuple[str | None, str] | None = None


def reset_credential_cache() -> None:
    global _RESOLVED
    _RESOLVED = None


def resolve_credential() -> tuple[str | None, str]:
    global _RESOLVED
    if _RESOLVED is not None:
        return _RESOLVED
    key, source = _resolve_uncached()
    if key:
        _RESOLVED = (key, source)
    return key, source


def _resolve_uncached() -> tuple[str | None, str]:
    try:
        from jevkit import keystore  # type: ignore

        key = keystore.resolve()
        if key:
            try:
                source = f"jevkit:{keystore.source()}"
            except Exception:
                source = "jevkit"
            return key, source
    except Exception:
        pass

    env = os.environ.get("TYPESAFE_API_KEY", "").strip()
    if env:
        return env, "environment"

    loader = _host_env_loader()
    if loader is not None:
        try:
            loader.load_env(loader.ENV_PATH)
        except Exception:
            return None, "absent"
        fallback = os.environ.get("TYPESAFE_API_KEY", "").strip()
        if fallback:
            return fallback, "host-env-file"
    return None, "absent"


def credential_source() -> str:
    return resolve_credential()[1]


def ensure_credential() -> str | None:
    return resolve_credential()[0]


def _sdk_symbols():
    try:
        from typesafe_sdk import Choice, Noul, RetryPolicy, TypeSafeClient
    except Exception as exc:
        raise VerifierUnavailable(
            "jev",
            f"official typesafe-sdk unavailable: {exc}",
        ) from exc
    return TypeSafeClient, RetryPolicy, Choice, Noul


def _usage_dict(usage: Any) -> dict[str, int]:
    out: dict[str, int] = {}
    for name in ("input_tokens", "output_tokens"):
        value = getattr(usage, name, None)
        if isinstance(value, int) and value >= 0:
            out[name] = value
    return out


def _system_one(
    state: object,
    questions: Mapping[str, Any],
    *,
    timeout: float,
) -> Any:
    key = ensure_credential()
    if not key:
        raise VerifierUnavailable("jev", "no credential resolvable")

    TypeSafeClient, RetryPolicy, _, _ = _sdk_symbols()
    try:
        with TypeSafeClient(
            api_key=key,
            model=EXPECTED_JEV_MODEL,
            timeout=timeout,
            retry=RetryPolicy(max_retries=0),
        ) as client:
            return client.system_one(
                state,
                questions,
                model=EXPECTED_JEV_MODEL,
                timeout=timeout,
                retry=RetryPolicy(max_retries=0),
            )
    except VerifierUnavailable:
        raise
    except Exception as exc:
        status = getattr(exc, "status", None)
        prefix = f"http_{status}" if isinstance(status, int) else type(exc).__name__
        raise VerifierUnavailable("jev", f"{prefix}: {exc}") from exc


def _served_model(reply: Any, *, strict: bool = True) -> str:
    served = getattr(reply, "model", None)
    if not isinstance(served, str) or not served:
        raise VerifierUnavailable("jev", "TypeSafe response missing served model")
    if strict and served != EXPECTED_JEV_MODEL:
        raise VerifierUnavailable(
            "jev",
            f"served revision {served!r} != validated {EXPECTED_JEV_MODEL!r}",
        )
    return served


class JevVerifier:
    backend = "jev"
    role = "reference_escalation"

    def __init__(self, *, timeout: float = 15.0, strict_model: bool = True) -> None:
        self.timeout = timeout
        self.strict_model = strict_model

    def capabilities(self, *, available: bool | None = None) -> BackendCapabilities:
        source = credential_source()
        return BackendCapabilities(
            function_id=FUNCTION_ID,
            backend=self.backend,
            local=False,
            role=self.role,
            model=EXPECTED_JEV_MODEL,
            revision=EXPECTED_JEV_MODEL,
            available=(source != "absent") if available is None else available,
            credential_source=source,
            validated_on=(
                "authored144",
                "perturbations108",
                "evidence-sufficiency controls",
                "3 controlled packets x 3 phrasings",
            ),
            validation=VALIDATION,
            typical_latency_ms=280.0,
            cost_per_call_usd=0.00006,
            detail=(
                "TypeSafe Jev via official typesafe-sdk; "
                "P(true) is the native Noul probability"
            ),
        )

    def health(self) -> tuple[bool, str]:
        source = credential_source()
        if source == "absent":
            return False, (
                "TYPESAFE_API_KEY not resolvable through the optional legacy "
                "keystore, environment, or host env file"
            )
        return True, f"credential present via {source}; live readiness requires a call"

    def verify(
        self,
        state: str,
        *,
        proposition: str = PROPOSITION,
    ) -> tuple[float, dict[str, Any]]:
        _, _, _, Noul = _sdk_symbols()
        started = time.monotonic()
        reply = _system_one(
            state,
            {_QUESTION_ID: Noul(instructions=proposition)},
            timeout=self.timeout,
        )
        served = _served_model(reply, strict=self.strict_model)
        answer = getattr(reply, "nouls", {}).get(_QUESTION_ID)
        if answer is None:
            raise VerifierUnavailable("jev", "Noul response missing sufficiency answer")
        p_true = float(answer.noul)
        if not math.isfinite(p_true) or not 0 <= p_true <= 1:
            raise VerifierUnavailable("jev", "Noul probability must be finite in [0,1]")
        return p_true, {
            "model": served,
            "revision": served,
            "model_matches_validated": served == EXPECTED_JEV_MODEL,
            "credential_source": credential_source(),
            "usage": _usage_dict(getattr(reply, "usage", None)),
            "provider_latency_ms": (time.monotonic() - started) * 1000,
            "question_type": "noul",
            "client": "typesafe-sdk",
        }


def assess_claim(state: dict[str, str]) -> dict[str, Any]:
    _, _, Choice, _ = _sdk_symbols()
    choices = {
        "supported": "The evidence establishes the claim",
        "insufficient": "The evidence does not establish either",
        "contradicted": "The evidence establishes the opposite",
    }
    started = time.monotonic()
    reply = _system_one(
        state,
        {
            "assess": Choice(
                instructions="Which option does the supplied evidence establish?",
                criteria=choices,
            )
        },
        timeout=15.0,
    )
    served = _served_model(reply)
    answer = getattr(reply, "choices", {}).get("assess")
    if answer is None:
        raise VerifierUnavailable("jev", "Choice response missing assess answer")
    probabilities = dict(answer.probabilities)
    if set(probabilities) != set(choices):
        raise VerifierUnavailable("jev", "incomplete choice distribution")
    usage = _usage_dict(getattr(reply, "usage", None))
    return {
        "backend": "jev",
        "model": served,
        "revision": served,
        "status": {
            "supported": "SUPPORTED",
            "insufficient": "UNKNOWN",
            "contradicted": "UNSUPPORTED",
        }[answer.choice],
        "answers": [{
            "id": "assess",
            "type": "choice",
            "choice": answer.choice,
            "confidence": float(answer.confidence),
            "probabilities": probabilities,
        }],
        "latency_ms": (time.monotonic() - started) * 1000,
        "diagnostics": {
            **usage,
            "credential_source": credential_source(),
            "usage_source": "provider_response",
            "client": "typesafe-sdk",
        },
    }


def decide_choice(
    state: object,
    *,
    instructions: str,
    options: dict[str, str],
) -> dict[str, Any]:
    if not isinstance(instructions, str) or not instructions.strip():
        raise ValueError("choice instructions are required")
    if not isinstance(options, dict) or not 2 <= len(options) <= 16:
        raise ValueError("choice options must contain 2..16 labels")
    if any(
        not isinstance(label, str)
        or not label.strip()
        or len(label) > 80
        or not isinstance(description, str)
        or not description.strip()
        for label, description in options.items()
    ):
        raise ValueError("choice labels/descriptions must be bounded nonempty strings")

    _, _, Choice, _ = _sdk_symbols()
    started = time.monotonic()
    reply = _system_one(
        state,
        {"decision": Choice(instructions=instructions, criteria=options)},
        timeout=15.0,
    )
    served = _served_model(reply)
    answer = getattr(reply, "choices", {}).get("decision")
    if answer is None:
        raise VerifierUnavailable("jev", "Choice response missing decision answer")

    probabilities = {
        label: float(value)
        for label, value in dict(answer.probabilities).items()
    }
    if answer.choice not in options or set(probabilities) != set(options):
        raise VerifierUnavailable(
            "jev",
            "choice response labels do not match requested options",
        )
    if any(
        not math.isfinite(value) or not 0 <= value <= 1
        for value in probabilities.values()
    ):
        raise VerifierUnavailable(
            "jev",
            "choice probabilities must be finite in [0,1]",
        )
    if abs(math.fsum(probabilities.values()) - 1.0) > 1e-4:
        raise VerifierUnavailable("jev", "choice probabilities must sum to 1")

    confidence = float(answer.confidence)
    if not math.isfinite(confidence) or not 0 <= confidence <= 1:
        raise VerifierUnavailable("jev", "choice confidence must be finite in [0,1]")

    return {
        "backend": "jev",
        "model": served,
        "revision": served,
        "label": answer.choice,
        "probabilities": probabilities,
        "confidence": confidence,
        "latency_ms": (time.monotonic() - started) * 1000,
        "usage": _usage_dict(getattr(reply, "usage", None)),
        "credential_source": credential_source(),
        "client": "typesafe-sdk",
    }


def decide_noul(state: object, *, proposition: str) -> dict[str, Any]:
    if not isinstance(proposition, str) or not proposition.strip() or len(proposition) > 4000:
        raise ValueError("noul proposition must be 1..4000 characters")

    _, _, _, Noul = _sdk_symbols()
    started = time.monotonic()
    reply = _system_one(
        state,
        {"decision": Noul(instructions=proposition)},
        timeout=15.0,
    )
    served = _served_model(reply)
    answer = getattr(reply, "nouls", {}).get("decision")
    if answer is None:
        raise VerifierUnavailable("jev", "Noul response missing decision answer")

    probability = float(answer.noul)
    if not math.isfinite(probability) or not 0 <= probability <= 1:
        raise VerifierUnavailable("jev", "Noul probability must be finite in [0,1]")

    return {
        "backend": "jev",
        "model": served,
        "revision": served,
        "probability": probability,
        "latency_ms": (time.monotonic() - started) * 1000,
        "usage": _usage_dict(getattr(reply, "usage", None)),
        "credential_source": credential_source(),
        "client": "typesafe-sdk",
    }
