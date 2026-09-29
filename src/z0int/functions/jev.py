"""Canonical Jev implementation of ``verify.evidence_sufficiency``.

One adapter around the existing canonical client, ``jevkit.client``:

    jevkit/client.py  ->  POST https://api.typesafe.ai/v1/systemone

There is no second HTTP client here. ``ask()`` is called with an injected
``transport`` only so the *served model revision* can be captured: ``ask()``
returns answers/usage/latency and drops the payload's ``model`` field, and the
receipt must record which revision actually answered.

Credentials are not re-implemented either. Resolution order is
``jevkit.keystore`` (env -> OS secret store -> credentials file) first; only if
that is absent do we fall back to the *existing* host loader already used by
``scripts/race-omp-backends.py``, and then the source is recorded as
``host-env-file`` so the fallback is visible and can be retired.
"""

from __future__ import annotations

import importlib.util
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

_QUESTION_ID = "sufficient"

#: Documented published numbers for this model, for the capabilities block.
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


#: Resolution is cached on first success. The host loader mutates ``os.environ``
#: as a side effect, so without caching a second call reports the key as coming
#: from "environment" when it actually came from the host env file -- and receipt
#: provenance would depend on how many times it had been read.
_RESOLVED: tuple[str | None, str] | None = None


def reset_credential_cache() -> None:
    """For tests: forget the cached resolution."""
    global _RESOLVED
    _RESOLVED = None


def resolve_credential() -> tuple[str | None, str]:
    """Return ``(key, source)``. The single resolver: availability reporting and
    the call path must never disagree about whether a credential exists.

    Order: the canonical ``jevkit.keystore`` first, then the process environment,
    then the *existing* host env file. Nothing is written and the value is never
    logged.
    """
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
    """Where the key comes from, using the canonical store first."""
    return resolve_credential()[1]


def ensure_credential() -> str | None:
    """Return a key, or None. Never writes, never logs the value."""
    return resolve_credential()[0]


class _Capture:
    """Wrap jevkit's own transport to record the served model revision."""

    def __init__(self) -> None:
        self.model: str | None = None
        self.payload: dict[str, Any] | None = None

    def __call__(self, body: bytes, headers: dict[str, str], timeout: float, *a, **kw) -> bytes:
        from jevkit import client as jev_client  # type: ignore

        raw = jev_client._http_transport(body, headers, timeout)  # the canonical transport
        try:
            import json

            payload = json.loads(raw)
            if isinstance(payload, dict):
                self.payload = payload
                model = payload.get("model")
                self.model = str(model) if model else None
        except Exception:
            pass
        return raw


def ask_named_choice(state: str, *, question_id: str, instructions: str,
                     criteria: dict[str, str], timeout: float = 20.0,
                     model: str | None = None) -> tuple[dict[str, float], dict[str, Any]]:
    """One named-choice call on the CANONICAL Jev transport.

    Reuses ``_Capture`` so the served revision is recovered exactly the way
    ``JevVerifier.verify`` recovers it: jevkit drops the model from its return
    value, so the transport capture is the only place it survives. No new HTTP
    path is introduced.
    """
    import time as _t

    key = ensure_credential()
    if not key:
        raise VerifierUnavailable("jev", "no credential resolvable")
    try:
        from jevkit import client as jev_client  # type: ignore
    except Exception as exc:  # pragma: no cover - environment problem
        raise VerifierUnavailable("jev", f"jevkit.client unavailable: {exc}") from exc

    expected = model or EXPECTED_JEV_MODEL
    capture = _Capture()
    t0 = _t.perf_counter()
    try:
        reply = jev_client.ask(
            state,
            {question_id: jev_client.choice(instructions, criteria)},
            timeout=timeout, model=expected, api_key=key, transport=capture,
        )
    except Exception as exc:
        code = getattr(exc, "code", type(exc).__name__)
        detail = getattr(exc, "detail", "") or str(exc)
        raise VerifierUnavailable("jev", f"{code}: {detail}") from exc

    served = capture.model
    answer = reply["answers"][question_id]
    return dict(answer.get("probabilities") or {}), {
        "model": served or expected,
        "revision": served or expected,
        "requested_model": expected,
        "served_model": served,
        "model_matches_validated": (served == expected) if served else None,
        "credential_source": credential_source(),
        "usage": reply.get("usage") or {},
        "provider_latency_ms": (_t.perf_counter() - t0) * 1000.0,
        "reply_latency_ms": reply.get("latency_ms"),
    }


