"""Canonical TypeSafe/Jev bounded-decision implementation.

Network execution uses the official typesafe-sdk client. z0 remains the
authority for authorization, admission, idempotency, served-model pinning and
receipts. The optional legacy jevkit.keystore may still supply credentials when
installed, but it is not an execution dependency.
"""

from __future__ import annotations

import importlib.util
import math
import os
from pathlib import Path
from typing import Any

from .contract import (
    EXPECTED_JEV_MODEL,
    FUNCTION_ID,
    PROPOSITION,
    BackendCapabilities,
    VerifierUnavailable,
)
from .typesafe_sdk_client import ask_choice, ask_noul


VALIDATION = {
    "authored144": {"n": 144, "family_balanced_accuracy": 0.9514, "ece_top1": 0.0334},
    "perturbations108": {"n": 108, "family_balanced_accuracy": 1.0000, "ece_top1": 0.0029},
    "evidence_sufficiency_controls": {"answer_present": 0.97, "answer_absent": 0.01},
}


def _repo_script(name: str) -> Path:
    return Path(__file__).resolve().parents[3] / "scripts" / name


def _host_env_loader():
    """The existing host credential loader, imported rather than re-implemented."""
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
    """Resolve key provenance without writing or logging the credential."""
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


def _unavailable(exc: Exception) -> VerifierUnavailable:
    return VerifierUnavailable("jev", f"{type(exc).__name__}: {exc}")


def _validate_model(model: object) -> str:
    served = str(model or "")
    if served != EXPECTED_JEV_MODEL:
        raise VerifierUnavailable(
            "jev",
            f"served revision {served!r} != validated {EXPECTED_JEV_MODEL!r}",
        )
    return served


def _validate_probability(value: object, name: str) -> float:
    if not isinstance(value, (int, float)):
        raise VerifierUnavailable("jev", f"{name} is not numeric")
    parsed = float(value)
    if not math.isfinite(parsed) or not 0 <= parsed <= 1:
        raise VerifierUnavailable("jev", f"{name} must be finite in [0,1]")
    return parsed


class JevVerifier:
    """Reference/escalation implementation. Remote, metered, pinned revision."""

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
                "P(true) is the native noul probability"
            ),
        )

    def health(self) -> tuple[bool, str]:
        source = credential_source()
        if source == "absent":
            return (
                False,
                "TYPESAFE_API_KEY not resolvable through optional legacy "
                "keystore, environment, or host env file",
            )
        return True, f"credential present via {source}; live readiness requires a call"

    def verify(
        self,
        state: str,
        *,
        proposition: str = PROPOSITION,
    ) -> tuple[float, dict[str, Any]]:
        key = ensure_credential()
        if not key:
            raise VerifierUnavailable(self.backend, "no credential resolvable")
        try:
            result = ask_noul(
                state,
                proposition=proposition,
                api_key=key,
                model=EXPECTED_JEV_MODEL,
                timeout=self.timeout,
            )
        except Exception as exc:
            raise _unavailable(exc) from exc

        served = str(result.get("model") or "")
        if self.strict_model:
            served = _validate_model(served)
        probability = _validate_probability(
            result.get("probability"), "noul probability"
        )
        return probability, {
            "model": served or EXPECTED_JEV_MODEL,
            "revision": served or EXPECTED_JEV_MODEL,
            "model_matches_validated": served == EXPECTED_JEV_MODEL,
            "credential_source": credential_source(),
            "usage": result.get("usage") or {},
            "provider_latency_ms": result.get("latency_ms"),
            "question_type": "noul",
        }


