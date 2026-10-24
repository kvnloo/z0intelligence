"""Exact policy-serving binding shared by live and observe-only consumers."""
from __future__ import annotations

import json
from urllib.parse import quote
from urllib.request import urlopen
from dataclasses import replace
from typing import Any

from ... import policy_runtime
from .local_slm import LocalSLMBackend, ToolDecision, ToolDecisionRequest
from .transport import OpenAICompatTransport, ServerConfig


class _PolicyTransport(OpenAICompatTransport):
    """Capture endpoint-returned identity, not the requested model label."""

    response: dict[str, Any] | None = None

    def chat(self, *args: Any, **kwargs: Any) -> Any:
        result = super().chat(*args, **kwargs)
        self.response = result.raw
        return result


class PolicyArmBackend:
    """Bind one decision to one measured serving arm, with no role fallback."""

    def __init__(self, endpoint: dict[str, Any], threshold: float, *, shadow: bool = False) -> None:
        self.endpoint = endpoint
        self.threshold = threshold
        self.shadow = shadow
        base = endpoint["url"].rstrip("/")
        if base.endswith("/v1"):
            base = base[:-3]
        config = ServerConfig(base_url=base, model=endpoint.get("expected_model_id") or endpoint["model"],
                              runtime=endpoint["engine"], quantization=endpoint["quant"],
                              timeout_s=30.0)
        self.transport = _PolicyTransport(config)
        self.backend = LocalSLMBackend(backend_id="decision-policy", config=config, transport=self.transport)
        self.last: ToolDecision | None = None
        self.request: ToolDecisionRequest | None = None

    @property
    def backend_id(self) -> str:
        return self.backend.backend_id

    def health(self, *, load: bool = False) -> dict[str, Any]:
        return {"ready": policy_runtime.serving_health(self.endpoint["url"], self.endpoint) is True}

    def decide(self, request: ToolDecisionRequest) -> ToolDecision:
        # Several semantic tiers can visit this arm. Never repeat its request
        # or let a later tier accept the same below-threshold answer.
        if self.request == request and self.last is not None:
            return self.last
        self.request = request
        self.transport.response = None
        decision = self.backend.decide(request)
        response = self.transport.response or {}
        model = response.get("model")
        expected = {
            "model_id": self.endpoint.get("expected_model_id"),
            "engine_build": self.endpoint.get("engine_build"),
            "model_path": self.endpoint.get("model_path"),
            "model_sha256": self.endpoint.get("model_sha256"),
        }
        props: dict[str, Any] = {}
        try:
            props_url = self.transport.config.base_url + "/props?model=" + quote(self.transport.config.model, safe="")
            with urlopen(props_url, timeout=1.5) as reply:
                parsed = json.loads(reply.read().decode("utf-8"))
                if isinstance(parsed, dict):
                    props = parsed
        except Exception:
            pass
        observed = {"model_id": model, "engine_build": props.get("build_info"),
                    "model_path": props.get("model_path")}
        actual = {k: self.endpoint[k] for k in ("model", "quant", "engine", "backend", "device")}
        diagnostics = dict(decision.diagnostics)
        diagnostics.update(
            selected_arm=actual, response_model=model, response_revision=None,
            observed_identity=observed, expected_identity=expected,
            props_model_id=props.get("model_alias"), serving_url=self.endpoint["url"],
        )
        reason = None
        if not all(isinstance(value, str) and value for value in expected.values()):
            reason = "endpoint_expected_identity_missing"
        elif not props:
            reason = "endpoint_identity_unknown"
        elif model != expected["model_id"] or props.get("model_alias") != expected["model_id"]:
            reason = "endpoint_model_mismatch"
        elif observed["engine_build"] != expected["engine_build"]:
            reason = "endpoint_build_mismatch"
        elif observed["model_path"] != expected["model_path"]:
            reason = "endpoint_path_mismatch"
        elif not self.shadow and (decision.confidence is None or decision.confidence < self.threshold):
            reason = "policy_confidence_abstain"
        if reason:
            diagnostics["reason"] = reason
        self.last = replace(
            decision, model=model if isinstance(model, str) else None,
            revision=None,
            abstained=decision.abstained or reason is not None,
            selected_action=None if reason else decision.selected_action,
            diagnostics=diagnostics,
        )
        return self.last