class JevVerifier:
    """Reference/escalation implementation. Remote, metered, pinned revision."""

    backend = "jev"
    role = "reference_escalation"

    def __init__(self, *, timeout: float = 15.0, strict_model: bool = True) -> None:
        self.timeout = timeout
        self.strict_model = strict_model

    # -- contract ---------------------------------------------------------
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
            validated_on=("authored144", "perturbations108", "evidence-sufficiency controls",
                          "3 controlled packets x 3 phrasings"),
            validation=VALIDATION,
            typical_latency_ms=280.0,
            cost_per_call_usd=0.00006,
            detail="TypeSafe Jev via jevkit.client; P(true) is the native noul probability",
        )

    def health(self) -> tuple[bool, str]:
        source = credential_source()
        if source == "absent":
            return False, "TYPESAFE_API_KEY not resolvable through jevkit.keystore or the host env file"
        return True, f"credential present via {source}; live readiness requires a call"

    def verify(self, state: str, *, proposition: str = PROPOSITION
               ) -> tuple[float, dict[str, Any]]:
        """One noul call. Returns (p_true, metadata). Raises VerifierUnavailable."""
        key = ensure_credential()
        if not key:
            raise VerifierUnavailable(self.backend, "no credential resolvable")
        try:
            from jevkit import client as jev_client  # type: ignore
        except Exception as exc:  # pragma: no cover - environment problem
            raise VerifierUnavailable(self.backend, f"jevkit.client unavailable: {exc}") from exc

        capture = _Capture()
        try:
            reply = jev_client.ask(
                state,
                {_QUESTION_ID: jev_client.noul(proposition)},
                timeout=self.timeout,
                model=EXPECTED_JEV_MODEL,
                api_key=key,
                transport=capture,
            )
        except Exception as exc:  # JevError carries a code; keep the detail
            code = getattr(exc, "code", type(exc).__name__)
            detail = getattr(exc, "detail", "") or str(exc)
            raise VerifierUnavailable(self.backend, f"{code}: {detail}") from exc

        answer = reply["answers"][_QUESTION_ID]
        p_true = float(answer["noul"])
        served = capture.model
        if self.strict_model and served and served != EXPECTED_JEV_MODEL:
            raise VerifierUnavailable(
                self.backend, f"served revision {served!r} != validated {EXPECTED_JEV_MODEL!r}")
        return p_true, {
            "model": served or EXPECTED_JEV_MODEL,
            "revision": served or EXPECTED_JEV_MODEL,
            "model_matches_validated": served == EXPECTED_JEV_MODEL,
            "credential_source": credential_source(),
            "usage": reply.get("usage") or {},
            "provider_latency_ms": reply.get("latency_ms"),
            "question_type": "noul",
        }


def assess_claim(state: dict[str, str]) -> dict[str, Any]:
    """Validated three-way claim contract, distinct from binary sufficiency.

    Uses the same canonical client and credential resolver. No internal retry:
    the dispatch authority owns physical-attempt accounting and reconciliation.
    """
    import time
    from jevkit import client as jev_client
    key = ensure_credential()
    if not key:
        raise VerifierUnavailable("jev", "no credential resolvable")
    choices = {"supported": "The evidence establishes the claim",
               "insufficient": "The evidence does not establish either",
               "contradicted": "The evidence establishes the opposite"}
    capture = _Capture()
    started = time.monotonic()
    reply = jev_client.ask(state, {"assess": jev_client.choice(
        "Which option does the supplied evidence establish?", choices)},
        model=EXPECTED_JEV_MODEL, api_key=key, timeout=15, retries=0, transport=capture)
    if capture.model != EXPECTED_JEV_MODEL:
        raise VerifierUnavailable("jev", "served revision does not match validated revision")
    answer = reply["answers"]["assess"]
    if set(answer["probabilities"]) != set(choices):
        raise VerifierUnavailable("jev", "incomplete choice distribution")
    usage = reply.get("usage") or {}
    return {"backend": "jev", "model": capture.model, "revision": capture.model,
            "status": {"supported": "SUPPORTED", "insufficient": "UNKNOWN",
                       "contradicted": "UNSUPPORTED"}[answer["choice"]],
            "answers": [{"id": "assess", **answer}],
            "latency_ms": (time.monotonic()-started)*1000,
            "diagnostics": {**usage, "credential_source": credential_source(),
                            "usage_source": "provider_response"}}