def assess_claim(state: dict[str, str]) -> dict[str, Any]:
    """Validated supported/insufficient/contradicted contract."""
    key = ensure_credential()
    if not key:
        raise VerifierUnavailable("jev", "no credential resolvable")

    choices = {
        "supported": "The evidence establishes the claim",
        "insufficient": "The evidence does not establish either",
        "contradicted": "The evidence establishes the opposite",
    }
    try:
        result = ask_choice(
            state,
            instructions="Which option does the supplied evidence establish?",
            options=choices,
            api_key=key,
            model=EXPECTED_JEV_MODEL,
            timeout=15,
        )
    except Exception as exc:
        raise _unavailable(exc) from exc

    served = _validate_model(result.get("model"))
    probabilities = result.get("probabilities")
    if not isinstance(probabilities, dict) or set(probabilities) != set(choices):
        raise VerifierUnavailable("jev", "incomplete choice distribution")
    parsed = {
        label: _validate_probability(value, f"probability {label}")
        for label, value in probabilities.items()
    }
    if abs(math.fsum(parsed.values()) - 1.0) > 1e-4:
        raise VerifierUnavailable("jev", "choice probabilities must sum to 1")
    label = result.get("label")
    if label not in choices:
        raise VerifierUnavailable("jev", "choice label is not in requested options")
    confidence = _validate_probability(result.get("confidence"), "choice confidence")

    usage = result.get("usage") or {}
    return {
        "backend": "jev",
        "model": served,
        "revision": served,
        "status": {
            "supported": "SUPPORTED",
            "insufficient": "UNKNOWN",
            "contradicted": "UNSUPPORTED",
        }[label],
        "answers": [{
            "id": "assess",
            "type": "choice",
            "choice": label,
            "probabilities": parsed,
            "confidence": confidence,
        }],
        "latency_ms": result.get("latency_ms"),
        "diagnostics": {
            **usage,
            "credential_source": credential_source(),
            "usage_source": "provider_response",
        },
    }


def decide_choice(
    state: object,
    *,
    instructions: str,
    options: dict[str, str],
) -> dict[str, Any]:
    """Experimental bounded Choice using the official TypeSafe SDK."""
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

    key = ensure_credential()
    if not key:
        raise VerifierUnavailable("jev", "no credential resolvable")
    try:
        result = ask_choice(
            state,
            instructions=instructions,
            options=options,
            api_key=key,
            model=EXPECTED_JEV_MODEL,
            timeout=15,
        )
    except Exception as exc:
        raise _unavailable(exc) from exc

    served = _validate_model(result.get("model"))
    label = result.get("label")
    probabilities = result.get("probabilities")
    if (
        label not in options
        or not isinstance(probabilities, dict)
        or set(probabilities) != set(options)
    ):
        raise VerifierUnavailable(
            "jev", "choice response labels do not match requested options"
        )
    parsed = {
        name: _validate_probability(value, f"probability {name}")
        for name, value in probabilities.items()
    }
    if abs(math.fsum(parsed.values()) - 1.0) > 1e-4:
        raise VerifierUnavailable("jev", "choice probabilities must sum to 1")
    confidence = _validate_probability(result.get("confidence"), "choice confidence")
    return {
        "backend": "jev",
        "model": served,
        "revision": served,
        "label": label,
        "probabilities": parsed,
        "confidence": confidence,
        "latency_ms": result.get("latency_ms"),
        "usage": result.get("usage") or {},
        "credential_source": credential_source(),
    }


def decide_noul(state: object, *, proposition: str) -> dict[str, Any]:
    """Experimental bounded Noul using the official TypeSafe SDK."""
    if (
        not isinstance(proposition, str)
        or not proposition.strip()
        or len(proposition) > 4000
    ):
        raise ValueError("noul proposition must be 1..4000 characters")

    key = ensure_credential()
    if not key:
        raise VerifierUnavailable("jev", "no credential resolvable")
    try:
        result = ask_noul(
            state,
            proposition=proposition,
            api_key=key,
            model=EXPECTED_JEV_MODEL,
            timeout=15,
        )
    except Exception as exc:
        raise _unavailable(exc) from exc

    served = _validate_model(result.get("model"))
    probability = _validate_probability(
        result.get("probability"), "noul probability"
    )
    return {
        "backend": "jev",
        "model": served,
        "revision": served,
        "probability": probability,
        "latency_ms": result.get("latency_ms"),
        "usage": result.get("usage") or {},
        "credential_source": credential_source(),
    }
